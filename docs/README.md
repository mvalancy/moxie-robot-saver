# Documentation

The complete map of how Moxie works and how we bring it back. Suggested reading order:

## Start here
1. [`../README.md`](../README.md) — the project and the vision.
2. [`../ROADMAP.md`](../ROADMAP.md) — the phased end-to-end plan.
3. [`architecture/overview.md`](architecture/overview.md) — how all the pieces fit together.
4. [`architecture/revival-path.md`](architecture/revival-path.md) — the exact steps + firmware gate to revive a robot.

## Reverse-engineering (source of truth)
Clean-room maps of the decompiled parent app. Everything else is derived from these.
- [`reverse-engineering/rest-api.md`](reverse-engineering/rest-api.md) — every endpoint, auth flow, headers, tokens.
- [`reverse-engineering/crypto-and-keys.md`](reverse-engineering/crypto-and-keys.md) — the one-seed key system, Argon2id, recovery keys, E2E encryption.
- [`reverse-engineering/pairing-and-robot.md`](reverse-engineering/pairing-and-robot.md) — pairing handshake, Wi-Fi provisioning, robot control API.
- [`reverse-engineering/qr-format.md`](reverse-engineering/qr-format.md) — the exact pairing-QR wire format.
- [`reverse-engineering/app-structure.md`](reverse-engineering/app-structure.md) — manifest, components, SDKs, packages.

## Features
Everything the parent app can do — so we can rebuild all of it, not just the happy path.
- [`features/`](features/) — the complete feature catalog, including hidden/developer features,
  factory reset, restore/backup, robot control, and more.

## Guides (for owners)
Task-oriented how-tos.
- [`guides/`](guides/) — first-time setup, factory-resetting a paired Moxie, finding Moxie on your LAN, etc.

## Context
- [`community-research.md`](community-research.md) — the existing revival community (OpenMoxie and friends) and where this project fits.

---
*Docs are versioned with the code. The reverse-engineering maps describe the app as of
`com.embo.embodied.parent` v2.2.2 (versionCode 249).*
