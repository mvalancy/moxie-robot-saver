"""
M2 wiring tests — the shipped example module runs through ContentApp, the templated
opener renders, and the AI-seam offline/soft-error handling behaves per ai-seam.md §2.
Pure (no openai/broker); runs in CI's pytest.
"""
import json
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.content import load_modules, ContentApp  # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile, ResultCode  # noqa: E402
from moxie_sdk.chat import is_offline_error  # noqa: E402

STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")


def _app(chat):
    with open(STARTER) as fh:
        module = load_modules(json.load(fh))
    return ContentApp(module, chat, persona="P")


def _robot(nickname="Sam"):
    return RobotContext(device_id="d1", child=ChildProfile(nickname=nickname),
                        module_id="FREE_CHAT", content_id="default")


def test_shipped_module_is_valid_and_runs():
    app = _app(lambda messages: "Let's talk about dinosaurs!")
    reply = app.respond(Turn(robot=_robot(), speech="hi"))
    assert reply.text == "Let's talk about dinosaurs!"


def test_shipped_opener_renders_nickname():
    app = _app(lambda m: "x")
    g = app.greeting(_robot("Robin"))
    assert g is not None
    assert g.text == "Hi Robin! What do you want to talk about?"


def test_offline_endpoint_yields_error_offline():
    def dead(messages):
        raise ConnectionError("connection refused")
    reply = _app(dead).respond(Turn(robot=_robot(), speech="hi"))
    assert reply.result_code is ResultCode.ERROR_OFFLINE


def test_soft_error_keeps_talking():
    def boom(messages):
        raise ValueError("bad json from model")
    reply = _app(boom).respond(Turn(robot=_robot(), speech="hi"))
    assert reply.result_code is ResultCode.SUCCESS
    assert reply.text and "fuzzy" in reply.text.lower()


def test_is_offline_error_classification():
    assert is_offline_error(ConnectionError()) is True
    assert is_offline_error(TimeoutError()) is True
    assert is_offline_error(ValueError()) is False


# --- The always-listening commands ---
# A global answers BEFORE the brain, so over-matching is silent; both directions are
# asserted (content-and-conversation.md:123-125). `Hello` is deliberately free chat's.
def _counting_app():
    calls = []

    def chat(messages):
        calls.append(messages)
        return "FREE CHAT ANSWERED"

    return _app(chat), calls


def test_always_listening_commands_fire_without_spending_a_turn():
    for speech, expect in [
        ("hold on", "wait right here"),
        ("hang on a second", "wait right here"),
        ("can we do something else", "what would you like to do instead"),
        ("earmuffs", "earmuffs on"),
    ]:
        app, calls = _counting_app()
        reply = app.respond(Turn(robot=_robot(), speech=speech))
        assert expect in reply.text.lower(), f"{speech!r} -> {reply.text!r}"
        assert not calls, f"{speech!r} spent an LLM call; a global must short-circuit"


def test_an_ordinary_sentence_is_not_hijacked_by_a_global():
    # "wait a long time" contains "wait a"; "something else happened" contains
    # "something else". Both are ordinary speech and must reach the brain.
    for speech in [
        "I had to wait a long time at school",
        "hello moxie",
        "tell me about elephants",
        "my mum said something else happened at work",
    ]:
        app, calls = _counting_app()
        reply = app.respond(Turn(robot=_robot(), speech=speech))
        assert reply.text == "FREE CHAT ANSWERED", f"{speech!r} was hijacked -> {reply.text!r}"
        assert calls, f"{speech!r} never reached the brain"


def test_earmuffs_promises_only_what_it_actually_does():
    """It cannot stop the microphone, so the copy is pinned to the honest half."""
    app, _ = _counting_app()
    reply = app.respond(Turn(robot=_robot(), speech="earmuffs")).text.lower()
    assert "not listening" in reply
    assert "say earmuffs off" in reply, "the child is told how to undo it"


# --- No shipped global may fire and do nothing ---
# A matched global with no way to act falls through to free chat, indistinguishable from
# never matching — so this guard is structural.
def _shipped_globals():
    with open(STARTER) as fh:
        return json.load(fh)["globals"]


def test_every_shipped_global_can_actually_do_something():
    dead = [g["name"] for g in _shipped_globals() if not g.get("extension")]
    assert not dead, (
        f"these shipped globals match and then fall through to free chat: {dead}. "
        "A global needs an `extension` (or a handler registered in production, which "
        "nothing does) or it silently does nothing — indistinguishable from never matching."
    )


def test_every_shipped_extension_actually_runs_under_its_shipped_grants():
    """Declaring a capability is not being granted one: a shipped extension (digest in the
    baseline) gets `SHIPPED_EXTRA_GRANTS` + defaults; anything else fails open at runtime and
    lands in free chat like a dead global."""
    from moxie_sdk.content.content_app import SHIPPED_EXTRA_GRANTS
    from moxie_sdk.content import ext as E

    allowed = set(E.DEFAULT_GRANTS) | set(SHIPPED_EXTRA_GRANTS)
    for g in _shipped_globals():
        block = g.get("extension") or {}
        declared = set(block.get("capabilities") or [])
        missing = sorted(declared - allowed)
        assert not missing, (
            f"shipped global {g['name']!r} declares {missing}, which is not in "
            f"DEFAULT_GRANTS | SHIPPED_EXTRA_GRANTS — it would load and then quietly "
            f"refuse at runtime, falling through to free chat."
        )


def test_the_timer_actually_sets_a_timer():
    """The behaviour: `eb_timer_request` (a recovered function) with args (1 = start, duration
    in ms), checked at three points — an argument-swapped draft failed open silently."""
    from moxie_sdk.content import packs as P
    with open(STARTER) as fh:
        raw = json.load(fh)
    app = ContentApp(load_modules(raw), lambda m: "FREE CHAT", persona="P",
                     content_defaults=P.shipped_items(raw))
    for speech, words, ms in [
        ("set a timer for 5 minute", "5 minutes", "300000"),
        ("set a timer for 1 minute", "1 minute", "60000"),   # singular, not "1 minutes"
        ("timer for 30 second", "30 seconds", "30000"),
    ]:
        reply = app.respond(Turn(robot=_robot(), speech=speech))
        assert reply.text != "FREE CHAT", f"{speech!r} fell through to the brain"
        assert words in reply.text, f"{speech!r} -> {reply.text!r}"
        assert [a.function for a in reply.actions] == ["eb_timer_request"]
        assert reply.actions[0].args == ["1", ms], f"{speech!r} -> {reply.actions[0].args}"
