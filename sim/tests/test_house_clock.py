"""The house's clock (K10): Moxie keeps the family's time, never the container's.

A parent picks the house's time zone in the console (Settings, Time zone, for one robot or as
a house rule; while none is set and the phone is elsewhere, one click offers the phone's). The
robot is told `timezone_id` in every config push, and the appliance keeps time in the same
zone: the hello's bedtime silence and the telehealth warning (`_in_bedtime`), the day plan's
date, slots and bedtime, and "what time is it" (`clock.local`). The appliance container sets
no TZ, so before this every one of those ran on UTC while the robot was told Los Angeles: a
child asking at 7:30 pm in California heard "The time is 2:30 AY M".

Every instant here is pinned, and this process runs on UTC as the container does
(`utc_process`), so a test answers the same on any machine at any hour. `MOXIE_TIMEZONE` is
cleared unless a test sets it. No broker: the transport is `helpers_runtime.FakeClient`.
"""
import datetime
import json
import os
import re
import time
import types

import pytest

from moxie_sdk import cloud_config as C
from moxie_sdk.store import JsonStore

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")
SETTINGS_JS = os.path.join(REPO, "server", "static", "js", "settings.js")
CONFIG = "/devices/{}/config"
UTC = datetime.timezone.utc
NIGHT = ["20:00", "07:00"]


def _utc(*args) -> float:
    """Epoch seconds of a UTC wall time."""
    return datetime.datetime(*args, tzinfo=UTC).timestamp()


#: 02:30Z on Thursday 8 October 2026: 19:30 on Wednesday in Los Angeles, 04:30 in Berlin.
ASKED = _utc(2026, 10, 8, 2, 30)


@pytest.fixture(autouse=True)
def no_env_zone(monkeypatch):
    monkeypatch.delenv("MOXIE_TIMEZONE", raising=False)


@pytest.fixture
def utc_process():
    """This process on UTC, as the appliance container runs (it sets no TZ): the clock a
    naive `datetime.now()` or `time.localtime()` judged in before the house's clock."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def _app():
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import Reply

    class _App(MoxieApp):
        name = "house-clock"

        def respond(self, turn):
            return Reply(text="ok")
    return _App()


def _runtime(tmp_path, app=None, *, device_id="d_house", **kw):
    """A real runtime on a scratch store with one robot connected."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime
    return make_runtime(app or _app(), device_id=device_id,
                        store=JsonStore(str(tmp_path)), **kw)


def _clock_app(at):
    """The shipped content module as `config.build_content_app()` builds it, on a pinned
    clock: its "What Time Is It" global is a program that reads `clock.local`."""
    from moxie_sdk.content import packs as P
    from moxie_sdk.content.content_app import ContentApp
    with open(STARTER) as fh:
        defaults = P.shipped_items(json.load(fh))
    return ContentApp(P.build_module(defaults, {}), lambda messages: "the model answered",
                      default_module_id="FREE_CHAT", memory=False, safety_classifier=False,
                      content_defaults=defaults, clock=lambda: at)


def _what_time(tmp_path, set_zone=None):
    """What Moxie says to "what time is it" at `ASKED`, after `set_zone(rt, dev)` (the
    console's write), or with nothing set: the settle's push when the robot connects."""
    from helpers_runtime import drive_turn
    rt, dev = _runtime(tmp_path, _clock_app(ASKED))
    if set_zone is None:
        rt._push_config(dev)
    else:
        set_zone(rt, dev)
    return drive_turn(rt, dev, "what time is it")["output"]["text"]


def _pinned_clock(monkeypatch, at):
    """The runtime's house clock (`presence.time`, which `house_now` reads) stopped at `at`.
    Only that module's reference moves: nothing else in the process sees a fake clock."""
    import moxie_runtime.presence as presence_mod
    fake = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time)
                                    if not k.startswith("_")})
    fake.time = lambda: at
    monkeypatch.setattr(presence_mod, "time", fake)


def _notes(rt, needle):
    return [e for e in list(rt.recent) if needle in str(e.get("text"))]


