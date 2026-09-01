"""
Config & telemetry (config-and-telemetry-contract.md) — the robot's remotely-managed
state. Build the `/config` (RobotCloudConfig) the server pushes down, parse the
`/state` (RobotStatus) the robot reports up, and the LoggingPolicy privacy gate.

Field names verbatim from embodied/logging/Cloud.proto + enums.proto. A self-hosted
server IS the pairing key-holder, so it populates `child_pii` (ChildDecrypted) directly
and leaves the encrypted `child` empty.
"""
from __future__ import annotations
from enum import IntEnum
from typing import Optional


class LoggingPolicy(IntEnum):
    """What may leave the device — the child-privacy gate (enums.proto)."""
    NO_DATA = 0
    NO_MEDIA = 1        # everything but audio/video
    FULL = 2


class MoxieMode(IntEnum):
    DEFAULT_MODE = 0
    TELEHEALTH = 1


def child_pii_from_profile(child) -> dict:
    """A ChildDecrypted (`child_pii`) from a ChildProfile — plaintext, as the paired
    server's own config (the encryption blinds a 3rd-party cloud, not our backend)."""
    pii = {"nickname": child.nickname}
    if getattr(child, "birthday_iso", None):
        pii["birthday"] = child.birthday_iso
    return pii


def build_robot_cloud_config(child, *, audio_volume: float = 0.6,
                             screen_brightness: float = 1.0,
                             timezone_id: str = "America/Los_Angeles",
                             logging_policy: LoggingPolicy = LoggingPolicy.NO_DATA,
                             moxie_mode: MoxieMode = MoxieMode.DEFAULT_MODE,
                             privacy_mode_enabled: bool = False,
                             weekday_bedtime: Optional[tuple] = None,
                             weekend_bedtime: Optional[tuple] = None,
                             wake_button_enabled: bool = True,
                             touch_wake_enabled: bool = True,
                             audio_wake_set: str = "off",
                             num_children: int = 1, max_children: int = 1,
                             last_updated_at: str = "", timestamp: int = 0) -> dict:
    """The RobotCloudConfig document (JSON) pushed on /devices/{id}/config.

    `weekday_bedtime`/`weekend_bedtime` are optional ("HH:MM","HH:MM") start/end tuples.
    `pairing_status:"paired"` + `settings` are the wrapper the robot's config handler
    expects (kept from the working minimal config)."""
    cfg = {
        "pairing_status": "paired",
        "child_pii": child_pii_from_profile(child),
        "audio_volume": audio_volume,
        "screen_brightness": screen_brightness,
        "timezone_id": timezone_id,
        "data_sharing": LoggingPolicy(logging_policy).name,
        "moxie_mode": MoxieMode(moxie_mode).name,
        "privacy_mode_enabled": privacy_mode_enabled,
        "wake_button_enabled": wake_button_enabled,
        "touch_wake_enabled": touch_wake_enabled,
        "audio_wake_set": audio_wake_set,
        "num_children": num_children,
        "max_children": max_children,
        "settings": {"props": {
            "touch_wake": "1" if touch_wake_enabled else "0",
            "wake_alarms": "1", "wake_button": "1" if wake_button_enabled else "0",
            "doa_range": "80", "target_all": "1", "gcp_upload_disable": "1",
            "local_stt": "on", "max_enroll": "2", "audio_wake": "1",
            "cloud_schedule_reset_threshold": "5", "brain_entrances_available": "1",
            "default_loglevel": "warning", "stt": "4",
        }},
    }
    for tag, bt in (("weekday", weekday_bedtime), ("weekend", weekend_bedtime)):
        if bt:
            cfg[f"{tag}_bedtime_enabled"] = True
            cfg[f"{tag}_bedtime_starts_at"], cfg[f"{tag}_bedtime_ends_at"] = bt[0], bt[1]
        else:
            cfg[f"{tag}_bedtime_enabled"] = False
    if last_updated_at:
        cfg["last_updated_at"] = last_updated_at
    if timestamp:
        cfg["timestamp"] = timestamp
    return cfg


# RobotStatus (/state) fields we surface (embodied/logging/Cloud.proto message RobotStatus)
_STATUS_FIELDS = ("embodied_robot_id", "robot_firmware_version", "android_version",
                  "battery_level", "audio_volume", "screen_brightness", "mode",
                  "wifi_ssid", "last_back_up_at", "ota_reboot_required", "mac",
                  "timestamp", "last_updated_at", "software_version")


def parse_robot_status(payload) -> dict:
    """Parse a RobotStatus JSON (from /devices/{id}/state) into the known fields.
    Tolerant: `software_version` also serves as the firmware fallback."""
    import json
    data = payload if isinstance(payload, dict) else json.loads(payload)
    out = {k: data[k] for k in _STATUS_FIELDS if k in data}
    if "robot_firmware_version" not in out and data.get("software_version"):
        out["robot_firmware_version"] = data["software_version"]
    return out
