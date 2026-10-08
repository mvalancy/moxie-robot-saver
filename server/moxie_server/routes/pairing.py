"""`/local/*` setup helpers for our own web client — not part of the original API.

Login without an email round trip, the Wi-Fi-only first code, the whole pre-QR crypto
dance server-side (for the original app's pairing-key code), the QR images (EC level L by
default: the original app used ZXing L because Moxie's camera struggles with dense codes),
the factory-reset code, Moxie Direct, `simulate-robot-scan`, which completes a pairing
with no hardware, and the claim that adds a robot which paired by QR to the parent's
account.
"""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import socket
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import crypto, db, diceware, lifecycle, supervisor
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


@router.post("/local/wifi/payload")
async def wifi_payload(request: Request):
    """The Wi-Fi tab's code: Wi-Fi ONLY (`StartPairingQR.wifi_only`, field 5, and no
    pairing key), the first code of the re-home flow. A pairing key in that code sends
    the robot looking for the original cloud (`docs/debugging/live-hardware-debug.md`);
    `encode_wifi_only` is byte-identical to OpenMoxie's Wi-Fi code. Nothing is registered:
    once the robot reaches the broker it is added with Add to my account (the claim).
    Body: `{ssid, password, band(any|5g|24g), hidden}`."""
    body = await read_json(request)
    ssid = str(body.get("ssid") or "").strip()
    if not ssid:
        raise HTTPException(400, "ssid required")
    wifi = moxie_qr.WifiInfo(ssid, str(body.get("password") or ""),
                             is_hidden=bool(body.get("hidden")),
                             band=BANDS.get(body.get("band", "any"), moxie_qr.Band.ANY))
    return {"qr_payload": moxie_qr.encode_wifi_only(wifi), "wifi_only": True}


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


@router.get("/local/factory-reset/payload")
def factory_reset_payload():
    """The `restore_factory` setup code and its instructions, each step labelled with
    where it comes from. Public like the other QR routes: the code is fixed and documented."""
    return lifecycle.reset_view()


@router.get("/local/factory-reset/qr.png")
def factory_reset_qr_png(ec: str = "l"):
    return png(lifecycle.RESTORE_FACTORY_QR, ec)


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


#: Why Simulate robot scan refuses a pairing-key code that is no longer open.
CODE_VOIDED = ("this pairing QR was cancelled when a robot was unpaired from this account "
               "— make a new one")
CODE_USED = "this pairing QR has already been used"


@router.post("/local/simulate-robot-scan")
async def simulate_robot_scan(request: Request):
    """Do what a real Moxie does when it phones home after scanning: decode the QR, find
    the pending pairing by SHA256(seed), bind a robot record to that parent and child.
    Body: `{qr_payload, device_id?}`.

    A pairing completes once; a replayed QR is a 409, and one voided by an unpair is a
    410, also when that scan or unpair lands while this one runs. With `device_id` (the
    MQTT `d_<uuid>`, which the QR does not carry) it also permits that robot on the
    supervisor and remembers the id on the record for later device commands —
    best-effort: a down supervisor leaves the robot pending, it never fails the pairing.
    A `device_id` another account's record names is a 409 in the claim's words, before
    anything changes: one robot is on one account on every path."""
    body = await read_json(request)
    decoded = moxie_qr.decode_proto(body.get("qr_payload", ""))
    seed = decoded.get("secret_key")
    if not seed:
        raise HTTPException(400, "QR carries no secret_key (wifi-only)")
    id_hash = hashlib.sha256(seed).hexdigest()
    pairing = db.q1("SELECT * FROM pairings WHERE id_hash=?", (id_hash,))
    if not pairing:
        raise HTTPException(404, "no pending pairing matches this QR")
    if pairing["consumed"] == db.PAIRING_VOID:
        raise HTTPException(410, CODE_VOIDED)
    if pairing["consumed"]:
        raise HTTPException(409, CODE_USED)
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
        attrs["mqtt-device-id"] = device_id
        out["device_id"] = device_id
    # The record first, checked against the code's state and every other account in the
    # same transaction, and only then the permit post, the one call here that waits on the
    # network: a claim that lands meanwhile finds the robot on this account, so it is never
    # on two, and an unpair or a second scan of this code cannot slip in between.
    outcome = db.bind_scanned_robot(rid, pairing["user_id"], pairing["child_id"], attrs,
                                    {"volume": 0.7, "screen-brightness": 0.8}, id_hash)
    if outcome == "void":
        raise HTTPException(410, CODE_VOIDED)
    if outcome == "used":
        raise HTTPException(409, CODE_USED)
    if outcome == "taken":
        return _claim_refusal(409, "on another account", ON_ANOTHER_ACCOUNT, device_id)
    if device_id:
        res, code = supervisor.post_json("/permits", {
            "device_id": device_id, "permitted": True, "label": "paired via console"})
        out["permitted"] = bool(code == 200 and res.get("ok"))
        if not out["permitted"]:
            out["permit_error"] = res.get("error") or f"supervisor returned {code}"
    return out


