"""
Parent console <-> supervisor round trip: the console-runtime contract, end to end.

The console's `/local/*` endpoints are thin proxies over the MQTT supervisor's status
server. `test_fleet.py` unit-tests the normalizers; this exercises the *seam* — the URL
the console builds, the query string and body it forwards, and what it does with a 400,
404 or dead supervisor on the other side. `helpers_console_supervisor.FakeSupervisor`
stands in for the supervisor on a free port, backed by a REAL `MoxieRuntime` wherever
one can be; `test_fake_status_server_matches_the_real_runtime_shapes` keeps the
hand-built parts honest.

Sibling files cover the other cards against the same fake: `test_console_memory.py`,
`test_console_telehealth.py`, `test_console_voice.py`, `test_console_content.py`,
`test_console_devices.py`. Tests in each file share one module-scoped supervisor and
some deliberately build on the previous test's state (config edits).

Skips cleanly when fastapi/httpx (or the server's own deps) are absent.
"""
import json

import pytest

from helpers_console import set_status_url

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console_supervisor import (CONV, DEAD, DEVICE,  # noqa: E402
                                        PACKETS, SCHEDULE, SEED_AT, _snapshot,
                                        bare_runtime, client, console_js_served,
                                        face_catalog, install_content,
                                        merge_config_layers, quicklogin, schedule,
                                        schedulable_module_ids, static, supervisor,
                                        telemetry)

__all__ = ["client", "supervisor"]      # fixtures, imported for pytest


def _get(client, route, **kw):
    return client.get(f"/local/robots/{DEVICE}/{route}", **kw)


# --------------------------------------------------------------------------- #
# GET /local/fleet
# --------------------------------------------------------------------------- #

def test_fleet_normalizes_the_supervisors_snapshot(client):
    r = client.get("/local/fleet")
    assert r.status_code == 200
    f = r.json()
    assert f["ok"] is True and f["app"] == "content" and f["robot_count"] == 1
    robot = f["robots"][0]
    assert robot["device_id"] == DEVICE and robot["child"] == "Sam"
    assert robot["online"] is True and robot["firmware"] == "3.6.4"
    assert robot["battery_level"] == 91 and robot["wifi_ssid"] == "Home"
    assert robot["telemetry_count"] == 2 and robot["summary"]
    assert "battery 91%" in robot["summary"]
    assert f["recent"] == [{"t": 1, "kind": "chat", "text": "hi"}]
    assert f["error"] is None


def test_the_console_learns_the_module_catalog_from_the_supervisor(client):
    """The activity picker's options come from the one on-board catalog, never a copy."""
    f = client.get("/local/fleet").json()
    assert "JOKE" in f["schedule_modules"]
    assert set(f["schedule_modules"]) == set(schedulable_module_ids())


# --------------------------------------------------------------------------- #
# Every card, with the supervisor down: a readable ok:false in the card's own
# shape, never a 500 and never an empty list that reads as "nothing there".
# --------------------------------------------------------------------------- #

#: `...` means "present and truthy", `_ABSENT` "key not sent"; dotted keys reach into
#: nested dicts.
_ABSENT = "<absent>"
_DOWN = [
    ("GET", "/local/fleet", 200,             # the fleet list itself stays renderable
     {"ok": False, "robots": [], "robot_count": 0, "error": ...}),
    ("GET", f"/local/robots/{DEVICE}/telemetry", 503,
     {"ok": False, "history": [], "persisted": False, "totals.total": 0}),
    ("DELETE", f"/local/robots/{DEVICE}/telemetry", 503,     # never claims an erase
     {"ok": False, "erased": False, "records": [], "error": ...}),
    ("GET", f"/local/robots/{DEVICE}/safety", 503,
     {"ok": False, "events": [], "error": ...}),
    ("POST", f"/local/robots/{DEVICE}/permit", 503, {"ok": False}),
    ("GET", "/local/permits", 503, {"pending": []}),
    ("GET", f"/local/robots/{DEVICE}/memory", 503,
     {"ok": False, "namespaces": [], "error": ...}),
    ("DELETE", f"/local/robots/{DEVICE}/memory/mchat", 503, {"ok": False, "erased": _ABSENT}),
    # a blank plan would read as "Moxie has nothing planned"
    ("GET", f"/local/robots/{DEVICE}/schedule", 503,
     {"ok": False, "entries": [], "error": ...,
      "constraints.bedtime": {"enabled": False, "kind": ""}}),
    ("GET", f"/local/robots/{DEVICE}/telehealth", 503,
     {"ok": False, "transcript": [], "max_intensity": 2}),
    ("GET", f"/local/robots/{DEVICE}/voice", 503,
     {"ok": False, "available": {"speech": [], "listening": []}, "error": ...}),
    ("GET", "/local/content", 503, {"ok": False, "items": [], "packs": [], "error": ...}),
    ("POST", "/local/content/undo", 503, {"error": ...}),
]


