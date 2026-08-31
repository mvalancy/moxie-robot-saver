# 🔑 `tools/`

Standalone utilities that don't need the server running.

- [`robot-toolkit/`](robot-toolkit/) — the **Moxie protocol toolkit**: the QR codec, the ZMQ `MoxieBus`
  client, cloud MQTT/REST helpers, `protoref`, the secrets extractor, and the 120 recovered proto
  bindings. The Python surface for scripting the reverse-engineered protocol.
- [`pairing/`](pairing/) — the clean-room Moxie **pairing-QR** codec and CLI. Generate the QR you
  hold up to Moxie's camera, straight from the command line.
- [`qr-rig/`](qr-rig/) — a **camera QR validation rig**: check that a generated QR actually decodes
  the way Moxie's scanner would read it.

---
📖 [Back to top](../README.md)
