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

from moxie_sdk.store import (JsonStore, MemoryStore, item_id,  # noqa: E402
                             item_text, json_safe, normalize_items, prune_stale)
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


def texts(values):
    """The sentences of a stored list — items are records now, not bare strings."""
    return [item_text(v) for v in values or []]


def ids(values):
    return [v.get("id") for v in values or [] if isinstance(v, dict)]


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
    assert texts(mem.load(DEV)["mchat"]["facts"]) == ["likes dinosaurs"]
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
    facts = texts(mem.load(DEV)["mchat"]["facts"])
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
    assert texts(stored["facts"]) == ["ok"] and stored["n"] == 3 and "obj" not in stored
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
    assert texts(mem.load(DEV)["mchat"]["facts"]) == ["remembered before the switch"]
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
# items: a stable id, per-item provenance, and what a parent does with one line
# ---------------------------------------------------------------------------

def test_every_item_gets_a_stable_id_and_its_own_provenance(tmp_path):
    """The parent-facing half of BEYOND #4 starts here: without an id there is nothing
    to erase or correct one line at a time."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog", "is in year 2"]},
              provenance=provenance(module_id="MCHAT", turns=4, reason="exit"))
    facts = mem.load(DEV)["mchat"]["facts"]
    assert [f["id"] for f in facts] == [item_id("mchat", "facts", "has a dog"),
                                        item_id("mchat", "facts", "is in year 2")]
    assert all(len(f["id"]) == 8 for f in facts)
    assert facts[0]["_provenance"]["module_id"] == "MCHAT"
    assert facts[0]["_provenance"]["turns"] == 4
    # the same thing under a different kind (or namespace) is a different item
    assert item_id("mchat", "facts", "x") != item_id("mchat", "preferences", "x")
    assert item_id("mchat", "facts", "x") != item_id("free_chat", "facts", "x")


def test_ids_are_stable_across_reads_and_a_later_merge(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]}, provenance=provenance())
    first = ids(mem.load(DEV)["mchat"]["facts"])
    assert ids(mem.load(DEV)["mchat"]["facts"]) == first        # read twice, same ids
    assert ids(mem.view(DEV)["namespaces"]["mchat"]["data"]["facts"]) == first
    mem.merge(DEV, "mchat", {"facts": ["is in year 2"]}, provenance=provenance())
    kept = {f["text"]: f["id"] for f in mem.load(DEV)["mchat"]["facts"]}
    assert kept["has a dog"] == first[0]                        # the old item kept its id


def test_a_memory_file_written_before_ids_is_migrated_on_read(tmp_path):
    """Every robot that ran the first memory release has bare strings on disk. They must
    read back with ids (so they can be erased one at a time) and gain them permanently on
    the next merge, without changing what any of them say."""
    mem = _store(tmp_path)
    legacy = {"mchat": {"facts": ["has a dog", "is in year 2"],
                        "_meta": {"summarized_through": 4},
                        "_provenance": [{"at": 100.0, "date": "2026-01-01",
                                         "module_id": "MCHAT"}]}}
    mem.store.write(DEV, "memory", legacy)
    view = mem.view(DEV)["namespaces"]["mchat"]
    assert [f["text"] for f in view["data"]["facts"]] == ["has a dog", "is in year 2"]
    migrated = ids(view["data"]["facts"])
    assert migrated == [item_id("mchat", "facts", "has a dog"),
                        item_id("mchat", "facts", "is in year 2")]
    # a read never rewrites the file...
    assert mem.store.read(DEV, "memory")["mchat"]["facts"][0] == "has a dog"
    # ...the next merge does, to exactly the ids the read already showed
    mem.merge(DEV, "mchat", {"facts": ["likes drawing"]}, provenance=provenance())
    on_disk = {f["text"]: f["id"] for f in mem.load(DEV)["mchat"]["facts"]}
    assert [on_disk["has a dog"], on_disk["is in year 2"]] == migrated
    assert mem.load(DEV)["mchat"]["_meta"] == {"summarized_through": 4}


def test_view_carries_meta_so_the_console_can_show_summarized_through(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]}, provenance=provenance(),
              meta={"summarized_through": 6})
    ns = mem.view(DEV)["namespaces"]["mchat"]
    assert ns["meta"] == {"summarized_through": 6}
    assert list(ns["data"]) == ["facts"]        # `_meta`/`_provenance` still not in data


def test_erase_one_item_leaves_the_rest_of_the_activity(tmp_path):
    """The whole point: one wrong line goes without costing everything else."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog", "is in year 2"],
                             "preferences": ["likes drawing"]},
              provenance=provenance(), meta={"summarized_through": 6})
    wrong = mem.load(DEV)["mchat"]["facts"][0]["id"]
    assert mem.erase_item(DEV, "mchat", wrong) is True
    block = mem.load(DEV)["mchat"]
    assert texts(block["facts"]) == ["is in year 2"]
    assert texts(block["preferences"]) == ["likes drawing"]
    assert block["_meta"] == {"summarized_through": 6}   # not re-summarized afterwards
    assert mem.erase_item(DEV, "mchat", wrong) is False  # already gone
    assert mem.erase_item(DEV, "nope", wrong) is False


