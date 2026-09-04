"""
The adaptive day-planner (mqtt/moxie_sdk/schedule.py) — audit §4.2 BEYOND #7.

`build_schedule` used to be a `(device_id, day)` rotation. It is now a scored recommender
that plans from history, parent preferences, the clock and the robot's config, and hands
back a parent-readable *why* for every entry. The wire is unchanged: still exactly the
recovered `ContentSchedule` (`recovered-proto/embodied/logging/Cloud.proto`:343) with
`Recommendation`-only entries (`RemoteChat.proto`:26-34) — `test_schedule.py` guards that.

Each test here isolates ONE scoring factor by flattening its neighbours (a catalog of two
modules in the same category, or a template with `chat_count: 0`), so a failure names the
factor that broke. The clock is always injected — no test reads the wall clock, and none
sleeps.
"""
import datetime
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.schedule import (  # noqa: E402
    AFFINITY_FLOOR, CATEGORY_ENERGY, DEFAULT_TEMPLATE, ONBOARD_MODULES, SLOT_MINUTES,
    TIME_FIT, bedtime_window, build_schedule, category_energy, clock_label, in_bedtime,
    module_history, module_label, parent_requests_due, plan, plan_day, plan_inputs,
    score_module, select_template, telemetry_signals, time_bucket, validate_schedule)

MORNING = datetime.datetime(2026, 9, 2, 8, 0)          # a Wednesday
AFTERNOON = datetime.datetime(2026, 9, 2, 14, 0)
EVENING = datetime.datetime(2026, 9, 2, 18, 30)
NIGHT = datetime.datetime(2026, 9, 2, 22, 0)


def _mbh(module_id, action="COMPLETED", when=None, **kw):
    """One MentorBehavior record, timestamped in ms like the robot stamps them."""
    when = when or datetime.datetime(2026, 8, 1, 12, 0)
    return {"module_id": module_id, "action": action,
            "timestamp": int(when.timestamp() * 1000), **kw}


def _gen(**over):
    """A template with only the generated rotation — no chats, no pinned spine, so the
    scored fill is the whole plan."""
    gen = {"chat_count": 0, "module_count": 3, "chat_modules": [], "extra_modules": [],
           "excluded_module_ids": []}
    gen.update(over.pop("generate", {}))
    tmpl = {"provided_schedule": [], "generate": gen}
    tmpl.update(over)
    return tmpl


def _ids(sched):
    return [r["module_id"] for r in sched["provided_schedule"]]


# ---------------------------------------------------------------- time of day ----

def test_time_buckets_partition_the_clock():
    assert time_bucket(MORNING) == "morning"
    assert time_bucket(AFTERNOON) == "afternoon"
    assert time_bucket(EVENING) == "evening"
    assert time_bucket(NIGHT) == "night"
    assert time_bucket(datetime.datetime(2026, 9, 2, 3, 0)) == "night"   # wraps midnight


def test_every_catalog_category_has_an_energy_class():
    """The time-of-day mapping is per `ModuleDetail.ModuleCategory` (ContentModule.proto
    :46-60), so every category the catalog actually uses must be classified."""
    used = {m["category"] for m in ONBOARD_MODULES}
    assert used <= set(CATEGORY_ENERGY), used - set(CATEGORY_ENERGY)
    for bucket in ("morning", "afternoon", "evening", "night"):
        assert set(TIME_FIT[bucket]) == {"energetic", "neutral", "calm"}


def test_energetic_activities_win_the_morning_and_calm_ones_win_the_evening():
    catalog = [{"module_id": "DANCE", "category": "MOVEMENT"},          # energetic
               {"module_id": "AUDMED", "category": "REGULATION"}]       # calm
    tmpl = _gen(generate={"module_count": 1})
    assert _ids(build_schedule(tmpl, device_id="d", now=MORNING,
                               catalog=catalog)) == ["DANCE"]
    assert _ids(build_schedule(tmpl, device_id="d", now=EVENING,
                               catalog=catalog)) == ["AUDMED"]
    assert _ids(build_schedule(tmpl, device_id="d", now=NIGHT,
                               catalog=catalog)) == ["AUDMED"]