# --------------------------------------------------------------------------- #
# red before green: the three defects a family met
# --------------------------------------------------------------------------- #
def test_bedtime_is_judged_in_the_houses_zone_not_the_containers(utc_process, tmp_path):
    """A family in New York sets bedtime 20:00-07:00. The container's clock reads 23:30 when
    it is 19:30 in New York: the hello must not stay quiet yet. At 01:30Z it is 21:30 there."""
    rt, dev = _runtime(tmp_path)
    rt.update_config(dev, timezone_id="America/New_York",
                     weekday_bedtime=NIGHT, weekend_bedtime=NIGHT)
    assert rt._in_bedtime(dev, _utc(2026, 10, 7, 23, 30)) is False      # 19:30 in New York
    assert rt._in_bedtime(dev, _utc(2026, 10, 8, 1, 30)) is True        # 21:30 in New York


def test_what_time_is_it_answers_in_the_houses_zone(utc_process, tmp_path):
    """The house rule names Berlin: at 02:30Z the child hears 4:30, not the container's 2:30.
    The shipped program is untouched (test_ext's G1 fence); only the host's clock moved."""
    said = _what_time(tmp_path, lambda rt, dev: rt.update_fleet_config(
        timezone_id="Europe/Berlin"))
    assert said == "The time is 4:30 AY M", said


def test_a_typo_in_the_zone_is_refused(tmp_path):
    """A zone the server's tz database does not know is a 400 with the reason, and nothing is
    stored, pushed or judged in: a robot told "Mars/Olympus" would keep no one's time."""
    with pytest.raises(ValueError, match="is not a time zone"):
        C.sanitize_config_overrides({"timezone_id": "Mars/Olympus"})
    from helpers_runtime import http_call, status_server
    rt, dev = _runtime(tmp_path)
    base = status_server(rt)
    pushed = len(rt.client.on(CONFIG.format(dev)))
    for url, typo in ((f"{base}/config?device_id={dev}", "Mars/Olympus"),
                      (f"{base}/config?scope=fleet", "America/NewYork")):
        code, out = http_call(url, method="POST", body={"timezone_id": typo})
        assert code == 400 and "is not a time zone" in out["error"], out
    assert len(rt.client.on(CONFIG.format(dev))) == pushed, "a refused zone was pushed"
    assert "timezone_id" not in rt._config_overrides.get(dev, {})
    assert rt.fleet_config() == {}
    assert not (tmp_path / "robots" / dev / "config.json").exists()


# --------------------------------------------------------------------------- #
# bedtime: the hello, the telehealth warning, every minute of the day
# --------------------------------------------------------------------------- #
def test_bedtime_holds_in_the_houses_zone_at_every_minute(utc_process, tmp_path):
    """All 1440 minutes of a day, for a house in New York and one in Tokyo, against the
    zone conversion the standard library does on its own. At 01:30Z and 12:30Z the two
    houses answer opposite ways (21:30 and 08:30 in New York, 10:30 and 21:30 in Tokyo)."""
    from zoneinfo import ZoneInfo
    rt, dev = _runtime(tmp_path)
    answers = {}
    for zone in ("America/New_York", "Asia/Tokyo"):
        rt.update_config(dev, timezone_id=zone, weekday_bedtime=NIGHT, weekend_bedtime=NIGHT)
        start = datetime.datetime(2026, 10, 7, tzinfo=UTC)
        for minute in range(1440):
            at = start + datetime.timedelta(minutes=minute)
            local = at.astimezone(ZoneInfo(zone))
            want = local.hour >= 20 or local.hour < 7
            assert rt._in_bedtime(dev, at.timestamp()) is want, (zone, f"{at:%H:%M}Z")
        answers[zone] = (rt._in_bedtime(dev, _utc(2026, 10, 8, 1, 30)),
                         rt._in_bedtime(dev, _utc(2026, 10, 8, 12, 30)))
    assert answers == {"America/New_York": (True, False), "Asia/Tokyo": (False, True)}


def test_the_hello_and_the_telehealth_warning_keep_the_houses_bedtime(
        utc_process, tmp_path, monkeypatch):
    """The two places a parent meets bedtime on the server: the walk-back-in hello stays
    quiet, and the telehealth card warns, both on the house's clock."""
    from helpers_runtime import seed_absent
    arrived = [{"name": "arrived", "away_s": 9000.0}]
    seen = {}
    for label, at in (("21:30 in New York", _utc(2026, 10, 8, 1, 30)),
                      ("19:30 in New York", _utc(2026, 10, 7, 23, 30))):
        _pinned_clock(monkeypatch, at)
        rt, dev = _runtime(tmp_path / label.replace(" ", "_"))
        rt.update_config(dev, timezone_id="America/New_York",
                         weekday_bedtime=NIGHT, weekend_bedtime=NIGHT)
        rt.greet_after_s = 300.0
        seed_absent(rt, dev, away_s=9000.0)
        seen[label] = (rt._greeting_for(dev, rt.robots[dev], arrived) is not None,
                       rt.telehealth_view(dev)["in_bedtime"],
                       bool(_notes(rt, "hello suppressed (bedtime)")))
    assert seen == {"21:30 in New York": (False, True, True),
                    "19:30 in New York": (True, False, False)}, seen