def _dig(body, dotted):
    for part in dotted.split("."):
        if part not in body:
            return _ABSENT
        body = body[part]
    return body


@pytest.mark.parametrize("method, path, status, expect", _DOWN,
                         ids=[f"{m} {p}" for m, p, _, _ in _DOWN])
def test_every_card_is_graceful_when_the_supervisor_is_down(client, monkeypatch,
                                                            method, path, status, expect):
    set_status_url(DEAD, monkeypatch)
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == status, r.text
    body = r.json()
    for key, want in expect.items():
        if want is ...:
            got = _dig(body, key)
            assert got is not _ABSENT and got, (key, body)
        else:
            assert _dig(body, key) == want, (key, body)


@pytest.mark.parametrize("route, empty", [
    ("telemetry", {"by_event": [], "events": []}),
    ("safety", {"events": [], "total": 0}),
    ("memory", {"namespaces": [], "total": 0}),
    ("schedule", {"entries": []}),
    ("telehealth", {}),
])
def test_an_unknown_device_is_a_404_in_the_cards_own_shape(client, route, empty):
    r = client.get(f"/local/robots/d_nope/{route}")
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["ok"] is False
    assert {k: body[k] for k in empty} == empty
    assert "d_nope" in body["error"]


# --------------------------------------------------------------------------- #
# POST /local/robots/{id}/config and /local/fleet/config (these build on each other)
# --------------------------------------------------------------------------- #

def test_config_edit_forwards_and_returns_the_applied_overrides(client, supervisor):
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"audio_volume": 60, "weekday_bedtime": ["20:30", "07:00"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["device_id"] == DEVICE
    # the runtime's own sanitizer ran: a 0-100 slider became a 0-1 float
    assert body["applied"]["audio_volume"] == pytest.approx(0.6)
    assert body["config_overrides"]["audio_volume"] == pytest.approx(0.6)
    assert "weekday_bedtime" in body["applied"]
    # the console forwarded the device id + the raw body, not a rewrite of it
    device_id, raw = supervisor.config_posts[-1]
    assert device_id == DEVICE
    assert json.loads(raw) == {"audio_volume": 60, "weekday_bedtime": ["20:30", "07:00"]}


def test_a_config_edit_shows_up_in_the_next_fleet_read(client):
    f = client.get("/local/fleet").json()
    assert f["robots"][0]["config_overrides"]["audio_volume"] == pytest.approx(0.6)


@pytest.mark.parametrize("path, body, needle", [
    # a sanitizer refusal must reach the parent as a 400 with the reason, not a 200
    (f"/local/robots/{DEVICE}/config", {"audio_wake_set": "maybe"}, "audio_wake_set"),
    ("/local/robots/d_nope/config", {"audio_volume": 50}, None),
    ("/local/fleet/config", {"alarms": [{"days": ["funday"], "time": "07:00"}]}, None),
    (f"/local/robots/{DEVICE}/config", {"face": {"eye_color": "chartreuse"}}, "chartreuse"),
])
def test_bad_config_input_surfaces_the_supervisors_400(client, path, body, needle):
    r = client.post(path, json=body)
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False
    if needle:
        assert needle in r.json()["error"]


def test_an_alarm_edit_round_trips_in_the_recovered_wakeschedule_shape(client):
    """Weekday checkboxes + time → `WakeSchedule` on the wire, and back out of the next
    fleet read in the shape the robot was pushed."""
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"alarms": {"wakes": [{"days": ["mon", 6], "time": "07:15"}],
                                     "enabled": True}})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["alarms"] == {
        "wakes": [{"days": [0, 6], "time": "07:15"}], "enabled": True}
    f = client.get("/local/fleet").json()
    assert f["robots"][0]["config_effective"]["alarms"]["wakes"][0]["days"] == [0, 6]


