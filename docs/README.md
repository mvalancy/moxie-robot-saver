# Documentation

The complete map of Moxie, **end to end**. We reverse-engineer and rebuild the whole machine across
**three domains** — the **robot**, the **parent app** (phone), and the **server app** (the backend we
run to replace the dead cloud) — plus the shared protocol that ties them together.

> **Scope note.** This project covers everything: on-device firmware *and* hardware (flashing,
> teardown, USB, UART/TTL serial, JTAG), the phone app, and the replacement server. "No-disassembly"
> options are a *first tier* we exhaust for owners who can't open the robot — not the boundary.

## Start here
1. [`../README.md`](../README.md) — the project and the vision.
2. [`../ROADMAP.md`](../ROADMAP.md) — the phased end-to-end plan.
2b. [`../STRUCTURE.md`](../STRUCTURE.md) — how the repo is organized across the three domains.
3. [`reverse-engineering/FIELD-GUIDE.md`](reverse-engineering/FIELD-GUIDE.md) — everything organized by what you want to do.
4. [`reverse-engineering/architecture-diagrams.md`](reverse-engineering/architecture-diagrams.md) — the whole system as a hierarchy of diagrams (product → hardware → motor drivers).
5. [`architecture/`](architecture/README.md) — how we rebuild it: the [overview](architecture/overview.md), the [revival path](architecture/revival-path.md), and the spec layer (MQTT/conversation, the [AI seam](architecture/ai-seam.md), the platform SDK). The [architecture index](architecture/README.md) lists them all.

> The robot-side docs describe firmware **v3.6.4-Zephyr / OTA v24.10.803** — see
> [`reverse-engineering/firmware-803-reference.md`](reverse-engineering/firmware/firmware-803-reference.md).

## ① The Robot — the machine itself
Firmware, hardware, boot, on-device software. Everything on the device.
- [`reverse-engineering/firmware-803-reference.md`](reverse-engineering/firmware/firmware-803-reference.md) — version-stamped reference for the analyzed build.
- [`reverse-engineering/firmware-inventory.md`](reverse-engineering/firmware/firmware-inventory.md) — complete app + binary manifest (embodied vs stock).
- [`reverse-engineering/firmware-image.md`](reverse-engineering/firmware/firmware-image.md) — RK3288/Android 9 partitions, verified boot, unlock & flash custom firmware.
- [`reverse-engineering/hardware-access.md`](reverse-engineering/hardware/hardware-access.md) — the physical surface: maskrom/rockusb/fastboot, `rkdeveloptool`, UART/TTL serial console, JTAG (full teardown, in scope).
- [`reverse-engineering/hardware-map.md`](reverse-engineering/hardware/hardware-map.md) — motors, sensors, LED patterns, power rails (the Lizard MCU).
- [`reverse-engineering/boot-and-launcher.md`](reverse-engineering/firmware/boot-and-launcher.md) — the Launcher state machine + component supervision.
- [`reverse-engineering/ota-and-recovery.md`](reverse-engineering/firmware/ota-and-recovery.md) — A/B OTA machinery + tiered upgrade/revival vectors.
- [`reverse-engineering/robot-ipc-protocol.md`](reverse-engineering/protocol/robot-ipc-protocol.md) — the on-device ZeroMQ + protobuf bus.
- [`reverse-engineering/perception-pipeline.md`](reverse-engineering/runtime/perception-pipeline.md) — audio (XMOS→STT) & vision (faces/people/QR).
- [`reverse-engineering/factory-provisioning.md`](reverse-engineering/firmware/factory-provisioning.md) — production apps, serial/part grammar, factory secrets.

## ② The Parent app — the phone (`com.embo.embodied.parent` v2.2.2)
Clean-room maps of the original phone app, so we can rebuild its behavior.
- [`reverse-engineering/rest-api.md`](reverse-engineering/phone/rest-api.md) — every endpoint, auth flow, headers, tokens.
- [`reverse-engineering/crypto-and-keys.md`](reverse-engineering/phone/crypto-and-keys.md) — the one-seed key system, Argon2id, recovery keys, E2E.
- [`reverse-engineering/pairing-and-robot.md`](reverse-engineering/phone/pairing-and-robot.md) — pairing handshake, Wi-Fi provisioning, robot control API.
- [`reverse-engineering/qr-format.md`](reverse-engineering/phone/qr-format.md) — the pairing-QR wire format (as the phone emits it).
- [`reverse-engineering/app-structure.md`](reverse-engineering/phone/app-structure.md) — manifest, components, SDKs, packages.
- [`features/`](features/) — the complete parent-app **feature catalog** (hidden/developer features, factory reset, restore/backup, robot control).

