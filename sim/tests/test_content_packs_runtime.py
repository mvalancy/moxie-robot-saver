"""
📦 Content packs through the REAL runtime — the store, the five routes, the live swap.

Tests 10 and 11 of `docs/architecture/backlog/content-packs.md` §3. Everything runs against
a genuine `MoxieRuntime` on a scratch data dir with a fake MQTT transport
(`helpers_runtime.make_runtime`) and its OWN status HTTP server on a free port
(`helpers_runtime.status_server`) — so what is proved is the handler the parent console
actually talks to, not a double of it. No broker, no gateway, no robot, no sleeps.

The two claims this file exists for:

* **An imported pack is live on the next turn, with no restart** — `test_an_imported_pack_…`
  drives a real turn through the runtime and reads the new prompt back out of the reply.
* **Nothing else moves.** An import publishes nothing on the wire and never re-pushes
  config: a P0 pack carries no `RobotCloudConfig` field, which is why face/config packs are
  P2. Asserted, not assumed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("paho.mqtt.client", reason="the runtime's transport")
pytest.importorskip("jinja2", reason="content prompts are Jinja templates")

from helpers_runtime import (drive_turn, http_json, make_runtime,  # noqa: E402
                             status_server)
from moxie_sdk.content import ContentApp                            # noqa: E402
from moxie_sdk.content import packs as P                            # noqa: E402
from moxie_sdk.store import JsonStore                               # noqa: E402

SHIPPED_PROMPT = "You are Moxie, the shipped starter chat."
PACK_PROMPT = "You are Moxie, and the imported pack wrote this line."
IDENT = "conversation:FREE_CHAT/default"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def shipped_module(prompt=SHIPPED_PROMPT, version=1) -> dict:
    """The `MOXIE_CONTENT_MODULE` file every appliance has on disk today."""
    return {"conversations": [{"name": "Free Chat", "module_id": "FREE_CHAT",
                               "content_id": "default", "prompt": prompt,
                               "opener": "Hi!", "source_version": version}],
            "globals": [{"name": "Timer", "pattern": r"timer for (\d+)",
                         "entity_groups": "1"}]}


def echo_prompt(messages):
    """A brain that says the system prompt back — so a turn reveals which module ran."""
    return messages[0]["content"].splitlines()[0]


def build(tmp_path, *, prompt=SHIPPED_PROMPT, version=1, chat=echo_prompt,
          defaults=None):
    """A real runtime whose app is a real `ContentApp` over the shipped module.

    Boots the way `config.build_content_app()` does — shipped defaults, then whatever
    overlay is already in this data dir — so calling it twice against one `tmp_path` is a
    faithful *restart*, which is how several tests below prove an import survives one.
    """
    store = JsonStore(str(tmp_path))
    shipped = P.shipped_items(shipped_module(prompt, version) if defaults is None
                              else defaults)
    stored = store.read_shared("content_items", {}) or {}
    overlay = stored.get("items") if isinstance(stored, dict) else None
    app = ContentApp(P.build_module(shipped, overlay if isinstance(overlay, dict) else {}),
                     chat, memory=False, content_defaults=shipped)
    rt, device_id = make_runtime(app, store=store)
    return rt, device_id


def pack_of(prompt=PACK_PROMPT, version=2, now=1788400000, **kw) -> dict:
    item = {"kind": "conversation", "key": "FREE_CHAT/default",
            "source_version": version,
            "data": dict({"name": "Free Chat", "module_id": "FREE_CHAT",
                          "content_id": "default", "prompt": prompt,
                          "opener": "Hello from the pack!"}, **kw)}
    return P.export_pack([item], name="Bedtime wind-down", pack_id="bedtime-wind-down",
                         now=now)


def post(base, path, body=None, *, method="POST"):
    """`(status, payload)` — `http_json` raises on 4xx/5xx and we want to read the body."""
    try:
        return 200, http_json(base + path, method=method, body=body if body is not None
                              else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def review(rt, pack):
    return rt.content_review(json.dumps(pack))


# --------------------------------------------------------------------------- #
# 10 · The runtime: the overlay, the swap, and the next turn
# --------------------------------------------------------------------------- #

def test_a_fresh_appliance_loads_exactly_the_shipped_defaults(tmp_path):
    """Acceptance criterion 9 — nothing imported, nothing changed."""
    rt, _ = build(tmp_path)
    view = rt.content_view()
    assert [r["id"] for r in view["items"]] == [IDENT, "global:Timer"]
    assert {r["origin"] for r in view["items"]} == {"shipped"}
    assert view["packs"] == [] and view["undo_available"] is False
    assert rt._content_overlay() == {}
    assert rt.app.module.conversation("FREE_CHAT").prompt == SHIPPED_PROMPT


def test_an_imported_pack_is_live_on_the_next_turn_with_no_restart(tmp_path):
    """The whole point of `reload_content()` (acceptance criterion 8)."""
    rt, device_id = build(tmp_path)
    first = drive_turn(rt, device_id, "hello")
    assert first["output"]["text"] == SHIPPED_PROMPT

    out = rt.content_import(pack_of(), [IDENT])
    assert out["ok"] and out["applied"] == [IDENT]
    assert out["reload"]["live"] is True

    # live in THIS process (the swap), and after a restart (the store)
    assert rt.app.module.conversation("FREE_CHAT").prompt == PACK_PROMPT
    rt2, d2 = build(tmp_path)                    # a second process, same data dir
    assert drive_turn(rt2, d2, "hello")["output"]["text"] == PACK_PROMPT


def test_a_turn_that_has_already_started_finishes_on_the_module_it_started_with(tmp_path):
    """The documented no-lock rule (§2.5): the swap is one attribute assignment, and a
    turn that has already resolved its conversation keeps it for that turn."""
    swapped = []

    def chat_then_swap(messages):
        if not swapped:
            swapped.append(rt.content_import(pack_of(), [IDENT]))
        return messages[0]["content"].splitlines()[0]

    rt, device_id = build(tmp_path, chat=chat_then_swap)
    during = drive_turn(rt, device_id, "hello")
    assert swapped and swapped[0]["ok"]
    assert during["output"]["text"] == SHIPPED_PROMPT, "the in-flight turn kept its module"

    rt2, d2 = build(tmp_path)
    assert drive_turn(rt2, d2, "hello")["output"]["text"] == PACK_PROMPT


def test_the_overlay_is_written_never_the_merged_view(tmp_path):
    """Only the accepted items land in `fleet/content_items.json` — so a later release's
    improved shipped item is still an upgrade rather than something the overlay shadows."""
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(), [IDENT])
    assert list(rt._content_overlay()) == [IDENT]
    assert set(rt.content_items()) == {IDENT, "global:Timer"}
    stored = json.load(open(rt.store.shared_path("content_items")))
    assert list(stored["items"]) == [IDENT]


def test_the_merge_order_is_defaults_then_overlay_both_ways(tmp_path):
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(prompt="overlay wins"), [IDENT])
    assert rt.content_items()[IDENT]["data"]["prompt"] == "overlay wins"
    assert rt.content_items()["global:Timer"]["data"]["pattern"] == r"timer for (\d+)"
    # and the other way: a shipped item the overlay does not name is untouched
    assert rt.content_items()["global:Timer"]["provenance"]["origin"] == "shipped"
    assert rt.content_items()[IDENT]["provenance"]["origin"] == "pack"


def test_reload_content_survives_a_restart_through_the_store(tmp_path):
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(), [IDENT])
    again, _ = build(tmp_path)                   # a brand-new runtime, same data dir
    assert again.reload_content()["overlay"] == 1
    assert again.app.module.conversation("FREE_CHAT").prompt == PACK_PROMPT


def test_an_import_publishes_nothing_and_never_re_pushes_config(tmp_path):
    """A P0 pack carries no `RobotCloudConfig` field — that is why face packs are P2."""
    rt, _ = build(tmp_path)
    rt.client.published.clear()
    rt.content_import(pack_of(), [IDENT])
    rt.content_undo()
    assert rt.client.published == [], "content packs never touch the wire"


def test_undo_restores_the_previous_content_exactly(tmp_path):
    """Acceptance criterion 4, through the store rather than in the abstract."""
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(prompt="v2 from the pack", version=2), [IDENT])
    mine = P.mark_edited(rt._content_overlay(), IDENT,
                         dict(rt.content_items()[IDENT]["data"],
                              prompt="MY prompt, which I wrote."))
    rt._write_content_overlay(mine)
    rt.reload_content()
    before = P.canonical(rt._content_overlay())

    rt.content_import(pack_of(prompt="v3 from the pack", version=3), [IDENT])
    assert rt.app.module.conversation("FREE_CHAT").prompt == "v3 from the pack"

    out = rt.content_undo()
    assert out["ok"] and out["restored"] == 1
    assert P.canonical(rt._content_overlay()) == before, "byte for byte"
    assert rt.app.module.conversation("FREE_CHAT").prompt == "MY prompt, which I wrote."


def test_undo_is_one_slot_and_says_so(tmp_path):
    rt, _ = build(tmp_path)
    assert rt.content_undo()["ok"] is False
    rt.content_import(pack_of(), [IDENT])
    assert rt.content_view()["undo_available"] is True
    assert "bedtime" in rt.content_view()["undo_label"].lower()
    assert rt.content_undo()["ok"] is True
    assert rt.content_view()["undo_available"] is False
    assert rt.content_undo()["ok"] is False, "the slot is used up"


def test_undo_also_rolls_back_the_pack_ledger(tmp_path):
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(), [IDENT])
    assert [p["id"] for p in rt.content_view()["packs"]] == ["bedtime-wind-down"]
    rt.content_undo()
    assert rt.content_view()["packs"] == []


def test_an_import_that_applies_nothing_takes_no_snapshot(tmp_path):
    rt, _ = build(tmp_path)
    out = rt.content_import(pack_of(), [])
    assert out["ok"] and out["applied"] == []
    assert rt.content_view()["undo_available"] is False


def test_re_importing_a_pack_updates_its_ledger_row_rather_than_duplicating_it(tmp_path):
    rt, _ = build(tmp_path)
    rt.content_import(pack_of(version=2), [IDENT])
    rt.content_import(pack_of(prompt="newer", version=3), [IDENT])
    packs = rt.content_view()["packs"]
    assert [p["id"] for p in packs] == ["bedtime-wind-down"]
    assert packs[0]["item_count"] == 1


def test_the_review_against_a_shipped_item_a_parent_edited_is_a_conflict(tmp_path):
    """The clobber guarantee, end to end through the store."""
    rt, _ = build(tmp_path, version=1)
    rt._write_content_overlay(P.mark_edited(
        {}, IDENT, dict(rt.content_items()[IDENT]["data"], prompt="I edited this.")))
    rt.reload_content()
    rows = {r["id"]: r for r in review(rt, pack_of(version=2))["items"]}
    assert rows[IDENT]["state"] == P.CONFLICT
    assert rows[IDENT]["default"] is False
    assert rows[IDENT]["local_edited"] is True


def test_content_export_builds_a_pack_from_the_installed_items(tmp_path):
    rt, _ = build(tmp_path)
    pack = rt.content_export([IDENT], name="Just the chat", now=1788400000)
    assert [i["key"] for i in pack["items"]] == ["FREE_CHAT/default"]
    assert pack["items"][0]["data"]["prompt"] == SHIPPED_PROMPT
    assert pack["digest"] == P.pack_digest(pack)
    everything = rt.content_export(None, name="All of it", now=1788400000)
    assert len(everything["items"]) == 2


def test_exporting_something_that_is_not_installed_is_an_error(tmp_path):
    rt, _ = build(tmp_path)
    with pytest.raises(P.PackError) as e:
        rt.content_export(["conversation:NOPE/x"], name="x")
    assert "not installed" in str(e.value)


def test_the_export_flags_a_prompt_that_names_the_child(tmp_path):
    """The residual leak of §2.2, named honestly: it catches the names we know."""
    rt, _ = build(tmp_path, prompt="You are talking to Sam, who is six.")
    rows = {r["id"]: r for r in rt.content_view()["items"]}
    assert rows[IDENT]["pii"] == [{"field": "prompt", "name": "Sam"}]
    assert rows["global:Timer"]["pii"] == []
    pack = rt.content_export([IDENT], name="leaky", now=1788400000)
    assert "Sam" in pack["items"][0]["data"]["prompt"], "flagged, never blocked"


def test_review_writes_nothing_to_the_store(tmp_path):
    rt, _ = build(tmp_path)
    review(rt, pack_of())
    assert rt._content_overlay() == {}
    assert not os.path.exists(rt.store.shared_path("content_items"))
    assert not os.path.exists(rt.store.shared_path("content_backup"))


def test_an_import_of_a_pack_that_is_not_the_reviewed_one_is_refused(tmp_path):
    rt, _ = build(tmp_path)
    reviewed = review(rt, pack_of(prompt="the one I read"))
    out = rt.content_import(pack_of(prompt="a different one"), [IDENT],
                            reviewed["expect_digest"])
    assert out["ok"] is False and out["conflict"] is True
    assert rt._content_overlay() == {}


def test_the_reviewed_digest_lets_the_same_pack_through(tmp_path):
    rt, _ = build(tmp_path)
    pack = pack_of()
    reviewed = review(rt, pack)
    assert rt.content_import(pack, reviewed["accept"], reviewed["expect_digest"])["ok"]
    assert reviewed["accept"] == [IDENT], "a new-vs-installed upgrade is pre-ticked"


def test_a_code_carrying_pack_imports_and_the_string_is_never_executed(tmp_path):
    rt, _ = build(tmp_path)
    boom = "raise SystemExit('a pack must never run this')"
    rt.content_import(pack_of(code=boom), [IDENT])
    assert rt.content_items()[IDENT]["data"]["code"] == boom
    assert rt.content_view()["counts"]["with_code"] == 1
    rows = {r["id"]: r for r in review(rt, pack_of(code=boom, version=9))["items"]}
    assert any("never runs" in w for w in rows[IDENT]["warnings"])
    # …and a turn still runs, because the string is data
    rt2, d2 = build(tmp_path)
    assert drive_turn(rt2, d2, "hello")["output"]["text"] == PACK_PROMPT


def test_a_pack_sent_as_raw_text_is_digested_over_exactly_those_bytes(tmp_path):
    """What the 📦 card actually sends. The console never re-serializes a pack: a browser
    would turn `1.0` into `1` and make a perfectly good file report as tampered."""
    rt, _ = build(tmp_path)
    raw = P.dumps_pack(pack_of(temperature=1.0))
    assert '"temperature": 1.0' in raw
    reviewed = rt.content_review(raw)
    assert reviewed["digest"] == "ok"
    out = rt.content_import(raw, [IDENT], reviewed["expect_digest"])
    assert out["ok"] and out["applied"] == [IDENT]
    assert rt.content_items()[IDENT]["data"]["temperature"] == 1.0


def test_the_pack_size_cap_is_env_configurable(tmp_path, monkeypatch):
    import moxie_runtime
    monkeypatch.setenv("MOXIE_PACK_MAX_BYTES", "4096")
    assert moxie_runtime.MoxieRuntime.pack_max_bytes() == 4096
    monkeypatch.setenv("MOXIE_PACK_MAX_BYTES", "not a number")
    assert moxie_runtime.MoxieRuntime.pack_max_bytes() == P.DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------- #
# 11 · The five routes, through the runtime's own status HTTP server
# --------------------------------------------------------------------------- #

@pytest.fixture()
def served(tmp_path):
    rt, device_id = build(tmp_path)
    return rt, status_server(rt), device_id


def test_get_content_serves_the_inventory_and_the_ledger(served):
    rt, base, _ = served
    out = http_json(base + "/content")
    assert out["ok"] is True
    assert {r["id"] for r in out["items"]} == {IDENT, "global:Timer"}
    assert set(out) == {"ok", "items", "packs", "counts", "undo_available",
                        "undo_label", "max_bytes", "pack_format"}
    assert out["pack_format"] == P.PACK_FORMAT
    assert out["counts"]["total"] == 2


def test_get_content_export_serves_the_pack_file_itself(served):
    rt, base, _ = served
    pack = http_json(base + "/content/export?items=" + IDENT + "&name=Chat&id=my-chat")
    assert pack["pack_format"] == 1 and pack["id"] == "my-chat"
    assert [i["key"] for i in pack["items"]] == ["FREE_CHAT/default"]
    assert pack["digest"] == P.pack_digest(pack)
    everything = http_json(base + "/content/export?name=All")
    assert len(everything["items"]) == 2


def test_get_content_export_refuses_an_item_that_is_not_installed(served):
    _rt, base, _ = served
    code, body = post(base, "/content/export?items=conversation:NOPE/x", method="GET")
    assert code == 400 and "not installed" in body["error"]


def test_post_content_review_returns_a_row_per_item_and_writes_nothing(served):
    rt, base, _ = served
    out = http_json(base + "/content/review", method="POST", body=pack_of())
    assert out["ok"] is True and out["digest"] == "ok"
    assert [r["id"] for r in out["items"]] == [IDENT]
    assert out["items"][0]["state"] == P.UPGRADE and out["items"][0]["default"] is True
    assert out["accept"] == [IDENT]
    assert "items" not in out["pack"] and out["pack"]["name"] == "Bedtime wind-down"
    assert rt._content_overlay() == {}


def test_post_content_review_of_a_tampered_pack_ticks_nothing(served):
    _rt, base, _ = served
    body = pack_of()
    body["items"][0]["data"]["prompt"] = "an edit made after the export"
    out = http_json(base + "/content/review", method="POST", body=body)
    assert out["digest"] == "mismatch"
    assert out["accept"] == [] and not any(r["default"] for r in out["items"])


def test_post_content_import_applies_only_what_was_accepted(served):
    rt, base, _ = served
    out = http_json(base + "/content/import", method="POST",
                    body={"pack": pack_of(), "accept": [IDENT]})
    assert out["ok"] and out["applied"] == [IDENT] and out["count"] == 1
    assert out["reload"]["live"] is True
    assert http_json(base + "/content")["undo_available"] is True
    assert rt.app.module.conversation("FREE_CHAT").prompt == PACK_PROMPT


def test_post_content_import_is_409_when_the_digest_is_not_the_reviewed_one(served):
    rt, base, _ = served
    reviewed = http_json(base + "/content/review", method="POST", body=pack_of())
    code, body = post(base, "/content/import",
                      {"pack": pack_of(prompt="a different file"), "accept": [IDENT],
                       "expect_digest": reviewed["expect_digest"]})
    assert code == 409
    assert body["ok"] is False and "not the pack that was reviewed" in body["error"]
    assert rt._content_overlay() == {}


def test_post_content_import_is_400_for_a_key_that_is_not_in_the_pack(served):
    _rt, base, _ = served
    code, body = post(base, "/content/import",
                      {"pack": pack_of(), "accept": ["conversation:NOPE/x"]})
    assert code == 400 and "not in this pack" in body["error"]


def test_post_content_import_is_400_for_an_unreadable_pack(served):
    _rt, base, _ = served
    code, body = post(base, "/content/import", {"pack": {"pack_format": 99, "items": []}})
    assert code == 400 and "pack_format" in body["error"]


def test_post_content_undo_restores_and_is_404_when_there_is_nothing_to_undo(served):
    rt, base, _ = served
    code, body = post(base, "/content/undo")
    assert code == 404 and body["error"] == "nothing to undo"
    http_json(base + "/content/import", method="POST",
              body={"pack": pack_of(), "accept": [IDENT]})
    out = http_json(base + "/content/undo", method="POST")
    assert out["ok"] and out["restored"] == 0
    assert rt.app.module.conversation("FREE_CHAT").prompt == SHIPPED_PROMPT


def test_a_body_over_the_cap_is_413_and_is_never_buffered(served, monkeypatch):
    _rt, base, _ = served
    monkeypatch.setenv("MOXIE_PACK_MAX_BYTES", "512")
    big = pack_of(prompt="x" * 4096)
    code, body = post(base, "/content/review", big)
    assert code == 413 and body["max_bytes"] == 512
    assert "too big" in body["reason"]


def test_unknown_content_routes_are_404(served):
    _rt, base, _ = served
    for path, method in (("/content/nope", "GET"), ("/content/nope", "POST"),
                         ("/contents", "GET")):
        with pytest.raises(urllib.error.HTTPError) as e:
            http_json(base + path, method=method,
                      body={} if method == "POST" else None)
        assert e.value.code == 404


def test_the_whole_round_trip_over_http(served):
    """Export → review → import → read the inventory back, all through the real server."""
    rt, base, device_id = served
    exported = http_json(base + "/content/export?items=" + IDENT + "&name=Mine&id=mine")
    exported["items"][0]["data"]["prompt"] = "A prompt I wrote by hand."
    exported["items"][0]["source_version"] = 5
    exported["digest"] = P.pack_digest(exported)          # re-signed, so it is not tampered

    reviewed = http_json(base + "/content/review", method="POST", body=exported)
    assert reviewed["digest"] == "ok"
    assert reviewed["items"][0]["state"] == P.UPGRADE

    applied = http_json(base + "/content/import", method="POST",
                        body={"pack": exported, "accept": reviewed["accept"],
                              "expect_digest": reviewed["expect_digest"]})
    assert applied["applied"] == [IDENT]

    view = http_json(base + "/content")
    row = {r["id"]: r for r in view["items"]}[IDENT]
    assert (row["origin"], row["pack_id"], row["source_version"]) == ("pack", "mine", 5)
    assert row["local_edited"] is False
    assert [p["id"] for p in view["packs"]] == ["mine"]
    assert drive_turn(rt, device_id, "hi")["output"]["text"] == "A prompt I wrote by hand."
