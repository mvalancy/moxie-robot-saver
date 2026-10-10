"""Conversation transcript + long-term memory: persistence, the privacy gate, parent read/erase,
and the robot's notify reconciled with the turn it reports (never a second copy of it)."""
from __future__ import annotations
import json, os, re, threading

from moxie_sdk.types import ActionType
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.cloud_config import LoggingPolicy
from moxie_sdk.filler import FILLERS
from .constants import MEMORY_POLICY

#: Guards each robot's turn records and its history list between the MQTT thread (a prompt
#: starting a turn, a notify arriving) and the worker that finishes the turn (`_remember`).
#: Module-level, as `turns._OPEN_TURNS_LOCK` is: one lock per process, held for list work.
_NOTIFY_LOCK = threading.Lock()

#: How many answered turns are kept for the robot's notify to be reconciled against: the
#: child's speech windows answered as separate turns and reported together, a report that
#: lands after the child's next prompt. A report retires every turn older than the one it
#: names, so the records run from the last turn the robot reported, and a robot that never
#: notifies keeps this many and no more (a record is at most this many turns old).
UNREPORTED_TURNS = 8

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


def _best_run(hay: list, needle: list, covered: set) -> tuple:
    """Where `needle` fits `hay` best as a contiguous run, and how well: `(2, run)` at the
    first run no word of which is in `covered`, else `(1, run)` at the first run part of
    which is, else `(0, run)` at the first run (every word reported: a repeat), else
    `(-1, None)`. So an answer that repeats a sentence ("No. No. No.") reported one chunk
    at a time is covered chunk by chunk, and a report that starts over inside words
    already reported fits a turn where none of them are better."""
    best = (-1, None)
    at = _find(hay, needle)
    while at >= 0:
        run = range(at, at + len(needle))
        fresh = sum(i not in covered for i in run)
        fit = 2 if fresh == len(needle) else 1 if fresh else 0
        if fit > best[0]:
            best = (fit, run)
            if fit == 2:
                break
        at = _find(hay, needle, at + 1)
    return best


def _words(toks) -> str:
    return " ".join(word for word, _key in toks)


#: The lines the runtime speaks around an answer and never writes in history itself: the
#: fillers (`turns._say_filler`). The queued hello is per turn (`_TurnRecord.extras`).
_FILLER_KEYS = tuple(_keys(text) for text, _markup in FILLERS)


def _notify_report(rcr) -> tuple:
    """`(child lines, what Moxie said)` from a notify: `extra_lines[].text` with
    `context_type == "input"`, and `speech` minus its `animation:` / `silent:` lines. A
    shape the proto cannot carry (`RemoteChat.proto`: `speech` a string, `extra_lines`
    contexts whose `text` is a string) raises ValueError. Checked before a report is held,
    so a bad one is dropped on arrival and never meets the turn it would be held for."""
    lines = rcr.get("extra_lines") or []
    speech = rcr.get("speech") or ""
    if not isinstance(lines, list) or not isinstance(speech, str):
        raise ValueError("extra_lines must be a list and speech a string")
    inputs = []
    for ln in lines:
        if not isinstance(ln, dict) or not isinstance(ln.get("text") or "", str):
            raise ValueError("each extra_line must be an object whose text is a string")
        if ln.get("context_type") == "input" and ln.get("text"):
            inputs.append(ln["text"])
    said = " ".join(line for line in speech.splitlines()
                    if not line.startswith(("animation:", "silent:")))
    return inputs, said


