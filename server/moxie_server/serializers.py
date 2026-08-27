"""JSON:API response shaping that the parent app's DataManager expects."""
from __future__ import annotations
import json


def _child_resource(row):
    return {"id": row["id"], "type": "children",
            "attributes": json.loads(row["attributes"]), "relationships": {}}


def _robot_resource(row):
    attrs = json.loads(row["attributes"])
    return {"id": row["id"], "type": "robots", "attributes": attrs,
            "relationships": {
                "robot-setting": {"data": ({"id": row["id"], "type": "robot-setting"}
                                           if row["robot_setting"] else None)},
            }}


def _robot_setting_resource(row):
    return {"id": row["id"], "type": "robot-setting",
            "attributes": json.loads(row["robot_setting"] or "{}")}


def user_document(user, children, robots):
    """Full GET users/me JSON:API document with included child/robots."""
    attrs = json.loads(user["attributes"])
    child_ids = [c["id"] for c in children]
    robot_ids = [r["id"] for r in robots]
    active_child = attrs.get("active-child-id") or (child_ids[0] if child_ids else None)

    relationships = {
        "child": {"data": ({"id": active_child, "type": "children"} if active_child else None)},
        "children": {"data": [{"id": i, "type": "children"} for i in child_ids]},
        "robots": {"data": [{"id": i, "type": "robots"} for i in robot_ids]},
        "mobile-devices": {"data": []},
        "identity-verification": {"data": None},
    }
    included = [_child_resource(c) for c in children]
    for r in robots:
        included.append(_robot_resource(r))
        if r["robot_setting"]:
            included.append(_robot_setting_resource(r))
    return {
        "data": {"id": user["id"], "type": "users", "attributes": attrs,
                 "relationships": relationships},
        "included": included,
    }


def robot_document(robot):
    inc = []
    if robot["robot_setting"]:
        inc.append(_robot_setting_resource(robot))
    return {"data": _robot_resource(robot), "included": inc}
