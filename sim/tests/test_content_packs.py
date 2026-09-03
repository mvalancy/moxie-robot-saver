"""
📦 Content packs — the pure engine (`moxie_sdk/content/packs.py`).

Tests 1-9 of `docs/architecture/backlog/content-packs.md` §3. Everything here is stdlib
and hermetic: no store, no HTTP, no gateway, no clock (`now=` is injected everywhere), so
the whole file runs in the fast-tier venv with nothing installed but pytest.

The three that carry the design, and would be worth writing even if the others were
dropped:

* `test_the_allowlist_is_pinned_to_the_dataclass_fields` — risk R6. A field added to
  `Conversation` fails HERE, before it starts shipping in everybody's packs.
* `test_nothing_private_leaves_in_an_exported_pack` — the §2.2 guarantee, asserted against
  the serialized bytes with sentinels for every record a pack must never carry.
* `test_re_importing_after_a_local_edit_never_clobbers_it` — the clobber test (§3 row 5),
  which is the whole reason our review is a 2×2 and upstream's is two integers.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import packs as P           # noqa: E402
from moxie_sdk.content.module import (Conversation, Global,  # noqa: E402
                                      Schedule, load_modules)

NOW = 1788400000            # a fixed instant, so every assertion here is reproducible


# --------------------------------------------------------------------------- #
# Fixtures — three items, one of each kind, with something interesting in each
# --------------------------------------------------------------------------- #

def conv(prompt="You are Moxie. Be kind.", version=1, **kw) -> dict:
    data = {"name": "Free Chat", "module_id": "FREE_CHAT", "content_id": "default",
            "prompt": prompt, "opener": "Hi!", "max_tokens": 120, "temperature": 0.7,
            "memory": {"namespace": "free_chat", "summarize": True}}
    data.update(kw)
    return {"kind": "conversation", "key": "FREE_CHAT/default",
            "source_version": version, "data": data}


def glob(pattern=r"timer for (\d+) (minute|second)", version=1, **kw) -> dict:
    data = {"name": "Timer", "pattern": pattern, "entity_groups": "1,2", "action": 0}
    data.update(kw)
    return {"kind": "global", "key": "Timer", "source_version": version, "data": data}


def sched(version=1, modules=("WELCOME", "DM"), **kw) -> dict:
    data = {"name": "wind_down",
            "schedule": {"provided_schedule": [{"module_id": m} for m in modules]}}
    data.update(kw)
    return {"kind": "schedule", "key": "wind_down", "source_version": version,
            "data": data}


def make_pack(items=None, **kw) -> dict:
    kw.setdefault("name", "Bedtime wind-down")
    kw.setdefault("pack_id", "bedtime-wind-down")
    kw.setdefault("now", NOW)
    return P.export_pack(items if items is not None else [conv(), glob(), sched()], **kw)


def install(pack, accept=None, installed=None, now=NOW + 10):
    accept = accept if accept is not None else [
        P.full_key(i["kind"], i["key"]) for i in pack["items"]]
    items, _summary = P.apply_pack(pack, installed or {}, accept, now=now)
    return items


# --------------------------------------------------------------------------- #
# 1 · Round trip
# --------------------------------------------------------------------------- #

def test_export_serialize_parse_is_an_identity_on_the_item_set():
    pack = make_pack()
    back, meta = P.parse_pack(P.dumps_pack(pack))
    assert meta["digest"] == "ok"
    assert back == pack, "a pack must survive its own serializer unchanged"
    assert [i["key"] for i in back["items"]] == ["FREE_CHAT/default", "Timer", "wind_down"]


def test_the_round_trip_is_byte_stable():
    """Same content, same bytes — twice, and after a full parse in between."""
    first = P.dumps_pack(make_pack())
    again = P.dumps_pack(make_pack())
    assert first == again
    parsed, _ = P.parse_pack(first)
    assert P.dumps_pack(P.export_pack(parsed["items"], name=parsed["name"],
                                      pack_id=parsed["id"], now=NOW)) == first


def test_the_canonical_form_ignores_key_order_and_whitespace():
    pack = make_pack()
    shuffled = json.loads(json.dumps(pack))
    reordered = {k: shuffled[k] for k in reversed(list(shuffled))}
    pretty = json.dumps(reordered, indent=4)
    _back, meta = P.parse_pack(pretty)
    assert meta["digest"] == "ok", "pretty-printing must not disturb the digest"


def test_json_round_trips_our_field_types_losslessly():
    """Assumption A6: a float `temperature`, an int `action`, a dict `memory`."""
    pack = make_pack([conv(temperature=0.35, max_tokens=77), glob(action=3)])
    back, _ = P.parse_pack(P.dumps_pack(pack))
    c = back["items"][0]["data"]
    assert isinstance(c["temperature"], float) and c["temperature"] == 0.35
    assert isinstance(c["max_tokens"], int) and c["max_tokens"] == 77
    assert c["memory"] == {"namespace": "free_chat", "summarize": True}
    assert back["items"][1]["data"]["action"] == 3


def test_an_exported_pack_loads_as_a_content_module():
    """The end of the round trip that matters: the items become a live module."""
    pack = make_pack()
    module = load_modules(P.module_data(install(pack)))
    assert module.conversation("FREE_CHAT", "default").prompt.startswith("You are Moxie")
    assert module.match_global("set a timer for 5 minutes")[1] == ["5", "minute"]
    assert [s.name for s in module.schedules] == ["wind_down"]


def test_a_clean_appliance_that_imports_a_pack_re_exports_the_same_file():
    """**The circle closed** — acceptance criterion 2, proved in both directions.

    Every other round-trip test above checks one leg: a pack survives its serializer, or
    installed items become a module. Neither would catch a field that the *store* drops
    on the way in, because the exporter and the parser would still agree with each other
    about the shape they never saw. This one runs the whole loop —

        export → serialize → parse → apply into an empty appliance → export again

    — and demands the same bytes. A field lost in `apply_pack`'s provenance stamping, a
    `source_version` that reverted to the default, a float coerced to an int: each shows
    up here as a byte difference and nowhere else."""
    original = P.dumps_pack(make_pack())
    parsed, meta = P.parse_pack(original)
    assert meta["digest"] == "ok"

    clean_appliance = {}
    installed, _summary = P.apply_pack(
        parsed, clean_appliance,
        [P.full_key(i["kind"], i["key"]) for i in parsed["items"]], now=NOW + 10)

    again = P.dumps_pack(P.export_pack(installed, name=parsed["name"],
                                       pack_id=parsed["id"], now=NOW))
    assert again == original, "an imported pack no longer exports as the file it came from"


def test_the_circle_still_closes_when_the_pack_upgrades_something_installed():
    """The same proof over the path that *replaces* rather than adds, because that is
    the path with a previous version's provenance already in the store to confuse it."""
    v1 = make_pack([conv(prompt="Version one.", version=1)])
    installed = install(v1)
    v2 = P.export_pack([conv(prompt="Version two.", version=2)],
                       name="Bedtime wind-down", pack_id="bedtime-wind-down", now=NOW)

    upgraded, _ = P.apply_pack(v2, installed, [P.full_key("conversation",
                                                          "FREE_CHAT/default")],
                               now=NOW + 20)
    again = P.export_pack(upgraded, name="Bedtime wind-down",
                          pack_id="bedtime-wind-down", now=NOW)
    assert P.dumps_pack(again) == P.dumps_pack(v2)
    assert again["items"][0]["source_version"] == 2


