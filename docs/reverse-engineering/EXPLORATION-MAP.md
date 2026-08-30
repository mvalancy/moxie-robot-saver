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
[`proto-catalog.md`](proto-catalog.md); sources in [`recovered-proto/`](recovered-proto/)). Per-namespace
status (the count is a documented-by-name proxy; the status reflects real coverage):

| Status | Namespace | Msgs+enums | Where |
|:--:|---|:--:|---|
| ✅ | `embodied.unity` | 55/55 | [unity-mainapp-interface](unity-mainapp-interface.md) + [unity-face-animation](unity-face-animation.md) |
| ✅ | `embodied.perception.fusion` | 17/17 | [perception-fusion](perception-fusion.md) |
| ✅ | `embodied.sys` | 12/12 | [power-and-system-events](power-and-system-events.md) |
| ✅ | `embodied.power` | 5/5 | [power-and-system-events](power-and-system-events.md) |
| ✅ | `embodied.robotbrain.serialized` | 8/8 | [offline-and-brain-state](offline-and-brain-state.md) |
| ✅ | `embodied.robotbrain.tags` | 4/4 | [content-and-conversation](content-and-conversation.md) |
| ✅ | `embodied.telehealth` | 7/7 | [telehealth](telehealth.md) |
| ✅ | `embodied.TTSMarkupTool` | 5/5 | [behavior-markup](behavior-markup.md) |
| ✅ | `embodied.Robot` | 1/1 | [behavior-input-events](behavior-input-events.md) |
| 🟡 | `embodied.robotbrain` (100) | 91/100 | [remote-chat-protocol](remote-chat-protocol.md), [content-and-conversation](content-and-conversation.md), [runtime-control](runtime-control.md), [gaze-and-attention](gaze-and-attention.md) — residue: a few `ChatResponse`/`RemoteChat` sub-messages |
| 🟡 | `embodied.logging` (55) | 37/55 | [device-config-and-telemetry](device-config-and-telemetry.md), [cloud-protocol](cloud-protocol.md) — residue: `Cloud`/`CloudQuery` sub-messages (covered conceptually) |
| 🟡 | `embodied.lizzerface` (32) | 26/32 | [hardware-map](hardware-map.md), [robot-ipc-protocol](robot-ipc-protocol.md) — residue: a few MCU face/motor input/output messages |
| 🟡 | `embodied.perception.vision` (30) | 14/30 | [perception-pipeline](perception-pipeline.md) — concepts fully covered; not every message named |
| 🟡 | `embodied.perception.audio` (22) | 14/22 | [perception-pipeline](perception-pipeline.md) — residue: `STT`/`Interrupt`/`zmqSTT` sub-messages |
| 🟡 | `embodied.testing` (4) · `embodied.launcher` (3) | 2/4 · 2/3 | [factory-provisioning](factory-provisioning.md) · [boot-and-launcher](boot-and-launcher.md) |
| ⛔ | `embodied.playspace` (23) | 8/23 | peripheral to the 3 goals + Graphling-sensitive — **deliberately deferred** |

**Next proto threads (⬜/🟡 residue):** the scattered `lizzerface` input/output messages (goal ① — driving
the MCU), then the `perception.audio` STT/Interrupt sub-messages. Most other residue is covered
conceptually; playspace is out of scope.

## Decompiled C# — `bo-android` (Assembly-CSharp.dll, 2750 classes)

The brain + MAINAPP. Major subsystems and their exploration status:

| Status | Subsystem | Where |
|:--:|---|---|
| ✅ | Face-animation engine (EBAnimGrinder, rig3 blendshapes, Eyeseme, visemes, Playables) | [unity-face-animation](unity-face-animation.md) |
| ✅ | Behavior engine (NodeCanvas BT/FSM/Dialogue, Blackboard, `Bht_*` trees) | [behavior-tree-engine](behavior-tree-engine.md) |
| ✅ | Behavior input events (163 `InputEvent`s, Farmer→InputEngine) | [behavior-input-events](behavior-input-events.md) |
| ✅ | Behavior markup (`<mark cmd:…>` verbs) | [behavior-markup](behavior-markup.md) |
| ✅ | Gaze & attention (interest points, saccades, IK look-at) | [gaze-and-attention](gaze-and-attention.md) |
| ✅ | Turn-taking & engagement state machine | [turn-taking](turn-taking.md) |
| ✅ | Conversation (ChatScript + LLM + RemoteChat + recommender + SEL) | [content-and-conversation](content-and-conversation.md), [remote-chat-protocol](remote-chat-protocol.md) |
| ✅ | Perception (vision + audio + fusion world-model) | [perception-pipeline](perception-pipeline.md), [perception-fusion](perception-fusion.md) |
| ✅ | Cloud/MQTT/REST client + config/telemetry data-model | [cloud-protocol](cloud-protocol.md), [device-config-and-telemetry](device-config-and-telemetry.md) |
| ✅ | Offline fallback + persisted brain state | [offline-and-brain-state](offline-and-brain-state.md) |
| ✅ | Crypto & pairing | [crypto-and-keys](crypto-and-keys.md), [pairing-and-robot](pairing-and-robot.md) |
| ✅ | The `EB*` game-task / GT-manager runtime scheduler (priority + resource arbitration) | [task-scheduler](task-scheduler.md) |
| ✅ | Top-level action/activity arbiter (RobotAction scored selection) | [robot-actions](robot-actions.md) |
| ✅ | Content-module runtime: on-device activity shells + server-side execution (no on-device Python) | [robot-actions](robot-actions.md), [content-and-conversation](content-and-conversation.md), [remote-chat-protocol](remote-chat-protocol.md) |
| ⬜ | `libbo-brain` native ML models (sentiment/intent/NLU on-device) | format unmapped (low priority) |

**Next C# threads:** the on-device C# subsystems are now mapped end-to-end (decision → action arbiter →
task scheduler → face engine). Remaining: `libbo-brain` native ML model formats (⬜ low priority) and the
proto residue below (lizzerface MCU I/O, perception.audio STT).

## Disk images

| Status | Image | Where |
|:--:|---|---|
| ✅ | `system.img` — partitions, apps, daemons, SELinux, signing | [firmware-image](firmware-image.md), [firmware-inventory](firmware-inventory.md), [firmware-manifest](firmware-manifest.md), [security-policy](security-policy.md) |
| ✅ | `boot.img` — kernel cmdline, init, launcher state machine | [firmware-image](firmware-image.md), [boot-and-launcher](boot-and-launcher.md) |
| ✅ | `parts/vendor.img` — HALs, kernel drivers, DTB, co-processor blobs | [hal-and-drivers](hal-and-drivers.md), [device-tree](device-tree.md) |
| ✅ | `oem.img` — one BSP leftover | [firmware-image](firmware-image.md#oemimg-one-telling-leftover-oemetcpackage_performancexml) |

## Apps & native libs

| Status | Surface | Where |
|:--:|---|---|
| ✅ | Embodied apps (versioned inventory: bo-android 24.10.803, bo-wifi 24.6.100, factory 3005004-PP) | [firmware-inventory](firmware-inventory.md) |
| ✅ | `bo-wifi` setup app (QR grammar + status/brick protocol) | [qr-commands](qr-commands.md) |
| ✅ | Parent app (`com.embo.embodied.parent` 2.2.2) — REST + crypto | [rest-api](rest-api.md), [app-structure](app-structure.md), [crypto-and-keys](crypto-and-keys.md) |
| ✅ | Factory/production-testing apps (finaltest 15-test catalog) | [factory-provisioning](factory-provisioning.md) |
| ✅ | Native libs: XMOS DSP, `libbo-*`, CereVoice, ChatScript, libsecrets | [hal-and-drivers](hal-and-drivers.md), [perception-pipeline](perception-pipeline.md), [content-and-conversation](content-and-conversation.md), [factory-provisioning](factory-provisioning.md) |
| ✅ | Unity assets + bootanimation inventory | [unity-assets](unity-assets.md) |

## Hardware (needs a bench unit — tracked in COVERAGE)

Board-level map is done from the FCC filings ([fcc-teardown](fcc-teardown.md)) + DTB
([device-tree](device-tree.md)); the remaining open items (USB-without-open, macro-button ADC thresholds,
a signed 803 `update.zip`, SoC UART/maskrom pads) are **bench-only** and listed in
[`COVERAGE.md`](COVERAGE.md#open-items-need-a-bench-unit-or-an-external-artifact).

---
📖 [Field guide](FIELD-GUIDE.md) · [Coverage matrix](COVERAGE.md) · [Reverse-engineering index](README.md)
