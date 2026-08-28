# 🧱 Moxie firmware image — the robot's own Android system

> **What this is.** A teardown of Moxie's **on-robot firmware** (not the phone app): the partition
> layout, boot/verified-boot chain, security posture, installed apps, and the hardware it drives.
> This is the map you need to **run custom software on the robot itself**, not just stand up a
> replacement cloud. Reconstructed by mounting/`debugfs`-reading the factory partition images
> (`system.img`, `vendor.img`, `oem.img`, `boot.img`, `vbmeta.img`) — no Embodied source is included,
> only observed facts about the shipped images.

Analyzed build: **`v3.6.4-Zephyr` / OTA `v24.10.803-rls-robot`**, built **2024-12-28** (`ro.build.date`),
i.e. a **post-Google, "803"-era** AWS-endpoint firmware — the same generation OpenMoxie targets, and
*newer* than the pre-801 Google-IoT unit on the bench (see [`../debugging/live-hardware-debug.md`](../debugging/live-hardware-debug.md)).

## TL;DR for going custom

| Question | Answer |
|---|---|
| SoC / arch | **Rockchip RK3288**, ARMv7 (`armeabi-v7a`, 32-bit), Cortex-A15 |
| OS | **Android 9 (Pie)**, API 28, security patch **2019-04-05**, `user`/`release-keys` |
| Boot | **A/B seamless** (`ro.build.ab_update=true`), **system-as-root** (`ro.build.system_root_image=true`) |
| Verified boot | **AVB 1.1** (`avbtool 1.1.0`), `androidboot.veritymode=enforcing`, system mounted `ro` with `wait,avb,slotselect` |
| Secure props | `ro.secure=1`, `ro.adb.secure=1`, `ro.debuggable=0` |
| **OEM unlock** | `ro.oem_unlock_supported=1`, **but** unlock is **AVB-ATX attestation** (`libavb_atx`, challenge-response — needs Embodied's Product Attestation Key). Maskrom/rockusb bypasses it — see *Unlock reality* below. |
| Data | `/data` is **f2fs, forceencrypt** (`forceencrypt=/cache/key_file`) |
| Flashing | Rockchip loader / maskrom → `rkdeveloptool` (see below) |

**Bottom line:** custom code is very achievable. The RK3288 supports maskrom/loader mode, OEM unlock is
enabled, and AVB can be disabled by flashing a `--disable-verification` `vbmeta`. The friction is not a
locked bootloader — it's **rebuilding a `system`/`vendor` that still drives Moxie's non-standard
hardware** (DLP projector face, PCA963x LED array, the "Lizard" motor/sensor MCU, XMOS audio DSP).

## Partition set

The full factory image (`moxie-prod-partitions_*.zip`) contains eight partitions:

| Partition | Size | Contents |
|---|--:|---|
| `uboot.img` | 4 MiB | Rockchip U-Boot (RK3288 SPL + U-Boot) |
| `trust.img` | 4 MiB | ARM Trusted Firmware / OP-TEE secure world (`ro.tee.storage=rkss`) |
| `dtbo.img` | 4 MiB | Device-tree overlay |
| `vbmeta.img` | 4 KiB | **AVB metadata** — hashtree descriptors for system/vendor, signed |
| `boot.img` | 64 MiB | Kernel (`0x10008000`) + ramdisk (system-as-root init) + 2nd-stage |
| `oem.img` | 384 MiB | Boot animation only (`/oem/media/bootanimation.zip`, 84 MB) + `fs_config` |
| `system.img` | 3.4 GiB | Android `/system` incl. all `bo-*` apps (ext4, mounted at `/`) |
| `vendor.img` | 4.7 GiB | Rockchip HALs, `fstab.rk30board`, hw init `.rc`, firmware blobs |

### boot.img kernel cmdline (verbatim)

```
console=ttyFIQ0 androidboot.baseband=N/A androidboot.wificountrycode=US
androidboot.veritymode=enforcing androidboot.hardware=rk30board
androidboot.console=ttyFIQ0 firmware_class.path=/vendor/etc/firmware
init=/init rootwait ro init=/init buildvariant=user
```

### fstab (`/vendor/etc/fstab.rk30board`)

```
/dev/block/by-name/system    /                    ext4  ro,barrier=1                     wait,avb,slotselect
/dev/block/by-name/cache     /cache               ext4  noatime,nosuid,nodev,discard     wait,check
/dev/block/by-name/metadata  /mnt/vendor/metadata ext4  noatime,nosuid,nodev,discard     wait
/dev/block/by-name/misc      /misc                emmc  defaults                         defaults
/dev/block/by-name/userdata  /data                f2fs  noatime,discard,inline_xattr     wait,check,notrim,forceencrypt=/cache/key_file,quota,reservedsize=128M
```

`wait,avb,slotselect` on `/` = A/B slot selection + AVB hashtree verification. There is **no
`fs_mgr` `verify` with an on-device key** — integrity is enforced entirely by `vbmeta`/AVB.

## The verified-boot chain (and how to break it for custom code)

1. **maskrom → SPL/U-Boot** (`uboot.img`) verifies via Rockchip's loader signature.
2. **U-Boot → AVB**: reads `vbmeta.img` (`AVB0` magic, `avbtool 1.1.0`), checks the hashtree
   descriptors covering `system` and `vendor`. `androidboot.veritymode=enforcing` means a hash
   mismatch **hard-stops** the boot.
3. **Kernel + system-as-root init** mounts `system` read-only at `/`, dm-verity backed by the AVB
   hashtree.

To run a modified `system`/`vendor` you must defeat step 2, three ways, easiest first:

- **Flash a "disabled" vbmeta.** `avbtool make_vbmeta_image --flags 2 --padding_size 4096 -o vbmeta_disabled.img` sets
  `AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED`; flash it to the `vbmeta` partition. The bootloader
  then skips hashtree checks and your modified `system` boots. (Requires the bootloader to honor the
  flag — Rockchip's does when unlocked.)
- **OEM-unlock the bootloader.** Gated by **AVB-ATX attestation** (`libavb_atx`, challenge-response
  with Embodied's Product Attestation Key, attributes in Trusty/OP-TEE — see *Unlock reality* below);
  not a plain `fastboot oem unlock`, so this path needs a key we don't have. The **maskrom bypass** is
  the practical route instead.
- **Re-sign properly.** Generate your own AVB key, re-sign `system`/`vendor` hashtrees + `vbmeta`,
  and (optionally) fuse your public key. Heaviest, but keeps verification *on*.

### Flashing (Rockchip)

RK3288 has no fastboot by default; use the Rockchip path:

- Enter **maskrom** (hold the board's recovery/maskrom key while powering, or short the flash clk) or
  **loader/rockusb** mode.
- `rkdeveloptool db <loader.bin>` then `rkdeveloptool wl <offset> <partition.img>`, or use
  `upgrade_tool`. Partition offsets come from the parameter/GPT (`by-name` symlinks in fstab map to
  the GPT names above).
- A/B: there are `system_a/system_b` etc.; flash the **inactive** slot or both.

> ⚠️ `/data` is `forceencrypt` f2fs. Wiping/replacing `system` without matching keystore state may
> force a data reset on first boot. Back up `/data` (contents, credentials, paired state) first.

## What's installed (`/system/priv-app`, `/system/app`)

Stock AOSP 9 minus telephony extras, **plus Embodied's stack**:

| App (priv-app) | Role |
|---|---|
| **`bo-android`** | **The "brain."** Main experience app — conversation, vision, behavior, Unity face. See [`robot-ipc-protocol.md`](robot-ipc-protocol.md). |
| **`bo-wifi`** | **The setup/"Wifi App."** Unity app that scans setup/pairing/**debug** QR codes, joins Wi-Fi, drives pairing. See [`qr-commands.md`](qr-commands.md). |
| `bo-firmwareUpdate` | Pushes MCU/XMOS firmware to the "Lizard" board and audio DSP. |
| `bo_motor_test`, `bo_xmosupdate`, `xmosdfu`, `qcapp` | Factory/service utilities (motor exercise, XMOS DFU, QC). |
| `OSControl`, `OSUpdate` | Display-hw control and OS OTA (A/B via `update_engine`). |
| `Launcher3Robot` | Custom home/launcher — the robot boots into this, which foregrounds the experience. |
| `me.embodied.productiontesting.{finaltest,internalassytest,lifetest}`, `FabTestSoftware` | **Factory line** apps. See [`factory-provisioning.md`](factory-provisioning.md). |

### Embodied system daemons & hardware hooks (`init.rockchip.rc` / `init.rk3288.rc`)

- **`ledctrld`** (`/system/bin/ledctrld`) — drives the **PCA963x** I²C LED controller (channels
  `red_1..6`, `green_1..5`, `blue_1..5` under `/sys/class/leds/pca963x:*`).
- **`projectorfanpid`** — PID fan controller for the DLP projector; configurable via
  `/sdcard/scripts.config`.
- **DLP face**: TI **DLPC3430** at I²C `5-001b` exposes `led_out`, `rgb_out`, `brightness_alt`,
  `temperature`; `/dev/panel_name` selects `sys.embodied.displayhw`.
- Camera **OV2710** (`sys.embodied.camhw=ov2710`, V4L2).

See [`hardware-map.md`](hardware-map.md) for the motor/sensor/LED enumeration recovered from the
firmware's own protobufs.

## TEE / secure world (OP-TEE + RPMB)

`trust.img` is the **OP-TEE** secure world (ARM TrustZone). It's device-bound and **separate from
`system`/`vendor`** — reflashing those does **not** wipe it. What lives there matters when you rebuild:

- **Runtime:** `tee-supplicant` (`/vendor/bin`) + `/dev/tee0`, `/dev/opteearmtz00` (`init.optee.rc`);
  Trusted Apps under `/vendor/lib/optee_armtz` (`*.ta`, incl. `uboot_storedata_rpmb.ta`).
- **Secure storage = RPMB** (`ro.tee.storage=rkss`; OP-TEE `tee_rpmb_fs`) — a **Replay-Protected
  Memory Block** on the eMMC with an authentication key and **anti-rollback** protection
  (`gpd.tee.trustedStorage.antiRollback.protectionLevel`).
- **What it protects:**
  - **Keymaster** — hardware-backed Android keystore keys (the `/data` `forceencrypt` FEK is wrapped
    by keymaster, so **`/data` is cryptographically bound to this TEE** — a raw eMMC copy won't decrypt
    elsewhere).
  - **Gatekeeper** — lock-credential verification.
  - **Widevine keybox** — DRM device keys (`storage_widevine_write`, `rk_store_keybox`).
  - **AVB-ATX permanent attributes** — the Product Attestation Key public key + Product ID used by the
    [attestation unlock](#unlock-reality-avb-attestation-not-plain-oem-unlock) (`trusty_read/write_permanent_attributes`), stored in RPMB (which is why unlock can't be spoofed).

**Custom-firmware implications:** you can freely replace `system`/`vendor`/`boot` (maskrom route) and
the **TEE + RPMB survive** — keymaster/gatekeeper keep working, so a debuggable custom `system` still
boots. But **don't expect to read the old `/data`** without the original keymaster keys, and you
**can't forge the AVB unlock** without Embodied's Product Attestation Key (RPMB-anchored). If you wipe
RPMB or reflash `trust.img`, you lose keymaster-bound data and may brick attestation.

## Key `build.prop` / `prop.default` values

```
sys.embodied.otaver   = v3.6.4-24_12_28-18_42-master-a90cfdee72-v24.10.803-rls-robot
sys.embodied.version  = v3.6.4-Zephyr
sys.embodied.hwtype   = robot
sys.embodied.buildtype= customer          # vs. "eng"
sys.embodied.certtype = release
sys.embodied.camtype  = v4l2 / camhw=ov2710
sys.embodied.qrsetup  = 0                  # QR-only setup mode toggle
sys.embodied.auto_unity = 0
persist.sys.disable_rescue = 1
ro.board.platform = rk3288 / ro.product.board = rk30sdk
ro.rksdk.version  = RK30_ANDROID9-SDK-v1.00.00
ro.oem_unlock_supported = 1
ro.build.fingerprint = rockchip/rk3288/rk3288:9/PQ2A.190305.002/cloud12282012:user/release-keys
```

`sys.embodied.buildtype` flips **customer ↔ eng** and `certtype` **release ↔ dev** — the factory/eng
builds relax constraints. `sys.embodied.qrsetup` and `auto_unity` gate the setup flow and whether the
Unity experience auto-launches.

## Code signing & app trust

The build (`v24.10.803`) uses **three distinct signing identities** — worth knowing before you
re-sign or replace anything:

| Identity (cert subject) | SHA-256 fingerprint (16) | Signs |
|---|---|---|
| **`CN=Embodied`** (Pasadena, `signing@embodied.com`) | `6FA1065B92D3A5F0…` | **OTA / verified boot** — the `releasekey.x509.pem` in `otacerts.zip`; the `release-keys` build identity |
| **`CN=Embodied Inc`** (Pasadena) | `789BC175525358FA…` | `bo-firmwareUpdate`, `me.embodied.productiontesting.*` (factory apps) |
| **`CN=Android Debug, O=Android`** | `D5EF722984577 9CA…` | **`bo-android` and `bo-wifi`** — the two main experience apps |

**The headline:** the brain (`bo-android`) and setup app (`bo-wifi`) are signed with a generic
**"Android Debug"-identity** certificate, **not** the platform/OTA key. Implications:

- They are **priv-app** (privileged) but there is **no Embodied `privapp-permissions` allowlist** in
  `/system/etc/permissions` (only stock AOSP `privapp-permissions-platform.xml`), so their privileges
  come from being privileged system apps, not from platform-signature permissions.
- Signature-level relationships (shared `sharedUserId`, `signature` permissions) between the two apps
  hold via this debug identity. If this is the **public Android SDK debug key**, that signature is
  trivially reproducible by anyone (a weak choice for the main app); if it's a private key that merely
  uses the default `Android Debug` subject, it isn't — the subject alone can't tell you which, only
  possession of the key does.
- **For custom firmware:** replacing `bo-android`/`bo-wifi` is easiest of all — you can re-sign your
  replacement with your own key (and, if needed, update any `sharedUserId`/permission expectations),
  since they don't chain to the platform key. Replacing `bo-firmwareUpdate`/factory apps or the OTA
  payload requires the respective **Embodied** private keys (which we do **not** have) or an
  AVB/signature bypass (see above + [`ota-and-recovery.md`](ota-and-recovery.md)).

`/system/etc/permissions` and `/system/etc/sysconfig` are otherwise **stock AOSP 9** (no embodied
feature/permission XML).

## Custom-firmware roadmap (pragmatic)

1. **Get a shell first, non-destructively.** Flash a `--disable-verification` `vbmeta` + a `system`
   with `ro.debuggable=1` / `ro.adb.secure=0` (or an `adbd` allowing root). Confirm ADB, `getprop`,
   `dmesg`, `/dev` hardware nodes.
2. **Keep `vendor` stock at first.** The Rockchip HALs, `ledctrld`, DLP and camera plumbing all live
   in `vendor`/`system/bin`; reuse them and only replace the *app* layer.
3. **Replace the experience, not the hardware layer.** Swap `bo-android`/`bo-wifi` for your own APK
   that speaks the same **ZMQ + protobuf** IPC (see [`robot-ipc-protocol.md`](robot-ipc-protocol.md))
   to the "Lizard" MCU and audio/vision daemons. This gets you a fully custom personality on stock
   motor/face/LED plumbing.
4. **Full rebuild (advanced).** Rebuild `system`/`vendor` from the RK3288 Android-9 SDK
   (`RK30_ANDROID9-SDK`), port the Embodied HAL hooks, re-sign with your own AVB key.

---
📖 [Reverse-engineering index](README.md) · [Docs index](../README.md) · [Back to top](../../README.md)