def test_fleet_config_edit_forwards_with_scope_fleet(client, supervisor):
    r = client.post("/local/fleet/config", json={"timezone_id": "America/Chicago"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["scope"] == "fleet"
    assert body["fleet_config"]["timezone_id"] == "America/Chicago"
    where, raw = supervisor.config_posts[-1]
    assert where == "scope=fleet"
    assert json.loads(raw) == {"timezone_id": "America/Chicago"}


def test_the_fleet_default_shows_up_in_every_robots_effective_config(client):
    f = client.get("/local/fleet").json()
    assert f["fleet_config"]["timezone_id"] == "America/Chicago"
    robot = f["robots"][0]
    assert robot["config_effective"]["timezone_id"] == "America/Chicago"
    assert "timezone_id" not in robot["config_overrides"]        # inherited, not local
    assert robot["config_sources"]["timezone_id"] == "fleet"
    assert robot["config_sources"]["audio_volume"] == "robot"    # set per robot earlier


def test_a_per_robot_override_beats_the_fleet_default_through_the_console(client):
    client.post("/local/fleet/config", json={"audio_volume": 20})
    client.post(f"/local/robots/{DEVICE}/config", json={"audio_volume": 90})
    f = client.get("/local/fleet").json()
    assert f["fleet_config"]["audio_volume"] == pytest.approx(0.2)
    assert f["robots"][0]["config_effective"]["audio_volume"] == pytest.approx(0.9)


# --------------------------------------------------------------------------- #
# Moxie's look — face customization
# --------------------------------------------------------------------------- #

def test_the_console_learns_the_face_catalog_from_the_supervisor(client):
    """The card renders the SDK's catalog, so it can never offer an option
    `validate_face` would then reject."""
    f = client.get("/local/fleet").json()
    assert [s["id"] for s in f["face_catalog"]] == [s["id"] for s in face_catalog()]
    eyes = next(s for s in f["face_catalog"] if s["id"] == "eye_color")
    assert eyes["cited"] is True
    assert {"green", "blue", "purple", "brown", "gold", "teal"} <= {
        o["id"] for o in eyes["options"]}          # the recovered enum, still offered
    swatchable = [o for o in eyes["options"] if "hex" in o]
    assert len(swatchable) == 6                    # …and still the only previewable six
    assert all(o["hex"].startswith("#") for o in swatchable)
    # a slot no source lists ids for says so rather than lying
    assert next(s for s in f["face_catalog"] if s["id"] == "stickers")["cited"] is False


def test_a_face_edit_round_trips_and_changes_the_texture_key(client, supervisor):
    """A picked look reaches the supervisor, comes back in the effective config, and
    moves the cache-buster the robot keys its texture on."""
    before = client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"face": {"eye_color": "teal", "face_color": "pink"}})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["face"] == {"eye_color": "teal", "face_color": "pink"}
    assert json.loads(supervisor.config_posts[-1][1])["face"]["eye_color"] == "teal"
    robot = client.get("/local/fleet").json()["robots"][0]
    assert robot["config_effective"]["face"]["face_color"] == "pink"
    assert robot["face_cache_id"] and robot["face_cache_id"] != before

    # a *different* look must not reuse the same texture key
    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"eye_color": "gold"}})
    after = client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    assert after != robot["face_cache_id"]


def test_a_fleet_face_is_the_house_look_and_one_robot_can_restyle_a_layer(client):
    """The house sets teal eyes; this robot already wears gold ones (test above), so the
    robot layer wins that slot. Restyling a different slot replaces the robot layer
    wholesale, so the eyes fall through to the house look."""
    client.post("/local/fleet/config", json={"face": {"eye_color": "teal"}})
    f = client.get("/local/fleet").json()
    robot = f["robots"][0]
    assert f["fleet_config"]["face"] == {"eye_color": "teal"}
    assert robot["config_effective"]["face"]["eye_color"] == "gold"   # robot beats house
    assert robot["config_sources"]["face"] == "robot"

    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"face_color": "pink"}})
    robot = client.get("/local/fleet").json()["robots"][0]
    assert robot["config_effective"]["face"] == {"eye_color": "teal",
                                                "face_color": "pink"}


def test_reset_to_default_clears_the_look_and_the_texture_key(client):
    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"eye_color": "gold"}})
    assert client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    r = client.post(f"/local/robots/{DEVICE}/config", json={"face": None})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["face"] is None
    client.post("/local/fleet/config", json={"face": None})
    robot = client.get("/local/fleet").json()["robots"][0]
    assert robot["config_effective"]["face"] is None
    assert robot["face_cache_id"] == ""


# --------------------------------------------------------------------------- #
# Telemetry: the summary, the durable week, and the Erase history button
# --------------------------------------------------------------------------- #

