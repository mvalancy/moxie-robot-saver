"""
The durable robot roster — which robots this appliance has *ever* served.

production-hardening.md §8 P1. `MoxieRuntime.robots` is memory-only, and every way the
supervisor learns a robot is present (a `$SYS` connect line, a `/state`, an event) is an
event — none replays after a supervisor restart. The roster (every device id ever
served, re-read at boot) lets the appliance re-push config as soon as it has a broker.

A rostered robot is NOT marked connected: `self.robots` means "evidence it is here", the
roster means "served before". A push to an absent robot is a discarded QoS-0 message.

Pure: shapes, the cap and the eviction order. The runtime does the disk I/O through
`JsonStore.transaction_shared` / `read_shared`.
"""
from __future__ import annotations

import os
import time
from typing import Optional

#: Fleet-tier collection (`$MOXIE_DATA_DIR/fleet/roster.json`).
COLLECTION = "roster"

#: Devices remembered. Capped because the roster drives publishes on every broker connect;
#: on overflow the least recently seen device is evicted.
MAX_DEVICES = 64


def max_devices() -> int:
    """Devices remembered (`MOXIE_ROSTER_MAX`)."""
    raw = os.environ.get("MOXIE_ROSTER_MAX", "").strip()
    if not raw:
        return MAX_DEVICES
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return MAX_DEVICES


def resume_enabled() -> bool:
    """Whether a broker connect re-pushes config to the roster (`MOXIE_ROSTER_RESUME`).

    On by default; off still records the roster.
    """
    raw = (os.environ.get("MOXIE_ROSTER_RESUME") or "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def new_roster() -> dict:
    """An empty roster (a dict keyed by device id)."""
    return {"devices": {}}


def _rows(roster) -> dict:
    if not isinstance(roster, dict):
        return {}
    devices = roster.get("devices")
    if not isinstance(devices, dict):
        return {}
    return {str(k): v for k, v in devices.items() if isinstance(v, dict)}


def record_seen(roster, device_id: str, *, at: Optional[float] = None,
                cap: Optional[int] = None) -> dict:
    """Return a roster with `device_id` seen at `at` (default now).

    Pure (never mutates the argument), for use inside `store.transaction_shared()`.
    `first_seen` is preserved across sightings.
    """
    device_id = str(device_id or "").strip()
    if not device_id:
        return {"devices": dict(_rows(roster))}
    now = float(at if at is not None else time.time())
    rows = dict(_rows(roster))
    prev = rows.get(device_id) or {}
    first = prev.get("first_seen")
    try:
        first = float(first)
    except (TypeError, ValueError):
        first = now
    rows[device_id] = {"first_seen": round(min(first, now), 3),
                       "last_seen": round(now, 3),
                       "sightings": int(prev.get("sightings") or 0) + 1}
    limit = max_devices() if cap is None else max(0, int(cap))
    if limit and len(rows) > limit:
        for stale in sorted(rows, key=lambda d: _last_seen(rows[d]))[: len(rows) - limit]:
            rows.pop(stale, None)
    return {"devices": rows}


def forget(roster, device_id: str) -> dict:
    """Return a roster without `device_id` (un-pairing must stop the config pushes)."""
    rows = dict(_rows(roster))
    rows.pop(str(device_id or "").strip(), None)
    return {"devices": rows}


def _last_seen(row) -> float:
    try:
        return float(row.get("last_seen") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def device_ids(roster) -> list:
    """Every rostered device, most recently seen first (the reconnect-burst order)."""
    rows = _rows(roster)
    return sorted(rows, key=lambda d: -_last_seen(rows[d]))


def resume_targets(roster, connected=(), *, permitted=None) -> list:
    """Which rostered devices a broker connect should re-push config to.

    Minus `connected` robots (their own connect path already pushes) and minus any that
    `permitted` refuses (the gate refuses events, not pushes we initiate). `permitted=None`
    means no gate (open fleet / SIL).
    """
    live = {str(d) for d in (connected or ())}
    out = [d for d in device_ids(roster) if d not in live]
    if permitted is not None:
        out = [d for d in out if permitted(d)]
    return out


def summarize(roster) -> dict:
    """`{known, oldest_first_seen, newest_last_seen}` for `/status` and the console.

    Not the ids: `/status` is polled every few seconds.
    """
    rows = _rows(roster)
    if not rows:
        return {"known": 0, "oldest_first_seen": None, "newest_last_seen": None}
    firsts = []
    for row in rows.values():
        try:
            firsts.append(float(row.get("first_seen") or 0.0))
        except (TypeError, ValueError):
            pass
    firsts = [f for f in firsts if f > 0]
    return {"known": len(rows),
            "oldest_first_seen": round(min(firsts), 3) if firsts else None,
            "newest_last_seen": round(max(_last_seen(r) for r in rows.values()), 3)}
