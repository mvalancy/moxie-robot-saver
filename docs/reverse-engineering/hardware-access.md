# 🔌 Hardware access & flashing — the physical surface

> **Scope: the whole machine.** This project deliberately covers Moxie **end to end** — including
> opening the shell, USB flashing, UART/TTL serial, JTAG, and re-signing firmware. The
> [no-disassembly options](ota-and-recovery.md) are just the *first tier* we exhaust for owners who
> can't (or shouldn't) open the robot; everything below is the **planned, in-scope** path for custom
> firmware and for reviving robots the over-the-air route can't reach. Build: **v3.6.4-Zephyr / OTA
> v24.10.803**, board **`rk3288-robot-gen1p5`** (`board=evb_rk3288`).

## Access tiers

```mermaid
flowchart TB
    t1["Tier 1 — no open<br/>QR · network · OTA config"]
    t2["Tier 2 — external ports<br/>USB (rockusb/fastboot) · UART TTL console"]
    t3["Tier 3 — full teardown<br/>maskrom · rkdeveloptool · test points · JTAG · chip-off"]
    t1 --> t2 --> t3
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    class t1,t2,t3 d;
```

## The Rockchip boot / download modes (RK3288)

U-Boot's `bootcmd` (recovered from `uboot.img`) shows the built-in fallbacks:

```
bootcmd = boot_android ${devtype} ${devnum};
          echo AVB boot failed and enter rockusb or fastboot!;
          rockusb 0 ${devtype} ${devnum};
          fastboot usb 0;
```

So on an **AVB verification failure the bootloader itself drops into `rockusb` (Rockchip USB download
mode), then `fastboot`** — both reachable over the USB data lines without special keys. The RK3288
also has:

| Mode | How to enter | Tool |
|---|---|---|
| **Maskrom** | hold the SoC's `BOOT`/recovery test-point low while powering (or corrupt the loader) | `rkdeveloptool db <loader>` then `wl`/`ul` |
| **Loader / rockusb** | U-Boot download mode (auto-entered on AVB fail, or `reboot loader`) | `rkdeveloptool`, `upgrade_tool`, Rockchip DriverAssistant |
| **Fastboot** | U-Boot fallback (see bootcmd) or `reboot bootloader` | `fastboot` (note RK fastboot is limited) |
| **Recovery** | A/B recovery-as-boot; `reboot recovery` or BCB in `misc` | `adb sideload` |

`bootloader-locked=%s` / `bootloader-min-versions=%s` strings confirm a lock-state variable; combined
with `ro.oem_unlock_supported=1`, the bootloader can be unlocked (see [`firmware-image.md`](firmware-image.md)).

## Flashing a partition (the reliable path)

1. Enter **maskrom** or **loader** mode over USB.
2. `rkdeveloptool db rk3288_loader.bin` (download the DDR init + loader).
3. `rkdeveloptool wl <offset> <partition.img>` (or `upgrade_tool uf update.img`). Partition names map
   to the GPT `by-name` entries in [`firmware-image.md`](firmware-image.md).
4. To run **modified** `system`/`vendor`: flash a `--disable-verification` `vbmeta` (or re-sign the
   AVB hashtrees with your own key), then flash your images. A/B: flash the inactive slot or both.

> ⚠️ `/data` is `forceencrypt` f2fs — replacing `system`/keystore state may force a data wipe on first
> boot. Back up `/data` first if you care about paired state.

## Serial console (UART / TTL)

The kernel cmdline sets **`console=ttyFIQ0`** (`androidboot.console=ttyFIQ0`) — the RK3288 **FIQ debugger
serial console**. Bring a 3.3 V USB-TTL adapter to the board's debug UART (RK3288 debug UART is
typically **1500000 baud**, 8N1; some builds use 115200 — try both) to get:

- U-Boot prompt + boot logs (watch the AVB / `rockusb`/`fastboot` fallback live).
- Android kernel `dmesg` and the `init`/`FIQ` console.
- A shell for debugging (subject to `ro.secure`/SELinux; recovery/maskrom bypass this).

Locating the pads: the DTB is `rk3288-robot-gen1p5.dtb` — dump it (`dtc`) to find the `uart` node and
its pinmux, then map to test points on the mainboard. (Teardown photos / test-point map: TODO as we
open a unit.)

## ADB / USB (when booted normally)

- Gadget offers `adb`, `mtp`, `mtp+adb`, `rndis` (idVendor `18d1`, idProduct `4EE7`).
- `ro.adb.secure=1` + `ro.debuggable=0`: normal-mode ADB needs an authorized key and an on-screen
  "allow" (hard on a projector-face device). **Recovery-mode adb sideload** and **maskrom/rockusb**
  bypass this — hence Tier-2/3.
- MTP needs no adb auth (can drop files on `/sdcard` if a port is reachable) — see [`ota-and-recovery.md`](ota-and-recovery.md).

## JTAG / SWD & chip-off (deep tier)

The RK3288 exposes JTAG (muxed on SD/other pins; enabled via eFuse/loader in some configs). For a
fully bricked unit or key extraction, JTAG/SWD or eMMC chip-off + external programmer are the last
resort. Documenting pinouts requires a unit on the bench (TODO).

## In-scope checklist (as we open a unit)

- [ ] Photograph the mainboard; identify SoC, eMMC, MCU, DLPC3430, XMOS, PMIC.
- [ ] Find + label the **debug UART** pads; capture a full boot log at both baud rates.
- [ ] Confirm maskrom entry (test point) and dump the loader.
- [ ] `rkdeveloptool` read-back of each partition (compare SHA-256 to [`firmware-803-reference.md`](firmware-803-reference.md)).
- [ ] Flash a `--disable-verification` vbmeta + a debuggable `system`; get root ADB.
- [ ] Map the Lizard MCU UART + its firmware-update header (see [`hardware-map.md`](hardware-map.md)).

---
📖 [Reverse-engineering index](README.md) · [Firmware image (build & sign)](firmware-image.md) · [OTA & recovery](ota-and-recovery.md) · [Hardware map](hardware-map.md) · [Docs index](../README.md)
