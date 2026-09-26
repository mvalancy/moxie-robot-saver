"""The planner's inputs: clock helpers, mentor-behavior history, bedtime, parent
requests, telemetry, and template selection."""
from __future__ import annotations

import datetime
import hashlib

from .catalog import (ABANDONED, CATEGORY_ENERGY, COMPLETED, DEFAULT_CHILD_NAME,
    DEFAULT_ENERGY, DEFAULT_TEMPLATE, FTUE_COMPLETION_COUNTS, MODULE_LABELS,
    ONBOARD_MODULES, SLOT_MINUTES, TIEBREAK_RANGE, TIME_BUCKETS)
from .normalize import _recommendation_list


# ---------------------------------------------------------------- mentor behaviors ----

def completed_counts(mentor_behaviors) -> dict:
    """`{module_id: COMPLETED count}` from a robot's stored MentorBehavior records.

    Records with any other `action` (PRESENTED / QUIT / REFUSED / …, MentorBehavior.proto
    `MentorAction`) are counted as *offered*, not done, so a refused activity can come
    back around."""
    counts: dict = {}
    for mbh in mentor_behaviors or ():
        if not isinstance(mbh, dict):
            continue
        if str(mbh.get("action", "")).upper() != COMPLETED:
            continue
        mid = mbh.get("module_id")
        if mid:
            counts[mid] = counts.get(mid, 0) + 1
    return counts


def ftue_skips(counts: dict) -> set:
    """Which onboarding modules this robot is done with (drop them from the day)."""
    skips = {m for m, need in FTUE_COMPLETION_COUNTS.items()
             if m != "WELCOME" and counts.get(m, 0) >= need}
    if skips or any(v > 0 for v in counts.values()):
        skips.add("WELCOME")            # anything completed at all retires the welcome
    return skips


# --------------------------------------------------------------------- normalizing ----


def _iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value):
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


def time_bucket(dt) -> str:
    """Which part of the day a clock time falls in (`TIME_BUCKETS`)."""
    hour = dt.hour
    for name, start, end in TIME_BUCKETS:
        if start <= end:
            if start <= hour < end:
                return name
        elif hour >= start or hour < end:
            return name
    return "night"


def category_energy(category) -> str:
    """`ModuleCategory` → energy class (`CATEGORY_ENERGY`, `DEFAULT_ENERGY`)."""
    return CATEGORY_ENERGY.get(str(category or "").upper(), DEFAULT_ENERGY)


def module_label(rec) -> str:
    """A parent-readable name for one entry. `Recommendation.module_name` (a real proto
    field, RemoteChat.proto:26-34) wins; then `MODULE_LABELS`; then the id verbatim."""
    if isinstance(rec, dict):
        if rec.get("module_name"):
            return str(rec["module_name"])
        mid = str(rec.get("module_id") or "")
    else:
        mid = str(rec or "")
    return MODULE_LABELS.get(mid, mid)


def _tiebreak(device_id: str, day: str, module_id: str) -> int:
    """A stable 0…`TIEBREAK_RANGE`-1 jitter. `blake2b` (not `hash()`) so the same day
    plans identically under any `PYTHONHASHSEED`, in any process, on any machine."""
    digest = hashlib.blake2b(f"{device_id}|{day}|{module_id}".encode(),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big") % TIEBREAK_RANGE


# ------------------------------------------------------------------- input signals ----

def module_history(mentor_behaviors) -> dict:
    """`{module_id: {seen, completed, abandoned, last_ts, last_action}}` from a robot's
    MentorBehavior records — the only recovered signal of finished vs. abandoned.
    `last_ts` is the robot's own stamp; the planner only asks "how many days ago"."""
    out: dict = {}
    for mbh in mentor_behaviors or ():
        if not isinstance(mbh, dict):
            continue
        mid = mbh.get("module_id")
        if not mid:
            continue
        action = str(mbh.get("action", "")).upper()
        rec = out.setdefault(mid, {"seen": 0, "completed": 0, "abandoned": 0,
                                   "last_ts": None, "last_action": ""})
        rec["seen"] += 1
        if action == COMPLETED:
            rec["completed"] += 1
        elif action in ABANDONED:
            rec["abandoned"] += 1
        ts = mbh.get("timestamp")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            if rec["last_ts"] is None or ts > rec["last_ts"]:
                rec["last_ts"] = ts
                rec["last_action"] = action
    return out


def _age_days(last_ts, now) -> float | None:
    """How long ago `MentorBehavior.timestamp` was, in days (None when unstamped). The
    unit is unstated in the proto and robots stamp milliseconds, so a value that is
    plainly milliseconds is divided down (same rule as `cloud_config._scheduled_at`)."""
    if last_ts is None:
        return None
    try:
        seconds = float(last_ts)
    except (TypeError, ValueError):
        return None
    if seconds >= 1e11:                       # milliseconds, not seconds
        seconds /= 1000.0
    return (now.timestamp() - seconds) / 86400.0


def bedtime_window(effective_config, now) -> dict:
    """Today's bedtime window from the effective config (`weekday_bedtime` Mon-Fri,
    `weekend_bedtime` Sat/Sun, each `["HH:MM","HH:MM"]`), or `{"enabled": False}`."""
    cfg = effective_config if isinstance(effective_config, dict) else {}
    kind = "weekday" if now.weekday() < 5 else "weekend"
    value = cfg.get(f"{kind}_bedtime")
    if not value:                             # fall back to the wire spelling if present
        starts = cfg.get(f"{kind}_bedtime_starts_at")
        ends = cfg.get(f"{kind}_bedtime_ends_at")
        value = [starts, ends] if starts and ends else None
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(value)):
        return {"enabled": False, "kind": kind}
    return {"enabled": True, "kind": kind,
            "starts_at": str(value[0]), "ends_at": str(value[1])}


