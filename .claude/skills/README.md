# 🛠️ Skills

Task skills (Claude Code `.claude/skills/`). Each folder holds a `SKILL.md` — YAML frontmatter (`name`,
`description`) plus the steps — and a short `README.md`. The method behind the reverse-engineering
skills is [`docs/reverse-engineering/PLAYBOOK.md`](../../docs/reverse-engineering/PLAYBOOK.md).

## Using Moxie (owner / developer)

| Skill | Use it to |
|---|---|
| [`generate-pairing-qr`](generate-pairing-qr/) | Make a Wi-Fi pairing QR (`"PA"`+protobuf) from the CLI or the local server. |
| [`find-moxie-on-lan`](find-moxie-on-lan/) | Locate a Moxie's IP after it joins Wi-Fi. |
| [`factory-reset-moxie`](factory-reset-moxie/) | Unpair or factory-reset a robot before fresh setup. |
| [`using-the-moxie-toolkit`](using-the-moxie-toolkit/) | Script the recovered protocol from Python (QR codec, `MoxieBus`, cloud helpers, protoref, markup). |

## Reverse-engineering any Android robot (Moxie is the worked example)

| Skill | Use it to |
|---|---|
| [`reverse-engineering-android-robots`](reverse-engineering-android-robots/) | Orient: the phases, the principles, which skill when. |
| [`unpacking-android-firmware`](unpacking-android-firmware/) | Acquire and unpack images (payload/sparse/ext4/boot/AVB); inventory apps, libs, init, device tree. |
| [`decompiling-android-apps`](decompiling-android-apps/) | DEX → Java (jadx) and Unity C# (`Assembly-CSharp`, Mono/IL2CPP, ilspycmd). |
| [`decompiling-native-arm-libraries`](decompiling-native-arm-libraries/) | `nm`/`strings` → capstone → PyGhidra, with the gotchas that cost real time. |
| [`recovering-protobuf-schemas`](recovering-protobuf-schemas/) | Rebuild exact `.proto` files from embedded `FileDescriptorProto`s. |
| [`extracting-unity-assets`](extracting-unity-assets/) | Pull meshes/blendshapes/clips/textures with UnityPy. |
| [`mapping-robot-hardware`](mapping-robot-hardware/) | Device tree, init graph, the SoC + MCU + DSP layout and update paths. |

## Operating a long project autonomously

| Skill | Use it to |
|---|---|
| [`running-layered-session-loops`](running-layered-session-loops/) | Structure recurring, scoped agent loops that make safe progress for days. |
| [`continuing-moxie-re`](continuing-moxie-re/) | Run the Moxie-specific deep-work loop over its evidence base. |
| [`publishing-moxie-docs`](publishing-moxie-docs/) | Rebuild the docs bundle and run every doc guard before committing. |

---
📖 [Shared agents & skills](../README.md) · [Back to top](../../README.md)
