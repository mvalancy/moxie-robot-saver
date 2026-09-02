# 🙏 Attribution & credits

This project stands on the shoulders of the Moxie revival community. Their work — especially
OpenMoxie — is what makes any of this possible. We build on it gratefully: taking the best ideas and
(where licenses allow) the best code from across the ecosystem, crediting it clearly, and extending
it toward one complete, local-first system that also brings back the phone-side experience.

## OpenMoxie — the foundation for the robot cloud
**[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie)** · **MIT License** · © 2025 Justin Beghtol

Written by a former Embodied engineer and sanctioned by Embodied's CEO as the official open-source
off-ramp before shutdown. It is the canonical LAN replacement for Moxie's MQTT/IoT cloud. Our `mqtt/`
robot-cloud layer builds on its groundwork:
- the real Embodied **protobuf schemas** and the MQTT topic/message protocol,
- the **`automarkup`** text→behavior (expressiveness) engine,
- the **endpoint/migration QR** relocation mechanism and mosquitto TLS setup,
- the conversation **volley** model, scheduler, and content-module concepts,
- the **two-level config merge** — one appliance-wide default config layered under each robot's own
  overrides (`models.py::HiveConfiguration` + `robot_data.py::build_config`'s `deepmerge`); ours is
  [`mqtt/moxie_sdk/cloud_config.py`](mqtt/moxie_sdk/cloud_config.py)`::merge_config_layers`,
- the **device permit list** — the idea that a self-hosted robot cloud keeps an allowlist and a
  "serve anything that connects" escape hatch (`models.py::MoxieDevice.permit`,
  `HiveConfiguration.allow_unverified_bots`) and that `pairing_status:"unpairing"` is the value a
  *not-paired* robot is sent (`models.py::MoxieDevice.is_paired`). Upstream stores the flag without
  enforcing it on the MQTT path; our enforcement, the pending state and the minimal child-free config
  are ours — [`mqtt/supervisor/moxie_runtime.py`](mqtt/supervisor/moxie_runtime.py)`::permits` +
  [`mqtt/moxie_sdk/cloud_config.py`](mqtt/moxie_sdk/cloud_config.py)`::build_unpaired_cloud_config`,
- the **response action-tag** convention — `<exit>` / `<sleep>` / `<launch:MOD:CID>` written inline by the
  model and lifted into real robot actions (`volley.py::ingest_action_tags`); our own implementation lives
  in [`mqtt/moxie_sdk/actions.py`](mqtt/moxie_sdk/actions.py).

> When we vendor any OpenMoxie source into this repo, its MIT `LICENSE` and copyright notice are
> included alongside it (see `mqtt/` third-party notices as that code lands). Nothing here is a
> substitute for the original — go star it.

## Noonster77/openmoxie — the foundation for our local-AI approach
**[Noonster77/openmoxie](https://github.com/Noonster77/openmoxie)** · MIT (fork of OpenMoxie)

The most active fork, and the closest existing work to our **local-first, no-cloud** AI goal — it
already runs a **local LLM (LM Studio)** and **local STT (faster-whisper)**, exactly the direction our
`ai/` layer takes. We build on its groundwork:
- local LLM + local faster-whisper integration patterns (our OpenAI-compatible-endpoint seam),
- Docker/MQTT **reconnect** and SQLite-**locking** fixes, wake/sleep fixes (production hardening),
- "Parent Corner", conversation **transcripts**, and extra modules (trivia/jokes/homework),
- a real **test suite** and clearer install docs.

## Best of the rest of the ecosystem
We fold in the strongest ideas from the wider community (forks of OpenMoxie inherit its MIT license;
verify each repo's license before vendoring code):

| Project | What we take from it |
|---------|----------------------|
| **[vapors/openmoxie-ollama](https://github.com/vapors/openmoxie-ollama)** | **Ollama** and OpenAI-compatible local-LLM integration patterns; faster-whisper STT. |
| **[nhertanto/Embodied-Moxie](https://github.com/nhertanto/Embodied-Moxie)** | Reference for the original **activity content** (Reading, Daily Missions, Tips, Enrollment, jokes) — a content/design reference. *License unconfirmed; verify before reusing code.* |

Full landscape and how the pieces fit: [`docs/community-research.md`](docs/community-research.md).

## Other components
- **EFF Short Wordlist** (`server/moxie_server/data/eff_short_wordlist_1.txt`) — © Electronic Frontier
  Foundation, CC-BY-3.0-US. Used for recovery-phrase generation, mirroring the original app.
- Runtime libraries — FastAPI, Uvicorn, PyNaCl (libsodium), segno — under their respective licenses.

## What is uniquely ours
No prior project rebuilt the **parent app** (the phone-side setup experience) or unified everything
into one appliance. Our original contributions:
- a clean-room **parent-app client + server** (account, child, pairing-QR, robot settings),
- a clean-room, hardware-verified **pairing-QR** codec and the full **reverse-engineering docs**,
- a **local-first, single-box** architecture (parent app + MQTT + local AI on one GPU machine),
- **local LLM/STT with OpenAI-compatible fallback** as a first-class principle (never vendor lock-in),
- planned **camera/vision** (OpenCV + VLM) integration,
- shared **Claude agents/skills** so the knowledge travels with the repo.

---
*If we've used your work and gotten the credit wrong, please open an issue — we want to get this right.*
