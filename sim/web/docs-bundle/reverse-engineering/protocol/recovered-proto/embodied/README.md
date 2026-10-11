# 📁 embodied

The recovered `embodied.*` packages, one folder per area of the robot.
Some folders hold more than one package, and `wifiapp/` holds `embodied.unity`; the [folder-to-package map](../README.md#layout) lists them all.
Every message and field is in the [protocol catalog](../../proto-catalog.md).

- [`launcher/`](launcher/README.md) — component state.
- [`lizzerface/`](lizzerface/README.md) — MCU protocol: motors, PID config, power rails, LED patterns, touch/switch/IMU/battery/servo events.
- [`logging/`](logging/README.md) — cloud config, backup/file sync, metrics, `IOTEndpoint`, SEL updates.
- [`perception/`](perception/README.md) — audio, vision and fusion, one folder each.
- [`playspace/`](playspace/README.md) — play-space model.
- [`robotbrain/`](robotbrain/README.md) — ChatScript, content modules and schedules, intents, contexts, idle/mentor/STAR, remote chat, users.
- [`system/`](system/README.md) — power, time, system events.
- [`telehealth/`](telehealth/README.md) — remote-puppet sessions.
- [`testing/`](testing/README.md) — test harness messages.
- [`unity/`](unity/README.md) — brain to face: CloudTTS, speech/SFX playback, markup, gaze, camera, console commands, status.
- [`wifiapp/`](wifiapp/README.md) — setup app: `QRCommand`, status, silent boot, shutdown, bricked.

---
📖 [Recovered protos](../README.md) · [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
