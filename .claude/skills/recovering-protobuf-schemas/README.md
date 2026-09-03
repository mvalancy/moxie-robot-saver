# 🛠️ Recovering protobuf schemas from binaries

Reconstruct a device's exact protobuf .proto schemas from its shipped binaries (the embedded FileDescriptorProto), giving wire-compatible message definitions for its protocol. Use when a device speaks protobuf over MQTT/ZMQ/gRPC/a custom bus and you need the message/field/enum definitions to build a compatible client or server.

Invoke it by name (`recovering-protobuf-schemas`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- Where the descriptors live
- Reconstruct the .proto
- Verify + catalog
- Worked example (Moxie)

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
