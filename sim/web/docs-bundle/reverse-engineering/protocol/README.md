# 🔌 Protocol — the robot↔server & on-device wire

The wire formats between robot, server and on-device modules, recovered from firmware `v24.10.803`.
Start with **cloud-protocol** (what a self-hosted server implements) and **robot-ipc-protocol** (the
on-device bus); the rest go deep on one message family each.

| Doc | What it covers |
|---|---|
| [`cloud-protocol.md`](cloud-protocol.md) | Robot↔backend: REST `client-service`, the `IOTEndpoint` host table, `ServiceConfiguration`/`cloud.json`, MQTT topic map (incl. `commands/zmq`), device auth (RS256 JWT), Deepgram STT, `CloudQuery`, file sync, the full session sequence, minimum viable backend. |
| [`robot-ipc-protocol.md`](robot-ipc-protocol.md) | The on-device ZeroMQ + protobuf bus: ports, two-frame framing, module/package map, `<mark cmd:…>` behavior markup, the 31 console commands. |
| [`remote-chat-protocol.md`](remote-chat-protocol.md) | Per-turn robot↔brain RPC: `RemoteChatRequest`/`RemoteChatResponse`, the 10 `ResultCode`s, output scoring, navigation actions, `InputSafety`, metrics, dialog-act/emotion/signal taxonomies. |
| [`device-config-and-telemetry.md`](device-config-and-telemetry.md) | `embodied.logging` data model: `RobotCloudConfig`, the child-PII boundary, `RobotStatus`, `CloudStatus.UserState`, `Packet` telemetry, `LoggingPolicy`. |
| [`runtime-control.md`](runtime-control.md) | Imperative commands to a running brain: volume, slow-input pacing, force-listen, barge-in gate, soft/hard reset, ChatScript lifecycle. |
| [`power-and-system-events.md`](power-and-system-events.md) | `PowerStatePB` (11 states), `ResumeCause`, `RESTART_XMOS`, Wi-Fi/internet/STT/OTA status, unpair/disengage, timezone and wake alarms. |
| [`perception-fusion.md`](perception-fusion.md) | `libbo-fusion` people model: `FusedPersonPB` (world + screen frames, eyes, head pose, engagement, translation-aware speech) and the person event stream. |
| [`offline-and-brain-state.md`](offline-and-brain-state.md) | Persisted brain state: the `FallbackInfo` offline tree (pushed via `upgrade_fallbacks`), `CSData` resume point, recommender memory. |
| [`telehealth.md`](telehealth.md) | Remote-puppet mode: `STATE_TELEBRAIN` + `TeleHealth.proto` (`START_SESSION`/`PLAY_OUTPUT`/`INTERRUPT`) over MQTT. |
| [`unity-mainapp-interface.md`](unity-mainapp-interface.md) | `embodied.unity` brain↔Unity seam: lifecycle, virtual camera, CloudTTS audio + marks, playback control, asset bundles, pairing actions, stats, markup tool. |
| [`qr-commands.md`](qr-commands.md) | The closed QR grammar: `PA`/`VN`/JSON forms, the 4 setup-app debug commands, the 3 native `RightPoint` codes (`report`, `endpoint_update`, `om`), Wi-Fi support, setup-app status/error codes, runtime content QR. |
| [`network-trust.md`](network-trust.md) | TLS trust: CA-store validation with no pinning, `disable_verify`, why pre-801 is stuck, NTP/clock skew. |
| [`proto-catalog.md`](proto-catalog.md) | Generated catalog of every message, enum and field (382 messages · 84 enums · 2074 fields). |
| [`recovered-proto/`](recovered-proto/) | The 120 `.proto` files reconstructed from the robot binaries. |

---
📖 [Reverse-engineering index](../README.md) · [Field guide](../FIELD-GUIDE.md) · [Exploration map](../EXPLORATION-MAP.md)
