"""
The MoxieApp interface — the heart of the SDK.

"Being Moxie" means implementing this one small interface. The runtime handles all
the hard parts (MQTT, TLS, protobufs, STT, behavior markup, the robot's quirks); an
app only decides what Moxie says and does.

    class MyApp(MoxieApp):
        def respond(self, turn: Turn) -> Reply:
            return Reply(text=f"You said: {turn.speech}")

This is how a game, an agent, or any external AI drives Moxie as an avatar. The
default app is an LLM persona (apps/llm_app.py); an external service plugs in over
the network via apps/webhook_app.py — no code from the external app lives here.
"""
from __future__ import annotations
from typing import Optional
from .types import Turn, Reply, RobotContext


class MoxieApp:
    """Base class for anything that drives Moxie. Subclass and override `respond`.

    Lifecycle hooks are optional; override only what you need."""

    name: str = "moxie-app"

    # --- required: the conversational turn ---
    def respond(self, turn: Turn) -> Reply:
        """Given what the child said (+ context/history), return what Moxie says/does."""
        raise NotImplementedError

    # --- optional: answer incrementally, a sentence at a time ---
    def respond_stream(self, turn: Turn):
        """Return an `Iterator[ReplyChunk]` to answer *while* the brain is still writing,
        or **None** to say "I don't stream" (the default).

        The runtime publishes each chunk as its own `RemoteChatResponse`
        (`result=REPLY_PENDING` + `chunk_num`) and closes the sequence on the chunk marked
        `final`. That is what gets a real first sentence to a child in ~3 s instead of
        waiting 18-45 s for a whole completion — see
        docs/architecture/mqtt-and-conversation.md §4.5.

        Returning None (or raising) is always safe: the runtime falls straight back to
        `respond`, so an app that never heard of streaming behaves exactly as before."""
        return None

    # --- optional lifecycle hooks ---
    def on_connect(self, robot: RobotContext) -> None:
        """Called when a robot comes online (after config is pushed)."""

    def on_disconnect(self, robot: RobotContext) -> None:
        """Called when a robot goes offline."""

    def on_event(self, robot: RobotContext, name: str, payload: dict) -> None:
        """Called for non-conversation events (vision events like found-face,
        module lifecycle, etc.). Useful for reactive/game behavior."""

    def greeting(self, robot: RobotContext) -> Optional[Reply]:
        """Optional opening line when a session starts."""
        return None

    def on_session_end(self, robot: RobotContext, history: list,
                       reason: str = "") -> None:
        """Called when a conversation *finishes* — the module exited (`<exit>` / an EXIT
        action), the robot switched to another module, or it went offline.

        This is the contract's `complete_handler` moment
        (docs/architecture/content-module-contract.md → the `code` hooks): the last point
        at which the whole transcript still exists, and therefore where long-term memory
        is written (`ContentApp` summarizes it into `volley.persist_data`).

        `reason` is one of "exit" / "module_switch" / "disconnect". Called off the MQTT
        loop; it may take as long as a brain call, and anything it raises is swallowed by
        the runtime — a failed summary must never end a child's session badly."""
