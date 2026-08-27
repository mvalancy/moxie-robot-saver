# 🚦 Boot & lifecycle — the Launcher state machine

> **What this is.** How the robot boots into its experience and switches modes — the `me.embodied
> .services.Launcher` state machine that starts/stops the `bo-*` components. This is the app-level
> lifecycle *above* the Android boot in [`firmware-image.md`](firmware-image.md): what a custom
> firmware must replicate (goal #1), and what a stranded robot actually does (goal #3). From
> `bo-android`'s Java service layer (`Launcher`, `ServiceLauncher`).

## Components (`BOComponent`)

The Launcher supervises 12 components (mostly the native `libbo-*` + the Unity apps), starting/stopping
them per state:

| Component | What |
|---|---|
| `BO_DISPATCH` | the ZeroMQ message bus ([`robot-ipc-protocol.md`](robot-ipc-protocol.md)) — always up |
| `BO_LOGGER` / `BO_SYSMON` | logging + system monitor (temps, RSSI, health) |
| `BO_UPDATER` | **OTA** ([`ota-and-recovery.md`](ota-and-recovery.md)) — up even in config/recovery |
| `BO_BRAIN` | ChatScript + ML conversation brain |
| `BO_FUSION` / `BO_VISION` / `BO_AUDIO` | perception (people fusion, faces/QR, STT/TTS/XMOS) |
| `BO_MAINAPP` | the Unity face/experience (`bo-android`'s Unity) |
| `BO_CFGAPP` | the config/setup app = **`bo-wifi`** (QR scanning, pairing) |
| `BO_ANALYTICS` | analytics (gated by `FEA_ANALYTICS`) |
| `BO_XMOS_WD` | XMOS audio-DSP watchdog (gated by `FEA_XMOS_WATCHDOG`) |

## States (`LauncherState`)

```mermaid
stateDiagram-v2
  [*] --> STATE_INIT
  STATE_INIT --> STATE_STARTUP
  STATE_STARTUP --> STATE_CONFIG: not paired / offline
  STATE_STARTUP --> STATE_RUNNING: paired + online
  STATE_CONFIG --> STATE_RUNNING: paired via QR + online
  STATE_RUNNING --> STATE_CONFIG: lost internet on resume
  STATE_RUNNING --> STATE_SUSPEND --> STATE_LIGHT_SLEEP
  STATE_RUNNING --> STATE_RECOVERY: user-data recovery
  STATE_RUNNING --> STATE_TELEBRAIN: telehealth session
  STATE_STARTUP --> STATE_SILENT_REBOOT: apply OTA
  STATE_* --> STATE_SHUTDOWN
```

| State | Components up | Meaning |
|---|---|---|
| `STATE_INIT` / `STATE_STARTUP` | boot bring-up | early boot, splash |
| **`STATE_CONFIG`** | DISPATCH, LOGGER, SYSMON, **UPDATER**, **CFGAPP** | **QR-reading / setup** — `bo-wifi` scans pairing/Wi-Fi/debug QRs. **No brain.** |
| **`STATE_RUNNING`** | full stack incl. BRAIN, FUSION, VISION, AUDIO, MAINAPP | the normal experience |
| `STATE_RECOVERY` / `STATE_SILENT_RECOVERY` | + BRAIN, MAINAPP (no UPDATER-only) | user-data recovery (see below); silent = no animation |
| `STATE_SILENT_REBOOT` | DISPATCH, LOGGER, SYSMON, UPDATER, BRAIN | minimal set to **apply an OTA** then reboot |
| `STATE_TELEBRAIN` | perception + MAINAPP, **no BRAIN** | telehealth remote-brain session |
| `STATE_DEMO` | — | factory/retail demo loop |
| `STATE_LIGHT_SLEEP` / `STATE_SUSPEND` | trimmed | idle / low-power |
| `STATE_SHUTDOWN` | — | powering off |

### Two facts that matter for revival

1. **Offline ⇒ QR-reading.** On resume, if `FEA_RESUME_WAIT_NET` is set and the net check fails
   (`waitOnNetwork(20000)`), the Launcher logs *"Could not detect internet after resume. Returning to
   QR reading state"* and `RequestState(STATE_CONFIG)`. **A stranded robot sits in `STATE_CONFIG`
   scanning QR codes** — which is exactly why the re-home QR works on 801+ (and why the pre-801 block
   is purely the endpoint pinning, not the robot refusing to scan).
2. **The updater runs in config.** `BO_UPDATER` is active in `STATE_CONFIG` and `STATE_SILENT_REBOOT`,
   so OTA can proceed while unpaired/offline-then-reconnected — the basis for "point at a server, get
   updated."

## User-data recovery (`UserDataRecoveryMode`)

`RECOVER_CLOUD` (default) / `RECOVER_LOCAL` / `UPDATE_CLOUD` — restores a child's pairing + data from
cloud backup or the local `/sdcard/EmbodiedStaticData/PERSISTENT_DATA` (e.g. `rightpoint/user_key.pub`,
`brightness`, legacy `S3`). `STATE_RECOVERY` runs the brain + main app against recovered data.

## Factory test entry (`FactoryTest`)

`Launcher.OnFactoryTestRequest(i)` launches a factory app by index via an Android intent — the
privileged manufacturing tools ([`factory-provisioning.md`](factory-provisioning.md)):

| i | FactoryTest | Component |
|--:|---|---|
| — | `BURN_IN` | `me.embodied.productiontesting.burnintest/.ActivityBurnInTest` |
| — | `TUMMY_BACK` (QC) | `…productiontesting.qc/.ActivityQC` |
| — | `DEMO_MODE` | (retail demo loop) |
| — | `LIFE_TEST` | `…lifetest/.ActivityLifeTest` |
| — | `INTERNAL_ASSEMBLY` | `…internalassytest/.ActivityInternalAssyTest` |
| — | `FINAL_TEST` | `…finaltest/.ActivityFinalTest` |

The request arrives over the bus (a factory/service message), not a user QR — but it shows the hook a
custom build could use to launch privileged bring-up tools.

## For custom firmware

A replacement app layer must reproduce this supervision: bring up `BO_DISPATCH` (the bus) first, then
your equivalents of perception/brain/face, and honor the config↔running transition on
pairing/network. The component boundaries are clean process starts/stops, so you can replace one
component (e.g. swap `BO_BRAIN`) while keeping the rest — the minimal-invasive custom-personality path
in [`firmware-image.md`](firmware-image.md).

---
📖 [Reverse-engineering index](README.md) · [Firmware image](firmware-image.md) · [OTA & recovery](ota-and-recovery.md) · [Docs index](../README.md)
