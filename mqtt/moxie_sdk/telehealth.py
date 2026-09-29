"""
🎭 Telehealth / "Be Moxie" — the wire an operator drives the robot's body with.

Pure (stdlib + `moxie_sdk.vocab` only), JSON-safe, no I/O, no runtime imports. The
supervisor's `telehealth_*` methods build their payloads here; the console's card reads
the vocabulary from here through the runtime, so a picker can never offer a mood the
robot's enum does not have.

**Mode.** `RobotCloudConfig.moxie_mode = 21` (`enum MoxieMode {DEFAULT_MODE=0;
TELEHEALTH=1}`, proto-catalog.md:212, :369). The launcher's `STATE_TELEBRAIN` runs
perception + MAINAPP with no on-device brain — the remote human is the brain
(firmware/boot-and-launcher.md:48, :61; protocol/telehealth.md:50-54).

**Protocol**, from the recovered `embodied.telehealth.TeleHealth.proto` (telehealth.md:11-39)::

    enum Action     { UNKNOWN_ACTION=0; START_SESSION=1; PLAY_OUTPUT=2;
                      END_SESSION=3;    UPDATE_STATE=4; INTERRUPT=5; }
    enum RobotState { UNKNOWN_STATE=0;  READY=1; IN_SESSION=2; EXITING=3; }
    message Output { line_id=1; repeated line_params=2; text=3; markup=4; }
    message TelehealthMessage { timestamp=1; action=2; output=3; state=4; session_id=5;
                                software_version=100; module_name=101; }
    message TelehealthRobotCommand { command=1; message=2; }
    message TelehealthRobotEvent   { subtopic=1; message=2; }

**Transport.** Cloud → robot as JSON on `/devices/{id}/commands/telehealth`; robot →
cloud on `events/client-service-activity-log` with `subtopic: "telehealth"`
(telehealth.md:69-78). `sim/tests/test_telehealth.py` checks every emitted key against
the compiled `TeleHealth_pb2` in tools/robot-toolkit.

**Assumptions.** B1: writing `moxie_mode: TELEHEALTH` into `/config` enters
`STATE_TELEBRAIN` (field-proven by OpenMoxie, not captured; one constant below).
B5: `line_id`/`line_params` name pre-authored lines we have no catalog of, so they are
never emitted. Nothing here has run against a physical robot
(backlog/telehealth.md §6).
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from . import vocab

# --------------------------------------------------------------------------- #
# The enums, by NAME, in field-number order (telehealth.md:16-17)
# --------------------------------------------------------------------------- #
#: `TeleHealth.Action` — the operator's control verbs.
ACTIONS = ("UNKNOWN_ACTION", "START_SESSION", "PLAY_OUTPUT",
           "END_SESSION", "UPDATE_STATE", "INTERRUPT")

#: `TeleHealth.RobotState` — what the robot reports back about itself.
STATES = ("UNKNOWN_STATE", "READY", "IN_SESSION", "EXITING")

#: The one action that carries an `Output`; no other action emits an `output` key.
OUTPUT_ACTION = "PLAY_OUTPUT"

#: `TelehealthRobotCommand.command` (field 1): the channel name, as OpenMoxie sends it.
COMMAND_NAME = "telehealth"

#: The `client-service-activity-log` subtopic the robot reports its state on
#: (mqtt-and-conversation.md §3.3, telehealth.md:74-77).
EVENT_SUBTOPIC = "telehealth"

#: The MQTT command name (cloud → robot), i.e. `/devices/{id}/commands/telehealth`.
COMMAND_TOPIC = "telehealth"

# --------------------------------------------------------------------------- #
# ASSUMPTION B1 — the mode toggle, behind one constant
# --------------------------------------------------------------------------- #
# Values mirror `cloud_config.MoxieMode` (test_telehealth.py pins them).
MOXIE_MODE_KEY = "moxie_mode"
TELEHEALTH_MOXIE_MODE = 1        # MoxieMode.TELEHEALTH
DEFAULT_MOXIE_MODE = 0           # MoxieMode.DEFAULT_MODE

#: Transcript entries kept per robot — in memory only, never persisted.
TRANSCRIPT_MAX = 200

#: Who a transcript line came from (STT for the child, the console for the operator).
CHILD, OPERATOR = "child", "operator"


def telehealth_topic(device_id: str) -> str:
    """`/devices/{device_id}/commands/telehealth` — cloud → robot."""
    return f"/devices/{device_id}/commands/{COMMAND_TOPIC}"


def new_session_id() -> str:
    """A fresh `TelehealthMessage.session_id` (shape is ours; the proto says `string`)."""
    return f"ths-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# The vocabulary a human picks from
# --------------------------------------------------------------------------- #
def moods() -> list:
    """The 11 recovered `ePlaybackMood` names (`vocab.MOODS`), lowest id first — the
    closed list the console's picker renders."""
    return [{"id": name, "label": name.capitalize(), "value": value}
            for name, value in sorted(vocab.MOODS.items(), key=lambda kv: kv[1])]


