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
| AI seam — LLM brain | [ai-seam](ai-seam.md) §2 · [mqtt §4.5](mqtt-and-conversation.md#45-slow-brain-a-filler-now-the-real-answer-next-reply_pending) | 🟢 expressive + ResultCodes/actions/scored-output; ERROR_OFFLINE fallback; **latency budget** (`MOXIE_BRAIN_BUDGET_S`, default 6 s) — a slower brain speaks a rotating filler as `REPLY_PENDING` chunk 0, with a stale-turn guard, every chunk synthesized. **Streaming** (`MOXIE_STREAMING`, default on): each finished sentence is published as its own `REPLY_PENDING` chunk as the model writes it, closed by a `SUCCESS` + `is_completed`; the filler timer re-arms per chunk (cap 2/turn) and a newer turn cancels the stream mid-answer. **Live-proven:** filler at 3.0 s + answer at 17.9 s (blocking, PR #14); streamed, first sentence at **1.52 s** vs whole answer at **4.38 s** on a healthy gateway (PR #15) | `mqtt/moxie_sdk/apps/llm_app.py` + `segment.py` + `chat.stream_completion` + `filler.py` + `moxie_runtime.py::_handle_stream_turn` |
| AI seam — presence (vision events) | [ai-seam](ai-seam.md) §2 · [vision](vision.md) §7 · [mqtt §4.7](mqtt-and-conversation.md#47-vision-events-and-whether-the-cloud-may-speak-first-built-v1-2026-09-02) | 🟡 **built, unproven against hardware** (audit BEYOND #9): the runtime subscribes the robot to its own vision events (`EventSubscription.active[]`, once per `(device, module_id)`) and routes `eb-found-face`/`eb-lost-target`/`eb-qr-event`/`eb-dr-event`/`eb-br-event` — which arrive as the `speech` of a `RemoteChatRequest`, not a topic of their own — into a pure presence state machine with hysteresis (a face blinking at the frame edge yields **one** `arrived` + **one** `left`, not twenty). `Turn.presence` carries a resolved snapshot into the prompt, and its `line` is **empty on most turns by design**. An `arrived` after ≥ `MOXIE_GREET_AFTER_S` (default 300 s, 0 = off) earns **one** unprompted hello — answered on the arrival event's own `event_id` (an unsolicited publish is *not* established as legal — see vision.md §7.4), rate-limited once per absence, never over a turn, never unpermitted, never in bedtime hours. **Honest ceiling:** no physical robot has ever sent us one of these events; the whole path is exercised by the SIL robot (`--face-event`) and the browser SIM, which proves consistency, not hardware truth | `mqtt/moxie_sdk/presence.py` + `moxie_runtime.py::_on_vision_turn`/`_greeting_for` + `wire.py` + `apps/llm_app.py::_system` + `sim/virtual_moxie.py` + `sim/web/bridge.js` |
| AI seam — expressive markup | [ai-seam](ai-seam.md) §2 · [mqtt §4.6](mqtt-and-conversation.md#46-the-markup-floor-built-v1-2026-09-02) | 🟢 the **markup floor**: `supervisor/markup.py` is no longer a passthrough — one pure, deterministic, stdlib-only generator (`annotate`) performs every reply that does not bring its own markup, so the echo/content/webhook apps stopped speaking flat and `LLMApp` stopped being a second generator (the model's mood/gesture are now *hints* into the same floor; `stream_style` deleted). Per line: a mood from `ePlaybackMood` 0-10, a `<usel>` delivery on a question or an exclamation, arm gestures on the carrying words, a `<break>` at an internal boundary (never after the final word), at most one whole-body `Bht_*`, a closing `Gesture_None`. Every id validated against a frozen, doc-cited catalog (`vocab.py`); an id a brain invents is dropped, never forwarded. Deterministic via `blake2b`, never `hash()`. **Measured p95 0.23 ms/line** (1 ms budget), no model call, no dependency. 8 byte-exact goldens + 277 hermetic cases; the goldens render six distinct faces through the real browser bridge. `MOXIE_AUTOMARKUP=0` restores the passthrough | `mqtt/moxie_sdk/automarkup.py` + `vocab.py` + `supervisor/markup.py` + `apps/llm_app.py::build_markup` + `sim/tests/test_automarkup.py` + `sim/test_automarkup_render.mjs` |
| AI seam — input safety | [ai-seam](ai-seam.md) §2 · [mqtt §4.5](mqtt-and-conversation.md#safety-on-the-wire-inputsafety-inputsafety) | 🟢 `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` **enforced**, not just specified: assessed pre-inference (a hard block never reaches a model) and **per streamed chunk** before publication (blocked chunk never goes out; a safe line closes the sequence with `SUCCESS`+`is_completed`; the stream is cancelled). 8 categories with a per-side block/flag policy in a parent-readable `safety_rules.json`, normalization + false-positive guards, a `Classifier` protocol for a drop-in local model, and a parent review queue (`GET|POST /safety` → console 🛡️ panel, `NO_DATA` = counts only). **Live-proven:** an unsafe request cost **0 gateway calls** and got a redirect + `input.safety`; a benign turn in the same run streamed 4 clean chunks | `mqtt/moxie_sdk/safety.py` + `safety_rules.json` + `moxie_runtime.py::_safety_gate_input`/`_handle_stream_turn` + `server/moxie_server/fleet.py` |
| AI seam — STT in | [ai-seam](ai-seam.md) §1 | 🟢 seam + runtime-wired + **real zmqSTTRequest protobuf decode** (dep-free) + JSON bridge, e2e-tested; live faster-whisper is an optional dep | `mqtt/moxie_sdk/stt.py` + `moxie_runtime.py` |
| AI seam — TTS out (for SIM) | [ai-seam](ai-seam.md) §3 · [sim](sim-as-a-client.md) | 🟢 seam + runtime-wired + **3 backends: built-in tone (zero-dep) · Piper (offline, Amy) · OpenAI-voice (gateway)**; **full audio round-trip proven through a real broker** (SIL smoke `--expect-tts`); **the gateway voice is live-proven (2026-09-02)** — `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL=piper-amy` is the whole switch, its WAV is unwrapped to the header's true 22050 Hz, Whisper reads the audio back at overlap **1.00**, `piper-ryan` swaps the voice, and a gateway failure downgrades to Piper/tone instead of silence ([guide](../guides/litellm-tts-setup.md)) | `mqtt/moxie_sdk/tts.py` + `moxie_runtime.py` |
| Content-module engine | [content-module](content-module-contract.md) | 🟢 engine + ContentApp, runtime-selectable (MOXIE_APP=content) + example modules, e2e-tested through the runtime. **Memory built (2026-09-02):** `volley.persist_data` (durable, module-namespaced, bounded, `NO_DATA`-gated) + `session.summarize()` at end-of-conversation, served to a parent over `/memory`; ships as `content_modules/memory_chat.json`. **Per-item control (2026-09-02):** every remembered thing carries a stable id and its own provenance, so a parent can **erase or correct one line** (`DELETE …&item=` / `POST {"edit":…}` → the console's ✕/✏️), a correction is pinned, and unused items age out after `MOXIE_MEMORY_MAX_AGE_DAYS` (90). exec-code + action-plumbing still deferred. **Adaptive day plan (2026-09-02):** `schedules[]` is no longer expanded by a `(device_id, day)` rotation — `plan_inputs`/`plan_day` score every candidate on parent request, unfinished FTUE, coverage, recency, completion affinity, category variety and time-of-day fit, never plan into bedtime, and return a parent-readable *"why this activity today"* line per entry (stored as `robots/<id>/schedule_explain.json`, served by `GET /schedule`). The wire is byte-unchanged | `mqtt/moxie_sdk/content/` + `mqtt/moxie_sdk/schedule.py` + `mqtt/moxie_sdk/store.py` + `mqtt/content_modules/` |
| Cloud queries — schedule + `mentor_behaviors` | [mqtt](mqtt-and-conversation.md) · [content-module](content-module-contract.md) | 🟢 the robot gets a **real day plan** and its **own history back**: `build_schedule` plans onboarding + a variety rotation of on-board activities, skipping what this robot already completed (so FTUE ends and nothing repeats); reported `mentor_behavior`s are ingested and served. Deterministic (day+device seeded), not yet LLM-planned | `mqtt/moxie_sdk/schedule.py` + `wire.py` + `moxie_runtime.py::_on_activity` |
| Durable per-robot state | — | 🟡 JSON files under `MOXIE_DATA_DIR` (default `mqtt/data/`), atomic-ish writes, survives restarts — a **stepping stone**, not the database the audit asks for (ADOPT #8) | `mqtt/moxie_sdk/store.py` + [`mqtt/data/`](../../mqtt/data/) |
| Config/telemetry data-model | [config](config-and-telemetry-contract.md) | 🟢 RobotCloudConfig (now incl. **`alarms` + `schedule_preferences`**, contract gap closed) + RobotStatus ingest + **Packet telemetry (build/parse/ingest/summarize) + LoggingPolicy upload-gate**; served to the console as `GET /telemetry`. Config is layered **`defaults ⊕ fleet ⊕ per-robot`** (audit ADOPT #6) — one `fleet/config.json`, `POST /config?scope=fleet`. **Gated by a device allowlist** (audit §3.1, closed by default): only a permitted robot is pushed `child_pii`; an unknown one is *pending* and gets `build_unpaired_cloud_config()` — `fleet/permits.json`, `GET`/`POST /permits`, the console's 🔐 Robot access card, `MOXIE_ALLOW_UNVERIFIED_BOTS` to migrate. **Appearance built (2026-09-02, audit ADOPT #9):** the child's chosen face rides in `child_pii.face_options` and the pushed `child_pii.id` is a deterministic UUIDv5 over the chosen layers, so a look change re-keys the robot's face-texture cache and an idempotent re-push does not — a frozen 14-slot catalog (`MoxieCustomizationType`) with the **12 doc-cited colour options and zero invented asset ids**, layered fleet ⊕ per-robot like every other override, and the console's 🎨 Moxie's look card | `mqtt/moxie_sdk/cloud_config.py` + `faces.py` + `telemetry.py` + `store.py` |
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
  panel (counts by event + a recent-events list). All three live-verified end to end.
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

- **face customization — the plumbing is complete, the vocabulary is 12 of ~60 (2026-09-02).**
  `moxie_sdk/faces.py` ships every slot our docs name (14, `MoxieCustomizationType`) but concrete
  options for only **two** of them — the `EyeColor`/`FaceColor` enums with hex
  (`robot-lifecycle.md`:281-282). The other twelve slots' art loads from a **streamed** bundle
  (`content-delivery.md`:79, `REMOTE_ASSETBUNDLES`) that our corpus has never opened, and
  `behavior-markup.md`:161-163 records that the generators accept *any* id the loaded bundle
  defines — so the id space is bundle-defined and cannot be inferred. We ship no invented ids; a
  parent supplies their own through `face.custom` (shape-checked only, and
  `mqtt-and-conversation.md`:824 warns some assets crash Unity). Two flagged assumptions sit
  behind one function each: the wire spelling of a layer label, and that the texture cache is
  keyed on `child_pii.id` (field-proven via OpenMoxie, not capture-proven). **No physical Moxie
  has rendered any of it** — the whole path is proven against the simulator only. The cheapest
  way to widen the vocabulary is the precedent `backlog/expressiveness.md`:336 already sets for
  SFX: ingest OpenMoxie's asset manifest as *data*, cite it, copy no code — not done here.
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
  would plan against the wrong clock. Also pending: the *"why this activity today"* line has no
  console page yet — `GET /schedule` serves it and `server/` does not read it.
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
- **gateway voice is live; gateway EARS are not.** TTS out through the LiteLLM gateway is proven end
  to end (2026-09-02): `piper-amy`/`piper-ryan` speak, the WAV's own header sets the
  `CloudTTSResponse` rate, and a failure downgrades to Piper/tone rather than silence. **STT on the
  gateway is still WIP** — there is no `/v1/audio/transcriptions` model registered, so the ears remain
  local `faster-whisper` only, and a box with no whisper wheels has a voice but no hearing. Two smaller
  honesties: the gateway is ~3-5× slower than local Piper for the same sentence (~1.3-1.7 s vs a
  few hundred ms), and a 429/5xx path cannot be provoked on demand, so the backoff around the new WAV
  decode is asserted against a fake rather than against the live proxy.
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
- **the pairing gate — an application-layer permit list, and the un-paired value is not
  capture-proven.** The allowlist is built, closed by default and enforced on the transport
  boundary, but three honesties stand. (a) **It is not authentication.** The broker still
  accepts anonymous connections (the robot's RS256 JWT is never verified — [mqtt §3b](mqtt-and-conversation.md)),
  so an unpermitted device can still *connect*, publish, and see its own topics; what the
  gate stops is our server *serving* it — no `child_pii`, no brain, no schedule, no
  telemetry ingest. A device that spoofs a permitted robot's `d_<uuid>` is served as that
  robot. Broker-level password/ACL + real JWT verification is the deeper fix and is still
  deferred. (b) **`pairing_status:"unpairing"` is field-proven, not capture-proven.** No
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
  stored events (M6 🟢). Remaining (not blocking): telemetry is **in-memory only** — the runtime keeps the last 50
  events per robot and loses them on restart or robot disconnect; durable per-session persistence + typed
  `event_data` decoding (LogDevice/LogUser wrappers) are a later slice.
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

## DoD progress (audited 2026-09-02, at v0.7.0) — 4/6 🟢 · overall ≈ 90% (done = all six 🟢)

| # | Criterion | Status | Notes |
|--:|---|---|---|
| 1 | Talk end-to-end (mic→STT→brain→markup→TTS→SIM/robot) | 🟡 ~90% | **Every link is built and live-proven with real speech through the real runtime** (PR #12): Piper "child" audio → zmqSTT protobuf frames → faster-whisper → brain (gateway, live) → spec `RemoteChatResponse` → Piper Amy `CloudTTSResponse` → re-heard at overlap 1.00 (and, since 2026-09-02, the same round trip through the **gateway voice** at overlap 1.00 — `piper-amy`, 22050 Hz, 1.69 s); the browser SIM decodes and **plays** it with mouth animation (PR #11); markup/actions reach the client. Remaining: the same loop on a **physical Moxie** (needs the operator's robot; the SIM stands in). Brain latency is no longer silence *or* a wait: a slow turn speaks a filler inside `MOXIE_BRAIN_BUDGET_S` (live: 3.0 s / 17.9 s) and the answer itself now **streams** a sentence at a time (live: first words at 1.52 s, whole answer at 4.38 s), with the filler timer re-arming per chunk. Remaining honesty: no capture proves a *physical* robot plays chunk 2 of an `event_id` (see Known gaps) |
| 2 | Data-driven content | 🟢 | M2 engine + ContentApp, e2e-tested |
| 3 | Cloud management (console + config/telemetry) | 🟢 | RobotCloudConfig + RobotStatus + status snapshot + Packet telemetry + LoggingPolicy gate 🟢. The console surfaces **live state** (`/local/fleet`), **edits config** (Settings form → `POST /local/robots/{id}/config` → runtime `POST /config` → `update_config` re-pushes `RobotCloudConfig`), and shows **telemetry insights** (`summarize_events` → runtime `GET /telemetry` → `GET /local/robots/{id}/telemetry` → the 📈 Insights panel) — all three live-verified against a real broker. It also **browses and erases long-term memory** (`normalize_memory` → runtime `GET`/`DELETE /memory` → `GET /local/robots/{id}/memory` + `DELETE …/memory[/{namespace}]` → the 🧠 What Moxie remembers card), so BEYOND #4's floor is a parent-facing screen rather than `curl` — live-verified: seeded facts read back per activity with date/module provenance, a namespace erase confirmed on disk and in the next read. Caveats: telemetry is in-memory (last 50 events/robot), not persisted; memory erase or correct any single remembered line (PR #33)|
| 4 | Interchangeable SIM/robot clients | 🟢 | backend is client-agnostic; SIM round-trips the real protocol |
| 5 | One-command stack | 🟢 | `docker compose up` (repo root) = broker + supervisor + parent console, one `.env`, healthchecks + named volumes. **Proven** by [`sim/run_compose_smoke.sh`](../../sim/run_compose_smoke.sh): build → health → `virtual_moxie --expect-tts` round-trip through the composed broker → the robot visible in the console's `/local/fleet` → `down -v`; shape asserted hermetically by `sim/tests/test_compose.py`. Guide: [`../guides/one-command-stack.md`](../guides/one-command-stack.md). Prebuilt path (2026-09-02): [`docker-compose.images.yml`](../../docker-compose.images.yml) is self-contained (broker config inlined, no repo file referenced), so the documented install is *download one file + `docker compose up`*; the release workflow publishes the three multi-arch images to GHCR on every `v*` tag, and `MOXIE_SMOKE_MODE=images sim/run_compose_smoke.sh` runs the full round-trip against it with `pull_policy: never`. Caveats (documented, not hidden): the images **publish on tag and nothing has been tagged yet**, so the registry pull is verified at v0.6.0 (all three multi-arch publishes green); the `content`/`llm` brains still need a gateway key to say anything real (keyless → the "brain got fuzzy" fallback, which is why the smoke uses `echo`); and the `voice`/`stt` profiles each need one `.env` line + `up --build` on the *clone* path — a prebuilt supervisor cannot grow those wheels |
| 6 | Green + live-tested | 🟡 ~90% | Three-tier CI green incl. the compose-stack deep job; hermetic suite ≈281; **live-proven against real infra:** LLM turn, action tags 3/3, content module e2e, console↔runtime round-trip, and **real speech end to end** (Piper→whisper 1.00; **gateway voice→whisper 1.00**, a creds-gated `test_live_gateway_tts.py`; full talk loop with 0 gateway calls and with a live completion; degraded gateway skips, never false-greens). Remaining: live tests need creds so CI runs them skipped (a secrets-gated dispatch exists for the LLM path); a physical-robot HIL job on a self-hosted runner |

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

**Most valuable next slice (2026-09-02, after v0.7.0):** the pairing gate, published images, memory erase/edit/decay, vision events, face customization, the adaptive schedule and the gateway voice have all landed. In value order now: **the voice picker** (`backlog/voice-picker.md`: Speech + Listening dropdowns fed by live gateway discovery + installed local engines, default `piper-amy`/`stt-whisper`, explicit local wins — launches when the STT and telehealth slices land); **puppet / telehealth (ADOPT #7)** — the build-ready brief is `backlog/telehealth.md` (pure `telehealth.py` cross-checked against the recovered `TeleHealth_pb2`, `GET/POST /telehealth`, a 🎭 console card, a SIM handler); **broker authentication P0** (`backlog/security-broker-auth.md`: pattern ACLs on `%c`, a per-appliance supervisor credential, 1883 loopback-bound — containment first, since a stock robot can carry no secret by QR); then a console card for the schedule's *why this activity today* lines (`GET /schedule` serves them, `server/` does not read them yet); then content packs export/import (ADOPT #5). Gateway STT waits for the gateway (WIP).

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
