# 📁 fusion

The [`embodied.perception.fusion`](../../../../proto-catalog.md#embodiedperceptionfusion) package: the model of the people in the room that `libbo-fusion` builds from faces, bodies and speech.
Its events report a person added, removed, moved, speaking, saying something, smiling, engaged or disengaged.
[Perception fusion](../../../../perception-fusion.md) explains the model.

| File | Defines |
|---|---|
| [`FusedPeople.proto`](FusedPeople.proto) | `FusedPeoplePB`, `FusedPersonPB`, `FusedFacePB`, `FusedBodyPB`, `FusedSpeechPB`, `FusedPersonAddedPB`, `FusedPersonRemovedPB`, `FusedPersonMovedPB`, `FusedPersonStartedSpeakingPB`, `FusedPersonStoppedSpeakingPB`, `FusedPersonSayingPB`, `FusedPersonSayingTimeoutPB`, `FusedPersonSaidPB`, `FusedPersonSmiledPB`, `FusedPersonEngagedPB`, `FusedPersonDisengagedPB`; enums `FusedPersonSpeakingSource` |

---
📖 [perception](../README.md) · [Docs index](../../../../../../../docs/README.md) · [Back to top](../../../../../../../README.md)
