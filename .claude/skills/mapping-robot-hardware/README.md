# 🛠️ Mapping robot hardware from firmware

Map a robot/appliance's hardware from its firmware — the device-tree (buses, GPIOs, display path), the init service graph, and the multi-processor layout (SoC + MCUs + DSP) with each one's firmware-update path. Use to understand or rebuild the physical/driver layer for custom firmware, or to find how motors/sensors/audio are actually driven.

Invoke it by name (`mapping-robot-hardware`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- The device-tree = the wiring diagram
- The init service graph = what runs
- The multi-processor pattern (very common)
- Cross-reference with the native HAL + the MCU protocol
- Worked example (Moxie)

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
