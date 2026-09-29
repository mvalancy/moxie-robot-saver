# 🧱 Firmware — the OS image, boot, security & flashing

The robot's own Android image (RK3288, Android 9), as shipped in **v3.6.4-Zephyr / OTA v24.10.803**:
what is on it, how it boots and updates, and how to flash custom firmware.

| Doc | What it covers |
|---|---|
| [`firmware-803-reference.md`](firmware-803-reference.md) | **Start here.** The version-stamped reference: identity, partition hashes, cmdline, fstab, properties, `bo-android` native libraries |
| [`firmware-inventory.md`](firmware-inventory.md) | Apps (15 Embodied APKs, signers, versions), binaries, the on-device ML stack, and whole-image file counts |
| [`firmware-image.md`](firmware-image.md) | Verified-boot chain and how to break it, OP-TEE/RPMB, the three signing keys, custom-firmware roadmap |
| [`flashing-runbook.md`](flashing-runbook.md) | The canonical step-by-step to flash custom firmware or revive a robot by reflashing |
| [`ota-and-recovery.md`](ota-and-recovery.md) | A/B OTA machinery, the signing gate, recovery sideload, and the no-open upgrade vectors |
| [`boot-and-launcher.md`](boot-and-launcher.md) | The Launcher state machine, component supervision, factory-test entry, the 98 init services |
| [`security-policy.md`](security-policy.md) | Permissions and SELinux: stock `priv_app` domain, 2 daemon domains, `emb_*` device labels, declared features |
| [`hal-and-drivers.md`](hal-and-drivers.md) | Stock HAL set (no Embodied HAL), in-tree drivers, XMOS DSP images and BCM4339 radio blobs |
| [`settings-schema.md`](settings-schema.md) | The 199 `SettingSchema` keys a server can tune |
| [`unity-assets.md`](unity-assets.md) | Unity 2020.3 face/HUD/effects assets and the boot animation |
| [`factory-provisioning.md`](factory-provisioning.md) | Factory apps, serial/part grammar, factory DB, test sequences, the cracked factory secrets |
| [`manifests/`](manifests/README.md) | Machine-readable file lists, hashes, init services and decompiled device trees |

---
📖 [Reverse-engineering index](../README.md) · [Exploration map](../EXPLORATION-MAP.md)
