"""
ContentApp tests (M2) — the content engine driving live turns through an injected
brain (no real LLM/broker). Covers docs/architecture/content-module-contract.md:
the conversation path (Jinja prompt personalization → brain → Reply), globals-first
handling, and the opener (a `greeting()`, and the answer to a `prompt` with no speech).
"""
import os
import random

from moxie_sdk.content import ext as E  # noqa: E402
from moxie_sdk.content import load_module, ContentApp  # noqa: E402
from moxie_sdk.types import ActionType, Turn, RobotContext, ChildProfile  # noqa: E402

MODULE = {
    "conversations": [{
        "name": "Chat", "module_id": "CHAT", "content_id": "default",
        "max_history": 10, "max_volleys": 30,
        "opener": "Hi there!<opener>|Hello!",
        "prompt": "You are Moxie talking to {{ volley.config.child_pii.nickname }}.",
    }],
    "globals": [{"name": "Timer", "pattern": r"timer for (\d+)", "entity_groups": "1"}],
}


def _robot(nickname="Sam", module_id="CHAT"):
    return RobotContext(device_id="d1", child=ChildProfile(nickname=nickname),
                        module_id=module_id, content_id="default")


def test_conversation_path_personalizes_prompt_and_calls_brain():
    seen = {}

    def fake_chat(messages):
        seen["messages"] = messages
        return "Nice to meet you!"

    app = ContentApp(load_module(MODULE), fake_chat)
    reply = app.respond(Turn(robot=_robot("Sam"), speech="hello"))
    assert reply.text == "Nice to meet you!"
    system = seen["messages"][0]
    assert system["role"] == "system"
    assert "talking to Sam" in system["content"]          # Jinja rendered the nickname
    assert seen["messages"][-1] == {"role": "user", "content": "hello"}


def test_persona_prepended_to_module_prompt():
    grabbed = {}
    app2 = ContentApp(load_module(MODULE),
                      lambda m: grabbed.setdefault("sys", m[0]["content"]) or "ok",
                      persona="PERSONA-X")
    app2.respond(Turn(robot=_robot(), speech="hi"))
    assert grabbed["sys"].startswith("PERSONA-X")
    assert "talking to" in grabbed["sys"]


def test_global_handler_runs_before_the_brain():
    called = {"brain": 0}

    def brain(messages):
        called["brain"] += 1
        return "LLM SHOULD NOT RUN"

    def timer_handler(volley, session):
        mins = volley.entities[0]
        volley.set_output(f"Okay, {mins} minutes!")
        volley.add_execution_action("eb_timer_request", ["t1", int(mins) * 60000])

    app = ContentApp(load_module(MODULE), brain,
                     global_handlers={"Timer": timer_handler})
    reply = app.respond(Turn(robot=_robot(), speech="set a timer for 5 please"))
    assert reply.text == "Okay, 5 minutes!"
    assert called["brain"] == 0                            # global short-circuited the LLM


def test_unhandled_global_falls_through_to_conversation():
    app = ContentApp(load_module(MODULE), lambda m: "chat reply")   # no Timer handler
    reply = app.respond(Turn(robot=_robot(), speech="timer for 5"))
    assert reply.text == "chat reply"                      # matched but no handler → chat


def test_greeting_uses_the_opener():
    app = ContentApp(load_module(MODULE), lambda m: "x")
    g = app.greeting(_robot())
    assert g is not None and g.text == "Hi there!"         # first '|' alt, tag stripped


def test_empty_brain_reply_is_graceful():
    app = ContentApp(load_module(MODULE), lambda m: "   ")
    reply = app.respond(Turn(robot=_robot(), speech="hi"))
    assert reply.text == "Tell me more!"


# --- The opener is what a conversation starts with ---
# OpenMoxie (conversations.py `handle_volley`) answers `prompt` with a random opener
# alternative. Here only a `prompt` with no speech does, so a typed or spoken first line
# still reaches the brain.

def _counting(answer="from the brain"):
    calls = []

    def chat(messages):
        calls.append(messages)
        return answer

    return chat, calls


def _with_opener(opener, **conv):
    return {"conversations": [dict(MODULE["conversations"][0], opener=opener, **conv)]}


def test_an_empty_prompt_is_answered_with_the_opener_not_the_brain():
    chat, calls = _counting()
    reply = ContentApp(load_module(MODULE), chat).respond(
        Turn(robot=_robot(), speech="", command="prompt"))
    assert calls == [], "an opener must not cost a model call"
    assert reply.text == "Hi there!" and reply.actions == []


