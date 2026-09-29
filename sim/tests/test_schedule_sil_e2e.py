"""
End-to-end for the adaptive day plan: a SIL robot pulls its schedule off the wire and the
parent reads back WHY each entry is there — real runtime, real `CloudQuery` round trip,
real status HTTP. (`test_schedule_planner.py` has the recommender unit tests.)

    parent → POST /config?scope=fleet (bedtime + a ParentRequest)
    child  → stored `mentor_behaviors` (what was finished, what was quit)
    robot  → events/client-service-activity-log {subtopic:"query", query:"schedule"}
    server → commands/query_result  {query, request_id, schedule:{provided_schedule:[…]}}
    parent → GET /schedule?device_id=…  {explanations:[{module_id, at, line, …}]}

The binding claim: the ids the robot was served are exactly the ids the parent sees
explained. Hermetic (loopback broker, tmp store). Bedtime is wall-clock by contract, so
windows are relative to now and `served` reads the clock ONCE.
"""
import datetime
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

pytest.importorskip("paho.mqtt.client")            # the SIL client needs paho
from virtual_moxie import VirtualMoxie              # noqa: E402

from helpers_runtime import (http_json, loopback, make_runtime,  # noqa: E402
                             status_server)
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.schedule import SLOT_MINUTES         # noqa: E402
from moxie_sdk.store import JsonStore               # noqa: E402
from moxie_sdk.types import Reply                   # noqa: E402

DEV = "d_sil-schedule"


class _App(MoxieApp):
    name = "sil-schedule"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


# --------------------------------------------------------------------------- #
# The seeded child: FTUE finished, one loved module, one abandoned, one just played
# --------------------------------------------------------------------------- #
def _seed_behaviors(store, device_id=DEV, now_ms=None):
    """A history with a signal in every direction the planner reads, placed whole days (or an
    hour) before `now_ms`; recency is scored as an age, so it means the same at any hour."""
    now_ms = now_ms or int(datetime.datetime.now().timestamp() * 1000)
    day = 86_400_000
    recs = [dict(module_id="WELCOME", action="COMPLETED", timestamp=now_ms - 10 * day)]
    recs += [dict(module_id="TNT", content_id=f"tnt{i}", action="COMPLETED",
                  timestamp=now_ms - (9 - i) * day) for i in range(9)]
    recs += [dict(module_id="SYSTEMSCHECK", action="COMPLETED",
                  timestamp=now_ms - (5 - i) * day) for i in range(4)]
    recs += [dict(module_id="DRAW", action="COMPLETED", timestamp=now_ms - (4 - i) * day)
             for i in range(3)]                                  # always finishes it
    recs += [dict(module_id="PASSWORDGAME", action="QUIT", timestamp=now_ms - (3 - i) * day)
             for i in range(3)]                                  # always walks out
    recs += [dict(module_id="DANCE", action="QUIT", timestamp=now_ms - 3_600_000)]  # 1 h ago
    store.write(device_id, "mentor_behaviors", recs)
    return recs


def _stack(tmp_path, *, device_id=DEV):
    """A real runtime on a scratch store, a SIL robot, and the loopback between them."""
    store = JsonStore(str(tmp_path))
    _seed_behaviors(store, device_id)
    rt, dev = make_runtime(_App(), device_id=device_id, nickname="Sam", store=store)
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=dev, verbose=False)
    loopback(rt, vm)
    return rt, vm, dev


#: Allowed drift between the fixture's clock read and the runtime's (ms apart in practice).
CLOCK_SLACK_MINUTES = 5


def _request_lands_today(now, request_in_minutes=2 * SLOT_MINUTES) -> bool:
    """Does an activity `request_in_minutes` from `now` fall on *today's* calendar day?"""
    return (now + datetime.timedelta(minutes=request_in_minutes)).date() == now.date()


def _request_offset(now, request_in=2 * SLOT_MINUTES) -> int:
    """Signed minutes to the requested instant, always today: two slots ahead, or two behind
    in the tail of a day (an earlier request still pins to the first slot by contract)."""
    return request_in if _request_lands_today(
        now, request_in + CLOCK_SLACK_MINUTES) else -request_in


def _bedtime_body(minutes_ahead=60, request_in=2 * SLOT_MINUTES, module="STORYTELLING",
                  now=None, request_at=None):
    """`POST /config?scope=fleet` body relative to `now`: bedtime an hour out, one activity
    requested a couple of slots away (`_request_offset`); `request_at` overrides. `now` is a
    parameter so the caller can ask about the same instant twice."""
    now = now or datetime.datetime.now()
    start = now + datetime.timedelta(minutes=minutes_ahead)
    end = start + datetime.timedelta(hours=8)
    when = request_at if request_at is not None else now + datetime.timedelta(
        minutes=_request_offset(now, request_in))
    return {"weekday_bedtime": [start.strftime("%H:%M"), end.strftime("%H:%M")],
            "weekend_bedtime": [start.strftime("%H:%M"), end.strftime("%H:%M")],
            "schedule_preferences": {"parent_requests": [
                {"module_id": module, "scheduled_at": int(when.timestamp())}]}}


