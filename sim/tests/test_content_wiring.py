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


# --------------------------------------------------------------------------- #
# No shipped global may fire and do nothing (2026-09-08)
# --------------------------------------------------------------------------- #
# `Timer` matched and did nothing from the day it was written. `register_global` is never
# called in production, and it carried no `extension`, so `respond` fell through to free
# chat: a child asking for a timer got a conversational reply and no timer.
#
# NOTHING ANYWHERE RAISED, and it could not have. A global that fires and does nothing
# produces the same observable turn as one that never matched — free chat answers either
# way — so no assertion about the REPLY can tell them apart. That is why this guard is
# structural: it asks whether each shipped global has any way to act at all, which is a
# property of the module rather than of a turn.
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
    """Declaring a capability is not being granted one.

    A shipped extension is trusted only when its digest is in the recorded baseline, and
    it then gets `SHIPPED_EXTRA_GRANTS` on top of the four defaults. An extension that
    declares something outside that set loads fine and then **fails open at runtime** —
    `[ext] … stopped: has not been granted: …; Moxie carried on without it` — which lands
    the turn in free chat looking exactly like the dead global above.
    """
    from moxie_sdk.content import packs as P
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
    """The behaviour, not just the wiring: the right action with the right milliseconds.

    `eb_timer_request` is a RECOVERED robot function (`ext.ACTION_WORDS`), and args are
    (action=1 start, duration in ms). The arithmetic is asserted at three points because
    the first version of this program had `plural` argument-swapped, which made the whole
    rule fail open — the extension layer logged and carried on, so the symptom was again
    an ordinary free-chat reply.
    """
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
