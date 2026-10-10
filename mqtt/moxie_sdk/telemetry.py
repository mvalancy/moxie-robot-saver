"""
Telemetry (config-and-telemetry-contract.md) — the analytics/event envelope robots
upload and the parent console reads, plus the LoggingPolicy upload-gate.

Field names verbatim from embodied/logging/Cloud.proto (message Packet). A client/SIM
BUILDS packets (respecting the policy the server set in RobotCloudConfig.data_sharing);
the server INGESTS them for insights.

Two halves:
  * the **envelope** (`build_packet`/`parse_packet`), the upload gate (`should_upload`)
    and the live roll-up (`summarize_events`) — the wire and the "what just happened";
  * **durable, bounded storage** (bottom of the file) — the shapes, caps, privacy filter,
    day arithmetic and the **log/view reconcile** behind a history that survives a
    supervisor restart. Pure: the runtime does the disk I/O through
    `moxie_sdk.store.JsonStore`.
"""
from __future__ import annotations
import base64
import os
import time
from enum import IntEnum
from typing import Optional

from .cloud_config import LoggingPolicy   # NO_DATA / NO_MEDIA / FULL


class PacketModel(IntEnum):
    UNKNOWN = 0
    SessionLog = 1
    Device = 2
    Event = 3
    Raw = 4


def should_upload(policy, *, is_media: bool = False) -> bool:
    """The child-privacy gate: what may leave the device.
    NO_DATA → nothing; NO_MEDIA → everything but audio/video; FULL → everything."""
    p = LoggingPolicy(int(policy))
    if p == LoggingPolicy.NO_DATA:
        return False
    if p == LoggingPolicy.NO_MEDIA:
        return not is_media
    return True


def build_packet(event_name: str, event_data=b"", *, moxie_id: str,
                 model: PacketModel = PacketModel.Event, session_id: str = "",
                 user_id: str = "", version: int = 1,
                 recorded_at: Optional[int] = None) -> dict:
    """A telemetry Packet (JSON). `event_data` bytes are base64-encoded for the wire."""
    if isinstance(event_data, (bytes, bytearray)):
        event_data = base64.b64encode(bytes(event_data)).decode()
    return {
        "model": PacketModel(model).name,
        "version": version,
        "recorded_at": recorded_at if recorded_at is not None else int(time.time()),
        "moxie_id": moxie_id,
        "moxie_session_id": session_id,
        "user_id": user_id,
        "event_name": event_name,
        "event_data": event_data,
    }


_PACKET_FIELDS = ("model", "version", "recorded_at", "moxie_id",
                  "moxie_session_id", "user_id", "event_name", "event_data")


def parse_packet(payload) -> dict:
    """Parse an incoming Packet JSON into its known fields (server-side ingest)."""
    import json
    data = payload if isinstance(payload, dict) else json.loads(payload)
    return {k: data[k] for k in _PACKET_FIELDS if k in data}


