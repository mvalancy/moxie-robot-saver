"""
The console's content-pack card: export → review → import → inventory → undo.

The console never invents a pack: it forwards the file's own bytes to the supervisor,
renders what the review says, and posts back the parent's decisions. The supervisor
runs the REAL content verbs over a real `JsonStore` (`helpers_console_supervisor`), so
this proves the console's URL, body, status codes and normalizer against genuine
payloads. Every test starts and ends on a fresh content store (`content` fixture) — an
import leaking into the next test would make the review states meaningless.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console_supervisor import CONV, client, supervisor  # noqa: E402,F401


def _content_reset(supervisor):
    """Empty overlay, empty ledger, no undo slot — a fresh appliance."""
    rt = supervisor.runtime
    for c in (rt.CONTENT_ITEMS_COLLECTION, rt.CONTENT_PACKS_COLLECTION,
              rt.CONTENT_BACKUP_COLLECTION):
        rt.store.delete_shared(c)
    rt.reload_content()


@pytest.fixture()
def content(supervisor):
    _content_reset(supervisor)
    yield supervisor
    _content_reset(supervisor)


def _export(client, **edits):
    """Export the shipped chat, apply `edits` to its item's data, and — when
    `source_version` is given — re-sign it the way an author's re-export would."""
    r = client.get("/local/content/export",
                   params={"items": CONV, "name": "Bedtime", "id": "bedtime"})
    assert r.status_code == 200, r.text
    pack = r.json()
    version = edits.pop("source_version", None)
    pack["items"][0]["data"].update(edits)
    if version is not None:
        from moxie_sdk.content import packs as _packs
        pack["items"][0]["source_version"] = version
        pack["digest"] = _packs.pack_digest(pack)
    return pack


def _review(client, pack):
    r = client.post("/local/content/review", content=json.dumps(pack))
    assert r.status_code == 200, r.text
    return r.json()


def _import(client, pack, accept, expect_digest=None):
    body = {"pack": json.dumps(pack), "accept": accept}
    if expect_digest is not None:
        body["expect_digest"] = expect_digest
    return client.post("/local/content/import", json=body)


def _items(client):
    return {i["id"]: i for i in client.get("/local/content").json()["items"]}


def _prompt(supervisor):
    return supervisor.runtime.app.module.conversation("FREE_CHAT").prompt


def test_the_content_card_lists_what_is_installed(client, content):
    v = client.get("/local/content").json()
    assert v["ok"] is True
    assert {i["id"] for i in v["items"]} == {CONV, "global:Timer"}
    row = {i["id"]: i for i in v["items"]}[CONV]
    assert (row["origin"], row["source_version"], row["local_edited"]) == ("shipped", 1, False)
    assert row["name"] == "Free Chat" and row["kind"] == "conversation"
    assert v["packs"] == [] and v["undo_available"] is False
    assert v["counts"]["total"] == 2 and v["error"] is None


def test_the_export_download_carries_a_filename_and_the_pack_itself(client, content):
    r = client.get("/local/content/export", params={"items": CONV, "name": "Bedtime",
                                                    "id": "bedtime"})
    assert r.status_code == 200
    assert 'filename="bedtime.moxiepack.json"' in r.headers["content-disposition"]
    pack = r.json()
    assert pack["pack_format"] == 1 and pack["id"] == "bedtime"
    assert [i["key"] for i in pack["items"]] == ["FREE_CHAT/default"]


def test_exporting_something_that_is_not_installed_is_a_400_the_card_can_render(
        client, content):
    r = client.get("/local/content/export", params={"items": "conversation:NOPE/x"})
    assert r.status_code == 400
    assert r.json()["ok"] is False and "not installed" in r.json()["error"]


