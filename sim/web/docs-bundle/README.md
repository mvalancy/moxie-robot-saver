# Documentation

Everything about Moxie, in three parts: the **robot** itself, the original **parent app** on the
phone, and the **server** we run to replace the dead cloud.

## Start here

| If you want to… | Read |
|---|---|
| Understand the project | [Project README](../README.md), then the [roadmap](../ROADMAP.md) |
| Revive a real robot | [Revive your Moxie](guides/revive-your-moxie.md) |
| Run the backend at home | [One-command stack](guides/one-command-stack.md) |
| Build or change the backend | [Architecture and contracts](architecture/README.md) |
| Find your way around the repo | [`STRUCTURE.md`](../STRUCTURE.md) |
| Dig into how the original works | [Reverse-engineering field guide](reverse-engineering/FIELD-GUIDE.md) |

## Sections

- [`guides/`](guides/README.md) — how-tos for owners and operators.
- [`architecture/`](architecture/README.md) — how the replacement is built, and the contracts it is built
  from.
- [`features/`](features/README.md) — what the original parent app did, feature by feature.
- [`design/`](design/README.md) — the visual style of Moxie's web pages.
- [`debugging/`](debugging/README.md) — notes from debugging real robots.
- [`reverse-engineering/`](reverse-engineering/README.md) — the clean-room study of the robot, the phone
  app and the cloud protocol. This is the source of truth the contracts cite.
- [`community-research.md`](community-research.md) — other revival projects (OpenMoxie and forks) and
  where this one fits.

## Reverse-engineering highlights

**The robot** (firmware v3.6.4-Zephyr / OTA v24.10.803):
[firmware reference](reverse-engineering/firmware/firmware-803-reference.md) ·
[firmware image and flashing](reverse-engineering/firmware/firmware-image.md) ·
[hardware access](reverse-engineering/hardware/hardware-access.md) ·
[hardware map](reverse-engineering/hardware/hardware-map.md) ·
[boot and launcher](reverse-engineering/firmware/boot-and-launcher.md) ·
[OTA and recovery](reverse-engineering/firmware/ota-and-recovery.md) ·
[on-device IPC](reverse-engineering/protocol/robot-ipc-protocol.md) ·
[perception](reverse-engineering/runtime/perception-pipeline.md)

**The parent app** (`com.embo.embodied.parent` v2.2.2):
[REST API](reverse-engineering/phone/rest-api.md) ·
[crypto and keys](reverse-engineering/phone/crypto-and-keys.md) ·
[pairing](reverse-engineering/phone/pairing-and-robot.md) ·
[QR format](reverse-engineering/phone/qr-format.md) ·
[app structure](reverse-engineering/phone/app-structure.md)

**The cloud protocol:**
[cloud protocol](reverse-engineering/protocol/cloud-protocol.md) ·
[network trust](reverse-engineering/protocol/network-trust.md) ·
[content and conversation](reverse-engineering/runtime/content-and-conversation.md) ·
[behavior markup](reverse-engineering/runtime/behavior-markup.md) ·
[QR commands](reverse-engineering/protocol/qr-commands.md) ·
[recovered protobufs](reverse-engineering/protocol/proto-catalog.md)

## Maintaining these docs

- Every docs folder with two or more pages has a `README.md` that lists them in reading order; the docs
  explorer follows that order.
- When a finding changes the story, fix every page that states the old belief in the same change.
- Robot-side reverse-engineering pages carry the firmware stamp `v24.10.803`.
- Before committing: `python3 sim/tools/build_docs_bundle.py`, `node sim/test_docs.mjs`,
  `python3 scripts/check-doc-links.py`, `python3 scripts/check-doc-consistency.py`.

Research method: [`reverse-engineering/METHODOLOGY.md`](reverse-engineering/METHODOLOGY.md).
