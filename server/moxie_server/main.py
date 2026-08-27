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

from fastapi import FastAPI, Request, Response, Header, HTTPException, Query
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


@app.get("/local/broker/status")
def broker_status():
    """Proxy the MQTT supervisor's status (connection monitor). Server-side fetch so
    the browser has no CORS issue. Returns {ok:false} if the supervisor isn't running."""
    import urllib.request
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": "supervisor not reachable", "detail": str(e),
                "robots": [], "recent": []}


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
    hardware. Body: {qr_payload}"""
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
    return {"robot_id": rid, "bound_user": pairing["user_id"], "bound_child": pairing["child_id"],
            "ssid": decoded.get("ssid")}


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
