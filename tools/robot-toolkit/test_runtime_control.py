#!/usr/bin/env python3
"""The imperative runtime-control builders in moxie_toolkit.bus (embodied.robotbrain
System/Reset/ChatScriptState): volume (absolute + signed delta), accessibility pacing,
force-listen, barge-in gate, soft/hard reset. See
docs/reverse-engineering/protocol/runtime-control.md.

    python3 tools/robot-toolkit/test_runtime_control.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.robotbrain import ChatScriptState_pb2 as C  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  runtime-control toolkit test skipped — {e}")
    sys.exit(0)

vabs, vrel = bus.volume_modify(6), bus.volume_modify(-1, relative=True)
ok((vabs.volume, vabs.relative) == (6, False), "absolute volume wrong")
ok((vrel.volume, vrel.relative) == (-1, True), "relative volume delta wrong (must be signed)")
ok(bus.full_name(vabs) == "embodied.robotbrain.SystemVolumeModify", "unexpected volume full name")
ok(bus.slow_input(True).slow_input is True and bus.slow_input(False).slow_input is False, "slow_input wrong")
lis = bus.chatbot_listening(True, user="u1", bot="moxie")
ok((lis.listening, lis.user, lis.bot) == (True, "u1", "moxie"), "listening request wrong")
ok(bus.allow_cutoff(False).allow is False and bus.allow_cutoff(True).allow is True, "allow_cutoff wrong")
ok(bus.full_name(bus.brain_reset()) == "embodied.robotbrain.SoftReset", "soft reset type wrong")
ok(bus.full_name(bus.brain_reset(hard=True)) == "embodied.robotbrain.HardReset", "hard reset type wrong")
C.ChatScriptReady(user="u1", bot="moxie")
C.ChatScriptException(message="boom", restore_default=True)

report("runtime-control", "volume (abs + signed delta), slow_input, chatbot_listening, "
       "allow_cutoff, soft/hard reset + ChatScript lifecycle fields")
