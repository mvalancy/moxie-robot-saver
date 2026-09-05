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
    """The RemoteChat response outcome — `RemoteChatResponse.result` (uint32), values
    verbatim from embodied/robotbrain/RemoteChat.proto. The robot acts on these:
    SUCCESS renders the output; ERROR_OFFLINE makes it fall back to its on-device brain
    (see docs/architecture/ai-seam.md §2). Emitted on the wire as the enum NAME."""
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


class ActionType(str, Enum):
    """Structured things a Reply can ask Moxie to do beyond speaking."""
    LAUNCH = "launch"      # launch a module/experience (module_id[/content_id])
    EXIT = "exit"          # end the current module
    SLEEP = "sleep"        # go to sleep
    ENABLE_QR = "enable_qr"  # turn on QR scanning (for launch cards)
    EXECUTE = "execute"    # call a named on-robot function (advanced)


@dataclass
class Action:
    """One `RemoteChatAction` for the robot to carry out.

    `function` / `args` are the `execute` half: `function` is the robot-side function to
    run and `args` are its arguments. Both reach the wire through
    `moxie_sdk.wire.encode_action`, which spells them `function_id` (proto field 7) and,
    **by type**, either `function_args` (field 8, `repeated string` — for a list/tuple) or
    `action_args` (field 10, `repeated {key, value}` — for a dict). Empty means the keys
    are not emitted at all. They were silently dropped before 2026-09-04, which made every
    `execute` this appliance could send arrive unnamed
    (docs/architecture/backlog/qr-launch-cards.md §P0-a).
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

    The robot emits `eb-found-face` / `eb-lost-target` / QR / ArUco / book events and
    nothing else — no pixels, no bounding boxes, no identity
    (docs/architecture/vision.md §1.1) — so this is presence, not vision. `line` is a
    short, kid-safe sentence for the system prompt, and is `""` unless something
    actually changed. Empty dict = a robot whose vision events we have never seen.
    The same snapshot is on `robot.extra["presence"]` for apps that only get a
    `RobotContext` (`greeting`, `on_event`)."""


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

    This is a **request, not the final list.** `moxie_runtime._publish_chat` merges it
    *into* the supervisor's own vision subscription and never the other way round, so an
    app (or a sandboxed content pack, which is where these come from — `content_app
    .subscriptions_of`) can add a perception it needs and cannot switch off the events
    presence and greeting depend on. Empty for every app that does not ask, which is why
    a reply that never sets it is byte-identical on the wire to what we sent before."""
    # ---- scored output (docs/architecture/ai-seam.md §② "Response out") ----------
    # `RemoteChatOutput` is not just text: it is a fully-scored line. Everything below is
    # optional, and everything below is FILLED IN by the seam when the app leaves it None
    # — `supervisor/markup.py::perform` scores every line it performs, so a brain that
    # says nothing about its own delivery still ships a scored turn.
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
    """The staged `moxie_sdk.performance.Performance` behind `markup`, when the behavior
    planner performed this line. Diagnostics and the preview console — the wire carries
    the rendered `markup` plus the scored fields above, never this structure. An app may
    also SET it to stage a line itself; every id in it still passes `validate()`."""

    @classmethod
    def offline(cls, text: str = "") -> "Reply":
        """A brain that can't answer (endpoint unreachable) → ERROR_OFFLINE, so the
        robot degrades to its on-device fallback instead of hanging."""
        return cls(text=text, result_code=ResultCode.ERROR_OFFLINE)


@dataclass
class ReplyChunk:
    """One piece of a **streamed** Reply — a finished sentence, ready to speak.

    An app that can answer incrementally implements `MoxieApp.respond_stream(turn) ->
    Iterator[ReplyChunk]`; the runtime publishes each chunk as its own
    `RemoteChatResponse` (`result=REPLY_PENDING` + `chunk_num`) and closes the sequence
    on the chunk marked `final`, which goes out as `SUCCESS` with
    `consistency_control.is_completed` (RemoteChat.proto fields 22 / 18 — see
    docs/architecture/mqtt-and-conversation.md §4.5).

    `actions` are the robot-control tags found *in this chunk*. Our prompt convention puts
    them at the very front of the answer, so in practice they ride on chunk 0 — but the
    field is per-chunk so a tag can never be lost by arriving late.

    `result_code` is normally left None: the runtime picks REPLY_PENDING for a
    non-final chunk and SUCCESS for the final one. Set it to override the final chunk's
    outcome (e.g. `ResultCode.ERROR_OFFLINE`).
    """
    text: str
    markup: Optional[str] = None
    actions: list = field(default_factory=list)   # list[Action]
    final: bool = False                  # last chunk of the answer (closes the sequence)
    end_turn: bool = False
    result_code: Optional[ResultCode] = None
    # ---- scored output, per chunk ------------------------------------------------
    # These did not exist before the behavior planner, which meant a STREAMED answer
    # could not carry scored output even in principle: `_publish_stream_chunk` had
    # nothing to pass (docs/architecture/backlog/expressiveness.md §2.3, C2/C4). They
    # mirror `Reply`'s, and like `Reply`'s they are filled in by the seam when an app
    # leaves them None, so every published chunk is scored.
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
