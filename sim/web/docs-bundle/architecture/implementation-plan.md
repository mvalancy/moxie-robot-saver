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
| AI seam — **which** brain, per child | [ai-seam](ai-seam.md) §2 · [brief](backlog/brain-picker.md) | 🟢 **any brain, hot-swappable, per child — P0 built 2026-09-03** (audit BEYOND #3, ranked #7). The seam said any AI could wear the shell; the appliance chose one brain, once, globally, from `MOXIE_APP`, and returned the LLM app for **anything it did not recognise**. Now: a **closed positive registry** (`moxie_sdk/brains.py` — `llm`/`content`/`webhook`/`echo`, the idiom of `packs.py::SPEC` and `ext.py::OPS`), where a name resolves to a builder and an unknown one is refused naming the four (`MOXIE_APP=gpt5` used to boot the free-form companion); the selection is `brain` as an **ordinary key in the ordinary config layers** — `defaults ⊕ fleet ⊕ per-robot`, ADOPT #6 — so `POST /config?scope=fleet` and `POST /config?device_id=` already set it, there is no second store and no second layering (a test pins `resolve_brain` against `merge_config_layers` itself), and `SERVER_ONLY_KEYS` keeps it out of the document pushed to the robot; the swap is `MoxieRuntime.app_for(device_id)` resolved **once at the top of a turn**, so a parent's Save lands on the child's next turn and a turn already in flight finishes with the brain that heard the question (`voice_update`/`reload_content`'s rule) and a brain that will not build keeps the appliance talking, saying so once; and an explicit **`MOXIE_APP` pins** the appliance's brain over any per-child pick (PR #77's owner rule) — read from the RAW environment, because `config.MOXIE_APP` already reads as `llm` where nobody said anything, so pinning the resolved value would have locked every unconfigured box out of its own picker. `MOXIE_APP=any` hands the choice back. `GET`/`POST /brain` serve the **🧠 console card** — a brain for this robot or a house rule, each robot's row naming *which layer decided*, and the pin's sentence first when there is one. **126 tests** + `sim/tools/brain_mutation_check.py` (22 guards deleted, 22 red). **Honest gaps:** no browser harness clicks the card (the ceiling every card here has); our own `docker-compose.yml` `${MOXIE_APP:-content}` pins a default compose deployment (told on the card, not hidden); no per-child *persona*, which is BEYOND #3's other half | `mqtt/moxie_sdk/brains.py` + `config.py::BRAIN_BUILDERS`/`BrainEngines` + `moxie_runtime.py::app_for`/`brain_view` + `server/moxie_server/fleet.py::normalize_brain` + `server/static/` (the 🧠 card) + `sim/tests/test_brain{s,_runtime,_console}.py` |
| AI seam — presence (vision events) | [ai-seam](ai-seam.md) §2 · [vision](vision.md) §7 · [mqtt §4.7](mqtt-and-conversation.md#47-vision-events-and-whether-the-cloud-may-speak-first-built-v1-2026-09-02) | 🟡 **built, unproven against hardware** (audit BEYOND #9): the runtime subscribes the robot to its own vision events (`EventSubscription.active[]`, once per `(device, module_id)`) and routes `eb-found-face`/`eb-lost-target`/`eb-qr-event`/`eb-dr-event`/`eb-br-event` — which arrive as the `speech` of a `RemoteChatRequest`, not a topic of their own — into a pure presence state machine with hysteresis (a face blinking at the frame edge yields **one** `arrived` + **one** `left`, not twenty). `Turn.presence` carries a resolved snapshot into the prompt, and its `line` is **empty on most turns by design**. An `arrived` after ≥ `MOXIE_GREET_AFTER_S` (default 300 s, 0 = off) earns **one** unprompted hello — answered on the arrival event's own `event_id` (an unsolicited publish is *not* established as legal — see vision.md §7.4), rate-limited once per absence, never over a turn, never unpermitted, never in bedtime hours. **Honest ceiling:** no physical robot has ever sent us one of these events; the whole path is exercised by the SIL robot (`--face-event`) and the browser SIM, which proves consistency, not hardware truth | `mqtt/moxie_sdk/presence.py` + `moxie_runtime.py::_on_vision_turn`/`_greeting_for` + `wire.py` + `apps/llm_app.py::_system` + `sim/virtual_moxie.py` + `sim/web/bridge.js` |
| AI seam — expressive markup | [ai-seam](ai-seam.md) §2 · [mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02) | 🟢 the **markup floor** (v1, 2026-09-02) *and* the **behavior planner** (P1, 2026-09-03). The floor performs every reply that does not bring its own markup — one pure, deterministic, stdlib generator (`annotate`), a mood from `ePlaybackMood` 0-10, a `<usel>` delivery, arm gestures on the carrying words, a `<break>` at an internal boundary, a closing `Gesture_None`, **p95 0.23 ms/line**. The planner now sits above it behind the same seam: it scores the line's **dialog act** (all 22 `RemoteDialog.DialogAct`s) and stages a validated `Performance` that exactly **one** `render()` turns into markup — a question tilts and holds its gaze, an apology stops gesturing, praise celebrates, an "mm-hm" moves nothing. Contract changes C1–C7 landed, so `mood`/`mood_intensity`/`dialog_act`/`emotion`/`signals` are filled on **100 % of published turns including streamed chunks** (plumbed and empty since PR #17), and `POST /local/robots/{id}/preview` rehearses a line on the SIM before a child hears it — an ordinary `remote_chat`, no SIM-specific API. Every id, rule-chosen or model-suggested, passes one `validate()` against the frozen `vocab.py` catalog: **0 unknown ids over a 300-line corpus**. Deterministic, no model call, p95 0.25–0.56 ms; any failure (exception, decline, or an 8 ms budget blown 3x) degrades to the floor with an identical wire shape. 8 + 22 byte-exact goldens, 277 + 124 hermetic cases; the 22 acts reach 8 distinct faces through the real browser bridge with a contact sheet as a CI artifact. `MOXIE_EXPRESSIVE=planner\|floor\|off`, `MOXIE_AUTOMARKUP=0` | `mqtt/moxie_sdk/performance.py` + `automarkup.py` + `vocab.py` + `supervisor/markup.py` + `moxie_runtime.py::_stage`/`preview` + `sim/tests/test_performance.py` + `sim/test_performance_render.mjs` |
| AI seam — input safety | [ai-seam](ai-seam.md) §2 · [mqtt §4.5](mqtt-and-conversation.md#safety-on-the-wire-inputsafety-inputsafety) | 🟢 `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` **enforced**, not just specified: assessed pre-inference (a hard block never reaches a model) and **per streamed chunk** before publication (blocked chunk never goes out; a safe line closes the sequence with `SUCCESS`+`is_completed`; the stream is cancelled). 8 categories with a per-side block/flag policy in a parent-readable `safety_rules.json`, normalization + false-positive guards, a `Classifier` protocol for a drop-in local model, and a parent review queue (`GET|POST /safety` → console 🛡️ panel, `NO_DATA` = counts only). **Live-proven:** an unsafe request cost **0 gateway calls** and got a redirect + `input.safety`; a benign turn in the same run streamed 4 clean chunks | `mqtt/moxie_sdk/safety.py` + `safety_rules.json` + `moxie_runtime.py::_safety_gate_input`/`_handle_stream_turn` + `server/moxie_server/fleet.py` |
| AI seam — STT in | [ai-seam](ai-seam.md) §1 | 🟢 seam + runtime-wired + **real zmqSTTRequest protobuf decode** (dep-free) + JSON bridge, e2e-tested; **2 first-class engines — local faster-whisper (offline, no key) · gateway STT (`OpenAITranscriber`, live 2026-09-02)** — chosen by `MOXIE_STT=auto\|gateway\|whisper\|off` **or from the console's 🎚️ Listening dropdown (2026-09-02), which lists the gateway's STT models discovered live plus the installed local sizes and swaps the engine with no restart — and an explicit `MOXIE_STT` PINS the engine so the dropdown cannot overrule the operator (2026-09-03)**, the headerless mic PCM wrapped in an in-memory WAV **at the bus's true 16 kHz**, and a `FallbackTranscriber` that latches to local whisper (or an honest `""`) instead of raising mid-sentence. **Live-proven:** gateway TTS → gateway STT at word overlap **1.00** at both 22050 Hz and 16 kHz, and one child utterance through the real runtime on gateway ears + brain + voice ([guide](../guides/litellm-stt-setup.md)) | `mqtt/moxie_sdk/stt.py` + `moxie_runtime.py` |
| AI seam — TTS out (for SIM) | [ai-seam](ai-seam.md) §3 · [sim](sim-as-a-client.md) | 🟢 seam + runtime-wired + **3 backends: built-in tone (zero-dep) · Piper (offline, Amy) · OpenAI-voice (gateway)**, choosable **from the console's 🎚️ Speech dropdown (2026-09-02)** — live gateway discovery + installed local voices, default `piper-amy`, persisted in `fleet/voice.json`, a Test button, explicit-local-wins, and an explicit `MOXIE_TTS` pins the engine (2026-09-03); **full audio round-trip proven through a real broker** (SIL smoke `--expect-tts`); **the gateway voice is live-proven (2026-09-02)** — `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL=piper-amy` is the whole switch, its WAV is unwrapped to the header's true 22050 Hz, Whisper reads the audio back at overlap **1.00**, `piper-ryan` swaps the voice, and a gateway failure downgrades to Piper/tone instead of silence ([guide](../guides/litellm-tts-setup.md)) | `mqtt/moxie_sdk/tts.py` + `moxie_runtime.py` |
| Content-module engine | [content-module](content-module-contract.md) | 🟢 engine + ContentApp, runtime-selectable (MOXIE_APP=content) + example modules, e2e-tested through the runtime. **Memory built (2026-09-02):** `volley.persist_data` (durable, module-namespaced, bounded, `NO_DATA`-gated) + `session.summarize()` at end-of-conversation, served to a parent over `/memory`; ships as `content_modules/memory_chat.json`. **Per-item control (2026-09-02):** every remembered thing carries a stable id and its own provenance, so a parent can **erase or correct one line** (`DELETE …&item=` / `POST {"edit":…}` → the console's ✕/✏️), a correction is pinned, and unused items age out after `MOXIE_MEMORY_MAX_AGE_DAYS` (90). action-plumbing still deferred (`code` is never executed, and never will be — see 🧬 below). **Adaptive day plan (2026-09-02):** `schedules[]` is no longer expanded by a `(device_id, day)` rotation — `plan_inputs`/`plan_day` score every candidate on parent request, unfinished FTUE, coverage, recency, completion affinity, category variety and time-of-day fit, never plan into bedtime, and return a parent-readable *"why this activity today"* line per entry (stored as `robots/<id>/schedule_explain.json`, served by `GET /schedule`). The wire is byte-unchanged. **📦 Content packs (2026-09-02):** content is no longer a file in our repo — `moxie_sdk/content/packs.py` is a versioned, digest-checked pack file exported from a **positive field allowlist** (pinned to `dataclasses.fields()`; no child PII, memory, telemetry, permits, config or keys), and import-with-review whose per-item state is a **2×2** over `source_version` **and** a `local_rev` digest — so an item *you* edited is never silently replaced (upstream compares two integers and cannot see it). Three fleet collections + five status-HTTP routes (`GET /content`, `/content/export`, `POST /content/review|import|undo`; 409 on a review/import digest mismatch, 413 over 1 MiB), one-slot undo, and `reload_content()` — an attribute swap, so an import is live on the **next turn** with no restart and an in-flight turn keeps its module. Effective content = shipped defaults ⊕ the overlay, so our own release upgrades obey the same rule as a stranger's pack. An imported `code` block is stored, flagged ⚠️ and **never executed**. **🧩 The documented template form actually works in production (2026-09-02):** `prompt` is rendered through a sandboxed Jinja2 environment, and the *container* now ships `jinja2>=3.0` (+437 KB) instead of silently falling back — so `{% if %}`, which this contract has always advertised, no longer reaches the brain as literal template source. Where jinja2 is genuinely absent (a bare-metal SDK install without the `content` extra) the fallback evaluates `{{ dotted.path }}` and `{% if dotted.path %}` byte-identically to jinja2 and **removes** everything it cannot evaluate, counting each removal in `render.STRIPPED`; nothing template-shaped can reach a system prompt on any install. **Hardened 2026-09-03:** the pack *import path* now has its own fence (`sim/tests/test_content_pack_sandbox.py`, 46 tests) following the whole route a pack travels — parse → review → apply → store → reload → turn — rather than only the renderer at the end of it, and writing it found a live hole: the dependency-free fallback's "bare dotted path" grammar was a `getattr` walk over live objects, so an imported `prompt` reading `{{ session.__class__.__repr__.__globals__.inspect.os.environ }}` put the whole process environment — `MOXIE_LLM_API_KEY` included — into the system prompt on any install without jinja2 (the container was never exposed; `SandboxedEnvironment` already refused it). `_resolve` now refuses any `_`-leading segment and counts it in `BLOCKED`. Also fixed: `review_pack` showed **no diff for a `NEW` item**, the one row it pre-ticks — a parent installing a stranger's chat now sees the whole prompt (R4). And the round trip is proved as a *circle* (export → import into a clean appliance → re-export ⇒ the same bytes, including across two separate runtimes) rather than one leg at a time **🧬 Sandboxed extensions (BEYOND #6 P0, 2026-09-03):** a pack item may now carry an `extension` — a small, total, capability-scoped **program** the appliance actually runs, so a shared activity can check the clock, count something and remember a score instead of only carrying prompts. `moxie_sdk/content/ext.py` is a declarative rule list over a JSON-AST expression language: 53 frozen operators, 10 statements, **no `exec`, no `eval`, no parser, no loop, no user function, no recursion**, and no name that resolves to a host object — the fact base handed to the evaluator is a plain-JSON dict the host builds, so an attribute walk has nothing to walk to. The module imports neither `time`, `random`, `os`, `datetime`, `secrets` nor `subprocess` (asserted over its own source); the clock and the PRNG seed are injected, so a turn replays byte for byte. Every op is total, so `/` by zero is an error *value* and there is no state in which the evaluator does not return. Capabilities are checked at load in **both** directions — declared == used, or it does not install — so the plain-English grant list a parent reads is provably what the program can do; default-granted is exactly `{say, handled, session, child.nickname}` and `MOXIE_EXT_BUDGET_S` (0.25 s) is asserted strictly inside `MOXIE_BRAIN_BUDGET_S` at startup. A breach discards the effect list **whole** and falls through to today's path — a failed `global` goes to the conversation, a failed `turn.before` lets the model answer — so **Moxie keeps talking** and the child hears no error text; the parent gets one plain-language `ext_events` entry and three breaches quarantine. `explain()` renders each rule as one English sentence beside a **capability-escalation** review rule that un-ticks an item asking for more than the installed version. All six upstream OpenMoxie hooks are hand-ported as the conformance golden set, and G1 ships as a real activity (`What Time Is It`, answered with **no model call**). 150 tests — `test_ext_escapes.py` (X1–X12) and `test_ext.py` (T1–T18) — plus `sim/tools/ext_mutation_check.py`, which deletes each of 28 guards in turn and requires its test to go red (28/28). `act`/`subscribe`/`brain` validate as grammar and are refused at load until the `RemoteChatAction` wire lands (P1). | `mqtt/moxie_sdk/content/` (+ `ext.py`) + `mqtt/moxie_sdk/schedule.py` + `mqtt/moxie_sdk/store.py` + `mqtt/content_modules/` + `mqtt/requirements.txt` + the 📦 console card |
| Cloud queries — schedule + `mentor_behaviors` | [mqtt](mqtt-and-conversation.md) · [content-module](content-module-contract.md) | 🟢 the robot gets a **real day plan** and its **own history back**: `build_schedule` plans onboarding + a variety rotation of on-board activities, skipping what this robot already completed (so FTUE ends and nothing repeats); reported `mentor_behavior`s are ingested and served. Deterministic (day+device seeded), not yet LLM-planned | `mqtt/moxie_sdk/schedule.py` + `wire.py` + `moxie_runtime.py::_on_activity` |
| Durable per-robot state | — | 🟡 JSON files under `MOXIE_DATA_DIR` (default `mqtt/data/`), atomic-ish writes, survives restarts — a **stepping stone**, not the database the audit asks for (ADOPT #8) | `mqtt/moxie_sdk/store.py` + [`mqtt/data/`](../../mqtt/data/) |
| Config/telemetry data-model | [config](config-and-telemetry-contract.md) | 🟢 RobotCloudConfig (now incl. **`alarms` + `schedule_preferences`**, contract gap closed) + RobotStatus ingest + **Packet telemetry (build/parse/ingest/summarize) + LoggingPolicy upload-gate**; served to the console as `GET /telemetry`. **Telemetry is durable (2026-09-02):** two per-robot collections — a 500-envelope ring (`telemetry_packets`) plus 35 days of daily roll-ups (`telemetry_daily`) — so the 📈 card shows a **week** instead of an event log over one process's lifetime, and the `LoggingPolicy` gate now applies **on the way to disk** as well: `NO_DATA` writes nothing at all, `NO_MEDIA` (the default, as for the safety journal and memory) withholds **every** `event_data` payload because the recovered proto types none of them, `FULL` keeps them truncated. Caps configurable (`MOXIE_TELEMETRY_MAX_PACKETS`/`_MAX_DAYS`), lifetime `total` kept separately from the sliding window, a clock-lying `recorded_at` filed under arrival time, and the in-memory buffer is now a cache of the ring hydrated on first touch — so `telemetry_count`, the insights view and the planner's signals all see history. Live-proven: three packets into one supervisor, read back through the **next** one with the robot not reconnected. Config is layered **`defaults ⊕ fleet ⊕ per-robot`** (audit ADOPT #6) — one `fleet/config.json`, `POST /config?scope=fleet`. **Gated by a device allowlist** (audit §3.1, closed by default): only a permitted robot is pushed `child_pii`; an unknown one is *pending* and gets `build_unpaired_cloud_config()` — `fleet/permits.json`, `GET`/`POST /permits`, the console's 🔐 Robot access card, `MOXIE_ALLOW_UNVERIFIED_BOTS` to migrate. **Appearance built (2026-09-02, audit ADOPT #9):** the child's chosen face rides in `child_pii.face_options` and the pushed `child_pii.id` is a deterministic UUIDv5 over the chosen layers, so a look change re-keys the robot's face-texture cache and an idempotent re-push does not — a data-driven 14-slot catalog (`MoxieCustomizationType`, `moxie_sdk/face_assets.json`) carrying **72 options across 11 slots and zero invented asset ids** — the 12 doc-cited hex colours (`origin: recovered-enum`) plus 60 asset ids ingested as cited data from OpenMoxie's `MOXIE_CUSTOMIZATIONS` (MIT, commit `c8c2d380`, `origin: openmoxie-manifest`, every one flagged `caution`); `Stickers`/`Extras`/`Misc` stay empty, layered fleet ⊕ per-robot like every other override, and the console's 🎨 Moxie's look card renders whatever the catalog holds | `mqtt/moxie_sdk/cloud_config.py` + `faces.py` + `telemetry.py` + `store.py` |
| Telehealth / puppet mode ("Be Moxie") | [mqtt §3.9](mqtt-and-conversation.md) · [brief](backlog/telehealth.md) | 🟡 **built 2026-09-02, unproven against hardware** (audit ADOPT #7): the `commands/telehealth` command path a remote grown-up speaks through — six runtime verbs, `GET`/`POST /telehealth`, the console's 🎭 **Be Moxie** card (11 recovered moods, intensity **0-2** as the robot's own `maxIntensity`, interrupt, live text transcript) and a `bridge.js` handler so the SIM performs the operator's line through the same markup a brain reply uses. Every JSON key is cross-checked in CI against the recovered `TeleHealth.proto`; `line_id`/`line_params` are never emitted (no catalog of authored ids). The operator's text is classified as `role=MOXIE` and journalled — **a block is returned to them with its reason (400), never silently rewritten** — and while a session is open the brain is not called at all, so two voices can never share one mouth. `sim/run_smoke.sh --telehealth` runs the whole chain through a real broker. **Honest ceiling:** that `moxie_mode:"TELEHEALTH"` is what enters `STATE_TELEBRAIN` is field-proven (OpenMoxie), not capture-proven, and lives behind one constant | `mqtt/moxie_sdk/telehealth.py` + `moxie_runtime.py::telehealth_*` + `server/moxie_server/fleet.py::normalize_telehealth` + `server/static/app.js` + `sim/web/bridge.js` + `sim/virtual_moxie.py` |
| Hosted static site — what it admits it can do | [static-experience](static-experience.md) · [brief](backlog/live-sim-demo.md) | 🟡 **P0-a + P0-b built 2026-09-02, P1's EARS built 2026-09-03, unverified on a real Pages deploy**: the page no longer decides from the HOSTNAME, and it can now hold a conversation. `sim/web/mode.js` polls same-origin `GET /api/health` (a Pages Function that makes **no gateway call, ever**, and is always 200 so a non-200 unambiguously means *route absent*) and `sim/web/env.js` paints the badge, a capacity pill, the `needs-backend` marks and the banner from the answer — `offline` is **byte-identical to the site as it shipped**, `degraded` adds the honest reason, `live` stops claiming the mic needs a locally-run server. P0-b adds the live turn: `POST /api/chat` **builds the upstream body and never forwards the client's** (fixed model, fixed `max_tokens` 160, fixed temperature; a client `model`/`messages`/`tools`/`n` is *ignored*, not allowlisted — ignoring cannot drift), runs a pre-inference safety floor whose hard block never reaches a model, and returns the exact `remote_chat` payload `bridge.js` already renders plus an HMAC **speech ticket** and a signed context blob. `POST /api/speech` **has no text field at all** — the text lives inside the ticket's signature, so the only text the demo will ever synthesize is text it wrote itself in the last 60 s, which takes the most expensive per-request vector off the table structurally rather than with a counter. `sim/web/cloud-transport.js` wraps `sendUserTurn` (nothing in `bridge.js`/`audio.js` is touched) and routes the TTS message **before** the chat message, so there is one voice and not two. Every refusal is the same envelope with §4.5's status and `Retry-After`, so a 429, a spent budget, a full queue or a dead gateway all answer from `stub.js` instead of going quiet. No secret is involved on the client and none can leave the Function: the key is read only as `context.env.DEMO_GATEWAY_API_KEY`, is non-enumerable on the config object, and no upstream status, body or header is forwarded — `sim/test_demo_proxy.mjs` sweeps every response it produces (139 sweeps, 0 leaks) for the key, the base URL and every model id, in the body and in every header. With no variables set at all every route answers `gateway_not_configured` and makes zero upstream calls, so a keyless branch preview is automatically the scripted demo. A gateway behind a **Cloudflare Tunnel** works as a plain base URL; behind **Cloudflare Access** it needs a service token, and half a token is refused rather than sent. **The ears (2026-09-03).** `POST /api/transcribe` completes the chain: a visitor can now *speak*. Same caps, same envelope, same admission order — plus a byte floor below which **no upstream call is made at all**, and a container sniff that refuses 500 KB of non-audio for free. It also settled the spec's §10 assumption 15 with five real gateway calls, **negatively and consequentially**: the gateway transcribes a 16 kHz mono WAV word-perfect in 2.58 s but answers **HTTP 500** to webm/Opus, ogg/Opus and mp4/AAC — the three containers a browser's `MediaRecorder` can actually produce. Since 500 means `upstream_down`, which degrades the whole page, that would have taken the brain and the voice down on every press of the microphone. So `DEMO_STT_FORMATS` (default `wav`) refuses an unaccepted container *before* the call and per-turn, and `sim/web/mic.js` now **encodes 16 kHz mono WAV in the browser** — the downsampler and RIFF writer the spec's §2.1 noted were missing from `sim/web` entirely — while the local sidecar keeps `MediaRecorder` unchanged. `mic.js` also gained the honest cost ceiling the byte cap is not: a hard stop at `DEMO_MAX_RECORD_MS` (15 s), published in `/api/health`'s `limits`, proven by `sim/test_demo_ears.mjs` to stop a **fake** recorder at 15 001 ms with no microphone opened anywhere. **The fallback has a voice now (2026-09-03).** With no variables set — a fresh deployment, and every branch preview — the page is `degraded`, so the fallback IS the demo. It had 12 Moxie clips and went quiet for the rest: 9 of `stub.js`'s 11 replies and all 8 of `filler.py`'s thinking lines fell through to a 1.4 s probe for a Piper sidecar that cannot exist on a hosted origin, and then to whatever voice the visitor's browser owns — Moxie changing character mid-conversation. All 18 are pre-rendered now (`en_US-amy-medium`, mono 22050 Hz 64 kbit, **zero gateway calls**, 452 596 bytes), plus one line she says **once** on entering degraded and never again — *"The cloud has gone quiet. Do not worry, I am running on what I remember."* — fired on `mode.js`'s state transition rather than on a turn, and `offline` is deliberately excluded so a fork with no Functions stays byte-identical. The probe is skipped when `degraded` and kept in `offline`, where a self-hoster's Piper really is on :8081. `sim/test_fallback_coverage.mjs` (414 → **717 assertions**) now asserts a clip for every one of the **78** lines the degraded page can utter and drives the real `ambient.js` and `audio.js` under a stub window rather than grepping them; ten mutations were checked to turn it red. It also caught a live bug in `sim/tools/prerender_audio.py`: the manifest merge named its groups, so any run without `--ambient` rewrote `audio/index.json` with no `ambient` key — 56 committed MP3s orphaned, the whole self-talk layer muted, no error printed. **Honest ceiling:** the per-IP, concurrency and unit-budget counters are best-effort **in-process** — a Worker isolate is not a shared counter (§4.6) — and exact counters, Turnstile, a recovery line and streaming are still P1/P2 | `functions/api/{chat,speech,health,transcribe}.js` + `functions/api/_lib/{env,envelope,hmac,limits,wire,wav,safety}.js` + `sim/web/{mode,cloud-transport,env,mic}.js` + `sim/web/_headers` |
| Scripted demo — the child's voice | [sim](sim-as-a-client.md#the-other-voice-on-the-page-the-child-2026-09-03) · [static-experience](static-experience.md) | 🟢 **the demo conversation has two voices (2026-09-03)**. The child's two `sessions/demo.json` lines had been pre-rendered into the manifest's `child` group since the audio was first built and **nothing ever played them** — `handleUserTurn` wrote the transcript row and stopped, so half the shipped conversation was silent. It could not be fixed with `speak()`: the same `/events/remote-chat` handler carries a **visitor's own** typed/spoken words, and `speak()`'s clip→Piper→browser-voice guarantee would read them back in a stranger's voice. So `audio.js` gained `speakClipOnly(text, who)` — strict same-group manifest lookup, **no code path to a synthesizer**, a separate entry point rather than a flag so the guarantee cannot be loosened by editing a condition. Ordering is asymmetric on purpose (the child yields, Moxie interrupts), the child's clip never drives Moxie's mouth, and `demo.json`'s first reply moved 1800→3000 ms because her clip runs 2.6 s and `speak()`'s `stop()` was cutting her off mid-word. Proven by `test_fallback_coverage.mjs` §2b (script timing, codec-free) + §8b (the real `audio.js` under a stubbed Web Audio stack) and `test_bridge.mjs`; **10 mutations checked red** | `sim/web/audio.js` + `bridge.js` + `sessions/demo.json` |
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

- **The hosted demo's per-IP windows were free to bypass over IPv6, and its duration cap was not one —
  fixed 2026-09-03.** Four holes in `functions/api/_lib/limits.js` and the three spending routes, all in
  the controls that protect the **self-hosted gateway the demo shares with the owner's video game**, so
  the thing at stake is a neighbour's capacity rather than a bill. **(1) `clientIp` keyed the rate-limit
  bucket on the raw address string.** On IPv4 that is one person; on IPv6 it is one *interface*, and a
  residential allocation is a /64 or wider — so a single visitor held 2⁶⁴ buckets and every per-IP row in
  `backlog/live-sim-demo.md` §4.1 was, for them, unlimited, defeated by a `for` loop. The key is now the
  **first four hextets**, with `::ffff:a.b.c.d` **unmapped to the v4 address rather than truncated** —
  truncating it would have given every IPv4 visitor the same `0:0:0:ffff` prefix and collapsed the v4
  internet into one bucket, which is the way this fix usually breaks. **(2) With `CF-Connecting-IP`
  absent it fell back to `X-Forwarded-For`,** which the caller types; that fallback now needs
  `DEMO_TRUST_XFF` (unset in production) and otherwise keys as `unknown`, deliberately **one shared
  bucket** so unidentifiable callers are throttled together. **(3) `DEMO_MAX_AUDIO_BYTES` was never a
  duration cap, and STT is billed by duration** — the same 500 KB is ~15 s of 16 kHz 16-bit PCM but 62 s
  at 8 kHz 8-bit and ~125 s at 4-bit, all well-formed WAVs. `/api/transcribe` now reads a RIFF header
  server-side (`_lib/wav.js::wavDurationMs`) and refuses `too_long` above `DEMO_MAX_RECORD_MS` with zero
  upstream calls. **(4) All three routes fetched with `redirect` unset,** i.e. `follow`, carrying the only
  credential; they now set `redirect: "manual"` and read a 3xx as `gateway_unreachable_or_gated` —
  a tunnel that redirects is a door problem, not a brain problem. **The honest gaps, both stated in the
  code:** the duration cap covers **WAV only** — webm/Opus and the rest hide their length in a bitstream
  and reading it means shipping a decoder at a hostile upload — so it is total today only because
  `DEMO_STT_FORMATS` ships as `wav` alone, and a fork that widens it re-opens the gap silently; and
  defect (2) was **latent, not live** (Cloudflare always sets `CF-Connecting-IP`), closed because it would
  open the moment anything sat in front. Every counter is still **per-isolate** (§4.6); nothing here
  changes that. Tables of every awkward address form and every rate/width combination are in
  `sim/test_demo_proxy.mjs` block 14, `sim/test_demo_ears.mjs` A-DUR/A-RDR and `sim/test_wav_decode.mjs`
  block 9.
- **The most obvious control on the hosted Sim was dead, and the phone's primary control was buried — fixed 2026-09-03.**
  Measured in Chrome against `https://moxie.mattvalancy.com/sim`, not inferred. **(a) The typed line went
  nowhere.** `#speech-input` + `#speech-btn` ("Say") drives `moxieAudio.speak()`, whose only route for
  arbitrary text is the LOCAL Piper sidecar on `:8081` — which cannot exist on a hosted origin and which the
  site's own `connect-src 'self'` correctly refuses. A visitor typed a sentence, pressed the button, and got
  `apiCalls: only /api/health · audioDecoded 0 · audioStarted 0` plus a CSP console error. `env.js` already
  MARKED the button `needs-backend`, but a mark was a tooltip and half opacity: it stayed fully clickable and
  silently failed. Now, **when and only when no local Piper answers**, `cloud-transport.js::adoptSpeechControl`
  hands that box the typed turn — "Say" becomes "Ask", the line goes through the same
  `moxieBridge.sendUserTurn` the microphone uses (so the same `admit()` gate: origin pin, per-IP window,
  budget, the PR #107 FIFO), and the duplicate injected Talk box stands down so exactly one typed control is
  ever visible. **With a sidecar reachable, nothing changes at all** — the local engines stay first-class, and
  the mode (never the hostname) decides. Controls that genuinely *cannot* work off-localhost — `#tts-test`,
  `#bus-connect`, `#tts-base`, `#stt-base` — are now **disabled**, not hinted; `#mic-btn` deliberately is not,
  because "Listen" really does play a scripted line there. `audio.js::skipProbe` also stops probing `:8081`
  from any non-local origin, which removes the CSP violation at its root rather than at the button.
  **(b) The rail toggle was untappable on a phone.** At 375x667, `#env-banner`
  (`position: fixed; bottom: …; z-index: 30`, stretched edge-to-edge under `@media (max-width: 640px)`) sat
  exactly on top of the bottom-anchored `#rail-toggle`: `elementFromPoint()` at its centre returned the
  banner, `tap()` was refused as obscured, and a forced click left `aria-expanded` false — the toggle was
  visible, sized and `pointer-events:auto` the whole time, so **no visibility check could have caught it.**
  Fixed in layout, not z-index: `env.js` measures the panel and lifts the banner clear via `--eb-lift`.
  **(c) The CSP had no `script-src` at all**, so script execution was unrestricted; it is now
  `script-src 'self' 'unsafe-inline'` behind `default-src 'self'`, plus `form-action 'none'`,
  `frame-src 'none'`, `worker-src 'self' blob:` and **HSTS**. Three new browser suites hold all of it:
  `sim/test_typed_turn.mjs` (56 checks — asserts the **peak sample amplitude** of the buffer handed to Web
  Audio, so a silent clip cannot pass), `sim/test_mobile_layout.mjs` (48 checks, with a teeth block that
  restores the old geometry and requires the collision to reappear) and `sim/test_csp.mjs` (37 checks — the
  first suite in this repo that serves the pages with the **real** `_headers` applied). **Honest gaps:**
  `'unsafe-inline'` for scripts stays — `_headers` is static so a nonce is impossible, and hashing nine
  inline blocks plus ten inline `onclick=` attributes needs a build step and a freshness guard; HSTS covers
  the static pages only, because `_headers` does not apply to Pages *Function* responses (ledger row 27), so
  `/api/*` still needs it set in `envelope.js`; `/api/*` responses carry no CSP (JSON with `nosniff`, low
  risk); the root `README.md` embeds a github.com image that `img-src 'self'` correctly refuses, named as a
  single shrinking exception in `test_csp.mjs`; and `mic.js`'s degraded path still publishes a **scripted
  child line** through `sendUserTurn`, which on a live page spends a real chat + speech turn on words the
  visitor never said — the typed path deliberately does not copy that, but the mic's copy is untouched here.

- **The hosted demo refused the eleventh visitor instead of queueing them — fixed 2026-09-03.** At
  `DEMO_MAX_CONCURRENT_CHAT` in-flight turns, `functions/api/_lib/limits.js::admit` answered `at_capacity`
  on the spot, so a momentary collision between the ~ten people the demo is sized for turned into scripted
  lines for whoever arrived second. **The ceiling was deliberately NOT raised:** it is matched to the
  upstream key's `max_parallel_requests`, which protects another service on the same self-hosted gateway,
  so a bigger number would only move the refusal upstream as a 429 and starve the neighbour — and at ~1.2 s
  a turn, four slots already serve ~3 turns/second, far above what ten conversational visitors need.
  Instead `admit()` is now `async` and, at the ceiling, joins a **bounded FIFO** for up to
  `DEMO_QUEUE_MAX_WAIT_MS` (2 500 ms) with at most `DEMO_QUEUE_MAX_DEPTH` (8) waiting; past the depth, or
  when the wait expires, it refuses with the same `at_capacity` + `Retry-After: 15` as before — **a queue
  with no depth cap is just a slower way to fall over.** `release()` *hands the slot over* rather than
  freeing it, which is what makes the order FIFO by construction: the count never dips, so a late arrival
  has no gap to slip into. All three routes await it and keep `release()` in a `finally`;
  **`/api/health` is untouched and still non-`async` with zero upstream calls**, and `sim/test_mode.mjs`
  now pins both halves of that pair. The charge/refund question the queue forced — a timed-out waiter had
  paid a rate-limit unit and budget units for a turn it never got — was answered with a **refund** rather
  than a reordering, because reordering would let an un-rate-limited script occupy queue slots it never
  earned; the full argument is in `refundCharges()` and in `backlog/live-sim-demo.md` §4.1. **The honest
  gap:** the FIFO is **per-isolate**, exactly like every counter around it (§4.6) — a fair order among the
  requests one isolate holds, never a global queue position — and the load-bearing premise (a ~1.2 s turn,
  which is what makes a depth of 8 deliverable inside 2 500 ms) is inherited from earlier measurements
  rather than re-measured under real load. Ledger row 28.

- **`/api/health` told the page what it wanted to hear — fixed 2026-09-03.** `functions/api/health.js`
  shipped two LOCAL STUBS that shadowed the real implementations: `budgetState()` returned `null` and
  `loadState()` returned a hard-coded `{inflight: 0}`. Both were honest in P0-a (no spending route was
  deployed) and became a lie the moment `chat.js` and `speech.js` landed with `_lib/limits.js` behind
  them: the probe **could not answer `budget_exhausted` at all**, so a new visitor's page painted **LIVE
  on an over-budget deployment** and §7's BUSY pill could never fire — and after every spend refusal the
  next 30-second poll re-armed the page to `live` because health kept saying everything was fine. Now it
  imports `budgetState`/`loadOf` from `_lib/limits.js` and answers from the real counters, with §4.5's
  `Retry-After` on the `budget_exhausted` row. **The hard invariant holds:** `/api/health` still makes
  **zero** upstream calls — both reads are synchronous in-memory map lookups, `onRequestGet` is not
  `async` (a handler that cannot await cannot call upstream), and `sim/test_mode.mjs` asserts
  `limits.__state().stats.upstreamCalls === 0` across every probe as well as no `fetch(` in the source.
  **The honest gap, now written down in three places instead of contradicted in three places:** the answer
  is **that isolate's view**, not the deployment's. §4.6 and assumption-ledger row 25 had claimed the
  counters were "an in-isolate map plus the per-colo Cache API" — **the Cache API leg was never
  implemented**, verified against the shipped code on 2026-09-03 — so the multiplier is *isolates*, not
  colos, and the configured caps are a **per-isolate throttle, not a global budget**. The document was
  corrected rather than the tier built, because §10 assumption 13 (is KV or a Durable Object available on
  this plan?) is still open and decides which counter is worth building. `sim/web/mode.js` was
  **deliberately not changed** — see §4.6's "why a spend refusal opens no separate client-side suppression
  window".
- **`/api/speech` shipped an upstream 200 body to the visitor, and the leak sweep could not see it
  (2026-09-03, closed).** `pcmFromAudio` was never told which `response_format` had been asked for, so its
  raw-PCM fallback — correct **only** under `DEMO_TTS_FORMAT=pcm` — was live under the shipped `wav`
  default after just three sniffs (empty, `{`/`[`, `<`). Any other 200 (a `text/plain` proxy error, an SSE
  `data: {"error":…}` frame whose prefix defeats the `{` sniff, an `ID3` mp3, a webm EBML header) came back
  as `container:"raw"`, was base64'd into `messages[0].payload.audio.buffer` and shipped at **status 200,
  `reason: null`, `degraded: false`** — a body-disclosure hole, and with an mp3 several seconds of
  full-scale static in a child's ear, which is the exact harm `_lib/wav.js`'s header claims to prevent.
  **The test blind spot is the more useful finding:** `assertClean` swept with `text.includes(secret)`, and
  **base64 defeats substring matching**, so ~1000 sweeps reported clean on responses that carried the key.
  Fixed by passing `cfg.ttsFormat` down and gating the raw branch on it (absent ⇒ strict `wav`), by a
  magic-number container refusal mirroring `transcribe.js::audioKind`, and by two headerless-body guards
  under `pcm` (odd byte length; >90 % printable). `assertClean` in **both** sweep files now decodes
  `payload.audio.buffer` and sweeps the bytes. Same slice: the per-isolate `spent` set keyed on the raw MAC
  **string**, and base64url is not canonical — an HMAC's 43rd character carries two bits nothing reads, so
  four spellings verified and one paid chat turn bought **exactly 4x** TTS per isolate (measured, then
  pinned); it now keys on canonical bytes. **Honest ceiling:** under `DEMO_TTS_FORMAT=pcm` there is no
  header and no magic number, so a short opaque binary error blob is still undecidable and would pass —
  `wav` is the default for that reason.
- **Integration evidence (2026-09-03 21:10 PDT, seventh pass) — the stack is healthy, and the
  readiness contract PR #103 opened was only half built.** Everything asked of the whole stack came
  back green on a docker broker: `run_smoke.sh` ✅ (TTS audio 50 934 B @ 22050 Hz + the five scored
  fields), `run_scenarios.sh` ✅ 2/2 × 4/4, `--telehealth` ✅ enable→start→speak→interrupt→end,
  hermetic suite **4702 passed / 27 skipped / 4 xfailed creds-free** *and* **4319 / 69 in a venv with
  no `openai`, `fastapi`, `httpx`, `jinja2` or `PyYAML`**, `python -m build` → wheel + sdist at
  0.7.0. **Live, 2 gateway calls total:** `test_live_gateway_turn_e2e.py` 5/5 — the gateway brain
  answered *"Hello there! What's a fun thing you did today?"* and the robot heard **152 296 B @
  22050 Hz (3.45 s) at spectral flatness 2.013e-02**, `piper-amy`, four orders above the 1e-6 floor,
  so not the placeholder tone. **Both halves of the creds gate verified**, which is the property that
  matters: with the key the live tier runs, and with `MOXIE_LLM_API_KEY=` it reports **14 named
  skips** while the hermetic anti-tone guard in the same file still runs — so the live assertion can
  never be vacuous.
  **THE GAP this found — the honest readiness line is not observable.** PR #103 made
  `[runtime] broker connected` *true* (subscribe, then print) and `test_connect_readiness.py` fences
  that order of effects. It is not *visible*: the print carries no `flush=True` while the refusal
  branch three lines above it always has, and **every one of the five things that block on this line
  reads it from a redirected stdout**, where Python is block-buffered. Four callers each carried
  `PYTHONUNBUFFERED=1` to compensate — `helpers_stack.py` even says why in a comment — which is four
  guards covering for one missing keyword, and the fifth caller (this pass's rewrite of
  `run_smoke.sh`) forgot it and waited the full 40 s for a supervisor that had connected in 0.11 s.
  Fixed at the source; the environment variable is now belt and the keyword braces.
  **THE SECOND GAP — `run_smoke.sh` was the last SIL script still guessing at its boot.**
  `run_scenarios.sh` was converted to polling; the script CI actually gates on kept `sleep 2` +
  `sleep 3`. Measured here: the broker listens in **0.35 s** and the supervisor is ready **0.11 s**
  later, so the smoke burned **4.7 s of pure waiting** every run (8.03 s → **3.31 s**) — and was
  still blind the other way. Reproduced by making `mqtt/run.py` 8 s slow, which is a loaded runner:
  the old script failed 20 s later as **`no config pushed within timeout`**, naming the config push,
  the robot and the broker and never the boot that had not happened; the polled script passes the
  same case. Both waits now live once in [`sim/readiness.sh`](../../sim/readiness.sh), sourced by
  both scripts, because two copies of a wait are two waits.
  **THE THIRD GAP, the worst of them — `--telehealth` drove another run's supervisor.**
  That mode drives the robot over the supervisor's own status HTTP, and the script derived the port
  from the broker port and called the bind *"best-effort either way"* — true when nothing read it,
  never revisited when `--telehealth` made it load-bearing. On `MOXIE_SIL_PORT=1930` → `:8930`, held
  by a stale supervisor from an unrelated run, the runtime logged
  `status server failed: [Errno 98] Address already in use`, carried on, and the telehealth robot
  **POSTed its commands into that stranger**, failing 20 s later as
  `exception: Expecting value: line 1 column 1 (char 0)` — a JSON error blamed on the TeleHealth
  wire. Both bind outcomes are printed by `_start_status_server`, so both are observable; the script
  simply did not look. It now does, in ~1 s with the port named, and it honours an operator's
  `MOXIE_STATUS_PORT` the way `run_scenarios.sh` always has. Same shape as the two above and as the
  `MOXIE_DATA_DIR` leak the sixth pass closed: an unobserved precondition becoming a false
  accusation against the subject under test — and here, one run reaching into another run's process.
  Guarded generically in [`test_harness_readiness.py`](../../sim/tests/test_harness_readiness.py)
  (8 tests, 5 of which fail on the pre-fix tree), deliberately **not** named `test_sil_*`: both CI
  tiers select the hermetic suite with `-k "not test_sil"`, so a SIL-prefixed guard about the SIL
  scripts would have been deselected everywhere it was meant to run.
  **Honestly not verified:** no physical robot, as everywhere else in this document; the residual
  paho window between `subscribe()` queuing a packet and the loop writing it is unmeasured (it is
  microseconds against a robot's TCP+CONNECT round trip, and the fix does not claim to close it);
  and the sixth pass's stray-`.tmp` finding is still recorded-not-fixed — I left it alone rather than
  widen this slice.

- **Integration evidence (2026-09-04, sixth pass) — the `week` soak finished, 12/12 bars, and
  P1's three fixes hold from outside.** P1's soak was killed at 59 of 60 minutes before it wrote a
  report, so every §5.3 bar was unverified. Run to completion: **3601 s · 4407 turns answered while
  the broker was up (bar: 2000) · 24 broker restarts · 4 supervisor restarts · 20 mid-write
  `SIGKILL`s → 12 met, 0 failed, 0 not exercised.** Headline numbers: A1 4407/4407 (100 %); A3 p95
  **0.62 s** re-subscribe (bar 3 s), max 0.62 s over 24 reconnects; A4 4/4 roster resumes, max
  1.12 s; A5 0 lost of 10 000 appends across 4 processes at the **default** 2.0 s lock budget, 0
  refused; A7 RSS **-1.4 %**; A8 fds +0; A9 recent=60 robots=3 roster=3 conn_events=67/400; A12
  24/24 restarts re-onboarded every robot, max 9.77 s. **A6 passed with 0 unreadable records and 5
  stray `.tmp` files** — the bar asks only that nothing is unreadable, but a temp file per
  interrupted write is uncollected state that grows with faults; recorded, not fixed.
  **§5.4 unchanged and repeated rather than softened:** an hour at a raised rate against a simulator
  is a **rate substitution**, not a week — it stands in only for failures that scale with *events* —
  and **no physical Moxie has ever been on this broker**.
  **The three fixes, confirmed from outside the code that implements them.** The roster ghost:
  `run_broker_outage.sh` EXIT=0, and with the pre-P1 early-return restored in a scratch copy EXIT=1
  with `seen_since_connect=False` while `/status` still listed the device. The vision latch: a real
  broker + a real `mqtt/run.py`, asserting `EventSubscription.active[]` **on the wire** — sent
  first, suppressed on a repeat, re-sent on A→B, on the **B→A re-entry** and after a broker restart;
  ceiling unchanged, that proves *we re-subscribe*, never that a robot delivers. The `OverflowError`:
  at a raised `MOXIE_STORE_LOCK_TIMEOUT_S=30`, **13 346 polls**, refused cleanly and recorded, while
  the same harness without the clamp still raises at exactly **1 024**.
  **Bench-roster noise — a harness defect, not a runtime one, and fixed.** The runtime is correct
  (cap 64, LRU eviction, QoS 0 pushes, `forget()` on unpair, `MOXIE_ROSTER_RESUME=0`); what was
  wrong is that three SIL scripts booted `mqtt/run.py` against the repo's `mqtt/data`, so a fresh
  smoke re-pushed config to `d_outage…` ids minted a quarter of an hour earlier. Each now scopes a
  per-run `MOXIE_DATA_DIR`, guarded generically in `test_roster.py`.
  **THE GAP this found — `helpers_stack` calls the supervisor ready before it is listening.**
  `Supervisor.start()` waits for `[runtime] broker connected`, which `_on_connect` prints **before**
  `c.subscribe(...)`; the SIL robot then publishes its single `/state` a millisecond later and
  `run_smoke()` never re-announces, so the live one-turn e2e fails as *"no config pushed within
  timeout"*. Reproduced repeatedly (a second `/state` is answered at once). Rule 23's shape in the
  readiness contract. Not fixed here: it touches `_on_connect` and the shared harness.
  **(3) The rest of the stack.** `run_smoke.sh` (1991) ✅ incl. TTS audio + the five scored fields,
  `--telehealth` (1992) ✅, `run_scenarios.sh` (1993) ✅ 2/2 × 4/4, `run_acl_proof.sh` ✅ 18/18,
  `run_compose_smoke.sh` ✅ in **both** modes, and **one live gateway turn** through the P1 runtime:
  the gateway brain answered *"Hello Sam! I'm so happy to see you."* and the robot heard 145 640 B @
  22050 Hz at spectral flatness **7.1e-02** — `helpers_audio.is_real_speech` **True**, five orders
  above the 1e-6 floor, so not the placeholder tone. Hermetic suite **4791 passed / 26 skipped /
  4 xfailed** with `fastapi httpx pynacl` present.

- **Integration evidence (2026-09-03, fifth pass) — the connection rewrite (#94) survives a real
  broker outage; the ROSTER does not.** P0 rewrote how the supervisor connects, reconnects and
  publishes, and every one of its tests uses a fake client or an injected transport. Nothing had
  taken a real broker away from a real `mqtt/run.py`. [`sim/run_broker_outage.sh`](../../sim/run_broker_outage.sh)
  now does, against a mosquitto container it owns (deep tier, `sim/ci/ci-deep.yml`).
  **(1) Cold start with no broker at all — PROVEN.** The supervisor started against a dead port
  stays up, prints `⛔ could not reach the broker at 127.0.0.1:18943 — retrying` once per backoff
  step, and answers `/status` with `broker_connected=False` + a populated `last_connect_error`
  throughout. That is `test_connection_resilience.py::S6`'s claim — `loop_forever()` re-raising the
  first `OSError` without `retry_first_connection=True` — made about a **process** rather than a
  stub. It then connected on its own the moment the broker appeared, with no restart.
  **(2) The broker taken away from a running supervisor — PROVEN, all six fields.** Measured across
  one outage: `broker_connected` True → **False** → True; `last_broker_disconnect` **0.0 →
  1788475928.9289165** (67 ms after `docker stop` was issued); `last_broker_connect` 1788475926.58 →
  **1788475929.93** (410 ms after `docker start`); `last_connect_error` "" during the gap on the
  disconnect path (it is the *connect-fail* field, and no connect was attempted in that window);
  `publish_drops` **0 → 1** for a `POST /config` in the gap, logged as `⚠️ dropped config for
  d_outage… — The supervisor is not connected to the broker.`; `store_lock_timeouts` 0. The console
  route answered **409** with `{"published": false, "acknowledged": false, "error": "no broker
  connection", "reason": "The supervisor is not connected to the broker."}` — never `published:
  true`. The supervisor process survived, and a robot it had never seen took a full turn
  (state→config(paired)→remote-chat→reply) over the reconnected socket.
  **THE GAP this found — a returning robot is never re-onboarded after a broker restart.**
  `_device_connect` early-returns for a device already in `self.robots`, and the only thing that
  ever removes one is `_device_disconnect`, which fires off a `$SYS/broker/log` line — a line that
  dies **with the broker**. `_on_disconnect` bumps `_turn_seq` (correctly: it stales in-flight
  turns) but does not clear the roster. So after any broker restart `/status` still lists every
  robot as connected, and the robot that was mid-conversation comes back with the same device id,
  gets **no config push and no `app.on_connect`**, and sits there. Reproduced twice; phase 5c of the
  harness reports it and `MOXIE_OUTAGE_STRICT_ROSTER=1` makes it fatal the day it is fixed. Not
  fixed here — `mqtt/supervisor/moxie_runtime.py` is owned by the hardening P1 slice.
  **(3) The rest of the stack still works.** `sim/run_smoke.sh` (1991) ✅ incl. TTS audio + the five
  scored fields, `--telehealth` (1992) ✅ enable→start→speak→interrupt→end, `sim/run_scenarios.sh`
  (1993) ✅ 2/2 scenarios 4/4 turns, `sim/run_acl_proof.sh` ✅ 18/18 against
  eclipse-mosquitto:2.0.20, and `sim/run_compose_smoke.sh` ✅ in **both** modes (build and images) —
  the composed stack reads the new connection code and the new store lock. Hermetic suite
  **4680 passed / 26 skipped / 4 xfailed** with `fastapi httpx pynacl` present (the 92
  `test_console_roundtrip` tests run rather than skipping as one module). `python -m build` →
  `moxie_cloud_sdk-0.7.0`, carrying `performance.py`, `brains.py`, `content/ext.py` and both
  `moxie_sdk/*.json`, and importing 15 modules in a `paho-mqtt`-only venv.
  **(4) One live turn through the hardened runtime.** `test_live_gateway_turn_e2e.py` — real broker,
  `mqtt/run.py` as its own process, gateway brain + gateway voice, 5/5 passed, and the audio the
  robot heard passes `helpers_audio.is_real_speech` (the anti-tone guard) rather than the
  placeholder. **2 gateway calls** (1 completion + 1 `/audio/speech`).
  **Honestly not verified:** a broker that *refuses* the CONNACK (rc=5) — the credential path
  `_connack_failed` exists for — was never exercised against a real broker; nor was a **half-open**
  socket (the failure `KEEPALIVE_S=30` is tuned for), which needs packet-level interference rather
  than `docker stop`. One flake seen once and never again: `test_store_concurrency.py::test_t1`
  failed in a full-suite run and then passed 5/5 in isolation and in the next full run.
- **Integration evidence (2026-09-03, fourth pass) — the behavior planner P1 (#92) holds on the
  wire, and the criterion its author qualified now has the real number.** P1 touches the turn loop
  and every published path, and had never met a broker: its criterion (c) was proven "through the
  real runtime", which is an in-process runtime with a fake MQTT client.
  **(1) First audio — the experiment, not the bench.** Criterion (f) reported p95 0.25 / 0.56 ms
  from a loop around `perform()` and said plainly that it was "a bench measurement of the seam, not
  a re-run of the first-audio experiment". [`sim/tools/first_audio_ab.py`](../../sim/tools/first_audio_ab.py)
  re-runs it: a real broker, `mqtt/run.py` as its own process, one supervisor boot per arm, timed
  from the robot's own `events/remote-chat` to the first `remote_chat` carrying words and the first
  `tts` carrying audio. **Controlled arm** (local brain, fixed answer, fixed pace, 20 turns each):
  first words **356.5 ms** planner vs **357.9 ms** floor; first audio **409.2 / 411.3 ms** — the
  planner **1.4 / 2.1 ms faster**, i.e. inside a single arm's own 2–4 ms spread. **Live arm** (real
  gateway, 6 completions, the whole budget): planner 1.579 / 2.386 s, floor 1.019 / 1.838 s, all
  straddling PR #15's **1.52 s**, with ~800 ms of spread inside one arm — N=2 against that can bound
  the seam's cost and never resolve it, which is why the controlled arm carries the verdict.
  **No first-audio regression.** Honestly not measured: `t_audio` is the local tone synthesizer, not
  a gateway voice (identical in both arms, so the comparison is fair; the absolute number is our
  pipeline's, not a voice provider's).
  **(2) Scored output survives a real broker, on both paths.**
  [`sim/tests/test_sil_performance_e2e.py`](../../sim/tests/test_sil_performance_e2e.py) (+19) puts
  robots on a real mosquitto: the five fields (`mood`, `mood_intensity`, `dialog_act`, `emotion`,
  `signals` — **plural**, renamed across the `_publish_chat` seam) on the single reply and on
  **every** streamed chunk including the closing `SUCCESS`, one face per answer, and
  `vocab.validate_markup` clean over everything a robot was handed. Mutation-checked: dropping
  `signals` in `wire.py` reddens five of them. `sim/run_smoke.sh` (1991), `--telehealth` (1992),
  `sim/run_scenarios.sh` (1993) and `sim/run_acl_proof.sh` are green (18/18 ACL checks), and the
  standing smoke now **reads the score** — `virtual_moxie.py --expect-scored`, proven in both
  directions (default prints the five fields; `MOXIE_EXPRESSIVE=off` fails it).
  **(3) The 🎬 rehearsal card, end to end.** `POST /preview` on the supervisor's real status HTTP
  and `POST /local/robots/{id}/preview` on the real console app both land an ordinary `remote_chat`
  in a robot's hands (no `chunk_num`, `event_id` `preview-…`, the console's markup identical to the
  robot's), and the captured payloads are then played through the real `sim/web/bridge.js`
  ([`sim/test_preview_render.mjs`](../../sim/test_preview_render.mjs), new, wired into the fast
  tier) — four lines, four dialog acts, the faces and motors that follow. No brain call is spent.
  **(4) All four slices at once.** One supervisor, three robots: the shipped clock extension (#86)
  under a brain chosen per robot (#88), beside `echo` and a streaming model, every one of them
  scored by the planner (#92).
  **The finding, pinned rather than fixed.** On the `llm` brain — the brain a real deployment runs —
  the markup a robot performs is the **floor's** `annotate` output byte for byte, not
  `render(validate(plan(…)))`: `LLMApp` authors `Reply.markup`/`ReplyChunk.markup` and `_stage`
  honours authored markup verbatim by design. So `MOXIE_EXPRESSIVE=planner` changes the five scored
  fields and **not the performance** there; the act profile, the gaze tree and the per-clause
  staging never reach the wire on the model path, which is C6 (`backlog/expressiveness.md` §2.3)
  unmet. Corollary: only `LLMApp` implements `respond_stream`, so **no published path carries
  planner markup on a streamed chunk at all**. Closing it is a design call on the turn loop, not an
  integration one, so `test_the_model_path_performs_the_floors_markup` holds the current behaviour
  and turns that change into a red test. **Also found:** `MOXIE_TTS=tone` does **not** pin the tone
  engine — `config.build_synthesizer`'s auto precedence is voice-server > Piper > tone, so a
  `MOXIE_VOICE_BASE_URL` inherited from a developer's `mqtt/.env` silently makes every chunk a paid
  `/audio/speech` call; every harness here blanks it explicitly. Hermetic **4548 passed / 27 skipped
  / 4 xfailed**, unchanged (the 19 new cases carry `test_sil` in the file name and are run by the
  fast tier's own `pytest sim/tests` step, not by the hermetic `-k`); the wheel carries
  `performance.py`, `brains.py`, `content/ext.py` and both `moxie_sdk/*.json` and imports in a venv
  holding only `paho-mqtt`, with `plan`/`validate`/`render` exercised there. **Honestly not proven:**
  no physical robot; nothing heard by ear; first-audio through a *gateway voice* was not measured
  (the budget bought brain turns instead); and the live A/B is 2 measured turns per arm, which is a
  bound and not a resolution. 8 gateway calls budgeted, **6 spent**.

- **Integration evidence (2026-09-03, third pass) — #86 and #88 hold together, and the coupling their
  authors flagged is real but told loudly.** The sandboxed-extension evaluator (#86) and per-robot brains
  (#88) both changed the same turn, had 293 unit tests between them, and had never met a running
  appliance. All four risks were settled against real infrastructure.
  **(1) The compose stack.** `docker-compose.yml` interpolates `MOXIE_APP: ${MOXIE_APP:-content}` and #88
  made an explicit `MOXIE_APP` a **pin**, so a bare deployment arrives in the container as an explicit
  `content` and pins: its 🧠 card offers `content` alone, `MOXIE_APP=any` restores all four and pins
  nothing. Both are now steps 3d/3e of [`sim/run_compose_smoke.sh`](../../sim/run_compose_smoke.sh),
  green in **build and images** mode alongside the full robot round-trip with TTS audio, checked over
  `/brain` *and* inside the container with `any` as the control. **The sharper half predates #88 and was
  found by running the stack rather than reading it:** `content` with no `MOXIE_LLM_BASE_URL` exits at
  assembly (#68's loud failure), `restart: unless-stopped` makes that a crash loop, and the console waits
  on `supervisor: service_healthy` — so `docker compose up` with **no `.env`** brings up the broker and
  nothing else (observed: `supervisor restarting (1)`, `console created`). The compose header claimed
  "every value below has a working default"; it now says which one has none. The remaining honesty: the
  smoke pins `MOXIE_APP=echo` in its own env file, so **the documented one-command install has still
  never been proven in its default configuration** — DoD criterion 5's 🟢 is earned for a stack that was
  told what brain to run.
  **(2) Brains resolve per robot, and the swap lands between turns.** `sim/run_smoke.sh` (1981),
  `--telehealth` (1982), `sim/run_scenarios.sh` (1983) and `sim/run_acl_proof.sh` are all green
  (18/18 ACL checks). `sim/tests/test_sil_brains_and_ext.py` (+13) boots the real stack and proves the
  boundary rather than the stored value: a fleet brain POSTed to the real status HTTP answers the *next*
  turn as a different sentence on the wire; a per-robot brain overrides it for that robot alone while a
  second robot on the same supervisor stays on the house rule; and with the brain made to take two
  seconds and the swap posted while the robot is parked inside the call, the in-flight turn is still
  answered by the **old** brain and the next one by the new. Same POST, different timing, different
  outcome.
  **(3) The shipped extension runs live with no model call.** `starter.json`'s G1 answers *"what time is
  it"* on the wire as `The time is …` for **zero** calls to the brain endpoint — checkable because that
  endpoint is a counting stub, with an unmatched utterance on the same robot and the same brain as the
  positive control that still costs exactly one.
  **(4) The two together** is the same file: the extension ran under a brain chosen *per robot*, beside a
  robot on `echo`, in one supervisor.
  **The open finding from the last pass is closed, and it was two leaks, not one.**
  `test_live_gateway.py` left `MOXIE_APP=content` + `MOXIE_STT=off` in `os.environ`, which is why the
  voice picker reported 3 failures in-suite and none alone (reproduced in 2 gateway calls:
  `[picker] 1 listening entries: off`; fixed, and the same pair is 5 passed in 2 more). `test_assemble.py`
  **deleted** `MOXIE_LLM_BASE_URL`/`MOXIE_LLM_API_KEY` and set `MOXIE_SKIP_DOTENV=1`, so
  `test_live_gateway_turn_e2e.py`'s supervisor could not find a brain endpoint and exited at assembly —
  the recorded "4 errors" — proved at zero gateway cost with an ordered probe. Both are fenced by
  `sim/tests/test_env_hygiene_live_suites.py` (+9 hermetic, mutation-checked). **Not verified:** the full
  `pytest sim/tests` with credentials was not re-run — it costs 30–50 gateway calls — so "the tier's own
  command is green on a developer box" remains a claim about two proven mechanisms, not an observation.
  Hermetic 4384 → **4393 passed / 27 skipped / 4 xfailed**; whole suite creds-blanked 4415 passed /
  95 skipped; the wheel carries every `moxie_sdk/*.json` plus `brains.py` and `content/ext.py` and
  imports in a venv holding only `paho-mqtt`. **Honestly not proven:** no physical robot, nothing heard
  by ear, and no live-gateway turn through a per-robot brain (the stub is the model everywhere above).
  6 gateway calls, 4 spent.

- **Integration evidence (2026-09-03, second pass) — four of the five slices merged that day hold; the
  fifth had shipped half-done.** #77 (engine pin), #78 (content packs), #79 (STT/telehealth CI dispatch),
  #81 (audit) and #82 (the child's voice) had never been exercised together. **The pin does not collapse
  the compose stack** — the coupling its author flagged is closed by construction and now by the running
  stack: `sim/run_compose_smoke.sh` green in **build and images** mode with two new steps proving
  `MOXIE_TTS=tone` (what *both* compose files default to) pins nothing, while `MOXIE_STT=off` in the same
  env file *does* — the positive control that keeps the check from passing vacuously — and, inside the
  supervisor container with the composed environment, a gateway listing still yields
  `["gateway:piper-amy", "gateway:piper-ryan", "tone"]` rather than one entry. **A pinned engine still
  speaks on the wire**: the live e2e turn now runs with `MOXIE_TTS=gateway` *and* `MOXIE_STT=gateway`,
  drops a console pick of `tone`/`off` at the builders (probes chosen because those are the only
  cross-engine picks this box could really build, so the assertion is not vacuous), hears the child at
  overlap 1.00 and puts 6.17 s of `is_real_speech` audio on `commands/tts`. **Both live suites #79
  dispatched have now actually run** — `test_live_gateway_stt.py` at overlap 1.00 native *and* at the
  robot's 16 kHz, and `test_live_telehealth_voice.py` speaking the operator's line through the assembled
  appliance (4.28 s, flatness 2.4e-02 against a 1e-06 tone floor). 8 gateway calls, the budget exactly.
  **#82 is the one that had not landed**: driving the shipped page in a real Chromium showed the child
  *still* being cut off — `stop()` 2237 ms into a 2519 ms clip — and her second line clearing being
  **dropped in silence** (`speakClipOnly` refuses to play over Moxie) by ~700 ms of load latency. §2b of
  `test_fallback_coverage.mjs` compared the scripted gap to the clip duration with **no margin**, but a
  clip does not start at its scripted `t`: manifest + MP3 + decode measured ~900 ms warm on localhost,
  while `stop()` is synchronous — a ~260 ms deficit against shipped margins of 181 ms and 193 ms. Fixed
  with a 1 s margin, a check of the dropped direction that did not exist, and `demo.json` retimed
  (3000/7000/8400 → 4200/8800/11400); verified in both directions. `sim/tests/test_sil_child_voice.py` is
  the standing proof — real PCM, real amplitude, reaching `ctx.destination`, uncut. **Honestly not
  proven:** nothing here was heard by ear (a headless browser has no speaker), no physical robot was in
  any loop, and `test_live_gateway_stt.py`'s 400-downgrade test was deselected to stay inside the call
  budget. Suite unchanged at 4091 passed / 27 skipped; guards green; the wheel carries every
  `moxie_sdk` data file and imports clean in a venv holding only `paho-mqtt`.
  **One finding left OPEN, and it deserves its own slice: the SIL tier's own documented command is not
  creds-free on a developer box.** `python3 -m pytest sim/tests -q` — what `sim/ci/ci.yml`'s SIL job
  runs — gave **9 failed, 4170 passed, 10 skipped, 4 errors** here, twice, in
  `test_live_voice_picker.py` and `test_live_gateway_turn_e2e.py`. Every one of those files passes **in
  isolation** (`test_live_voice_picker.py`: 4 passed), and the cause was not isolated — the obvious
  suspect, `test_live_gateway_stt._config()` leaving `config` reloaded under a pin, was reproduced
  deliberately and does **not** do it, because the picker's own fixture reloads `config` again. This is
  rule 8 one level deeper than it is written: rule 8 warns that a careless full-suite run spends gateway
  calls, but the command that spends them here is the *tier's own*, and it is green in CI only because CI
  has no `mqtt/.env` to find. So a cross-test interaction among the live suites is invisible to every
  tier and shows up only on the one machine that has credentials. Not chased further because doing so
  costs gateway calls on a failure outside this pass's four risks — but it is real, it is reproducible,
  and it should be briefed rather than rediscovered.

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
  which is exactly what P1's TTS cache is for; and ~~(c) **the child is still mute**~~ —
  **closed 2026-09-03, see the row above**: the child speaks through `speakClipOnly`, a clip-only
  entry point with no route to a synthesizer. Neither (a) nor (b) is a regression and neither is
  silent *where it used to speak*; they are the honest remainder of "a real voice for the lines it
  plays".

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
- **hosted safety floor — the invisible-character bypass is closed (2026-09-03); the local one is
  not.** The bullet above lists "obfuscation past its normalizer" as a known limit, and on the
  hosted side one specific case of it was not a limit but a hole: `functions/api/_lib/safety.js`'s
  `ALWAYS` stripped exactly four zero-width code points (U+200B/C/D, U+FEFF), so `"suicide"` blocked
  and the same word with a U+00AD SOFT HYPHEN or a U+2060 WORD JOINER between each letter did not —
  identical on screen, and `self_harm` is the FIRST blocking category on a live child-facing demo.
  `ALWAYS` now strips the whole Unicode **`Cf`** category (probed against V8 11.3 rather than assumed
  — including U+180E, which was `Zs` before Unicode 6.3) plus the four glyphless Hangul fillers
  (U+115F/U+1160/U+3164/U+FFA0), which are `Lo` and so outside it; `variants()` gained a fourth form
  that folds separators a writer put *inside* a word, closing `s.u.i.c.i.d.e` and `s-u-i-c-i-d-e`.
  **The narrow transform was chosen on measurement:** dropping *all* punctuation, the obvious version,
  also deletes the boundary between sentences and turned two innocent corpus sentences ("that's what
  i want. To die of laughter would be great") into `self_harm` blocks — a child told to go find a
  grown-up for saying something ordinary is a real harm, not a safe default. `sim/test_demo_proxy.mjs`
  carries the 25-character evasion table, the `Zs` proof (an exotic space folds onto a *real* space,
  which is correct) and an 18-sentence false-positive guard; 47 of its assertions are red against the
  pre-change module. **Two honest gaps.** Intra-letter *spacing* (`s u i c i d e`) is still open and
  deliberately so — it renders visibly, is identical to typing real spaces, and closing it means
  deleting spaces from every utterance; a test pins it as known rather than leaving it to be
  discovered. And `mqtt/moxie_sdk/safety.py::normalize`/`_variants` still carry the old narrow
  behaviour, so the hosted demo now blocks a strict **superset** of the local stack — the safe
  direction, but a divergence the header of `safety.js` promises does not exist, and the same fix
  belongs on the Python side.
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

## DoD progress (audited 2026-09-04 00:40 PDT, at v0.7.0) — **5/6 🟢 · overall ≈ 92%** (done = all six 🟢)

> **Criterion 6 is green, and it was earned in the place it used to be false.** The day the merge gate was a
> `grep` is fixed and the fix has since caught a genuine red; the three flake classes are fenced by ratchets that
> can only shrink; and the acceptance property the config slice existed to create is now verified where it used
> to fail silently — the hermetic suite gives an **identical 4020 passed / 16 skipped with and without a real
> `mqtt/.env` present**, which is the exact shape in which twelve tests once asserted nothing. The one-command
> stack passes in **both** compose modes, the wheel carries its data files, and a live turn answered through the
> real gateway brain **and** the real gateway voice (`piper-amy`, 141 kB @ 22050 Hz, flatness 7.35e-02 — speech,
> not a tone). Criterion 1 stays honestly amber: its ceiling is a physical robot we have never had, and the
> hosted mic→gateway join still has no human recording through it.
>
> **The "nothing verified against a real Cloudflare deployment" gap closed on 2026-09-03 and is recorded here
> exactly as far as it goes.** The five `DEMO_*` variables are set on Production (all as `secret_text` — the API
> silently drops `plain_text` on this project, which cost two false confirmations before a read-back caught it),
> Preview is deliberately empty, and the public domain now answers for real: `GET /api/health` → `mode: live`;
> `POST /api/chat` → **200 in 1.23 s** with *"Hello there! My name is Moxie. What's yours?"* and behaviour
> markup; `POST /api/speech` → **200 in 2.76 s, 257 742 bytes** redeemed against that turn's ticket. A leak sweep
> over both bodies and every header found no key, no gateway host, no model id and no Tailscale address. Environment
> separation is proven for the first time — the keyless preview answers `gateway_not_configured` while Production
> answers `live`, a control that was impossible while neither had variables (assumption 11 can close).
>
> **The ears were closed the same evening, as a loop rather than a probe.** `/api/transcribe` had never been
> exercised against the live deployment; it now has been, by making Moxie listen to herself. One live chat turn
> (*"The quick brown fox jumps over the lazy dog. What a fun sentence!"*), redeemed for audio through
> `/api/speech` (237 800 B PCM @ 22 050 Hz, 5.39 s), resampled to the **16 kHz mono RIFF/WAVE** the gateway
> actually accepts (§10 assumption 15: it rejects webm/Opus, ogg/Opus and mp4/AAC), and posted back to
> `/api/transcribe` → **200 in 2.93 s**, transcript *"The quick brown fox jumps over the lazy dog. What a fun
> sentence."* — word-perfect, differing only in the closing punctuation. No key, gateway host, STT model id or
> Tailscale address in the body or any header. **All three hosted routes are now proven on the public domain.**
> That response also carried `load.inflight: 1`, **which is NOT evidence for PR #104 and was briefly recorded
> here as though it were.** `transcribe.js` takes its `load` from `admit()` in `_lib/limits.js`, which has
> always reported the real in-flight count; the stub PR #104 replaced was in `health.js` alone. Production
> serves from `main`, which at the time of this test did not carry #104 at all — so the number proves the
> admission counter, not the health wiring. Corrected the same evening, before promotion.
>
> **DRIVING THE LIVE PAGE IN A REAL BROWSER MOVED THIS SCORE DOWN, AND THAT IS THE POINT.** Everything
> above was measured with `curl`. On 2026-09-04 the hosted `/sim` was driven with headless Chromium across
> seven viewports, and three defects appeared that no server-side test could have found:
>
> 1. ~~**The only door to the brain is the microphone.**~~ **FIXED — PR #112 (2026-09-04).** Recorded in
>    full because the *shape* is the lesson, not the bug. Typing into `#speech-input` and pressing Say never
>    calls `/api/chat` at all — it targets a local Piper sidecar on `:8081`, which CSP correctly blocks, so
>    nothing plays and the only feedback is a console error. `cloud-transport.js`:339 states it outright
>    (*"the page has no 'type a sentence to Moxie' control today"*) and `mic.js`:157 is the sole caller of
>    `sendUserTurn`. A visitor with no microphone, or who denies the permission, **cannot use the demo**.
>    That is criterion 1 failing on the hosted path for a whole class of visitor, which is why this audit
>    scores 92% rather than 94% — nothing regressed; the measurement got honest.
> 2. ~~**On phones the env banner covers the rail toggle.**~~ **FIXED — PR #112 (2026-09-04),** verified on
>    the preview: `elementFromPoint` at the toggle's centre now returns the toggle at 360/375/414 px and a
>    real `tap()` opens the rail. The layout suite's teeth restore the old offset and the collision returns. `document.elementFromPoint()` at the toggle's
>    centre returns `div#env-banner`. The tap does nothing, silently. Recoverable — dismissing the banner
>    frees it and the mic then works — but nothing tells a visitor that.
> 3. **The safety floor's hard block is defeated by one invisible character.** Verified against the real
>    module: `"suicide"` blocks, the same word with U+00AD SOFT HYPHEN or U+2060 WORD JOINER between each
>    letter does **not** (U+200B is handled). `safety.js`:60-63 strips four zero-width characters and misses
>    the rest of the Unicode format class. `self_harm` is the first blocking category and this floor runs
>    *before* the gateway, so the pre-inference block is what fails.
>
> **What the same sweep proved GOOD**, so the score is not read as decay: mic → STT → brain → TTS works end
> to end on the public domain in 3/3 runs, with audio decoded and played (asserted at the Web Audio layer,
> where a silent clip would fail); **zero horizontal overflow at any of the seven viewports**; no leak of
> key, gateway host, model id or Tailscale address in the page, any API body, or any header; and
> `/api/health` reported `inflight: 1` **during an actual in-flight turn**, which the old stub could not do.
>
> **UPDATE, same night.** #1 and #2 are fixed and verified on the preview: the Say button became **Ask**,
> routed through the same `sendUserTurn` → `admit()` gate as the mic (so typing is not a cheaper way to
> spend the gateway), `/api/chat` is called once with the typed words and `/api/speech` once by ticket,
> playing a buffer at **peak 0.800 of full scale** — an assertion a silent clip fails. The controls that
> could never work off-localhost are now **disabled** rather than merely hinted, and the `:8081` probe is
> gone at the root, so the CSP console error is gone with it. `_headers` gained HSTS and a real
> `script-src`/`default-src`. **#3, the safety bypass, is fixed in PR #113 but is the more important find.**
> **Still open and named:** `/api/*` carries no HSTS or CSP (confirmed live — it needs `envelope.js`);
> `'unsafe-inline'` remains for scripts (14 inline blocks, and a static `_headers` cannot carry a nonce);
> `mic.js` still spends a full turn on a scripted line the visitor never said; and no *human* has recorded
> through the hosted mic.
>
> **What is still NOT covered:** no *human* has recorded through the hosted mic — this loop used synthesized
> speech and a hand-built WAV, so it proves the route and the gateway, not `MediaRecorder` in a real browser on
> a real microphone. And a 39-agent adversarial audit
> of that live surface returned **23 confirmed findings (0 critical, 0 high)**; two are fixed (PR #104, PR #105),
> the rest are filed. A deployment that answers is not the same as a deployment that is finished.

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

**Production hardening P0 — 🟢 done (2026-09-03).** The supervisor stops dying when the broker is late,
stops lying when the socket is dead, stops answering a question the child abandoned, and stops losing a
write to a second process it did not know was there. Built to
[`backlog/production-hardening.md`](backlog/production-hardening.md) §3–§4, whose §3 decision —
advisory `flock` on a per-record `.lock` sidecar behind a public `JsonStore.transaction()`, JSON staying
on disk, over WAL-SQLite and over a single-writer rule — was implemented as written; **none of §3.2's
three falsifiers appeared** (no caller wants two-collection atomicity, `/data` is not on NFS, the console
still does not write this tree). **Three real bugs closed**, each with a test that failed first:
`_on_connect` logged *"broker connected"* for a CONNACK **refusal** and subscribed into a closing socket;
the wakeup route answered `published: true` into a dead socket because its guard was `client is None`
rather than `is_connected()` — PR #55's own bug surviving in the one place that fix did not look, with
all eight `publish()` sites ignoring `info.rc`; and `connect_async` alone is a **no-op** under
`loop_forever()` unless `retry_first_connection=True`. Measured: on `origin/dev`, two processes × 500
`append`s lost **500 of 1 000** every run; now 0. +40 tests
([`test_store_concurrency.py`](../../sim/tests/test_store_concurrency.py),
[`test_connection_resilience.py`](../../sim/tests/test_connection_resilience.py)) and **35 mutations, 0
missed** — which found five holes, four of them two guards each covering for the other's absence.
**Honest ceiling, unchanged:** no physical Moxie has ever been on our broker, the P1 soak that stands in
for *"a week"* does not exist, and both `MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` and the 60 s reconnect ceiling
are **chosen, not measured**. `/data` on a network filesystem is declared unsupported. The one number
that *is* measured is the lock's backoff cadence (0.5 ms / 2 ms), because `flock` has no queue and a
coarse poller starves against a tight writer.

**Production hardening P1 — 🟢 done (2026-09-03).** §5's soak exists and **runs**, the roster and the
connection history are durable, and the appliance stops politely. `sim/run_soak.sh` +
[`sim/tools/soak.py`](../../sim/tools/soak.py): real mosquitto, real `mqtt/run.py`, real virtual robots,
three profiles (`smoke` ~1 min · `quick` ~5 min · `week` 60 min = §5.2's table), every acceptance bar
computed and printed **pass or fail, never inferred**, with §5.4 printed under every report so no number
can be quoted without the sentence that says it is an hour at a raised rate against a simulator.
**Measured (`quick`, 301 s):** 1 046 turns answered while the broker was up, **0 lost**; 4 broker restarts
→ re-subscribed **p95 0.62 s / max 0.62 s**; 2 SIGTERM restarts → roster resume **≤ 1.02 s**; 4 processes
× 250 appends on **one** record → **0 lost** (998 on disk + 2 refused-and-recorded); 10 mid-write
`SIGKILL`s → **0** unreadable records; RSS **+3.2 %**; file descriptors **+0**; **0** tracebacks. Also
built: a **durable robot roster** (15th collection) so a restart re-pushes config instead of waiting for
an event that, after a supervisor restart, never comes; a **connection telemetry stream** (16th
collection — connects, disconnects, CONNACK reason codes, gap durations, dropped publishes, lock waits) on
`GET /conn` and as a strip on the console's 📈 card; and a **SIGTERM/SIGINT handler** that `disconnect()`s
so the broker logs the close now rather than at the 45 s keepalive expiry (measured: rc=0 in ~0.5 s, even
mid-reconnect, which is the case `docker stop` hits). **What it found is worth more than what it built:**
two defects reported a day apart were **one** defect — *a cached belief about the robot's state outliving
the robot's actual state*. A robot returning with the same id after a broker restart was **never
re-onboarded** (no config push, no `app.on_connect`, `/status` calling it present), and the vision/STT
subscription latch was **never cleared** (so eyes went silent after a module exit, a wake or an outage —
upstream openmoxie PR #59 diagnoses the same shape from four owner reports). Both now clear through one
rule at the moment connection continuity breaks; membership is kept and **confirmation** is cleared, so a
socket blip costs nothing and a returning child keeps their conversation. A **12th** soak bar exists
because *the other eleven were green while this was happening*. +76 tests and **64 mutations, 0 missed**
([`hardening_p1_mutation_check.py`](../../sim/tools/hardening_p1_mutation_check.py)), three of which found
real holes. **Honest ceiling:** A13 is **unchanged** — P1 built the instrument that would settle
`MOXIE_STORE_LOCK_TIMEOUT_S` (`lock_timeout` rows carrying `waited_s`) and an instrument is not a
measurement; **no physical robot has ever sent this appliance a vision event**, so the latch tests prove
we re-subscribe and never that a robot then delivers; and **not one of the six hardware-gated assumptions
moved**, again.

**Most valuable next slice (2026-09-03, re-ranked — the previous two rankings were stale and cost two agent runs on already-built work; check each backlog spec's own status banner before briefing):** ① **the unreachable child voice** — `audio.js`:160 `speak(text, who)` accepts a `who` and **no caller ever passes `"child"`** (`bridge.js`:300,306 pass nothing; `ambient.js`:106 passes `"ambient"`), so two committed clips are dead weight and the demo speaks with one voice where it was designed for two — small, provable, and visible on the page the owner is trying to launch; ② **live-Sim P1 remainder** — exact counters (the per-IP and concurrency limits are best-effort in-process, and a Worker isolate is not a shared counter), Turnstile, a TTS cache; ③ **sandboxed content extensions** (`backlog/sandboxed-extensions.md` P0 — the declarative rule list; two server-side execution holes have now been closed reactively, so the durable model is worth building); ④ **broker auth** (`backlog/security-broker-auth.md` P1, still blocked on assumptions A1–A4); ⑤ the behavior planner. **Already built, do not re-brief:** content packs P0+P1 (PR #51, hardened #78), the voice + listening picker P0 (PR #48, pinned #77), durable telemetry, the console `wakeup` fix. **Owner-blocked, not agent-blocked:** the three Cloudflare Production variables (`DEMO_GATEWAY_BASE_URL`, `DEMO_GATEWAY_API_KEY` as a secret, `DEMO_CHAT_MODEL`) — every other link in the hosted chain is proven on a real deploy.

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
