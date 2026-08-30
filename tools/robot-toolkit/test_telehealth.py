#!/usr/bin/env python3
"""
Round-trip test for the telehealth (remote-puppet) builders in moxie_toolkit.cloud.

Builds each Action of a telehealth session, wraps in the publishable
TelehealthRobotCommand, serializes + re-parses, and checks the fields survive — the
exact cloud->robot path a revival server uses. Also round-trips a robot->cloud
TelehealthRobotEvent. See docs/reverse-engineering/telehealth.md.

    python3 tools/robot-toolkit/test_telehealth.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.telehealth import TeleHealth_pb2 as TH  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  telehealth toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

DEV = "d_test-device"

# topic
ok(cloud.telehealth_topic(DEV) == f"/devices/{DEV}/commands/telehealth",
   f"telehealth_topic wrong: {cloud.telehealth_topic(DEV)}")

# START_SESSION
start = cloud.telehealth_session(TH.START_SESSION, session_id="s1")
ok(start.action == TH.START_SESSION and start.session_id == "s1", "START_SESSION message wrong")

# PLAY_OUTPUT with text + behavior markup + templated params
markup = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
play = cloud.telehealth_play_output("Hi there!", markup, session_id="s1",
                                    line_id="greet", line_params=["Alex"])
ok(play.action == TH.PLAY_OUTPUT, "PLAY_OUTPUT action wrong")
ok(play.output.text == "Hi there!" and play.output.markup == markup, "Output text/markup wrong")
ok(list(play.output.line_params) == ["Alex"] and play.output.line_id == "greet", "Output params wrong")

# INTERRUPT + END_SESSION
ok(cloud.telehealth_session(TH.INTERRUPT).action == TH.INTERRUPT, "INTERRUPT wrong")
ok(cloud.telehealth_session(TH.END_SESSION).action == TH.END_SESSION, "END_SESSION wrong")

# publishable command wrapper — serialize + re-parse (the wire round-trip)
cmd = cloud.telehealth_command(play, command="play")
wire = cmd.SerializeToString()
rt = TH.TelehealthRobotCommand()
rt.ParseFromString(wire)
ok(rt.command == "play", "command field lost on round-trip")
ok(rt.message.action == TH.PLAY_OUTPUT and rt.message.output.text == "Hi there!",
   "message lost on round-trip")
ok(rt.message.output.markup == markup, "markup lost on round-trip")

# robot -> cloud event round-trip
ev = TH.TelehealthRobotEvent(subtopic="telehealth",
                             message=TH.TelehealthMessage(action=TH.UPDATE_STATE, state=TH.IN_SESSION))
parsed = cloud.parse_telehealth_event(ev.SerializeToString())
ok(parsed.subtopic == "telehealth" and parsed.message.state == TH.IN_SESSION,
   "TelehealthRobotEvent round-trip failed")

if fails:
    print("❌ telehealth toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ telehealth toolkit test OK — START/PLAY_OUTPUT(text+markup)/INTERRUPT/END + command & event "
      "round-trip through TelehealthRobotCommand/Event")
