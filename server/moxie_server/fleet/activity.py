"""📈 Insights (telemetry), 🔌 the broker connection, and the 🛡️ safety review queue.

Each view tolerates a None/error payload (supervisor down, unknown device) with
`ok:false` and an empty view — never an exception, and never an empty history rendered
as if the robot had simply been quiet.
"""
from __future__ import annotations
from typing import Optional

from ._coerce import _dict, _int, _num


# --- telemetry ------------------------------------------------------------------------
def event_counts(summary: Optional[dict]) -> list:
    """summary{by_event,last_seen} → rows, most frequent first (ties by name, no jitter)."""
    s = summary or {}
    seen = s.get("last_seen") or {}
    rows = [{"event": str(k), "count": int(v or 0), "last_seen": _num(seen.get(k))}
            for k, v in (s.get("by_event") or {}).items()]
    rows.sort(key=lambda r: (-r["count"], r["event"]))
    return rows


def normalize_event(e: Optional[dict]) -> dict:
    """One stored Packet → the console's event row (tolerates a partial packet)."""
    e = e or {}
    return {"event_name": str(e.get("event_name") or "event"),
            "recorded_at": _num(e.get("recorded_at")),
            "session_id": e.get("moxie_session_id") or "",
            "model": e.get("model")}


def normalize_history(rows) -> list:
    """The runtime's zero-filled daily roll-up → one row per day, oldest first. `share`
    is the day's count against the busiest day (0.0–1.0), all the card's bars need."""
    out = [{"day": str(r["day"]), "count": max(0, _int(r.get("count"))),
            "top_event": str(r["top_event"]) if r.get("top_event") else None}
           for r in (rows if isinstance(rows, list) else [])
           if isinstance(r, dict) and r.get("day")]
    peak = max([r["count"] for r in out] or [0])
    for r in out:
        r["share"] = round(r["count"] / peak, 4) if peak else 0.0
    return out


def normalize_telemetry(payload: Optional[dict]) -> dict:
    """Runtime `/telemetry` → the 📈 card. Besides counts and events it carries what the
    card needs to be honest about history: `history`, `totals` (lifetime count and how
    far back the store reaches), `retention`, and `policy`/`persisted` — under NO_DATA
    the card must say "nothing is being kept", not show an empty week as quiet."""
    p = _dict(payload)
    ok = bool(p.get("ok"))
    summary, totals, retention = (_dict(p.get(k)) for k in ("summary", "totals", "retention"))
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "count": int(summary.get("count") or 0) if ok else 0,
        "by_event": event_counts(summary) if ok else [],
        "events": [normalize_event(e) for e in (p.get("events") or [])] if ok else [],
        "history": normalize_history(p.get("history")) if ok else [],
        "policy": str(p.get("policy") or "") if ok else "",
        # only ever True when the runtime said so (an older runtime has no history)
        "persisted": bool(p.get("persisted")) if ok else False,
        "connected": bool(p.get("connected")) if ok else False,
        "totals": {"total": _int(totals.get("total")),
                   "days_kept": _int(totals.get("days_kept")),
                   "first_day": totals.get("first_day") or None,
                   "last_day": totals.get("last_day") or None,
                   "dropped_days": _int(totals.get("dropped_days"))},
        "retention": {"packets": _int(retention.get("packets")),
                      "days": _int(retention.get("days"))},
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }


# --- the broker connection's durable history ------------------------------------------
#: One parent-readable sentence per row kind (keys: `moxie_sdk/conn_telemetry.py`).
CONNECTION_LABELS = {
    "connect": "Connected to the broker",
    "disconnect": "Lost the broker connection",
    "connect_fail": "Could not reach the broker",
    "refused": "The broker refused the connection",
    "publish_drop": "A message was dropped",
    "lock_timeout": "A save was refused (another process held the file)",
    "shutdown": "Stopped cleanly",
}

#: The verdict for `health.state`. "recovered" is deliberately not "healthy": up now
#: after nine drops this hour is not the same as never having dropped.
CONNECTION_STATES = {
    "steady": "Connected, with nothing to report",
    "recovered": "Connected now — but it has not been the whole time",
    "down": "Not connected to the broker",
}


