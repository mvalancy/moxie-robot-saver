"""The child's name Moxie says: the parent's account record, sent to the robot's supervisor.

The account's child record (`children.attributes`: `nickname`, else `child-first-name`) is
where the name comes from. The supervisor keeps the robot's own copy on that robot's
settings (`POST /config?device_id=… {"child": {"nickname": …}}`, saved in
`robots/<id>/config.json`) and says it from there: `child_pii.nickname` in the robot's
`/config`, both brains' prompts, the walk-back-in hello, the opener and `/status`. This
module is the one place the console sends or clears it. Like the permit post it is
best-effort: a supervisor that is down never fails the parent's call, and the answer says
whether the name went (`child_pushed`) and why not (`reason`). The supervisor is the one
judge of a name (`moxie_sdk.cloud_config.NAME_RULE`; this process has no `moxie_sdk`), so
here only the pairing placeholder and a blank are held back.

The name is a child's, so nothing here logs it, and the console's two `/status` views hand
it only to a caller signed in to the account that has the robot (`redact_status`).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import db, supervisor

#: What pairing and Add to my account name the child of an account that has none yet.
#: A placeholder, never a name: Moxie keeps its default instead of saying it.
PLACEHOLDER = "Moxie Kid"

NO_NAME = ("Your account has no name for your child yet, so Moxie uses its default. Type "
           "the name in the Wi-Fi tab and make the code: that sends it.")
NO_DEVICE = ("This robot's record does not say which robot on this server it is, so the "
             "name was not sent.")
NO_ROBOT = "No robot on this account is bound to this child yet: the name goes with it."
UNREACHABLE = ("This server could not reach its robot side, so the name was not sent. "
               "Save it again once the supervisor is running.")
#: Where a name that is held back from a caller stands in the activity feed's lines.
MASK = "[name]"


def name_for(child_row) -> str:
    """The name to send for one `children` row, or `""` when there is none to send (no
    row, a blank name, or the pairing placeholder)."""
    if child_row is None:
        return ""
    try:
        attrs = json.loads(child_row["attributes"]) or {}
    except (TypeError, ValueError):
        return ""
    raw = attrs.get("nickname") or attrs.get("child-first-name") or ""
    name = " ".join(str(raw).split())
    return "" if name.casefold() == PLACEHOLDER.casefold() else name


def _birthday(value) -> str:
    """A record's `birthday` when it is a real `YYYY-MM-DD` date, else `""`: the web app
    collects none, and one in another shape must not cost the child their name (the
    supervisor refuses the whole change for a bad one)."""
    import datetime
    text = str(value or "").strip()
    try:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) and \
            datetime.date.fromisoformat(text) else ""
    except ValueError:
        return ""


def _outcome(out: dict, code: int) -> Optional[str]:
    """`None` when the supervisor took the change, else the parent's reason."""
    if code == 200 and out.get("ok"):
        return None
    if code == 503:
        return UNREACHABLE
    return str(out.get("reason") or out.get("error") or f"supervisor returned {code}")


def push_child(device_id: str, child_row) -> dict:
    """Send one child's name to the robot behind `device_id`: the supervisor saves it for
    that robot and pushes it now if the robot is connected, else on its next connect.
    `{"child_pushed": bool, "reason": str | None}`; never raises."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {"child_pushed": False, "reason": NO_DEVICE}
    name = name_for(child_row)
    if not name:
        return {"child_pushed": False, "reason": NO_NAME}
    child = {"nickname": name}
    birthday = _birthday((json.loads(child_row["attributes"]) or {}).get("birthday"))
    if birthday:
        child["birthday"] = birthday
    out, code = supervisor.post_json(supervisor.device_query("/config", device_id),
                                     {"child": child})
    reason = _outcome(out, code)
    return {"child_pushed": reason is None, "reason": reason}


def clear_child(device_id: str) -> dict:
    """Take the name off the robot behind `device_id` (unpair, factory reset): the
    supervisor drops it from the robot's saved settings and the robot falls back to the
    appliance's default. `{"child_cleared": bool, "reason": str | None}`; never raises."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {"child_cleared": False, "reason": NO_DEVICE}
    out, code = supervisor.post_json(supervisor.device_query("/config", device_id),
                                     {"child": None})
    reason = _outcome(out, code)
    return {"child_cleared": reason is None, "reason": reason}


