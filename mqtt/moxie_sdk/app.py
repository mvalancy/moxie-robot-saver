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

        Each chunk is published as its own REPLY_PENDING response and the `final` one
        closes the turn (mqtt-and-conversation.md §4.5). Returning None (or raising)
        falls back to `respond`."""
        return None

    # --- optional: be WOKEN by something the robot noticed ---
    def perceive(self, turn: Turn) -> Optional[Reply]:
        """A subscribed robot event reached the server — answer it, or return **None**.

        `turn.speech` is the event name (`eb-qr-event`, …) as the robot delivers it
        (vision.md §7.1); `turn.input_vars` carries its payload (`$eb_qr_value`, …).

        **Must not call a model**: `eb-found-face` fires every time a child walks into
        frame (tests assert this via `chat.model_calls()`). None means "nothing to say" and
        the runtime does its own presence handling (greeting, launch card, `NOREPLY_ACK`).

        Called only for events the app subscribed to (`Reply.subscribe`), on that module,
        never under `MOXIE_VISION=0` or for an unpermitted robot.
        """
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

        The contract's `complete_handler` moment (content-module-contract.md): the last
        point the whole transcript exists, so long-term memory is written here. `reason`
        is "exit" / "module_switch" / "disconnect". Runs off the MQTT loop; exceptions are
        swallowed."""
