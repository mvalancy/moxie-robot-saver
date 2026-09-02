# 🔬 OpenMoxie feature audit — what to adopt, and where to go beyond

> **Audit version 1 · 2026-09-02.** A feature-by-feature read of **OpenMoxie** (upstream `v0.8`, MIT)
> and its two active forks, measured against **our** build contracts and what we have actually shipped
> ([`implementation-plan.md`](implementation-plan.md)). Every OpenMoxie claim below cites a file in
> *their* repo. Every claim about us cites a file in *ours*. Nothing here is guessed.
>
> Scope note: this is the **deep** companion to [`../community-research.md`](../community-research.md)
> (which places OpenMoxie in the landscape) and to
> [`mqtt-and-conversation.md`](mqtt-and-conversation.md) §7.2 (which decided *take vs rebuild* for the
> transport layer). This doc covers **every** feature, user-facing and architectural, and turns the
> result into a ranked backlog.
>
> **Baseline + status.** The audit was measured at commit `fa70309` (PR #4, 2026-09-02) and its
> *findings* are frozen at that baseline — they are not rewritten as we ship. What we have built since
> is tracked in the **Status** column of the two ranked-backlog tables (§4.1, §4.2), last refreshed
> **2026-09-02**, and in [`orchestration-plan.md`](orchestration-plan.md)'s status log. Where a status
> is *partial*, the column says what is actually done, not what was intended.

## 🙏 Credit — OpenMoxie is MIT, and it is the reason any of this is possible

**[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie)** — **MIT License**, © Justin Beghtol.

Justin Beghtol is a **former Embodied engineer**. When Embodied shut down in December 2024, OpenMoxie
was released as the **CEO-sanctioned open-source off-ramp** so owners would not be left with a brick.
It is the canonical LAN replacement for Moxie's MQTT/IoT cloud, and it is the origin of nearly
everything the community knows about the robot's cloud protocol: the migration-QR relocation trick,
the mosquitto TLS setup, the `RemoteChat` volley model, the real Embodied protobuf schemas, and the
`automarkup` text→behavior engine. **If you own a Moxie and want it talking tonight, install OpenMoxie.**

The two active forks audited here, both MIT by inheritance:

| Fork | Last commit | Direction |
|---|---|---|
| **[Noonster77/openmoxie](https://github.com/Noonster77/openmoxie)** — "OpenMoxie Family Edition" | 2026-08-30 (active) | Local-first AI (LM Studio + faster-whisper), a rebuilt family-facing UI, speaker-scoped memory, transcripts, safety flags + parent review, 70 tests |
| **[vapors/openmoxie-ollama](https://github.com/vapors/openmoxie-ollama)** | 2025-08-17 (stale) | Ollama + xAI Grok providers, a standalone FastAPI faster-whisper STT microservice, Docker Hub CI. Explicitly adult/unfiltered |

We take ideas and (where we vendor code) carry the MIT notice with it — see
[`../../ATTRIBUTION.md`](../../ATTRIBUTION.md). **This audit is not a competitive teardown.** It is a
list of what a mature project already solved, so we do not re-solve it badly, plus an honest list of
the places their design stops and ours should keep going.

## How this audit was done

- Upstream cloned at `v0.8` (`site/VERSION`), last upstream commit `c8c2d38` (2026-01-15). Both forks
  cloned shallow and diffed file-by-file against upstream (Fork A adds 33 files, Fork B adds 30; neither
  deletes any).
- **Read in full:** every `.py` under `site/hive/` except the generated `*_pb2.py` and the `automarkup`
  internals; every template under `site/hive/templates/hive/`; every doc under `doc/`; the seed data
  under `site/data/`; the four shareable packs under `content_modules/`; the Docker/compose files.
- **Our side:** the six build contracts in this folder, [`implementation-plan.md`](implementation-plan.md)
  (status tables + Definition of done), and the code in [`../../mqtt/`](../../mqtt/) and
  [`../../server/`](../../server/).
- **Clean-room boundary unaffected.** OpenMoxie is MIT open source; reading it is fine and we credit it.
  The vendor Android app remains off-limits and is not referenced anywhere in this doc.

**Size, for calibration.** Upstream OpenMoxie is **≈5,900 lines of Python** — small, and that is a
compliment: it is the *minimum* that makes a robot work.

| Subsystem | LOC | Note |
|---|--:|---|
| `site/hive/automarkup/` | 2,157 | the text→behavior markup engine — the single densest asset |
| `site/hive/mqtt/` (excl. protos) | 1,889 | the whole robot cloud: server, chat, volley, globals, robot data, scheduler, STT |
| `site/hive/` views + models + import/export | 633 | the entire web app's logic |
| `site/hive/templates/hive/` | 797 | 12 HTML pages |
| `site/hive/mqtt/protos/**` | 213 | 5 generated protobuf modules |

---

## 1. Feature inventory — upstream OpenMoxie

One Django process does everything: the web UI, the database, and — on a daemon thread started by an
overridden `runserver` — the MQTT supervisor that talks to the robot.

```mermaid
flowchart LR
  browser["🖥️ browser<br/>/hive + /admin"] -->|"HTTP :8001"| hive["🧩 Django app 'hive'<br/>views · models · admin"]
  hive --> db[("🗄️ sqlite3<br/>8 models")]
  hive -->|"same process,<br/>daemon thread"| sup["⚙️ MoxieServer<br/>remote-chat · robot_data · scheduler"]
  sup --> mark["✨ automarkup<br/>text → behavior"]
  sup -->|"MQTT/TLS :8883<br/>anonymous"| broker["📡 mosquitto"]
  broker --> robot(["🤖 Moxie"])
  sup -.->|"chat + whisper"| oai(["☁️ OpenAI"])
  classDef s fill:#0e0e14,stroke:#00f0ff,color:#e8edf5;
  class browser,hive,db,sup,mark,broker,robot,oai s;
```

### 1.1 Web UI — pages and flows

Django 5.1 app mounted at `/hive/`; 24 routes in `site/hive/urls.py`. There is **no parent app** —
everything is done in this one web UI, plus the raw **Django admin** at `/admin/` for anything the
hand-built pages do not cover (`site/hive/admin.py` registers all 7 models).

| Page / route | View | What it does | Maturity |
|---|---|---|---|
| `/hive/setup` → `hive_configure` | `SetupView` | First-run wizard: OpenAI API key, optional Google service-account JSON, external hostname, "allow unverified bots", **creates the Django superuser** | 🟢 solid, doubles as the onboarding doc |
| `/hive/dashboard` | `DashboardView` | Device list w/ online badge + `timesince`, per-device action buttons, schedule list, conversation list, content import/export, "Refresh from DB" | 🟢 the hub |
| `/hive/endpoint/` | `endpoint_qr` | **Migration QR** PNG — repoints the robot at this server | 🟢 the killer feature |
| `/hive/wifi_edit/` → `wifi_qr` | `WifiQREditView` | **Wi-Fi QR** PNG from SSID/password/band/hidden | 🟢 |
| `/hive/moxie/<pk>` → `moxie_edit` | `MoxieView` | Per-robot: name, pairing status, mentor nickname, schedule, **volume slider**, **brightness slider**; read-only firmware/battery/mode/last-connect | 🟢 |
| `/hive/face/<pk>` → `face_edit` | `MoxieFaceView` | **Face customizer** — layered asset picker (eyes, face colour, brows, glasses, nose, hair…) + a "new child ID" button that invalidates Moxie's Unity texture cache | 🟢 genuinely delightful |
| `/hive/moxie_missions/<pk>` → `mission_edit` | `MoxieMissionsView` | Mark Daily-Mission sets **complete / forget / reset all** by writing `MentorBehavior` rows | 🟢 |
| `/hive/puppet/<pk>` → `puppet_api` | `MoxiePuppetView` | **Puppet / telehealth mode**: type a line + pick mood + intensity → Moxie says it; interrupt button; live online/puppet state polling | 🟢 the demo that sells it |
| `/hive/moxie_data/<pk>` | `MoxieDataView` | Raw JSON dump of the robot's active config + persistent data | 🟡 debug-grade |
| `/hive/interact/<pk>` → `interact_update` | `InteractionView` | **Browser chat harness** — talk to any conversation module without a robot, including global-command matching | 🟢 excellent for authoring |
| `/hive/export_content/` → `export_data` | `ExportDataView` | Select globals/schedules/conversations → download a **content pack** JSON | 🟢 |
| `/hive/import_review/` → `import_data` | upload + review | Upload a pack, see per-record `New / Upgrade from vN / Replace vN`, tick what to import | 🟢 nice two-step |
| `/hive/moxie_wake/<pk>` | `moxie_wake` | Send a `wakeup` command to a robot using the wake button | 🟢 |
| `/hive/reload_database` | `reload_database` | Re-read DB-backed caches after an admin edit | 🟡 manual cache-bust |

Styling is Bootstrap 5 + a small `site/static/hive/style.css`; jQuery for the puppet/interact AJAX.
No build step, no SPA. `openmoxie/version_context.py` stamps `site/VERSION` into every page header.

### 1.2 Robot onboarding — the two QR codes

The single most important thing OpenMoxie proved. Both QR builders live in
`site/hive/mqtt/moxie_server.py`:

- **Migration / endpoint QR** — `MoxieServer.get_endpoint_qr_data()` builds a `ServiceConfiguration2`
  protobuf (`gcp_project`, `mqtt_host`, `override_port`, `disable_verify`), base64s it, and wraps it as
  `{"debug": {"command": "om", "param": "<b64>"}}`. The robot's camera scans it and **permanently
  relocates** to your broker.
- **Wi-Fi QR** — `get_wifi_qr_data()` builds `embodied.wifiapp.QRCommands.StartPairingQR`
  (`wifi_only`, `ssid`, `password`, `is_hidden`, `band_select`), serializes, and prefixes the literal
  `PA` header.
- **Launch-card QRs** — `site/data/qr/extract.py` generates 24 PNGs of the form `GO<launch:MODULE_ID>`
  so a child can scan a card to start an activity. Small idea, big charm.
- **OTA lever** — `doc/RemoteModuleAPI.md` documents (as notes, not code) how the author upgraded
  801 → 803 robots by adding `webservice_root` to the endpoint QR, flipping `_PROVIDE_HTTP_TOKENS`,
  and serving a static `api/ota_updates/{id}/url` JSON file.
- **Robot identity** — `site/hive/mqtt/robot_credentials.py` can pull `uuid.txt` + `RS256.key` off a
  robot **over ADB** and mint per-device JWTs. The server itself connects as a fake `supervisor`
  identity because the broker allows anonymous auth.

### 1.3 Robot configuration & settings

`site/hive/mqtt/robot_data.py` holds the model, and it is the cleanest idea in the codebase:
a **two-level merge**. `HiveConfiguration.common_config` / `.common_settings` (fleet-wide) are
`deepmerge.always_merger`-merged with `MoxieDevice.robot_config` / `.robot_settings` (per-robot).
`DEFAULT_ROBOT_CONFIG` / `DEFAULT_ROBOT_SETTINGS` are the fallbacks.

Config keys exercised: `pairing_status`, `audio_volume`, `screen_brightness`, `audio_wake_set`,
`timezone_id`, `child_pii{nickname, input_speed, face_options, id}`, `moxie_mode` (TELEHEALTH),
`wake_button_enabled`, `touch_wake_enabled`, `ota_update{id,version}`.
Settings props: `touch_wake`, `wake_alarms`, `wake_button`, `doa_range`, `target_all`,
`gcp_upload_disable`, `local_stt`, `max_enroll`, `audio_wake`, `cloud_schedule_reset_threshold`,
`debug_whiteboard`, `brain_entrances_available`, `mqtt_files`, `file_sync_wait`, `default_loglevel`,
`stt`, `no_reprompt`. All of it is documented, with warnings, in `doc/MoxieOverview.md` —
**the best config documentation that exists for this robot.**

Config is pushed on connect and re-pushed live on edit (`MoxieServer.handle_config_updated` →
`RobotData.config_update_live`). Robots hold their data in memory only while connected
(`db_connect` / `db_release`), which keeps the hot path off the DB.

### 1.4 Schedules and the session model

`MoxieSchedule.schedule` is free-form JSON. `site/hive/mqtt/scheduler.py` then does something
smarter than a static list — `expand_schedule()`:

- a `generate` block declares `chat_count`, `module_count`, `chat_modules`, `extra_modules`,
  `excluded_module_ids`;
- `ransac_select()` runs 20 random samples and scores them to **avoid adjacent/duplicate categories**,
  picking the most varied day;
- `distribute_elements()` interleaves the random chats evenly between activities;
- `ftue_remove()` drops the first-time-user modules (`TNT`, `SYSTEMSCHECK`, `WELCOME`) once the child
  has completed them, by counting `MentorBehavior` rows.

Other documented schedule keys (`doc/MoxieOverview.md`): `wake_module`, `chat_request`,
`end_of_session`, `alarm_module`, and `hub_config` (a "hub and spokes" module injected between
activities). Three seed schedules ship: `default`, `no_onboarding`, `only_chat`
(`site/data/default_schedules.json`).

The robot asks for its schedule per session on `client-service-activity-log` with `query:"schedule"`;
it also asks for `mentor_behaviors` (progress history) and `license` (a Google service-account key,
shared verbatim if configured). All three are answered in `MoxieServer.on_device_event`.

### 1.5 Content — conversations, globals, packs

**Conversations** (`SinglePromptChat`): `module_id` + `content_id` (multiple CIDs may be `|`-separated),
`opener` (random pick from `|`-alternatives), `prompt`, `max_history`, `max_volleys`, `model`,
`max_tokens`, `temperature`, and a free-form Python `code` field. The prompt is rendered as a **Django
template** with `volley` and `session` in scope, so a prompt can say
`{{volley.config.child_pii.nickname}}` or branch on `{% if session.overflow %}`
(`site/hive/mqtt/conversations.py`, `SingleContextChatSession.make_volley_context`).

**Hooks.** The `code` field is `exec`'d at session construction and may define `pre_process`,
`post_process`, `complete_handler`, `notify_handler`
(`conversations.py` `SinglePromptDBChatSession.__init__`). `complete_handler` is where the shipped
`content_modules/MemoryChat.json` calls `session.summarize()` and writes facts into
`volley.persist_data` — a working long-term-memory pattern in ~60 lines of user code.

**Response tags.** The model can emit `<exit>`, `<sleep>`, `<launch:MOD>`, `<launch:MOD:CID>`,
`<launch_if_confirmed:…>` inside its text; `Volley.ingest_action_tags()` converts them into real
`response_actions` and strips them from the spoken line (`site/hive/mqtt/volley.py`). This is a
**very** cheap way to give an LLM agency over the robot, and it works.

**Globals** (`GlobalResponse`, `site/hive/mqtt/global_responses.py`): lowercase regex over user
speech, sorted by `sort_key` desc, with four action types — `RESPONSE`, `LAUNCH`, `CONFIRM_LAUNCH`,
`METHOD`. `METHOD` `exec`s a stored Python function (`get_response(request, response, entities)` or
the newer `handle_volley(volley)`) **with a 10-second `ThreadPoolExecutor` timeout**. Globals are
matched *before* the module runs, so "moxie what time is it" works inside any activity.

**The `Volley` object** is the per-turn API: `request`, `response`, `local_data` (session-scoped),
`persist_data` (DB-backed per robot, `PersistentData` model), `config`, `state`, `entities`,
`set_output`, `add_response_action`, `add_execution_action`, `update_subscriptions`,
`add_launch_or_exit`.

**Shareable content packs** — `content_modules/*.json`, importable through the UI:

| Pack | What it adds |
|---|---|
| `MemoryChat.json` | A chat that summarizes itself on exit and accumulates facts about the child; plus an "About Me" module |
| `MoxieGo.json` | "moxie go" hub launcher + a `moxie_go_hub_timers` schedule using hub-and-spokes navigation |
| `MoxieTime.json` | "moxie what time is it" (the canonical `METHOD` example) |
| `MoxieTimers.json` | Set/check/cancel real timers via `eb_timer_request`, plus an `ALARM` module that fires on expiry |

### 1.6 `automarkup` — text → behavior (the crown jewel)

`site/hive/automarkup/` (2,157 LOC) turns a plain sentence into Moxie's XML-JSON markup with voice
tags, prosody, pauses, mood, and **gestural behavior-tree marks**. It is rule + ML-table driven
(`ml/mlrules.py`, `ml/data/_mlprocesseddata.txt`, `ml/data/text_replacement.json`), does span-conflict
resolution so nested tags do not corrupt (`markup.check_span_conflicts`), and assembles the final XML
(`markup_core/markup_xmlassembly.py`). Entry point: `automarkup.process(text, rules, mood_and_intensity)`.

Every AI response without explicit markup goes through it
(`site/hive/mqtt/moxie_remote_chat.py`, `RemoteChat.create_session_response`). This is **why an
OpenMoxie robot feels alive** rather than like a speaker reading text.

`doc/AssetBundleMasterManifest.csv` (188 KB) additionally lists **every asset in the robot's bundle
repository** — labels, bundle names, types, default-loaded flag — which is the lookup table for sound
effects and behavior trees referenced from markup.

### 1.7 AI — LLM in, STT in, and the TTS non-story

| Seam | Upstream implementation | Honest maturity |
|---|---|---|
| **LLM** | `site/hive/mqtt/ai_factory.py` — **14 lines**, a module-global OpenAI key and `OpenAI(api_key=…)`. `AIVendor` enum has exactly one member, `OPEN_AI`. No `base_url`, no provider abstraction. | 🔴 the weakest part of the design |
| **STT** | `site/hive/mqtt/zmq_stt_handler.py` — robot streams `zmqSTTRequest` frames over `events/zmq`; `STTSession` accumulates PCM until `END_OF_SPEECH`, writes a WAV with `soundfile`, calls OpenAI `whisper-1` with `timestamp_granularities=["word"]`, and replies with a `zmqSTTResponse` carrying speech + start/end timestamps. | 🟢 correct + complete for one vendor |
| **TTS** | **None.** Moxie synthesizes on-device from markup. Puppet mode's `send_telehealth_speech` just ships text+markup. | ✅ correct — there is nothing to build for a real robot |
| **Wake word** | On-robot. The server only sets `local_stt`, `audio_wake`, `audio_wake_set`, `wake_button` config. | ✅ correct |
| **Safety / moderation** | **None.** No content filter, no input moderation, no age gate beyond prompt wording. | 🔴 real gap for a child's device |

### 1.8 Telemetry, logging, insights

- `MoxieLogs` model exists but is **never written** — device logs on `events/device-logs` are only
  `logger.debug`'d (`MoxieServer.on_device_event`).
- `MoxieDevice.state` + `.state_updated` store the last `/state` (battery, firmware, mode) — surfaced
  read-only on the robot page.
- `MentorBehavior` is the real analytics store: every activity completion the robot reports, indexed
  `(device, timestamp)`. It exists to be **fed back** to the robot as history, not to be charted.
- `_client_metrics` scrapes `$SYS/broker/clients/#` and prints once a minute.
- **No insights UI, no charts, no event/session analytics, no transcript storage.**

### 1.9 Multi-robot, storage, deployment, quality

- **Multi-robot:** yes, natively — every robot gets a `MoxieDevice` row, its own schedule, config
  overlay, persistent data, and MBH history. `allow_unverified_bots` / `pairing_status` gate access.
  But **one chat session per device** (`RemoteChat._device_sessions` keyed by `device_id`), and one
  global `HiveConfiguration` named `"default"`.
- **Storage:** SQLite via the Django ORM; 8 models; 16 migrations; `source_version` integers on
  `SinglePromptChat` / `MoxieSchedule` / `GlobalResponse` give a real **content-upgrade mechanism**
  (`management/commands/init_data.py` only overwrites when the shipped version is newer).
- **Deployment:** `docker-compose.yml` with two services — `openmoxie/openmoxie-mqtt` (eclipse-mosquitto
  + baked self-signed keys from `keys/`) and `openmoxie/openmoxie-server` (Django). **Prebuilt
  multi-arch images on Docker Hub**, so the documented install is *download one compose file and run it*.
  `deploy.sh` / `deploy.bat` build+push `latest` and the `site/VERSION` tag.
- **How the MQTT service starts:** `management/commands/runserver.py` overrides Django's `runserver` to
  spawn the MQTT supervisor on a daemon thread — which is why the docs insist on `--noreload`.
- **Tests:** `site/hive/tests.py` is the **3-line Django stub**. There are none.
- **CI:** none. `.github/` contains only `FUNDING.yml`.
- **Docs:** 5 markdown files under `doc/` — `MoxieOverview.md`, `RemoteModuleAPI.md`,
  `ContentModules.md`, `GlobalResponses.md`, `Markup.md` — plus the asset manifest CSV. They are
  **excellent**: dense, honest, full of "this will break your robot" warnings.
- **Security posture:** `DEBUG = True`, `ALLOWED_HOSTS = ['*']`, a committed Django `SECRET_KEY`,
  `allow_anonymous true` on the broker, `django-debug-toolbar` enabled in production, and API keys
  stored plaintext in SQLite. Defensible for a LAN appliance; documented as such; still a real
  posture to improve on.

---

## 2. The active forks

Both are MIT by inheritance and both credit upstream. They pull in opposite directions, and the
contrast is instructive.

### 2.1 `Noonster77/openmoxie` — "OpenMoxie Family Edition"

`site/VERSION` `v0.9`; last commit 2026-08-30 — **actively developed**. 33 files added, none deleted.
This is the closest existing project to what we are building, and it is ahead of us on several fronts.

| Area | What it does | Where |
|---|---|---|
| **Provider abstraction** | `ai_factory.py` grows 14 → 134 lines: `configure_ai(...)`, `create_chat_client()`, `chat_completion(...)`. Providers: **OpenAI, OpenRouter, LM Studio, generic OpenAI-compatible**. The `base_url` lives in the DB (`HiveConfiguration.chat_provider/chat_base_url/chat_model/chat_api_key`) and is editable in the Setup UI; a `test_ai_connection` route probes `models.list()` and warns if the configured model is not loaded. | `site/hive/mqtt/ai_factory.py`, `models.py`, `views.py::hive_configure`/`test_ai_connection` |
| **LM Studio done properly** | Uses LM Studio's *native* `/api/v1/chat` rather than the OpenAI shim, so it can set `reasoning: on\|off` — then handles the real failure mode: a local reasoning model burning its whole output budget on hidden thoughts and returning no message, retried once with reasoning off. | `ai_factory.py` |
| **Narrow retry** | Retries **only** `BadRequestError`, selectively dropping `reasoning_effort` or swapping `max_tokens` → `max_completion_tokens`; never retries timeouts. | `ai_factory.py` |
| **Local STT** | faster-whisper **in-process**, `WhisperModel` cached in a lock-guarded module dict, model dir on the persisted volume. `stt_provider` ∈ {`openai`, `local`}, `local_stt_model` ∈ {`tiny.en`, `base.en`, `small.en`, `medium.en`}. | `site/hive/mqtt/zmq_stt_handler.py` |
| **Speaker-scoped memory** | Per-speaker profiles inside the existing `PersistentData` JSON — a 20-item ring buffer of verbatim turn pairs, **provenance on every item** (`source_event_id`, module, timestamp, speaker), injected into the prompt **only at session start** (last 8 items) to avoid re-paying tokens every turn, and the session resets when the active speaker changes. v1 memory with no trustworthy speaker attribution is **quarantined**, never prompted. | `site/hive/mqtt/conversation_memory.py`, `conversations.py` |
| **Safety redirect + parent review** | Five regex categories (self-harm, weapons/violence, sexual content, drugs/alcohol, abuse/danger) checked **before inference**, so a flagged utterance never reaches the model; escalated wording for self-harm/abuse. Flags land on a `ConversationEvent` row with an acknowledge-reviewed timestamp, surfaced as a dashboard alert. The UI is honest that keyword flags are a review aid, not a filter. | `site/hive/mqtt/conversation_log.py`, `templates/hive/transcripts.html` |
| **Transcripts + real deletion** | Dual write: a DB row **and** a per-day `.txt` under the work dir (device id path-sanitized, file-locked, 8 s dedup). Parent deletion regenerates the day's file from the DB, unlinking it when empty — so "delete" actually deletes. | `conversation_log.py::record_conversation`/`rewrite_daily_transcript`, `views.py::transcript_manage` |
| **Background reasoning mode** | A long local inference runs on a `ThreadPoolExecutor` while Moxie speaks **DB-sourced filler** (enabled trivia facts / jokes, no repeats) — because the robot re-prompts after ~20 s and a two-minute inference would otherwise break the turn. The best-engineered idea in either fork. | `conversations.py::ReasoningChatSession` |
| **Homework mode** | Answers spoken arithmetic with **no model call** using a whitelisted-AST evaluator (`ast.Expression`/`Constant`/`UnaryOp`/`BinOp` only, exponent + overflow guards, **no `eval()`**), plus a `concise_answer` post-filter that strips trailing questions. | `conversations.py::HomeworkChatSession` |
| **Command reliability** | UI actions are *queued* and spliced into the next genuine router request — including replacing an in-flight AI answer so Stop/Sleep can't be undone by a slow inference — then confirmed against the robot's reported state, and replayed after a reconnect. | `moxie_remote_chat.py`, `moxie_server.py` |
| **New UI** | 17 new routes and 6 new pages: a **Live Room** monitor (metric tiles, command deck, live bubble feed, command-journey timeline, filtered debug panel, 2 s poll), transcripts browser, activity launcher, trivia + joke studios, and a guide. `style.css` grows from 659 bytes to ~23 KB. DOM insertion of user data uses `textContent`, not `innerHTML`. | `urls.py`, `templates/hive/*.html` |
| **Production hardening** | SQLite WAL + `busy_timeout` + a process-wide write lock with backoff retry on "database is locked"; paho `CallbackAPIVersion.VERSION1` pinned; `connect_async` + `reconnect_delay_set` replacing a synchronous connect that killed the supervisor thread on first failure; per-device init serialization; worker futures whose exceptions are logged instead of swallowed. | `apps.py`, `util.py`, `settings.py`, `moxie_server.py` |
| **Tests** | **70 test methods / 1,143 lines** across 5 `TestCase` classes, covering real regressions (LM Studio empty-reasoning retry, speaker-scoped memory, legacy-memory quarantine, knock-knock turn-taking, premature wake confirmation, offline arithmetic). | `site/hive/tests.py` |

**Honest caveats.** Still **zero authentication** — `transcripts/<pk>`, `transcript_download`,
`live_activity` and `clear_conversation_memory` expose or destroy a child's full conversation history to
anyone who can reach the port; the mitigation is a "trusted home network only" banner. A shipped prompt
line and several data migrations **hardcode the author's own family and pet names**, which every user
then inherits. No type hints anywhere. `SECRET_KEY` is generated to a work-dir file rather than committed
and `DEBUG` defaults off — a real improvement over upstream.

### 2.2 `vapors/openmoxie-ollama`

Last commit 2025-08-17, a single squashed commit — **effectively stale**. Explicitly **not** a children's
product: the README says "not for children — may contain offensive language."

| Area | What it does | Where |
|---|---|---|
| **Provider abstraction** | A real interface: `class LLMProvider` with `chat(messages, temperature, stream)` and three subclasses — `OpenAIProvider`, `XAIProvider` (native `xai_sdk`, not the OpenAI shim), `OllamaProvider`. Selection is **per conversation** (`AIVendor` extended to `OPEN_AI`/`OLLAMA`/`XAI` on `SinglePromptChat.vendor`) — the opposite choice from Fork A. Base URLs come from `settings.py`/env only; there is no UI for them. | `site/hive/mqtt/ai_factory.py`, `models.py`, `settings.py` |
| **STT as a microservice** | A standalone FastAPI + faster-whisper service with `POST /stt`, `GET /health`, `GET /control/models` (scans a models dir) and `POST /control/reload` (**hot-swaps model/device/compute under a lock**), plus a Django-side client that falls back to OpenAI Whisper. Config: `stt_backend`, `stt_url`, `stt_lang`, `stt_device`, `stt_compute`, `stt_model`. | `site/services/stt/stt_service.py`, `site/hive/stt.py`, `stt.Dockerfile` |
| **CI + packaging** | The only fork with CI: a GitHub Actions workflow that builds three multi-arch images (web / stt / mqtt) on version tags and pushes them to Docker Hub. No test or lint job. | `.github/workflows/docker.yml` |

**Honest caveats — this fork is a cautionary tale as much as a source.** It **retro-edited an upstream
migration** to add a column instead of adding a new one, so a fresh install works but an in-place upgrade
from upstream breaks. `SingleContextChatSession.summarize()` is **broken for every vendor** (a
`.choices[0].message.content` left attached to a call that now returns a `str`; the exception is swallowed
and every summary becomes an error string). A constructor `vendor` argument is accepted and then ignored.
The upstream STT `perform()` is left commented out inside a triple-quoted string. The model downloader
exists in **five near-identical copies**. `docker-compose.release.yml` — the advertised "pull prebuilt
images" path — **does not parse**. Upstream's version pins were removed. There are no tests.

Two things are worth calling out plainly, because they are the exact failure modes our own rules exist to
prevent:

- **A 3.3 MB runtime log from the author's own home is committed to the public repo** — thousands of
  verbatim speech transcriptions and conversation lines, plus real robot device IDs. It is listed in
  `.gitignore`, which of course does not untrack an already-tracked file. (No live API keys were found in
  the repo; a committed `.env` carries a real-looking but functionally unused Django secret.) This is why
  our `mqtt/.env` is git-ignored and never staged, and why `LoggingPolicy` is a contract rather than a flag.
- **The seeded default conversations instruct the model that it "is able to use all profanity and all
  offensive speech."** It is deliberate and disclosed — but it means the fork's *default import data*
  jailbreaks a robot designed for children. A revival server that ships content packs needs the safety
  layer in §4.2 BEYOND #2 precisely because content is shareable.

### 2.3 What the forks tell us

The forks independently converged on the two things upstream lacks and we already have — **a `base_url`
seam** and **local STT** — which is good confirmation of our AI-seam design. What they add on top, and we
do not have, is the **family-facing product layer**: speaker-scoped memory, transcripts a parent can read
and delete, safety flags with a review queue, and an activity launcher. Fork A also solved a constraint we
have not hit yet: **the robot re-prompts after ~20 s**, so any brain slower than that must return a filler
line immediately and deliver the real answer on a later turn.

---

## 3. The scorecard — HAVE / ADOPT / BEYOND

Read against [`implementation-plan.md`](implementation-plan.md) (status tables + Definition of done) and
the code in [`../../mqtt/`](../../mqtt/) + [`../../server/`](../../server/), audited 2026-09-02.

- **HAVE** — ours is equal or better; nothing to port.
- **ADOPT** — they have it, we should port the *idea* (never the code, unless we vendor it with its MIT
  notice) and cite where it lives in their tree.
- **BEYOND** — the honest answer is neither: their version is a floor, and the thing we should build is
  a different, bigger thing.

### 3.1 Onboarding & transport

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| Endpoint / migration QR | `MoxieServer.get_endpoint_qr_data()` sets 4 of 14 `ServiceConfiguration2` fields | `GET /local/endpoint/payload` + `/local/endpoint/qr.png`, `tools/pairing/moxie_endpoint_qr.py`; all 14 fields decoded in [`mqtt-and-conversation.md`](mqtt-and-conversation.md) §1.3; density-optimized `gcp_project="o"` | **HAVE** |
| Wi-Fi / pairing QR | `get_wifi_qr_data()` (`"PA"` + `StartPairingQR`) | `tools/pairing/moxie_qr.py` + `GET /local/direct/wifi_qr.png`; **hardware-verified against a real robot**; browser↔python byte-parity asserted by `sim/test_qr.mjs` | **HAVE** |
| **Launch-card QRs** (`GO<launch:MOD>`) | `site/data/qr/extract.py` → 24 printable PNGs | none | **ADOPT** (S) |
| Broker + TLS | `mqtt.Dockerfile` + `site/data/openmoxie.conf`, **keys baked into the image** (`keys/`) | `mqtt/broker/gen-certs.sh` mints a **per-appliance CA**; keys git-ignored | **HAVE** |
| Connect/disconnect detection | regex over `$SYS/broker/log/#` | same technique, `CONNECT_RE`/`DISCONNECT_RE` in `moxie_runtime.py` | **HAVE** |
| Robot identity / JWT | `RobotCredentials` mints RS256 JWTs, can ADB-pull `uuid.txt` + `RS256.key` | broker is anonymous; we don't verify JWTs either (deferred, §3b) | **HAVE** (parity — both punt) |
| **Device allowlist / pairing gate** | `MoxieDevice.permit`, `pairing_status`, `HiveConfiguration.allow_unverified_bots` | we always push `pairing_status:"paired"` to whoever connects | **ADOPT** (S) |
| OTA push (801→803 lever) | notes only in `doc/RemoteModuleAPI.md` (`ota_update`, `_PROVIDE_HTTP_TOKENS`, static `url` file) | `ota_update`/`forbid_otaver` specified in [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md), **not built** | **ADOPT** (M) |

### 3.2 The turn — brain, memory, expressiveness

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| RemoteChat response | text + markup + `response_actions`; `result` is always `0` | full contract: `ResultCode` enum incl. **`ERROR_OFFLINE`** (robot falls back to its on-device brain), scored output (mood/dialog-act), `Action` passthrough — `moxie_sdk/wire.py`, `types.py` | **HAVE** |
| LLM provider | upstream `ai_factory.py` — **14 lines, OpenAI only**, no `base_url`. Both forks fixed this independently (§2) | `chat.py::make_openai_chat(base_url, …)` — any OpenAI-compatible endpoint (LiteLLM, Ollama, vLLM, LM Studio), plus `Pacer` + `call_with_backoff` for 429/5xx, plus `WebhookApp` as a language-agnostic second brain | **HAVE** vs upstream; **parity** with Fork A, which also puts the `base_url` in the UI with a connection test |
| Notify-based history | `ChatSession.ingest_notify` | `MoxieRuntime._ingest_notify` — same rule (skip `animation:`/`silent:` lines) | **HAVE** |
| History persistence | in-memory per session; lost on restart | per-device JSON under `MOXIE_MEMORY_DIR`, atomic write, trimmed to `MOXIE_MEMORY_TURNS` | **HAVE** |
| **Conversation summarization → long-term facts** | `session.summarize()` + `complete_handler` writing to `volley.persist_data`; shipped as `content_modules/MemoryChat.json` | `session.summarize()` is **explicitly unimplemented** (plan §Known gaps); no `persist_data` store | **ADOPT** (M) |
| Prompt templating | Django templates over `volley`/`session` | Jinja2 `content/render.py::render_prompt`, same variables | **HAVE** |
| **LLM response tags** (`<exit>`, `<launch:MOD:CID>`, `<sleep>`) | `Volley.ingest_action_tags()` — parses the model's own text into real actions and strips them | we have `Reply.actions`, but nothing parses tags out of model text | **ADOPT** (S) — very cheap model agency |
| **`automarkup` — text → behavior** | 2,157 LOC rule+ML engine; every AI line goes through it | 🟢 **closed at the floor (2026-09-02):** `supervisor/markup.py` delegates to one pure deterministic generator every app shares (`moxie_sdk/automarkup.py`), with a frozen doc-cited catalog (`vocab.py`) and 8 byte-exact goldens. The ML rule table is deliberately not ported — that is the P2 conversation | **BEYOND** (L) — the *planner* remains: see §4.2 and [`backlog/expressiveness.md`](backlog/expressiveness.md) §2 |
| Puppet / telehealth | full: `moxie_mode:"TELEHEALTH"`, `PLAY_OUTPUT`/`INTERRUPT`, mood+intensity picker, live state poll | `MoxieMode.TELEHEALTH` exists in `cloud_config.py`; **no command path, no UI** | **ADOPT** (M) |
| **Safety / moderation** | upstream: none at all. Fork A: 5 regex categories checked pre-inference + a parent review queue (§2.1) | **BUILT 2026-09-02** — `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` enforced pre-inference **and per streamed chunk** ([`ai-seam.md`](ai-seam.md) §② "Input safety"): 8 categories with a per-side block/flag policy in a parent-readable rule table, a `Classifier` protocol for a drop-in local model, and a review queue with acknowledge. Plus a hardened persona "Safety:" paragraph | **BEYOND #2 — done** (the honest limit: the classifier is still a rule engine) |
| Streaming / barge-in | none | **streaming shipped** (PR #17): one `ReplyChunk` per finished sentence → `REPLY_PENDING` + `chunk_num`, closed by `SUCCESS` + `is_completed`; first sentence at first-token latency. Barge-in and STT partials still unbuilt | **BEYOND** (M) — half done |

### 3.3 Content & the session

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| Content model | `SinglePromptChat` rows, editable in Django admin, multi-CID via `\|` | `content/module.py` dataclasses loaded from JSON files; `content_modules/starter.json` has **1 conversation + 1 global** | **ADOPT** (see below) |
| **Content authoring UI** | dashboard list + Django admin + the `interact` browser harness | none — edit JSON by hand | **ADOPT** (M) |
| **Content packs: import/export + review** | `ExportDataView`/`export_data`, `upload_import_data`/`import_data`, with per-record `New / Upgrade from vN / Replace vN` | none | **ADOPT** (M) |
| **Content versioning** | `source_version` int on 3 models; `init_data` upgrades only when newer | none | **ADOPT** (S) |
| Globals — regex + actions | 4 action types (`RESPONSE`/`LAUNCH`/`CONFIRM_LAUNCH`/`METHOD`), `sort_key` ordering, lowercase matching | `Global` regex + entity groups + **registered handlers only**; no LAUNCH/CONFIRM_LAUNCH shorthand | **ADOPT** (S) |
| Globals — stored Python (`METHOD`) | `exec` + 10 s `ThreadPoolExecutor` timeout | deliberately deferred (sandboxing) — `content_app.py` says so in its docstring | **BEYOND** (L) — see §4.2 |
| **Schedule serving** | `RobotData.get_schedule` + `scheduler.expand_schedule` — generative day plan, category-variety RANSAC, chat interleaving, FTUE pruning; 3 seed schedules | `_on_activity` answers `query:"schedule"` with `{}` **and with the wrong shape** (generic `result` key; the robot expects `schedule` / `mentor_behaviors` and a `request_id` echo) | **ADOPT — the single biggest functional gap** (M) |
| **`mentor_behaviors` history** | `MentorBehavior` model, ingest + serve, indexed `(device, timestamp)` | returns `[]` | **ADOPT** (M) |
| Native-module launching (~23 `RECOMMENDABLE_MODULES` + DM) | `content/data.py` lists them all with categories | listed in our docs; **no code schedules them** | **ADOPT** (rides on the schedule work) |
| `license` query (Google speech key) | shares the service-account JSON verbatim with any robot | returns `{}` | **HAVE** (deliberate — we are local-first; but see the honest note in §6) |
| Execution actions (`eb_timer_request`, `eb_enable_qr`…) | `Volley.add_execution_action` → real `response_actions` | `Volley.add_execution_action` captures them; **not plumbed onto the wire** (plan §Known gaps) | **ADOPT** (S) |

### 3.4 Fleet, config, console

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| Config model | **two-level deep-merge**: `HiveConfiguration.common_config/settings` (fleet) ⊕ `MoxieDevice.robot_config/settings` (per-robot) | one flat `build_robot_cloud_config()` + per-device `_config_overrides`; no fleet-wide layer | **ADOPT** (S) |
| Config editing UI | robot page: name, pairing, nickname, schedule, volume + brightness sliders | console Settings form: volume, weekday bedtime, wake/touch toggles, validated by `sanitize_config_overrides` | **HAVE** (ours validates; theirs has brightness + schedule pick) |
| Live re-push on edit | `handle_config_updated` → `config_update_live` | `update_config` → `_push_config` | **HAVE** |
| `/state` ingest | stored on `MoxieDevice.state`, shown read-only | `parse_robot_status` → `fleet.py::normalize_fleet` → `GET /local/fleet` live card | **HAVE** |
| **Face customization** | full layered editor + ~60-asset table + "new child ID" cache-buster | nothing (`face_options` is documented, not built) | **ADOPT** (S/M) |
| **Progress / missions editor** | complete / forget / reset by writing `MentorBehavior` rows | nothing | **ADOPT** (M, after MBH) |
| Wake command | `send_wakeup_to_bot` → `commands/wakeup` | `POST /api/robots/{rid}/wakeup` exists on the **REST** side; the MQTT `wakeup` command is not sent | **ADOPT** (S) |
| **Telemetry / insights** | `MoxieLogs` model exists but is never written; no charts, no sessions | `Packet` build/parse/ingest + `LoggingPolicy` gate + `telemetry_count`; **console view still missing** (plan's own "most valuable next slice") | **HAVE** (model) / **BEYOND** (the view) |
| Multi-robot | full per-robot rows, schedules, config, persist, MBH | per-`device_id` everywhere in the runtime + `/local/fleet`; but **one `ChildProfile` for all robots** (`MoxieRuntime(child=…)`) and a single-robot console card | **ADOPT** (M) |
| Parent app (phone) | **none — by design** | full clean-room `client-service` REST + mobile web client, 65 routes, zero-knowledge crypto | **HAVE** (uniquely ours) |
| SIM / hardware-free testing | `interact` — a text box that talks to a conversation | a **WebGL 3D robot** driven by the real MQTT protocol, plus `virtual_moxie.py`, scenarios, mic, TTS round-trip | **HAVE** (far ahead) |

### 3.5 Engineering & operations

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| Storage | Django ORM + SQLite, 8 models, 16 migrations | `server/` = SQLite, 8 tables, no ORM. **`mqtt/` has no database at all** — memory + JSON files | **ADOPT** (M) — the robot cloud needs durable content/schedule/progress storage |
| **Prebuilt images / one-file install** | multi-arch images on Docker Hub; documented install is *download `docker-compose.yml`, run two commands* | `mqtt/docker-compose.yml` + `sim/docker-compose.yml`; **no published images, no single stack** (plan DoD #5 🟡) | **ADOPT** (M) |
| Tests | `site/hive/tests.py` is the 3-line Django stub — **zero tests** | 37 test files (`sim/tests/*.py`, `sim/test_*.mjs`, `tools/robot-toolkit/test_*.py`) | **HAVE** (far ahead) |
| CI | none (`.github/` = `FUNDING.yml`) | three-tier: fast on `dev`, deep + HIL on PR-to-`main`, release on tags | **HAVE** (far ahead) |
| Docs | 5 dense `doc/*.md` + `AssetBundleMasterManifest.csv` (every robot asset) | 97 doc pages + a searchable static explorer + mechanical link/consistency guards | **HAVE** (ahead) — but see the asset-manifest note in §5 |
| Security posture | `DEBUG=True`, `ALLOWED_HOSTS=['*']`, committed `SECRET_KEY`, debug-toolbar on, plaintext keys in SQLite, anonymous broker | FastAPI, status endpoint bound to `127.0.0.1`, whitelist-validated config edits, git-ignored `.env`, per-appliance CA | **HAVE** |
| Ops ergonomics | "Refresh from DB" button; MQTT thread inside Django `runserver --noreload` | separate supervisor process + `/status` JSON | **HAVE** |

---

## 4. The ranked backlog

Effort key: **S** ≈ a day or less · **M** ≈ a few days · **L** ≈ a milestone of its own.

### 4.1 Top 10 ADOPT — port the idea, credit the source

| # | Adopt | Why (one line) | Their file | Effort | Status (2026-09-02) |
|--:|---|---|---|:--:|---|
| 1 | **Schedule serving + a generative day plan** | Without a schedule the robot never enters a session and none of its ~23 on-board activities run — this is the difference between "connects" and "works". Also fix our `query_result` shape: echo `request_id` and key the payload `schedule` / `mentor_behaviors`. | `site/hive/mqtt/scheduler.py`, `robot_data.py::get_schedule`, `moxie_server.py::provide_schedule` | **M** | 🟢 **shipped** (PR #7) — real day plan from our content modules + the 23-module catalog; `query_result` shape fixed earlier in PR #5. Plan is *deterministic*, not generative (→ BEYOND #7) |
| 2 | **`mentor_behaviors` ingest + serve** | The robot's memory of what it already did; without it Moxie repeats the same missions forever and FTUE never ends. | `models.py::MentorBehavior`, `robot_data.py::add_mbh`/`get_mbh` | **M** | 🟢 **shipped** (PR #7) — ingest + serve, durable across restarts; missions stop repeating, FTUE completes |
| 3 | **Vendor `automarkup` as the expressiveness floor** | It is MIT, pure Python, and it is *the* reason an OpenMoxie robot feels alive. Ship it behind `supervisor/markup.py` today; build the better planner behind the same seam. | `site/hive/automarkup/` (2,157 LOC) | **M** | 🟢 **shipped 2026-09-02 — as a clean-room floor, not a vendored copy.** `moxie_sdk/automarkup.py` + `vocab.py` behind the unchanged `make_markup` seam: the *behaviors* are ported and credited, the code and the 170 KB ML data table are not. Deterministic where theirs rolls dice (`blake2b`, so a golden test can pin it), and it emits only ids in our own recovered catalog — theirs includes `AUTO_GESTURE_ME` / `Gesture_We` / `Gesture_Small`, which we cannot justify from our evidence. p95 0.23 ms/line, no dependency. [mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02) |
| 4 | **Parse LLM response tags into actions** | `<exit>`, `<launch:MOD:CID>`, `<launch_if_confirmed:…>`, `<sleep>` inside the model's own text → real `response_actions`, stripped before speaking. Model agency for ~40 lines of code. | `volley.py::ingest_action_tags` | **S** | 🟢 **shipped** (PR #6) — `<exit>` / `<sleep>` / `<launch:MOD[:CID]>` parsed out of the model's own text into `Reply.actions`, stripped before speaking; per-chunk since PR #17. Caveat: `launch_if_confirmed` is lossy (→ LAUNCH) |
| 5 | **Content packs: export / import-with-review + `source_version`** | Turns content from "edit JSON in the repo" into something a community shares, and gives us a safe upgrade path for shipped defaults. | `data_import.py`, `views.py::export_data`, `management/commands/init_data.py` | **S/M** | 🔴 **open** |
| 6 | **Two-level config merge (fleet ⊕ per-robot)** | One appliance, several robots, one place to set house rules — with per-robot overrides layered on top. A `deepmerge` and a second config record. | `robot_data.py::build_config`, `models.py::HiveConfiguration` | **S** | 🟢 **shipped** — a fleet record (`$MOXIE_DATA_DIR/fleet/config.json`) merged under each robot's overrides by the pure `cloud_config.merge_config_layers` (`defaults ⊕ fleet ⊕ per-robot`; nested objects deep-merge, scalars and lists replace). `POST /config?scope=fleet` re-pushes every connected robot; the console's ⚙️ form has an *"Apply to all robots"* toggle and labels which layer a value came from |
| 7 | **Puppet / telehealth mode + a console page** | `moxie_mode:"TELEHEALTH"` + `PLAY_OUTPUT`/`INTERRUPT` makes Moxie a telepresence body — the single best demo, and a real accessibility feature. We already have the enum; we need the command path and a page. | `views.py::puppet_api`, `moxie_server.py::send_telehealth*`, `templates/hive/puppet.html` | **M** | 🔴 **open** |
| 8 | **A durable store for `mqtt/`** | Our robot cloud has **no database** — content, schedules, progress, memory and telemetry live in process memory or loose JSON. Everything above (1, 2, 5) needs one. | Django ORM + `site/hive/models.py` (8 models, 16 migrations) as the shape reference | **M** | 🟡 **partial** (PR #7) — `mqtt/moxie_sdk/store.py`, a durable **JSON** store (atomic writes) that content/schedule/`mentor_behaviors` now use. It is a stepping stone, **not a database**: no schema, no migrations, no concurrent writers, no query layer |
| 9 | **Face customization** | ~60 layered assets, a picker, and the "new child ID" cache-buster that stops Unity serving a stale texture. Pure delight for near-zero protocol risk. | `views.py::face_edit`, `content/data.py::MOXIE_CUSTOMIZATIONS` | **S/M** | 🔴 **open** |
| 10 | **Prebuilt multi-arch images + a one-file install** | Their documented install is *download one `docker-compose.yml`, run two commands*. Ours is a git clone and a cert script. This is the difference between "a project" and "a thing owners actually run". | `docker-compose.yml`, `deploy.sh`, Docker Hub `openmoxie/openmoxie-{server,mqtt}` | **M** | 🟡 **partial** (PR #8) — `docker compose up` runs certs → broker → supervisor → console with healthchecks, proven by `sim/run_compose_smoke.sh` (DoD 5 🟢). **Prebuilt multi-arch images are not published**, so the install is still a git clone |

**Quick wins worth doing in the same pass (S each):** printable **launch-card QRs** (`GO<launch:MOD>`,
`site/data/qr/extract.py`); a **device allowlist** so an anonymous broker cannot be joined by anything
(`models.py::DevicePermit` + `allow_unverified_bots`); the MQTT **`wakeup`** command
(`moxie_server.py::send_wakeup_to_bot`) behind our existing `POST /api/robots/{id}/wakeup`; and
`session.summarize()` + `persist_data`, which unlocks their shipped `MemoryChat` pattern.

**Also adopt, from the forks (each S/M):** Fork A's **speaker-scoped memory with provenance and a
quarantine for un-attributed history** (`site/hive/mqtt/conversation_memory.py`) — the right shape for
BEYOND #4; its **pre-inference safety redirect + parent-acknowledgeable flags**
(`conversation_log.py`) — the pragmatic floor under BEYOND #2; its **background-inference-with-filler**
pattern for brains slower than the robot's ~20 s reprompt window (`conversations.py::ReasoningChatSession`);
its **SQLite WAL + write-lock + backoff** and **paho `connect_async` + `reconnect_delay_set`** hardening
(`apps.py`, `util.py`, `moxie_server.py`); and Fork B's **STT `/control/reload` hot-swap** endpoint
(`site/services/stt/stt_service.py`), which lets an operator change Whisper model/device/compute without a
restart.

> **Shipped from that list since the baseline:** Fork A's **background-inference-with-filler** pattern
> (PR #14 — past a latency budget the runtime speaks a kid-appropriate filler as chunk 0, keeps inferring,
> and delivers the real line as chunk 1) and, beyond it, **streamed sentence chunks** (PR #17). Everything
> else in the two paragraphs above is still open.

### 4.2 Top 10 BEYOND — what we should build instead

| # | Go beyond | Why (one line) | Effort | Status (2026-09-02) |
|--:|---|---|:--:|---|
| 1 | **A behavior *planner*, not a markup regexer** | `automarkup` maps words to tags. The 10× version scores each line for mood, dialog-act, gesture, gaze, screen icon and SFX from the recovered vocabularies, validates every asset reference before it ships, and previews the result on the 3D SIM — so authors *see* the performance before a child does. | **L** | 🔵 **open — spec'd.** Contract-level spec: [`backlog/expressiveness.md`](backlog/expressiveness.md) §2 (P0 floor → P1 planner → P2 model-assisted) |
| 2 | **Child safety as an enforced contract** | Build `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` from [`ai-seam.md`](ai-seam.md) §② as a real pre- and post-inference stage: a local classifier (no cloud), a parent-visible review queue, and a documented escalation path. Upstream has nothing at all; Fork A's keyword flags (§2.1) are a good floor and honest about being a review aid, not a filter — this runs on a child's device and deserves better than regexes. | **M** | 🟢 PR #20 **in flight** — an agent is building `InputSafety` as a real pre-/post-inference stage |
| 3 | **Any brain, hot-swappable, per child** | OpenMoxie has one global vendor. We already have `MoxieApp` — make it a registry: per-robot and per-child app + persona binding, switchable live from the console, with the SIM as the safe rehearsal client. "Any AI wears the shell" becomes an operation, not an architecture diagram. | **M** | 🔴 **open** |
| 4 | **Memory as a product** | Beyond `complete_handler` writing a summary string: structured long-term memory (people, preferences, ongoing stories, goals) with summarization, decay, and a **parent-editable memory browser** including per-child deletion — because a memory a parent cannot read or erase is not acceptable on a child's device. | **L** | 🔴 **open** — per-device conversation history persists as JSON, but there is no structured memory and no parent-facing browser |
| 5 | **Insights that mean something** | We already ingest `Packet` telemetry and gate it on `LoggingPolicy`. Turn it into a local parent console: sessions, activity mix, mood trend over time, time-of-day patterns, "what did we talk about this week" — all on-device, nothing uploaded. This is the plan's own next slice. | **M** | 🔴 **open** — `Packet` telemetry + `LoggingPolicy` are ingested; the console view is still missing |
| 6 | **Sandboxed content extensions** | Their `METHOD` globals and conversation `code` fields are `exec()` with a 10-second timeout — powerful, and un-shareable. A capability-scoped module runtime (declared permissions, no filesystem/network by default, resource limits) makes community content packs safe to install. | **L** | 🔴 **open** |
| 7 | **A schedule that adapts** | Their `ransac_select` maximizes category variety at random. Ours should plan from `mentor_behaviors` + telemetry + parent preferences + time of day, and show the parent an explainable *"why this activity today"* line. Same interface, a real recommender behind it. | **M** | 🔴 **open** — the *floor* shipped in PR #7 (a deterministic plan); nothing adapts yet |
| 8 | **A voice you choose, with lips that match** | Ship Piper voices plus the `TTSMark[]` visemes [`ai-seam.md`](ai-seam.md) §③ specifies, so the SIM lip-syncs and telepresence sounds like the person driving it. A real robot keeps its own on-device voice — this is for every *other* body Moxie can wear. | **M** | 🟡 **partial** — Piper voices are real and the browser SIM plays the server's `CloudTTSResponse` (PR #11), proven end to end with real speech (PR #12). **`TTSMark[]` visemes are still empty** — `marks` is plumbed through `moxie_sdk/tts.py` and never populated, so nothing lip-syncs |
| 9 | **Vision events in the turn loop** | The robot already emits `eb-found-face`, `eb-lost-target`, `eb-qr-event`, `eb-dr-event`, `eb-br-event` and accepts `eb_custom_face_search` ([`vision.md`](vision.md)). Nobody — not OpenMoxie, not us — subscribes to them. A Moxie that notices you walked in is a different robot. | **M** | 🔴 **open** |
| 10 | **One appliance, one identity, one command** | OpenMoxie is a robot cloud with a config UI. Ours should be a single stack where the **parent app**, broker, supervisor, brain, STT and TTS share one device/child registry, publish as multi-arch images, and come up with a guided first run — with the SIM as a first-class client so the whole appliance is testable before a robot is ever plugged in. | **L** | 🟡 **partial** (PR #8) — one command brings the stack up; one shared device/child registry, published images and a guided first run are not built |

---

## 4.3 Backlog — the build briefs

A ranked line is a *decision*; a build agent needs a **brief**. Items whose next step is big enough to
need one get a page under [`backlog/`](backlog/README.md), written so an agent can execute it as-is:
the seam it plugs into, the recovered vocabularies it may draw from, the design, the tests, the
acceptance criteria, the effort and the risks.

| Brief | Covers | State |
|---|---|---|
| [`backlog/expressiveness.md`](backlog/expressiveness.md) | **ADOPT #3** (the markup floor) §1 · **BEYOND #1** (the behavior planner) §2 | §1 🟢 **shipped 2026-09-02** · §2 ready to build |

---

## 5. The honest ledger

### Where OpenMoxie (and its forks) are genuinely ahead of us

Not "different" — **ahead**. Listed plainly so the status tables stay honest.

1. **It makes a real robot run a real day.** Schedule + `mentor_behaviors` + the ~23 native modules is
   the whole product for an owner. We answer both queries with empty values, and in the wrong wire
   shape (`moxie_runtime.py::_on_activity` publishes a generic `result` key with no `request_id` echo,
   where the robot expects `schedule` / `mentor_behaviors` — see `moxie_server.py::provide_schedule`).
2. **Expressiveness.** 2,157 lines of markup engine vs our passthrough plus one mood and one gesture.
3. **Content you can author and share.** DB-backed conversations, globals, import/export with a review
   step, and version-aware upgrades. Ours is one starter JSON file with one conversation in it.
4. **Installability.** Prebuilt multi-arch images; the README's happy path is two commands.
5. **Delight.** Face customizer, puppet mode, mission editor, printable launch cards, a browser chat
   harness. Owner-facing features we simply do not have.
6. **The `doc/` folder.** `MoxieOverview.md` is still the best config/settings reference for this robot
   that exists anywhere, and `AssetBundleMasterManifest.csv` is a 188 KB list of **every asset in the
   robot's bundle repository** — a lookup table we should make sure our own protocol docs can match.
7. **It shipped.** v0.8, in the field, since early 2025.
8. **The family-facing product layer (Fork A).** Speaker-scoped memory with provenance, transcripts a
   parent can read, download and truly delete, pre-inference safety flags with a review queue, an
   activity launcher, and a live monitor. We have none of it, and it is the layer a parent actually
   touches.
9. **Production hardening under real load (Fork A).** SQLite WAL + write-lock + backoff, paho callback
   pinning, `connect_async` + reconnect backoff, ordered state commits — every one of these is a bug we
   have not hit yet only because no real robot has been on our broker for a week.
10. **A brain slower than the robot (Fork A).** They discovered and solved the ~20 s reprompt window with
    background inference plus filler lines. Our `Pacer`/backoff handles a *busy* gateway; it does not
    handle a brain that is simply slow.

### Where we are already ahead

1. **Brain-agnostic AI seam.** Any OpenAI-compatible endpoint, with adaptive pacing and backoff
   (`moxie_sdk/chat.py`) and a real `ERROR_OFFLINE` fallback so the robot degrades to its on-device
   brain instead of hanging. Upstream's is 14 lines hard-wired to OpenAI, and a failed inference makes
   Moxie say *"Oh no. I have run into a bug"*.
2. **Local-first speech.** faster-whisper with a dependency-free `zmqSTTRequest` decoder
   (`moxie_sdk/stt.py`) vs upstream's mandatory OpenAI Whisper API call per utterance, which sends a
   child's voice to a vendor and bills for it. Both forks fixed this independently — which is the
   strongest possible signal that local-first speech was the right default.
3. **The full RemoteChat contract.** `ResultCode`s, scored output, structured `Action`s — not just
   text + markup with `result: 0`.
4. **Config & telemetry as a contract.** `RobotCloudConfig` + `RobotStatus` + the `Packet` envelope with
   a `LoggingPolicy` upload gate. Their `MoxieLogs` model is never written to.
5. **The parent app.** Nobody else rebuilt it: 65 REST routes, zero-knowledge crypto, recovery phrases,
   a mobile web client, hardware-verified pairing QR.
6. **The SIM.** A 3D robot driven by the real protocol, so a change is testable with no hardware.
7. **Tests and CI.** 37 test files and a three-tier pipeline. Upstream has a 3-line Django stub and no
   CI; Fork A has 70 real test methods but no CI; Fork B has CI but no tests. Nobody else has both.
8. **The reverse-engineering base.** 97 doc pages, 120 recovered `.proto` files, and a full protocol
   catalog — which is why we can implement fields OpenMoxie never used (all 14 of
   `ServiceConfiguration2`, the `CloudStatus.UserState` lifecycle, `SystemState` health).
9. **Security posture.** No `DEBUG=True`, no committed secret key, no debug toolbar in production, a
   per-appliance CA instead of keys baked into a published image, and validated config edits.

## 6. How this feeds the build

Nothing here changes the [build contracts](README.md) — every ADOPT item is already *specified* in one
of them; what this audit adds is **evidence that the spec matters and a proven reference implementation
to check ourselves against.** The mapping:

| Contract | What this audit adds |
|---|---|
| [`content-module-contract.md`](content-module-contract.md) | Schedule serving, `mentor_behaviors`, packs + `source_version`, response tags, execution actions — ADOPT 1, 2, 4, 5 |
| [`ai-seam.md`](ai-seam.md) | The markup planner and `InputSafety` are the two seams OpenMoxie leaves empty — BEYOND 1, 2 |
| [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md) | Fleet ⊕ per-robot merge, face options, OTA lever — ADOPT 6, 9; insights view — BEYOND 5 |
| [`mqtt-and-conversation.md`](mqtt-and-conversation.md) | The `query_result` wire shape we currently get wrong, telehealth commands, launch QRs, device allowlist |
| [`sim-as-a-client.md`](sim-as-a-client.md) | The SIM is where the markup planner and any-brain switching get rehearsed before a child sees them |
| [`implementation-plan.md`](implementation-plan.md) | ADOPT 1, 2 and 8 belong in M6/M7; ADOPT 3 unblocks Definition-of-done criterion 1; the forks' hardening list (§2.1) is a pre-flight checklist for the first real robot on our broker |

**Go star [OpenMoxie](https://github.com/jbeghtol/openmoxie).** It is the reason there is anything to
build on, and it is still the fastest way for an owner to get a Moxie talking tonight.

---
📖 [Docs index](../README.md) · [Implementation plan](implementation-plan.md) · [Community landscape](../community-research.md) · [MQTT & conversation](mqtt-and-conversation.md) · [Backlog briefs](backlog/README.md)