def _hhmm(value) -> int:
    """"HH:MM" → minutes past midnight."""
    h, _, m = str(value).partition(":")
    return int(h) * 60 + int(m)


def in_bedtime(dt, window) -> bool:
    """Is this clock time inside the bedtime window? Windows wrap midnight."""
    if not (window or {}).get("enabled"):
        return False
    try:
        start, end = _hhmm(window["starts_at"]), _hhmm(window["ends_at"])
    except (KeyError, ValueError):
        return False
    minute = dt.hour * 60 + dt.minute
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end            # 20:00 → 07:00


def parent_requests_due(effective_config, now, *, slot_count, first_slot_index=0,
                        window=None) -> list:
    """Today's `SchedulePreferences.parent_requests[]`, each resolved to the plan slot
    nearest its `scheduled_at` (epoch seconds). Clamped into the plan, and back out of
    bedtime; two requests never share a slot (the earlier keeps it)."""
    cfg = effective_config if isinstance(effective_config, dict) else {}
    prefs = cfg.get("schedule_preferences") or {}
    items = prefs.get("parent_requests") if isinstance(prefs, dict) else None
    out, taken = [], set()
    for item in sorted([i for i in (items or ()) if isinstance(i, dict)],
                       key=lambda i: i.get("scheduled_at") or 0):
        mid = str(item.get("module_id") or "").strip().upper()
        raw = item.get("scheduled_at")
        if not mid or not isinstance(raw, (int, float)) or isinstance(raw, bool):
            continue
        try:
            when = datetime.datetime.fromtimestamp(float(raw))
        except (OverflowError, OSError, ValueError):
            continue
        entry = {"module_id": mid, "scheduled_at": int(raw),
                 "at": when.strftime("%H:%M"), "due_today": when.date() == now.date(),
                 "slot": None}
        if entry["due_today"] and slot_count > 0:
            offset = (when - now).total_seconds() / 60.0 / SLOT_MINUTES
            slot = int(round(offset)) - first_slot_index
            slot = max(0, min(slot_count - 1, slot))
            if window and window.get("enabled"):
                while slot > 0 and in_bedtime(
                        now + datetime.timedelta(
                            minutes=(first_slot_index + slot) * SLOT_MINUTES), window):
                    slot -= 1
                    entry.setdefault("reason_codes", []).append("bedtime_clamped")
            while slot in taken and slot + 1 < slot_count:
                slot += 1
            if slot not in taken:
                taken.add(slot)
                entry["slot"] = slot
        out.append(entry)
    return out


