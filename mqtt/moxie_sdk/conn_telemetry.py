"""
Connection telemetry — a durable record of what the broker connection actually did.

`docs/architecture/backlog/production-hardening.md` **§8 P1**: *"a connection telemetry
stream (connects, disconnects, CONNACK reason codes, gap durations, dropped publishes) on
the existing `JsonStore` telemetry shape."*

P0 gave the runtime six live fields on `/status` — `broker_connected`,
`last_broker_connect`, `last_broker_disconnect`, `last_connect_error`, `publish_drops`,
`store_lock_timeouts`. All six are **scalars in one process's RAM**: they say what is true
*now*, they are erased by the restart that is often the interesting event, and they cannot
answer the two questions an operator actually has —

  * *"how long was it down?"* — a `last_broker_disconnect` plus a `last_broker_connect`
    describes the **most recent** gap and forgets every earlier one;
  * *"is this getting worse?"* — a count with no history has no trend.

So this module is the history behind those scalars: an appliance-wide ring of small rows,
one per connection event, on the same `JsonStore` shape `telemetry.py` already uses for
per-robot packets. Pure — shapes, caps and the roll-up arithmetic; the runtime
(`mqtt/supervisor/moxie_runtime.py`) does the disk I/O.

**Appliance-wide, not per-robot** (`fleet/conn_events.json`, not
`robots/<id>/conn_events.json`). The supervisor has exactly one socket to the broker, and
a disconnect is not an event *about* a robot even though every robot feels it. A dropped
publish does carry the `device_id` it was meant for, because that one is per-robot — but
it is filed in the same appliance ring so the sequence *disconnect → three drops →
connect after 4.2 s* reads in order, which is the whole point of a stream.

**What is deliberately NOT here: child data.** Every field is a topic, a device id, a
reason code or a duration. No transcript, no packet payload, no `event_data` — so
`LoggingPolicy` does not gate this the way it gates `telemetry.py` (`storable_packet`),
because there is nothing about the child in it to gate. That asymmetry is intentional and
is the reason a `NO_DATA` appliance still gets a connection history: a parent who has
turned off data sharing has not asked to be blinded to their own appliance's health.

**Why the timeout row matters most.** `MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` is the one number
the brief admits is *chosen rather than measured* (§9 A13), and §8's P1 line asks for it to
be *"retuned from a week of real data rather than from this brief's guess"*. A
`lock_timeout` row with its `waited_s` is that data. This module cannot retune the number
— only a week of an appliance can — but without it there is nothing to retune *from*.
"""
from __future__ import annotations

import os
import time
from typing import Optional

#: Fleet-tier collection (`$MOXIE_DATA_DIR/fleet/conn_events.json`). The 16th collection —
#: `roster` is the 15th. The count matters only in that it keeps going up, which is the
#: brief's §2.1 observation about why a per-collection lock beats a global one.
COLLECTION = "conn_events"

# --- the kinds ----------------------------------------------------------------------
#: A CONNACK said yes. Carries `gap_s` when we had been connected before.
CONNECT = "connect"
#: The socket went away after a successful connect.
DISCONNECT = "disconnect"
#: The socket never opened (broker down, DNS gone) — `on_connect_fail`, distinct from
#: both of the above and the one that used to be invisible.
CONNECT_FAIL = "connect_fail"
#: A CONNACK said **no** (`rc=5`, not authorised). Kept apart from `connect_fail` because
#: the operator action is completely different: one is "the broker is down", the other is
#: "your credential is wrong".
REFUSED = "refused"
#: A publish the transport would not take. QoS 0 does not queue (A3), so this is a message
#: the robot never got.
PUBLISH_DROP = "publish_drop"
#: A store write another **process** would not release the record for (§5.3 A11). The row
#: that measures A13.
LOCK_TIMEOUT = "lock_timeout"
#: A deliberate, clean close — the SIGTERM handler. Its presence is what distinguishes
#: "the operator stopped it" from "it fell over", which no `disconnect` row can.
SHUTDOWN = "shutdown"

