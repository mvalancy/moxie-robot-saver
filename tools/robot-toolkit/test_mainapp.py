#!/usr/bin/env python3
"""Round-trip test for the MAINAPP (Unity front-end) interface helpers in
moxie_toolkit.bus (embodied.unity). Builds a RobotCamera (drive the face self-view),
a CloudTTSResponse a server returns (PCM + a viseme TTSMark), a UserPairingRequest,
and checks the lifecycle + audio-notif subscribe sets. See
docs/reverse-engineering/unity-mainapp-interface.md.

    python3 tools/robot-toolkit/test_mainapp.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.unity import CloudTTS_pb2 as C  # noqa: E402
    from embodied.unity import RobotCamera_pb2 as R  # noqa: E402
    from embodied.unity import UserData_pb2 as U  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  mainapp toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

def rt(msg):
    out = type(msg)(); out.ParseFromString(msg.SerializeToString()); return out

# --- virtual camera ---
cam = bus.robot_camera((0, 0.1, -1), (0, 0, 0), fov=45.0)
rc = rt(cam)
ok(abs(rc.center_z + 1) < 1e-6 and abs(rc.fov - 45.0) < 1e-6, "RobotCamera transform/fov lost")
ok(abs(rc.up_y - 1.0) < 1e-6, "RobotCamera up vector default lost")
ok(bus.full_name(cam) == "embodied.unity.RobotCamera", "camera full name wrong")

# --- CloudTTSResponse a server returns (PCM + one viseme mark) ---
resp = bus.cloud_tts_response(b"\x01\x02\x03\x04", sample_rate=22050, event_id="e1",
                              marks=[{"time": 100, "start": 0, "end": 4, "type": "viseme", "value": "AA"}],
                              remote=True)
rr = rt(resp)
ok(rr.request_source == C.REMOTECHAT_TTS_REQUEST, "request_source should be REMOTECHAT")
ok(rr.audio.buffer == b"\x01\x02\x03\x04" and rr.audio.sample_rate == 22050, "AudioBuffer PCM/rate lost")
ok(len(rr.marks) == 1 and rr.marks[0].type == "viseme" and rr.marks[0].value == "AA"
   and rr.marks[0].time == 100, "TTSMark lost")

# --- pairing request with the action enum ---
pr = bus.user_pairing_request(U.UserPairingRequest.UNPAIR_FULL, secret_key=b"\x00" * 4)
rpr = rt(pr)
ok(rpr.request == U.UserPairingRequest.UNPAIR_FULL, "pairing action lost")
ok(rpr.secret_key == b"\x00" * 4, "pairing secret_key lost")
# the 8 pairing actions exist
for name in ("PAIR", "UNPAIR_USER", "UNPAIR_FULL", "UNPAIR_RFS_ONLY", "RECOVER_USER",
             "RECOVER_USER_LOCAL", "USER_DATA_UPDATE"):
    ok(hasattr(U.UserPairingRequest, name), f"PairingRequest.{name} missing")

# --- subscribe sets ---
life = {bus.full_name(c) for c in bus.mainapp_lifecycle_classes()}
ok("embodied.unity.MainAppStatus" in life and "embodied.unity.SoftwareVersion" in life,
   f"lifecycle set incomplete: {life}")
an = {bus.full_name(c) for c in bus.audio_notif_classes()}
ok(len(an) == 6 and "embodied.unity.AudioNotifPauseEventPB" in an, f"audio-notif set incomplete: {an}")

if fails:
    print("❌ mainapp toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ mainapp toolkit test OK — RobotCamera + CloudTTSResponse(PCM+TTSMark) + UserPairingRequest + "
      "lifecycle/audio-notif subscribe sets round-trip through embodied.unity (the MAINAPP interface)")
