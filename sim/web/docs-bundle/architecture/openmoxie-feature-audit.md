# OpenMoxie feature audit

A feature-by-feature comparison of [OpenMoxie](https://github.com/jbeghtol/openmoxie) (upstream
`v0.8`, MIT) and its two active forks with this project. Each item is marked **HAVE** (ours is equal or
better), **ADOPT** (port the idea and credit it), or **BEYOND** (theirs is a floor; build something
bigger). Every OpenMoxie claim cites a file in their repo; the status columns were checked against our
code as it is now.

Companion pages: [`../community-research.md`](../community-research.md) places OpenMoxie in the wider
landscape; [`backlog/community-signals.md`](backlog/community-signals.md) collects what real owners
report; [`../../ATTRIBUTION.md`](../../ATTRIBUTION.md) credits each idea we took.

## Credit

**[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie)** is MIT, © Justin Beghtol, a former
Embodied engineer. It was released as the CEO-sanctioned open-source off-ramp when Embodied shut down,
and it is the origin of most of what the community knows about Moxie's cloud: the migration-QR
relocation, the mosquitto TLS setup, the `RemoteChat` volley model, the real protobuf schemas and the
`automarkup` text-to-behavior engine. **If you own a Moxie and want it talking tonight, install
OpenMoxie.**

