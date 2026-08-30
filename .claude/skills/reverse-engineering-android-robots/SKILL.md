---
name: reverse-engineering-android-robots
description: Master playbook for reverse-engineering an Android-computer robot or appliance (RK/Qualcomm SoC + an Android app + often an MCU/DSP + a cloud protocol) toward clean-room docs and a revival toolkit. Use when starting to RE a new device like this, or to orient before diving into firmware/apps/native-libs/protocol/hardware. Moxie is the worked example.
---

# Reverse-engineering an Android-computer robot — the playbook

Distilled from fully reversing the **Moxie** robot (Embodied Inc., RK3288/Android 9): a clean-room
recovery of its firmware, protocol, and hardware, plus a working revival toolkit + server + simulator.
Many consumer robots/appliances follow the **same shape** — a Rockchip/Qualcomm SoC running Android,
one big **Unity/Android app** as the brain, one or more **microcontrollers** (motors/sensors) and a
**DSP** (audio), talking to a **cloud** over MQTT/REST. This playbook is that pattern; the technique
skills below are the steps.

## The mindset (this is what actually mattered)
- **Clean-room.** Work only from the shipped, freely-distributed binaries — observed facts + schemas.
  Ship no vendor source. Goal test: *if every original binary/asset vanished, could someone rebuild
  this piece from your doc alone?* Capture the data; don't point at it.
- **Confirmed vs inferred.** A symbol name proves a *capability*; the exact value/trigger usually needs
  the next tool tier. Never restate a guess as fact — escalate the tool or label it. (On Moxie, several
  "observed" values were flat wrong until decompiled.)
- **"Named but not enumerated."** Code and docs constantly *name* a mechanism (a command verb, an event,
  a config key, an error) but leave the actual **set** in the binary. Hunting that set (hardcoded
  `string[]` arrays, enums, dispatch tables) is where most of the high-value findings come from.
- **Escalate tools light → heavy.** Most questions fall to `grep`/`strings`/`nm`; reach for a decompiler
  only when data-flow defeats you. Don't start heavy.
- **Small, shippable, honest.** One thread per iteration; if nothing genuinely new, say so — don't pad.

## The phases (and the skill for each)
1. **Acquire + unpack the firmware** → partitions, a mounted/readable filesystem, extracted apps + native
   libs + init/sysconfig + device-tree. → `unpacking-android-firmware`
2. **Decompile the app layer** — DEX→Java and, for Unity apps, the C# `Assembly-CSharp` (Mono or IL2CPP);
   this is usually **the brain**, grep it first for anything managed. → `decompiling-android-apps`
3. **Decompile the native layer** — `nm`/`strings` → capstone → Ghidra/PyGhidra for the `.so` internals
   (dispatch logic, GOT-indirected strings, the hardware C API). → `decompiling-native-arm-libraries`
4. **Recover the wire protocol** — most such devices use **protobuf**; the serialized `FileDescriptorProto`
   is embedded in the binaries, so you can reconstruct the exact `.proto` and get wire-compatible
   bindings. → `recovering-protobuf-schemas`
5. **Extract the assets** (Unity) — meshes, animation clips, textures, behavior graphs from
   `sharedassets`/`level`/bundles. → `extracting-unity-assets`
6. **Map the hardware** — device-tree, the init service graph, the **multi-processor pattern** (SoC + MCU
   + DSP) and each one's firmware-update path (UART/DFU/USB). → `mapping-robot-hardware`
7. **Map the cloud/comms** — REST + MQTT (often the Google IoT-Core topic convention), the TLS trust model
   (CA vs pinning), device auth (a signed JWT), and the endpoint/relocation config. This is the seam a
   **self-hosted server** answers.
8. **Document + verify** — write clean-room, version-stamped findings into a navigable doc tree; guard
   links/anchors/consistency mechanically. → `publishing-moxie-docs` (adapt the guards to your repo).

## What "done" looks like (goals worth setting)
Frame everything toward three concrete revival goals — they keep the work honest and prioritized:
1. **Build custom firmware / run custom software on the device.**
2. **Client/server revival** — stand up a self-hosted backend the device talks to (the cloud seam).
3. **Revive units without disassembly** — the no-open paths (a re-homing QR, a config push, an OTA).

## Reuse what Moxie already produced
- Recovered protocol + toolkit: `tools/robot-toolkit/` (see `using-the-moxie-toolkit`).
- A working self-hosted server + MQTT broker + AI seam: `server/`, `mqtt/`, `ai/`.
- A browser simulator (SIL) that speaks the real protocol: `sim/`.
- The full clean-room docs: `docs/reverse-engineering/` (start at its `README.md` + `METHODOLOGY.md`).
For a different robot, the *code* won't transfer but the **method, tool recipes, and doc discipline do**.
