"""💬 Try it: one preview turn through a robot's own brain, published nowhere.

The console's *Try it* card (backlog/content-authoring.md §5.3; OpenMoxie's Interact page,
MIT, is the prior art) asks the SAME app a robot's turn would: the brain `app_for` picks
for that robot, or one the parent names under the same registry and `MOXIE_APP` pin, built
by the same `app_named`, sitting in the module the parent chose. The answer goes through
the same output classifier, redirect and `_stage` as a published one. What a try never
does is the transport half of a turn: no MQTT publish, no filler, no transcript, no
long-term memory, no safety journal, no telemetry.

The turn runs as a robot with NO device id. Every store path a brain takes is keyed by
the device id and does nothing without one (`ContentApp.persist_data` and
`_save_persist_data`, `note_used`, an extension's memory effects and breach records), so a
try reads and writes nothing about any child. The cost of that: a module whose prompt
renders what Moxie remembers sees it empty. The session itself travels in the request
(`history`); the supervisor keeps only a rolling-hour budget and an in-flight count.
"""
from __future__ import annotations
import dataclasses, os, re, threading, time

from moxie_sdk import brains as brain_seam
from moxie_sdk import chat as chat_seam
from moxie_sdk import presence as presence_seam
from moxie_sdk import safety as safety_seam
from moxie_sdk import vocab
# The name rule is shared with the child's name a parent saves (`cloud_config`): the shape,
# NFC, and Moxie's safety table.
from moxie_sdk.cloud_config import (NAME_MAX_CHARS as TRY_MAX_NAME_CHARS,
                                    check_name as _check_name)
from moxie_sdk.types import Reply, ReplyChunk, ResultCode, RobotContext, Turn
from moxie_sdk.wire import encode_action

#: The longest line a child "says" here: the hosted demo's own cap (DEMO_MAX_INPUT_CHARS).
TRY_MAX_CHARS = 500
#: One line of the session the card sends back (a reply is ~200 tokens at most).
TRY_MAX_LINE_CHARS = 2000
#: A request body larger than this is refused unread (413), like an oversized pack.
TRY_MAX_BODY_BYTES = 64 * 1024
#: Tries per rolling hour when `MOXIE_AUTHOR_TRY_BUDGET` is unset or invalid
#: (`mqtt/config.py` declares the knob with the same default).
TRY_BUDGET_DEFAULT = 40
#: Brains that read `robot.module_id`: `content` runs the module, `webhook` forwards it.
MODULE_BRAINS = ("content", "webhook")

#: `kind` -> the HTTP status `status_http` answers with. A refusal names its kind so the
#: card can say what to do; a brain that failed (502/504) still carries the line the
#: child would have heard.
TRY_STATUS = {
    "bad_request": 400, "empty": 400, "too_long": 400, "bad_brain": 400,
    "unknown_module": 400, "unknown_device": 404, "pending": 409, "too_large": 413,
    "budget": 429, "busy": 429, "brain_unavailable": 503,
    "brain_unreachable": 502, "brain_refused": 502, "brain_error": 502, "timeout": 504,
    "internal": 500,
}

_BREAK_RE = re.compile(r"<break\b", re.I)
#: Scrubbed before an error is shown (the console page may be screenshotted; the
#: supervisor log keeps the full text): an endpoint's address and a bearer token always,
#: and — in what an upstream server said — any long key-, hash- or host-shaped run.
_SCRUB = ((re.compile(r"https?://[^\s'\")]+"), "<endpoint>"),
          (re.compile(r"Bearer\s+\S+", re.I), "Bearer …"))
_UPSTREAM_SCRUB = _SCRUB + ((re.compile(r"[A-Za-z0-9_\-*.]{20,}"), "[redacted]"),)


def _refuse(kind: str, reason: str, **extra) -> dict:
    return {"ok": False, "kind": kind, "error": reason, "reason": reason,
            "preview": True, "published": False, **extra}


def _scrub(text, limit: int = 300, rules=_UPSTREAM_SCRUB) -> str:
    out = str(text or "")
    for rx, repl in rules:
        out = rx.sub(repl, out)
    return out[:limit]


