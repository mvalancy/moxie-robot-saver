"""
Connection telemetry — a durable record of what the broker connection actually did.

`docs/architecture/backlog/production-hardening.md` **§8 P1**: a connection telemetry
stream (connects, disconnects, CONNACK reason codes, gap durations, dropped publishes).

The `/status` scalars (`broker_connected`, `last_broker_connect`, …) say what is true
*now* and are erased by a restart; they cannot answer "how long was it down?" or "is this
getting worse?". This module is the history behind them: a ring of small rows, one per
connection event. Pure — shapes, caps and roll-up arithmetic; the runtime does the I/O.

**Appliance-wide** (`fleet/conn_events.json`): there is one broker socket. A dropped
publish carries its `device_id` but is filed in the same ring so a sequence like
*disconnect → drops → connect after 4.2 s* reads in order.

**No child data**: every field is a topic, device id, reason code or duration, so
`LoggingPolicy` does not gate it — a `NO_DATA` parent still sees their appliance's health.

**Lock-timeout rows** (`waited_s`) are the data for retuning `MOXIE_STORE_LOCK_TIMEOUT_S`,
the one number the brief admits is chosen rather than measured (§9 A13).
"""
from __future__ import annotations

import os
import time
from typing import Optional

#: Fleet-tier collection (`$MOXIE_DATA_DIR/fleet/conn_events.json`).
COLLECTION = "conn_events"

# --- the kinds ----------------------------------------------------------------------
#: A CONNACK said yes. Carries `gap_s` when we had been connected before.
CONNECT = "connect"
#: The socket went away after a successful connect.
DISCONNECT = "disconnect"
#: The socket never opened (broker down, DNS gone) — `on_connect_fail`.
CONNECT_FAIL = "connect_fail"
#: A CONNACK said **no** (`rc=5`). Separate from `connect_fail`: "credential wrong", not
#: "broker down".
REFUSED = "refused"
#: A publish the transport would not take. QoS 0 does not queue (A3), so this is a message
#: the robot never got.
PUBLISH_DROP = "publish_drop"
#: A store write another **process** would not release the record for (§5.3 A11).
LOCK_TIMEOUT = "lock_timeout"
#: A deliberate, clean close (SIGTERM) — "stopped", as opposed to "fell over".
SHUTDOWN = "shutdown"

KINDS = (CONNECT, DISCONNECT, CONNECT_FAIL, REFUSED, PUBLISH_DROP, LOCK_TIMEOUT, SHUTDOWN)

#: Rows kept (~50 KB; `JsonStore` rewrites the file per append, so the cap is also the
#: write cost). Days at the soak's rate, months in a household. A ring, not an archive.
MAX_EVENTS = 400

#: Reasons come partly off the wire, so they are truncated rather than trusted.
MAX_REASON_CHARS = 200

#: Before this (2020-01-01 UTC) an `at` is not a real timestamp (as `telemetry.py`).
_EPOCH_FLOOR = 1577836800


def max_events() -> int:
    """Rows kept (`MOXIE_CONN_MAX_EVENTS`)."""
    raw = os.environ.get("MOXIE_CONN_MAX_EVENTS", "").strip()
    if not raw:
        return MAX_EVENTS
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return MAX_EVENTS


def _clean(text) -> str:
    return str(text or "").strip()[:MAX_REASON_CHARS]


def build_event(kind: str, *, at: Optional[float] = None, reason: str = "",
                device_id: str = "", topic: str = "",
                gap_s: Optional[float] = None,
                waited_s: Optional[float] = None) -> dict:
    """One connection-event row.

    Only fields meaningful for `kind` are present (an absent `gap_s` means "not
    applicable"). An unknown `kind` is kept under its own name — still evidence.
    """
    row = {"kind": str(kind or "").strip() or "unknown",
           "at": _stamp(at)}
    if reason:
        row["reason"] = _clean(reason)
    if device_id:
        row["device_id"] = _clean(device_id)
    if topic:
        row["topic"] = _clean(topic)
    if gap_s is not None:
        row["gap_s"] = _duration(gap_s)
    if waited_s is not None:
        row["waited_s"] = _duration(waited_s)
    return row