KINDS = (CONNECT, DISCONNECT, CONNECT_FAIL, REFUSED, PUBLISH_DROP, LOCK_TIMEOUT, SHUTDOWN)

#: Rows kept. An appliance in a bedroom, and `JsonStore` rewrites the whole file on every
#: append, so the cap is also the write cost. 400 rows is ~50 KB. At the soak's raised rate
#: (a broker restart every 2.5 min → ~3 rows each) that is days; at a household rate, where
#: a drop is rare, it is months. It is a ring, not an archive — `summarize()` is what
#: answers "how has it been", and a ring can answer that because the roll-up is computed
#: over whatever the ring still holds and says how many rows that was.
MAX_EVENTS = 400

#: Reason strings are `connack_string(rc)` or our own sentences today, but `rc` is
#: attacker-adjacent in the sense that it comes off the wire — so it is truncated rather
#: than trusted to be short.
MAX_REASON_CHARS = 200

#: Before this, an `at` is not a real timestamp (2020-01-01 UTC). Same floor as
#: `telemetry.py::_EPOCH_FLOOR`, and for the same reason: a row stamped 1970 sorts to the
#: front of the ring forever.
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

    Only the fields that mean something for `kind` are present, so a reader never has to
    decide whether `gap_s: 0.0` means "no gap" or "not applicable" — the absent key is the
    answer. `kind` is validated against `KINDS`: an unknown one is filed under its own
    name rather than dropped, because a row we did not anticipate is still evidence, but
    it is normalised to a string so nothing downstream can be surprised by a type.
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

    Clamped at zero because the only way to get a negative gap is a clock that moved
    backwards (NTP stepping the appliance at boot is the realistic one), and a negative
    duration in a roll-up poisons every average computed from it.
    """
    try:
        return round(max(0.0, float(value)), 3)
    except (TypeError, ValueError):
        return 0.0


def gap_since(last_disconnect: float, now: Optional[float] = None) -> Optional[float]:
    """How long the connection was down, or None when there was no previous connection.

    `0.0` (never disconnected) is None, not zero: the appliance's **first** connect has no
    gap, and reporting one as a zero-second outage would put a fake row at the head of
    every appliance's history.
    """
    if not last_disconnect:
        return None
    return _duration((now if now is not None else time.time()) - last_disconnect)


def summarize(events, *, limit: int = 20) -> dict:
    """Roll a ring of rows up into what an operator (and §5.3's bars) actually read.

    `gaps` is computed only from rows that carry a `gap_s`, i.e. reconnects — so an
    appliance that has never dropped reports `count: 0` rather than a zero-second average
    that reads like a measurement.
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
        # The lock-wait side of the same story: how long a refused store write actually
        # waited before giving up, which is the evidence A13 is missing.
        "lock_waits": {"count": len(waits),
                       "max_s": _duration(max(waits)) if waits else 0.0},
        "first_at": rows[0].get("at") if rows else None,
        "last_at": rows[-1].get("at") if rows else None,
        "latest": list(reversed(rows))[:max(0, int(limit))],
    }


def _gap_stats(gaps) -> dict:
    """`count / total_s / max_s / p95_s` over a **sorted** list of gap durations.

    p95 by nearest-rank on the sorted list (`ceil(0.95n)`-th, 1-indexed) rather than by
    interpolation: with the handful of gaps a real appliance produces, interpolating
    between two samples invents a number that was never observed, and §5.3's A3 bar is
    stated as a p95 over observed reconnects.
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

    Deliberately three states and no fourth. **`connected` is the runtime's recorded
    CONNACK state, not a guess derived from the rows** — a ring whose newest row is a
    `disconnect` may well be a currently-connected appliance whose reconnect row has not
    been written yet, and a card that read the ring instead of the state would flicker
    "down" once per reconnect.
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
