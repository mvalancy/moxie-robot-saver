"""
📅 Today's plan — the console's read of the recommender's "why this activity today".

`normalize_schedule_view` is the whole of `server/`'s new logic: the route in `main.py` is
a thin proxy of the supervisor's `GET /schedule?device_id=…` (the same shape the 🎨 look
and 🎭 Be Moxie cards use), and everything the card renders is decided here.

`RECORDED` below is **not hand-written**: it is a real `GET /schedule` body, captured on
2026-09-02 from a real mosquitto + `mqtt/run.py` + `sim/virtual_moxie.py --query schedule`
on free ports, with a fleet bedtime an hour out and one `ParentRequest`. So this file
tests against the payload the runtime actually emits — an FTUE spine with no clock times,
a parent request that drifted to a later slot, a scored pick, chat breaks, a bedtime that
truncated the day, and `telemetry.carries_module_signal: false`.

Pure — no fastapi, no network — so it runs in CI's hermetic env. The seam itself (URL,
query string, status codes) is covered in `test_console_roundtrip.py`.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from moxie_server.fleet import (  # noqa: E402
    normalize_schedule_entry, normalize_schedule_view,
)

#: A real `GET /schedule?device_id=…` body (see the module docstring).
RECORDED = {'day': '2026-09-02',
 'device_id': 'd_schedcard',
 'explanations': [{'at': None,
                   'factors': {},
                   'line': "Welcome is part of Moxie's first-week onboarding, which is "
                           'still running.',
                   'module_id': 'WELCOME',
                   'reason_codes': ['ftue'],
                   'score': None,
                   'slot': None},
                  {'at': None,
                   'factors': {},
                   'line': "TNT is part of Moxie's first-week onboarding, which is "
                           'still running.',
                   'module_id': 'TNT',
                   'reason_codes': ['ftue'],
                   'score': None,
                   'slot': None},
                  {'at': None,
                   'factors': {},
                   'line': "Systems Check is part of Moxie's first-week onboarding, "
                           'which is still running.',
                   'module_id': 'SYSTEMSCHECK',
                   'reason_codes': ['ftue'],
                   'score': None,
                   'slot': None},
                  {'at': None,
                   'factors': {},
                   'line': 'Daily Missions is a daily fixture — it runs every day.',
                   'module_id': 'DM',
                   'reason_codes': ['fixture'],
                   'score': None,
                   'slot': None},
                  {'at': '09:03',
                   'factors': {'affinity': 100,
                               'category_spread': 0,
                               'coverage': 0,
                               'parent_request': 4000,
                               'recency': 0,
                               'tiebreak': 4,
                               'time_of_day': 60},
                   'line': 'Requested by a parent for 8:43 am — this session starts '
                           'later than that, so Storytelling is queued at 9:03 am '
                           'instead.',
                   'module_id': 'STORYTELLING',
                   'reason_codes': ['parent_request', 'unseen'],
                   'score': 4164,
                   'slot': 4},
                  {'at': None,
                   'factors': {},
                   'line': 'A free chat, so friend gets a breather between activities.',
                   'module_id': 'FREE_CHAT',
                   'reason_codes': ['chat'],
                   'score': None,
                   'slot': None},
                  {'at': '09:13',
                   'factors': {'affinity': 100,
                               'category_spread': 0,
                               'coverage': 0,
                               'recency': 0,
                               'tiebreak': 22,
                               'time_of_day': 120},
                   'line': 'Friend has not tried Scavenger hunt yet — new for today in '
                           'the morning slot.',
                   'module_id': 'SCAVENGERHUNT',
                   'reason_codes': ['unseen', 'time_of_day', 'variety'],
                   'score': 242,
                   'slot': 5},
                  {'at': None,
                   'factors': {},
                   'line': 'A free chat, so friend gets a breather between activities.',
                   'module_id': 'FREE_CHAT',
                   'reason_codes': ['chat'],
                   'score': None,
                   'slot': None}],
 'inputs': {'bedtime': {'enabled': True,
                        'ends_at': '17:23',
                        'kind': 'weekday',
                        'starts_at': '09:23'},
            'bucket': 'morning',
            'child_name': 'friend',
            'day': '2026-09-02',
            'device_id': 'd_schedcard',
            'ftue_skips': [],
            'history': {},
            'now': '2026-09-02T08:23:20',
            'parent_requests': [{'at': '08:43',
                                 'due_today': True,
                                 'module_id': 'STORYTELLING',
                                 'scheduled_at': 1788363799,
                                 'slot': 0}],
            'planned': {'activities': 2,
                        'dropped_for_bedtime': 4,
                        'entries': 8,
                        'requested': 6},
            'slot_minutes': 10,
            'slots': [{'at': '09:03',
                       'bucket': 'morning',
                       'in_bedtime': False,
                       'index': 0},
                      {'at': '09:13',
                       'bucket': 'morning',
                       'in_bedtime': False,
                       'index': 1},
                      {'at': '09:23',
                       'bucket': 'morning',
                       'in_bedtime': True,
                       'index': 2},
                      {'at': '09:33',
                       'bucket': 'morning',
                       'in_bedtime': True,
                       'index': 3},
                      {'at': '09:43',
                       'bucket': 'morning',
                       'in_bedtime': True,
                       'index': 4},
                      {'at': '09:53',
                       'bucket': 'morning',
                       'in_bedtime': True,
                       'index': 5}],
            'telemetry': {'active_buckets': {},
                          'by_event': {},
                          'carries_module_signal': False,
                          'count': 0,
                          'note': 'Packet.event_name is a free string in the recovered '
                                  'proto; no module launch/exit vocabulary is '
                                  'established, so completion affinity comes from '
                                  'mentor_behaviors only.',
                          'sessions': 0}},
 'ok': True,
 'planned_at': '2026-09-02T08:23:20',
 'schedule': {'chat_request': {'content_id': 'default', 'module_id': 'FREE_CHAT'},
              'provided_schedule': [{'module_id': 'WELCOME'},
                                    {'module_id': 'TNT'},
                                    {'module_id': 'SYSTEMSCHECK'},
                                    {'module_id': 'DM'},
                                    {'module_id': 'STORYTELLING'},
                                    {'content_id': 'default', 'module_id': 'FREE_CHAT'},
                                    {'module_id': 'SCAVENGERHUNT'},
                                    {'content_id': 'default',
                                     'module_id': 'FREE_CHAT'}]},
 'served': True}


# --------------------------------------------------------------------------- #
# the recorded payload
# --------------------------------------------------------------------------- #

def test_recorded_payload_becomes_the_cards_shape():
    v = normalize_schedule_view(RECORDED)
    assert v["ok"] is True and v["error"] is None
    assert v["device_id"] == "d_schedcard" and v["day"] == "2026-09-02"
    assert v["child_name"] == "friend" and v["served"] is True
    assert set(v) == {"ok", "device_id", "day", "planned_at", "child_name", "served",
                      "entries", "constraints", "dropped_for_bedtime", "error"}
    assert set(v["constraints"]) == {"bedtime", "parent_request", "telemetry_signal"}


def test_every_served_entry_gets_exactly_one_explained_row():
    """The claim the card makes: one row per entry the ROBOT was served, in that order."""
    v = normalize_schedule_view(RECORDED)
    served = [e["module_id"] for e in RECORDED["schedule"]["provided_schedule"]]
    assert [r["module_id"] for r in v["entries"]] == served
    assert all(r["why"] for r in v["entries"]), "an entry with no reason is the whole bug"


def test_the_spine_has_no_clock_time_and_the_scored_fill_does():
    v = normalize_schedule_view(RECORDED)
    rows = {r["module_id"]: r for r in v["entries"]}
    # authored spine: onboarding, the DM daily fixture and the FREE_CHAT breathers
    for mid in ("WELCOME", "TNT", "SYSTEMSCHECK", "DM", "FREE_CHAT"):
        assert rows[mid]["time_local"] is None and rows[mid]["fixture"] is True
    # the scored fill is slotted onto the clock
    assert rows["STORYTELLING"]["time_local"] == "09:03"
    assert rows["SCAVENGERHUNT"]["time_local"] == "09:13"
    assert rows["SCAVENGERHUNT"]["fixture"] is False


def test_the_parent_request_is_the_only_pinned_row():
    v = normalize_schedule_view(RECORDED)
    assert [r["module_id"] for r in v["entries"] if r["pinned"]] == ["STORYTELLING"]
    pr = v["constraints"]["parent_request"]
    assert pr["count"] == 1 and pr["pinned"] == [{"module_id": "STORYTELLING",
                                                  "at": "08:43"}]
    # and the *why* line says so in words a parent reads, drift included
    why = next(r["why"] for r in v["entries"] if r["module_id"] == "STORYTELLING")
    assert "Requested by a parent" in why and "9:03 am" in why


def test_bedtime_is_carried_with_the_slots_it_cost():
    v = normalize_schedule_view(RECORDED)
    bed = v["constraints"]["bedtime"]
    assert bed == {"enabled": True, "kind": "weekday",
                   "starts_at": "09:23", "ends_at": "17:23"}
    assert v["dropped_for_bedtime"] == 4       # inputs.planned.dropped_for_bedtime


def test_telemetry_signal_is_reported_false_not_dropped():
    """`carries_module_signal` is the runtime saying finish/abandon comes from the robot's
    own reports. Losing it here would let the card imply a signal we do not have."""
    v = normalize_schedule_view(RECORDED)
    assert v["constraints"]["telemetry_signal"] is False
    louder = {**RECORDED,
              "inputs": {**RECORDED["inputs"],
                         "telemetry": {**RECORDED["inputs"]["telemetry"],
                                       "carries_module_signal": True}}}
    assert normalize_schedule_view(louder)["constraints"]["telemetry_signal"] is True


# --------------------------------------------------------------------------- #
# names: the wire's, or the id verbatim — never an invented one
# --------------------------------------------------------------------------- #

def test_module_name_from_the_wire_wins():
    payload = {"ok": True, "device_id": "d_x", "day": "2026-09-02", "served": True,
               "schedule": {"provided_schedule": [
                   {"module_id": "STORY", "module_name": "Story time"}]},
               "explanations": [{"module_id": "STORY", "slot": 0, "at": "10:00",
                                 "reason_codes": ["unseen"], "line": "New today."}]}
    row = normalize_schedule_view(payload)["entries"][0]
    assert row["name"] == "Story time" and row["module_id"] == "STORY"


def test_a_module_with_no_name_on_the_wire_shows_its_id():
    row = normalize_schedule_entry({"module_id": "RDL", "line": "why", "slot": 1,
                                    "at": "10:10", "reason_codes": []},
                                   {"module_id": "RDL"})
    assert row["name"] == "RDL", "an invented product name would be a lie about the robot"


def test_rows_are_joined_by_id_when_the_two_lists_fall_out_of_order():
    """The runtime's contract is same-order; a payload that broke it still renders the
    right reason against the right module rather than silently mislabelling one."""
    payload = {"ok": True, "device_id": "d_x", "served": True,
               "schedule": {"provided_schedule": [
                   {"module_id": "DANCE", "module_name": "Dance"},
                   {"module_id": "JOKE", "module_name": "Joke"}]},
               "explanations": [{"module_id": "JOKE", "line": "b", "slot": None},
                                {"module_id": "DANCE", "line": "a", "slot": None}]}
    rows = normalize_schedule_view(payload)["entries"]
    assert [(r["module_id"], r["name"], r["why"]) for r in rows] == [
        ("JOKE", "Joke", "b"), ("DANCE", "Dance", "a")]


def test_duplicate_ids_each_take_their_own_wire_entry():
    """FREE_CHAT appears twice in a real day; two rows must not collapse onto one name."""
    payload = {"ok": True, "device_id": "d_x", "served": True,
               "schedule": {"provided_schedule": [
                   {"module_id": "FREE_CHAT", "module_name": "Chat"},
                   {"module_id": "FREE_CHAT", "module_name": "Chat"}]},
               "explanations": [{"module_id": "FREE_CHAT", "line": "one", "slot": None},
                                {"module_id": "FREE_CHAT", "line": "two", "slot": None}]}
    rows = normalize_schedule_view(payload)["entries"]
    assert [r["why"] for r in rows] == ["one", "two"]
    assert all(r["name"] == "Chat" for r in rows)


# --------------------------------------------------------------------------- #
# the states the card has to render: empty, offline, unknown device, garbage
# --------------------------------------------------------------------------- #

def test_a_plan_with_no_entries_is_ok_and_empty_not_an_error():
    v = normalize_schedule_view({"ok": True, "device_id": "d_x", "day": "2026-09-02",
                                 "schedule": {}, "explanations": [], "inputs": {}})
    assert v["ok"] is True and v["entries"] == [] and v["error"] is None
    assert v["served"] is False          # the robot has not pulled its day yet


def test_supervisor_down_is_a_renderable_error_not_an_exception():
    for payload in (None, {}, "nope", 7, []):
        v = normalize_schedule_view(payload)
        assert v["ok"] is False and v["entries"] == [] and v["error"]
        assert v["constraints"]["bedtime"] == {"enabled": False, "kind": ""}


def test_unknown_device_keeps_the_runtimes_own_reason():
    v = normalize_schedule_view({"ok": False, "device_id": "d_nope",
                                 "error": "unknown device_id 'd_nope'"})
    assert v["ok"] is False and v["error"] == "unknown device_id 'd_nope'"
    assert v["device_id"] == "d_nope" and v["entries"] == []


def test_a_mistyped_payload_never_raises():
    """Every field the wrong type at once. A card must degrade, not 500."""
    v = normalize_schedule_view({"ok": True, "device_id": 5, "day": 20260902,
                                 "schedule": "not-a-dict", "explanations": "nope",
                                 "inputs": ["not", "a", "dict"], "served": "yes"})
    assert v["entries"] == [] and v["day"] == "20260902"
    assert v["constraints"]["parent_request"] == {"count": 0, "pinned": []}
    assert v["constraints"]["telemetry_signal"] is False
    assert v["dropped_for_bedtime"] == 0


def test_explanations_with_junk_members_still_produce_rows():
    v = normalize_schedule_view({"ok": True, "device_id": "d_x",
                                 "schedule": {"provided_schedule": [None, "x"]},
                                 "explanations": [None, {"module_id": "DM"}]})
    assert [r["module_id"] for r in v["entries"]] == ["", "DM"]
    assert all(r["why"] == "" for r in v["entries"])
    assert all(r["fixture"] is True for r in v["entries"])


def test_bedtime_that_is_not_set_says_so_without_inventing_a_window():
    v = normalize_schedule_view({"ok": True, "device_id": "d_x", "explanations": [],
                                 "inputs": {"bedtime": {"enabled": False,
                                                        "kind": "weekend"}}})
    assert v["constraints"]["bedtime"] == {"enabled": False, "kind": "weekend"}


def test_a_parent_request_that_is_not_due_today_is_not_pinned():
    v = normalize_schedule_view({"ok": True, "device_id": "d_x", "explanations": [],
                                 "inputs": {"parent_requests": [
                                     {"module_id": "STORY", "at": "08:00",
                                      "due_today": False, "slot": None},
                                     {"module_id": "DANCE", "at": "09:00",
                                      "due_today": True, "slot": None}]}})
    assert v["constraints"]["parent_request"] == {"count": 0, "pinned": []}
