"""Telehealth / "Be Moxie": an operator drives the robot's body."""
from __future__ import annotations
import time

from moxie_sdk import safety as safety_seam
from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import vocab as vocab_seam
from markup import make_markup


class TelehealthMixin:
    # ---- telehealth / "Be Moxie" (audit ADOPT #7, backlog/telehealth.md) ----
    # A remote human replaces the brain and says the lines. Protocol and citations live in
    # `moxie_sdk/telehealth.py`. Session: enable (moxie_mode TELEHEALTH in /config,
    # assumption B1) -> START_SESSION -> PLAY_OUTPUT* -> END_SESSION; the robot reports
    # READY/IN_SESSION/EXITING on the activity log. Every verb refuses a pending robot.

    def _th(self, device_id) -> dict:
        """This robot's live telehealth state, created on first use. Runtime-level (not
        `RobotContext.extra`) so it survives a disconnect; in memory, bounded, never
        persisted (backlog/telehealth.md R6)."""
        from collections import deque
        st = self._telehealth.get(device_id)
        if st is None:
            st = {"session_id": "", "state": "", "state_at": None, "lines": 0,
                  "transcript": deque(maxlen=telehealth_seam.TRANSCRIPT_MAX)}
            self._telehealth[device_id] = st
        return st

    def telehealth_enabled(self, device_id) -> bool:
        """True when this robot's effective config puts it in TELEHEALTH mode (read off the
        document actually pushed, not a flag of our own)."""
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
            # PLAY_OUTPUT at a robot still running its brain = two voices (R1/R2).
            return {"ok": False, "device_id": device_id, "error": "not in telehealth mode",
                    "reason": "Turn on Be Moxie first."}
        return None

    def _telehealth_publish(self, device_id, command: dict):
        """Publish one `TelehealthRobotCommand` on `commands/telehealth`."""
        self._publish(telehealth_seam.telehealth_topic(device_id), command,
                      device_id=device_id, what="telehealth")
        return command

    def _telehealth_note(self, device_id, who: str, text: str):
        """Append one line to this robot's transcript ring. Child lines obey the safety
        journal's LoggingPolicy (dropped under NO_DATA); operator lines are always kept —
        the record of what a third party said to a child (R3)."""
        if who == telehealth_seam.CHILD and not self._safety_keeps_rows(device_id):
            return None
        entry = telehealth_seam.transcript_entry(who, text)
        self._th(device_id)["transcript"].append(entry)
        return entry

    def telehealth_enable(self, device_id, on: bool = True) -> dict:
        """Turn puppet mode on or off for one robot.

        Assumption B1: writes `moxie_mode` into the robot's override layer via
        `update_config` (`sanitize_config_overrides` does not whitelist it, so a fleet-wide
        edit cannot puppet every robot). Turning it off ends an open session first."""
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
        """`START_SESSION` (mints a session_id) / `END_SESSION` (clears it) /
        `UPDATE_STATE` (asks the robot to report its state)."""
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
        """An operator's line becomes something the robot says (telehealth.md §2.3 order:
        permit, mode, safety, markup, publish, voice, journal).

        The line is checked as `role=MOXIE` like the brain's output and journaled. Unlike
        the brain path, a BLOCK is returned to the operator with its reason and nothing is
        spoken (a human can rephrase); a FLAG passes and is journaled.
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
        # One PLAY_OUTPUT per line, always chunk 0 of its own utterance: no streaming, the
        # SIM player needs each utterance to start at chunk 0, and the mood mark rides
        # chunk 0 only.
        line_key = f"{session_id or device_id}#{n}"
        markup = make_markup(line, mood_hint=mood, gesture_hint=gesture,
                             intensity=intensity, turn_key=line_key, chunk_index=0)
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "PLAY_OUTPUT", text=line, markup=markup, session_id=session_id))
        # A real robot self-synthesizes; this gives the SIM a voice (mqtt-and-conversation §5.3).
        self._maybe_synthesize(device_id, markup, event_id=line_key, chunk_num=0)
        self._telehealth_note(device_id, telehealth_seam.OPERATOR, line)
        self._note("telehealth", f"🎭 said '{self._masked(line, 40)}'")
        out = self.telehealth_view(device_id)
        out["spoke"] = line
        out["markup"] = markup
        out["mood"] = mood
        out["intensity"] = intensity
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

    def telehealth_interrupt(self, device_id) -> dict:
        """Cut Moxie off mid-line (barge-in). Inferred (B2): the physical effect is
        unobserved; the message carries no `output`."""
        refusal = self._telehealth_guard(device_id, need_mode=True)
        if refusal:
            return refusal
        self._telehealth_publish(device_id, telehealth_seam.build_telehealth_command(
            "INTERRUPT", session_id=self._th(device_id)["session_id"]))
        self._note("telehealth", "🎭 interrupt")
        return self.telehealth_view(device_id)

    def telehealth_view(self, device_id) -> dict:
        """The telehealth card: mode, session, the robot's reported state, bedtime and the
        transcript. `state` stays empty until the robot reports (never invented as READY).
        `in_bedtime` is a warning, not a gate (B4: robot behaviour unknown)."""
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted.",
                    "enabled": False, "online": device_id in self.robots,
                    "transcript": [], "moods": telehealth_seam.moods(),
                    "max_intensity": vocab_seam.MAX_INTENSITY}
        if device_id not in self.robots and device_id not in self._telehealth:
            # Never seen, nothing held: same shape as the other views (proxy -> 404).
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
        """A `TelehealthRobotEvent` (activity log, `subtopic: "telehealth"`) -> the
        robot's reported state. Unknown state names are stored verbatim and flagged."""
        event = telehealth_seam.parse_telehealth_event(payload)
        st = self._th(device_id)
        if event["state"]:
            st["state"] = event["state"]
            st["state_at"] = event["at"] if event["at"] is not None else time.time()
            flag = "" if event["known"] else " (not a state we know)"
            self._note("telehealth", f"🎭 robot reports {event['state']}{flag}")
        # Adopt a session id only from a robot reporting IN_SESSION (resume after our
        # restart), never from a late EXITING report.
        if (event["session_id"] and not st["session_id"]
                and event["state"] == "IN_SESSION"):
            st["session_id"] = event["session_id"]
        return event
