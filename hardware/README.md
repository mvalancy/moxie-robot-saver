# `hardware/` — the robot itself

Reference facts about the Moxie hardware.

> ⚠️ **Authoritative source:** the deep, version-stamped hardware analysis lives in
> [`../docs/reverse-engineering/`](../docs/reverse-engineering/) (firmware **v3.6.4-Zephyr / OTA
> v24.10.803**). This folder is a short pointer + the external-research track; where they differ, the
> reverse-engineering docs win. An earlier version of this file guessed the platform from Moxie's
> Intrinsyc/Lantronix association — **corrected below** against the actual firmware.

## What Moxie is (corrected against the firmware)
- A **Rockchip RK3288** (ARMv7 Cortex-A17) **Android 9** device — **not** a Qualcomm/Intrinsyc Open-Q
  board, as sometimes assumed. AVB-signed A/B, verity-enforcing. See
  [`firmware-image.md`](../docs/reverse-engineering/firmware-image.md) and the
  [803 reference](../docs/reverse-engineering/firmware-803-reference.md).
- The animated face is a **DLP projector** (DLPC3430) throwing onto a fresnel-lens faceplate — Unity
  renders the face. See [`hardware-map.md`](../docs/reverse-engineering/hardware-map.md).
- Body is a **"Lizard" STM32 MCU** (motors/touch/IMU/LED/battery); audio via an **XMOS** DSP. Three
  processors total — [`perception-pipeline.md`](../docs/reverse-engineering/perception-pipeline.md).
- **Wi-Fi/BT is a Broadcom `BCM4339` (AzureWave `AP6335`)** combo — **not** an AMPAK module. Wi-Fi over
  SDIO, BT over UART0. See [`device-tree.md`](../docs/reverse-engineering/device-tree.md).
- Lantronix (ex-Intrinsyc) provided **Secure Boot + AVB + a camera auto-exposure library** as
  engineering services ([case study](https://www.lantronix.com/resources/case-studies/moxie/); mapped
  in [`external-sources.md`](../docs/reverse-engineering/external-sources.md)).

## Scope: the whole machine, end to end
We map and revive Moxie across **every tier** — non-invasive *and* invasive:
1. **Tier 1 — no-disassembly** (best for a non-technical owner): camera-QR re-home, network, OTA/config.
2. **Tier 2 — external ports**: USB (rockusb/fastboot), the UART/TTL serial console.
3. **Tier 3 — full teardown & flashing**: maskrom/`rkdeveloptool`, disable/re-sign AVB, test points, JTAG.

Tier 1 first only because it's cheapest for the owner — **teardown, USB, TTL, and flashing are planned
and fully in scope**. The physical/flashing surface is in
[`hardware-access.md`](../docs/reverse-engineering/hardware-access.md); build & sign in
[`firmware-image.md`](../docs/reverse-engineering/firmware-image.md).

## Files here
- [`firmware-and-older-robots.md`](firmware-and-older-robots.md) — the pre-801 / older-robot revival
  research track (reconciled with the current RE).

## External work (teardowns, FCC, community)
Mapped, with a provenance/licensing policy, in
[`../docs/reverse-engineering/external-sources.md`](../docs/reverse-engineering/external-sources.md)
(FCC ID `2AV9N-EMBODIEDMOXIEA`, teardown videos, OpenMoxie, Lantronix, press).

---
📖 [Reverse-engineering index](../docs/reverse-engineering/README.md) · [Field guide](../docs/reverse-engineering/FIELD-GUIDE.md) · [Back to top](../README.md)