def _stamp(at) -> int:
    """A whole-second timestamp, floored. `None` means now."""
    if at is None:
        return int(time.time())
    try:
        val = int(float(at))
    except (TypeError, ValueError):
        return int(time.time())
    return val if val >= _EPOCH_FLOOR else int(time.time())


def _duration(value) -> float:
    """A non-negative duration in seconds, to 3 dp.

    Clamped at zero: a clock stepped backwards (NTP at boot) must not poison averages.
    """
    try:
        return round(max(0.0, float(value)), 3)
    except (TypeError, ValueError):
        return 0.0


def gap_since(last_disconnect: float, now: Optional[float] = None) -> Optional[float]:
    """How long the connection was down, or None when there was no previous connection.

    The first connect has no gap (not a zero-second outage).
    """
    if not last_disconnect:
        return None
    return _duration((now if now is not None else time.time()) - last_disconnect)


def summarize(events, *, limit: int = 20) -> dict:
    """Roll a ring of rows up into what an operator (and §5.3's bars) actually read.

    `gaps` uses only reconnect rows (`gap_s`), so a never-dropped appliance reports
    `count: 0`, not a fake zero average.
    """
    rows = [e for e in (events or []) if isinstance(e, dict)]
    by_kind: dict = {}
    for e in rows:
        k = str(e.get("kind") or "unknown")
        by_kind[k] = by_kind.get(k, 0) + 1
    gaps = sorted(_duration(e["gap_s"]) for e in rows if e.get("gap_s") is not None)
    waits = [_duration(e["waited_s"]) for e in rows if e.get("waited_s") is not None]
    return {
        "count": len(rows),
        "by_kind": by_kind,
        "gaps": _gap_stats(gaps),
        # How long refused store writes waited — the evidence A13 needs.
        "lock_waits": {"count": len(waits),
                       "max_s": _duration(max(waits)) if waits else 0.0},
        "first_at": rows[0].get("at") if rows else None,
        "last_at": rows[-1].get("at") if rows else None,
        "latest": list(reversed(rows))[:max(0, int(limit))],
    }


def _gap_stats(gaps) -> dict:
    """`count / total_s / max_s / p95_s` over a **sorted** list of gap durations.

    p95 by nearest rank (`ceil(0.95n)`-th), never interpolated — every reported value
    was actually observed (§5.3 A3).
    """
    if not gaps:
        return {"count": 0, "total_s": 0.0, "max_s": 0.0, "p95_s": 0.0}
    import math
    rank = max(1, math.ceil(0.95 * len(gaps)))
    return {"count": len(gaps),
            "total_s": _duration(sum(gaps)),
            "max_s": _duration(gaps[-1]),
            "p95_s": _duration(gaps[rank - 1])}


def health(summary: Optional[dict], *, connected: bool) -> dict:
    """The one-line verdict the console renders above the rows.

    Three states. **`connected` is the recorded CONNACK state, not derived from the
    rows** — the newest row may lag a reconnect, and the card must not flicker "down".
    """
    s = summary if isinstance(summary, dict) else {}
    by = s.get("by_kind") if isinstance(s.get("by_kind"), dict) else {}
    drops = int(by.get(PUBLISH_DROP) or 0)
    outages = int(by.get(DISCONNECT) or 0) + int(by.get(CONNECT_FAIL) or 0)
    refusals = int(by.get(REFUSED) or 0)
    if not connected:
        state = "down"
    elif refusals or drops or outages:
        state = "recovered"
    else:
        state = "steady"
    return {"state": state, "outages": outages, "refusals": refusals, "drops": drops,
            "lock_timeouts": int(by.get(LOCK_TIMEOUT) or 0)}
