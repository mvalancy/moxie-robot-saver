# 🛠️ Skills

Task skills (Claude Code `.claude/skills`) — each folder holds a `SKILL.md` Claude invokes by name.
Grouped by audience; the narrative that ties the reverse-engineering ones together is
[`../../docs/reverse-engineering/PLAYBOOK.md`](../../docs/reverse-engineering/PLAYBOOK.md).

## Using Moxie (owner / developer)
- [`generate-pairing-qr/`](generate-pairing-qr/) — make a Wi-Fi pairing QR (the `"PA"`+protobuf code the
  robot's camera scans), from the CLI or the local server.
- [`find-moxie-on-lan/`](find-moxie-on-lan/) — locate a Moxie's IP on the LAN after it joins Wi-Fi.
- [`factory-reset-moxie/`](factory-reset-moxie/) — unpair / factory-reset a robot stuck on the old
  Embodied cloud or another account, before fresh setup.
- [`using-the-moxie-toolkit/`](using-the-moxie-toolkit/) — script the recovered protocol from Python
  (QR codec, ZMQ `MoxieBus`, cloud MQTT/REST helpers, protoref, markup).

## Reverse-engineering ANY Android robot (Moxie is the worked example)
- [`reverse-engineering-android-robots/`](reverse-engineering-android-robots/) — the master playbook:
  the phases, the mindset, which skill when.
- [`unpacking-android-firmware/`](unpacking-android-firmware/) — acquire + unpack images
  (payload/sparse/ext4/boot/AVB); inventory apps, libs, init, sysconfig, device-tree.
- [`decompiling-android-apps/`](decompiling-android-apps/) — DEX→Java (jadx) + Unity C#
  (`Assembly-CSharp`, Mono/IL2CPP, ilspycmd).
- [`decompiling-native-arm-libraries/`](decompiling-native-arm-libraries/) — `nm`/`strings` → capstone →
  **PyGhidra**, with the JRE-only / string-ref / project-lock gotchas that cost real time.
- [`recovering-protobuf-schemas/`](recovering-protobuf-schemas/) — reconstruct exact, wire-compatible
  `.proto` from the embedded `FileDescriptorProto`s.
- [`extracting-unity-assets/`](extracting-unity-assets/) — meshes/blendshapes/clips/textures via UnityPy.
- [`mapping-robot-hardware/`](mapping-robot-hardware/) — device-tree, the init graph, the multi-processor
  (SoC+MCU+DSP) layout + firmware-update paths.

## Operating a long project autonomously
- [`running-layered-session-loops/`](running-layered-session-loops/) — structure recurring, scoped,
  independent agent loops that make safe steady progress for days (what sustained this project).
- [`continuing-moxie-re/`](continuing-moxie-re/) — the Moxie-specific deep-work loop over its evidence base.
- [`publishing-moxie-docs/`](publishing-moxie-docs/) — rebuild the docs bundle + run every guard so the
  tree stays consistent root-to-leaf.

---
📖 [Shared agents & skills](../README.md) · [Back to top](../../README.md)