def telemetry_signals(telemetry_summary, packets=()) -> dict:
    """What the recovered telemetry envelope can honestly tell a planner: packet count,
    event names, sessions, and a `recorded_at` histogram — never a module signal (see the
    module docstring), so `carries_module_signal` is False."""
    summary = telemetry_summary if isinstance(telemetry_summary, dict) else {}
    by_event = summary.get("by_event") if isinstance(summary.get("by_event"), dict) else {}
    items = [p for p in (packets or ()) if isinstance(p, dict)]
    if not items:
        items = [p for p in (summary.get("latest") or ()) if isinstance(p, dict)]
    hours: dict = {}
    sessions = set()
    for pkt in items:
        ts = pkt.get("recorded_at")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            try:
                bucket = time_bucket(datetime.datetime.fromtimestamp(float(ts)))
            except (OverflowError, OSError, ValueError):
                continue
            hours[bucket] = hours.get(bucket, 0) + 1
        if pkt.get("moxie_session_id"):
            sessions.add(pkt["moxie_session_id"])
    return {"count": int(summary.get("count") or len(items)),
            "by_event": dict(by_event), "sessions": len(sessions),
            "active_buckets": hours, "carries_module_signal": False,
            "note": "Packet.event_name is a free string in the recovered proto; no "
                    "module launch/exit vocabulary is established, so completion "
                    "affinity comes from mentor_behaviors only."}


# -------------------------------------------------------------------------- inputs ----

def _schedules_entries(content_schedules) -> list:
    """Normalize a content module's `schedules[]` — dicts *or* `ContentModule` schedule
    objects — into `[{"name": str, "schedule": dict}]`."""
    out = []
    for item in content_schedules or ():
        if isinstance(item, dict):
            name, sched = item.get("name", ""), item.get("schedule")
        else:
            name, sched = getattr(item, "name", ""), getattr(item, "schedule", None)
        if isinstance(sched, dict) and sched:
            out.append({"name": str(name or ""), "schedule": sched})
    return out


def select_template(content_schedules, *, bucket: str = "", name: str = "") -> dict:
    """Pick the `schedules[]` entry to plan from.

    Order: an explicit `name`; then an entry named after the current time-of-day bucket
    (`morning`/`afternoon`/`evening`/`night`) so a module can ship a wind-down day; then
    the first entry; then `DEFAULT_TEMPLATE`. Read-only — nothing is mutated.
    """
    entries = _schedules_entries(content_schedules)
    if not entries:
        return dict(DEFAULT_TEMPLATE)
    if name:
        for e in entries:
            if e["name"] == name:
                return dict(e["schedule"])
        return dict(DEFAULT_TEMPLATE)
    if bucket:
        for e in entries:
            if e["name"].lower() == bucket:
                return dict(e["schedule"])
    return dict(entries[0]["schedule"])


def plan_inputs(device_id, now=None, *, mentor_behaviors=(), telemetry_summary=None,
                effective_config=None, content_schedules=None, catalog=None,
                template=None, day: str = "", child_name: str = "",
                telemetry_packets=()) -> dict:
    """Every signal `plan_day` may see, as a JSON-safe dict — also the "inputs summary"
    `GET /schedule` shows a parent. Pure: pass `now`; no store, no MQTT."""
    now = _parse_iso(now) if now is not None else datetime.datetime.now()
    day = day or now.date().isoformat()
    bucket = time_bucket(now)
    if template is None:
        template = select_template(content_schedules, bucket=bucket)
    template = dict(template)
    gen = template.get("generate") or {}
    history = module_history(mentor_behaviors)
    counts = completed_counts(mentor_behaviors)
    skips = ftue_skips(counts)
    window = bedtime_window(effective_config, now)

    prefix = [r for r in _recommendation_list(template.get("provided_schedule"))
              if r.get("module_id") not in skips]
    module_count = int(gen.get("module_count", 0) or 0)
    slots = []
    for i in range(module_count):
        at = now + datetime.timedelta(minutes=(len(prefix) + i) * SLOT_MINUTES)
        slots.append({"index": i, "at": at.strftime("%H:%M"),
                      "bucket": time_bucket(at), "in_bedtime": in_bedtime(at, window)})
    requests = parent_requests_due(effective_config, now, slot_count=module_count,
                                   first_slot_index=len(prefix), window=window)

    base = list(catalog if catalog is not None else ONBOARD_MODULES)
    return {
        "device_id": str(device_id or ""), "day": day, "now": _iso(now),
        "bucket": bucket, "slot_minutes": SLOT_MINUTES,
        "child_name": str(child_name or "") or DEFAULT_CHILD_NAME,
        "template": template,
        "catalog": [dict(m) for m in base],
        "history": history, "completed_counts": counts,
        "ftue_skips": sorted(skips),
        "bedtime": window, "slots": slots, "parent_requests": requests,
        "telemetry": telemetry_signals(telemetry_summary, telemetry_packets),
    }


# ------------------------------------------------------------------------- scoring ----