def _recorded_at(value):
    """Coerce a Packet's `recorded_at` to a number; None when absent/unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def summarize_events(packets, limit: int = 20) -> dict:
    """Roll a robot's stored Packets up into the parent console's insights view.

    Pure + tolerant of partial packets (a Packet may be missing `event_name` or
    `recorded_at`; anything that isn't a dict is skipped). Returns:
      count     — how many packets were summarized
      by_event  — {event_name: how many}
      last_seen — {event_name: newest recorded_at seen} (absent when never stamped)
      latest    — the newest `limit` packets, newest-first

    "Newest" is arrival order (the runtime appends as packets land), not a sort on
    `recorded_at` — device clocks lie and the field is optional.
    """
    items = [p for p in (packets or []) if isinstance(p, dict)]
    by_event: dict[str, int] = {}
    last_seen: dict[str, float] = {}
    for p in items:
        name = str(p.get("event_name") or "event")
        by_event[name] = by_event.get(name, 0) + 1
        ts = _recorded_at(p.get("recorded_at"))
        if ts is not None and (name not in last_seen or ts > last_seen[name]):
            last_seen[name] = ts
    n = max(0, int(limit))
    return {"count": len(items), "by_event": by_event, "last_seen": last_seen,
            "latest": list(reversed(items))[:n]}


# ---------------------------------------------------------------------------
# Durable, bounded telemetry — the history behind the parent console's 📈 card
# ---------------------------------------------------------------------------
# Two records, because a parent asks two questions (openmoxie-feature-audit.md §4.4 #2):
#
#   * `telemetry_packets.json` — a rolling ring of the newest envelopes ("what just
#     happened");
#   * `telemetry_daily.json`   — one small row per calendar day ("what has been
#     happening"), which a ring could only answer by keeping every packet forever.
#
# Pure: shapes, caps, the policy filter and day arithmetic. The runtime does the disk I/O
# via `JsonStore`. Field names and the privacy gate: `embodied/logging/Cloud.proto`
# (`Packet`) and `config-and-telemetry-contract.md` §③.
#
# **The two records are a log and a view over it, not two independent counters.** Two
# separate writes leave a window where the ring holds a packet the roll-up never counted,
# and a supervisor killed in it made the gap permanent. So:
#
#   * every stored envelope carries a monotonic **`seq`** (`SEQ_FIELD`), stamped by the
#     server after the privacy gate (a robot cannot forge one);
#   * the roll-up carries **`through_seq`**, the highest `seq` it has folded;
#   * `reconcile_rollup` replays whatever the roll-up is missing from the ring.
#
# Rejected: one combined file (loses the independent caps and three-record erase);
# ordering alone (a refused write still leaves the ring ahead — the runtime does write the
# roll-up first as a half-measure); recomputing from the ring on read (the ring is capped,
# so the lifetime `total` would reset).

#: Collections under the robot's data dir (`robots/<device>/<collection>.json`).
PACKETS_COLLECTION = "telemetry_packets"
DAILY_COLLECTION = "telemetry_daily"

#: The monotonic per-robot sequence number a **stored** envelope carries. Not in
#: `_PACKET_FIELDS` (server bookkeeping): a robot's own `seq` is dropped by the gate.
SEQ_FIELD = "seq"

#: `LoggingPolicy` values, by value (nothing below needs the enum).
POLICY_NO_DATA = 0
POLICY_NO_MEDIA = 1
POLICY_FULL = 2

# --- the caps ---------------------------------------------------------------------
# `JsonStore` rewrites the whole file on every append, so each cap is also a write cost.
#
#: Raw envelopes kept per robot (~60 KB; sized against write cost, not a measured robot
#: rate). A ring, not an archive — the daily roll-up answers "last week".
MAX_PACKETS = 500
#: Daily roll-up rows kept per robot (~a month plus a week; ~7 KB).
MAX_ROLLUP_DAYS = 35
#: Distinct `event_name`s per day row (a free string on the wire); overflow is counted
#: under `OTHER_EVENT`, not dropped.
MAX_DAY_EVENTS = 24
#: Where a day's overflowing event names are counted.
OTHER_EVENT = "(other)"
#: Base64 characters of `event_data` kept **under FULL only**; longer is truncated + marked.
MAX_EVENT_DATA_CHARS = 2048

#: Before this, a `recorded_at` is not a real Moxie timestamp (2020-01-01 UTC).
_EPOCH_FLOOR = 1577836800


def _int_env(name: str, default: int) -> int:
    """A non-negative int from the environment, or `default` when unset/unparseable."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return default


def max_packets() -> int:
    """Raw envelopes kept per robot (`MOXIE_TELEMETRY_MAX_PACKETS`)."""
    return _int_env("MOXIE_TELEMETRY_MAX_PACKETS", MAX_PACKETS)


def max_rollup_days() -> int:
    """Daily roll-up rows kept per robot (`MOXIE_TELEMETRY_MAX_DAYS`)."""
    return _int_env("MOXIE_TELEMETRY_MAX_DAYS", MAX_ROLLUP_DAYS)


def retention() -> dict:
    """The live caps, so the console can state the retention window it shows."""
    return {"packets": max_packets(), "days": max_rollup_days()}


def policy_value(policy) -> Optional[int]:
    """A `LoggingPolicy` (enum / int / name string) as its int value; None if unknown
    (callers read None as "no explicit parent choice")."""
    if policy is None or isinstance(policy, bool):
        return None
    if isinstance(policy, int):
        return int(policy)
    return {"NO_DATA": POLICY_NO_DATA, "NO_MEDIA": POLICY_NO_MEDIA,
            "POLICY_FULL": POLICY_FULL, "FULL": POLICY_FULL}.get(
                str(policy).strip().upper())


def storable_packet(pkt, policy) -> Optional[dict]:
    """One parsed Packet reduced to what this robot's `LoggingPolicy` allows **on disk**.
    The privacy gate (contract §③ "MUST honor NO_DATA/NO_MEDIA"), failing closed:

    * **`NO_DATA` → `None`** — nothing written: no packet, count or day row.
    * **`NO_MEDIA` → envelope without `event_data`**, marked `event_data_withheld`. The
      payload is untyped `bytes`, so no blob can be proven not to be audio/video; every
      payload is withheld.
    * **`FULL` → the whole envelope**, `event_data` truncated at `MAX_EVENT_DATA_CHARS`.

    Unknown/absent policy is treated as `NO_MEDIA`, like the safety journal and memory.
    Returns a NEW dict.
    """
    if not isinstance(pkt, dict):
        return None
    value = policy_value(policy)
    if value == POLICY_NO_DATA:
        return None
    out = {k: v for k, v in pkt.items() if k in _PACKET_FIELDS}
    data = out.get("event_data")
    if value == POLICY_FULL:
        if isinstance(data, str) and len(data) > MAX_EVENT_DATA_CHARS:
            out["event_data"] = data[:MAX_EVENT_DATA_CHARS]
            out["event_data_truncated"] = True
        return out
    out.pop("event_data", None)
    out["event_data_withheld"] = "NO_MEDIA"
    return out


def packet_day(pkt, *, now=None, tz=None) -> str:
    """The calendar day a Packet belongs to, as `YYYY-MM-DD`: on the house's clock when `tz`
    (a tzinfo: the runtime passes the house's zone, `house_zone`), else this process's local
    time. A missing, pre-2020 or >1 day-future `recorded_at` (device clocks lie) falls back
    to arrival time."""
    now = time.time() if now is None else float(now)
    ts = _recorded_at((pkt or {}).get("recorded_at") if isinstance(pkt, dict) else None)
    if ts is None or ts < _EPOCH_FLOOR or ts > now + 86400:
        ts = now
    if tz is not None:
        import datetime
        return datetime.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def new_rollup() -> dict:
    """An empty daily roll-up record. `through_seq` is the watermark: the highest `seq`
    folded in (0 = nothing, also what a pre-watermark record reads as)."""
    return {"days": {}, "total": 0, "dropped_days": 0, "updated_at": None,
            "through_seq": 0}


def _count(value) -> int:
    """A non-negative int from anything a hand-edited JSON file might hold, else 0."""
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _clean_rollup(rollup) -> dict:
    """A roll-up record from the store, defensively normalised (a corrupt or
    hand-edited file must never take a robot's session down)."""
    r = rollup if isinstance(rollup, dict) else {}
    days = r.get("days")
    out = new_rollup()
    if isinstance(days, dict):
        for day, row in days.items():
            if not isinstance(day, str) or not isinstance(row, dict):
                continue
            by = row.get("by_event") if isinstance(row.get("by_event"), dict) else {}
            out["days"][day] = {
                "count": _count(row.get("count")),
                "by_event": {str(k): _count(v) for k, v in by.items()
                             if _count(v) or v == 0},
                "first": _recorded_at(row.get("first")),
                "last": _recorded_at(row.get("last")),
            }
    out["total"] = _count(r.get("total"))
    out["dropped_days"] = _count(r.get("dropped_days"))
    out["updated_at"] = _recorded_at(r.get("updated_at"))
    out["through_seq"] = _count(r.get("through_seq"))
    return out


def roll_up_packet(rollup, pkt, *, now=None, max_days: Optional[int] = None,
                   tz=None) -> dict:
    """Fold one Packet into the daily roll-up and return the NEW record.

    Shape::

        {"days": {"2026-09-02": {"count": 7, "by_event": {"wake": 2}, "first": …, "last": …}},
         "total": 128,          # lifetime — never decremented when a day is pruned
         "dropped_days": 3,     # how many day rows the cap has retired
         "updated_at": 1756…}

    `total` is a **lifetime** count (stays true as the window slides). The newest
    `max_days` rows survive; a day keeps `MAX_DAY_EVENTS` names, the rest under `OTHER_EVENT`.
    The day is `packet_day`'s, on `tz`'s clock (the house's).
    """
    now = time.time() if now is None else float(now)
    cap = max_rollup_days() if max_days is None else max(0, int(max_days))
    out = _clean_rollup(rollup)
    if not isinstance(pkt, dict):
        return out
    day = packet_day(pkt, now=now, tz=tz)
    row = out["days"].get(day) or {"count": 0, "by_event": {}, "first": None, "last": None}
    name = str(pkt.get("event_name") or "event")
    by = dict(row["by_event"])
    if name not in by and len(by) >= MAX_DAY_EVENTS:
        name = OTHER_EVENT
    by[name] = by.get(name, 0) + 1
    ts = _recorded_at(pkt.get("recorded_at"))
    if ts is None or ts < _EPOCH_FLOOR or ts > now + 86400:
        ts = now
    row = {"count": row["count"] + 1, "by_event": by,
           "first": ts if row["first"] is None else min(row["first"], ts),
           "last": ts if row["last"] is None else max(row["last"], ts)}
    out["days"][day] = row
    out["total"] += 1
    seq = packet_seq(pkt)
    if seq is not None and seq > out["through_seq"]:
        out["through_seq"] = seq
    if cap and len(out["days"]) > cap:
        for old in sorted(out["days"])[: len(out["days"]) - cap]:
            del out["days"][old]
            out["dropped_days"] += 1
    out["updated_at"] = now
    return out


# --- the log/view relationship: `seq`, the watermark, and the repair -----------------

def packet_seq(pkt) -> Optional[int]:
    """A stored envelope's `seq` as a positive int, else None ("no watermark info")."""
    if not isinstance(pkt, dict):
        return None
    value = pkt.get(SEQ_FIELD)
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def with_seq(row, seq: int) -> dict:
    """A stored envelope stamped with its sequence number. Returns a NEW dict."""
    out = dict(row or {})
    out[SEQ_FIELD] = max(1, int(seq))
    return out


def next_seq(ring, rollup=None) -> int:
    """The next sequence number: one past the highest either record knows. The roll-up is
    consulted too because a lost ring append leaves `through_seq` ahead; reusing it would
    duplicate an identity. Monotonic beats gapless."""
    highest = 0
    if isinstance(ring, list):
        for row in ring:
            n = packet_seq(row)
            if n is not None and n > highest:
                highest = n
    if rollup is not None:
        highest = max(highest, _clean_rollup(rollup)["through_seq"])
    return highest + 1


def unfolded_packets(rollup, ring) -> list:
    """The stored envelopes the roll-up has **not** counted yet, oldest first.

    **An envelope with no `seq` counts as folded** (migration rule): legacy rings were
    already counted, so treating them as unfolded would double every install's total."""
    out = _clean_rollup(rollup)
    rows = [r for r in ring if isinstance(r, dict)] if isinstance(ring, list) else []
    missing = [(n, r) for r in rows
               for n in (packet_seq(r),) if n is not None and n > out["through_seq"]]
    missing.sort(key=lambda pair: pair[0])
    return [r for _, r in missing]


def reconcile_rollup(rollup, ring, *, now=None, max_days=None, tz=None) -> dict:
    """The roll-up with everything the ring holds and it does not, folded back in — so a
    lost roll-up write is recoverable. Always returns a normalised record (usable as the
    read path). Never recomputes from scratch: the ring is capped, `total` is lifetime."""
    out = _clean_rollup(rollup)
    for row in unfolded_packets(out, ring):
        out = roll_up_packet(out, row, now=now, max_days=max_days, tz=tz)
    return out


def _day_before(day: str, back: int) -> str:
    """`day` minus `back` days, both `YYYY-MM-DD`."""
    import datetime
    d = datetime.date.fromisoformat(day) - datetime.timedelta(days=back)
    return d.isoformat()


def history_view(rollup, *, days: int = 7, today: Optional[str] = None) -> list:
    """The last `days` calendar days, oldest→newest, **zero-filled** (a quiet day is an
    answer). Rows are `{day, count, by_event, top_event}`; ties break by name. `today` is
    the last day (the runtime passes the house's, from `house_now`); default: this
    process's local date."""
    r = _clean_rollup(rollup)
    n = max(0, int(days))
    if not n:
        return []
    end = today or time.strftime("%Y-%m-%d", time.localtime())
    try:
        span = [_day_before(end, i) for i in range(n - 1, -1, -1)]
    except (TypeError, ValueError):
        return []
    rows = []
    for day in span:
        row = r["days"].get(day)
        by = dict(row["by_event"]) if row else {}
        top = min(by.items(), key=lambda kv: (-kv[1], kv[0]))[0] if by else None
        rows.append({"day": day, "count": int(row["count"]) if row else 0,
                     "by_event": by, "top_event": top})
    return rows


def rollup_totals(rollup) -> dict:
    """Lifetime total, the window actually held, and day rows the cap retired."""
    r = _clean_rollup(rollup)
    keys = sorted(r["days"])
    return {"total": r["total"], "days_kept": len(keys),
            "first_day": keys[0] if keys else None,
            "last_day": keys[-1] if keys else None,
            "dropped_days": r["dropped_days"], "updated_at": r["updated_at"]}
