#!/usr/bin/env python3
"""Round-trip test for the RemoteChat (robot <-> brain) builders in moxie_toolkit.cloud.

Builds the RemoteChatResponse a self-hosted brain returns for one turn — text + markup +
mood, a launch action that drives the robot into a module, and a SUCCESS result — then
serializes + re-parses it (the exact reply a revival server sends). Also round-trips a
RemoteChatRequest (robot -> brain) with translated speech. See
docs/reverse-engineering/remote-chat-protocol.md.

    python3 tools/robot-toolkit/test_remote_chat.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.robotbrain import RemoteChat_pb2 as RC  # noqa: E402
    import moxie_toolkit.cloud as cloud  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  remote-chat toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# --- brain -> robot: a scored reply that also launches a module ---
markup = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
resp = cloud.remote_chat_reply("Let's play!", markup=markup, mood="joy", mood_intensity=0.9,
                               dialog_act="opening", sequence=3, event_id="evt-9")
resp.response_action.CopyFrom(cloud.remote_chat_action(RC.RemoteChatAction.launch, module_id="m_game"))

rt = cloud.parse_remote_chat_response(resp.SerializeToString())
ok(rt.result == RC.RemoteChatResponse.SUCCESS, "result should default to SUCCESS")
ok(rt.sequence == 3 and rt.event_id == "evt-9", "sequence/event_id lost")
ok(rt.output.text == "Let's play!" and rt.output.markup == markup, "output text/markup lost")
ok(rt.output.mood == "joy" and abs(rt.output.mood_intensity - 0.9) < 1e-6, "mood lost")
ok(rt.output.dialog_act == "opening", "dialog_act lost")
ok(rt.response_action.action == RC.RemoteChatAction.launch
   and rt.response_action.module_id == "m_game", "launch action lost")

# an execute action carries a function + args (result returns via execute_returns next turn)
ex = cloud.remote_chat_action(RC.RemoteChatAction.execute, function_id="set_volume", function_args=["6"])
ok(ex.action == RC.RemoteChatAction.execute and list(ex.function_args) == ["6"], "execute action wrong")

# result codes exist (force-quit / offline-fallback semantics)
for name in ("SUCCESS", "ERROR_OFFLINE", "NOREPLY_ACK", "REPLY_FORCE_QUIT", "REPLY_PENDING"):
    ok(hasattr(RC.RemoteChatResponse, name), f"ResultCode.{name} missing")

# --- robot -> brain: a request with translated speech ---
req = RC.RemoteChatRequest(speech="I want to play", confidence=0.92, session_id="s1",
                           user_id="u1", user_age=7, nickname="Alex",
                           original_language="es", original_speech="quiero jugar")
preq = cloud.parse_remote_chat_request(req.SerializeToString())
ok(preq.speech == "I want to play" and preq.nickname == "Alex" and preq.user_age == 7,
   "request identity/speech lost")
ok(preq.original_language == "es" and preq.original_speech == "quiero jugar",
   "request translation fields lost")

# dialog-act + emotion taxonomies present
ok(RC.RemoteDialog.yes_no_question and RC.RemoteDialog.thanking, "DialogAct enum incomplete")
ok(RC.RemoteDialog.joy and RC.RemoteDialog.neutral, "EmotionState enum incomplete")

if fails:
    print("❌ remote-chat toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ remote-chat toolkit test OK — RemoteChatResponse (text+markup+mood+dialog_act, launch/execute "
      "actions, ResultCodes) + RemoteChatRequest (translated speech) round-trip through embodied.robotbrain")