def test_time_of_day_is_scored_per_slot_not_per_day():
    """Slots carry a clock time (`now + index * SLOT_MINUTES`), so a session that starts
    in the afternoon and runs into the evening plans calmer as it goes."""
    start = datetime.datetime(2026, 9, 2, 16, 50)
    inputs = plan_inputs("d", start, template=_gen(generate={"module_count": 6}))
    buckets = [s["bucket"] for s in inputs["slots"]]
    assert buckets[0] == "afternoon" and buckets[-1] == "evening"


# ------------------------------------------------------------------- coverage ----

def test_an_unseen_activity_outranks_one_the_child_has_already_had():
    catalog = [{"module_id": "A", "category": "FUN_TIDBIT"},
               {"module_id": "B", "category": "FUN_TIDBIT"}]
    sched = build_schedule(_gen(generate={"module_count": 1}), device_id="d",
                           now=AFTERNOON, catalog=catalog,
                           mentor_behaviors=[_mbh("A")])
    assert _ids(sched) == ["B"]


def test_coverage_outranks_affinity_so_nothing_repeats_while_fresh_ones_remain():
    """The PR #7 invariant survives the recommender: a module the child adores still
    waits its turn behind one they have never seen."""
    catalog = [{"module_id": "LOVED", "category": "LISTENING"},
               {"module_id": "NEW", "category": "LISTENING"}]
    history = [_mbh("LOVED") for _ in range(3)]
    sched = build_schedule(_gen(generate={"module_count": 1}), device_id="d",
                           now=EVENING, catalog=catalog, mentor_behaviors=history)
    assert _ids(sched) == ["NEW"]


# -------------------------------------------------------------------- recency ----

def test_yesterdays_activity_is_demoted_against_an_equally_seen_one():
    catalog = [{"module_id": "FRESH", "category": "FUN_TIDBIT"},
               {"module_id": "STALE", "category": "FUN_TIDBIT"}]
    history = [_mbh("FRESH", "PRESENTED", when=datetime.datetime(2026, 6, 1, 9, 0)),
               _mbh("STALE", "PRESENTED", when=datetime.datetime(2026, 9, 2, 7, 0))]
    sched = build_schedule(_gen(generate={"module_count": 1}), device_id="d",
                           now=AFTERNOON, catalog=catalog, mentor_behaviors=history)
    assert _ids(sched) == ["FRESH"]


def test_recency_grades_today_harder_than_this_week():
    inputs = plan_inputs("d", AFTERNOON, mentor_behaviors=[
        _mbh("A", "PRESENTED", when=datetime.datetime(2026, 9, 2, 7, 0)),     # today
        _mbh("B", "PRESENTED", when=datetime.datetime(2026, 9, 1, 7, 0)),     # yesterday
        _mbh("C", "PRESENTED", when=datetime.datetime(2026, 7, 1, 7, 0))])    # long ago
    got = {mid: score_module({"module_id": mid, "category": "FUN_TIDBIT"},
                             inputs=inputs, slot=0)[1]["recency"]
           for mid in ("A", "B", "C")}
    assert got["A"] < got["B"] < got["C"] == 0


# ------------------------------------------------------------------- affinity ----

def test_a_module_the_child_finishes_beats_one_they_walk_out_of():
    catalog = [{"module_id": "FINISHES", "category": "CREATIVITY"},
               {"module_id": "ABANDONS", "category": "CREATIVITY"}]
    history = ([_mbh("FINISHES") for _ in range(3)] +
               [_mbh("ABANDONS", "QUIT") for _ in range(3)])
    sched = build_schedule(_gen(generate={"module_count": 1}), device_id="d",
                           now=AFTERNOON, catalog=catalog, mentor_behaviors=history)
    assert _ids(sched) == ["FINISHES"]


