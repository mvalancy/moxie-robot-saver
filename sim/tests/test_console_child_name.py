"""The child's name from the parent's account to the robot, through the console (K7).

`server/moxie_server/child_profile.py` sends the account's child name to the supervisor
(`POST /config?device_id=… {"child": {"nickname": …}}`): after Add to my account (the claim
itself is pinned in `test_robot_claim.py`) and Simulate robot scan, when the Wi-Fi tab
renames the child (`PUT /api/children/{id}`), and when Permit lets in a robot an account's
record names. Unpair and factory reset clear it (`{"child": null}`) while the record and the
permit still stand. What each test pins:

* a rename reaches every robot bound to that child, once; the record is saved even when
  the supervisor cannot be reached, and the answer says why the name did not go;
* the pairing placeholder "Moxie Kid" and a blank name are never sent, on any path: a
  clear (`child: null`) goes instead, so a robot joining an account never says a name an
  earlier record left on it (also when the account's name is one the rule refuses);
* another account's child is never sent;
* unpair and reset clear the robot's copy first (post order), and through a REAL runtime
  the robot's saved settings then hold no name;
* the console's two `/status` views, and a config answer, name a robot's child only to
  the account that has the robot (nor give away the face cache id, from which a list of
  first names recovers it); the activity feed masks the rest.

The supervisor is `helpers_console_supervisor.FakeSupervisor` (the REAL sanitizer behind
`/config`); one test points the console at a real `MoxieRuntime`'s status server instead.
A child's name is personal data: only 'Sam', 'José', 'Zoë' and 'Moxie Kid' appear here.
"""
import hashlib
import json
import os
import sys

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console import set_status_url  # noqa: E402
from helpers_console_supervisor import (DEAD, DEVICE, client,  # noqa: E402,F401
                                        quicklogin, supervisor)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REVOKE = {"device_id": DEVICE, "permitted": False, "label": ""}


@pytest.fixture(scope="module", autouse=True)
def leaves_no_robot_record(client):
    """The console database is shared by every console module in a run."""
    from moxie_server import db
    before = {r["id"] for r in db.q("SELECT id FROM robots")}
    yield
    for row in db.q("SELECT id FROM robots"):
        if row["id"] not in before:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))


@pytest.fixture(autouse=True)
def no_record_names_the_robot(client, supervisor):
    from moxie_server import db
    for row in db.q("SELECT id, attributes FROM robots"):
        if json.loads(row["attributes"]).get("mqtt-device-id") == DEVICE:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    supervisor.permits["devices"].clear()
    supervisor.overrides.pop("child", None)
    yield


def _child(client, auth, name):
    r = client.post("/api/children", headers=auth, json={"child": {"child-first-name": name}})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _claimed(client, email, name=None):
    """A parent whose account has `DEVICE` (and a child named `name` first, if given)."""
    auth = quicklogin(client, email)
    cid = _child(client, auth, name) if name is not None else None
    r = client.post(f"/local/robots/{DEVICE}/claim", headers=auth)
    assert r.status_code == 200, r.text
    return auth, r.json(), cid


def _sent(supervisor, since):
    """The `/config` bodies the console sent since `since`, decoded."""
    return [(d, json.loads(b)) for d, b in supervisor.config_posts[since:]]


def _named(supervisor, since):
    """The posts since `since` that carry a name (a clear, `child: null`, carries none)."""
    return [(d, b) for d, b in _sent(supervisor, since) if b.get("child") is not None]


def _cleared(supervisor, since):
    """How many clears (`{"child": null}`) the console sent since `since`."""
    return sum(b == {"child": None} for _, b in _sent(supervisor, since))


def test_a_rename_reaches_every_robot_bound_to_that_child_once(client, supervisor):
    auth, claim, cid = _claimed(client, "rename@child.lan", "Sam")
    assert claim["child_pushed"] is True
    posts = len(supervisor.config_posts)
    r = client.put(f"/api/children/{cid}", headers=auth,
                   json={"child": {"child-first-name": "José"}})
    assert r.status_code == 200, r.text
    assert (r.json()["child_pushed"], r.json()["reason"]) == (True, None)
    assert r.json()["data"]["attributes"]["child-first-name"] == "José"
    assert _sent(supervisor, posts) == [(DEVICE, {"child": {"nickname": "José"}})]
    kids = client.get("/local/state", headers=auth).json()["children"]
    assert [k["id"] for k in kids] == [cid]                         # renamed, not added


