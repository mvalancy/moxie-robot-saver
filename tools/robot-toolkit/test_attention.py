#!/usr/bin/env python3
"""Round-trip test for the published attention decision (moxie_toolkit.bus.attention_classes).

Builds an Attention message (TARGET_FOCUS on a specific fused person, with candidate
InterestPoints), frames + re-parses it via the bus registry, and round-trips the
TargetedUser acquire edge. See docs/reverse-engineering/gaze-and-attention.md
(The published attention state).

    python3 tools/robot-toolkit/test_attention.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.robotbrain import TargetUser_pb2 as A  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  attention toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

registry = {bus.full_name(c): c for c in bus.attention_classes()}
ok(len(registry) == 3, f"expected 3 attention classes, got {len(registry)}")
ok("embodied.robotbrain.Attention" in registry, "Attention not registered")

# the state enum values
ok((A.TARGET_FOCUS, A.NO_TARGET_FOCUS, A.SEARCHING) == (1, 2, 3), "AttentionState enum values wrong")

# focused on fused person id 42, with two weighted candidate interest points
att = A.Attention(state=A.TARGET_FOCUS, targeted_user=42)
ip1 = att.locations.add(); ip1.weight = 0.9; ip1.person_id = 42
ip1.location.id = 1; ip1.location.x = 0.2; ip1.location.z = 1.1
ip2 = att.locations.add(); ip2.weight = 0.3; ip2.person_id = 43
fn = bus.full_name(att)
rt = registry[fn](); rt.ParseFromString(att.SerializeToString())
ok(rt.state == A.TARGET_FOCUS and rt.targeted_user == 42, "Attention state/target lost")
ok(len(rt.locations) == 2 and rt.locations[0].person_id == 42
   and abs(rt.locations[0].weight - 0.9) < 1e-6, "interest points lost")
ok(abs(rt.locations[0].location.z - 1.1) < 1e-6, "world location lost")

# SEARCHING state with no target
searching = A.Attention(state=A.SEARCHING, targeted_user=0)
ok(searching.state == A.SEARCHING and searching.targeted_user == 0, "searching build wrong")

# the acquire edge names the fused person + their face tracker id
tu = A.TargetedUser(targeted_user_id=42, targeted_user_face_id=7)
rtu = A.TargetedUser(); rtu.ParseFromString(tu.SerializeToString())
ok(rtu.targeted_user_id == 42 and rtu.targeted_user_face_id == 7, "TargetedUser round-trip failed")

if fails:
    print("❌ attention toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ attention toolkit test OK — Attention (TARGET_FOCUS/SEARCHING, targeted fused-person + candidate "
      "InterestPoints) + TargetedUser acquire edge round-trip through embodied.robotbrain.TargetUser")