def test_erase_one_item_is_never_policy_gated(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]}, provenance=provenance())
    one = mem.load(DEV)["mchat"]["facts"][0]["id"]
    mem.policy = lambda device_id: 0                     # LoggingPolicy.NO_DATA
    assert mem.erase_item(DEV, "mchat", one) is True
    assert mem.load(DEV)["mchat"]["facts"] == []


def test_edit_an_item_keeps_its_id_and_pins_it(tmp_path):
    """PR #25's live run stored "Puppy sleeps on **his** bed" for "my bed". A parent
    fixes the pronoun instead of losing the activity."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["Puppy sleeps on his bed"]},
              provenance=provenance(module_id="MCHAT"))
    one = mem.load(DEV)["mchat"]["facts"][0]["id"]
    edited = mem.edit_item(DEV, "mchat", one, "Puppy sleeps on my bed")
    assert edited["id"] == one and edited["pinned"] is True and edited["edited_at"] > 0
    stored = mem.load(DEV)["mchat"]["facts"][0]
    assert stored["text"] == "Puppy sleeps on my bed" and stored["id"] == one
    assert stored["_provenance"]["module_id"] == "MCHAT"   # where it came from survives


def test_edit_refuses_what_may_never_be_remembered(tmp_path):
    """A text box that writes into every later prompt runs the same two rules the model's
    own summary does — otherwise the safety filter is one console field away from moot."""
    from moxie_sdk import safety as safety_seam
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]}, provenance=provenance())
    one = mem.load(DEV)["mchat"]["facts"][0]["id"]
    unsafe = "I want to kill myself"
    assert safety_seam.default_classifier().assess(
        unsafe, role=safety_seam.MOXIE).action == safety_seam.BLOCK
    with pytest.raises(ValueError):
        mem.edit_item(DEV, "mchat", one, unsafe)
    # ...and the child's own words, pasted back in, are refused too
    history = [{"role": "user",
                "content": "my grandma lives on Elm Street in the yellow house"}]
    with pytest.raises(ValueError):
        mem.edit_item(DEV, "mchat", one, history[0]["content"], history=history)
    with pytest.raises(ValueError):
        mem.edit_item(DEV, "mchat", one, "   ")             # an empty edit is an erase
    with pytest.raises(ValueError):
        mem.edit_item(DEV, "mchat", "nope", "fine text")
    assert texts(mem.load(DEV)["mchat"]["facts"]) == ["has a dog"]   # nothing changed


def test_edit_works_even_when_writing_new_memories_is_off(tmp_path):
    """`NO_DATA` stops Moxie learning. It must not stop a parent fixing what it learned
    before — the only alternative would be deleting a line that is nearly right."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["Puppy sleeps on his bed"]},
              provenance=provenance())
    one = mem.load(DEV)["mchat"]["facts"][0]["id"]
    mem.policy = lambda device_id: 0
    assert mem.edit_item(DEV, "mchat", one, "Puppy sleeps on my bed")["pinned"] is True
    assert texts(mem.load(DEV)["mchat"]["facts"]) == ["Puppy sleeps on my bed"]


