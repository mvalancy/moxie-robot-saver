"""
Local Moxie parent-app server — a clean-room reimplementation of
client-service-api.embodied.com (see docs/REST_API.md), plus a small set of
/local/* convenience endpoints for our own web UI and for testing pairing
WITHOUT a physical robot.

Run:  python -m uvicorn moxie_server.main:app --host 0.0.0.0 --port 8000
or:   python server/run.py
"""
from __future__ import annotations
import base64, json, os, secrets
from typing import Optional

from fastapi import Body, FastAPI, Request, Response, Header, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, crypto, diceware, extra_api
from .serializers import user_document, robot_document

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "pairing"))
import moxie_qr  # noqa: E402

# Production OAuth client credentials the original APK sends (docs/REST_API.md §1.5).
# Accepted so a repointed original app also works; our own web client ignores them.
PROD_CLIENT_ID = "1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4"
PROD_CLIENT_SECRET = "OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4"

app = FastAPI(title="Local Moxie Parent-App Server")
db.init()


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _token(access_token: Optional[str]):
    if not access_token:
        raise HTTPException(401, "missing token")
    tok = access_token.split(" ", 1)[-1].strip()      # strip "Bearer "
    u = db.user_by_token(tok)
    if not u:
        raise HTTPException(401, "invalid token")
    return u


def _mint_tokens(user_id: str) -> dict:
    at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    created = db.now_s()
    db.ex("INSERT INTO tokens(access_token,refresh_token,user_id,token_type,scope,created_at,expires_in)"
          " VALUES(?,?,?,?,?,?,?)", (at, rt, user_id, "Bearer", "openid", created, 7200))
    return {"access_token": at, "token_type": "Bearer", "expires_in": 7200,
            "refresh_token": rt, "scope": "openid", "created_at": created}


async def _json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


# ----------------------------------------------------------------------------
# AUTH  (docs/REST_API.md §2)
# ----------------------------------------------------------------------------
@app.post("/api/login/start")
async def login_start(request: Request):
    body = await _json(request)
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    redirect_uri = secrets.token_urlsafe(8)
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    db.ex("INSERT INTO login_codes(email,code,redirect_uri,created_at) VALUES(?,?,?,?)",
          (email, code, redirect_uri, db.now_s()))
    # No email server locally: surface the code so the user (or web UI) can read it.
    print(f"\n[LOGIN CODE] {email} -> {code}\n")
    return {"redirect_uri": redirect_uri, "login_code": code}   # login_code is a local extra


@app.post("/api/login/finish")
async def login_finish(request: Request):
    body = await _json(request)
    code = (body.get("code") or "").strip()
    row = db.q1("SELECT * FROM login_codes WHERE code=? ORDER BY created_at DESC LIMIT 1", (code,))
    if not row:
        raise HTTPException(400, "invalid code")
    email = row["email"]
    u = db.get_user_by_email(email)
    if not u:
        uid = db.create_user(email, {"email": email, "first-name": "", "last-name": "",
                                     "iot-endpoint": 0, "user-type": None,
                                     "coppa-consent-status": "granted", "max-children": 4,
                                     "timezone-id": "America/Los_Angeles"})
    else:
        uid = u["id"]
    db.ex("DELETE FROM login_codes WHERE code=?", (code,))
    return _mint_tokens(uid)


@app.post("/api/oauth/token")
async def oauth_token(request: Request):
    form = await request.form()
    grant = form.get("grant_type")
    if grant == "refresh_token":
        rt = form.get("refresh_token")
        row = db.q1("SELECT * FROM tokens WHERE refresh_token=?", (rt,))
        if not row:
            raise HTTPException(401, "invalid refresh token")
        db.ex("DELETE FROM tokens WHERE refresh_token=?", (rt,))
        return _mint_tokens(row["user_id"])
    if grant == "password":     # legacy/testing path (docs §2 Step 6)
        u = db.get_user_by_email((form.get("username") or "").lower())
        if not u:
            raise HTTPException(401, "no such user")
        return _mint_tokens(u["id"])
    raise HTTPException(400, "unsupported grant_type")


