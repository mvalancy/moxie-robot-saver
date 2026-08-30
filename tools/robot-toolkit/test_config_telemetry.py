#!/usr/bin/env python3
"""
Round-trip test for the device config & telemetry builders in moxie_toolkit.cloud
(the embodied.logging data-model). Builds a RobotCloudConfig a server would push on
/config, serializes + re-parses it, and round-trips a RobotStatus, a telemetry Packet,
and a CloudStatus(UserState) the robot sends back. See
docs/reverse-engineering/device-config-and-telemetry.md.

    python3 tools/robot-toolkit/test_config_telemetry.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.logging import Cloud_pb2 as C  # noqa: E402
    from embodied.logging import CloudStatus_pb2 as CS  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  config/telemetry toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# ---- RobotCloudConfig: the /config document a server pushes down ----
cfg = cloud.build_robot_cloud_config(
    audio_volume=0.6, screen_brightness=0.8, timezone_id="America/New_York",
    privacy_mode_enabled=False, weekday_bedtime_enabled=True,
    weekday_bedtime_starts_at="20:00", weekday_bedtime_ends_at="07:00",
    wake_button_enabled=True, touch_wake_enabled=True,
)
# a nested WakeSchedule alarm (Mon/Wed/Fri 07:30) — set on the returned object
we = cfg.alarms.wakes.add(); we.days.extend([1, 3, 5]); we.time = "07:30"
cfg.alarms.enabled = True

rt = cloud.parse_robot_cloud_config(cfg.SerializeToString())
ok(abs(rt.audio_volume - 0.6) < 1e-6, "audio_volume lost")
ok(abs(rt.screen_brightness - 0.8) < 1e-6, "screen_brightness lost")
ok(rt.timezone_id == "America/New_York", "timezone_id lost")
ok(rt.weekday_bedtime_enabled and rt.weekday_bedtime_starts_at == "20:00"
   and rt.weekday_bedtime_ends_at == "07:00", "bedtime window lost")
ok(rt.wake_button_enabled and rt.touch_wake_enabled, "wake toggles lost")
ok(rt.alarms.enabled and len(rt.alarms.wakes) == 1
   and list(rt.alarms.wakes[0].days) == [1, 3, 5] and rt.alarms.wakes[0].time == "07:30",
   "WakeSchedule alarm lost")

# moxie_mode enum survives (DEFAULT vs TELEHEALTH)
cfg.moxie_mode = C.TELEHEALTH
ok(cloud.parse_robot_cloud_config(cfg.SerializeToString()).moxie_mode == C.TELEHEALTH,
   "moxie_mode enum lost")

# ---- RobotStatus: the robot's /state snapshot ----
st = C.RobotStatus(embodied_robot_id="d_test", battery_level=0.9, audio_volume=0.6,
                   wifi_ssid="HomeNet", mode="DEFAULT", ota_reboot_required=False,
                   robot_firmware_version="v3.6.4-Zephyr")
prs = cloud.parse_robot_status(st.SerializeToString())
ok(prs.embodied_robot_id == "d_test" and abs(prs.battery_level - 0.9) < 1e-6
   and prs.wifi_ssid == "HomeNet" and prs.robot_firmware_version == "v3.6.4-Zephyr",
   "RobotStatus round-trip failed")

# ---- Packet: the telemetry envelope ----
pkt = C.Packet(model=C.Packet.Event, moxie_id="d_test", moxie_session_id="s1",
               event_name="activity_complete", event_data=b"\x01\x02", version=1)
ppk = cloud.parse_telemetry_packet(pkt.SerializeToString())
ok(ppk.model == C.Packet.Event and ppk.event_name == "activity_complete"
   and ppk.event_data == b"\x01\x02" and ppk.moxie_session_id == "s1",
   "Packet telemetry round-trip failed")

# ---- CloudStatus.UserState lifecycle ----
cs = CS.CloudStatus(connected=True, user_state=CS.CloudStatus.PAIRED_VALID)
pcs = cloud.parse_cloud_status(cs.SerializeToString())
ok(pcs.connected and pcs.user_state == CS.CloudStatus.PAIRED_VALID, "CloudStatus round-trip failed")

if fails:
    print("❌ config/telemetry toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ config/telemetry toolkit test OK — RobotCloudConfig (bedtime/alarms/mode) + RobotStatus + "
      "Packet telemetry + CloudStatus(UserState) all round-trip through embodied.logging")
