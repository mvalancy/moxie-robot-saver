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
    child_pii_from_profile, sanitize_config_overrides,
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


# --- sanitize_config_overrides (M6 parent-console config edit) ---
import json as _json
import pytest


def test_sanitize_whitelists_and_coerces():
    out = sanitize_config_overrides({
        "audio_volume": 80,                 # 0–100 slider → 0–1
        "wake_button_enabled": 0,           # truthy-coerce → bool
        "touch_wake_enabled": True,
        "weekday_bedtime": ["20:00", "07:00"],
        "logging_policy": "FULL",           # name → int value
        "bogus_key": "dropped",             # unknown → dropped
    })
    assert out["audio_volume"] == 0.8
    assert out["wake_button_enabled"] is False and out["touch_wake_enabled"] is True
    assert out["weekday_bedtime"] == ["20:00", "07:00"]
    assert out["logging_policy"] == int(LoggingPolicy.FULL)
    assert "bogus_key" not in out


def test_sanitize_output_is_json_safe_and_feeds_builder():
    """config_overrides are echoed in the status snapshot (JSON) and passed to the
    builder — so sanitized values must be JSON-serializable AND valid builder kwargs."""
    out = sanitize_config_overrides({"audio_volume": 0.5, "logging_policy": 2,
                                     "weekday_bedtime": ["21:00", "06:30"]})
    _json.dumps(out)                        # must not raise (no enums)
    cfg = build_robot_cloud_config(ChildProfile(nickname="Sam"), **out)
    assert cfg["audio_volume"] == 0.5 and cfg["data_sharing"] == "FULL"
    assert cfg["weekday_bedtime_enabled"] and cfg["weekday_bedtime_starts_at"] == "21:00"


def test_sanitize_clears_bedtime_with_null():
    out = sanitize_config_overrides({"weekday_bedtime": None})
    assert out["weekday_bedtime"] is None
    cfg = build_robot_cloud_config(ChildProfile(nickname="Sam"), **out)
    assert cfg["weekday_bedtime_enabled"] is False


def test_sanitize_rejects_bad_values():
    with pytest.raises(ValueError):
        sanitize_config_overrides({"weekday_bedtime": ["25:00", "07:00"]})
    with pytest.raises(ValueError):
        sanitize_config_overrides({"audio_wake_set": "maybe"})
    with pytest.raises(ValueError):
        sanitize_config_overrides([1, 2, 3])          # not an object
