---
name: mapping-robot-hardware
description: Map a robot/appliance's hardware from its firmware — the device-tree (buses, GPIOs, display path), the init service graph, and the multi-processor layout (SoC + MCUs + DSP) with each one's firmware-update path. Use to understand or rebuild the physical/driver layer for custom firmware, or to find how motors/sensors/audio are actually driven.
---

# Mapping robot hardware from firmware

Goal: the wiring diagram a custom firmware (or a curious engineer) needs — which chips exist, on which
buses, and how each is driven + updated. Phase 6 of `reverse-engineering-android-robots`.

## The device-tree = the wiring diagram
Extract the `.dtb` (from `boot.img`/`dtbo.img`) and decompile:
```bash
dtc -I dtb -O dts robot.dtb > robot.dts
```
Read out of `robot.dts`:
- **Buses + what's on them:** `i2cN`/`spiN`/`uartN` nodes and their child `@addr` devices with
  `compatible = "vendor,part"` → the exact ICs (sensor, motor controller, display controller, fuel gauge).
- **The display path** (for a screen/projector robot): find the panel (`simple-panel`/DSI/RGB) → the
  display controller (over I²C) → GPIO enable/backlight → any fan PWM. This is often the "face".
- **GPIOs / regulators / PWMs:** power rails, enables, button lines, fan control.
- **Pin mux + interrupts** tie it together.

## The init service graph = what runs
From the ramdisk `*.rc`: every `service`, its `class`/triggers/`user`/`seclabel`, and the `on <trigger>`
blocks. Separate the vendor's **own daemons** (the interesting few — LED/fan/sensor helpers, the launcher/
watchdog) from the ~90 stock AOSP services. `seapp_contexts`/`*_contexts` (SELinux) label the custom
device nodes and daemons.

## The multi-processor pattern (very common)
These devices usually have **more than one processor**, each with its own firmware updated from Android:
- **The SoC** (RK/Qualcomm) — runs Android; updated by A/B `update_engine` OTA (signed `payload.bin`).
- **One or more MCUs** (e.g. STM32) — motors, touch, IMU, LEDs, battery/power. Updated over **UART** with a
  vendor bootloader, image usually **Intel HEX** to a flash base (`0x08000000` on STM32). Find the JNI/
  native updater (`sendBootLoaderPkt`-style) + the `.hex` images shipped in an update app.
- **A DSP** (e.g. XMOS) — mic array / AEC / wake-word. Updated over **USB DFU** (libusb; the chip enumerates
  as its vendor's USB VID). Find the `.bin` images + the DFU orchestration (variant select + version-gate).
For each: note the transport, the image format, the trigger (a watchdog/version check at boot), and whether
a custom firmware can reflash it from userspace (usually yes for MCU/DSP; the SoC is gated by AVB/locked bootloader).

## Cross-reference with the native HAL + the MCU protocol
The device-tree tells you the chip; the native lib (`decompiling-native-arm-libraries`) tells you the
**protocol** to it (the UART command set, the I²C register writes). And the recovered protos
(`recovering-protobuf-schemas`) often expose the same operations as bus messages.

## Worked example (Moxie)
RK3288 SoC + **Lizard STM32 MCU** (motors/touch/IMU/LEDs/battery, UART `/dev/ttyS3`, "GOBY" bootloader,
Intel HEX @ `0x08000000`) + **XMOS DSP** (mic array/AEC/wake, USB DFU, VID `0x20B1`). The DLP face
projector is a `DLPC3430` on i2c5 @0x1b driven as an RGB panel via the VOP, GPIO7 backlight, PWM fan.
Only ~2 of ~97 init services are vendor daemons (`projectorfanpid`, LED). Findings:
`docs/reverse-engineering/hardware/{device-tree,hardware-map,hardware-access,fcc-teardown}.md` +
`firmware/hal-and-drivers.md` + the XMOS/MCU update chains in `runtime/perception-pipeline.md`.
