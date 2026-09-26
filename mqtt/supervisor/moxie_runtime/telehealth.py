"""Telehealth / "Be Moxie": an operator drives the robot's body."""
from __future__ import annotations
import time

from moxie_sdk import safety as safety_seam
from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import vocab as vocab_seam
from markup import make_markup


class TelehealthMixin:
    # ---- 🎭 telehealth / "Be Moxie": the operator drives the body (audit ADOPT #7) ----
    #
    # A remote human replaces the robot's brain and says the lines themselves. The whole
    # protocol is recovered (`moxie_sdk/telehealth.py` carries the citations); what lives
    # here is the six verbs, the state the console polls, and the one gate that keeps two
    # voices out of one mouth.
    #
    # THE SHAPE OF A SESSION (telehealth.md:66-79):
    #     enable  → moxie_mode:"TELEHEALTH" in this robot's /config  (ASSUMPTION B1)
    #     start   → START_SESSION, a session_id is minted
    #     speak*  → safety(MOXIE) → automarkup → PLAY_OUTPUT (+ commands/tts for the SIM)
    #     end     → END_SESSION, the session_id is cleared
    # and the robot reports READY → IN_SESSION → EXITING → READY on the activity log.
    #
    # EVERY verb refuses a device that is not on the permit list. A *pending* robot is by
    # definition one we have not identified; puppeting it would be the pairing gate's
    # exact failure mode with a microphone attached.

    def _th(self, device_id) -> dict:
        """This robot's live telehealth state — created on first use.

        Runtime-level rather than on `RobotContext.extra` on purpose: `_device_disconnect`
        pops the robot, and an operator whose robot just dropped off Wi-Fi should still see
        the transcript of what was said and the last state the robot reported, not an empty
        card. In memory, bounded, never written through `store.py` (`backlog/telehealth.md`
        R6)."""
        from collections import deque
        st = self._telehealth.get(device_id)
        if st is None:
            st = {"session_id": "", "state": "", "state_at": None, "lines": 0,
                  "transcript": deque(maxlen=telehealth_seam.TRANSCRIPT_MAX)}
            self._telehealth[device_id] = st
        return st

    def telehealth_enabled(self, device_id) -> bool:
        """True when this robot's *effective* config puts it in TELEHEALTH mode.

        Read off `effective_config` rather than a flag of our own, so the card can never
        disagree with the document that actually went down the wire."""
        try:
            mode = (self.effective_config(device_id) or {}).get(
                telehealth_seam.MOXIE_MODE_KEY)
        except Exception:
            return False
        try:
            return int(mode) == telehealth_seam.TELEHEALTH_MOXIE_MODE
        except (TypeError, ValueError):
            return str(mode).upper() == "TELEHEALTH"

    def _telehealth_guard(self, device_id, *, need_mode: bool = False):
        """`None` when the call may proceed, else the refusal the console renders."""
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted. Let it in on the "
                              "Robot access card first."}
        if device_id not in self.robots:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected."}
        if need_mode and not self.telehealth_enabled(device_id):
            # Publishing PLAY_OUTPUT at a robot still running its own brain would put two
            # voices in one mouth (`backlog/telehealth.md` R1/R2).
            return {"ok": False, "device_id": device_id, "error": "not in telehealth mode",
                    "reason": "Turn on Be Moxie first."}
        return None

    def _telehealth_publish(self, device_id, command: dict):
        """Publish one `TelehealthRobotCommand` on `commands/telehealth`."""
        self._publish(telehealth_seam.telehealth_topic(device_id), command,
                      device_id=device_id, what="telehealth")
        return command

    def _telehealth_note(self, device_id, who: str, text: str):
        """Append one line to this robot's transcript ring.

        A **child** line is subject to the same `LoggingPolicy` check the safety journal
        uses: under `NO_DATA` the ring keeps operator lines only, because a transcript of
        a child's words is exactly the thing that gate exists to withhold. Operator lines
        are always kept — they are the record of what a third party said to a child, and
        that record is the mitigation for this feature's inherent risk (R3)."""
        if who == telehealth_seam.CHILD and not self._safety_keeps_rows(device_id):
            return None
        entry = telehealth_seam.transcript_entry(who, text)
        self._th(device_id)["transcript"].append(entry)
        return entry

    def telehealth_enable(self, device_id, on: bool = True) -> dict:
        """Turn puppet mode on or off for one robot.

        **ASSUMPTION B1**: this writes `moxie_mode` into the robot's own override layer and
        re-pushes `/config` through the ordinary `update_config` path — so the fleet⊕robot
        merge, the console's layer labels and every existing config test apply unchanged,
        and `sanitize_config_overrides` (which does *not* whitelist `moxie_mode`) still
        makes it impossible for the ⚙️ form's "Apply to all robots" to put a whole fleet
        into puppet mode.

        Turning it **off** ends an open session first: leaving a brain-less robot holding a
        session nobody will send lines to is the one state worse than either end."""
        refusal = self._telehealth_guard(device_id)
        if refusal:
            return refusal
        on = bool(on)
        if not on and self._th(device_id)["session_id"]:
            self.telehealth_session(device_id, "END_SESSION")
        mode = (telehealth_seam.TELEHEALTH_MOXIE_MODE if on
                else telehealth_seam.DEFAULT_MOXIE_MODE)
        self.update_config(device_id, **{telehealth_seam.MOXIE_MODE_KEY: mode})
        self._note("telehealth",
                   f"🎭 Be Moxie {'ON' if on else 'off'} for {device_id}")
        print(f"[runtime] 🎭 telehealth {'enabled' if on else 'disabled'} on {device_id}",
              flush=True)
        return self.telehealth_view(device_id)

    def telehealth_session(self, device_id, action: str) -> dict:
        """`START_SESSION` / `END_SESSION` / `UPDATE_STATE`.

        A start mints a `session_id` and remembers it; an end clears it. `UPDATE_STATE` is
        the "tell me what you are" poke — it changes nothing here and simply asks the robot
        to report on the activity log."""
        name = str(action or "").strip().upper()
        if name not in ("START_SESSION", "END_SESSION", "UPDATE_STATE"):
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown session action {action!r}",
                    "reason": "Expected START_SESSION, END_SESSION or UPDATE_STATE."}
        refusal = self._telehealth_guard(device_id, need_mode=(name == "START_SESSION"))
        if refusal:
            return refusal
        st = self._th(device_id)
        if name == "START_SESSION":
            st["session_id"] = telehealth_seam.new_session_id()
            st["lines"] = 0
        session_id = st["session_id"]
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            name, session_id=session_id))
        if name == "END_SESSION":
            st["session_id"] = ""
        self._note("telehealth", f"🎭 {name.lower().replace('_', ' ')} "
                                 f"{session_id or '(no session)'}")
        return self.telehealth_view(device_id)

    def telehealth_speak(self, device_id, text, *, mood=None, intensity=None,
                         gesture=None) -> dict:
        """The hot path: an operator's line becomes something the robot says.

        Order — permit, mode, **safety**, markup, publish, voice, journal — is
        `backlog/telehealth.md` §2.3 and the order matters:

        **The operator's text IS checked**, as `role=MOXIE`, by the same classifier that
        guards the brain's own output. The `MOXIE` role is defined as *text about to be
        spoken to a child*; the author is not part of that definition and the child cannot
        tell the difference. Telehealth's whole premise is that the operator is a third
        party, and a channel that skipped the parent's journal would make that journal a
        lie.

        **But the handling differs from the brain path, deliberately.** When a model
        produces an unsafe line there is nobody to tell, so the runtime substitutes a
        redirect. Here a human is at the keyboard: a BLOCK is **returned to the operator
        with its reason and nothing is spoken**, so they can rephrase. Substituting a
        redirect for a clinician's sentence would be both useless and dishonest. A FLAG
        passes through and is journaled.
        """
        refusal = self._telehealth_guard(device_id, need_mode=True)
        if refusal:
            return refusal
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "device_id": device_id, "error": "empty line",
                    "reason": "Type something for Moxie to say."}
        try:
            mood = telehealth_seam.validate_mood(mood)
            intensity = telehealth_seam.validate_intensity(intensity)
        except ValueError as e:
            return {"ok": False, "device_id": device_id, "error": str(e),
                    "reason": str(e)}

        verdict = self._assess(line, safety_seam.MOXIE)
        if verdict:
            self._record_safety(device_id, verdict)
            if verdict.action == safety_seam.BLOCK:
                labels = safety_seam.category_labels(self.safety) if self.safety else {}
                named = [str(labels.get(c) or c) for c in verdict.categories]
                return {"ok": False, "device_id": device_id, "error": "blocked",
                        "blocked": True, "categories": list(verdict.categories),
                        "labels": named,
                        "reason": "Moxie will not say that (%s). Nothing was spoken — "
                                  "please rephrase." % (", ".join(named) or "safety")}

        st = self._th(device_id)
        session_id = st["session_id"]
        st["lines"] = n = int(st.get("lines") or 0) + 1
        # One PLAY_OUTPUT per line, always chunk 0 of its own utterance. Telehealth never
        # streams (there is no brain and no token stream — the line is complete when the
        # operator presses send), and the SIM's player requires an utterance's first chunk
        # to be `chunk_num` 0 (`sim/web/audio.js`, the ORDERING/EVENT rules). Numbering the
        # LINE rather than a chunk keeps every line its own utterance and keeps the mood
        # mark — which `annotate` emits on chunk 0 only — on every one of them.
        line_key = f"{session_id or device_id}#{n}"
        markup = make_markup(line, mood_hint=mood, gesture_hint=gesture,
                             intensity=intensity, turn_key=line_key, chunk_index=0)
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "PLAY_OUTPUT", text=line, markup=markup, session_id=session_id))
        # A real robot self-synthesizes from the markup and ignores this; it is how the SIM
        # (and any voice-enabled client) gets an actual voice (mqtt-and-conversation §5.3).
        self._maybe_synthesize(device_id, markup, event_id=line_key, chunk_num=0)
        self._telehealth_note(device_id, telehealth_seam.OPERATOR, line)
        self._note("telehealth", f"🎭 said '{line[:40]}'")
        out = self.telehealth_view(device_id)
        out["spoke"] = line
        out["markup"] = markup
        out["mood"] = mood
        out["intensity"] = intensity
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

    def telehealth_interrupt(self, device_id) -> dict:
        """Cut Moxie off mid-line — barge-in from the operator side.

        **INFERRED (B2).** Our corpus records the verb and that it *"cuts Moxie off
        mid-line"*; what that looks like physically (clean cut, fade, ignored mid-phoneme)
        has never been observed. The message carries **no `output`**, which is the one
        thing the proto makes unambiguous."""
        refusal = self._telehealth_guard(device_id, need_mode=True)
        if refusal:
            return refusal
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "INTERRUPT", session_id=self._th(device_id)["session_id"]))
        self._note("telehealth", "🎭 interrupt")
        return self.telehealth_view(device_id)

    def telehealth_view(self, device_id) -> dict:
        """What the 🎭 card renders: mode, session, the robot's own reported state, whether
        it is inside its bedtime window, and the live transcript.

        `state` is **empty until the robot says otherwise** — the console renders that as
        *"never reported"* rather than inventing `READY`. Whether a robot reports on this
        subtopic at all is one of the open questions (`backlog/telehealth.md` §6).

        `in_bedtime` is a **warning, not a gate**: we do not know whether the robot
        suppresses a puppet line inside its bedtime window (B4), so the operator is told
        the truth and the line is sent anyway."""
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted.",
                    "enabled": False, "online": device_id in self.robots,
                    "transcript": [], "moods": telehealth_seam.moods(),
                    "max_intensity": vocab_seam.MAX_INTENSITY}
        if device_id not in self.robots and device_id not in self._telehealth:
            # A device we have never seen and hold nothing about — the same shape
            # `safety_view` and `memory_view` return, so the console's proxy 404s.
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected.",
                    "enabled": False, "online": False, "transcript": [],
                    "moods": telehealth_seam.moods(),
                    "max_intensity": vocab_seam.MAX_INTENSITY}
        st = self._th(device_id)
        return {
            "ok": True, "device_id": device_id,
            "enabled": self.telehealth_enabled(device_id),
            "online": device_id in self.robots,
            "session_id": st["session_id"],
            "in_session": bool(st["session_id"]),
            "state": st["state"], "state_at": st["state_at"],
            "in_bedtime": self._in_bedtime(device_id),
            "transcript": list(st["transcript"]),
            "moods": telehealth_seam.moods(),
            "max_intensity": vocab_seam.MAX_INTENSITY,
        }

    def ingest_telehealth_event(self, device_id, payload) -> dict:
        """A `TelehealthRobotEvent` off `client-service-activity-log` (`subtopic:
        "telehealth"`) → this robot's reported state.

        An unrecognised state name is stored verbatim and flagged rather than coerced: a
        robot telling us something new must not be rounded off to something we already
        believe."""
        event = telehealth_seam.parse_telehealth_event(payload)
        st = self._th(device_id)
        if event["state"]:
            st["state"] = event["state"]
            st["state_at"] = event["at"] if event["at"] is not None else time.time()
            flag = "" if event["known"] else " (not a state we know)"
            self._note("telehealth", f"🎭 robot reports {event['state']}{flag}")
        # Adopt a session id we do not have ONLY from a robot that says it is in one — a
        # supervisor restarted mid-session should pick the session back up, but an
        # `EXITING` report arriving after we closed the session must never resurrect it.
        if (event["session_id"] and not st["session_id"]
                and event["state"] == "IN_SESSION"):
            st["session_id"] = event["session_id"]
        return event
