"""
The console's "what Moxie remembers" card against the supervisor's REAL `MemoryStore`.

A parent can read every durable fact with its day and activity, correct one line (which
pins it against decay), forget one item, one activity or everything — and every erase is
asserted on the supervisor's store afterwards, not just on the console's reply. The fake
supervisor and its seeded memory live in `helpers_console_supervisor.py`; tests that
change memory put the seed back.
"""
import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console_supervisor import (DEVICE, client, reseed,  # noqa: E402,F401
                                        seed_memory, supervisor)

URL = f"/local/robots/{DEVICE}/memory"


def _namespaces(client):
    return {n["namespace"]: n for n in client.get(URL).json()["namespaces"]}


def test_memory_reaches_the_console_as_dated_rows(client, supervisor):
    r = client.get(URL)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["ok"] is True and m["device_id"] == DEVICE
    assert m["policy"] == "NO_MEDIA" and m["writes_allowed"] is True and m["bytes"] > 0
    assert m["namespace_count"] == 2 and m["total"] == 5
    ns = {n["namespace"]: n for n in m["namespaces"]}
    assert set(ns) == {"mchat", "free_chat"}
    assert ns["mchat"]["counts"] == {"facts": 2, "preferences": 1, "open_threads": 0,
                                     "summaries": 1, "total": 4}
    texts = [i["text"] for i in ns["mchat"]["items"]]
    assert "Sam has a beagle named Pepper" in texts
    assert "They talked about pets." in texts
    top = ns["mchat"]["items"][0]
    assert top["provenance"]["module_id"] == "MCHAT" and top["provenance"]["turns"] == 4
    assert top["provenance"]["date"] and top["provenance"]["reason"] == "exit"
    # newest activity first (mchat's provenance is later than free_chat's)
    assert [n["namespace"] for n in m["namespaces"]] == ["mchat", "free_chat"]
    assert supervisor.memory_queries[-1] == DEVICE
    # the card can say how far the transcript was written down
    assert m["summarized_through"] == 6
    assert ns["mchat"]["summarized_through"] == 6
    # ...and every row carries the unique id the per-item erase and edit act on
    assert all(i["id"] for i in ns["mchat"]["items"])
    assert len({i["id"] for i in ns["mchat"]["items"]}) == len(ns["mchat"]["items"])
    assert all(i["pinned"] is False for i in ns["mchat"]["items"])


def test_erasing_one_activity_forwards_and_the_next_read_reflects_it(client, supervisor):
    r = client.delete(f"{URL}/mchat")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["erased"] is True and body["namespace"] == "mchat"
    assert supervisor.memory_erases[-1] == (DEVICE, "mchat")
    after = client.get(URL).json()
    assert [n["namespace"] for n in after["namespaces"]] == ["free_chat"]
    assert after["total"] == 1
    assert supervisor.memory.load(DEVICE).get("mchat") is None


def test_erasing_everything_empties_the_store(client, supervisor):
    r = client.delete(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["erased"] is True and body["namespace"] == "all"
    assert body["namespaces"] == [] and body["total"] == 0
    assert supervisor.memory_erases[-1] == (DEVICE, "all")
    assert supervisor.memory.load(DEVICE) == {}
    # ...and the empty state is an empty view, not an error
    again = client.get(URL).json()
    assert again["ok"] is True and again["total"] == 0 and again["error"] is None
    seed_memory(supervisor.runtime)


def test_correcting_one_line_forwards_and_pins_it(client, supervisor):
    """A parent fixes a mis-heard line instead of erasing the activity, and the
    correction is pinned so decay never takes it."""
    total = client.get(URL).json()["total"]
    row = [i for i in _namespaces(client)["mchat"]["items"]
           if i["text"].startswith("Sam has a beagle")][0]
    r = client.post(f"{URL}/mchat/{row['id']}", json={"text": "Sam has a beagle named Peppa"})
    assert r.status_code == 200, r.text
    assert r.json()["edited"] is True and r.json()["item"] == row["id"]
    assert supervisor.memory_edits[-1] == (DEVICE, "mchat", row["id"],
                                           "Sam has a beagle named Peppa")
    fixed = [i for i in _namespaces(client)["mchat"]["items"] if i["id"] == row["id"]][0]
    assert fixed["text"] == "Sam has a beagle named Peppa" and fixed["pinned"] is True
    assert client.get(URL).json()["total"] == total      # nothing else was touched
    reseed(supervisor)


def test_a_correction_the_safety_check_refuses_is_a_400_with_a_reason(client):
    """A text box that writes into every later prompt must not bypass the safety filter,
    and the card must be able to say why."""
    total = client.get(URL).json()["total"]
    row = _namespaces(client)["mchat"]["items"][0]
    r = client.post(f"{URL}/mchat/{row['id']}", json={"text": "I want to kill myself"})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False and r.json()["error"]
    unchanged = client.get(URL).json()
    assert unchanged["total"] == total
    assert "I want to kill myself" not in {
        i["text"] for n in unchanged["namespaces"] for i in n["items"]}


def test_forgetting_one_item_leaves_the_rest_of_the_activity(client, supervisor):
    ns = _namespaces(client)["mchat"]
    row, kept = ns["items"][0], ns["counts"]["total"] - 1
    r = client.delete(f"{URL}/mchat/{row['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["erased"] is True and r.json()["item"] == row["id"]
    assert supervisor.memory_erases[-1] == (DEVICE, f"mchat/{row['id']}")
    after = _namespaces(client)
    assert after["mchat"]["counts"]["total"] == kept
    assert row["id"] not in {i["id"] for i in after["mchat"]["items"]}
    assert "free_chat" in after                     # the other activity is untouched
    # ...and the store really lost that one line, not just the console's copy
    stored = supervisor.memory.load(DEVICE)["mchat"]
    assert row["id"] not in {i.get("id") for v in stored.values()
                             if isinstance(v, list) for i in v if isinstance(i, dict)}
    assert stored["_meta"] == {"summarized_through": 6}   # not re-summarized after
    reseed(supervisor)
