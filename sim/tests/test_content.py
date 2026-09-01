"""
Content-module engine unit tests (M2) — pure, no broker/LLM, runs in CI's pytest.
Covers docs/architecture/content-module-contract.md: the module loader, globals
regex + entity capture, the volley/session API, and prompt rendering.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import (  # noqa: E402
    load_module, load_modules, Volley, Session, render_prompt,
)

MODULE_JSON = {
    "conversations": [{
        "name": "Basic Memory Chat", "module_id": "OPENMOXIE_CHAT", "content_id": "memory",
        "max_history": 40, "max_volleys": 30,
        "opener": "Let's chat.|Anything on your mind?",
        "prompt": "You are Moxie talking to {{ volley.config.child_pii.nickname }}.",
        "model": "gpt-4o-mini", "max_tokens": 100, "temperature": 0.5,
    }],
    "globals": [{
        "name": "Timer Start",
        "pattern": r"^(moxie|moxy) (start|set) a? timer for? (\d+) (minute|hour|second)s?$",
        "entity_groups": "3,4", "action": 4,
    }],
    "schedules": [{"name": "day1", "schedule": {"provided_schedule": [{"module_id": "DM"}]}}],
}


def test_loader_parses_all_sections():
    m = load_module(MODULE_JSON)
    assert len(m.conversations) == 1 and len(m.globals) == 1 and len(m.schedules) == 1
    c = m.conversations[0]
    assert c.module_id == "OPENMOXIE_CHAT" and c.content_id == "memory"
    assert c.max_tokens == 100 and c.temperature == 0.5 and c.max_volleys == 30


def test_conversation_lookup():
    m = load_module(MODULE_JSON)
    assert m.conversation("OPENMOXIE_CHAT", "memory") is not None
    assert m.conversation("OPENMOXIE_CHAT") is not None
    assert m.conversation("NOPE") is None


def test_global_regex_captures_entities():
    m = load_module(MODULE_JSON)
    hit = m.match_global("Moxie set a timer for 5 minutes")
    assert hit is not None
    g, entities = hit
    assert g.name == "Timer Start"
    assert entities == ["5", "minute"]        # entity_groups "3,4"
    assert m.match_global("what's the weather") is None


def test_load_modules_merges_a_list():
    m = load_modules([MODULE_JSON, {"globals": [{"name": "Stop", "pattern": "^stop$"}]}])
    assert len(m.globals) == 2


def test_prompt_renders_child_nickname():
    m = load_module(MODULE_JSON)
    v = Volley(speech="hi", config={"child_pii": {"nickname": "Sam"}})
    out = render_prompt(m.conversations[0].prompt, {"volley": v})
    assert out == "You are Moxie talking to Sam."


def test_prompt_missing_var_is_blank_not_error():
    v = Volley(config={})
    out = render_prompt("Hi {{ volley.config.child_pii.nickname }}!", {"volley": v})
    assert out == "Hi !"


def test_volley_output_and_actions():
    v = Volley(speech="set a timer", entities=["5", "minute"],
               request={"input_vars": {"$eb_qr_value": "GO42"}})
    v.set_output("Okay, five minutes!", markup="<mark/>")
    v.add_execution_action("eb_timer_request", ["t1", 300000])
    v.update_subscriptions(["eb-timer-event"])
    assert v.output_text == "Okay, five minutes!" and v.output_markup == "<mark/>"
    assert v.execution_actions == [{"name": "eb_timer_request", "args": ["t1", 300000]}]
    assert v.subscriptions == ["eb-timer-event"]
    assert v.entities == ["5", "minute"]
    assert v.input_var("$eb_qr_value") == "GO42"
    assert v.input_var("missing", "def") == "def"


def test_session_accounting():
    s = Session(history=[{"role": "user", "content": "a"},
                         {"role": "assistant", "content": "b"},
                         {"role": "user", "content": "c"}], max_volleys=2)
    assert s.total_volleys == 2
    assert s.overflow is True
    assert s.is_empty() is False
    assert Session().is_empty() is True
