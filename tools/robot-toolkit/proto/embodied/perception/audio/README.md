# 📁 `audio`

Recovered `.proto` schemas for the **`embodied.perception.audio`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`DOA.proto`](DOA.proto) | `DOA` |
| [`GoogleAccount.proto`](GoogleAccount.proto) | `GoogleAccount` |
| [`Interrupt.proto`](Interrupt.proto) | `Interrupt`, `AllowInterrupt`, `CutoffStatistics`, `CutoffDetected`, `NonTargetCutoff` |
| [`SNR.proto`](SNR.proto) | `PoorSNR` |
| [`STT.proto`](STT.proto) | `STTPartial`, `STTFinal`, `STTReady`, `ASRAnalytics`, `DeepgramResponse`, `Channel` … |
| [`Speaker.proto`](Speaker.proto) | `Speaker`, `EnrollmentState`; enums `State` |
| [`Speech.proto`](Speech.proto) | `SpeechStateChanged`, `VoiceActivity`; enums `VoiceActivityState` |
| [`Status.proto`](Status.proto) | `Status` |
| [`WakeWord.proto`](WakeWord.proto) | `WakeWordEvent` |
| [`XmosConfig.proto`](XmosConfig.proto) | `EchoSuppressConfig` |
| [`zmqSTT.proto`](zmqSTT.proto) | `zmqSTTRequest`, `zmqSTTResponse`; enums `VADState`, `ResponseType` |

---
📖 [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
