---
name: continuing-moxie-re
description: Continue the Moxie firmware deconstruction — the project-specific loop over its evidence base toward clean-room-complete docs and toolkit. Use when picking up the Moxie reverse-engineering where it left off, deciding what to document next, or writing v24.10.803-stamped findings into docs/reverse-engineering.
---

# Continuing the Moxie deconstruction

The Moxie-specific driver over the general method (`reverse-engineering-android-robots` + its technique
skills). Firmware under analysis: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9) — stamp every
robot-side page with it.

## Evidence base (under `work/`, one level up from the repo)
- Images: `work/firmware-re/{system.img,oem.img,parts/vendor.img,parts/boot.img}` (read with `debugfs`).
- Apps: `work/firmware-re/extract/apps/*.apk`; jadx at `work/tools/jadx/bin/jadx`.
- The brain: `work/firmware-re/extract/csharp/src-asm/Assembly-CSharp.decompiled.cs` — grep first.
- Native libs: inside each APK's `lib/armeabi-v7a/`; Ghidra at `work/tools/ghidra`; the venv (capstone,
  UnityPy, pyghidra) at `work/firmware-re/extract/csharp/.venv`.
- Recovered protos: `docs/reverse-engineering/protocol/recovered-proto/**`.

## The loop (every iteration)
1. Read `work/firmware-re/progress/PLAN.md` (status / next / blockers).
2. Check `docs/reverse-engineering/COVERAGE.md` (esp. its "Clean-room self-sufficiency" register) +
   `EXPLORATION-MAP.md` + the existing docs — **do not re-document**; pick the next genuinely unexplored thread.
3. RE with the right technique skill (`decompiling-android-apps` / `-native-arm-libraries` /
   `recovering-protobuf-schemas` / `extracting-unity-assets` / `mapping-robot-hardware`). Use the two
   lenses: **named-but-not-enumerated** and **clean-room sufficiency**.
4. Write detailed, `v24.10.803`-stamped findings into the right `docs/reverse-engineering/**` subfolder;
   extend `tools/robot-toolkit/` where a server/agent would use the finding.
5. Push it upward + rebuild + verify with `publishing-moxie-docs`.
6. Commit, push, update `PLAN.md`.

## The three goals to serve
1. Build custom firmware. 2. Client/server revival (the ghost-in-the-shell brain seam). 3. Revive pre-801
units without disassembly. `COVERAGE.md` tracks each; `FIELD-GUIDE.md` organizes the docs by them.

## Known-open in-scope gaps (as tracked in COVERAGE.md)
The streamed **`rig3animations`** Unity bundle (eyeseme/viseme clips + `Bht_*` graphs — needs a bench unit
or OTA content pull) and the **native settings defaults** (deep native RE). Most enumerable-set and
protocol/firmware threads are captured — if a tick finds nothing genuinely new, say so; don't pad a commit.