@app.post("/api/login/register")
async def login_register(request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    body = await _json(request)
    db.update_user_attrs(u["id"], {"user-type": "clinician",
                                   "pro-registration-code": body.get("pro_registration_code")})
    return Response(status_code=204)


# ----------------------------------------------------------------------------
# USER  (docs/REST_API.md §3.2)
# ----------------------------------------------------------------------------
@app.get("/api/users/me")
def users_me(include: str = "", authorization: str = Header(None)):
    u = _token(authorization)
    return user_document(u, db.children_of(u["id"]), db.robots_of(u["id"]))


@app.put("/api/users/me")
async def update_user(request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    body = await _json(request)
    patch = body.get("user", body)
    attrs = db.update_user_attrs(u["id"], patch)
    return {"data": {"id": u["id"], "type": "users", "attributes": attrs}}


@app.delete("/api/users/me")
def delete_user(authorization: str = Header(None)):
    u = _token(authorization)
    for t in ("children", "robots"):
        db.ex(f"DELETE FROM {t} WHERE user_id=?", (u["id"],))
    db.ex("DELETE FROM tokens WHERE user_id=?", (u["id"],))
    db.ex("DELETE FROM users WHERE id=?", (u["id"],))
    return Response(status_code=204)


@app.put("/api/secret-key-collection")
async def secret_key_collection(request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    body = await _json(request)
    coll = (body.get("secret_key_collection") or {}).get("secret-keys-indexed-by-public-keys", {})
    for pub, sealed in coll.items():
        db.ex("INSERT OR REPLACE INTO secret_keys(user_id,pubkey_b64,sealed_b64) VALUES(?,?,?)",
              (u["id"], pub, sealed))
    return Response(status_code=204)


@app.get("/api/user-options")
def user_options(authorization: str = Header(None)):
    _token(authorization)
    return {"pro_positions": [], "organization_state": [], "organization_type": []}


# ----------------------------------------------------------------------------
# CHILDREN  (docs/REST_API.md §3.3)
# ----------------------------------------------------------------------------
@app.post("/api/children")
async def create_child(request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    body = await _json(request)
    attrs = body.get("child", body)
    cid = db.new_id()
    db.ex("INSERT INTO children(id,user_id,attributes,created_at) VALUES(?,?,?,?)",
          (cid, u["id"], json.dumps(attrs), db.now_s()))
    if not json.loads(u["attributes"]).get("active-child-id"):
        db.update_user_attrs(u["id"], {"active-child-id": cid})
    return {"data": {"id": cid, "type": "children", "attributes": attrs}}


@app.put("/api/children/{cid}")
async def update_child(cid: str, request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    row = db.q1("SELECT * FROM children WHERE id=? AND user_id=?", (cid, u["id"]))
    if not row:
        raise HTTPException(404, "no such child")
    body = await _json(request)
    patch = body.get("child", body)
    attrs = json.loads(row["attributes"]); attrs.update(patch)
    db.ex("UPDATE children SET attributes=? WHERE id=?", (json.dumps(attrs), cid))
    return {"data": {"id": cid, "type": "children", "attributes": attrs}}


@app.delete("/api/children/{cid}")
def delete_child(cid: str, authorization: str = Header(None)):
    u = _token(authorization)
    db.ex("DELETE FROM children WHERE id=? AND user_id=?", (cid, u["id"]))
    return Response(status_code=204)


@app.get("/api/children/{cid}/pending-info")
def child_pending(cid: str, authorization: str = Header(None)):
    _token(authorization)
    return {"consent_status": "granted", "consent_url": "", "parent_email": ""}


@app.get("/api/content-preferences")
def content_prefs(authorization: str = Header(None)):
    _token(authorization)
    return {"data": []}


# ----------------------------------------------------------------------------
# ROBOT / PAIRING  (docs/REST_API.md §3.4)
# ----------------------------------------------------------------------------
@app.post("/api/pairing-info")
def pairing_info(request: Request, authorization: str = Header(None)):
    """Called by the ORIGINAL app just before it shows the QR. id = hex(SHA256(seed)).
    Query params use hyphens (user-id/child-id), so read them from the raw query.
    We record id_hash -> user/child so a robot that later presents the seed can be
    bound. (Our own web UI uses /local/pairing/prepare, which also stores the seed.)"""
    u = _token(authorization)
    qp = request.query_params
    id_hash = qp.get("id")
    if not id_hash:
        raise HTTPException(400, "id required")
    child_id = qp.get("child-id") or None
    restore = str(qp.get("restore", "false")).lower() == "true"
    db.ex("INSERT OR REPLACE INTO pairings(id_hash,user_id,child_id,restore,consumed,created_at,seed_hex,phrase)"
          " VALUES(?,?,?,?,0,?,NULL,NULL)",
          (id_hash, u["id"], child_id, int(restore), db.now_s()))
    return Response(status_code=204)


@app.get("/api/robots/{rid}")
def get_robot(rid: str, authorization: str = Header(None)):
    u = _token(authorization)
    row = db.q1("SELECT * FROM robots WHERE id=? AND user_id=?", (rid, u["id"]))
    if not row:
        raise HTTPException(404, "no such robot")
    return robot_document(row)


@app.put("/api/robots/{rid}")
async def update_robot(rid: str, request: Request, authorization: str = Header(None)):
    u = _token(authorization)
    row = db.q1("SELECT * FROM robots WHERE id=? AND user_id=?", (rid, u["id"]))
    if not row:
        raise HTTPException(404, "no such robot")
    body = await _json(request)
    if "robot-setting" in body or "robot-settings" in body:
        setting = body.get("robot-setting", body.get("robot-settings"))
        db.ex("UPDATE robots SET robot_setting=? WHERE id=?", (json.dumps(setting), rid))
    else:
        patch = body.get("robot", body)
        attrs = json.loads(row["attributes"]); attrs.update(patch)
        db.ex("UPDATE robots SET attributes=? WHERE id=?", (json.dumps(attrs), rid))
    return robot_document(db.q1("SELECT * FROM robots WHERE id=?", (rid,)))


@app.delete("/api/robots/{rid}")
def delete_robot(rid: str, rfs: str = Query(None), authorization: str = Header(None)):
    u = _token(authorization)
    db.ex("DELETE FROM robots WHERE id=? AND user_id=?", (rid, u["id"]))
    return Response(status_code=204)


@app.post("/api/robots/{rid}/wakeup")
def wakeup(rid: str, authorization: str = Header(None)):
    _token(authorization); return {"error": None}


@app.post("/api/robots/{rid}/reboot")
def reboot(rid: str, authorization: str = Header(None)):
    _token(authorization); return {"error": None}


@app.get("/api/robots/{rid}/ota_status")
def ota_status(rid: str, authorization: str = Header(None)):
    _token(authorization)
    return {"status": "up_to_date", "version": None}


@app.post("/api/robots/{rid}/set-language")
async def set_language(rid: str, request: Request, authorization: str = Header(None)):
    _token(authorization); return Response(status_code=204)


@app.post("/api/robots/{rid}/restores")
async def restores(rid: str, request: Request, authorization: str = Header(None)):
    _token(authorization); return Response(status_code=204)


# ----------------------------------------------------------------------------
# LOCAL convenience + testing endpoints (NOT part of the original API)
# ----------------------------------------------------------------------------
@app.post("/local/quicklogin")
async def quicklogin(request: Request):
    """One-shot login for our web UI: create/get a user by email, return a token
    plus a completed profile so pairing is reachable immediately."""
    body = await _json(request)
    email = (body.get("email") or "parent@local").strip().lower()
    u = db.get_user_by_email(email)
    if not u:
        uid = db.create_user(email, {"email": email, "first-name": body.get("first_name", "Parent"),
                                     "last-name": body.get("last_name", "Local"), "iot-endpoint": 0,
                                     "coppa-consent-status": "granted", "max-children": 4,
                                     "timezone-id": "America/Los_Angeles"})
    else:
        uid = u["id"]
    tokens = _mint_tokens(uid)
    return {"token": tokens["access_token"], "user_id": uid, "email": email}


@app.post("/local/pairing/prepare")
async def pairing_prepare(request: Request, authorization: str = Header(None)):
    """Do the app's whole pre-QR crypto dance server-side and register the pairing.
    Body: {ssid, password, band(any|5g|24g), hidden, passphrase?, restore?, child_id?}
    Returns the QR payload string + the recovery phrase (generated if not supplied)."""
    u = _token(authorization)
    body = await _json(request)
    # recovery phrase -> seed -> keys (mirrors ExportRecoveryKey)
    phrase = (body.get("passphrase") or "").strip() or diceware.generate_phrase()
    keys = crypto.keys_from_passphrase(phrase)
    x_pub_b64 = base64.b64encode(keys.x25519_public).decode()
    db.update_user_attrs(u["id"], {"public-key": x_pub_b64})

    # ensure a child exists (pairing needs child-id; app NPEs otherwise)
    kids = db.children_of(u["id"])
    if body.get("child_id"):
        child_id = body["child_id"]
    elif kids:
        child_id = kids[0]["id"]
    else:
        child_id = db.new_id()
        db.ex("INSERT INTO children(id,user_id,attributes,created_at) VALUES(?,?,?,?)",
              (child_id, u["id"], json.dumps({"child-first-name": "Moxie Kid"}), db.now_s()))
        db.update_user_attrs(u["id"], {"active-child-id": child_id})

    restore = bool(body.get("restore"))
    id_hash = keys.secret_hash_hex
    db.ex("INSERT OR REPLACE INTO pairings(id_hash,user_id,child_id,restore,consumed,created_at,seed_hex,phrase)"
          " VALUES(?,?,?,?,0,?,?,?)",
          (id_hash, u["id"], child_id, int(restore), db.now_s(), keys.seed.hex(), phrase))

    band = {"any": moxie_qr.Band.ANY, "5g": moxie_qr.Band.ONLY_5G,
            "24g": moxie_qr.Band.ONLY_24G}.get(body.get("band", "any"), moxie_qr.Band.ANY)
    wifi = moxie_qr.WifiInfo(body.get("ssid", ""), body.get("password", ""),
                             is_hidden=bool(body.get("hidden")), band=band)
    payload = moxie_qr.encode_proto(wifi, keys.seed,
                                    iot_endpoint=int(json.loads(u["attributes"]).get("iot-endpoint", 0) or 0))
    return {"qr_payload": payload, "recovery_phrase": phrase,
            "secret_hash": id_hash, "child_id": child_id,
            "public_key": x_pub_b64}


import moxie_endpoint_qr  # noqa: E402  (from tools/pairing, on sys.path)

STATUS_URL = os.environ.get("MOXIE_SUPERVISOR_STATUS", "http://127.0.0.1:8930/status")


def _lan_ip() -> str:
    """Best-guess LAN IP of this machine — the address the robot should use to
    reach the broker. Prefer an explicit MOXIE_BROKER_HOST; else the private IP on
    the default route; never a Tailscale/CGNAT address unless it's all we have."""
    env = os.environ.get("MOXIE_BROKER_HOST")
    if env:
        return env
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))    # doesn't send; picks the LAN-facing iface
        ip = s.getsockname()[0]; s.close()
        if ip.startswith(("192.168.", "10.", "172.")) and not ip.startswith("100."):
            return ip
    except Exception:
        pass
    return "192.168.1.9"


@app.get("/local/endpoint/payload")
def endpoint_payload(host: str = "", port: int = 8883):
    """QR #2 payload: repoints Moxie from Embodied's dead cloud to our MQTT broker.
    `host` MUST be an address the ROBOT can reach (its LAN IP) — NOT a Tailscale IP."""
    h = host or _lan_ip()
    return {"qr_payload": moxie_endpoint_qr.build_endpoint_qr(h, port),
            "mqtt_host": h, "mqtt_port": port, "default_host": _lan_ip()}


@app.get("/local/endpoint/qr.png")
def endpoint_qr_png(host: str = "", port: int = 8883, ec: str = "l"):
    import segno, io
    h = host or _lan_ip()
    payload = moxie_endpoint_qr.build_endpoint_qr(h, port)
    buf = io.BytesIO()
    segno.make(payload, error=ec).save(buf, kind="png", scale=10, border=4)
    return Response(content=buf.getvalue(), media_type="image/png")


def _ap_config():
    return {"ssid": os.environ.get("MOXIE_AP_SSID"),
            "password": os.environ.get("MOXIE_AP_PASSWORD"),
            "host": os.environ.get("MOXIE_AP_HOST", _lan_ip())}


@app.get("/local/direct/info")
def direct_info():
    """Moxie Direct mode: this machine hosts its own Wi-Fi AP. Returns everything
    needed to show Moxie two QRs with zero manual entry — a wifi-only QR for the AP,
    then the endpoint QR pointing at the AP IP."""
    ap = _ap_config()
    ready = bool(ap["ssid"] and ap["password"])
    wifi_payload = None
    if ready:
        wifi = moxie_qr.WifiInfo(ap["ssid"], ap["password"], band=moxie_qr.Band.ONLY_24G)
        wifi_payload = moxie_qr.encode_wifi_only(wifi)
    return {"ready": ready, "ssid": ap["ssid"], "password": ap["password"], "host": ap["host"],
            "wifi_qr_payload": wifi_payload,
            "endpoint_qr_payload": moxie_endpoint_qr.build_endpoint_qr(ap["host"])}


@app.get("/local/direct/wifi_qr.png")
def direct_wifi_qr(ec: str = "l"):
    import segno, io
    ap = _ap_config()
    if not (ap["ssid"] and ap["password"]):
        raise HTTPException(404, "Moxie Direct AP not configured")
    wifi = moxie_qr.WifiInfo(ap["ssid"], ap["password"], band=moxie_qr.Band.ONLY_24G)
    buf = io.BytesIO()
    segno.make(moxie_qr.encode_wifi_only(wifi), error=ec).save(buf, kind="png", scale=10, border=4)
    return Response(content=buf.getvalue(), media_type="image/png")


def _fetch_status() -> dict:
    """Fetch the MQTT supervisor's status snapshot; {ok:false,...} if unreachable."""
    import urllib.request
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": "supervisor not reachable", "detail": str(e),
                "robots": [], "recent": []}


@app.get("/local/broker/status")
def broker_status():
    """Proxy the MQTT supervisor's status (connection monitor). Server-side fetch so
    the browser has no CORS issue. Returns {ok:false} if the supervisor isn't running."""
    return _fetch_status()


@app.get("/local/fleet")
def fleet():
    """Parent-console fleet view (M6): the supervisor snapshot normalized into one tidy
    record per connected robot — live state (battery/volume/Wi-Fi/mode/firmware), config
    overrides, telemetry count, and a one-line summary. Graceful {ok:false} when down."""
    from .fleet import normalize_fleet
    return normalize_fleet(_fetch_status())


@app.post("/local/robots/{device_id}/config")
async def set_robot_config(device_id: str, request: Request):
    """Parent-console config edit (M6): forward whitelisted overrides (volume/bedtime/wake
    …) to the supervisor's POST /config, which validates + re-pushes RobotCloudConfig to
    the robot. Server-side call so the browser has no CORS issue."""
    import urllib.request, urllib.error
    body = await request.body()
    url = STATUS_URL.rsplit("/status", 1)[0] + f"/config?device_id={device_id}"
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code, content=json.loads(e.read().decode() or "{}"))
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "supervisor not reachable", "detail": str(e)})


