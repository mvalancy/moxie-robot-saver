"""Durable telemetry and the three honest buttons — against a REAL running supervisor.

`test_telemetry.py` unit-tests the pure roll-up, `test_telemetry_runtime.py` drives a
`MoxieRuntime` object, and `test_console_roundtrip.py` drives the console against a
hand-written status server. All three are fixtures. Two of PR #55's claims are not
things a fixture can establish:

1. **"Telemetry survives a restart."** A second `MoxieRuntime` built in the same
   interpreter proves the hydration *code path* and nothing about durability — a
   module-level cache, a store the first runtime kept open, or an `atexit` flush would
   all survive it silently. So this file boots a real mosquitto, a real `mqtt/run.py`,
   sends telemetry from a real paho robot, **kills the supervisor process**, starts a
   new one over the same `MOXIE_DATA_DIR`, and reads the history back through the new
   process's own status HTTP server with the robot not reconnected.
2. **"`LoggingPolicy` is the gate and it fails closed."** Being wrong here is a privacy
   incident, not a bug, so all three values are exercised against a *running*
   supervisor — the policy set the way a parent sets it (`POST /config`), the packet
   sent the way a robot sends it (`/devices/<id>/events/telemetry`), and the verdict
   read off **disk**, not off an API that could be reporting its own intentions.

Then the three console endpoints that used to report success for nothing, through the
same real supervisor with the console app in-process: `wakeup` must actually publish
(asserted by a real MQTT subscriber, not a recorded fake), `reboot` must be a 501 that
says why, and `ota_status` must return the firmware the robot itself reported.

Named `test_sil_*` on purpose: it boots a broker, so `-k "not test_sil"` keeps it out of
the tiers that promise to report in seconds, and the fast tier's full-suite step runs it.
Skips cleanly with no mosquitto binary and no docker.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "server"))

mqtt = pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")

from helpers_runtime import http_json                     # noqa: E402
from helpers_stack import Stack, broker_available         # noqa: E402

pytestmark = pytest.mark.skipif(not broker_available(),
                                reason="no mosquitto binary and no runnable docker")

DEVICE = "d_00000000-0000-4000-8000-0000dead0001"
FIRMWARE = "24.10.803"

#: `event_data` as a robot sends it — base64 of an opaque blob. `Cloud.proto` declares
#: the field `bytes` and our corpus recovers no payload vocabulary, which is exactly why
#: NO_MEDIA has to withhold *every* one of them rather than the ones it recognises.
PAYLOAD = b"\x01\x02opaque-blob\xff"


# --------------------------------------------------------------------- helpers --
class Robot:
    """A real paho client wearing a `d_<uuid>` client id — what the supervisor's broker
    log watch (`CONNECT_RE`) and its `/devices/+/state` subscription actually see."""

    def __init__(self, port: int, device_id: str = DEVICE):
        self.device_id = device_id
        self.port = port
        self.received: list = []
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=device_id)
        self._c.on_message = self._on_message

    def _on_message(self, c, u, msg):
        try:
            body = json.loads(msg.payload.decode())
        except Exception:
            body = msg.payload
        self.received.append((msg.topic, body))

    def connect(self) -> "Robot":
        self._c.connect("127.0.0.1", self.port, keepalive=30)
        self._c.loop_start()
        self._c.subscribe(f"/devices/{self.device_id}/commands/#")
        return self

    def announce(self, **status):
        """`/devices/<id>/state` — the RobotStatus that makes this robot visible and
        carries the only OTA facts the recovered protocol gives us."""
        body = {"robot_firmware_version": FIRMWARE, "battery_level": 88,
                "audio_volume": 0.5, "wifi_ssid": "Lab", "mode": "normal",
                "ota_reboot_required": False}
        body.update(status)
        self._c.publish(f"/devices/{self.device_id}/state", json.dumps(body), qos=1).wait_for_publish(5)

    def telemetry(self, event_name: str, event_data: bytes = b"", **kw):
        """One `Packet` on `/devices/<id>/events/telemetry`, built by the SDK the client
        side really uses (`moxie_sdk.telemetry.build_packet`)."""
        from moxie_sdk.telemetry import build_packet
        pkt = build_packet(event_name, event_data, moxie_id=self.device_id, **kw)
        self._c.publish(f"/devices/{self.device_id}/events/telemetry",
                        json.dumps(pkt), qos=1).wait_for_publish(5)
        return pkt

    def close(self):
        try:
            self._c.loop_stop()
            self._c.disconnect()
        except Exception:
            pass


def _wait(predicate, timeout: float = 20.0, what: str = "condition"):
    """Poll a real distributed system without sleeping blind. Returns the truthy value."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


