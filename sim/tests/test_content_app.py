"""
ContentApp tests (M2) — the content engine driving live turns through an injected
brain (no real LLM/broker). Covers docs/architecture/content-module-contract.md:
the conversation path (Jinja prompt personalization → brain → Reply), globals-first
handling, and the opener greeting.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import load_module, ContentApp  # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile  # noqa: E402

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
    app = ContentApp(load_module(MODULE), lambda m: "ok", persona="PERSONA-X")
    app.respond(Turn(robot=_robot(), speech="hi"))
    # capture via a second call with a recording brain
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


# --------------------------------------------------------------------------- #
# 📦 A module's `code` string is DATA — never behaviour (backlog/content-packs.md §2.2)
# --------------------------------------------------------------------------- #
# `ContentApp`'s docstring has always promised this ("arbitrary `code`-string execution
# from module JSON is deliberately NOT done here"), and content packs make it a security
# property rather than a deferral: an imported pack cannot execute anything, which is what
# lets a pack be unsigned and still safe to install on a child's appliance. The honest cost
# is that upstream's `MoxieTime`/`MoxieTimers` would import as a global that matches an
# utterance and then does nothing — the review says so, and the audit's BEYOND #6 (a
# sandboxed module runtime) is what would change it.

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