class _TurnRecord:
    """One turn as the notify reconcile sees it (`MemoryMixin`, "the robot's notify").
    Open from the prompt until `_remember` completes it with what Moxie was told to say; a
    notify that arrives while it is open is held for that moment. Completed, it is kept
    until the robot reports a later turn, or `UNREPORTED_TURNS` newer turns exist."""
    __slots__ = ("child", "extras", "text", "words", "keys", "lead", "covered", "said",
                 "entry", "windows", "held")

    def __init__(self, child: str, extras=()):
        self.child = _keys(child)                 # the child's line, as compared
        self.extras = [k for k in (_keys(e) for e in extras) if k]   # the queued hello
        self.text = None                          # what Moxie was told to say; None = open
        self.words: list = []                     # its words, for a cut-off entry
        self.keys: list = []
        self.lead = 0                             # how many keys of a hello open `text`
        self.covered: set = set()                 # key indices the robot has reported
        self.said = None                          # the history dict holding the child's line
        self.entry = None                         # the history dict holding `text`
        self.windows: list = []                   # the child's other windows joined into `said`
        self.held: list = []                      # reports that arrived while open

    @property
    def open(self) -> bool:
        return self.text is None

    @property
    def reported(self) -> bool:
        """Every word of the text has been reported by the robot."""
        return bool(self.keys) and len(self.covered) >= len(self.keys)

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
    # `command: "notify"`, the child's line(s) in `extra_lines[]` (`context_type: "input"`)
    # and Moxie's words in `speech` (mqtt-and-conversation.md §4.2; OpenMoxie
    # conversations.py:59-68 reads the same two fields, and moxie_remote_chat.py:8-11 says
    # this is what keeps its context right when the child speaks in several windows before
    # hearing a response). The runtime ALSO writes each turn when it answers (`_remember`):
    # the only writer a robot that never notifies has (the browser SIM; OpenMoxie's
    # `_auto_history`, conversations.py:128-130, :187-197). Both wrote, so a robot's every
    # exchange was held twice. Now each turn has a record (`_TurnRecord`): the child's
    # line, the hello queued for it and, once `_remember` runs, what Moxie was told to say.
    # The records from the last turn the robot reported onward are kept, oldest first
    # (`UNREPORTED_TURNS` at most), and a notify is reconciled against them:
    #  * a child line equal to one of those turns' (case and punctuation aside) is already
    #    held; it names the turn whose text the report's words fit best (`_best_run`: a
    #    cut-off answer said again whole for the same line starts over inside words
    #    already reported, so it names the next such turn, where none are), among equals
    #    the oldest the robot has not reported in full (reports arrive in order);
    #  * a child line no turn of ours answered (the earlier of several speech windows:
    #    `_on_remote_chat` answers the last) is joined into the child's line of the turn
    #    it was reported with, in the order reported, as OpenMoxie's `add_history` joins
    #    consecutive child lines (conversations.py:29-39); with no such turn, appended;
    #  * fillers, and a hello the runtime spoke as chunk 0, are never history (they were
    #    not before either); an answer that happens to say a filler's words is the turn's
    #    text and is matched as such;
    #  * a report that is a turn's text, or a run inside it (one streamed chunk, "Rock
    #    and" after the child cut in), marks those words reported, on the turn it fits
    #    best (the oldest holding them all unreported, else the oldest holding some, else
    #    a repeat); the entry becomes what the robot says it got through only on a clean
    #    cut (a prefix, nothing after it), never on a tail piece alone or a report with a
    #    hole, so a line the robot may still report is never dropped and per-chunk reports
    #    in any order re-assemble the text; a report holding a turn's whole text with
    #    words around it marks the text and treats the rest the same way (another turn's
    #    answer is matched, a line of the robot's own is appended);
    #  * anything else (a module's own line) is appended, consecutive same-role reports
    #    joined as `add_history` joins them, never into a turn's own entry.
    # The robot speaks in order, so a report that names a turn (by the child's line or by
    # Moxie's words) means every older turn is over, reported in full, in part (the child
    # cut in) or never (a reply the robot skipped): all of them are dropped. A turn is thus
    # matched only until the robot reports the next one, a line the child really says
    # twice is two lines, and a cut-off turn keeps its cut whatever is said later. A
    # notify that arrives while the turn is open (the robot speaks chunk 0 before the
    # stream closes) is held and reconciled when `_remember` runs; if that turn never
    # closes (superseded, its worker died) nothing of it reached history, so what the
    # robot reported is kept as reported when the next turn starts.
    # A report the proto cannot carry is dropped on arrival (`_notify_report`), one the
    # reconcile cannot handle is dropped alone (`_reconcile_reports`): the turn's own lines
    # are written whatever the robot sent. Which cadence an 803 robot uses, one notify per
    # utterance or per event, is not captured (§4.2 says each utterance); the rule holds
    # for both. `animation:` / `silent:` lines are still dropped; one `_save_memory` per
    # notify, as before.

    def _turn_records(self) -> dict:
        """`{device_id: [_TurnRecord, …]}`: the turns answered from the last one the robot
        reported onward, oldest first, the turn in flight (open) last. Created on first
        use (held under `_NOTIFY_LOCK` by every caller)."""
        return self.__dict__.setdefault("_notify_records", {})

    def _notify_tails(self) -> dict:
        """`{device_id: entry}`: the history entry the last unmatched report appended, the
        one a consecutive same-role report joins. Cleared by the turn's own lines."""
        return self.__dict__.setdefault("_notify_tail_entries", {})

    def _start_turn_record(self, device_id, speech):
        """A turn starts (`_on_remote_chat`): open its record, with the hello queued to ride
        out as its chunk 0 (`_speak_opener`; one dict read, so no `_presence_lock`). A
        previous turn still open with held reports never reached history: what the robot
        reported about it is kept as reported (its own hello aside), before this turn's
        lines, each report on its own."""
        with _NOTIFY_LOCK:
            recs = self._turn_records().setdefault(device_id, [])
            hello = self._pending_opener.get(device_id)
            old = recs[-1] if recs and recs[-1].open else None
            changed = False
            if old is not None:
                old.complete("")                  # void: nothing of it is in history
                if old.held:
                    h = self.history.setdefault(device_id, [])
                    changed = self._reconcile_reports(device_id, old.held, h, recs)
                recs.remove(old)
            recs.append(_TurnRecord(speech, [hello] if hello else ()))
            del recs[:-UNREPORTED_TURNS]
            if changed:
                self._save_memory(device_id)

    def _remember(self, device_id, speech, text):
        """Fold one finished turn into the robot's conversation history, completing its
        record with `text` (what Moxie was told to say). Reports held while the turn was
        open are reconciled first, in arrival order, each on its own. A turn with no record
        (a direct call) gets one here, so the notify that follows still reconciles.

        The open record is this turn's when its child line is `speech` (the safety gate
        remembers its redirect with no child line: that is this turn too). A record for a
        newer turn, which a superseded worker meets when the child's next prompt lands
        between its stale check and this call, is left open for that turn: this turn gets
        a completed record before it, and the newer turn's reports stay held for it."""
        with _NOTIFY_LOCK:
            h = self.history.setdefault(device_id, [])
            recs = self._turn_records().setdefault(device_id, [])
            rec = recs[-1] if recs and recs[-1].open else None
            if rec is not None and speech and _keys(speech) != rec.child:
                rec = _TurnRecord(speech)         # a newer turn's: ours goes before it
                recs.insert(len(recs) - 1, rec)
            elif rec is None:
                rec = _TurnRecord(speech)
                recs.append(rec)
            rec.complete(text)
            if speech:
                rec.said = {"role": "user", "content": speech}
            rec.entry = {"role": "assistant", "content": text}
            held, rec.held = rec.held, []
            self._reconcile_reports(device_id, held, h, recs)
            if rec.said is not None:
                h.append(rec.said)
            h.append(rec.entry)
            del recs[:-UNREPORTED_TURNS]
            self._save_memory(device_id)

    def _ingest_notify(self, device_id, rcr):
        """The robot's report of what was said (`command: "notify"`): reconciled with the
        turns it may report, or held until the open turn completes. A report the proto
        cannot carry is dropped here, with one line, as `_on_message` drops one bad
        message: it never reaches a turn."""
        try:
            report = _notify_report(rcr)
        except ValueError as e:
            print(f"[runtime] dropped a malformed notify from {device_id}: {e}", flush=True)
            self._note("chat", "dropped a malformed notify")
            return
        with _NOTIFY_LOCK:
            h = self.history.setdefault(device_id, [])
            recs = self._turn_records().setdefault(device_id, [])
            if recs and recs[-1].open:
                recs[-1].held.append(report)
                return
            self._reconcile_reports(device_id, [report], h, recs)
            self._save_memory(device_id)

    def _reconcile_reports(self, device_id, reports, h, recs) -> bool:
        """Each report in arrival order, each on its own: one the reconcile cannot handle
        is logged and dropped, never the turn whose lines follow. True when `h` changed."""
        changed = False
        for report in reports:
            try:
                changed |= self._reconcile_notify(device_id, report, h, recs)
            except Exception as e:                # noqa: BLE001 — a bad report fails alone
                print(f"[runtime] dropped a notify on {device_id} "
                      f"({self._masked(f'{type(e).__name__}: {e}', 160)})", flush=True)
        return changed

    def _reconcile_notify(self, device_id, report, h, recs) -> bool:
        """Fold one report `(child lines, what Moxie said)` into `h` against `recs`, the
        turns from the last one the robot reported onward (an open one, last, is never
        matched: nothing of it is in history). True when history changed."""
        inputs, said = report
        done = [r for r in recs if not r.open]
        toks = _tokens(said)
        extras = self._extras_of(done)
        matched: list = []                        # the turns this report names
        changed = False
        windows: list = []                        # child lines no turn of ours answered
        anchor = None                             # the turn the last child line named
        for text in inputs:
            keys = _keys(text)
            rec = self._turn_of(keys, done, toks, extras)
            if rec is None:
                if not any(keys in r.windows for r in done):
                    windows.append(text)          # else already joined (reported again)
                continue
            matched.append(rec)
            anchor = rec
            if windows:                           # said before the line that names `rec`
                changed |= self._join_windows(device_id, h, rec, windows, before=True)
                windows = []
        if windows:
            changed |= self._join_windows(device_id, h, anchor, windows, before=False)
        if toks:
            changed |= self._reconcile_speech(device_id, h, toks, done, matched)
        if matched:                               # the robot speaks in order: every turn
            del recs[:max(recs.index(r) for r in matched)]     # older than the one it
        return changed                            # names is over, however much it reported

    @staticmethod
    def _turn_of(keys, done, toks, extras):
        """The turn that answered this child line. Of the records with that line (only
        turns whose lines reached history: never a superseded turn, whose record is void),
        the one the report's words fit best (`_fit`): a cut-off answer said again whole for
        the same line starts over inside words already reported, so it names the next such
        turn, where none are, not the cut one. Among equals, the oldest the robot has not
        reported in full (reports arrive in order), else the oldest."""
        hits = [r for r in done if r.entry is not None and keys == r.child]
        if len(hits) > 1 and toks:
            fits = [MemoryMixin._fit(r, toks, extras)[0] for r in hits]
            hits = [r for r, fit in zip(hits, fits) if fit == max(fits)]
        return next((r for r in hits if not r.reported), hits[0] if hits else None)

    def _join_windows(self, device_id, h, rec, windows, before) -> bool:
        """Child lines the robot reported that no turn of ours answered (the earlier of
        several speech windows; `_on_remote_chat` answers the last): joined into the child's
        line of the turn they were reported with, in the order reported, as OpenMoxie's
        `add_history` joins consecutive child lines (conversations.py:29-39). With no such
        turn (a module's own conversation, a turn that kept no child line) they are
        appended as reported."""
        if rec is None or rec.said is None:
            changed = False
            for text in windows:
                changed |= self._append_reported(device_id, h, "user", text)
            return changed
        rec.windows += [_keys(w) for w in windows]
        joined = " ".join(windows)
        rec.said["content"] = (f"{joined} {rec.said['content']}" if before
                               else f"{rec.said['content']} {joined}")
        return True

    def _reconcile_speech(self, device_id, h, toks, done, matched) -> bool:
        """What the robot says Moxie said, against the turns it has not reported yet."""
        extras = self._extras_of(done)
        # The text of one turn, or a run inside it (a streamed chunk; the child cut in).
        rec = self._cover_run(toks, extras, done)
        if rec is not None:
            matched.append(rec)
            return self._apply_coverage(rec)
        if not self._without_extras(toks, extras):
            return False                          # a filler, a hello: never history
        # One turn's whole text with words around it: the text is marked, the rest is
        # reconciled on its own against the other turns (a filler there is the runtime's).
        # The turn least reported first (as `_fit` ranks a run): a report that starts
        # over is the next turn's, not a cut-off one's.
        for rec in sorted(done, key=lambda r: 2 if r.reported else 1 if r.covered else 0):
            if not rec.keys:
                continue
            kept = self._without_extras(toks, extras, rec.keys)
            at = _find([key for _word, key in kept], rec.keys)
            if at < 0:
                continue
            rec.covered.update(range(len(rec.keys)))
            matched.append(rec)
            changed = self._apply_coverage(rec)
            rest = [r for r in done if r is not rec]
            for piece in (kept[:at], kept[at + len(rec.keys):]):
                piece = self._without_extras(piece, extras)
                if piece:
                    changed |= self._reconcile_speech(device_id, h, piece, rest, matched)
            return changed
        # A line the runtime never sent.
        return self._append_reported(device_id, h, "assistant",
                                     _words(self._without_extras(toks, extras)))

    @staticmethod
    def _cover_run(toks, extras, done):
        """The turn whose text holds the report (minus fillers and that turn's hello) as
        a contiguous run, the one it fits best (`_fit`): the oldest with a run no word of
        which the robot has reported, else the oldest with a run it has reported part of,
        else the oldest with any run (a repeat report). Marks the run reported; None if
        no turn holds it."""
        best = None
        for rec in done:
            fit, run = MemoryMixin._fit(rec, toks, extras)
            if run is not None and (best is None or fit > best[0]):
                best = (fit, rec, run)
                if fit == 2:
                    break
        if best is None:
            return None
        _fit, rec, run = best
        rec.covered.update(run)
        return rec

    @staticmethod
    def _fit(rec, toks, extras) -> tuple:
        """How the report's words (minus the extras this turn's text does not hold) fit
        this turn's text, as `_best_run` ranks it, and the run they fit: `(-1, None)` when
        the turn has no text or they are no run in it."""
        if not rec.keys:
            return (-1, None)
        keys = [key for _word, key in MemoryMixin._without_extras(toks, extras, rec.keys)]
        if not keys:
            return (-1, None)
        return _best_run(rec.keys, keys, rec.covered)

    @staticmethod
    def _extras_of(done) -> list:
        """The runs that are never history: the fillers, and the hello queued for any of
        these turns (`_TurnRecord.extras`)."""
        return list(_FILLER_KEYS) + [e for rec in done for e in rec.extras]

    @staticmethod
    def _without_extras(toks, extras, text_keys=()):
        """`toks` minus every run in `extras`, except a run that `text_keys` holds (the
        hello opening a streamed answer; an answer that happens to say a filler's words):
        that is the turn's text and is matched as such. With no `text_keys` (the words
        beside a matched text, a line of the robot's own) every such run goes: a filler
        there is the runtime's."""
        if text_keys:
            extras = [e for e in extras if _find(text_keys, e) < 0]
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
        """Append a reported line no turn holds, joining it to the previous reported line
        of the same role (OpenMoxie conversations.py:33-35)."""
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
