# ✅ Coverage matrix — how complete is the deconstruction?

> A single view of what's documented for each goal, and the honest remaining gaps (mostly things that
> need a **bench unit**). Firmware under analysis: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9).
> 28 reverse-engineering docs + a validated toolkit; the map below is the table of contents by outcome.

## Goal ① — Build custom firmware / run custom software on the robot

| Layer | Status | Doc |
|---|---|---|
| Board wiring (I²C/UART/USB/display/PMIC/GPIO) | ✅ from the DTB | [device-tree](device-tree.md) |
| Partitions, AVB, A/B, OEM-unlock, security posture | ✅ | [firmware-image](firmware-image.md) · [firmware-803-reference](firmware-803-reference.md) |
| Full file/app/binary manifest + hashes | ✅ | [firmware-manifest](firmware-manifest.md) · [firmware-inventory](firmware-inventory.md) |
| Boot chain + Launcher state machine + init services | ✅ | [boot-and-launcher](boot-and-launcher.md) |
| Code signing (3 keys) & permissions | ✅ | [firmware-image](firmware-image.md#code-signing--app-trust) |
| Android permissions + SELinux confinement | ✅ | [security-policy](security-policy.md) |
| Vendor HALs + kernel drivers + co-processor/radio firmware | ✅ | [hal-and-drivers](hal-and-drivers.md) |
| On-device ZMQ bus (drive the robot) + client | ✅ + tool | [robot-ipc-protocol](robot-ipc-protocol.md) · `MoxieBus` |
| Behavior input-event vocabulary (163 events, Farmers→InputEngine) | ✅ | [behavior-input-events](behavior-input-events.md) |
| Decision engine (NodeCanvas BT/FSM/Dialogue + Blackboard, 45 trees) | ✅ | [behavior-tree-engine](behavior-tree-engine.md) |
| Gaze & attention (interest points, saccades, IK look-at) | ✅ | [gaze-and-attention](gaze-and-attention.md) |
| Conversation turn-taking & engagement state machine | ✅ | [turn-taking](turn-taking.md) |
| Hardware map: motors/sensors/LEDs/power rails | ✅ | [hardware-map](hardware-map.md) |
| **3-processor firmware** (RK3288 OTA · Lizard STM32 · XMOS DSP) | ✅ incl. images | [hardware-map](hardware-map.md) · [perception-pipeline](perception-pipeline.md) |
| Physical flashing surface (maskrom/rockusb/fastboot/UART/JTAG) | ✅ | [hardware-access](hardware-access.md) |
| Runtime config surface (199 settings) | ✅ | [settings-schema](settings-schema.md) |
| Unity face/asset pipeline | ✅ inventory (full per-object export = open) | [unity-assets](unity-assets.md) |

## Goal ② — Client/server revival (self-hosted backend)

| Layer | Status | Doc |
|---|---|---|
| TLS trust model (what cert a server needs) | ✅ | [network-trust](network-trust.md) |
| Robot device auth (RS256 JWT) | ✅ | [cloud-protocol](cloud-protocol.md#robot-authentication-device-identity) |
| MQTT topics + envelope + `/commands/zmq` inject | ✅ + tool | [cloud-protocol](cloud-protocol.md) · `cloud.py` |
| REST `client-service` (sessions, OTA, backups) + endpoints | ✅ | [cloud-protocol](cloud-protocol.md) |
| Repointing (`ServiceConfiguration`, `EndpointStore`, QR) | ✅ + tool | [cloud-protocol](cloud-protocol.md) · [qr-commands](qr-commands.md) · `moxie-qr` |
| Conversation: ChatScript + LLM + module format + volley API | ✅ | [content-and-conversation](content-and-conversation.md) |
| Content delivery (dynamic AssetBundles: remote fetch, manifest, processors) | ✅ | [content-delivery](content-delivery.md) |
| Behavior markup (make it move while talking) | ✅ + tool | [behavior-markup](behavior-markup.md) · `markup.py` |
| Perception in/out: STT (Deepgram/local) · TTS (CloudTTS) · vision | ✅ | [perception-pipeline](perception-pipeline.md) |
| Wake-word/VAD (server doesn't handle it) | ✅ | [perception-pipeline](perception-pipeline.md#wake-word--vad-fully-on-device) |
| Scheduling/recommender/STAR/StarBits/mentor history | ✅ | [content-and-conversation](content-and-conversation.md#scheduling-progression--rewards-what-to-offer-next) |
| Telehealth / live remote puppet | ✅ | [content-and-conversation](content-and-conversation.md#telehealth--remote-puppet-mode) |
| Full protocol reference (382 msgs) + bindings | ✅ + tools | [proto-catalog](proto-catalog.md) · [recovered-proto/](recovered-proto/) · `protoref` |
| Pairing crypto (phone side) | ✅ | [crypto-and-keys](crypto-and-keys.md) · [qr-format](qr-format.md) |

## Goal ③ — Revive old pre-801 robots without disassembly

| Path | Status |
|---|---|
| **801+**: QR `endpoint_update` → OPEN_MOXIE/local, run your server | ✅ works ([qr-commands](qr-commands.md)) |
| **pre-801** over the air | ❌ endpoint hardcoded to Google, CA-validated cert ([network-trust](network-trust.md)) — can't redirect |
| Recovery sideload (SD/USB/adb) | ⚠️ exists, but **gated on a signed OTA** ([ota-and-recovery](ota-and-recovery.md)) |
| **Button-triggered rockusb (download mode) → unsigned flash** | ⭐ **best low-open lead** ([hardware-access](hardware-access.md#boot-mode-entry-reboot-reasons--keys)) |

## Open items (need a bench unit or an external artifact)

- [ ] **USB data port reachable without opening?** — decides whether the download-mode/ADB/MTP paths are truly "no-open".
- [ ] **Macro-button → mode mapping** (long-press at power-on → recovery vs bootrom-download) + exact ADC thresholds (compiled into U-Boot; watch serial console).
- [ ] **A genuine signed 803 `update.zip`** — unlocks recovery sideload / network-OTA revival.
- [~] **Teardown artifacts:** ✅ mainboard photos + full chip inventory + the `LOAD` (download-mode) / `RESET` / `POWER` buttons + the Lizard MCU `ISP & DEBUG` (SWD) + `RX`/`TX` header — all from the **FCC filings**, see [`fcc-teardown.md`](fcc-teardown.md). ❌ still open: **SoC-side UART pad map** and **maskrom test-point** (not visible in the FCC photos), and per-partition read-back (needs a unit).
- [ ] **Full Unity per-object export** (face mesh/expressions, audio banks) via AssetStudio.
- [ ] **libsecrets DB/FTP creds** already recovered; other native ML model formats (`libbo-brain`) unmapped (low priority).

---
📖 [Field guide](FIELD-GUIDE.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
