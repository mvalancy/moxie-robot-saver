#!/usr/bin/env python3
"""The perception-fusion world model (moxie_toolkit.bus.fused_people_classes): the registry a
revival server uses to consume who-is-in-the-room events off the [FullName][bytes] bus, and
the FusedPeoplePB / person / face / speech field names. See
docs/reverse-engineering/protocol/perception-fusion.md.

    python3 tools/robot-toolkit/test_fusion.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.perception.fusion import FusedPeople_pb2 as F  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  fusion toolkit test skipped — {e}")
    sys.exit(0)

registry = {bus.full_name(c): c for c in bus.fused_people_classes()}
ok(len(registry) == 12, f"expected 12 fusion classes, got {len(registry)}")
for name in ("FusedPeoplePB", "FusedPersonAddedPB", "FusedPersonEngagedPB", "FusedPersonSaidPB",
             "FusedPersonStartedSpeakingPB"):
    ok(f"embodied.perception.fusion.{name}" in registry, f"{name} missing from fused_people_classes()")

# the recovered field names (a missing or retyped field raises here)
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
F.FusedPersonStartedSpeakingPB(person=person, source=F.STT, timestamp=123457)

# the registry resolves a framed roster back to the class that parses it
parsed = registry[bus.full_name(roster)].FromString(roster.SerializeToString())
ok(parsed.people[0].speech.original_utterance == "hola Moxie", "registry resolved the wrong class")

report("fusion", "fused_people_classes() registry + FusedPeoplePB/person/face/speech/StartedSpeaking fields")
