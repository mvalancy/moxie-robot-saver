"""
Action tags — the model's own text driving the robot (OpenMoxie audit ADOPT #4).

Four layers, bottom-up:
  1. the pure parser (`moxie_sdk/actions.py`) — every tag form, tolerance, cleanliness
  2. LLMApp.respond — a tagged model line becomes Reply.text + Reply.actions
  3. ContentApp — same, for the content engine (model path and global-handler path)
  4. the real MoxieRuntime — the action reaches the wire as `response_actions`

Layer 4 deliberately re-implements the tiny fake-transport helper rather than
importing it from test_runtime_turn.py: these files are edited independently and a
test that shares fixtures across files fails for reasons that have nothing to do
with the thing under test.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

from moxie_sdk.actions import (ACTION_TAG_PROMPT, LAUNCH_IF_CONFIRMED_AS,  # noqa: E402
                               parse_action_tags)
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


def test_exit_tag():
    text, actions = parse_action_tags("Bye bye, see you tomorrow! <exit>")
    assert text == "Bye bye, see you tomorrow!"
    assert len(actions) == 1
    assert actions[0].type is ActionType.EXIT
    assert actions[0].module_id is None and actions[0].content_id is None


def test_sleep_tag():
    text, actions = parse_action_tags("<sleep>Okay, goodnight.")
    assert text == "Okay, goodnight."
    assert [a.type for a in actions] == [ActionType.SLEEP]


def test_launch_module_only():
    text, actions = parse_action_tags("Let's draw! <launch:DRAW>")
    assert text == "Let's draw!"
    a = actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "DRAW", None)


def test_launch_module_and_content():
    text, actions = parse_action_tags("Great pick. <launch:DRAW:default>")
    assert text == "Great pick."
    a = actions[0]
    assert (a.type, a.module_id, a.content_id) == (ActionType.LAUNCH, "DRAW", "default")


def test_launch_if_confirmed_maps_to_the_contract_we_have():
    """Our ActionType has no confirm variant yet — actions.py maps it to LAUNCH and
    says so. This test pins the mapping so a future confirm member trips it."""
    for tag in ("<launch_if_confirmed:DRAW>", "<launch_if_confirmed:DRAW:default>"):
        text, actions = parse_action_tags("Want to draw? " + tag)
        assert text == "Want to draw?"
        assert actions[0].type is LAUNCH_IF_CONFIRMED_AS
        assert actions[0].module_id == "DRAW"
    assert LAUNCH_IF_CONFIRMED_AS is ActionType.LAUNCH   # documented caveat, not a wish


def test_tag_names_are_case_insensitive_but_ids_are_not():
    text, actions = parse_action_tags("ok <LAUNCH:DRAW:Default> <ExIt>")
    assert text == "ok"
    assert actions[0].module_id == "DRAW" and actions[0].content_id == "Default"
    assert actions[1].type is ActionType.EXIT


def test_whitespace_inside_a_tag_is_tolerated():
    _, actions = parse_action_tags("hi < launch : DRAW : default >")
    a = actions[0]
    assert (a.module_id, a.content_id) == ("DRAW", "default")
    assert parse_action_tags("bye <  exit  >")[1][0].type is ActionType.EXIT


def test_multiple_tags_keep_source_order():
    text, actions = parse_action_tags("a <sleep> b <launch:GAME> c <exit> d")
    assert text == "a b c d"
    assert [a.type for a in actions] == [ActionType.SLEEP, ActionType.LAUNCH,
                                         ActionType.EXIT]


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


def test_a_bare_tag_leaves_no_text_at_all():
    text, actions = parse_action_tags("<exit>")
    assert text == ""
    assert [a.type for a in actions] == [ActionType.EXIT]


def test_prompt_paragraph_names_every_tag_it_teaches():
    for token in ("<exit>", "<sleep>", "<launch:MODULE>"):
        assert token in ACTION_TAG_PROMPT


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
    pytest.importorskip("openai")            # LLMApp constructs a real client object
    from moxie_sdk.apps import LLMApp
    app = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="sk-not-used", model="test")
    fake = _FakeCompletion(canned)
    app._client = fake                        # inject the brain, no network
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


def test_llm_app_teaches_the_model_the_tags():
    app, fake = _llm_app('{"say": "hi"}')
    app.respond(Turn(robot=RobotContext(device_id="d1"), speech="hi"))
    system = fake.seen[0][0]
    assert system["role"] == "system"
    assert "Robot controls" in system["content"]
    assert "<exit>" in system["content"]


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

class _FakeClient:
    """Records publishes; no network. (Local copy — see this module's docstring.)"""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


def _drive(app, device_id="d_tags", speech="can we draw"):
    import moxie_runtime
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child,
                                        module_id="CHAT", content_id="default")
    event = json.dumps({"command": "prompt", "backend": "router",
                        "event_id": "evt-tag", "speech": speech})
    rt._on_remote_chat(device_id, rt.robots[device_id], event)
    rt._pool.shutdown(wait=True)
    topic = f"/devices/{device_id}/commands/remote_chat"
    msgs = [p for (t, p) in rt.client.published if t == topic]
    assert msgs, f"no remote_chat published; got {rt.client.published}"
    return msgs[-1]


def test_a_tag_in_model_text_reaches_the_wire_as_response_actions():
    """End to end: brain writes `<launch:DRAW:default>` → RemoteChatResponse carries
    a launch action and the spoken text is clean."""
    pytest.importorskip("paho.mqtt.client")
    app = ContentApp(load_module(MODULE),
                     lambda m: "Sure! Let's draw. <launch:DRAW:default>")
    resp = _drive(app)
    assert resp["command"] == "remote_chat" and resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "Sure! Let's draw."
    assert "<launch" not in resp["output"]["markup"]
    ra = resp["response_actions"]
    assert len(ra) == 1
    assert ra[0]["action"] == "launch"
    assert ra[0]["module_id"] == "DRAW" and ra[0]["content_id"] == "default"


def test_an_exit_tag_reaches_the_wire():
    pytest.importorskip("paho.mqtt.client")
    app = ContentApp(load_module(MODULE), lambda m: "Bye Sam! <exit>")
    resp = _drive(app, speech="bye moxie")
    assert resp["output"]["text"] == "Bye Sam!"
    assert [a["action"] for a in resp["response_actions"]] == ["exit"]
