# 📁 audio

The [`embodied.perception.audio`](../../../../../../docs/reverse-engineering/protocol/proto-catalog.md#embodiedperceptionaudio) package: what the audio module reports.
That is speech-to-text results and the `zmqSTT` engine interface, wake word, voice activity and direction of arrival.
It also carries interrupts, speaker enrollment and the XMOS echo-suppression config.
[Perception pipeline](../../../../../../docs/reverse-engineering/runtime/perception-pipeline.md#input-side-embodiedperceptionaudio) explains each one.

| File | Defines |
|---|---|
| [`DOA.proto`](DOA.proto) | `DOA` |
| [`GoogleAccount.proto`](GoogleAccount.proto) | `GoogleAccount` |
| [`Interrupt.proto`](Interrupt.proto) | `Interrupt`, `AllowInterrupt`, `CutoffStatistics`, `CutoffDetected`, `NonTargetCutoff` |
| [`SNR.proto`](SNR.proto) | `PoorSNR` |
| [`STT.proto`](STT.proto) | `STTPartial`, `STTFinal`, `STTReady`, `ASRAnalytics`, `DeepgramResponse`, `Channel`, `Alternative`, `Word` |
| [`Speaker.proto`](Speaker.proto) | `Speaker`, `EnrollmentState`; enums `State` |
| [`Speech.proto`](Speech.proto) | `SpeechStateChanged`, `VoiceActivity`; enums `VoiceActivityState` |
| [`Status.proto`](Status.proto) | `Status` |
| [`WakeWord.proto`](WakeWord.proto) | `WakeWordEvent` |
| [`XmosConfig.proto`](XmosConfig.proto) | `EchoSuppressConfig` |
| [`zmqSTT.proto`](zmqSTT.proto) | `zmqSTTRequest`, `zmqSTTResponse`; enums `VADState`, `ResponseType` |

---
📖 [perception](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