# --------------------------------------------------------------------------- #
# 2 · Tamper detection
# --------------------------------------------------------------------------- #

def test_a_pack_edited_after_export_is_reported_as_mismatched():
    raw = P.dumps_pack(make_pack())
    tampered = raw.replace("Be kind.", "Be cruel.")
    assert tampered != raw
    _pack, meta = P.parse_pack(tampered)
    assert meta["digest"] == "mismatch"
    assert meta["computed"] != meta["claimed"]


def test_a_tampered_pack_default_ticks_nothing():
    """Acceptance criterion 5: nothing is pre-selected when the file was changed."""
    pack, meta = P.parse_pack(P.dumps_pack(make_pack()).replace("Be kind.", "Be cruel."))
    rows = P.review_pack(pack, {}, digest=meta["digest"])
    assert [r["state"] for r in rows] == [P.NEW, P.NEW, P.NEW]
    assert not any(r["default"] for r in rows), "a changed file pre-selects nothing"
    # …and the same pack, untouched, DOES tick its new items.
    clean, clean_meta = P.parse_pack(P.dumps_pack(make_pack()))
    assert all(r["default"] for r in P.review_pack(clean, {}, digest=clean_meta["digest"]))


def test_a_pack_with_no_digest_parses_and_is_flagged_not_refused():
    """A hand-written pack is a legitimate thing — flagged, never rejected."""
    body = json.loads(P.dumps_pack(make_pack()))
    body.pop("digest")
    pack, meta = P.parse_pack(json.dumps(body))
    assert meta["digest"] == "absent"
    assert len(pack["items"]) == 3
    assert not any(r["default"] for r in P.review_pack(pack, {}, digest=meta["digest"]))