def child_row(user_id: str, child_id) -> Optional[object]:
    """This account's `children` row with that id, or None: another account's child is
    never read, let alone sent."""
    if not child_id:
        return None
    return db.q1("SELECT * FROM children WHERE id=? AND user_id=?", (child_id, user_id))


def push_for_robot(user_id: str, robot_row) -> dict:
    """`push_child` for one of this account's robot records (its own child, its own
    `mqtt-device-id`)."""
    return push_child(db.device_id_of(robot_row), child_row(user_id, robot_row["child_id"]))


def push_to_robots_of_child(user_id: str, child_id: str) -> dict:
    """After a rename: the name to every robot of this account bound to that child.
    `child_pushed` is true when every one took it."""
    rows = db.q("SELECT * FROM robots WHERE user_id=? AND child_id=?", (user_id, child_id))
    if not rows:
        return {"child_pushed": False, "reason": NO_ROBOT}
    results = [push_for_robot(user_id, r) for r in rows]
    failed = [r["reason"] for r in results if not r["child_pushed"]]
    return {"child_pushed": not failed, "reason": failed[0] if failed else None}


def clear_from_robots_of_child(user_id: str, child_id: str) -> None:
    """The child's record is going: no robot of this account says its name any more."""
    for row in db.q("SELECT * FROM robots WHERE user_id=? AND child_id=?",
                    (user_id, child_id)):
        clear_child(db.device_id_of(row))


# --- who may read the name back -------------------------------------------------------
# Any device on the home network can call the console's `/local/*` routes without signing
# in (owner question OQ3), and the supervisor's `/status` carries each connected robot's
# name. So the two console views of it name a robot's child only to a caller whose bearer
# token is the account that has that robot.

def viewer(authorization: Optional[str]):
    """The account behind an optional `Authorization: Bearer …`, or None (no token, or
    one this server does not know: such a caller is simply not signed in)."""
    if not authorization:
        return None
    return db.user_by_token(authorization.split(" ", 1)[-1].strip())


def _names(rows) -> set:
    """The names on these child rows, lower-cased (the feed's mask ignores case)."""
    return {n.lower() for n in map(name_for, rows) if len(n) >= 2}


def redact_status(snapshot, user) -> dict:
    """A copy of a supervisor `/status` snapshot that names no child the caller may not
    read: a robot not on `user`'s account gets `child: None` and loses the `child` key from
    its config layers, and in the activity feed (`recent`, where a line Moxie spoke can
    carry a name) every child name this server knows of but the caller's own is masked."""
    if not isinstance(snapshot, dict):
        return snapshot
    snap = dict(snapshot)
    mine = ({db.device_id_of(r) for r in db.robots_of(user["id"])} - {""}
            if user is not None else set())
    own = _names(db.children_of(user["id"])) if user is not None else set()
    hidden = _names(db.q("SELECT attributes FROM children"))
    robots = []
    for r in snap.get("robots") or []:
        if not isinstance(r, dict):
            continue
        name = " ".join(str(r.get("child") or "").split()).lower()
        if r.get("device_id") in mine:
            own.add(name)
        else:
            if len(name) >= 2:
                hidden.add(name)
            r = {**r, "child": None,
                 **{k: {kk: vv for kk, vv in r[k].items() if kk != "child"}
                    for k in ("config_overrides", "config_effective")
                    if isinstance(r.get(k), dict)}}
        robots.append(r)
    if "robots" in snap:
        snap["robots"] = robots
    hidden -= own
    if hidden and isinstance(snap.get("recent"), list):
        rx = re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(
            map(re.escape, sorted(hidden, key=len, reverse=True))), re.IGNORECASE)
        snap["recent"] = [{**e, "text": rx.sub(MASK, e["text"])}
                          if isinstance(e, dict) and isinstance(e.get("text"), str) else e
                          for e in snap["recent"]]
    return snap


def redact_config_answer(answer, device_id: str, user) -> dict:
    """A supervisor `POST /config` answer for one robot, without the robot's `child` (its
    config layers and `applied`) unless `user`'s account has that robot."""
    if not isinstance(answer, dict) or user is not None and device_id in {
            db.device_id_of(r) for r in db.robots_of(user["id"])}:
        return answer
    out = dict(answer)
    for k in ("config_overrides", "config_effective", "applied"):
        if isinstance(out.get(k), dict) and "child" in out[k]:
            out[k] = {kk: vv for kk, vv in out[k].items() if kk != "child"}
    return out
