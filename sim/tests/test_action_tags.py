"""
Action tags — the model's own text driving the robot (OpenMoxie audit ADOPT #4).

Four layers, bottom-up:
  1. the pure parser (`moxie_sdk/actions.py`) — every tag form, tolerance, cleanliness
  2. LLMApp.respond — a tagged model line becomes Reply.text + Reply.actions
  3. ContentApp — same, for the content engine (model path and global-handler path)
  4. the real MoxieRuntime — the action reaches the wire as `response_actions`
"""
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.actions import LAUNCH_IF_CONFIRMED_AS, parse_action_tags  # noqa: E402
from moxie_sdk.content import ContentApp, load_module            # noqa: E402
from moxie_sdk.content.volley import Volley, Session             # noqa: E402
from moxie_sdk.types import (ActionType, ChildProfile, RobotContext,  # noqa: E402
                             Turn)


# ---------------------------------------------------------------- 1. the parser

def test_no_tags_is_a_passthrough():
    text, actions = parse_action_tags("Hi Sam! What did you build today?")
    assert text == "Hi Sam! What did you build today?"
    assert actions == []


def test_empty_and_none_are_safe():
    assert parse_action_tags("") == ("", [])
    assert parse_action_tags(None) == ("", [])


#: (model line, spoken text, [(type, module_id, content_id)]) — every well-formed tag.
TAGGED = [
    ("Bye bye, see you tomorrow! <exit>", "Bye bye, see you tomorrow!",
     [(ActionType.EXIT, None, None)]),
    ("<sleep>Okay, goodnight.", "Okay, goodnight.", [(ActionType.SLEEP, None, None)]),
    ("Let's draw! <launch:DRAW>", "Let's draw!", [(ActionType.LAUNCH, "DRAW", None)]),
    ("Great pick. <launch:DRAW:default>", "Great pick.",
     [(ActionType.LAUNCH, "DRAW", "default")]),
    # tag names are case-insensitive, ids are not
    ("ok <LAUNCH:DRAW:Default> <ExIt>", "ok",
     [(ActionType.LAUNCH, "DRAW", "Default"), (ActionType.EXIT, None, None)]),
    ("hi < launch : DRAW : default >", "hi", [(ActionType.LAUNCH, "DRAW", "default")]),
    ("bye <  exit  >", "bye", [(ActionType.EXIT, None, None)]),
    # several tags keep source order
    ("a <sleep> b <launch:GAME> c <exit> d", "a b c d",
     [(ActionType.SLEEP, None, None), (ActionType.LAUNCH, "GAME", None),
      (ActionType.EXIT, None, None)]),
    ("<exit>", "", [(ActionType.EXIT, None, None)]),        # a bare tag leaves no text
]


@pytest.mark.parametrize("line,text,want", TAGGED)
def test_a_tag_becomes_an_action_and_leaves_the_spoken_line(line, text, want):
    got_text, actions = parse_action_tags(line)
    assert got_text == text
    assert [(a.type, a.module_id, a.content_id) for a in actions] == want


def test_launch_if_confirmed_maps_to_the_contract_we_have():
    """Our ActionType has no confirm variant yet — actions.py maps it to LAUNCH and
    says so. This test pins the mapping so a future confirm member trips it."""
    for tag in ("<launch_if_confirmed:DRAW>", "<launch_if_confirmed:DRAW:default>"):
        text, actions = parse_action_tags("Want to draw? " + tag)
        assert text == "Want to draw?"
        assert actions[0].type is LAUNCH_IF_CONFIRMED_AS
        assert actions[0].module_id == "DRAW"
    assert LAUNCH_IF_CONFIRMED_AS is ActionType.LAUNCH   # documented caveat, not a wish


@pytest.mark.parametrize("bad", [
    "<launch>",                 # no module
    "<launch:>",                # empty module
    "<launch::default>",        # empty module, content given
    "<launch:A:B:C>",           # too many fields — a wrong module is worse than none
    "<exit:now>",               # exit takes no fields
    "<sleep:8>",                # sleep takes no fields
])
def test_malformed_tags_yield_no_action_but_are_never_spoken(bad):
    text, actions = parse_action_tags("Okay. " + bad + " Bye.")
    assert actions == []
    assert text == "Okay. Bye."
    assert "<" not in text and ">" not in text


def test_tags_we_do_not_own_are_left_alone():
    """Behavior markup (`<mark .../>`) and content openers (`<opener>`) are live
    syntax — a blanket 'strip every <...>' would eat them."""
    markup = '<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hello!'
    assert parse_action_tags(markup) == (markup, [])
    text, actions = parse_action_tags("Hi there!<opener> <exit>")
    assert text == "Hi there!<opener>"
    assert [a.type for a in actions] == [ActionType.EXIT]


def test_spoken_text_is_tidied_after_the_tag_is_removed():
    assert parse_action_tags("Bye <exit> !")[0] == "Bye!"
    assert parse_action_tags("  <exit>  Bye now.  ")[0] == "Bye now."
    assert parse_action_tags("Let's <launch:DRAW> draw")[0] == "Let's draw"
    assert parse_action_tags("one\n\n<exit>\n\ntwo")[0] == "one\n\ntwo"


# ---------------------------------------------------------------- 2. LLMApp