def validate_mood(mood) -> Optional[str]:
    """A canonical `ePlaybackMood` name, or None for "no hint".

    Accepts a name, an alias (`vocab.MOOD_ALIASES`) or an int id; an unknown label
    raises rather than being silently dropped."""
    if mood in (None, ""):
        return None
    if isinstance(mood, bool):
        raise ValueError("mood must be a name or an ePlaybackMood id")
    if isinstance(mood, int):
        name = vocab.MOOD_NAME_BY_ID.get(mood)
        if name is None:
            raise ValueError(f"unknown mood id {mood!r}")
        return name
    key = str(mood).strip().lower()
    value = vocab.MOOD_ALIASES.get(key)
    if value is None:
        raise ValueError(f"unknown mood {mood!r}")
    return vocab.MOOD_NAME_BY_ID[value]


def validate_intensity(intensity) -> Optional[int]:
    """An int 0-`vocab.MAX_INTENSITY`, or None for "let the text decide".

    The robot's `maxIntensity=2` (behavior-markup.md:107). Out of range clamps; a
    non-number raises."""
    if intensity in (None, ""):
        return None
    if isinstance(intensity, bool):
        raise ValueError("intensity must be an integer 0-%d" % vocab.MAX_INTENSITY)
    try:
        value = int(intensity)
    except (TypeError, ValueError):
        raise ValueError("intensity must be an integer 0-%d" % vocab.MAX_INTENSITY)
    return max(0, min(vocab.MAX_INTENSITY, value))


def transcript_entry(who: str, text: str, at: Optional[float] = None) -> dict:
    """One line of the live transcript: `{who, text, at}`.

    Text only: the recovered message has no audio field, and `LoggingPolicy` does not
    authorize piping a child's microphone to a third party (backlog/telehealth.md §2.5)."""
    return {"who": OPERATOR if who == OPERATOR else CHILD,
            "text": str(text or ""),
            "at": float(at if at is not None else time.time())}


# --------------------------------------------------------------------------- #
# Cloud → robot
# --------------------------------------------------------------------------- #
def build_telehealth_command(action: str, *, text: str = "", markup: str = "",
                             session_id: str = "",
                             timestamp: Optional[int] = None) -> dict:
    """A `TelehealthRobotCommand` as the JSON the robot's command handler reads.

    `{"command": "telehealth", "message": {timestamp, action[, output][, session_id]}}`.
    `output` only for `PLAY_OUTPUT` (`text` required, `markup` optional); `timestamp` in
    milliseconds like the rest of this transport.
    """
    name = str(action or "").strip().upper()
    if name not in ACTIONS:
        raise ValueError(f"unknown telehealth action {action!r}; expected one of "
                         f"{', '.join(ACTIONS)}")
    if name == "UNKNOWN_ACTION":
        raise ValueError("UNKNOWN_ACTION is the proto's zero value, not a command")
    message = {
        "timestamp": int(timestamp if timestamp is not None else time.time() * 1000),
        "action": name,
    }
    if name == OUTPUT_ACTION:
        spoken = str(text or "").strip()
        if not spoken:
            raise ValueError("PLAY_OUTPUT needs text to speak")
        output = {"text": spoken}
        if markup:
            output["markup"] = str(markup)
        message["output"] = output
    if session_id:
        message["session_id"] = str(session_id)
    return {"command": COMMAND_NAME, "message": message}


# --------------------------------------------------------------------------- #
# Robot → cloud
# --------------------------------------------------------------------------- #
def parse_telehealth_event(payload) -> dict:
    """A `TelehealthRobotEvent` off the activity log → `{state, session_id, at, known}`.

    Accepts the wrapped shape or a bare `TelehealthMessage`. An unknown state is kept
    verbatim with `known: False`, never coerced. Never raises (runs on the MQTT loop).
    """
    data = payload if isinstance(payload, dict) else {}
    message = data.get("message")
    if not isinstance(message, dict):
        message = data if "state" in data or "action" in data else {}
    raw_state = message.get("state")
    if isinstance(raw_state, bool) or raw_state is None:
        state = ""
    elif isinstance(raw_state, int):
        # A numeric RobotState (the proto's own encoding) → its recovered name.
        state = STATES[raw_state] if 0 <= raw_state < len(STATES) else str(raw_state)
    else:
        state = str(raw_state).strip()
    at = message.get("timestamp")
    try:
        at = float(at) / 1000.0 if at is not None else None
    except (TypeError, ValueError):
        at = None
    action = str(message.get("action") or "").strip().upper()
    return {
        "state": state,
        "known": state in STATES,
        "session_id": str(message.get("session_id") or ""),
        "action": action if action in ACTIONS else "",
        "at": at,
    }
