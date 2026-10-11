"""
Action tags — the model's own text driving the robot (OpenMoxie audit ADOPT #4).

Five layers, bottom-up:
  1. the pure parser (`moxie_sdk/actions.py`) — every tag form, tolerance, cleanliness
  2. LLMApp.respond — a tagged model line becomes Reply.text + Reply.actions
  3. ContentApp — same, for the content engine (model path and global-handler path)
  4. the real MoxieRuntime — the action reaches the wire as `response_actions`
  5. the model-line policy (`ext_host.model_line`, K18a): on every brain, the tags written
     whole act (that one-pass parse) and what is spoken holds no tag of ours however nested
     and no catalogue mark this appliance could not have minted however assembled
"""
import json
import os
import random
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk import vocab                                        # noqa: E402
from moxie_sdk.actions import (LAUNCH_IF_CONFIRMED_AS, lift_action_tags,  # noqa: E402
                               parse_action_tags, tag_names)
from moxie_sdk.content import ContentApp, load_module            # noqa: E402
from moxie_sdk.content import ext_host as H                      # noqa: E402
from moxie_sdk.content.volley import Volley, Session             # noqa: E402
from moxie_sdk.types import (ActionType, ChildProfile, ResultCode, RobotContext,  # noqa: E402
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
    ("Bye Sam! <exit>", "Bye Sam!", {"action": "exit_module"}),
])
def test_a_tag_in_model_text_reaches_the_wire_as_response_actions(line, text, action):
    """End to end: the brain writes a tag → RemoteChatResponse carries the action and the
    spoken text is clean."""
    pytest.importorskip("paho.mqtt.client")
    resp = _drive(ContentApp(load_module(MODULE), lambda m: line))
    assert resp["command"] == "remote_chat" and resp["result"] == ResultCode.SUCCESS
    assert resp["output"]["text"] == text
    assert "<" not in resp["output"]["text"] and "<launch" not in resp["output"]["markup"]
    (ra,) = resp["response_actions"]
    assert {k: ra.get(k) for k in action} == action


# ---------------------------------------------------------------- 5. the model's own line (K18a)
#
# Every brain's own line is held to one policy (`ext_host.model_line`): the tags written
# whole act, by the one-pass parse of section 1, and what is spoken holds no tag of ours
# however nested and no catalogue mark this appliance could not have minted however
# assembled (the pack gate test_pack_markup_gate.py pins, run on the parse's text). Before
# this the llm brain spoke `<exit>Bye for now!` for `<ex<sleep>it>Bye for now!` (the sleep
# acted; the exit that formed was said and performed), spoke `<launch:DRAW>Go` for
# `<la<exit>unch:DRAW>Go`, and passed a depth-0 `<mark name="cmd:start-systemunpair"/>` to
# the robot's text and markup; the webhook brain did the same in text and markup. The
# content brain's model line was gated by the pack gate already and now shares the policy.

UNPAIR = '<mark name="cmd:start-systemunpair"/>'
SCRIPTED = '<mark name="cmd:scripted"/>'
MOOD = vocab.mood_mark(1)
SYSTEM_TREE = vocab.tree_mark("Gesture_None", "Bht_System_Suspend")
#: Anything the robot could read as a system verb or a system tree, in any case.
_SYSTEM_RE = re.compile(r"start-system|Bht_System|Bht_Motor_Test|Bht_TestState", re.I)


def _gate_clean(text) -> bool:
    """Every `<mark` opening left is a mark this appliance could have minted (read whole by
    the catalogue's pattern, an expressive verb, catalogue ids) and names no system verb or
    tree (test_pack_markup_gate's `_clean`, plus the tree). Read as the gate reads: from a
    `<mark` opening; `<ma\\nrk …` is not a mark to any reader, and stays text."""
    for m in re.finditer(r"<mark\b[^>]*>?", text or "", re.I):
        tag = m.group(0)
        read = vocab._MARK_RE.fullmatch(tag)
        if (read is None or read.group(1) not in vocab.EXPRESSIVE_VERBS
                or vocab.validate_markup(tag) or _SYSTEM_RE.search(tag)):
            return False
    return True


