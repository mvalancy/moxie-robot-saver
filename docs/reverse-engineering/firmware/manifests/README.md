# 📄 Firmware manifests (v3.6.4-Zephyr / OTA v24.10.803)

Machine-readable data behind the firmware docs, generated from read-only loop mounts of the partition
images. The human-readable summary is in [`../firmware-inventory.md`](../firmware-inventory.md#file-manifest-every-file-by-the-numbers).

| File | Contents |
|---|---|
| [`system-files.tsv`](system-files.tsv) | `size⇥path` for every file in `system.img` (2,250 files, ~2,085 MB) |
| [`vendor-files.tsv`](vendor-files.tsv) | every file in `vendor.img` (507 files, ~1,186 MB) |
| [`oem-files.tsv`](oem-files.tsv) | `oem.img` (4 files, ~81 MB; the boot animation) |
| [`embodied-sha256.tsv`](embodied-sha256.tsv) | `sha256⇥size⇥path` for the Embodied-specific apps/binaries |
| [`embodied-apps.tsv`](embodied-apps.tsv) | the 15 Embodied APKs: package, versionName/Code, targetSdk, signer ([table](../firmware-inventory.md#embodied-apps-the-ones-that-make-it-a-moxie)) |
| [`init-services.tsv`](init-services.tsv) | all 98 Android `init` services: name, class, binary, user, flags, source `.rc` ([summary](../boot-and-launcher.md#init-service-graph-native-daemons)) |
| [`rk3288-robot-gen1p5.dts`](rk3288-robot-gen1p5.dts) | the board device tree decompiled from `boot.img` ([summary](../../hardware/device-tree.md)) |
| [`uboot-control.dts`](uboot-control.dts) | U-Boot's control DTB, a stripped `Evb-RK3288` with no `adc-keys` node |

Regenerate a file list: mount each image `-o loop,ro`, then `find <mnt> -type f -printf '%s\t%P\n' | sort -k2`.

---
📖 [Firmware](../README.md) · [Reverse-engineering index](../../README.md)