def _try_name(raw) -> str:
    """A nickname typed for this try only, or `""`. The child's-name rule (`check_name`):
    plain name characters, since it is read into the prompt (no tags, braces or line
    breaks; a line break folds into a space here), NFC, and nothing Moxie's safety table
    blocks or flags."""
    name = " ".join(str(raw or "").split())
    if not name:
        return ""
    return _check_name(name, refusal=f"A name here is up to {TRY_MAX_NAME_CHARS} letters, "
                                     f"spaces, apostrophes or hyphens.")


def read_markup(markup: str) -> dict:
    """What one staged line asks the body to do, read back out of its markup: the faces
    in order, the arm gestures, the whole-body behaviours, voice styles, icons, sounds and
    pauses, plus every id outside the recovered catalog (`vocab.validate_markup`). Pure."""
    text = markup or ""
    out = {"faces": [], "gestures": [], "behaviours": [], "voice": [], "icons": [],
           "sounds": [], "spurts": [], "pauses": len(_BREAK_RE.findall(text)),
           "unknown": vocab.validate_markup(text)}
    for verb, body in vocab._MARK_RE.findall(text):
        data = vocab._decode(body) if body else None
        if not isinstance(data, dict):
            continue
        if verb == "playback-mood":
            mood = data.get("mood")
            out["faces"].append({"mood": vocab.MOOD_NAME_BY_ID.get(mood, str(mood)),
                                 "intensity": data.get("intensity", 0)})
        elif verb == "behaviour-tree":
            gesture, tree = data.get("eventName") or "", data.get("behaviour") or ""
            if gesture and gesture != "Gesture_None":     # Gesture_None = back to rest
                out["gestures"].append(str(gesture))
            if tree:
                out["behaviours"].append(str(tree))
        elif verb == "icons-v2":
            for i in range(vocab.ICON_SLOTS):
                value = (data.get(f"icon{i}") or {}).get("value")
                if value and value != "Null":
                    out["icons"].append(str(value))
        elif verb == "playaudio" and data.get("SoundToPlay"):
            out["sounds"].append(str(data["SoundToPlay"]))
    out["voice"] = vocab._USEL_RE.findall(text)
    out["spurts"] = vocab._SPURT_RE.findall(text)
    return out


def _action_view(a) -> dict:
    """One action as the card shows it, plus the `RemoteChatAction` entry it would be."""
    return {"type": getattr(a.type, "value", str(a.type)), "module_id": a.module_id or "",
            "content_id": a.content_id or "", "function": a.function or "",
            "wire": encode_action(a)}


def _brain_failure(error, result) -> tuple:
    """`(kind, sentence, detail)` for a brain that did not really answer, or `("", "",
    None)`. `error` is how the try's last model request ended (`chat.last_call_error`);
    the app has already turned it into a line for the child, so this is the only place a
    parent can see why."""
    if result == ResultCode.ERROR_OFFLINE:
        kind = "brain_unreachable"
    elif error is None:
        return "", "", None
    elif chat_seam.is_offline_error(error):
        kind = "brain_unreachable"
    else:
        status = chat_seam._status_code(error)
        refused = (isinstance(error, chat_seam.ModelCallBudgetExceeded)
                   or (isinstance(status, int) and 400 <= status < 500))
        kind = "brain_refused" if refused else "brain_error"
    status = chat_seam._status_code(error) if error is not None else None
    status = status if isinstance(status, int) else None
    detail = {"type": type(error).__name__ if error is not None else "", "status": status,
              "message": _scrub(error) if error is not None else ""}
    if kind == "brain_unreachable":
        return kind, ("Moxie's brain could not be reached (refused or timed out). On a "
                      "robot she would fall back to her own on-device chat."), detail
    if isinstance(error, chat_seam.ModelCallBudgetExceeded):
        why = ("MOXIE_MODEL_CALL_LIMIT is not a positive whole number, so this supervisor "
               "makes no model requests." if "positive integer" in str(error) else
               "This supervisor's model-call cap (MOXIE_MODEL_CALL_LIMIT) is used up.")
    elif status in (401, 403):
        why = (f"The brain's server refused the key (HTTP {status}). Check "
               f"MOXIE_LLM_API_KEY.")
    elif status == 404:
        why = "The brain's server does not know this model (HTTP 404). Check MOXIE_LLM_MODEL."
    elif status == 429:
        why = ("The brain's server is rate-limiting this key, or the key may not use this "
               "model (HTTP 429).")
    elif status is not None:
        why = (f"The brain's server {'rejected' if status < 500 else 'failed on'} the "
               f"request (HTTP {status}).")
    else:
        why = f"The brain failed ({type(error).__name__})."
    return kind, why + " Moxie would have covered it with a stock line.", detail