def test_the_digest_covers_every_field_not_just_the_items():
    body = json.loads(P.dumps_pack(make_pack()))
    body["author"] = "somebody else"
    _pack, meta = P.parse_pack(json.dumps(body))
    assert meta["digest"] == "mismatch"


def test_the_reserved_signatures_field_is_outside_the_digest():
    """So a detached signature can be added later without invalidating old packs."""
    body = json.loads(P.dumps_pack(make_pack()))
    body["signatures"] = [{"alg": "ed25519", "sig": "…"}]
    _pack, meta = P.parse_pack(json.dumps(body))
    assert meta["digest"] == "ok"


# --------------------------------------------------------------------------- #
# 3 · Format guard — a readable refusal, never a traceback
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body, needle", [
    ('{"pack_format": 2, "items": []}', "pack_format"),
    ('{"items": []}', "not written as a content pack"),
    ('{"pack_format": 1}', "no `items`"),
    ('{"pack_format": 1, "items": {}}', "must be a list"),
    ('{"pack_format": 1, "items": [3]}', "expected an object"),
    ('{"pack_format": 1, "items": [{"kind": "spell", "data": {}}]}', "unknown kind"),
    ('{"pack_format": 1, "items": [{"kind": "conversation", "data": 7}]}', "must be an object"),
    ('{"pack_format": 1, "items": [{"kind": "conversation",'
     ' "data": {"max_tokens": "lots"}}]}', "integer"),
    ('not json at all', "not valid JSON"),
    ('[1, 2, 3]', "must be a JSON object"),
    ('"a string"', "must be a JSON object"),
])
def test_a_pack_this_appliance_cannot_read_is_refused_readably(body, needle):
    with pytest.raises(P.PackError) as e:
        P.parse_pack(body)
    assert needle in str(e.value), str(e.value)


def test_two_items_with_the_same_key_are_refused():
    body = {"pack_format": 1, "items": [conv(), conv(prompt="different")]}
    with pytest.raises(P.PackError) as e:
        P.parse_pack(json.dumps(body))
    assert "appears twice" in str(e.value)


def test_unknown_fields_are_dropped_and_named_never_stored():
    body = {"pack_format": 1, "items": [
        {"kind": "conversation", "key": "FREE_CHAT/default",
         "data": {"module_id": "FREE_CHAT", "content_id": "default",
                  "prompt": "hi", "exec_on_load": "rm -rf /", "id": 12}}]}
    pack, meta = P.parse_pack(json.dumps(body))
    assert "exec_on_load" in meta["warnings"][0] and "id" in meta["warnings"][0]
    assert "exec_on_load" not in pack["items"][0]["data"]
    assert set(pack["items"][0]["data"]) == set(P.FIELDS["conversation"])


def test_non_utf8_bytes_are_refused_readably():
    with pytest.raises(P.PackError) as e:
        P.parse_pack(b"\xff\xfe{")
    assert "UTF-8" in str(e.value)


# --------------------------------------------------------------------------- #
# 4 · The review matrix — one case per cell of §2.3
# --------------------------------------------------------------------------- #

def edit(items, ident="conversation:FREE_CHAT/default", prompt="I edited this myself."):
    data = dict(items[ident]["data"], prompt=prompt)
    return P.mark_edited(items, ident, data)


def row_for(pack, installed, ident="conversation:FREE_CHAT/default"):
    return {r["id"]: r for r in P.review_pack(pack, installed)}[ident]


def test_review_new_item_is_ticked():
    r = row_for(make_pack(), {})
    assert (r["state"], r["default"], r["local_edited"]) == (P.NEW, True, False)
    assert r["installed_version"] is None


def test_review_upgrade_is_ticked_when_nothing_was_edited_here():
    installed = install(make_pack([conv(version=2)]))
    r = row_for(make_pack([conv(prompt="Now warmer.", version=3)]), installed)
    assert (r["state"], r["default"]) == (P.UPGRADE, True)
    assert (r["installed_version"], r["source_version"]) == (2, 3)
    assert "v2 → v3" in r["label"]


def test_review_upgrade_over_a_local_edit_is_a_conflict_and_is_not_ticked():
    installed = edit(install(make_pack([conv(version=2)])))
    r = row_for(make_pack([conv(prompt="Now warmer.", version=3)]), installed)
    assert (r["state"], r["default"], r["local_edited"]) == (P.CONFLICT, False, True)
    assert "replaces the changes you made" in r["label"]


