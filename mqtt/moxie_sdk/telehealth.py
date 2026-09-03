"""
🎭 Telehealth / "Be Moxie" — the wire an operator drives the robot's body with.

Pure (stdlib + `moxie_sdk.vocab` only), JSON-safe, no I/O, no runtime imports. The
supervisor's `telehealth_*` methods build their payloads here; the console's card reads
the vocabulary from here through the runtime, so a picker can never offer a mood the
robot's enum does not have.

WHAT OUR RECOVERED DOCS ESTABLISH
---------------------------------
**The mode.** `enum MoxieMode { DEFAULT_MODE = 0; TELEHEALTH = 1; }` lives in
`embodied/logging/Cloud.proto` and `RobotCloudConfig` carries it as field 21,
`embodied.logging.MoxieMode moxie_mode = 21`
(`docs/reverse-engineering/protocol/proto-catalog.md`:212, :369). `cloud_config.py` has
emitted it since the first config push; nothing has ever set it to anything but
`DEFAULT_MODE`.

**The launcher state.** `STATE_TELEBRAIN` runs *"perception + MAINAPP, no BRAIN"* and is
entered from `STATE_RUNNING` when a telehealth session starts
(`docs/reverse-engineering/firmware/boot-and-launcher.md`:48, :61). Dropping the local
dialog engine is the whole point: *"the remote human **is** the brain, so there's no
on-device dialog engine to conflict with the operator's lines"*
(`docs/reverse-engineering/protocol/telehealth.md`:26-28).

**The protocol**, verbatim from the recovered `embodied.telehealth.TeleHealth.proto`
(`docs/reverse-engineering/protocol/telehealth.md`:30-58, the compiled twin is
`tools/robot-toolkit/moxie_toolkit/embodied/telehealth/TeleHealth_pb2.py`)::

    enum Action     { UNKNOWN_ACTION=0; START_SESSION=1; PLAY_OUTPUT=2;
                      END_SESSION=3;    UPDATE_STATE=4; INTERRUPT=5; }
    enum RobotState { UNKNOWN_STATE=0;  READY=1; IN_SESSION=2; EXITING=3; }

    message Output {
      optional string line_id     = 1;   repeated string line_params = 2;
      optional string text        = 3;   optional string markup      = 4;
    }
    message TelehealthMessage {
      optional uint64 timestamp = 1;  optional Action     action = 2;
      optional Output output    = 3;  optional RobotState state  = 4;
      optional string session_id = 5;
      optional string software_version = 100;  optional string module_name = 101;
    }
    message TelehealthRobotCommand { optional string command=1; optional TelehealthMessage message=2; }
    message TelehealthRobotEvent   { optional string subtopic=1; optional TelehealthMessage message=2; }

**The transport.** Cloud → robot on `/devices/{id}/commands/telehealth`, sent as **JSON**
like every other `commands/{name}` payload (`telehealth.md`:81-91, cross-checked against
`docs/architecture/mqtt-and-conversation.md` §3.5 and
`docs/reverse-engineering/protocol/cloud-protocol.md`:144-150). Robot → cloud on
`events/client-service-activity-log` with **`subtopic: "telehealth"`**.

**Why JSON and not protobuf.** `tools/robot-toolkit/moxie_toolkit/cloud.py` already ships
*protobuf* builders for this exact protocol, wire-round-tripped in
`tools/robot-toolkit/test_telehealth.py`. The runtime path is JSON, so it does not import
them — but they are the **schema oracle**: `sim/tests/test_telehealth.py` cross-checks
every key this module emits against `TeleHealth_pb2`, so a typo cannot ship.

WHAT IS ASSUMED, AND WHERE THE ASSUMPTION LIVES
-----------------------------------------------
* **B1 — that writing `moxie_mode:"TELEHEALTH"` into the pushed `/config` is what enters
  `STATE_TELEBRAIN`.** Our corpus says the state is entered *"when a telehealth session
  starts"*; it does not name the trigger. It is one constant here
  (`MOXIE_MODE_KEY` / `TELEHEALTH_MOXIE_MODE`), exactly like
  `cloud_config.UNPAIRED_PAIRING_STATUS`, so a capture that contradicts it is a one-line
  fix.
* **B5 — `Output.line_id` / `line_params` resolve against on-board authored content.**
  The field comment says *"id of a pre-authored line"*; we have no catalog of those ids.
  So `build_telehealth_command` **never emits them**. An id we cannot cite is an id we do
  not send.

Nothing in this module has been exercised against a physical robot. See
`docs/architecture/backlog/telehealth.md` §6 for the five questions only one can settle.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from . import vocab

# --------------------------------------------------------------------------- #
# The enums, by NAME, in field-number order (telehealth.md:35-36)
# --------------------------------------------------------------------------- #
#: `TeleHealth.Action` — the operator's control verbs.
ACTIONS = ("UNKNOWN_ACTION", "START_SESSION", "PLAY_OUTPUT",
           "END_SESSION", "UPDATE_STATE", "INTERRUPT")

#: `TeleHealth.RobotState` — what the robot reports back about itself.
STATES = ("UNKNOWN_STATE", "READY", "IN_SESSION", "EXITING")

#: The one action that carries an `Output`. Every other action emits no `output` key at
#: all — an empty `Output` on the wire would be a claim we have a line when we do not.
OUTPUT_ACTION = "PLAY_OUTPUT"

#: `TelehealthRobotCommand.command` (field 1). OpenMoxie's server sends the channel name
#: here and the robot accepts it; nothing in our corpus records another value, so this is
#: the one string we send. (Prior art, MIT — see ATTRIBUTION.md.)
COMMAND_NAME = "telehealth"

#: The `client-service-activity-log` subtopic the robot reports its state on
#: (mqtt-and-conversation.md §3.3, telehealth.md:88-91).
EVENT_SUBTOPIC = "telehealth"

#: The MQTT command name (cloud → robot), i.e. `/devices/{id}/commands/telehealth`.
COMMAND_TOPIC = "telehealth"

# --------------------------------------------------------------------------- #
# ASSUMPTION B1 — the mode toggle, behind one constant
# --------------------------------------------------------------------------- #
# `RobotCloudConfig.moxie_mode` (field 21) is a recovered field with a recovered enum;
# what is NOT recovered is that writing TELEHEALTH into it is what puts the robot into
# `STATE_TELEBRAIN`. OpenMoxie's `views.py::puppet_api` does exactly this in a server that
# drives real robots, so it is field-proven, not capture-proven — the same standing as
# `cloud_config.UNPAIRED_PAIRING_STATUS`. Both values mirror `cloud_config.MoxieMode`;
# `sim/tests/test_telehealth.py` asserts they cannot drift apart.
MOXIE_MODE_KEY = "moxie_mode"
TELEHEALTH_MOXIE_MODE = 1        # MoxieMode.TELEHEALTH
DEFAULT_MOXIE_MODE = 0           # MoxieMode.DEFAULT_MODE

#: How many transcript entries one robot keeps. In memory, bounded, never written through
#: `store.py` — it is a live view of a session, not an archive of a child's words.
TRANSCRIPT_MAX = 200

#: Who a transcript line came from. `child` lines are transcribed by the STT path that
#: already exists; `operator` lines are what the person at the console typed.
CHILD, OPERATOR = "child", "operator"


def telehealth_topic(device_id: str) -> str:
    """`/devices/{device_id}/commands/telehealth` — cloud → robot."""
    return f"/devices/{device_id}/commands/{COMMAND_TOPIC}"


def new_session_id() -> str:
    """A fresh `TelehealthMessage.session_id`. Shape is ours (the proto says only
    `string`); the `ths-` prefix matches the `sfe-` safety-event convention."""
    return f"ths-{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# The vocabulary a human picks from
# --------------------------------------------------------------------------- #
def moods() -> list:
    """The 11 recovered `ePlaybackMood` names, lowest id first — the closed list the
    console's picker renders. Recovered by name AND value from Assembly-CSharp
    (`docs/reverse-engineering/runtime/behavior-markup.md`:107-133) and frozen in
    `moxie_sdk.vocab.MOODS`; this module never adds to it."""
    return [{"id": name, "label": name.capitalize(), "value": value}
            for name, value in sorted(vocab.MOODS.items(), key=lambda kv: kv[1])]


def validate_mood(mood) -> Optional[str]:
    """A canonical `ePlaybackMood` name, or None for "no hint".

    Accepts a canonical name, a known alias (`vocab.MOOD_ALIASES`) or an int id, and
    returns the canonical *name* so what the operator picked is what reaches the wire.
    An unknown label raises rather than being silently dropped: a picker is a closed
    vocabulary and a human deserves to be told when their choice was not one of them."""
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

    **0-2, not a 0.0-1.0 float**: `maxIntensity=2` is what the robot's own enum accepts
    (`behavior-markup.md`:107). Out-of-range clamps — an operator who drags a slider past
    the end meant "as strong as it goes", not "fail" — but a non-number raises."""
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

    Text only. No child audio and no video reach the operator this phase — the recovered
    `TelehealthMessage` has no audio field, so a return path would be *our* invention, and
    `LoggingPolicy` does not authorize piping a live child microphone to a third party.
    See `docs/architecture/backlog/telehealth.md` §2.5 for the argument and the stated
    limit."""
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

    `{"command": "telehealth", "message": {timestamp, action[, output][, session_id]}}` —
    every key a recovered field name, every action string a recovered enum name.

    `output` is present **only** for `PLAY_OUTPUT`, and then carries `text` (required) and
    `markup` (when the caller has one). `line_id` / `line_params` are never emitted
    (assumption B5). `timestamp` is milliseconds, matching every other timestamp on this
    transport (`send_query` in `sim/virtual_moxie.py`, `Packet.timestamp`).
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

    Accepts the wrapped shape (`{subtopic:"telehealth", message:{...}}`) and a bare
    `TelehealthMessage`, because an activity-log record may carry either.

    An **unknown state name is preserved verbatim and flagged** (`known: False`), never
    coerced to `UNKNOWN_STATE`: a robot telling us something new must not be silently
    rounded off to something we already believe. A malformed payload returns the same
    empty-but-valid view rather than raising — this runs on the MQTT loop.
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
