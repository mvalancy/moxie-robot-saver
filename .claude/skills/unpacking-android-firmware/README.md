# 🛠️ Unpacking Android firmware + first inventory

Acquire and unpack an Android device's firmware (OTA payload.bin or factory images) into readable partitions, and inventory its apps, native libs, init services, permissions, and device-tree. Use at the start of reverse-engineering an Android robot/appliance, or when you need a file off a system/vendor/oem/boot image.

Invoke it by name (`unpacking-android-firmware`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- Acquire the firmware
- Unpack partitions (no mounting needed)
- Inventory (write these down — they anchor everything)
- Worked example (Moxie, v24.10.803)

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
