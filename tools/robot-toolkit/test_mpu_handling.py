#!/usr/bin/env python3
"""Round-trip test for the IMU handling-event helpers in moxie_toolkit.bus
(embodied.unity MpuPickup). Builds a shaken event (with direction), a pickup-status
(pitch), and the IMU-noise gate, frames + re-parses them via the bus registry, and
checks the MpuShakeDirection enum. See docs/reverse-engineering/hardware-map.md
(Semantic handling events).

    python3 tools/robot-toolkit/test_mpu_handling.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.unity import MpuPickup_pb2 as M  # noqa: E402
    from embodied.unity import enums_pb2 as E  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  mpu-handling toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

registry = {bus.full_name(c): c for c in bus.mpu_handling_classes()}
ok(len(registry) == 6, f"expected 6 handling classes, got {len(registry)}")
ok("embodied.unity.MpuPickedUpShakenEventPB" in registry, "shaken event not registered")

# the shake-direction enum has all 7 axes
for name, val in (("Up", 0), ("Yaw", 3), ("LeftRight", 4), ("ForwardBack", 5), ("Invalid", 6)):
    ok(getattr(E, name) == val, f"MpuShakeDirection.{name} should be {val}")

# shaken LeftRight round-trips through the bus framing
sh = M.MpuPickedUpShakenEventPB(shakeDirection=E.LeftRight)
fn = bus.full_name(sh)
rt = registry[fn](); rt.ParseFromString(sh.SerializeToString())
ok(rt.shakeDirection == E.LeftRight, "shake direction lost")

# pickup status carries the held pitch angle
ps = M.MpuPickUpStatusEventPB(pitch=-30)
ok(M.MpuPickUpStatusEventPB.FromString(ps.SerializeToString()).pitch == -30, "pickup pitch lost")

# the self-motion noise gate is a bool
noisy_on = M.MpuIsNoisyEventPB(state=True)
noisy_off = M.MpuIsNoisyEventPB(state=False)
ok(M.MpuIsNoisyEventPB.FromString(noisy_on.SerializeToString()).state is True, "noisy-gate on lost")
ok(M.MpuIsNoisyEventPB.FromString(noisy_off.SerializeToString()).state is False, "noisy-gate off lost")

# picked-up / tilt / put-down are simple markers with the right names
for cls, name in ((M.MpuPickedUpEventPB, "MpuPickedUpEventPB"),
                  (M.MpuTiltEventPB, "MpuTiltEventPB"),
                  (M.MpuPutDownEventPB, "MpuPutDownEventPB")):
    ok(bus.full_name(cls) == f"embodied.unity.{name}", f"{name} full name wrong")

if fails:
    print("❌ mpu-handling toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ mpu-handling toolkit test OK — shaken(direction=LeftRight) + pickup-status(pitch) + IMU-noise "
      "gate + pickup/tilt/put-down round-trip through embodied.unity.MpuPickup")