def test_telemetry_returns_the_normalized_summary(client, supervisor):
    r = _get(client, "telemetry?limit=5")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["ok"] is True and t["device_id"] == DEVICE and t["count"] == 3
    # counts, most frequent first
    assert t["by_event"][0] == {"event": "conversation_start", "count": 2,
                                "last_seen": 140}
    assert [row["event"] for row in t["by_event"]] == ["conversation_start", "battery_low"]
    # newest-first event rows
    assert [e["event_name"] for e in t["events"]] == [
        "battery_low", "conversation_start", "conversation_start"]
    assert all(e["session_id"] == "s1" for e in t["events"])
    assert supervisor.telemetry_queries[-1] == (DEVICE, 5)


def test_the_card_gets_a_week_of_history_not_just_this_session(client):
    """The daily roll-up lets the card render "last week", not one supervisor lifetime."""
    body = _get(client, "telemetry?days=3").json()
    assert [row["day"] for row in body["history"]] == ["2026-08-31", "2026-09-01",
                                                       "2026-09-02"]
    assert [row["count"] for row in body["history"]] == [5, 0, 3]
    # bar heights, scaled against the busiest day in the window
    assert [row["share"] for row in body["history"]] == [1.0, 0.0, 0.6]
    assert body["history"][1]["top_event"] is None      # a quiet day stays a quiet day


def test_the_card_is_told_the_retention_window_and_the_lifetime_total(client):
    """A sliding window must not be mistaken for everything that ever happened."""
    body = _get(client, "telemetry").json()
    assert body["totals"]["total"] == 11 and body["count"] == 3
    assert body["totals"]["first_day"] == "2026-08-31"
    assert body["totals"]["dropped_days"] == 2
    assert body["retention"]["packets"] > 0 and body["retention"]["days"] > 0
    assert body["policy"] == "NO_MEDIA" and body["persisted"] is True


@pytest.mark.parametrize("days", [1, 7])
def test_the_days_window_is_forwarded_to_the_supervisor(client, days):
    assert len(_get(client, f"telemetry?days={days}").json()["history"]) == days


def test_erasing_telemetry_from_the_console_really_empties_the_store(client, supervisor):
    """Through the REAL `erase_telemetry`, asserted on the supervisor's store afterwards —
    a 200 is not evidence that anything was deleted."""
    from moxie_sdk import telemetry as T
    rt = supervisor.runtime
    rt.ingest_telemetry(DEVICE, json.dumps(       # writes the ring AND the day roll-up
        T.build_packet("wake", b"", moxie_id=DEVICE, recorded_at=1756800000)))
    rt._on_activity(DEVICE, json.dumps(
        {"timestamp": 1756800000,
         "mentor_behavior": {"module_id": "MODULE_MISSION", "action": "COMPLETED"}}))
    collections = [T.PACKETS_COLLECTION, T.DAILY_COLLECTION, "mentor_behaviors"]
    assert all(rt.store.read(DEVICE, c, None) is not None for c in collections)

    r = client.delete(f"/local/robots/{DEVICE}/telemetry")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["erased"] is True
    assert out["records"] == sorted(collections)
    assert supervisor.telemetry_erases[-1] == DEVICE
    assert all(rt.store.read(DEVICE, c, None) is None for c in collections)
    assert rt.mentor_behaviors(DEVICE) == []


# --------------------------------------------------------------------------- #
# The parent safety review queue
# --------------------------------------------------------------------------- #

def test_safety_queue_reaches_the_console(client, supervisor):
    r = _get(client, "safety?limit=5")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["ok"] is True and s["device_id"] == DEVICE and s["enabled"] is True
    assert s["classifier"] == "rules" and s["total"] == 2
    assert s["blocked"] == 1 and s["flagged"] == 1 and s["unreviewed"] == 2
    assert {row["label"] for row in s["by_category"]} >= {"Self-harm", "Profanity"}
    # newest first, and the child's words are masked before they leave the runtime
    top = s["events"][0]
    assert top["side"] == "child" and top["excerpt"] and "***" in top["excerpt"]
    assert "bullshit" not in json.dumps(s)
    assert supervisor.safety_queries[-1] == (DEVICE, 5)


def test_acknowledging_one_event_clears_it_for_the_parent(client):
    before = _get(client, "safety").json()
    target = [e for e in before["events"] if e["action"] == "block"][0]
    r = client.post(f"/local/robots/{DEVICE}/safety", json={"event_id": target["id"]})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["ok"] is True and after["unreviewed"] == before["unreviewed"] - 1
    assert [e for e in after["events"] if e["id"] == target["id"]][0]["reviewed"] is True
    # ...and the empty body acknowledges the rest
    assert client.post(f"/local/robots/{DEVICE}/safety", json={}).json()["unreviewed"] == 0


# --------------------------------------------------------------------------- #
# The device allowlist (pairing gate)
# --------------------------------------------------------------------------- #

