---
name: recovering-protobuf-schemas
description: Reconstruct a device's exact protobuf .proto schemas from its shipped binaries (the embedded FileDescriptorProto), giving wire-compatible message definitions for its protocol. Use when a device speaks protobuf over MQTT/ZMQ/gRPC/a custom bus and you need the message/field/enum definitions to build a compatible client or server.
---

# Recovering protobuf schemas from binaries

Most Android robots/appliances speak **protobuf** on the wire (over MQTT, ZeroMQ, gRPC, or a custom bus).
You almost never need the vendor's `.proto` files — **the compiler embeds them**. Every `protoc`-generated
class carries its own serialized `FileDescriptorProto` (the schema, minus comments/options). Decode those
and you get back the exact IDL — field numbers, types, enum values, packages, nesting — so your bindings
are **wire-compatible** with the stock firmware.

## Where the descriptors live
- **Unity/C# (Mono):** in `Assembly-CSharp.dll` / `Embodied.Protos.dll` etc., each message type exposes a
  base64 `descriptor` blob (look for `Google.Protobuf` / `FileDescriptor.FromGeneratedCode` / a big base64
  string near the type). In the decompiled C#, grep for the type name + `descriptorData` / `ByteString.FromBase64`.
- **C++/native:** the `.so` embeds `descriptor_table_<path>_2eproto` symbols and the serialized
  `FileDescriptorProto` bytes in `.rodata` (visible in `strings` as `…/foo.proto` plus the field-name
  strings). Python-generated `_pb2.py` files (if any ship) literally contain `AddSerializedFile(b'…')`.

## Reconstruct the .proto
Feed the serialized descriptor bytes to protobuf's own parser and pretty-print IDL:
```python
from google.protobuf import descriptor_pb2
fdp = descriptor_pb2.FileDescriptorProto()
fdp.ParseFromString(raw_descriptor_bytes)      # base64-decode first if it was base64
# walk fdp.message_type / .enum_type / .field and emit `.proto` text (types, numbers, labels, nesting)
```
(Or reuse an existing extractor — e.g. `protobuf-inspector`, or the approach in the Moxie toolkit.) Then:
```bash
protoc --python_out=. --proto_path=. $(find . -name '*.proto')   # or --cpp_out / --go_out / --java_out
```
Field numbers are preserved, so the generated bindings serialize/parse **identically** to the device.

## Verify + catalog
- **Compile-check** every recovered `.proto` under `protoc` (a clean compile means the schema is internally consistent).
- **Round-trip cross-check** against any independent implementation of the protocol (a community server, a captured message) — byte-identical encoding confirms fidelity.
- **Catalog** them: one browsable page listing every message with its fields + every enum with its values. This is the protocol source-of-truth and is trivially clean-room-complete (the `.proto` *is* the data).

## Worked example (Moxie)
Recovered **120 `.proto` files** (382 messages, 84 enums, 17 namespaces) from the base64 descriptors in
`Embodied.Protos.dll` / `WifiApp.Protos.dll` / `Assembly-CSharp.dll`. All compile under `protoc`; a
round-trip cross-check against the community **OpenMoxie** server matched byte-for-byte. Output:
`docs/reverse-engineering/protocol/recovered-proto/**` + `proto-catalog.md`, queryable via the toolkit's
`protoref` (`using-the-moxie-toolkit`). These recovered protos are what let the revival server + `MoxieBus`
speak the robot's exact wire format.
