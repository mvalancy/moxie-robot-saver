"""
Telemetry tests (M5 remainder) — the Packet envelope (build/parse) + the LoggingPolicy
upload-gate. Field names verified against embodied/logging/Cloud.proto.
"""
import base64
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.telemetry import (  # noqa: E402
    PacketModel, build_packet, parse_packet, should_upload, summarize_events,
)
from moxie_sdk.cloud_config import LoggingPolicy  # noqa: E402


def test_packet_model_values_match_proto():
    assert (PacketModel.UNKNOWN, PacketModel.SessionLog, PacketModel.Device,
            PacketModel.Event, PacketModel.Raw) == (0, 1, 2, 3, 4)


def test_build_packet_shape_and_base64():
    p = build_packet("wake", b"\x01\x02", moxie_id="d_x", session_id="s1",
                     recorded_at=123)
    assert p["model"] == "Event" and p["event_name"] == "wake"
    assert p["moxie_id"] == "d_x" and p["moxie_session_id"] == "s1"
    assert p["recorded_at"] == 123 and p["version"] == 1
    assert base64.b64decode(p["event_data"]) == b"\x01\x02"


def test_logging_policy_upload_gate():
    # NO_DATA → nothing leaves
    assert should_upload(LoggingPolicy.NO_DATA) is False
    assert should_upload(LoggingPolicy.NO_DATA, is_media=True) is False
    # NO_MEDIA → everything but audio/video
    assert should_upload(LoggingPolicy.NO_MEDIA) is True
    assert should_upload(LoggingPolicy.NO_MEDIA, is_media=True) is False
    # FULL → everything
    assert should_upload(LoggingPolicy.FULL, is_media=True) is True


def test_parse_packet_round_trip():
    p = build_packet("said", "hello", moxie_id="d_y")
    got = parse_packet(json.dumps(p))
    assert got["event_name"] == "said" and got["moxie_id"] == "d_y"
    assert got["model"] == "Event"


def test_parse_packet_ignores_unknown_fields():
    got = parse_packet(json.dumps({"event_name": "e", "moxie_id": "d", "junk": 1}))
    assert "junk" not in got and got["event_name"] == "e"


# --- summarize_events (M6 insights view) ---

def _pkts():
    return [build_packet("wake", b"", moxie_id="d1", recorded_at=100),
            build_packet("said", "hi", moxie_id="d1", recorded_at=200),
            build_packet("wake", b"", moxie_id="d1", recorded_at=300)]


def test_summarize_events_counts_and_last_seen():
    s = summarize_events(_pkts())
    assert s["count"] == 3
    assert s["by_event"] == {"wake": 2, "said": 1}
    assert s["last_seen"] == {"wake": 300, "said": 200}


def test_summarize_events_latest_is_newest_first_and_capped():
    s = summarize_events(_pkts(), limit=2)
    assert [p["event_name"] for p in s["latest"]] == ["wake", "said"]   # newest first
    assert len(s["latest"]) == 2 and s["count"] == 3                    # count is total


def test_summarize_events_empty_and_none_are_safe():
    for empty in ([], None):
        s = summarize_events(empty)
        assert s == {"count": 0, "by_event": {}, "last_seen": {}, "latest": []}


def test_summarize_events_tolerates_partial_packets():
    """Packets may arrive without event_name/recorded_at; non-dicts are skipped."""
    s = summarize_events([{"moxie_id": "d1"},                      # no event_name
                          {"event_name": "said"},                  # no recorded_at
                          {"event_name": "said", "recorded_at": "77"},  # stringy ts
                          "not-a-packet", None])
    assert s["count"] == 3                                  # the two non-dicts dropped
    assert s["by_event"] == {"event": 1, "said": 2}         # unnamed → "event"
    assert s["last_seen"] == {"said": 77}                   # only stamped events appear


def test_summarize_events_limit_zero_returns_no_rows():
    s = summarize_events(_pkts(), limit=0)
    assert s["latest"] == [] and s["count"] == 3
