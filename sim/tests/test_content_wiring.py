"""
M2 wiring tests — the shipped example module runs through ContentApp, the templated
opener renders, and the AI-seam offline/soft-error handling behaves per ai-seam.md §2.
Pure (no openai/broker); runs in CI's pytest.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

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


# --------------------------------------------------------------------------- #
# The always-listening commands (2026-09-08)
# --------------------------------------------------------------------------- #
# `docs/reverse-engineering/runtime/content-and-conversation.md`:136-138 recovered the ten
# phrases the real robot recognised at any time, independent of the running activity:
# Sleep, WakeUp, Hello, ListenToMe, Earmuffs, HoldOn, RepeatThat, SpeakLouder, SpeakSofter,
# SomethingElse. The shipped module carried none of them.
#
# THE FAILURE MODE HERE IS OVER-MATCHING, AND IT IS SILENT. A global short-circuits BEFORE
# the brain, so a pattern one word too loose does not raise anything — it quietly answers a
# real sentence with a canned line, and the only symptom is a robot that has become
# strangely wooden. So both directions are asserted: the command fires with NO llm call,
# and the sentence that merely contains its words does not.
#
# `Hello` is deliberately NOT authored despite being on the list: greeting is exactly what
# free chat does well, and short-circuiting it to a fixed string would make her less like
# Moxie, not more.
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
    """It says the line; it does not stop the microphone or drive the Earmuffs
    engagement state, because this sim has no such wiring. A global that CLAIMED to stop
    listening while still listening would be a lie told to a child, so the copy is pinned
    to the honest half — and this test is what makes the gap deliberate rather than
    forgotten."""
    app, _ = _counting_app()
    reply = app.respond(Turn(robot=_robot(), speech="earmuffs")).text.lower()
    assert "not listening" in reply
    assert "say earmuffs off" in reply, "the child is told how to undo it"