#: `(the model's line, what is spoken, the actions, what the parent is told)`.
MODEL_LINES = {
    "a sleep nested in an exit: the sleep acts, the exit that forms is never spoken": (
        "<ex<sleep>it>Bye for now! I loved hearing about your day.",
        "Bye for now! I loved hearing about your day.", [ActionType.SLEEP], []),
    "an exit nested in a launch: the exit acts and nothing launches": (
        "<la<exit>unch:DRAW>Go", "Go", [ActionType.EXIT], []),
    "a system mark assembled around a nested exit: the exit acts, the mark is cut and named": (
        '<ma<ex<exit>it>rk name="cmd:start-systemunpair"/>Bye!', "Bye!", [ActionType.EXIT],
        ["start-systemunpair"]),
    "a system mark written whole at depth 0": (UNPAIR + "Hi there", "Hi there", [],
                                               ["start-systemunpair"]),
    "a system mark in the middle, and the gap it leaves is closed": (
        "Hi " + UNPAIR + " there!", "Hi there!", [], ["start-systemunpair"]),
    "a system tree in the expressive tree verb": (SYSTEM_TREE + "Hi", "Hi", [],
                                                  ["Bht_System_Suspend"]),
    "a catalogue verb a brain may not send": (SCRIPTED + "Hi", "Hi", [], ["scripted"]),
    "a system verb behind a decoy verb in one span is named": (
        '<mark name="cmd:zzz" <mark name="cmd:start-systemunpair"/>Okay!', "Okay!", [],
        ["start-systemunpair"]),
    "a system verb in a second name= attribute is named": (
        '<mark name="cmd:playback-mood" name="cmd:start-systemunpair"/>Okay!', "Okay!", [],
        ["start-systemunpair"]),
    "an expressive mark in the minted form is kept, as a pack's is": (MOOD + "Hi", MOOD + "Hi",
                                                                     [], []),
    "a tag written whole beside a plain line is what the parse gives": (
        "Bye Sam! <exit>", "Bye Sam!", [ActionType.EXIT], []),
    "a plain line is byte for byte what the parse gives": ("Tell me about it!",
                                                           "Tell me about it!", [], []),
    "a mark alone leaves nothing to say": (UNPAIR, "", [], ["start-systemunpair"]),
}


@pytest.mark.parametrize("shape", sorted(MODEL_LINES))
def test_a_brains_own_line_acts_on_tags_written_whole_and_speaks_nothing_of_ours(shape):
    """The helper: the actions are exactly the one-pass parse's (no new agency), the text
    holds no tag of ours and no mark this appliance could not have minted, and the names
    for the parent are the system verbs, the other catalogue verbs and the system trees."""
    line, said, acts, told = MODEL_LINES[shape]
    spoken, actions, refused = H.model_line(line)
    assert (spoken, [a.type for a in actions], refused) == (said, acts, told), (shape, spoken, actions)
    assert [a.type for a in actions] == [a.type for a in parse_action_tags(line)[1]], shape
    assert tag_names(spoken) == [] and _gate_clean(spoken), (shape, spoken)


class _FakeClient:
    """The OpenAI client's `chat.completions` seam: one canned reply, whole or streamed
    (the runtime streams a brain that can, so the fake answers both ways)."""

    def __init__(self, content, size=7):
        self._content, self._size = content, size
        self.chat = self
        self.completions = self

    def create(self, **kw):
        if kw.get("stream"):
            return iter([{"choices": [{"delta": {"content": self._content[i:i + self._size]}}]}
                         for i in range(0, len(self._content), self._size)])
        msg = type("M", (), {"content": self._content})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _brain(say, mood="positive", gesture="talk"):
    """The real LLMApp over a fake client that answers the expressive JSON for `say`."""
    from moxie_sdk.apps import LLMApp
    canned = json.dumps({"say": say, "mood": mood, "gesture": gesture})
    return LLMApp(base_url="http://127.0.0.1:1/v1", api_key="sk-not-used", model="test",
                  client=_FakeClient(canned))


def _turn(speech="hi", device_id="d1"):
    return Turn(robot=RobotContext(device_id=device_id, child=ChildProfile(nickname="Sam")),
                speech=speech)


@pytest.mark.parametrize("shape", sorted(MODEL_LINES))
def test_the_llm_brains_reply_is_held_to_the_policy(shape, capsys):
    """`LLMApp.respond`: the line is spoken without anything of ours or any mark this
    appliance could not have minted, the expressive markup is built from the gated text so
    it carries nothing the text does not, the actions are the one-pass parse's, and each cut
    is named in the log once and counted per robot and name."""
    line, said, acts, told = MODEL_LINES[shape]
    app = _brain(line)
    reply = app.respond(_turn())
    assert reply.text == (said or "Tell me more!"), (shape, reply.text)
    assert [a.type for a in reply.actions] == acts, (shape, reply.actions)
    for field in (reply.text, reply.markup or ""):
        assert tag_names(field) == [] and _gate_clean(field), (shape, field)
    if MOOD in said:
        assert MOOD in reply.text and MOOD in reply.markup, reply
    out = capsys.readouterr().out
    for name in told:
        assert out.count(f"the brain wrote {H.command_label(name)}") == 1, (name, out)
    assert app._commands_refused == {("d1", name): 1 for name in told}, shape


@pytest.mark.parametrize("shape", sorted(MODEL_LINES))
def test_the_llm_brains_streamed_reply_is_held_to_the_policy_sentence_by_sentence(shape):
    """`LLMApp.respond_stream`: each sentence and the tail go through the same policy, so
    no chunk's text or markup holds a tag of ours or a mark this appliance could not have
    minted, the actions ride on their chunks as the one-pass parse read them, and the
    chunks together say the line. (A nest split across two sentence chunks is a stream
    edge the segmenter decides, out of scope here; every shape above stays in one.)"""
    line, said, acts, told = MODEL_LINES[shape]
    app = _brain(line)
    chunks = list(app.respond_stream(_turn()))
    assert chunks and chunks[-1].final, shape
    assert " ".join(c.text for c in chunks if c.text) == (said or "Tell me more!"), (shape, chunks)
    assert [a.type for c in chunks for a in c.actions] == acts, (shape, chunks)
    for c in chunks:
        for field in (c.text, c.markup or ""):
            assert tag_names(field) == [] and _gate_clean(field), (shape, field)
    assert app._commands_refused == {("d1", name): 1 for name in told}, shape


def test_the_llm_brain_names_a_cut_command_once_per_robot_and_name(capsys):
    """A depth-0 system mark from the model (today it passed to the robot's text and
    markup): said without it, named in the supervisor log once per robot and name, in the
    parent's words, and counted per robot and name on the app."""
    app = _brain(UNPAIR + "Hi there")
    for device in ("d1", "d1", "d2"):
        reply = app.respond(_turn("hi", device))
        assert reply.text == "Hi there" and reply.actions == [], reply
        assert UNPAIR not in reply.markup and "<mark" in reply.markup, "performed, without it"
    out = capsys.readouterr().out
    line = "the brain wrote the system command start-systemunpair, which would unpair Moxie from this home; Moxie said its line without it"
    assert out.count(f"[safety] d1: {line}") == 1 and out.count(f"[safety] d2: {line}") == 1, out
    assert app._commands_refused == {("d1", "start-systemunpair"): 2, ("d2", "start-systemunpair"): 1}
    assert H.refused_command_words("start-systemunpair", hook="brain") == line


