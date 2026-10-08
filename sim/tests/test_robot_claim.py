"""Add a robot that paired by QR to the parent's account (ROADMAP Next #5).

A real Moxie scans the Wi-Fi code and the server code, reaches the broker and shows up in
Robot access. Until now the only thing that made an account robot record was the
console's simulated scan, so that robot never got a robot card: no settings, insights,
safety review, memory, Wake, Unpair or Factory reset. `POST /local/robots/{id}/claim`
makes the record. What each test below pins:

* a claim binds the device id the supervisor lists to this account and its child, and
  permits it with the console's own Permit body, once;
* it fails closed and changes nothing when it cannot be sure: no bearer (401), an id the
  supervisor never listed (404), a supervisor it cannot ask (503), a robot on another
  account or an account that already has a robot (409); a repeat is a no-op;
* it is the parent's word, not a proof: no pairing code is used, no public key is written;
* the whole lifecycle (Wake, Unpair, Factory reset) then works on that record;
* `/local/state.unclaimed` lists the connected robots no account has added, and
  `on_other_accounts` those another account has; the claim's own supervisor read is the one
  the page's redraw after it gets;
* Simulate robot scan keeps the same rules, also when a claim, an unpair or another scan
  lands while it runs.

The supervisor is `helpers_console_supervisor.FakeSupervisor`, which lists exactly one
connected robot, `DEVICE`; its `permit_posts`, `memory_erases`, `telemetry_erases`,
`config_posts` and `wakeups` record every call that reached it. A robot on its permit list
that is not `DEVICE` is listed but offline (`OFFLINE`). The console database is shared by
every console module in a run, so each test starts with no record naming `DEVICE` or
`OFFLINE` and an empty permit list, and the module deletes every robot record it made when
it ends.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console import set_status_url  # noqa: E402
from helpers_console_supervisor import (DEAD, DEVICE, client,  # noqa: E402,F401
                                        quicklogin, supervisor)

PERMIT = {"device_id": DEVICE, "permitted": True}
REVOKE = {"device_id": DEVICE, "permitted": False, "label": ""}
RESTORE_FACTORY = '{"debug":{"command":"restore_factory"}}'
#: A robot permitted earlier and switched off now: on the permit list, not connected.
OFFLINE = "d_claim_offline"


@pytest.fixture(scope="module", autouse=True)
def leaves_no_robot_record(client):
    """The console database is shared by every console module in a run: every robot
    record this module made (DEVICE claimed, simulated scans) is deleted when it ends, so
    no later module finds DEVICE already on an account."""
    from moxie_server import db
    before = {r["id"] for r in db.q("SELECT id FROM robots")}
    yield
    for row in db.q("SELECT id FROM robots"):
        if row["id"] not in before:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))


@pytest.fixture(autouse=True)
def no_record_names_the_robot(client, supervisor):
    """Every test starts where a bench robot does: on the broker, on no account, not on
    the permit list. Read straight from the table, never through the code under test."""
    from moxie_server import db
    for row in db.q("SELECT id, attributes FROM robots"):
        if json.loads(row["attributes"]).get("mqtt-device-id") in (DEVICE, OFFLINE):
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    supervisor.permits["devices"].clear()
    yield


def _claim(client, auth, device_id=DEVICE):
    return client.post(f"/local/robots/{device_id}/claim", headers=auth)


def _me_robots(client, auth):
    return [i for i in client.get("/api/users/me", headers=auth).json()["included"]
            if i["type"] == "robots"]


def _state(client, auth):
    r = client.get("/local/state", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _calls(supervisor):
    """Every supervisor call a claim must never make, as one comparable snapshot."""
    return (len(supervisor.memory_erases), len(supervisor.telemetry_erases),
            len(supervisor.config_posts), len(supervisor.wakeups))


def _rows_naming(device_id):
    from moxie_server import db
    return [r for r in db.q("SELECT * FROM robots")
            if json.loads(r["attributes"]).get("mqtt-device-id") == device_id]


def test_a_robot_that_paired_by_qr_can_be_added_to_the_account(client, supervisor):
    auth = quicklogin(client, "bench@claim.lan")
    assert _me_robots(client, auth) == []          # the bench-day defect: no record at all
    assert _state(client, auth)["children"] == []

    r = _claim(client, auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_id"] == DEVICE and body["robot_id"] and body["created"] is True
    assert body["permitted"] is True and body["permit_error"] is None

    robots = _me_robots(client, auth)
    assert [x["id"] for x in robots] == [body["robot_id"]]
    assert robots[0]["attributes"]["mqtt-device-id"] == DEVICE
    state = _state(client, auth)
    kids = state["children"]
    assert [k["child-first-name"] for k in kids] == ["Moxie Kid"]   # made, as pairing does
    assert state["user"]["active-child-id"] == kids[0]["id"]
    assert [(x["id"], x["child_id"]) for x in state["robots"]] == [(body["robot_id"],
                                                                    kids[0]["id"])]
    assert body["child_id"] == kids[0]["id"]


def test_a_claim_binds_the_accounts_own_child(client):
    auth = quicklogin(client, "has-a-child@claim.lan")
    cid = client.post("/api/children", headers=auth,
                      json={"child": {"child-first-name": "Ada"}}).json()["data"]["id"]
    assert _claim(client, auth).json()["child_id"] == cid
    state = _state(client, auth)
    assert [k["id"] for k in state["children"]] == [cid]           # no second child
    assert state["robots"][0]["child_id"] == cid


def test_the_claim_permits_the_robot_once_and_a_repeat_changes_nothing(client, supervisor):
    auth = quicklogin(client, "once@claim.lan")
    posts, calls = len(supervisor.permit_posts), _calls(supervisor)
    first = _claim(client, auth).json()
    sent = supervisor.permit_posts[posts:]
    assert len(sent) == 1 and {k: sent[0][k] for k in PERMIT} == PERMIT
    assert set(sent[0]) == {"device_id", "permitted", "label"}     # the Permit button's body
    assert sent[0]["label"].strip()                               # says where it came from
    assert DEVICE in supervisor.permits["devices"]
    assert _calls(supervisor) == calls          # no erase, no config push, no wake-up

    again = _claim(client, auth)
    assert again.status_code == 200, again.text
    assert again.json()["robot_id"] == first["robot_id"] and again.json()["created"] is False
    assert again.json()["permitted"] is True
    assert len(supervisor.permit_posts) == posts + 1
    assert len(_rows_naming(DEVICE)) == 1 and _calls(supervisor) == calls


def test_a_robot_on_the_permit_list_can_be_added_while_it_is_offline(client, supervisor):
    """The supervisor lists a robot that is connected now OR on its permit list: one a
    grown-up permitted earlier and has switched off can still be added. Keyed on the
    connected robots alone, this claim would be a 404."""
    auth = quicklogin(client, "listed-offline@claim.lan")
    supervisor.permits["devices"][OFFLINE] = {"permitted_at": 1, "label": "permitted earlier"}
    r = _claim(client, auth, OFFLINE)
    assert r.status_code == 200, r.text
    assert r.json()["device_id"] == OFFLINE and r.json()["created"] is True
    assert [x["attributes"]["mqtt-device-id"] for x in _me_robots(client, auth)] == [OFFLINE]


def test_a_repeat_is_answered_from_the_record_once_the_robot_is_off_every_list(
        client, supervisor, monkeypatch):
    """A robot on this account that is now switched off and was revoked in Robot access is
    on no list the supervisor keeps, and a supervisor that is down lists nothing: a repeat
    still finds it on this account (200, `created: false`, which the page words as "already
    on your account"), posts nothing, and says honestly whether it is let in."""
    auth = quicklogin(client, "repeat-offline@claim.lan")
    supervisor.permits["devices"][OFFLINE] = {"permitted_at": 1, "label": "permitted earlier"}
    first = _claim(client, auth, OFFLINE).json()
    supervisor.permits["devices"].pop(OFFLINE)                 # Revoke; it is not connected
    posts = len(supervisor.permit_posts)

    again = _claim(client, auth, OFFLINE)
    assert again.status_code == 200, again.text
    assert again.json()["robot_id"] == first["robot_id"] and again.json()["created"] is False
    assert again.json()["permitted"] is False and again.json()["permit_error"] is None
    set_status_url(DEAD, monkeypatch)
    down = _claim(client, auth, OFFLINE)
    assert down.status_code == 200, down.text
    assert down.json()["robot_id"] == first["robot_id"] and down.json()["created"] is False
    assert down.json()["permitted"] is False and "not reachable" in down.json()["permit_error"]
    assert len(supervisor.permit_posts) == posts and len(_rows_naming(OFFLINE)) == 1


def test_refusals_create_no_record_and_post_nothing(client, supervisor, monkeypatch):
    """Each refusal says why in a sentence (`reason`, what the web app shows) and leaves
    the account, its children and the permit list exactly as they were."""
    from moxie_server import db
    owner = quicklogin(client, "owner@claim.lan")
    stranger = quicklogin(client, "stranger@claim.lan")
    taken = quicklogin(client, "has-a-robot@claim.lan")
    assert _claim(client, owner).status_code == 200                 # DEVICE is the owner's
    prep = client.post("/local/pairing/prepare", headers=taken,
                       json={"ssid": "Home", "password": "pw"}).json()
    assert client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"]}).status_code == 200

    def snapshot():
        return (db.q1("SELECT COUNT(*) n FROM robots")["n"],
                db.q1("SELECT COUNT(*) n FROM children")["n"],
                len(supervisor.permit_posts), dict(supervisor.permits["devices"]),
                _calls(supervisor))

    before = snapshot()
    for headers in ({}, {"Authorization": "Bearer nope"}):
        assert _claim(client, headers).status_code == 401
    never = _claim(client, stranger, "d_never_seen")
    assert never.status_code == 404 and "connected" in never.json()["reason"]
    other = _claim(client, stranger)
    assert other.status_code == 409 and "another account" in other.json()["reason"]
    assert snapshot() == before

    for row in _rows_naming(DEVICE):                      # the robot is free again ...
        db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    before = snapshot()
    full = _claim(client, taken)                          # ... but this account is not
    assert full.status_code == 409, full.text
    assert "Unpair the current robot first" in full.json()["reason"]
    set_status_url(DEAD, monkeypatch)
    down = _claim(client, stranger)
    assert down.status_code == 503 and "Nothing was changed" in down.json()["reason"]
    assert snapshot() == before
    state = _state(client, stranger)
    assert state["robots"] == [] and state["children"] == []


def test_a_simulated_scan_refuses_a_robot_on_another_account(client, supervisor):
    """One robot, one account, on every path: Simulate robot scan, handed the id of a robot
    another account has added, is refused in the claim's own words and changes nothing. No
    record, no permit post, and the code is still unused, so the same scan without that id
    still completes."""
    from moxie_server import db
    owner = quicklogin(client, "sim-owner@claim.lan")
    other = quicklogin(client, "sim-other@claim.lan")
    assert _claim(client, owner).status_code == 200
    prep = client.post("/local/pairing/prepare", headers=other,
                       json={"ssid": "Home", "password": "pw"}).json()
    robots, posts = db.q1("SELECT COUNT(*) n FROM robots")["n"], len(supervisor.permit_posts)

    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": prep["qr_payload"], "device_id": DEVICE})
    assert r.status_code == 409, r.text
    claimed = _claim(client, other)                     # the claim path, for its words
    assert claimed.status_code == 409 and r.json() == claimed.json()
    assert db.q1("SELECT COUNT(*) n FROM robots")["n"] == robots
    assert len(supervisor.permit_posts) == posts and len(_rows_naming(DEVICE)) == 1
    assert client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"]}).status_code == 200


def test_a_claim_that_lands_mid_scan_leaves_the_robot_on_one_account(client, monkeypatch):
    """The scan's check and its record are one step. Its permit post is the one call in
    the scan that waits on the network (up to 3 s): another account's claim that lands
    then finds the robot already on the scanning account and is refused in the usual
    words, so the robot is never on two accounts. That claim is the real route, called
    from inside the post."""
    from moxie_server import db, supervisor as sv
    from moxie_server.routes import pairing
    owner = quicklogin(client, "mid-scan-owner@claim.lan")
    scanner = quicklogin(client, "mid-scan-scanner@claim.lan")
    owner_row = db.get_user(_state(client, owner)["user"]["id"])
    prep = client.post("/local/pairing/prepare", headers=scanner,
                       json={"ssid": "Home", "password": "pw"}).json()
    real, raced = sv.post_json, []

    def post_json(path, payload, timeout=3):
        if payload.get("label") == "paired via console" and not raced:
            raced.append(pairing.claim_robot(DEVICE, u=owner_row))
        return real(path, payload, timeout)

    monkeypatch.setattr(sv, "post_json", post_json)
    scan = client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"], "device_id": DEVICE})
    assert len(raced) == 1                                  # the claim did land mid-scan
    owners = sorted({r["user_id"] for r in _rows_naming(DEVICE)})
    assert owners == [_state(client, scanner)["user"]["id"]], owners
    assert scan.status_code == 200, scan.text
    assert raced[0].status_code == 409
    assert json.loads(raced[0].body)["reason"] == pairing.ON_ANOTHER_ACCOUNT


@pytest.mark.parametrize("meanwhile, status", [("unpair", 410), ("scan", 409)])
def test_a_scan_checks_its_code_again_in_the_step_that_writes_the_record(
        client, monkeypatch, meanwhile, status):
    """The scan reads its code open, derives the robot's keys, then writes the record. An
    unpair that lands in between voids the code (it voids every code the account still
    had open), and another scan of the same code that lands in between uses it: either way
    this scan must not complete, so the record's step checks the code again. The other
    step runs inside the key derivation, the one between the read and the record."""
    from moxie_server import crypto, db
    from moxie_server.routes import pairing
    auth = quicklogin(client, f"mid-scan-{meanwhile}@claim.lan")
    uid = _state(client, auth)["user"]["id"]
    prepare = lambda: client.post("/local/pairing/prepare", headers=auth,
                                  json={"ssid": "Home", "password": "pw"}).json()
    old = client.post("/local/simulate-robot-scan",
                      json={"qr_payload": prepare()["qr_payload"]}).json()["robot_id"]
    code = prepare()                        # made while the account still has that robot
    real, ran = crypto.keys_from_seed, []

    def keys_from_seed(seed):
        if not ran:
            ran.append(meanwhile)
            if meanwhile == "unpair":
                assert db.unpair_robot(old, uid)[1] == 1         # this code is voided
            else:                                                 # the other scan's record
                assert db.bind_scanned_robot(db.new_id(), uid, code["child_id"],
                                             {"name": "Moxie (other scan)"}, {},
                                             code["secret_hash"]) == "bound"
        return real(seed)

    monkeypatch.setattr(crypto, "keys_from_seed", keys_from_seed)
    scan = client.post("/local/simulate-robot-scan", json={"qr_payload": code["qr_payload"]})
    assert ran == [meanwhile]
    assert scan.status_code == status, scan.text
    assert scan.json()["detail"] == (pairing.CODE_VOIDED if meanwhile == "unpair"
                                     else pairing.CODE_USED)
    names = sorted(json.loads(r["attributes"]).get("name")
                   for r in db.q("SELECT attributes FROM robots WHERE user_id=?", (uid,)))
    assert names == ([] if meanwhile == "unpair"
                     else ["Moxie (other scan)", "Moxie (simulated)"]), names


def test_a_claim_uses_no_pairing_code_and_writes_no_public_key(client):
    """Nothing the robot sends carries the pairing seed, so the server cannot tell which
    code (if any) this robot scanned: a claim asserts nothing about one."""
    from moxie_server import db
    auth = quicklogin(client, "no-seed@claim.lan")
    client.post("/local/pairing/prepare", headers=auth, json={"ssid": "Home", "password": "pw"})
    codes = lambda: sorted(tuple(r) for r in db.q("SELECT id_hash, consumed FROM pairings"))
    before = codes()
    rid = _claim(client, auth).json()["robot_id"]
    assert codes() == before
    attrs = client.get(f"/api/robots/{rid}", headers=auth).json()["data"]["attributes"]
    assert "public-key" not in attrs and attrs["mqtt-device-id"] == DEVICE


def test_wake_unpair_and_factory_reset_work_on_a_claimed_robot(client, supervisor):
    auth = quicklogin(client, "lifecycle@claim.lan")
    rid = _claim(client, auth).json()["robot_id"]
    wake = client.post(f"/api/robots/{rid}/wakeup", headers=auth)
    assert wake.status_code == 200, wake.text
    assert wake.json()["resolved_by"] == "record" and supervisor.wakeups[-1] == DEVICE

    posts = len(supervisor.permit_posts)
    gone = client.delete(f"/api/robots/{rid}", headers=auth).json()
    assert gone["unpaired"] is True and supervisor.permit_posts[posts:] == [REVOKE]
    assert _me_robots(client, auth) == []

    rid = _claim(client, auth).json()["robot_id"]                  # added again, then reset
    posts = len(supervisor.permit_posts)
    reset = client.delete(f"/api/robots/{rid}?rfs=1", headers=auth).json()
    assert reset["unpaired"] is True and reset["factory_reset"] is True
    assert reset["reset"]["qr_payload"] == RESTORE_FACTORY
    assert supervisor.permit_posts[posts:] == [REVOKE]


def test_the_state_read_is_bounded_shared_and_rides_out_a_busy_supervisor(client,
                                                                          monkeypatch):
    """`/local/state` is polled, so its supervisor read has a short timeout (a black-holed
    supervisor address used to cost every poll 2 s) and one answer serves every call for
    STATE_TTL_S. The status server answers one request at a time, so a read that fails
    soon after a good one keeps that answer; one that has failed for longer than
    STATE_GRACE_S lists nothing and says it does not know. The route's clock is pinned
    here; no real clock is read."""
    from moxie_server import supervisor as sv
    from moxie_server.routes import pairing
    now, reads, busy, real = [5000.0], [], [False], sv.call

    def call(method, path, data=None, timeout=3, device_id=None):
        if path == "/permits":
            reads.append(timeout)
            if busy[0]:
                return {"ok": False, "error": sv.UNREACHABLE, "detail": "timed out"}, 503
        return real(method, path, data, timeout, device_id)

    monkeypatch.setattr(sv, "call", call)
    monkeypatch.setattr(pairing, "_clock", lambda: now[0])
    monkeypatch.setattr(pairing, "_state_read", {})
    auth = quicklogin(client, "bounded@claim.lan")
    assert _state(client, auth)["unclaimed"] == [DEVICE]
    assert reads == [pairing.STATE_TIMEOUT_S] and pairing.STATE_TIMEOUT_S <= 0.5
    now[0] += pairing.STATE_TTL_S / 2
    assert _state(client, auth)["unclaimed"] == [DEVICE] and len(reads) == 1   # shared
    busy[0] = True
    now[0] += pairing.STATE_TTL_S
    kept = _state(client, auth)
    assert (kept["unclaimed"], kept["unclaimed_known"]) == ([DEVICE], True) and len(reads) == 2
    now[0] += pairing.STATE_GRACE_S
    gone = _state(client, auth)
    assert (gone["unclaimed"], gone["unclaimed_known"]) == ([], False) and len(reads) == 3
    assert all(t == pairing.STATE_TIMEOUT_S for t in reads)


def test_the_redraw_after_a_claim_sees_what_the_claim_saw(client, monkeypatch):
    """The page redraws straight after a claim, and `/local/state` shares one supervisor
    read for STATE_TTL_S. The claim asks the supervisor afresh, and its answer becomes that
    shared read: otherwise a robot the claim found gone is still offered on the redraw,
    under the claim's own refusal. The route's clock is pinned; no real clock is read."""
    from moxie_server import supervisor as sv
    from moxie_server.routes import pairing
    now, left, real = [9000.0], [False], sv.call

    def call(method, path, data=None, timeout=3, device_id=None):
        out, code = real(method, path, data, timeout, device_id)
        if path == "/permits" and left[0]:
            out = {**out, "connected": [], "pending": []}         # off the broker
        return out, code

    monkeypatch.setattr(sv, "call", call)
    monkeypatch.setattr(pairing, "_clock", lambda: now[0])
    monkeypatch.setattr(pairing, "_state_read", {})
    auth = quicklogin(client, "redraw@claim.lan")
    assert _state(client, auth)["unclaimed"] == [DEVICE]          # what the page last saw
    left[0] = True
    refused = _claim(client, auth)
    assert refused.status_code == 404 and refused.json()["reason"] == pairing.UNKNOWN_ROBOT
    assert _state(client, auth)["unclaimed"] == []                # the same instant


def test_unclaimed_lists_the_connected_robots_no_account_has_added(client, monkeypatch):
    """`unclaimed_known` tells "nobody could check" (the supervisor could not be asked)
    apart from "no robot arrived": the list is empty either way."""
    first, second = quicklogin(client, "a@unclaimed.lan"), quicklogin(client, "b@unclaimed.lan")
    assert _state(client, first)["unclaimed"] == [DEVICE]
    assert _state(client, second)["unclaimed"] == [DEVICE]
    assert _claim(client, first).status_code == 200
    assert _state(client, first)["unclaimed"] == []
    taken = _state(client, second)               # nobody is offered a robot that is taken ...
    assert taken["unclaimed"] == [] and taken["unclaimed_known"] is True      # ... and we know
    set_status_url(DEAD, monkeypatch)
    down = _state(client, second)
    assert down["unclaimed"] == [] and down["unclaimed_known"] is False


def test_a_robot_on_another_account_is_named_apart(client, monkeypatch):
    """Robot access lists every robot on the broker to every account. One that another
    account has added is not offered (`unclaimed`), and `on_other_accounts` names it (its
    id, never the account) so the page can say why there is no button. The account's own
    robot is in neither list, and nothing is named when nobody could check."""
    owner, other = quicklogin(client, "owner@apart.lan"), quicklogin(client, "other@apart.lan")
    assert _claim(client, owner).status_code == 200
    mine, theirs = _state(client, owner), _state(client, other)
    assert (mine["unclaimed"], mine["on_other_accounts"]) == ([], [])
    assert (theirs["unclaimed"], theirs["on_other_accounts"]) == ([], [DEVICE])
    set_status_url(DEAD, monkeypatch)
    down = _state(client, other)
    assert (down["on_other_accounts"], down["unclaimed_known"]) == ([], False)
