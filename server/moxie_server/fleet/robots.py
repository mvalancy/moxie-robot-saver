"""The fleet view, and which robot-action buttons are real.

`normalize_fleet` turns `MoxieRuntime.status_snapshot()` into one tidy record per
connected robot plus the appliance-wide config, the pairing gate and the face catalog.
"""
from __future__ import annotations
import re
import time
from typing import Optional

from ._coerce import _dict, _num


def robot_summary(r: dict) -> str:
    """A one-line human summary of a robot's live state for the console card."""
    bits = []
    # The pairing gate leads: a pending robot is served nothing, which changes every line.
    if r.get("pending"):
        bits.append("pending — not permitted")
    bat = r.get("battery_level")
    if bat is not None:
        bits.append(f"battery {bat}%" if isinstance(bat, (int, float)) and bat <= 100
                    else f"battery {bat}")
    if r.get("audio_volume") is not None:
        bits.append(f"vol {r['audio_volume']}")
    if r.get("wifi_ssid"):
        bits.append(f"Wi-Fi {r['wifi_ssid']}")
    if r.get("mode"):
        bits.append(f"mode {r['mode']}")
    if r.get("telemetry_count"):
        bits.append(f"{r['telemetry_count']} events")
    n = r.get("safety_unreviewed")
    if n:
        bits.append(f"{n} safety flag{'' if n == 1 else 's'} to review")
    if r.get("ota_reboot_required"):
        bits.append("OTA reboot pending")
    asked = r.get("stt_subscribed_at")
    if isinstance(asked, (int, float)) and not isinstance(asked, bool) and asked > 0:
        # The supervisor asked for the robot's microphone (a request; the robot sends no
        # acknowledgement, so this is never "listening"). The zone is named: the server's
        # clock, not the parent's (the appliance container runs on UTC).
        bits.append(f"mic asked {time.strftime('%H:%M %Z', time.localtime(asked)).strip()}")
    return " · ".join(bits) or "connected"


def config_sources(fleet_config: Optional[dict], overrides: Optional[dict]) -> dict:
    """`{key: "robot" | "fleet"}` — which layer each effective override came from, so the
    console can tell a house rule from a per-robot exception. Labels only; the layering
    itself is `moxie_sdk.cloud_config.merge_config_layers`."""
    out = {k: "fleet" for k in (fleet_config or {})}
    out.update({k: "robot" for k in (overrides or {})})
    return out


def normalize_robot(r: dict) -> dict:
    """One robot record from the snapshot → the console-facing shape (live + online)."""
    overrides = dict(r.get("config_overrides") or {})
    return {
        "device_id": r.get("device_id"),
        "child": r.get("child"),
        "firmware": r.get("firmware"),
        # A snapshot that predates the allowlist served everything → reads as permitted.
        "permitted": bool(r.get("permitted", True)),
        "pending": bool(r.get("pending", False)),
        "permit_label": str(r.get("permit_label") or ""),
        "battery_level": _num(r.get("battery_level")),
        "audio_volume": _num(r.get("audio_volume")),
        "wifi_ssid": r.get("wifi_ssid"),
        "mode": r.get("mode"),
        "ota_reboot_required": bool(r.get("ota_reboot_required")),
        "config_overrides": overrides,
        # fleet ⊕ per-robot as the supervisor computed it (a pre-fleet one: per-robot only)
        "config_effective": dict(r.get("config_effective") or overrides),
        # the face cache-buster the next config push carries; "" = default look
        "face_cache_id": str(r.get("face_cache_id") or ""),
        "telemetry_count": int(r.get("telemetry_count") or 0),
        "safety_total": int(r.get("safety_total") or 0),
        "safety_unreviewed": int(r.get("safety_unreviewed") or 0),
        "online": True,                     # present in the live snapshot ⇒ connected
        "summary": robot_summary(r),
    }


_HEX = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def _face_catalog(raw) -> list:
    """The supervisor's face catalog → render-ready rows; odd rows are skipped."""
    out = []
    for slot in (raw or []):
        if not isinstance(slot, dict) or not slot.get("id"):
            continue
        options = []
        for opt in (slot.get("options") or []):
            if not isinstance(opt, dict) or not opt.get("id"):
                continue
            row = {"id": str(opt["id"]), "label": str(opt.get("label") or opt["id"])}
            # interpolated into an inline style= in the console, so shape-checked here
            if _HEX.match(str(opt.get("hex") or "")):
                row["hex"] = str(opt["hex"])
            options.append(row)
        out.append({"id": str(slot["id"]), "type": str(slot.get("type") or ""),
                    "label": str(slot.get("label") or slot["id"]),
                    "note": str(slot.get("note") or ""),
                    "options": options, "cited": bool(options)})
    return out