def test_the_content_brains_model_line_shares_the_policy_and_the_parent_is_told(tmp_path):
    """`ContentApp.respond` runs the model's line through the same helper: the assembled
    system mark is cut around the exit that acts, and the conversation's own `ext_events`
    row says the AI's reply carried it (hook `model`), not that the item did."""
    from moxie_sdk.memory_store import MemoryStore
    from moxie_sdk.store import JsonStore
    store = MemoryStore(JsonStore(str(tmp_path)))
    line = '<ma<ex<exit>it>rk name="cmd:start-systemunpair"/>Bye!'
    app = ContentApp(load_module(MODULE), lambda m: line, memory=store,
                     clock=lambda: 1_700_000_000)
    reply = app.respond(Turn(robot=_robot(), speech="bye"))
    assert reply.text == "Bye!" and [a.type for a in reply.actions] == [ActionType.EXIT], reply
    rows = store.store.read("d1", H.EXT_EVENTS_COLLECTION, [])
    assert [(r["extension"], r["hook"], r["reason"]) for r in rows] == [
        ("conversation:CHAT/default", "model", "command:start-systemunpair")], rows
    assert rows[0]["sentence"] == H.refused_command_words("start-systemunpair", hook="model")
    assert rows[0]["sentence"].startswith("the AI's reply under it carried the system command")


def test_the_webhook_brains_text_and_markup_are_held_to_the_policy(capsys):
    """A webhook's `text` is a brain's own line; its `markup` is a third party's authored
    markup and gets the gate a pack's markup gets (`ext_host.pack_markup`): a system mark
    in either is cut (today both reached the robot), a tag of ours in the markup is lifted,
    a minted expressive mark is untouched, a system tree drops the mark, and each cut is
    named once per robot and name and counted."""
    from moxie_sdk.apps import WebhookApp

    class _Stub(WebhookApp):
        def __init__(self, answer):
            super().__init__("http://127.0.0.1:1/turn")
            self._answer = answer

        def _post(self, path_hint, body):
            return dict(self._answer)

    app = _Stub({"text": '<ma<ex<exit>it>rk name="cmd:start-systemunpair"/>Bye!',
                 "markup": UNPAIR + "Bye!<exit>"})
    reply = app.respond(_turn("bye", "d_web"))
    assert reply.text == "Bye!" and [a.type for a in reply.actions] == [ActionType.EXIT], reply
    assert reply.markup == "Bye!", reply.markup
    out = capsys.readouterr().out
    assert out.count("[safety] d_web: the brain wrote the system command start-systemunpair") == 1
    assert app._commands_refused == {("d_web", "start-systemunpair"): 2}, "text and markup each"
    for markup, kept, told in ((MOOD + "Happy birthday!", MOOD + "Happy birthday!", []),
                               (SYSTEM_TREE + "Hi", "Hi", ["Bht_System_Suspend"]),
                               ("<ex" + UNPAIR + "it>Hi", None, ["start-systemunpair"])):
        app = _Stub({"text": "Hi", "markup": markup})
        reply = app.respond(_turn("hi", "d_web"))
        assert (reply.text, reply.markup, reply.actions) == ("Hi", kept, []), markup
        assert app._commands_refused == {("d_web", name): 1 for name in told}, markup


