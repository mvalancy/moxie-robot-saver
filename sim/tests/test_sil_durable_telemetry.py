"""Durable telemetry and the console's device buttons against a REAL running supervisor.

Needs real processes: telemetry must survive killing `mqtt/run.py` (a second in-process
runtime would not catch a module cache or an `atexit` flush); `LoggingPolicy` is judged
from what is on DISK; `wakeup` is witnessed by a real subscriber. Boots a broker.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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
        self.subscribed = threading.Event()
        self._pending_subs: set = set()
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=device_id)
        self._c.on_message = self._on_message
        self._c.on_subscribe = self._on_subscribe

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        self._pending_subs.discard(mid)
        if not self._pending_subs:
            self.subscribed.set()

    def _on_message(self, c, u, msg):
        try:
            body = json.loads(msg.payload.decode())
        except Exception:
            body = msg.payload
        self.received.append((msg.topic, body))

    def connect(self) -> "Robot":
        self._c.connect("127.0.0.1", self.port, keepalive=30)
        self._c.loop_start()
        self._pending_subs = {self._c.subscribe(f"/devices/{self.device_id}/commands/#")[1]}
        return self

    def announce(self, **status):
        """`/devices/<id>/state` — makes this robot visible and carries the only OTA facts
        the protocol gives us. Waits for the SUBACK explicitly rather than relying on
        packet ordering (the lost-config race)."""
        body = {"robot_firmware_version": FIRMWARE, "battery_level": 88,
                "audio_volume": 0.5, "wifi_ssid": "Lab", "mode": "normal",
                "ota_reboot_required": False}
        body.update(status)
        assert self.subscribed.wait(30), \
            f"{self.device_id}: the broker never acknowledged our subscription"
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
    """Poll a real distributed system without sleeping blind; returns the truthy value.
    `time.time()` is used only as a deadline (duration), never as a date."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


