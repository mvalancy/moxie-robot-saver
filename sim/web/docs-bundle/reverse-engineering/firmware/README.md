# 🧱 Firmware — the OS image, boot, security & flashing

The **OS image** — partitions, boot, security, HAL/drivers, OTA, flashing, and the app/asset inventory (building custom firmware).

- [`firmware-803-reference.md`](firmware-803-reference.md) — the **version-stamped reference**: identifiers, partition hashes, and the full app + native-lib inventory. All robot-side docs describe this build.
- [`firmware-image.md`](firmware-image.md) — RK3288 / Android 9 partition layout, verified boot (AVB), security posture, installed apps, and **how to unlock & flash custom firmware**.
- [`firmware-inventory.md`](firmware-inventory.md) — complete app + binary manifest (50 priv-app / 29 app / 334 bin), embodied vs stock.
- [`firmware-manifest.md`](firmware-manifest.md) — consolidated per-file manifest (2250 system / 507 vendor files) + machine-readable TSVs.
- [`boot-and-launcher.md`](boot-and-launcher.md) — the app-level **Launcher state machine** (config/QR-reading, running, recovery, factory test) and component supervision.
- [`security-policy.md`](security-policy.md) — the **Android permission + SELinux** surface: embodied apps are platform-signed (no privapp/seapp policy), the 2 custom daemon domains (`ledctrld`/`projectorfanpid`) + `emb_*` device labels, and the minimal declared hardware-feature set.
- [`hal-and-drivers.md`](hal-and-drivers.md) — the **vendor HAL set** (all stock — no embodied HAL), in-tree kernel drivers, and the **co-processor firmware blobs**: the two XMOS voice-DSP images (`xmosdfu.bin` + a VAD variant, with hashes) and the BCM4339 radio.
- [`settings-schema.md`](settings-schema.md) — the **199 `SettingSchema` keys** (the full runtime config surface a server can tune).
- [`ota-and-recovery.md`](ota-and-recovery.md) — the A/B OTA machinery, payload signing gate, and an **honest, tiered map of upgrade vectors** (no-open first, then USB/UART, then full teardown/flash) for reviving old robots.
- [`flashing-runbook.md`](flashing-runbook.md) — **step-by-step** to build/flash custom firmware and revive a robot by reflashing.
- [`unity-assets.md`](unity-assets.md) — the Unity 2020.3 face/HUD/effects asset inventory + the boot animation.
- [`factory-provisioning.md`](factory-provisioning.md) — the production-line apps, serial/part grammar, and the **factory secret** getters (and how to recover them).

---
📖 [Reverse-engineering index](../README.md) · [Coverage](../COVERAGE.md) · [Exploration map](../EXPLORATION-MAP.md)