def test_export_review_import_round_trips_through_the_console(client, content, supervisor):
    pack = _export(client, prompt="A prompt somebody else wrote.", source_version=4)
    reviewed = _review(client, pack)
    assert reviewed["ok"] and reviewed["digest"] == "ok"
    row = reviewed["items"][0]
    assert (row["state"], row["decision"], row["installable"]) == ("upgrade", "accept", True)
    assert row["installed_version"] == 1 and row["source_version"] == 4
    assert [d["field"] for d in row["diff"]] == ["prompt"]
    assert reviewed["accept"] == [CONV]

    r = _import(client, pack, reviewed["accept"], reviewed["expect_digest"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["applied"] == [CONV] and out["count"] == 1
    assert out["pack"]["id"] == "bedtime" and out["undo_available"] is True

    v = client.get("/local/content").json()
    installed = {i["id"]: i for i in v["items"]}[CONV]
    assert (installed["origin"], installed["pack_id"]) == ("pack", "bedtime")
    assert installed["source_version"] == 4 and installed["local_edited"] is False
    assert [p["id"] for p in v["packs"]] == ["bedtime"]
    assert v["undo_available"] is True
    assert _prompt(supervisor) == "A prompt somebody else wrote."   # live module changed


def test_the_review_pre_selects_keep_mine_on_an_item_edited_here(client, content,
                                                                 supervisor):
    """The clobber guarantee: the safe choice is pre-selected, and Accept is still
    available for a parent who means it."""
    from moxie_sdk.content import packs as _packs
    rt = supervisor.runtime
    pack = _export(client, source_version=2)
    rt._write_content_overlay(_packs.mark_edited(
        {}, CONV, dict(rt.content_items()[CONV]["data"], prompt="I wrote this myself.")))
    rt.reload_content()

    row = _review(client, pack)["items"][0]
    assert row["state"] == "conflict"
    assert row["decision"] == "keep", "the un-destructive choice is pre-selected"
    assert row["default"] is False and row["local_edited"] is True
    assert row["installable"] is True

    # Keep mine → nothing is sent for it → nothing changes
    r = _import(client, pack, [])
    assert r.status_code == 200 and r.json()["applied"] == []
    assert rt.content_items()[CONV]["data"]["prompt"] == "I wrote this myself."


def test_a_pack_changed_after_it_was_exported_pre_selects_nothing(client, content):
    reviewed = _review(client, _export(client, prompt="edited after export, same digest"))
    assert reviewed["digest"] == "mismatch"
    assert reviewed["accept"] == []
    assert reviewed["items"][0]["decision"] == "skip"


def test_a_file_that_is_not_a_pack_is_a_400_the_card_can_explain(client, content):
    r = client.post("/local/content/review", content='{"hello": "world"}')
    assert r.status_code == 400
    assert r.json()["ok"] is False and "content pack" in r.json()["error"]
    assert r.json()["items"] == [], "an empty-but-renderable review"


def test_importing_a_different_file_than_the_one_reviewed_is_a_409(client, content):
    reviewed = _review(client, _export(client, source_version=3))
    other = _export(client, prompt="a different file entirely", source_version=3)
    r = _import(client, other, [CONV], reviewed["expect_digest"])
    assert r.status_code == 409, r.text
    assert r.json()["conflict"] is True
    assert client.get("/local/content").json()["items"][0]["origin"] == "shipped"


def test_undo_through_the_console_puts_the_content_back(client, content, supervisor):
    assert client.post("/local/content/undo").status_code == 404
    pack = _export(client, prompt="the imported prompt", source_version=2)
    reviewed = _review(client, pack)
    _import(client, pack, reviewed["accept"], reviewed["expect_digest"])
    assert _prompt(supervisor) == "the imported prompt"

    r = client.post("/local/content/undo")
    assert r.status_code == 200 and r.json()["ok"] is True
    v = client.get("/local/content").json()
    assert v["undo_available"] is False and v["packs"] == []
    assert {i["id"]: i for i in v["items"]}[CONV]["origin"] == "shipped"
    assert _prompt(supervisor) == "You are Moxie, talking to Sam."


def test_a_code_carrying_item_is_flagged_all_the_way_to_the_card(client, content):
    pack = _export(client, code="def complete_handler(v, s): s.summarize()",
                   source_version=2)
    row = _review(client, pack)["items"][0]
    assert any("never runs" in w for w in row["warnings"])
    _import(client, pack, [CONV])
    assert _items(client)[CONV]["has_code"] is True
