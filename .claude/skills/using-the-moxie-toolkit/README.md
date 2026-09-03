# 🛠️ Using the Moxie toolkit

Use the moxie-robot toolkit programmatically — generate/decode the QR codes a stock robot scans, query the 120 recovered protobuf schemas, drive the on-device ZeroMQ bus, build cloud MQTT/REST messages, and emit behavior markup. Use when scripting against a Moxie robot or a revival server, or reusing the recovered protocol from Python.

Invoke it by name (`using-the-moxie-toolkit`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- QR channel — the one input a stock robot acts on with no disassembly
- Query the recovered protocol (382 messages / 84 enums)
- Drive the on-device ZeroMQ bus (`bus.py` → `MoxieBus`)
- Build cloud messages (`cloud.py`)
- Emit behavior markup (`markup.py`)
- Secrets extractor

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
