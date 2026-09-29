#!/usr/bin/env python3
"""The time/alarm builders in moxie_toolkit.bus (embodied.sys TimeEvents): UserAlarmRequest
(the on-device wake behind RobotCloudConfig's WakeSchedule), the ReservedTimers namespace,
and TimeZoneInfo. See docs/reverse-engineering/protocol/power-and-system-events.md
(Time, timezone & alarms).

    python3 tools/robot-toolkit/test_time_alarms.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.system import TimeEvents_pb2 as T  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  time/alarm toolkit test skipped — {e}")
    sys.exit(0)

R = T.UserAlarmRequest
a = bus.user_alarm(1_700_000_000)
ok(a.timer_id == R.TIMER_ID_USER_WAKE and a.alarm_expires == 1_700_000_000 and a.alarm_repeats == 0,
   "user_alarm default should be a one-shot USER_WAKE")
a2 = bus.user_alarm(1_700_003_600, timer_id=R.TIMER_ID_PARENT_APP, alarm_repeats=86400)
ok(a2.timer_id == R.TIMER_ID_PARENT_APP and a2.alarm_repeats == 86400, "parent-app recurring alarm wrong")
ok(R.TIMER_ID_CUSTOM == 100, "TIMER_ID_CUSTOM should be 100")

tz = bus.time_zone_info("America/New_York", midnight_in_timezone="2026-08-31T00:00:00-04:00")
ok(tz.olson_id == "America/New_York" and tz.midnight_in_timezone.startswith("2026-08-31"),
   "time_zone_info fields wrong")
T.UserAlarmTriggered(timer_id=R.TIMER_ID_USER_WAKE)
ok((bus.full_name(a), bus.full_name(tz)) == ("embodied.sys.UserAlarmRequest", "embodied.sys.TimeZoneInfo"),
   f"unexpected full names {bus.full_name(a)} / {bus.full_name(tz)}")

report("time/alarm", "user_alarm (USER_WAKE/PARENT_APP/CUSTOM, repeats) + time_zone_info + full names")