@app.post("/local/fleet/config")
async def set_fleet_config(request: Request):
    """Parent-console **fleet** config edit (audit ADOPT #6): forward the same whitelisted
    overrides to the supervisor's `POST /config?scope=fleet`, which validates them, stores
    them as the appliance-wide defaults and re-pushes every connected robot. A per-robot
    override still wins. Server-side call so the browser has no CORS issue."""
    import urllib.request, urllib.error
    body = await request.body()
    url = STATUS_URL.rsplit("/status", 1)[0] + "/config?scope=fleet"
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code, content=json.loads(e.read().decode() or "{}"))
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "supervisor not reachable", "detail": str(e)})


def _supervisor_post(path: str, payload: dict):
    """POST one JSON body to the supervisor's little status server and return its reply.

    Same server-side-call pattern as the config endpoints above (no CORS problem in the
    browser, no supervisor port exposed to it). Returns `(dict, status_code)`; a
    supervisor that is down is a 503 with a readable body rather than an exception."""
    import urllib.request, urllib.error
    url = STATUS_URL.rsplit("/status", 1)[0] + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode()), 200
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code
    except Exception as e:
        return {"ok": False, "error": "supervisor not reachable", "detail": str(e)}, 503


@app.get("/local/permits")
def get_permits():
    """The device allowlist (audit §3.1 pairing gate): who is permitted, who is pending,
    and whether the appliance is currently serving unverified robots."""
    import urllib.request
    url = STATUS_URL.rsplit("/status", 1)[0] + "/permits"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "supervisor not reachable", "detail": str(e),
            "permits": [], "pending": []})


