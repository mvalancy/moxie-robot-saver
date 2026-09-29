# 🛠️ Flashing runbook — custom firmware & flash-based revival

This is the **one canonical procedure** for putting your own firmware on a Moxie, or reviving a robot
by reflashing it. It is the Tier-3 (open + USB) path that always works, grounded in **v3.6.4-Zephyr /
OTA v24.10.803** (RK3288). Steps marked **(bench)** need a physical unit to confirm exact values (GPT
offsets, UART pads, button→mode); those are tracked in [exploration map](../EXPLORATION-MAP.md#open-items-need-a-bench-unit-or-an-external-artifact).

> This modifies the robot. Back up first. A bad flash can brick it until it is re-flashed.

## What you need

- A **Linux host** with **`rkdeveloptool`** (or Rockchip `upgrade_tool` / AndroidTool + DriverAssistant on Windows).
- The **RK3288 loader** (`rk3288_loader_vX.bin`): DDR init + miniloader for maskrom.
- **`avbtool`** (from AOSP) to make a disabled `vbmeta`.
- Images: a genuine 803 set or your own `system`/`vendor`/`boot`. Hashes are in the
  [firmware reference](firmware-803-reference.md#partition-images); partition names in
  [hardware-access](../hardware/hardware-access.md#partition-table-flash-targets).
- A **USB data connection** to the board (bench: confirm the port is reachable).

## A. Enter a flashing mode (USB)

All of these bypass the locked normal-boot ADB. Mode details and reboot magics:
[hardware-access](../hardware/hardware-access.md#the-rockchip-boot-download-modes-rk3288).

1. **Maskrom:** hold the mainboard **`LOAD`** button ([fcc-teardown](../hardware/fcc-teardown.md#reset-load-power-on-board-buttons-major-bench-finding))
   or the SoC `BOOT`/recovery test point while powering; then `rkdeveloptool db rk3288_loader.bin`.
2. **Loader / rockusb:** `reboot loader` from a root shell; **or** hold the **Macro** button at power-on
   (bench: long-press → bootrom download, [confirm on the serial console](../hardware/hardware-access.md#boot-mode-entry-reboot-reasons-keys));
   **or** let it auto-enter on AVB failure (U-Boot `bootcmd` falls back to `rockusb`).
3. Confirm: `rkdeveloptool ld` should list a device in `Maskrom` or `Loader` mode.

## B. Read the partition table and back up (bench)

```sh
rkdeveloptool ppt                                   # print the GPT (names → offsets/sizes)
rkdeveloptool rl <start> <count> boot_backup.img    # or read-by-name if supported
```
Back up at least `vbmeta_a/b` and `boot_a/b`. `/data` is keymaster-bound, so a raw `userdata` copy won't
decrypt elsewhere ([TEE](firmware-image.md#tee-secure-world-op-tee-rpmb)). Compare read-backs of
`system`/`vendor`/`boot` to the [SHA-256s](firmware-803-reference.md#partition-images) to confirm the build.

## C. Disable AVB verification

`fastboot oem unlock` is not available (AVB-ATX attestation needs Embodied's key,
[firmware-image](firmware-image.md#the-verified-boot-chain-and-how-to-break-it-for-custom-code)), but
maskrom flashes below AVB, so neuter `vbmeta`:

```sh
avbtool make_vbmeta_image --flags 2 --padding_size 4096 -o vbmeta_disabled.img
#   flag 2 = AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED
rkdeveloptool wlx vbmeta_a vbmeta_disabled.img
rkdeveloptool wlx vbmeta_b vbmeta_disabled.img
```
Alternatively, generate your own AVB key and re-sign the hashtrees + `vbmeta` to keep verification on.

## D. Flash your images

```sh
rkdeveloptool wlx system_a  system.img       # or your custom build
rkdeveloptool wlx vendor_a  vendor.img       # keep stock vendor for the HALs/DLP/MCU plumbing
rkdeveloptool wlx boot_a    boot.img         # e.g. ro.debuggable=1 / adbd allowing root
rkdeveloptool td                             # reset the device
```
- Flash the **inactive A/B slot**, or both `_a` and `_b`. (`rkdeveloptool wl <offset> <img>` or
  `upgrade_tool uf update.img` also work.)
- **Leave `trust`/`uboot` alone** unless you must. Reflashing `trust` risks the TEE/RPMB (keymaster,
  attestation) and can brick unlock.
- **Minimal-invasive custom personality:** keep stock `system`/`vendor` + a debuggable `boot`, get root
  ADB, then replace only the app layer (`bo-android`) and speak the [ZMQ bus](../protocol/robot-ipc-protocol.md).

## E. First boot and verify

- The disabled `vbmeta` lets the modified `system` boot. `adb shell getprop ro.debuggable` should print `1`.
- The **TEE + RPMB survive**, so keymaster/gatekeeper work and a debuggable system boots. The **old
  `/data` is unreadable** without the original keymaster keys; expect a first-boot wipe of the
  `forceencrypt` f2fs.

## Reviving a stranded robot by flashing

Same procedure with a **genuine 803 image set** (or your own): enter maskrom/loader → flash
`system`/`vendor`/`boot` + disabled `vbmeta` → boot. The robot is now on 803, and
[QR re-home](../FIELD-GUIDE.md#-revive-an-old-robot) + a [self-hosted server](../protocol/cloud-protocol.md)
apply. This is the reliable route for **pre-801** units that can't be relocated over the air.

The open question is whether **step A works without opening the shell**: is a USB data port reachable,
and does Macro enter download mode? If yes, this runbook becomes low-open (USB + a button).

---
📖 [Hardware access](../hardware/hardware-access.md) · [Firmware image](firmware-image.md) · [Field guide](../FIELD-GUIDE.md) · [Exploration map](../EXPLORATION-MAP.md) · [Docs index](../../README.md)
