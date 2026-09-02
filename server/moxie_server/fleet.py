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
    # The pairing gate leads, because it changes what every other line means: a pending
    # robot's config is the minimal child-free one, and nothing else is being served to it.
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
    if r.get("safety_unreviewed"):
        bits.append(f"{r['safety_unreviewed']} safety flag"
                    f"{'' if r['safety_unreviewed'] == 1 else 's'} to review")
    if r.get("ota_reboot_required"):
        bits.append("OTA reboot pending")
    return " · ".join(bits) or "connected"


def config_sources(fleet_config: Optional[dict], overrides: Optional[dict]) -> dict:
    """`{key: "robot" | "fleet"}` — which layer each effective override came from.

    The console renders it as a "set for every robot" hint next to a field, so a parent
    can tell a house rule from a per-robot exception without opening two screens. Pure:
    the layering itself lives in `moxie_sdk.cloud_config.merge_config_layers`; this only
    labels it, top-level key by top-level key (a per-robot key wins the label even when
    the layers deep-merged, because that is the field the parent last touched here)."""
    out = {k: "fleet" for k in (fleet_config or {})}
    out.update({k: "robot" for k in (overrides or {})})
    return out


def normalize_robot(r: dict) -> dict:
    """One robot record from the snapshot → the console-facing shape (live + online)."""
    return {
        "device_id": r.get("device_id"),
        "child": r.get("child"),
        "firmware": r.get("firmware"),
        # Device allowlist. A snapshot from a supervisor that predates the gate carries
        # neither key — it served everything, so it reads back as permitted.
        "permitted": bool(r.get("permitted", True)),
        "pending": bool(r.get("pending", False)),
        "permit_label": str(r.get("permit_label") or ""),
        "battery_level": _num(r.get("battery_level")),
        "audio_volume": _num(r.get("audio_volume")),
        "wifi_ssid": r.get("wifi_ssid"),
        "mode": r.get("mode"),
        "ota_reboot_required": bool(r.get("ota_reboot_required")),
        "config_overrides": dict(r.get("config_overrides") or {}),
        # fleet ⊕ per-robot, as the supervisor computed it (falls back to the per-robot
        # layer for a pre-fleet snapshot, so an older supervisor still renders).
        "config_effective": dict(r.get("config_effective")
                                 or r.get("config_overrides") or {}),
        "telemetry_count": int(r.get("telemetry_count") or 0),
        "safety_total": int(r.get("safety_total") or 0),
        "safety_unreviewed": int(r.get("safety_unreviewed") or 0),
        "online": True,                     # present in the live snapshot ⇒ connected
        "summary": robot_summary(r),
    }


def normalize_fleet(snapshot: Optional[dict]) -> dict:
    """Supervisor status snapshot → the console fleet view. Tolerates a None/error
    snapshot (supervisor down) by returning ok=False with an empty fleet."""
    snap = snapshot or {}
    ok = bool(snap.get("ok"))
    robots = [normalize_robot(r) for r in (snap.get("robots") or [])] if ok else []
    fleet_config = dict(snap.get("fleet_config") or {}) if ok else {}
    for r in robots:
        r["config_sources"] = config_sources(fleet_config, r["config_overrides"])
    return {
        "ok": ok,
        "app": snap.get("app"),
        "uptime_s": int(snap.get("uptime_s") or 0),
        "robot_count": len(robots),
        # appliance-wide defaults every robot inherits (audit ADOPT #6) + the on-board
        # module ids a parent may schedule, so the console never copies the catalog.
        "fleet_config": fleet_config,
        # The pairing gate (audit §3.1): the appliance-wide "serve anything that
        # connects" switch as the supervisor ENFORCES it (env included), plus the robots
        # waiting for a parent to click Permit. `pending` is the console's whole to-do
        # list, so it is lifted out of `robots` rather than left to be filtered client-side.
        "allow_unverified_bots": bool(snap.get("allow_unverified_bots")) if ok else False,
        "pending": [r["device_id"] for r in robots if r["pending"]],
        "pending_count": sum(1 for r in robots if r["pending"]),
        "schedule_modules": [str(m) for m in (snap.get("schedule_modules") or [])] if ok else [],
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


# --- safety review queue (ai-seam §2 InputSafety) ------------------------------------
# The runtime's GET /safety returns {ok, device_id, policy, detail, enabled, classifier,
# counts, unreviewed, labels, events}; the console wants a sorted category table and tidy
# rows it can render without knowing the storage shape. Pure, so it unit-tests here.

def safety_counts(view: Optional[dict]) -> list:
    """counts.by_category + labels → render-ready rows, most frequent first (ties broken
    by label so the table doesn't jitter between refreshes)."""
    v = view or {}
    by = (v.get("counts") or {}).get("by_category") or {}
    labels = v.get("labels") or {}
    rows = [{"category": str(k), "label": str(labels.get(k) or k), "count": int(n or 0)}
            for k, n in by.items()]
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def normalize_safety_event(e: Optional[dict], labels: Optional[dict] = None) -> dict:
    """One stored review-queue row → the console's event row.

    `excerpt` is already redacted by the runtime (`moxie_sdk.safety.redact` masks the
    matched words and drops the excerpt entirely if masking could not be verified), and
    it is absent under LoggingPolicy NO_DATA — so this never has raw unsafe text to
    handle. It is passed through as-is; the UI escapes it.
    """
    e = e or {}
    labels = labels or {}
    cats = [str(c) for c in (e.get("categories") or [])]
    return {
        "id": str(e.get("id") or ""),
        "ts": _num(e.get("ts")),
        "side": "moxie" if e.get("side") == "moxie" else "child",
        "action": "block" if e.get("action") == "block" else "flag",
        "categories": cats,
        "labels": [str(labels.get(c) or c) for c in cats],
        "escalate": bool(e.get("escalate")),
        "excerpt": str(e.get("excerpt") or ""),
        "reviewed": bool(e.get("reviewed")),
    }


def normalize_safety(payload: Optional[dict]) -> dict:
    """Runtime `/safety` response → the console's 🛡️ Safety panel shape. Tolerates a
    None/error payload (supervisor down, unknown device) with ok=false and an empty view."""
    p = payload or {}
    ok = bool(p.get("ok"))
    labels = p.get("labels") or {}
    counts = p.get("counts") or {}
    events = [normalize_safety_event(e, labels) for e in (p.get("events") or [])] if ok else []
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "enabled": bool(p.get("enabled")) if ok else False,
        "classifier": p.get("classifier") if ok else None,
        "policy": p.get("policy") if ok else None,
        "detail": bool(p.get("detail")) if ok else False,
        "total": int(counts.get("total") or 0) if ok else 0,
        "blocked": int((counts.get("by_action") or {}).get("block") or 0) if ok else 0,
        "flagged": int((counts.get("by_action") or {}).get("flag") or 0) if ok else 0,
        "unreviewed": int(p.get("unreviewed") or 0) if ok else 0,
        "by_category": safety_counts(p) if ok else [],
        "events": events,
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }
