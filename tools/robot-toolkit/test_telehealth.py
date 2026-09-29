#!/usr/bin/env python3
"""The telehealth (remote-puppet) builders in moxie_toolkit.cloud: each session Action, the
publishable TelehealthRobotCommand and its topic, and the robot->cloud event parser. See
docs/reverse-engineering/protocol/telehealth.md.

    python3 tools/robot-toolkit/test_telehealth.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.telehealth import TeleHealth_pb2 as TH  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  telehealth toolkit test skipped — {e}")
    sys.exit(0)

ok(cloud.telehealth_topic("d_x") == "/devices/d_x/commands/telehealth",
   f"telehealth_topic wrong: {cloud.telehealth_topic('d_x')}")
start = cloud.telehealth_session(TH.START_SESSION, session_id="s1")
ok(start.action == TH.START_SESSION and start.session_id == "s1", "START_SESSION message wrong")
for action in (TH.INTERRUPT, TH.END_SESSION):
    ok(cloud.telehealth_session(action).action == action, f"session action {action} wrong")

markup = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
play = cloud.telehealth_play_output("Hi there!", markup, session_id="s1",
                                    line_id="greet", line_params=["Alex"])
ok(play.action == TH.PLAY_OUTPUT, "PLAY_OUTPUT action wrong")
ok((play.output.text, play.output.markup, play.output.line_id, list(play.output.line_params))
   == ("Hi there!", markup, "greet", ["Alex"]), "Output fields wrong")
cmd = cloud.telehealth_command(play, command="play")
ok(cmd.command == "play" and cmd.message == play, "telehealth_command must wrap the message as given")

ev = TH.TelehealthRobotEvent(subtopic="telehealth",
                             message=TH.TelehealthMessage(action=TH.UPDATE_STATE, state=TH.IN_SESSION))
parsed = cloud.parse_telehealth_event(ev.SerializeToString())
ok(parsed.subtopic == "telehealth" and parsed.message.state == TH.IN_SESSION,
   "parse_telehealth_event wrong")

report("telehealth", "START/PLAY_OUTPUT(text+markup)/INTERRUPT/END + command wrapper/topic + event parser")
