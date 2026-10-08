"""`/local/*` parent-console cards, proxied to the MQTT supervisor (see `supervisor.py`).

Each route forwards one call and normalizes the answer with the card's pure view from
`fleet/`; the supervisor owns validation, the safety check and the protocol. A refusal
keeps its status code (400 with `reason`, 404 for an unknown robot) so a card can tell
a parent what to do instead of silently doing nothing.
"""
from __future__ import annotations
import json
from typing import Optional

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import JSONResponse

from .. import child_profile, db, fleet, supervisor as sv
from ..auth import read_json
from ..supervisor import device_query as dq, proxy, reply

router = APIRouter()


async def _raw(request: Request) -> bytes:
    return await request.body() or b"{}"


# --- fleet, config, access -------------------------------------------------------------
# A robot's child name (and the feed lines that carry it) reach only a caller with a token
# for the account that has that robot: anyone on the home network can call these routes
# (`child_profile.redact_status`; a filter, not a lock: OQ3).
@router.get("/local/broker/status")
def broker_status(authorization: Optional[str] = Header(None)):
    return child_profile.redact_status(sv.fetch_status(), child_profile.viewer(authorization))


@router.get("/local/fleet")
def fleet_view(authorization: Optional[str] = Header(None)):
    return fleet.normalize_fleet(child_profile.redact_status(
        sv.fetch_status(), child_profile.viewer(authorization)))


@router.post("/local/robots/{device_id}/config")
async def set_robot_config(device_id: str, request: Request,
                           authorization: Optional[str] = Header(None)):
    """Whitelisted overrides; the supervisor validates and re-pushes RobotCloudConfig."""
    out, code = sv.call("POST", dq("/config", device_id), await _raw(request))
    return reply(child_profile.redact_config_answer(
        out, device_id, child_profile.viewer(authorization)), code)


@router.post("/local/fleet/config")
async def set_fleet_config(request: Request):
    """Appliance-wide defaults every robot inherits; a per-robot override still wins."""
    return proxy("POST", "/config?scope=fleet", data=await _raw(request))


@router.get("/local/permits")
def get_permits():
    """The device allowlist: who is permitted, who is pending, whether any robot is served."""
    out, code = sv.call("GET", "/permits")
    if code != 200:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": sv.UNREACHABLE, "detail": out.get("detail") or
            out.get("error"), "permits": [], "pending": []})
    return out


@router.post("/local/robots/{device_id}/permit")
async def permit_robot(device_id: str, request: Request):
    """Permit (or `{"permitted": false}` revoke) one robot; the supervisor re-pushes its
    config on the spot, so a pending robot becomes paired without a reconnect. A Permit
    for a robot an account's record names also sends that child's name (the recovery path
    when the claim could not): `child_pushed` and `reason` say how it went."""
    body = await read_json(request)
    permitted = bool(body.get("permitted", True))
    out, code = sv.post_json("/permits", {
        "device_id": device_id, "permitted": permitted, "label": body.get("label") or ""})
    record = next((r for r in db.q("SELECT * FROM robots")
                   if db.device_id_of(r) == device_id.strip()), None)
    if permitted and code == 200 and out.get("ok") and record is not None:
        out = {**out, **child_profile.push_for_robot(record["user_id"], record,
                                                     joining=True)}
    return reply(out, code)


@router.post("/local/fleet/permits")
async def set_fleet_permits(request: Request):
    """The "serve any robot that connects" switch. Off is the safe default."""
    body = await read_json(request)
    return reply(*sv.post_json(
        "/permits", {"allow_unverified_bots": bool(body.get("allow_unverified_bots"))}))


@router.post("/local/robots/{device_id}/preview")
async def preview_line(device_id: str, request: Request):
    """Rehearse one line: staged by the behavior planner and published as an ordinary
    `remote_chat`, so whatever is subscribed performs it. No brain call, no history.
    The reply is the staged Performance plus `dropped` (ids the validator refused)."""
    body = await read_json(request)
    return reply(*sv.post_json(dq("/preview", device_id), {
        "text": body.get("text") or "", "speak": bool(body.get("speak")),
        "icons": bool(body.get("icons")), "sfx": bool(body.get("sfx"))}))


