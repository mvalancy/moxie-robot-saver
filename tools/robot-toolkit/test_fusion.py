#!/usr/bin/env python3
"""Round-trip test for the perception-fusion world-model helpers (moxie_toolkit.bus).

Builds a FusedPeoplePB roster with one recognized, engaged, speaking person (face
with head pose + eye landmarks, a DOA-placed utterance with a translation), applies
the same [FullName][bytes] framing the on-device ZMQ bus uses, and re-parses it via
the fused_people_classes() registry — the exact path a revival server uses to consume
who-is-in-the-room events. See docs/reverse-engineering/perception-fusion.md.

    python3 tools/robot-toolkit/test_fusion.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.perception.fusion import FusedPeople_pb2 as F  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  fusion toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

# helper: the bus frames a message as [descriptor full_name][serialized bytes]
def frame(msg):
    return bus.full_name(msg), msg.SerializeToString()

# a registry of the fusion classes, keyed by full name (what MoxieBus.subscribe builds)
registry = {bus.full_name(c): c for c in bus.fused_people_classes()}
ok(len(registry) == 12, f"expected 12 fusion classes, got {len(registry)}")
ok("embodied.perception.fusion.FusedPeoplePB" in registry, "FusedPeoplePB not registered")

# build one fully-populated person
face = F.FusedFacePB(world_x=0.2, world_y=0.0, world_z=1.1, yaw=-5.0, pitch=2.0, roll=0.0,
                     is_smiling=True, smile_confidence=0.8,
                     world_left_eye_x=0.17, world_right_eye_x=0.23, face_tracker_id=7)
speech = F.FusedSpeechPB(doa=12.5, doa_confidence=0.9, is_speaking=True,
                         utterance="hello Moxie", alternate_utterances=["hello moxi"],
                         language="en", original_language="es", original_utterance="hola Moxie",
                         stt_event_id="evt-1")
person = F.FusedPersonPB(id=1, name="Alex", fullname="Alex Doe", is_visible=True, is_engaged=True,
                         engagement=0.87, confidence=0.95, world_x=0.2, world_y=0.0, world_z=1.1,
                         vad_speaking=True, face=face, speech=speech)
roster = F.FusedPeoplePB(people=[person], timestamp=123456)

# frame + re-parse through the registry (the recv() path)
fn, body = frame(roster)
cls = registry[fn]
rt = cls(); rt.ParseFromString(body)
ok(len(rt.people) == 1, "roster lost the person")
p = rt.people[0]
ok(p.id == 1 and p.name == "Alex" and p.fullname == "Alex Doe", "identity lost")
ok(p.is_engaged and abs(p.engagement - 0.87) < 1e-6, "engagement lost")
ok(abs(p.world_z - 1.1) < 1e-6 and p.vad_speaking, "world pos / vad lost")
ok(p.face.is_smiling and p.face.face_tracker_id == 7 and abs(p.face.world_left_eye_x - 0.17) < 1e-6,
   "face sub-model lost")
ok(abs(p.speech.doa - 12.5) < 1e-6 and p.speech.utterance == "hello Moxie", "speech/DOA lost")
ok(p.speech.original_language == "es" and p.speech.original_utterance == "hola Moxie",
   "translation fields lost")

# an event wraps the person + carries a speaking source (STT vs VAD)
ev = F.FusedPersonStartedSpeakingPB(person=person, source=F.STT, timestamp=123457)
efn, ebody = frame(ev)
ecls = registry[efn]
ret = ecls(); ret.ParseFromString(ebody)
ok(ret.source == F.STT and ret.person.name == "Alex",
   "StartedSpeaking event (source/person) round-trip failed")

# added/removed/engaged events are all in the registry
for name in ("FusedPersonAddedPB", "FusedPersonEngagedPB", "FusedPersonSaidPB"):
    ok(f"embodied.perception.fusion.{name}" in registry, f"{name} missing from fused_people_classes()")

if fails:
    print("❌ fusion toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ fusion toolkit test OK — FusedPeoplePB roster (identity/engagement/world-pos/face/DOA/"
      "translation) + StartedSpeaking(source=STT) round-trip through the bus framing + fused_people_classes()")
