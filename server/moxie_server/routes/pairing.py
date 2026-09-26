"""`/local/*` setup helpers for our own web client — not part of the original API.

Login without an email round trip, the whole pre-QR crypto dance server-side, the QR
images (EC level L by default: the original app used ZXing L because Moxie's camera
struggles with dense codes), Moxie Direct, and `simulate-robot-scan`, which completes a
pairing with no hardware.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import socket
import sys

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import crypto, db, diceware, supervisor
from ..auth import current_user, mint_tokens, read_json
from .account import create_child_row, user_id_for
from .robots import register_pairing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "pairing"))
import moxie_endpoint_qr  # noqa: E402
import moxie_qr  # noqa: E402

router = APIRouter()
BANDS = {"any": moxie_qr.Band.ANY, "5g": moxie_qr.Band.ONLY_5G, "24g": moxie_qr.Band.ONLY_24G}


def png(payload: str, ec: str = "l") -> Response:
    import segno
    buf = io.BytesIO()
    segno.make(payload, error=ec).save(buf, kind="png", scale=10, border=4)
    return Response(content=buf.getvalue(), media_type="image/png")


def lan_ip() -> str:
    """The address the ROBOT should use to reach the broker: `MOXIE_BROKER_HOST`, else the
    private IP on the default route (never a Tailscale/CGNAT 100.x address)."""
    env = os.environ.get("MOXIE_BROKER_HOST")
    if env:
        return env
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))    # sends nothing; picks the LAN-facing iface
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith(("192.168.", "10.", "172.")):
            return ip
    except Exception:
        pass
    return "192.168.1.9"


@router.post("/local/quicklogin")
async def quicklogin(request: Request):
    """Create-or-get a user by email and return a token — no login code round trip."""
    body = await read_json(request)
    email = (body.get("email") or "parent@local").strip().lower()
    uid = user_id_for(email, **{"first-name": body.get("first_name", "Parent"),
                                "last-name": body.get("last_name", "Local")})
    return {"token": mint_tokens(uid)["access_token"], "user_id": uid, "email": email}


@router.post("/local/pairing/prepare")
async def pairing_prepare(request: Request, u=Depends(current_user)):
    """Recovery phrase → seed → keys, register the pairing, return the QR payload.
    Body: `{ssid, password, band(any|5g|24g), hidden, passphrase?, restore?, child_id?}`."""
    body = await read_json(request)
    phrase = (body.get("passphrase") or "").strip() or diceware.generate_phrase()
    keys = crypto.keys_from_passphrase(phrase)
    x_pub_b64 = base64.b64encode(keys.x25519_public).decode()
    db.update_user_attrs(u["id"], {"public-key": x_pub_b64})

    # pairing needs a child (the original app NPEs without one), and it must be theirs
    kids = db.children_of(u["id"])
    if body.get("child_id"):
        child_id = body["child_id"]
        if child_id not in {k["id"] for k in kids}:
            raise HTTPException(404, "no such child")
    elif kids:
        child_id = kids[0]["id"]
    else:
        child_id = create_child_row(u["id"], {"child-first-name": "Moxie Kid"})

    register_pairing(keys.secret_hash_hex, u["id"], child_id, bool(body.get("restore")),
                     keys.seed.hex(), phrase)
    wifi = moxie_qr.WifiInfo(body.get("ssid", ""), body.get("password", ""),
                             is_hidden=bool(body.get("hidden")),
                             band=BANDS.get(body.get("band", "any"), moxie_qr.Band.ANY))
    iot = int(json.loads(u["attributes"]).get("iot-endpoint", 0) or 0)
    return {"qr_payload": moxie_qr.encode_proto(wifi, keys.seed, iot_endpoint=iot),
            "recovery_phrase": phrase, "secret_hash": keys.secret_hash_hex,
            "child_id": child_id, "public_key": x_pub_b64}


@router.get("/local/pairing/qr.png")
def pairing_qr_png(payload: str, ec: str = "l"):
    return png(payload, ec)


@router.get("/local/endpoint/payload")
def endpoint_payload(host: str = "", port: int = 8883):
    """QR #2: repoint Moxie at our MQTT broker. `host` must be reachable by the ROBOT."""
    h = host or lan_ip()
    return {"qr_payload": moxie_endpoint_qr.build_endpoint_qr(h, port),
            "mqtt_host": h, "mqtt_port": port, "default_host": lan_ip()}


