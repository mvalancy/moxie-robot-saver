# 🗺️ Exploration map — what's been examined, what's open

> The **source-surface view** of the reverse-engineering effort: every part of the firmware — proto
> namespaces, decompiled C# subsystems, disk images, apps, native libs — with its exploration status and
> the doc that covers it. Where [`COVERAGE.md`](COVERAGE.md) answers *"how complete is each goal?"*, this
> answers *"what have we actually looked at, and what's left?"* **Maintained every iteration** — pick the
> next thread from the ⬜/🟡 rows here. Firmware: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9).
>
> Legend: ✅ documented in depth · 🟡 substantially covered, minor/scattered residue · ⬜ open ·
> ⛔ deliberately deferred (out of scope for the 3 goals).

## Protocol — the `embodied.*` proto namespaces

Recovered protos: **377 messages / 84 enums** across 17 namespaces (browsable in
[`proto-catalog.md`](protocol/proto-catalog.md); sources in [`recovered-proto/`](protocol/recovered-proto/)). Per-namespace
status (the count is a documented-by-name proxy; the status reflects real coverage):

| Status | Namespace | Msgs+enums | Where |
|:--:|---|:--:|---|
| ✅ | `embodied.unity` | 55/55 | [unity-mainapp-interface](protocol/unity-mainapp-interface.md) + [unity-face-animation](runtime/unity-face-animation.md) |
| ✅ | `embodied.perception.fusion` | 17/17 | [perception-fusion](protocol/perception-fusion.md) |
| ✅ | `embodied.sys` | 12/12 | [power-and-system-events](protocol/power-and-system-events.md) |
| ✅ | `embodied.power` | 5/5 | [power-and-system-events](protocol/power-and-system-events.md) |
| ✅ | `embodied.robotbrain.serialized` | 8/8 | [offline-and-brain-state](protocol/offline-and-brain-state.md) |
| ✅ | `embodied.robotbrain.tags` | 4/4 | [content-and-conversation](runtime/content-and-conversation.md) |
| ✅ | `embodied.telehealth` | 7/7 | [telehealth](protocol/telehealth.md) |
| ✅ | `embodied.TTSMarkupTool` | 5/5 | [behavior-markup](runtime/behavior-markup.md) |
| ✅ | `embodied.Robot` | 1/1 | [behavior-input-events](runtime/behavior-input-events.md) |
| 🟡 | `embodied.robotbrain` (100) | 91/100 | [remote-chat-protocol](protocol/remote-chat-protocol.md), [content-and-conversation](runtime/content-and-conversation.md), [runtime-control](protocol/runtime-control.md), [gaze-and-attention](runtime/gaze-and-attention.md) — residue: a few `ChatResponse`/`RemoteChat` sub-messages |
| 🟡 | `embodied.logging` (55) | 40/55 | [device-config-and-telemetry](protocol/device-config-and-telemetry.md), [cloud-protocol](protocol/cloud-protocol.md) (CloudQuery API now documented) — residue: RobotCloudConfig field-messages (covered as fields) + small pairing/report stubs |
| ✅ | `embodied.lizzerface` (32) | 32/32 | [hardware-map](hardware/hardware-map.md) (+ the [native MCU C API](runtime/native-boundary.md)), [robot-ipc-protocol](protocol/robot-ipc-protocol.md) |
| ✅ | `embodied.perception.vision` (30) | 30/30 | [perception-pipeline](runtime/perception-pipeline.md) (detection→tracking→pose wire schema + ShowState/ArUco) |
| ✅ | `embodied.perception.audio` (22) | 22/22 | [perception-pipeline](runtime/perception-pipeline.md) (incl. the `zmqSTT` bus interface + STT event stream) |
| 🟡 | `embodied.testing` (4) · `embodied.launcher` (3) | 2/4 · 2/3 | [factory-provisioning](firmware/factory-provisioning.md) · [boot-and-launcher](firmware/boot-and-launcher.md) |
| ⛔ | `embodied.playspace` (23) | 8/23 | peripheral to the 3 goals + Graphling-sensitive — **deliberately deferred** |

**Next proto threads (⬜/🟡 residue):** the `perception.audio` STT/Interrupt sub-messages and the
`perception.vision` detection messages (both covered conceptually in perception-pipeline). Most residue is
named-but-conceptual; playspace is out of scope.

## Decompiled C# — `bo-android` (Assembly-CSharp.dll, 2750 classes)

The brain + MAINAPP. Major subsystems and their exploration status:

