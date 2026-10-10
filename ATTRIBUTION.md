# Attribution and credits

This project builds on the Moxie revival community, above all OpenMoxie. We study their work, port
the best **behaviors** behind our own contracts, and credit each one here. No OpenMoxie code is copied
into this tree; if any is ever vendored, its MIT license and copyright notice go with it.

## OpenMoxie

**[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie)** · MIT · © 2025 Justin Beghtol

OpenMoxie is the original local replacement for Moxie's MQTT cloud, written so the robot keeps working
after the shutdown; [its README](https://github.com/jbeghtol/openmoxie#readme) calls itself the
official repository and warns that openmoxie.org and "OpenMoxie 2.0" are unaffiliated. It is widely
reported that its author is a former Embodied engineer and that Embodied's CEO endorsed it as the
official open-source off-ramp; this repo holds no source for that, so treat it as reported, not
verified. Paths below are in their repository (commit `c8c2d380efd37d2e83761957587f5d08f73b3a63`
where pinned).

| What we took | Where it comes from in OpenMoxie | Our implementation, and what differs |
|---|---|---|
| Protobuf schemas and the MQTT topic protocol | throughout | [`mqtt-and-conversation.md`](docs/architecture/mqtt-and-conversation.md) |
| The `automarkup` text-to-behavior idea | automarkup engine | [`mqtt/moxie_sdk/automarkup.py`](mqtt/moxie_sdk/automarkup.py) |
| Endpoint/migration QR and the mosquitto TLS setup | QR + broker setup | `tools/pairing/`, `mqtt/broker/` |
| Cloud-to-robot command names, e.g. `wakeup` (`{"command":"wakeup"}` on `/devices/{id}/commands/wakeup`) | their command table | Our console's *Wake up* button. Where their table establishes nothing (remote reboot) we refuse rather than invent. |
| The volley model, scheduler and content-module concepts | `mqtt/`, `content/` | [`content-module-contract.md`](docs/architecture/content-module-contract.md) |
| Shareable content packs with a per-record `source_version` and a review-then-apply import | `site/hive/views.py::export_data`, `upload_import_data`, `import_data`; `data_import.py`; `init_data.py` | Ours adds a versioned envelope with a digest, a field allowlist so no child data leaves, key-based selection, and local-edit detection ([brief](docs/architecture/backlog/content-packs.md)). |
| Two-level config merge (appliance default under per-robot overrides) | `models.py::HiveConfiguration`, `robot_data.py::build_config` | [`cloud_config.py`](mqtt/moxie_sdk/cloud_config.py)`::merge_config_layers` |
| Device permit list, and `pairing_status:"unpairing"` for an unpaired robot | `models.py::MoxieDevice.permit`, `is_paired`; `HiveConfiguration.allow_unverified_bots` | Upstream stores the flag; we enforce it on the MQTT path, with a pending state and a child-free config ([`moxie_runtime`](mqtt/supervisor/moxie_runtime/), `cloud_config.py::build_unpaired_cloud_config`). |
| Face cache-buster: changing a child's look must change the child `id` | `views.py::face_edit` | Deterministic UUIDv5 instead of random ([`faces.py`](mqtt/moxie_sdk/faces.py)`::face_child_id`). Taken as field-proven. |
| The 60 face-customization asset ids (`MX_<nnn>_<Group>_<Detail>`) | `site/hive/content/data.py::MOXIE_CUSTOMIZATIONS` | Id strings only, transcribed into [`face_assets.json`](mqtt/moxie_sdk/face_assets.json) with the full citation and a sha256 inline. Slot mapping and labels are ours; entries are flagged `caution` because upstream notes some crashed Unity. |
| Day-plan shape: a schedule template, first-run pruning, chats between activities, no two same-category activities in a row | `mqtt/scheduler.py::expand_schedule`, `ftue_remove`, `ransac_select`, `distribute_elements`; FTUE thresholds `TNT_CIDS`/`SYSTEMSCHECK_CIDS` in `content/data.py` | Upstream samples randomly; ours is a deterministic recommender that explains each pick ([`schedule/`](mqtt/moxie_sdk/schedule/)). |
| Inline action tags `<exit>`, `<sleep>`, `<launch:MOD:CID>` | `volley.py::ingest_action_tags` | [`actions.py`](mqtt/moxie_sdk/actions.py) |
| Puppet (telehealth) console: enable, disable, speak, interrupt; mood and intensity; enabling is a config write (`moxie_mode = "TELEHEALTH"`) | `views.py::puppet_api`, `templates/hive/puppet.html` | [`telehealth.py`](mqtt/moxie_sdk/telehealth.py). Intensity is an integer 0–2 (the recovered maximum), the operator's line passes our safety check and the parent's journal, and a blocked line is refused with a reason. |
| Executable content hooks (`pre_process`, `post_process`, `complete_handler`, `notify_handler`; `METHOD` globals) | `models.py::GlobalAction.METHOD`, `mqtt/global_responses.py`, `mqtt/conversations.py`; modules `MoxieTime`, `MoxieTimers`, `MemoryChat`, `MoxieGo` | We keep the hook vocabulary but not `exec()`: behaviors were hand-ported into a declarative rule language with no host access ([brief](docs/architecture/backlog/sandboxed-extensions.md)). |
| Launch cards: a printed QR `GO<launch:MODULE>` a child shows Moxie | `site/data/qr/extract.py`; the `MoxieGo` module | [`launch_cards.py`](mqtt/moxie_sdk/launch_cards.py) resolves the scan in the runtime against a closed catalog and refuses `<sleep>`, `<exit>` and `<launch_if_confirmed:…>`. |
| Shipping those cards as printable paper | `site/data/qr/extract.py` (the idea only) | [`launch_sheet.py`](mqtt/moxie_sdk/launch_sheet.py): one HTML page with inline SVG sized in millimetres, error correction Q, payloads from `launch_cards.encode`. |

Go star the original.

## Noonster77/openmoxie

**[Noonster77/openmoxie](https://github.com/Noonster77/openmoxie)** · MIT (fork of OpenMoxie)

The most active fork and the closest to our local-first goal. It already runs a local LLM (LM Studio)
and local speech-to-text (faster-whisper). We learned from its local model integration, its MQTT
reconnect and SQLite locking fixes, its wake/sleep fixes, its Parent Corner and transcripts, its extra
modules (trivia, jokes, homework), and its test suite.

## Others

| Project | What we take from it |
|---|---|
| [vapors/openmoxie-ollama](https://github.com/vapors/openmoxie-ollama) | Ollama and OpenAI-compatible local LLM patterns; faster-whisper STT. |
| [nhertanto/Embodied-Moxie](https://github.com/nhertanto/Embodied-Moxie) | A reference for the original activity content. License unconfirmed; no code reused. |

The wider landscape: [`docs/community-research.md`](docs/community-research.md).

## Other components

- **EFF Short Wordlist** (`server/moxie_server/data/eff_short_wordlist_1.txt`) — © Electronic Frontier
  Foundation, CC-BY-3.0-US. Used for recovery phrases, as the original app did.
- Runtime libraries (FastAPI, Uvicorn, PyNaCl, segno and others) under their own licenses.

## What is original here

- A clean-room parent-app server and web client (account, child, pairing QR, robot settings).
- A pairing-QR codec verified on real hardware, and the reverse-engineering documentation.
- One self-hosted box for parent app, robot cloud and AI, with any OpenAI-compatible model as the brain.
- A browser simulator that speaks the real protocol.

*If we have used your work and got the credit wrong, please open an issue.*

---
[Project README](README.md) · [Community research](docs/community-research.md) · [License](LICENSE)