@pytest.mark.parametrize("line,text,actions,names", [
    ("<ex<sleep>it>Bye for now! I loved hearing about your day.",
     "Bye for now! I loved hearing about your day.", ["sleep"], []),
    ('<ma<ex<exit>it>rk name="cmd:start-systemunpair"/>Bye!', "Bye!", ["exit_module"],
     ["start-systemunpair"]),
    (UNPAIR + "Hi there", "Hi there", [], ["start-systemunpair"]),
])
def test_the_llm_brains_line_reaches_the_wire_clean(line, text, actions, names, tmp_path, capsys):
    """Through the real runtime, plain and streamed: `output.text` is the gated line, the
    floor's `output.markup` holds no tag of ours and no system verb, the action is the
    one-pass parse's (`response_actions`), and the cut is named once per robot."""
    pytest.importorskip("paho.mqtt.client")
    from helpers_runtime import drive_turn, make_runtime
    from moxie_sdk.store import JsonStore
    for streaming in (False, True):
        app = _brain(line)
        rt, device = make_runtime(app, device_id="d_tags", module_id="CHAT",
                                  store=JsonStore(str(tmp_path / str(streaming))))
        rt.streaming = streaming
        rt.brain_budget_s = 0
        last = drive_turn(rt, device, "bye", event_id="evt-nest")
        replies = rt.client.chat_replies(device)
        assert last["command"] == "remote_chat" and last["result"] == ResultCode.SUCCESS
        spoken = [r["output"]["text"] for r in replies if (r.get("output") or {}).get("text")]
        assert spoken and " ".join(spoken).endswith(text) if streaming else spoken == [text], (streaming, spoken)
        for r in replies:
            out = r.get("output") or {}
            for field in (out.get("text", ""), out.get("markup", "")):
                assert tag_names(field) == [] and _gate_clean(field), (streaming, r)
        # (the runtime's event-subscription entry carries no `action`; it is not one)
        sent = [a.get("action") for r in replies for a in r.get("response_actions", [])]
        assert [a for a in sent if a] == actions, (streaming, replies)
        assert app._commands_refused == {("d_tags", n): 1 for n in names}
    out = capsys.readouterr().out
    for name in names:
        assert out.count(f"[safety] d_tags: the brain wrote {H.command_label(name)}") == 2, "once per runtime"


#: Pieces of our tags, of marks and of text, from which random model lines are built.
_PIECES = ["<exit>", "<sleep>", "<launch:DRAW>", "<launch:DRAW:default>", "<exit:now>", "<EXIT>",
           "<ex", "it>", "<sl", "eep>", "<la", "unch:DRAW>", "<ma", 'rk name="cmd:start-systemunpair"/>',
           UNPAIR, SCRIPTED, MOOD, SYSTEM_TREE, '<mark name="cmd:zzz"/>', "<opener>", "<", ">", "<<",
           ">>", "Hi", " there", "Bye", "!", " ", ", ", "Sam", "\n"]


def test_over_random_lines_a_brain_gains_no_agency_and_speaks_nothing_of_ours():
    """20,000 random lines of pieces (fixed seed): the actions are always exactly the
    one-pass parse's, so no nest reads as an action a one-pass reader would not read; what
    is spoken never holds a tag of ours, a system verb or tree, or a mark this appliance
    could not have minted; and whenever the parse's text already holds no mark and the
    one-pass lift is its own fixpoint, the spoken text is byte for byte the parse's."""
    rng = random.Random(18)
    same = differ = named = 0
    for _ in range(20000):
        line = "".join(rng.choice(_PIECES) for _ in range(rng.randint(1, 10)))
        spoken, actions, refused = H.model_line(line)
        parsed_text, parsed = parse_action_tags(line)
        key = lambda a: (a.type, a.module_id, a.content_id)   # noqa: E731
        assert [key(a) for a in actions] == [key(a) for a in parsed], line
        assert tag_names(spoken) == [] and _gate_clean(spoken), (line, spoken)
        if "<mark" not in parsed_text.lower() and lift_action_tags(parsed_text) == parsed_text:
            assert spoken == parsed_text, (line, spoken, parsed_text)
            same += 1
        else:
            differ += 1
        named += bool(refused)
    assert same >= 1000 and differ >= 1000 and named >= 1000, (same, differ, named)


def test_a_printed_card_is_still_read_in_one_pass_so_a_nested_card_never_launches():
    """The one-pass rule is shared with the launch cards: `launch_cards.decode` refuses a
    nested card by its residue (its guard 5), and a fixpoint inside the parse would have
    launched both of these. The model-line policy lifts nests from what is SPOKEN only, so
    the cards still refuse (test_launch_cards pins the first)."""
    from moxie_sdk import launch_cards
    assert launch_cards.decode("GO<launch:<launch:DM>>") is None
    assert launch_cards.decode("GO<la<launch:DM>unch:DRAW>") is None
    assert launch_cards.decode("GO<launch:DM>") is not None
