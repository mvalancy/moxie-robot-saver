"""
Telemetry (config-and-telemetry-contract.md) — the analytics/event envelope robots
upload and the parent console reads, plus the LoggingPolicy upload-gate.

Field names verbatim from embodied/logging/Cloud.proto (message Packet). A client/SIM
BUILDS packets (respecting the policy the server set in RobotCloudConfig.data_sharing);
the server INGESTS them for insights.
"""
from __future__ import annotations
import base64
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