# --------------------------------------------------------------------------- #
# what time is it: the zone the robot was told
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label, set_zone, said", [
    ("no zone chosen: the zone the robot is told",
     None, "The time is 7:30 P M"),
    ("the house rule",
     lambda rt, dev: rt.update_fleet_config(timezone_id="America/Los_Angeles"),
     "The time is 7:30 P M"),
    ("this robot's own zone over the house rule",
     lambda rt, dev: (rt.update_fleet_config(timezone_id="Europe/Berlin"),
                      rt.update_config(dev, timezone_id="America/New_York")),
     "The time is 10:30 P M"),
])
def test_what_time_is_it_tells_the_zone_the_robot_was_told(
        utc_process, tmp_path, label, set_zone, said):
    assert _what_time(tmp_path, set_zone) == said, label


def test_the_try_it_card_tells_the_same_time_the_robot_would(utc_process, tmp_path):
    """A parent trying "what time is it" in the console hears the house's time too, for a
    robot or for none (the house rule)."""
    rt, dev = _runtime(tmp_path, _clock_app(ASKED))
    rt.update_fleet_config(timezone_id="Europe/Berlin")
    for body in ({"speech": "what time is it", "device_id": dev},
                 {"speech": "what time is it"}):
        out = rt.tryit_turn(body)
        assert out["ok"] is True, out
        assert out["reply"]["text"] == "The time is 4:30 AY M", (body, out["reply"])


# --------------------------------------------------------------------------- #
# the day plan
# --------------------------------------------------------------------------- #
def test_the_day_plan_is_keyed_and_slotted_on_the_houses_clock(
        utc_process, tmp_path, monkeypatch):
    """02:00Z is 19:00 on Wednesday in Los Angeles (the zone a robot is told when none is
    chosen): the plan is Wednesday's, its slots are evening ones, and the parent's GET
    /schedule shows that same now. In Tokyo the same instant is Thursday morning."""
    from helpers_runtime import http_json, status_server
    _pinned_clock(monkeypatch, _utc(2026, 10, 8, 2, 0))
    rt, dev = _runtime(tmp_path)
    _, _, inputs = rt.plan_schedule_for(dev)
    assert (inputs["day"], inputs["now"], inputs["bucket"]) == (
        "2026-10-07", "2026-10-07T19:00:00-07:00", "evening"), inputs
    assert inputs["slots"] and {s["bucket"] for s in inputs["slots"]} == {"evening"}, \
        inputs["slots"]
    view = http_json(f"{status_server(rt)}/schedule?device_id={dev}&refresh=1")
    assert (view["day"], view["planned_at"], view["inputs"]["now"]) == (
        "2026-10-07", "2026-10-07T19:00:00-07:00", "2026-10-07T19:00:00-07:00"), view

    rt.update_fleet_config(timezone_id="Asia/Tokyo")
    _, _, inputs = rt.plan_schedule_for(dev)
    assert (inputs["day"], inputs["now"], inputs["bucket"]) == (
        "2026-10-08", "2026-10-08T11:00:00+09:00", "morning"), inputs


def test_the_plan_keeps_bedtime_and_a_parents_request_on_the_houses_clock(
        utc_process, tmp_path, monkeypatch):
    """19:00 in Los Angeles, bedtime from 20:00: the evening's first slots (19:40, 19:50) come
    before bedtime, so the story a parent asked for at 19:45 there (02:45Z) takes the 19:40
    slot and the slots from 20:00 are dropped for bedtime. On the container's clock the same
    instant is 02:00, deep inside bedtime: every slot, the parent's request with them, was
    dropped (measured on origin/dev: 0 activities, 6 dropped)."""
    _pinned_clock(monkeypatch, _utc(2026, 10, 8, 2, 0))
    rt, dev = _runtime(tmp_path)
    rt.update_config(dev, **C.sanitize_config_overrides({         # as the console's POST
        "weekday_bedtime": ["20:00", "07:00"], "weekend_bedtime": ["20:00", "07:00"],
        "schedule_preferences": [{"module_id": "STORYTELLING",
                                  "scheduled_at": int(_utc(2026, 10, 8, 2, 45))}]}))
    sched, explanations, inputs = rt.plan_schedule_for(dev)
    request, = inputs["parent_requests"]
    assert (request["due_today"], request["at"], request["slot"]) == (True, "19:45", 0), request
    assert [(s["at"], s["in_bedtime"]) for s in inputs["slots"][:3]] == [
        ("19:40", False), ("19:50", False), ("20:00", True)], inputs["slots"]
    timed = [(e["module_id"], e["at"]) for e in explanations if e.get("at")]
    assert timed[:1] == [("STORYTELLING", "19:40")], timed
    assert len(timed) == 2 and all(at < "20:00" for _, at in timed), timed
    dropped = sum(s["in_bedtime"] for s in inputs["slots"])
    assert dropped and inputs["planned"]["dropped_for_bedtime"] == dropped, inputs["planned"]
    assert "STORYTELLING" in [r["module_id"] for r in sched["provided_schedule"]], sched


