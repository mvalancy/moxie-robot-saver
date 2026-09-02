"""
Moxie SDK — drive a Moxie robot as an embodied avatar for any AI.

Public API:
    from moxie_sdk import MoxieApp, Turn, Reply, Action, ActionType
    from moxie_sdk import RobotContext, ChildProfile
    from moxie_sdk.apps import LLMApp, WebhookApp, EchoApp

Implement `MoxieApp.respond(turn) -> Reply` and register it with the runtime
(see mqtt/supervisor). The runtime does the rest.
"""
__version__ = "0.4.0"     # single source of truth (pyproject reads this)

from .types import (Turn, Reply, ReplyChunk, Action, ActionType,
                    RobotContext, ChildProfile)
from .app import MoxieApp

__all__ = ["MoxieApp", "Turn", "Reply", "ReplyChunk", "Action", "ActionType",
           "RobotContext", "ChildProfile", "__version__"]