def test_permitting_a_pending_robot_reaches_the_supervisor(client, supervisor):
    supervisor.permits["devices"].clear()
    r = client.post(f"/local/robots/{DEVICE}/permit", json={"label": "Sam's Moxie"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert supervisor.permit_posts[-1] == {
        "device_id": DEVICE, "permitted": True, "label": "Sam's Moxie"}
    assert DEVICE in supervisor.permits["devices"]
    assert [p["device_id"] for p in r.json()["permits"]] == [DEVICE]


def test_revoking_forwards_permitted_false(client, supervisor):
    supervisor.permits["devices"][DEVICE] = {"permitted_at": 1, "label": ""}
    r = client.post(f"/local/robots/{DEVICE}/permit", json={"permitted": False})
    assert r.status_code == 200
    assert supervisor.permit_posts[-1]["permitted"] is False
    assert DEVICE not in supervisor.permits["devices"]


def test_the_open_toggle_round_trips(client, supervisor):
    r = client.post("/local/fleet/permits", json={"allow_unverified_bots": True})
    assert r.status_code == 200 and r.json()["allow_unverified_bots"] is True
    assert supervisor.permits["allow_unverified_bots"] is True
    client.post("/local/fleet/permits", json={"allow_unverified_bots": False})
    assert supervisor.permits["allow_unverified_bots"] is False


def test_the_console_lists_the_allowlist(client, supervisor):
    supervisor.permits["devices"][DEVICE] = {"permitted_at": 7, "label": "Sam's Moxie"}
    r = client.get("/local/permits")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["permits"][0]["label"] == "Sam's Moxie"
    supervisor.permits["devices"].clear()


def _prepare_pairing(client, email):
    auth = quicklogin(client, email)
    prep = client.post("/local/pairing/prepare",
                       json={"ssid": "Home", "password": "hunter2"}, headers=auth)
    assert prep.status_code == 200, prep.text
    return prep.json()["qr_payload"]


def test_pairing_through_the_console_auto_permits_the_robot(client, supervisor):
    """Completing the parent's own pairing flow IS the parent saying "this robot is
    mine". The QR carries no device id, so the caller supplies the MQTT `d_<uuid>`."""
    supervisor.permits["devices"].clear()
    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": _prepare_pairing(client, "permit-test@local"),
                          "device_id": "d_just_paired"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["permitted"] is True and body["permit_error"] is None
    assert "d_just_paired" in supervisor.permits["devices"]
    assert supervisor.permits["devices"]["d_just_paired"]["label"] == "paired via console"


def test_pairing_without_a_device_id_still_pairs(client, supervisor):
    """A robot only reveals its `d_<uuid>` when it reaches the broker, so it pairs in the
    REST database and shows up as pending — nothing is permitted blindly."""
    before = dict(supervisor.permits["devices"])
    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": _prepare_pairing(client, "permit-test2@local")})
    assert r.status_code == 200
    assert r.json()["permitted"] is False and r.json()["robot_id"]
    assert supervisor.permits["devices"] == before


def test_pairing_remembers_the_mqtt_identity_on_the_record(client):
    """`resolve_device_id`'s best branch exists only if pair-complete stores the id."""
    from moxie_server import db
    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": _prepare_pairing(client, "devices-test3@local"),
                          "device_id": "d_remembered"})
    assert r.status_code == 200, r.text
    rid = r.json()["robot_id"]
    attrs = json.loads(db.q1("SELECT * FROM robots WHERE id=?", (rid,))["attributes"])
    assert attrs["mqtt-device-id"] == "d_remembered"


# --------------------------------------------------------------------------- #
# Today's plan — the recommender's "why this activity today"
# --------------------------------------------------------------------------- #

def test_todays_plan_reaches_the_console_with_a_reason_per_entry(client, supervisor):
    before = len(supervisor.schedule_queries)
    r = _get(client, "schedule")
    assert r.status_code == 200, r.text
    s = r.json()
    assert supervisor.schedule_queries[before:] == [(DEVICE, "")]
    assert s["ok"] is True and s["error"] is None
    assert s["device_id"] == DEVICE and s["day"] == "2026-09-02"
    assert s["child_name"] == "friend" and s["served"] is True
    # the rows ARE the served day, in order — the whole point of the card
    assert [e["module_id"] for e in s["entries"]] == [
        e["module_id"] for e in SCHEDULE["schedule"]["provided_schedule"]]
    assert all(e["why"] for e in s["entries"])