| Fork | Direction |
|---|---|
| [Noonster77/openmoxie](https://github.com/Noonster77/openmoxie) ("Family Edition", active) | Local AI (LM Studio + faster-whisper), a family-facing UI, speaker-scoped memory, transcripts, safety flags with parent review, 70 tests |
| [vapors/openmoxie-ollama](https://github.com/vapors/openmoxie-ollama) (stale since 2025-08) | Ollama and xAI providers, a standalone faster-whisper STT service, Docker Hub CI. Explicitly adult, unfiltered content |

Both forks inherit MIT. This is not a competitive teardown: it lists what a mature project already
solved, so we do not re-solve it badly, and where their design stops.

**Method.** Upstream was read at commit `c8c2d38` (`site/VERSION` = `v0.8`), Noonster77 at `a97c85c`,
vapors at `a80a81e`; each fork was diffed file by file against upstream. Every `.py` under
`site/hive/` (except generated protos and the `automarkup` internals), every template, every `doc/`
page, the seed data and the four shareable packs were read. Upstream has not moved since those
commits. OpenMoxie is open source and fine to study; the vendor Android app stays off-limits and is not
referenced here.

Upstream is about **5,900 lines of Python** — the minimum that makes a robot work:

| Subsystem | Lines |
|---|--:|
| `site/hive/automarkup/` (text → behavior) | 2,157 |
| `site/hive/mqtt/` (robot cloud, excluding protos) | 1,889 |
| `site/hive/` views, models, import/export | 633 |
| `site/hive/templates/hive/` (12 pages) | 797 |

---

## 1. Feature inventory — upstream OpenMoxie

One Django process does everything: the web UI, the SQLite database, and (on a daemon thread started
by an overridden `runserver`) the MQTT supervisor that talks to the robot.

### 1.1 Web UI

A Django app at `/hive/` (24 routes in `site/hive/urls.py`) plus the raw Django admin. There is no
parent app.

| Page | What it does |
|---|---|
| Setup (`SetupView`) | First-run wizard: OpenAI key, optional Google service-account JSON, hostname, "allow unverified bots", creates the admin user |
| Dashboard | Devices with online state, schedules, conversations, content import/export |
| Endpoint QR (`endpoint_qr`) | The migration QR that repoints a robot at this server |
| Wi-Fi QR (`WifiQREditView`) | Wi-Fi QR from SSID, password, band, hidden flag |
| Robot page (`MoxieView`) | Name, pairing status, nickname, schedule, volume, brightness; read-only firmware, battery, mode |
| Face editor (`MoxieFaceView`) | Layered face customizer plus a "new child ID" button that busts the robot's texture cache |
| Missions (`MoxieMissionsView`) | Mark Daily-Mission sets complete, forget or reset (`MentorBehavior` rows) |
| Puppet (`MoxiePuppetView`) | Type a line, pick mood and intensity, Moxie says it; interrupt; live state |
| Interact (`InteractionView`) | Chat with any conversation module in the browser, no robot needed |
| Export / import | Download a content pack; upload one and review `New / Upgrade from vN / Replace vN` per record |
| Wake (`moxie_wake`) | Send the MQTT `wakeup` command |

### 1.2 Robot onboarding — the QR codes

All in `site/hive/mqtt/moxie_server.py`:

- **Migration QR** — `get_endpoint_qr_data()` builds a `ServiceConfiguration2` protobuf (4 of its 14
  fields), base64s it, and wraps it as `{"debug": {"command": "om", "param": "<b64>"}}`. The robot scans
  it and permanently relocates to your broker.
- **Wi-Fi QR** — `get_wifi_qr_data()` serializes `StartPairingQR` with a literal `PA` prefix.
- **Launch cards** — `site/data/qr/extract.py` prints 24 `GO<launch:MODULE_ID>` PNGs a child can show
  Moxie to start an activity.
- **OTA lever** — `doc/RemoteModuleAPI.md` describes (as notes, not code) upgrading 801 → 803 robots by
  adding `webservice_root` to the endpoint QR and serving a static OTA URL file.
- **Robot identity** — `robot_credentials.py` can pull `uuid.txt` and `RS256.key` off a robot over ADB
  and mint JWTs, but the broker allows anonymous clients and verifies nothing.

### 1.3 Configuration

`site/hive/mqtt/robot_data.py` deep-merges a fleet-wide `HiveConfiguration.common_config` /
`common_settings` with each `MoxieDevice.robot_config` / `robot_settings`. Config is pushed on connect
and re-pushed on edit (`handle_config_updated` → `config_update_live`). `doc/MoxieOverview.md` documents
every config key and settings prop with warnings — **the best config reference for this robot that
exists.**

### 1.4 Schedules

`site/hive/mqtt/scheduler.py::expand_schedule()` expands a `generate` block into a day plan:
`ransac_select()` tries 20 random orderings and keeps the one with the fewest same-category neighbors,
`distribute_elements()` interleaves chats, and `ftue_remove()` drops first-time modules once completed.
The robot asks for `schedule`, `mentor_behaviors` and `license` per session; `on_device_event` answers
all three.

### 1.5 Content

- **Conversations** (`SinglePromptChat`): module and content ids, opener, prompt rendered as a Django
  template over `volley` and `session`, history and token limits, and a free-form Python `code` field
  whose `pre_process` / `post_process` / `complete_handler` / `notify_handler` hooks are `exec`'d
  (`conversations.py`). `content_modules/MemoryChat.json` uses `complete_handler` to summarize a session
  into `persist_data` — long-term memory in about 60 lines.
- **Response tags**: the model writes `<exit>`, `<sleep>`, `<launch:MOD[:CID]>` or
  `<launch_if_confirmed:…>` inline; `Volley.ingest_action_tags()` turns them into robot actions and
  strips them (`volley.py`).
- **Globals** (`global_responses.py`): regexes over the child's speech, matched before the module, with
  actions `RESPONSE`, `LAUNCH`, `CONFIRM_LAUNCH` and `METHOD` (stored Python, `exec`'d with a 10 s
  timeout).
- **Packs**: `content_modules/MemoryChat.json`, `MoxieGo.json`, `MoxieTime.json`, `MoxieTimers.json`,
  each carrying a `source_version` so `init_data` upgrades only when newer.

### 1.6 `automarkup`

`site/hive/automarkup/` (2,157 lines, rule- and table-driven) turns a plain sentence into Moxie's markup:
voice, prosody, pauses, mood and gesture marks, with span-conflict resolution. Every AI reply without
markup goes through it (`moxie_remote_chat.py`). **This is why an OpenMoxie robot feels alive.**
`doc/AssetBundleMasterManifest.csv` lists every asset in the robot's bundle repository.

### 1.7 AI

| Seam | Upstream |
|---|---|
| LLM | `ai_factory.py`, 14 lines: OpenAI only, no `base_url` |
| Speech-to-text | `zmq_stt_handler.py`: buffers the robot's audio frames until end of speech, calls OpenAI `whisper-1`, replies with word timestamps |
| Text-to-speech | None needed: the robot speaks on-device from markup |
| Safety | None |

### 1.8 Telemetry, storage, deployment, quality

- **Telemetry:** a `MoxieLogs` model that is never written; the last `/state` is shown read-only;
  `MentorBehavior` stores activity history to feed back to the robot. No insights, no transcripts.
- **Multi-robot:** yes — per-robot rows, schedule, config, data and history; one chat session per device.
- **Storage:** Django ORM on SQLite, 8 models, 16 migrations.
- **Deployment:** two prebuilt multi-arch Docker Hub images (broker with baked-in keys, and the Django
  server); install is "download one compose file and run it".
- **Tests and CI:** none (`site/hive/tests.py` is the Django stub).
- **Docs:** five dense `doc/*.md` pages. Excellent.
- **Security:** `DEBUG = True`, `ALLOWED_HOSTS = ['*']`, a committed `SECRET_KEY`, anonymous broker,
  plaintext API keys. Defensible on a LAN, and documented as such.

---

## 2. The active forks

### 2.1 Noonster77/openmoxie — "Family Edition"

The closest existing project to ours, and ahead of us in places.

| Area | What it does | Where |
|---|---|---|
| Provider abstraction | OpenAI, OpenRouter, LM Studio or any OpenAI-compatible endpoint, set in the UI, with a connection test | `site/hive/mqtt/ai_factory.py`, `views.py::test_ai_connection` |
| LM Studio | Native API so reasoning can be switched off; retries once when a reasoning model returns nothing | `ai_factory.py` |
| Local STT | faster-whisper in-process, model selectable | `zmq_stt_handler.py` |
| Speaker-scoped memory | Per-speaker profiles, provenance on every item, injected only at session start; unattributed memory is quarantined | `conversation_memory.py` |
| Safety and parent review | Five regex categories checked before inference; flags a parent acknowledges | `conversation_log.py`, `templates/hive/transcripts.html` |
| Transcripts | DB row plus per-day text file; parent deletion really deletes | `conversation_log.py`, `views.py::transcript_manage` |
| Slow-brain filler | Long inference runs in the background while Moxie speaks trivia or jokes, because the robot re-prompts after about 20 s | `conversations.py::ReasoningChatSession` |
| Homework mode | Spoken arithmetic answered by a whitelisted-AST evaluator, no model call | `conversations.py::HomeworkChatSession` |
| Command reliability | UI commands queued into the next robot request, confirmed against state, replayed after reconnect | `moxie_remote_chat.py`, `moxie_server.py` |
| Hardening | SQLite WAL, write lock with backoff, `connect_async` + `reconnect_delay_set` | `apps.py`, `util.py`, `moxie_server.py` |
| Tests | 70 test methods on real regressions | `site/hive/tests.py` |

Caveats: still no authentication (anyone on the network can read or delete a child's transcripts), and
shipped prompts and migrations hard-code the author's family names.

### 2.2 vapors/openmoxie-ollama

A per-conversation `LLMProvider` interface (OpenAI, xAI, Ollama), a standalone FastAPI faster-whisper
service with `POST /control/reload` to hot-swap the model, and a Docker Hub release workflow
(`site/services/stt/stt_service.py`, `.github/workflows/docker.yml`). It is also a cautionary tale: it
edits an upstream migration in place, `summarize()` is broken for every vendor, its release compose
file does not parse, it has committed a runtime log of the author's household conversations, and its
seed content tells the model it may use profanity — on a robot built for children. That last point is
why content packs need an enforced safety layer (BEYOND #2).

### 2.3 What the forks tell us

Both forks independently added the two things upstream lacks and we already had — a `base_url` seam and
local speech-to-text — which confirms the [AI seam](ai-seam.md) design. Noonster77 adds the
family-facing layer on top, and discovered the robot's ~20 s re-prompt window: a slower brain must speak
a filler line first.

**Field reports worth knowing.** Two independent owner reports suggest an empty `license_values: []`
reply may crash-loop the robot's Wi-Fi app; details and our recovered `LicenseID` enum are in
[`backlog/community-signals.md`](backlog/community-signals.md). Upstream PR
[#59](https://github.com/jbeghtol/openmoxie/pull/59) shows a sleeping robot drops its event
subscriptions; our wake handler forgets its subscription latch so the next reply re-sends them
(`mqtt/supervisor/moxie_runtime/fleet.py`). No real robot has confirmed it end to end.

---

## 3. Scorecard

Status is today's code. Items marked ADOPT or BEYOND that we have since built say **shipped**.

### 3.1 Onboarding and transport

| Feature | OpenMoxie | Us | Verdict |
|---|---|---|---|
| Endpoint / migration QR | 4 of 14 `ServiceConfiguration2` fields | All 14 decoded; `tools/pairing/moxie_endpoint_qr.py` and a console route | HAVE |
| Wi-Fi QR | `get_wifi_qr_data()` | `tools/pairing/moxie_qr.py`, verified on a real robot; browser and Python byte-identical (`sim/test_qr.mjs`) | HAVE |
| Launch-card QRs | `site/data/qr/extract.py` | Shipped: decoder with a closed allowlist (`mqtt/moxie_sdk/launch_cards.py`) and a printable sheet (`launch_sheet.py`). No robot has scanned one | ADOPT, shipped |
| Broker and TLS | Keys baked into the published image | Per-appliance CA (`mqtt/broker/gen-certs.sh`) | HAVE |
| Robot identity | JWT minting, never verified | Broker ACL confines each client to its own topics; device credentials blocked on a real robot ([brief](backlog/security-broker-auth.md)) | ADOPT, partial |
| Device allowlist | `MoxieDevice.permit`, stored but not enforced | Enforced, closed by default, with a pending state and a console card (`test_device_permits.py`) | HAVE |
| OTA push | Notes only | Specified, deliberately not built ([brief](backlog/ota-push.md)) | not planned |

### 3.2 The turn

| Feature | OpenMoxie | Us | Verdict |
|---|---|---|---|
| `RemoteChat` response | Text, markup, actions; result always 0 | Full `ResultCode` set including `ERROR_OFFLINE`, scored output, actions (`moxie_sdk/types.py`, `wire.py`) | HAVE |
| LLM provider | OpenAI only | Any OpenAI-compatible endpoint (`moxie_sdk/chat.py`), brain chosen per child (`brains.py`) | HAVE |
| History | In memory | Durable per-robot, trimmed | HAVE |
| Summaries → long-term memory | `MemoryChat.json` | Structured memory a parent can read, edit and erase | BEYOND, shipped |
| Prompt templating | Django templates | Jinja2 (`content/render.py`), same variables | HAVE |
| Response tags | `ingest_action_tags()` | `moxie_sdk/actions.py`, per streamed chunk | ADOPT, shipped |
| `automarkup` | 2,157-line engine | Clean-room deterministic floor (`automarkup.py`) plus a behavior planner (`performance.py`) | ADOPT + BEYOND, shipped |
| Puppet mode | Full page | `moxie_sdk/telehealth.py` and the "Be Moxie" console card | ADOPT, shipped |
| Safety | None (Noonster77: regex + review) | Rule classifier before and after inference, redirect not refusal, parent review queue (`safety.py`) | BEYOND, shipped |
| Streaming | None | Sentence chunks; filler line for slow brains (`filler.py`). No barge-in | HAVE |

### 3.3 Content and the session

| Feature | OpenMoxie | Us | Verdict |
|---|---|---|---|
| Content model | DB rows | JSON modules (`mqtt/content_modules/`) | HAVE |
| Authoring UI | Django admin + interact harness | Console editor, plus the 💬 Try it card: chat with any installed module, free chat or any brain the appliance offers, through the robot's own brain and staging, with no robot and nothing published ([brief](backlog/content-authoring.md) §5.3); an unsaved draft cannot be tried yet | ADOPT, shipped |
| Packs with review + `source_version` | Two-step import | Versioned, digest-checked packs that also detect local edits (`content/packs/`) | ADOPT, shipped |
| Globals | Four action types | Regex globals with handlers | HAVE |
| Stored Python (`METHOD`, `code`) | `exec` with a timeout | Sandboxed declarative rules, no `exec` (`content/ext/`) | BEYOND, shipped |
| Schedule serving | Randomized variety | Deterministic recommender with a "why today" line (`moxie_sdk/schedule/`) | BEYOND, shipped |
| `mentor_behaviors` | Stored and served | Stored and served, durable | ADOPT, shipped |
| `license` query | Shares a Google key verbatim | Returns an empty value (local-first); see field reports in §2.3 | HAVE |
| Execution actions | Sent on the wire | Sent on the wire (`wire.encode_action`) | ADOPT, shipped |

### 3.4 Fleet, config and console

| Feature | OpenMoxie | Us | Verdict |
|---|---|---|---|
| Config model | Fleet ⊕ per-robot merge | `cloud_config.merge_config_layers` | ADOPT, shipped |
| Config editing | Robot page | Validated console form, fleet or per-robot | HAVE |
| `/state` ingest | Read-only | Live fleet card | HAVE |
| Face customization | Layered editor, ~60 assets | 72 options, deterministic cache-buster (`faces.py`, `test_faces.py`) | ADOPT, shipped |
| Missions editor | Complete / forget / reset | History stored; no editor | ADOPT, open |
| Wake command | `send_wakeup_to_bot` | Published on `commands/wakeup`; no acknowledgement exists in the protocol | ADOPT, shipped |
| Telemetry / insights | Never written | Durable events and daily counts in the console; sessions and trends not built | BEYOND, partial |
| Multi-robot | Per-robot everything | Per-robot runtime and fleet view; one child profile from config | ADOPT, partial |
| Parent app | None | Full clean-room REST server and phone web app | HAVE |
| Hardware-free testing | Interact text box | 3D simulator on the real protocol, and the console's 💬 Try it | HAVE |

### 3.5 Engineering

| Feature | OpenMoxie | Us | Verdict |
|---|---|---|---|
| Storage | SQLite ORM | Atomic JSON files with cross-process locks (`moxie_sdk/store.py`) | ADOPT, partial |
| Images and one-file install | Docker Hub | Multi-arch GHCR images and `docker-compose.images.yml` | ADOPT, shipped |
| Tests and CI | None | Large suite, three CI tiers | HAVE |
| Docs | Five dense pages | Contracts, guides, reverse-engineering study, docs explorer | HAVE |
| Security | Debug on, committed secret, anonymous broker | No committed secrets, per-appliance CA, validated edits, broker ACL | HAVE |
| Reconnection | Blocking connect (Noonster77 fixed it) | `connect_async` + `reconnect_delay_set` | HAVE |

---

## 4. The ranked backlog

Effort: **S** about a day, **M** a few days, **L** a milestone.

### 4.1 Top 10 ADOPT

| # | Adopt | Their file | Effort | Status |
|--:|---|---|:--:|---|
| 1 | Schedule serving and a day plan | `scheduler.py::expand_schedule` | M | Shipped (deterministic; see BEYOND #7) |
| 2 | `mentor_behaviors` ingest and serve | `models.py::MentorBehavior`, `robot_data.py` | M | Shipped |
| 3 | `automarkup` as the expressiveness floor | `site/hive/automarkup/` | M | Shipped as a clean-room floor (`automarkup.py`) |
| 4 | Parse response tags into actions | `volley.py::ingest_action_tags` | S | Shipped (`actions.py`) |
| 5 | Content packs with review and `source_version` | `data_import.py`, `views.py::export_data`, `init_data.py` | S/M | Shipped ([brief](backlog/content-packs.md)) |
| 6 | Fleet ⊕ per-robot config | `robot_data.py::build_config` | S | Shipped |
| 7 | Puppet / telehealth mode | `views.py::puppet_api`, `moxie_server.py::send_telehealth*` | M | Shipped ([brief](backlog/telehealth.md)); unproven on a robot |
| 8 | A durable store for `mqtt/` | `site/hive/models.py` | M | Partial: JSON store with locks, not a database ([brief](backlog/production-hardening.md)) |
| 9 | Face customization | `views.py::face_edit`, `content/data.py::MOXIE_CUSTOMIZATIONS` | S/M | Shipped |
| 10 | Prebuilt multi-arch images | `docker-compose.yml`, `deploy.sh` | S | Shipped |

**Also from the forks:** Noonster77's slow-brain filler (shipped), pre-inference safety with parent
review (shipped), `connect_async` hardening (shipped), and speaker-scoped memory (**open**: we attribute a
memory to the activity that produced it, not to the person who said it). vapors' STT hot-swap is covered
for our own engines by the console voice picker.

### 4.2 Top 10 BEYOND

| # | Go beyond | Effort | Status |
|--:|---|:--:|---|
| 1 | A behavior planner, not a markup regexer | L | Planner shipped and default (`performance.py`); on the LLM path the model's own markup is kept, so P2 is open ([brief](backlog/expressiveness.md)) |
| 2 | Child safety as an enforced contract | M | Shipped (`safety.py`) |
| 3 | Any brain, hot-swappable, per child | M | Shipped (`brains.py`, [brief](backlog/brain-picker.md)) |
| 4 | Memory as a product | M | Shipped: structured, provenance-tagged, parent-editable |
| 5 | Insights that mean something | M | Partial: durable counts and daily history; sessions and trends open ([brief](backlog/insights.md)) |
| 6 | Sandboxed content extensions | L | Shipped (`content/ext/`, [brief](backlog/sandboxed-extensions.md)); nothing grants the `act` capability yet |
| 7 | A schedule that adapts | M | Shipped |
| 8 | A voice you choose, with lips that match | M | Voice picker shipped; lip-sync follows loudness because no engine emits phoneme marks yet ([brief](backlog/visemes.md)) |
| 9 | Vision events in the turn loop | M | Built; no robot has ever sent one ([vision](vision.md)) |
| 10 | One appliance, one identity, one command | L | One command and images shipped; one shared child registry and a guided first run open |

### 4.3 Build briefs

Items big enough to need a design get a brief in [`backlog/`](backlog/README.md); its README tracks
each brief's state.

### 4.4 The open backlog

Ranked by the owner's rule: **anything that makes a stranger's visit to the hosted Sim better, safer or
cheaper outranks work for a robot we do not have.**

| # | Open item | Robot-side? | Ready? |
|--:|---|:--:|---|
| 1 | Content authoring P1 remainder: try an unsaved draft (💬 Try it covers installed items), and *Rehearse this opener* | no | Build-ready ([brief](backlog/content-authoring.md)) |
| 2 | Insights: sessions, activity mix, trends | no | Build-ready ([brief](backlog/insights.md)) |
| 3 | Lip-sync from phoneme marks (BEYOND #8) | no | Needs a spec ([brief](backlog/visemes.md)) |
| 4 | Drop `'unsafe-inline'` from `style-src` | no | Blocked: Mermaid in `docs.html` emits inline styles (`sim/web/_headers`) |
| 5 | A parent-facing grant for the `act` capability | no | Needs a spec |
| 6 | Behavior planner P2 on the LLM path (BEYOND #1) | no | Needs a spec |
| 7 | One child registry and a guided first run (BEYOND #10) | partly | Needs a spec |
| 8 | Speaker-scoped memory (from Noonster77) | partly | Needs a spec |
| 9 | Missions editor (complete / forget / reset) | yes | Small once wanted |
| 10 | Broker device credentials and spoof refusal | yes | Blocked on a real robot ([brief](backlog/security-broker-auth.md)) |

Two ceilings no ranking moves: **no physical Moxie has connected to this broker**, and **no person has
used the hosted microphone in a real browser** (the route is tested with recorded speech). Everything
robot-side above — vision events, the `wakeup` acknowledgement, launch cards on a real camera, the
`license_values` question, OTA — waits on the first. An hour with a real Moxie on our broker would
settle more of this page than a week of building.

---

## 5. The honest ledger

### Where OpenMoxie is still ahead

1. **It runs real robots, and has since early 2025.** Everything of ours is proven only against the
   simulator.
2. **Its `doc/` folder.** `MoxieOverview.md` is the best config reference anywhere, and
   `AssetBundleMasterManifest.csv` lists every robot asset.
3. **Family layer (Noonster77).** Speaker-scoped memory and downloadable transcripts.
4. **Missions editor.** We store the history but cannot edit it.

### Where we are ahead

1. **Brain-agnostic AI seam** with pacing, backoff and an `ERROR_OFFLINE` fallback to the robot's
   on-device brain.
2. **Local-first speech** (`moxie_sdk/stt.py`) instead of a paid cloud call per utterance.
3. **The full `RemoteChat` contract**, config and telemetry contracts with a `LoggingPolicy` privacy gate.
4. **The parent app**, rebuilt clean-room: REST server, zero-knowledge crypto, phone web app,
   hardware-verified pairing QR.
5. **The simulator**, driven by the real protocol.
6. **Tests and CI** — neither upstream nor either fork has both.
7. **The reverse-engineering base**: 120 recovered `.proto` files and a protocol catalog, so we use fields
   OpenMoxie never touched.
8. **Security posture**, and safety, memory, sandboxed content and an adaptive schedule that go beyond
   what any OpenMoxie variant has.
9. **A try that tells the truth about itself.** Its interact page chats with a module; our 💬 Try it card
   runs the robot's own brain, safety check and staging, shows her face, moves and actions, keeps to an
   hourly budget, and writes nothing about the child.

## 6. How this feeds the build

| Contract | What this audit adds |
|---|---|
| [`content-module-contract.md`](content-module-contract.md) | Schedule serving, `mentor_behaviors`, packs, response tags, execution actions |
| [`ai-seam.md`](ai-seam.md) | The markup planner and `InputSafety`, the two seams OpenMoxie leaves empty |
| [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md) | Fleet ⊕ per-robot merge, face options, the OTA lever, insights |
| [`mqtt-and-conversation.md`](mqtt-and-conversation.md) | Telehealth commands, launch cards, the device allowlist |
| [`sim-as-a-client.md`](sim-as-a-client.md) | The simulator is where the planner and brain switching are rehearsed |

Noonster77's hardening list (§2.1) is a pre-flight checklist for the first real robot on our broker.

**Go star [OpenMoxie](https://github.com/jbeghtol/openmoxie).**

---
[Architecture index](README.md) · [Roadmap](../../ROADMAP.md) · [Community landscape](../community-research.md) · [Backlog briefs](backlog/README.md)
