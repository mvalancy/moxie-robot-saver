# 🦾 Hardware — the physical board & teardown

The physical robot as seen from firmware **v24.10.803** and the public FCC filings: board wiring,
motors and sensors, and how to get into it.

| Doc | What it covers |
|---|---|
| [`hardware-map.md`](hardware-map.md) | Motors, touch/switch/IMU, LEDs, power rails, the Lizard MCU (DFU, UART opcodes), from the MCU protobufs and factory apps |
| [`device-tree.md`](device-tree.md) | Board wiring from the DTB: I²C/UART map, display/camera path, buttons, PMIC rails |
| [`hardware-access.md`](hardware-access.md) | Boot/download modes and how to enter them, partition names, serial console, ADB/USB, JTAG |
| [`fcc-teardown.md`](fcc-teardown.md) | FCC filings (rev1 vs rev2): chip inventory with provenance, `LOAD` button, Lizard `ISP & DEBUG` header, per-chip toolchains |

The flash procedure itself is the [flashing runbook](../firmware/flashing-runbook.md).

---
📖 [Reverse-engineering index](../README.md) · [Exploration map](../EXPLORATION-MAP.md)