def test_untimed_fixtures_show_no_clock_time_and_scored_picks_do(client):
    rows = {e["module_id"]: e for e in _get(client, "schedule").json()["entries"]}
    assert rows["DM"]["time_local"] is None and rows["DM"]["fixture"] is True
    assert rows["FREE_CHAT"]["time_local"] is None
    assert rows["WELCOME"]["time_local"] is None
    assert rows["STORYTELLING"]["time_local"] == "09:03"
    assert rows["SCAVENGERHUNT"]["time_local"] == "09:13"
    assert rows["SCAVENGERHUNT"]["fixture"] is False


def test_the_constraints_the_planner_reported_reach_the_footer(client):
    c = _get(client, "schedule").json()
    assert c["constraints"]["bedtime"] == {"enabled": True, "kind": "weekday",
                                           "starts_at": "09:23", "ends_at": "17:23"}
    assert c["dropped_for_bedtime"] == 4
    assert c["constraints"]["parent_request"] == {
        "count": 1, "pinned": [{"module_id": "STORYTELLING", "at": "08:43"}]}
    assert [e["module_id"] for e in c["entries"] if e["pinned"]] == ["STORYTELLING"]
    # the runtime says telemetry carries no module signal; the console must not lose it
    assert c["constraints"]["telemetry_signal"] is False


def test_refresh_is_forwarded_so_a_parent_can_re_plan_the_day(client, supervisor):
    before = len(supervisor.schedule_queries)
    assert _get(client, "schedule", params={"refresh": "true"}).status_code == 200
    assert supervisor.schedule_queries[before:] == [(DEVICE, "1")]


# --------------------------------------------------------------------------- #
# The double is honest
# --------------------------------------------------------------------------- #

