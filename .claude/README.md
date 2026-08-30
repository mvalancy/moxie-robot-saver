# 🤝 Shared Claude agents & skills

This folder ships **Claude-compatible agents and skills** with the repo, so the hard-won Moxie
knowledge is shared, not trapped on one laptop. Clone the repo, open it in
[Claude Code](https://claude.com/claude-code) (or any Claude Agent SDK harness), and you get an
expert helper for reviving and extending Moxie.

> 💚 This is to help kids everywhere get their robot back. Improve these and send a PR.

## Agents (`agents/`)
Specialists you can delegate to.
- **`moxie-revival-guide`** — walks a Moxie *owner* through bringing their robot back to life
  (firmware check → Wi-Fi QR → endpoint QR → talking), grounded in `docs/`.
- **`moxie-protocol-expert`** — a *developer's* deep expert on the reverse-engineered protocol (REST,
  crypto, pairing QR, MQTT/conversation) for extending the project.

## Skills (`skills/`)
Task recipes Claude (or you) can invoke by name. Distilled from reversing + reviving Moxie — but most are
**transferable to other Android-computer robots**. The narrative deep-dive that ties them together is
[`docs/reverse-engineering/PLAYBOOK.md`](../docs/reverse-engineering/PLAYBOOK.md).

**Using Moxie (owner / developer):**
- **`generate-pairing-qr`** — make a Wi-Fi pairing QR from the CLI or server.
- **`factory-reset-moxie`** — unpair or factory-reset a paired robot.
- **`find-moxie-on-lan`** — locate the robot's IP after it joins Wi-Fi.
- **`using-the-moxie-toolkit`** — script the recovered protocol from Python (QR codec, ZMQ `MoxieBus`, cloud MQTT/REST, protoref, markup).

**Reverse-engineering ANY Android robot** (generalized method — Moxie is the worked example):
- **`reverse-engineering-android-robots`** — the master playbook: the phases, the mindset, which skill when.
- **`unpacking-android-firmware`** — acquire + unpack images (payload/sparse/ext4/boot/AVB); inventory apps, libs, init, sysconfig, device-tree.
- **`decompiling-android-apps`** — DEX→Java (jadx) + Unity C# (`Assembly-CSharp`, Mono/IL2CPP, ilspycmd).
- **`decompiling-native-arm-libraries`** — `nm`/`strings` → capstone → **PyGhidra** (with the JRE-only, string-ref, and project-lock gotchas that cost real time).
- **`recovering-protobuf-schemas`** — reconstruct exact, wire-compatible `.proto` from the embedded `FileDescriptorProto`s.
- **`extracting-unity-assets`** — meshes/blendshapes/clips/textures via UnityPy.
- **`mapping-robot-hardware`** — device-tree, init graph, the multi-processor (SoC+MCU+DSP) layout + firmware-update paths.

**Operating a long project autonomously:**
- **`running-layered-session-loops`** — structure recurring, scoped, independent agent loops that make safe steady progress for days (what sustained this project's 322 commits).
- **`continuing-moxie-re`** — the Moxie-specific deep-work loop over its evidence base.
- **`publishing-moxie-docs`** — rebuild the docs bundle + run every guard so the tree stays consistent root-to-leaf.

## How they stay accurate
Every agent and skill points at the **source-of-truth docs** in this repo (`docs/reverse-engineering/`,
`docs/architecture/`, `docs/guides/`). When the docs improve, the helpers improve.

---
📖 [Back to top](../README.md)
