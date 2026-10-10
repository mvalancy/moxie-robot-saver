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
| [`hardware/`](hardware/) | Robot | The physical robot: what it is made of, and firmware versions. |
| [`ai/`](ai/) | Server | One README pointing at the SDK's AI adapters (`mqtt/moxie_sdk/`); no code. |
| [`docs/`](docs/README.md) | All | Guides, architecture contracts, and the reverse-engineering study. |
| [`scripts/`](scripts/) | Repo | Maintenance checks (doc links, doc consistency, mermaid) and PR helpers. |
| [`.claude/`](.claude/) | Repo | Shared Claude agents and skills, so the project's know-how travels with the repo. |
| [`.github/workflows/`](.github/workflows/) | Repo | Installed CI workflows; the editable templates are in `sim/ci/` ([RELEASING.md](RELEASING.md)). |
| [`CONTRIBUTING.md`](CONTRIBUTING.md), [`RELEASING.md`](RELEASING.md) | Repo | How we write docs and run the guards; branches, CI and releases. |

## Why the backend is two folders

The phone talks REST to `server/` and the robot talks MQTT to `mqtt/`; they share one compose stack
and one `.env`, and each runs on its own ([how it fits together](README.md#how-it-fits-together)).

## Conventions

Every folder has a `README.md`, including every generated-protobuf package folder. The rest of the
rules, the firmware stamp and the doc guards are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

---
[Docs index](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Field guide](docs/reverse-engineering/FIELD-GUIDE.md)
