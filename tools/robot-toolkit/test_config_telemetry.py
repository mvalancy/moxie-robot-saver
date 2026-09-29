#!/usr/bin/env python3
"""The device config & telemetry builders/parsers in moxie_toolkit.cloud (embodied.logging):
the RobotCloudConfig a server pushes on /config, and the RobotStatus, telemetry Packet and
CloudStatus(UserState) the robot sends back. See
docs/reverse-engineering/protocol/device-config-and-telemetry.md.

    python3 tools/robot-toolkit/test_config_telemetry.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.logging import Cloud_pb2 as C  # noqa: E402
    from embodied.logging import CloudStatus_pb2 as CS  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  config/telemetry toolkit test skipped — {e}")
    sys.exit(0)

cfg = cloud.build_robot_cloud_config(
    audio_volume=0.6, screen_brightness=0.8, timezone_id="America/New_York",
    privacy_mode_enabled=None,   # None is skipped, not set (setattr would raise)
    weekday_bedtime_enabled=True,
    weekday_bedtime_starts_at="20:00", weekday_bedtime_ends_at="07:00",
    wake_button_enabled=True, touch_wake_enabled=True, moxie_mode=C.TELEHEALTH,
)
ok(abs(cfg.audio_volume - 0.6) < 1e-6 and cfg.timezone_id == "America/New_York"
   and cfg.weekday_bedtime_starts_at == "20:00" and cfg.moxie_mode == C.TELEHEALTH,
   "build_robot_cloud_config dropped a field")
cfg.alarms.wakes.add(days=[1, 3, 5], time="07:30")
cfg.alarms.enabled = True
ok(cloud.parse_robot_cloud_config(cfg.SerializeToString()).alarms.wakes[0].time == "07:30",
   "parse_robot_cloud_config wrong")

st = C.RobotStatus(embodied_robot_id="d_test", battery_level=0.9, audio_volume=0.6,
                   wifi_ssid="HomeNet", mode="DEFAULT", ota_reboot_required=False,
                   robot_firmware_version="v3.6.4-Zephyr")
ok(cloud.parse_robot_status(st.SerializeToString()).wifi_ssid == "HomeNet", "parse_robot_status wrong")
pkt = C.Packet(model=C.Packet.Event, moxie_id="d_test", moxie_session_id="s1",
               event_name="activity_complete", event_data=b"\x01\x02", version=1)
ok(cloud.parse_telemetry_packet(pkt.SerializeToString()).event_name == "activity_complete",
   "parse_telemetry_packet wrong")
cs = CS.CloudStatus(connected=True, user_state=CS.CloudStatus.PAIRED_VALID)
ok(cloud.parse_cloud_status(cs.SerializeToString()).user_state == CS.CloudStatus.PAIRED_VALID,
   "parse_cloud_status wrong")

report("config/telemetry", "build_robot_cloud_config (bedtime/alarms/mode) + RobotStatus/Packet/"
       "CloudStatus fields and parsers")