# --------------------------------------------------------------------------- #
# Insights: the family's days
# --------------------------------------------------------------------------- #
def test_insights_files_activity_under_the_houses_day(utc_process, tmp_path, monkeypatch):
    """A wake reported at 19:30 on Wednesday in Los Angeles (02:30Z Thursday) is Wednesday's
    on the parent's Insights card, and the card's newest day is the house's today (19:40
    Wednesday there). On the container's clock it was filed under Thursday, a day the
    family had not reached, and the card ended on the container's today."""
    from zoneinfo import ZoneInfo
    from moxie_sdk import telemetry as T
    assert T.packet_day({"recorded_at": ASKED}, tz=ZoneInfo("America/Los_Angeles")) == \
        "2026-10-07"
    assert T.packet_day({"recorded_at": ASKED}, tz=UTC) == "2026-10-08"
    _pinned_clock(monkeypatch, _utc(2026, 10, 8, 2, 40))
    rt, dev = _runtime(tmp_path)
    rt.update_config(dev, **C.sanitize_config_overrides({"logging_policy": "FULL"}))
    rt.ingest_telemetry(dev, json.dumps(T.build_packet("wake", b"", moxie_id=dev,
                                                       recorded_at=int(ASKED))))
    view = rt.telemetry_view(dev, days=2)
    assert [(r["day"], r["count"]) for r in view["history"]] == [
        ("2026-10-06", 0), ("2026-10-07", 1)], view["history"]
    rolled = rt.store.read(dev, T.DAILY_COLLECTION, {})
    assert list(rolled["days"]) == ["2026-10-07"], rolled
    # A lost roll-up write is repaired from the ring on the next read: on the same clock.
    rt.store.delete(dev, T.DAILY_COLLECTION)
    view = rt.telemetry_view(dev, days=2)
    assert [(r["day"], r["count"]) for r in view["history"]] == [
        ("2026-10-06", 0), ("2026-10-07", 1)], view["history"]


# --------------------------------------------------------------------------- #
# the whitelist, the push, the fallbacks
# --------------------------------------------------------------------------- #
def test_a_zone_this_server_knows_is_stored_and_pushed_as_given(tmp_path):
    from helpers_runtime import http_call, status_server
    rt, dev = _runtime(tmp_path)
    base = status_server(rt)
    code, out = http_call(f"{base}/config?device_id={dev}", method="POST",
                          body={"timezone_id": "America/New_York"})
    assert code == 200 and out["applied"] == {"timezone_id": "America/New_York"}, out
    assert out["saved"] is True and out["config_effective"]["timezone_id"] == "America/New_York"
    assert rt.client.on(CONFIG.format(dev))[-1]["timezone_id"] == "America/New_York"
    assert rt.robots[dev].extra["timezone_id"] == "America/New_York"
    record = json.loads((tmp_path / "robots" / dev / "config.json").read_text())
    assert record == {"timezone_id": "America/New_York"}
    # A name typed in the wrong case is the zone it names, stored in its own spelling.
    code, out = http_call(f"{base}/config?scope=fleet", method="POST",
                          body={"timezone_id": " europe/berlin "})
    assert code == 200 and out["fleet_config"] == {"timezone_id": "Europe/Berlin"}, out
    # An empty zone is no edit at all: the zone stays as it was.
    assert C.sanitize_config_overrides({"timezone_id": ""}) == {}
    assert C.sanitize_config_overrides({"timezone_id": None}) == {}


