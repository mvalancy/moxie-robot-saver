# 📱 Phone side — the parent app

Clean-room study of the original **parent app** (`com.embo.embodied.parent` v2.2.2). It covers how the phone signs in, pairs a
robot through a QR code, and controls the robot through the cloud. Each fact lives in one file, and the others link to it.

| Doc | Canonical for |
|---|---|
| [`rest-api.md`](rest-api.md) | Base URLs, headers, OAuth client credentials, the passwordless email→OAuth flow, token shapes, status codes, every endpoint, and the minimum server surface |
| [`pairing-and-robot.md`](pairing-and-robot.md) | The pairing sequence and success detection, Wi-Fi rules, robot control request/response bodies, and restore/unpair flows |
| [`qr-format.md`](qr-format.md) | Byte-exact pairing QR (`PA`+protobuf and legacy JSON), mode selection, rendering |
| [`crypto-and-keys.md`](crypto-and-keys.md) | The single 32-byte seed (Argon2id, zero salt) → Ed25519/X25519/secretbox, the recovery phrase, `secret-key-collection`, child-PII encryption, AUID |
| [`app-structure.md`](app-structure.md) | Manifest and components, network-security posture (no pinning), Firebase config, SDKs, packages, hostnames |
| [`keys/`](keys/README.md) | Notes on public keys recovered from the robot firmware (OTA verification) |

The robot side of the same handshake is in [`../protocol/qr-commands.md`](../protocol/qr-commands.md) and
[`../protocol/cloud-protocol.md`](../protocol/cloud-protocol.md). Our implementations are in
[`server/`](../../../server/) and [`tools/pairing/`](../../../tools/pairing/).

---
📖 [Reverse-engineering index](../README.md) · [Exploration map](../EXPLORATION-MAP.md)