def test_review_same_version_same_content_is_a_no_op():
    installed = install(make_pack())
    r = row_for(make_pack(), installed)
    assert (r["state"], r["default"], r["diff"]) == (P.SAME, False, [])


def test_review_same_version_after_a_local_edit_is_keep_local():
    installed = edit(install(make_pack()))
    r = row_for(make_pack(), installed)
    assert (r["state"], r["default"], r["local_edited"]) == (P.KEEP_LOCAL, False, True)
    assert r["diff"], "a parent must be able to see what would come back"


def test_review_same_version_different_content_is_a_fork():
    """Assumption A1 failing safe: an author who never bumps is not a silent no-op."""
    installed = install(make_pack([conv(version=2)]))
    r = row_for(make_pack([conv(prompt="Quietly different.", version=2)]), installed)
    assert (r["state"], r["default"]) == (P.FORK, False)
    assert "different content" in r["label"]


def test_review_fork_over_a_local_edit_is_still_a_fork():
    installed = edit(install(make_pack([conv(version=2)])))
    r = row_for(make_pack([conv(prompt="Quietly different.", version=2)]), installed)
    assert (r["state"], r["default"], r["local_edited"]) == (P.FORK, False, True)


def test_review_an_older_pack_is_a_downgrade_and_is_not_ticked():
    installed = install(make_pack([conv(version=3)]))
    r = row_for(make_pack([conv(prompt="the old one", version=1)]), installed)
    assert (r["state"], r["default"]) == (P.DOWNGRADE, False)
    assert "v1 < v3" in r["label"]


def test_review_an_older_pack_over_a_local_edit_says_both():
    installed = edit(install(make_pack([conv(version=3)])))
    r = row_for(make_pack([conv(prompt="the old one", version=1)]), installed)
    assert (r["state"], r["default"]) == (P.DOWNGRADE_CONFLICT, False)
    assert "changes you made" in r["label"]


def test_only_new_and_clean_upgrades_are_ever_ticked_by_default():
    assert set(P.DEFAULT_ACCEPT) == {P.NEW, P.UPGRADE}


def test_an_item_with_no_provenance_at_all_counts_as_edited():
    """We cannot attribute it, so we take the cautious half of the matrix."""
    installed = {"conversation:FREE_CHAT/default": {"kind": "conversation",
                                                    "data": conv()["data"]}}
    assert P.is_local_edited(installed["conversation:FREE_CHAT/default"]) is True
    assert row_for(make_pack(), installed)["state"] == P.KEEP_LOCAL


def test_the_review_writes_nothing():
    installed = install(make_pack())
    before = json.dumps(installed, sort_keys=True)
    P.review_pack(make_pack([conv(prompt="new", version=9)]), installed)
    assert json.dumps(installed, sort_keys=True) == before


def test_the_diff_is_field_level_and_shows_the_whole_prompt():
    installed = install(make_pack([conv(prompt="one\ntwo\nthree")]))
    r = row_for(make_pack([conv(prompt="one\nTWO\nthree", version=2)]), installed)
    fields = {d["field"]: d for d in r["diff"]}
    assert set(fields) == {"prompt"}
    assert fields["prompt"]["kind"] == "text"
    assert any(line.startswith("-two") for line in fields["prompt"]["diff"])
    assert any(line.startswith("+TWO") for line in fields["prompt"]["diff"])
    assert fields["prompt"]["new"] == "one\nTWO\nthree"


def test_a_scalar_change_diffs_as_old_arrow_new():
    installed = install(make_pack([conv(max_tokens=120)]))
    r = row_for(make_pack([conv(max_tokens=400, version=2)]), installed)
    row = {d["field"]: d for d in r["diff"]}["max_tokens"]
    assert (row["kind"], row["old"], row["new"]) == ("scalar", "120", "400")


def test_diff_item_against_nothing_lists_every_field():
    rows = P.diff_item(None, conv()["data"])
    assert {r["field"] for r in rows} >= {"prompt", "module_id", "opener"}


# --------------------------------------------------------------------------- #
# 5 · The clobber test — §3 row 5, the requirement the 2×2 exists for
# --------------------------------------------------------------------------- #