def test_fake_status_server_matches_the_real_runtime_shapes():
    """Diff the fake's payloads against the REAL MoxieRuntime, so runtime drift fails
    here rather than quietly turning these suites into a test of themselves. Routes the
    fake serves from a real runtime are pinned on the key sets the normalizers read."""
    pytest.importorskip("paho.mqtt.client")
    import tempfile
    from moxie_sdk.store import JsonStore
    rt = bare_runtime()
    rt.robots[DEVICE].extra["telemetry"] = list(PACKETS)

    real = rt.status_snapshot()
    fake = _snapshot({}, {})
    assert set(fake) == set(real), "status snapshot top-level keys drifted"
    assert set(fake["robots"][0]) == set(real["robots"][0]), "robot record keys drifted"

    real_t = rt.telemetry_view(DEVICE, limit=5)
    fake_t, code = telemetry(DEVICE, 5)
    assert code == 200 and set(fake_t) == set(real_t)
    assert set(fake_t["summary"]) == set(real_t["summary"])
    assert fake_t["summary"]["by_event"] == real_t["summary"]["by_event"]
    fake_missing, code = telemetry("d_nope", 20)
    assert code == 404 and set(fake_missing) == set(rt.telemetry_view("d_nope"))

    # the fleet-config seam: same endpoint, `scope=fleet`, same sanitizer
    assert isinstance(rt.fleet_config(), dict)
    assert set(rt.effective_config(DEVICE)) == set(
        merge_config_layers(rt.fleet_config(), {}))

    # /safety, /memory, /telehealth: real runtime behind the fake — guard the
    # unknown-device shape and the keys the cards read
    assert rt.safety_view("d_nope")["ok"] is False
    assert rt.acknowledge_safety("d_nope", "sfe-nope")["ok"] is False
    assert rt.memory_view("d_nope")["ok"] is False
    assert rt.telehealth_view("d_nope")["ok"] is False
    rt._allow_unverified_bots = True     # every telehealth verb checks the permit gate
    assert set(rt.telehealth_view(DEVICE)) == {
        "ok", "device_id", "enabled", "online", "session_id", "in_session",
        "state", "state_at", "in_bedtime", "transcript", "moods", "max_intensity"}
    assert set(rt.memory_view(DEVICE)) == {"ok", "device_id", "namespaces", "bytes",
                                           "writes_allowed", "policy"}

    # /content: the key set each normalizer reads, on a fresh store
    rt.store = JsonStore(root=tempfile.mkdtemp())
    install_content(rt)
    assert set(rt.content_view()) == {"ok", "items", "packs", "counts", "undo_available",
                                      "undo_label", "max_bytes", "pack_format"}
    assert set(rt.content_view()["items"][0]) == {
        "id", "kind", "key", "name", "source_version", "origin", "pack_id",
        "imported_at", "local_edited", "has_code", "warnings", "pii"}
    exported = rt.content_export([CONV], name="Shapes", pack_id="shapes")
    assert set(exported) == {"pack_format", "id", "name", "details", "author",
                             "pack_version", "created_at", "generator", "items",
                             "signatures", "digest"}
    reviewed = rt.content_review(json.dumps(exported))
    assert set(reviewed) == {"ok", "pack", "digest", "expect_digest", "warnings",
                             "items", "accept", "counts"}
    assert set(reviewed["items"][0]) >= {
        "id", "kind", "key", "name", "state", "label", "default", "local_edited",
        "source_version", "installed_version", "origin", "pack_id", "warnings",
        "reasons", "diff"}
    assert set(rt.content_import(exported, [CONV])) == {
        "ok", "digest", "pack", "applied", "replaced", "skipped", "count", "reload",
        "undo_available"}
    assert set(rt.content_undo()) == {"ok", "restored", "reload", "label",
                                      "undo_available"}

    # the per-item memory seam: an id-carrying view, an item erase, and an edit
    rt.store = JsonStore(root=tempfile.mkdtemp())
    rt.memory_store().merge(DEVICE, "mchat", {"facts": ["has a dog", "likes red"]},
                            provenance={"module_id": "MCHAT", "turns": 2, "at": SEED_AT},
                            meta={"summarized_through": 4}, now=SEED_AT)
    view_ns = rt.memory_view(DEVICE)["namespaces"]["mchat"]
    assert set(view_ns) == {"data", "provenance", "meta"}
    one, two = [f["id"] for f in view_ns["data"]["facts"]]
    assert view_ns["meta"] == {"summarized_through": 4}
    edited = rt.edit_memory_item(DEVICE, "mchat", one, "has a beagle")
    assert set(edited) >= {"edited", "namespace", "item", "namespaces"}
    assert edited["namespaces"]["mchat"]["data"]["facts"][0]["pinned"] is True
    dropped = rt.erase_memory(DEVICE, "mchat", two)
    assert set(dropped) >= {"erased", "namespace", "item", "namespaces"}
    assert [f["text"] for f in
            dropped["namespaces"]["mchat"]["data"]["facts"]] == ["has a beagle"]
    erased = rt.erase_memory(DEVICE)
    assert erased["ok"] is True and set(erased) >= {"erased", "namespace", "namespaces"}

    # /schedule IS recorded, so diff it against the live planner (which re-plans when
    # nothing is stored — the path a parent hits before the robot pulls its day)
    real_s = rt.schedule_view(DEVICE)
    fake_s, code = schedule(DEVICE)
    assert code == 200 and set(fake_s) == set(real_s), "schedule view keys drifted"
    assert set(fake_s["schedule"]) <= set(real_s["schedule"])
    assert set(fake_s["explanations"][0]) == set(real_s["explanations"][0])
    assert set(fake_s["inputs"]) == set(real_s["inputs"]), "inputs summary keys drifted"
    assert set(fake_s["inputs"]["telemetry"]) == set(real_s["inputs"]["telemetry"])
    assert real_s["inputs"]["telemetry"]["carries_module_signal"] is False
    assert set(fake_s["inputs"]["planned"]) == set(real_s["inputs"]["planned"])
    fake_missing, code = schedule("d_nope")
    assert code == 404 and set(fake_missing) == set(rt.schedule_view("d_nope"))


# --------------------------------------------------------------------------- #
# The console page itself: ids and tokens the JS drives must exist
# --------------------------------------------------------------------------- #
# No browser harness covers `server/static/`, so the classic failure is a silently dead
# card: JS reaches for an id the HTML no longer has, or never reads a payload key.

def test_the_console_serves_the_ids_the_insights_and_device_code_drives(client):
    html = static(client, "/index.html")
    js = console_js_served(client)
    for element_id in ("robot-insights", "btn-wake", "btn-reboot", "dev-status"):
        assert f'id="{element_id}"' in html, f"#{element_id} vanished from the page"
        assert f"'#{element_id}'" in js or f"#{element_id}" in js, \
            f"#{element_id} is in the HTML but nothing drives it"


def test_the_reboot_button_ships_disabled_in_the_markup(client):
    """Before any JS runs, the button must not look like a working control."""
    row = [ln for ln in static(client, "/index.html").splitlines()
           if 'id="btn-reboot"' in ln]
    assert row and "disabled" in row[0], "the Reboot button is offered as if it worked"


def test_the_voice_card_reads_the_environments_pin(client):
    assert "pin_notes" in console_js_served(client), \
        "the voice card never reads the environment's pin"