@router.get("/local/endpoint/qr.png")
def endpoint_qr_png(host: str = "", port: int = 8883, ec: str = "l"):
    return png(moxie_endpoint_qr.build_endpoint_qr(host or lan_ip(), port), ec)


def _ap():
    ssid, pw = os.environ.get("MOXIE_AP_SSID"), os.environ.get("MOXIE_AP_PASSWORD")
    wifi = (moxie_qr.encode_wifi_only(moxie_qr.WifiInfo(ssid, pw, band=moxie_qr.Band.ONLY_24G))
            if ssid and pw else None)
    return ssid, pw, os.environ.get("MOXIE_AP_HOST", lan_ip()), wifi


@router.get("/local/direct/info")
def direct_info():
    """Moxie Direct: this machine hosts its own AP, so both QRs need no typing — a
    Wi-Fi-only code for the AP, then the endpoint code pointing at the AP's IP."""
    ssid, pw, host, wifi = _ap()
    return {"ready": wifi is not None, "ssid": ssid, "password": pw, "host": host,
            "wifi_qr_payload": wifi,
            "endpoint_qr_payload": moxie_endpoint_qr.build_endpoint_qr(host)}


@router.get("/local/direct/wifi_qr.png")
def direct_wifi_qr(ec: str = "l"):
    wifi = _ap()[3]
    if wifi is None:
        raise HTTPException(404, "Moxie Direct AP not configured")
    return png(wifi, ec)


@router.post("/local/simulate-robot-scan")
async def simulate_robot_scan(request: Request):
    """Do what a real Moxie does when it phones home after scanning: decode the QR, find
    the pending pairing by SHA256(seed), bind a robot record to that parent and child.
    Body: `{qr_payload, device_id?}`.

    A pairing completes once; a replayed QR is a 409. With `device_id` (the MQTT
    `d_<uuid>`, which the QR does not carry) it also permits that robot on the supervisor
    and remembers the id on the record for later device commands — best-effort: a down
    supervisor leaves the robot pending, it never fails the pairing."""
    body = await read_json(request)
    decoded = moxie_qr.decode_proto(body.get("qr_payload", ""))
    seed = decoded.get("secret_key")
    if not seed:
        raise HTTPException(400, "QR carries no secret_key (wifi-only)")
    id_hash = hashlib.sha256(seed).hexdigest()
    pairing = db.q1("SELECT * FROM pairings WHERE id_hash=?", (id_hash,))
    if not pairing:
        raise HTTPException(404, "no pending pairing matches this QR")
    if pairing["consumed"]:
        raise HTTPException(409, "this pairing QR has already been used")
    keys = crypto.keys_from_seed(seed)      # the robot derives its identity from the seed
    rid = db.new_id()
    attrs = {"embodied-robot-id": rid, "serial": "SIM-" + rid[:8],
             "public-key": base64.b64encode(keys.x25519_public).decode(),
             "wifi-ssid": decoded.get("ssid"), "name": "Moxie (simulated)",
             "state": "paired", "pairing-status": "paired"}
    out = {"robot_id": rid, "bound_user": pairing["user_id"],
           "bound_child": pairing["child_id"], "ssid": decoded.get("ssid"),
           "permitted": False, "permit_error": None}
    device_id = (body.get("device_id") or "").strip()
    if device_id:
        res, code = supervisor.post_json("/permits", {
            "device_id": device_id, "permitted": True, "label": "paired via console"})
        attrs["mqtt-device-id"] = device_id
        out["device_id"] = device_id
        out["permitted"] = bool(code == 200 and res.get("ok"))
        if not out["permitted"]:
            out["permit_error"] = res.get("error") or f"supervisor returned {code}"
    db.ex("INSERT INTO robots(id,user_id,child_id,attributes,robot_setting,last_seen_at,created_at)"
          " VALUES(?,?,?,?,?,?,?)",
          (rid, pairing["user_id"], pairing["child_id"], json.dumps(attrs),
           json.dumps({"volume": 0.7, "screen-brightness": 0.8}), db.now_s(), db.now_s()))
    db.ex("UPDATE pairings SET consumed=1 WHERE id_hash=?", (id_hash,))
    return out


@router.get("/local/state")
def local_state(u=Depends(current_user)):
    def rows(rs):
        return [{"id": r["id"], **json.loads(r["attributes"])} for r in rs]
    return {"user": {"id": u["id"], **json.loads(u["attributes"])},
            "children": rows(db.children_of(u["id"])), "robots": rows(db.robots_of(u["id"]))}


@router.get("/healthz")
def healthz():
    return {"ok": True}