def test_a_repeatedly_abandoned_module_is_floored_never_zeroed():
    """Demote, do not delete — a child who quits Dance ten times still gets offered it
    again once the rest of the catalog has had its turn. Variety over a losing streak."""
    inputs = plan_inputs("d", AFTERNOON,
                         mentor_behaviors=[_mbh("DANCE", "QUIT") for _ in range(10)])
    _, factors, codes = score_module({"module_id": "DANCE", "category": "MOVEMENT"},
                                     inputs=inputs, slot=0)
    assert factors["affinity"] == AFFINITY_FLOOR > 0
    assert "abandons" in codes
    # and it is still reachable: alone in the catalog, it is still scheduled.
    sched = build_schedule(_gen(generate={"module_count": 1}), device_id="d",
                           now=AFTERNOON, mentor_behaviors=[_mbh("DANCE", "QUIT")] * 10,
                           catalog=[{"module_id": "DANCE", "category": "MOVEMENT"}])
    assert _ids(sched) == ["DANCE"]


def test_module_history_separates_finished_from_abandoned_from_merely_offered():
    hist = module_history([_mbh("X"), _mbh("X", "QUIT"), _mbh("X", "REFUSED"),
                           _mbh("X", "PRESENTED"), {"junk": 1}, "nope"])
    assert hist["X"]["seen"] == 4
    assert hist["X"]["completed"] == 1 and hist["X"]["abandoned"] == 2


# -------------------------------------------------------------------- variety ----

def test_two_activities_of_the_same_category_never_sit_next_to_each_other():
    cat = {m["module_id"]: m["category"] for m in ONBOARD_MODULES}
    for now in (MORNING, AFTERNOON, EVENING, NIGHT):
        ids = [m for m in _ids(build_schedule(device_id="d_v", now=now)) if m in cat]
        assert all(cat[a] != cat[b] for a, b in zip(ids, ids[1:])), (now, ids)


def test_the_day_spreads_across_categories_rather_than_stacking_one():
    """Time-of-day fit tilts the day; it must not collapse it onto one category (the
    goal OpenMoxie's `ransac_select` samples for — see ATTRIBUTION.md)."""
    cat = {m["module_id"]: m["category"] for m in ONBOARD_MODULES}
    ids = [m for m in _ids(build_schedule(device_id="d_s", now=MORNING)) if m in cat]
    assert len({cat[m] for m in ids}) >= 3, ids


# ------------------------------------------------------------------- FTUE ----

def test_ftue_placement_is_exactly_what_it_was_before_the_recommender():
    ids = _ids(build_schedule(device_id="d_new", now=MORNING, day="2026-09-02"))
    assert ids[:4] == ["WELCOME", "TNT", "SYSTEMSCHECK", "DM"]
    done = ([_mbh("TNT") for _ in range(9)] + [_mbh("SYSTEMSCHECK") for _ in range(4)])
    after = _ids(build_schedule(device_id="d_new", now=MORNING, mentor_behaviors=done))
    assert not ({"WELCOME", "TNT", "SYSTEMSCHECK"} & set(after)) and "DM" in after


def test_unfinished_onboarding_outranks_every_other_signal_in_the_score():
    inputs = plan_inputs("d", NIGHT)
    ftue, _, codes = score_module({"module_id": "TNT", "category": "MOVEMENT"},
                                  inputs=inputs, slot=0)
    other, _, _ = score_module({"module_id": "AUDMED", "category": "REGULATION"},
                               inputs=inputs, slot=0)
    assert "ftue" in codes and ftue > other


# ------------------------------------------------------------ parent requests ----

def test_a_parent_request_lands_in_the_slot_nearest_the_time_they_asked_for():
    now = datetime.datetime(2026, 9, 2, 15, 0)
    at_four = datetime.datetime(2026, 9, 2, 16, 0)
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY", "scheduled_at": int(at_four.timestamp())}]}}
    sched, expl, inputs = plan("d_req", now=now, effective_config=cfg, child_name="Sam")
    pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
    assert [e["module_id"] for e in pinned] == ["STORY"]
    assert pinned[0]["at"] == "16:00"
    assert "4:00 pm" in pinned[0]["line"]
    assert inputs["parent_requests"][0]["due_today"] is True


