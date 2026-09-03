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
- the **shareable content pack and its `source_version` upgrade rule** — the idea that authored content
  (conversations, globals, schedules) travels as one JSON file, that each record carries an
  author-owned integer version, and that an import is a *two-step review then apply* rather than a
  blind overwrite (`site/hive/views.py::export_data` + `upload_import_data` + `import_data`,
  `site/hive/data_import.py::update_import_status`/`import_content`, and the same version comparison
  reused to upgrade their own shipped defaults in
  `site/hive/management/commands/init_data.py`). The behaviour is theirs and we credit it; the design
  we build on top — a versioned self-describing envelope with a content digest, a positive field
  allowlist so no child data can leave, selection by key rather than array index, and a review that
  also detects **local edits** so an upstream re-import cannot silently destroy them — is ours, and is
  specified in [`docs/architecture/backlog/content-packs.md`](docs/architecture/backlog/content-packs.md),
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
- the **face cache-buster** — the observation that Moxie's Unity layer keeps a *composited face
  texture keyed on the child's `id`*, so changing a child's appearance must also change that id or the
  robot serves a stale picture (`views.py::face_edit`, which writes a fresh `uuid4` there on save).
  Our corpus never captured that; theirs is a server that drives real robots, so we take the mechanism
  as **field-proven** and say so in the code. Ours is deterministic rather than random —
  [`mqtt/moxie_sdk/faces.py`](mqtt/moxie_sdk/faces.py)`::face_child_id`, a UUIDv5 over the chosen
  layers, so an idempotent re-push does not churn the child's identity,
- the **face-customization asset table, ingested as data** — `site/hive/content/data.py::MOXIE_CUSTOMIZATIONS`,
  60 `MX_<nnn>_<Group>_<Detail>` asset ids their authors harvested from a robot they could run. Our own
  corpus structurally *cannot* supply these (the art streams from `REMOTE_ASSETBUNDLES`, never the APK),
  so on 2026-09-02 we transcribed **the id strings and nothing else** — no code, no comments, no function
  bodies — into [`mqtt/moxie_sdk/face_assets.json`](mqtt/moxie_sdk/face_assets.json), which carries the
  full citation inline: repo URL, file path, symbol, commit `c8c2d380efd37d2e83761957587f5d08f73b3a63`,
  MIT (© 2025 Justin Beghtol), ingest date, entry count and a sha256 of the id list. The slot mapping
  (each group prefix → exactly one recovered `MoxieCustomizationType`) and every human-readable label
  are ours; an id we could not place would be parked in `unmapped` rather than guessed, and all 60
  placed. Each entry is tagged `origin: "openmoxie-manifest"` and `caution: true`, because upstream's
  own note beside the list records that some of these crashed Unity without saying which. The twelve
  hex colour options our documents cite keep `origin: "recovered-enum"` and stay separable —
  72 options across 11 of the 14 slots, and still no invented ids,
- the **day-plan shape** — a `schedules[]` template with a `generate` block, FTUE pruning, chats
  distributed between activities, and the goal of *avoiding two same-category activities in a row*
  (`mqtt/scheduler.py::expand_schedule`/`ftue_remove`/`ransac_select`/`distribute_elements`, plus the
  field-proven FTUE thresholds `TNT_CIDS`/`SYSTEMSCHECK_CIDS` in `content/data.py`). Upstream picks by
  drawing 20 random samples and keeping the least-clumpy one; ours is a deterministic, explainable
  recommender over the same goal — parent requests, completion history, recency, bedtime and time of
  day, with a "why this activity today" line per entry —
  [`mqtt/moxie_sdk/schedule.py`](mqtt/moxie_sdk/schedule.py)`::plan_inputs`/`plan_day`,
- the **response action-tag** convention — `<exit>` / `<sleep>` / `<launch:MOD:CID>` written inline by the
  model and lifted into real robot actions (`volley.py::ingest_action_tags`); our own implementation lives
  in [`mqtt/moxie_sdk/actions.py`](mqtt/moxie_sdk/actions.py),
- the **puppet page** — the shape of a telehealth console (`views.py::puppet_api`,
  `templates/hive/puppet.html`): four verbs (enable / disable / speak / interrupt), a mood + intensity
  picker beside the line box, a state poll, and — the load-bearing insight — that turning puppet mode on
  is *just a config write* (`robot_config["moxie_mode"] = "TELEHEALTH"`, then re-push). Our corpus
  recovered the `TeleHealth.proto` and the `STATE_TELEBRAIN` launcher state but never captured what
  *triggers* the mode, so theirs is a server driving real robots and we take that trigger as
  **field-proven**, behind one constant that says so
  ([`mqtt/moxie_sdk/telehealth.py`](mqtt/moxie_sdk/telehealth.py)`::TELEHEALTH_MOXIE_MODE`, assumption
  B1). Three things are deliberately ours: intensity is an **integer 0-2** (the recovered
  `maxIntensity=2`, not a 0.0-1.0 float), the operator's line goes through **our safety classifier and
  the parent's journal**, and a blocked line is **refused back to the operator with its reason** rather
  than silently rewritten. No code was copied.
- the **executable content hook** — the idea that a content module can carry *behaviour* keyed to a
  trigger, not just a prompt: a `METHOD` global whose matched regex runs authored code
  (`models.py::GlobalAction.METHOD` + `mqtt/global_responses.py::MethodPattern.create_response`) and a
  conversation whose `code` field is harvested for the named hooks `pre_process`, `post_process`,
  `complete_handler` and `notify_handler` (`mqtt/conversations.py::SinglePromptDBChatSession`). Both are
  `exec()`d with the module's own `globals()`, which is the right call for a server whose operator *is*
  the author — and the very thing that stops working the moment content is shareable. We keep the
  **concept and the hook vocabulary** and reject the mechanism: their nine shipped hooks
  (`content_modules/MoxieTime`, `MoxieTimers`, `MemoryChat`, `MoxieGo` at commit
  `c8c2d380efd37d2e83761957587f5d08f73b3a63`) were read as a **requirements corpus** — none of them
  iterates — and hand-ported into a declarative rule list over a total JSON-AST expression language with
  no `exec`, no host objects and a declared capability set, specified in
  [`docs/architecture/backlog/sandboxed-extensions.md`](docs/architecture/backlog/sandboxed-extensions.md).
  Not one line of their code is in this tree; the ported *behaviours* are credited here.

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
