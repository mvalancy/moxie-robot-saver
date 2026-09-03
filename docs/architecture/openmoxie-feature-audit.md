# 🔬 OpenMoxie feature audit — what to adopt, and where to go beyond

> **Audit version 1 · baseline 2026-09-02 · status column refreshed 2026-09-03.** A feature-by-feature read of **OpenMoxie** (upstream `v0.8`, MIT)
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
> **2026-09-03**, and in [`orchestration-plan.md`](orchestration-plan.md)'s status log. Where a
> status is *partial*, the column says what is actually done, not what was intended. §4.4 re-ranks what is
> still open.
>
> **⚠️ How to read this page — the frozen half and the live half**
>
> This distinction is not pedantry. On **2026-09-03** two build agents were each briefed off this page
> and each lost a full run discovering on arrival that their item had already shipped: **content packs**
> (ADOPT #5, merged as PR #51 on 2026-09-02) still carried a 🔵 *open* marker, and the **voice +
> listening picker** (BEYOND #8's console half, merged as PR #48) was ranked as work to do. An audit that
> overstates what is left is worse than no audit, because it is trusted. So:
>
> | Section | Frozen or live? | What it means |
> |---|---|---|
> | §1, §2 (inventory of *their* code) | **frozen** at `c8c2d380` / `a97c85c0` / `a80a81ef` | Re-verified against the live remote heads — see the upstream re-check below |
> | §3 (the HAVE/ADOPT/BEYOND scorecard) | **FROZEN at 2026-09-02 morning** | Its *"Us today"* column is a **historical snapshot** and several cells are deliberately left describing a gap we have since closed. **Never brief an agent from §3.** §3.0 lists every superseded row |
> | §4.1, §4.2 **Status** columns | **live** | The authority on what we have shipped. Every 🟢 names the PR *and* the test that proves it |
> | §4.3 (briefs), §4.4 (the ranking) | **live** | §4.4 is the only place to look for *"what should I build next"* |
> | §5 (the honest ledger) | **frozen**, with a live "closed since" note at its head | Same rule as §3 |
>
> **The rule for anyone editing this page:** a row is 🟢 only if you can name the **file** and the
> **test** that prove it. "The PR is merged" is not proof; a test name is.
>
> **Upstream re-check — checked 2026-09-03: still nothing new upstream.** All three source repos were
> read again at their **live remote heads** (`git ls-remote`, so this is not a stale local clone
> speaking), branches and tags both:
> upstream [`jbeghtol/openmoxie`](https://github.com/jbeghtol/openmoxie) is still `c8c2d380`
> (2026-01-15, *"Added warning about alt openmoxie (#58)"*), [`Noonster77/openmoxie`](https://github.com/Noonster77/openmoxie)
> is still `a97c85c0` (2026-08-30, *"Add parent review acknowledgments"*), and
> [`vapors/openmoxie-ollama`](https://github.com/vapors/openmoxie-ollama) is still `a80a81ef`
> (2025-08-17). No repo has gained a branch or a tag either: both openmoxie repos carry the same three
> branches (`main`, `disclaimer` `a1521754`, `release` `36f8c122` — the latter two identical across the
> fork and unmoved) and the same seven tags topping out at `v0.7`; the ollama fork has one branch and
> three tags all pointing at `6eaa7458`. Every one of those is the exact commit this audit was written
> against — **nothing has been published upstream or in either fork since the baseline**, so there is
> nothing new to adopt, re-rank or re-cite from their side. **A verified nothing is a result**, and it
> is now the second consecutive day of it: the ADOPT list below is complete, and what remains is ours
> to build.

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
| **TTS** | **None.** Moxie synthesizes on-device from markup. Puppet mode's `send_telehealth_speech` just ships text+markup. | ✅ correct — there is nothing to build for a real robot. We build it anyway for every *other* body: three server voices (gateway `piper-amy` — live 2026-09-02 — → offline Piper → tone), each falling back to the next |
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

### 2.4 Upstream re-check — 2026-09-03, and the one thing that *was* new

**The code: nothing new, verified three ways.** Beyond the `git ls-remote` head check in the header, the
whole fork network was swept rather than only the two forks this audit already tracks — because "the two
active forks" is a claim that decays. Upstream has **40 forks**; the 15 most recently pushed were compared
to `jbeghtol/openmoxie:main` through the GitHub compare API. The result is that our selection still holds:

| Fork | vs upstream `main` | Verdict |
|---|---|---|
| `Noonster77/openmoxie` (**Fork A**) | ahead (the audited family edition) | Still the only fork with a product layer. Unmoved since 2026-08-30 |
| `vapors/openmoxie-ollama` (**Fork B**) | ahead (separate repo, not a GitHub fork) | Unmoved since 2025-08-17. Stale, still cited for its STT `/control/reload` idea |
| `omerarman-git/openmoxie` | ahead 2 | **Docs only** — a Mac-Mini native-install plan and a pairing/TLS note (2026-05-18). No code. But see the field report below, filed by the same author |
| `oregonlooney/openmoxie_loon` | diverged, ahead 2 | *"Add telehealth studio for custom markup playback"* (2025-09-25) — the same instinct as ADOPT #7, which we shipped past in PR #43 |
| `Novators-kz/openmoxie` | ahead 1 | One commit, message *"antigravity test"*. Noise |
| `justin-zeno`, `tungpttech-ai`, and the rest of the 15 | **identical** to upstream | Mirrors |

**So: nothing to adopt from anyone's code, for the second consecutive day.** That is the whole finding on
the code side, and it is stated rather than padded.

#### What *was* new — two field reports on upstream's issue tracker, and one of them matters to us

Upstream's **issues** are not code, and we would not port them; but they are the only place in this
landscape where a **real robot's behaviour** is written down by someone holding one, and we have none. Two
open issues postdate this audit's evidence base:

**[jbeghtol/openmoxie#60](https://github.com/jbeghtol/openmoxie/issues/60)** (opened 2026-05-19, 0 comments)
— *"Empty `google_api_key` silently drops `license` query → `bo-wifi.apk` crash loop on firmware
24.10.801."* The reporter's account: upstream's handler (`moxie_server.py`:194) short-circuits and
publishes **nothing** when no Google service account is configured, and a 24.10.801 robot answers that
silence by crash-looping its Wi-Fi App every few seconds — `std::terminate` out of a `regex_error`, and a
Unity-side `SIGABRT` variant. Their stated workaround is that a *syntactically valid but fake* service
account is enough, because the robot only checks that a `query_result` carrying a `license_values` **entry**
comes back.

> **What this changes for us — and what it does not.** §3.3 scores our `license` handling **HAVE
> (deliberate — we are local-first)**, and this report does not overturn that; if anything it *validates*
> the shape we chose. Upstream's failure mode is a **dropped** answer, and we never drop one:
> `moxie_runtime.py::_on_activity` handles `license` unconditionally alongside `schedule` and
> `mentor_behaviors`, and `wire.py::build_activity_response` sends that field's **empty value** rather
> than skipping the publish — deliberately, and the docstring says so: *"we answer honestly-empty."* The
> robot's pull resolves; it does not hang. **The residual risk is precise and we cannot close it:** we
> publish `license_values: []`, and this report claims the firmware wants *an entry*, not merely the
> field. Empty-list versus at-least-one-record is exactly the distinction no test of ours can settle,
> because settling it needs a robot. Filed in §4.4's blocked list, **not** ranked — and deliberately not
> "fixed" by inventing a fake credential to hand a robot, which is a decision an owner should make
> knowingly rather than one an agent should slip into a merge.
>
> **Weight it honestly:** one reporter, zero corroborating comments, firmware **24.10.801** where our
> corpus is stamped `v24.10.803`, and the same account authored the docs-only fork above. It is an
> unconfirmed field report — which still makes it the strongest evidence anyone has about a code path we
> deliberately no-op.

**[jbeghtol/openmoxie#62](https://github.com/jbeghtol/openmoxie/issues/62)** (opened 2026-07-10, 4 comments)
— a `BoVision` crash in `TFEmbeddingRecognizer::RecognizeFace()` (`std::out_of_range: unordered_map::at:
key not found`) that restarts `me.embodied.services.BoVision`, shows the crossed-ear icon and stops the
conversation, on `v24.10.803`. It is **robot-side and not cloud-triggered** — the reporter says covering
the camera changes nothing — so there is nothing for us to adopt or fix. It is worth one line anyway as
context for **BEYOND #9**: our vision-events work is built and unproven, and the face pipeline it
subscribes to is fragile enough on real hardware to take the robot out of a conversation. One more reason
that row stays 🟡.

---

## 3. The scorecard — HAVE / ADOPT / BEYOND

Read against [`implementation-plan.md`](implementation-plan.md) (status tables + Definition of done) and
the code in [`../../mqtt/`](../../mqtt/) + [`../../server/`](../../server/), audited 2026-09-02.

- **HAVE** — ours is equal or better; nothing to port.
- **ADOPT** — they have it, we should port the *idea* (never the code, unless we vendor it with its MIT
  notice) and cite where it lives in their tree.
- **BEYOND** — the honest answer is neither: their version is a floor, and the thing we should build is
  a different, bigger thing.

### 3.0 ⚠️ This scorecard is a frozen snapshot — these rows are superseded

Everything in §3 describes **2026-09-02 morning**. It is kept unrewritten so the audit's reasoning stays
auditable, which means its *"Us today"* column now understates us in fifteen places. **Do not brief a
build agent from §3** — brief from §4.4. Each row below is closed; the PR and the test that prove it are
in the §4.1/§4.2 Status column, and the ones re-verified against `origin/dev` on 2026-09-03 are marked.

| §3 row | What it still says | The truth on `origin/dev`, 2026-09-03 |
|---|---|---|
| 3.1 Robot identity / JWT | *"broker is anonymous … brief ready"* | **P0 shipped** — `per_listener_settings` + two ACL files confine every client to its own `%c` subtree; `sim/tests/test_broker_acl.py` (30 tests) + `sim/run_acl_proof.sh` (18 delivery checks against a real `eclipse-mosquitto:2.0.20`). P1/P2 remain open and blocked on A1–A4 |
| 3.2 LLM response tags | *"nothing parses tags out of model text"* | Shipped PR #6, per-chunk since PR #17 — `sim/tests/test_action_tags.py`, `sim/tests/test_live_action_tags.py` |
| 3.2 Puppet / telehealth | *"no command path, no UI"* | Shipped PR #43 — `mqtt/moxie_sdk/telehealth.py`; `test_telehealth.py` (42) + `test_telehealth_runtime.py` (50) + `test_telehealth_view.py` (18) |
| 3.3 Content model / **authoring UI** | *"edit JSON by hand"* | **Partly closed.** A parent can now install, review, diff and export content from the 📦 card (`server/static/index.html`), but there is still no editor for authoring a *new* conversation — that half is genuinely open (§4.4 #6) |
| 3.3 Content packs: import/export + review | *"none"* | Shipped PR #51, hardened PR #78 — `mqtt/moxie_sdk/content/packs.py` (942 lines); `test_content_packs.py` (78) + `test_content_packs_runtime.py` (36) + `test_content_pack_sandbox.py` (46) |
| 3.3 Content versioning | *"none"* | Same PR — `source_version` **and** a `local_rev` digest, so an upstream re-import reports `CONFLICT` instead of clobbering |
| 3.3 Schedule serving | *"answers with `{}` and with the wrong shape"* | Shipped PR #5 (shape) + PR #7 (the plan) + the recommender — `sim/tests/test_schedule.py`, `test_schedule_planner.py` (37), `test_schedule_sil_e2e.py` |
| 3.3 `mentor_behaviors` history | *"returns `[]`"* | Shipped PR #7, durable across restarts |
| 3.3 Native-module launching | *"no code schedules them"* | The 23-module catalog is scheduled by `mqtt/moxie_sdk/schedule.py` |
| 3.4 Config model | *"no fleet-wide layer"* | Shipped — `cloud_config.merge_config_layers`; `sim/tests/test_fleet_config.py` |
| 3.4 Face customization | *"~60-asset table"* vs ours | Shipped PR #36, widened PR #47 — 72 options across 11 slots; `sim/tests/test_faces.py` (46). Row already reads **HAVE**; listed here only because the count moved |
| 3.4 Wake command | *"the MQTT `wakeup` command is not sent"* | Shipped PR #55 — `moxie_runtime.py::WAKEUP_COMMAND` publishes `{"command":"wakeup"}` on `/devices/{id}/commands/wakeup`, and `server/moxie_server/main.py`:301 forwards to it instead of returning a lie |
| 3.4 Telemetry / insights | *"console view still missing"* | Shipped PR #55 — durable `telemetry_packets` + `telemetry_daily`; `test_telemetry.py` (29) + `test_telemetry_runtime.py` (21) + `test_sil_durable_telemetry.py` (10). The *interesting* half (sessions, mood trend) is still open — BEYOND #5 |
| 3.4 **Device allowlist** | already **HAVE** | Now also enforced at the broker, not just the supervisor (the ACL above) |
| 3.5 Tests | *"37 test files"* | **105** — `sim/tests/test_*.py` + `sim/test_*.mjs` + `tools/robot-toolkit/test_*.py`, counted on `origin/dev` 2026-09-03 |
| 3.5 Prebuilt images | *"published at v0.6.0"* | **Verified in the registry 2026-09-03**, which the ADOPT #10 row could not claim before: an anonymous GHCR tag list returns `0.6.0 / 0.6 / 0.7.0 / 0.7 / latest` for all three of `supervisor`, `console`, `broker-certs`, and `supervisor:0.7.0` is a real OCI image **index** carrying `linux/amd64` **and** `linux/arm64` |

**Still true in §3, and worth reading it for:** launch-card QRs (`GO<launch:MOD>` — nothing renders one),
OTA push, the missions editor, stored-`METHOD` extensions, and the one-`ChildProfile`-for-all-robots
identity gap. Those five are the live remainder of the scorecard.

### 3.1 Onboarding & transport

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| Endpoint / migration QR | `MoxieServer.get_endpoint_qr_data()` sets 4 of 14 `ServiceConfiguration2` fields | `GET /local/endpoint/payload` + `/local/endpoint/qr.png`, `tools/pairing/moxie_endpoint_qr.py`; all 14 fields decoded in [`mqtt-and-conversation.md`](mqtt-and-conversation.md) §1.3; density-optimized `gcp_project="o"` | **HAVE** |
| Wi-Fi / pairing QR | `get_wifi_qr_data()` (`"PA"` + `StartPairingQR`) | `tools/pairing/moxie_qr.py` + `GET /local/direct/wifi_qr.png`; **hardware-verified against a real robot**; browser↔python byte-parity asserted by `sim/test_qr.mjs` | **HAVE** |
| **Launch-card QRs** (`GO<launch:MOD>`) | `site/data/qr/extract.py` → 24 printable PNGs | none | **ADOPT** (S) |
| Broker + TLS | `mqtt.Dockerfile` + `site/data/openmoxie.conf`, **keys baked into the image** (`keys/`) | `mqtt/broker/gen-certs.sh` mints a **per-appliance CA**; keys git-ignored | **HAVE** |
| Connect/disconnect detection | regex over `$SYS/broker/log/#` | same technique, `CONNECT_RE`/`DISCONNECT_RE` in `moxie_runtime.py` | **HAVE** |
| Robot identity / JWT | `RobotCredentials` mints RS256 JWTs, can ADB-pull `uuid.txt` + `RS256.key` — but only to *impersonate* a robot: their broker is `allow_anonymous true` and never verifies one | broker is anonymous; we don't verify JWTs either (deferred, §3b). The pairing gate is **service refusal, not authentication** — an unpermitted device still connects, and a spoofed `d_<uuid>` is served as that robot | **ADOPT** (M) — 🔵 **brief ready**: [`backlog/security-broker-auth.md`](backlog/security-broker-auth.md) phases it P0 (a `%c` ACL + a supervisor credential, shippable against an unmodified robot) → P1 (the broker asks the supervisor to verify the RS256 JWT against an enrolled public key) → P2 (a spoofed id refused at CONNECT). *"Parity — both punt"* was true and useless; repriced. |
| **Device allowlist / pairing gate** | `MoxieDevice.permit`, `pairing_status`, `HiveConfiguration.allow_unverified_bots` (stored but never enforced on the MQTT path) | **SHIPPED** — durable `fleet/permits.json`, **closed by default**: an unpermitted robot is *pending* (minimal config, `pairing_status:"unpairing"`, **no `child_pii`**, no brain/schedule/telemetry), gated at the transport boundary; `GET`/`POST /permits` + the console's 🔐 Robot access card (one-click Permit/Revoke + the `allow_unverified_bots` toggle); pairing through the console auto-permits; `MOXIE_ALLOW_UNVERIFIED_BOTS=1` keeps a pre-gate deployment working | **HAVE** |
| OTA push (801→803 lever) | notes only in `doc/RemoteModuleAPI.md` (`ota_update`, `_PROVIDE_HTTP_TOKENS`, static `url` file) | `ota_update`/`forbid_otaver` specified in [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md), **not built** | **ADOPT** (M) |

### 3.2 The turn — brain, memory, expressiveness

| Feature | OpenMoxie | Us today | Verdict |
|---|---|---|---|
| RemoteChat response | text + markup + `response_actions`; `result` is always `0` | full contract: `ResultCode` enum incl. **`ERROR_OFFLINE`** (robot falls back to its on-device brain), scored output (mood/dialog-act), `Action` passthrough — `moxie_sdk/wire.py`, `types.py` | **HAVE** |
| LLM provider | upstream `ai_factory.py` — **14 lines, OpenAI only**, no `base_url`. Both forks fixed this independently (§2) | `chat.py::make_openai_chat(base_url, …)` — any OpenAI-compatible endpoint (LiteLLM, Ollama, vLLM, LM Studio), plus `Pacer` + `call_with_backoff` for 429/5xx, plus `WebhookApp` as a language-agnostic second brain | **HAVE** vs upstream; **parity** with Fork A, which also puts the `base_url` in the UI with a connection test |
| Notify-based history | `ChatSession.ingest_notify` | `MoxieRuntime._ingest_notify` — same rule (skip `animation:`/`silent:` lines) | **HAVE** |
| History persistence | in-memory per session; lost on restart | per-device JSON under `MOXIE_MEMORY_DIR`, atomic write, trimmed to `MOXIE_MEMORY_TURNS` | **HAVE** |
| **Conversation summarization → long-term facts** | `session.summarize()` + `complete_handler` writing to `volley.persist_data`; shipped as `content_modules/MemoryChat.json` | 🟢 **BUILT 2026-09-02** — `MemoryStore` (durable per-robot `memory.json`, module-namespaced, bounded, provenance per merge, `LoggingPolicy.NO_DATA` drops writes) + a **structured** `session.summarize()` (facts/preferences/open_threads, safety-filtered, no verbatim child speech, brain failure → nothing written), fired by the runtime on `<exit>` / module switch / disconnect. Ships as `content_modules/memory_chat.json`; a parent reads and erases it over `GET`/`DELETE /memory`. See [content-module-contract.md](content-module-contract.md) §Memory | **ADOPT — done** (the console's 🧠 What Moxie remembers card browses and erases it since 2026-09-02; the honest limits are that a wrong fact is sticky and erase is per activity, not per item — BEYOND #4) |
| Prompt templating | Django templates over `volley`/`session` | Jinja2 `content/render.py::render_prompt`, same variables | **HAVE** |
| **LLM response tags** (`<exit>`, `<launch:MOD:CID>`, `<sleep>`) | `Volley.ingest_action_tags()` — parses the model's own text into real actions and strips them | we have `Reply.actions`, but nothing parses tags out of model text | **ADOPT** (S) — very cheap model agency |
| **`automarkup` — text → behavior** | 2,157 LOC rule+ML engine; every AI line goes through it | 🟢 **closed at the floor (2026-09-02):** `supervisor/markup.py` delegates to one pure deterministic generator every app shares (`moxie_sdk/automarkup.py`), with a frozen doc-cited catalog (`vocab.py`) and 8 byte-exact goldens. The ML rule table is deliberately not ported — that is the P2 conversation | **BEYOND** (L) — the *planner* remains: see §4.2 and [`backlog/expressiveness.md`](backlog/expressiveness.md) §2 |
| Puppet / telehealth | full: `moxie_mode:"TELEHEALTH"`, `PLAY_OUTPUT`/`INTERRUPT`, mood+intensity picker, live state poll | `MoxieMode.TELEHEALTH` exists in `cloud_config.py`; **no command path, no UI** | **ADOPT** (M) — 🔵 **🟢 **built** (2026-09-02, `feat/telehealth`)**: [`backlog/telehealth.md`](backlog/telehealth.md) |
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
| **Face customization** | full layered editor + ~60-asset table + "new child ID" cache-buster | **SHIPPED** — `child_pii.face_options` + a deterministic `child_pii.id` cache-buster, fleet ⊕ per-robot layering, the console's 🎨 Moxie's look card; catalog is now **72 options across 11 of the 14 recovered slots** — the 12 doc-cited hex colours plus upstream's 60-id asset table **ingested as cited data** (see ADOPT #9), still with no invented ids | **HAVE** (parity on the vocabulary; ours is data-driven, cited and `caution`-flagged) |
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
| **Prebuilt images / one-file install** | multi-arch images on Docker Hub; documented install is *download `docker-compose.yml`, run two commands* | root `docker-compose.yml` (one stack, clone+build) **and** a self-contained `docker-compose.images.yml` that pulls multi-arch GHCR images the release workflow pushes on every `v*` tag — *download one file, run one command* | **HAVE** (published at v0.6.0) |
| Tests | `site/hive/tests.py` is the 3-line Django stub — **zero tests** | 37 test files (`sim/tests/*.py`, `sim/test_*.mjs`, `tools/robot-toolkit/test_*.py`) | **HAVE** (far ahead) |
| CI | none (`.github/` = `FUNDING.yml`) | three-tier: fast on `dev`, deep + HIL on PR-to-`main`, release on tags | **HAVE** (far ahead) |
| Docs | 5 dense `doc/*.md` + `AssetBundleMasterManifest.csv` (every robot asset) | 97 doc pages + a searchable static explorer + mechanical link/consistency guards | **HAVE** (ahead) — but see the asset-manifest note in §5 |
| Security posture | `DEBUG=True`, `ALLOWED_HOSTS=['*']`, committed `SECRET_KEY`, debug-toolbar on, plaintext keys in SQLite, anonymous broker | FastAPI, status endpoint bound to `127.0.0.1`, whitelist-validated config edits, git-ignored `.env`, per-appliance CA | **HAVE** |
| Ops ergonomics | "Refresh from DB" button; MQTT thread inside Django `runserver --noreload` | separate supervisor process + `/status` JSON | **HAVE** |

---

## 4. The ranked backlog

Effort key: **S** ≈ a day or less · **M** ≈ a few days · **L** ≈ a milestone of its own.

### 4.1 Top 10 ADOPT — port the idea, credit the source

| # | Adopt | Why (one line) | Their file | Effort | Status — live, refreshed 2026-09-03 |
|--:|---|---|---|:--:|---|
| 1 | **Schedule serving + a generative day plan** | Without a schedule the robot never enters a session and none of its ~23 on-board activities run — this is the difference between "connects" and "works". Also fix our `query_result` shape: echo `request_id` and key the payload `schedule` / `mentor_behaviors`. | `site/hive/mqtt/scheduler.py`, `robot_data.py::get_schedule`, `moxie_server.py::provide_schedule` | **M** | 🟢 **shipped** (PR #7) — real day plan from our content modules + the 23-module catalog; `query_result` shape fixed earlier in PR #5. Plan is *deterministic*, not generative (→ BEYOND #7) |
| 2 | **`mentor_behaviors` ingest + serve** | The robot's memory of what it already did; without it Moxie repeats the same missions forever and FTUE never ends. | `models.py::MentorBehavior`, `robot_data.py::add_mbh`/`get_mbh` | **M** | 🟢 **shipped** (PR #7) — ingest + serve, durable across restarts; missions stop repeating, FTUE completes |
| 3 | **Vendor `automarkup` as the expressiveness floor** | It is MIT, pure Python, and it is *the* reason an OpenMoxie robot feels alive. Ship it behind `supervisor/markup.py` today; build the better planner behind the same seam. | `site/hive/automarkup/` (2,157 LOC) | **M** | 🟢 **shipped 2026-09-02 — as a clean-room floor, not a vendored copy.** `moxie_sdk/automarkup.py` + `vocab.py` behind the unchanged `make_markup` seam: the *behaviors* are ported and credited, the code and the 170 KB ML data table are not. Deterministic where theirs rolls dice (`blake2b`, so a golden test can pin it), and it emits only ids in our own recovered catalog — theirs includes `AUTO_GESTURE_ME` / `Gesture_We` / `Gesture_Small`, which we cannot justify from our evidence. p95 0.23 ms/line, no dependency. [mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02) |
| 4 | **Parse LLM response tags into actions** | `<exit>`, `<launch:MOD:CID>`, `<launch_if_confirmed:…>`, `<sleep>` inside the model's own text → real `response_actions`, stripped before speaking. Model agency for ~40 lines of code. | `volley.py::ingest_action_tags` | **S** | 🟢 **shipped** (PR #6) — `<exit>` / `<sleep>` / `<launch:MOD[:CID]>` parsed out of the model's own text into `Reply.actions`, stripped before speaking; per-chunk since PR #17. Caveat: `launch_if_confirmed` is lossy (→ LAUNCH) |
| 5 | **Content packs: export / import-with-review + `source_version`** | Turns content from "edit JSON in the repo" into something a community shares, and gives us a safe upgrade path for shipped defaults. | `data_import.py`, `views.py::export_data`, `management/commands/init_data.py` | **S/M** | 🟢 **SHIPPED — P0 *and* P1, PR #51 (2026-09-02); hardened by PR #78 (2026-09-03).** **Proof:** [`moxie_sdk/content/packs.py`](../../mqtt/moxie_sdk/content/packs.py) (942 lines) behind 160 tests — `sim/tests/test_content_packs.py` (78) · `test_content_packs_runtime.py` (36) · `test_content_pack_sandbox.py` (46, the hostile-pack fence). ⚠️ *This cell read "open" until 2026-09-03 and cost a build agent a whole run — it is why the header now has a frozen/live table.* Design: [`backlog/content-packs.md`](backlog/content-packs.md): a versioned, digest-checked pack file; export from a positive field allowlist (no `child_pii`, no memory, no telemetry, no keys); import-with-review whose per-item state is a **2×2** — upstream compares only `source_version` integers (`data_import.py`:8-11) and so silently clobbers a locally-edited item on "upgrade"; ours also tracks a `local_rev` digest, so a re-imported upstream pack reports `CONFLICT` and defaults to un-ticked. **What shipped:** [`moxie_sdk/content/packs.py`](../../mqtt/moxie_sdk/content/packs.py) (pure, stdlib) + three fleet `JsonStore` collections + five status-HTTP routes + `reload_content()` — an attribute swap, so an import is live on the **next turn** with no restart — + the 📦 console card (inventory with an "edited here" badge, tick-and-export, a review table with the field-level diff and Accept / Keep mine / Skip per item, undo). Effective content = shipped defaults ⊕ the overlay, so a release upgrading `starter.json` obeys the same rule as a stranger's pack. Checksummed, deliberately **not** signed — the honest security property is structural: an imported `code` block is stored, flagged ⚠️ and never executed (BEYOND #6 is what would change that). Contract: [content-module-contract.md](content-module-contract.md#content-packs-moving-content-between-machines-p0-built-2026-09-02). Not in P0, and still open: removing an item, face/config packs, reading an OpenMoxie v0 pack. **PR #78's hardening is a finding, not a tidy-up:** the dependency-free fallback renderer (`render.py::_minimal_render`, used on any install without the `content` extra) evaluated a bare dotted path with `getattr` over the live context, and a pack chooses every segment of that path — so `{{ session.__class__.__repr__.__globals__.inspect.os.environ }}` read this process's environment. An untrusted-input surface earned its own suite the day it was found |
| 6 | **Two-level config merge (fleet ⊕ per-robot)** | One appliance, several robots, one place to set house rules — with per-robot overrides layered on top. A `deepmerge` and a second config record. | `robot_data.py::build_config`, `models.py::HiveConfiguration` | **S** | 🟢 **shipped** — a fleet record (`$MOXIE_DATA_DIR/fleet/config.json`) merged under each robot's overrides by the pure `cloud_config.merge_config_layers` (`defaults ⊕ fleet ⊕ per-robot`; nested objects deep-merge, scalars and lists replace). `POST /config?scope=fleet` re-pushes every connected robot; the console's ⚙️ form has an *"Apply to all robots"* toggle and labels which layer a value came from |
| 7 | **Puppet / telehealth mode + a console page** | `moxie_mode:"TELEHEALTH"` + `PLAY_OUTPUT`/`INTERRUPT` makes Moxie a telepresence body — the single best demo, and a real accessibility feature. We already have the enum; we need the command path and a page. | `views.py::puppet_api`, `moxie_server.py::send_telehealth*`, `templates/hive/puppet.html` | **M** | 🟢 **shipped 2026-09-02 (PR #43).** Pure [`moxie_sdk/telehealth.py`](../../mqtt/moxie_sdk/telehealth.py) (JSON keys cross-checked in CI against the recovered `TeleHealth.proto`, and `TeleHealth_pb2` where protobuf is present); six runtime verbs + `GET`/`POST /telehealth` on the status server; the 🎭 "Be Moxie" card (11 recovered moods, intensity **0–2** not a float, interrupt, live text transcript, an honest "never reported" state and a bedtime warning); `bridge.js` + `virtual_moxie.py --telehealth`, so CI proves the SIM speaks the operator's line. Operator text goes through the safety classifier as `role=MOXIE` and into the parent's journal — a block is **returned to the operator with its reason (400)**, never silently rewritten. Contract: [`mqtt-and-conversation.md` §3.9](mqtt-and-conversation.md); design record + what differs: [`backlog/telehealth.md`](backlog/telehealth.md). |
| 8 | **A durable store for `mqtt/`** | Our robot cloud has **no database** — content, schedules, progress, memory and telemetry live in process memory or loose JSON. Everything above (1, 2, 5) needs one. | Django ORM + `site/hive/models.py` (8 models, 16 migrations) as the shape reference | **M** | 🟡 **partial** (PR #7) — `mqtt/moxie_sdk/store.py`, a durable **JSON** store (atomic writes) that seven collections now use — `mentor_behaviors`, `memory`, `schedule_explain`, `safety_events`, `safety_counts` and the two fleet-scoped ones, `config` and `permits`. It is a stepping stone, **not a database**: no schema, no migrations, no concurrent writers, no query layer. Re-assessed 2026-09-02: it keeps absorbing collections without breaking, so the DB is **not** on the critical path of anything ranked in §4.4 — content packs (ADOPT #5) deliberately add three more shared collections rather than force the migration. **Re-checked 2026-09-03: the telemetry exception is closed.** This cell used to end *"the one place the missing store already hurts is telemetry, which is not durable at all"* — that is no longer true. PR #55 moved telemetry onto the same `JsonStore` (a 500-envelope ring + 35 days of roll-ups; `test_telemetry.py` 29 · `test_telemetry_runtime.py` 21 · `test_sil_durable_telemetry.py` 10), so **no ranked item is now blocked on the missing database.** The row stays 🟡 because the honest limits are unchanged — **thirteen** collections now (`config`, `permits`, `voice`, `memory`, `mentor_behaviors`, `schedule_explain`, `safety_events`, `safety_counts`, `content_items`, `content_packs`, `content_backup`, `telemetry_packets`, `telemetry_daily`, counted 2026-09-03) and still no schema, no migrations, no cross-process writer, no query layer — and the first thing that will actually force the migration is a second concurrent writer, which is §4.4 #3's production-hardening item, not a feature |
| 9 | **Face customization** | ~60 layered assets, a picker, and the "new child ID" cache-buster that stops Unity serving a stale texture. Pure delight for near-zero protocol risk. | `views.py::face_edit`, `content/data.py::MOXIE_CUSTOMIZATIONS` | **S/M** | 🟢 **shipped (PR #36); catalog widened to parity by ingesting upstream's asset table as cited data (PR #47, 2026-09-02)**. `mqtt/moxie_sdk/faces.py` renders a selection into `child_pii.face_options` (`ChildDecrypted` field 17) and re-keys `child_pii.id` on every change — a **deterministic UUIDv5** over the chosen layers rather than upstream's random `uuid4`, so a look change busts the texture cache and an idempotent re-push does not. Layered fleet ⊕ per-robot like every other override (ADOPT #6); the console's 🎨 Moxie's look card previews colours as swatches, per-robot with the house look underneath. **The catalog is now 72 options across 11 of the 14 slots** (2026-09-02): the 12 doc-cited hex colours (`origin: recovered-enum`) plus upstream's 60-entry `MOXIE_CUSTOMIZATIONS` table **ingested as data, not code** — id strings only, under an inline citation (MIT, `site/hive/content/data.py`, commit `c8c2d380`, sha256 of the id list) in `mqtt/moxie_sdk/face_assets.json`; the slot mapping and all labels are ours, `unmapped` is empty, every manifest entry carries `caution: true` because upstream's own note says some crashed Unity without saying which, and `Stickers`/`Extras`/`Misc` stay empty rather than invented. A manifest id is a whole asset label and rides the wire verbatim; a recovered enum keeps the assumed join. Ids outside the catalog still go through `face.custom`. See `ATTRIBUTION.md`. Two flagged assumptions (the enum label spelling; that the cache is keyed on `child_pii.id`) and **no physical robot has rendered it** |
| 10 | **Prebuilt multi-arch images + a one-file install** | Their documented install is *download one `docker-compose.yml`, run two commands*. Ours is a git clone and a cert script. This is the difference between "a project" and "a thing owners actually run". | `docker-compose.yml`, `deploy.sh`, Docker Hub `openmoxie/openmoxie-{server,mqtt}` | **M** | 🟢 **shipped — published at v0.6.0** (PR #8 + `feat/published-images`) — `docker compose up` runs certs → broker → supervisor → console with healthchecks (DoD 5 🟢), **and** the release workflow now builds `linux/amd64`+`linux/arm64` images for all three on every `v*` tag → `ghcr.io/mvalancy/moxie-robot-saver/{supervisor,console,broker-certs}` (`X.Y.Z` / `X.Y` / `latest`, OCI-labelled). The self-contained `docker-compose.images.yml` makes the install *download one file + `docker compose up`*, no clone; proven end to end by `MOXIE_SMOKE_MODE=images sim/run_compose_smoke.sh` with `pull_policy: never`. **Registry presence verified 2026-09-03** (this cell previously said *"nothing is in the registry until the first post-merge tag, so the pull itself is unverified"*): an anonymous GHCR tag list returns `0.6.0 / 0.6 / 0.7.0 / 0.7 / latest` for **all three** of `supervisor`, `console` and `broker-certs`, and `supervisor:0.7.0` resolves to an OCI image **index** carrying `linux/amd64` and `linux/arm64`. What is still unverified is a *pull on a clean machine* — the tags list proves publication, not that the image runs where it was never built |

**Quick wins worth doing in the same pass (S each):** printable **launch-card QRs** (`GO<launch:MOD>`,
`site/data/qr/extract.py`); a **device allowlist** so an anonymous broker cannot be joined by anything
(`models.py::DevicePermit` + `allow_unverified_bots`); the MQTT **`wakeup`** command
(`moxie_server.py::send_wakeup_to_bot`) behind our existing `POST /api/robots/{id}/wakeup`; and
`session.summarize()` + `persist_data`, which unlocks their shipped `MemoryChat` pattern
(🟢 **done 2026-09-02** — see the §3.2 row and BEYOND #4 below).

> **Quick-win status, re-checked 2026-09-03 against `origin/dev`.**
> 🟢 **Device allowlist — shipped**, and beyond upstream's: `permits_view` / `set_permit` /
> `set_allow_unverified_bots` on the runtime with `GET`/`POST /permits`, a pending state, a
> child-free `build_unpaired_cloud_config` for a robot that is not let in, and the console's permits
> card. Upstream stores the flag and never enforces it on the MQTT path; ours does, and the broker ACL
> (PR #44) now confines every client to its own device subtree underneath it.
> 🔴 **Launch-card QRs — still open (re-verified 2026-09-03).** A repo-wide search for `launch_card` /
> `GO<launch` finds only the action-tag *parser* side — `moxie_sdk/types.py`, `sim/web/bridge.js`,
> `test_presence*.py`. Nothing in `tools/` or `server/` renders a printable card. The parser understands
> `<launch:MOD[:CID]>` (PR #6); the sheet a parent would print does not exist.
> 🟢 **MQTT `wakeup` — shipped (PR #55, 2026-09-02).** This was the page's most misleading gap: the
> route returned `{"error": null}` and published nothing, so the console reported success for an action
> that never happened. It now publishes for real —
> [`moxie_runtime.py`](../../mqtt/supervisor/moxie_runtime.py)`::WAKEUP_COMMAND` sends
> `{"command":"wakeup"}` on `/devices/{id}/commands/wakeup`, and
> [`server/moxie_server/main.py`](../../server/moxie_server/main.py):301 forwards to
> `POST /wakeup?device_id=…` instead of answering for it. `sim/tests/test_sil_durable_telemetry.py`
> pins the sibling honesty case (`reboot` is a **501 that says why** and publishes nothing, rather than
> a comfortable lie). **Honest ceiling:** our corpus establishes the topic and payload but no
> acknowledgement, so a published `wakeup` is fire-and-forget — the console can say *sent*, never
> *worked*, and no physical robot has confirmed one.

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
> and delivers the real line as chunk 1) and, beyond it, **streamed sentence chunks** (PR #17); its
> **pre-inference safety redirect + parent-acknowledgeable flags** (PR #20 — `moxie_sdk/safety.py`'s
> `RuleClassifier` → `InputSafety`, a durable `safety_events` queue and the console's 🛡️ card with
> "I have seen this"); and the *provenance* half of its speaker-scoped memory (every
> remembered item carries `_provenance`, though **not** which speaker said it).
>
> **Still open from those two paragraphs, re-checked 2026-09-03:** Fork A's genuinely
> speaker-*scoped* memory (attributing a fact to *who said it*, with a quarantine for un-attributed
> history) — we attribute a fact to the *activity* that produced it, not to a person; its **SQLite WAL +
> write-lock + backoff** and **paho `connect_async` + `reconnect_delay_set`** hardening (our
> `JsonStore` has an in-process lock and no cross-process story; `moxie_runtime.py`:458 — the line moved, the
> problem did not — is still a plain blocking `client.connect(self.host, self.port, 30)` with no
> `connect_async` and no `reconnect_delay_set`); and Fork B's **STT `/control/reload` hot-swap**, which PR #48's 🎚️ voice picker
> answers *for our own engines* (pick a model, next turn uses it, no restart) but not for a separate
> STT microservice.

### 4.2 Top 10 BEYOND — what we should build instead

| # | Go beyond | Why (one line) | Effort | Status — live, refreshed 2026-09-03 |
|--:|---|---|:--:|---|
| 1 | **A behavior *planner*, not a markup regexer** | `automarkup` maps words to tags. The 10× version scores each line for mood, dialog-act, gesture, gaze, screen icon and SFX from the recovered vocabularies, validates every asset reference before it ships, and previews the result on the 3D SIM — so authors *see* the performance before a child does. | **L** | 🔵 **open — spec'd.** Contract-level spec: [`backlog/expressiveness.md`](backlog/expressiveness.md) §2 (P0 floor → P1 planner → P2 model-assisted) |
| 2 | **Child safety as an enforced contract** | Build `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` from [`ai-seam.md`](ai-seam.md) §② as a real pre- and post-inference stage: a local classifier (no cloud), a parent-visible review queue, and a documented escalation path. Upstream has nothing at all; Fork A's keyword flags (§2.1) are a good floor and honest about being a review aid, not a filter — this runs on a child's device and deserves better than regexes. | **M** | 🟢 **shipped (PR #20)** — [`moxie_sdk/safety.py`](../../mqtt/moxie_sdk/safety.py): a local, dependency-free `RuleClassifier` over a data file (`safety_rules.json`) producing the contract's `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` on **both** sides of a turn, a `Redirect` instead of a refusal, a durable per-robot review queue (`safety_events` / `safety_counts`) with `GET`/`POST /safety` and the console's 🛡️ card ("I have seen this"), and redacted excerpts. It also gates telehealth operator lines (ADOPT #7). **Honest ceiling:** it is rules over normalized text with variant folding — not a model — so it catches the vocabulary it was given and nothing else; the "documented escalation path" this row asked for is a parent's review queue, not a policy |
| 3 | **Any brain, hot-swappable, per child** | OpenMoxie has one global vendor. We already have `MoxieApp` — make it a registry: per-robot and per-child app + persona binding, switchable live from the console, with the SIM as the safe rehearsal client. "Any AI wears the shell" becomes an operation, not an architecture diagram. | **M** | 🟢 **P0 SHIPPED 2026-09-03** — the registry is [`moxie_sdk/brains.py`](../../mqtt/moxie_sdk/brains.py) (a closed positive list; an unknown name is refused, never guessed), the selection is `brain` as an ordinary `defaults ⊕ fleet ⊕ per-robot` config key, and the swap is `MoxieRuntime.app_for` resolved once per turn — so two children on one appliance really are answered by two brains, live, with no restart. An explicit `MOXIE_APP` **pins** it (PR #77's rule). Ships with the 🧠 console card (a brain per robot or a house rule, and a row per robot saying which layer chose it). Behind [`test_brains.py`](../../sim/tests/test_brains.py) (82) + [`test_brain_runtime.py`](../../sim/tests/test_brain_runtime.py) (31) + [`test_brain_console.py`](../../sim/tests/test_brain_console.py) (13) and a 22-guard mutation run. **Still open:** the *persona* half of the binding — see [`backlog/brain-picker.md`](backlog/brain-picker.md) |
| 4 | **Memory as a product** | Beyond `complete_handler` writing a summary string: structured long-term memory (people, preferences, ongoing stories, goals) with summarization, decay, and a **parent-editable memory browser** including per-child deletion — because a memory a parent cannot read or erase is not acceptable on a child's device. | **L** | 🟢 **shipped 2026-09-02** — memory *is* structured (facts / preferences / open_threads / summaries, module-namespaced, bounded), written by `session.summarize()` at end-of-conversation, gated by `LoggingPolicy.NO_DATA`, and **readable, correctable and erasable by a parent**. All four of this row's asks now exist: **per-item provenance** — every item is a `{id, text, _provenance, use_count, pinned}` record, attributed at merge time (Fork A's `conversation_memory.py` instinct, taken to the line); **per-item deletion** — `MemoryStore.erase_item` behind `DELETE /memory?…&item=` → `DELETE /local/robots/{id}/memory/{ns}/{item}` → the card's ✕; **editing** — `edit_item` behind `POST /memory {"edit":…}` → the card's inline ✏️, which keeps the item's id, re-runs the safety classifier + no-verbatim check on the new wording, and pins the result; **decay** — items unused for `MOXIE_MEMORY_MAX_AGE_DAYS` (default 90, `0` = off) are pruned at merge, never pinned or undatable ones. The console card also shows `summarized_through` and states the `LoggingPolicy` when remembering is off (`normalize_memory`, [what-moxie-remembers.md](../guides/what-moxie-remembers.md)). Live-verified end to end: seeded facts read back with ids, "Puppy sleeps on **his** bed" corrected to "…my bed" and pinned, one item erased, the rest intact. **Not** a product, and the docs say so: decay is a use-clock (did this sentence appear in a rendered prompt?), so it cannot tell an important fact from a trivial one, and a wrong summary is still written in the first place — a parent correcting it is the mechanism, not the model |
| 5 | **Insights that mean something** | We already ingest `Packet` telemetry and gate it on `LoggingPolicy`. Turn it into a local parent console: sessions, activity mix, mood trend over time, time-of-day patterns, "what did we talk about this week" — all on-device, nothing uploaded. This is the plan's own next slice. | **M** | 🟡 **partial — the durable half SHIPPED (PR #55, 2026-09-02); the insight half is open.** Proof: `sim/tests/test_telemetry.py` (29) · `test_telemetry_runtime.py` (21) · `test_sil_durable_telemetry.py` (10). Telemetry is now **durable**: two per-robot collections ([`moxie_sdk/telemetry.py`](../../mqtt/moxie_sdk/telemetry.py)) — a 500-envelope ring (`telemetry_packets`) and 35 days of daily roll-ups (`telemetry_daily`) — written through `JsonStore`, hydrated on first touch, and gated by `LoggingPolicy` on the way to disk as well as on the way off the robot (`NO_DATA` writes nothing; `NO_MEDIA`, the default, withholds **every** `event_data` payload because the recovered proto types none of them). Proven live: three packets sent to one supervisor, read back through the next one's `GET /telemetry` with the robot not even reconnected. So the 📈 card now shows a real **week** — a zero-filled per-day bar row, the lifetime total beside the retained count, and the actual retention window stated — where it used to be an event log over one process's lifetime. **Still open, and it is the interesting half:** no *sessions*, no activity mix, no mood trend, no time-of-day pattern, no "what did we talk about this week". Those need a vocabulary this history does not have — `Packet.event_name` is a free string and our corpus recovers no module-scoped events ([`schedule.py::telemetry_signals`](../../mqtt/moxie_sdk/schedule.py)) — so the next slice is either deriving them from `mentor_behaviors` + conversation history, or *emitting* our own named events from the runtime and rolling those up. Contract: [config & telemetry §How this server persists telemetry](config-and-telemetry-contract.md#how-this-server-persists-telemetry-built-v1-2026-09-02) |
| 6 | **Sandboxed content extensions** | Their `METHOD` globals and conversation `code` fields are `exec()` with a 10-second timeout — powerful, and un-shareable. A capability-scoped module runtime (declared permissions, no filesystem/network by default, resource limits) makes community content packs safe to install. | **L** | 🟢 **SHIPPED — P0, 2026-09-03.** **Proof:** [`moxie_sdk/content/ext.py`](../../mqtt/moxie_sdk/content/ext.py) (~1 000 lines, pure stdlib) behind **150 tests** — [`sim/tests/test_ext_escapes.py`](../../sim/tests/test_ext_escapes.py) (113, X1–X12) · [`test_ext.py`](../../sim/tests/test_ext.py) (33 + 4 `xfail(strict)` for P1) — plus [`sim/tools/ext_mutation_check.py`](../../sim/tools/ext_mutation_check.py), which deletes each of **28 guards** in turn and requires its test to go red (28/28 caught). **What shipped:** a *declarative rule list over a total JSON-AST expression language* — 53 frozen operators, 10 statements, no `exec`, no `eval`, no parser, no loop, no user function, no recursion, and no name that resolves to a host object, because the fact base handed to the evaluator is a pre-built plain-JSON dict the host assembles. The evaluator's own module imports neither `time`, `random`, `os`, `datetime`, `secrets` nor `subprocess` (asserted over its own source with `ast`): the clock and the PRNG seed are **injected**, so a turn replays byte for byte. Every op is total — `/` by zero is the error *value*, a missing key is null, an out-of-range index is null — so there is no state in which the evaluator does not return. Capabilities are checked at load in **both** directions (declared == used), so a parent's grant list is provably equal to what the program can do; default-granted is exactly `{say, handled, session, child.nickname}`, and `act`/`subscribe`/`brain`/`schedule.request` validate as grammar and are refused at load because the `RemoteChatAction` wire is not plumbed (S5). `MOXIE_EXT_BUDGET_S` (0.25 s) is asserted **strictly less** than `MOXIE_BRAIN_BUDGET_S` at startup. On any breach the effect list is discarded whole and `ContentApp` proceeds exactly as before — a `global` falls through to the conversation, a `turn.before` is skipped and the model runs — **the child hears no error text** (upstream speaks `f"Script error: {e}"` at a seven-year-old); the parent gets one plain-language `ext_events` entry, and three breaches quarantine for the session. `explain()` renders each rule as one English sentence and `grant_list()` each capability from a fixed table, both pure functions of the AST, both in the pack review — beside a **capability-escalation** rule that defaults an item asking for more than the installed version un-ticked whatever its state. All six upstream hooks are hand-ported as the conformance golden set ([`ext_conformance.json`](../../sim/tests/data/ext_conformance.json)); G1 **ships as a real activity** in [`starter.json`](../../mqtt/content_modules/starter.json) and answers *"what time is it"* with no model call. `code` is still, and forever, never executed — `extension` is a different field, and §7.4 rules out compiling one into the other. Contract: [content-module-contract.md](content-module-contract.md#extensions-a-pack-that-can-do-something). **Not in P0, deliberately:** `act`, `subscribe`, `brain`, `schedule.request`, `turn.after`, `session.end`, the text surface, the JS evaluator, the console card, signatures |
| 7 | **A schedule that adapts** | Their `ransac_select` maximizes category variety at random. Ours should plan from `mentor_behaviors` + telemetry + parent preferences + time of day, and show the parent an explainable *"why this activity today"* line. Same interface, a real recommender behind it. | **M** | 🟢 **shipped (2026-09-02)** — `plan_inputs`/`plan_day` score every candidate on parent request → unfinished FTUE → coverage → recency → completion affinity → category variety → time-of-day fit, never plan into bedtime, and return a parent-readable *"why this activity today"* line per entry (`GET /schedule`, `robots/<id>/schedule_explain.json`). Still deterministic and byte-compatible on the wire. Honest limits: the recovered telemetry carries no module-scoped event, so finish/abandon comes from `mentor_behaviors`. **The console follow-up landed the same day (PR #46)** — the 📅 *Today's plan* card (`fleet.py::normalize_schedule_view` → `GET /local/robots/{id}/schedule`) shows a parent every entry with its *why* line, the bedtime window, pinned parent requests, and an honest "the robot has not pulled its day yet" state |
| 8 | **A voice you choose, with lips that match** | Ship Piper voices plus the `TTSMark[]` visemes [`ai-seam.md`](ai-seam.md) §③ specifies, so the SIM lip-syncs and telepresence sounds like the person driving it. A real robot keeps its own on-device voice — this is for every *other* body Moxie can wear. | **M** | 🟡 **partial** — Piper voices are real and the browser SIM plays the server's `CloudTTSResponse` (PR #11), proven end to end with real speech (PR #12). **Choosing one is now a one-line switch (2026-09-02):** the LiteLLM gateway serves `piper-amy`/`piper-ryan` over the chat key, `MOXIE_VOICE_MODEL` picks the voice, the WAV's own header carries the rate so a voice swap needs no config change, and a gateway failure downgrades to Piper/tone instead of silence — live-proven by transcribing the audio back at overlap 1.00 ([guide](../guides/litellm-tts-setup.md)). **`TTSMark[]` visemes are still empty** — `marks` is plumbed through `moxie_sdk/tts.py` and never populated, so nothing lip-syncs. **Choosing a voice from the console** (the 🎚️ picker, [`backlog/voice-picker.md`](backlog/voice-picker.md)) is 🟢 **SHIPPED — P0, PR #48 (2026-09-02)**, covering *both* dropdowns (speech **and** listening): `sim/tests/test_voice_settings.py` (76) · `test_voice_runtime.py` (34) · `test_live_voice_picker.py`. ⚠️ *This was the second row a build agent was briefed on as open work on 2026-09-03; it was already merged.* **Corrected 2026-09-03 by PR #77:** the picker shipped sitting *above* every environment value except `off`, so on a box whose operator had written `MOXIE_TTS=piper` a console pick of `gateway:piper-ryan` silently moved the house onto the gateway. An explicit `MOXIE_TTS`/`MOXIE_STT` now **pins the engine** — one rule (`voice_settings.pin_for_env` / `honours_pin` / `filter_available` / `pin_note`) enforced in three places, with the startup line refusing to print `chosen` for a pick the pin ignores. That is the owner's *"local engines stay first-class"* made enforceable rather than merely documented. `MOXIE_VOICE_MODEL` remains the env default under an unpinned picker choice |
| 9 | **Vision events in the turn loop** | The robot already emits `eb-found-face`, `eb-lost-target`, `eb-qr-event`, `eb-dr-event`, `eb-br-event` and accepts `eb_custom_face_search` ([`vision.md`](vision.md)). OpenMoxie still does not subscribe to them. **We now do** ([`vision.md`](vision.md) §7): the runtime sends `EventSubscription.active[]`, folds the events into a per-robot presence state with hysteresis, carries a snapshot into the prompt (`Turn.presence`), and lets a child who walks back in after ≥ `MOXIE_GREET_AFTER_S` earn one unprompted hello — on the arrival event's own `event_id`, since an unsolicited publish is not established as legal. **Honest ceiling:** no physical robot has ever sent us one of these events, and `eb_custom_face_search` is catalogued but not yet driven. | **M** | 🟡 **built, unproven on hardware** |
| 10 | **One appliance, one identity, one command** | OpenMoxie is a robot cloud with a config UI. Ours should be a single stack where the **parent app**, broker, supervisor, brain, STT and TTS share one device/child registry, publish as multi-arch images, and come up with a guided first run — with the SIM as a first-class client so the whole appliance is testable before a robot is ever plugged in. | **L** | 🟡 **partial** (PR #8 + `feat/published-images`) — one command brings the stack up and multi-arch images publish on every `v*` tag; one shared device/child registry and a guided first run are not built. Re-checked 2026-09-02: the *console* is now the single pane it was meant to be (permits, ⚙️ settings with a fleet layer, 🎨 look, 🎭 Be Moxie, 📅 plan, 🧠 memory, 🛡️ safety, 📈 insights — and 🎚️ voice in PR #48), so what is left is the identity half: the parent-app server keeps real child records (`POST /api/children`), while the supervisor builds its one `ChildProfile` from an environment variable (`mqtt/run.py`:35, `ChildProfile(nickname=config.CHILD_NICKNAME)` — re-verified 2026-09-03, the line moved and the gap did not). Two registries, reconciled by an env var — that is the gap this row names. **A fourth surface arrived since:** the hosted static Sim now runs its own edge tier (`functions/api/{chat,speech,transcribe,health}.js` + `_lib/`, 3 680 lines), which is deliberately *stateless and childless* — demo mode, no registry at all — so it widens the row's question without answering it |

---

## 4.3 Backlog — the build briefs

A ranked line is a *decision*; a build agent needs a **brief**. Items whose next step is big enough to
need one get a page under [`backlog/`](backlog/README.md), written so an agent can execute it as-is:
the seam it plugs into, the recovered vocabularies it may draw from, the design, the tests, the
acceptance criteria, the effort and the risks.

| Brief | Covers | State — live, 2026-09-03 |
|---|---|---|
| [`backlog/expressiveness.md`](backlog/expressiveness.md) | **ADOPT #3** (the markup floor) §1 · **BEYOND #1** (the behavior planner) §2 | §1 🟢 **shipped 2026-09-02** · §2 🔵 **build-ready** |
| [`backlog/telehealth.md`](backlog/telehealth.md) | **ADOPT #7** (puppet / telehealth) — the `commands/telehealth` command path and the 🎭 "Be Moxie" console panel | 🟢 **shipped 2026-09-02 (PR #43)** — `test_telehealth*.py`, 110 tests |
| [`backlog/voice-picker.md`](backlog/voice-picker.md) | **BEYOND #8** (the console half) — 🎚️ Speech + Listening dropdowns fed by live gateway discovery + the installed local engines | 🟢 **shipped 2026-09-02 (PR #48)**, corrected 2026-09-03 (PR #77 — an explicit engine pins) — `test_voice_settings.py` (76) · `test_voice_runtime.py` (34) |
| [`backlog/content-packs.md`](backlog/content-packs.md) | **ADOPT #5** (content packs) — a versioned, digest-checked pack file; export from a field allowlist; import-with-review with `source_version` **and** local-edit detection; P0 headless, P1 the 📦 console card | 🟢 **shipped 2026-09-02 (PR #51), both P0 and P1**; hardened 2026-09-03 (PR #78) — 160 tests. *This line read "ready to build" until 2026-09-03* |
| [`backlog/live-sim-demo.md`](backlog/live-sim-demo.md) | 🌐 **the headline goal** — the hosted Sim on a static edge: same-origin Pages Functions for brain · voice · ears behind hard caps, degrading to the pre-cached scripted Moxie | 🟡 **P0-a + P0-b shipped 2026-09-02 (PR #54, #61); the ears + the fallback voice 2026-09-03 (PR #66, #69).** P1's remainder is 🔵 build-ready — see §4.4 #1. **This brief is the best-maintained page in the folder** and needs no refresh: it self-updates, and its §10 ledger settled three deploy-only unknowns against a real Pages preview on 2026-09-03 |
| [`backlog/sandboxed-extensions.md`](backlog/sandboxed-extensions.md) | **BEYOND #6** (sandboxed content extensions) — a declarative rule list over a total JSON-AST expression language; no `exec`, no parser, no loops, no reachable host object, behind a capability set the parent reads in plain English | 🟢 **P0 SHIPPED 2026-09-03** (`ext.py` + 150 tests + a 28-guard mutation run). P1 (`act`/`subscribe`/`brain`, the text surface, the JS evaluator in `workerd`, the console card) and P2 (a Wasm runtime, signatures) remain |
| [`backlog/security-broker-auth.md`](backlog/security-broker-auth.md) | **§3.1 Robot identity / JWT** — phased P0 (ACL + supervisor credential, no robot change) → P1 (device credentials the broker verifies) → P2 (a spoofed `d_<uuid>` refused at CONNECT) | 🟢 **P0 shipped 2026-09-02 (PR #44)** — `test_broker_acl.py` (30) + `run_acl_proof.sh` (18 delivery checks against a real mosquitto). **P1/P2 🟠 needs-a-spec-decision, not build-ready**: both are blocked on the unanswered assumptions A1–A4 |

**Every brief in [`backlog/`](backlog/README.md) is listed here.** If a brief exists and this table does
not name it, this table is wrong — that was the case for `live-sim-demo.md` and `sandboxed-extensions.md`
until 2026-09-03.

---

## 4.4 The open backlog, re-ranked — 2026-09-03

**Nine of the previous ranking's ten entries are gone**, which is why it had to be rewritten rather than
edited: #1 content packs (PR #51), #2 durable telemetry (PR #55), #3's security half (PR #56/#78), the
`wakeup` half of #9 (PR #55), and — on the ranking that preceded it — telehealth, the face catalog, the
schedule card, memory browse/erase and the voice picker. The old table's #1 and the old §4.2's voice row
were still marked as work to do on 2026-09-03, and **two build agents each lost a full run to that**.
This table is the one place to look for *"what should I build next"*; §3 is a museum.

**Every row carries a readiness verdict**, because "ranked #2" and "an agent can start on it this
morning" are different claims and conflating them is what wasted the runs:

- 🟢 **build-ready** — a brief exists (or the design is settled in the row) and an agent can open a
  worktree and start. No decision is owed first.
- 🟠 **needs-a-spec** — the *what* is agreed and the *how* is not. Sending a build agent produces a
  design argument, not a merge. These want a research/spec slice first.
- ⛔ **blocked** — not on effort. Named blocker, and no ranking moves it.

| # | Open item | Outcome | Readiness | Why it ranks here (one line) |
|--:|---|:--:|:--:|---|
| — | **The unreachable child voice** (live-Sim assumption 22) | ① | 🔵 *in flight — do not assign* | `bridge.js::handleUserTurn` never speaks, so `deploy-cloudflare.md`:19's claim that the child is audible is false. An agent owns it right now; it is listed only so nobody starts it twice |
| 1 | **Live-Sim P1** — exact counters, Turnstile, the TTS cache | ① | 🟢 **build-ready** | The headline goal is *a stranger opens the production domain and Moxie answers*, and P0 shipped with its own ceiling written down: the rate counter is an in-isolate map plus the Cache API, **per-colo and per-isolate**, so it is not a ceiling at all ([`live-sim-demo.md`](backlog/live-sim-demo.md) §4.6). Everything else on this list protects a robot in someone's house; this protects the one surface the public can reach and the one budget a stranger can spend. Brief: [`live-sim-demo.md`](backlog/live-sim-demo.md) §9 P1 — but note assumption 13 (**is KV or a Durable Object even available on this plan?**) is unverified, and the answer changes the design, so *check the dashboard before writing code* |
| 2 | **Sandboxed content extensions P0** (BEYOND #6, §3-onward) | ③ | 🟢 **SHIPPED 2026-09-03** | Done. A pack can now check the clock, count and remember a score behind a capability set a parent reads in English, and the worst a malicious one can do is stop working. `ext.py` + `test_ext_escapes.py` (X1–X12) + `test_ext.py` (T1–T18) + a 28-guard mutation run, all six upstream hooks hand-ported as the golden set, and G1 shipped as a real activity. **The next slice here is P1:** the `RemoteChatAction` plumbing that unlocks `act`/`subscribe` (which turns four `xfail(strict)` conformance rows green), the `brain` capability, and the JS evaluator run against the same conformance file in `workerd`. Brief: [`sandboxed-extensions.md`](backlog/sandboxed-extensions.md) |
| 3 | **Production hardening for a robot that stays connected** (Fork A's list) | ① | 🟠 **needs-a-spec** | `moxie_runtime.py`:458 is still a plain blocking `client.connect(host, port, 30)` with no `connect_async` and no `reconnect_delay_set`; `JsonStore`'s lock is in-process only and now carries **thirteen** collections. Every one is a bug we have not hit because no real robot has been on our broker for a week — and outcome ① is measured live. It is 🟠 rather than 🟢 because the cross-process story is a **design** decision (WAL-backed SQLite? a single-writer process? file locks?) that ADOPT #8 has deferred three times; deciding it in a PR review is how it gets decided badly |
| 4 | **The behavior planner** (BEYOND #1) | ③ | 🟢 **build-ready** | Promoted, on the arithmetic rather than on enthusiasm: the four items that used to outrank it have shipped. It is still an **L** that improves something already good rather than filling a hole — but it is now the largest *unfilled* claim in [`vision.md`](vision.md), the markup floor beneath it is deterministic and golden-tested, and the SIM can rehearse it before a child sees it. Brief: [`expressiveness.md`](backlog/expressiveness.md) §2 |
| 5 | **Broker auth P1/P2** — device credentials, then a refused spoof | ①② | ⛔ **blocked on A1–A4** | P0 confines a client to its own subtree, so this is no longer *"anyone can read another child's `child_pii`"* — it is *"the broker still cannot tell **which** robot a client id belongs to"*, which is the difference between containment and identity. It drops below the planner **only** because it cannot start: [`security-broker-auth.md`](backlog/security-broker-auth.md) §0.4's A1–A4 are unanswered and every one of them needs a **physical robot** to answer. Assigning it produces a rewritten brief, not a merge |
| 6 | **Content authoring, not just content distribution** (§3.3's authoring-UI row) | ②③ | 🟠 **needs-a-spec** | Packs made content *shippable*; nothing made it *writable*. A parent can install a stranger's conversation and diff it against their own, and still cannot compose one without editing JSON — and upstream's browser chat harness (`/hive/interact`) remains a genuinely better authoring loop than ours. New to this ranking, because packs shipping is what exposed it. No brief |
| 7 | **Any brain, hot-swappable, per child** (BEYOND #3) | ③ | 🟢 **P0 BUILT 2026-09-03** | The literal ghost-in-the-shell claim, and it was exactly what the audit said it was — a registry plus a selection, not an architecture. [`moxie_sdk/brains.py`](../../mqtt/moxie_sdk/brains.py) is the closed positive list; `brain` rides the ONE existing config layering (ADOPT #6) rather than a second store; `MoxieRuntime.app_for` resolves once per turn, so a swap lands on the next turn and an in-flight turn keeps its brain (PR #48's `voice_update` rule); an explicit `MOXIE_APP` pins the appliance (PR #77's rule, with the raw-environment correction its lesson demanded). and the 🧠 console card ships with it. Named test: [`test_brain_runtime.py`](../../sim/tests/test_brain_runtime.py)`::test_two_children_on_one_appliance_get_two_different_brains`. **P1 remainder:** the *persona* half of the binding, and per-child keys/cost accounting — all of which need a new secret |
| 8 | **`TTSMark[]` visemes** (the rest of BEYOND #8) | ①③ | 🟠 **needs-a-spec** | Re-verified 2026-09-03: `marks` is plumbed through `moxie_sdk/tts.py`:370-402 and populated by **nothing but tests**, so the SIM's mouth is animated by amplitude rather than phonemes. 🟠 because whether Piper can emit the alignment we need is an unanswered question, not an implementation detail — and the hosted path now encodes its own WAV, which changes where the marks would come from |
| 9 | **Printable launch-card QRs** (`GO<launch:MOD>`) | ② | 🟢 **build-ready** | The cheapest delight left on the ADOPT list and now the *whole* of the old #9 — its `wakeup` twin shipped. The action-tag parser already understands `<launch:MOD[:CID]>`; what is missing is a sheet a parent prints. An **S**, and it ranks last among build-ready items only because nothing depends on it |
| 10 | **One identity, one guided first run** (BEYOND #10) | ① | 🟠 **needs-a-spec** | The parent app keeps real child records (`POST /api/children`) while the supervisor builds one `ChildProfile` from an env var (`mqtt/run.py`:35) — and the hosted edge tier is now a third, deliberately childless surface. Reconciling three registries is a data-model decision before it is code |

**Not ranked, deliberately — and the list grew.** BEYOND #9 (vision events), ADOPT #9's two flagged
assumptions, the `wakeup` acknowledgement, broker auth A1–A4, every claim in the OTA row, and — new on
2026-09-03 — **whether an empty `license_values: []` satisfies the robot or crash-loops its Wi-Fi App**
([§2.4](#24-upstream-re-check-2026-09-03-and-the-one-thing-that-was-new), from an unconfirmed field
report on upstream's tracker) are **blocked on a physical robot**, not on effort. They are built or specified, unproven, and no amount of
ranking moves them. *A real Moxie on our broker for an hour would settle more of this page than a week of
building* — that sentence has been true at every refresh, and it is the single highest-value thing an
owner could do for this project.

**Verified nothing, twice.** There is no upstream item on this list because there is nothing new upstream:
all three source repos were re-read at their live remote heads on 2026-09-03 (see the header) and every
branch, tag and HEAD is unmoved since 2026-09-02. What remains is ours to build.

---

## 5. The honest ledger

### Where OpenMoxie (and its forks) are genuinely ahead of us

Not "different" — **ahead**. Listed plainly so the status tables stay honest.

> **Frozen at the baseline, read with §4.1/§4.2/§4.4 — updated 2026-09-03.** These ten were true on
> 2026-09-02 morning at commit `fa70309` and the numbered text below is **not** rewritten as we ship —
> that is what the Status columns are for. Only this note moves.
>
> **Closed since:** **1** (schedule + `mentor_behaviors`, PR #5/#7), **2** (the markup floor), **4**
> (published images — and verified in the registry 2026-09-03, §3.0), most of **5** (face customizer
> PR #36/#47, puppet mode PR #43, the mission/plan view PR #46), the safety-flags half of **8** (PR #20),
> and **10** (a brain slower than the robot — PR #14's filler-then-real-line, PR #17's streamed chunks).
>
> **Closed on 2026-09-02, corrected here on 2026-09-03: item 3.** *"Content you can author and share"* was
> still listed as unmoved, and pointed at ADOPT #5 as *"ranked #1 in §4.4"* — but ADOPT #5 had shipped
> the day before (PR #51). **Half of item 3 is genuinely closed:** a parent installs, reviews, diffs,
> version-upgrades and exports content, which is more than upstream's flow does. **The other half is not,
> and it is now §4.4 #6:** we made content *shareable* without making it *writable*, so upstream's
> DB-backed editing and its `/hive/interact` browser chat harness are still theirs alone. Splitting this
> item rather than ticking it is the whole lesson of this refresh.
>
> **Still true and unmoved:** the authoring half of **3** (above), **5**'s printable launch cards and
> browser harness, **6** (their `doc/` folder), **7** (it shipped), the transcripts + speaker-scoped
> memory half of **8**, and **9** (production hardening — §4.4 #3).

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
7. **Tests and CI.** **105** test files (counted on `origin/dev`, 2026-09-03 — it was 37 at the
   baseline) and a three-tier pipeline. Upstream has a 3-line Django stub and no
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