def test_a_relearned_fact_keeps_its_pin_and_its_use_count(tmp_path):
    """Moxie hearing the same thing again must not quietly undo a parent's correction."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog"]}, provenance=provenance())
    one = mem.load(DEV)["mchat"]["facts"][0]["id"]
    mem.edit_item(DEV, "mchat", one, "has a beagle")
    mem.note_used(DEV, "FACTS:\n- has a beagle")
    mem.merge(DEV, "mchat", {"facts": ["has a beagle", "is in year 2"]},
              provenance=provenance(module_id="MCHAT", turns=9))
    again = {f["text"]: f for f in mem.load(DEV)["mchat"]["facts"]}
    assert again["has a beagle"]["id"] == one
    assert again["has a beagle"]["pinned"] is True
    assert again["has a beagle"]["use_count"] == 1
    assert again["has a beagle"]["_provenance"]["turns"] == 9      # newest attribution


# ---------------------------------------------------------------------------
# decay — the blunt, explicit floor
# ---------------------------------------------------------------------------

def test_a_rendered_prompt_marks_the_items_it_used(tmp_path):
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog", "is in year 2"]},
              provenance=provenance())
    assert mem.note_used(DEV, "FACTS:\n- has a dog\n") == 1
    facts = {f["text"]: f for f in mem.load(DEV)["mchat"]["facts"]}
    assert facts["has a dog"]["use_count"] == 1 and facts["has a dog"]["last_used_at"] > 0
    assert "use_count" not in facts["is in year 2"]      # never rendered, never counted
    assert mem.note_used(DEV, "FACTS:\n- has a dog\n") == 1
    assert mem.load(DEV)["mchat"]["facts"][0]["use_count"] == 2
    assert mem.note_used(DEV, "") == 0
    mem.policy = lambda device_id: 0            # NO_DATA: the clock simply stops
    assert mem.note_used(DEV, "FACTS:\n- has a dog\n") == 0


def test_decay_prunes_only_stale_unpinned_items(tmp_path):
    """Simple, explicit, and honest about what it cannot judge: this only knows whether a
    prompt has rendered an item lately, never whether it mattered."""
    old, now = 1_000_000.0, 1_000_000.0 + 200 * 86400
    data = {"mchat": {"facts": [
        {"id": "a", "text": "stale", "_provenance": {"at": old}},
        {"id": "b", "text": "pinned + stale", "_provenance": {"at": old}, "pinned": True},
        {"id": "c", "text": "used recently", "_provenance": {"at": old},
         "last_used_at": now - 86400},
        {"id": "d", "text": "undatable"},
    ], "_meta": {"summarized_through": 2}}}
    pruned, removed = prune_stale(data, max_age_days=90, now=now)
    assert removed == 1
    assert [f["text"] for f in pruned["mchat"]["facts"]] == [
        "pinned + stale", "used recently", "undatable"]
    # 0 turns it off entirely
    assert prune_stale({"mchat": {"facts": [{"id": "a", "text": "stale",
                                             "_provenance": {"at": old}}]}},
                       max_age_days=0, now=now)[1] == 0


def test_decay_runs_at_merge_time_and_is_configurable(tmp_path, monkeypatch):
    mem = _store(tmp_path)
    old = 1_000_000.0
    mem.merge(DEV, "mchat", {"facts": ["ancient"]},
              provenance=provenance(clock=lambda: old))
    monkeypatch.setenv("MOXIE_MEMORY_MAX_AGE_DAYS", "30")
    mem.merge(DEV, "mchat", {"facts": ["fresh"]}, provenance=provenance(),
              now=old + 100 * 86400)
    assert texts(mem.load(DEV)["mchat"]["facts"]) == ["fresh"]
    # off by default? no — 90 days is the default; 0 switches it off
    monkeypatch.setenv("MOXIE_MEMORY_MAX_AGE_DAYS", "0")
    mem.merge(DEV, "mchat", {"facts": ["ancient"]},
              provenance=provenance(clock=lambda: old))
    mem.merge(DEV, "mchat", {"facts": ["newer"]}, provenance=provenance(),
              now=old + 100 * 86400)
    assert sorted(texts(mem.load(DEV)["mchat"]["facts"])) == ["ancient", "fresh", "newer"]


def test_normalize_items_is_idempotent_and_leaves_non_items_alone():
    once = normalize_items("mchat", "facts", ["a", {"text": "b"}, 7, {"n": 1}])
    twice = normalize_items("mchat", "facts", once)
    assert once == twice
    assert [item_text(x) for x in once] == ["a", "b", None, None]
    assert once[2] == 7 and once[3] == {"n": 1}      # a module's own values, untouched


# ---------------------------------------------------------------------------
# rendering: persist_data really reaches the prompt
# ---------------------------------------------------------------------------

def test_render_exposes_persist_data():
    v = Volley(persist_data=wrap_facts({"mchat": {"facts": ["has a dog", "likes red"]}}))
    out = render_prompt("FACTS:\n{{ volley.persist_data.mchat.facts }}", {"volley": v})
    assert "- has a dog" in out and "- likes red" in out


def test_render_turns_stored_items_back_into_plain_bullets(tmp_path):
    """Items grew ids and provenance; prompts did not change one character. A model must
    never see an id, and a `{{ ... }}` that leaked a JSON blob into the system prompt
    would be a silent regression in every conversation."""
    mem = _store(tmp_path)
    mem.merge(DEV, "mchat", {"facts": ["has a dog", "likes red"], "empty": []},
              provenance=provenance())
    v = Volley(persist_data=wrap_facts(mem.load(DEV)))
    out = render_prompt("FACTS:\n{{ volley.persist_data.mchat.facts }}", {"volley": v})
    assert out == "FACTS:\n- has a dog\n- likes red"
    blank = render_prompt("X:{{ volley.persist_data.mchat.empty }}", {"volley": v})
    assert blank == "X:"                       # an empty list is blank, not "[]"


def test_render_missing_namespace_is_blank_not_an_error():
    v = Volley(persist_data={})
    out = render_prompt("FACTS:\n{{ volley.persist_data.mchat.facts }}", {"volley": v})
    assert out.strip() == "FACTS:"


def test_render_exposes_persist_data_without_jinja2_installed():
    """`render.py` falls back to a dependency-free `{{ dotted.path }}` substitution when
    jinja2 is absent — a bare `pip install moxie-cloud-sdk` with no `content` extra (the
    container itself now ships jinja2). Memory must render on that path too."""
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
    assert texts(block["facts"]) == ["Sam has a dog named Pepper", "Sam is in second grade"]
    assert texts(block["summaries"]) == ["They talked about pets and school."]
    # ...and each item carries its own attribution, not just the namespace's log
    assert block["facts"][0]["_provenance"]["module_id"] == "MCHAT"
    assert block["facts"][0]["_provenance"]["turns"] == 2
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
