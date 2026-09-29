#!/usr/bin/env python3
"""The IMU handling-event helpers in moxie_toolkit.bus (embodied.unity MpuPickup): the
registry, the MpuShakeDirection values and the event field/full names. See
docs/reverse-engineering/hardware/hardware-map.md (Semantic handling events).

    python3 tools/robot-toolkit/test_mpu_handling.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.unity import MpuPickup_pb2 as M  # noqa: E402
    from embodied.unity import enums_pb2 as E  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  mpu-handling toolkit test skipped — {e}")
    sys.exit(0)

registry = {bus.full_name(c) for c in bus.mpu_handling_classes()}
want = {f"embodied.unity.{n}" for n in ("MpuPickedUpShakenEventPB", "MpuPickedUpEventPB",
                                        "MpuTiltEventPB", "MpuPutDownEventPB")}
ok(len(registry) == 6 and want <= registry, f"handling registry wrong: {sorted(registry)}")
for name, val in (("Up", 0), ("Yaw", 3), ("LeftRight", 4), ("ForwardBack", 5), ("Invalid", 6)):
    ok(getattr(E, name) == val, f"MpuShakeDirection.{name} should be {val}")

M.MpuPickedUpShakenEventPB(shakeDirection=E.LeftRight)
M.MpuPickUpStatusEventPB(pitch=-30)
M.MpuIsNoisyEventPB(state=True)

report("mpu-handling", "registry + MpuShakeDirection values + shaken/pickup-status/noise fields")