def test_speech_continue_and_reprompt_still_reach_the_brain():
    for command, speech in [("prompt", "hello"), ("prompt", "  hi  "),
                            ("continue", ""), ("reprompt", "")]:
        chat, calls = _counting()
        reply = ContentApp(load_module(MODULE), chat).respond(
            Turn(robot=_robot(), speech=speech, command=command))
        assert reply.text == "from the brain", (command, speech)
        assert [c[-1] for c in calls] == [{"role": "user", "content": speech}]


def test_a_conversation_without_an_opener_still_asks_the_brain():
    chat, calls = _counting()
    module = {"conversations": [{k: v for k, v in MODULE["conversations"][0].items()
                                 if k != "opener"}]}
    reply = ContentApp(load_module(module), chat).respond(
        Turn(robot=_robot(), speech="", command="prompt"))
    assert reply.text == "from the brain" and len(calls) == 1


def test_openers_rotate_and_never_repeat_back_to_back():
    app = ContentApp(load_module(_with_opener("One!<opener>|Two!|Three!")),
                     lambda m: "x", rng=random.Random(3))
    said = [app.greeting(_robot()).text for _ in range(30)]
    assert said[0] == "One!", "a robot hears the first opener first"
    assert set(said) == {"One!", "Two!", "Three!"}
    assert all(a != b for a, b in zip(said, said[1:])), said


def test_each_robot_has_its_own_opener_rotation():
    app = ContentApp(load_module(_with_opener("One!|Two!")), lambda m: "x")
    first = [app.greeting(RobotContext(device_id=d, child=ChildProfile(nickname="Sam"),
                                       module_id="CHAT")).text for d in ("a", "b", "a")]
    assert first == ["One!", "One!", "Two!"]


def test_a_tag_in_an_opener_is_an_action_never_spoken():
    app = ContentApp(load_module(_with_opener("Let's draw!<launch:DRAW>")), lambda m: "x")
    reply = app.greeting(_robot())
    assert reply.text == "Let's draw!"
    assert [(a.type, a.module_id) for a in reply.actions] == [(ActionType.LAUNCH, "DRAW")]


def test_what_a_starting_extension_asks_for_rides_out_with_the_opener():
    """A `turn.before` program that acts and subscribes without taking the turn: both go
    out with the opener, as they would with a model's line."""
    starts = {"ext_format": 1, "capabilities": ["act.eb_enable_qr", "subscribe"],
              "on": "turn.before",
              "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}},
                                {"subscribe": ["eb-qr-event"]}]}]}
    chat, calls = _counting()
    app = ContentApp(load_module(_with_opener("Show me a card!", extension=starts)), chat,
                     ext_grants=E.DEFAULT_GRANTS | {"act.eb_enable_qr", "subscribe"})
    reply = app.respond(Turn(robot=_robot(), speech="", command="prompt"))
    assert calls == [] and reply.text == "Show me a card!"
    assert [(a.type, a.function, a.args) for a in reply.actions] == [
        (ActionType.EXECUTE, "eb_enable_qr", ["true"])]
    assert reply.subscribe == ["eb-qr-event"]


# --- 📦 A module's `code` string is DATA — never behaviour (backlog/content-packs.md §2.2) ---
# An imported pack cannot execute anything, which is what lets an unsigned pack be safe on a
# child's appliance. The cost: upstream's `MoxieTime`/`MoxieTimers` import as globals that
# match and do nothing (the review says so; sandboxed extensions are the answer).

CODE_MODULE = {
    "conversations": [dict(MODULE["conversations"][0],
                           code="import os\nos.environ['MOXIE_PACK_RAN_CODE'] = '1'\n"
                                "raise SystemExit('a module must never run this')")],
    "globals": [dict(MODULE["globals"][0],
                     code="open('/tmp/moxie-pack-should-not-exist', 'w').write('x')")],
}


def test_a_module_code_string_is_carried_but_never_executed():
    os.environ.pop("MOXIE_PACK_RAN_CODE", None)
    module = load_module(CODE_MODULE)
    assert module.conversations[0].code.startswith("import os")
    assert module.globals[0].code

    app = ContentApp(module, lambda m: "still talking")
    assert app.respond(Turn(robot=_robot(), speech="hello")).text == "still talking"
    assert app.greeting(_robot()).text == "Hi there!"
    # a global with a `code` string and no registered handler falls through to the chat —
    # it does NOT become a handler
    assert app.respond(Turn(robot=_robot(), speech="timer for 5")).text == "still talking"

    assert "MOXIE_PACK_RAN_CODE" not in os.environ
    assert not os.path.exists("/tmp/moxie-pack-should-not-exist")
