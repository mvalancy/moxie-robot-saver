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
from typing import Optional


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
    type: ActionType
    module_id: Optional[str] = None
    content_id: Optional[str] = None
    function: Optional[str] = None
    args: dict = field(default_factory=dict)


@dataclass
class Turn:
    """One conversational turn: what reached Moxie, plus who/where."""
    robot: RobotContext
    speech: str                          # recognized user utterance (from STT)
    history: list = field(default_factory=list)   # [{role, content}, ...] prior turns
    command: str = "prompt"              # prompt | continue | notify
    input_vars: dict = field(default_factory=dict)  # e.g. scanned QR value


@dataclass
class Reply:
    """What the AI wants Moxie to say/do. `markup` is optional — if omitted, the
    runtime auto-generates expressive behavior markup from `text`."""
    text: str
    markup: Optional[str] = None
    actions: list = field(default_factory=list)   # list[Action]
    end_turn: bool = False               # True → Moxie stops listening after this
    result_code: ResultCode = ResultCode.SUCCESS  # the RemoteChat outcome (see ResultCode)
    mood: Optional[str] = None           # optional scored output: emotional performance
    dialog_act: Optional[str] = None     # optional scored output: what the line does

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

    @classmethod
    def from_reply(cls, reply: "Reply") -> "ReplyChunk":
        """The whole of a non-streamed `Reply` as one closing chunk."""
        return cls(text=reply.text, markup=reply.markup, actions=list(reply.actions),
                   final=True, end_turn=reply.end_turn, result_code=reply.result_code)
