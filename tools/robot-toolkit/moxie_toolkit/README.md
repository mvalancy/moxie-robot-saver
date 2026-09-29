# 📦 `moxie_toolkit/` — the Python package

The importable toolkit: hand-written modules plus the generated protobuf bindings.

- [`qr_codec.py`](qr_codec.py) — encode/decode every QR `bo-wifi` accepts (`PA`, `VN`, JSON), mirroring `QRData.ParseFromString`.
- [`validate_qr.py`](validate_qr.py) — QR suite: schema round-trip plus byte-parity with the phone-side encoder (27 checks).
- [`validate_protos.py`](validate_protos.py) — cross-checks the recovered protos against OpenMoxie's independently compiled set.
- [`protoref.py`](protoref.py) — look up any message/enum in the 120 recovered protos (382 messages, 84 enums).
- [`cli.py`](cli.py) — the `moxie-qr` CLI: `pair`, `wifi`, `debug`, `endpoint`, `vpn`, `decode`, `proto`, `validate`, `list-commands`.
- [`bus.py`](bus.py) — `MoxieBus`, the on-device ZeroMQ bus client (face, motors, LEDs, sensors, runtime control); tunnel via `adb forward`.
- [`cloud.py`](cloud.py) — MQTT topic builders and envelopes, the `/commands/zmq` framing, telehealth, `service_configuration()`, config/telemetry, RemoteChat, offline brain-state and SEL-taxonomy parsers.
- [`markup.py`](markup.py) — build `<mark name="cmd:…">` behavior markup to weave into TTS text.
- [`embodied/`](embodied/README.md) — generated `*_pb2.py` bindings mirroring the firmware's proto packages. Regenerate with `grpc_tools.protoc` (command in the [toolkit README](../README.md)).

Each module's header cites the RE doc it implements.

---
📖 [robot-toolkit](../README.md) · [Back to top](../../../README.md)
