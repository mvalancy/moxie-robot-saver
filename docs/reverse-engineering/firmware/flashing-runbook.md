# 🛠️ Flashing runbook — custom firmware & flash-based revival

> Step-by-step to put **your own firmware on a Moxie**, and to **revive a robot by reflashing** — the
> Tier-3 (open + USB) path that always works. Grounded in the verified findings for **v3.6.4-Zephyr /
> OTA v24.10.803** (RK3288). Steps that need a **bench unit** are marked ⚙️; do those once and the
> exact values (GPT offsets, UART pads, button→mode) fill in the [open items](../COVERAGE.md).
>
> ⚠️ This modifies the robot. Back up first; a bad flash can brick until re-flashed. You accept the risk.

## What you need

- A **Linux host** with **`rkdeveloptool`** (or Rockchip `upgrade_tool` / DriverAssistant on Windows).
- The **RK3288 loader** (`rk3288_loader_vX.bin`) — DDR init + miniloader for maskrom.
- **`avbtool`** (from AOSP) to make a disabled `vbmeta`.
- Your images (a genuine 803 set, or your own `system`/`vendor`/`boot`) — partitions + hashes in
  [firmware-803-reference](firmware-803-reference.md); flash targets in [hardware-access](../hardware/hardware-access.md#partition-table-flash-targets).
- A **USB data connection** to the board ⚙️ (confirm the port is reachable — [open item](../COVERAGE.md)).

## A. Enter a flashing mode (USB)

Pick whichever you can reach — all bypass the locked normal-boot ADB:

1. **Maskrom** ⚙️ — hold the SoC `BOOT`/recovery test-point low while powering (needs the board open),
   then `rkdeveloptool db rk3288_loader.bin` to load the miniloader.
2. **Loader / rockusb** — `reboot loader` from a root shell, **or** hold the **Macro button at
   power-on** ⚙️ (long-press → bootrom download; [confirm on serial console](../hardware/hardware-access.md#boot-mode-entry-reboot-reasons-keys)),
   **or** it auto-enters on AVB failure (U-Boot `bootcmd` falls back to `rockusb`).
3. Confirm the host sees it: `rkdeveloptool ld` (should list a device in `Maskrom`/`Loader` mode).

## B. Read the partition table & back up ⚙️

```sh
rkdeveloptool ppt                       # print the GPT (names → offsets/sizes)
rkdeveloptool rl <start> <count> boot_backup.img     # or read-by-name if supported
# back up at least: vbmeta_a/b, boot_a/b, and (if you value paired state) note that
# /data is keymaster-bound — a raw userdata copy won't decrypt elsewhere (see firmware-image TEE).
```
Verify a read-back of `system`/`vendor`/`boot` against the [SHA-256s](firmware-803-reference.md) to
confirm you have this exact build.

## C. Disable AVB verification (to run modified images)

You can't `fastboot oem unlock` (AVB-ATX attestation needs Embodied's key — see
[firmware-image](firmware-image.md#the-verified-boot-chain-and-how-to-break-it-for-custom-code)), but maskrom
flashes below AVB, so just neuter `vbmeta`:

```sh
avbtool make_vbmeta_image --flags 2 --padding_size 4096 -o vbmeta_disabled.img
#   flag 2 = AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED
rkdeveloptool wlx vbmeta_a vbmeta_disabled.img
rkdeveloptool wlx vbmeta_b vbmeta_disabled.img
```
(Alternatively, generate your own AVB key and **re-sign** the hashtrees + `vbmeta` to keep verification on.)

## D. Flash your images

```sh
rkdeveloptool wlx system_a  system.img       # (or your custom build)
rkdeveloptool wlx vendor_a  vendor.img        # keep stock vendor for the HALs/DLP/MCU plumbing
rkdeveloptool wlx boot_a    boot.img          # e.g. a boot.img with ro.debuggable=1 / adbd allowing root
rkdeveloptool td                              # reset the device
```
- Flash the **inactive A/B slot** (or both `_a` and `_b`) — see the [partition table](../hardware/hardware-access.md#partition-table-flash-targets).
- **Keep `trust`/`uboot` untouched** unless you must — reflashing `trust` risks the TEE/RPMB
  (keymaster, attestation) and can brick unlock ([firmware-image TEE](firmware-image.md#tee-secure-world-op-tee-rpmb)).
- **Minimal-invasive custom personality:** keep stock `system`/`vendor` + a debuggable `boot`, get root
  ADB, then replace only the **app layer** (`bo-android`) and speak the [ZMQ bus](../protocol/robot-ipc-protocol.md).

## E. First boot & verify

- On boot, the disabled `vbmeta` lets the modified `system` run. Get a shell:
  `adb shell getprop ro.debuggable` should be `1`.
- The **TEE + RPMB survive** the reflash — keymaster/gatekeeper still work, so a debuggable system boots
  fine; but the **old `/data` is unreadable** without the original keymaster keys (expect a first-boot
  data wipe of `forceencrypt` f2fs).

## Reviving a stranded robot by flashing

Same procedure, with a **genuine signed 803 image set** (or your own): enter maskrom/loader → flash
`system`/`vendor`/`boot`/`vbmeta` (disabled) → boot → the robot is now on 803, and the [Tier-1 QR
re-home](../FIELD-GUIDE.md#-revive-an-old-robot) + [self-hosted server](../protocol/cloud-protocol.md) apply. This is
the reliable route for **pre-801** units that can't be relocated over the air.

> **The open question** ([COVERAGE](../COVERAGE.md)) is whether **step A works without opening the shell**
> — i.e. is a USB data port reachable, and does the Macro button enter download mode? If yes, this whole
> runbook becomes **low-open** (USB + a button). If no, it's a teardown. Both are in scope.

---
📖 [Hardware access](../hardware/hardware-access.md) · [Firmware image](firmware-image.md) · [Field guide](../FIELD-GUIDE.md) · [Coverage](../COVERAGE.md) · [Docs index](../../README.md)
