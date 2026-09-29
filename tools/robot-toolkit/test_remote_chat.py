#!/usr/bin/env python3
"""The RemoteChat (robot <-> brain) builders and parsers in moxie_toolkit.cloud: the
RemoteChatResponse a self-hosted brain returns for one turn (text + markup + mood, a launch
or execute action, the ResultCodes) and a RemoteChatRequest with translated speech. See
docs/reverse-engineering/protocol/remote-chat-protocol.md.

    python3 tools/robot-toolkit/test_remote_chat.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.robotbrain import RemoteChat_pb2 as RC  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  remote-chat toolkit test skipped — {e}")
    sys.exit(0)

markup = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
resp = cloud.remote_chat_reply("Let's play!", markup=markup, mood="joy", mood_intensity=0.9,
                               dialog_act="opening", sequence=3, event_id="evt-9")
ok(resp.result == RC.RemoteChatResponse.SUCCESS, "result should default to SUCCESS")
ok((resp.sequence, resp.event_id) == (3, "evt-9"), "sequence/event_id wrong")
o = resp.output
ok((o.text, o.markup, o.mood, o.dialog_act) == ("Let's play!", markup, "joy", "opening")
   and abs(o.mood_intensity - 0.9) < 1e-6, "output fields wrong")

launch = cloud.remote_chat_action(RC.RemoteChatAction.launch, module_id="m_game")
ok(launch.action == RC.RemoteChatAction.launch and launch.module_id == "m_game", "launch action wrong")
ex = cloud.remote_chat_action(RC.RemoteChatAction.execute, function_id="set_volume", function_args=["6"])
ok(ex.action == RC.RemoteChatAction.execute and ex.function_id == "set_volume"
   and list(ex.function_args) == ["6"], "execute action wrong")
ok(cloud.parse_remote_chat_response(resp.SerializeToString()).event_id == "evt-9",
   "parse_remote_chat_response wrong")

for name in ("SUCCESS", "ERROR_OFFLINE", "NOREPLY_ACK", "REPLY_FORCE_QUIT", "REPLY_PENDING"):
    ok(hasattr(RC.RemoteChatResponse, name), f"ResultCode.{name} missing")
ok(RC.RemoteDialog.yes_no_question and RC.RemoteDialog.thanking, "DialogAct enum incomplete")
ok(RC.RemoteDialog.joy and RC.RemoteDialog.neutral, "EmotionState enum incomplete")

req = RC.RemoteChatRequest(speech="I want to play", confidence=0.92, session_id="s1",
                           user_id="u1", user_age=7, nickname="Alex",
                           original_language="es", original_speech="quiero jugar")
ok(cloud.parse_remote_chat_request(req.SerializeToString()).original_speech == "quiero jugar",
   "parse_remote_chat_request wrong")

report("remote-chat", "remote_chat_reply/action (launch, execute) + ResultCodes/DialogAct/Emotion "
       "+ request/response parsers")