def normalize_fleet(snapshot: Optional[dict]) -> dict:
    """Supervisor status snapshot → the console fleet view (ok=False, empty when down)."""
    snap = snapshot or {}
    ok = bool(snap.get("ok"))
    robots = [normalize_robot(r) for r in (snap.get("robots") or [])] if ok else []
    fleet_config = dict(snap.get("fleet_config") or {}) if ok else {}
    for r in robots:
        r["config_sources"] = config_sources(fleet_config, r["config_overrides"])
    pending = [r["device_id"] for r in robots if r["pending"]]
    return {
        "ok": ok,
        "app": snap.get("app"),
        "uptime_s": int(snap.get("uptime_s") or 0),
        "robot_count": len(robots),
        "fleet_config": fleet_config,
        # the "serve anything" switch as ENFORCED, and the parent's Permit to-do list
        "allow_unverified_bots": bool(snap.get("allow_unverified_bots")) if ok else False,
        "pending": pending,
        "pending_count": len(pending),
        "schedule_modules": [str(m) for m in (snap.get("schedule_modules") or [])] if ok else [],
        # straight from moxie_sdk.faces, so the console never offers what the SDK rejects
        "face_catalog": _face_catalog(snap.get("face_catalog")) if ok else [],
        "robots": robots,
        "recent": list(snap.get("recent") or [])[-60:],
        "error": None if ok else (snap.get("error") or "supervisor not reachable"),
    }


# --- which buttons are real ---------------------------------------------------------
# Each was decided from the recovered corpus, nothing invented:
#   wakeup     → real: `/devices/{id}/commands/wakeup` + `{"command":"wakeup"}`
#                (mqtt-and-conversation.md §3.5). Reports `published`, never "awake" —
#                no acknowledgement exists in the corpus.
#   reboot     → unsupported: no cloud→robot reboot command is recovered (see below).
#   ota_status → real data, honest verdict: the appliance serves no `api/ota`
#                (cloud-protocol.md:32), so it can never truthfully say "up_to_date".

#: Console actions with no recovered command: the reason a parent reads and the
#: evidence a maintainer checks travel with the refusal.
UNSUPPORTED_ACTIONS = {
    "reboot": {
        "reason": "Rebooting Moxie remotely is not something this appliance can do.",
        "detail": "No cloud-to-robot reboot command has been recovered from the robot's "
                  "firmware. Turn Moxie off and on at the button instead.",
        "evidence": "docs/reverse-engineering/protocol/power-and-system-events.md — "
                    "STATE_SILENT_REBOOT is an on-device power state and "
                    "ShutdownRequest/SystemShutdown are events the robot emits, not "
                    "commands the cloud is known to be able to send.",
    },
}


def unsupported_action(name: str) -> dict:
    """The honest body for an action we have no command for — never `{"error": null}`."""
    known = UNSUPPORTED_ACTIONS.get(str(name)) or {}
    return {"ok": False, "supported": False, "action": str(name),
            "error": "unsupported",
            "reason": known.get("reason") or f"{name} is not supported.",
            "detail": known.get("detail") or "",
            "evidence": known.get("evidence") or ""}


def ota_status_view(snapshot: Optional[dict], device_id: Optional[str] = None) -> dict:
    """The `ota_status` body from the supervisor snapshot. `status` is
    `reboot_required` (the robot said `ota_reboot_required`), `unknown` (live state but
    no update server to compare with) or `unavailable` (no live state)."""
    snap = _dict(snapshot)
    robots = [r for r in (snap.get("robots") or []) if isinstance(r, dict)]
    robot = None
    if device_id:
        robot = next((r for r in robots if r.get("device_id") == device_id), None)
    elif len(robots) == 1:
        robot = robots[0]
    note = ("This appliance runs no OTA server, so it cannot tell you whether a newer "
            "Moxie firmware exists — only what your robot last reported about itself.")
    if not snap.get("ok") or robot is None:
        return {"status": "unavailable", "version": None, "ota_reboot_required": None,
                "ota_server": False, "supported": False, "device_id": device_id,
                "reason": "No live state for this robot (it has not connected to this "
                          "appliance, or the supervisor is not running).", "note": note}
    pending = bool(robot.get("ota_reboot_required"))
    return {
        "status": "reboot_required" if pending else "unknown",
        "version": robot.get("firmware") or None,    # the firmware the robot reported
        "ota_reboot_required": pending,
        "ota_server": False, "supported": False,
        "device_id": robot.get("device_id"),
        "reason": ("Moxie is holding a reboot to finish an update it already downloaded."
                   if pending else
                   "Moxie's reported firmware is below; whether that is the newest build "
                   "is not something this appliance can know."),
        "note": note,
    }


def resolve_device_id(robot_attrs: Optional[dict], snapshot: Optional[dict]) -> tuple:
    """`(device_id, how)` — the MQTT identity behind a parent-app robot record.

    The QR carries no device id, so a record's id and the robot's `d_<uuid>` meet only
    if the console knew both at pair time. Most trustworthy first: `"record"` (the
    record carries `mqtt-device-id`), `"sole-served"` (exactly one permitted robot is
    served), else `(None, "ambiguous" | "none")` — the caller must say so, not pick."""
    stored = str(_dict(robot_attrs).get("mqtt-device-id") or "").strip()
    if stored:
        return stored, "record"
    served = [r for r in (_dict(snapshot).get("robots") or [])
              if isinstance(r, dict) and r.get("device_id") and not r.get("pending")]
    if len(served) == 1:
        return str(served[0]["device_id"]), "sole-served"
    return None, ("ambiguous" if served else "none")
