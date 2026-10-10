# Documentation

This documentation covers the whole Moxie system: the **robot** itself, the original **parent app**
on the phone, and the **server** we run in place of the dead cloud. The guides are written for owners
and parents, the architecture pages for anyone building or changing the backend, and the
reverse-engineering study for anyone who wants to know how the original works. These are the same
docs the hosted site's explorer serves.

## Start here

| If you are… | Read |
|---|---|
| New to the project | The [project README](../README.md), then the [roadmap](../ROADMAP.md) |
| A parent | [Child safety](guides/child-safety.md), [what Moxie remembers](guides/what-moxie-remembers.md), [permitting a robot](guides/permitting-a-robot.md) |
| Reviving a robot | [Revive your Moxie](guides/revive-your-moxie.md) |
| Running the backend at home | [One-command stack](guides/one-command-stack.md) |
| Trying Moxie with no robot | [The simulator](../sim/README.md): hosted, or on your own machine with one Docker command |
| Deploying your own hosted simulator | [Deploy on Cloudflare](guides/deploy-cloudflare.md) |
| Building or changing the backend | [Architecture and contracts](architecture/README.md) |
| Contributing | [How we write docs, and the guards](../CONTRIBUTING.md); [branches, CI and releases](../RELEASING.md) |
| Studying the original | The [reverse-engineering index](reverse-engineering/README.md), then the [field guide](reverse-engineering/FIELD-GUIDE.md) |
| Finding your way around the repo | [`STRUCTURE.md`](../STRUCTURE.md) |

## Sections

- [`guides/`](guides/README.md) — how-tos for owners, parents and anyone running the backend.
- [`architecture/`](architecture/README.md) — how the replacement is built, and the contracts it is built
  from.
- [`features/`](features/README.md) — what the original parent app did, feature by feature.
- [`design/`](design/README.md) — the visual style of Moxie's web pages.
- [`debugging/`](debugging/README.md) — notes from debugging real robots.
- [`reverse-engineering/`](reverse-engineering/README.md) — the clean-room study of the robot, the phone
  app and the cloud protocol. This is the source of truth the contracts cite.
- [`community-research.md`](community-research.md) — other revival projects (OpenMoxie and forks) and
  where this one fits.

## The reverse-engineering study

One index per part of the system; every page is one hop down from its index.

- [FIELD-GUIDE](reverse-engineering/FIELD-GUIDE.md) — the facts organized by goal: revive a robot, put
  any AI inside, write custom firmware.
- [EXPLORATION-MAP](reverse-engineering/EXPLORATION-MAP.md) — the status board: what is covered, and
  the open items.
- [`phone/`](reverse-engineering/phone/README.md) — the parent app (`com.embo.embodied.parent` v2.2.2):
  REST API, crypto and keys, pairing, QR format, app structure.
- [`protocol/`](reverse-engineering/protocol/README.md) — the cloud protocol, network trust, on-device
  IPC, QR commands and the recovered protobufs.
- [`runtime/`](reverse-engineering/runtime/README.md) — the brain and face at run time: content and
  conversation, behavior markup, perception.
- [`firmware/`](reverse-engineering/firmware/README.md) — the robot's firmware (v3.6.4-Zephyr / OTA
  v24.10.803): the image, boot and launcher, OTA and recovery, flashing.
- [`hardware/`](reverse-engineering/hardware/README.md) — the board: hardware access and the hardware
  map.

## Maintaining these docs

Three rules, and the guards that enforce them:

- Every folder has a `README.md` that indexes its pages and links its parent. The explorer orders a
  section by its section README's link list (`sim/tools/build_docs_bundle.py`); subfolder READMEs
  index for readers. A finding that changes the story is fixed on every page that states the old
  belief, in the same change.
- Robot-side reverse-engineering pages carry the firmware stamp `v24.10.803`.
- Before committing, run the guards and commit the rebuilt bundle with the doc:
  `python3 sim/tools/build_docs_bundle.py && python3 sim/tools/check_bundle_fresh.py &&
  node sim/test_docs.mjs && python3 scripts/check-doc-links.py &&
  python3 scripts/check-doc-consistency.py &&
  python3 -m pytest -q sim/tests/test_hosted_docs_truth.py sim/tests/test_no_offsite_images.py`

The full Style Card and procedure: [`CONTRIBUTING.md`](../CONTRIBUTING.md). Research method:
[`reverse-engineering/PLAYBOOK.md`](reverse-engineering/PLAYBOOK.md).

---
[Project README](../README.md) · [Contributing](../CONTRIBUTING.md)