def _packets(data_dir: str, device_id: str = DEVICE):
    p = os.path.join(data_dir, "robots", device_id, "telemetry_packets.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _daily(data_dir: str, device_id: str = DEVICE):
    p = os.path.join(data_dir, "robots", device_id, "telemetry_daily.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _status_url(sup) -> str:
    return f"http://127.0.0.1:{sup.status_port}"


def _telemetry_view(sup, device_id: str = DEVICE, **q):
    qs = "".join(f"&{k}={v}" for k, v in q.items())
    return http_json(f"{_status_url(sup)}/telemetry?device_id={device_id}{qs}")


def _set_policy(sup, value, device_id: str = DEVICE):
    """Set `logging_policy` the way a parent does — the console's `POST /config`, which
    is the runtime's own `sanitize_config_overrides` + `update_config`."""
    out = http_json(f"{_status_url(sup)}/config?device_id={device_id}",
                    method="POST", body={"logging_policy": value})
    assert out.get("ok") is True, out
    return out


# ---------------------------------------------------------------- the fixtures --
@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """Broker + `mqtt/run.py`, on free ports, with its data under a scratch dir."""
    logs = tmp_path_factory.mktemp("durable-telemetry")
    with Stack(str(logs)) as s:
        yield s


@pytest.fixture(scope="module")
def robot(stack):
    r = Robot(stack.port).connect()
    r.announce()
    _wait(lambda: any(x["device_id"] == DEVICE
                      for x in http_json(f"{_status_url(stack.supervisor)}/status")
                      .get("robots", [])),
          what="the supervisor to see the robot")
    yield r
    r.close()


# ================================================================ the restart ==
def test_the_supervisor_writes_both_collections_to_disk(stack, robot):
    """Three packets in, two files on disk — the ring and the daily roll-up."""
    for i, name in enumerate(("module_started", "module_finished", "battery_report")):
        robot.telemetry(name, PAYLOAD, session_id=f"s-{i}")
    ring = _wait(lambda: (_packets(stack.data_dir) or None) if
                 len(_packets(stack.data_dir) or []) >= 3 else None,
                 what="three envelopes in telemetry_packets.json")
    assert [p["event_name"] for p in ring] == ["module_started", "module_finished",
                                               "battery_report"]
    daily = _daily(stack.data_dir)
    assert daily and daily["total"] == 3, daily
    day = sorted(daily["days"])[-1]
    assert daily["days"][day]["count"] == 3
    assert daily["days"][day]["by_event"]["module_started"] == 1


def test_the_first_supervisor_serves_the_history_it_just_stored(stack, robot):
    v = _telemetry_view(stack.supervisor)
    assert v["ok"] is True and v["connected"] is True
    assert v["policy"] == "NO_MEDIA" and v["persisted"] is True
    assert v["summary"]["count"] == 3
    assert v["totals"]["total"] == 3
    assert len(v["history"]) == 7 and v["history"][-1]["count"] == 3


def test_telemetry_survives_a_real_supervisor_restart(stack, robot):
    """**The claim.** Kill `mqtt/run.py`, disconnect the robot, start a new supervisor
    over the same `MOXIE_DATA_DIR`, and ask it what happened last week.

    The robot is deliberately NOT reconnected: a parent asking what Moxie did should get
    an answer whether or not the robot is on the broker right now, and a "durable"
    history that only appears once the device re-announces itself is a cache, not a
    history.
    """
    robot.close()                                   # nothing to re-populate RAM from
    sup = stack.restart_supervisor()
    print(f"\n[restart] new supervisor status port {sup.status_port}")

    snap = http_json(f"{_status_url(sup)}/status")
    assert not any(r["device_id"] == DEVICE for r in snap.get("robots", [])), \
        "the robot must be absent for this to be a durability proof"

    v = _telemetry_view(sup)
    print("[restart] GET /telemetry →",
          json.dumps({k: v[k] for k in ("ok", "connected", "policy", "persisted",
                                        "totals")}, sort_keys=True))
    assert v["ok"] is True, v
    assert v["connected"] is False, "this robot is not on the broker; say so"
    assert v["summary"]["count"] == 3, v["summary"]
    # `summarize_events` returns the newest first — the order the card renders.
    assert [e["event_name"] for e in v["events"]] == \
        ["battery_report", "module_finished", "module_started"]
    assert v["totals"]["total"] == 3 and v["totals"]["days_kept"] == 1
    assert v["history"][-1]["count"] == 3
    assert v["retention"]["packets"] >= 3


def test_the_buffer_is_a_cache_hydrated_on_first_touch(stack):
    """`telemetry_count` in the status snapshot is the RAM buffer's length. After a
    restart the new process has an empty buffer, so a reconnecting robot must show 3 —
    the hydration path (`_telemetry_buffer`) reading the ring off disk. A 0 here would
    mean the console's fleet card silently disagrees with its own insights card."""
    sup = stack.supervisor
    r = Robot(stack.port).connect()
    try:
        r.announce()
        row = _wait(lambda: next((x for x in http_json(f"{_status_url(sup)}/status")
                                  .get("robots", []) if x["device_id"] == DEVICE), None),
                    what="the reconnected robot in /status")
        print(f"[hydration] telemetry_count={row['telemetry_count']}")
        assert row["telemetry_count"] == 3, row
        assert row["firmware"] == FIRMWARE
    finally:
        r.close()


# =========================================================== the privacy gate ==
def _fresh_device(stack, suffix: str) -> str:
    """A per-policy device id so one policy's disk state cannot be read as another's."""
    return f"d_00000000-0000-4000-8000-0000beef{suffix}"


@pytest.mark.parametrize("policy,expected", [
    (0, "NO_DATA"),
    (1, "NO_MEDIA"),
    (2, "FULL"),
])
def test_the_logging_policy_gate_holds_against_a_running_supervisor(stack, policy,
                                                                    expected):
    """All three values, end to end on the real appliance: parent sets the policy over
    HTTP, robot publishes a Packet with a payload over MQTT, and the verdict is read off
    **disk** — never off the API that might merely be describing its intentions."""
    sup = stack.supervisor
    device = _fresh_device(stack, f"{policy:04d}")
    r = Robot(stack.port, device_id=device).connect()
    try:
        r.announce()
        _wait(lambda: any(x["device_id"] == device
                          for x in http_json(f"{_status_url(sup)}/status").get("robots", [])),
              what=f"{device} to be seen")
        _set_policy(sup, policy, device)
        view_before = http_json(f"{_status_url(sup)}/telemetry?device_id={device}")
        assert view_before["policy"] == expected, view_before

        r.telemetry("policy_probe", PAYLOAD)

        if policy == 0:
            # NO_DATA: nothing at all. Give the write a chance to happen before
            # asserting that it did not — a race here would pass for the wrong reason.
            time.sleep(1.5)
            assert _packets(stack.data_dir, device) is None, \
                "NO_DATA wrote a telemetry ring"
            assert _daily(stack.data_dir, device) is None, \
                "NO_DATA wrote a daily roll-up"
            v = http_json(f"{_status_url(sup)}/telemetry?device_id={device}")
            assert v["persisted"] is False and v["totals"]["total"] == 0
            print(f"[policy NO_DATA] on-disk: none · view={json.dumps(v['totals'])}")
            return

        ring = _wait(lambda: _packets(stack.data_dir, device) or None,
                     what=f"a stored envelope under {expected}")
        assert len(ring) == 1, ring
        row = ring[0]
        assert row["event_name"] == "policy_probe"
        if policy == 1:
            assert "event_data" not in row, "NO_MEDIA kept an opaque payload"
            assert row["event_data_withheld"] == "NO_MEDIA"
        else:
            import base64
            assert base64.b64decode(row["event_data"]) == PAYLOAD
            assert "event_data_withheld" not in row
        print(f"[policy {expected}] on-disk row: {json.dumps(row, sort_keys=True)[:220]}")
        assert (_daily(stack.data_dir, device) or {}).get("total") == 1
    finally:
        r.close()


# ====================================================== the three console buttons ==
@pytest.fixture(scope="module")
def console(stack):
    """The real console app in-process, pointed at the REAL supervisor's status server.

    `test_console_roundtrip.py` does this against a hand-written double; the point here
    is that the other end is `mqtt/run.py` with a live broker behind it, so "wakeup
    published" can be asserted by a subscriber instead of by a recorded fake.
    """
    pytest.importorskip("fastapi", reason="the console needs fastapi")
    pytest.importorskip("httpx", reason="the console's TestClient needs httpx")
    if "moxie_server.main" not in sys.modules:      # db.init() runs at import time
        os.environ["MOXIE_DB"] = os.path.join(stack.log_dir, "console-test.db")
    try:
        from fastapi.testclient import TestClient
        from moxie_server import main
    except Exception as e:                          # pynacl/segno/... absent
        pytest.skip(f"console app not importable: {e}")
    main.STATUS_URL = f"{_status_url(stack.supervisor)}/status"
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def paired(console):
    """An authenticated parent whose robot record remembers this file's MQTT device id."""
    from moxie_server import db
    tok = console.post("/local/quicklogin",
                       json={"email": "integration-3@local"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    me = console.get("/local/state", headers=auth).json()
    rid = db.new_id()
    db.ex("INSERT INTO robots(id,user_id,child_id,attributes,robot_setting,"
          "last_seen_at,created_at) VALUES(?,?,?,?,?,?,?)",
          (rid, me["user"]["id"], None,
           json.dumps({"name": "Moxie", "mqtt-device-id": DEVICE}),
           json.dumps({}), db.now_s(), db.now_s()))
    yield auth, rid
    db.ex("DELETE FROM robots WHERE id=?", (rid,))


@pytest.fixture()
def listener(stack):
    """A robot on the broker subscribed to its own command topics — the only witness
    that can tell "published" from "reported published"."""
    r = Robot(stack.port).connect()
    r.announce()
    _wait(lambda: any(x["device_id"] == DEVICE
                      for x in http_json(f"{_status_url(stack.supervisor)}/status")
                      .get("robots", [])), what="the robot to be seen again")
    r.received.clear()
    yield r
    r.close()


def test_wakeup_really_reaches_the_robot_over_the_broker(console, paired, listener):
    """Console → supervisor → mosquitto → the robot's own subscription. Asserted by the
    subscriber, so nothing between the button and the wire can be a stub."""
    auth, rid = paired
    r = console.post(f"/api/robots/{rid}/wakeup", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    print("\n[wakeup] console →", json.dumps({k: body.get(k) for k in
                                              ("published", "error", "topic",
                                               "acknowledged", "resolved_by", "note")}))
    assert body["published"] is True and body["error"] is None
    assert body["resolved_by"] == "record"
    assert body["topic"] == f"/devices/{DEVICE}/commands/wakeup"
    assert body["acknowledged"] is False, "nothing in the corpus acknowledges wakeup"
    got = _wait(lambda: [m for m in listener.received
                         if m[0].endswith("/commands/wakeup")] or None,
                what="the wakeup command on the broker")
    assert got[-1] == (f"/devices/{DEVICE}/commands/wakeup", {"command": "wakeup"}), got


def test_reboot_is_a_501_that_says_why_and_publishes_nothing(console, paired, listener):
    auth, rid = paired
    r = console.post(f"/api/robots/{rid}/reboot", headers=auth)
    assert r.status_code == 501, r.text
    body = r.json()
    print("[reboot] console →", json.dumps({k: body.get(k) for k in
                                            ("ok", "supported", "error", "reason",
                                             "evidence")}))
    assert body["ok"] is False and body["supported"] is False
    assert body["error"] == "unsupported" and body["reason"]
    assert "power-and-system-events.md" in body["evidence"]
    time.sleep(1.0)                                  # a guess would have arrived by now
    assert [m for m in listener.received if "/commands/" in m[0]] == [], \
        "reboot must not publish a guessed command at a child's robot"


def test_ota_status_reports_the_firmware_the_robot_itself_sent(console, paired,
                                                               listener):
    """The version comes off `/devices/<id>/state` — the robot's own `RobotStatus` —
    through the supervisor's snapshot and out of the console. Never `up_to_date`: this
    appliance serves no `api/ota` and is in no position to claim there is no newer build."""
    auth, rid = paired
    body = console.get(f"/api/robots/{rid}/ota_status", headers=auth).json()
    print("[ota_status] console →", json.dumps(body, sort_keys=True))
    assert body["status"] != "up_to_date"
    assert body["version"] == FIRMWARE, body
    assert body["ota_reboot_required"] is False
    assert body["ota_server"] is False and body["supported"] is False
    assert body["note"]