@app.post("/local/robots/{device_id}/permit")
async def permit_robot(device_id: str, request: Request):
    """Let one pending robot in — the console's one-click **Permit** (body
    `{"permitted": false}` revokes it, `{"label": "…"}` names it). The supervisor stores
    the permit in `fleet/permits.json` and re-pushes that robot's config on the spot, so
    a robot that was pending becomes paired without a reconnect."""
    body = await _json(request)
    out, code = _supervisor_post("/permits", {
        "device_id": device_id,
        "permitted": bool(body.get("permitted", True)),
        "label": body.get("label") or ""})
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.post("/local/fleet/permits")
async def set_fleet_permits(request: Request):
    """The appliance-wide **"serve any robot that connects"** switch
    (`{"allow_unverified_bots": true|false}`). Off is the safe default; on restores the
    pre-gate behavior for a deployment that was running before the allowlist existed.
    Flipping it re-pushes every connected robot's config."""
    body = await _json(request)
    out, code = _supervisor_post(
        "/permits", {"allow_unverified_bots": bool(body.get("allow_unverified_bots"))})
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.get("/local/robots/{device_id}/telemetry")
def robot_telemetry(device_id: str, limit: int = 20):
    """Parent-console insights (M6): the robot's stored telemetry Packets, fetched from
    the supervisor's GET /telemetry and normalized for the UI (counts by event + the
    newest events). Server-side call so the browser has no CORS issue; graceful
    {ok:false} when the supervisor is down or the device is unknown."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_telemetry
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/telemetry?device_id={quote(device_id)}&limit={int(limit)}")
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return normalize_telemetry(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        return JSONResponse(status_code=e.code, content=normalize_telemetry(body))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_telemetry(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


@app.get("/local/robots/{device_id}/safety")
def robot_safety(device_id: str, limit: int = 20):
    """Parent-console safety review queue (ai-seam §2): every block/flag the runtime's
    `InputSafety` classifier recorded for this robot, fetched from the supervisor's
    GET /safety and normalized for the UI (counts by category + the newest events).
    Server-side call so the browser has no CORS issue; graceful {ok:false} when the
    supervisor is down or the device is unknown."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_safety
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/safety?device_id={quote(device_id)}&limit={int(limit)}")
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return normalize_safety(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        return JSONResponse(status_code=e.code, content=normalize_safety(body))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_safety(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


@app.post("/local/robots/{device_id}/safety")
async def acknowledge_robot_safety(device_id: str, request: Request):
    """Parent acknowledges safety events — `{"event_id": "sfe-…"}` for one, `{}` for all.
    Forwarded to the supervisor's POST /safety, which marks them reviewed and returns the
    refreshed queue."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_safety
    body = await request.body()
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/safety?device_id={quote(device_id)}")
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return normalize_safety(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code,
                            content=normalize_safety(json.loads(e.read().decode() or "{}")))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_safety(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


# --- 🎭 "Be Moxie": puppet / telehealth mode (audit ADOPT #7) -------------------------
# A remote grown-up types a line and the robot says it, in a mood they picked, with the
# robot's own brain switched off so there is only one voice in the room. Two thin proxies,
# exactly like the other cards: the runtime does the protocol, the safety check and the
# journalling; this layer only forwards and normalizes.
#
# The one behaviour worth naming here: a line the safety classifier BLOCKS comes back as a
# **400 with its reason**, and the card shows it to the operator so they can rephrase. It
# is never silently rewritten — a human is at the keyboard, and substituting a redirect
# for a clinician's sentence would be both useless and dishonest.

@app.get("/local/robots/{device_id}/telehealth")
def robot_telehealth(device_id: str):
    """The 🎭 card's poll: is puppet mode on, is there a session, what state did the ROBOT
    report (empty = never reported), is the robot inside its bedtime window, and the live
    text transcript. Server-side call so the browser has no CORS issue; graceful
    {ok:false} when the supervisor is down or the device is unknown."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_telehealth
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/telehealth?device_id={quote(device_id)}")
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return normalize_telehealth(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        return JSONResponse(status_code=e.code, content=normalize_telehealth(body))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_telehealth(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


@app.post("/local/robots/{device_id}/telehealth")
async def drive_robot_telehealth(device_id: str, request: Request):
    """One operator verb — `{"action": "enable"|"disable"|"start"|"end"|"state"|
    "speak"|"interrupt"}`, with `{"text", "mood", "intensity"}` on a speak. Forwarded to
    the supervisor's `POST /telehealth`, which runs the permit check, the mode gate, the
    safety classifier and the markup, then publishes `commands/telehealth`.

    A refused line keeps the supervisor's status code — **400 with `reason`** for a safety
    block or a mode that is off, 404 for a robot that is not connected — so the card can
    tell the operator what to do instead of silently doing nothing."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_telehealth
    body = await request.body()
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/telehealth?device_id={quote(device_id)}")
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return normalize_telehealth(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code,
                            content=normalize_telehealth(json.loads(e.read().decode() or "{}")))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_telehealth(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


# --- 📅 Today's plan — "why this activity today" (audit BEYOND #7) --------------------
# The supervisor plans the day the robot pulls (`moxie_sdk/schedule.py::plan_day`) and
# keeps a parallel, parent-readable audit trail of *why* every entry is on it, served at
# `GET /schedule?device_id=…`. Until now nothing but `curl` read it. This is the same thin
# proxy shape as the 🎨 look and 🎭 Be Moxie cards: the runtime owns the planning, this
# layer only forwards and normalizes, and a supervisor that is down is a 503 carrying the
# card's own shape rather than a 500.
#
# Read-only on purpose. `GET /schedule` re-plans when nothing is stored yet, so a poll
# from the console can show a parent tomorrow's reasoning *before* the robot wakes — but
# the card must never look like a control, because nothing here changes the day. Editing a
# plan is `POST /config` (bedtime, `schedule_preferences.parent_requests`), which the
# ⚙️ Settings form already owns.

@app.get("/local/robots/{device_id}/schedule")
def robot_schedule(device_id: str, refresh: bool = False):
    """The 📅 card's poll: today's plan for one robot, one *why* line per entry, and the
    constraints the planner reported (bedtime window, pinned parent requests, and whether
    telemetry carries any module signal — it does not). Server-side call so the browser
    has no CORS issue; graceful {ok:false} when the supervisor is down or the device is
    unknown."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_schedule_view
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/schedule?device_id={quote(device_id)}")
    if refresh:
        url += "&refresh=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return normalize_schedule_view(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        return JSONResponse(status_code=e.code, content=normalize_schedule_view(body))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_schedule_view(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


# --- 🎚️ The voice picker (backlog/voice-picker.md) -----------------------------------
# Which voice Moxie speaks with and which ears she listens with, chosen from what this
# appliance can genuinely use — the gateway's audio models (discovered live), the local
# Piper voices and whisper sizes installed on the box, and the two built-ins. Three thin
# proxies in the shape every other card uses: the supervisor owns discovery, validation,
# persistence and the engine swap; this layer forwards and normalizes.
#
# The record is **fleet-level** — a voice is a property of the house, not of one robot —
# so `GET`/`POST` ignore `device_id` beyond the console's URL convention. `POST …/voice/test`
# is the one that needs it: it names the robot that should play the sample line.

@app.get("/local/robots/{device_id}/voice")
def robot_voice(device_id: str, refresh: bool = False):
    """The 🎚️ card's poll: every speech/listening option this appliance can use, which one
    is in force, which one is the default, what is actually installed, and whether the
    gateway listing is still on its way. Server-side call so the browser has no CORS
    issue; a supervisor that is down is a 503 carrying the card's own shape."""
    import urllib.request, urllib.error
    from .fleet import normalize_voice
    url = STATUS_URL.rsplit("/status", 1)[0] + "/voice"
    if refresh:
        url += "?refresh=1"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return normalize_voice(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        return JSONResponse(status_code=e.code, content=normalize_voice(body))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_voice(
            {"ok": False, "error": "supervisor not reachable", "detail": str(e)}))


@app.post("/local/robots/{device_id}/voice")
async def set_robot_voice(device_id: str, request: Request):
    """A parent's pick — `{"speech": "gateway:piper-amy", "listening": "whisper:base.en"}`,
    either side optional, `null` to go back to the default. Forwarded to the supervisor's
    `POST /voice`, which checks it against what is available *right now*, persists it to
    `fleet/voice.json` and swaps the live engines.

    A pick the supervisor refuses keeps its status code — **400 with `reason`** — so the
    card can tell a parent their page was stale instead of silently doing nothing."""
    import urllib.request, urllib.error
    from .fleet import normalize_voice
    body = await request.body()
    url = STATUS_URL.rsplit("/status", 1)[0] + "/voice"
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return normalize_voice(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code,
                            content=normalize_voice(json.loads(e.read().decode() or "{}")))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_voice(
            {"ok": False, "error": "supervisor not reachable", "detail": str(e)}))


@app.post("/local/robots/{device_id}/voice/test")
async def test_robot_voice(device_id: str, request: Request):
    """The **Test** button: speak one line through the engine that is actually installed
    and send it to this robot, which the SIM plays. The only honest answer to "did my pick
    work" — it exercises the live engine rather than echoing the record back."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_voice
    body = await request.body()
    url = (STATUS_URL.rsplit("/status", 1)[0] +
           f"/voice/test?device_id={quote(device_id)}")
    req = urllib.request.Request(url, data=body or b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return normalize_voice(json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        return JSONResponse(status_code=e.code,
                            content=normalize_voice(json.loads(e.read().decode() or "{}")))
    except Exception as e:
        return JSONResponse(status_code=503, content=normalize_voice(
            {"ok": False, "device_id": device_id, "error": "supervisor not reachable",
             "detail": str(e)}))


# --- 🧠 What Moxie remembers (audit BEYOND #4) ---------------------------------------
# The runtime stores durable, provenance-carrying facts per robot
# (`robots/<id>/memory.json`, moxie_sdk/store.py::MemoryStore) and serves them on its
# localhost status server. A memory a parent cannot read or erase is not acceptable on a
# child's device, so the console proxies every verb here rather than leaving them to
# `curl`. Granularity is exactly what the runtime offers, and that is now **one item, one
# activity, or all of it** — plus an edit, because a summary is more often wrong in a word
# than worthless ("Puppy sleeps on *his* bed" for "my bed" is a real line from our own
# live run, and erasing the activity to fix it costs everything else Moxie learned).

def _memory_request(device_id: str, method: str = "GET", namespace: str = "",
                    item: str = "", body: dict | None = None):
    """Call the supervisor's `/memory` for one robot and normalize the reply.

    Same server-side-call shape as the telemetry/safety proxies (no CORS problem in the
    browser, no supervisor port exposed to it). A supervisor that is down is a 503 whose
    body is still the console's memory shape with `ok:false` — never a 500."""
    import urllib.request, urllib.error
    from urllib.parse import quote
    from .fleet import normalize_memory
    url = STATUS_URL.rsplit("/status", 1)[0] + f"/memory?device_id={quote(device_id)}"
    if namespace:
        url += f"&namespace={quote(namespace)}"
    if item:
        url += f"&item={quote(item)}"
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, method=method, data=data)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=3) as r:
            return normalize_memory(json.loads(r.read().decode())), 200
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"ok": False, "error": raw[:200]}
        return normalize_memory(payload), e.code
    except Exception as e:
        return normalize_memory({"ok": False, "device_id": device_id,
                                 "error": "supervisor not reachable",
                                 "detail": str(e)}), 503