def test_a_write_for_a_sleeping_robot_lands_when_it_connects(tmp_path):
    """K7's path: the zone saved for a robot that is switched off rides the settle's first
    `/config` when it connects, and its content app tells the time in it from then on."""
    from helpers_runtime import LatchClient, deliver, http_call, status_server
    rt, _ = _runtime(tmp_path, device_id="d_awake", allow_unverified_bots=False)
    rt.client = LatchClient(rt)
    rt.set_permit("d_asleep", True)
    base = status_server(rt)
    code, out = http_call(f"{base}/config?device_id=d_asleep", method="POST",
                          body={"timezone_id": "Asia/Tokyo"})
    assert code == 200 and (out["online"], out["pushed"]) == (False, False), out
    before = len(rt.client.on(CONFIG.format("d_asleep")))
    deliver(rt, "/devices/d_asleep/state", json.dumps({"robot_firmware_version": "x"}))
    assert rt.client.wait_for(lambda pubs: sum(t == CONFIG.format("d_asleep")
                                               for t, _ in pubs) > before)
    first = rt.client.on(CONFIG.format("d_asleep"))[before]
    assert first["timezone_id"] == "Asia/Tokyo"
    assert rt.robots["d_asleep"].extra["timezone_id"] == "Asia/Tokyo"


def test_a_stored_zone_this_server_cannot_read_is_labelled_utc_once_and_never_raises(
        utc_process, tmp_path, monkeypatch):
    """A house rule an older build accepted unchecked ("Mars/Olympus" on disk): bedtime,
    the plan and the house clock run on UTC, labelled, with one line in the feed however
    often they are asked; nothing raises."""
    _pinned_clock(monkeypatch, _utc(2026, 10, 8, 2, 0))
    rt, dev = _runtime(tmp_path)
    rt.store.write_shared(rt.FLEET_CONFIG_COLLECTION, {
        "timezone_id": "Mars/Olympus", "weekday_bedtime": NIGHT, "weekend_bedtime": NIGHT})
    zone = rt.house_zone(dev)
    assert (zone.tz, zone.name, zone.resolved) == (UTC, "UTC", False)
    assert rt._in_bedtime(dev, _utc(2026, 10, 7, 23, 30)) is True        # 23:30 UTC
    assert rt._in_bedtime(dev, _utc(2026, 10, 7, 12, 0)) is False
    _, _, inputs = rt.plan_schedule_for(dev)
    assert inputs["now"] == "2026-10-08T02:00:00+00:00", inputs["now"]
    rt.house_now(dev)
    notes = _notes(rt, "'Mars/Olympus'")
    assert len(notes) == 1, notes
    assert notes[0]["kind"] == "error" and "UTC" in notes[0]["text"], notes
    assert "does not know that zone" in notes[0]["text"], notes


def test_a_saved_robot_zone_the_whitelist_now_refuses_is_dropped_at_load(tmp_path, capsys):
    """A robot's own saved record holding a typo (written before the check existed) loses
    that key when the supervisor starts, said in one line, and the robot is told the zone in
    force underneath (here the default), never the typo."""
    record = tmp_path / "robots" / "d_house" / "config.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"timezone_id": "Mars/Olympus", "audio_volume": 0.3}))
    rt, dev = _runtime(tmp_path)
    assert rt._config_overrides[dev] == {"audio_volume": 0.3}
    assert re.search(r"dropped at load.*timezone_id", capsys.readouterr().out)
    assert rt._push_config(dev)["timezone_id"] == C.DEFAULT_TIMEZONE_ID


def test_with_no_tz_database_a_name_is_shape_checked_and_the_clock_is_labelled_utc(
        utc_process, tmp_path):
    """A host with neither a system tz database nor the `tzdata` package: a plausible name
    is still accepted (the robot has its own database), garbage is not, and the house's
    clock says it runs on UTC because no zone can be read here."""
    import zoneinfo
    real = zoneinfo.ZoneInfo, zoneinfo.available_timezones

    def no_db(key):
        raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")

    zoneinfo.ZoneInfo, zoneinfo.available_timezones = no_db, (lambda: set())
    C.known_timezones.cache_clear()
    C._known_folded.cache_clear()
    try:
        assert C.check_timezone("America/New_York") == "America/New_York"
        for garbage in ("not a zone", "../etc/passwd", "a" * 80):
            with pytest.raises(ValueError):
                C.check_timezone(garbage)
        rt, dev = _runtime(tmp_path)
        rt.update_fleet_config(timezone_id="America/New_York")
        assert rt.house_zone(dev) == (UTC, "UTC", False)
        note, = _notes(rt, "'America/New_York'")
        assert "no time zone database" in note["text"], note
    finally:
        zoneinfo.ZoneInfo, zoneinfo.available_timezones = real
        C.known_timezones.cache_clear()
        C._known_folded.cache_clear()
    assert "America/New_York" in C.known_timezones()


