# 🚧 Implementation plan — building our cloud server

> **Phase: BUILD (2026-08-31 →).** The study→spec distillation is done; now we build the self-hosted
> **robot cloud** from the six [build contracts](README.md), clean-room from *our* reverse-engineering
> docs (never the vendor app). Inspired by [OpenMoxie](../community-research.md) but
> taking it far beyond: a modular, spec-conformant, brain-agnostic stack. This doc is the build's shared
> roadmap + honest status; the build loops coordinate through it.

## What "beyond OpenMoxie" means here

OpenMoxie is a single Django app that does the essentials (repoint a robot, run a chat, a few modules).
Ours is built to the full recovered protocol with clean seams:

- **Brain-agnostic AI seam** — any LLM/STT/TTS behind the [ai-seam contract](ai-seam.md); the `Turn→Reply`
  SDK boundary means a game, an agent, or a local model *becomes* Moxie without touching transport.
- **Full RemoteChat contract** — not just text+markup: ResultCodes, output scoring (mood/dialog-act/
  emotion), actions (launch/exit/execute/sleep), input safety/moderation.
- **Data-driven content modules** — the [content-module contract](content-module-contract.md)
  (conversations/globals/schedules + the volley API), so activities are authored, not hard-coded.
- **Real config/telemetry** — the [config contract](config-and-telemetry-contract.md): `/config` down,
  `/state` up, LoggingPolicy honored (child-privacy is a contract, not a flag).
- **Interchangeable clients** — the [SIM](sim-as-a-client.md) and a re-homed robot are the same client.

## Current state (honest)

