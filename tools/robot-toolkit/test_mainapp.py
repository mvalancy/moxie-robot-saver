#!/usr/bin/env python3
"""The MAINAPP (Unity front-end) builders in moxie_toolkit.bus (embodied.unity): RobotCamera
(the face self-view), the CloudTTSResponse a server returns (PCM + a viseme TTSMark),
UserPairingRequest, and the lifecycle + audio-notif subscribe sets. See
docs/reverse-engineering/protocol/unity-mainapp-interface.md.

    python3 tools/robot-toolkit/test_mainapp.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.unity import CloudTTS_pb2 as C  # noqa: E402
    from embodied.unity import UserData_pb2 as U  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  mainapp toolkit test skipped — {e}")
    sys.exit(0)

cam = bus.robot_camera((0, 0.1, -1), (0, 0, 0), fov=45.0)
ok(abs(cam.center_z + 1) < 1e-6 and abs(cam.fov - 45.0) < 1e-6, "RobotCamera transform/fov wrong")
ok(abs(cam.up_y - 1.0) < 1e-6, "RobotCamera up vector default wrong")
ok(bus.full_name(cam) == "embodied.unity.RobotCamera", "camera full name wrong")

resp = bus.cloud_tts_response(b"\x01\x02\x03\x04", sample_rate=22050, event_id="e1",
                              marks=[{"time": 100, "start": 0, "end": 4, "type": "viseme", "value": "AA"}],
                              remote=True)
ok(resp.request_source == C.REMOTECHAT_TTS_REQUEST, "request_source should be REMOTECHAT")
ok(resp.audio.buffer == b"\x01\x02\x03\x04" and resp.audio.sample_rate == 22050, "AudioBuffer PCM/rate wrong")
ok([(m.time, m.type, m.value) for m in resp.marks] == [(100, "viseme", "AA")], "TTSMark wrong")

pr = bus.user_pairing_request(U.UserPairingRequest.UNPAIR_FULL, secret_key=b"\x00" * 4)
ok(pr.request == U.UserPairingRequest.UNPAIR_FULL and pr.secret_key == b"\x00" * 4, "pairing request wrong")
for name in ("PAIR", "UNPAIR_USER", "UNPAIR_FULL", "UNPAIR_RFS_ONLY", "RECOVER_USER",
             "RECOVER_USER_LOCAL", "USER_DATA_UPDATE"):
    ok(hasattr(U.UserPairingRequest, name), f"PairingRequest.{name} missing")

life = {bus.full_name(c) for c in bus.mainapp_lifecycle_classes()}
ok({"embodied.unity.MainAppStatus", "embodied.unity.SoftwareVersion"} <= life,
   f"lifecycle set incomplete: {life}")
an = {bus.full_name(c) for c in bus.audio_notif_classes()}
ok(len(an) == 6 and "embodied.unity.AudioNotifPauseEventPB" in an, f"audio-notif set incomplete: {an}")

report("mainapp", "robot_camera + cloud_tts_response(PCM+TTSMark) + user_pairing_request + "
       "lifecycle/audio-notif subscribe sets")
