"""
`WebhookApp` must strip its own tags — an external brain must not speak its markup aloud.

A webhook service answers `{"text", "markup", "actions", "end_turn"}`; declared `actions`
already reach the robot (`test_actions_reach_the_robot.py`). An INLINE tag (`<launch:DRAW>`,
`<exit>`, `<sleep>` — the grammar `actions.py` teaches every model) must likewise be lifted
out of the spoken text and still fire, as `LLMApp` and `ContentApp` do. Also pinned: declared
actions still work, both sources compose, `<mark .../>` markup is untouched, and tags we do
not own are left alone.

Hermetic: `_post` is stubbed; everything after the one network call is shipped code.
"""
import pytest

from moxie_sdk.apps import WebhookApp
from moxie_sdk.types import ActionType, ChildProfile, RobotContext, Turn


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
@pytest.mark.parametrize("tag,want", [
    ("<launch:DRAW:default>", (ActionType.LAUNCH, "DRAW", "default")),
    ("<exit>", (ActionType.EXIT, None, None)),
    ("<sleep>", (ActionType.SLEEP, None, None)),
])
def test_an_inline_tag_is_lifted_out_of_the_spoken_text_and_still_fires(tag, want):
    reply = _brain({"text": f"Okay. {tag}"}).respond(_turn())
    assert reply.text == "Okay.", reply.text
    assert [(a.type, a.module_id, a.content_id) for a in reply.actions] == [want]


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
def test_a_service_may_use_both_and_the_declared_one_comes_first():
    reply = _brain({"text": "One more, then bed. <exit>",
                    "actions": [{"type": "launch", "module_id": "GAME"}]}
                   ).respond(_turn())
    assert reply.text == "One more, then bed."
    assert [x.type for x in reply.actions] == [ActionType.LAUNCH, ActionType.EXIT]


def test_the_documented_exit_alias_is_read_and_spelled_exit_module_on_the_wire():
    """`{"type": "exit"}` is the alias the contract documents (webhook_app.py:11-12,
    moxie-as-a-platform.md); `ActionType._missing_` reads it and the wire still carries
    the recovered ActionID name `exit_module` (RemoteChat.proto:260)."""
    from moxie_sdk.wire import build_chat_response
    reply = _brain({"text": "Bye!", "actions": [{"type": "exit"}]}).respond(_turn("bye"))
    assert [x.type for x in reply.actions] == [ActionType.EXIT], reply.actions
    on_wire = build_chat_response("e", reply.text, actions=reply.actions)
    assert [a["action"] for a in on_wire["response_actions"]] == ["exit_module"]


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