# --- 💬 Try it: the brain, without a robot ----------------------------------------------
#: The supervisor holds a try for up to its own 30 s deadline; the proxy waits longer, so
#: the sentence a parent reads is the supervisor's, not a bare timeout.
TRYIT_PROXY_TIMEOUT_S = 40


@router.get("/local/tryit")
def tryit_options(device_id: str = ""):
    """The 💬 card's choices: who answers this robot (or, with no robot, the appliance)
    and which layer decided, what else may answer, the installed conversations, the
    child's name, the limits and what is left of the hour's tries."""
    path = dq("/tryit", device_id) if device_id else "/tryit"
    return proxy("GET", path, fleet.normalize_tryit_options, timeout=10)


@router.post("/local/tryit")
def tryit_turn(body: dict = Body(default=None)):
    """One preview turn through the robot's own brain: `{"speech", "history",
    "device_id"?, "brain"?, "module"?, "nickname"?}`. Never published to a robot and
    never remembered; the session travels in the request. The supervisor validates and
    bounds it (nothing is checked here). A plain `def`, so FastAPI runs it on a worker
    thread: a try holds its call for as long as the brain takes, and the rest of the
    console keeps answering meanwhile."""
    # UTF-8, not \uXXXX escapes: a non-Latin session costs half the bytes of the 64 KiB cap.
    out, code = sv.call("POST", "/tryit", json.dumps(body or {}, ensure_ascii=False).encode(),
                        TRYIT_PROXY_TIMEOUT_S)
    if code == 503 and "timed out" in str(out.get("detail") or "").lower():
        why = f"The supervisor did not answer within {TRYIT_PROXY_TIMEOUT_S} s."
        out, code = {"ok": False, "kind": "timeout", "error": why, "reason": why}, 504
    return reply(fleet.normalize_tryit(out), code)


# --- 📈 insights, 🔌 connection, 🛡️ safety ------------------------------------------------
@router.get("/local/robots/{device_id}/telemetry")
def robot_telemetry(device_id: str, limit: int = 20, days: int = 7):
    """Counts by event, the newest events, and `days` of daily history + retention."""
    return proxy("GET", dq("/telemetry", device_id, limit=int(limit), days=max(0, int(days))),
                 fleet.normalize_telemetry, device_id=device_id)


@router.delete("/local/robots/{device_id}/telemetry")
def forget_telemetry(device_id: str):
    """Erase the stored activity history. Never policy-gated: erase always works.
    `erased` says whether anything was there; `records` names what went."""
    raw, code = sv.call("DELETE", dq("/telemetry", device_id), device_id=device_id)
    out = fleet.normalize_telemetry(raw)
    ok = code == 200
    out["erased"] = bool(raw.get("erased")) if ok else False
    out["records"] = list(raw.get("records") or []) if ok else []
    return reply(out, code)


@router.get("/local/connection")
def appliance_connection(limit: int = 30):
    """The appliance's own broker connection, live state beside the durable history.
    Not per-robot: there is one socket to the broker."""
    return proxy("GET", f"/conn?limit={max(0, int(limit))}", fleet.normalize_connection)


@router.get("/local/robots/{device_id}/safety")
def robot_safety(device_id: str, limit: int = 20):
    return proxy("GET", dq("/safety", device_id, limit=int(limit)), fleet.normalize_safety,
                 device_id=device_id)


@router.post("/local/robots/{device_id}/safety")
async def acknowledge_robot_safety(device_id: str, request: Request):
    """`{"event_id": "sfe-…"}` marks one reviewed, `{}` marks all."""
    return proxy("POST", dq("/safety", device_id), fleet.normalize_safety,
                 data=await _raw(request), device_id=device_id)


# --- 🎭 Be Moxie, 📅 Today's plan ---------------------------------------------------------
@router.get("/local/robots/{device_id}/telehealth")
def robot_telehealth(device_id: str):
    return proxy("GET", dq("/telehealth", device_id), fleet.normalize_telehealth,
                 device_id=device_id)


