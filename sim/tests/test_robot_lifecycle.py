"""Unpair and factory reset from the parent app (ROADMAP Next #5).

`DELETE /api/robots/{id}` unpairs and `?rfs=1` also factory-resets: one endpoint, one flag,
as in the original app (`docs/features/robot-lifecycle.md` §1). What the doc asks of the
server, and what each test below pins:

* the robot leaves the account (the app's `UNPAIRED`: no robot in `users/me`) and the child
  stays (§2, "Child data on server: not deleted");
* the robot stops being served: its permit is revoked through the supervisor's EXISTING
  `POST /permits` (the console's Revoke button), which re-pushes the un-paired config;
* a pairing code made before the unpair cannot pair the robot back (the stale-QR case);
* unpair is idempotent, and auth and scoping are the neighbours' (bearer token, own robots);
* a factory reset does the same server-side, then hands back the `restore_factory` setup
  code, the only reset delivery the docs establish (no cloud-to-robot reset command is
  recovered, `mqtt-and-conversation.md` §3.5).

The supervisor is `helpers_console_supervisor.FakeSupervisor`; its `permit_posts`,
`memory_erases`, `telemetry_erases`, `config_posts` and `wakeups` record every call that
reached it. The repo has no QR image decoder (segno only encodes), so the reset code's PNG
is compared with the pairing route's rendering of the exact expected text, and with a
near-miss spelling to prove the comparison can tell them apart.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console import set_status_url  # noqa: E402
from helpers_console_supervisor import (DEAD, DEVICE, client,  # noqa: E402,F401
                                        quicklogin, supervisor)

RESTORE_FACTORY = '{"debug":{"command":"restore_factory"}}'
REVOKE = {"device_id": DEVICE, "permitted": False, "label": ""}


@pytest.fixture(autouse=True)
def the_robot_is_on_no_account(client):
    """Each test pairs `DEVICE` to an account of its own, and a robot is on one account
    (Simulate robot scan refuses one that another account's record names), so every test
    starts with no record naming it. Read straight from the table."""
    from moxie_server import db
    for row in db.q("SELECT id, attributes FROM robots"):
        if json.loads(row["attributes"]).get("mqtt-device-id") == DEVICE:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    yield


def _prepare(client, auth):
    r = client.post("/local/pairing/prepare", headers=auth,
                    json={"ssid": "Home", "password": "pw"})
    assert r.status_code == 200, r.text
    return r.json()


def _scan(client, prep, device_id=""):
    return client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"], "device_id": device_id})


def _paired(client, email, device_id=DEVICE, child=None):
    """A parent with one robot paired through the console. With `device_id` the record
    names the robot's MQTT identity (`mqtt-device-id`); `child` names the child first."""
    auth = quicklogin(client, email)
    if child:
        client.post("/api/children", headers=auth, json={"child": {"child-first-name": child}})
    prep = _prepare(client, auth)
    r = _scan(client, prep, device_id)
    assert r.status_code == 200, r.text
    return auth, r.json()["robot_id"], prep


def _state(client, auth):
    return client.get("/local/state", headers=auth).json()


def _calls(supervisor):
    """Every supervisor call except the permit list, as one comparable snapshot."""
    return (len(supervisor.memory_erases), len(supervisor.telemetry_erases),
            len(supervisor.config_posts), len(supervisor.wakeups))


def test_unpair_takes_the_robot_off_the_account_and_keeps_the_child(client, supervisor):
    auth, rid, prep = _paired(client, "unpair@lifecycle.lan", child="Ada")
    posts, calls = len(supervisor.permit_posts), _calls(supervisor)
    r = client.delete(f"/api/robots/{rid}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unpaired"] is True and body["factory_reset"] is False
    assert body["reset"] is None

    # The doc's UNPAIRED: the app finds no robot, and the record itself is gone.
    me = client.get("/api/users/me", headers=auth).json()
    assert me["data"]["relationships"]["robots"]["data"] == []
    assert not [i for i in me["included"] if i["type"] in ("robots", "robot-setting")]
    assert client.get(f"/api/robots/{rid}", headers=auth).status_code == 404
    assert _state(client, auth)["robots"] == []

    # The child is the account's, not the robot's (§2): kept, and named in the answer.
    kids = _state(client, auth)["children"]
    assert [k["id"] for k in kids] == [prep["child_id"]]
    assert body["child_id"] == prep["child_id"] and body["child_kept"] is True
    assert any("Ada" in d["text"] for d in body["details"] if d["key"] == "child")

    # The robot stops being served, through the supervisor's existing revoke and nothing
    # else: unpairing never erases what Moxie remembers or the activity history.
    assert supervisor.permit_posts[posts:] == [REVOKE]
    assert body["access"]["revoked"] is True and body["access"]["device_id"] == DEVICE
    assert _calls(supervisor) == calls


def test_unpair_sends_exactly_what_the_consoles_revoke_button_sends(client, supervisor):
    """'Publish through the existing command path': the unpair's supervisor call is
    byte-for-byte the one `POST /local/robots/{id}/permit {permitted:false}` makes."""
    auth, rid, _ = _paired(client, "same-path@lifecycle.lan")
    start = len(supervisor.permit_posts)
    assert client.delete(f"/api/robots/{rid}", headers=auth).status_code == 200
    via_unpair = supervisor.permit_posts[start:]
    assert client.post(f"/local/robots/{DEVICE}/permit",
                       json={"permitted": False}).status_code == 200
    via_button = supervisor.permit_posts[start + len(via_unpair):]
    assert via_unpair == via_button == [REVOKE]


def test_unpairing_twice_is_idempotent(client, supervisor):
    auth, rid, prep = _paired(client, "twice@lifecycle.lan")
    assert client.delete(f"/api/robots/{rid}", headers=auth).json()["unpaired"] is True
    posts, calls = len(supervisor.permit_posts), _calls(supervisor)
    again = client.delete(f"/api/robots/{rid}", headers=auth)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["unpaired"] is False and body["pairing_codes_cancelled"] == 0
    assert "already" in body["message"]
    assert len(supervisor.permit_posts) == posts and _calls(supervisor) == calls
    state = _state(client, auth)
    assert state["robots"] == [] and [k["id"] for k in state["children"]] == [prep["child_id"]]


def test_a_pairing_code_made_before_the_unpair_cannot_pair_the_robot_again(client):
    """The stale-QR case: a code the parent made and never used stayed valid forever, so
    after an unpair it re-bound a robot to the account without anyone choosing to."""
    auth = quicklogin(client, "stale@lifecycle.lan")
    used, spare = _prepare(client, auth), _prepare(client, auth)
    rid = _scan(client, used, DEVICE).json()["robot_id"]
    body = client.delete(f"/api/robots/{rid}", headers=auth).json()
    assert body["unpaired"] is True and body["pairing_codes_cancelled"] == 1

    stale = _scan(client, spare)
    assert stale.status_code == 410, stale.text
    assert "unpaired" in stale.json()["detail"]
    assert _scan(client, used).status_code == 409          # already used, as before
    assert _state(client, auth)["robots"] == []

    fresh = _prepare(client, auth)                         # pairing again is one new code away
    assert _scan(client, fresh, DEVICE).status_code == 200
    assert len(_state(client, auth)["robots"]) == 1


def test_factory_reset_unpairs_and_hands_back_the_restore_factory_code(client, supervisor):
    """§2: a reset's server-side cleanup is the unpair's (child kept); what differs is the
    wipe reaching the robot, which here is the setup code — nothing is published."""
    from moxie_server import db
    auth, rid, prep = _paired(client, "reset@lifecycle.lan", child="Bo")
    client.put("/api/secret-key-collection", headers=auth, json={
        "secret_key_collection": {"secret-keys-indexed-by-public-keys": {"pub": "sealed"}}})
    spare = _prepare(client, auth)
    uid = _state(client, auth)["user"]["id"]
    posts, calls = len(supervisor.permit_posts), _calls(supervisor)

    r = client.delete(f"/api/robots/{rid}?rfs=1", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unpaired"] is True and body["factory_reset"] is True
    reset = body["reset"]
    assert reset["qr_payload"] == RESTORE_FACTORY
    assert reset["qr_png"] == "/local/factory-reset/qr.png"
    assert reset["mqtt_command"] is None and reset["verified_on_robot"] is False
    assert reset["limit"] == "No physical robot has been reset this way by this project yet."

    # Server side, exactly the unpair: record gone, access revoked, codes voided ...
    assert client.get(f"/api/robots/{rid}", headers=auth).status_code == 404
    assert supervisor.permit_posts[posts:] == [REVOKE]
    assert _scan(client, spare).status_code == 410
    # ... and the account, its child and its escrowed keys untouched.
    assert [k["id"] for k in _state(client, auth)["children"]] == [prep["child_id"]]
    assert db.q("SELECT 1 FROM secret_keys WHERE user_id=?", (uid,))
    assert client.get("/api/users/me", headers=auth).status_code == 200
    # No MQTT reset, no config push, no erase: the permit was the only supervisor call.
    assert _calls(supervisor) == calls


def test_the_reset_code_is_exactly_the_restore_factory_debug_command(client):
    view = client.get("/local/factory-reset/payload")
    assert view.status_code == 200, view.text
    view = view.json()
    assert view["qr_payload"] == RESTORE_FACTORY
    assert json.loads(view["qr_payload"]) == {"debug": {"command": "restore_factory"}}

    png = client.get("/local/factory-reset/qr.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    # The same renderer that draws the pairing code (scanned by a real Moxie), fed the
    # exact text; a near-miss spelling renders differently, so equality means this text.
    same = client.get("/local/pairing/qr.png", params={"payload": RESTORE_FACTORY})
    near = client.get("/local/pairing/qr.png",
                      params={"payload": '{"debug": {"command": "restore_factory"}}'})
    assert png.content == same.content and png.content != near.content

    # Every instruction says where it comes from, and the honest limit is stated.
    assert view["steps"] and all(s["text"] and s["basis"] for s in view["steps"])
    assert any(s["basis"].startswith("inferred") for s in view["steps"])
    assert view["limit"] == "No physical robot has been reset this way by this project yet."
    assert view["verified_on_robot"] is False and view["mqtt_command"] is None


def test_a_down_supervisor_still_unpairs_and_says_the_robot_is_still_served(
        client, monkeypatch):
    auth, rid, _ = _paired(client, "down@lifecycle.lan")
    set_status_url(DEAD, monkeypatch)
    r = client.delete(f"/api/robots/{rid}", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unpaired"] is True
    assert body["access"]["revoked"] is False and body["access"]["device_id"] == DEVICE
    assert "Robot access" in body["access"]["reason"]      # where the parent finishes it
    assert _state(client, auth)["robots"] == []


def test_a_record_that_does_not_name_its_robot_is_unpaired_without_a_guess(client,
                                                                            supervisor):
    """The fake serves exactly one robot, so `resolve_device_id` would answer
    "sole-served" — fine for a wake-up, wrong for taking a child's robot off the
    appliance. Nothing is revoked, and the parent is told where to do it."""
    auth, rid, _ = _paired(client, "noid@lifecycle.lan", device_id="")
    posts = len(supervisor.permit_posts)
    body = client.delete(f"/api/robots/{rid}", headers=auth).json()
    assert body["unpaired"] is True
    assert body["access"]["revoked"] is False and body["access"]["device_id"] is None
    assert "Robot access" in body["access"]["reason"]
    assert len(supervisor.permit_posts) == posts


def test_unpair_and_reset_follow_the_neighbours_auth_rules(client, supervisor):
    """A bearer token like `GET`/`PUT /api/robots/{id}`; another account's robot is never
    touched (a no-op, not a leak of whether it exists). The reset code's routes are public
    like the pairing and endpoint QR images, which an <img> must be able to load."""
    from moxie_server import db
    owner, rid, _ = _paired(client, "owner@lifecycle.lan")
    spare = _prepare(client, owner)
    for headers in ({}, {"Authorization": "Bearer nope"}):
        for query in ("", "?rfs=1"):
            assert client.delete(f"/api/robots/{rid}{query}",
                                 headers=headers).status_code == 401
    stranger = quicklogin(client, "stranger@lifecycle.lan")
    posts, calls = len(supervisor.permit_posts), _calls(supervisor)
    for query in ("", "?rfs=1"):
        r = client.delete(f"/api/robots/{rid}{query}", headers=stranger)
        assert r.status_code == 200 and r.json()["unpaired"] is False
    assert client.get(f"/api/robots/{rid}", headers=owner).status_code == 200
    assert len(supervisor.permit_posts) == posts and _calls(supervisor) == calls
    open_row = db.q1("SELECT consumed FROM pairings WHERE id_hash=?", (spare["secret_hash"],))
    assert open_row["consumed"] == 0                       # the owner's code still works
    for path in ("/local/factory-reset/qr.png", "/local/factory-reset/payload"):
        assert client.get(path).status_code == 200, path


def test_the_childs_profile_can_be_deleted_once_the_robot_is_unpaired(client):
    """The doc's order (§2): unpair the robot, then the child may go — through the
    existing `DELETE /api/children/{id}`, which is what the console calls when the parent
    ticks the box. The unpair itself never deletes it."""
    auth, rid, prep = _paired(client, "child-after@lifecycle.lan", child="Cy")
    body = client.delete(f"/api/robots/{rid}", headers=auth).json()
    assert body["child_id"] == prep["child_id"]
    assert [k["id"] for k in _state(client, auth)["children"]] == [prep["child_id"]]
    assert client.delete(f"/api/children/{body['child_id']}", headers=auth).status_code == 204
    assert _state(client, auth)["children"] == []


def test_the_console_state_names_each_robots_child(client):
    """The confirmation sheet words its erase choice with the child's name, so the robot
    row in `/local/state` carries the child it is bound to."""
    auth, rid, prep = _paired(client, "names@lifecycle.lan", child="Dee")
    robot = _state(client, auth)["robots"][0]
    assert robot["id"] == rid and robot["child_id"] == prep["child_id"]
    assert robot["mqtt-device-id"] == DEVICE
