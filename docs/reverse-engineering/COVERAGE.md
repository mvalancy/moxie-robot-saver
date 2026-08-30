# ✅ Coverage matrix — how complete is the deconstruction?

> A single view of what's documented for each goal, and the honest remaining gaps (mostly things that
> need a **bench unit**). Firmware under analysis: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9).
> 40 reverse-engineering docs + a validated toolkit; the map below is the table of contents by outcome.

## Goal ① — Build custom firmware / run custom software on the robot

| Layer | Status | Doc |
|---|---|---|
| Board wiring (I²C/UART/USB/display/PMIC/GPIO) | ✅ from the DTB | [device-tree](hardware/device-tree.md) |
| Partitions, AVB, A/B, OEM-unlock, security posture | ✅ | [firmware-image](firmware/firmware-image.md) · [firmware-803-reference](firmware/firmware-803-reference.md) |
| Full file/app/binary manifest + hashes | ✅ | [firmware-manifest](firmware/firmware-manifest.md) · [firmware-inventory](firmware/firmware-inventory.md) |
| Boot chain + Launcher state machine + init services | ✅ | [boot-and-launcher](firmware/boot-and-launcher.md) |
| Power lifecycle + time/alarms: PowerStatePB states, resume causes, XMOS recovery, suspend/keep-awake, TimeZone + UserAlarm wake | ✅ + tool | [power-and-system-events](protocol/power-and-system-events.md) · `bus.py` |
| Code signing (3 keys) & permissions | ✅ | [firmware-image](firmware/firmware-image.md#code-signing-app-trust) |
| Android permissions + SELinux confinement | ✅ | [security-policy](firmware/security-policy.md) |
| Vendor HALs + kernel drivers + co-processor/radio firmware | ✅ | [hal-and-drivers](firmware/hal-and-drivers.md) |
| On-device ZMQ bus (drive the robot) + client | ✅ + tool | [robot-ipc-protocol](protocol/robot-ipc-protocol.md) · `MoxieBus` |
| Native boundary (P/Invoke liblizzerface MCU C API, JNI, out-of-process bus modules) | ✅ decompiled | [native-boundary](runtime/native-boundary.md) |
| MAINAPP interface (brain↔Unity face/audio/camera seam, full embodied.unity map) | ✅ + tool | [unity-mainapp-interface](protocol/unity-mainapp-interface.md) · `bus.py` |
| Behavior input-event vocabulary (163 events, Farmers→InputEngine) | ✅ | [behavior-input-events](runtime/behavior-input-events.md) |
| Top-level action arbiter (scored: handling>affection>activity>idle; personality) | ✅ decompiled | [robot-actions](runtime/robot-actions.md) |
| Decision engine (NodeCanvas BT/FSM/Dialogue + Blackboard, 45 trees) | ✅ | [behavior-tree-engine](runtime/behavior-tree-engine.md) |
| Behavior-node vocabulary (the 65 RobotBT_* nodes trees are built from) | ✅ decompiled | [behavior-nodes](runtime/behavior-nodes.md) |
| Task scheduler (priority + resource arbitration over 44 outputs, RobotTaskPriority ladder) | ✅ decompiled | [task-scheduler](runtime/task-scheduler.md) |
| Gaze & attention (interest points, saccades, IK look-at, published Attention state machine) | ✅ + tool | [gaze-and-attention](runtime/gaze-and-attention.md) · `bus.py` |
| Perception fusion: the tracked world-model of people (FusedPeople, world/screen coords, events) | ✅ + tool | [perception-fusion](protocol/perception-fusion.md) · `bus.py` |
| Conversation turn-taking & engagement state machine | ✅ | [turn-taking](runtime/turn-taking.md) |
| Hardware map: motors/sensors/LEDs/power rails + semantic handling events (pickup/shake/tilt, IMU-noise gate) | ✅ + tool | [hardware-map](hardware/hardware-map.md) · `bus.py` |
| **3-processor firmware** (RK3288 OTA · Lizard STM32 · XMOS DSP) | ✅ incl. images | [hardware-map](hardware/hardware-map.md) · [perception-pipeline](runtime/perception-pipeline.md) |
| Physical flashing surface (maskrom/rockusb/fastboot/UART/JTAG) | ✅ | [hardware-access](hardware/hardware-access.md) |
| Runtime config surface (199 settings) | ✅ | [settings-schema](firmware/settings-schema.md) |
| Unity face/asset pipeline | ✅ inventory (full per-object export = open) | [unity-assets](firmware/unity-assets.md) |
| Face-animation engine (rig3 blendshapes, EBAnimGrinder, Eyeseme moods, visemes, blackboard) | ✅ decompiled | [unity-face-animation](runtime/unity-face-animation.md) |

## Goal ② — Put any AI inside Moxie (the *ghost in the shell*)

> More than a self-hosted backend: the goal is a full **brain transplant** — any AI (LLM, agent, your own
> model) wearing Moxie's body by answering the [`RemoteChat`](protocol/remote-chat-protocol.md) seam. A
> compatible MQTT+REST server is the *floor*; swapping the brain behind the bus is the *ceiling*
> ([why the brain is replaceable](runtime/native-boundary.md)).

| Layer | Status | Doc |
|---|---|---|
| TLS trust model (what cert a server needs) | ✅ | [network-trust](protocol/network-trust.md) |
| Robot device auth (RS256 JWT) | ✅ | [cloud-protocol](protocol/cloud-protocol.md#robot-authentication-device-identity) |
| MQTT topics + envelope + `/commands/zmq` inject | ✅ + tool | [cloud-protocol](protocol/cloud-protocol.md) · `cloud.py` |
| Config/telemetry data-model (RobotCloudConfig down · RobotStatus/Packet up · LoggingPolicy) | ✅ + tool | [device-config-and-telemetry](protocol/device-config-and-telemetry.md) · `cloud.py` |
| REST `client-service` (sessions, OTA, backups) + endpoints | ✅ | [cloud-protocol](protocol/cloud-protocol.md) |
| Repointing (`ServiceConfiguration`, `EndpointStore`, QR) | ✅ + tool | [cloud-protocol](protocol/cloud-protocol.md) · [qr-commands](protocol/qr-commands.md) · `moxie-qr` |
| Conversation: ChatScript + LLM + module format + volley API + SEL taxonomy (Pillars→Skills→Goals→Levels, ModuleTagData) | ✅ + tool | [content-and-conversation](runtime/content-and-conversation.md) · `cloud.py` |
| RemoteChat robot↔brain RPC (full response: output/actions/safety/metrics/ResultCodes) | ✅ + tool | [remote-chat-protocol](protocol/remote-chat-protocol.md) · `cloud.py` |
| Offline fallback + persisted brain state (FallbackInfo, CSData resume, recommender history) | ✅ + tool | [offline-and-brain-state](protocol/offline-and-brain-state.md) · `cloud.py` |
| Content delivery (dynamic AssetBundles: remote fetch, manifest, processors) | ✅ | [content-delivery](runtime/content-delivery.md) |
| Behavior markup (make it move while talking) | ✅ + tool | [behavior-markup](runtime/behavior-markup.md) · `markup.py` |
| Imperative runtime control (volume/pacing/listen/barge-in/reset live) | ✅ + tool | [runtime-control](protocol/runtime-control.md) · `bus.py` |
| Perception in/out: STT (Deepgram/local) · TTS (CloudTTS) · vision | ✅ | [perception-pipeline](runtime/perception-pipeline.md) |
| Wake-word/VAD (server doesn't handle it) | ✅ | [perception-pipeline](runtime/perception-pipeline.md#wake-word-vad-fully-on-device) |
| Scheduling/recommender/STAR/StarBits/mentor history | ✅ | [content-and-conversation](runtime/content-and-conversation.md#scheduling-progression-rewards-what-to-offer-next) |
| Telehealth / live remote puppet (TeleBrain protocol) | ✅ | [telehealth](protocol/telehealth.md) |
| System status a server observes (Wi-Fi vs internet, STT/OTA health, shutdown, unpair/disengage) | ✅ | [power-and-system-events](protocol/power-and-system-events.md) |
| Full protocol reference (382 msgs) + bindings | ✅ + tools | [proto-catalog](protocol/proto-catalog.md) · [recovered-proto/](protocol/recovered-proto/) · `protoref` |
| Pairing crypto (phone side) | ✅ | [crypto-and-keys](phone/crypto-and-keys.md) · [qr-format](phone/qr-format.md) |

## Goal ③ — Revive old pre-801 robots without disassembly

| Path | Status |
|---|---|
| **801+**: QR `endpoint_update` → OPEN_MOXIE/local, run your server | ✅ works ([qr-commands](protocol/qr-commands.md)) |
| **pre-801** over the air | ❌ endpoint hardcoded to Google, CA-validated cert ([network-trust](protocol/network-trust.md)) — can't redirect |
| Recovery sideload (SD/USB/adb) | ⚠️ exists, but **gated on a signed OTA** ([ota-and-recovery](firmware/ota-and-recovery.md)) |
| **Button-triggered rockusb (download mode) → unsigned flash** | ⭐ **best low-open lead** ([hardware-access](hardware/hardware-access.md#boot-mode-entry-reboot-reasons-keys)) |
| **Setup-app status signal** (`WifiAppReady`=100 → ready to scan a QR; `WifiAppBricked` → needs physical recovery) | ✅ + tool ([qr-commands](protocol/qr-commands.md) · `bus.py`) |

## Clean-room self-sufficiency — *what would go missing?*

The sharpest completeness test: **if every Moxie binary, image, and asset vanished, could someone rebuild
this piece from the doc alone?** A doc that only *points at* Moxie data isn't done. Where the stack stands:

**Self-sufficient — the doc *is* the data (rebuild from it):**

| Piece | Captured in |
|---|---|
| Wire protocol — all 376 messages + fields, + the `.proto` IDL | [proto-catalog](protocol/proto-catalog.md) · [recovered-proto/](protocol/recovered-proto/) |
| Cloud/REST/MQTT — endpoints, topics, auth, [built-in hosts](protocol/cloud-protocol.md#the-built-in-endpoint-hosts-baked-into-libbo-logger), [full session sequence](protocol/cloud-protocol.md#the-full-session-power-on-to-first-spoken-line) | [cloud-protocol](protocol/cloud-protocol.md) |
| QR command space — the closed grammar + native dispatch (`report`/`endpoint_update`/`om`) | [qr-commands](protocol/qr-commands.md) |
| Crypto & pairing — the one-seed key system, algorithms | [crypto-and-keys](phone/crypto-and-keys.md) |
| Behavior markup — all 24 command verbs + the CereVoice SSML dialect | [behavior-markup](runtime/behavior-markup.md) |
| Face — the [customizable-avatar visual spec](runtime/unity-face-animation.md#the-face-itself-a-customizable-avatar-clean-room-visual-spec) + the animation engine | [unity-face-animation](runtime/unity-face-animation.md) |
| Firmware — partitions, AVB, OTA, flashing; MCU (GOBY/HEX) + XMOS (DFU chain) updates | [firmware-image](firmware/firmware-image.md) · [hardware-map](hardware/hardware-map.md) · [perception-pipeline](runtime/perception-pipeline.md) |
| Hardware — the device-tree (DTS captured verbatim), board map, teardown | [device-tree](hardware/device-tree.md) · [hardware-map](hardware/hardware-map.md) |
| On-device bus — the ZMQ two-frame framing + module roster | [robot-ipc-protocol](protocol/robot-ipc-protocol.md) · [native-boundary](runtime/native-boundary.md) |

**Gaps — the doc references Moxie data that *would* go missing:**

| Gap | Status | In scope? |
|---|---|---|
| The `rig3` **face mesh + full blendshape list** (only ~8 shapes surface in the C#) | ⚠ needs AssetStudio on the asset bundle | Yes — for a *pixel-faithful* face; a clean-room build supplies its own art on the [same blendshape roles](runtime/unity-face-animation.md) |
| The **45 `Bht_*` tree structures** (names + the 65-node vocabulary are captured; the graphs aren't) | ⚠ NodeCanvas assets — needs AssetStudio/JSON export | Partly — a revival authors its *own* trees from the [node catalog](runtime/behavior-nodes.md); the *stock personality* would go missing |
| The **animation/gesture clip set** (`rig3animations`) | ⚠ asset bundle — needs AssetStudio | Partly — same as above |
| The 199 settings' **default values** (keys + meaning captured; defaults aren't) | ⚠ native `SettingSchema` init (libbo-logger) — deep native RE | Yes for goal ①; goal ② runs on the robot's baked-in defaults |
| CereVoice **voice data**, ChatScript/**content modules**, `libbo-brain` **ML weights** | ⚠ licensed/downloaded/native blobs | **No** — a clean-room build supplies its own TTS, content, and brain (the whole point of [goal ②](#goal-put-any-ai-inside-moxie-the-ghost-in-the-shell)) |

So the **in-scope** clean-room backlog is concentrated in one place: the **Unity asset bundle**
(blendshapes, `Bht_*` graphs, animation clips) — one AssetStudio pass would close most of it — plus the
native settings defaults. Everything else is either captured or is content a revival replaces by design.

## Open items (need a bench unit or an external artifact)

- [ ] **USB data port reachable without opening?** — decides whether the download-mode/ADB/MTP paths are truly "no-open".
- [ ] **Macro-button → mode mapping** (long-press at power-on → recovery vs bootrom-download) + exact ADC thresholds (compiled into U-Boot; watch serial console).
- [ ] **A genuine signed 803 `update.zip`** — unlocks recovery sideload / network-OTA revival.
- [~] **Teardown artifacts:** ✅ mainboard photos + full chip inventory + the `LOAD` (download-mode) / `RESET` / `POWER` buttons + the Lizard MCU `ISP & DEBUG` (SWD) + `RX`/`TX` header — all from the **FCC filings**, see [`fcc-teardown.md`](hardware/fcc-teardown.md). ❌ still open: **SoC-side UART pad map** and **maskrom test-point** (not visible in the FCC photos), and per-partition read-back (needs a unit).
- [ ] **Full Unity per-object export** (face mesh/expressions, audio banks) via AssetStudio.
- [ ] **libsecrets DB/FTP creds** already recovered; other native ML model formats (`libbo-brain`) unmapped (low priority).

---
📖 [Field guide](FIELD-GUIDE.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