def test_a_rename_is_saved_when_it_cannot_reach_a_robot_and_says_why(client, supervisor,
                                                                      monkeypatch):
    auth = quicklogin(client, "no-robot@child.lan")
    cid = _child(client, auth, "Sam")
    posts = len(supervisor.config_posts)
    r = client.put(f"/api/children/{cid}", headers=auth, json={"child": {"child-first-name": "Zoë"}})
    assert r.status_code == 200 and r.json()["child_pushed"] is False
    assert "No robot" in r.json()["reason"] and _sent(supervisor, posts) == []

    auth, _, cid = _claimed(client, "dead@child.lan", "Sam")
    set_status_url(DEAD, monkeypatch)
    r = client.put(f"/api/children/{cid}", headers=auth, json={"child": {"child-first-name": "Zoë"}})
    assert r.status_code == 200, r.text
    assert r.json()["child_pushed"] is False and "could not reach" in r.json()["reason"]
    kids = client.get("/local/state", headers=auth).json()["children"]
    assert kids[0]["child-first-name"] == "Zoë"                     # the record is saved


def test_the_pairing_placeholder_and_a_blank_name_are_never_sent(client, supervisor):
    """The claim and pairing name a child "Moxie Kid" when the account has none: a
    placeholder, so Moxie says its default. No name is ever sent for it or for a blank
    one, on any path: each sends a clear instead (`child: null`), so the robot says the
    default even if an earlier record left a name on it; the answers say why."""
    from moxie_server import db
    posts = len(supervisor.config_posts)
    auth, claim, _ = _claimed(client, "placeholder@child.lan")
    assert claim["child_pushed"] is False and "no name" in claim["reason"]
    assert _cleared(supervisor, posts) == 1
    kid = client.get("/local/state", headers=auth).json()["children"][0]
    assert kid["child-first-name"] == "Moxie Kid"
    for name in ("Moxie Kid", "   ", "moxie  kid"):
        r = client.put(f"/api/children/{kid['id']}", headers=auth,
                       json={"child": {"child-first-name": name}})
        assert r.status_code == 200 and r.json()["child_pushed"] is False, name
    assert client.post(f"/local/robots/{DEVICE}/permit", json={}).status_code == 200
    assert _cleared(supervisor, posts) == 5         # the claim, three renames, the Permit
    for row in db.q("SELECT id, attributes FROM robots"):           # free DEVICE again
        if json.loads(row["attributes"]).get("mqtt-device-id") == DEVICE:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    other = quicklogin(client, "placeholder-scan@child.lan")
    prep = client.post("/local/pairing/prepare", headers=other,
                       json={"ssid": "Home", "password": "pw"}).json()
    scan = client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"], "device_id": DEVICE})
    assert scan.status_code == 200 and scan.json()["child_pushed"] is False
    assert _named(supervisor, posts) == [] and _cleared(supervisor, posts) == 6
    assert not [raw for _, raw in supervisor.config_posts[posts:]
                if "moxie kid" in " ".join(raw.lower().split())]


def test_another_accounts_child_is_never_sent(client, supervisor):
    """An account names only its own child: a PUT on another account's child is a 404,
    and a pairing the original app registered with another account's `child-id` (its
    `pairing-info` takes the id as given) binds the robot but sends no name."""
    sys.path.insert(0, os.path.join(REPO, "tools", "pairing"))
    import moxie_qr
    owner = quicklogin(client, "owner@child.lan")
    owners_child = _child(client, owner, "Sam")
    other = quicklogin(client, "other@child.lan")
    _child(client, other, "José")
    posts = len(supervisor.config_posts)
    r = client.put(f"/api/children/{owners_child}", headers=other,
                   json={"child": {"child-first-name": "Zoë"}})
    assert r.status_code == 404
    seed = bytes(range(32))
    id_hash = hashlib.sha256(seed).hexdigest()
    assert client.post(f"/api/pairing-info?id={id_hash}&child-id={owners_child}",
                       headers=other).status_code == 204
    qr = moxie_qr.encode_proto(moxie_qr.WifiInfo("Home", "pw"), seed)
    scan = client.post("/local/simulate-robot-scan",
                       json={"qr_payload": qr, "device_id": DEVICE})
    assert scan.status_code == 200, scan.text
    assert scan.json()["child_pushed"] is False
    assert _named(supervisor, posts) == [] and _cleared(supervisor, posts) == 1