def _supervisor_devices(timeout: float = 2):
    """What the supervisor's permit view says about robots, or `None` when it cannot be
    asked: `connected` (on the broker now, served or pending), `listed` (that, plus every
    id on the permit list) and `permitted` (served if it connects)."""
    out, code = supervisor.call("GET", "/permits", timeout=timeout)
    if code != 200 or not out.get("ok"):
        return None
    def ids(values):
        return {str(v).strip() for v in values or [] if v and str(v).strip()}
    connected = ids(out.get("connected")) | ids(out.get("pending"))
    on_list = ids(p.get("device_id") for p in out.get("permits") or [] if isinstance(p, dict))
    return {"connected": connected, "listed": connected | on_list,
            "permitted": on_list, "open": bool(out.get("allow_unverified_bots"))}


#: `/local/state` reads the supervisor's robots on every call, and the web app polls it (the
#: Wi-Fi tab every 2 s, the Moxie tab while it waits for a robot), so that read is bounded:
#: a short timeout, and one answer serves every caller for STATE_TTL_S. The supervisor's
#: status server answers one request at a time, so a long Try it turn can hold it past the
#: timeout: a read that fails within STATE_GRACE_S of a good answer keeps that answer
#: instead of reporting the supervisor gone. The claim never uses this; it asks afresh,
#: and a good answer it gets becomes the shared one (`_share_with_state`).
STATE_TIMEOUT_S, STATE_TTL_S, STATE_GRACE_S = 0.5, 1.0, 10.0
_clock = time.monotonic
_state_lock = threading.Lock()
_state_read: dict = {}


def _state_cache() -> dict:
    """The shared read, for the supervisor this server asks now. Call under `_state_lock`."""
    c, url = _state_read, supervisor.url("/permits")
    if c.get("url") != url:                      # another supervisor: nothing carries over
        c.clear()
        c["url"] = url
    return c


def _devices_for_state():
    """`_supervisor_devices()` for `/local/state`: bounded and briefly shared (above)."""
    with _state_lock:
        c, now = _state_cache(), _clock()
        if "seen" in c and now - c["at"] < STATE_TTL_S:
            return c["seen"]
        seen = _supervisor_devices(STATE_TIMEOUT_S)
        if seen is not None:
            c["good"], c["good_at"] = seen, now
        elif "good" in c and now - c["good_at"] < STATE_GRACE_S:
            seen = c["good"]
        c["seen"], c["at"] = seen, now
        return seen


def _share_with_state(seen) -> None:
    """A good read the claim just made becomes `/local/state`'s shared one. The page redraws
    straight after a claim, and the read it would otherwise get can be up to STATE_TTL_S
    older than the claim's: a robot the claim found gone would still be offered."""
    if seen is None:
        return
    with _state_lock:
        c, now = _state_cache(), _clock()
        c["seen"] = c["good"] = seen
        c["at"] = c["good_at"] = now


def _claim_refusal(status: int, error: str, reason: str, device_id: str, **extra):
    return JSONResponse(status_code=status, content={
        "ok": False, "error": error, "reason": reason, "device_id": device_id, **extra})


#: The label a claim leaves on the robot's permit. Robot access is not per-account, so it
#: names no one.
CLAIM_LABEL = "added to a parent account"
#: Why a robot another account's record names is refused, by the claim and by Simulate
#: robot scan alike.
ON_ANOTHER_ACCOUNT = ("That robot is already on another account on this server. Unpair it "
                      "there first, then add it here.")
#: Why the claim refuses an id the supervisor has never listed, and why it refuses when the
#: supervisor cannot be asked. `sim/test_robot_claim.mjs` reads these two and
#: ON_ANOTHER_ACCOUNT out of this file, so its refusals are the route's own words.
UNKNOWN_ROBOT = ("No robot with that id has connected to this server. Show Moxie the Wi-Fi "
                 "code and then the server code; it is listed in Robot access once it arrives.")
