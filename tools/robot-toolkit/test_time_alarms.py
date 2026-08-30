#!/usr/bin/env python3
"""Round-trip test for the time/alarm helpers in moxie_toolkit.bus (embodied.sys TimeEvents).

Builds a UserAlarmRequest (the on-device wake that implements RobotCloudConfig's
WakeSchedule) and a TimeZoneInfo (the Olson timezone that turns bedtime wall-clock
strings into local instants), frames + re-parses them, and checks the ReservedTimers
namespacing + a triggered event. See
docs/reverse-engineering/power-and-system-events.md (Time, timezone & alarms).

    python3 tools/robot-toolkit/test_time_alarms.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.system import TimeEvents_pb2 as T  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  time/alarm toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

def roundtrip(msg):
    cls = type(msg)
    out = cls(); out.ParseFromString(msg.SerializeToString()); return out

# default alarm = the child's wake timer, one-shot
a = bus.user_alarm(1_700_000_000)
ra = roundtrip(a)
ok(ra.timer_id == T.UserAlarmRequest.TIMER_ID_USER_WAKE, "default timer_id should be USER_WAKE")
ok(ra.alarm_expires == 1_700_000_000 and ra.alarm_repeats == 0, "alarm fields lost")

# a recurring parent-app timer
a2 = bus.user_alarm(1_700_003_600, timer_id=T.UserAlarmRequest.TIMER_ID_PARENT_APP, alarm_repeats=86400)
ra2 = roundtrip(a2)
ok(ra2.timer_id == T.UserAlarmRequest.TIMER_ID_PARENT_APP and ra2.alarm_repeats == 86400,
   "parent-app recurring alarm lost")

# reserved-timer namespacing exists (custom base = 100)
ok(T.UserAlarmRequest.TIMER_ID_CUSTOM == 100, "TIMER_ID_CUSTOM should be 100")

# timezone info carries an Olson id
tz = bus.time_zone_info("America/New_York", midnight_in_timezone="2026-08-31T00:00:00-04:00")
rtz = roundtrip(tz)
ok(rtz.olson_id == "America/New_York", "olson_id lost")
ok(rtz.midnight_in_timezone.startswith("2026-08-31"), "midnight_in_timezone lost")

# the triggered event carries the timer_id back
trig = T.UserAlarmTriggered(timer_id=T.UserAlarmRequest.TIMER_ID_USER_WAKE)
ok(roundtrip(trig).timer_id == T.UserAlarmRequest.TIMER_ID_USER_WAKE, "UserAlarmTriggered round-trip failed")

# they carry the right descriptor full names (bus subscription topics)
ok(bus.full_name(a) == "embodied.sys.UserAlarmRequest", f"unexpected full name {bus.full_name(a)}")
ok(bus.full_name(tz) == "embodied.sys.TimeZoneInfo", f"unexpected full name {bus.full_name(tz)}")

if fails:
    print("❌ time/alarm toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ time/alarm toolkit test OK — UserAlarmRequest (USER_WAKE/PARENT_APP/CUSTOM, repeats) + "
      "TimeZoneInfo (Olson id) + UserAlarmTriggered round-trip through embodied.sys.TimeEvents")