def test_a_scan_sends_the_name_of_the_account_the_code_was_made_on(client, supervisor):
    """Simulate robot scan completes a pairing code: the robot joins the account that made
    the code, and that account's child's name follows the permit, once."""
    auth = quicklogin(client, "scan-named@child.lan")
    _child(client, auth, "José")
    prep = client.post("/local/pairing/prepare", headers=auth,
                       json={"ssid": "Home", "password": "pw"}).json()
    posts, permits = len(supervisor.config_posts), len(supervisor.permit_posts)
    scan = client.post("/local/simulate-robot-scan",
                       json={"qr_payload": prep["qr_payload"], "device_id": DEVICE})
    assert scan.status_code == 200, scan.text
    assert (scan.json()["permitted"], scan.json()["child_pushed"]) == (True, True)
    assert len(supervisor.permit_posts) == permits + 1
    assert _sent(supervisor, posts) == [(DEVICE, {"child": {"nickname": "José"}})]


def test_permit_sends_a_bound_robots_name_once_and_nothing_else(client, supervisor):
    """Permit in Robot access is the recovery path: for a robot an account's record names
    it sends that child's name once, after the permit. A revoke sends none, and a robot no
    record names gets none. Never an erase."""
    def counts():
        return (len(supervisor.memory_erases), len(supervisor.telemetry_erases))

    _claimed(client, "permit@child.lan", "Sam")
    posts, permits, erases = (len(supervisor.config_posts), len(supervisor.permit_posts),
                              counts())
    off = client.post(f"/local/robots/{DEVICE}/permit", json={"permitted": False})
    assert off.status_code == 200 and "child_pushed" not in off.json()
    on = client.post(f"/local/robots/{DEVICE}/permit", json={})
    assert on.status_code == 200 and on.json()["child_pushed"] is True
    assert _sent(supervisor, posts) == [(DEVICE, {"child": {"nickname": "Sam"}})]
    assert len(supervisor.permit_posts) == permits + 2 and counts() == erases
    assert "Sam" not in json.dumps(on.json(), ensure_ascii=False)   # never echoed back

    from moxie_server import db
    for row in db.q("SELECT id, attributes FROM robots"):
        if json.loads(row["attributes"]).get("mqtt-device-id") == DEVICE:
            db.ex("DELETE FROM robots WHERE id=?", (row["id"],))
    posts = len(supervisor.config_posts)
    assert "child_pushed" not in client.post(f"/local/robots/{DEVICE}/permit",
                                             json={}).json()
    assert _sent(supervisor, posts) == []


@pytest.mark.parametrize("query", ["", "?rfs=1"])
def test_unpair_and_reset_clear_the_name_before_the_record_and_the_permit_go(
        client, supervisor, monkeypatch, query):
    from moxie_server import db, supervisor as sv
    auth, claim, _ = _claimed(client, f"clear{query[1:4]}@child.lan", "Sam")
    rid = claim["robot_id"]
    calls, real = [], sv.post_json

    def post_json(path, payload, timeout=3):
        exists = db.q1("SELECT 1 FROM robots WHERE id=?", (rid,)) is not None
        calls.append((path, payload, exists, DEVICE in supervisor.permits["devices"]))
        return real(path, payload, timeout)

    monkeypatch.setattr(sv, "post_json", post_json)
    body = client.delete(f"/api/robots/{rid}{query}", headers=auth).json()
    assert body["unpaired"] is True and body["child_cleared"] is True
    assert body["child_clear_error"] is None
    assert calls == [(f"/config?device_id={DEVICE}", {"child": None}, True, True),
                     ("/permits", REVOKE, False, True)], calls
    assert supervisor.overrides["child"] is None        # the fake keeps the null it was sent
    assert any(d["key"] == "name" for d in body["details"])