CANNOT_CHECK = ("This server cannot reach its robot side, so it cannot check which robots "
                "have connected. Nothing was changed: start the supervisor and try again.")


@router.post("/local/robots/{device_id}/claim")
def claim_robot(device_id: str, u=Depends(current_user)):
    """Add a robot that paired by QR to this account: the record a simulated scan makes,
    for the `d_<uuid>` the supervisor lists, so the robot card (settings, insights,
    safety, memory, Wake, Unpair, Factory reset) has something to render.

    A claim is the parent's word that the robot is theirs, the same trust as Permit:
    nothing the robot sends carries the pairing seed, so no pairing code is used and no
    `public-key` is written. It fails closed: 503 when the supervisor cannot say which
    robots it has seen, 404 for an id it has never listed, 409 for a robot on another
    account or an account that already has a different robot. On success it posts the
    console's Permit body once (best-effort: `permitted: false` and the reason if that
    fails). A repeat returns the same record and posts nothing. The supervisor is still
    asked first (2 s at most), so the answer says whether the robot is let in, but a repeat
    is answered from this account's record whatever the supervisor says: also once the
    robot is off every list (switched off and revoked) or the supervisor is down. A good
    supervisor read becomes `/local/state`'s shared one, so the page's redraw after the
    claim agrees with it."""
    device_id = device_id.strip()
    seen = _supervisor_devices()
    _share_with_state(seen)
    mine = next((r for r in db.robots_of(u["id"]) if db.device_id_of(r) == device_id), None)
    if mine is None and seen is None:
        return _claim_refusal(503, supervisor.UNREACHABLE, CANNOT_CHECK, device_id)
    if mine is None and device_id not in seen["listed"]:
        return _claim_refusal(404, "unknown robot", UNKNOWN_ROBOT, device_id)
    if mine is not None:
        outcome, row = "exists", mine
    else:
        outcome, row = db.claim_robot(
            u["id"], device_id,
            {"serial": device_id, "name": "Moxie", "state": "paired", "pairing-status": "paired"},
            {"volume": 0.7, "screen-brightness": 0.8}, {"child-first-name": "Moxie Kid"})
    if outcome == "taken":
        return _claim_refusal(409, "on another account", ON_ANOTHER_ACCOUNT, device_id)
    if outcome == "occupied":
        name = json.loads(row["attributes"]).get("name") or "Moxie"
        return _claim_refusal(
            409, "account already has a robot",
            f"This account already has a robot ({name}). Unpair the current robot first, "
            "then add this one.", device_id, robot_id=row["id"])
    out = {"ok": True, "robot_id": row["id"], "device_id": device_id,
           "child_id": row["child_id"], "created": outcome == "created",
           "permitted": bool(seen) and (seen["open"] or device_id in seen["permitted"]),
           "permit_error": None if seen else supervisor.UNREACHABLE}
    if outcome == "created":
        res, code = supervisor.post_json("/permits", {
            "device_id": device_id, "permitted": True, "label": CLAIM_LABEL})
        out["permitted"] = bool(code == 200 and res.get("ok"))
        if not out["permitted"]:
            out["permit_error"] = res.get("error") or f"supervisor returned {code}"
    return out


@router.get("/local/state")
def local_state(u=Depends(current_user)):
    def rows(rs):
        return [{"id": r["id"], **json.loads(r["attributes"])} for r in rs]
    # Each robot row also names the child it is bound to: the unpair sheet words its
    # erase choice with that child's name.
    mine = db.robots_of(u["id"])
    robots = [{"id": r["id"], **json.loads(r["attributes"]), "child_id": r["child_id"]}
              for r in mine]
    # Robots on the broker that no account's record names: what "Add to my account"
    # offers. Empty when the supervisor cannot be asked, and then `unclaimed_known` is
    # false: nobody could check, which is not the same as no robot having arrived. Those
    # another account's record names are listed apart (`on_other_accounts`, ids only), so
    # Robot access can say why it offers no button for them.
    seen = _devices_for_state()
    connected, bound = (seen["connected"] if seen else set()), db.bound_device_ids()
    others = bound - {db.device_id_of(r) for r in mine}
    return {"user": {"id": u["id"], **json.loads(u["attributes"])},
            "children": rows(db.children_of(u["id"])), "robots": robots,
            "unclaimed": sorted(connected - bound), "unclaimed_known": seen is not None,
            "on_other_accounts": sorted(connected & others)}


@router.get("/healthz")
def healthz():
    return {"ok": True}
