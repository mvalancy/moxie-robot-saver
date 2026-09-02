"""
Long-term memory — `volley.persist_data` + `session.summarize()`.

The content-module contract (docs/architecture/content-module-contract.md → "The
volley / session API") lists both calls; this suite is the floor under them:

  * `MemoryStore` — namespaced, bounded, JSON-safe, erasable, and **gated by
    `LoggingPolicy.NO_DATA`** (writes dropped, reads still allowed);
  * `render_prompt` really exposes `{{ volley.persist_data.<ns>.<key> }}`;
  * `session.summarize()` — structured parse, safety + no-verbatim filters, and a brain
    failure that writes nothing instead of crashing;
  * `ContentApp.on_session_end` — the `complete_handler` moment: merge with provenance,
    only once per transcript, and a second conversation reads the facts back.

Hermetic: the brain is a fake `chat(messages) -> str`, storage is a tmp dir, no sleeps,
and nothing here imports `openai`.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.store import JsonStore, MemoryStore, json_safe  # noqa: E402
from moxie_sdk.content import ContentApp, Session, load_module, render_prompt  # noqa: E402
from moxie_sdk.content.memory import (FactList, build_transcript,  # noqa: E402
                                      filter_summary, parse_summary, provenance,
                                      strip_verbatim, wrap_facts)
from moxie_sdk.content.volley import Volley  # noqa: E402
from moxie_sdk.types import ChildProfile, RobotContext, Turn  # noqa: E402

DEV = "d_mem"

MODULE = {
    "conversations": [{
        "name": "Memory Chat", "module_id": "MCHAT", "content_id": "default",
        "prompt": ("Talking to {{ volley.config.child_pii.nickname }}.\n"
                   "FACTS:\n{{ volley.persist_data.mchat.facts }}"),
        "memory": {"namespace": "mchat", "summarize": True, "min_volleys": 2},
    }],
}

GOOD_SUMMARY = json.dumps({
    "facts": ["Sam has a dog named Pepper", "Sam is in second grade"],
    "preferences": ["Sam likes dinosaurs"],
    "open_threads": ["Ask how the school play went"],
    "summary": "They talked about pets and school.",
})


def _store(tmp_path, **kw):
    return MemoryStore(JsonStore(str(tmp_path)), **kw)


def _robot(module_id="MCHAT", device_id=DEV):
    return RobotContext(device_id=device_id, child=ChildProfile(nickname="Sam"),
                        module_id=module_id, content_id="default")


def _chat(reply="ok", record=None):
    def chat(messages):
        if record is not None:
            record.append(messages)
        return reply
    return chat


# ---------------------------------------------------------------------------
# the store: round-trip, namespacing, bounds, policy
# ---------------------------------------------------------------------------

def test_persist_data_round_trips_and_is_namespaced(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["likes dinosaurs"]})
    mem.merge(DEV, "timers", {"next": 1234})
    assert mem.load(DEV)["mchat"]["facts"] == ["likes dinosaurs"]
    assert mem.load(DEV)["timers"]["next"] == 1234
    assert mem.namespaces(DEV) == ["mchat", "timers"]
    # a different robot never sees it
    assert mem.load("d_other") == {}
    # the file really is `robots/<id>/memory.json`
    assert os.path.exists(os.path.join(str(tmp_path), "robots", DEV, "memory.json"))


def test_merge_prepends_new_facts_and_dedupes(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]})
    mem.merge(DEV, "mchat", {"facts": ["is in second grade", "Has A Dog"]})
    facts = mem.load(DEV)["mchat"]["facts"]
    assert facts[0] == "is in second grade"          # newest first
    assert len([f for f in facts if f.lower() == "has a dog"]) == 1


def test_provenance_is_recorded_and_readable(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]},
              provenance=provenance(module_id="MCHAT", content_id="default", turns=4,
                                    reason="exit"))
    view = mem.view(DEV)
    prov = view["namespaces"]["mchat"]["provenance"][0]
    assert prov["module_id"] == "MCHAT" and prov["turns"] == 4 and prov["reason"] == "exit"
    assert prov["date"] and prov["at"] > 0
    # `_provenance` / `_meta` are ours: a parent reads `data`, not our bookkeeping
    assert list(view["namespaces"]["mchat"]["data"]) == ["facts"]


def test_bounds_cap_items_namespaces_and_bytes(tmp_path):
    mem = _store(tmp_path, max_items=3, max_namespaces=2, max_bytes=400)
    mem.merge(DEV, "a", {"facts": [f"fact {i}" for i in range(10)]})
    assert len(mem.load(DEV)["a"]["facts"]) == 3
    for ns in ("b", "c", "d"):
        mem.merge(DEV, ns, {"facts": ["x"]})
    assert len(mem.load(DEV)) <= 2
    # total bytes: a single huge namespace cannot blow the file up
    mem2 = _store(tmp_path / "b", max_bytes=200)
    mem2.merge(DEV, "big", {"facts": ["y" * 200 for _ in range(20)]})
    assert len(json.dumps(mem2.load(DEV))) <= 200 or mem2.load(DEV) == {}


def test_values_are_made_json_safe(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["ok"], "obj": object(), "n": 3})
    stored = mem.load(DEV)["mchat"]
    assert stored["facts"] == ["ok"] and stored["n"] == 3 and "obj" not in stored
    json.dumps(stored)                                   # must be serializable
    # non-string keys and un-encodable values are dropped, never stored as junk
    assert json_safe({"a": {1: "int key dropped", "b": b"bytes", "c": 1}}) == {"a": {"c": 1}}


def test_no_data_policy_drops_writes_but_allows_reads_and_erase(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["remembered before the switch"]})
    mem.policy = lambda device_id: 0                     # LoggingPolicy.NO_DATA
    assert mem.writes_allowed(DEV) is False
    assert mem.merge(DEV, "mchat", {"facts": ["must not be stored"]}) is None
    assert mem.save(DEV, {"mchat": {"facts": ["nope"]}}) is False
    # reads still work, so a parent can see (and delete) what was stored before
    assert mem.load(DEV)["mchat"]["facts"] == ["remembered before the switch"]
    assert mem.view(DEV)["writes_allowed"] is False
    assert mem.erase(DEV, "mchat") is True               # erase is never gated
    assert mem.load(DEV) == {}


def test_erase_one_namespace_or_everything(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "a", {"facts": ["1"]})
    mem.merge(DEV, "b", {"facts": ["2"]})
    assert mem.erase(DEV, "a") is True
    assert mem.namespaces(DEV) == ["b"]
    assert mem.erase(DEV, "nope") is False
    assert mem.erase(DEV) is True
    assert mem.load(DEV) == {}


# ---------------------------------------------------------------------------
# rendering: persist_data really reaches the prompt
# ---------------------------------------------------------------------------

def test_render_exposes_persist_data():
    v = Volley(persist_data=wrap_facts({"mchat": {"facts": ["has a dog", "likes red"]}}))
    out = render_prompt("FACTS:\n{{ volley.persist_data.mchat.facts }}", {"volley": v})
    assert "- has a dog" in out and "- likes red" in out


def test_render_missing_namespace_is_blank_not_an_error():
    v = Volley(persist_data={})
    out = render_prompt("FACTS:\n{{ volley.persist_data.mchat.facts }}", {"volley": v})
    assert out.strip() == "FACTS:"


def test_render_exposes_persist_data_without_jinja2_installed():
    """`render.py` falls back to a dependency-free `{{ dotted.path }}` substitution when
    jinja2 is absent (it is not in mqtt/requirements.txt) — memory must render there too."""
    from moxie_sdk.content import render as render_mod
    v = Volley(persist_data=wrap_facts({"mchat": {"facts": ["has a dog"]}}))
    out = render_mod._minimal_render("FACTS:\n{{ volley.persist_data.mchat.facts }}",
                                     {"volley": v})
    assert "- has a dog" in out
    assert render_mod._minimal_render("{{ volley.persist_data.nope.facts }}",
                                      {"volley": v}) == ""


def test_fact_list_is_a_real_list_and_renders_as_bullets():
    facts = FactList(["a", "b"])
    assert facts == ["a", "b"] and json.loads(json.dumps(facts)) == ["a", "b"]
    assert str(facts) == "- a\n- b"


# ---------------------------------------------------------------------------
# summarize: parsing, filtering, failure
# ---------------------------------------------------------------------------

def test_parse_summary_handles_json_fences_and_prose():
    parsed = parse_summary("```json\n" + GOOD_SUMMARY + "\n```")
    assert parsed["facts"][0] == "Sam has a dog named Pepper"
    assert parsed["preferences"] == ["Sam likes dinosaurs"]
    assert parsed["summary"].startswith("They talked")
    # prose around the object still parses
    assert parse_summary("Sure! " + GOOD_SUMMARY + " Hope that helps.")["facts"]
    # unparseable → the whole answer is kept as a readable summary, lists stay empty
    loose = parse_summary("They chatted about the school play.")
    assert loose["facts"] == [] and "school play" in loose["summary"]


def test_summary_never_keeps_unsafe_items():
    from moxie_sdk import safety as safety_seam
    unsafe = "I want to kill myself"                     # a self-harm block phrase
    verdict = safety_seam.default_classifier().assess(unsafe, role=safety_seam.MOXIE)
    assert verdict.action == safety_seam.BLOCK, "test phrase must actually be blocked"
    out = filter_summary({"facts": ["Sam has a dog", unsafe], "summary": unsafe},
                         classifier=safety_seam.default_classifier())
    assert out["facts"] == ["Sam has a dog"] and out["summary"] == ""


def test_summary_never_keeps_the_childs_own_words():
    history = [{"role": "user",
                "content": "my grandma lives on Elm Street in the yellow house"}]
    kept = strip_verbatim(["Sam has a grandmother",
                           "my grandma lives on Elm Street in the yellow house"], history)
    assert kept == ["Sam has a grandmother"]


def test_session_summarize_returns_structured_facts():
    calls = []
    session = Session(history=[{"role": "user", "content": "I have a dog"},
                               {"role": "assistant", "content": "Nice!"}],
                      chat=_chat(GOOD_SUMMARY, calls))
    out = session.summarize(classifier=None)
    assert out["facts"] == ["Sam has a dog named Pepper", "Sam is in second grade"]
    assert out["open_threads"] == ["Ask how the school play went"]
    # the brain saw the transcript, labelled by speaker
    prompt = calls[0][0]["content"]
    assert "Child: I have a dog" in prompt and "Moxie: Nice!" in prompt
    assert "Never quote the child" in prompt


def test_session_summarize_returns_none_when_the_brain_fails():
    def boom(messages):
        raise ValueError("gateway exploded")
    session = Session(history=[{"role": "user", "content": "hi"}], chat=boom)
    assert session.summarize() is None                   # no crash, nothing to write


def test_session_summarize_returns_none_without_a_brain_or_transcript():
    assert Session(history=[{"role": "user", "content": "hi"}]).summarize() is None
    assert Session(history=[], chat=_chat(GOOD_SUMMARY)).summarize() is None


def test_build_transcript_labels_speakers():
    assert build_transcript([{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}]) == \
        "Child: hi\nMoxie: hello"


# ---------------------------------------------------------------------------
# ContentApp: the end-of-conversation hook and the second conversation
# ---------------------------------------------------------------------------

def _app(tmp_path, reply=GOOD_SUMMARY, module=None, record=None):
    return ContentApp(load_module(module or MODULE), _chat(reply, record),
                      memory=_store(tmp_path))


def test_on_session_end_writes_a_summary_with_provenance(tmp_path):
    app = _app(tmp_path)
    history = [{"role": "user", "content": "I have a dog"},
               {"role": "assistant", "content": "What is its name?"},
               {"role": "user", "content": "Pepper"},
               {"role": "assistant", "content": "Great name!"}]
    app.on_session_end(_robot(), history, "exit")
    block = app.memory.load(DEV)["mchat"]
    assert block["facts"] == ["Sam has a dog named Pepper", "Sam is in second grade"]
    assert block["summaries"] == ["They talked about pets and school."]
    assert block["_provenance"][0]["module_id"] == "MCHAT"
    assert block["_provenance"][0]["reason"] == "exit"
    assert block["_provenance"][0]["turns"] == 2


def test_on_session_end_skips_a_conversation_too_short_to_matter(tmp_path):
    app = _app(tmp_path)
    app.on_session_end(_robot(), [{"role": "user", "content": "hi"}], "exit")
    assert app.memory.load(DEV) == {}


def test_on_session_end_does_not_re_summarize_the_same_transcript(tmp_path):
    calls = []
    app = _app(tmp_path, record=calls)
    history = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "a"},
               {"role": "user", "content": "two"}, {"role": "assistant", "content": "b"}]
    app.on_session_end(_robot(), history, "exit")
    app.on_session_end(_robot(), history, "module_switch")
    assert len(calls) == 1                               # the brain was asked once
    assert len(app.memory.load(DEV)["mchat"]["_provenance"]) == 1


def test_on_session_end_writes_nothing_under_no_data(tmp_path):
    app = _app(tmp_path)
    app.memory.policy = lambda device_id: 0              # LoggingPolicy.NO_DATA
    app.on_session_end(_robot(), [{"role": "user", "content": "one"},
                                  {"role": "assistant", "content": "a"},
                                  {"role": "user", "content": "two"}], "exit")
    assert app.memory.load(DEV) == {}


def test_on_session_end_writes_nothing_when_the_brain_fails(tmp_path):
    def boom(messages):
        raise ValueError("gateway exploded")
    app = ContentApp(load_module(MODULE), boom, memory=_store(tmp_path))
    app.on_session_end(_robot(), [{"role": "user", "content": "one"},
                                  {"role": "assistant", "content": "a"},
                                  {"role": "user", "content": "two"}], "exit")
    assert app.memory.load(DEV) == {}


def test_a_module_without_a_memory_block_remembers_nothing(tmp_path):
    plain = {"conversations": [{"name": "Plain", "module_id": "MCHAT",
                               "content_id": "default", "prompt": "hi"}]}
    app = _app(tmp_path, module=plain)
    app.on_session_end(_robot(), [{"role": "user", "content": "one"},
                                  {"role": "assistant", "content": "a"},
                                  {"role": "user", "content": "two"}], "exit")
    assert app.memory.load(DEV) == {}


def test_a_second_conversation_sees_the_remembered_facts_in_its_prompt(tmp_path):
    """The point of the whole slice: what conversation #1 learned is in the system
    prompt of conversation #2."""
    seen = []
    app = _app(tmp_path, record=seen)
    app.on_session_end(_robot(), [{"role": "user", "content": "I have a dog"},
                                  {"role": "assistant", "content": "Nice"},
                                  {"role": "user", "content": "Pepper"},
                                  {"role": "assistant", "content": "Great name"}], "exit")
    seen.clear()
    app.respond(Turn(robot=_robot(), speech="hello again"))
    system = seen[0][0]["content"]
    assert "- Sam has a dog named Pepper" in system
    assert "- Sam is in second grade" in system