def test_the_status_views_name_a_robots_child_only_to_its_account(client, supervisor,
                                                                  monkeypatch):
    """Anyone on the home network can call the console's `/local/*` routes without
    signing in (OQ3). The supervisor's `/status` carries each connected robot's name, so
    the console's two views of it, and a config answer, name a robot's child only to a
    caller signed in to the account that has that robot. The activity feed, where a line
    Moxie spoke can carry a name, masks every other name this server knows."""
    from moxie_sdk.faces import face_child_id, face_options_list, validate_face
    from moxie_server import supervisor as sv
    owner, _, _ = _claimed(client, "owner-view@child.lan", "Sam")
    stranger = quicklogin(client, "stranger-view@child.lan")
    _child(client, stranger, "José")
    # another account's child spelled another way: the owner's own name still shows
    _child(client, quicklogin(client, "shouty-view@child.lan"), "SAM")
    face = {"eye_color": "teal"}

    def cache_id(effective, name):
        """`face_child_id` as the supervisor computes it, from what a view shows."""
        return face_child_id(face_options_list(validate_face(effective["face"])),
                             child_key=name)

    snap = {"ok": True, "app": "content", "robots": [
        {"device_id": DEVICE, "child": "Sam", "config_overrides": {
            "child": {"nickname": "Sam"}, "audio_volume": 0.4},
         "config_effective": {"child": {"nickname": "Sam"}, "audio_volume": 0.4,
                              "face": face},
         "face_cache_id": cache_id({"face": face}, "Sam")},
        {"device_id": "d_neighbour", "child": "Zoë",
         "config_overrides": {"child": {"nickname": "Zoë"}},
         "config_effective": {"child": {"nickname": "Zoë"}, "face": face},
         "face_cache_id": cache_id({"face": face}, "Zoë")}],
        "recent": [{"t": 1, "kind": "chat", "text": "hello (unprompted): 'Welcome back, Sam!'"},
                   {"t": 2, "kind": "chat", "text": "💬 'hi' → 'Hi Zoë! Is José there?'"},
                   {"t": 3, "kind": "config", "text": "⚙️  config updated: child"}]}
    monkeypatch.setattr(sv, "fetch_status", lambda: json.loads(json.dumps(snap)))

    def views(headers):
        return [client.get(p, headers=headers).json()
                for p in ("/local/fleet", "/local/broker/status")]

    def guessed(view):
        """The names a list of first names recovers from a view's face cache ids."""
        return sorted(n for r in view["robots"] if r.get("face_cache_id")
                      for n in ("Sam", "Zoë", "José")
                      if cache_id(r["config_effective"], n) == r["face_cache_id"])

    assert guessed(snap) == ["Sam", "Zoë"]       # the attack works on the raw snapshot
    for headers in ({}, {"Authorization": "Bearer nope"}):        # not signed in
        for v in views(headers):
            text = json.dumps(v, ensure_ascii=False)
            assert not any(n in text for n in ("Sam", "Zoë", "José")), text
            assert [r["child"] for r in v["robots"]] == [None, None]
            assert v["robots"][0]["config_overrides"] == {"audio_volume": 0.4}
            assert [r["face_cache_id"] for r in v["robots"]] == ["", ""]
            assert guessed(v) == []
            assert [e["text"] for e in v["recent"]] == [
                "hello (unprompted): 'Welcome back, [name]!'",
                "💬 'hi' → 'Hi [name]! Is [name] there?'", "⚙️  config updated: child"]
    for v in views(stranger):             # signed in, no robot: only their own child's name
        assert [r["child"] for r in v["robots"]] == [None, None]
        assert guessed(v) == []
        assert [e["text"] for e in v["recent"]][:2] == [
            "hello (unprompted): 'Welcome back, [name]!'",
            "💬 'hi' → 'Hi [name]! Is José there?'"]
    for v in views(owner):
        assert [r["child"] for r in v["robots"]] == ["Sam", None]
        assert v["robots"][0]["config_overrides"]["child"] == {"nickname": "Sam"}
        assert "child" not in v["robots"][1]["config_effective"]
        assert guessed(v) == ["Sam"]             # the owner's own texture key is kept
        assert [e["text"] for e in v["recent"]][:2] == [
            "hello (unprompted): 'Welcome back, Sam!'",
            "💬 'hi' → 'Hi [name]! Is [name] there?'"]

    supervisor.overrides["child"] = {"nickname": "Sam"}
    anon = client.post(f"/local/robots/{DEVICE}/config", json={"audio_volume": 30}).json()
    assert anon["ok"] is True and "Sam" not in json.dumps(anon)
    mine = client.post(f"/local/robots/{DEVICE}/config", headers=owner,
                       json={"audio_volume": 30}).json()
    assert mine["config_overrides"]["child"] == {"nickname": "Sam"}


