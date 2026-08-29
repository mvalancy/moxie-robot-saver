# 📄 Firmware manifests (v3.6.4-Zephyr / OTA v24.10.803)

Machine-readable file inventories of the partition images, generated from read-only loop mounts.

- `system-files.tsv` — `size⇥path` for every file in `system.img` (2250 files, ~2085 MB).
- `vendor-files.tsv` — every file in `vendor.img` (507 files, ~1186 MB).
- `oem-files.tsv` — `oem.img` (4 files, ~81 MB; the boot animation).
- `embodied-sha256.tsv` — `sha256⇥size⇥path` for the Embodied-specific apps/binaries.

Regenerate: mount each image `-o loop,ro` and `find <mnt> -type f -printf '%s\t%P\n' | sort -k2`.
See [`../firmware-manifest.md`](../firmware-manifest.md) for the summary.
- [`init-services.tsv`](init-services.tsv) — all **98 Android `init` services** (name, class, binary, user, flags, source `.rc`). See [`../boot-and-launcher.md`](../boot-and-launcher.md#init-service-graph-native-daemons).
