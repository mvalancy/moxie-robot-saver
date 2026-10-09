"""The child's name Moxie says: the parent's account record, sent to the robot's supervisor.

The account's child record (`children.attributes`: `nickname`, else `child-first-name`) is
where the name comes from. The supervisor keeps the robot's own copy on that robot's
settings (`POST /config?device_id=… {"child": {"nickname": …}}`, saved in
`robots/<id>/config.json`) and says it from there: `child_pii.nickname` in the robot's
`/config`, both brains' prompts, the walk-back-in hello, the opener and `/status`. This
module is the one place the console sends or clears it. Like the permit post it is
best-effort: a supervisor that is down never fails the parent's call, and the answer says
whether the name went and was saved (`child_pushed`, `child_cleared`) and why not
(`reason`): a change the supervisor applies but cannot save (`saved: false`) is not done,
since a restart undoes it. The supervisor is the one
judge of a name (`moxie_sdk.cloud_config.check_name`: the shape and Moxie's safety table;
this process has no `moxie_sdk`): the console asks it before saving a typed name
(`refusal_for`, `POST /child-name`), and here only the pairing placeholder and a blank are
held back, and both CLEAR the robot's copy instead (`child: null`): the robot then says
the appliance's default, never a name an earlier record left on it (an unpair whose clear
and revoke could not reach the supervisor, a console database that was reset under a kept
supervisor).

The name is a child's, so nothing here logs it, and the console's two `/status` views hand
it only to a caller with a token for the account that has the robot (`redact_status`). That
keeps it off what a device on the network polls without asking; it is not a lock, since
`/local/quicklogin` gives any caller a token for any email (owner question OQ3).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import db, supervisor

#: What pairing and Add to my account name the child of an account that has none yet.
#: A placeholder, never a name: Moxie says its default instead.
PLACEHOLDER = "Moxie Kid"

NO_NAME = ("Your account has no name for your child yet, so Moxie uses its default. Type "
           "the name in the Wi-Fi tab and make the code: that sends it.")
NO_DEVICE = ("This robot's record does not say which robot on this server it is, so the "
             "name was not sent.")
NO_ROBOT = "No robot on this account is bound to this child yet: the name goes with it."
UNREACHABLE = ("This server could not reach its robot side, so the name was not sent. "
               "Save it again once the supervisor is running.")
#: The same for a clear (unpair, reset): there is nothing for the parent to save again.
UNREACHABLE_CLEAR = "This server could not reach its robot side."
#: The supervisor applied the change but could not write the robot's saved settings
#: (`saved: false`): the robot has it now, and a restart undoes it.
NOT_SAVED = ("This server could not save the name in the robot's settings, so a restart "
             "undoes it. Save it again.")
NOT_SAVED_CLEAR = ("The robot stopped using the name, but this server could not save that, "
                   "so the name is still in the robot's saved settings and a restart would "
                   "bring it back.")
#: Where a name that is held back from a caller stands in the activity feed's lines.
MASK = "[name]"


def name_in(attrs) -> str:
    """The name a child record's attributes give Moxie (`nickname`, else
    `child-first-name`), whitespace-collapsed, or `""` for none (no attributes, a blank
    name, or the pairing placeholder)."""
    if not isinstance(attrs, dict):
        return ""
    raw = attrs.get("nickname") or attrs.get("child-first-name") or ""
    name = " ".join(str(raw).split())
    return "" if name.casefold() == PLACEHOLDER.casefold() else name


def name_for(child_row) -> str:
    """The name to send for one `children` row, or `""` when there is none to send (no
    row, a blank name, or the pairing placeholder)."""
    if child_row is None:
        return ""
    try:
        attrs = json.loads(child_row["attributes"]) or {}
    except (TypeError, ValueError):
        return ""
    return name_in(attrs)


def refusal_for(name: str) -> Optional[str]:
    """Why the supervisor would refuse `name` as a child's name, in the parent's words, or
    None. The supervisor is the one judge (`POST /child-name`: the shape, NFC and Moxie's
    safety table; nothing is saved there). None too when there is no name to judge (`""`)
    or the supervisor cannot be asked (down, or a build without that route): then the
    record is saved as before, and the name is judged again when it is sent."""
    if not name:
        return None
    out, code = supervisor.post_json("/child-name", {"nickname": name})
    if code != 400:
        return None
    out = out if isinstance(out, dict) else {}
    return str(out.get("reason") or out.get("error") or "Moxie will not take that name.")


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


def _took(out, code: int) -> bool:
    """Did the supervisor apply the change (saved or not)?"""
    return code == 200 and isinstance(out, dict) and bool(out.get("ok"))


def _outcome(out: dict, code: int, not_saved: str,
             unreachable: str = UNREACHABLE) -> Optional[str]:
    """`None` when the supervisor took the change and saved it, else the parent's reason:
    `not_saved` when it applied the change but its answer says the robot's record does not
    hold it (`saved: false`), since a restart would undo it."""
    if _took(out, code):
        return not_saved if out.get("saved") is False else None
    out = out if isinstance(out, dict) else {}
    if code == 503:
        return unreachable
    return str(out.get("reason") or out.get("error") or f"supervisor returned {code}")


def push_child(device_id: str, child_row, *, joining: bool = False) -> dict:
    """Send one child's name to the robot behind `device_id`: the supervisor saves it for
    that robot and pushes it now if the robot is connected, else on its next connect.

    With no name to send (no row, a blank, the pairing placeholder) the robot's copy is
    cleared instead, so it says the appliance's default and never a name an earlier record
    left there. `joining` is a robot coming onto this account (a claim, a scan, a Permit):
    then a name the supervisor refuses clears it too, for the same reason; a rename that is
    refused leaves the family's previous name, and the reason says why.
    `{"child_pushed": bool, "reason": str | None}`; never raises."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return {"child_pushed": False, "reason": NO_DEVICE}
    name = name_for(child_row)
    if not name:
        clear_child(device_id)
        return {"child_pushed": False, "reason": NO_NAME}
    child = {"nickname": name}
    birthday = _birthday((json.loads(child_row["attributes"]) or {}).get("birthday"))
    if birthday:
        child["birthday"] = birthday
    out, code = supervisor.post_json(supervisor.device_query("/config", device_id),
                                     {"child": child})
    reason = _outcome(out, code, NOT_SAVED)
    if joining and not _took(out, code) and code != 503:
        clear_child(device_id)       # refused: never a name an earlier record left there
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
    reason = _outcome(out, code, NOT_SAVED_CLEAR, UNREACHABLE_CLEAR)
    return {"child_cleared": reason is None, "reason": reason}


def child_row(user_id: str, child_id) -> Optional[object]:
    """This account's `children` row with that id, or None: another account's child is
    never read, let alone sent."""
    if not child_id:
        return None
    return db.q1("SELECT * FROM children WHERE id=? AND user_id=?", (child_id, user_id))


def push_for_robot(user_id: str, robot_row, *, joining: bool = False) -> dict:
    """`push_child` for one of this account's robot records (its own child, its own
    `mqtt-device-id`)."""
    return push_child(db.device_id_of(robot_row), child_row(user_id, robot_row["child_id"]),
                      joining=joining)


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
# token is the account that has that robot: a filter on what is polled without asking, not
# a lock (`/local/quicklogin` mints a token for any email).

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
    read: a robot not on `user`'s account gets `child: None`, loses the `child` key from
    its config layers and its `face_cache_id` (a UUIDv5 of the name and the face, both
    otherwise in view, so a list of first names would recover the name), and in the
    activity feed (`recent`, where a line Moxie spoke can carry a name) every child name
    this server knows of but the caller's own is masked."""
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
                 **({"face_cache_id": ""} if "face_cache_id" in r else {}),
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