def test_a_claim_and_an_unpair_round_trip_through_the_real_runtime(client, tmp_path,
                                                                    monkeypatch):
    """The console against a REAL runtime's status server: the claim lets the robot in
    and its next `/config` carries the child's name; `/status` says it (to the owner);
    the unpair clears it from the robot's saved settings (no `child` key left) before the
    revoke sends the robot back to pending. An account with no name sends none."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime, status_server
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.store import JsonStore

    class _App(MoxieApp):
        name = "content"

    rt, _ = make_runtime(_App(), device_id=DEVICE, nickname="friend",
                         allow_unverified_bots=False, store=JsonStore(root=str(tmp_path)))
    set_status_url(status_server(rt) + "/status", monkeypatch)
    topic = f"/devices/{DEVICE}/config"

    auth, claim, _ = _claimed(client, "real@child.lan", "Sam")
    assert claim["permitted"] is True and claim["child_pushed"] is True
    assert rt.client.on(topic)[-1]["child_pii"]["nickname"] == "Sam"
    assert rt.robots[DEVICE].child.nickname == "Sam"
    assert client.get("/local/fleet", headers=auth).json()["robots"][0]["child"] == "Sam"
    assert client.get("/local/fleet").json()["robots"][0]["child"] is None

    gone = client.delete(f"/api/robots/{claim['robot_id']}?rfs=1", headers=auth).json()
    assert gone["unpaired"] is True and gone["child_cleared"] is True
    saved = json.loads((tmp_path / "robots" / DEVICE / "config.json").read_text())
    assert "child" not in saved, saved
    assert rt.robots[DEVICE].child.nickname == "friend"
    assert rt.client.on(topic)[-1]["pairing_status"] == "unpairing"   # pending again

    auth, claim, _ = _claimed(client, "real-unnamed@child.lan")       # "Moxie Kid"
    assert claim["permitted"] is True and claim["child_pushed"] is False
    assert rt.client.on(topic)[-1]["child_pii"]["nickname"] == "friend"
    status = client.get("/local/fleet", headers=auth).json()["robots"][0]
    assert status["child"] == "friend"


@pytest.mark.parametrize("name", [None, "Sam!"], ids=["placeholder", "refused"])
def test_a_robot_that_joins_an_account_never_says_a_name_an_earlier_record_left(
        client, tmp_path, monkeypatch, name):
    """An unpair whose clear the supervisor never got leaves the child's name in the
    robot's saved settings (the unpair's answer says so). When that robot joins another
    account, the account's name replaces it, or, when the account names none (the "Moxie
    Kid" placeholder) or one the rule refuses, the claim clears it: Moxie says the
    appliance's default, never the earlier family's child's name. (Between the permit and
    the clear, a few milliseconds, the robot's config still carries the old name.)"""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime, status_server
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.store import JsonStore
    from moxie_server import supervisor as sv

    class _App(MoxieApp):
        name = "content"

    rt, _ = make_runtime(_App(), device_id=DEVICE, nickname="friend",
                         allow_unverified_bots=False, store=JsonStore(root=str(tmp_path)))
    set_status_url(status_server(rt) + "/status", monkeypatch)
    topic, record = f"/devices/{DEVICE}/config", tmp_path / "robots" / DEVICE / "config.json"
    tag = "placeholder" if name is None else "refused"

    earlier, claim, _ = _claimed(client, f"earlier-{tag}@child.lan", "Zoë")
    assert rt.robots[DEVICE].child.nickname == "Zoë"
    real = sv.post_json

    def clear_lost(path, payload, timeout=3):
        if payload == {"child": None}:            # what `call` answers when it is down
            return {"ok": False, "error": sv.UNREACHABLE, "detail": "refused"}, 503
        return real(path, payload, timeout)

    monkeypatch.setattr(sv, "post_json", clear_lost)
    gone = client.delete(f"/api/robots/{claim['robot_id']}", headers=earlier).json()
    assert gone["unpaired"] is True and gone["child_cleared"] is False
    assert json.loads(record.read_text())["child"] == {"nickname": "Zoë"}     # left behind
    monkeypatch.setattr(sv, "post_json", real)

    later, again, _ = _claimed(client, f"later-{tag}@child.lan", name)
    assert again["permitted"] is True and again["child_pushed"] is False
    assert ("no name" if name is None else "40 letters") in again["reason"]
    assert rt.robots[DEVICE].child.nickname == "friend"
    assert rt.client.on(topic)[-1]["child_pii"]["nickname"] == "friend"
    assert "child" not in json.loads(record.read_text())
    assert client.get("/local/fleet", headers=later).json()["robots"][0]["child"] == "friend"