def _hhmm(value) -> int:
    h, _, m = str(value).partition(":")
    return int(h) * 60 + int(m)


def _inside(at, window) -> bool:
    minute, start, end = _hhmm(at), _hhmm(window["starts_at"]), _hhmm(window["ends_at"])
    return start <= minute < end if start < end else (minute >= start or minute < end)


@pytest.fixture()
def served(tmp_path):
    """The whole path, once: parent writes the fleet config, the robot pulls its day,
    the parent reads the explanations back. Returns everything the assertions need."""
    rt, vm, dev = _stack(tmp_path)
    base = status_server(rt)
    # ONE clock read, handed back: the posted config and "was it due today?" must be
    # answered about the same instant.
    now = datetime.datetime.now()
    applied = http_json(f"{base}/config?scope=fleet", method="POST",
                        body=_bedtime_body(now=now))
    assert applied["ok"] and applied["scope"] == "fleet", applied
    wire = vm.query("schedule", timeout=5.0)
    view = http_json(f"{base}/schedule?device_id={dev}")
    return dict(rt=rt, vm=vm, dev=dev, base=base, wire=wire, view=view, applied=applied,
                now=now)


# --------------------------------------------------------------------------- #
# The wire: a real CloudQueryResponse, not an empty one
# --------------------------------------------------------------------------- #
def test_the_robot_pulls_a_real_day_off_the_query_topic(served):
    got = served["vm"].query_results["schedule"]
    assert got["field"] == "schedule", got            # CloudQueryResponse.schedule (not "result")
    raw = got["raw"]
    assert raw["query"] == "schedule" and raw["request_id"], raw
    entries = served["wire"]["provided_schedule"]
    assert entries, "the robot was served an EMPTY day"
    assert all(e.get("module_id") for e in entries), entries
    # the ContentSchedule that goes on the wire carries no explanations — they are the
    # parent's view, never the robot's payload
    assert "explanations" not in served["wire"] and "why" not in json.dumps(served["wire"])
    # the query rides the activity-log topic and the answer echoes its request_id
    sent = [json.loads(p) for (t, p) in served["vm"].client.published
            if t.endswith("/events/client-service-activity-log")]
    assert sent and sent[-1]["subtopic"] == "query" and sent[-1]["query"] == "schedule"
    answered = [json.loads(p) for (t, p) in served["rt"].client.published
                if t.endswith("/commands/query_result")]
    assert answered[-1]["request_id"] == sent[-1]["request_id"]


# --------------------------------------------------------------------------- #
# The claim that binds the wire to the audit trail
# --------------------------------------------------------------------------- #
def test_the_ids_the_robot_got_are_exactly_the_ids_the_parent_is_shown(served):
    robot_ids = [e["module_id"] for e in served["wire"]["provided_schedule"]]
    parent_ids = [e["module_id"] for e in served["view"]["explanations"]]
    assert robot_ids == parent_ids, (robot_ids, parent_ids)
    assert served["view"]["served"] is True, "GET /schedule re-planned instead of "\
                                             "replaying what the robot was served"
    again = http_json(f"{served['base']}/schedule?device_id={served['dev']}")
    assert (again["explanations"], again["day"]) == \
        (served["view"]["explanations"], served["view"]["day"]), "a second read re-planned"


def test_every_entry_carries_a_parent_readable_why(served):
    lines = {e["module_id"]: e["line"] for e in served["view"]["explanations"]}
    assert lines, served["view"]
    blank = [m for m, line in lines.items() if not (line or "").strip()]
    assert not blank, f"no reason given for {blank}"
    assert all(len((line or "").split()) >= 4 for line in lines.values()), lines
    assert all(e.get("reason_codes") for e in served["view"]["explanations"])


# --------------------------------------------------------------------------- #
# Bedtime is absolute
# --------------------------------------------------------------------------- #
def test_nothing_is_planned_inside_bedtime(served):
    window = served["view"]["inputs"]["bedtime"]
    assert window["enabled"] is True, window
    timed = [e for e in served["view"]["explanations"] if e.get("at")]
    assert timed, "no entry carried a clock time"
    late = [(e["module_id"], e["at"]) for e in timed if _inside(e["at"], window)]
    assert not late, f"planned inside bedtime {window}: {late}"


def test_the_day_is_actually_truncated_by_bedtime(served):
    planned = served["view"]["inputs"]["planned"]
    assert planned["dropped_for_bedtime"] > 0, planned
    assert planned["activities"] < planned["requested"], planned