@router.post("/local/robots/{device_id}/telehealth")
async def drive_robot_telehealth(device_id: str, request: Request):
    """One operator verb (`enable|disable|start|end|state|speak|interrupt`). A line the
    safety check blocks is a 400 with its reason — never silently rewritten, because a
    human is at the keyboard."""
    return proxy("POST", dq("/telehealth", device_id), fleet.normalize_telehealth,
                 data=await _raw(request), timeout=5, device_id=device_id)


@router.get("/local/robots/{device_id}/schedule")
def robot_schedule(device_id: str, refresh: bool = False):
    """Today's plan with one *why* line per entry. Read-only: plans change via ⚙️ config."""
    return proxy("GET", dq("/schedule", device_id, refresh=1 if refresh else None),
                 fleet.normalize_schedule_view, timeout=5, device_id=device_id)


# --- 🎚️ voice, 🧠 brain ------------------------------------------------------------------
# A voice is fleet-level (device_id only names the robot a test line plays on); a brain
# is per child unless the body says `scope: "fleet"`.
@router.get("/local/robots/{device_id}/voice")
def robot_voice(device_id: str, refresh: bool = False):
    return proxy("GET", "/voice" + ("?refresh=1" if refresh else ""), fleet.normalize_voice,
                 timeout=5)


@router.post("/local/robots/{device_id}/voice")
async def set_robot_voice(device_id: str, request: Request):
    """`{"speech": …, "listening": …}`; a pick that is no longer available is a 400."""
    return proxy("POST", "/voice", fleet.normalize_voice, data=await _raw(request), timeout=10)


@router.post("/local/robots/{device_id}/voice/test")
async def test_robot_voice(device_id: str, request: Request):
    """Speak one line through the engine actually installed, on this robot."""
    return proxy("POST", dq("/voice/test", device_id), fleet.normalize_voice,
                 data=await _raw(request), timeout=30, device_id=device_id)


@router.get("/local/robots/{device_id}/brain")
def robot_brain(device_id: str):
    return proxy("GET", "/brain", fleet.normalize_brain, timeout=5)


@router.post("/local/robots/{device_id}/brain")
async def set_robot_brain(device_id: str, request: Request):
    """`{"brain": id|null, "scope"?: "fleet"}`. The supervisor checks the pick against its
    registry and `MOXIE_APP`'s pin. `scope` travels as a query parameter (the supervisor's
    route shape), so it is stripped from the forwarded body."""
    body = await read_json(request)
    fleet_scope = str(body.get("scope") or "") == "fleet"
    forward = json.dumps({k: v for k, v in body.items() if k != "scope"}).encode()
    return proxy("POST", "/brain?scope=fleet" if fleet_scope else dq("/brain", device_id),
                 fleet.normalize_brain, data=forward, timeout=10)


# --- 🧠 what Moxie remembers ------------------------------------------------------------
# Granularity is what the runtime offers: one item, one activity, or all of it, plus an
# edit. Erase is never policy-gated.
def _memory(device_id: str, method: str = "GET", body=None, **where):
    data = json.dumps(body).encode() if body is not None else None
    return proxy(method, dq("/memory", device_id, **where), fleet.normalize_memory,
                 data=data, device_id=device_id)


@router.get("/local/robots/{device_id}/memory")
def robot_memory(device_id: str):
    return _memory(device_id)


@router.delete("/local/robots/{device_id}/memory")
def forget_all_memory(device_id: str):
    return _memory(device_id, "DELETE")


@router.delete("/local/robots/{device_id}/memory/{namespace}")
def forget_memory_namespace(device_id: str, namespace: str):
    return _memory(device_id, "DELETE", namespace=namespace)


@router.delete("/local/robots/{device_id}/memory/{namespace}/{item}")
def forget_memory_item(device_id: str, namespace: str, item: str):
    return _memory(device_id, "DELETE", namespace=namespace, item=item)


@router.post("/local/robots/{device_id}/memory/{namespace}/{item}")
def correct_memory_item(device_id: str, namespace: str, item: str,
                        body: dict = Body(default=None)):
    """`{"text": …}`. The supervisor re-runs the safety and no-verbatim checks and pins
    the result; a refused edit is a 400 carrying the reason."""
    text = str((body or {}).get("text") or "")
    return _memory(device_id, "POST",
                   {"edit": {"namespace": namespace, "item": item, "text": text}})