def test_re_importing_after_a_local_edit_never_clobbers_it():
    v1 = make_pack([conv(prompt="the shipped prompt", version=1)])
    installed = install(v1)
    mine = edit(installed, prompt="MY prompt, which I wrote.")

    # (a) the SAME pack again: KEEP LOCAL, un-ticked, and nothing changes
    r = row_for(v1, mine)
    assert (r["state"], r["default"]) == (P.KEEP_LOCAL, False)
    after, summary = P.apply_pack(v1, mine, [], now=NOW)
    assert after == mine and summary["applied"] == []

    # (b) a NEWER pack: CONFLICT, un-ticked
    v2 = make_pack([conv(prompt="the improved prompt", version=2)])
    r2 = row_for(v2, mine)
    assert (r2["state"], r2["default"]) == (P.CONFLICT, False)

    # (c) tick it anyway → applied
    ident = "conversation:FREE_CHAT/default"
    forced, summary = P.apply_pack(v2, mine, [ident], now=NOW + 99)
    assert forced[ident]["data"]["prompt"] == "the improved prompt"
    assert summary["replaced"] == [ident]

    # (d) undo → the edited version back, byte for byte
    assert P.canonical(mine) == P.canonical(json.loads(json.dumps(mine)))
    restored = json.loads(json.dumps(mine))       # the one-slot snapshot the runtime keeps
    assert P.canonical(restored) == P.canonical(mine)
    assert restored[ident]["data"]["prompt"] == "MY prompt, which I wrote."


def test_the_shipped_defaults_are_upgraded_by_the_same_rule_as_a_stranger_s_pack():
    """Upstream's `init_data.py` idea, taken as behaviour: our own content only replaces
    an installed item when the shipped version is newer AND nothing was edited here."""
    shipped_v1 = P.shipped_items({"conversations": [
        dict(conv()["data"], source_version=1)]})
    assert shipped_v1["conversation:FREE_CHAT/default"]["provenance"]["origin"] == "shipped"
    release = make_pack([conv(prompt="the 0.8 starter chat", version=2)])
    assert row_for(release, shipped_v1)["state"] == P.UPGRADE
    parent_edited = edit(shipped_v1, prompt="I made Moxie call him Bear.")
    assert row_for(release, parent_edited)["state"] == P.CONFLICT


# --------------------------------------------------------------------------- #
# 6 · Selection by key
# --------------------------------------------------------------------------- #

def test_accepting_a_key_that_is_not_in_the_pack_is_an_error():
    with pytest.raises(P.PackError) as e:
        P.apply_pack(make_pack(), {}, ["conversation:NOPE/default"], now=NOW)
    assert "not in this pack" in str(e.value)


def test_an_index_shaped_accept_is_rejected():
    """Upstream selects by array index against a re-posted pack; we do not."""
    for bad in (0, 1, True):
        with pytest.raises(P.PackError) as e:
            P.apply_pack(make_pack(), {}, [bad], now=NOW)
        assert "not by index" in str(e.value)


def test_applying_the_same_import_twice_is_idempotent():
    pack = make_pack()
    once = install(pack)
    twice, summary = P.apply_pack(pack, once,
                                  [P.full_key(i["kind"], i["key"]) for i in pack["items"]],
                                  now=NOW + 10)
    assert twice == once
    assert summary["replaced"] == sorted(twice)


def test_only_the_accepted_items_are_applied():
    pack = make_pack()
    items, summary = P.apply_pack(pack, {}, ["global:Timer"], now=NOW)
    assert list(items) == ["global:Timer"]
    assert summary["applied"] == ["global:Timer"]
    assert summary["skipped"] == ["conversation:FREE_CHAT/default", "schedule:wind_down"]
    assert summary["count"] == 1


def test_apply_never_mutates_the_mapping_it_was_given():
    pack = make_pack()
    installed = install(make_pack([conv(prompt="before")]))
    before = json.dumps(installed, sort_keys=True)
    P.apply_pack(pack, installed, ["conversation:FREE_CHAT/default"], now=NOW)
    assert json.dumps(installed, sort_keys=True) == before


def test_provenance_is_stamped_on_every_applied_item():
    pack = make_pack([conv(version=4)])
    items = install(pack, now=NOW + 5)
    prov = items["conversation:FREE_CHAT/default"]["provenance"]
    assert prov["origin"] == "pack"
    assert prov["pack_id"] == "bedtime-wind-down"
    assert prov["source_version"] == 4
    assert prov["imported_at"] == NOW + 5
    assert prov["imported_rev"] == P.digest_of(
        items["conversation:FREE_CHAT/default"]["data"])
    assert P.is_local_edited(items["conversation:FREE_CHAT/default"]) is False


# --------------------------------------------------------------------------- #
# 7 · Nothing private leaves — the §2.2 guarantee
# --------------------------------------------------------------------------- #

