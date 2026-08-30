# 🦾 Hardware — the physical board & teardown

The **physical robot** — board, motors, sensors, LEDs, power, and the FCC teardown.

- [`hardware-map.md`](hardware-map.md) — motors, touch/switch/IMU sensors, LED face patterns, and power rails, from the MCU protobufs.
- [`device-tree.md`](device-tree.md) — board-level hardware wiring from the DTB (I²C/UART/display/camera/PMIC map) + the decompiled `.dts`.
- [`hardware-access.md`](hardware-access.md) — the **physical surface**: maskrom/rockusb/fastboot, `rkdeveloptool` flashing, the **UART/TTL serial console** (`ttyFIQ0`), JTAG — the full teardown path (in scope).
- [`fcc-teardown.md`](fcc-teardown.md) — **board-level hardware map from the FCC filings** (rev1 vs rev2): full chip inventory, per-chip programmer/IDE/toolchain, the `LOAD` download button + STM32 `ISP & DEBUG` header, all cited to exhibit pages.

---
📖 [Reverse-engineering index](../README.md) · [Coverage](../COVERAGE.md) · [Exploration map](../EXPLORATION-MAP.md)
