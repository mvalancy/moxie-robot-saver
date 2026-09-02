"""
The parent console's memory view — `moxie_server/fleet.py::normalize_memory`.

`test_memory.py` covers the store and the summarizer; `test_memory_runtime.py` covers the
runtime's `/memory` endpoints. This is the third piece: the **pure** transform that turns
what the runtime serves into what the 🧠 "What Moxie remembers" card renders — flat dated
rows per activity, counts, and the two hints a parent needs (the privacy switch and how
far a transcript was summarized).

Pure (no fastapi, no network, no supervisor), so it runs in the hermetic suite exactly
like `test_fleet.py`. The shape it consumes is `MemoryStore.view()` wrapped by
`MoxieRuntime.memory_view()`:

    {ok, device_id, policy, writes_allowed, bytes,
     namespaces: {ns: {data: {facts: [...], ...}, provenance: [{at, date, module_id,
                                                               turns, reason}, ...]}}}
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from moxie_server.fleet import (  # noqa: E402
    memory_provenance, normalize_memory, normalize_namespace,
)


def _prov(at, module_id="MCHAT", **kw):
    out = {"at": at, "date": kw.pop("date", "2026-09-0%d" % (int(at) % 9 + 1)),
           "module_id": module_id, "content_id": "default", "conversation_id": "",
           "turns": kw.pop("turns", 2), "reason": kw.pop("reason", "exit"),
           "source": "session.summarize"}
    out.update(kw)
    return out


def _view():
    """A two-namespace `memory_view()` payload, as the runtime serves it."""
    return {
        "ok": True, "device_id": "d_abc", "policy": "NO_MEDIA", "writes_allowed": True,
        "bytes": 412,
        "namespaces": {
            "mchat": {
                "data": {"facts": ["Sam has a beagle named Pepper", "Sam is in year 2"],
                         "preferences": ["Likes drawing"],
                         "open_threads": ["Ask how the play went"],
                         "summaries": ["They talked about pets."]},
                "provenance": [_prov(200, turns=4), _prov(100, turns=2)],
            },
            "free_chat": {
                "data": {"facts": ["Sam's favourite colour is red"]},
                "provenance": [_prov(50, module_id="FREE_CHAT", reason="switch")],
            },
        },
    }


# --- shape -------------------------------------------------------------------------

def test_the_whole_view_flattens_into_dated_rows():
    m = normalize_memory(_view())
    assert m["ok"] is True and m["device_id"] == "d_abc" and m["error"] is None
    assert m["policy"] == "NO_MEDIA" and m["writes_allowed"] is True and m["bytes"] == 412
    assert m["namespace_count"] == 2 and m["total"] == 6
    ns = {n["namespace"]: n for n in m["namespaces"]}
    assert set(ns) == {"mchat", "free_chat"}
    assert ns["mchat"]["counts"] == {"facts": 2, "preferences": 1, "open_threads": 1,
                                     "summaries": 1, "total": 5}
    assert [i["kind"] for i in ns["mchat"]["items"]] == [
        "fact", "fact", "preference", "open thread", "summary"]
    first = ns["mchat"]["items"][0]
    assert first["text"] == "Sam has a beagle named Pepper"
    assert first["provenance"] == {"date": "2026-09-03", "at": 200, "module_id": "MCHAT",
                                   "content_id": "default", "turns": 4, "reason": "exit"}
    # the namespace header carries the newest attribution + how many conversations fed it
    assert ns["mchat"]["last_learned"]["module_id"] == "MCHAT"
    assert ns["mchat"]["conversations"] == 2


def test_every_value_is_json_safe():
    """Nothing exotic survives the transform — the console serializes this straight out."""
    import json
    m = normalize_memory(_view())
    assert json.loads(json.dumps(m)) == m


# --- ordering ----------------------------------------------------------------------

def test_namespaces_are_newest_first_by_provenance_date():
    m = normalize_memory(_view())
    assert [n["namespace"] for n in m["namespaces"]] == ["mchat", "free_chat"]
    # ...and it is the provenance that decides, not the alphabet
    flipped = _view()
    flipped["namespaces"]["free_chat"]["provenance"] = [_prov(900, module_id="FREE_CHAT")]
    m2 = normalize_memory(flipped)
    assert [n["namespace"] for n in m2["namespaces"]] == ["free_chat", "mchat"]


def test_per_item_provenance_is_honoured_when_a_store_ever_records_it():
    """Items are plain strings today (provenance is per merge). A dict item carrying its
    own `_provenance` sorts and renders on that, so a runtime that later attributes item
    by item needs no console change."""
    ns = normalize_namespace("mchat", {
        "data": {"facts": [
            {"text": "older", "_provenance": _prov(10, date="2026-01-01")},
            {"text": "newer", "_provenance": _prov(900, date="2026-09-02")},
        ]},
        "provenance": [_prov(500)]})
    assert [i["text"] for i in ns["items"]] == ["newer", "older"]
    assert ns["items"][0]["provenance"]["date"] == "2026-09-02"


def test_undated_items_fall_back_to_the_namespaces_own_provenance():
    ns = normalize_namespace("mchat", {"data": {"facts": ["a"]}, "provenance": []})
    assert ns["items"][0]["provenance"] == {"date": "", "at": None, "module_id": "",
                                            "content_id": "", "turns": 0, "reason": ""}


# --- tolerance ---------------------------------------------------------------------

def test_nothing_remembered_yet_is_an_empty_view_not_an_error():
    m = normalize_memory({"ok": True, "device_id": "d1", "namespaces": {},
                          "bytes": 0, "writes_allowed": True, "policy": "NO_MEDIA"})
    assert m["ok"] is True and m["total"] == 0 and m["namespaces"] == []
    assert m["error"] is None


def test_supervisor_down_and_none_are_safe():
    for payload in (None, {"ok": False, "error": "supervisor not reachable"}, {}):
        m = normalize_memory(payload)
        assert m["ok"] is False and m["namespaces"] == [] and m["total"] == 0
        assert m["writes_allowed"] is False and m["error"]


def test_an_unknown_device_keeps_the_runtimes_reason():
    m = normalize_memory({"ok": False, "device_id": "d_x",
                          "error": "unknown device_id 'd_x'"})
    assert m["ok"] is False and m["error"] == "unknown device_id 'd_x'"


def test_a_partial_namespace_never_raises():
    m = normalize_memory({"ok": True, "namespaces": {
        "half": {"data": {"facts": None, "preferences": "not a list"},
                 "provenance": ["junk", {"at": "12", "turns": "3"}]},
        "empty": {},
        "weird": "not even a dict",
    }})
    assert m["ok"] is True
    ns = {n["namespace"]: n for n in m["namespaces"]}
    assert ns["half"]["counts"]["preferences"] == 1        # a bare string is one item
    assert ns["half"]["items"][0]["provenance"]["at"] == 12   # "12" coerced
    assert ns["half"]["items"][0]["provenance"]["turns"] == 3
    assert ns["empty"]["items"] == [] and ns["weird"]["items"] == []


def test_a_list_a_module_invented_is_still_shown():
    """A count that hides rows a parent cannot see would be worse than an ugly label."""
    ns = normalize_namespace("mchat", {"data": {"pet_names": ["Pepper"]},
                                       "provenance": [_prov(1)]})
    assert ns["counts"]["pet_names"] == 1 and ns["counts"]["total"] == 1
    assert ns["items"][0]["kind"] == "pet_names" and ns["items"][0]["text"] == "Pepper"


def test_a_raw_memory_json_off_disk_renders_too():
    """The same transform reads the stored file, whose `_`-prefixed keys the runtime's
    view() strips — which is the only place `summarized_through` survives today."""
    m = normalize_memory({"mchat": {"facts": ["has a dog"],
                                    "_meta": {"summarized_through": 6},
                                    "_provenance": [_prov(3)]}})
    assert m["ok"] is True and m["total"] == 1
    assert m["summarized_through"] == 6
    assert m["namespaces"][0]["summarized_through"] == 6
    assert m["namespaces"][0]["items"][0]["text"] == "has a dog"


def test_an_erase_reply_keeps_its_confirmation():
    m = normalize_memory({"ok": True, "device_id": "d1", "namespaces": {},
                          "erased": True, "namespace": "mchat", "policy": "NO_MEDIA"})
    assert m["erased"] is True and m["namespace"] == "mchat" and m["total"] == 0


def test_the_privacy_switch_comes_through_for_the_ui_note():
    m = normalize_memory({"ok": True, "device_id": "d1", "policy": "NO_DATA",
                          "writes_allowed": False, "bytes": 120,
                          "namespaces": {"mchat": {"data": {"facts": ["old fact"]},
                                                   "provenance": [_prov(1)]}}})
    # NO_DATA stops new writes; what was stored before is still readable AND erasable
    assert m["writes_allowed"] is False and m["policy"] == "NO_DATA" and m["total"] == 1


def test_memory_provenance_tolerates_anything():
    assert memory_provenance(None)["turns"] == 0
    assert memory_provenance({"turns": "5", "date": 20260902})["date"] == "20260902"
    assert memory_provenance("nonsense")["module_id"] == ""
