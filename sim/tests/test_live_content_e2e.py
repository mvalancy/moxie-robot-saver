"""
Live content-module end-to-end — the shipped module, the real runtime, the real brain.

`test_live_gateway.py` proves the gateway answers and that a turn survives the
runtime. This file proves the thing DoD criterion 6 actually asks for: that
`mqtt/content_modules/starter.json` — the module we ship, not a test fixture — driven
by a REAL gateway completion, comes back on the wire as a **spec-conformant**
`RemoteChatResponse` (result SUCCESS, non-empty `output.text` AND `output.markup`,
`event_id` echoed), and that a `globals[]` entry short-circuits the turn **without
spending an LLM call at all**.

Runs only when a gateway key is present (`MOXIE_LLM_API_KEY` / `LITELLM_MASTER_KEY`,
e.g. from the git-ignored `mqtt/.env`); skips cleanly otherwise so CI stays green.
Deliberately frugal: ONE live completion for the whole module — the global-handler
test asserts the count stays at zero, so it costs nothing.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import assert_spec_response, drive_once, load_repo_dotenv  # noqa: E402

STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")


load_repo_dotenv()          # mqtt/.env from this tree or the main checkout
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

pytestmark = pytest.mark.skipif(
    not KEY, reason="no gateway key (set MOXIE_LLM_API_KEY in mqtt/.env for live tests)")


class _CountingChat:
    """Wraps the live chat callable so a test can assert how many completions a turn
    really cost — the only way to prove a global short-circuited the brain."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.last_messages = None

    def __call__(self, messages):
        self.calls += 1
        self.last_messages = messages
        return self._inner(messages)


def _shipped_app(**kw):
    """ContentApp over the module we actually ship, on the live gateway."""
    from moxie_sdk.chat import make_openai_chat
    from moxie_sdk.content import ContentApp, load_modules
    with open(STARTER) as fh:
        module = load_modules(json.load(fh))
    chat = _CountingChat(make_openai_chat(BASE, KEY, MODEL, max_tokens=96))
    return ContentApp(module, chat, **kw), chat, module


def _sdk_or_skip():
    try:
        import openai  # noqa: F401
        import moxie_sdk.content  # noqa: F401
    except Exception as e:
        pytest.skip(f"SDK/openai unavailable: {e}")
    pytest.importorskip("paho.mqtt.client")   # runtime imports it lazily, but be honest
    pytest.importorskip("jinja2")             # the module's prompt is a Jinja template


def test_shipped_module_live_turn_is_a_spec_conformant_response():
    """starter.json + a real graphling completion + the real MoxieRuntime → the exact
    RemoteChatResponse a robot would receive. One live call."""
    _sdk_or_skip()
    app, chat, module = _shipped_app()
    conv = module.conversations[0]
    resp = drive_once(app, "What's your favorite animal, and why?",
                      device_id="d_live_content", module_id=conv.module_id,
                      content_id=conv.content_id, event_id="evt-live-1")
    assert_spec_response(resp, event_id="evt-live-1")
    text = resp["output"]["text"]
    print(f"\n[live] result={resp['result']} len(text)={len(text)} "
          f"len(markup)={len(resp['output']['markup'])} text={text!r}")
    assert chat.calls == 1, f"expected exactly one gateway call, got {chat.calls}"
    # The module's prompt really reached the model (rendered, not raw Jinja).
    system = chat.last_messages[0]
    assert system["role"] == "system" and "{{" not in system["content"]
    assert "Sam" in system["content"], "child_pii never rendered into the prompt"
    # A real answer, not a stub or an apology for being offline.
    assert len(text) > 10, f"suspiciously short live reply: {text!r}"
    assert resp["output"]["markup"], "markup must never be empty on the wire"


def test_a_global_short_circuits_the_turn_with_no_llm_call():
    """starter.json's `globals[]` Timer regex is matched BEFORE the conversation, so a
    registered handler answers with zero gateway calls. Same runtime, same wire shape —
    the difference is that the brain is never asked."""
    _sdk_or_skip()

    def timer_handler(volley, session):
        amount, unit = (volley.entities + ["5", "minute"])[:2]
        volley.set_output(f"Okay! A timer for {amount} {unit}s. Go!")
        volley.add_execution_action("eb_timer_request", [amount, unit])

    app, chat, module = _shipped_app(global_handlers={"Timer": timer_handler})
    conv = module.conversations[0]
    resp = drive_once(app, "set a timer for 5 minutes please",
                      device_id="d_live_global", module_id=conv.module_id,
                      content_id=conv.content_id, event_id="evt-live-2")
    assert_spec_response(resp, event_id="evt-live-2")
    assert chat.calls == 0, "a matched global must not spend an LLM call"
    assert resp["output"]["text"] == "Okay! A timer for 5 minutes. Go!"


def test_an_unmatched_global_falls_through_to_the_conversation():
    """The short-circuit is a match, not a bypass: speech the Timer regex does not
    match must still reach the brain. Asserted WITHOUT a live call by failing the
    chat callable loudly — reaching it is the proof."""
    _sdk_or_skip()
    from moxie_sdk.content import ContentApp, load_modules
    from moxie_sdk.types import ChildProfile, RobotContext, Turn

    reached = []

    def _brain(messages):
        reached.append(messages)
        return "Ask me anything!"

    with open(STARTER) as fh:
        module = load_modules(json.load(fh))
    app = ContentApp(module, _brain,
                     global_handlers={"Timer": lambda v, s: v.set_output("timer!")})
    conv = module.conversations[0]
    robot = RobotContext(device_id="d1", child=ChildProfile(nickname="Sam"),
                         module_id=conv.module_id, content_id=conv.content_id)
    reply = app.respond(Turn(robot=robot, speech="what is a timer, anyway?"))
    assert reached, "an unmatched global swallowed the turn"
    assert reply.text == "Ask me anything!"
