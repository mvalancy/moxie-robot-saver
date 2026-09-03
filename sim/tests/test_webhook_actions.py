"""
`WebhookApp` must strip its own tags — the external brain that spoke its markup aloud.

`WebhookApp` is how a service outside this repo becomes Moxie's brain: the runtime POSTs
the turn, the service answers `{"text", "markup", "actions", "end_turn"}`. Its contract
lets a service name `actions` outright, and `sim/tests/test_e2e_actions_to_robot.py`
already proves those reach the robot.

But an action tag written INLINE — `<launch:DRAW>`, `<exit>`, `<sleep>`, the grammar
`mqtt/moxie_sdk/actions.py` defines and teaches to every model — was handled by
`LLMApp.respond` and by `ContentApp` (`content_app.py:141-142, :217`) and by NOTHING in
`WebhookApp._json_to_reply`. So an external brain writing the tag the way our own prompt
teaches got the worst of both: the tag was **spoken to the child verbatim** ("Let's draw
less-than launch colon DRAW greater-than") and it produced **no action at all**.

These tests pin the fix from both sides — the tag never reaches the spoken text, and the
action it asked for still fires — and pin the properties that make it safe: declared
`actions` still work, the two sources compose, behavior markup (`<mark .../>`) is not
touched, and a tag we do not own is left alone rather than eaten.

Hermetic: `_post` is stubbed, so everything after the one network call is the shipped
code. No broker, no network, no gateway.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (os.path.join(REPO, "mqtt"), os.path.join(REPO, "mqtt", "supervisor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from moxie_sdk.apps import WebhookApp           # noqa: E402
from moxie_sdk.types import (ActionType, ChildProfile, RobotContext,  # noqa: E402
                             Turn)


def _brain(answer: dict) -> WebhookApp:
    """The real app with only its single HTTP call replaced by a canned answer."""
    class _Stub(WebhookApp):
        def _post(self, path_hint, body):
            return dict(answer)
    return _Stub("http://127.0.0.1:1/turn")


def _turn(speech="can we draw?") -> Turn:
    return Turn(robot=RobotContext(device_id="d_webhook",
                                   child=ChildProfile(nickname="Sam")),
                speech=speech)


# --------------------------------------------------------------------------- #
# The bug this file exists for
# --------------------------------------------------------------------------- #
def test_a_launch_tag_never_reaches_the_spoken_text():
    reply = _brain({"text": "Yes! Let's draw. <launch:DRAW:default>"}).respond(_turn())
    assert reply.text == "Yes! Let's draw.", reply.text
    assert "<launch" not in reply.text


def test_the_launch_tag_still_becomes_a_real_action():
    reply = _brain({"text": "Yes! Let's draw. <launch:DRAW:default>"}).respond(_turn())
    assert len(reply.actions) == 1, reply.actions
    a = reply.actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "DRAW", "default")


def test_exit_and_sleep_tags_are_consumed_too():
    for tag, kind in (("<exit>", ActionType.EXIT), ("<sleep>", ActionType.SLEEP)):
        reply = _brain({"text": f"Okay. {tag}"}).respond(_turn())
        assert reply.text == "Okay.", (tag, reply.text)
        assert [x.type for x in reply.actions] == [kind], (tag, reply.actions)


def test_a_tag_in_the_markup_is_stripped_from_the_markup_as_well():
    """`markup` is what a robot *performs*, and it is a second place a tag can hide.
    It is stripped for its text, and the action is NOT counted twice (the text field is
    the one that declares it)."""
    reply = _brain({"text": "Bye Sam! <exit>",
                    "markup": "Bye Sam! <exit>"}).respond(_turn("bye"))
    assert "<exit>" not in (reply.markup or ""), reply.markup
    assert [x.type for x in reply.actions] == [ActionType.EXIT], reply.actions


# --------------------------------------------------------------------------- #
# …without breaking what already worked
# --------------------------------------------------------------------------- #
def test_declared_actions_still_arrive_and_bogus_types_are_still_dropped():
    reply = _brain({"text": "Let's play a game!",
                    "actions": [{"type": "launch", "module_id": "GAME",
                                 "content_id": "level1"},
                                {"type": "not-a-real-action"}]}).respond(_turn())
    assert reply.text == "Let's play a game!"
    assert len(reply.actions) == 1, reply.actions
    a = reply.actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "GAME", "level1")


def test_a_service_may_use_both_and_the_declared_one_comes_first():
    reply = _brain({"text": "One more, then bed. <exit>",
                    "actions": [{"type": "launch", "module_id": "GAME"}]}
                   ).respond(_turn())
    assert reply.text == "One more, then bed."
    assert [x.type for x in reply.actions] == [ActionType.LAUNCH, ActionType.EXIT]


def test_behavior_markup_is_not_a_tag_and_survives_untouched():
    """`<mark .../>` is the robot's own behavior language, not one of the four names we
    claim. A blanket "strip every angle bracket" would eat it — this asserts we don't."""
    markup = ('<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
              'Happy birthday!')
    reply = _brain({"text": "Happy birthday!", "markup": markup}).respond(_turn())
    assert reply.markup == markup, reply.markup
    assert reply.actions == []


def test_an_untagged_answer_is_unchanged():
    reply = _brain({"text": "Tell me about it!", "end_turn": True}).respond(_turn())
    assert reply.text == "Tell me about it!"
    assert reply.actions == [] and reply.end_turn is True


def test_an_unreachable_service_still_degrades_the_way_it_did():
    class _Dead(WebhookApp):
        def _post(self, path_hint, body):
            return None
    reply = _Dead("http://127.0.0.1:1/turn").respond(_turn())
    assert "trouble" in reply.text.lower() and reply.actions == []
