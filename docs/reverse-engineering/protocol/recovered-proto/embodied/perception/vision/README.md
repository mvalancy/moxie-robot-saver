# 📁 vision

The [`embodied.perception.vision`](../../../../proto-catalog.md#embodiedperceptionvision) package: what the vision module reports from the camera.
That is detected, tracked and recognized faces, face-ID enrollment, people, poses and QR strings.
It also carries book and drawing IDs, occlusion, rapid motion, "show me" state and offline face analysis.
[Perception pipeline](../../../../../runtime/perception-pipeline.md#vision-embodiedperceptionvision) explains each one.

| File | Defines |
|---|---|
| [`BookId.proto`](BookId.proto) | `BookIdPB` |
| [`DrawId.proto`](DrawId.proto) | `DrawIdPB` |
| [`Face.proto`](Face.proto) | `Face` |
| [`FaceIDEnrollment.proto`](FaceIDEnrollment.proto) | `FaceIDEnrollmentState`, `FaceIDEnrollmentInfo`, `FaceIDEnrollmentsInfo`; enums `State` |
| [`FacesDetected.proto`](FacesDetected.proto) | `DetectedFacePB`, `FacesDetectedPB` |
| [`FacesRecognized.proto`](FacesRecognized.proto) | `RecognizedPersonPB`, `FacesRecognizedPB` |
| [`FacesTracked.proto`](FacesTracked.proto) | `WorldPosition`, `TrackedFacePB`, `FacesTrackedPB` |
| [`ImageToText.proto`](ImageToText.proto) | `ImageToTextPB` |
| [`OcclusionDetected.proto`](OcclusionDetected.proto) | `OcclusionPB` |
| [`OfflineFace.proto`](OfflineFace.proto) | `ActionUnitPB`, `HeadPosePB`, `AnalyzedFacePB`, `FacesAnalyzedPB`, `OfflineMediaPB`, `OfflineAnalysisReady` |
| [`PeopleDetected.proto`](PeopleDetected.proto) | `PersonPB`, `PeopleDetectedPB` |
| [`Person.proto`](Person.proto) | `Person` |
| [`PosesEstimated.proto`](PosesEstimated.proto) | `jointPosPB`, `PosePB`, `PosesEstimatedPB` |
| [`QR.proto`](QR.proto) | `QRPB` |
| [`RapidMotionDetected.proto`](RapidMotionDetected.proto) | `RapidMotionPB` |
| [`ShowState.proto`](ShowState.proto) | `ShowState`; enums `Type`, `State` |

---
📖 [perception](../README.md) · [Docs index](../../../../../../../docs/README.md) · [Back to top](../../../../../../../README.md)
