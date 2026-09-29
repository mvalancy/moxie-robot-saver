# tools

Command-line utilities that work without the server running.

| Folder | What |
|---|---|
| [`pairing/`](pairing/README.md) | The clean-room pairing-QR codec and CLI (`moxie_qr.py`, `moxie_pair.py`), and `moxie_endpoint_qr.py`, which makes the QR that points a robot at your broker. |
| [`robot-toolkit/`](robot-toolkit/README.md) | The Moxie protocol toolkit: QR codec, a client for the robot's on-device ZeroMQ bus, cloud MQTT/REST helpers, `protoref`, a secrets extractor, and Python bindings for the 120 recovered `.proto` files. |
| [`qr-rig/`](qr-rig/README.md) | A camera rig that shows QR codes to a real Moxie and records how it reacts. Used to validate codes we already know the firmware parses; the QR grammar itself is fully mapped ([QR commands](../docs/reverse-engineering/protocol/qr-commands.md)). |
