"""
Config & telemetry tests (M5) — RobotCloudConfig builder, RobotStatus parser, and the
LoggingPolicy gate. Field names verified against embodied/logging/Cloud.proto.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.cloud_config import (  # noqa: E402
    LoggingPolicy, MoxieMode, build_robot_cloud_config, parse_robot_status,
    child_pii_from_profile,
)
from moxie_sdk.types import ChildProfile  # noqa: E402


def test_logging_policy_values_match_proto():
    assert (LoggingPolicy.NO_DATA, LoggingPolicy.NO_MEDIA, LoggingPolicy.FULL) == (0, 1, 2)
    assert (MoxieMode.DEFAULT_MODE, MoxieMode.TELEHEALTH) == (0, 1)


def test_config_has_core_fields_and_pairing_wrapper():
    cfg = build_robot_cloud_config(ChildProfile(nickname="Sam"))
    # the wrapper the robot needs + spec fields (exact proto names)
    assert cfg["pairing_status"] == "paired"
    assert cfg["child_pii"]["nickname"] == "Sam"
    for k in ("audio_volume", "screen_brightness", "timezone_id", "data_sharing",
              "moxie_mode", "privacy_mode_enabled", "wake_button_enabled",
              "touch_wake_enabled", "num_children", "max_children"):
        assert k in cfg, f"missing {k}"
    assert cfg["settings"]["props"]["stt"] == "4"      # stream audio to our STT


def test_data_sharing_reflects_logging_policy():
    assert build_robot_cloud_config(ChildProfile(), logging_policy=LoggingPolicy.NO_DATA)["data_sharing"] == "NO_DATA"
    assert build_robot_cloud_config(ChildProfile(), logging_policy=LoggingPolicy.FULL)["data_sharing"] == "FULL"


def test_bedtime_windows():
    cfg = build_robot_cloud_config(ChildProfile(), weekday_bedtime=("20:00", "07:00"))
    assert cfg["weekday_bedtime_enabled"] is True
    assert cfg["weekday_bedtime_starts_at"] == "20:00"
    assert cfg["weekday_bedtime_ends_at"] == "07:00"
    assert cfg["weekend_bedtime_enabled"] is False     # unset → disabled


def test_child_pii_from_profile_includes_birthday():
    pii = child_pii_from_profile(ChildProfile(nickname="Robin", birthday_iso="2018-05-01"))
    assert pii == {"nickname": "Robin", "birthday": "2018-05-01"}


def test_config_is_json_serializable():
    json.dumps(build_robot_cloud_config(ChildProfile(nickname="Sam")))  # must not raise


def test_parse_robot_status_extracts_known_fields():
    state = json.dumps({
        "embodied_robot_id": "d_x", "robot_firmware_version": "v24.10.803",
        "android_version": "9", "battery_level": 0.82, "audio_volume": 0.6,
        "wifi_ssid": "home", "mode": "idle", "ota_reboot_required": False,
        "mac": "aa:bb", "unknown_field": "ignored",
    })
    s = parse_robot_status(state)
    assert s["embodied_robot_id"] == "d_x"
    assert s["robot_firmware_version"] == "v24.10.803"
    assert s["battery_level"] == 0.82 and s["wifi_ssid"] == "home"
    assert "unknown_field" not in s                    # only known fields surfaced


def test_status_firmware_falls_back_to_software_version():
    s = parse_robot_status({"software_version": "v24.10.803"})
    assert s["robot_firmware_version"] == "v24.10.803"
