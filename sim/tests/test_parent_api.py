"""The parent-app REST surface: login, the account, children, pairing, robots.

The half that needs no supervisor (`test_console_*.py` cover the `/local/*` proxies):
the clean-room `client-service-api` contract (`docs/architecture/rest-api-contract.md`)
and the local pairing helpers, in-process with a throwaway database.
"""
import hashlib
import sys
import os

import pytest

from helpers_console import REPO, console_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    TestClient, main = console_app(tmp_path_factory.mktemp("parent-api") / "api.db")
    with TestClient(main.app) as c:
        yield c


def _login(client, email):
    code = client.post("/api/login/start", json={"email": email}).json()["login_code"]
    tokens = client.post("/api/login/finish", json={"code": code}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens


def _pair(client, auth, **body):
    prep = client.post("/local/pairing/prepare", headers=auth,
                       json={"ssid": "Home", "password": "pw", **body})
    assert prep.status_code == 200, prep.text
    return prep.json()


def test_the_email_code_login_mints_a_working_token(client):
    auth, tokens = _login(client, "Login@Example.lan")
    assert tokens["token_type"] == "Bearer" and tokens["refresh_token"]
    me = client.get("/api/users/me", headers=auth).json()
    assert me["data"]["attributes"]["email"] == "login@example.lan"


def test_no_token_and_a_bad_token_are_401(client):
    assert client.get("/api/users/me").status_code == 401
    assert client.get("/api/users/me",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_a_refresh_rotates_the_token_pair(client):
    auth, tokens = _login(client, "refresh@example.lan")
    new = client.post("/api/oauth/token", data={"grant_type": "refresh_token",
                                                 "refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    assert client.get("/api/users/me", headers=auth).status_code == 401
    fresh = {"Authorization": f"Bearer {new.json()['access_token']}"}
    assert client.get("/api/users/me", headers=fresh).status_code == 200
    again = client.post("/api/oauth/token", data={"grant_type": "refresh_token",
                                                   "refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


def test_children_are_created_patched_and_scoped_to_their_parent(client):
    auth, _ = _login(client, "kids@example.lan")
    other, _ = _login(client, "stranger@example.lan")
    cid = client.post("/api/children", headers=auth,
                      json={"child": {"child-first-name": "Ada"}}).json()["data"]["id"]
    r = client.put(f"/api/children/{cid}", headers=auth, json={"child": {"age": 6}})
    assert r.json()["data"]["attributes"] == {"child-first-name": "Ada", "age": 6}
    assert client.put(f"/api/children/{cid}", headers=other, json={}).status_code == 404
    state = client.get("/local/state", headers=auth).json()
    assert state["user"]["active-child-id"] == cid


def test_pairing_binds_a_robot_to_the_parent_and_child(client):
    auth, _ = _login(client, "pair@example.lan")
    prep = _pair(client, auth)
    r = client.post("/local/simulate-robot-scan", json={"qr_payload": prep["qr_payload"]})
    assert r.status_code == 200, r.text
    assert r.json()["bound_child"] == prep["child_id"] and r.json()["ssid"] == "Home"
    robot = client.get(f"/api/robots/{r.json()['robot_id']}", headers=auth).json()
    assert robot["data"]["attributes"]["public-key"] == prep["public_key"]
    sys.path.insert(0, os.path.join(REPO, "tools", "pairing"))
    import moxie_qr
    seed = moxie_qr.decode_proto(prep["qr_payload"])["secret_key"]
    assert hashlib.sha256(seed).hexdigest() == prep["secret_hash"]


def test_a_pairing_qr_completes_once_not_once_per_scan(client):
    """Regression: `consumed` was written and never read, so every re-scan of the same
    QR minted another robot record for the parent."""
    auth, _ = _login(client, "replay@example.lan")
    qr = _pair(client, auth)["qr_payload"]
    assert client.post("/local/simulate-robot-scan", json={"qr_payload": qr}).status_code == 200
    again = client.post("/local/simulate-robot-scan", json={"qr_payload": qr})
    assert again.status_code == 409
    assert len(client.get("/local/state", headers=auth).json()["robots"]) == 1


def test_pairing_refuses_another_parents_child(client):
    """Regression: `child_id` in the prepare body was trusted as-is, so a robot could be
    bound to a child the caller does not own."""
    auth, _ = _login(client, "mine@example.lan")
    other, _ = _login(client, "theirs@example.lan")
    theirs = client.post("/api/children", headers=other,
                         json={"child": {"child-first-name": "Bo"}}).json()["data"]["id"]
    r = client.post("/local/pairing/prepare", headers=auth,
                    json={"ssid": "Home", "password": "pw", "child_id": theirs})
    assert r.status_code == 404


def test_deleting_the_account_removes_its_keys_and_pairing_secrets(client):
    """Account deletion must take the sealed keys, the pairing rows (seed AND recovery
    phrase in clear) and the push registrations with it."""
    from moxie_server import db
    auth, _ = _login(client, "gone@example.lan")
    uid = client.get("/local/state", headers=auth).json()["user"]["id"]
    _pair(client, auth)
    client.put("/api/secret-key-collection", headers=auth, json={
        "secret_key_collection": {"secret-keys-indexed-by-public-keys": {"pub": "sealed"}}})
    client.post("/api/mobile-devices", headers=auth, json={"mobile-device": {"os": "x"}})
    assert client.delete("/api/users/me", headers=auth).status_code == 204
    for table in ("users", "tokens", "children", "robots", "pairings",
                  "secret_keys", "mobile_devices"):
        col = "id" if table == "users" else "user_id"
        assert not db.q(f"SELECT 1 FROM {table} WHERE {col}=?", (uid,)), table
    assert client.get("/api/users/me", headers=auth).status_code == 401


def test_the_qr_image_routes_render_pngs(client):
    auth, _ = _login(client, "png@example.lan")
    qr = _pair(client, auth)["qr_payload"]
    for path, params in (("/local/pairing/qr.png", {"payload": qr}),
                         ("/local/endpoint/qr.png", {"host": "192.168.1.5"})):
        r = client.get(path, params=params)
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", path
    ep = client.get("/local/endpoint/payload", params={"host": "10.0.0.2"}).json()
    assert ep["mqtt_host"] == "10.0.0.2" and ep["qr_payload"].startswith('{"debug"')


def test_reboot_is_501_and_the_stub_surface_answers(client):
    auth, _ = _login(client, "stubs@example.lan")
    assert client.post("/api/robots/x/reboot", headers=auth).status_code == 501
    assert client.get("/api/notifications", headers=auth).json()["meta"] == {"unread": 0}
    assert client.get("/api/help/faq", headers=auth).json() == {"data": [], "path": "faq"}
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/healthz").json() == {"ok": True}


def test_the_console_escapes_quotes_in_attribute_values():
    """`escapeHtml` output lands inside `data-id="…"`/`title="…"` attributes, and a device
    id is whatever connected to an anonymous broker — quotes must be escaped too."""
    import json
    import re
    import shutil
    import subprocess
    from helpers_console import console_js
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    fn = re.search(r"function escapeHtml\(s\)\{.*?\}\n", console_js()).group(0)
    evil = 'd_x" onmouseover="alert(1)\'<b>'
    out = subprocess.run([node, "-e", fn + f"process.stdout.write(escapeHtml({json.dumps(evil)}))"],
                         capture_output=True, text=True, check=True).stdout
    assert '"' not in out and "'" not in out and "<" not in out
    assert out == "d_x&quot; onmouseover=&quot;alert(1)&#39;&lt;b&gt;"