def _on_disk(name: str, data_dir: str, device_id: str = DEVICE):
    p = os.path.join(data_dir, "robots", device_id, f"telemetry_{name}.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _packets(data_dir: str, device_id: str = DEVICE):
    return _on_disk("packets", data_dir, device_id)


def _daily(data_dir: str, device_id: str = DEVICE):
    return _on_disk("daily", data_dir, device_id)


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


#: The three events every test below is *about*. Named once so the assertions and the
#: fixture that sends them cannot drift apart.
EVENTS = ("module_started", "module_finished", "battery_report")


@pytest.fixture(scope="module")
def history(stack, robot):
    """Three telemetry packets ON DISK for DEVICE, as a fixture so any subset of this file
    runs the same (run alone, a later test otherwise failed as a bare `assert 0 == 3` that
    looked like broken durability). Idempotent: sends nothing if the ring already has three.
    """
    if len(_packets(stack.data_dir) or []) < 3:
        for i, name in enumerate(EVENTS):
            robot.telemetry(name, PAYLOAD, session_id=f"s-{i}")
    return _wait(lambda: (_packets(stack.data_dir) or None)
                 if len(_packets(stack.data_dir) or []) >= 3 else None,
                 what="three envelopes in telemetry_packets.json")


@pytest.fixture(scope="module")
def restarted(stack, robot, history):
    """The robot gone and a NEW `mqtt/run.py` over the same MOXIE_DATA_DIR — module-scoped,
    so the restart happens once and no test depends on another having run first."""
    robot.close()                                   # nothing to re-populate RAM from
    sup = stack.restart_supervisor()
    return sup


# ================================================================ the restart ==
def test_the_supervisor_writes_both_collections_to_disk(stack, history):
    """Three packets in, two files on disk — the ring and the daily roll-up."""
    ring = history
    assert [p["event_name"] for p in ring] == list(EVENTS)
    daily = _daily(stack.data_dir)
    assert daily and daily["total"] == 3, daily
    day = sorted(daily["days"])[-1]
    assert daily["days"][day]["count"] == 3
    assert daily["days"][day]["by_event"]["module_started"] == 1


def test_the_first_supervisor_serves_the_history_it_just_stored(stack, history):
    v = _telemetry_view(stack.supervisor)
    assert v["ok"] is True and v["connected"] is True
    assert v["policy"] == "NO_MEDIA" and v["persisted"] is True
    assert v["summary"]["count"] == 3
    assert v["totals"]["total"] == 3
    assert len(v["history"]) == 7 and v["history"][-1]["count"] == 3


def test_telemetry_survives_a_real_supervisor_restart(stack, restarted):
    """The claim: kill `mqtt/run.py`, disconnect the robot, start a new supervisor over the
    same `MOXIE_DATA_DIR`, and ask what happened. The robot is NOT reconnected — a history
    that appears only when the device re-announces is a cache.
    """
    sup = restarted
    snap = http_json(f"{_status_url(sup)}/status")
    assert not any(r["device_id"] == DEVICE for r in snap.get("robots", [])), \
        "the robot must be absent for this to be a durability proof"

    v = _telemetry_view(sup)
    assert v["ok"] is True, v
    assert v["connected"] is False, "this robot is not on the broker; say so"
    assert v["summary"]["count"] == 3, v["summary"]
    # `summarize_events` returns the newest first — the order the card renders.
    assert [e["event_name"] for e in v["events"]] == \
        ["battery_report", "module_finished", "module_started"]
    assert v["totals"]["total"] == 3 and v["totals"]["days_kept"] == 1
    assert v["history"][-1]["count"] == 3
    assert v["retention"]["packets"] >= 3


def test_the_buffer_is_a_cache_hydrated_on_first_touch(stack, restarted):
    """After a restart the RAM buffer is empty; a reconnecting robot's status row must still
    say 3 (hydrated from the ring), or the fleet card disagrees with the insights card."""
    sup = restarted
    r = Robot(stack.port).connect()
    try:
        r.announce()
        seen = {}

        def _populated():
            row = next((x for x in http_json(f"{_status_url(sup)}/status").get("robots", [])
                        if x["device_id"] == DEVICE), None)
            seen["row"] = row
            return row if row and row.get("firmware") else None

        try:
            row = _wait(_populated, what="the reconnected robot's row with its firmware")
        except AssertionError:
            # name which half failed: never seen at all vs. seen but /state not ingested
            tail = os.linesep.join(sup.text().splitlines()[-15:])
            raise AssertionError(f"row={seen.get('row')!r}\nsupervisor tail:\n{tail}") from None
        assert row["telemetry_count"] == 3, (
            f"ring on disk holds {len(_packets(stack.data_dir) or [])}; a 0 means "
            f"`_telemetry_buffer` cached an empty read instead of hydrating. row={row!r}")
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
            # NO_DATA: nothing at all. Barrier, not a sleep: the supervisor ingests events
            # synchronously and in order per connection, so once a later /state from the
            # same robot is visible, the probe has been handled.
            r.announce(battery_level=42)
            _wait(lambda: any(x["device_id"] == device and x.get("battery_level") == 42
                              for x in http_json(f"{_status_url(sup)}/status")
                              .get("robots", [])),
                  what="the barrier /state to be ingested")
            assert _packets(stack.data_dir, device) is None, \
                "NO_DATA wrote a telemetry ring"
            assert _daily(stack.data_dir, device) is None, \
                "NO_DATA wrote a daily roll-up"
            v = http_json(f"{_status_url(sup)}/telemetry?device_id={device}")
            assert v["persisted"] is False and v["totals"]["total"] == 0
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
        # The ring and the roll-up are separate writes; wait for the roll-up field itself
        # (a wrong total still returns and fails; a missing one times out with a reason).
        total = _wait(lambda: (_daily(stack.data_dir, device) or {}).get("total"),
                      what=f"the daily roll-up's total under {expected}")
        assert total == 1, _daily(stack.data_dir, device)
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
    from helpers_console import console_app
    TestClient, main = console_app(os.path.join(stack.log_dir, "console-test.db"),
                                   f"{_status_url(stack.supervisor)}/status")
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
    assert body["ok"] is False and body["supported"] is False
    assert body["error"] == "unsupported" and body["reason"]
    assert "power-and-system-events.md" in body["evidence"]
    # Barrier: a wakeup published after it arrives in order behind anything reboot sent.
    assert console.post(f"/api/robots/{rid}/wakeup", headers=auth).json()["published"]
    _wait(lambda: listener.received, what="the barrier wakeup")
    assert [m[0] for m in listener.received if "/commands/" in m[0]] == \
        [f"/devices/{DEVICE}/commands/wakeup"], \
        "reboot must not publish a guessed command at a child's robot"


def test_ota_status_reports_the_firmware_the_robot_itself_sent(console, paired,
                                                               listener):
    """The version comes off `/devices/<id>/state` — the robot's own `RobotStatus` —
    through the supervisor's snapshot and out of the console. Never `up_to_date`: this
    appliance serves no `api/ota` and is in no position to claim there is no newer build."""
    auth, rid = paired
    body = console.get(f"/api/robots/{rid}/ota_status", headers=auth).json()
    assert body["status"] != "up_to_date"
    assert body["version"] == FIRMWARE, body
    assert body["ota_reboot_required"] is False
    assert body["ota_server"] is False and body["supported"] is False
    assert body["note"]