def test_the_allowlist_is_pinned_to_the_dataclass_fields():
    """Risk R6. A new field on a content dataclass fails HERE, before it ships in packs."""
    for kind in P.KINDS:
        assert P.FIELDS[kind] == P.dataclass_fields(kind), (
            f"{kind}: the pack allowlist and {P.DATACLASS[kind].__name__} have drifted. "
            f"Decide whether the new field should travel in packs, then update FIELDS.")
    assert "_rx" not in P.FIELDS["global"], "derived state must never travel"
    assert all("source_version" not in P.FIELDS[k] for k in P.KINDS), \
        "source_version is the ITEM's field, not content — that is what makes FORK work"


def test_the_allowlist_defaults_match_the_dataclass_defaults():
    """So a missing field normalizes to exactly what the loader would have produced."""
    for kind, spec in P.SPEC.items():
        blank = P.DATACLASS[kind]()
        for name, _coerce, default in spec:
            assert getattr(blank, name) == default, f"{kind}.{name} default drifted"


def test_nothing_private_leaves_in_an_exported_pack():
    """Seed every record a pack must never carry, export everything exportable, and
    assert the SERIALIZED BYTES contain none of the sentinels (acceptance criterion 6)."""
    sentinels = {
        "child_pii": "Ada-Nickname-Sentinel",
        "pronouns": "they/them-SENTINEL",
        "birthday": "2018-04-01-SENTINEL",
        "memory": "Ada has a beagle named Pepper-SENTINEL",
        "telemetry": "battery_low-SENTINEL",
        "safety_event": "sfe-SENTINEL-0001",
        "telehealth": "the therapist typed this-SENTINEL",
        "device_id": "d_SENTINEL-0000-1111",
        "permit": "permitted_at-SENTINEL",
        "config_override": "weekday_bedtime-SENTINEL",
        # Assembled rather than written out: the repo's own staged-diff secret guard
        # greps for `sk-` + 12 word characters, and a test fixture must not trip it.
        "credential": "sk-" + "SENTINELCREDENTIAL0",
        "endpoint": "https://gateway.SENTINEL.example/v1",
    }
    # Every one of these lives in a record a pack has no field for; they are handed to the
    # exporter anyway, as extra keys on the very items being exported.
    dirty = []
    for item in (conv(), glob(), sched()):
        item = json.loads(json.dumps(item))
        item["data"].update({f"leak_{k}": v for k, v in sentinels.items()})
        item["data"]["child_pii"] = {"nickname": sentinels["child_pii"]}
        item["data"]["_rx"] = "compiled-SENTINEL"
        dirty.append(item)
    raw = P.dumps_pack(P.export_pack(dirty, name="Everything", pack_id="everything",
                                     now=NOW))
    for label, sentinel in sentinels.items():
        assert sentinel not in raw, f"{label} leaked into an exported pack"
    assert "compiled-SENTINEL" not in raw
    # …and what DID travel is exactly the allowlist.
    pack = json.loads(raw)
    for item in pack["items"]:
        assert set(item["data"]) == set(P.FIELDS[item["kind"]])
        assert set(item) == {"kind", "key", "source_version", "data"}


def test_a_memory_block_travels_but_remembered_data_does_not():
    """`memory` names a namespace — it is content. What Moxie remembered lives in
    `robots/<id>/memory.json`, which no pack can reach."""
    raw = P.dumps_pack(make_pack([conv()]))
    assert '"namespace": "free_chat"' in raw or '"namespace":"free_chat"' in raw
    assert "beagle" not in raw


def test_scan_outgoing_flags_a_name_the_appliance_knows():
    items = [conv(prompt="You are talking to Ada, who is six."), glob()]
    hits = P.scan_outgoing(items, ["Ada", "Sam"])
    assert hits == [{"kind": "conversation", "key": "FREE_CHAT/default",
                     "field": "prompt", "name": "Ada"}]
    assert P.scan_outgoing(items, []) == []
    # a substring is not a hit — "Ada" must not fire on "Adamant"
    assert P.scan_outgoing([conv(prompt="Be adamant.")], ["Ada"]) == []


# --------------------------------------------------------------------------- #
# 8 · `code` stays inert
# --------------------------------------------------------------------------- #

def test_a_pack_carrying_code_imports_with_a_warning_and_the_string_is_kept():
    pack = make_pack([conv(code="def complete_handler(v, s):\n    s.summarize()\n")])
    row = row_for(pack, {})
    assert row["state"] == P.NEW
    assert any("never runs" in w for w in row["warnings"])
    items = install(pack)
    stored = items["conversation:FREE_CHAT/default"]["data"]["code"]
    assert "complete_handler" in stored, "kept for a future sandboxed runtime (BEYOND #6)"


