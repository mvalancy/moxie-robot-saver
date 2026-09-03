"""
The durable robot roster — which robots this appliance has *ever* served.

`docs/architecture/backlog/production-hardening.md` **§8 P1**: *"a durable robot roster (a
15th collection) so a restart re-pushes config to every robot it has ever seen rather than
waiting for an event."*

The hole it fills, precisely (brief §2.2, A15). `MoxieRuntime.robots` is memory-only, and
the supervisor learns a robot is present in exactly three ways:

  1. a `$SYS/broker/log` connect line — **live-only, never replayed** on re-subscribe;
  2. a `/state` publish — which a real Moxie sends *on its own connect*, not on ours;
  3. an event, which P0's C6 now registers from.

Every one of those is an **event**, and after a supervisor restart with the robot still
happily connected there is no event to have. So the appliance sits there knowing nothing,
and the robot gets no config push until the child next speaks — which, at bedtime, may be
tomorrow. C6 made that recovery *possible*; it did not make it *prompt*.

The roster makes it prompt: a small fleet-tier record of every device id we have served,
re-read at boot, and pushed to as soon as there is a broker again.

**What this file refuses to do, and why it is the whole design.** A rostered robot is
**not** marked connected. `self.robots` still means *"we have evidence this robot is
here"*; the roster means *"we have served this robot before"*. Conflating them would put
phantom robots on the parent's console and in `/status` — the exact disease this brief
exists to cure, which is a status field that reports a comfortable belief instead of an
observation. A config push to an absent robot is a QoS 0 message the broker discards; that
costs nothing and claims nothing, and it is the only honest way to say hello to a robot
that may or may not be listening.

Pure: shapes, the cap and the eviction order. The runtime does the disk I/O through
`JsonStore.transaction_shared` / `read_shared`.
"""
from __future__ import annotations

import os
import time
from typing import Optional

#: Fleet-tier collection (`$MOXIE_DATA_DIR/fleet/roster.json`). Appliance-wide because the
#: question it answers — *"who do we serve?"* — is not about any one robot.
COLLECTION = "roster"

#: Devices remembered. A household has one to three; a classroom or a repair bench might
#: cycle through dozens. The cap exists because the roster drives **publishes** on every
#: broker connect, so an unbounded roster is an unbounded reconnect burst — and because a
#: JSON file that grows forever is the failure §5.3's A9 ("no state grows without bound")
#: is written to catch. When it overflows the **least recently seen** device is evicted,
#: which is the only ordering that keeps the robots actually in use.
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

    On by default — it is the feature. Off is for an operator who has a reason (a bench
    with fifty stale device ids, a broker they do not want us publishing into), and it
    leaves the roster still *recorded*, so turning it back on needs no rediscovery.
    """
    raw = (os.environ.get("MOXIE_ROSTER_RESUME") or "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def new_roster() -> dict:
    """An empty roster. A dict rather than a list because the hot operation is
    *"have we seen this id"*, and because a per-device row has somewhere to grow."""
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

    Pure: takes a roster, returns a **new** one, never mutates the argument — so the
    caller can do the read-modify-write inside `store.transaction_shared()` and a refused
    lock leaves the in-memory value untouched rather than half-applied.

    `first_seen` is preserved across every later sighting, because *"since when has this
    appliance served this robot"* is a different question from *"when did it last speak"*
    and only one of them is recoverable if we overwrite it.
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
        # Least-recently-seen first, so the eviction takes the robot nobody has used.
        for stale in sorted(rows, key=lambda d: _last_seen(rows[d]))[: len(rows) - limit]:
            rows.pop(stale, None)
    return {"devices": rows}


def forget(roster, device_id: str) -> dict:
    """Return a roster without `device_id`.

    The roster is a record of *service*, and a parent who unpairs a robot has said the
    appliance no longer serves it — so un-permitting must be able to reach in here, or the
    appliance keeps publishing config at a robot the family gave away.
    """
    rows = dict(_rows(roster))
    rows.pop(str(device_id or "").strip(), None)
    return {"devices": rows}


def _last_seen(row) -> float:
    try:
        return float(row.get("last_seen") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def device_ids(roster) -> list:
    """Every rostered device, **most recently seen first** — which is the order a
    reconnect burst should go out in, because the robot that spoke most recently is the
    one most likely to still be listening."""
    rows = _rows(roster)
    return sorted(rows, key=lambda d: -_last_seen(rows[d]))


def resume_targets(roster, connected=(), *, permitted=None) -> list:
    """Which rostered devices a broker connect should re-push config to.

    Two subtractions, and both are load-bearing:

    * **`connected`** — a robot already in `self.robots` is being handled by the paths that
      always handled it (`_device_connect`'s settle timer pushes its config), so including
      it here would double every push on a reconnect for no gain.
    * **`permitted`** — the pairing gate lives on the transport boundary and refuses
      *events*; it does not refuse a push we initiate. Without this the roster would
      cheerfully push config at a robot a parent has un-paired, which is the one thing the
      permit list exists to stop. `None` means "no permit gate configured" (the open-fleet
      / SIL case) and lets everything through, exactly as the rest of the runtime does.
    """
    live = {str(d) for d in (connected or ())}
    out = [d for d in device_ids(roster) if d not in live]
    if permitted is not None:
        out = [d for d in out if permitted(d)]
    return out


def summarize(roster) -> dict:
    """`{known, oldest_first_seen, newest_last_seen}` for `/status` and the console.

    A count plus two timestamps rather than the ids themselves: `/status` is polled by the
    console every few seconds, and a fleet-sized id list on every poll is a payload cost
    paid for something no card renders. The ids are available on their own route.
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