def test_a_parent_request_outscores_every_other_candidate():
    now = datetime.datetime(2026, 9, 2, 15, 0)
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "AUDMED", "scheduled_at": int(now.timestamp())}]}}
    sched, expl, _ = plan("d_req2", now=now, effective_config=cfg,
                          template=_gen(generate={"module_count": 3}))
    assert _ids(sched)[0] == "AUDMED"            # a calm module, in the afternoon, first
    assert expl[0]["factors"]["parent_request"] > 0


def test_a_request_for_another_day_is_not_scheduled_today():
    tomorrow = datetime.datetime(2026, 9, 3, 16, 0)
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY", "scheduled_at": int(tomorrow.timestamp())}]}}
    reqs = parent_requests_due(cfg, datetime.datetime(2026, 9, 2, 15, 0), slot_count=6)
    assert reqs[0]["due_today"] is False and reqs[0]["slot"] is None


def test_the_requested_module_is_held_for_its_slot_not_eaten_by_an_earlier_one():
    """The scored fill must not spend the parent's module before its own slot arrives.

    The isolated factor is the reservation itself. STORY is the only unseen module in this
    catalog — the other three have been played nine times each, so `coverage` sinks them —
    which means the free choice would take STORY first on score alone. It was asked for two
    slots out. Without the hold, slot 0 takes it, slot 2 finds it gone, and the pin
    evaporates *silently*: a day carrying the requested activity at the wrong time whose
    audit trail never mentions that a parent asked for anything."""
    now = datetime.datetime(2026, 9, 2, 15, 0)
    catalog = [{"module_id": "STORY", "category": "READING"},
               {"module_id": "AUDMED", "category": "REGULATION"},
               {"module_id": "JUKEBOX", "category": "MOVEMENT"},
               {"module_id": "JOKE", "category": "FUN_TIDBIT"}]
    played = [_mbh(m, when=now - datetime.timedelta(days=i + 1))
              for m in ("AUDMED", "JUKEBOX", "JOKE") for i in range(9)]
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY",
         "scheduled_at": int((now + datetime.timedelta(
             minutes=2 * SLOT_MINUTES)).timestamp())}]}}
    _, expl, inputs = plan("d_hold", now=now, effective_config=cfg, catalog=catalog,
                           mentor_behaviors=played,
                           template=_gen(generate={"module_count": 3}))
    assert inputs["parent_requests"][0]["slot"] == 2, inputs["parent_requests"]
    pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
    assert [e["module_id"] for e in pinned] == ["STORY"], [
        (e["module_id"], e["at"], e["reason_codes"]) for e in expl]
    assert pinned[0]["at"] == "15:20", pinned      # the slot they asked for, not slot 0


def test_a_held_module_is_released_when_there_is_nothing_else_left_to_plan():
    """The hold is a preference between candidates, never a reason to ship a shorter day.

    With one module in the catalog and a request two slots out there is no way to both
    honour the pin and fill slot 0; the day still gets planned rather than truncated."""
    now = datetime.datetime(2026, 9, 2, 15, 0)
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY",
         "scheduled_at": int((now + datetime.timedelta(
             minutes=2 * SLOT_MINUTES)).timestamp())}]}}
    sched, expl, _ = plan("d_release", now=now, effective_config=cfg,
                          catalog=[{"module_id": "STORY", "category": "READING"}],
                          template=_gen(generate={"module_count": 3}))
    assert _ids(sched) == ["STORY"] and validate_schedule(sched) == []
    assert [e["at"] for e in expl] == ["15:00"]


