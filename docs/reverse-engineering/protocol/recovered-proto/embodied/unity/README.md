# 📁 `unity`

Recovered `.proto` schemas for the **`embodied.unity`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`AssetBundle.proto`](AssetBundle.proto) | `AssetBundleCache`, `AssetBundleScan`, `AssetBundleRelease`, `AssetBundleReload` |
| [`AudioNotif.proto`](AudioNotif.proto) | `AudioNotifPauseEventPB`, `AudioNotifSpeedChangeEventPB`, `AudioNotifVolumeChangeEventPB`, `AudioIsFinishedEventPB`, `AudioNotifResumeEventPB`, `AudioNotifChatEventPB` |
| [`CloudTTS.proto`](CloudTTS.proto) | `TTSMark`, `CloudTTSRequest`, `AudioBuffer`, `CloudTTSResponse`, `CloudTTSSupplement`; enums `RequestSourceType` |
| [`ConsoleCommandRequest.proto`](ConsoleCommandRequest.proto) | `ConsoleCommandRequest` |
| [`EngagementScore.proto`](EngagementScore.proto) | `EngagedEvent` |
| [`Gaze.proto`](Gaze.proto) | `Gaze` |
| [`MainAppShutdown.proto`](MainAppShutdown.proto) | `MainAppShutdown` |
| [`MainAppStatus.proto`](MainAppStatus.proto) | `MainAppStatus` |
| [`MarkUpToolMessages.proto`](MarkUpToolMessages.proto) | `MarkUpEditRequest`, `MarkUpEditResponse`, `MarkUpEditorClosedEvent`, `MarkUpLineRequest`, `MarkUpLineResponse` |
| [`MpuPickup.proto`](MpuPickup.proto) | `MpuPickedUpEventPB`, `MpuPickedUpShakenEventPB`, `MpuPickUpStatusEventPB`, `MpuPutDownEventPB`, `MpuTiltEventPB`, `MpuIsNoisyEventPB` |
| [`NetworkState.proto`](NetworkState.proto) | `NetworkState` |
| [`PredictedMotorNoise.proto`](PredictedMotorNoise.proto) | `PredictedMotorNoise` |
| [`RobotCamera.proto`](RobotCamera.proto) | `RobotCamera` |
| [`RobotCameraShake.proto`](RobotCameraShake.proto) | `RobotCameraShake` |
| [`RobotEngageTurn.proto`](RobotEngageTurn.proto) | `RobotEngageTurn` |
| [`RobotPosition.proto`](RobotPosition.proto) | `RobotPosition` |
| [`RobotRequestChatPause.proto`](RobotRequestChatPause.proto) | `RobotRequestChatPause` |
| [`RobotTurnToOutOfViewChatTarget.proto`](RobotTurnToOutOfViewChatTarget.proto) | `RobotTurnToOutOfViewChatTarget` |
| [`SFXPlayback.proto`](SFXPlayback.proto) | `SFXPlaybackState` |
| [`SilentBootComplete.proto`](SilentBootComplete.proto) | `SilentBootComplete` |
| [`SoftwareVersion.proto`](SoftwareVersion.proto) | `SoftwareVersion` |
| [`SpeechPlayback.proto`](SpeechPlayback.proto) | `SpeechPlaybackState` |
| [`Stats.proto`](Stats.proto) | `FPSStatsPB`, `TTSAudioClipInfoPB`, `TTSStatsPB` |
| [`UserData.proto`](UserData.proto) | `UserPairingRequest`, `UserDataStatus`; enums `PairingRequest` |
| [`enums.proto`](enums.proto) | enums `MpuShakeDirection` |

---
📖 [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