@app.get("/local/robots/{device_id}/memory")
def robot_memory(device_id: str):
    """What Moxie remembers about this child, by activity, with the date and the module
    each item came from — plus whether writing new memories is currently allowed
    (`LoggingPolicy.NO_DATA` stops writes; reads and erase always work)."""
    out, code = _memory_request(device_id)
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.delete("/local/robots/{device_id}/memory")
def forget_all_memory(device_id: str):
    """Erase **everything** Moxie remembers about this child. Never policy-gated: a
    parent must always be able to delete. Returns the (now empty) memory view."""
    out, code = _memory_request(device_id, method="DELETE")
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.delete("/local/robots/{device_id}/memory/{namespace}")
def forget_memory_namespace(device_id: str, namespace: str):
    """Erase one activity's memory (one namespace) — everything it ever learned."""
    out, code = _memory_request(device_id, method="DELETE", namespace=namespace)
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.delete("/local/robots/{device_id}/memory/{namespace}/{item}")
def forget_memory_item(device_id: str, namespace: str, item: str):
    """Forget exactly one remembered line, by its id. The finest cut there is: everything
    else that activity learned stays, and so does how far it had summarized."""
    out, code = _memory_request(device_id, method="DELETE", namespace=namespace,
                                item=item)
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.post("/local/robots/{device_id}/memory/{namespace}/{item}")
def correct_memory_item(device_id: str, namespace: str, item: str,
                        body: dict = Body(default=None)):
    """Correct one remembered line — `{"text": "…"}`.

    The supervisor re-runs the safety classifier and the no-verbatim check on the new
    wording (a text box that writes into every later prompt is the one place those rules
    matter most) and **pins** the result, which takes it out of decay. A refused edit
    comes back as a 400 carrying the reason, not a silent no-op."""
    text = str((body or {}).get("text") or "")
    out, code = _memory_request(device_id, method="POST",
                                body={"edit": {"namespace": namespace, "item": item,
                                               "text": text}})
    return out if code == 200 else JSONResponse(status_code=code, content=out)