def normalize_connection_event(e: Optional[dict]) -> dict:
    """One connection row → the card's shape. Tolerates a row from a newer runtime."""
    e = _dict(e)
    kind = str(e.get("kind") or "unknown")
    row = {"kind": kind,
           "label": CONNECTION_LABELS.get(kind, kind.replace("_", " ")),
           "at": _int(e.get("at")),
           "reason": str(e.get("reason") or ""),
           "device_id": str(e.get("device_id") or "")}
    # present only when the kind means them — never flattened into a "0s outage"
    for k in ("gap_s", "waited_s"):
        if e.get(k) is not None:
            row[k] = float(_num(e.get(k)) or 0.0)
    return row


def normalize_connection(payload: Optional[dict]) -> dict:
    """Runtime `GET /conn` → the console's connection view (live half + recorded half)."""
    p = _dict(payload)
    ok = bool(p.get("ok"))
    summary, health = _dict(p.get("summary")), _dict(p.get("health"))
    gaps = _dict(summary.get("gaps"))
    state = str(health.get("state") or ("steady" if ok else ""))

    def n(v):
        return _int(v) if ok else 0
    return {
        "ok": ok,
        "connected": bool(p.get("connected")) if ok else False,
        "state": state if ok else "",
        "verdict": CONNECTION_STATES.get(state, "") if ok else "",
        "last_error": str(p.get("last_error") or "") if ok else "",
        "uptime_s": n(p.get("uptime_s")),
        "count": n(summary.get("count")),
        "outages": n(health.get("outages")),
        "refusals": n(health.get("refusals")),
        "drops": n(health.get("drops")),
        "lock_timeouts": n(health.get("lock_timeouts")),
        "gaps": {"count": _int(gaps.get("count")),
                 **{k: float(_num(gaps.get(k)) or 0.0) for k in ("total_s", "max_s", "p95_s")}},
        "events": [normalize_connection_event(e) for e in (p.get("events") or [])] if ok else [],
        "retention": {"events": _int(_dict(p.get("retention")).get("events"))},
        # how many robots this appliance has ever served, beside the connection
        "roster": {"known": _int(_dict(p.get("roster")).get("known"))},
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }


# --- safety review queue (ai-seam §2 InputSafety) ---------------------------------------
def safety_counts(view: Optional[dict]) -> list:
    """counts.by_category + labels → rows, most frequent first (ties by label)."""
    v = view or {}
    labels = v.get("labels") or {}
    rows = [{"category": str(k), "label": str(labels.get(k) or k), "count": int(n or 0)}
            for k, n in ((v.get("counts") or {}).get("by_category") or {}).items()]
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def normalize_safety_event(e: Optional[dict], labels: Optional[dict] = None) -> dict:
    """One review-queue row. `excerpt` is already redacted by the runtime (and absent
    under NO_DATA), so there is no raw unsafe text here; the UI escapes it."""
    e, labels = e or {}, labels or {}
    cats = [str(c) for c in (e.get("categories") or [])]
    return {"id": str(e.get("id") or ""),
            "ts": _num(e.get("ts")),
            "side": "moxie" if e.get("side") == "moxie" else "child",
            "action": "block" if e.get("action") == "block" else "flag",
            "categories": cats,
            "labels": [str(labels.get(c) or c) for c in cats],
            "escalate": bool(e.get("escalate")),
            "excerpt": str(e.get("excerpt") or ""),
            "reviewed": bool(e.get("reviewed"))}


def normalize_safety(payload: Optional[dict]) -> dict:
    """Runtime `/safety` → the 🛡️ panel."""
    p = payload or {}
    ok = bool(p.get("ok"))
    counts = _dict(p.get("counts"))
    by_action = _dict(counts.get("by_action"))
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "enabled": bool(p.get("enabled")) if ok else False,
        "classifier": p.get("classifier") if ok else None,
        "policy": p.get("policy") if ok else None,
        "detail": bool(p.get("detail")) if ok else False,
        "total": int(counts.get("total") or 0) if ok else 0,
        "blocked": int(by_action.get("block") or 0) if ok else 0,
        "flagged": int(by_action.get("flag") or 0) if ok else 0,
        "unreviewed": int(p.get("unreviewed") or 0) if ok else 0,
        "by_category": safety_counts(p) if ok else [],
        "events": [normalize_safety_event(e, p.get("labels") or {})
                   for e in (p.get("events") or [])] if ok else [],
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }
