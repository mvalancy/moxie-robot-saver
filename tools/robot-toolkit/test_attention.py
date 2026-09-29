#!/usr/bin/env python3
"""The published attention decision (moxie_toolkit.bus.attention_classes): the registry,
the AttentionState values, and the Attention / TargetedUser field names. See
docs/reverse-engineering/runtime/gaze-and-attention.md (The published attention state).

    python3 tools/robot-toolkit/test_attention.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.robotbrain import TargetUser_pb2 as A  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  attention toolkit test skipped — {e}")
    sys.exit(0)

registry = {bus.full_name(c) for c in bus.attention_classes()}
ok(len(registry) == 3 and "embodied.robotbrain.Attention" in registry,
   f"attention registry wrong: {sorted(registry)}")
ok((A.TARGET_FOCUS, A.NO_TARGET_FOCUS, A.SEARCHING) == (1, 2, 3), "AttentionState enum values wrong")

# the recovered field names (a missing or retyped field raises here)
att = A.Attention(state=A.TARGET_FOCUS, targeted_user=42)
ip = att.locations.add(weight=0.9, person_id=42)
ip.location.id, ip.location.x, ip.location.z = 1, 0.2, 1.1
A.TargetedUser(targeted_user_id=42, targeted_user_face_id=7)

report("attention", "registry + AttentionState values + Attention/InterestPoint/TargetedUser fields")
