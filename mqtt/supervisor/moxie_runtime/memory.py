"""Conversation transcript + long-term memory: persistence, the privacy gate, parent read/erase."""
from __future__ import annotations
import json, os

from moxie_sdk.types import ActionType
from moxie_sdk.store import MemoryStore
from moxie_sdk.cloud_config import LoggingPolicy
from .constants import MEMORY_POLICY


class MemoryMixin:
    # ---- conversation memory (survives restarts) ----
    #
    # THIS IS THE SECOND MEMORY, AND IT IS THE ONE THAT HOLDS THE CHILD'S OWN WORDS.
    #
    # `MemoryStore` (below) keeps a handful of durable *facts* a module derived from a
    # conversation. This keeps the **rolling transcript** — every line, as said — and
    # `MOXIE_MEMORY_DIR` writes it to disk. Both compose files set that variable
    # (`docker-compose.yml`, `docker-compose.images.yml` → `/data/memory`), so on a
    # shipped appliance this path is ON by default.
    #
    # It used to be guarded by nothing but `if not self._memory_dir`, while this file's
    # own comments and `docs/architecture/content-module-contract.md` both promised that
    # `LoggingPolicy.NO_DATA` means nothing about the child is written. That was a stated
    # guarantee the code did not keep. These four methods are the gate that keeps it.
    #
    # Three decisions, written down because a reader will ask about each:
    #
    #  1. **`NO_MEDIA` (the default) writes the transcript; only `NO_DATA` stops it.**
    #     Telemetry's `NO_MEDIA` withholds `event_data` because that field is opaque
    #     `bytes` that could be audio or video — literal media a gate cannot classify. A
    #     transcript has no such payload: it is *entirely* text this process is already
    #     holding in RAM to make conversation work. So there is nothing to withhold and
    #     the choice is binary, and it is made the same way long-term memory and the
    #     safety journal make it (`MEMORY_POLICY`, `SAFETY_JOURNAL_POLICY`): allowed
    #     under `NO_MEDIA`/`FULL`, refused under `NO_DATA`. Deciding otherwise would mean
    #     the *default* deployment loses conversational continuity across a restart while
    #     still storing derived facts about the same child — stricter in name and not in
    #     substance.
    #
    #  2. **Flipping to `NO_DATA` deletes the file that is already there.** Refusing new
    #     writes and leaving yesterday's transcript on disk is a half-guarantee, and the
    #     contract is explicit that *"reads and erase always work"* — erasure is never
    #     policy-gated, so removing it is always permitted. It is also load-bearing:
    #     `_load_memory` reads that file straight back into RAM and into the next prompt,
    #     so a file left behind is not merely stored, it is still *in use*. The sweep runs
    #     at boot and after any config edit that could have flipped the switch, and the
    #     write path removes the file too, so no single missed hook leaves it lying about.
    #
    #  3. **In-memory history is NOT gated.** This is a *persistence* gate. The rolling
    #     window in RAM is what lets Moxie hold the thread of the conversation it is in;
    #     a robot that forgot the previous sentence would not be more private, it would
    #     be broken. Nothing about the child leaves this process either way, and the
    #     window dies with it. The privacy question is what survives on disk.
    #
    # The gate resolves through `memory_policy` — the same per-device callable the
    # runtime installs on `MemoryStore` — rather than a new constant or a new resolver,
    # so a parent has one switch, not two that could disagree.

    def _memory_path(self, device_id: str) -> str:
        safe = "".join(c for c in device_id if c.isalnum() or c in "-_")
        return os.path.join(self._memory_dir, f"{safe}.json")

    def transcript_persists(self, device_id) -> bool:
        """False under `NO_DATA` — this robot's transcript is never written to disk, and
        anything already there is removed. Resolved per call from the **effective**
        (`fleet ⊕ per-robot`) config, so a parent flipping the switch on a live robot
        takes effect on the very next turn with no restart."""
        return self.memory_policy(device_id) != LoggingPolicy.NO_DATA

    def _unlink(self, path: str) -> bool:
        """Remove one file. True if it was there. Best-effort by design: this runs on the
        MQTT thread, and a file we cannot delete must not cost the child their turn."""
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            print(f"[runtime] transcript erase failed ({path}): {e}", flush=True)
            return False

    def _forget_transcript(self, device_id: str) -> bool:
        """Delete this robot's on-disk transcript (and any half-written `.tmp` beside
        it). Never policy-gated — an erase always works — and idempotent."""
        if not self._memory_dir:
            return False
        path = self._memory_path(device_id)
        gone = self._unlink(path)
        self._unlink(path + ".tmp")          # a crash mid-save must not leave a copy
        if gone:
            print(f"[runtime] 🧽 erased on-disk transcript for {device_id} (NO_DATA)",
                  flush=True)
        return gone

    def purge_transcripts(self) -> int:
        """Remove every stored transcript whose robot is now under `NO_DATA`.

        Called at startup and after any config edit that could have flipped the switch,
        so "I turned recording off" means the file is gone *now* — not at the next turn,
        and not only for the robots that happen to be connected."""
        if not self._memory_dir or not os.path.isdir(self._memory_dir):
            return 0
        removed = 0
        try:
            names = os.listdir(self._memory_dir)
        except OSError as e:
            print(f"[runtime] transcript sweep failed: {e}", flush=True)
            return 0
        for name in names:
            if not name.endswith(".json") or self.transcript_persists(name[:-5]):
                continue
            self.history.pop(name[:-5], None)     # do not keep serving what we just erased
            if self._unlink(os.path.join(self._memory_dir, name)):
                removed += 1
        if removed:
            print(f"[runtime] 🧽 erased {removed} stored transcript(s) under NO_DATA",
                  flush=True)
        return removed

    def _load_memory(self):
        """Restore per-device conversation history from disk, if configured — for the
        robots whose parents allow it. The `NO_DATA` sweep runs FIRST, so a fleet-wide
        rule (which is durable, and therefore outlives the process) is honoured across a
        restart instead of being undone by one."""
        if not self._memory_dir:
            return
        try:
            os.makedirs(self._memory_dir, exist_ok=True)
            self.purge_transcripts()
            for name in os.listdir(self._memory_dir):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(self._memory_dir, name)) as fh:
                    self.history[name[:-5]] = json.load(fh)
            if self.history:
                print(f"[runtime] restored memory for {len(self.history)} robot(s)")
        except Exception as e:
            print(f"[runtime] memory load failed: {e}")

    def _save_memory(self, device_id: str):
        """Persist one robot's history (trimmed) so it survives a restart — **unless the
        parent's `LoggingPolicy` says nothing about this child may be stored**, in which
        case we write nothing and remove whatever is already there (see decisions 1-3
        above). `self.history` is untouched either way: the conversation still works."""
        if not self._memory_dir:
            return
        if not self.transcript_persists(device_id):
            self._forget_transcript(device_id)
            return
        h = self.history.get(device_id) or []
        if len(h) > self._max_memory:
            del h[: len(h) - self._max_memory]
        try:
            os.makedirs(self._memory_dir, exist_ok=True)
            tmp = self._memory_path(device_id) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(h, fh)
            os.replace(tmp, self._memory_path(device_id))
        except Exception as e:
            print(f"[runtime] memory save failed: {e}")

    # ---- long-term memory (persist_data + what a parent may read/erase) ----
    # The *conversation history* above is the rolling transcript. This is the other
    # memory: the durable facts a content module keeps between conversations
    # (docs/architecture/content-module-contract.md → `volley.persist_data` /
    # `session.summarize()`), stored by `moxie_sdk/store.py::MemoryStore`.
    #
    # The app owns the store (ContentApp builds one); the runtime owns two things the
    # app cannot know: the parent's per-device privacy switch, and *when a conversation
    # ended* — which is the only moment the whole transcript still exists.
    #
    # BEYOND #4 (openmoxie-feature-audit.md §4.2) says a memory a parent cannot read or
    # erase is not acceptable on a child's device. `/memory` is that floor: GET to read
    # what Moxie remembers (every item with its id and provenance), DELETE to forget one
    # item, one namespace or all of it, POST to erase the same way or to **correct** one
    # item in place. The console's 🧠 card is the browser over exactly these.

    def memory_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing what may be *remembered* about this child — the
        parent's explicit `logging_policy` if there is one, else `MEMORY_POLICY`.
        `NO_DATA` means no memory is written at all (reads and erase still work).

        Read from the **effective** config (fleet ⊕ per-robot), so a house rule set once
        for the appliance turns memory off for every robot on it, and a single robot can
        still be set apart."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return MEMORY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return MEMORY_POLICY

    def _wire_memory_policy(self, app=None):
        """Hand an app's memory store this runtime's per-device privacy gate.

        Done here rather than at construction so an app built by `config.build_app()`
        (which knows nothing about a device's config overrides) still honours them.
        `app` defaults to the appliance's own brain; `app_for` passes every brain it
        builds later, so a per-child brain's memory obeys the same parent switch as the
        default one — a privacy gate that applied to only one of them would be worse
        than none, because nobody would know which."""
        mem = getattr(app if app is not None else self.app, "memory", None)
        if mem is not None and getattr(mem, "policy", None) is None:
            try:
                mem.policy = self.memory_policy
            except Exception:
                pass

    def memory_store(self):
        """The app's memory store, or a read-only view of the same files for an app
        that has none (so `/memory` answers for any app)."""
        mem = getattr(self.app, "memory", None)
        if mem is not None:
            return mem
        return MemoryStore(self.store, policy=self.memory_policy)

    def memory_view(self, device_id) -> dict:
        """What Moxie remembers about one child, by namespace, with provenance."""
        mem = self.memory_store()
        view = mem.view(device_id)
        if device_id not in self.robots and not view.get("namespaces"):
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}"}
        view.update({"ok": True, "device_id": device_id,
                     "policy": self.memory_policy(device_id).name})
        return view

    def erase_memory(self, device_id, namespace=None, item=None) -> dict:
        """Forget one item, one namespace, or everything for this robot.

        Never policy-gated: a parent must always be able to delete. `item` is the finest
        cut — one wrong line goes without costing the rest of what that activity learned
        (BEYOND #4's other half)."""
        if item:
            removed = self.memory_store().erase_item(device_id, namespace, item)
            what = f"{namespace}/{item}"
        else:
            removed = self.memory_store().erase(device_id, namespace)
            what = namespace or "all"
        self._note("memory", f"🧽 erased memory: {what}")
        print(f"[runtime] 🧽 erased memory for {device_id} ({what}): {removed}",
              flush=True)
        out = self.memory_view(device_id)
        if not out.get("ok"):                     # erasing the last of it is still a hit
            out = {"ok": True, "device_id": device_id, "namespaces": {}, "bytes": 0,
                   "policy": self.memory_policy(device_id).name}
        out["erased"] = bool(removed)
        out["namespace"] = namespace or "all"
        if item:
            out["item"] = str(item)
        return out

    def edit_memory_item(self, device_id, namespace, item, text) -> dict:
        """Correct one remembered item — the other thing a parent needs when a summary is
        wrong but not worthless ("Puppy sleeps on **his** bed" → "…my bed").

        The store keeps the item's id, **pins** it (a human decision outranks decay) and
        re-runs the two rules that decide what may live in a prompt: the safety
        classifier, and the no-verbatim check against this robot's recent conversation —
        so a parent cannot paste the child's own words back in. A refusal raises, and the
        handler turns it into a 400 with the reason. Not policy-gated: fixing a wrong line
        must work even on a `NO_DATA` robot, where the only alternative is deleting it."""
        edited = self.memory_store().edit_item(
            device_id, namespace, item, text,
            history=list(self.history.get(device_id) or []))
        self._note("memory", f"✏️ corrected memory: {namespace}/{item}")
        print(f"[runtime] ✏️ corrected memory for {device_id} ({namespace}/{item})",
              flush=True)
        out = self.memory_view(device_id)
        out["edited"] = True
        out["namespace"] = str(namespace)
        out["item"] = str(item)
        return out

    # ---- end of a conversation (the contract's complete_handler moment) ----
    def _maybe_end_conversation(self, device_id, actions):
        """End the conversation if the answer carried an EXIT action (`<exit>`)."""
        for a in actions or []:
            if getattr(a, "type", None) == ActionType.EXIT:
                return self._end_conversation(device_id, "exit", inline=True)
        return None

    def _end_conversation(self, device_id, reason: str, *, robot=None, inline=False):
        """Tell the app a conversation finished, so it can write long-term memory.

        `inline=True` when we are already on a worker thread (the turn path); otherwise
        the work is submitted to the pool, because this can make a brain call and the
        MQTT loop must never block on one. A failure here is logged and dropped: a
        summary is a nice-to-have, and a child's session must not end badly for it."""
        # The module is exiting, and the recovered contract is explicit that *"events are
        # automatically unsubscribed when the module exits"* (RemoteModuleAPI
        # §Unsubscribing). The latch is keyed `(device, module)`, which catches a switch
        # A→B but **not** a re-entry A→B→A: the key matches again and we never re-subscribe.
        # Cleared here, before the early return below, because a conversation with no
        # history still ended a module.
        self._forget_robot_state(device_id, vision_only=True)
        robot = robot or self.robots.get(device_id)
        history = list(self.history.get(device_id) or [])
        if robot is None or not history:
            return None
        def _run():
            try:
                self.app_for(device_id).on_session_end(robot, history, reason)
            except Exception as e:
                print(f"[runtime] app.on_session_end error: {e}", flush=True)
        if inline:
            return _run()
        try:
            return self._pool.submit(_run)
        except RuntimeError:                      # pool already shutting down
            return _run()

    def _remember(self, device_id, speech, text):
        """Fold one finished turn into the robot's conversation history."""
        h = self.history.setdefault(device_id, [])
        if speech:
            h.append({"role": "user", "content": speech})
        h.append({"role": "assistant", "content": text})
        self._save_memory(device_id)

    def _ingest_notify(self, device_id, rcr):
        h = self.history.setdefault(device_id, [])
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                h.append({"role": "user", "content": ln["text"]})
        if rcr.get("speech"):
            spoken = "\n".join(l for l in rcr["speech"].splitlines()
                               if not l.startswith(("animation:", "silent:")))
            if spoken.strip():
                h.append({"role": "assistant", "content": spoken.strip()})
        self._save_memory(device_id)