def test_a_parent_request_survives_every_hour_the_planner_could_run_at():
    """The regression this guard exists to hold shut. Which module tops the board in the
    first scored slot is decided by `time_of_day` and settled by the `(device_id, day,
    module_id)` tiebreak, so before the hold a parent request could be eaten by an earlier
    slot in the *afternoon* and survive the morning — green on a developer's machine in
    PDT, red on a CI runner sitting in UTC, on three unrelated PRs at once.

    So sweep it rather than claim it: every hour of a fixed day, across several device ids
    (the tiebreak is per device, and it is what decides the race). The clock is injected,
    so the sweep means the same thing at every hour it is itself run at.

    The scenario is `test_schedule_sil_e2e`'s: FTUE finished, so the pinned spine is one
    entry and the request resolves to a *scored* slot rather than slot 0; bedtime an hour
    out; one activity asked for two slots ahead. Both real branches are covered from fixed
    instants — in the last 20 minutes of a day that request belongs to tomorrow, and the
    contract there is that it is NOT pinned into today."""
    day = datetime.datetime(2026, 9, 2)
    seeded = ([_mbh("WELCOME")] + [_mbh("TNT", content_id=f"tnt{i}") for i in range(9)]
              + [_mbh("SYSTEMSCHECK") for _ in range(4)])
    strict = crossed = 0
    for device_id in ("d_sil-schedule", "d_req", "d1", "d2"):
        for hour in range(24):
            for minute in (0, 17, 30, 43, 51):
                now = day.replace(hour=hour, minute=minute)
                start = now + datetime.timedelta(minutes=60)
                cfg = {"weekday_bedtime": [start.strftime("%H:%M"),
                                           (start + datetime.timedelta(
                                               hours=8)).strftime("%H:%M")],
                       "schedule_preferences": {"parent_requests": [
                           {"module_id": "STORYTELLING",
                            "scheduled_at": int((now + datetime.timedelta(
                                minutes=2 * SLOT_MINUTES)).timestamp())}]}}
                _, expl, inputs = plan(device_id, now=now, effective_config=cfg,
                                       mentor_behaviors=seeded)
                req = inputs["parent_requests"][0]
                pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
                where = (device_id, now.isoformat(), inputs["bucket"], req,
                         [(e["module_id"], e["at"], e["reason_codes"]) for e in expl])
                if (now + datetime.timedelta(
                        minutes=2 * SLOT_MINUTES)).date() != now.date():
                    crossed += 1
                    assert req["due_today"] is False and req["slot"] is None, where
                    assert pinned == [], where
                    assert expl and all(e["line"] for e in expl), where
                    continue
                strict += 1
                assert req["due_today"] is True and req["slot"] is not None, where
                assert [e["module_id"] for e in pinned] == ["STORYTELLING"], where
                assert pinned[0]["at"] == req["at"], where
    assert (strict, crossed) == (472, 8), (strict, crossed)   # both branches, really run


def test_two_requests_never_collide_on_one_slot():
    now = datetime.datetime(2026, 9, 2, 15, 0)
    when = int(datetime.datetime(2026, 9, 2, 15, 2).timestamp())
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY", "scheduled_at": when},
        {"module_id": "JOKE", "scheduled_at": when + 60}]}}
    slots = [r["slot"] for r in parent_requests_due(cfg, now, slot_count=6)]
    assert len(set(slots)) == 2 and None not in slots


# ------------------------------------------------------------------- bedtime ----

def test_bedtime_is_read_from_the_effective_config_for_the_right_kind_of_day():
    cfg = {"weekday_bedtime": ["20:00", "07:00"], "weekend_bedtime": ["21:30", "08:00"]}
    assert bedtime_window(cfg, MORNING)["starts_at"] == "20:00"        # a Wednesday
    saturday = datetime.datetime(2026, 9, 5, 10, 0)
    assert bedtime_window(cfg, saturday)["starts_at"] == "21:30"
    assert bedtime_window({}, MORNING) == {"enabled": False, "kind": "weekday"}


def test_a_bedtime_window_that_wraps_midnight_is_understood():
    w = {"enabled": True, "starts_at": "20:00", "ends_at": "07:00"}
    assert in_bedtime(datetime.datetime(2026, 9, 2, 21, 0), w)
    assert in_bedtime(datetime.datetime(2026, 9, 3, 2, 0), w)
    assert not in_bedtime(datetime.datetime(2026, 9, 2, 19, 0), w)


