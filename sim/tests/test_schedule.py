"""
Unit tests for the day-plan builder (mqtt/moxie_sdk/schedule.py) — audit ADOPT #1.

Shape is checked against the recovered protos:
  * `CloudQueryResponse.schedule` = field 6, an `embodied.robotbrain.ContentSchedule`
    (recovered-proto/embodied/logging/Cloud.proto:343)
  * `ContentSchedule.provided_schedule` = field 3, `Recommendation[]`
    (recovered-proto/embodied/robotbrain/ContentSchedule.proto)
  * `RecommendationContext.Recommendation{module_id, content_id, entry_line, module_name,
    module_description, seen, skip_hub}` (recovered-proto/.../RemoteChat.proto:26-34)

Pure: no MQTT, no store, no broker.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.schedule import (  # noqa: E402
    DEFAULT_TEMPLATE, ONBOARD_MODULES, RECOMMENDATION_FIELDS, SCHEDULE_FIELDS,
    build_schedule, completed_counts, ftue_skips, schedule_template, validate_schedule)


def _ids(sched):
    return [r["module_id"] for r in sched["provided_schedule"]]


def _mbh(module_id, action="COMPLETED", ts=1, **kw):
    return {"module_id": module_id, "action": action, "timestamp": ts, **kw}


# ---- shape ----

def test_default_plan_is_a_wellformed_content_schedule():
    sched = build_schedule(device_id="d_1", day="2026-09-02")
    assert validate_schedule(sched) == []
    assert set(sched) <= set(SCHEDULE_FIELDS)              # only proto fields on the wire
    assert len(sched["provided_schedule"]) >= 8            # onboarding + rotation + chats
    for rec in sched["provided_schedule"]:
        assert set(rec) <= set(RECOMMENDATION_FIELDS)
        assert rec["module_id"]


def test_authoring_keys_never_reach_the_wire():
    """`generate` is a server-side authoring block (content-module-contract.md
    §schedules[]) and is not a ContentSchedule field — it must be consumed, not sent."""
    sched = build_schedule({**DEFAULT_TEMPLATE, "notes": "for humans"},
                           device_id="d_1", day="2026-09-02")
    assert "generate" not in sched and "notes" not in sched
    assert validate_schedule(sched) == []


def test_non_recommendation_keys_are_stripped_from_entries():
    """The catalog carries a `category` for variety picking; `Recommendation` has no such
    field, so it must not go out (OpenMoxie's server does leak it — we strip)."""
    sched = build_schedule({"provided_schedule": [{"module_id": "DM", "category": "X"}]},
                           device_id="d_1", day="2026-09-02")
    assert sched["provided_schedule"] == [{"module_id": "DM"}]


def test_recommendation_typed_schedule_fields_are_normalized():
    sched = build_schedule({
        "provided_schedule": [{"module_id": "DM"}],
        "chat_request": {"module_id": "FREE_CHAT", "content_id": "default", "junk": 1},
        "wake_module": {"module_id": "WAKE"},
        "alarm_module": {"module_id": "ALARM", "content_id": "fire"},
        "hub_config": {"hubs": [{"module_id": "MOXIE_GO", "bogus": 2}],
                       "skipped_modules": ["JOKE"]},
        "end_of_session": {"chat_module": {"module_id": "FREE_CHAT"}, "chat_count": 1},
        "config": {"day_one_schedule": [{"module_id": "WELCOME", "category": "x"}]},
        "mission_config": {"mission_id": "m1"},
    }, device_id="d_1", day="2026-09-02")
    assert sched["chat_request"] == {"module_id": "FREE_CHAT", "content_id": "default"}
    assert sched["hub_config"] == {"hubs": [{"module_id": "MOXIE_GO"}],
                                   "skipped_modules": ["JOKE"]}
    assert sched["config"]["day_one_schedule"] == [{"module_id": "WELCOME"}]
    assert sched["end_of_session"]["chat_module"] == {"module_id": "FREE_CHAT"}
    assert sched["mission_config"] == {"mission_id": "m1"}       # passthrough, not a rec
    assert validate_schedule(sched) == []


def test_validate_schedule_catches_a_bad_plan():
    assert "provided_schedule is empty" in " ".join(validate_schedule({}))
    problems = validate_schedule({"provided_schedule": [{"category": "X"}], "nope": 1})
    assert any("unknown ContentSchedule field" in p for p in problems)
    assert any("no module_id" in p for p in problems)
    assert any("unknown field" in p for p in problems)


# ---- empty store (a brand-new robot) ----

def test_empty_history_keeps_the_full_ftue_onboarding():
    sched = build_schedule(mentor_behaviors=[], device_id="d_new", day="2026-09-02")
    ids = _ids(sched)
    assert ids[:4] == ["WELCOME", "TNT", "SYSTEMSCHECK", "DM"]      # onboarding first


def test_empty_history_and_no_generate_block_still_valid():
    sched = build_schedule({"provided_schedule": [{"module_id": "DM"}]},
                           mentor_behaviors=[], device_id="d_1", day="2026-09-02")
    assert _ids(sched) == ["DM"] and validate_schedule(sched) == []


# ---- skipping what's already done ----

def test_completed_counts_only_counts_completed():
    counts = completed_counts([_mbh("JOKE"), _mbh("JOKE"), _mbh("DRAW", "QUIT"),
                               _mbh("READ", "PRESENTED"), {"no": "module"}, "junk"])
    assert counts == {"JOKE": 2}


def test_ftue_modules_drop_out_once_completed_so_onboarding_ends():
    history = ([_mbh("TNT", ts=i) for i in range(9)] +
               [_mbh("SYSTEMSCHECK", ts=100 + i) for i in range(4)])
    assert ftue_skips(completed_counts(history)) == {"TNT", "SYSTEMSCHECK", "WELCOME"}
    ids = _ids(build_schedule(mentor_behaviors=history, device_id="d_1", day="2026-09-02"))
    assert "TNT" not in ids and "SYSTEMSCHECK" not in ids and "WELCOME" not in ids
    assert "DM" in ids                                   # the daily fixture stays


def test_welcome_retires_after_any_completion_but_tnt_needs_its_full_run():
    """TNT/SYSTEMSCHECK walk their content ids in order, so one completion is not done."""
    ids = _ids(build_schedule(mentor_behaviors=[_mbh("TNT")], device_id="d_1",
                              day="2026-09-02"))
    assert "WELCOME" not in ids and "TNT" in ids


def test_completed_activities_are_not_scheduled_again_while_fresh_ones_remain():
    """Nothing repeats until the catalog is exhausted — the point of ADOPT #2."""
    first = build_schedule(device_id="d_rot", day="2026-09-02")
    rotation = [m for m in _ids(first) if m in {x["module_id"] for x in ONBOARD_MODULES}]
    assert rotation, "expected generated on-board activities"
    history = [_mbh(m, ts=i) for i, m in enumerate(rotation)]
    second = _ids(build_schedule(mentor_behaviors=history, device_id="d_rot",
                                 day="2026-09-02"))
    assert not (set(rotation) & set(second)), f"{second} repeats {rotation}"


def test_an_exhausted_catalog_still_produces_a_day_rather_than_nothing():
    history = [_mbh(m["module_id"], ts=i) for i, m in enumerate(ONBOARD_MODULES)]
    sched = build_schedule(mentor_behaviors=history, device_id="d_1", day="2026-09-02")
    assert validate_schedule(sched) == []
    assert len(sched["provided_schedule"]) >= 6          # cycles back, never empty


def test_excluded_modules_are_honored():
    tmpl = {**DEFAULT_TEMPLATE,
            "generate": {**DEFAULT_TEMPLATE["generate"], "chat_count": 0,
                         "excluded_module_ids": [m["module_id"] for m in ONBOARD_MODULES
                                                 if m["module_id"] != "JOKE"]}}
    ids = _ids(build_schedule(tmpl, device_id="d_1", day="2026-09-02"))
    assert [m for m in ids if m in {x["module_id"] for x in ONBOARD_MODULES}] == ["JOKE"]


def test_extra_modules_can_add_a_server_side_activity():
    tmpl = {**DEFAULT_TEMPLATE,
            "generate": {"chat_count": 0, "module_count": 1, "chat_modules": [],
                         "extra_modules": [{"module_id": "MY_ACTIVITY",
                                            "content_id": "a", "category": "USER"}],
                         "excluded_module_ids": [m["module_id"] for m in ONBOARD_MODULES]}}
    sched = build_schedule(tmpl, device_id="d_1", day="2026-09-02")
    assert {"module_id": "MY_ACTIVITY", "content_id": "a"} in sched["provided_schedule"]


# ---- variety + chats ----

def test_chats_are_interleaved_between_activities_not_stacked_at_the_end():
    sched = build_schedule(device_id="d_1", day="2026-09-02")
    ids = _ids(sched)
    chat_at = [i for i, m in enumerate(ids) if m == "FREE_CHAT"]
    assert len(chat_at) == DEFAULT_TEMPLATE["generate"]["chat_count"]
    assert chat_at[-1] < len(ids) - 1                    # a chat never ends the day
    assert len(set(chat_at)) == len(chat_at)


def test_generated_activities_avoid_two_of_the_same_category_in_a_row():
    cat = {m["module_id"]: m["category"] for m in ONBOARD_MODULES}
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        ids = [m for m in _ids(build_schedule(device_id="d_1", day=day)) if m in cat]
        assert all(cat[a] != cat[b] for a, b in zip(ids, ids[1:])), ids


# ---- determinism ----

def test_the_same_inputs_always_produce_the_same_plan():
    a = build_schedule(device_id="d_1", day="2026-09-02")
    b = build_schedule(device_id="d_1", day="2026-09-02")
    assert a == b


def test_the_plan_varies_by_robot_and_by_day():
    base = _ids(build_schedule(device_id="d_1", day="2026-09-02"))
    assert base != _ids(build_schedule(device_id="d_2", day="2026-09-02"))
    assert base != _ids(build_schedule(device_id="d_1", day="2026-09-09"))


def test_build_schedule_never_mutates_its_template():
    tmpl = {"provided_schedule": [{"module_id": "DM"}],
            "generate": {"chat_count": 0, "module_count": 2}}
    before = repr(tmpl)
    build_schedule(tmpl, device_id="d_1", day="2026-09-02")
    assert repr(tmpl) == before                          # `generate` still there


# ---- content-module templates ----

def test_schedule_template_falls_back_to_the_default():
    assert schedule_template(None) == DEFAULT_TEMPLATE
    assert schedule_template(object()) == DEFAULT_TEMPLATE


def test_schedule_template_reads_a_content_modules_schedules_block():
    """The `schedules[]` section of a content module (content-module-contract.md) is the
    authoring surface for the day plan."""
    from moxie_sdk.content import load_modules
    module = load_modules({"schedules": [
        {"name": "quiet_day",
         "schedule": {"provided_schedule": [{"module_id": "AUDMED"}],
                      "chat_request": {"module_id": "FREE_CHAT"}}}]})
    tmpl = schedule_template(module)
    assert tmpl["provided_schedule"] == [{"module_id": "AUDMED"}]
    sched = build_schedule(tmpl, device_id="d_1", day="2026-09-02")
    assert _ids(sched) == ["AUDMED"] and validate_schedule(sched) == []


def test_schedule_template_picks_a_named_schedule():
    from moxie_sdk.content import load_modules
    module = load_modules({"schedules": [
        {"name": "a", "schedule": {"provided_schedule": [{"module_id": "A"}]}},
        {"name": "b", "schedule": {"provided_schedule": [{"module_id": "B"}]}}]})
    assert schedule_template(module, "b")["provided_schedule"] == [{"module_id": "B"}]
    assert schedule_template(module)["provided_schedule"] == [{"module_id": "A"}]
