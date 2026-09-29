# 🗺️ Exploration map — coverage, what's explored, what's open

The status board for the reverse-engineering effort on firmware **v3.6.4-Zephyr / OTA v24.10.803**
(RK3288, Android 9). It answers three questions: how complete is each project goal, which parts of the
firmware have been examined (and by which doc), and what is still open. Short version: the protocol,
cloud, firmware and on-device brain are mapped end to end; what remains is mostly **bench work on a
physical unit** plus a few blobs a revival replaces by design. Pick the next thread from the ⬜/🟡 rows
and the [open items](#open-items-need-a-bench-unit-or-an-external-artifact).

**Legend:** ✅ documented in depth · 🟡 substantially covered, minor residue · ⬜ open · ⛔ deliberately
deferred (outside the three goals). "+ tool" means the finding is also executable in
[`tools/robot-toolkit/`](../../tools/robot-toolkit/) (`bus.py`, `cloud.py`, `markup.py`, `moxie-qr`,
`protoref`). ✅ means a coherent thread is covered in depth, not that nothing is left: the brain alone
has 2750 C# classes.

## By goal

### Goal ① — Custom firmware / software on the robot

| Layer | Status | Doc |
|---|---|---|
| Board wiring (I²C/UART/USB/display/PMIC/GPIO), from the DTB | ✅ | [device-tree](hardware/device-tree.md) |
| Partitions, AVB, A/B, OEM-unlock, code signing (3 keys), security posture | ✅ | [firmware-image](firmware/firmware-image.md) · [firmware-803-reference](firmware/firmware-803-reference.md) |
| Full file/app/binary manifest + hashes | ✅ | [firmware-inventory](firmware/firmware-inventory.md) |
| Boot chain, Launcher state machine, init services | ✅ | [boot-and-launcher](firmware/boot-and-launcher.md) |
| Android permissions + SELinux confinement | ✅ | [security-policy](firmware/security-policy.md) |
| Vendor HALs, kernel drivers, co-processor/radio firmware | ✅ | [hal-and-drivers](firmware/hal-and-drivers.md) |
| Power lifecycle + time/alarms (PowerStatePB, resume causes, XMOS recovery, TimeZone + UserAlarm) | ✅ + tool | [power-and-system-events](protocol/power-and-system-events.md) |
| On-device ZMQ bus + MAINAPP (brain ↔ Unity face/audio/camera) seam | ✅ + tool | [robot-ipc-protocol](protocol/robot-ipc-protocol.md) · [unity-mainapp-interface](protocol/unity-mainapp-interface.md) |
| Native boundary (P/Invoke `liblizzerface` MCU C API, JNI, out-of-process bus modules) | ✅ | [native-boundary](runtime/native-boundary.md) |
| Decision stack: input events (163) → NodeCanvas engine (45 trees) → 65 `RobotBT_*` nodes → action arbiter → task scheduler (44 outputs) | ✅ | [behavior-input-events](runtime/behavior-input-events.md) · [behavior-tree-engine](runtime/behavior-tree-engine.md) · [robot-actions](runtime/robot-actions.md) · [task-scheduler](runtime/task-scheduler.md) |
| Gaze/attention, perception fusion, turn-taking | ✅ + tool | [gaze-and-attention](runtime/gaze-and-attention.md) · [perception-fusion](protocol/perception-fusion.md) · [turn-taking](runtime/turn-taking.md) |
| Hardware: motors/sensors/LEDs/power rails, handling events; 3-processor firmware (RK3288 OTA · Lizard STM32 · XMOS DSP) | ✅ + tool | [hardware-map](hardware/hardware-map.md) · [perception-pipeline](runtime/perception-pipeline.md) |
| Physical flashing surface (maskrom/rockusb/fastboot/UART/JTAG) + FCC board map | ✅ | [hardware-access](hardware/hardware-access.md) · [fcc-teardown](hardware/fcc-teardown.md) |
| Runtime config surface (199 settings; keys + meaning, not defaults) | ✅ | [settings-schema](firmware/settings-schema.md) |
| Unity assets (inventory; per-object export open) + face-animation engine | ✅ | [unity-assets](firmware/unity-assets.md) · [unity-face-animation](runtime/unity-face-animation.md) |

### Goal ② — Put any AI inside Moxie

A compatible MQTT + REST server is the floor; answering the [`RemoteChat`](protocol/remote-chat-protocol.md)
seam with any brain is the ceiling ([why the brain is replaceable](runtime/native-boundary.md)).

| Layer | Status | Doc |
|---|---|---|
| TLS trust model (CA-validated, no pinning) | ✅ | [network-trust](protocol/network-trust.md) |
| Device auth (RS256 JWT), MQTT topics + `/commands/zmq` inject, REST `client-service`, built-in endpoint hosts, full session sequence | ✅ + tool | [cloud-protocol](protocol/cloud-protocol.md) |
| Repointing (`ServiceConfiguration`, `EndpointStore`, QR) | ✅ + tool | [cloud-protocol](protocol/cloud-protocol.md) · [qr-commands](protocol/qr-commands.md) |
| Config/telemetry data model (RobotCloudConfig down · RobotStatus/Packet up · LoggingPolicy) | ✅ + tool | [device-config-and-telemetry](protocol/device-config-and-telemetry.md) |
| RemoteChat RPC (output/actions/safety/metrics/ResultCodes) | ✅ + tool | [remote-chat-protocol](protocol/remote-chat-protocol.md) |
| Conversation: ChatScript + LLM, module format, volley API, SEL taxonomy, scheduling/recommender/rewards | ✅ + tool | [content-and-conversation](runtime/content-and-conversation.md) |
| Offline fallback + persisted brain state | ✅ + tool | [offline-and-brain-state](protocol/offline-and-brain-state.md) |
| Content delivery (dynamic AssetBundles) | ✅ | [content-delivery](runtime/content-delivery.md) |
| Behavior markup (24 verbs) | ✅ + tool | [behavior-markup](runtime/behavior-markup.md) |
| Imperative runtime control (volume/pacing/listen/barge-in/reset) | ✅ + tool | [runtime-control](protocol/runtime-control.md) |
| Perception in/out: STT, TTS, vision; wake-word/VAD is fully on-device | ✅ | [perception-pipeline](runtime/perception-pipeline.md) |
| Telehealth / remote puppet | ✅ + tool | [telehealth](protocol/telehealth.md) |
| System status a server observes (Wi-Fi vs internet, STT/OTA health, unpair/disengage) | ✅ | [power-and-system-events](protocol/power-and-system-events.md) |
| Full protocol reference (382 messages / 84 enums) | ✅ + tool | [proto-catalog](protocol/proto-catalog.md) · [recovered-proto/](protocol/recovered-proto/) |
| Pairing crypto (phone side) | ✅ | [crypto-and-keys](phone/crypto-and-keys.md) · [qr-format](phone/qr-format.md) |

### Goal ③ — Revive old robots without disassembly

| Path | Status |
|---|---|
| **801+**: QR `endpoint_update` → `OPEN_MOXIE` / local, run your server | ✅ works ([qr-commands](protocol/qr-commands.md)) |
| **pre-801** over the air | ❌ endpoint hardcoded to Google, CA-validated cert ([network-trust](protocol/network-trust.md)) |
| Recovery sideload (SD/USB/adb) | ⚠️ exists, but gated on a signed OTA ([ota-and-recovery](firmware/ota-and-recovery.md)) |
| Button-triggered rockusb (download mode) → unsigned flash | ⭐ best low-open lead ([hardware-access](hardware/hardware-access.md)) |
| Setup-app status signal (`WifiAppReady`=100 → ready to scan; `WifiAppBricked` → needs physical recovery) | ✅ + tool ([qr-commands](protocol/qr-commands.md)) |

## By source surface

### Proto namespaces

17 `embodied.*` namespaces, 120 `.proto` files, 382 messages / 84 enums including nested types
(browse in [proto-catalog](protocol/proto-catalog.md)). The count column is **top-level types**
(383 in total) documented-by-name; status reflects real coverage.

| Status | Namespace | Top-level types | Where |
|:--:|---|:--:|---|
| ✅ | `embodied.robotbrain` | 100/100 | [remote-chat-protocol](protocol/remote-chat-protocol.md), [content-and-conversation](runtime/content-and-conversation.md), [runtime-control](protocol/runtime-control.md), [gaze-and-attention](runtime/gaze-and-attention.md) |
| ✅ | `embodied.unity` | 55/55 | [unity-mainapp-interface](protocol/unity-mainapp-interface.md), [unity-face-animation](runtime/unity-face-animation.md) |
| 🟡 | `embodied.logging` | 40/55 | [device-config-and-telemetry](protocol/device-config-and-telemetry.md), [cloud-protocol](protocol/cloud-protocol.md) (CloudQuery API, endpoint-host table for all 7 `IOTEndpoint` profiles, `cloud.json` persistence). Residue: RobotCloudConfig field messages, small pairing/report stubs |
| ✅ | `embodied.lizzerface` | 32/32 | [hardware-map](hardware/hardware-map.md), [native-boundary](runtime/native-boundary.md), [robot-ipc-protocol](protocol/robot-ipc-protocol.md) |
| ✅ | `embodied.perception.vision` | 30/30 | [perception-pipeline](runtime/perception-pipeline.md) (detection → tracking → pose, ShowState/ArUco) |
| ✅ | `embodied.perception.audio` | 22/22 | [perception-pipeline](runtime/perception-pipeline.md) (`zmqSTT` bus interface + STT events) |
| ✅ | `embodied.perception.fusion` | 17/17 | [perception-fusion](protocol/perception-fusion.md) |
| ✅ | `embodied.sys` | 12/12 | [power-and-system-events](protocol/power-and-system-events.md) |
| ✅ | `embodied.robotbrain.serialized` | 8/8 | [offline-and-brain-state](protocol/offline-and-brain-state.md) |
| ✅ | `embodied.telehealth` | 7/7 | [telehealth](protocol/telehealth.md) |
| ✅ | `embodied.power` | 5/5 | [power-and-system-events](protocol/power-and-system-events.md) |
| ✅ | `embodied.TTSMarkupTool` | 5/5 | [behavior-markup](runtime/behavior-markup.md) |
| ✅ | `embodied.robotbrain.tags` | 4/4 | [content-and-conversation](runtime/content-and-conversation.md) |
| ✅ | `embodied.Robot` | 1/1 | [behavior-input-events](runtime/behavior-input-events.md) |
| 🟡 | `embodied.testing` · `embodied.launcher` | 2/4 · 2/3 | [factory-provisioning](firmware/factory-provisioning.md) · [boot-and-launcher](firmware/boot-and-launcher.md) |
| ⛔ | `embodied.playspace` | 8/23 | peripheral to the three goals — deferred |

### Decompiled C# — `bo-android` (`Assembly-CSharp`, 2750 classes)

| Status | Subsystem | Where |
|:--:|---|---|
| ✅ | Face-animation engine (EBAnimGrinder, rig3 blendshapes, Eyeseme, visemes, Playables) | [unity-face-animation](runtime/unity-face-animation.md) |
| ✅ | Behavior engine, node vocabulary, input events, markup | [behavior-tree-engine](runtime/behavior-tree-engine.md), [behavior-input-events](runtime/behavior-input-events.md), [behavior-markup](runtime/behavior-markup.md) |
| ✅ | Gaze & attention; turn-taking & engagement | [gaze-and-attention](runtime/gaze-and-attention.md), [turn-taking](runtime/turn-taking.md) |
| ✅ | Conversation (ChatScript + LLM + RemoteChat + recommender + SEL) | [content-and-conversation](runtime/content-and-conversation.md), [remote-chat-protocol](protocol/remote-chat-protocol.md) |
| ✅ | Perception (vision + audio + fusion) | [perception-pipeline](runtime/perception-pipeline.md), [perception-fusion](protocol/perception-fusion.md) |
| ✅ | Cloud/MQTT/REST client, config/telemetry, offline fallback | [cloud-protocol](protocol/cloud-protocol.md), [device-config-and-telemetry](protocol/device-config-and-telemetry.md), [offline-and-brain-state](protocol/offline-and-brain-state.md) |
| ✅ | `EB*` game-task scheduler; RobotAction arbiter; content-activity shells (logic server-side, no on-device Python) | [task-scheduler](runtime/task-scheduler.md), [robot-actions](runtime/robot-actions.md) |
| ✅ | Native boundary (P/Invoke lizzerface/robinface/cerevoice/devset, JNI, bus modules) | [native-boundary](runtime/native-boundary.md) |
| ⬜ | `libbo-brain` native ML model weights (MXNet/sentiment/intent) — low priority: the module sits behind the bus and is replaced, not reimplemented | [native-boundary](runtime/native-boundary.md) |

### Disk images

| Status | Image | Where |
|:--:|---|---|
| ✅ | `system.img` — partitions, apps, daemons, SELinux, signing | [firmware-image](firmware/firmware-image.md), [firmware-inventory](firmware/firmware-inventory.md), [security-policy](firmware/security-policy.md) |
| ✅ | `boot.img` — kernel cmdline, init, launcher | [firmware-image](firmware/firmware-image.md), [boot-and-launcher](firmware/boot-and-launcher.md) |
| ✅ | `parts/vendor.img` — HALs, kernel drivers, DTB, co-processor blobs | [hal-and-drivers](firmware/hal-and-drivers.md), [device-tree](hardware/device-tree.md) |
| ✅ | `oem.img` — one BSP leftover | [firmware-image](firmware/firmware-image.md) |

### Apps and native libs

| Status | Surface | Where |
|:--:|---|---|
| ✅ | Embodied apps (bo-android 24.10.803, bo-wifi 24.6.100, factory 3005004-PP) | [firmware-inventory](firmware/firmware-inventory.md) |
| ✅ | `bo-wifi` setup app (QR grammar + status/brick protocol) | [qr-commands](protocol/qr-commands.md) |
| ✅ | Parent app (`com.embo.embodied.parent` 2.2.2) — REST + crypto | [rest-api](phone/rest-api.md), [app-structure](phone/app-structure.md), [crypto-and-keys](phone/crypto-and-keys.md) |
| ✅ | Factory/production-testing apps (finaltest 15-test catalog) | [factory-provisioning](firmware/factory-provisioning.md) |
| ✅ | Native libs: complete 30-`.so` roster + roles; `QRCommand` consumer resolved (logger/system-monitor) | [native-boundary](runtime/native-boundary.md#the-full-module-roster-what-each-remaining-bo-so-actually-is), [hal-and-drivers](firmware/hal-and-drivers.md) |
| ✅ | Unity assets + boot animation inventory | [unity-assets](firmware/unity-assets.md) |

## Clean-room self-sufficiency — what would go missing?

The test: if every Moxie binary, image and asset vanished, could someone rebuild the piece from the doc
alone?

**Self-sufficient (the doc is the data):**

| Piece | Captured in |
|---|---|
| Wire protocol — all 382 messages + the `.proto` IDL | [proto-catalog](protocol/proto-catalog.md) · [recovered-proto/](protocol/recovered-proto/) |
| Cloud/REST/MQTT — endpoints, topics, auth, built-in hosts, full session sequence | [cloud-protocol](protocol/cloud-protocol.md) |
| QR command space — closed grammar + native dispatch (`report`/`endpoint_update`/`om`) | [qr-commands](protocol/qr-commands.md) |
| Crypto & pairing — the one-seed key system | [crypto-and-keys](phone/crypto-and-keys.md) |
| Behavior markup — 24 verbs + the CereVoice SSML dialect | [behavior-markup](runtime/behavior-markup.md) |
| Face — the customizable-avatar visual spec + animation engine | [unity-face-animation](runtime/unity-face-animation.md) |
| Firmware — partitions, AVB, OTA, flashing; MCU (GOBY/HEX) + XMOS (DFU) updates | [firmware-image](firmware/firmware-image.md) · [hardware-map](hardware/hardware-map.md) · [perception-pipeline](runtime/perception-pipeline.md) |
| Hardware — device tree (DTS verbatim), board map, teardown | [device-tree](hardware/device-tree.md) · [hardware-map](hardware/hardware-map.md) · [fcc-teardown](hardware/fcc-teardown.md) |
| On-device bus — two-frame ZMQ framing + module roster | [robot-ipc-protocol](protocol/robot-ipc-protocol.md) · [native-boundary](runtime/native-boundary.md) |

**Gaps (data that would go missing):**

| Gap | Status | In scope? |
|---|---|---|
| `rig3` face-mesh blendshape list | ✅ captured (UnityPy): `rig3_faceMesh01` has exactly **10** shapes — see [unity-face-animation](runtime/unity-face-animation.md) · [unity-assets](firmware/unity-assets.md) | — |
| Eyeseme mood + viseme animation clips and the 45 `Bht_*` tree structures | ⚠ in the streamed **`rig3animations`** bundle, not the base APK (base-APK assets are inventoried) | Partly — a revival authors its own clips/trees from the [node catalog](runtime/behavior-tree-engine.md#the-node-catalog-the-65-robotbt_-nodes) + [markup verbs](runtime/behavior-markup.md); only the stock motion/personality would be lost |
| Default values of the 199 settings (keys + meaning captured) | ⚠ native `SettingSchema` init in `libbo-logger` | Yes for goal ①; goal ② runs on the robot's baked-in defaults |
| CereVoice voice data, ChatScript content modules, `libbo-brain` ML weights | ⚠ licensed/downloaded/native blobs | **No** — a clean-room build supplies its own TTS, content and brain |

The in-scope backlog is therefore the `rig3animations` bundle and the native settings defaults.
The self-sufficient facts are distilled into the standalone build contracts in
[`docs/architecture/`](../architecture/README.md).

## Open items (need a bench unit or an external artifact)

- [ ] **USB data port reachable without opening?** Decides whether download-mode/ADB/MTP paths are truly no-open.
- [ ] **Macro-button → boot-mode mapping** (long-press at power-on → recovery vs bootrom download) and the exact ADC thresholds (compiled into U-Boot; watch the serial console).
- [ ] **A genuine signed 803 `update.zip`** — unlocks recovery sideload / network-OTA revival.
- [~] **Teardown artifacts.** Done from the FCC filings ([fcc-teardown](hardware/fcc-teardown.md)): mainboard photos, full chip inventory, the `LOAD` (download-mode) / `RESET` / `POWER` buttons, the Lizard MCU `ISP & DEBUG` (SWD) + `RX`/`TX` header. Still open: the **SoC-side UART pad map**, the **maskrom test point**, and per-partition read-back.
- [ ] **Full Unity per-object export** (expressions, audio banks) and the streamed `rig3animations` bundle.
- [ ] `libbo-brain` native ML model formats (low priority; the `libsecrets` DB/FTP creds are already recovered — [factory-provisioning](firmware/factory-provisioning.md)).

---
📖 [Reverse-engineering index](README.md) · [Field guide](FIELD-GUIDE.md) · [Playbook](PLAYBOOK.md) · [Docs index](../README.md)