def test_nothing_is_ever_planned_into_bedtime():
    cfg = {"weekday_bedtime": ["20:00", "07:00"]}
    late = datetime.datetime(2026, 9, 2, 19, 35)
    sched, expl, inputs = plan("d_bed", now=late, effective_config=cfg)
    def _at(entry):
        h, m = (int(x) for x in entry["at"].split(":"))
        return datetime.datetime.combine(late.date(), datetime.time(h, m))

    timed = [e for e in expl if e["at"]]
    assert all(not in_bedtime(_at(e), inputs["bedtime"]) for e in timed)
    assert inputs["planned"]["dropped_for_bedtime"] > 0
    assert validate_schedule(sched) == []          # still a servable day, never empty


def test_a_parent_request_inside_bedtime_is_pulled_back_before_it():
    cfg = {"weekday_bedtime": ["20:00", "07:00"], "schedule_preferences":
           {"parent_requests": [{"module_id": "STORY", "scheduled_at": int(
               datetime.datetime(2026, 9, 2, 21, 0).timestamp())}]}}
    now = datetime.datetime(2026, 9, 2, 19, 0)
    _, expl, _ = plan("d_bedreq", now=now, effective_config=cfg)
    pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
    assert pinned and pinned[0]["at"] < "20:00", pinned


# --------------------------------------------------------------- determinism ----

def test_the_same_inputs_produce_byte_identical_plans():
    kw = dict(device_id="d_det", now=EVENING, mentor_behaviors=[_mbh("JOKE")])
    a = json.dumps(build_schedule(**kw), sort_keys=True)
    b = json.dumps(build_schedule(**kw), sort_keys=True)
    assert a == b


def test_the_plan_is_stable_across_python_hash_seeds():
    """`blake2b`, not `hash()` — a plan must not depend on the interpreter's salt, or two
    supervisor processes would serve the same robot two different days."""
    script = (
        "import datetime, json, sys;"
        f"sys.path.insert(0, {os.path.join(REPO, 'mqtt')!r});"
        "from moxie_sdk.schedule import build_schedule;"
        "print(json.dumps(build_schedule(device_id='d_seed',"
        " now=datetime.datetime(2026, 9, 2, 18, 30)), sort_keys=True))")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", script], env=env, check=True,
                                capture_output=True, text=True).stdout.strip())
    assert len(outs) == 1, outs


def test_the_plan_still_varies_by_robot_and_by_day():
    a = _ids(build_schedule(device_id="d_1", now=EVENING, day="2026-09-02"))
    assert a != _ids(build_schedule(device_id="d_2", now=EVENING, day="2026-09-02"))
    assert a != _ids(build_schedule(device_id="d_1", now=EVENING, day="2026-09-09"))


def test_plan_day_is_pure_and_replayable_from_its_inputs_alone():
    inputs = plan_inputs("d_pure", EVENING, mentor_behaviors=[_mbh("STORY")])
    first, e1 = plan_day(inputs)
    second, e2 = plan_day(json.loads(json.dumps(inputs)))    # round-trips through JSON
    assert first == second and e1 == e2


# -------------------------------------------------------------- explanations ----

def test_every_entry_on_the_plan_has_a_parent_readable_why():
    sched, expl, _ = plan("d_expl", now=EVENING, child_name="Sam",
                          mentor_behaviors=[_mbh("STORY"), _mbh("STORY"),
                                            _mbh("DANCE", "QUIT")])
    assert [e["module_id"] for e in expl] == _ids(sched)      # 1:1, same order
    for e in expl:
        line = e["line"]
        assert line and line[0].isupper() and line.endswith(".")
        assert len(line.split()) >= 5
        assert "_" not in line.replace("FREE_CHAT", "")       # no reason-code jargon
        assert e["reason_codes"]


def test_the_explanation_names_the_child_and_what_they_did():
    history = [_mbh("STORY") for _ in range(3)]
    catalog = [{"module_id": "STORY", "category": "LISTENING"}]
    _, expl, _ = plan("d_name", now=EVENING, child_name="Sam", catalog=catalog,
                      mentor_behaviors=history,
                      template=_gen(generate={"module_count": 1}))
    line = expl[0]["line"]
    assert line.startswith("Sam finished Story 3 times"), line


