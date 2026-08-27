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