class _Witness:
    """`app` as `_safe_respond` sees it, keeping what its `respond` raised in `box`: the
    robot's own fallback answers the child, and the parent still learns why."""

    def __init__(self, app, box: dict):
        self._app, self._box = app, box

    def respond(self, turn):
        try:
            return self._app.respond(turn)
        except Exception as e:
            self._box["crash"] = e
            raise


class TryItMixin:
    #: How long a try may take before the card is told so (the robot itself re-prompts
    #: after ~20 s of cloud silence; a failing endpoint is retried with backoff).
    TRY_TIMEOUT_S = 30.0
    #: Tries running at once. A try that outlived its deadline still holds its slot, so a
    #: hung endpoint cannot pile up threads (or spend later, once it recovers).
    TRY_MAX_INFLIGHT = 2

    # ---- the budget: a brake on a forgotten tab, not cost control ----
    @staticmethod
    def try_budget() -> int:
        """Tries per rolling hour (`MOXIE_AUTHOR_TRY_BUDGET`, default 40), read per call.
        A try is charged once if it made a model request (`chat.note_model_call`), never
        per token or retry: spent up front for a brain that needs an endpoint, given back
        when the try turned out to make none (a command, a webhook)."""
        try:
            value = int(os.environ.get("MOXIE_AUTHOR_TRY_BUDGET", "").strip() or 0)
        except ValueError:
            value = 0
        return value if value > 0 else TRY_BUDGET_DEFAULT

    def _try_window(self, now: float) -> int:
        """Drop spends older than an hour; how many are left in the window. Lock held."""
        while self._try_spent and now - self._try_spent[0] >= 3600.0:
            self._try_spent.popleft()
        return len(self._try_spent)

    def _try_spend(self):
        """Take one try from the rolling hour: the tries left after it, or None if spent."""
        now, budget = time.monotonic(), self.try_budget()
        with self._try_lock:
            if self._try_window(now) >= budget:
                return None
            self._try_spent.append(now)
            return budget - len(self._try_spent)

    def _try_refund(self):
        """Give back the newest spend (a try that turned out to cost no model call)."""
        with self._try_lock:
            if self._try_spent:
                self._try_spent.pop()

    def _try_budget_view(self) -> dict:
        now, budget = time.monotonic(), self.try_budget()
        with self._try_lock:
            used = self._try_window(now)
            oldest = self._try_spent[0] if self._try_spent else None
        resets = int(max(0.0, 3600.0 - (now - oldest))) if oldest is not None else 0
        return {"per_hour": budget, "remaining": max(0, budget - used),
                "resets_in_s": resets}

    # ---- whose child, which brain, which module ----
    def _try_robot_refusal(self, device_id: str):
        """The device-command refusal (`_command_refusal`), tagged with its `kind`."""
        refused = self._command_refusal(device_id)
        if refused is None:
            return None
        kind = "unknown_device" if device_id not in self.robots else "pending"
        return {**refused, "kind": kind, "preview": True}

    def _try_conversations(self) -> list:
        """The conversations a try can sit in: the live content brain's own module, so a
        pick always resolves exactly as `ContentApp._active_conversation` would. Built on
        first use like any brain; `[]` when the `content` brain is not offered here."""
        if "content" not in brain_seam.option_ids(
                self._brain_availability()["available"]):
            return []
        app = self.app_named("content")
        module = getattr(app, "module", None) if self._brains.get("content") is app \
            else None
        rows = []
        for c in getattr(module, "conversations", None) or []:
            key = f"{c.module_id}/{c.content_id}"
            rows.append({"key": key, "module_id": c.module_id, "content_id": c.content_id,
                         "name": c.name or key})
        return rows

    def tryit_view(self, device_id: str = "") -> dict:
        """The card's choices for one robot (or for none): who answers by default and
        which layer decided, the brains this appliance offers (pin applied), the
        conversations, the child's name, the limits and the hour's budget. No model call.
        """
        device_id = str(device_id or "").strip()
        if device_id:
            refused = self._try_robot_refusal(device_id)
            if refused:
                return refused
        avail = self._brain_availability()
        resolved = self.brain_for(device_id)
        modules = self._try_conversations()
        robot = self.robots.get(device_id) if device_id else None
        current = ""
        if robot is not None and robot.module_id:
            key = f"{robot.module_id}/{robot.content_id or 'default'}"
            current = key if any(m["key"] == key for m in modules) else ""
        child = robot.child if robot is not None else self.child
        return {
            "ok": True, "preview": True, "device_id": device_id,
            "child": {"nickname": child.nickname,
                      "source": "robot" if robot is not None else "appliance"},
            "brain": {"id": resolved["brain"],
                      "label": brain_seam.describe_brain(resolved["brain"]),
                      "source": resolved["source"], "note": resolved["note"]},
            "brains": [{k: e.get(k) for k in ("id", "label", "group", "blurb")}
                       for e in avail["available"] if isinstance(e, dict)],
            "pin": avail["pin"], "pin_note": avail["pin_note"],
            "modules": modules, "module_brains": list(MODULE_BRAINS),
            "current_module": current,
            "streaming": bool(self.streaming), "safety": self.safety is not None,
            "limits": {"max_chars": TRY_MAX_CHARS, "max_history": self._max_memory,
                       "max_name_chars": TRY_MAX_NAME_CHARS,
                       "timeout_s": self.TRY_TIMEOUT_S},
            "budget": self._try_budget_view(),
        }

    def _try_history(self, raw) -> tuple:
        """`(history, trimmed)`: the session the card sent back, checked, and cut the way
        a robot's own transcript is cut (`MOXIE_MEMORY_TURNS`, oldest out first)."""
        if raw in (None, ""):
            return [], 0
        if not isinstance(raw, list):
            raise ValueError("The session must be a list of {role, content} lines.")
        out = []
        for i, line in enumerate(raw):
            if (not isinstance(line, dict) or line.get("role") not in ("user", "assistant")
                    or not isinstance(line.get("content"), str)):
                raise ValueError(f"Line {i + 1} of the session is not a "
                                 f"{{role: user|assistant, content}} line.")
            if len(line["content"]) > TRY_MAX_LINE_CHARS:
                raise ValueError(f"Line {i + 1} of the session is longer than "
                                 f"{TRY_MAX_LINE_CHARS} characters.")
            out.append({"role": line["role"], "content": line["content"]})
        keep = max(0, int(self._max_memory))
        trimmed = max(0, len(out) - keep)
        return (out[-keep:] if keep else []), trimmed

    def _try_event_id(self) -> str:
        """The turn key the stager seeds its gesture spacing with (`preview-…` alike)."""
        return f"tryit-{int(time.time() * 1000)}"

    # ---- the turn ----
    def tryit_turn(self, body) -> dict:
        """One preview turn: `{speech, history?, device_id?, brain?, module?, nickname?}`
        -> what Moxie would say and do, chunk by chunk, plus the session to send next.

        Never published and never remembered (module doc). Refusals and failures carry a
        `kind` (`TRY_STATUS`) and a sentence; a brain failure also carries the line the
        child would have heard, and does not advance the session.
        """
        started = time.monotonic()
        if not isinstance(body, dict):
            return _refuse("bad_request", "The console sent something that is not a try.")
        speech = str(body.get("speech") or "").strip()
        if not speech:
            return _refuse("empty", "Type something for Moxie to answer.")
        if len(speech) > TRY_MAX_CHARS:
            return _refuse("too_long", f"That line is {len(speech)} characters long; a "
                                       f"line here can be at most {TRY_MAX_CHARS}.")
        try:
            history, trimmed = self._try_history(body.get("history"))
            nickname = _try_name(body.get("nickname"))
        except ValueError as e:
            return _refuse("bad_request", str(e))

        # -- whose child --
        device_id = str(body.get("device_id") or "").strip()
        if device_id:
            refused = self._try_robot_refusal(device_id)
            if refused:
                return refused
        base = self.robots[device_id].child if device_id else self.child
        child = dataclasses.replace(base, nickname=nickname) if nickname else base
        child_source = "typed" if nickname else ("robot" if device_id else "appliance")

        # -- which brain: the robot's, or one named under the same registry and pin --
        try:
            picked = brain_seam.normalize_brain_patch(
                {self.BRAIN_KEY: body.get("brain")}, pin=self.brain_pin())
        except ValueError as e:
            return _refuse("bad_brain", str(e))
        resolved = self.brain_for(device_id)
        name = picked or resolved["brain"]
        label = brain_seam.describe_brain(name)
        app = self.app_named(name)
        if self._brains.get(name) is not app:
            why = self._brain_failed.get(name) or "it could not be built"
            return _refuse("brain_unavailable",
                           f"{label} cannot run on this appliance right now: "
                           f"{_scrub(why, 400, _SCRUB)}")
        brain = {"id": name, "label": label,
                 "source": "picked" if picked else resolved["source"]}

        # -- which activity: an installed conversation, or none --
        module_key = str(body.get("module") or "").strip()
        if module_key.startswith("conversation:"):
            module_key = module_key.split(":", 1)[1]
        conv = None
        if module_key:
            conv = next((m for m in self._try_conversations() if m["key"] == module_key),
                        None)
            if conv is None:
                return _refuse("unknown_module", f"{module_key!r} is not a conversation "
                                                 f"installed on this appliance.")
        notes = []
        if conv is not None and name not in MODULE_BRAINS:
            notes.append(f"{label} does not read the activity, so it answers the same in "
                         f"every one.")
        if trimmed:
            notes.append(f"The session was cut to its last {len(history)} lines, as a "
                         f"robot's own transcript is.")

        # -- the turn, assembled as `_on_remote_chat` assembles one --
        robot = RobotContext(device_id="", child=child,
                             module_id=conv["module_id"] if conv else None,
                             content_id=conv["content_id"] if conv else None,
                             extra={"timezone_id": self.house_zone(device_id).name})
        turn = Turn(robot=robot, speech=speech, history=list(history), command="prompt",
                    input_vars={},
                    presence=presence_seam.snapshot(self._presence_state(robot)))
        event_id = self._try_event_id()
        answer = {"device_id": device_id, "speech": speech, "brain": brain,
                  "module": conv, "notes": notes, "history_trimmed": trimmed,
                  "child": {"nickname": child.nickname, "source": child_source}}

        # -- the child's side of the safety gate: same classifier, nothing journaled --
        safety = []
        said_before = next((h["content"] for h in reversed(history)
                            if h["role"] == "assistant"), "")
        verdict = self._assess(speech, safety_seam.CHILD)
        if verdict:
            safety.append(self._try_verdict(verdict, "input"))
        if verdict and verdict.action == safety_seam.BLOCK:
            red = safety_seam.redirect_for(verdict, last=said_before,
                                           classifier=self.safety)
            chunk = self._try_chunk(Reply(text=red.text, markup=red.markup), event_id, 0,
                                    final=True, solo=True)
            # Only OUR line is remembered: the blocked words never reach the next prompt.
            return self._try_answer(answer, started, ok=True, chunks=[chunk],
                                    delivery="single", safety=safety, calls=0,
                                    history=history + [{"role": "assistant",
                                                        "content": red.text}])

        # -- the brain, on a worker of its own, against a deadline --
        box, refusal = self._try_run(name, app, turn)
        if refusal is not None:
            # The session as it was travels back, so the card can simply send again.
            refusal.update({k: v for k, v in answer.items() if k != "history_trimmed"},
                           history=history,
                           elapsed_ms=int((time.monotonic() - started) * 1000))
            return refusal

        # -- stage what came back, exactly as a published turn is staged --
        chunks, said = self._try_stage(box, event_id, said_before, safety)
        result = chunks[-1]["result"] if chunks else ResultCode.SUCCESS.name
        kind, sentence, detail = "", "", None
        if box.get("crash") is not None:
            crash = box["crash"]
            kind, sentence = "brain_error", (f"The brain raised {type(crash).__name__}. "
                                             f"Moxie would have covered it with a stock "
                                             f"line.")
            detail = {"type": type(crash).__name__, "status": None,
                      "message": _scrub(crash)}
        else:
            kind, sentence, detail = _brain_failure(box.get("error"),
                                                    ResultCode[result])
        ok = not kind
        text = " ".join(t for t in said if t).strip()
        out = self._try_answer(
            answer, started, ok=ok, chunks=chunks, delivery=box.get("delivery", "single"),
            safety=safety, calls=int(box.get("calls") or 0),
            # The session advances only on a real answer: a failure is not remembered,
            # so the same line can simply be sent again.
            history=(history + [{"role": "user", "content": speech},
                                {"role": "assistant", "content": text}]) if ok
            else history)
        if not ok:
            out.update({"kind": kind, "error": sentence, "reason": sentence,
                        "detail": detail})
        return out

    def _try_run(self, name, app, turn) -> tuple:
        """`(box, None)` once the brain answered on a worker of its own (`_try_brain`), or
        `(None, refusal)`: busy, out of budget, or past `TRY_TIMEOUT_S` (a late worker keeps
        its in-flight slot until it ends). A brain needing an endpoint is charged up front
        and refunded when the try made no model request (a command, a webhook)."""
        with self._try_lock:
            busy = self._try_inflight >= self.TRY_MAX_INFLIGHT
            if not busy:
                self._try_inflight += 1
        if busy:
            return None, _refuse("busy", "Moxie's brain is still working on an earlier try. "
                                         "Wait for it to finish, then send again.",
                                 budget=self._try_budget_view())
        costs, handed_off, box = bool(brain_seam.brain_needs(name)), False, {}
        try:
            if costs and self._try_spend() is None:
                return None, _refuse(
                    "budget", f"That is all {self.try_budget()} tries for this hour "
                              f"(MOXIE_AUTHOR_TRY_BUDGET). Each one asks the brain once.",
                    budget=self._try_budget_view())
            worker = threading.Thread(target=self._try_brain, args=(app, turn, box),
                                      name="tryit", daemon=True)
            worker.start()
            handed_off = True
        finally:
            if not handed_off:
                with self._try_lock:
                    self._try_inflight -= 1
        worker.join(self.TRY_TIMEOUT_S)
        if worker.is_alive():
            box["late"] = True
            self._note("tryit", f"💬 try-it gave up after {self.TRY_TIMEOUT_S:g} s ({name})")
            return None, _refuse(
                "timeout", f"Moxie's brain did not finish within {self.TRY_TIMEOUT_S:g} s. "
                           f"A slow or unreachable endpoint is retried with backoff; the "
                           f"supervisor log shows each retry.",
                budget=self._try_budget_view())
        if costs and not box.get("calls"):
            self._try_refund()
        return box, None

    def _try_stage(self, box: dict, event_id: str, said_before: str, safety: list) -> tuple:
        """`(chunks, said)`: every piece that came back, staged as a published turn stages
        it — an unsafe piece first replaced by its redirect, as `_handle_turn` and
        `_handle_stream_turn` replace it. Appends each output verdict to `safety`."""
        chunks, said = [], []
        pieces = box.get("pieces") or []
        for i, (piece, verdict) in enumerate(pieces):
            if verdict:
                safety.append(self._try_verdict(verdict, "output"))
            if verdict and verdict.action == safety_seam.BLOCK:
                red = safety_seam.redirect_for(verdict, last=said_before,
                                               classifier=self.safety)
                piece = (ReplyChunk(text=red.text, markup=red.markup, final=True)
                         if box.get("delivery") == "stream"
                         else Reply(text=red.text, markup=red.markup,
                                    result_code=getattr(piece, "result_code",
                                                        ResultCode.SUCCESS)))
            chunks.append(self._try_chunk(piece, event_id, i, final=i == len(pieces) - 1,
                                          solo=len(pieces) == 1))
            if piece.text:
                said.append(piece.text)
        return chunks, said

    def _try_brain(self, app, turn, box: dict) -> None:
        """The brain half of `_handle_turn` on a fresh thread (so `chat.thread_model_calls`
        and `chat.last_call_error` describe this turn alone): stream when the runtime
        streams and the app can, else one reply; each piece is assessed as it arrives and
        a blocked one ends the stream, as on a robot. Fills `box`; never raises."""
        pieces, delivery = [], "single"
        try:
            stream = None
            if self.streaming:
                try:
                    stream = app.respond_stream(turn)
                except Exception as e:
                    print(f"[runtime] try-it respond_stream error: {e}", flush=True)
            failed = None
            if stream is not None:
                delivery = "stream"
                try:
                    for chunk in stream:
                        verdict = self._assess(chunk.text, safety_seam.MOXIE)
                        pieces.append((chunk, verdict))
                        if getattr(chunk, "final", False) or (
                                verdict and verdict.action == safety_seam.BLOCK):
                            break
                except Exception as e:
                    failed = e
                    print(f"[runtime] try-it stream error: {e}", flush=True)
                finally:
                    close = getattr(stream, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
                if not pieces and failed is not None:
                    stream = None                  # nothing spoken: answer in one piece
                elif pieces and not getattr(pieces[-1][0], "final", False) and not (
                        pieces[-1][1] and pieces[-1][1].action == safety_seam.BLOCK):
                    # A stream with no closing chunk: a robot is sent an empty one.
                    pieces.append((ReplyChunk(text="", final=True), None))
                elif not pieces:
                    pieces.append((ReplyChunk(text="", final=True), None))
            if stream is None:
                delivery = "single"
                # A brain that raises gets the robot's own stock line (`_safe_respond`).
                reply = self._safe_respond(turn, app=_Witness(app, box))
                pieces.append((reply, self._assess(reply.text, safety_seam.MOXIE)))
        except Exception as e:
            box["crash"] = e
            print(f"[runtime] try-it brain error: {type(e).__name__}: {e}", flush=True)
        finally:
            box.update(pieces=pieces, delivery=delivery,
                       calls=chat_seam.thread_model_calls(),
                       error=chat_seam.last_call_error())
            with self._try_lock:
                self._try_inflight -= 1
            if box.get("late"):
                print(f"[runtime] 💬 try-it finished after its deadline "
                      f"({box['calls']} model call(s))", flush=True)

    def _try_chunk(self, piece, event_id: str, index: int, *, final: bool,
                   solo: bool) -> dict:
        """One piece of the answer, staged as `_publish_stream_chunk` / `_handle_turn`
        stage it (`_stage`, same turn key and chunk index), and read back for the card."""
        markup, scored = self._stage(piece.text, piece, turn_key=event_id,
                                     chunk_index=index, markup=piece.markup)
        result = getattr(piece, "result_code", None)
        if result is None:
            result = ResultCode.SUCCESS if final else ResultCode.REPLY_PENDING
        actions = list(getattr(piece, "actions", None) or [])
        return {"index": index, "text": piece.text, "markup": markup or "",
                "final": bool(final), "solo": bool(solo),
                "result": ResultCode(result).name, "scored": dict(scored),
                "perform": read_markup(markup or ""),
                "actions": [_action_view(a) for a in actions],
                "end_turn": bool(getattr(piece, "end_turn", False))}

    def _try_verdict(self, verdict, stage: str) -> dict:
        labels = safety_seam.category_labels(self.safety) if self.safety else {}
        return {"stage": stage, "action": verdict.action,
                "categories": list(verdict.categories),
                "labels": [labels.get(c, c) for c in verdict.categories],
                "escalate": bool(getattr(verdict, "escalate", False))}

    def _try_answer(self, answer: dict, started: float, *, ok: bool, chunks: list,
                    delivery: str, safety: list, calls: int, history: list) -> dict:
        actions = [a for c in chunks for a in c["actions"]]
        elapsed = int((time.monotonic() - started) * 1000)
        brain = answer["brain"]["id"]
        where = f" {answer['module']['key']}" if answer.get("module") else ""
        self._note("tryit", f"💬 try-it {brain}{where}: {calls} model call(s), "
                            f"{elapsed} ms")
        print(f"[runtime] 💬 try-it → {brain}{where}: {calls} model call(s), "
              f"{elapsed} ms (preview, not published)", flush=True)
        return {"ok": ok, "kind": "", "error": None, "reason": None,
                "preview": True, "published": False, **answer,
                "delivery": delivery,
                "result": chunks[-1]["result"] if chunks else ResultCode.SUCCESS.name,
                "reply": {"text": " ".join(c["text"] for c in chunks if c["text"]).strip(),
                          "chunks": chunks, "actions": actions,
                          "end_turn": any(c["end_turn"] for c in chunks)},
                "safety": safety, "history": history, "model_calls": int(calls),
                "elapsed_ms": elapsed, "budget": self._try_budget_view()}