def test_explanations_never_reach_the_wire():
    sched, expl, _ = plan("d_wire", now=EVENING)
    assert validate_schedule(sched) == []
    blob = json.dumps(sched)
    assert "reason_codes" not in blob and "line" not in blob and "factors" not in blob


def test_a_request_the_session_cannot_reach_says_so_instead_of_pretending():
    """A session that starts at 7 am cannot run a 4 pm request at 4 pm. The activity is
    still honored (a parent asked), but the line never claims a time it did not get."""
    cfg = {"schedule_preferences": {"parent_requests": [
        {"module_id": "DRAW", "scheduled_at": int(
            datetime.datetime(2026, 9, 2, 16, 0).timestamp())}]}}
    _, expl, _ = plan("d_drift", now=MORNING, effective_config=cfg, child_name="Sam")
    line = [e["line"] for e in expl if "parent_request" in e["reason_codes"]][0]
    assert "4:00 pm" in line and "queued at" in line and "instead" in line


def test_a_lowercase_nickname_still_starts_the_sentence_properly():
    """`ChildProfile.nickname` defaults to "friend" — the parent must not read
    "friend has not tried ..."."""
    _, expl, _ = plan("d_lower", now=EVENING, child_name="friend")
    assert all(e["line"][0].isupper() for e in expl), [e["line"] for e in expl]


def test_module_labels_are_english_where_we_know_them_and_the_id_where_we_do_not():
    assert module_label({"module_id": "AUDMED"}) == "Guided meditation"
    assert module_label({"module_id": "RDL"}) == "RDL"          # never invented
    named = {"module_id": "X", "module_name": "Bedtime Story"}
    assert module_label(named) == "Bedtime Story"
    assert clock_label("16:00") == "4:00 pm" and clock_label("00:30") == "12:30 am"


# ---------------------------------------------------------------- telemetry ----

def test_telemetry_is_reported_as_context_and_never_as_a_module_signal():
    """Honest by construction: the recovered `Packet` has a free-string `event_name` and
    opaque `event_data`, so nothing in it says which module a child finished."""
    packets = [{"event_name": "wake", "recorded_at": int(MORNING.timestamp()),
                "moxie_session_id": "s1"},
               {"event_name": "said", "recorded_at": int(EVENING.timestamp()),
                "moxie_session_id": "s1"}]
    sig = telemetry_signals({"count": 2, "by_event": {"wake": 1, "said": 1}}, packets)
    assert sig["carries_module_signal"] is False
    assert sig["active_buckets"] == {"morning": 1, "evening": 1}
    assert sig["sessions"] == 1 and "mentor_behaviors" in sig["note"]


def test_the_planner_survives_junk_telemetry():
    sched = build_schedule(device_id="d_junk", now=EVENING,
                           telemetry_summary={"latest": ["nope", {"recorded_at": "x"}]},
                           telemetry_packets=[None, 7])
    assert validate_schedule(sched) == []


# ------------------------------------------------------- schedules[] selection ----

def test_a_content_module_can_ship_a_wind_down_day_named_after_the_bucket():
    entries = [
        {"name": "default", "schedule": {"provided_schedule": [{"module_id": "DM"}]}},
        {"name": "evening", "schedule": {"provided_schedule": [{"module_id": "AUDMED"}]}}]
    assert select_template(entries, bucket="evening")["provided_schedule"] == \
        [{"module_id": "AUDMED"}]
    assert select_template(entries, bucket="morning")["provided_schedule"] == \
        [{"module_id": "DM"}]
    assert select_template([], bucket="evening") == DEFAULT_TEMPLATE


def test_plan_inputs_is_json_safe_so_it_can_be_shown_to_a_parent():
    inputs = plan_inputs("d_json", EVENING, mentor_behaviors=[_mbh("JOKE")],
                         effective_config={"weekday_bedtime": ["20:00", "07:00"]})
    assert json.loads(json.dumps(inputs))["bedtime"]["starts_at"] == "20:00"
    assert inputs["slot_minutes"] == SLOT_MINUTES
    assert category_energy("REGULATION") == "calm"
