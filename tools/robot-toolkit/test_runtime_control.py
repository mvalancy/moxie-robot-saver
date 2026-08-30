#!/usr/bin/env python3
"""Round-trip test for the imperative runtime-control builders in moxie_toolkit.bus
(embodied.robotbrain System/Reset/ChatScriptState). Builds each control command a
server/app sends to a running brain — volume (absolute + relative delta), accessibility
pacing, force-listen, barge-in gate, soft/hard reset — frames + re-parses them, and
checks descriptor names. See docs/reverse-engineering/runtime-control.md.

    python3 tools/robot-toolkit/test_runtime_control.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.robotbrain import System_pb2 as S  # noqa: E402
    from embodied.robotbrain import Reset_pb2 as R  # noqa: E402
    from embodied.robotbrain import ChatScriptState_pb2 as C  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  runtime-control toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

def rt(msg):
    out = type(msg)(); out.ParseFromString(msg.SerializeToString()); return out

# volume: absolute set + relative down-nudge (signed)
vabs = rt(bus.volume_modify(6))
ok(vabs.volume == 6 and vabs.relative is False, "absolute volume wrong")
vrel = rt(bus.volume_modify(-1, relative=True))
ok(vrel.volume == -1 and vrel.relative is True, "relative volume delta wrong (must be signed)")
ok(bus.full_name(vabs) == "embodied.robotbrain.SystemVolumeModify", "unexpected volume full name")

# accessibility pacing
ok(rt(bus.slow_input(True)).slow_input is True, "slow_input on lost")
ok(rt(bus.slow_input(False)).slow_input is False, "slow_input off lost")

# listening + barge-in
lis = rt(bus.chatbot_listening(True, user="u1", bot="moxie"))
ok(lis.listening is True and lis.user == "u1" and lis.bot == "moxie", "listening request lost")
ok(rt(bus.allow_cutoff(False)).allow is False, "allow_cutoff block lost")
ok(rt(bus.allow_cutoff(True)).allow is True, "allow_cutoff permit lost")

# reset: soft (default) vs hard, distinct types
soft = bus.brain_reset()
hard = bus.brain_reset(hard=True)
ok(bus.full_name(soft) == "embodied.robotbrain.SoftReset", f"soft reset type wrong: {bus.full_name(soft)}")
ok(bus.full_name(hard) == "embodied.robotbrain.HardReset", f"hard reset type wrong: {bus.full_name(hard)}")
ok(isinstance(soft, R.SoftReset) and isinstance(hard, R.HardReset), "reset builder returned wrong class")

# chatscript lifecycle round-trips
ready = C.ChatScriptReady(user="u1", bot="moxie")
ok(rt(ready).user == "u1", "ChatScriptReady round-trip failed")
exc = C.ChatScriptException(message="boom", restore_default=True)
ok(rt(exc).restore_default is True and rt(exc).message == "boom", "ChatScriptException round-trip failed")

if fails:
    print("❌ runtime-control toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ runtime-control toolkit test OK — volume (abs + signed delta), slow_input, chatbot_listening, "
      "allow_cutoff, soft/hard reset + ChatScript lifecycle round-trip through embodied.robotbrain")