| Status | Subsystem | Where |
|:--:|---|---|
| ✅ | Face-animation engine (EBAnimGrinder, rig3 blendshapes, Eyeseme, visemes, Playables) | [unity-face-animation](runtime/unity-face-animation.md) |
| ✅ | Behavior engine (NodeCanvas BT/FSM/Dialogue, Blackboard, `Bht_*` trees) | [behavior-tree-engine](runtime/behavior-tree-engine.md) |
| ✅ | Behavior input events (163 `InputEvent`s, Farmer→InputEngine) | [behavior-input-events](runtime/behavior-input-events.md) |
| ✅ | Behavior markup (`<mark cmd:…>` verbs) | [behavior-markup](runtime/behavior-markup.md) |
| ✅ | Gaze & attention (interest points, saccades, IK look-at) | [gaze-and-attention](runtime/gaze-and-attention.md) |
| ✅ | Turn-taking & engagement state machine | [turn-taking](runtime/turn-taking.md) |
| ✅ | Conversation (ChatScript + LLM + RemoteChat + recommender + SEL) | [content-and-conversation](runtime/content-and-conversation.md), [remote-chat-protocol](protocol/remote-chat-protocol.md) |
| ✅ | Perception (vision + audio + fusion world-model) | [perception-pipeline](runtime/perception-pipeline.md), [perception-fusion](protocol/perception-fusion.md) |
| ✅ | Cloud/MQTT/REST client + config/telemetry data-model | [cloud-protocol](protocol/cloud-protocol.md), [device-config-and-telemetry](protocol/device-config-and-telemetry.md) |
| ✅ | Offline fallback + persisted brain state | [offline-and-brain-state](protocol/offline-and-brain-state.md) |
| ✅ | Crypto & pairing | [crypto-and-keys](phone/crypto-and-keys.md), [pairing-and-robot](phone/pairing-and-robot.md) |
| ✅ | The `EB*` game-task / GT-manager runtime scheduler (priority + resource arbitration) | [task-scheduler](runtime/task-scheduler.md) |
| ✅ | Top-level action/activity arbiter (RobotAction scored selection) | [robot-actions](runtime/robot-actions.md) |
| ✅ | Content-module runtime: on-device activity shells + server-side execution (no on-device Python) | [robot-actions](runtime/robot-actions.md), [content-and-conversation](runtime/content-and-conversation.md), [remote-chat-protocol](protocol/remote-chat-protocol.md) |
| ✅ | Native boundary: P/Invoke (lizzerface/robinface/cerevoice/devset) + JNI + out-of-process bus modules | [native-boundary](runtime/native-boundary.md) |
| ⬜ | `libbo-brain` native ML model *weights* (MXNet/sentiment/intent) — but the modules are behind the bus (replaceable, not reimplemented), so low priority | context in [native-boundary](runtime/native-boundary.md) |

**Next C# threads:** the on-device C# subsystems are now mapped end-to-end (decision → action arbiter →
task scheduler → face engine). Remaining: `libbo-brain` native ML model formats (⬜ low priority) and the
proto residue below (lizzerface MCU I/O, perception.audio STT).

## Disk images

| Status | Image | Where |
|:--:|---|---|
| ✅ | `system.img` — partitions, apps, daemons, SELinux, signing | [firmware-image](firmware/firmware-image.md), [firmware-inventory](firmware/firmware-inventory.md), [firmware-manifest](firmware/firmware-manifest.md), [security-policy](firmware/security-policy.md) |
| ✅ | `boot.img` — kernel cmdline, init, launcher state machine | [firmware-image](firmware/firmware-image.md), [boot-and-launcher](firmware/boot-and-launcher.md) |
| ✅ | `parts/vendor.img` — HALs, kernel drivers, DTB, co-processor blobs | [hal-and-drivers](firmware/hal-and-drivers.md), [device-tree](hardware/device-tree.md) |
| ✅ | `oem.img` — one BSP leftover | [firmware-image](firmware/firmware-image.md#oemimg-one-telling-leftover-oemetcpackage_performancexml) |

## Apps & native libs

| Status | Surface | Where |
|:--:|---|---|
| ✅ | Embodied apps (versioned inventory: bo-android 24.10.803, bo-wifi 24.6.100, factory 3005004-PP) | [firmware-inventory](firmware/firmware-inventory.md) |
| ✅ | `bo-wifi` setup app (QR grammar + status/brick protocol) | [qr-commands](protocol/qr-commands.md) |
| ✅ | Parent app (`com.embo.embodied.parent` 2.2.2) — REST + crypto | [rest-api](phone/rest-api.md), [app-structure](phone/app-structure.md), [crypto-and-keys](phone/crypto-and-keys.md) |
| ✅ | Factory/production-testing apps (finaltest 15-test catalog) | [factory-provisioning](firmware/factory-provisioning.md) |
| ✅ | Native libs: XMOS DSP, `libbo-*`, CereVoice, ChatScript, libsecrets | [hal-and-drivers](firmware/hal-and-drivers.md), [perception-pipeline](runtime/perception-pipeline.md), [content-and-conversation](runtime/content-and-conversation.md), [factory-provisioning](firmware/factory-provisioning.md) |
| ✅ | Unity assets + bootanimation inventory | [unity-assets](firmware/unity-assets.md) |

## Hardware (needs a bench unit — tracked in COVERAGE)

Board-level map is done from the FCC filings ([fcc-teardown](hardware/fcc-teardown.md)) + DTB
([device-tree](hardware/device-tree.md)); the remaining open items (USB-without-open, macro-button ADC thresholds,
a signed 803 `update.zip`, SoC UART/maskrom pads) are **bench-only** and listed in
[`COVERAGE.md`](COVERAGE.md#open-items-need-a-bench-unit-or-an-external-artifact).

---
📖 [Field guide](FIELD-GUIDE.md) · [Coverage matrix](COVERAGE.md) · [Reverse-engineering index](README.md)