def test_moxie_timezone_is_the_house_zone_until_the_console_picks_one(
        tmp_path, monkeypatch, capsys):
    """For an install nobody opens the console on. A zone the console saves wins over it,
    and a typo in it is ignored with one line: the robot is told the default instead."""
    monkeypatch.setattr(C, "_ENV_ZONE_REFUSED", set())
    monkeypatch.setenv("MOXIE_TIMEZONE", "Europe/Berlin")
    rt, dev = _runtime(tmp_path / "env")
    assert rt._push_config(dev)["timezone_id"] == "Europe/Berlin"
    assert rt.effective_config(dev)["timezone_id"] == "Europe/Berlin"   # the console shows it
    assert "timezone_id" not in rt.fleet_config(), "the env value is never written as a rule"
    assert rt.house_zone(dev).name == "Europe/Berlin"
    rt.update_fleet_config(timezone_id="America/New_York")
    assert rt._push_config(dev)["timezone_id"] == "America/New_York"

    monkeypatch.setenv("MOXIE_TIMEZONE", "Mars/Olympus")
    typo, dev2 = _runtime(tmp_path / "typo")
    capsys.readouterr()
    for _ in range(3):
        assert typo._push_config(dev2)["timezone_id"] == C.DEFAULT_TIMEZONE_ID
    assert capsys.readouterr().out.count("MOXIE_TIMEZONE ignored") == 1


def test_the_mic_asked_line_reads_the_same_zone_the_house_keeps(tmp_path):
    """K2's `mic asked HH:MM ZONE` line (server fleet/robots.py) reads the zone from the
    snapshot's `config_effective`, the same one the house's clock keeps."""
    import sys
    sys.path.insert(0, os.path.join(REPO, "server"))
    from moxie_server.fleet.robots import normalize_robot
    rt, dev = _runtime(tmp_path)
    rt.update_fleet_config(timezone_id="America/New_York")
    rt.robots[dev].extra["stt_subscribed_at"] = ASKED
    robot = next(r for r in rt.status_snapshot()["robots"] if r["device_id"] == dev)
    assert "mic asked 22:30 EDT" in normalize_robot(robot)["summary"], robot
    assert rt.house_now(dev, ASKED).strftime("%H:%M %Z") == "22:30 EDT"


# --------------------------------------------------------------------------- #
# the console's constants, and the tz database this venv resolves zones with
# --------------------------------------------------------------------------- #
def test_the_console_names_the_same_default_and_suggests_only_zones_this_server_knows():
    src = open(SETTINGS_JS, encoding="utf-8").read()
    default = re.search(r"const HOUSE_ZONE_DEFAULT='([^']+)';", src)
    assert default and default.group(1) == C.DEFAULT_TIMEZONE_ID, \
        "settings.js's default zone and cloud_config.DEFAULT_TIMEZONE_ID must be one zone"
    block = re.search(r"const COMMON_ZONES=\[(.*?)\];", src, re.S)
    zones = re.findall(r"'([^']+)'", block.group(1)) if block else []
    assert len(zones) >= 40, zones
    assert C.DEFAULT_TIMEZONE_ID in zones
    unknown = sorted(set(zones) - C.known_timezones())
    assert not unknown, f"the console suggests zones this server would refuse: {unknown}"


def test_the_requirements_pin_tzdata_and_this_venv_resolves_a_zone():
    """The slim image's insurance (mqtt/requirements.txt), and the hermetic venv itself
    resolving a zone with a daylight-saving rule."""
    from zoneinfo import ZoneInfo
    with open(os.path.join(REPO, "mqtt", "requirements.txt")) as fh:
        assert re.search(r"^tzdata>=\d", fh.read(), re.M), "tzdata is not pinned"
    assert "America/New_York" in C.known_timezones()
    summer = datetime.datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("America/New_York"))
    winter = datetime.datetime(2026, 12, 1, 12, tzinfo=ZoneInfo("America/New_York"))
    assert (summer.utcoffset(), winter.utcoffset()) == (datetime.timedelta(hours=-4),
                                                        datetime.timedelta(hours=-5))
