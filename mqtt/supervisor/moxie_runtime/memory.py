"""Conversation transcript + long-term memory: persistence, the privacy gate, parent read/erase,
and the robot's notify reconciled with the turn it reports (never a second copy of it)."""
from __future__ import annotations
import json, os, re, threading

from moxie_sdk.types import ActionType
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.cloud_config import LoggingPolicy
from moxie_sdk.filler import FILLERS
from .constants import MEMORY_POLICY

#: Guards each robot's turn record and its history list between the MQTT thread (a prompt
#: starting a turn, a notify arriving) and the worker that finishes the turn (`_remember`).
#: Module-level, as `turns._OPEN_TURNS_LOCK` is: one lock per process, held for list work.
_NOTIFY_LOCK = threading.Lock()

_TAG = re.compile(r"<[^>]*>")
_NOT_WORD = re.compile(r"[\W_]+")


def _tokens(text) -> list[tuple[str, str]]:
    """`[(word, key), …]`: the whitespace-split words of `text` that hold a letter or digit,
    each with the key it is compared by (markup tags out, case and punctuation ignored):
    "Rock and dust!" -> [("Rock", "rock"), ("and", "and"), ("dust!", "dust")]."""
    out = []
    for word in _TAG.sub(" ", text or "").split():
        key = _NOT_WORD.sub("", word.casefold())
        if key:
            out.append((word, key))
    return out


def _keys(text) -> list[str]:
    return [key for _word, key in _tokens(text)]


def _find(hay: list, needle: list, start: int = 0) -> int:
    """Index of `needle` as a contiguous run in `hay` at or after `start`, else -1."""
    n = len(needle)
    if not n:
        return -1
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def _find_uncovered(hay: list, needle: list, covered: set) -> int:
    """`_find`, preferring the first run with a word the robot has not reported yet, so an
    answer that repeats a sentence ("No. No. No.") reported one chunk at a time is covered
    chunk by chunk rather than the same run three times."""
    first = at = _find(hay, needle)
    while at >= 0:
        if any(i not in covered for i in range(at, at + len(needle))):
            return at
        at = _find(hay, needle, at + 1)
    return first


def _words(toks) -> str:
    return " ".join(word for word, _key in toks)


#: The lines the runtime speaks around an answer and never writes in history itself: the
#: fillers (`turns._say_filler`). The queued hello is per turn (`_TurnRecord.extras`).
_FILLER_KEYS = tuple(_keys(text) for text, _markup in FILLERS)


class _TurnRecord:
    """One robot's current turn as the notify reconcile sees it (`MemoryMixin`, "the
    robot's notify"). Open from the prompt until `_remember` completes it with what Moxie
    was told to say; a notify that arrives while it is open is held for that moment."""
    __slots__ = ("child", "extras", "text", "words", "keys", "lead", "covered", "entry",
                 "held")

    def __init__(self, child: str, extras=()):
        self.child = _keys(child)                 # the child's line, as compared
        self.extras = [k for k in (_keys(e) for e in extras) if k]   # the queued hello
        self.text = None                          # what Moxie was told to say; None = open
        self.words: list = []                     # its words, for a cut-off entry
        self.keys: list = []
        self.lead = 0                             # how many keys of a hello open `text`
        self.covered: set = set()                 # key indices the robot has reported
        self.entry = None                         # the history dict holding `text`
        self.held: list = []                      # notifies that arrived while open

    @property
    def open(self) -> bool:
        return self.text is None

    def complete(self, text: str):
        self.text = text
        toks = _tokens(text)
        self.words = [word for word, _key in toks]
        self.keys = [key for _word, key in toks]
        self.lead = next((len(e) for e in self.extras if self.keys[:len(e)] == e), 0)