class _FakeCompletion:
    """Stands in for the OpenAI client: one canned assistant message."""

    def __init__(self, content):
        self._content = content
        self.chat = self
        self.completions = self
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(kwargs["messages"])
        msg = type("M", (), {"content": self._content})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _llm_app(canned):
    from moxie_sdk.apps import LLMApp
    fake = _FakeCompletion(canned)
    # The fake IS the client (no network, and no openai import — LLMApp only builds a
    # real one when no client is handed to it), so these tag rules are checked on a
    # bare interpreter instead of being skipped wherever openai is not installed.
    app = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="sk-not-used", model="test",
                 client=fake)
    return app, fake


def test_llm_app_lifts_a_launch_tag_out_of_the_spoken_line():
    app, _ = _llm_app('{"say": "Yes! Let\'s draw. <launch:DRAW:default>", '
                      '"mood": "positive", "gesture": "celebrate"}')
    reply = app.respond(Turn(robot=RobotContext(device_id="d1"), speech="can we draw?"))
    assert reply.text == "Yes! Let's draw."
    assert "<launch" not in (reply.markup or "")
    assert len(reply.actions) == 1
    a = reply.actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "DRAW", "default")


def test_llm_app_exit_tag_on_a_goodbye():
    app, _ = _llm_app('{"say": "Bye Sam! <exit>", "mood": "positive", "gesture": "talk"}')
    reply = app.respond(Turn(robot=RobotContext(device_id="d1"), speech="bye moxie"))
    assert reply.text == "Bye Sam!"
    assert [x.type for x in reply.actions] == [ActionType.EXIT]


def test_llm_app_untagged_reply_is_unchanged():
    app, _ = _llm_app('{"say": "Tell me about it!", "mood": "positive", "gesture": "question"}')
    reply = app.respond(Turn(robot=RobotContext(device_id="d1"), speech="hi"))
    assert reply.text == "Tell me about it!"
    assert reply.actions == []


def test_llm_app_records_the_direct_request_attempt():
    from moxie_sdk.chat import model_calls, reset_model_calls
    reset_model_calls()
    app, _ = _llm_app('{"say": "Hello!", "mood": "happy"}')
    app.respond(Turn(robot=RobotContext(device_id="d1"), speech="hi"))
    assert model_calls("chat") == 1
    reset_model_calls()


def test_llm_app_teaches_the_model_the_tags():
    app, fake = _llm_app('{"say": "hi"}')
    app.respond(Turn(robot=RobotContext(device_id="d1"), speech="hi"))
    system = fake.seen[0][0]
    assert system["role"] == "system"
    assert all(tag in system["content"] for tag in ("<exit>", "<sleep>", "<launch:MODULE>"))


# ---------------------------------------------------------------- 3. ContentApp

MODULE = {
    "conversations": [{
        "name": "Chat", "module_id": "CHAT", "content_id": "default",
        "max_history": 10, "max_volleys": 30,
        "opener": "Hi there!",
        "prompt": "You are Moxie talking to {{ volley.config.child_pii.nickname }}.",
    }],
    "globals": [{"name": "Draw", "pattern": r"let'?s draw"}],
}


def _robot():
    return RobotContext(device_id="d1", child=ChildProfile(nickname="Sam"),
                        module_id="CHAT", content_id="default")


def test_content_app_model_line_yields_an_action_and_clean_text():
    app = ContentApp(load_module(MODULE),
                     lambda m: "Okay, drawing time! <launch:DRAW:default>")
    reply = app.respond(Turn(robot=_robot(), speech="can we draw"))
    assert reply.text == "Okay, drawing time!"
    a = reply.actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "DRAW", "default")


def test_content_app_global_handler_output_is_parsed_too():
    def handler(volley: Volley, session: Session):
        volley.set_output("Sure, let's go! <launch:DRAW>")

    app = ContentApp(load_module(MODULE), lambda m: "unused",
                     global_handlers={"Draw": handler})
    reply = app.respond(Turn(robot=_robot(), speech="let's draw"))
    assert reply.text == "Sure, let's go!"
    assert [x.module_id for x in reply.actions] == ["DRAW"]


def test_content_app_reply_with_only_a_tag_keeps_the_action():
    app = ContentApp(load_module(MODULE), lambda m: "<exit>")
    reply = app.respond(Turn(robot=_robot(), speech="bye"))
    assert reply.text == ""
    assert [x.type for x in reply.actions] == [ActionType.EXIT]


# ---------------------------------------------------------------- 4. the wire

def _drive(app, device_id="d_tags", speech="can we draw"):
    from helpers_runtime import drive_turn, make_runtime
    rt, _ = make_runtime(app, device_id=device_id, module_id="CHAT")
    return drive_turn(rt, device_id, speech, event_id="evt-tag")


@pytest.mark.parametrize("line,text,action", [
    ("Sure! Let's draw. <launch:DRAW:default>", "Sure! Let's draw.",
     {"action": "launch", "module_id": "DRAW", "content_id": "default"}),
    ("Bye Sam! <exit>", "Bye Sam!", {"action": "exit"}),
])
def test_a_tag_in_model_text_reaches_the_wire_as_response_actions(line, text, action):
    """End to end: the brain writes a tag → RemoteChatResponse carries the action and the
    spoken text is clean."""
    pytest.importorskip("paho.mqtt.client")
    resp = _drive(ContentApp(load_module(MODULE), lambda m: line))
    assert resp["command"] == "remote_chat" and resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == text
    assert "<" not in resp["output"]["text"] and "<launch" not in resp["output"]["markup"]
    (ra,) = resp["response_actions"]
    assert {k: ra.get(k) for k in action} == action
