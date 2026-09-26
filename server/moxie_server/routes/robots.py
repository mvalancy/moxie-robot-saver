"""Robots and the original app's pairing hook (`rest-api-contract.md` §3.4).

The device actions answer honestly: `wakeup` publishes the recovered command and says
only that it was published, `reboot` is a 501 (no cloud→robot reboot is recovered), and
`ota_status` reports what the robot said about itself — the reasoning lives beside
`UNSUPPORTED_ACTIONS` / `ota_status_view` in `fleet/robots.py`.
"""
from __future__ import annotations
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .. import db, supervisor
from ..auth import current_user, read_json
from ..fleet import ota_status_view, resolve_device_id, unsupported_action
from ..serializers import robot_document

router = APIRouter()


def register_pairing(id_hash: str, user_id: str, child_id, restore: bool,
                     seed_hex=None, phrase=None):
    db.ex("INSERT OR REPLACE INTO pairings(id_hash,user_id,child_id,restore,consumed,created_at,seed_hex,phrase)"
          " VALUES(?,?,?,?,0,?,?,?)",
          (id_hash, user_id, child_id, int(restore), db.now_s(), seed_hex, phrase))


@router.post("/api/pairing-info")
def pairing_info(request: Request, u=Depends(current_user)):
    """The original app registers `id = hex(SHA256(seed))` just before showing its QR.
    The query keys are hyphenated (`child-id`), hence the raw query params."""
    qp = request.query_params
    if not qp.get("id"):
        raise HTTPException(400, "id required")
    register_pairing(qp["id"], u["id"], qp.get("child-id") or None,
                     str(qp.get("restore", "false")).lower() == "true")
    return Response(status_code=204)


def _robot(rid: str, user_id: str):
    row = db.q1("SELECT * FROM robots WHERE id=? AND user_id=?", (rid, user_id))
    if not row:
        raise HTTPException(404, "no such robot")
    return row


def _robot_attrs(rid: str, user_id: str) -> dict:
    """This user's robot record attributes, or `{}` when it is not theirs."""
    row = db.q1("SELECT * FROM robots WHERE id=? AND user_id=?", (rid, user_id))
    return json.loads(row["attributes"]) if row else {}


@router.get("/api/robots/{rid}")
def get_robot(rid: str, u=Depends(current_user)):
    return robot_document(_robot(rid, u["id"]))


@router.put("/api/robots/{rid}")
async def update_robot(rid: str, request: Request, u=Depends(current_user)):
    row = _robot(rid, u["id"])
    body = await read_json(request)
    if "robot-setting" in body or "robot-settings" in body:
        setting = body.get("robot-setting", body.get("robot-settings"))
        db.ex("UPDATE robots SET robot_setting=? WHERE id=?", (json.dumps(setting), rid))
    else:
        attrs = {**json.loads(row["attributes"]), **body.get("robot", body)}
        db.ex("UPDATE robots SET attributes=? WHERE id=?", (json.dumps(attrs), rid))
    return robot_document(db.q1("SELECT * FROM robots WHERE id=?", (rid,)))


@router.delete("/api/robots/{rid}")
def delete_robot(rid: str, rfs: str = Query(None), u=Depends(current_user)):
    db.ex("DELETE FROM robots WHERE id=? AND user_id=?", (rid, u["id"]))
    return Response(status_code=204)


@router.post("/api/robots/{rid}/wakeup")
def wakeup(rid: str, u=Depends(current_user)):
    """Publish the recovered `wakeup` command at the robot behind this record.

    `error` is `null` only when the command left the appliance; there is no protocol
    acknowledgement, so the reply never claims the robot woke."""
    device_id, how = resolve_device_id(_robot_attrs(rid, u["id"]), supervisor.fetch_status())
    if not device_id:
        return JSONResponse(status_code=409, content={
            "error": "no robot on the broker", "ok": False, "published": False,
            "resolved_by": how,
            "reason": ("This robot has not connected to this appliance yet."
                       if how == "none" else
                       "Several robots are connected and this record does not say which "
                       "one it is — wake it from the fleet panel instead.")})
    out, code = supervisor.post_json(supervisor.device_query("/wakeup", device_id), {})
    out = dict(out or {}, resolved_by=how)
    sent = code == 200 and out.get("published")
    out["error"] = None if sent else (out.get("error") or f"supervisor returned {code}")
    return out if sent else JSONResponse(status_code=code if code >= 400 else 502, content=out)


@router.post("/api/robots/{rid}/reboot")
def reboot(rid: str, u=Depends(current_user)):
    return JSONResponse(status_code=501, content=unsupported_action("reboot"))


@router.get("/api/robots/{rid}/ota_status")
def ota_status(rid: str, u=Depends(current_user)):
    """Never `"up_to_date"`: this appliance serves no OTA, so it reports only what the
    robot said in `RobotStatus` (firmware, `ota_reboot_required`)."""
    snap = supervisor.fetch_status()
    device_id, _how = resolve_device_id(_robot_attrs(rid, u["id"]), snap)
    return ota_status_view(snap, device_id)


@router.post("/api/robots/{rid}/set-language")
@router.post("/api/robots/{rid}/restores")
def robot_ack(rid: str, u=Depends(current_user)):
    return Response(status_code=204)