class MemoryMixin:
    # ---- conversation transcript (survives restarts) ----
    # The rolling transcript — the child's own words — written under MOXIE_MEMORY_DIR
    # (on by default in both compose files). Persistence is gated on `memory_policy`, the
    # same switch as `MemoryStore`:
    #  1. NO_MEDIA (default) and FULL persist it; only NO_DATA stops it. A transcript has
    #     no opaque media payload to withhold, so the choice is binary.
    #  2. Flipping to NO_DATA deletes the existing file (at boot, on config edits, and on
    #     the write path): `_load_memory` would otherwise feed it into the next prompt.
    #     Not for a robot that failed closed (fleet.py `failed_closed`): its NO_DATA is a
    #     settings file that could not be read, not a parent's choice, so it writes nothing
    #     and its stored transcript stays as found.
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
        and after config edits — for connected and offline robots alike), except a robot
        that failed closed (`failed_closed`), whose transcript stays as found."""
        if not self._memory_dir or not os.path.isdir(self._memory_dir):
            return 0
        removed = 0
        try:
            names = os.listdir(self._memory_dir)
        except OSError as e:
            print(f"[runtime] transcript sweep failed: {e}", flush=True)
            return 0
        for name in names:
            if (not name.endswith(".json") or self.transcript_persists(name[:-5])
                    or self.failed_closed(name[:-5])):
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
        remove what exists (a robot that failed closed removes nothing). `self.history`
        is untouched either way."""
        if not self._memory_dir:
            return
        if not self.transcript_persists(device_id):
            if not self.failed_closed(device_id):
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
        """End the conversation if the answer carried an EXIT action (`<exit>`) or a SLEEP
        action (`<sleep>`): when Moxie goes to sleep the session is over too."""
        for a in actions or []:
            kind = getattr(a, "type", None)
            if kind in (ActionType.EXIT, ActionType.SLEEP):
                return self._end_conversation(
                    device_id, "exit" if kind == ActionType.EXIT else "sleep", inline=True)
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

    # ---- the robot's notify: the record, not a second copy ----
    # A real Moxie reports what it said after each utterance: a remote-chat request with
    # `command: "notify"`, the child's line in `extra_lines[]` (`context_type: "input"`) and
    # Moxie's words in `speech` (mqtt-and-conversation.md §4.2; OpenMoxie
    # conversations.py:59-68 reads the same two fields). The runtime ALSO writes each turn
    # when it answers (`_remember`): the only writer a robot that never notifies has (the
    # browser SIM; OpenMoxie's `_auto_history`, conversations.py:128-130, :187-197). Both
    # wrote, so a robot's every exchange was held twice. Now each turn has a record
    # (`_TurnRecord`): the child's line, the hello queued for it, and, once `_remember`
    # runs, what Moxie was told to say. A notify is reconciled against it:
    #  * a child line equal to the turn's (case and punctuation aside) is already held;
    #  * fillers, and a hello the runtime spoke as chunk 0, are never history (they were
    #    not before either);
    #  * a report that is the turn's text, or a run inside it (one streamed chunk, "Rock
    #    and" after the child cut in), marks those words reported; the entry becomes what
    #    the robot says it got through only on a clean cut (a prefix, nothing after it),
    #    never on a tail piece alone or a report with a hole, so a line the robot may still
    #    report is never dropped and per-chunk reports in any order re-assemble the text;
    #  * anything else (a module's own line, a child line the turn never heard) is
    #    appended, consecutive same-role reports joined as OpenMoxie's `add_history` does
    #    (conversations.py:29-39), never into the turn's own entry.
    # Only the current turn's record is matched, never the whole tail: a line the child
    # really says twice is two lines. A notify that arrives while the turn is open (the
    # robot speaks chunk 0 before the stream closes) is held and reconciled when
    # `_remember` runs; if that turn never closes (superseded, its worker died) nothing of
    # it reached history, so what the robot reported is kept as reported when the next turn
    # starts. Which cadence an 803 robot uses, one notify per utterance or per event, is not
    # captured (§4.2 says each utterance); the rule holds for both. `animation:` /
    # `silent:` lines are still dropped; one `_save_memory` per notify, as before.

    def _turn_records(self) -> dict:
        """`{device_id: _TurnRecord}`, the turn each robot is on or last finished. Created
        on first use (held under `_NOTIFY_LOCK` by every caller)."""
        return self.__dict__.setdefault("_notify_records", {})

    def _notify_tails(self) -> dict:
        """`{device_id: entry}`: the history entry the last unmatched report appended, the
        one a consecutive same-role report joins. Cleared by the turn's own lines."""
        return self.__dict__.setdefault("_notify_tail_entries", {})

    def _start_turn_record(self, device_id, speech):
        """A turn starts (`_on_remote_chat`): open its record, with the hello queued to ride
        out as its chunk 0 (`_speak_opener`; one dict read, so no `_presence_lock`). A
        previous turn still open with held notifies never reached history: what the robot
        reported about it is kept as reported, before this turn's lines."""
        with _NOTIFY_LOCK:
            records = self._turn_records()
            old = records.get(device_id)
            hello = self._pending_opener.get(device_id)
            records[device_id] = _TurnRecord(speech, [hello] if hello else ())
            if old is not None and old.open and old.held:
                h = self.history.setdefault(device_id, [])
                changed = False
                for rcr in old.held:
                    changed |= self._reconcile_notify(device_id, rcr, h, old)
                if changed:
                    self._save_memory(device_id)

    def _remember(self, device_id, speech, text):
        """Fold one finished turn into the robot's conversation history, completing its
        record with `text` (what Moxie was told to say). Notifies held while the turn was
        open are reconciled first, in arrival order. A turn with no record (a direct call)
        gets one here, so the notify that follows still reconciles.

        The open record is this turn's when its child line is `speech` (the safety gate
        remembers its redirect with no child line: that is this turn too). A record for a
        newer turn, which a superseded worker meets when the child's next prompt lands
        between its stale check and this call, is left open for that turn: the lines are
        written as they always were, and the newer turn's notifies stay held for it."""
        with _NOTIFY_LOCK:
            h = self.history.setdefault(device_id, [])
            records = self._turn_records()
            rec = records.get(device_id)
            if rec is not None and rec.open and speech and _keys(speech) != rec.child:
                rec = None                            # a newer turn's: not ours to complete
            elif rec is None or not rec.open:
                rec = records[device_id] = _TurnRecord(speech)
            if rec is not None:
                rec.complete(text)
                for rcr in rec.held:
                    self._reconcile_notify(device_id, rcr, h, rec)
                rec.held = []
            if speech:
                h.append({"role": "user", "content": speech})
            entry = {"role": "assistant", "content": text}
            h.append(entry)
            self._notify_tails().pop(device_id, None)
            if rec is not None:
                rec.entry = entry
                self._apply_coverage(rec)
            self._save_memory(device_id)

    def _ingest_notify(self, device_id, rcr):
        """The robot's report of what was said (`command: "notify"`): reconciled with the
        turn it reports, or held until that turn completes."""
        with _NOTIFY_LOCK:
            h = self.history.setdefault(device_id, [])
            rec = self._turn_records().get(device_id)
            if rec is not None and rec.open:
                rec.held.append(rcr)
                return
            self._reconcile_notify(device_id, rcr, h, rec)
            self._save_memory(device_id)

    def _reconcile_notify(self, device_id, rcr, h, rec) -> bool:
        """Fold one notify into `h` against `rec`: the turn it may report (completed), a
        turn that never completed (open: nothing of it is in history, so every line is
        kept), or None (no turn). True when history changed."""
        done = rec is not None and not rec.open
        changed = False
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") != "input" or not ln.get("text"):
                continue
            if done and rec.child and _keys(ln["text"]) == rec.child:
                continue                              # the line this turn answered
            changed |= self._append_reported(device_id, h, "user", ln["text"])
        said = " ".join(line for line in (rcr.get("speech") or "").splitlines()
                        if not line.startswith(("animation:", "silent:")))
        toks = self._without_extras(_tokens(said), rec)
        if not toks:
            return changed
        if not done or not rec.keys:
            return self._append_reported(device_id, h, "assistant", _words(toks)) or changed
        keys = [key for _word, key in toks]
        at = _find_uncovered(rec.keys, keys, rec.covered)
        if at >= 0:                                   # the text, or a run inside it
            rec.covered.update(range(at, at + len(keys)))
            return self._apply_coverage(rec) or changed
        at = _find(keys, rec.keys)
        if at >= 0:                                   # the text with more around it
            rec.covered.update(range(len(rec.keys)))
            changed |= self._apply_coverage(rec)
            for run in (toks[:at], toks[at + len(rec.keys):]):
                if run:
                    changed |= self._append_reported(device_id, h, "assistant", _words(run))
            return changed
        return self._append_reported(device_id, h, "assistant", _words(toks)) or changed

    @staticmethod
    def _without_extras(toks, rec):
        """`toks` minus every run that is a filler, or the turn's queued hello, unless that
        hello opens the turn's own text (the streamed path), where it is matched as text."""
        extras = list(_FILLER_KEYS)
        if rec is not None:
            extras += [e for e in rec.extras if _find(rec.keys, e) < 0]
        out, i = [], 0
        while i < len(toks):
            hit = next((len(e) for e in extras
                        if [k for _w, k in toks[i:i + len(e)]] == e), 0)
            if hit:
                i += hit
            else:
                out.append(toks[i])
                i += 1
        return out

    def _append_reported(self, device_id, h, role, text) -> bool:
        """Append a reported line the turn does not hold, joining it to the previous
        reported line of the same role (OpenMoxie conversations.py:33-35)."""
        tails = self._notify_tails()
        tail = tails.get(device_id)
        if tail is not None and h and h[-1] is tail and tail["role"] == role:
            tail["content"] = f"{tail['content']} {text}"
        else:
            tail = tails[device_id] = {"role": role, "content": text}
            h.append(tail)
        return True

    @staticmethod
    def _apply_coverage(rec) -> bool:
        """Make the turn's entry what the robot reports having said: the whole text, or,
        on a clean cut (its reports cover a prefix and nothing after it, past any hello),
        that prefix. A tail piece alone or a report with a hole keeps the whole text."""
        if rec.entry is None:
            return False
        n, p = len(rec.keys), 0
        while p < n and p in rec.covered:
            p += 1
        if 0 < p < n and len(rec.covered) == p and p > rec.lead:
            want = " ".join(rec.words[:p])
        else:
            want = rec.text
        if rec.entry["content"] == want:
            return False
        rec.entry["content"] = want
        return True