def test_a_global_handler_can_write_durable_persist_data(tmp_path):
    """OpenMoxie's timer global writes `volley.persist_data['timers']`; ours survives
    the turn (and `local_data` deliberately does not)."""
    module = dict(MODULE)
    module["globals"] = [{"name": "Timer", "pattern": r"timer for (\d+)",
                          "entity_groups": "1"}]

    def handler(volley, session):
        volley.persist_data.setdefault("timers", {})["1"] = int(volley.entities[0])
        volley.local_data["scratch"] = "gone after this turn"
        volley.set_output("Timer set!", None)

    app = ContentApp(load_module(module), _chat(), memory=_store(tmp_path),
                     global_handlers={"Timer": handler})
    assert app.respond(Turn(robot=_robot(), speech="timer for 5")).text == "Timer set!"
    assert app.memory.load(DEV)["timers"]["1"] == 5
    assert "scratch" not in json.dumps(app.memory.load(DEV))


def test_memory_off_is_a_supported_configuration(tmp_path):
    app = ContentApp(load_module(MODULE), _chat(GOOD_SUMMARY), memory=False)
    assert app.memory is None
    assert app.persist_data(DEV) == {}
    app.on_session_end(_robot(), [{"role": "user", "content": "one"},
                                  {"role": "assistant", "content": "a"},
                                  {"role": "user", "content": "two"}], "exit")


@pytest.mark.parametrize("path", ["content_modules/starter.json",
                                  "content_modules/memory_chat.json"])
def test_shipped_modules_declare_valid_memory_blocks(path):
    with open(os.path.join(REPO, "mqtt", path)) as fh:
        module = load_module(json.load(fh))
    ns = {c.memory_namespace for c in module.conversations}
    assert ns and "" not in ns
    for conv in module.conversations:
        # every declared namespace is actually referenced by the prompt
        assert f"persist_data.{conv.memory_namespace}" in conv.prompt
