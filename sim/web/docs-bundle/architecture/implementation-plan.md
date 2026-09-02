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
| MQTT runtime (connect/config/state/turn) | [mqtt](mqtt-and-conversation.md) · [config](config-and-telemetry-contract.md) | 🟡 core works + end-to-end turn test (lazy client → integration-testable, no broker) | `mqtt/supervisor/moxie_runtime.py` |
| AI seam — LLM brain | [ai-seam](ai-seam.md) §2 | 🟢 expressive + ResultCodes/actions/scored-output; ERROR_OFFLINE fallback | `mqtt/moxie_sdk/apps/llm_app.py` |
| AI seam — STT in | [ai-seam](ai-seam.md) §1 | 🟢 seam + runtime-wired + **real zmqSTTRequest protobuf decode** (dep-free) + JSON bridge, e2e-tested; live faster-whisper is an optional dep | `mqtt/moxie_sdk/stt.py` + `moxie_runtime.py` |
| AI seam — TTS out (for SIM) | [ai-seam](ai-seam.md) §3 · [sim](sim-as-a-client.md) | 🟢 seam + runtime-wired + **3 backends: built-in tone (zero-dep) · Piper (offline, Amy) · OpenAI-voice (gateway)**; **full audio round-trip proven through a real broker** (SIL smoke `--expect-tts`); real-speech play-through pending a Piper model/creds | `mqtt/moxie_sdk/tts.py` + `moxie_runtime.py` |
| Content-module engine | [content-module](content-module-contract.md) | 🟢 engine + ContentApp, runtime-selectable (MOXIE_APP=content) + example module, e2e-tested through the runtime; exec-code/action-plumbing/summarize deferred | `mqtt/moxie_sdk/content/` + `mqtt/content_modules/` |
| Cloud queries — schedule + `mentor_behaviors` | [mqtt](mqtt-and-conversation.md) · [content-module](content-module-contract.md) | 🟢 the robot gets a **real day plan** and its **own history back**: `build_schedule` plans onboarding + a variety rotation of on-board activities, skipping what this robot already completed (so FTUE ends and nothing repeats); reported `mentor_behavior`s are ingested and served. Deterministic (day+device seeded), not yet LLM-planned | `mqtt/moxie_sdk/schedule.py` + `wire.py` + `moxie_runtime.py::_on_activity` |
| Durable per-robot state | — | 🟡 JSON files under `MOXIE_DATA_DIR` (default `mqtt/data/`), atomic-ish writes, survives restarts — a **stepping stone**, not the database the audit asks for (ADOPT #8) | `mqtt/moxie_sdk/store.py` + [`mqtt/data/`](../../mqtt/data/) |
| Config/telemetry data-model | [config](config-and-telemetry-contract.md) | 🟢 RobotCloudConfig + RobotStatus ingest + **Packet telemetry (build/parse/ingest/summarize) + LoggingPolicy upload-gate**; served to the console as `GET /telemetry` | `mqtt/moxie_sdk/cloud_config.py` + `telemetry.py` |
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

## Known gaps (audited, honest)

Tracked so the status table above isn't over-claimed. Each is a build slice, not a bug:

- **content-module:** `session.summarize()` (the contract's volley/session API) is **not implemented**
  — it needs the brain wired in for LLM transcript-summarization; every other volley/session call exists.
  Arbitrary module `code`-string execution is deliberately deferred (sandboxing); `volley.execution_actions`
  (e.g. `eb_timer_request`) are captured but **not yet plumbed** into `RemoteChatAction` on the wire.
- **ai-seam:** STT seam is built + wired (feed_stt/handle_zmq, e2e via a JSON audio bridge); real zmqSTTRequest protobuf decode is DONE (dep-free field reader in stt.py); only a live faster-whisper test remains (optional dep). TTS out (§3) seam + runtime-wired (synthesize-on-reply → CloudTTSResponse); live voice needs creds + viseme TTSMarks deferred. Input safety/moderation (§2) unbuilt.
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
- **the day plan is deterministic, not intelligent.** `build_schedule` seeds on `(device_id, day)`, so a
  robot gets a stable plan that changes tomorrow — it does **not** read telemetry, mood, time of day or
  parent preference, and it cannot explain *why this activity today*. That recommender is BEYOND #7
  ([`openmoxie-feature-audit.md`](openmoxie-feature-audit.md) §4.2). Two smaller honesties: the
  "already done → don't schedule again" rule applies to the generated rotation and to first-time-user
  onboarding, **not** to fixtures an author pins in `provided_schedule` (e.g. `DM`, which is meant to
  recur daily); and the FTUE completion thresholds (`TNT`=9, `SYSTEMSCHECK`=4 content ids) are
  **OpenMoxie's field-proven constants**, not something our RE docs establish — see the note in
  `mqtt/moxie_sdk/schedule.py`.
- **the store is JSON files, not a database.** `mqtt/moxie_sdk/store.py` persists per-robot
  `mentor_behaviors` under `MOXIE_DATA_DIR` with atomic-ish writes and a 500-record cap. It has no
  indexes, no queries, no migrations, and no concurrent-writer story beyond a single process's lock —
  it exists so ADOPT #1/#2 could ship without blocking on ADOPT #8's real database. Conversation memory
  (`MOXIE_MEMORY_DIR`) and telemetry still use their own paths; folding all three onto one durable
  store is the next slice.

## DoD progress (audited 2026-09-02) — ≈ 57%

| # | Criterion | Status | Notes |
|--:|---|---|---|
| 1 | Talk end-to-end (mic→STT→brain→markup→TTS→SIM/robot) | 🟡 ~85% | brain live-validated 🟢; STT incl. **real zmqSTT protobuf decode** 🟢; **full audio round-trip proven through a real broker** — supervisor synthesizes → `CloudTTSResponse` on `/commands/tts` → SIM decodes audio (SIL smoke asserts it via `--expect-tts`) 🟢. **The browser SIM now SPEAKS it** 🟢: `sim/web/audio.js::playCloudTTS` decodes the base64 raw 16-bit PCM itself (no server SDK, like firmware), plays it on the shared Web Audio context in `chunk_num` order, and lip-syncs the face from `marks[]` (envelope-driven when a voice sends none) — `bridge.js` routes `/commands/tts` in; **live-observed** against a real broker + supervisor (`MOXIE_TTS=tone`): 4.0 s @ 22050 Hz mono played in Chromium, mouth animating, zero console errors. Three voices: built-in **tone** (zero-dep), **Piper/Amy** (offline), gateway. Remaining: one live talk-through with **real speech** (mic → Piper/gateway voice), which needs a Piper model or a gateway TTS model, not new code |
| 2 | Data-driven content | 🟢 | M2 engine + ContentApp, e2e-tested |
| 3 | Cloud management (console + config/telemetry) | 🟢 | RobotCloudConfig + RobotStatus + status snapshot + Packet telemetry + LoggingPolicy gate 🟢. The console surfaces **live state** (`/local/fleet`), **edits config** (Settings form → `POST /local/robots/{id}/config` → runtime `POST /config` → `update_config` re-pushes `RobotCloudConfig`), and shows **telemetry insights** (`summarize_events` → runtime `GET /telemetry` → `GET /local/robots/{id}/telemetry` → the 📈 Insights panel) — all three live-verified against a real broker. Caveat: telemetry is in-memory (last 50 events/robot), not persisted |
| 4 | Interchangeable SIM/robot clients | 🟢 | backend is client-agnostic; SIM round-trips the real protocol |
| 5 | One-command stack | 🟢 | `docker compose up` (repo root) = broker + supervisor + parent console, one `.env`, healthchecks + named volumes. **Proven** by [`sim/run_compose_smoke.sh`](../../sim/run_compose_smoke.sh): build → health → `virtual_moxie --expect-tts` round-trip through the composed broker → the robot visible in the console's `/local/fleet` → `down -v`; shape asserted hermetically by `sim/tests/test_compose.py`. Guide: [`../guides/one-command-stack.md`](../guides/one-command-stack.md). Caveats (documented, not hidden): the `content`/`llm` brains still need a gateway key to say anything real (keyless → the "brain got fuzzy" fallback, which is why the smoke uses `echo`), and the `voice`/`stt` profiles each need one `.env` line + `up --build` rather than the profile flag alone |
| 6 | Green + live-tested | 🟡 ~80% | **three-tier CI installed + green** (fast on dev · deep+HIL on PR-to-main · release on tags); hermetic suite green (210 pass · 6 live tests skipped without a key). **Live-proven against the real gateway** (`sim/tests/test_live_action_tags.py`, `test_live_content_e2e.py`; skip cleanly with no key): LLM **action tags** now emitted by graphling-medium — 4/4 goodbye→`<exit>`, 3/3 activity→`<launch:DRAW>` after prompt-only work in `LLMApp._system` (baseline was 0/3 and 0/2; the fix is writing the tag **before** the sentence, not after), and the action reaches the wire as `response_actions` 🟢; the shipped `content_modules/starter.json` through the **real** `MoxieRuntime` returns a spec-conformant `RemoteChatResponse` (SUCCESS, 123-char `output.text` + `markup`) with a `globals[]` short-circuit costing 0 LLM calls 🟢. Console↔runtime contract e2e (`test_console_roundtrip.py`): fleet / config (incl. 400 on bad input) / telemetry round-trip in-process against a status-server double that is **key-diffed against the real runtime** 🟢. SIL smoke + both scenarios + `python -m build` green. **Live voice is now proven with REAL SPEECH** (`sim/tests/test_live_talk_e2e.py` + `sim/tests/helpers_audio.py`; skips cleanly without piper / faster-whisper / the `.onnx` / a key). Tier 1 — Piper (Amy) speaks, resampled to the robot's 16 kHz, and faster-whisper `base.en` (int8/CPU) reads it back **verbatim: word overlap 1.00** (floor 0.7) 🟢; a companion test proves the suite cannot pass on the placeholder — `ToneSynthesizer` spectral flatness 3.1e-12 vs Amy's 5.0e-2, zero-crossing σ 0.0011 vs 0.182, and Whisper hears `''` in the tone. Tier 2 — the whole talk loop through the **real `MoxieRuntime`**: a second, different Piper voice (Lessac) plays the child, its audio is chopped into `zmqSTTRequest` protobuf frames (START_OF_SPEECH / SPEECH… / END_OF_SPEECH, hand-encoded to mirror `decode_zmq_stt_frame`, no protobuf dep) and pushed at `_on_event(dev, "zmq", …)` — the exact path `_on_message` uses for a robot's `events/zmq`; the runtime answers a FINAL `zmqSTTResponse`, the transcript drives the turn, and a spec `RemoteChatResponse` plus a `CloudTTSResponse` come back, whose audio is transcribed AGAIN and matched to the reply. Shipped `starter.json` `globals[]` variant (0 gateway calls): heard *'Hey Moxie, please set a timer for 5 minutes.'* (overlap 1.00), spoke back *'Okay, a timer for 5 minutes. Go race a robot.'* (overlap 1.00, 193 536 B / 4.39 s) 🟢. Live-brain variant (1 gateway call, same module): heard *'Hi Moxie, tell me a joke.'* (1.00) → *"Sure, Sam! Why don't scientists trust atoms? Because they make up everything!"* spoken back at overlap 1.00 (425 984 B / 9.66 s) 🟢. **Timing reality check (the ~20 s reprompt window): speech is not the problem — the brain is.** STT 0.59–0.71 s, TTS 0.12–0.36 s, TTS→STT 0.70 s, model+voice load ~1 s each (cached), but the one live gateway completion took **45.1 s** vs 0.58 s for the global handler — a real live turn is already past the reprompt window on gateway latency alone 🔴. SIL smoke re-run with the Piper voice (`MOXIE_PIPER_MODEL=sim/tts/voices/en_US-amy-medium.onnx`, port 1952) spoke **82 432 B @ 22050 Hz (~1.87 s)** vs the tone's 50 934 B (~1.15 s) — different audio, same wire. `mqtt/.env.example` now documents the STT half of the seam (`MOXIE_STT`, `MOXIE_STT_MODEL`) and that `MOXIE_PIPER_MODEL` — not `MOXIE_TTS` — is what enables Piper. Remaining (NOT proven): audio from a **real microphone / real robot** (both ends here are synthesized speech), streaming/partial transcripts + barge-in (only the END_OF_SPEECH one-shot is exercised), and `marks[]` is still empty so there are no visemes; emoji in a model reply are read aloud by Piper (*'grinning face with smiling eyes'*) — the markup stripper should drop them. Live tests need creds so CI still runs them skipped |

**Most valuable next slice:** criterion 1's **one live talk-through with real speech**. Browser Web-Audio
playback of the `CloudTTSResponse` is now **done** — the SIM decodes the server's PCM and speaks it, with the
mouth animating, observed live against a real broker + supervisor — so the last client-side link is closed and
every hop of `mic → STT → brain → markup → TTS → SIM` exists in code and is tested. What is *not* yet proven is
the same loop end to end **with a microphone and a real voice** rather than the zero-dependency tone synth: run
the `--profile voice` stack (it downloads Piper/Amy), speak into the SIM's mic, and confirm a child could hold
the conversation. That is a *doing* task, not a building one — the only gated alternative is the gateway voice,
which still needs a TTS model registered (handoff doc:
[`../guides/litellm-tts-setup.md`](../guides/litellm-tts-setup.md)). Honest caveat on what was just built: the
tone voice is a placeholder sound, not speech, and `marks[]`-driven visemes are implemented but untested against
a synthesizer that actually emits marks (neither Piper nor the tone synth does yet) — the mouth falls back to
the audio envelope, which is what the live run exercised. M7's one-command stack (criterion 5) is done.

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