def test_the_inventory_marks_an_item_that_carries_code():
    items = install(make_pack([conv(code="print('hi')"), glob()]))
    rows = {r["id"]: r for r in P.inventory(items)}
    assert rows["conversation:FREE_CHAT/default"]["has_code"] is True
    assert rows["global:Timer"]["has_code"] is False


# --------------------------------------------------------------------------- #
# 9 · Hostile input
# --------------------------------------------------------------------------- #

def test_a_pattern_that_does_not_compile_is_refused_at_review_with_the_item_named():
    """Never at `load_module` time: `Global.from_dict` compiles at load, and a pack that
    throws inside the loader would take the reload down."""
    pack = make_pack([glob(pattern="timer for (")])
    row = row_for(pack, {}, "global:Timer")
    assert row["state"] == P.INVALID
    assert row["default"] is False
    assert any("does not compile" in r for r in row["reasons"])
    with pytest.raises(P.PackError) as e:
        P.apply_pack(pack, {}, ["global:Timer"], now=NOW)
    assert "global:Timer" in str(e.value)


def test_a_pattern_longer_than_the_cap_is_refused():
    pack = make_pack([glob(pattern="a" * (P.MAX_PATTERN_CHARS + 1))])
    row = row_for(pack, {}, "global:Timer")
    assert row["state"] == P.INVALID
    assert "the limit is" in row["reasons"][0]


def test_one_invalid_item_does_not_spoil_the_rest_of_the_pack():
    pack = make_pack([conv(), glob(pattern="(")])
    rows = {r["id"]: r for r in P.review_pack(pack, {})}
    assert rows["conversation:FREE_CHAT/default"]["state"] == P.NEW
    assert rows["global:Timer"]["state"] == P.INVALID
    items, _ = P.apply_pack(pack, {}, ["conversation:FREE_CHAT/default"], now=NOW)
    assert list(items) == ["conversation:FREE_CHAT/default"]


def test_an_item_with_no_identity_cannot_be_installed():
    pack = {"pack_format": 1, "id": "x", "items": [
        {"kind": "global", "key": "", "source_version": 1, "data": {"pattern": "hi"}}]}
    parsed, _ = P.parse_pack(json.dumps(pack))
    assert P.review_pack(parsed, {})[0]["state"] == P.INVALID


def test_a_negative_source_version_is_refused():
    pack = {"pack_format": 1, "id": "x", "items": [
        dict(conv(), source_version=-2)]}
    parsed, _ = P.parse_pack(json.dumps(pack))
    row = P.review_pack(parsed, {})[0]
    assert row["state"] == P.INVALID and "source_version" in row["reasons"][0]


def test_a_schedule_naming_a_module_the_firmware_may_not_have_warns():
    """Brief §7: no physical robot has ever been served a pack-authored schedule."""
    row = row_for(make_pack([sched(modules=("WELCOME", "NOT_A_REAL_MODULE"))]), {},
                  "schedule:wind_down")
    assert any("NOT_A_REAL_MODULE" in w for w in row["warnings"])
    assert not any("WELCOME" in w for w in row["warnings"]), "the spine is always known"
    assert row["state"] == P.NEW, "a warning, not a refusal — it is unobserved, not wrong"


def test_a_known_onboard_module_does_not_warn():
    row = row_for(make_pack([sched(modules=("JOKE",))]), {}, "schedule:wind_down")
    assert not row["warnings"]


# --------------------------------------------------------------------------- #
# The overlay: shipped defaults ⊕ installed items
# --------------------------------------------------------------------------- #

def test_the_overlay_wins_over_the_shipped_default_by_key():
    shipped = P.shipped_items({"conversations": [conv(prompt="the shipped one")["data"]],
                               "globals": [glob()["data"]]})
    overlay = install(make_pack([conv(prompt="the imported one", version=2)]))
    merged = P.merge_items(shipped, overlay)
    assert merged["conversation:FREE_CHAT/default"]["data"]["prompt"] == "the imported one"
    assert merged["global:Timer"]["data"]["pattern"] == glob()["data"]["pattern"]
    module = load_modules(P.module_data(merged))
    assert module.conversation("FREE_CHAT").prompt == "the imported one"


def test_with_no_overlay_the_shipped_defaults_are_what_loads():
    """Acceptance criterion 9: a fresh appliance behaves exactly as it does today."""
    raw = json.load(open(os.path.join(REPO, "mqtt", "content_modules", "starter.json")))
    shipped = P.shipped_items(raw)
    plain = load_modules(raw)
    merged = load_modules(P.module_data(P.merge_items(shipped, {})))
    assert [c.prompt for c in merged.conversations] == [c.prompt for c in plain.conversations]
    assert [g.pattern for g in merged.globals] == [g.pattern for g in plain.globals]


