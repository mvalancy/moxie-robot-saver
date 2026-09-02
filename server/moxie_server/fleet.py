"""
Fleet view (M6 parent-console) — normalize the MQTT supervisor's status snapshot into
the shape the console renders: one tidy record per connected robot (live state + config
overrides + telemetry count) plus a supervisor summary.

Pure + dependency-free (no fastapi/network here) so it unit-tests in the hermetic suite;
the /local/fleet endpoint in main.py is just: fetch STATUS_URL → normalize_fleet(...).
The snapshot shape comes from MoxieRuntime.status_snapshot().
"""
from __future__ import annotations
from typing import Optional


def _num(v):
    """Coerce to int/float when it looks numeric; else None (a bool isn't a number)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def robot_summary(r: dict) -> str:
    """A one-line human summary of a robot's live state for the console card."""
    bits = []
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
    if r.get("ota_reboot_required"):
        bits.append("OTA reboot pending")
    return " · ".join(bits) or "connected"


def normalize_robot(r: dict) -> dict:
    """One robot record from the snapshot → the console-facing shape (live + online)."""
    return {
        "device_id": r.get("device_id"),
        "child": r.get("child"),
        "firmware": r.get("firmware"),
        "battery_level": _num(r.get("battery_level")),
        "audio_volume": _num(r.get("audio_volume")),
        "wifi_ssid": r.get("wifi_ssid"),
        "mode": r.get("mode"),
        "ota_reboot_required": bool(r.get("ota_reboot_required")),
        "config_overrides": dict(r.get("config_overrides") or {}),
        "telemetry_count": int(r.get("telemetry_count") or 0),
        "online": True,                     # present in the live snapshot ⇒ connected
        "summary": robot_summary(r),
    }


def normalize_fleet(snapshot: Optional[dict]) -> dict:
    """Supervisor status snapshot → the console fleet view. Tolerates a None/error
    snapshot (supervisor down) by returning ok=False with an empty fleet."""
    snap = snapshot or {}
    ok = bool(snap.get("ok"))
    robots = [normalize_robot(r) for r in (snap.get("robots") or [])] if ok else []
    return {
        "ok": ok,
        "app": snap.get("app"),
        "uptime_s": int(snap.get("uptime_s") or 0),
        "robot_count": len(robots),
        "robots": robots,
        "recent": list(snap.get("recent") or [])[-60:],
        "error": None if ok else (snap.get("error") or "supervisor not reachable"),
    }


# --- telemetry / insights view (M6) -------------------------------------------------
# The runtime's GET /telemetry returns {ok, device_id, summary, events}; the console
# wants a sorted count table + tidy event rows. Pure, so it unit-tests here.

def event_counts(summary: Optional[dict]) -> list:
    """summary{by_event,last_seen} → render-ready rows, most frequent first
    (ties broken by name so the table doesn't jitter between refreshes)."""
    s = summary or {}
    by = s.get("by_event") or {}
    seen = s.get("last_seen") or {}
    rows = [{"event": str(k), "count": int(v or 0), "last_seen": _num(seen.get(k))}
            for k, v in by.items()]
    rows.sort(key=lambda r: (-r["count"], r["event"]))
    return rows


def normalize_event(e: Optional[dict]) -> dict:
    """One stored Packet → the console's event row (tolerates a partial packet)."""
    e = e or {}
    return {
        "event_name": str(e.get("event_name") or "event"),
        "recorded_at": _num(e.get("recorded_at")),
        "session_id": e.get("moxie_session_id") or "",
        "model": e.get("model"),
    }


def normalize_telemetry(payload: Optional[dict]) -> dict:
    """Runtime `/telemetry` response → the console insights shape. Tolerates a
    None/error payload (supervisor down, unknown device) with ok=False + an empty view."""
    p = payload or {}
    ok = bool(p.get("ok"))
    summary = p.get("summary") or {}
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "count": int(summary.get("count") or 0) if ok else 0,
        "by_event": event_counts(summary) if ok else [],
        "events": [normalize_event(e) for e in (p.get("events") or [])] if ok else [],
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }
