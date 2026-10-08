"""Unpair and factory reset: what the server tells the parent, in one place.

`DELETE /api/robots/{id}` (unpair) and `DELETE /api/robots/{id}?rfs=1` (unpair + factory
reset) are the original app's two calls (`docs/features/robot-lifecycle.md` §1). The route
does the work; this module words the answer and owns the one reset code the docs establish,
so the route, `GET /local/factory-reset/payload`, the console and the browser suite's
fixtures share a single copy. Pure and dependency-free: no fastapi, no network.
"""
from __future__ import annotations

#: The setup-QR command Moxie's setup app handles itself: one of exactly four `debug`
#: commands, `restore_factory` = enter its factory-restore flow
#: (`docs/reverse-engineering/protocol/qr-commands.md`). Compact, with no `param`: the form
#: the factory tools print (`{"debug":{"command":"serial_number_display"}}`).
RESTORE_FACTORY_QR = '{"debug":{"command":"restore_factory"}}'
RESET_QR_PNG = "/local/factory-reset/qr.png"

#: Where a claim comes from. Every step a parent acts on carries one.
SEEN = "seen on a real Moxie by this project"
APP = "from the original parent app"
INFERRED = "inferred from Moxie's software; not yet seen on a robot"

LIMIT = "No physical robot has been reset this way by this project yet."


def reset_view() -> dict:
    """The reset code and how to use it: `GET /local/factory-reset/payload`, and the
    `reset` block of a factory-reset answer."""
    return {
        "ok": True,
        "qr_payload": RESTORE_FACTORY_QR,
        "qr_png": RESET_QR_PNG,
        "steps": [
            {"text": "Put Moxie on the screen where it asks for a code (the one it shows "
                     "when it needs Wi-Fi).", "basis": SEEN},
            {"text": "If Moxie is not on that screen: a Moxie that cannot reach the "
                     "internet goes back to it by itself, for example after a restart with "
                     "its Wi-Fi network switched off.", "basis": INFERRED},
            {"text": "Hold this code steady in front of Moxie until it beeps.",
             "basis": SEEN},
            {"text": "Moxie should then start its own factory-restore flow. Its screens for "
                     "this are not documented, and it may ask you to confirm.",
             "basis": INFERRED},
        ],
        "effect": {"text": "The original app called this \"Reset Moxie Back to New\" and "
                           "warned that all of your child's progress with Moxie is erased and "
                           "Moxie is reset as new.", "basis": APP},
        "after": "This cannot be undone. To use Moxie again, pair it from the Wi-Fi tab with "
                 "a new code. If it comes back with a new robot id, permit it again in Robot "
                 "access.",
        "limit": LIMIT,
        "verified_on_robot": False,
        # The original reset was relayed by the cloud, but no cloud-to-robot reset command
        # is recovered (mqtt-and-conversation.md §3.5), so this server publishes nothing.
        "mqtt_command": None,
        "why_not_mqtt": "No cloud-to-robot reset command has been recovered from Moxie's "
                        "software, so this server cannot reset Moxie over the network. "
                        "This code is the only documented way.",
    }


def access_view(device_id, *, revoked: bool, open_gate: bool = False, error=None) -> dict:
    """Whether this server still serves the robot after an unpair, in the parent's words.

    `device_id` is the MQTT identity the record names (None when it names none);
    `open_gate` is the supervisor answering that it serves ANY robot that connects."""
    if not device_id:
        reason = ("This robot's record does not say which robot on this server it is, so "
                  "nothing was revoked. If it is listed under Allowed in Robot access, "
                  "revoke it there.")
    elif not revoked:
        reason = (f"Could not reach the robot service to stop serving it ({error}). "
                  "Revoke it in Robot access once the service is running.")
    elif open_gate:
        reason = ("It is off the allowed list, but this server is set to let any robot "
                  "that connects use it, so it is still being served. Switch that off in "
                  "Robot access.")
    else:
        reason = ("This server stopped serving it and sent it the not-paired settings, "
                  "with no child data (what a physical Moxie shows then has not been seen "
                  "yet). While it stays connected it waits in Robot access; leave it there "
                  "unless you pair it again.")
    return {"device_id": device_id or None, "revoked": bool(revoked),
            "open_gate": bool(open_gate), "error": error, "reason": reason}


def unpair_result(robot_id: str, *, unpaired: bool, factory_reset: bool, child=None,
                  codes_voided: int = 0, access=None) -> dict:
    """The body of `DELETE /api/robots/{id}[?rfs=1]`. `unpaired` is False when nothing on
    this account had that id (already unpaired, or never this account's). `child` is
    `{"id", "name"}` for the child the robot was bound to. `details` are keyed so the
    console can replace the child line when the parent also deletes the profile."""
    if not unpaired:
        message = ("No robot with that id is paired to this account; it may already be "
                   "unpaired.")
    elif factory_reset:
        message = "Moxie is unpaired. Show it the reset code below to finish the reset."
    else:
        message = "Moxie is unpaired from this account."
    details = []
    if unpaired:
        details.append({"key": "record", "text": "The robot was removed from your account."})
        if access:
            details.append({"key": "access", "text": access["reason"]})
        if codes_voided:
            n = int(codes_voided)
            details.append({"key": "codes", "text": (
                f"{n} pairing code{'s' if n != 1 else ''} you made before now no longer "
                f"work{'' if n != 1 else 's'}; make a new one in the Wi-Fi tab when you "
                "pair again.")})
        if child:
            who = f"{child['name']}'s" if child.get("name") else "Your child's"
            details.append({"key": "child", "text": f"{who} profile was kept."})
    return {"ok": True, "robot_id": robot_id, "unpaired": bool(unpaired),
            "factory_reset": bool(factory_reset),
            "child_id": (child or {}).get("id") if unpaired else None,
            "child_kept": bool(unpaired and child),
            "pairing_codes_cancelled": int(codes_voided) if unpaired else 0,
            "access": access if unpaired else None,
            "message": message, "details": details,
            "reset": reset_view() if factory_reset else None}
