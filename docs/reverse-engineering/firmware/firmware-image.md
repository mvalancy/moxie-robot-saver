# 🧱 Moxie firmware image — the robot's own Android system

This is how the **on-robot firmware** (not the phone app) is put together: the boot and verified-boot
chain, the TEE, code signing, and what that means for running custom software on the robot. It was
reconstructed by mounting and `debugfs`-reading the factory partition images of **`v3.6.4-Zephyr` / OTA
`v24.10.803`**. Identifiers, partition hashes, cmdline, fstab and properties are in the
[firmware reference](firmware-803-reference.md). The flash procedure is in the [flashing runbook](flashing-runbook.md).

**Bottom line:** custom code is very achievable. The RK3288 has maskrom/loader mode, and AVB can be
disabled by flashing a `--disable-verification` `vbmeta` from below the bootloader. The real work is
not a locked bootloader. It is rebuilding a `system`/`vendor` that still drives Moxie's non-standard
hardware: the DLP projector face, the PCA963x LED array, the "Lizard" motor/sensor MCU and the XMOS audio DSP.

The analyzed build is newer than the pre-801 Google-IoT unit on the bench
([live-hardware-debug](../../debugging/live-hardware-debug.md)).

## TL;DR for going custom

| Question | Answer |
|---|---|
| SoC / arch | **Rockchip RK3288**, ARMv7 (`armeabi-v7a`, 32-bit) |
| OS | **Android 9 (Pie)**, API 28, security patch **2019-04-05**, `user`/`release-keys` |
| Boot | **A/B seamless**, **system-as-root** |
| Verified boot | **AVB 1.1**, `androidboot.veritymode=enforcing`; system mounted `ro` with `wait,avb,slotselect` |
| Secure props | `ro.secure=1`, `ro.adb.secure=1`, `ro.debuggable=0` |
| **OEM unlock** | `ro.oem_unlock_supported=1`, **but** unlock is **AVB-ATX attestation** (`libavb_atx`, challenge-response needing Embodied's Product Attestation Key). Maskrom/rockusb bypasses it |
| Data | `/data` is **f2fs, forceencrypt** (`forceencrypt=/cache/key_file`) |
| Flashing | Rockchip maskrom/loader → `rkdeveloptool` ([runbook](flashing-runbook.md)) |

Partition sizes, contents and SHA-256s: [firmware reference](firmware-803-reference.md#partition-images).

### `oem.img` — one telling leftover (`/oem/etc/package_performance.xml`)

Besides the boot animation, the OEM partition carries a **202-byte** `package_performance.xml`. This is a
**stock Rockchip BSP** feature that boosts CPU/GPU clocks when a listed package runs. On this image it
lists **only AnTuTu**, verbatim:

```xml
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<performance-package>
<app package="com.antutu.ABenchMark" mode="1"/>
<app package="com.antutu.benchmark.full" mode="1"/>
</performance-package>
```

Neither package is installed (`/system/app` has no `antutu*`), so the file is dormant vendor
boilerplate. It confirms the base OS is an **unmodified RK3288 Android-9 BSP** (brand/manufacturer
`rockchip`) with the `bo-*` apps layered on top. A custom build inherits it and can drop or repurpose it.

## The verified-boot chain (and how to break it for custom code)

1. **maskrom → SPL/U-Boot** (`uboot.img`) verifies via Rockchip's loader signature.
2. **U-Boot → AVB** reads `vbmeta.img` (`AVB0` magic, `avbtool 1.1.0`) and checks the hashtree
   descriptors covering `system` and `vendor`. With `androidboot.veritymode=enforcing`, a hash mismatch
   **hard-stops** the boot. U-Boot's `bootcmd` then falls back to `rockusb` and `fastboot`
   ([hardware-access](../hardware/hardware-access.md#the-rockchip-boot-download-modes-rk3288)).
3. **Kernel + system-as-root init** mounts `system` read-only at `/`, dm-verity backed by the AVB hashtree.

To run a modified `system`/`vendor` you must defeat step 2. Three ways, easiest first:

- **Flash a "disabled" vbmeta.** `avbtool make_vbmeta_image --flags 2 --padding_size 4096 -o vbmeta_disabled.img`
  sets `AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED`. Flashed to `vbmeta`, the bootloader skips hashtree
  checks. Maskrom flashing writes below AVB, so this works without an unlock.
- **OEM-unlock the bootloader.** Gated by **AVB-ATX attestation** with Embodied's Product Attestation
  Key (attributes in RPMB, see [TEE](#tee-secure-world-op-tee-rpmb)). It is not a plain `fastboot oem
  unlock`, so this path needs a key we don't have. U-Boot carries `bootloader-locked=%s` /
  `bootloader-min-versions=%s` lock-state strings.
- **Re-sign properly.** Generate your own AVB key, re-sign the `system`/`vendor` hashtrees + `vbmeta`,
  and optionally fuse your public key. Heaviest, but it keeps verification on.

A/B: partitions carry `_a`/`_b` suffixes; flash the inactive slot or both. `/data` is `forceencrypt`
f2fs, so replacing `system` without matching keystore state may force a data reset on first boot.

## TEE / secure world (OP-TEE + RPMB)

`trust.img` is the **OP-TEE** secure world (ARM TrustZone). It is device-bound and **separate from
`system`/`vendor`**, so reflashing those does **not** wipe it.

- **Runtime:** `tee-supplicant` (`/vendor/bin`) + `/dev/tee0`, `/dev/opteearmtz00` (`init.optee.rc`);
  Trusted Apps under `/vendor/lib/optee_armtz` (`*.ta`, incl. `uboot_storedata_rpmb.ta`).
- **Secure storage = RPMB** (`ro.tee.storage=rkss`; OP-TEE `tee_rpmb_fs`): a Replay-Protected Memory
  Block on the eMMC with an authentication key and **anti-rollback** protection
  (`gpd.tee.trustedStorage.antiRollback.protectionLevel`).
- **What it protects:**
  - **Keymaster**: hardware-backed keystore keys. The `/data` `forceencrypt` FEK is wrapped by
    keymaster, so **`/data` is cryptographically bound to this TEE**; a raw eMMC copy won't decrypt elsewhere.
  - **Gatekeeper**: lock-credential verification.
  - **Widevine keybox**: DRM device keys (`storage_widevine_write`, `rk_store_keybox`).
  - **AVB-ATX permanent attributes**: the Product Attestation Key public key + Product ID used by the
    attestation unlock (`trusty_read/write_permanent_attributes`), stored in RPMB. That is why unlock can't be spoofed.

**Implications:** you can replace `system`/`vendor`/`boot` (maskrom route) and the **TEE + RPMB
survive**, so keymaster/gatekeeper keep working and a debuggable custom `system` still boots. You
**cannot read the old `/data`** without the original keymaster keys, and you **cannot forge the AVB
unlock**. Wiping RPMB or reflashing `trust.img` loses keymaster-bound data and may brick attestation.

## Code signing & app trust

The build uses **three signing identities** (per-app assignment: [firmware-inventory](firmware-inventory.md#embodied-apps-the-ones-that-make-it-a-moxie)):

| Identity (cert subject) | SHA-256 fingerprint (first 16) | Signs |
|---|---|---|
| **`CN=Embodied`** (Pasadena, `signing@embodied.com`) | `6FA1065B92D3A5F0…` | **OTA / verified boot**: the `releasekey.x509.pem` in `otacerts.zip`, the `release-keys` build identity; also `OSUpdate`, `Launcher3Robot`, `BluetoothSpeaker` |
| **`CN=Embodied Inc`** (Pasadena) | `789BC175525358FA…` | `bo-firmwareUpdate`, the XMOS/motor utilities, `me.embodied.productiontesting.*` (factory apps) |
| **`CN=Android Debug, O=Android, C=US`** | `D5EF722984577 9CA…` | **`bo-android`** (`com.embodied.bo_unity` v24.10.803), **`bo-wifi`** (`com.embodied.bo_unity_wifi` v24.6.100), `FabTestSoftware` |

**The brain and setup app are signed with a generic "Android Debug"-identity certificate**, not the
OTA key. How they still hold privileged permissions:

- Both live in `/system/priv-app/`. `bo-android` requests **24** permissions, including five
  `signature|privileged` ones: `REBOOT`, `SET_TIME`, `SET_TIME_ZONE`, `READ_LOGS`, `PACKAGE_USAGE_STATS`
  (plus `CAMERA`, `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`, `SYSTEM_ALERT_WINDOW`, `INTERNET`, …).
  `bo-wifi` requests no `signature|privileged` permissions (11 normal/dangerous ones).
- There is **no `privapp-permissions` allowlist entry** for `com.embodied.bo_unity` anywhere
  (`/system`, `/system/product`, `/vendor` `/etc/permissions`; only the stock
  `privapp-permissions-platform.xml`). `ro.control_privapp_permissions` is **unset in every prop
  source** (system/vendor `build.prop`, ramdisk `default.prop`/`prop.default`).
- The app demonstrably uses `SET_TIME` (NTP) and `REBOOT` on shipping robots, so this build **does not
  enforce** the priv-app allowlist. A privileged app gets its `signature|privileged` grants from
  **`/system/priv-app` placement alone**. (A bench `getprop ro.control_privapp_permissions` + logcat would
  pin the exact default literal.)
- Neither app declares a `sharedUserId`; they run as ordinary distinct app UIDs. If the debug cert is
  the **public Android SDK debug key**, that signature is trivially reproducible; if it is a private
  key that merely uses the default subject, it isn't. The subject alone can't tell which.

**For custom firmware (the big lever):** a replacement brain **signed with any key, even a throwaway
debug key, dropped into `/system/priv-app/` inherits the same powers**. You do not need Embodied's
platform or OTA keys to reproduce `bo-android`'s privileges. The only real gate is writing the system
image (AVB/flashing). Replacing `bo-firmwareUpdate`/factory apps or the OTA payload in place requires
the respective Embodied private keys (which we do not have) or an AVB/signature bypass
([ota-and-recovery](ota-and-recovery.md)). SELinux confinement of these apps is covered in
[security-policy](security-policy.md). `/system/etc/permissions` and `/system/etc/sysconfig` are
otherwise stock AOSP 9.

## Custom-firmware roadmap (pragmatic)

1. **Get a shell first, non-destructively.** Flash a disabled `vbmeta` + a `system`/`boot` with
   `ro.debuggable=1` / `ro.adb.secure=0` (or an `adbd` allowing root). Confirm ADB, `getprop`, `dmesg`,
   `/dev` hardware nodes.
2. **Keep `vendor` stock at first.** The Rockchip HALs, `ledctrld`, DLP and camera plumbing live in
   `vendor`/`system/bin` ([hal-and-drivers](hal-and-drivers.md)); reuse them and replace only the app layer.
3. **Replace the experience, not the hardware layer.** Swap `bo-android`/`bo-wifi` for your own APK that
   speaks the same **ZMQ + protobuf** IPC ([robot-ipc-protocol](../protocol/robot-ipc-protocol.md)) to the
   Lizard MCU and the audio/vision modules.
4. **Full rebuild (advanced).** Rebuild `system`/`vendor` from the RK3288 Android-9 SDK
   (`RK30_ANDROID9-SDK`), port the Embodied hooks, re-sign with your own AVB key.

---
📖 [Reverse-engineering index](../README.md) · [Docs index](../../README.md) · [Back to top](../../../README.md)