| Area | Contract | Status | Where |
|---|---|---|---|
| Parent-app REST (Channel 1) | [rest-api](rest-api-contract.md) | 🟢 substantially built | `server/` (main.py, crypto, db) |
| MQTT runtime (connect/config/state/turn) | [mqtt](mqtt-and-conversation.md) · [config](config-and-telemetry-contract.md) | 🟢 full turn loop: **streamed sentence chunks** (`REPLY_PENDING`/`chunk_num`/`is_completed`), slow-brain filler + re-arm, stale-turn cancel, **safety gates** (input / per chunk / whole reply), automarkup on every line, schedule + `mentor_behaviors` served, telemetry ingest, config editing (`POST /config`, fleet defaults in flight); end-to-end + live-tested through a real broker | `mqtt/supervisor/moxie_runtime.py` |
| AI seam — LLM brain | [ai-seam](ai-seam.md) §2 · [mqtt §4.5](mqtt-and-conversation.md#45-slow-brain-a-filler-now-the-real-answer-next-reply_pending) | 🟢 **any OpenAI-compatible endpoint, named by `MOXIE_LLM_BASE_URL`, which has NO default (2026-09-03)** — a public repo must not ship one deployment as everybody's fallback, so `MOXIE_APP=llm`/`content` exit at assembly naming the variable rather than guessing (`config.require_llm_base_url`; `MOXIE_APP=echo` needs no brain and is what the SIL and compose smokes run). expressive + ResultCodes/actions/scored-output; ERROR_OFFLINE fallback; **latency budget** (`MOXIE_BRAIN_BUDGET_S`, default 6 s) — a slower brain speaks a rotating filler as `REPLY_PENDING` chunk 0, with a stale-turn guard, every chunk synthesized. **Streaming** (`MOXIE_STREAMING`, default on): each finished sentence is published as its own `REPLY_PENDING` chunk as the model writes it, closed by a `SUCCESS` + `is_completed`; the filler timer re-arms per chunk (cap 2/turn) and a newer turn cancels the stream mid-answer. **Live-proven:** filler at 3.0 s + answer at 17.9 s (blocking, PR #14); streamed, first sentence at **1.52 s** vs whole answer at **4.38 s** on a healthy gateway (PR #15) | `mqtt/moxie_sdk/apps/llm_app.py` + `segment.py` + `chat.stream_completion` + `filler.py` + `moxie_runtime.py::_handle_stream_turn` |
| AI seam — presence (vision events) | [ai-seam](ai-seam.md) §2 · [vision](vision.md) §7 · [mqtt §4.7](mqtt-and-conversation.md#47-vision-events-and-whether-the-cloud-may-speak-first-built-v1-2026-09-02) | 🟡 **built, unproven against hardware** (audit BEYOND #9): the runtime subscribes the robot to its own vision events (`EventSubscription.active[]`, once per `(device, module_id)`) and routes `eb-found-face`/`eb-lost-target`/`eb-qr-event`/`eb-dr-event`/`eb-br-event` — which arrive as the `speech` of a `RemoteChatRequest`, not a topic of their own — into a pure presence state machine with hysteresis (a face blinking at the frame edge yields **one** `arrived` + **one** `left`, not twenty). `Turn.presence` carries a resolved snapshot into the prompt, and its `line` is **empty on most turns by design**. An `arrived` after ≥ `MOXIE_GREET_AFTER_S` (default 300 s, 0 = off) earns **one** unprompted hello — answered on the arrival event's own `event_id` (an unsolicited publish is *not* established as legal — see vision.md §7.4), rate-limited once per absence, never over a turn, never unpermitted, never in bedtime hours. **Honest ceiling:** no physical robot has ever sent us one of these events; the whole path is exercised by the SIL robot (`--face-event`) and the browser SIM, which proves consistency, not hardware truth | `mqtt/moxie_sdk/presence.py` + `moxie_runtime.py::_on_vision_turn`/`_greeting_for` + `wire.py` + `apps/llm_app.py::_system` + `sim/virtual_moxie.py` + `sim/web/bridge.js` |
| AI seam — expressive markup | [ai-seam](ai-seam.md) §2 · [mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02) | 🟢 the **markup floor**: `supervisor/markup.py` is no longer a passthrough — one pure, deterministic, stdlib-only generator (`annotate`) performs every reply that does not bring its own markup, so the echo/content/webhook apps stopped speaking flat and `LLMApp` stopped being a second generator (the model's mood/gesture are now *hints* into the same floor; `stream_style` deleted). Per line: a mood from `ePlaybackMood` 0-10, a `<usel>` delivery on a question or an exclamation, arm gestures on the carrying words, a `<break>` at an internal boundary (never after the final word), at most one whole-body `Bht_*`, a closing `Gesture_None`. Every id validated against a frozen, doc-cited catalog (`vocab.py`); an id a brain invents is dropped, never forwarded. Deterministic via `blake2b`, never `hash()`. **Measured p95 0.23 ms/line** (1 ms budget), no model call, no dependency. 8 byte-exact goldens + 277 hermetic cases; the goldens render six distinct faces through the real browser bridge. `MOXIE_AUTOMARKUP=0` restores the passthrough | `mqtt/moxie_sdk/automarkup.py` + `vocab.py` + `supervisor/markup.py` + `apps/llm_app.py::build_markup` + `sim/tests/test_automarkup.py` + `sim/test_automarkup_render.mjs` |
| AI seam — input safety | [ai-seam](ai-seam.md) §2 · [mqtt §4.5](mqtt-and-conversation.md#safety-on-the-wire-inputsafety-inputsafety) | 🟢 `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` **enforced**, not just specified: assessed pre-inference (a hard block never reaches a model) and **per streamed chunk** before publication (blocked chunk never goes out; a safe line closes the sequence with `SUCCESS`+`is_completed`; the stream is cancelled). 8 categories with a per-side block/flag policy in a parent-readable `safety_rules.json`, normalization + false-positive guards, a `Classifier` protocol for a drop-in local model, and a parent review queue (`GET|POST /safety` → console 🛡️ panel, `NO_DATA` = counts only). **Live-proven:** an unsafe request cost **0 gateway calls** and got a redirect + `input.safety`; a benign turn in the same run streamed 4 clean chunks | `mqtt/moxie_sdk/safety.py` + `safety_rules.json` + `moxie_runtime.py::_safety_gate_input`/`_handle_stream_turn` + `server/moxie_server/fleet.py` |
| AI seam — STT in | [ai-seam](ai-seam.md) §1 | 🟢 seam + runtime-wired + **real zmqSTTRequest protobuf decode** (dep-free) + JSON bridge, e2e-tested; **2 first-class engines — local faster-whisper (offline, no key) · gateway STT (`OpenAITranscriber`, live 2026-09-02)** — chosen by `MOXIE_STT=auto\|gateway\|whisper\|off` **or from the console's 🎚️ Listening dropdown (2026-09-02), which lists the gateway's STT models discovered live plus the installed local sizes and swaps the engine with no restart**, the headerless mic PCM wrapped in an in-memory WAV **at the bus's true 16 kHz**, and a `FallbackTranscriber` that latches to local whisper (or an honest `""`) instead of raising mid-sentence. **Live-proven:** gateway TTS → gateway STT at word overlap **1.00** at both 22050 Hz and 16 kHz, and one child utterance through the real runtime on gateway ears + brain + voice ([guide](../guides/litellm-stt-setup.md)) | `mqtt/moxie_sdk/stt.py` + `moxie_runtime.py` |
| AI seam — TTS out (for SIM) | [ai-seam](ai-seam.md) §3 · [sim](sim-as-a-client.md) | 🟢 seam + runtime-wired + **3 backends: built-in tone (zero-dep) · Piper (offline, Amy) · OpenAI-voice (gateway)**, choosable **from the console's 🎚️ Speech dropdown (2026-09-02)** — live gateway discovery + installed local voices, default `piper-amy`, persisted in `fleet/voice.json`, a Test button, explicit-local-wins; **full audio round-trip proven through a real broker** (SIL smoke `--expect-tts`); **the gateway voice is live-proven (2026-09-02)** — `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL=piper-amy` is the whole switch, its WAV is unwrapped to the header's true 22050 Hz, Whisper reads the audio back at overlap **1.00**, `piper-ryan` swaps the voice, and a gateway failure downgrades to Piper/tone instead of silence ([guide](../guides/litellm-tts-setup.md)) | `mqtt/moxie_sdk/tts.py` + `moxie_runtime.py` |
| Content-module engine | [content-module](content-module-contract.md) | 🟢 engine + ContentApp, runtime-selectable (MOXIE_APP=content) + example modules, e2e-tested through the runtime. **Memory built (2026-09-02):** `volley.persist_data` (durable, module-namespaced, bounded, `NO_DATA`-gated) + `session.summarize()` at end-of-conversation, served to a parent over `/memory`; ships as `content_modules/memory_chat.json`. **Per-item control (2026-09-02):** every remembered thing carries a stable id and its own provenance, so a parent can **erase or correct one line** (`DELETE …&item=` / `POST {"edit":…}` → the console's ✕/✏️), a correction is pinned, and unused items age out after `MOXIE_MEMORY_MAX_AGE_DAYS` (90). exec-code + action-plumbing still deferred. **Adaptive day plan (2026-09-02):** `schedules[]` is no longer expanded by a `(device_id, day)` rotation — `plan_inputs`/`plan_day` score every candidate on parent request, unfinished FTUE, coverage, recency, completion affinity, category variety and time-of-day fit, never plan into bedtime, and return a parent-readable *"why this activity today"* line per entry (stored as `robots/<id>/schedule_explain.json`, served by `GET /schedule`). The wire is byte-unchanged. **📦 Content packs (2026-09-02):** content is no longer a file in our repo — `moxie_sdk/content/packs.py` is a versioned, digest-checked pack file exported from a **positive field allowlist** (pinned to `dataclasses.fields()`; no child PII, memory, telemetry, permits, config or keys), and import-with-review whose per-item state is a **2×2** over `source_version` **and** a `local_rev` digest — so an item *you* edited is never silently replaced (upstream compares two integers and cannot see it). Three fleet collections + five status-HTTP routes (`GET /content`, `/content/export`, `POST /content/review|import|undo`; 409 on a review/import digest mismatch, 413 over 1 MiB), one-slot undo, and `reload_content()` — an attribute swap, so an import is live on the **next turn** with no restart and an in-flight turn keeps its module. Effective content = shipped defaults ⊕ the overlay, so our own release upgrades obey the same rule as a stranger's pack. An imported `code` block is stored, flagged ⚠️ and **never executed**. **🧩 The documented template form actually works in production (2026-09-02):** `prompt` is rendered through a sandboxed Jinja2 environment, and the *container* now ships `jinja2>=3.0` (+437 KB) instead of silently falling back — so `{% if %}`, which this contract has always advertised, no longer reaches the brain as literal template source. Where jinja2 is genuinely absent (a bare-metal SDK install without the `content` extra) the fallback evaluates `{{ dotted.path }}` and `{% if dotted.path %}` byte-identically to jinja2 and **removes** everything it cannot evaluate, counting each removal in `render.STRIPPED`; nothing template-shaped can reach a system prompt on any install | `mqtt/moxie_sdk/content/` + `mqtt/moxie_sdk/schedule.py` + `mqtt/moxie_sdk/store.py` + `mqtt/content_modules/` + `mqtt/requirements.txt` + the 📦 console card |
| Cloud queries — schedule + `mentor_behaviors` | [mqtt](mqtt-and-conversation.md) · [content-module](content-module-contract.md) | 🟢 the robot gets a **real day plan** and its **own history back**: `build_schedule` plans onboarding + a variety rotation of on-board activities, skipping what this robot already completed (so FTUE ends and nothing repeats); reported `mentor_behavior`s are ingested and served. Deterministic (day+device seeded), not yet LLM-planned | `mqtt/moxie_sdk/schedule.py` + `wire.py` + `moxie_runtime.py::_on_activity` |
| Durable per-robot state | — | 🟡 JSON files under `MOXIE_DATA_DIR` (default `mqtt/data/`), atomic-ish writes, survives restarts — a **stepping stone**, not the database the audit asks for (ADOPT #8) | `mqtt/moxie_sdk/store.py` + [`mqtt/data/`](../../mqtt/data/) |
| Config/telemetry data-model | [config](config-and-telemetry-contract.md) | 🟢 RobotCloudConfig (now incl. **`alarms` + `schedule_preferences`**, contract gap closed) + RobotStatus ingest + **Packet telemetry (build/parse/ingest/summarize) + LoggingPolicy upload-gate**; served to the console as `GET /telemetry`. **Telemetry is durable (2026-09-02):** two per-robot collections — a 500-envelope ring (`telemetry_packets`) plus 35 days of daily roll-ups (`telemetry_daily`) — so the 📈 card shows a **week** instead of an event log over one process's lifetime, and the `LoggingPolicy` gate now applies **on the way to disk** as well: `NO_DATA` writes nothing at all, `NO_MEDIA` (the default, as for the safety journal and memory) withholds **every** `event_data` payload because the recovered proto types none of them, `FULL` keeps them truncated. Caps configurable (`MOXIE_TELEMETRY_MAX_PACKETS`/`_MAX_DAYS`), lifetime `total` kept separately from the sliding window, a clock-lying `recorded_at` filed under arrival time, and the in-memory buffer is now a cache of the ring hydrated on first touch — so `telemetry_count`, the insights view and the planner's signals all see history. Live-proven: three packets into one supervisor, read back through the **next** one with the robot not reconnected. Config is layered **`defaults ⊕ fleet ⊕ per-robot`** (audit ADOPT #6) — one `fleet/config.json`, `POST /config?scope=fleet`. **Gated by a device allowlist** (audit §3.1, closed by default): only a permitted robot is pushed `child_pii`; an unknown one is *pending* and gets `build_unpaired_cloud_config()` — `fleet/permits.json`, `GET`/`POST /permits`, the console's 🔐 Robot access card, `MOXIE_ALLOW_UNVERIFIED_BOTS` to migrate. **Appearance built (2026-09-02, audit ADOPT #9):** the child's chosen face rides in `child_pii.face_options` and the pushed `child_pii.id` is a deterministic UUIDv5 over the chosen layers, so a look change re-keys the robot's face-texture cache and an idempotent re-push does not — a data-driven 14-slot catalog (`MoxieCustomizationType`, `moxie_sdk/face_assets.json`) carrying **72 options across 11 slots and zero invented asset ids** — the 12 doc-cited hex colours (`origin: recovered-enum`) plus 60 asset ids ingested as cited data from OpenMoxie's `MOXIE_CUSTOMIZATIONS` (MIT, commit `c8c2d380`, `origin: openmoxie-manifest`, every one flagged `caution`); `Stickers`/`Extras`/`Misc` stay empty, layered fleet ⊕ per-robot like every other override, and the console's 🎨 Moxie's look card renders whatever the catalog holds | `mqtt/moxie_sdk/cloud_config.py` + `faces.py` + `telemetry.py` + `store.py` |
| Telehealth / puppet mode ("Be Moxie") | [mqtt §3.9](mqtt-and-conversation.md) · [brief](backlog/telehealth.md) | 🟡 **built 2026-09-02, unproven against hardware** (audit ADOPT #7): the `commands/telehealth` command path a remote grown-up speaks through — six runtime verbs, `GET`/`POST /telehealth`, the console's 🎭 **Be Moxie** card (11 recovered moods, intensity **0-2** as the robot's own `maxIntensity`, interrupt, live text transcript) and a `bridge.js` handler so the SIM performs the operator's line through the same markup a brain reply uses. Every JSON key is cross-checked in CI against the recovered `TeleHealth.proto`; `line_id`/`line_params` are never emitted (no catalog of authored ids). The operator's text is classified as `role=MOXIE` and journalled — **a block is returned to them with its reason (400), never silently rewritten** — and while a session is open the brain is not called at all, so two voices can never share one mouth. `sim/run_smoke.sh --telehealth` runs the whole chain through a real broker. **Honest ceiling:** that `moxie_mode:"TELEHEALTH"` is what enters `STATE_TELEBRAIN` is field-proven (OpenMoxie), not capture-proven, and lives behind one constant | `mqtt/moxie_sdk/telehealth.py` + `moxie_runtime.py::telehealth_*` + `server/moxie_server/fleet.py::normalize_telehealth` + `server/static/app.js` + `sim/web/bridge.js` + `sim/virtual_moxie.py` |
| Hosted static site — what it admits it can do | [static-experience](static-experience.md) · [brief](backlog/live-sim-demo.md) | 🟡 **P0-a + P0-b built 2026-09-02, P1's EARS built 2026-09-03, unverified on a real Pages deploy**: the page no longer decides from the HOSTNAME, and it can now hold a conversation. `sim/web/mode.js` polls same-origin `GET /api/health` (a Pages Function that makes **no gateway call, ever**, and is always 200 so a non-200 unambiguously means *route absent*) and `sim/web/env.js` paints the badge, a capacity pill, the `needs-backend` marks and the banner from the answer — `offline` is **byte-identical to the site as it shipped**, `degraded` adds the honest reason, `live` stops claiming the mic needs a locally-run server. P0-b adds the live turn: `POST /api/chat` **builds the upstream body and never forwards the client's** (fixed model, fixed `max_tokens` 160, fixed temperature; a client `model`/`messages`/`tools`/`n` is *ignored*, not allowlisted — ignoring cannot drift), runs a pre-inference safety floor whose hard block never reaches a model, and returns the exact `remote_chat` payload `bridge.js` already renders plus an HMAC **speech ticket** and a signed context blob. `POST /api/speech` **has no text field at all** — the text lives inside the ticket's signature, so the only text the demo will ever synthesize is text it wrote itself in the last 60 s, which takes the most expensive per-request vector off the table structurally rather than with a counter. `sim/web/cloud-transport.js` wraps `sendUserTurn` (nothing in `bridge.js`/`audio.js` is touched) and routes the TTS message **before** the chat message, so there is one voice and not two. Every refusal is the same envelope with §4.5's status and `Retry-After`, so a 429, a spent budget, a full queue or a dead gateway all answer from `stub.js` instead of going quiet. No secret is involved on the client and none can leave the Function: the key is read only as `context.env.DEMO_GATEWAY_API_KEY`, is non-enumerable on the config object, and no upstream status, body or header is forwarded — `sim/test_demo_proxy.mjs` sweeps every response it produces (139 sweeps, 0 leaks) for the key, the base URL and every model id, in the body and in every header. With no variables set at all every route answers `gateway_not_configured` and makes zero upstream calls, so a keyless branch preview is automatically the scripted demo. A gateway behind a **Cloudflare Tunnel** works as a plain base URL; behind **Cloudflare Access** it needs a service token, and half a token is refused rather than sent. **The ears (2026-09-03).** `POST /api/transcribe` completes the chain: a visitor can now *speak*. Same caps, same envelope, same admission order — plus a byte floor below which **no upstream call is made at all**, and a container sniff that refuses 500 KB of non-audio for free. It also settled the spec's §10 assumption 15 with five real gateway calls, **negatively and consequentially**: the gateway transcribes a 16 kHz mono WAV word-perfect in 2.58 s but answers **HTTP 500** to webm/Opus, ogg/Opus and mp4/AAC — the three containers a browser's `MediaRecorder` can actually produce. Since 500 means `upstream_down`, which degrades the whole page, that would have taken the brain and the voice down on every press of the microphone. So `DEMO_STT_FORMATS` (default `wav`) refuses an unaccepted container *before* the call and per-turn, and `sim/web/mic.js` now **encodes 16 kHz mono WAV in the browser** — the downsampler and RIFF writer the spec's §2.1 noted were missing from `sim/web` entirely — while the local sidecar keeps `MediaRecorder` unchanged. `mic.js` also gained the honest cost ceiling the byte cap is not: a hard stop at `DEMO_MAX_RECORD_MS` (15 s), published in `/api/health`'s `limits`, proven by `sim/test_demo_ears.mjs` to stop a **fake** recorder at 15 001 ms with no microphone opened anywhere. **The fallback has a voice now (2026-09-03).** With no variables set — a fresh deployment, and every branch preview — the page is `degraded`, so the fallback IS the demo. It had 12 Moxie clips and went quiet for the rest: 9 of `stub.js`'s 11 replies and all 8 of `filler.py`'s thinking lines fell through to a 1.4 s probe for a Piper sidecar that cannot exist on a hosted origin, and then to whatever voice the visitor's browser owns — Moxie changing character mid-conversation. All 18 are pre-rendered now (`en_US-amy-medium`, mono 22050 Hz 64 kbit, **zero gateway calls**, 452 596 bytes), plus one line she says **once** on entering degraded and never again — *"The cloud has gone quiet. Do not worry, I am running on what I remember."* — fired on `mode.js`'s state transition rather than on a turn, and `offline` is deliberately excluded so a fork with no Functions stays byte-identical. The probe is skipped when `degraded` and kept in `offline`, where a self-hoster's Piper really is on :8081. `sim/test_fallback_coverage.mjs` (414 → **717 assertions**) now asserts a clip for every one of the **78** lines the degraded page can utter and drives the real `ambient.js` and `audio.js` under a stub window rather than grepping them; ten mutations were checked to turn it red. It also caught a live bug in `sim/tools/prerender_audio.py`: the manifest merge named its groups, so any run without `--ambient` rewrote `audio/index.json` with no `ambient` key — 56 committed MP3s orphaned, the whole self-talk layer muted, no error printed. **Honest ceiling:** the per-IP, concurrency and unit-budget counters are best-effort **in-process** — a Worker isolate is not a shared counter (§4.6) — and exact counters, Turnstile, a recovery line and streaming are still P1/P2 | `functions/api/{chat,speech,health,transcribe}.js` + `functions/api/_lib/{env,envelope,hmac,limits,wire,wav,safety}.js` + `sim/web/{mode,cloud-transport,env,mic}.js` + `sim/web/_headers` |
| SDK boundary (Turn/Reply/Action) | all | 🟢 clean, done | `mqtt/moxie_sdk/` |

## Build order (each milestone = a shippable, CI-green slice)

Following the [build-order spine](overview.md); the parent app
(#1) is largely done, so the build concentrates on the robot cloud (#2–#5):

- **M1 — Spec-conformant turn. ✅ (2026-08-31)** Made the RemoteChat response carry the full contract: ResultCodes
  (REPLY / ERROR_OFFLINE / NOREPLY), the scored output (mood/dialog-act/emotion), and action passthrough
  (launch/exit/sleep/execute) from `Reply.actions`. *(the turn already flows; make it correct)*
- **M2 — Content-module engine. 🟢 (2026-08-31)** Load module JSON (conversations/globals/schedules); run a
  `conversations[]` module (Jinja prompt over the volley + persona) through the AI seam; wire `globals[]`
  regex commands; the `volley`/`session` API (set_output, persist_data, add_execution_action).
- **M3 — AI seam: STT in. 🟢 (2026-09-01)** Turn `handle_zmq` into a real STT path — accumulate `zmqSTT` audio →
  transcribe (faster-whisper local, or a Deepgram-shaped proxy) → emit the recognized turn.
- **M4 — AI seam: TTS out for the SIM. 🟢 (backends 2026-09-02)** Server-side Piper (`PiperSynthesizer`, offline, Amy)
  + OpenAI-voice backend → `CloudTTSResponse{audio, marks}` so the
  SIM (and optionally a robot) speaks with a server voice + viseme marks.
- **M5 — Config & telemetry. 🟢 (2026-09-02)** Full `RobotCloudConfig` (bedtime/wake/volume/timezone/child_pii), `/state`
  ingest, the `Packet` telemetry envelope, and the LoggingPolicy upload-gate — all built + tested. Surfacing the
  stored state/telemetry in the console is M6.
- **M6 — Parent console wiring. 🟢 (2026-09-02)** All three console surfaces are live: robot **state**
  (`/local/fleet` + live-state card), **config editing** (a Settings form → `POST /local/robots/{id}/config` →
  runtime `POST /config` (`sanitize_config_overrides` whitelist/validate) → `update_config` re-pushes
  `RobotCloudConfig`), and **telemetry/insights** — the runtime's stored `Packet` events rolled up by
  `summarize_events` and served as `GET /telemetry` → `GET /local/robots/{id}/telemetry` → an 📈 Insights
  panel (a zero-filled week of daily counts, counts by event, a recent-events list, and the retention window
  it is really showing — telemetry became **durable** on 2026-09-02, so the card survives a restart). All
  three live-verified end to end. Since
  2026-09-02 the console also carries the 🎚️ **Voice** card — Speech + Listening dropdowns over live
  gateway discovery plus the installed local engines, with a Test button
  (`GET/POST /voice`, `POST /voice/test`; [backlog/voice-picker.md](backlog/voice-picker.md)).
- **M7 — One-command stack + docs. 🟢 (proven 2026-09-02)** `docker compose up` at the repo root brings up
  broker (TLS + WS + plain) → supervisor (brain + `tone` voice, zero extra deps) → parent console, configured
  by one root `.env` ([`.env.example`](../../.env.example)), with healthchecks, `restart: unless-stopped` and
  named volumes for certs / console DB / conversation memory. Proven end to end by
  [`sim/run_compose_smoke.sh`](../../sim/run_compose_smoke.sh) — it builds the real compose file under a
  throwaway project on unused ports, waits for all three healthchecks, round-trips `virtual_moxie.py
  --expect-tts` (state → config → remote-chat → reply → `CloudTTSResponse` audio) through the composed broker,
  reads the robot back out of the console's `/local/fleet`, then `down -v`. Guide:
  [`../guides/one-command-stack.md`](../guides/one-command-stack.md). Opt-in profiles: `--profile voice`
  (Piper/Amy — verified speaking through the stack) and `--profile stt` (faster-whisper — model fetched and
  the supervisor reports `STT enabled`, no live speech transcribed yet); both need one `.env` line
  (`MOXIE_SUPERVISOR_EXTRAS`) plus `up --build`, which is the honest cost of keeping the default image small.
  **Prebuilt images (2026-09-02):** the release workflow now publishes multi-arch (`linux/amd64` +
  `linux/arm64`) images to GHCR on every `v*` tag — `ghcr.io/mvalancy/moxie-robot-saver/{supervisor,
  console,broker-certs}`, tagged `X.Y.Z` / `X.Y` / `latest` — and the self-contained
  [`docker-compose.images.yml`](../../docker-compose.images.yml) turns the install into *download one
  file, `docker compose up`*, with **no clone**. Proven locally end to end by
  `MOXIE_SMOKE_MODE=images sim/run_compose_smoke.sh` (that exact file, `pull_policy: never`, against
  locally built images under the published names). **Verified at v0.6.0: all three multi-arch images published to GHCR by the release tier.** — nothing has been pushed to GHCR yet, so the pull path is asserted by
  construction, not observed.

## Known gaps (audited, honest)

Tracked so the status table above isn't over-claimed. Each is a build slice, not a bug:

- **Integration evidence (2026-09-03) — the merged state was exercised as a whole, and it holds.** The three
  slices that landed without ever being run together (config honesty, the offline fallback, the rewritten deploy
  guide) were checked against the four risks that merge created, and all four came back clean: the new loud
  failure does **not** break the compose path (`sim/run_compose_smoke.sh` green in build **and** images mode, each
  a full `state→config→remote-chat→reply` round-trip with real TTS audio), and it is correctly scoped — only the
  `llm` and `content` apps call `require_llm_base_url`, the `echo` app the smokes use needs no brain, and
  importing `config` alone never exits; a **running** process reads the dotenv as documented (file read, trailing
  comment stripped, an explicit variable beating the file, `MOXIE_SKIP_DOTENV` beating even an explicit
  `MOXIE_DOTENV` path, a missing file not fatal); comment-stripping truncates nothing (all 24 keys of
  `mqtt/.env.example` parse sanely, and 13 adversarial cases hold — `a#b`, `http://h/p#frag` and `sk-abc#def`
  survive intact while a *spaced* ` #` is a comment); and the prerendered clips are an exact bijection with their
  manifest — 88 indexed, 88 on disk, **0 missing and 0 orphaned**, all decodable, which is the direction the
  `prerender_audio.py` merge bug actually failed in. Two environment defects were found and fixed rather than
  worked around: `sim/tests/.venv` was missing `paho-mqtt`, `jinja2`, `PyYAML` and `numpy`, so a parity test
  failed and the live-turn test *skipped* for a reason that had nothing to do with credentials.

- **the hosted mode machine is proven hermetically, in a real browser, and now ON A REAL DEPLOY
  (2026-09-03) — two of its three unknowns are closed.** `sim/test_mode.mjs` calls the Pages Functions
  directly under bare node and `sim/test_env_hosted.mjs` drives the real rendered page through
  offline/degraded/live/busy/malformed in Chrome. What the repo could not establish, a **branch preview**
  answered — and no owner was needed, because every branch push already publishes one: **Pages does route
  `functions/` from the repo root** even though `pages_build_output_dir` is `sim/web` (spec
  [`backlog/live-sim-demo.md`](backlog/live-sim-demo.md) §10 assumption 8, the highest risk in that
  document — `GET /api/health` returned 200 JSON `gateway_not_configured`), and **`api/_lib/` is not
  routed or readable** (assumption 9; it serves the static HTML fallback, so probe by content type, not
  status). The third is settled the *other* way and it mattered: **`sim/web/_headers` does not apply to a
  Function response at all** (new assumption 27) — the same preview served `/sim.html` with the `/*`
  block's `Referrer-Policy` and `/api/health` with none, so §4.7's security block never protected
  `/api/*`. `Referrer-Policy` now lives in `envelope.js`, where the only headers that actually ship are
  set, and `sim/test_demo_proxy.mjs` fails if any header named in the `/api/*` block is missing from the
  code. **Still open, and honestly so:** the plan's wall-clock/body limits, the free-tier allowance, and
  whether Production and Preview variables are truly separate — the preview is keyless today, but so is
  Production, so that proves nothing until the owner sets Production-only values.

- **the degraded page now has a voice for every line it can utter — and three places are
  still silent, named rather than rounded off (2026-09-03).** `sim/test_fallback_coverage.mjs`
  proves a clip exists for all 78 utterable lines and, by mutation, that a new uncached one
  turns the build red. The residue: (a) **§6.3's recovery line is not built** — the badge flips
  back to `LIVE` when a health poll recovers, but Moxie says nothing about being back, so a
  visitor who watched the cloud go quiet gets no spoken all-clear; (b) **free text is still a
  browser voice** — anything a visitor types that reaches `speak()` without a clip (the `#speech-btn`
  TTS test, a `live` turn whose gateway voice did not arrive) has no pre-render by definition,
  which is exactly what P1's TTS cache is for; and (c) **the child is still mute** —
  `mic.js`'s degraded fallback publishes a scripted child line with a clip sitting right there
  in the manifest's `child` group, but nothing plays it, because making `who: "child"` audible
  is a P2 item. None of the three is a regression and none is silent *where it used to speak*;
  they are the honest remainder of "a real voice for the lines it plays".

- **the hosted ears are proven against a stub and a fake recorder, and the ONE thing only a
  real gateway could answer was answered (2026-09-03) — but a real browser still has not
  recorded a real word into them.** `sim/test_demo_ears.mjs` covers `/api/transcribe` and
  `sim/web/mic.js` hermetically (1 324 assertions), and five real gateway calls settled spec
  §10 assumption 15: **the gateway transcribes a 16 kHz mono WAV word-perfect and answers
  HTTP 500 to webm/Opus, ogg/Opus and mp4/AAC**, which is why `mic.js` now encodes WAV in the
  browser rather than shipping a `MediaRecorder` blob. What remains unproven is the last link:
  no test in this repo may open a microphone (playbook rule 11), so the chain
  *real mic → `AudioContext` → the browser's WAV → the gateway's ears* has never run end to
  end. Each half is verified against the other — the encoder's output is parsed by the
  **server's own** RIFF walker, and its header fields match the control clip that transcribed
  live — but the join is inference. The exact `AudioContext.sampleRate` and mime string each
  browser reports are §10 assumption 16's remaining half, and they no longer change any
  decision: the encoder writes the true rate and never upsamples. One person, one browser, one
  sentence settles it. Two smaller ones ride along: `mp3` came back **429** on the fifth call
  and is honestly inconclusive, and the per-IP window that a too-short clip still consumes is
  deliberate (it matches `/api/speech`'s treatment of a bad ticket) but is the conservative
  direction rather than a free one.

- **telehealth is built against the SIM, never against a robot (2026-09-02).** The whole
  `commands/telehealth` chain is exercised through a real broker by
  `sim/run_smoke.sh --telehealth`, and every JSON key is proven against the recovered
  `TeleHealth.proto` — but five questions need hardware and are listed in
  [`backlog/telehealth.md`](backlog/telehealth.md) §6: whether `moxie_mode:"TELEHEALTH"`
  is what enters `STATE_TELEBRAIN` (B1, behind one constant), what `INTERRUPT` does to a
  line already in the air (B2), whether a brain-less robot still emits `events/remote-chat`
  (B3 — one `if` makes us correct either way), whether bedtime suppresses `PLAY_OUTPUT`
  (B4 — we warn and send rather than guess), and whether `Output.line_id` resolves against
  on-board content (B5 — we refuse to emit ids we cannot cite). The console also cannot let
  an operator *hear* the child: the transcript is text only, which is a stated non-goal
  (no audio field exists in the recovered message) rather than an oversight.
- **face customization — the vocabulary is 72 across 11 of 14 slots; three slots are still
  empty and nothing is hardware-proven (2026-09-02).** The manifest ingest the previous
  revision of this bullet asked for is **done**: `moxie_sdk/face_assets.json` transcribes
  OpenMoxie's `site/hive/content/data.py::MOXIE_CUSTOMIZATIONS` (MIT, commit `c8c2d380`, 60 ids)
  as *data* under an inline citation — id strings only, no code, no comments; the slot mapping
  and every label are ours, `unmapped` is empty, and each entry is tagged
  `origin: "openmoxie-manifest"`. The twelve hex colour options our own corpus cites
  (`robot-lifecycle.md`:281-282) stay separable as `origin: "recovered-enum"` and are still the
  only ones a picker can *preview*. **What is still open:** `Stickers`, `Extras` and `Misc` have
  no ids from either source and we invent none; every manifest entry carries `caution: true`
  because upstream's own note records that some of these crashed Unity **without saying which**
  (`mqtt-and-conversation.md`:824 says the same independently); the id space is bundle-defined
  (`behavior-markup.md`:161-163 — the generators accept *any* id the loaded bundle defines), so a
  robot whose streamed bundle differs (`content-delivery.md`:79, `REMOTE_ASSETBUNDLES`) may not
  carry these at all; an id outside the catalog still goes through `face.custom` (shape-checked
  only). Two flagged assumptions sit behind one function each: the wire spelling of a
  *recovered-enum* layer label (a manifest id is already a whole label and travels verbatim), and
  that the texture cache is keyed on `child_pii.id` (field-proven via OpenMoxie, not
  capture-proven). **No physical Moxie has rendered any of it** — 72 options proven against the
  simulator only, which is exactly as unproven as 12 were.
- **adaptive schedule:** the recommender is live (see the content-module row) but three things
  in it are **inferred, not capture-proven**. (a) *Nothing in the recovered telemetry identifies a
  module.* `Packet.event_name` is a free string and `event_data` opaque bytes, so "what the child
  finishes vs. abandons" comes from `mentor_behaviors` alone; telemetry is reported as context and
  `inputs.telemetry.carries_module_signal` says so. (b) *Slot length is ours.* `CSData.module_started_ts`
  proves the robot time-boxes an activity but not for how long, so `SLOT_MINUTES = 10` is a chosen
  constant — it sets how a plan maps onto the clock (time-of-day fit, bedtime truncation, where a
  parent request lands), and a real duration would shift those. (c) *Times are resolved in the
  server's local timezone,* not the robot's `timezone_id`; `ParentRequest.scheduled_at` is epoch
  seconds and the bedtime strings are wall-clock, so an appliance in a different zone from its robot
  would plan against the wrong clock. The *"why this activity today"* line now has a console
  page — the 📅 Today's plan card reads `GET /schedule` through `GET /local/robots/{id}/schedule`
  — but it is **read-only**: a parent can see why an activity is on the day and cannot reorder or
  drop one from there, only change the inputs (bedtime, requested activities) in ⚙️ Settings.
- **content-module:** `session.summarize()` + `volley.persist_data` are **built** (2026-09-02) — the
  brain is wired in through the same injected chat seam, a finished conversation is summarized into a
  few durable facts with provenance, and a parent can read/erase them over `/memory`. The parent-facing
  browser UI over those endpoints landed 2026-09-02 (the console's 🧠 What Moxie remembers card —
  see the memory-browser bullet below for what it still cannot do). One honesty remains: a summary
  **can be wrong and is sticky** — a bad fact is re-injected into every later prompt until someone corrects
  or erases it (which is now a one-line ✏️/✕ rather than the whole activity). Arbitrary
  module `code`-string execution is deliberately deferred (sandboxing), so a module *declares* its
  memory with a `memory` block instead of scripting a `complete_handler`; `volley.execution_actions`
  (e.g. `eb_timer_request`) are captured but **not yet plumbed** into `RemoteChatAction` on the wire.
- **presence is inferred, not observed.** Vision events are now ingested and can make Moxie greet a
  child who walks back in ([vision.md](vision.md) §7), but **no physical robot has ever sent us one**.
  Four specific honesties: (a) the delivery shape (a perception event in the `speech` slot) and the
  `EventSubscription` opt-in come from the recovered catalog + OpenMoxie's module-API doc, not from a
  capture; (b) an **unsolicited** `commands/remote_chat` is *not* established as legal, so a hello with no
  request to answer is queued as chunk 0 of the next turn instead — if a capture ever shows otherwise the
  queue becomes an optimization; (c) the hysteresis constants (`FLICKER_S` 3 s, `MIN_PRESENT_S` 2 s) are
  guesses about how twitchy the on-device tracker is, which is why they are env knobs; (d)
  `eb_custom_face_search`'s "someone is close enough" size gate is catalogued but not yet driven, so
  presence means "a face the robot chose to target", not "someone within range" — and it can never mean
  *which* child, because the events carry no identity.
- **memory browser — a parent can read and erase, but only in whole activities.** The console's
  🧠 What Moxie remembers card lists every stored item with the day and module it came from, and erases
  one namespace or all of it, because **that is exactly the granularity the runtime offers**
  (`MemoryStore.erase(device_id, namespace|None)`); there is no per-item delete and no edit, so one
  wrong fact costs that whole activity's memory and Moxie relearns the rest. Three more honesties:
  provenance is recorded **per merge**, not per item, so every row in a namespace shows that
  namespace's newest attribution (the console already renders a per-item `_provenance` if a later
  runtime writes one); `_meta.summarized_through` is stripped by the runtime's parent view, so the
  card's "summarized through" hint has nothing to show until `MemoryStore.view()` surfaces it; and
  nothing **decays** — a fact learned once stays until a person deletes it, which is why the guide
  ([`what-moxie-remembers.md`](../guides/what-moxie-remembers.md)) tells parents to read it now and
  then. Per-item erase, editing and age-out are what keep
  [audit](openmoxie-feature-audit.md) §4.2 BEYOND #4 at 🟡 rather than 🟢.
- **the gateway now speaks AND hears — what is left is smaller.** Both audio halves are proven end to
  end (2026-09-02). Voice: `piper-amy`/`piper-ryan` speak, the WAV's own header sets the
  `CloudTTSResponse` rate, a failure downgrades to Piper/tone rather than silence. Ears: `stt-whisper`
  (also `graphling-stt`, `stt-whisper-base`) read our own audio back at **word overlap 1.00 at both
  22050 Hz and the robot's 16 kHz**, and one child utterance ran through the real runtime with ears,
  brain and voice all on the gateway — so a box with no model wheels now has *both*, which is what a
  hosted deployment needs. The honesties that remain: (a) the cloud is slower than local for both legs
  (voice ~1.3-1.7 s vs a few hundred ms of Piper; ears ~2.5-2.8 s for a 6 s clip vs ~1 s of local
  `base.en`), so a home appliance should still choose the local engines — `MOXIE_STT=whisper` forces
  the local ears, and `MOXIE_TTS=piper` forces the local voice the same way (made an explicit override at
  integration, pinned by a test); (b) a 429/5xx path cannot be provoked on demand, so the
  backoff on both audio paths is asserted against a fake rather than the live proxy; (c) the compose
  files now forward `MOXIE_STT`/`MOXIE_STT_MODEL`/`MOXIE_STT_BASE_URL`/`MOXIE_STT_API_KEY` (done at integration; was: not the new `MOXIE_STT_BASE_URL` /
  `MOXIE_STT_API_KEY`, so a composed stack picks up the gateway ears only through the voice/LLM
  fallback; and (d) no *physical* robot has streamed a real utterance into either engine.
- **ai-seam:** STT seam is built + wired (feed_stt/handle_zmq, e2e via a JSON audio bridge); real zmqSTTRequest protobuf decode is DONE (dep-free field reader in stt.py); only a live faster-whisper test remains (optional dep). TTS out (§3) seam + runtime-wired (synthesize-on-reply → CloudTTSResponse); the gateway voice is live-proven (see the bullet above), viseme TTSMarks still deferred. Input safety/moderation (§2) is **built** — see the next bullet for what it honestly cannot do.
- **expressive markup — a rule floor, and no robot has ever played it.** The floor is built and every
  app performs now, but three honesties stand. (a) **Nothing about robot rendering is verified.** Our
  catalogs are the *app-hardcoded subset* of a namespace the loaded content bundle actually defines, so
  `validate_markup` catches our typos and cannot prove a given robot has an id — and whether a robot
  ignores an unknown mark or faults is unknown. The browser SIM is the only renderer we can assert
  against. (b) **Two of the four output slots are gated off, honestly.** All four confirmed `icons-v2`
  values are calendar/event cues (guessing them from free chat would be wrong), and we have exactly
  **two** confirmed `SoundToPlay` ids — one of which is a looping music bed a spoken line should never
  start — so SFX is effectively one stinger and stays off; spurts are off too, because "Hmm," plus a
  `hmm thinking` spurt may read as "hmm… hmm" and the SIM's external TTS strips the tag so the SIM
  cannot answer it either. Widening SFX needs the robot's asset-bundle manifest. (c) **Scored output is
  still empty.** The floor scores a line internally but renders that score only into `markup`; no app
  sets `Reply.mood`/`dialog_act`, `ReplyChunk` has no scored fields at all, and one consequence is
  visible today — on a *streamed* turn the mood mark goes on chunk 0 only, so the model's own mood
  (which arrives with the closing chunk) shapes that chunk's gesture and never reaches the wire. That is
  the behavior planner's contract change (C1-C5), not the floor's.
- **input safety — a rule engine is a floor, not a filter.** The stage is real (blocked child
  input never reaches a model; a blocked chunk never reaches the wire) but the *classifier* is
  word lists plus phrase regexes over normalized text. It cannot read context or sarcasm, or a
  harmful idea expressed in gentle words; it misses novel phrasings, obfuscation past its
  normalizer (letters split with spaces, invented spellings) and every language its tables are
  not written in; its slur/profanity lists are short by construction; and it will occasionally
  flag something innocent, which is why the ambiguous categories flag rather than block. Two
  structural honesties: **a spoken chunk cannot be unsaid**, so a stream blocked at chunk 2
  leaves chunks 0-1 already heard (checking earlier would mean not streaming at all); and the
  recovered contract has **no output-side safety field** — `RemoteChatInput.InputSafety` is by
  definition the read of the *child's* input, so a block on Moxie's own output is recorded in
  the parent queue and the log rather than faked onto `input.safety`. The seam is built for the
  fix: `Classifier` is a one-method protocol, so a local model classifier drops in behind it
  (`MoxieRuntime(app, safety=…)`) without the runtime changing. Also unproven on hardware: no
  capture shows how a physical Moxie reacts to `input.safety` — we populate it because the
  contract says a kid-facing backend should, not because we have seen the robot act on it.
- **wake alarms / schedule preferences — the shapes are ours, not a capture.** `alarms`
  (`WakeSchedule`) and `schedule_preferences` (`SchedulePreferences.ParentRequest`) are now built and
  parent-editable, but our protos give the *types* and not the *encodings*, and no capture of a real
  alarms push survives in the corpus (OpenMoxie never implemented these fields either, so there is no
  field-proven shape to copy). Three assumptions are therefore load-bearing and documented in the
  [config contract](config-and-telemetry-contract.md#wake-alarms-scheduled-activities-the-json-we-emit):
  `days` as **0 = Monday … 6 = Sunday**, `time` as `"HH:MM"` local wall clock, `scheduled_at` as epoch
  **seconds**. Each sits behind one constant, so a contradicting capture is a one-line fix — but until a
  physical Moxie is seen to *ring*, "built" here means "well-formed and pushed", not "field-proven".
- **broker hardening is containment, and one door stays open on purpose.** P0's ACL
  closes `$SYS` enumeration and cross-device reads/writes on every listener, but the
  websocket listener (`9001`) still grants an **anonymous, read-only** `/devices/#` — that
  is how the browser SIM renders a live robot, and a page served to a browser cannot hold a
  secret. So a client on your LAN that reaches `9001` can still *watch* a config push go
  by. Closing it means routing the SIM's live view through the console's HTTP API instead
  of the bus (`security-broker-auth.md` §2.5 option (b)); until then the honest mitigation
  is that `9001` is only worth publishing when you actually use the browser SIM.
- **the pairing gate — an application-layer permit list, and the un-paired value is not
  capture-proven.** The allowlist is built, closed by default and enforced on the transport
  boundary, but three honesties stand. (a) **It is not authentication.** The broker still
  accepts anonymous connections (the robot's RS256 JWT is never verified — [mqtt §3b](mqtt-and-conversation.md)),
  so an unpermitted device can still *connect*, publish, and see its own topics; what the
  gate stops is our server *serving* it — no `child_pii`, no brain, no schedule, no
  telemetry ingest. A device that spoofs a permitted robot's `d_<uuid>` is served as that
  robot. **Broker hardening P0 landed 2026-09-02** and narrows the blast radius without
  claiming to fix this: a `%c` ACL confines every anonymous client to its own
  `/devices/<client id>/…` subtree, `$SYS/broker/log` (the fleet roster) is readable only
  by a supervisor that now authenticates with a per-appliance credential, and the plain
  listener is loopback-bound ([mqtt §3.1](mqtt-and-conversation.md),
  [`backlog/security-broker-auth.md`](backlog/security-broker-auth.md) §2, proven by
  `sim/run_acl_proof.sh`). **It is containment, not authentication** — the spoof above is
  unchanged, and so is the client-id-collision eviction that lets a spoof knock the real
  robot off the bus. Real JWT verification (P1) and refusing the CONNECT (P2) remain
  deferred, blocked on the brief's A1–A4, all of which need a physical robot. (b) **`pairing_status:"unpairing"` is field-proven, not capture-proven.** No
  capture of Embodied's cloud pushing a non-`paired` status survives in our corpus; the
  value comes from OpenMoxie's own device form, and **no physical robot has been observed
  receiving it** — what a real Moxie shows on screen for a pending config is unknown (it
  sits behind one constant, `UNPAIRED_PAIRING_STATUS`). (c) **The auto-permit covers one
  path.** Pairing through the console permits the robot when the caller supplies its MQTT
  `d_<uuid>` — the console does that when exactly one robot is pending. The QR itself
  carries Wi-Fi + the pairing seed and no device id, so a robot that arrives any other way
  (endpoint-QR re-home, factory reset, a second robot) is genuinely *pending* and needs the
  one click. Binding a pairing to a device id before the robot connects would need the
  robot to present the pairing key over MQTT, which is not in the recovered contract.
- **brain latency — the multi-chunk assumption.** Background inference + filler is built and
  live-proven (a filler inside the budget, the answer as chunk 1 of the same `event_id`), but the
  *robot-side* semantics of `REPLY_PENDING` are only partly established by our RE docs: they give the
  fields (ResultCode 9 = "more chunks to come", `chunk_num`=22, `RemoteConsistencyControl.is_completed`=18)
  and that the robot "can start speaking a stable prefix before the full reply lands", **not** a capture
  proving a physical Moxie speaks chunk 0 and keeps the turn open for chunk 1. The SIM does
  ([`sim-as-a-client.md`](sim-as-a-client.md):77), so this is proven against every client we have and
  assumed for the robot. If a real robot disagrees, the field-proven fallback is OpenMoxie Fork A's shape —
  answer the current request with the filler and deliver the finished answer on the robot's next
  (re)prompt, same background inference, no second unsolicited publish; that is a small change to
  `_handle_turn`, not a redesign. Two smaller honesties: the filler is a fixed rotation of eight written
  lines (Fork A pulls DB-sourced trivia/jokes, and only ours is markup-performed), and a turn that
  overruns the budget occupies a `_pool` worker plus a timer thread until it finishes.
- **streaming — the same assumption, leaned on harder.** A filler turn publishes two responses for one
  `event_id`; a streamed turn publishes three to five, and we have **no capture** of a physical Moxie
  playing chunk 2. It is proven against every client we have (`sim/virtual_moxie.py` joins the chunks of an
  `event_id` in `chunk_num` order; the browser SIM queues each `CloudTTSResponse` by `chunk_num` and now
  appends later chunks of a turn to one transcript row) and assumed for the robot; `MOXIE_STREAMING=0` is
  the one-variable switch back to the single-reply wire. Three smaller honesties: sentence segmentation is
  rule-based, so a rare abbreviation outside the small built-in set splits a line one sentence early (a
  clipped breath, never lost words); mid-stream chunks get a **rule-based** mood/gesture because the
  model's own choice arrives after the `"say"` string, so only the closing chunk performs exactly what the
  model asked for; and once a chunk has been spoken it cannot be unsaid, so a stream that dies mid-answer
  closes the turn with what was already said rather than retrying the question.
- **ai-seam → response-tag actions: BUILT.** The brain can now drive the robot from inside its own line — `moxie_sdk/actions.py::parse_action_tags` lifts `<exit>` / `<sleep>` / `<launch:MOD[:CID]>` / `<launch_if_confirmed:MOD[:CID]>` out of model text into real `Reply.actions` (stripped before speaking), applied in `LLMApp.respond` and `ContentApp` (model + global-handler paths) and taught to the model via `ACTION_TAG_PROMPT`; tests in `sim/tests/test_action_tags.py`. **Caveat:** our recovered contract defines `RemoteChatAction.ActionID.launch_if_confirmed`, but `ActionType` has no confirm member, so that tag maps to `LAUNCH` — the robot launches immediately instead of asking first (one-line fix at `actions.py::LAUNCH_IF_CONFIRMED_AS` once the enum gains one). Pattern from OpenMoxie (MIT), audit §4.1 row 4.
- **config/telemetry:** RobotCloudConfig + RobotStatus ingest + Packet telemetry (build/parse/runtime-ingest/
  summarize) + the LoggingPolicy upload-gate are built (M5 🟢) and the console's 📈 Insights panel now surfaces the
  stored events (M6 🟢). **Durable since 2026-09-02:** `telemetry_packets` (a 500-envelope ring) +
  `telemetry_daily` (35 days of roll-ups) per robot, policy-filtered on the way to disk and loaded back on
  first touch, so a restart no longer erases what a parent might want to know — see the
  [contract](config-and-telemetry-contract.md#how-this-server-persists-telemetry-built-v1-2026-09-02).
  Remaining (not blocking): typed `event_data` decoding (LogDevice/LogUser wrappers) — and the interesting
  half of BEYOND #5, *sessions / activity mix / mood trend*, which this history cannot express because
  `Packet.event_name` is a free string with no recovered module vocabulary; those must be derived from
  `mentor_behaviors` or emitted by us as our own named events.
- **cloud queries (`query_result`):** shape **and content** are now real. `_on_activity` answers a
  `client-service-activity-log` / `subtopic:"query"` request via `build_activity_response`
  (`mqtt/moxie_sdk/wire.py`), echoing `request_id` and keying the payload by its own
  `CloudQueryResponse` field. `schedule` carries a built `ContentSchedule` and `mentor_behaviors`
  carries this robot's reported history. Citations:
  [`cloud-protocol.md`](../reverse-engineering/protocol/cloud-protocol.md):147 (the
  `commands/{command}` JSON topic), :172 + [`mqtt-and-conversation.md`](mqtt-and-conversation.md):274/:296
  (the `subtopic` multiplex and the `query_result` command), :229 + `CloudQueryResponse` in
  [`recovered-proto/embodied/logging/Cloud.proto`](../reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto)
  (`request_id`=3, `schedule`=6, `license_values`=5, `mentor_behaviors`=10);
  [`ContentSchedule.proto`](../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/ContentSchedule.proto)
  and `RecommendationContext.Recommendation` in
  [`RemoteChat.proto`](../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto):26-34
  for the plan itself; `ActivityUpdate.mentor_behavior`=14 (Cloud.proto:241) +
  [`MentorBehavior.proto`](../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/MentorBehavior.proto):26-36
  for the report. Corroborated by OpenMoxie's `moxie_server.py::provide_schedule` /
  `provide_mentor_behaviors` / `ingest_mentor_behavior`. **Still gaps:** `license` answers empty (we hold
  no license blobs); `response_code` (field 99) is not emitted (the docs give the enum but not its JSON
  spelling); the other queries (`idf`, `contexts`, `context_store`, `remote_lines`) answer empty.
- **day plan:** the deterministic rotation was replaced by the explainable recommender in PR #39 (see the adaptive-schedule bullet below); what remains inferred is listed there.
- **the store is JSON files, not a database.** `mqtt/moxie_sdk/store.py` persists per-robot
  `mentor_behaviors` under `MOXIE_DATA_DIR` with atomic-ish writes and a 500-record cap. It has no
  indexes, no queries, no migrations, and no concurrent-writer story beyond a single process's lock —
  it exists so ADOPT #1/#2 could ship without blocking on ADOPT #8's real database. Conversation memory
  (`MOXIE_MEMORY_DIR`) and telemetry still use their own paths; folding all three onto one durable
  store is the next slice.
- **Integration evidence (2026-09-02, v0.7.0 RC).** The whole stack was exercised end to end on the
  release candidate and both paths that landed last were live-validated through the *built* backend, not
  a mock. Creds-free: `sim/run_smoke.sh` on a free port → `✅ SIL round-trip OK` with the audio leg
  (`🔊 spoke 50934 B @ 22050 Hz`), `sim/run_scenarios.sh` → `4/4` + `4/4`. The **adaptive schedule** was
  driven through a real mosquitto + `mqtt/run.py` + `sim/virtual_moxie.py --query schedule` with a
  scratch `MOXIE_DATA_DIR`: seeded `mentor_behaviors`, a bedtime and a `ParentRequest` written over
  `POST /config?scope=fleet`, and the eight ids the robot received matched the eight `GET /schedule`
  explains, one activity dropped for bedtime, the request pinned, every entry carrying a *why* line —
  now a hermetic regression suite (`sim/tests/test_schedule_sil_e2e.py`, 13 tests, mutation-checked).
  The **gateway voice** was proven on the assembled appliance in one turn (`MOXIE_APP=llm` +
  `MOXIE_VOICE_BASE_URL`): `[run] server voice enabled: openai-voice (standby: tone)`, brain →
  `"Hello Sam! I'm so glad you're here."`, robot → `🔊 spoke 138472 B @ 22050 Hz (~3.14s)` at spectral
  flatness `5.09e-02` against the tone's `3.4e-14` — `sim/tests/test_live_gateway_turn_e2e.py`, budgeted
  at one completion + one `/audio/speech`. **Three honest gaps this pass did not fix:** (a) the
  `ToneSynthesizer` placeholder emits **22050 Hz mono, exactly like the gateway WAV**, so sample rate can
  never be the proof a real voice spoke — only spectral flatness can, and any future test that checks the
  rate alone is vacuous; (b) `[tool.setuptools.package-data]` maps `moxie_sdk = ["*.json"]` only, so a
  data file added under `moxie_sdk/apps/` or `moxie_sdk/content/` would be dropped from the wheel in
  silence — now *detected* by `sim/tests/test_package_contents.py`, not yet prevented by a wider glob;
  (c) the recommender's coverage term (−1000 per airing) outweighs its affinity term (10–200), so a module
  a child finishes every time is demoted below one they have never seen — variety by design, but it means
  "what they love" cannot currently come back the same week.

- **Integration evidence (2026-09-02, post-merges).** The whole stack exercised on `dev` after PRs
  #43–#48 through the real built backend. Green: `sim/run_smoke.sh` (`✅ SIL round-trip OK`, 🔊 50934 B
  @ 22050 Hz), `--telehealth` (`✅ telehealth SIL OK — enable→start→speak→interrupt→end`),
  `sim/run_scenarios.sh` (`✅ SIL scenarios OK — 2/2`), `sim/run_acl_proof.sh` (18/18 against real
  `eclipse-mosquitto:2.0.20`), `python -m build` (0.7.0 sdist+wheel, the wheel carrying the new
  `moxie_sdk/face_assets.json`). **Live, 5 gateway calls:** one child turn on all three gateway paths —
  ears `openai-stt (stt-whisper)` heard `"Hi Moxie, can you tell me a joke about a robot?"` at overlap
  **1.00**, brain `graphling-medium` answered, voice `openai-voice` spoke 213852 B @ 22050 Hz — and one
  telehealth operator line spoken by `piper-amy` at spectral flatness **2.32e-02** (tone ≈ 3e-12).
  **Three of the four gaps this pass found are now closed** (`feat/sim-robot-fidelity`, 2026-09-02): the
  browser SIM acts on `response_actions`, publishes `client-service-activity-log` with the same envelope
  keys in the same order as `sim/virtual_moxie.py` (held from **both** ends against
  `sim/tests/goldens/robot_to_cloud_activity.json`, so neither client can drift without a test going red),
  and `WebhookApp` strips its own tags on the `Reply` boundary. The only remaining divergence is the
  golden's `identity_keys` — *which* robot is speaking and *when* — which is the point, not a gap. Gap (d)
  stands. Original wording:
  **Four gaps that pass found:** (a) *no SIM client acts on `response_actions`* —
  the actions now provably reach the robot (`sim/tests/test_e2e_actions_to_robot.py`), but neither
  `sim/virtual_moxie.py` nor `sim/web/bridge.js` reads the field, so nothing launches a module or exits on
  a cloud action; (b) *the browser SIM never publishes `client-service-activity-log` at all* — no
  `subtopic` appears in `bridge.js`, so it reports no telehealth `RobotState` (§3.9's robot→cloud half),
  no `query`, no `mentor_behaviors`, and the two SIM clients are therefore **not** interchangeable in the
  robot→cloud direction that DoD criterion 4 claims; (c) *`WebhookApp` does not strip own-tags* — an
  external brain that writes `<launch:…>` in its `text` has it spoken aloud, because tag parsing lives in
  `LLMApp`/`ContentApp` and not on the `Reply` boundary; (d) *the brain took 33.8 s* on the live turn
  (filler + streaming cover it, but the gateway's own latency remains the ceiling on criterion 1).
- **Integration evidence (2026-09-02, merged state) — the four slices of the 22:40 window exercised
  *together* against real infrastructure, and three gaps that only the combination shows.** Verbatim:
  SIL smoke ✅ (`state→config(paired)→remote-chat→reply`, 50934 B @ 22050 Hz), telehealth SIL ✅
  (enable→start→speak→interrupt→end), scenarios **2/2**, broker ACL **18/18** against the real
  `eclipse-mosquitto:2.0.20`, package `moxie_cloud_sdk-0.7.0.{tar.gz,whl}` with `moxie_sdk/telemetry.py`
  and both `moxie_sdk/*.json` present and `import moxie_sdk` + `from moxie_sdk.content.render import
  render_prompt` green in a venv holding only `paho-mqtt`. **The sandbox (PR #56) breaks no legitimate
  prompt** — every Jinja-bearing string in `mqtt/content_modules/*.json` renders byte-identically
  through the sandbox and through the pre-sandbox environment it replaced, under a populated and an
  empty memory context, and `render.BLOCKED` never moves for our own content
  (`sim/tests/test_render_sandbox_parity.py`; a live turn on the shipped module answered *"I love dogs
  because they are so friendly and always want to play!"* with the rendered prompt reaching the model).
  **Durable telemetry survives a real process restart** — three packets → `mqtt/run.py` killed → a new
  supervisor over the same `MOXIE_DATA_DIR` answered `{"ok":true,"connected":false,"policy":"NO_MEDIA",
  "persisted":true,"totals":{"total":3,"days_kept":1}}` with the robot never reconnected, and a
  reconnecting robot then showed `telemetry_count: 3` (the hydration path); the `LoggingPolicy` gate
  holds on the running supervisor for all three values with the verdict read off **disk** (NO_DATA wrote
  no file at all, NO_MEDIA kept the envelope and withheld the payload, FULL round-tripped it). The three
  console endpoints answered honestly through that same stack: `wakeup` published `{"command":"wakeup"}`
  on `/devices/<id>/commands/wakeup` (asserted by a real MQTT subscriber), `reboot` returned **501** with
  its `power-and-system-events.md` citation and published nothing, `ota_status` returned the robot's own
  `24.10.803` with `status:"unknown"`. **What the combination newly exposes:** (a) *the shipped container
  had no jinja2* (`mqtt/requirements.txt`), so the renderer **every** deployment ran was the
  dependency-free fallback — fine for every module we ship (`{{ dotted.path }}` only, now pinned) but it
  passed `{% if %}` through **verbatim into the system prompt** while `content-module-contract.md`:42
  advertises that form to pack authors. **Closed 2026-09-02, both halves.** The container now installs
  `jinja2>=3.0` (+437 KB: 57,306,999 → 57,753,780 bytes, +0.8%; safe *only* because PR #56 made the
  renderer a `SandboxedEnvironment`, so the two changes are a package) and the documented form was
  rendered in the **real image** to prove it, both branches. And the fallback no longer passes anything
  through: it *evaluates* `{% if dotted.path %}` byte-identically to jinja2 and **removes** what it
  cannot evaluate, counting each removal in `render.STRIPPED` — `BLOCKED`'s sibling, because the
  degradation is invisible in the output by design. `pyproject.toml` deliberately keeps jinja2 in the
  `content` extra so the SDK still imports with nothing heavy, and that split is now pinned in both
  directions (`test_render_container_deps.py`); the fallback's guarantee is a **regex over the output**
  across ~1.8k nested construct pairs (`test_render_fallback.py`), whose differential half caught a
  second, quieter bug on the way in: `{% if true %}` was treating a jinja2 *literal* as a name and
  taking the wrong branch. (b) *`sim/run_acl_proof.sh` is run by no
  CI tier* — the only thing holding the P0 pattern ACLs honest runs when somebody types it; it needs
  docker, so it belongs beside `run_compose_smoke.sh` in the deep tier. (c) *`sim/run_smoke.sh
  --telehealth` is run by no tier either* — the file is named twice, but only in its default mode, so
  🎭 puppet mode's only end-to-end proof is manual. All three, plus the two `.mjs` suites nobody runs,
  are now fenced by `sim/tests/test_ci_test_coverage.py` as a two-directional ratchet whose lists can
  only shrink.
- **~~the supervisor hard-codes *our* gateway as its default~~ — CLOSED 2026-09-03, with a second defect found underneath it.** `mqtt/config.py`:81 defaulted `MOXIE_LLM_BASE_URL` to the maintainer's gateway, so a stranger cloning this public repo got a supervisor pointed at someone else's server — and it never worked anyway: that endpoint refuses unauthenticated calls, so the child heard the "my brain got fuzzy" fallback forever with no line anywhere saying why. The default is now **empty**, and `MOXIE_APP=llm`/`content` **exit at assembly** naming the variable and offering two loopback examples (`config.require_llm_base_url`); `MOXIE_APP=echo` needs no brain, which is what `run_smoke.sh` and `sim/compose-smoke.env` already used and what `helpers_stack.Supervisor` now defaults to. The same default lived in **both compose files and `.env.example`** — fixing only the Python would have left `docker compose up` pointed at the same host — so the guard is the class, not the line: `sim/tests/test_no_deployment_defaults.py` forbids any deployment hostname in shipped Python (AST literals, docstrings exempt), shipped JS (comments stripped) and shipped configuration **values**, with a negative control per scanner and an assertion that it scanned anything at all. **The second defect, found while fixing the first:** `config._load_env` read `mqtt/.env` with `setdefault` at import, so every test that simulated an unset variable by deleting it and reloading had it **refilled from the file** — 12 tests across `test_assemble.py`, `test_stt_gateway.py` and `test_voice_settings.py` asserted nothing on any developer's machine and passed everywhere the git-ignored file is absent, which is exactly where CI and every worktree run (playbook rule 20). `MOXIE_SKIP_DOTENV` (and `MOXIE_DOTENV` for a path) are the opt-out, the four `_fresh_config` helpers use it, and `test_config_dotenv.py` pins it — including the acceptance case that only *exists* in a main checkout and skips loudly elsewhere. It had also been hiding the first defect's blast radius: `test_sil_durable_telemetry` passed only because the dotenv refilled the endpoint its supervisor needed. **Honest ceiling:** `docker compose up` with no `.env` at all no longer starts the brain — it exits naming one variable. That path never actually talked; it now says so in one line instead of on every turn.
- **Integration evidence (2026-09-03) — the container renderer is proven *in the image*, and the
  clock-flake class is now fenced instead of hunted.** Four things had landed on `dev` within an hour
  and none had been exercised as a whole. Verdicts, in order. (a) **The container change is real where it
  matters.** PR #62's own proof was a build-mode compose smoke; this pass drove a **conditional-bearing
  prompt through `ContentApp.respond()` inside the running supervisor container** (`docker exec` into
  `moxie-smoke-*-supervisor-1`): jinja2 3.1.6, `{% if presence.face_present %}` taking both branches
  correctly, a `{% for %}…{% else %}` too, `render.STRIPPED == 0` proving the *real* renderer ran rather
  than the fallback, and `render.BLOCKED == 1` proving the sandbox is still the environment doing it.
  Both compose modes are green (`build` and `images`) and **neither pulled a published image** — images
  mode builds the three images from this clone, tags them with the published names and runs with
  `pull_policy: never`, so it proves the wiring, not the registry. (b) **The whole creds-free stack is
  green**: SIL round-trip, 🎭 telehealth, 2/2 scenarios, 18/18 broker-ACL checks against a real
  `eclipse-mosquitto:2.0.20`. (c) **One more clock flake of the PR #60/#63 family was found before it
  bit** — `test_telemetry_runtime.TODAY = int(time.time())` with packets stamped `TODAY - 30` files them
  under yesterday's roll-up row for ~30 s after local midnight — plus two narrower ones (a
  `date.today()` read twice either side of midnight, and two independent clock reads that had to agree
  about one instant). All fixed, and the whole class is now fenced: `sim/tests/test_clock_dependence.py`
  lists **every** wall-clock read in the test tree by `file::scope` with a verdict and a reason, and
  fails three ways — an unlisted read, a listed read that no longer exists, and a listed row whose
  constructs changed. (d) **The live turn still works on the merged tree**: one real child utterance
  through `mqtt/run.py` with the gateway brain *and* gateway voice, answered `"Hello Sam! I'm so happy
  to see you."` in 3.30 s of `piper-amy` audio at flatness 7.1e-02 — real speech by
  `helpers_audio.is_real_speech`, whose floor this pass de-duplicated so two copies cannot drift.
  **Honest gaps this pass leaves:** the container proof drives the *renderer* end to end inside the
  image but the composed stack still runs `MOXIE_APP=echo`, so no *container* turn has gone through the
  gateway brain with a templated pack; the clock ledger covers `sim/tests/*.py` and `sim/*.mjs` only —
  product code and `functions/` are unswept; and monotonic-clock load flakiness (playbook rule 11's
  disease) is deliberately out of that guard's scope and still unfenced.

## DoD progress (audited 2026-09-03 07:40 PDT, at v0.7.0) — **5/6 🟢 · overall ≈ 93%** (done = all six 🟢)

> **Criterion 6 is green, and it was earned in the place it used to be false.** The day the merge gate was a
> `grep` is fixed and the fix has since caught a genuine red; the three flake classes are fenced by ratchets that
> can only shrink; and the acceptance property the config slice existed to create is now verified where it used
> to fail silently — the hermetic suite gives an **identical 4020 passed / 16 skipped with and without a real
> `mqtt/.env` present**, which is the exact shape in which twelve tests once asserted nothing. The one-command
> stack passes in **both** compose modes, the wheel carries its data files, and a live turn answered through the
> real gateway brain **and** the real gateway voice (`piper-amy`, 141 kB @ 22050 Hz, flatness 7.35e-02 — speech,
> not a tone). Criterion 1 stays honestly amber: its ceiling is a physical robot we have never had, and the
> hosted mic→gateway join still has no human recording through it. **Nothing here has been verified against a
> real Cloudflare deployment** — that is owner-blocked on three variables and named in Known gaps, not hidden.

| # | Criterion | Status | Notes |
|--:|---|---|---|
| 1 | Talk end-to-end (mic→STT→brain→markup→TTS→SIM/robot) | 🟡 ~90% | **Every link is built and live-proven with real speech through the real runtime** (PR #12): Piper "child" audio → zmqSTT protobuf frames → faster-whisper → brain (gateway, live) → spec `RemoteChatResponse` → Piper Amy `CloudTTSResponse` → re-heard at overlap 1.00 (and, since 2026-09-02, the same round trip through the **gateway voice** at overlap 1.00 — `piper-amy`, 22050 Hz, 1.69 s); the browser SIM decodes and **plays** it with mouth animation (PR #11); markup/actions reach the client. Remaining: the same loop on a **physical Moxie** (needs the operator's robot; the SIM stands in). Brain latency is no longer silence *or* a wait: a slow turn speaks a filler inside `MOXIE_BRAIN_BUDGET_S` (live: 3.0 s / 17.9 s) and the answer itself now **streams** a sentence at a time (live: first words at 1.52 s, whole answer at 4.38 s), with the filler timer re-arming per chunk. Remaining honesty: no capture proves a *physical* robot plays chunk 2 of an `event_id` (see Known gaps) |
| 2 | Data-driven content | 🟢 | M2 engine + ContentApp, e2e-tested |
| 3 | Cloud management (console + config/telemetry) | 🟢 | RobotCloudConfig + RobotStatus + status snapshot + Packet telemetry + LoggingPolicy gate 🟢. The console surfaces **live state** (`/local/fleet`), **edits config** (Settings form → `POST /local/robots/{id}/config` → runtime `POST /config` → `update_config` re-pushes `RobotCloudConfig`), and shows **telemetry insights** (`summarize_events` → runtime `GET /telemetry` → `GET /local/robots/{id}/telemetry` → the 📈 Insights panel) — all three live-verified against a real broker. It also **browses and erases long-term memory** (`normalize_memory` → runtime `GET`/`DELETE /memory` → `GET /local/robots/{id}/memory` + `DELETE …/memory[/{namespace}]` → the 🧠 What Moxie remembers card), so BEYOND #4's floor is a parent-facing screen rather than `curl` — live-verified: seeded facts read back per activity with date/module provenance, a namespace erase confirmed on disk and in the next read. **Today's plan (2026-09-02, BEYOND #7):** the 📅 schedule card reads `GET /local/robots/{id}/schedule` → runtime `GET /schedule`, so the recommender's *"why this activity today"* line is a screen rather than a `curl` — one row per entry the robot was served, `—` for untimed fixtures, and a footer naming the constraints (bedtime window and the slots it cost, pinned parent requests, and that telemetry carries no module signal). Read-only: the day is changed from ⚙️ Settings. **Durable telemetry + honest buttons (2026-09-02) — the two deductions below are closed.** Telemetry is persisted per robot as a 500-envelope ring plus 35 days of daily roll-ups, `LoggingPolicy`-filtered **on the way to disk** as well as off the robot (`NO_DATA` writes nothing; `NO_MEDIA`, the default, withholds every opaque `event_data`), hydrated on first touch so `telemetry_count`, the insights view and the planner all see history — live-proven by sending three packets to one supervisor and reading them back through the **next** one with the robot not reconnected, and the 📈 card now renders a zero-filled week with its real retention window and lifetime total. And the console stopped reporting success for nothing: `wakeup` publishes the recovered `{"command":"wakeup"}` on `/devices/{id}/commands/wakeup` and reports *sent*, never *awake* (the corpus has no acknowledgement); `reboot` answers **501** with its citation, because no cloud→robot reboot command is recovered, and the button is shown unavailable rather than flashing "Sent!"; `ota_status` reports the robot's own `robot_firmware_version` + `ota_reboot_required` and **never** the old hard-coded `"up_to_date"` — this appliance serves no `api/ota` and says so. Caveats: memory erase or correct any single remembered line (PR #33). **Puppet mode (2026-09-02, ADOPT #7):** the 🎭 Be Moxie card drives `POST /local/robots/{id}/telehealth` → runtime `POST /telehealth` → `commands/telehealth`, so a remote grown-up speaks through the robot with a mood they picked; SIL-verified end to end (`sim/run_smoke.sh --telehealth`), never on hardware| **Both 2026-09-02 deductions are closed — back to 🟢** (the audit's ranked #2 slice, shipped the same evening): telemetry is durable and survives a restart, and no console endpoint reports success for something it did not do. **What is honestly still missing** (named, not hidden): the durable store is still JSON files, not a database (ADOPT #8 stays 🟡 — no schema, no migrations, no concurrent writers); BEYOND #5's interesting half — *sessions, activity mix, mood trend, "what did we talk about this week"* — needs a vocabulary this history cannot express, because `Packet.event_name` is a free string and our corpus recovers no module-scoped events, so it must come from `mentor_behaviors` or from events we emit ourselves; typed `event_data` decoding (`LogDevice`/`LogUser`) is undone; and, as everywhere else in this table, **no physical robot has ever been managed by this console** — every verification is against the SIL/virtual robot through a real broker.
| 4 | Interchangeable SIM/robot clients | 🟢 | backend is client-agnostic; SIM round-trips the real protocol | **Earned 2026-09-02 (PR #52):** until then the browser SIM read no `response_actions` and published no `client-service-activity-log` at all, so this row was true only cloud→robot. Both clients now publish the same envelopes, held from **both ends** against `sim/tests/goldens/robot_to_cloud_activity.json`; the one allowed divergence is *which* robot and *when*.
| 5 | One-command stack | 🟢 | `docker compose up` (repo root) = broker + supervisor + parent console, one `.env`, healthchecks + named volumes. **Proven** by [`sim/run_compose_smoke.sh`](../../sim/run_compose_smoke.sh): build → health → `virtual_moxie --expect-tts` round-trip through the composed broker → the robot visible in the console's `/local/fleet` → `down -v`; shape asserted hermetically by `sim/tests/test_compose.py`. Guide: [`../guides/one-command-stack.md`](../guides/one-command-stack.md). Prebuilt path (2026-09-02): [`docker-compose.images.yml`](../../docker-compose.images.yml) is self-contained (broker config inlined, no repo file referenced), so the documented install is *download one file + `docker compose up`*; the release workflow publishes the three multi-arch images to GHCR on every `v*` tag, and `MOXIE_SMOKE_MODE=images sim/run_compose_smoke.sh` runs the full round-trip against it with `pull_policy: never`. Caveats (documented, not hidden): the images **publish on tag and nothing has been tagged yet**, so the registry pull is verified at v0.6.0 (all three multi-arch publishes green); the `content`/`llm` brains still need a gateway key to say anything real (keyless → the "brain got fuzzy" fallback, which is why the smoke uses `echo`); and the `voice`/`stt` profiles each need one `.env` line + `up --build` on the *clone* path — a prebuilt supervisor cannot grow those wheels |
| 6 | Green + live-tested | 🟢 | Three-tier CI green incl. the compose-stack deep job; hermetic suite **4020 passed / 16 skipped**, and — the property that matters — **identical with and without a real `mqtt/.env` present** (2026-09-03), the shape in which twelve tests previously asserted nothing. **Live-proven against real infra:** LLM turn, action tags 3/3, content module e2e, console↔runtime round-trip, real speech end to end (Piper→whisper 1.00; gateway voice→whisper 1.00), and a full runtime turn through the gateway brain + gateway voice re-verified 2026-09-03 (`piper-amy`, 141544 B @ 22050 Hz, 3.21 s, flatness 7.35e-02). **One-command stack green in BOTH compose modes** (build and images, each a full robot round-trip with TTS audio through the composed supervisor); `python -m build` produces a wheel carrying every `moxie_sdk/*.json`. The deploy-only failure class is now caught locally: the guard forbidding `.json` imports and import attributes under `functions/` was **mutation-tested with 9 mutants** (both attribute forms, bare/dynamic/`require` json imports, a stray `.json` file, and a file nothing imports) — all caught, and it strips comments so its own documentation cannot trip it. | **Deduction (2026-09-02), now closed:** for one day the merge gate was `gh pr checks | grep`, which read a not-yet-listed job as green — PRs #43–#48 merged ~2 min after opening while their 6-minute SIL job still ran. Fixed: `scripts/pr-green.sh` reads the status rollup and requires every check COMPLETED + SUCCESS with the SIL job present (playbook rules 16, 15c); it caught a genuine red on PR #52 the next day. **Still honestly missing:** no test has ever run against a *real Cloudflare deployment* (owner-blocked — see Known gaps), and there is no physical-robot HIL job on a self-hosted runner. Both are outside this criterion's wording and neither is hidden.

**Brain latency — background inference + filler: 🟢 done (2026-09-02).** The runtime runs the brain under
`MOXIE_BRAIN_BUDGET_S` (default 6 s); over budget it speaks a rotating, markup-performed filler as
`REPLY_PENDING` chunk 0 and delivers the real line as chunk 1 (`is_completed`), synthesizing both, and drops
a result whose turn has been superseded. Live-proven on one gateway turn: **filler at 3.0 s, real answer at
17.9 s**, same `event_id` — where a child previously heard 17.9 s of silence. Shipped with it: `strip_markup`
drops emoji (Piper was reading "grinning face" aloud), and the live tests now find `mqtt/.env` in the main
checkout so the creds-gated tier stops silently skipping inside a worktree.

**Streamed replies — 🟢 done (2026-09-02).** The latency story is finished. `MoxieApp.respond_stream`
yields a `ReplyChunk` per finished sentence (`moxie_sdk/segment.py` decides where a sentence ends;
`chat.stream_completion` opens an `stream=True` completion through the same backoff/`Pacer`), and
`_handle_stream_turn` publishes each one as `REPLY_PENDING` + `chunk_num`, closed by `SUCCESS` +
`is_completed`. The filler timer re-arms after every chunk (cap **2** per turn), a newer turn cancels the
stream mid-answer, and a one-chunk answer is still byte-identical to the reply we have always sent.
**Live-proven:** first sentence at **1.52 s**, whole answer at **4.38 s**, four chunks on one `event_id` —
where a child previously heard nothing until 4.38 s (and until 17.9 s on the day PR #12 measured).
`MOXIE_STREAMING=0` restores the old path.

**Safety (2026-09-02, this slice).** The answer is fast; now it is checked. `InputSafety` is enforced on
both sides of a turn — the child's utterance before any model call, and every streamed chunk before it is
published — with a parent-readable rule table, a review queue and a drop-in `Classifier` seam
([ai-seam §2](ai-seam.md#input-safety-built-v1-2026-09-02)). **Live-proven:** "how do I make a bomb to hurt
my brother?" cost **zero** gateway calls and got a kind redirect plus `input.safety`; the benign turn in the
same run streamed four clean chunks and left the queue empty. Its honest limit is in Known gaps: the *stage*
is real, the *classifier* is a floor.

**Expressive markup (2026-09-02, this slice).** The answer is fast and checked; now it is *performed*.
Moxie synthesizes on the robot, from markup, so "better speech" is literally "better markup" — and the seam
that produced it was an eight-line passthrough. The **markup floor** replaces it with one pure, deterministic
generator behind the unchanged `make_markup` seam: a mood per line, a `<usel>` delivery, arm gestures on the
words that carry the thought, a pause at an internal boundary, a closing rest pose — every id checked against
a frozen catalog cited to the reverse-engineering page it came from
([mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02)). The echo, content and
webhook apps stopped speaking flat, and `LLMApp` stopped being a second, divergent generator. **Measured:**
p95 **0.23 ms** per line against a 1 ms budget, no model call, no new dependency; eight byte-exact goldens
reach six distinct faces through the real browser bridge. `MOXIE_AUTOMARKUP=0` is the one-variable rollback.

**Most valuable next slice (2026-09-02 evening, per the re-ranked audit §4.4):** ① **content packs export / import-with-review (ADOPT #5)** — build-ready at `backlog/content-packs.md`, P0 alone is shippable; ② **durable telemetry → real insights** (telemetry is RAM-only today, so the 📈 card cannot show a week — DoD criterion 3's gap); ③ sandboxed content extensions; ④ production hardening (blocking `connect()` with no reconnect backoff; no robot has been on our broker for a week); ⑤ any-brain-per-child. Small real bug ahead of all of them: the console's MQTT `wakeup` publishes nothing and reports success — a one-slice follow-up right after content packs (same status-HTTP region, so not concurrent).

## TTS strategy (2026-09-01)

- **Default server voice = Piper (Amy)** — local, free, no rate limits. **Built:** `PiperSynthesizer` in
  the SDK (`moxie_sdk.tts`, offline, Amy default), selected by `MOXIE_PIPER_MODEL` when no voice server is
  set; install with `pip install 'moxie-cloud-sdk[tts]'`. Needs a downloaded Piper `.onnx` model to speak.
- **Gateway TTS is possible now-ish:** `gateway.graphlings.net/v1/audio/speech` route EXISTS (returns 400
  "invalid model", not 404) → LiteLLM supports the TTS payload; it just needs a TTS model registered in the
  gateway config. Then set `MOXIE_VOICE_BASE_URL=<gateway>/v1` + a model name and `OpenAIVoiceSynthesizer`
  (already backoff+paced) drives it through the **same key + same limits**. Piper stays the default; the
  gateway is the alt.

## Definition of done — the complete end-to-end system

The build is DONE when all of the below hold together, not milestone-by-milestone:

1. **A child can talk to Moxie end to end** — mic → STT → brain (our LiteLLM gateway,
   `gateway.graphlings.net`) → behavior markup + text → TTS/voice → the SIM (and a real robot) speaks,
   emotes, and moves. Proven by a live scenario, not a mock.
2. **Data-driven content** — activities are authored modules (conversations/globals/schedules) the brain runs.
3. **Cloud management** — the parent console (server/) shows robot state + edits config (bedtime/volume/
   wake/OTA) via `RobotCloudConfig`; telemetry/insights flow up; LoggingPolicy honored.
4. **Interchangeable clients** — the SIM and a re-homed robot connect to the same backend identically.
5. **One command** — `docker compose up` runs broker + supervisor + brain + STT/TTS; config via `.env`.
6. **Green + tested** — every feature has a test; CI green; a live end-to-end test passes against the
   gateway (and the voice server) when keys are present (skips cleanly in CI).

**Live testing:** the brain uses our LiteLLM gateway `https://gateway.graphlings.net/v1` (key in a
git-ignored `mqtt/.env`, never committed); voice via `MOXIE_VOICE_BASE_URL` when available.
`sim/tests/test_live_gateway.py` exercises a real turn when the key is set.

## Working rules (build loops)

Per [`running-layered-session-loops`](../../.claude/skills/running-layered-session-loops/SKILL.md):
smallest shippable slice; a test with every feature; **verify before commit** (local guards + keep CI
green — a build that reddens CI is not done); honesty over green; don't manufacture. **Gateway resilience:**
the AI seam backs off + paces on rate-limits (429/5xx) instead of failing — a busy gateway slows us down,
the child hears a gentle "one moment", the operator sees a clean status (`moxie_sdk/chat.py`). Clean-room: build
only from these specs + `docs/reverse-engineering/`, never the vendor app. Never commit keys/endpoints
(git-ignored `.env` only).

---
📖 [Architecture index](README.md) · [Build contracts](overview.md) · [Roadmap](../../ROADMAP.md)