def test_shipped_records_carry_their_own_source_version():
    shipped = P.shipped_items({"conversations": [
        dict(conv()["data"], source_version=7)]})
    entry = shipped["conversation:FREE_CHAT/default"]
    assert entry["provenance"]["source_version"] == 7
    assert P.is_local_edited(entry) is False


def test_items_from_module_round_trips_a_loaded_module():
    """The export path when nothing recorded the shipped baseline."""
    module = load_modules(P.module_data(install(make_pack())))
    items = P.items_from_module(module)
    assert set(items) == {"conversation:FREE_CHAT/default", "global:Timer",
                          "schedule:wind_down"}
    assert items["conversation:FREE_CHAT/default"]["data"]["prompt"] == conv()["data"]["prompt"]
    # merging a module that already has the overlay in it is idempotent
    assert P.module_data(P.merge_items(items, install(make_pack()))) == P.module_data(items)


def test_module_data_orders_by_kind_and_key_so_a_reload_is_stable():
    items = install(make_pack([glob(), conv(), sched()]))
    once = P.module_data(items)
    shuffled = {k: items[k] for k in reversed(list(items))}
    assert P.module_data(shuffled) == once


def test_the_overlay_never_deletes():
    """P0 has no remove-item operation; an import only adds or replaces."""
    shipped = P.shipped_items({"globals": [glob()["data"]]})
    merged = P.merge_items(shipped, install(make_pack([conv()])))
    assert "global:Timer" in merged


def test_module_data_keeps_source_version_so_a_reload_does_not_lose_it():
    items = install(make_pack([conv(version=5)]))
    record = P.module_data(items)["conversations"][0]
    assert record["source_version"] == 5
    assert load_modules(P.module_data(items)).conversations[0].source_version == 5


# --------------------------------------------------------------------------- #
# Housekeeping: ids, ledger rows, inventory
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, want", [
    ("Bedtime Wind-Down!", "bedtime-wind-down"),
    ("  ../../etc/passwd ", "etc-passwd"),
    ("", "pack"),
    ("A" * 200, "a" * 64),
])
def test_a_pack_id_is_a_safe_slug(raw, want):
    assert P.sanitize_pack_id(raw) == want


def test_the_ledger_row_is_what_the_card_lists():
    pack = make_pack()
    row = P.pack_summary(pack, imported_at=NOW)
    assert row == {"id": "bedtime-wind-down", "name": "Bedtime wind-down", "details": "",
                   "author": "", "pack_version": 1, "digest": pack["digest"],
                   "imported_at": NOW, "item_count": 3}


def test_the_inventory_shows_provenance_and_the_edited_badge():
    items = edit(install(make_pack()))
    rows = {r["id"]: r for r in P.inventory(items)}
    assert rows["conversation:FREE_CHAT/default"]["local_edited"] is True
    assert rows["global:Timer"]["local_edited"] is False
    assert rows["global:Timer"]["origin"] == "pack"
    assert rows["global:Timer"]["pack_id"] == "bedtime-wind-down"
    assert rows["schedule:wind_down"]["name"] == "wind_down"


def test_item_keys_are_upstream_s_keys():
    """Assumption A5 — the reason a pack can interoperate at all."""
    assert P.item_key("conversation", {"module_id": "M", "content_id": "c"}) == "M/c"
    assert P.item_key("global", {"name": "Timer"}) == "Timer"
    assert P.item_key("schedule", {"name": "day"}) == "day"
    assert P.split_key("conversation:FREE_CHAT/default") == ("conversation",
                                                             "FREE_CHAT/default")


def test_the_dataclasses_still_load_a_module_written_before_packs_existed():
    """No `source_version` anywhere — the file every deployment already has on disk."""
    module = load_modules({"conversations": [{"module_id": "FREE_CHAT", "prompt": "hi"}],
                           "globals": [{"name": "Stop", "pattern": "stop"}],
                           "schedules": [{"name": "day", "schedule": {}}]})
    assert module.conversations[0].source_version == 1
    assert module.globals[0].source_version == 1
    assert module.schedules[0].source_version == 1
    assert dataclasses.fields(Conversation)[-1].name != "_rx"
    assert {f.name for f in dataclasses.fields(Global)} >= {"source_version", "_rx"}
    assert Schedule.from_dict({"name": "x"}).source_version == 1
