# 📱 Phone side — the parent app

The **parent app** (`com.embo.embodied.parent`) — how the phone pairs with, provisions, and controls a robot.

- [`rest-api.md`](rest-api.md) — every endpoint, the passwordless-email→OAuth flow, headers, token shapes.
- [`crypto-and-keys.md`](crypto-and-keys.md) — the one 32-byte seed (Argon2id) → Ed25519/X25519/secretbox, recovery keys, E2E encryption.
- [`pairing-and-robot.md`](pairing-and-robot.md) — the pairing handshake, Wi-Fi provisioning, robot control API.
- [`qr-format.md`](qr-format.md) — the exact pairing-QR wire format (protobuf + legacy JSON), as the phone emits it.
- [`app-structure.md`](app-structure.md) — manifest, components, third-party SDKs, package inventory.

---
📖 [Reverse-engineering index](../README.md) · [Coverage](../COVERAGE.md) · [Exploration map](../EXPLORATION-MAP.md)