## ③ The Server app — the backend we run
What a self-hosted replacement backend must implement (this repo's [`../server/`](../server/) + [`../mqtt/`](../mqtt/)).
The docs below are the reverse-engineered **facts**; the [`architecture/`](architecture/README.md) folder
**distills them into the build spec** — the [overview](architecture/overview.md), the
[REST services contract](architecture/rest-api-contract.md) (Channel 1, the parent-app server), the
[MQTT/conversation contract](architecture/mqtt-and-conversation.md) (Channel 2), and the
[AI seam](architecture/ai-seam.md) (the LLM/STT/TTS interface any backend fills).
- [`reverse-engineering/cloud-protocol.md`](reverse-engineering/protocol/cloud-protocol.md) — REST `client-service`, MQTT topics + envelope, device auth, STT.
- [`reverse-engineering/network-trust.md`](reverse-engineering/protocol/network-trust.md) — TLS trust model; the cert a server needs.
- [`reverse-engineering/content-and-conversation.md`](reverse-engineering/runtime/content-and-conversation.md) — ChatScript + LLM, content-module format, the `volley` API.
- [`reverse-engineering/behavior-markup.md`](reverse-engineering/runtime/behavior-markup.md) — the `<mark cmd:…>` language a server weaves into TTS.

## Shared — the protocol & interfaces (spans all three)
- [`reverse-engineering/qr-commands.md`](reverse-engineering/protocol/qr-commands.md) — the complete QR grammar the robot scans.
- [`reverse-engineering/recovered-proto/`](reverse-engineering/protocol/recovered-proto/) — 120 `.proto` files reconstructed from the robot binaries.
- [`reverse-engineering/proto-catalog.md`](reverse-engineering/protocol/proto-catalog.md) — browsable catalog of all 382 messages / 84 enums.
- [`../tools/robot-toolkit/`](../tools/robot-toolkit/) — the toolkit (QR, ZMQ bus, cloud helpers, protoref, secrets).

## Guides & context
- [`guides/`](guides/) — owner how-tos. **Start with [revive-your-moxie](guides/revive-your-moxie.md)**
  (the end-to-end path) or [one-command-stack](guides/one-command-stack.md) (`docker compose up`
  → the whole backend); then first-time setup, factory reset, find Moxie on LAN, and Cloudflare deploy.
- [`debugging/`](debugging/) — live hardware-debug notes, QR-command findings.
- [`community-research.md`](community-research.md) — the revival community (OpenMoxie & friends) and where we fit.
- [`architecture/openmoxie-feature-audit.md`](architecture/openmoxie-feature-audit.md) — the deep, cited
  feature-by-feature audit of OpenMoxie + its forks: what to adopt, and where to go beyond.

## 🌳 How this documentation tree is maintained (SOP)

These docs are a **tree we grow, not a pile we polish** — the message must stay consistent from the root
`README.md` down to every leaf. That consistency is a **standing step in every session loop**, not an
occasional cleanup:

1. **One message, root to leaf.** The framing at the top (revive the experience *and* put any AI inside
   Moxie — a [ghost in the shell](../README.md)) must hold at every level below it. When a finding
   changes the story, push the change **upward** — the leaf doc, its subfolder README, the section index,
   and (if it changes the headline) `docs/README.md` and the root `README.md` — never leave two levels
   disagreeing.
2. **Every folder is navigable.** Each docs subfolder with ≥2 pages carries a `README.md` that indexes it
   and links back up; parent indexes link *down*. New pages join the curated order, not an A–Z tail.
   (Guarded by `sim/test_docs.mjs`.)
3. **Retire, don't strand.** When a finding supersedes an earlier belief, update or banner the old page in
   the same pass — no doc should still assert something we've since disproven (e.g. "hunt for hidden QR
   codes" after the grammar was proven closed).
4. **Stamp the build.** Robot-side pages are stamped with the analyzed firmware (`v24.10.803`); the RE
   [methodology](reverse-engineering/METHODOLOGY.md) and per-iteration loop keep new pages consistent.
5. **Verify mechanically.** `python3 sim/tools/build_docs_bundle.py` → `node sim/test_docs.mjs` →
   `python3 scripts/check-doc-links.py` → `python3 scripts/check-doc-consistency.py` (stale-message +
   version-stamp guard). The tree must pass these before a commit.

See [`reverse-engineering/METHODOLOGY.md`](reverse-engineering/METHODOLOGY.md) for the full RE loop and
tool tiers (including Ghidra for native decompilation).

---
*Robot-side docs: firmware **v3.6.4-Zephyr / OTA v24.10.803**. Parent-app docs:
`com.embo.embodied.parent` v2.2.2 (versionCode 249). Docs are versioned with the code.*