@pytest.mark.parametrize("what, tokens, classes", [
    ("insights week", ("weekBars", "t.history", "ret.packets", "ret.days",
                       "tot.first_day", "t.persisted"),
     (".tweek", ".tbar", ".tday.zero", ".tnote")),
    ("connection strip", ("/local/connection", "connectionStrip", "c.verdict", "c.gaps",
                          "e.waited_s", "roster"),
     (".connstrip", ".connstrip.warn", ".connstrip.down")),
])
def test_the_insights_card_renders_its_durable_payload(client, what, tokens, classes):
    """A card that fetched the payload and ignored it would pass every API test above."""
    js = console_js_served(client)
    for token in tokens:
        assert token in js, f"the {what} never reads {token}"
    css = static(client, "/style.css")
    for cls in classes:
        assert cls in css, f"{cls} has no styling, so the {what} will not render"


def test_the_insights_card_renders_the_connection_strip_with_no_robot(client):
    """The strip earns its place in the no-robot branch, so it must be built before it."""
    js = console_js_served(client)
    assert js.index("connectionStrip") < js.index("Insights: no robot connected"), \
        "the strip is built after the no-robot early return, so it never shows there"


# --------------------------------------------------------------------------- #
# The appliance's own broker connection (`GET /local/connection`)
# --------------------------------------------------------------------------- #
# `sim/tools/hardening_p1_mutation_check.py` selects these by name; keep the names.

def test_the_connection_view_carries_the_live_state_and_the_history(client, supervisor):
    """"Is it down now" and "has it been flapping" are different questions."""
    supervisor.runtime._record_conn("connect")
    supervisor.runtime._record_conn("disconnect", reason="connection lost")
    supervisor.runtime._record_conn("connect", gap_s=4.5)

    c = client.get("/local/connection").json()
    assert c["ok"] is True
    assert c["count"] == 3
    assert c["outages"] == 1
    assert c["gaps"]["count"] == 1 and c["gaps"]["max_s"] == 4.5
    assert [e["kind"] for e in c["events"]][0] == "connect"      # newest first
    assert supervisor.conn_queries, "the console never asked the supervisor"


def test_every_row_kind_gets_a_sentence_a_parent_can_read(client, supervisor):
    """A card that rendered the raw `kind` would show a parent the wire vocabulary."""
    from moxie_sdk import conn_telemetry as conn
    for kind in conn.KINDS:
        supervisor.runtime._record_conn(kind)
    labels = {e["kind"]: e["label"] for e in client.get("/local/connection").json()["events"]}
    for kind in conn.KINDS:
        assert labels.get(kind), f"{kind} has no sentence"
        assert labels[kind] != kind, f"{kind} renders as its own wire name"


def test_a_supervisor_that_is_down_says_so_rather_than_looking_quiet():
    """"No robot connected" and "the appliance lost its broker" need different actions."""
    from moxie_server.fleet import normalize_connection
    view = normalize_connection(None)
    assert view["ok"] is False
    assert view["connected"] is False
    assert view["error"] == "supervisor not reachable"
    assert view["events"] == [] and view["count"] == 0
    # …and no verdict is manufactured for a supervisor it never reached
    assert view["verdict"] == "" and view["state"] == ""


def test_recovered_is_not_rendered_as_healthy():
    """Up-right-now after nine drops is not the same as never dropped."""
    from moxie_server.fleet import normalize_connection
    steady = normalize_connection({"ok": True, "connected": True,
                                   "health": {"state": "steady"}})
    recovered = normalize_connection({"ok": True, "connected": True,
                                      "health": {"state": "recovered", "outages": 9}})
    assert steady["verdict"] != recovered["verdict"]
    assert "not been the whole time" in recovered["verdict"]
    assert recovered["connected"] is True, "it IS up — the verdict is about its history"


def test_a_row_without_a_gap_does_not_render_a_zero_second_outage():
    """The absent-key contract, carried through: `gap_s: 0.0` would read as an outage."""
    from moxie_server.fleet import normalize_connection_event
    assert "gap_s" not in normalize_connection_event({"kind": "connect", "at": 1})
    assert normalize_connection_event({"kind": "connect", "at": 1, "gap_s": 0.0})["gap_s"] == 0.0
    assert "waited_s" not in normalize_connection_event({"kind": "connect", "at": 1})


def test_the_connection_view_survives_a_payload_from_a_newer_runtime():
    """A console that raised on an unfamiliar row would go blank at the wrong upgrade."""
    from moxie_server.fleet import normalize_connection
    view = normalize_connection({"ok": True, "connected": True,
                                 "events": [None, "nope", {"kind": "brand_new", "at": 5}],
                                 "summary": "not a dict", "health": []})
    assert view["ok"] is True
    assert view["events"][-1]["label"] == "brand new"
