"""Conversation transcript + long-term memory: persistence, the privacy gate, parent read/erase."""
from __future__ import annotations
import json, os

from moxie_sdk.types import ActionType
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.cloud_config import LoggingPolicy
from .constants import MEMORY_POLICY


class MemoryMixin:
    # ---- conversation transcript (survives restarts) ----
    # The rolling transcript — the child's own words — written under MOXIE_MEMORY_DIR
    # (on by default in both compose files). Persistence is gated on `memory_policy`, the
    # same switch as `MemoryStore`:
    #  1. NO_MEDIA (default) and FULL persist it; only NO_DATA stops it. A transcript has
    #     no opaque media payload to withhold, so the choice is binary.
    #  2. Flipping to NO_DATA deletes the existing file (at boot, on config edits, and on
    #     the write path): `_load_memory` would otherwise feed it into the next prompt.
    #  3. In-RAM history is not gated — without it Moxie forgets the last sentence.

    def _memory_path(self, device_id: str) -> str:
        safe = "".join(c for c in device_id if c.isalnum() or c in "-_")
        return os.path.join(self._memory_dir, f"{safe}.json")

    def transcript_persists(self, device_id) -> bool:
        """False under NO_DATA: the transcript is never written and existing copies are
        removed. Resolved per call from the effective config (no restart needed)."""
        return self.memory_policy(device_id) != LoggingPolicy.NO_DATA

    def _unlink(self, path: str) -> bool:
        """Remove one file; True if it was there. Best effort (runs on the MQTT thread)."""
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            print(f"[runtime] transcript erase failed ({path}): {e}", flush=True)
            return False

    def _forget_transcript(self, device_id: str) -> bool:
        """Delete this robot's on-disk transcript (+ any `.tmp`). Never policy-gated."""
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
        """Remove every stored transcript whose robot is now under NO_DATA (at startup
        and after config edits — for connected and offline robots alike)."""
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
        """Restore per-device history from disk. The NO_DATA sweep runs first so a
        durable fleet rule is honoured across a restart."""
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
        """Persist one robot's (trimmed) history, or under NO_DATA write nothing and
        remove what exists. `self.history` is untouched either way."""
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
    # Durable facts a content module keeps between conversations (content-module-contract.md
    # `volley.persist_data` / `session.summarize()`, `moxie_sdk/memory_store.py::MemoryStore`).
    # The app owns the store; the runtime owns the parent's privacy switch and the moment a
    # conversation ends. `/memory` lets a parent read, erase or correct it (audit BEYOND #4).

    def memory_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy for what may be remembered about this child: the effective
        (fleet + per-robot) `logging_policy`, else `MEMORY_POLICY`. NO_DATA = no writes."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return MEMORY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return MEMORY_POLICY

    def _wire_memory_policy(self, app=None):
        """Hand an app's memory store this runtime's per-device privacy gate. Done late
        so `config.build_app()` apps and every per-child brain `app_for` builds obey it."""
        mem = getattr(app if app is not None else self.app, "memory", None)
        if mem is not None and getattr(mem, "policy", None) is None:
            try:
                mem.policy = self.memory_policy
            except Exception:
                pass

    def memory_store(self):
        """The app's memory store, or a read-only view of the same files (so `/memory`
        answers for any app)."""
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
        """Forget one item, one namespace, or everything for this robot. Never
        policy-gated."""
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
        """Correct one remembered item in place. The store pins it and re-runs the safety
        classifier and the no-verbatim check against recent history; a refusal raises
        (the handler answers 400). Not policy-gated."""
        self.memory_store().edit_item(
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

        `inline=True` on a worker thread (the turn path); otherwise submitted to the pool
        so the MQTT loop never blocks on a brain call. Failures are logged and dropped.
        """
        # Module exit ends its event subscriptions (RemoteModuleAPI), so drop the vision
        # latch — before the early return: a history-less conversation still ended a module.
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
