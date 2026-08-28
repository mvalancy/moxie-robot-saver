# `moxie_toolkit/` — the Python package

The importable toolkit. Hand-written modules plus a generated protobuf tree.

**Modules:** `qr_codec` · `validate_qr` · `validate_protos` · `protoref` · `bus` (on-device ZMQ) ·
`cloud` (MQTT transport) · `markup` (behavior tags) · `cli` (the `moxie-qr` CLI). See the
[toolkit README](../README.md) for usage.

**`embodied/…`** is the **generated** protobuf bindings tree (`*_pb2.py`), mirroring the firmware's
proto packages — regenerate via `gen_catalog.py`/protoc; no per-folder READMEs (machine-generated).