# --------------------------------------------------------------------------- #
# The parent's request outranks the recommender
# --------------------------------------------------------------------------- #
def test_the_parent_request_is_pinned_and_says_so(served):
    """The pin must survive the scored fill (the free choice must not take the requested
    module in an earlier slot), asserted through the real wire on every run.
    `test_schedule_planner` sweeps it across every hour with an injected clock."""
    pinned = [e for e in served["view"]["explanations"]
              if "parent_request" in (e.get("reason_codes") or [])]
    assert len(pinned) == 1, pinned
    assert pinned[0]["module_id"] == "STORYTELLING", pinned
    assert "parent" in pinned[0]["line"].lower(), pinned[0]["line"]
    robot_ids = [e["module_id"] for e in served["wire"]["provided_schedule"]]
    assert "STORYTELLING" in robot_ids, robot_ids
    request = served["view"]["inputs"]["parent_requests"][0]
    assert request["due_today"] is True and request["slot"] is not None, request


def test_a_request_for_tomorrow_is_not_pinned_into_today(tmp_path):
    """A request stamped a day out is absent from today's plan, and today's plan still
    explains every entry — constructed, so it runs at any hour."""
    rt, vm, dev = _stack(tmp_path)
    base = status_server(rt)
    now = datetime.datetime.now()
    applied = http_json(f"{base}/config?scope=fleet", method="POST", body=_bedtime_body(
        now=now, request_at=now + datetime.timedelta(days=1)))
    assert applied["ok"], applied
    wire = vm.query("schedule", timeout=5.0)
    view = http_json(f"{base}/schedule?device_id={dev}")

    request = view["inputs"]["parent_requests"][0]
    assert request["due_today"] is False and request["slot"] is None, request
    pinned = [e for e in view["explanations"]
              if "parent_request" in (e.get("reason_codes") or [])]
    assert pinned == [], \
        f"a request landing tomorrow must not be pinned into today's plan: {pinned}"
    assert view["explanations"], "the plan must still explain itself"
    assert all(e.get("line") for e in view["explanations"]), \
        "every entry needs its explanation line even when no request is pinned"
    assert wire["provided_schedule"], "the robot was served an EMPTY day"


# --------------------------------------------------------------------------- #
# The history actually steers the plan
# --------------------------------------------------------------------------- #
def test_finished_onboarding_never_comes_back(served):
    robot_ids = [e["module_id"] for e in served["wire"]["provided_schedule"]]
    assert not ({"WELCOME", "TNT", "SYSTEMSCHECK"} & set(robot_ids)), robot_ids
    assert set(served["view"]["inputs"]["ftue_skips"]) == {"WELCOME", "TNT", "SYSTEMSCHECK"}


def test_a_module_the_child_just_quit_is_not_offered_again_today(served):
    robot_ids = [e["module_id"] for e in served["wire"]["provided_schedule"]]
    assert "DANCE" not in robot_ids, robot_ids          # quit an hour ago
    assert "PASSWORDGAME" not in robot_ids, robot_ids   # quit 3/3 times


def test_the_history_the_planner_used_is_shown_to_the_parent(served):
    history = served["view"]["inputs"]["history"]
    assert history["PASSWORDGAME"] == {"seen": 3, "completed": 0, "abandoned": 3,
                                       "last_ts": history["PASSWORDGAME"]["last_ts"],
                                       "last_action": "QUIT"}
    assert history["DRAW"]["completed"] == 3


# --------------------------------------------------------------------------- #
# The loop closes: what the robot reports changes the next day it is served
# --------------------------------------------------------------------------- #
def test_a_reported_completion_reaches_the_store_and_the_next_plan(tmp_path):
    rt, vm, dev = _stack(tmp_path)
    base = status_server(rt)
    http_json(f"{base}/config?scope=fleet", method="POST", body=_bedtime_body())
    first = [e["module_id"] for e in vm.query("schedule", timeout=5.0)["provided_schedule"]]
    played = next(m for m in first if m not in ("DM", "FREE_CHAT", "STORYTELLING"))

    # Reported as a robot does (ActivityUpdate.mentor_behavior); recency is the stamp's age.
    vm.report_mentor_behavior({"module_id": played, "action": "COMPLETED",
                               "timestamp": int(datetime.datetime.now().timestamp() * 1000)})
    stored = [r["module_id"] for r in rt.mentor_behaviors(dev)]
    assert played in stored, stored
    assert rt.store.read(dev, "mentor_behaviors")[-1]["module_id"] == played

    # …and it is gone from the freshly planned day (played today → recency + coverage)
    again = http_json(f"{base}/schedule?device_id={dev}&refresh=1")
    assert played not in [e["module_id"] for e in again["explanations"]], again["explanations"]
    assert again["served"] is False, "a refresh must not claim the robot was served this"

    # the robot can read its own history back over the same query channel
    history = vm.query("mentor_behaviors", timeout=5.0)
    assert isinstance(history, list) and played in [r["module_id"] for r in history]


def test_an_unknown_device_is_a_404_not_an_empty_day(tmp_path):
    import urllib.error
    rt, _vm, _dev = _stack(tmp_path)
    base = status_server(rt)
    with pytest.raises(urllib.error.HTTPError) as e:
        http_json(f"{base}/schedule?device_id=d_never-seen")
    assert e.value.code == 404
