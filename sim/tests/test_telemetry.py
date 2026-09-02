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
    PacketModel, build_packet, parse_packet, should_upload,
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
