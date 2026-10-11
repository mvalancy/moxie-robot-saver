# 📁 embodied — Python bindings

Generated `*_pb2.py` bindings for the recovered packages, one per `.proto` in [`proto/embodied/`](../../proto/embodied/README.md), in the same folders.
Regenerate them with the `grpc_tools.protoc` command in the [toolkit README](../../README.md); never edit them by hand.

With `moxie_toolkit/` on `sys.path`, as the toolkit's CLI and tests arrange, a module imports by its folder: `from embodied.wifiapp import QRCommands_pb2`.
A message's full name follows its proto package instead, as in `embodied.unity.QRCommand`; use the full name on the bus.

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
📖 [moxie_toolkit](../README.md) · [Docs index](../../../../docs/README.md) · [Back to top](../../../../README.md)