@app.get("/local/pairing/qr.png")
def pairing_qr_png(payload: str, ec: str = "l"):
    # The original app rendered with ZXing EC level L (low density) because Moxie's
    # camera struggles with dense codes. Match that by default.
    import segno, io
    buf = io.BytesIO()
    segno.make(payload, error=ec).save(buf, kind="png", scale=10, border=4)
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/local/simulate-robot-scan")
async def simulate_robot_scan(request: Request):
    """Pretend a robot scanned the QR: decode it, recompute SHA256(seed), find the
    pending pairing, and create the robot record bound to the user/child — exactly
    what a real Moxie does when it phones home. Lets you test the full flow with no
    hardware. Body: `{qr_payload, device_id?}`.

    **Auto-permit (the pairing gate, audit §3.1).** Pairing *is* the parent saying "this
    robot is mine", so a robot that completes pairing here should not then need a second
    click in the fleet panel. When `device_id` (the MQTT client id, `d_<uuid>`) is given,
    this permits it on the supervisor as part of completing the pairing, and reports the
    outcome as `permitted` / `permit_error`. It is best-effort: a supervisor that is down
    never fails the pairing itself — the robot simply shows up as pending, which the
    console's Permit button handles.

    `device_id` is optional because the QR carries no device id (it carries Wi-Fi + the
    pairing seed), so the *pairing* half of the system genuinely does not learn the
    robot's MQTT identity until the robot connects to the broker. The console's Simulate
    button passes the pending robot's id when there is exactly one; a real robot's path is
    still "connects → pending → Permit". See docs/guides/permitting-a-robot.md."""
    body = await _json(request)
    payload = body.get("qr_payload", "")
    decoded = moxie_qr.decode_proto(payload)
    seed = decoded.get("secret_key")
    if not seed:
        raise HTTPException(400, "QR carries no secret_key (wifi-only)")
    import hashlib
    id_hash = hashlib.sha256(seed).hexdigest()
    pairing = db.q1("SELECT * FROM pairings WHERE id_hash=?", (id_hash,))
    if not pairing:
        raise HTTPException(404, "no pending pairing matches this QR")
    # robot derives its own identity keypair from the same seed (as firmware would)
    keys = crypto.keys_from_seed(seed)
    rid = db.new_id()
    robot_attrs = {"embodied-robot-id": rid, "serial": "SIM-" + rid[:8],
                   "public-key": base64.b64encode(keys.x25519_public).decode(),
                   "wifi-ssid": decoded.get("ssid"), "name": "Moxie (simulated)",
                   "state": "paired", "pairing-status": "paired"}
    db.ex("INSERT INTO robots(id,user_id,child_id,attributes,robot_setting,last_seen_at,created_at)"
          " VALUES(?,?,?,?,?,?,?)",
          (rid, pairing["user_id"], pairing["child_id"], json.dumps(robot_attrs),
           json.dumps({"volume": 0.7, "screen-brightness": 0.8}), db.now_s(), db.now_s()))
    db.ex("UPDATE pairings SET consumed=1 WHERE id_hash=?", (id_hash,))
    out = {"robot_id": rid, "bound_user": pairing["user_id"],
           "bound_child": pairing["child_id"], "ssid": decoded.get("ssid"),
           "permitted": False, "permit_error": None}
    device_id = (body.get("device_id") or "").strip()
    if device_id:
        res, code = _supervisor_post("/permits", {
            "device_id": device_id, "permitted": True, "label": "paired via console"})
        out["device_id"] = device_id
        out["permitted"] = bool(code == 200 and res.get("ok"))
        if not out["permitted"]:
            out["permit_error"] = res.get("error") or f"supervisor returned {code}"
    return out


@app.get("/local/state")
def local_state(authorization: str = Header(None)):
    u = _token(authorization)
    return {"user": {"id": u["id"], **json.loads(u["attributes"])},
            "children": [{"id": c["id"], **json.loads(c["attributes"])} for c in db.children_of(u["id"])],
            "robots": [{"id": r["id"], **json.loads(r["attributes"])} for r in db.robots_of(u["id"])]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


extra_api.register(app)   # the rest of the RE'd REST surface (include_router is broken in this env)

# static web client at /
if os.path.isdir(_STATIC):
    app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
