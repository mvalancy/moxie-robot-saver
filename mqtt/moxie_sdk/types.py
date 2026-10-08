"""
Core data types for the Moxie SDK — the clean boundary between the robot and
whatever AI is driving it. These are what "a turn on Moxie" looks like, independent
of MQTT, protobufs, or any specific model.

An external app (a game, an agent, any AI service) only ever deals in these types:
it receives a `Turn` and returns a `Reply`. It never touches the transport.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:                    # a type-only import: `types` stays dependency-free
    from .performance import Performance


@dataclass
class ChildProfile:
    """The person Moxie is talking to (from the parent-app server's child record)."""
    nickname: str = "friend"
    pronouns: Optional[str] = None
    birthday_iso: Optional[str] = None
    input_speed: float = 0.0
    notes: str = ""                      # free-form context an app may attach


@dataclass
class RobotContext:
    """Identity + state of a connected Moxie."""
    device_id: str                       # "d_<uuid>"
    child: ChildProfile = field(default_factory=ChildProfile)
    firmware: Optional[str] = None
    module_id: Optional[str] = None      # the experience/module currently running
    content_id: Optional[str] = None
    extra: dict = field(default_factory=dict)


class ResultCode(int, Enum):
    """The RemoteChat response outcome — `RemoteChatResponse.result`, values verbatim
    from the proto's `ResultCode` (embodied/robotbrain/RemoteChat.proto:307-318). The
    robot acts on these: SUCCESS renders the output; ERROR_OFFLINE makes it fall back to
    its on-device brain (see docs/architecture/ai-seam.md §2).

    Emitted on the wire as the integer VALUE: the field is a plain `uint32 result = 2`
    (RemoteChat.proto:320), not the enum, so a robot parsing the JSON as protobuf rejects
    the name (`invalid literal for int(): 'SUCCESS'`); OpenMoxie sends `result: 0`
    (volley.py `create_response`)."""
    SUCCESS = 0
    ERROR_TIMEOUT = 1
    ERROR_STATE = 2
    ERROR_SERVICE = 3
    ERROR_OFFLINE = 4          # no brain/connectivity → robot uses its local fallback
    NOREPLY_INTERRUPT = 5      # deliberately say nothing (barge-in)
    NOREPLY_ACK = 6            # acknowledged, no spoken reply
    REPLY_FORCE_ANCHOR = 7
    REPLY_FORCE_QUIT = 8
    REPLY_PENDING = 9          # streaming: more chunks to come

    @classmethod
    def _missing_(cls, value):
        # The enum NAME this SDK put on the wire until 2026-10: still read by a robot
        # double meeting an older server (`ResultCode("SUCCESS")`), never emitted.
        return cls.__members__.get(value) if isinstance(value, str) else None


class ActionType(str, Enum):
    """Structured things a Reply can ask Moxie to do beyond speaking.

    Values are the recovered `RemoteChatAction.ActionID` names (RemoteChat.proto:256-266)
    and go on the wire verbatim — except ENABLE_QR, an SDK convenience with no ActionID of
    its own, which `wire.encode_action` spells as the contract's `execute` +
    `ENABLE_QR_FUNCTION` (qr-launch-cards.md §P0-a).
    """
    LAUNCH = "launch"      # launch a module/experience (module_id[/content_id]); ActionID 1
    EXIT = "exit_module"   # end the current module; ActionID 3
    SLEEP = "sleep"        # go to sleep; ActionID 7
    ENABLE_QR = "enable_qr"  # turn on QR scanning (for launch cards) → execute eb_enable_qr
    EXECUTE = "execute"    # call a named on-robot function (advanced); ActionID 6

    @classmethod
    def _missing_(cls, value):
        # The spelling this SDK used until 2026-10: still accepted from an app or a
        # webhook declaring `{"type": "exit"}`, never emitted.
        return cls.EXIT if value == "exit" else None


# ---- wire spellings (one table; `wire.py` reads it, the Sim clients mirror it) ----
#: `OutputType` (ChatResponse.proto:6-21) on every `RemoteChatAction` we send. A brain's
#: reply is GLOBAL_RESPONSE (9), as OpenMoxie's field-proven `volley.py` sends on every
#: response; GLOBAL_COMMAND (2) is a robot-wide command answered without a brain
#: (OpenMoxie `global_responses.py`) and nothing here emits one yet.
OUTPUT_TYPE_RESPONSE = "GLOBAL_RESPONSE"
OUTPUT_TYPE_COMMAND = "GLOBAL_COMMAND"

#: The `ActionID` names this SDK emits (RemoteChat.proto:258,:260,:263,:264), and the two
#: older spellings of ours that both Sim clients (`sim/virtual_moxie.py`,
#: `sim/web/bridge/actions.js`) still accept but that no longer go out.
ACTION_IDS = ("launch", "exit_module", "sleep", "execute")
LEGACY_ACTION_NAMES = {"exit": "exit_module", "enable_qr": "execute"}

#: What arms the robot's QR reader: `execute` + this `function_id` (field 7) and these
#: `function_args` (field 8) — the shape qr-launch-cards.md §P0-a / §4 T9 names.
ENABLE_QR_FUNCTION = "eb_enable_qr"
ENABLE_QR_ARGS = ("true",)


@dataclass
class Action:
    """One `RemoteChatAction` for the robot to carry out.

    `function` / `args` are the `execute` half; `wire.encode_action` spells them
    `function_id` and, by type, `function_args` (list) or `action_args` (dict).
    """
    type: ActionType
    module_id: Optional[str] = None
    content_id: Optional[str] = None
    function: Optional[str] = None        # -> function_id
    args: Any = field(default_factory=dict)   # dict -> action_args; list -> function_args


@dataclass
class Turn:
    """One conversational turn: what reached Moxie, plus who/where."""
    robot: RobotContext
    speech: str                          # recognized user utterance (from STT)
    history: list = field(default_factory=list)   # [{role, content}, ...] prior turns
    command: str = "prompt"              # prompt | continue | notify
    input_vars: dict = field(default_factory=dict)  # e.g. scanned QR value
    presence: dict = field(default_factory=dict)
    """What Moxie's own eyes have told the server — `moxie_sdk.presence.snapshot()`:
    `{known, face_present, present_s, away_s, faces_seen, last_qr/marker/book, line}`.

    Presence, not vision (vision.md §1.1). `line` is a kid-safe prompt sentence, `""`
    unless something changed. Empty = no vision events seen. Also on
    `robot.extra["presence"]` for apps that only get a `RobotContext`."""


@dataclass
class Reply:
    """What the AI wants Moxie to say/do. `markup` is optional — if omitted, the
    runtime auto-generates expressive behavior markup from `text`."""
    text: str
    markup: Optional[str] = None
    actions: list = field(default_factory=list)   # list[Action]
    end_turn: bool = False               # True → Moxie stops listening after this
    result_code: ResultCode = ResultCode.SUCCESS  # the RemoteChat outcome (see ResultCode)
    subscribe: list = field(default_factory=list)
    """Robot events this reply ASKS the robot to start pushing us — the app's half of
    `RemoteChatAction.EventSubscription.active[]` (remote-chat-protocol.md §RemoteChatAction).

    A request: the runtime merges it into its own vision subscription, so an app (or a
    content pack) can add events but never switch off the ones presence depends on."""
    # ---- scored output (ai-seam.md §②) — optional; the seam fills whatever is None ----
    mood: Optional[str] = None           # ePlaybackMood by NAME (happy/curious/…)
    dialog_act: Optional[str] = None     # one of the 22 RemoteDialog.DialogActs
    mood_intensity: int = 0              # 0-2 (`maxIntensity=2`)
    emotion: Optional[str] = None        # one of the 7 RemoteDialog.EmotionStates
    signal: Optional[str] = None         # one of the 9 RemoteSignals.Signals
    gesture: Optional[str] = None        # a `Gesture_*` the app wants (a HINT, validated)
    gaze: Optional[str] = None           # a look-bearing `Bht_*` (there is no gaze verb)
    icon: Optional[str] = None           # an `icons-v2` value (4 confirmed)
    sfx: Optional[str] = None            # a `SoundToPlay` id (2 confirmed)
    performance: Optional["Performance"] = None
    """The staged `Performance` behind `markup` (diagnostics/preview; never on the wire).
    An app may set it to stage a line itself; it is still validated."""

    @classmethod
    def offline(cls, text: str = "") -> "Reply":
        """A brain that can't answer (endpoint unreachable) → ERROR_OFFLINE, so the
        robot degrades to its on-device fallback instead of hanging."""
        return cls(text=text, result_code=ResultCode.ERROR_OFFLINE)


@dataclass
class ReplyChunk:
    """One piece of a **streamed** Reply — a finished sentence, ready to speak.

    Yielded by `MoxieApp.respond_stream`; each is published as a REPLY_PENDING chunk and
    the `final` one closes the sequence (mqtt-and-conversation.md §4.5). `actions` are
    per chunk so a late tag is never lost. `result_code=None` lets the runtime choose.
    """
    text: str
    markup: Optional[str] = None
    actions: list = field(default_factory=list)   # list[Action]
    final: bool = False                  # last chunk of the answer (closes the sequence)
    end_turn: bool = False
    result_code: Optional[ResultCode] = None
    # ---- scored output, per chunk (mirrors `Reply`; filled in by the seam) ----
    mood: Optional[str] = None
    dialog_act: Optional[str] = None
    mood_intensity: int = 0
    emotion: Optional[str] = None
    signal: Optional[str] = None
    performance: Optional["Performance"] = None

    @classmethod
    def from_reply(cls, reply: "Reply") -> "ReplyChunk":
        """The whole of a non-streamed `Reply` as one closing chunk."""
        return cls(text=reply.text, markup=reply.markup, actions=list(reply.actions),
                   final=True, end_turn=reply.end_turn, result_code=reply.result_code,
                   mood=reply.mood, dialog_act=reply.dialog_act,
                   mood_intensity=reply.mood_intensity, emotion=reply.emotion,
                   signal=reply.signal, performance=reply.performance)
