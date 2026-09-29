---
name: continuing-moxie-re
description: Continue the Moxie firmware deconstruction — the project-specific loop over its evidence base toward clean-room-complete docs and toolkit. Use when picking up the Moxie reverse-engineering where it left off, deciding what to document next, or writing v24.10.803-stamped findings into docs/reverse-engineering.
---

# Continuing the Moxie deconstruction

The Moxie-specific driver over the general method (`reverse-engineering-android-robots` + its technique
skills). Firmware under analysis: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9) — stamp every
robot-side page with it (`scripts/check-doc-consistency.py` reports pages that lack it). The method in
full: `docs/reverse-engineering/PLAYBOOK.md`.

## Evidence base (local workspace under `work/`, one level up from the repo — never committed)
- Images: `work/firmware-re/{system.img,oem.img,parts/vendor.img,parts/boot.img}` (read with `debugfs`).
- Apps: `work/firmware-re/extract/apps/*.apk`; jadx at `work/tools/jadx/bin/jadx`.
- The brain: `work/firmware-re/extract/csharp/src-asm/Assembly-CSharp.decompiled.cs` — grep first.
- Native libs: inside each APK's `lib/armeabi-v7a/`; Ghidra at `work/tools/ghidra`; the venv (capstone,
  UnityPy, pyghidra) at `work/firmware-re/extract/csharp/.venv`.
- In the repo: recovered protos at `docs/reverse-engineering/protocol/recovered-proto/`.

## The loop (every iteration)
1. Read `work/firmware-re/progress/PLAN.md` (status / next / blockers).
2. Check `docs/reverse-engineering/EXPLORATION-MAP.md` (coverage per goal, per source surface, the
   clean-room self-sufficiency register, open items) and the existing docs — **do not re-document**;
   pick the next genuinely open thread.
3. RE with the right technique skill (`decompiling-android-apps` / `decompiling-native-arm-libraries` /
   `recovering-protobuf-schemas` / `extracting-unity-assets` / `mapping-robot-hardware`). Use the two
   lenses: **named-but-not-enumerated** and **clean-room sufficiency**.
4. Write detailed, stamped findings into the right subfolder of `docs/reverse-engineering/`
   (`phone/`, `protocol/`, `runtime/`, `firmware/`, `hardware/`); extend `tools/robot-toolkit/` where a
   server or agent would use the finding.
5. Push it upward (subfolder README, RE README, exploration map) and verify with `publishing-moxie-docs`.
6. Commit, then update `PLAN.md`.

## The three goals
1. Custom firmware. 2. Client/server revival (the RemoteChat brain seam). 3. Revive pre-801 units without
disassembly. `EXPLORATION-MAP.md` tracks each; `FIELD-GUIDE.md` organizes the docs by them.

## Known open in-scope gaps (see the exploration map)
The streamed **`rig3animations`** Unity bundle (Eyeseme/viseme clips + `Bht_*` graphs — needs a unit or
an OTA content pull), the **native settings defaults** (deep native RE), and the bench-only hardware
items (USB reachability, macro-button ADC thresholds, SoC UART pads, a signed 803 `update.zip`). If an
iteration finds nothing genuinely new, say so; don't pad a commit.
