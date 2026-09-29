# Repository structure

The project covers three things: the **robot** (firmware and hardware), the original **parent app**
on the phone, and the **server** we run in place of the dead cloud. Every top-level folder belongs to
one of them.

| Path | Part | What it is |
|---|---|---|
| [`server/`](server/) | Server | Parent-app side of the backend: the REST API the phone app used, plus the phone web app and parent console (FastAPI). |
| [`mqtt/`](mqtt/) | Server | Robot side of the backend: broker config, the supervisor that speaks Moxie's protocol, and the Moxie SDK (brain, voice, content). |
| [`docker-compose.yml`](docker-compose.yml), [`.env.example`](.env.example) | Server | The one-command stack: broker, supervisor and console from one `.env` ([guide](docs/guides/one-command-stack.md)). [`docker-compose.images.yml`](docker-compose.images.yml) is the same stack from prebuilt images. |
| [`sim/`](sim/) | Server / demo | The browser simulator and virtual robot, the static site in `sim/web/`, the test suites in `sim/tests/`, and the CI workflow templates in `sim/ci/`. |
| [`functions/`](functions/README.md) | Server / demo | Cloudflare Pages Functions behind the hosted site (`/api/*`). Secrets are runtime bindings, never committed. |
| [`tools/`](tools/) | Robot and phone | Pairing and endpoint QR tools, the robot toolkit (ZMQ bus client, protobuf bindings), and a QR camera rig. |
| [`hardware/`](hardware/) | Robot | The physical robot: OS, firmware versions, finding it on the network. |
| [`ai/`](ai/) | Server | Notes on the AI adapters (the code lives in `mqtt/moxie_sdk/`). |
| [`docs/`](docs/README.md) | All | Guides, architecture contracts, and the reverse-engineering study. |
| [`scripts/`](scripts/) | Repo | Maintenance checks (doc links, doc consistency, mermaid) and PR helpers. |
| [`.claude/`](.claude/) | Repo | Shared Claude agents and skills, so the project's know-how travels with the repo. |
| [`.github/workflows/`](.github/workflows/) | Repo | Installed CI workflows; the editable templates are in `sim/ci/` ([RELEASING.md](RELEASING.md)). |

## Why the backend is two folders

The backend is split by who connects to it. The **phone** talks REST to `server/`; the **robot**
talks MQTT to `mqtt/`. They share one compose stack and one `.env`, and each runs on its own.

## Conventions

- Every folder has a `README.md` (generated protobuf trees have one at their root).
- Robot-side reverse-engineering docs are stamped with the analyzed firmware, v3.6.4-Zephyr /
  OTA v24.10.803.
- Run [`scripts/check-doc-links.py`](scripts/check-doc-links.py) before committing docs.

---
[Docs index](docs/README.md) · [Field guide](docs/reverse-engineering/FIELD-GUIDE.md)
