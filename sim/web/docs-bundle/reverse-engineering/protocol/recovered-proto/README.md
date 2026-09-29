# 📦 Recovered protobuf schemas

**120 `.proto` files** reconstructed from Moxie's on-robot binaries (firmware `v24.10.803`): the
message contract between the robot's modules and much of the cloud protocol. Field numbers, enum
values, packages and nesting are exact, so bindings generated from these files are wire-compatible
with stock firmware. Read the [message catalog](../proto-catalog.md) for a browsable listing and
[`robot-ipc-protocol.md`](../robot-ipc-protocol.md) for how the modules use them.

## Where they come from

These are not Embodied source. Every `protoc`-generated class embeds its serialized
`FileDescriptorProto`; the shipped Unity/Mono assemblies (`Embodied.Protos.dll`, `WifiApp.Protos.dll`,
`Assembly-CSharp.dll`) carry those descriptors as base64. Decoding each one
(`FileDescriptorProto.ParseFromString(base64_decode(blob))`) and printing it as IDL reproduces the
original file. Only comments and `option`s are lost.

An identical copy lives in [`tools/robot-toolkit/proto/`](../../../../tools/robot-toolkit/proto/); that
copy is the input for the toolkit's generated bindings and for the catalog generator.

```sh
protoc --python_out=. --proto_path=. $(find . -name '*.proto')   # or --cpp_out / --go_out / ...
```

## Layout

The folder is not always the package: `wifiapp/` files declare `embodied.unity`, and `system/`
holds both `embodied.sys` and `embodied.power`.

| Folder | Package(s) | What's in it | Catalog |
|---|---|---|---|
| `lizzerface/` | `embodied.lizzerface` | MCU protocol: motors, PID config, power rails, LED patterns, touch/switch/IMU/battery/servo events ([hardware map](../../hardware/hardware-map.md)) | [link](../proto-catalog.md#embodiedlizzerface) |
| `wifiapp/` | `embodied.unity` | Setup app: `QRCommand` (the [QR grammar](../qr-commands.md)), status, silent boot, shutdown, bricked | [link](../proto-catalog.md#embodiedunity) |
| `perception/audio/` | `embodied.perception.audio` | STT, wake word, DOA, XMOS config | [link](../proto-catalog.md#embodiedperceptionaudio) |
| `perception/vision/` | `embodied.perception.vision` | Faces, people, poses, QR detection | [link](../proto-catalog.md#embodiedperceptionvision) |
| `perception/fusion/` | `embodied.perception.fusion` | Fused people ([perception-fusion](../perception-fusion.md)) | [link](../proto-catalog.md#embodiedperceptionfusion) |
| `robotbrain/` | `embodied.robotbrain` (+ `.serialized`, `.tags`) | ChatScript, content modules and schedules, intents, contexts, idle/mentor/STAR, remote chat, users | [link](../proto-catalog.md#embodiedrobotbrain) |
| `robotbrain/serialized/` | `embodied.robotbrain.serialized` | Persisted brain state ([offline-and-brain-state](../offline-and-brain-state.md)) | [link](../proto-catalog.md#embodiedrobotbrainserialized) |
| `unity/` | `embodied.unity` (+ `embodied.Robot`, `embodied.TTSMarkupTool`) | Brain to face: CloudTTS, speech/SFX playback, markup, gaze, camera, console commands, status ([MAINAPP interface](../unity-mainapp-interface.md)) | [link](../proto-catalog.md#embodiedunity) |
| `logging/` | `embodied.logging` | Cloud config, backup/file sync, metrics, `IOTEndpoint`, SEL updates ([device config](../device-config-and-telemetry.md)) | [link](../proto-catalog.md#embodiedlogging) |
| `system/` | `embodied.sys`, `embodied.power` | Power, time, system events ([power-and-system-events](../power-and-system-events.md)) | [sys](../proto-catalog.md#embodiedsys) · [power](../proto-catalog.md#embodiedpower) |
| `launcher/` | `embodied.launcher` | Component state | [link](../proto-catalog.md#embodiedlauncher) |
| `playspace/` | `embodied.playspace` | Play-space model | [link](../proto-catalog.md#embodiedplayspace) |
| `telehealth/` | `embodied.telehealth` | Remote-puppet sessions ([telehealth](../telehealth.md)) | [link](../proto-catalog.md#embodiedtelehealth) |
| `testing/` | `embodied.testing` | Test harness messages | [link](../proto-catalog.md#embodiedtesting) |

## Cross-validation against OpenMoxie

The files that overlap with OpenMoxie's independently reverse-engineered protos agree field for
field: **17 messages, 99 fields, 3 enums, 19 enum values, zero differences**, including
`embodied.unity.QRCommand` (in `wifiapp/QRCommands.proto`) and `embodied.logging.IOTEndpoint`. Two
independent derivations agreeing exactly is strong evidence the schema is right. OpenMoxie's
`embodied/logging/Cloud2.proto` message `ServiceConfiguration2` is the same message as our
`embodied.logging.ServiceConfiguration` in `Cloud.proto` (a file name difference, not a gap).
Re-run from `tools/robot-toolkit/`:

```sh
python -m moxie_toolkit.validate_protos [path/to/openmoxie/site/hive/mqtt/protos]
```

## Caveats

- Files are rendered as `syntax = "proto3"` (the descriptors are proto3); `optional` means proto3
  field presence, not proto2 semantics.
- Cross-file `import`s use the original descriptor paths; keep the tree intact so `--proto_path=.`
  resolves them.

---
📖 [IPC protocol](../robot-ipc-protocol.md) · [Reverse-engineering index](../../README.md) · [Docs index](../../../README.md)
