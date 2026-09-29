# hardware — the robot itself

A short summary of the Moxie hardware. The detailed, version-stamped analysis (firmware
v3.6.4-Zephyr / OTA v24.10.803) is in [`../docs/reverse-engineering/`](../docs/reverse-engineering/README.md);
where the two differ, that wins.

## What Moxie is made of

- **Main board:** a Rockchip **RK3288** (32-bit ARM) running **Android 9**, with signed A/B
  partitions and verified boot. ([firmware image](../docs/reverse-engineering/firmware/firmware-image.md),
  [803 reference](../docs/reverse-engineering/firmware/firmware-803-reference.md))
- **Face:** a **DLP projector** (DLPC3430) shining onto the faceplate; Unity renders the face.
  ([hardware map](../docs/reverse-engineering/hardware/hardware-map.md))
- **Body:** an STM32 microcontroller ("Lizard") for motors, touch, IMU, LEDs and battery; audio goes
  through an **XMOS** DSP. ([perception pipeline](../docs/reverse-engineering/runtime/perception-pipeline.md))
- **Wireless:** an AmPak **AP6335** module (Broadcom BCM4339): Wi-Fi over SDIO, Bluetooth over UART.
  ([device tree](../docs/reverse-engineering/hardware/device-tree.md))
- Lantronix (formerly Intrinsyc) supplied secure boot, verified boot and a camera auto-exposure library
  as engineering services ([external sources](../docs/reverse-engineering/external-sources.md)).

## Ways in, from least to most invasive

1. **No disassembly:** QR codes shown to the camera, the network, OTA and config.
2. **External ports:** USB (rockusb/fastboot) and the UART serial console.
3. **Full teardown:** maskrom mode and `rkdeveloptool`, re-signing or disabling verified boot, test
   points, JTAG.

All three are in scope; the first is simply cheapest for an owner. Details:
[hardware access](../docs/reverse-engineering/hardware/hardware-access.md).

## Files here

- [`firmware-and-older-robots.md`](firmware-and-older-robots.md) — how robots older than firmware
  24.10.801 might be revived.

FCC filings, teardown videos and other outside sources are catalogued in
[`external-sources.md`](../docs/reverse-engineering/external-sources.md).
