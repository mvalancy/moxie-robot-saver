"""Add to my account against a REAL broker, the REAL supervisor and a real MQTT client.

`test_robot_claim.py` pins the claim route over a hand-built supervisor double. This is the
same path with `mqtt/run.py` behind it, its allowlist closed as on a parent's appliance
(`MOXIE_ALLOW_UNVERIFIED_BOTS=0`), and a paho client wearing a `d_<uuid>` id standing in
for the robot: it is pending and gets the child-free config; the claim lists it as
unclaimed, puts it on the account and permits it, and the next config carries the child;
Wake reaches it; Unpair sends it back to pending and the child-free config.

A paho client is not a Moxie. What a physical robot does with these messages is still
unmeasured (`docs/guides/bench-runbook.md`).
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

mqtt = pytest.importorskip("paho.mqtt.client", reason="the stand-in robot needs paho")
pytest.importorskip("fastapi", reason="the console app")
pytest.importorskip("httpx", reason="fastapi's TestClient")

from helpers_runtime import http_json                     # noqa: E402
from helpers_stack import Stack, broker_available         # noqa: E402

pytestmark = pytest.mark.skipif(not broker_available(),
                                reason="no mosquitto binary and no runnable docker")

DEVICE = "d_00000000-0000-4000-8000-00000000b001"


class StandIn:
    """A paho client with the robot's `d_<uuid>` client id: the supervisor's broker-log
    watch sees it connect, and it records every `/config` and command it is sent."""

    def __init__(self, port: int):
        self.port = port
        self.received: list = []
        self.acked = threading.Event()
        self._waiting: set = set()
        self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE)
        self._c.on_message = self._on_message
        self._c.on_subscribe = self._on_subscribe

    def _on_message(self, c, u, msg):
        try:
            body = json.loads(msg.payload.decode())
        except Exception:                     # a binary command is kept as it came
            body = msg.payload
        self.received.append((msg.topic, body))

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        self._waiting.discard(mid)
        if not self._waiting:
            self.acked.set()

    def start(self) -> "StandIn":
        self._c.connect("127.0.0.1", self.port, keepalive=30)
        self._c.loop_start()
        self._waiting = {self._c.subscribe(f"/devices/{DEVICE}/{t}")[1]
                         for t in ("config", "commands/#")}
        assert self.acked.wait(30), "the broker never acknowledged the subscriptions"
        self._c.publish(f"/devices/{DEVICE}/state", json.dumps(
            {"robot_firmware_version": "24.10.803", "wifi_ssid": "Bench"}), qos=1).wait_for_publish(5)
        return self

    def configs(self) -> list:
        return [b for t, b in self.received if t == f"/devices/{DEVICE}/config"]

    def stop(self):
        self._c.loop_stop()
        self._c.disconnect()


def _wait(predicate, timeout: float = 20.0, what: str = "condition"):
    """Poll a real distributed system; `time` here is a deadline, never a date."""
    import time
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with Stack(str(tmp_path_factory.mktemp("claim-stack")),
               env={"MOXIE_ALLOW_UNVERIFIED_BOTS": "0"}) as s:
        yield s


@pytest.fixture(scope="module")
def console(stack, tmp_path_factory):
    from helpers_console import console_app
    url = f"http://127.0.0.1:{stack.supervisor.status_port}/status"
    TestClient, main = console_app(tmp_path_factory.mktemp("claim-console") / "c.db", url)
    with TestClient(main.app) as c:
        yield c


def test_a_pending_robot_is_added_served_woken_and_unpaired_through_the_real_supervisor(
        stack, console):
    status = f"http://127.0.0.1:{stack.supervisor.status_port}"
    robot = StandIn(stack.port).start()
    try:
        _wait(lambda: DEVICE in (http_json(f"{status}/permits").get("pending") or []),
              what="the stand-in pending on the real supervisor")
        first = _wait(robot.configs, what="the first config push")[-1]
        assert first["pairing_status"] == "unpairing" and "child_pii" not in first

        tok = console.post("/local/quicklogin", json={"email": "sil@claim.lan"}).json()["token"]
        auth = {"Authorization": f"Bearer {tok}"}
        assert console.get("/local/state", headers=auth).json()["unclaimed"] == [DEVICE]

        pushed = len(robot.configs())
        claim = console.post(f"/local/robots/{DEVICE}/claim", headers=auth)
        assert claim.status_code == 200, claim.text
        assert claim.json()["created"] is True and claim.json()["permitted"] is True
        view = http_json(f"{status}/permits")
        assert [(p["device_id"], p["label"]) for p in view["permits"]] == [
            (DEVICE, "added to a parent account")]
        assert view["pending"] == []
        served = _wait(lambda: robot.configs()[pushed:], what="a config push after the claim")
        assert served[-1]["pairing_status"] == "paired" and "child_pii" in served[-1]
        assert [r["pending"] for r in console.get("/local/fleet").json()["robots"]] == [False]
        assert console.get("/local/state", headers=auth).json()["unclaimed"] == []

        rid = claim.json()["robot_id"]
        wake = console.post(f"/api/robots/{rid}/wakeup", headers=auth).json()
        assert wake["published"] is True and wake["resolved_by"] == "record"
        _wait(lambda: (f"/devices/{DEVICE}/commands/wakeup", {"command": "wakeup"})
              in robot.received, what="the wakeup on the broker")

        pushed = len(robot.configs())
        gone = console.delete(f"/api/robots/{rid}", headers=auth).json()
        assert gone["unpaired"] is True and gone["access"]["revoked"] is True
        back = _wait(lambda: robot.configs()[pushed:], what="a config push after the unpair")
        assert back[-1]["pairing_status"] == "unpairing" and "child_pii" not in back[-1]
        assert http_json(f"{status}/permits")["pending"] == [DEVICE]
    finally:
        robot.stop()
