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
| `oem.img` | 384 MiB | Boot animation (`/oem/media/bootanimation.zip`, 84 MB) + `fs_config_files`/`fs_config_dirs` + `/oem/etc/package_performance.xml` (see below) |
| `system.img` | 3.4 GiB | Android `/system` incl. all `bo-*` apps (ext4, mounted at `/`) |
| `vendor.img` | 4.7 GiB | Rockchip HALs, `fstab.rk30board`, hw init `.rc`, firmware blobs |

### SELinux confinement — the apps run in **stock** domains (v24.10.803)

The MAC layer complements the priv-app permission story above, and it's almost entirely **unmodified
AOSP**. Recovered from `/system/etc/selinux/` + `/vendor/etc/selinux/`:

- **Enforcing.** `user`/`release-keys` build, no `androidboot.selinux=permissive` in the
  [kernel cmdline](#bootimg-kernel-cmdline-verbatim), and no permissive statement for any Embodied
  type — so SELinux runs **enforcing**, the stock AOSP policy.
- **`bo-android` gets no special domain.** `plat_seapp_contexts` maps it by the ordinary rule
  `user=_app isPrivApp=true domain=priv_app` — the flagship app runs in the **standard `priv_app`
  domain**, exactly like any other privileged app. It appears **nowhere** in `seapp_contexts`,
  `mac_permissions.xml`, or as a custom type. So its power comes entirely from `/system/priv-app`
  placement + the (unenforced) priv-app permission grants — **not** from a permissive or bespoke MAC
  domain.
- **Embodied's entire sepolicy footprint is two daemon domains** — `ledctrld` and `projectorfanpid`
  (the LED + projector-fan helpers, [boot-and-launcher](boot-and-launcher.md#init-service-graph-native-daemons)),
  added to the **platform** policy (`plat_sepolicy.cil`), each with its own `*_exec` type. Every other
  type/domain is stock AOSP/Rockchip.

**For custom firmware (goal 1):** a replacement app dropped into `/system/priv-app` **inherits the same
`priv_app` domain automatically** — no sepolicy edit needed for an app-level swap, and it's neither more
nor less MAC-confined than the original. Only a *new native daemon touching new hardware* needs a policy
change — and Embodied's `ledctrld`/`projectorfanpid` pair is the ready-made template for how to add one.

### `oem.img` — one telling leftover (`/oem/etc/package_performance.xml`)

Besides the boot animation, the OEM partition carries a **202-byte** `package_performance.xml` — a
**stock Rockchip BSP** feature that boosts CPU/GPU clocks when a listed package runs. On this 803 image
it lists **only AnTuTu**, verbatim:

```xml
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<performance-package>
<app package="com.antutu.ABenchMark" mode="1"/>
<app package="com.antutu.benchmark.full" mode="1"/>
</performance-package>
```

Neither AnTuTu package is installed (`/system/app` has no `antutu*`), so the file is **dormant vendor
boilerplate** — Rockchip's benchmark-boost list shipped untouched. It's a small but concrete data point
that the base OS is an **unmodified RK3288 Android-9 BSP** (brand/manufacturer `rockchip`, fingerprint
`rockchip/rk3288/rk3288:9/PQ2A.190305.002/…:user/release-keys`) with the Embodied `bo-*` apps layered on
top — so a custom build inherits the RK BSP's quirks, including this file (safe to drop or repurpose).

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
    [attestation unlock](#the-verified-boot-chain-and-how-to-break-it-for-custom-code) (`trusty_read/write_permanent_attributes`), stored in RPMB (which is why unlock can't be spoofed).

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
| **`CN=Android Debug, O=Android, C=US`** | `D5EF722984577 9CA…` | **`bo-android`** (`com.embodied.bo_unity` v24.10.803) and **`bo-wifi`** (`com.embodied.bo_unity_wifi` v24.6.100) — the two main experience apps (packages/versions decoded from each APK's binary `AndroidManifest.xml`) |

**The headline:** the brain (`bo-android`) and setup app (`bo-wifi`) are signed with a generic
**"Android Debug"-identity** certificate, **not** the platform/OTA key. Implications:

- **How they hold privileged permissions (the exact mechanism).** Both live in `/system/priv-app/`
  and `bo-android` requests **24** permissions including five `signature|privileged` ones —
  `REBOOT`, `SET_TIME`, `SET_TIME_ZONE`, `READ_LOGS`, `PACKAGE_USAGE_STATS` (plus `CAMERA`,
  `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS`, `SYSTEM_ALERT_WINDOW`, `INTERNET`, …). Yet there is **no
  `privapp-permissions` allowlist entry** for `com.embodied.bo_unity` **anywhere** (`/system`,
  `/system/product`, `/vendor` `/etc/permissions` all checked — only the stock
  `privapp-permissions-platform.xml`), **and** `ro.control_privapp_permissions` is **unset in every
  prop source** (system/vendor `build.prop`, ramdisk `default.prop`/`prop.default`). Since the app
  demonstrably uses `SET_TIME` (NTP) and `REBOOT` on shipping robots, this build effectively **does not
  enforce** the priv-app allowlist — a privileged app gets its `signature|privileged` grants from
  **`/system/priv-app` placement alone**, not from platform-signature or an allowlist. (`bo-wifi`
  requests no `signature|privileged` perms — 11 normal/dangerous ones.) A bench `getprop
  ro.control_privapp_permissions` + logcat would pin the exact default literal, but the shipped
  behaviour is unambiguous: privileged perms are granted.
- **Version skew:** `bo-wifi` is **v24.6.100** — *older* than the v24.10.803 OTA; the setup app was not
  rebuilt for this release, so its Wi-Fi/pairing surface is the 24.6 vintage even on an 803 robot.
- Neither app declares a `sharedUserId` (both decoded manifests have none), so they run as ordinary
  distinct app UIDs — the debug identity governs signature-permission *eligibility*, not a shared UID.
  If this is the **public Android SDK debug key**, that signature is trivially reproducible by anyone
  (a weak choice for the main app); if it's a private key that merely uses the default `Android Debug`
  subject, it isn't — the subject alone can't tell you which, only possession of the key does.
- **For custom firmware (the big lever):** because privileged grants come from priv-app *placement*
  (not platform-signature or allowlist), a replacement brain **signed with any key — even a throwaway
  debug key — dropped into `/system/priv-app/` inherits the same powers** (`REBOOT`/`SET_TIME`/
  `READ_LOGS`/…). You do **not** need Embodied's platform or OTA keys to reproduce `bo-android`'s
  privileges. The only real gate is *writing to the system image* (AVB / flashing — see above and
  [`hardware-access.md`](hardware-access.md)); once you can do that, the app layer imposes no extra
  signing barrier. Replacing `bo-firmwareUpdate`/factory apps or the OTA
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
