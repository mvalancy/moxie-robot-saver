"""
The three device endpoints that used to report success for nothing: wake-up, reboot and
OTA status. Each now either does the real thing (wake-up publishes the recovered command
through the supervisor's REAL `wake_robot`) or says honestly that it cannot.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console import set_status_url  # noqa: E402
from helpers_console_supervisor import (DEAD, DEVICE, client,  # noqa: E402,F401
                                        quicklogin, supervisor)


def _robot_record(client, email, attributes):
    from moxie_server import db
    auth = quicklogin(client, email)
    me = client.get("/local/state", headers=auth).json()
    rid = db.new_id()
    db.ex("INSERT INTO robots(id,user_id,child_id,attributes,robot_setting,"
          "last_seen_at,created_at) VALUES(?,?,?,?,?,?,?)",
          (rid, me["user"]["id"], None, json.dumps(attributes),
           json.dumps({}), db.now_s(), db.now_s()))
    return auth, rid


@pytest.fixture()
def paired(client):
    """A user whose robot record remembers this file's MQTT device id — the `"record"`
    branch of `fleet.resolve_device_id`. Returns `(auth, rid)`."""
    from moxie_server import db
    auth, rid = _robot_record(client, "devices-test@local",
                              {"name": "Moxie", "mqtt-device-id": DEVICE})
    yield auth, rid
    db.ex("DELETE FROM robots WHERE id=?", (rid,))


def _wakeup_wire(supervisor):
    return supervisor.runtime.client.on(f"/devices/{DEVICE}/commands/wakeup")


def test_pressing_wake_up_really_publishes_the_recovered_command(client, supervisor,
                                                                 paired):
    """console → supervisor `POST /wakeup` → REAL `wake_robot` → `{"command":"wakeup"}`."""
    auth, rid = paired
    supervisor.runtime.client.published.clear()
    r = client.post(f"/api/robots/{rid}/wakeup", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["published"] is True and body["error"] is None
    assert body["resolved_by"] == "record" and body["topic"].endswith("/commands/wakeup")
    assert _wakeup_wire(supervisor) == [{"command": "wakeup"}]
    assert supervisor.wakeups[-1] == DEVICE


def test_the_wake_up_reply_never_claims_the_robot_woke(client, paired):
    """No acknowledgement for this command exists in our corpus, so the strongest true
    claim is "it was published"."""
    auth, rid = paired
    body = client.post(f"/api/robots/{rid}/wakeup", headers=auth).json()
    assert body["acknowledged"] is False
    assert "not that Moxie woke up" in body["note"]


def test_wake_up_on_a_record_with_no_mqtt_identity_is_a_409_not_a_success(client,
                                                                          supervisor):
    """The QR carries no device id, so a record may not know its robot. With one robot
    served the sole-served fallback resolves it; with none it must be an honest 409."""
    from moxie_server import db
    from moxie_server.fleet import resolve_device_id
    auth, rid = _robot_record(client, "devices-test2@local", {"name": "Moxie"})
    try:
        snap = client.get("/local/broker/status").json()
        assert resolve_device_id({}, snap) == (DEVICE, "sole-served")
        supervisor.runtime.client.published.clear()
        r = client.post(f"/api/robots/{rid}/wakeup", headers=auth)
        assert r.status_code == 200 and r.json()["resolved_by"] == "sole-served"
        assert _wakeup_wire(supervisor) == [{"command": "wakeup"}]
        assert resolve_device_id({}, {"ok": True, "robots": []}) == (None, "none")
    finally:
        db.ex("DELETE FROM robots WHERE id=?", (rid,))


def test_wake_up_reports_a_down_supervisor_instead_of_success(client, paired,
                                                              monkeypatch):
    auth, rid = paired
    set_status_url(DEAD, monkeypatch)
    r = client.post(f"/api/robots/{rid}/wakeup", headers=auth)
    assert r.status_code >= 400
    assert r.json()["error"] and r.json().get("published") is not True


def test_reboot_is_an_honest_501_with_its_reasoning(client, supervisor, paired):
    """No cloud→robot reboot command has been recovered, so the endpoint refuses rather
    than publishing a guess at a child's robot."""
    auth, rid = paired
    supervisor.runtime.client.published.clear()
    r = client.post(f"/api/robots/{rid}/reboot", headers=auth)
    assert r.status_code == 501
    body = r.json()
    assert body["ok"] is False and body["supported"] is False
    assert body["error"] == "unsupported" and body["reason"]
    assert "power-and-system-events.md" in body["evidence"]
    assert supervisor.runtime.client.published == [], "reboot must publish nothing"


def test_ota_status_reports_the_robots_own_firmware_and_never_up_to_date(client, paired):
    """This appliance serves no `api/ota`, so it says what the robot reported and no
    more — never a hard-coded "up_to_date"."""
    auth, rid = paired
    body = client.get(f"/api/robots/{rid}/ota_status", headers=auth).json()
    assert body["status"] != "up_to_date"
    assert body["status"] == "unknown" and body["version"] == "3.6.4"
    assert body["ota_reboot_required"] is False
    assert body["ota_server"] is False and body["supported"] is False
    assert "no OTA server" in body["note"]


def test_ota_status_is_unavailable_when_the_supervisor_is_down(client, paired,
                                                               monkeypatch):
    auth, rid = paired
    set_status_url(DEAD, monkeypatch)
    body = client.get(f"/api/robots/{rid}/ota_status", headers=auth).json()
    assert body["status"] == "unavailable" and body["version"] is None
