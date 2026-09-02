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
| AI seam — STT in | [ai-seam](ai-seam.md) §1 | 🟡 seam + wired into runtime (feed_stt → publishes zmqSTTResponse; JSON-frame bridge e2e-tested); real zmqSTTRequest protobuf decode + live whisper remain | `mqtt/moxie_sdk/stt.py` + `moxie_runtime.py` |
| AI seam — TTS out (for SIM) | [ai-seam](ai-seam.md) §3 · [sim](sim-as-a-client.md) | 🟡 seam + **wired into runtime** (set_synthesizer → synthesize-on-reply → CloudTTSResponse on /commands/tts); live voice needs creds | `mqtt/moxie_sdk/tts.py` + `moxie_runtime.py` |
| Content-module engine | [content-module](content-module-contract.md) | 🟢 engine + ContentApp, runtime-selectable (MOXIE_APP=content) + example module, e2e-tested through the runtime; exec-code/action-plumbing/summarize deferred | `mqtt/moxie_sdk/content/` + `mqtt/content_modules/` |
| Config/telemetry data-model | [config](config-and-telemetry-contract.md) | 🟢 RobotCloudConfig + RobotStatus ingest + **Packet telemetry (build/parse/ingest) + LoggingPolicy upload-gate** | `mqtt/moxie_sdk/cloud_config.py` + `telemetry.py` |
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
- **M3 — AI seam: STT in. 🟡 (seam 2026-09-01)** Turn `handle_zmq` into a real STT path — accumulate `zmqSTT` audio →
  transcribe (faster-whisper local, or a Deepgram-shaped proxy) → emit the recognized turn.
- **M4 — AI seam: TTS out for the SIM. 🟡 (seam 2026-09-01)** Server-side Piper → `CloudTTSResponse{audio, marks}` so the
  SIM (and optionally a robot) speaks with a server voice + viseme marks.
- **M5 — Config & telemetry. 🟡 (config+state 2026-09-01)** Full `RobotCloudConfig` (bedtime/wake/volume/timezone/child_pii), `/state`
  ingest, the `Packet` telemetry envelope, and the LoggingPolicy gate.
- **M6 — Parent console wiring. 🟡 (backend 2026-09-01)** Surface robot state + config editing + insights in `server/`'s web UI.
- **M7 — One-command stack + docs.** `docker compose up` runs broker + supervisor + brain + STT/TTS; the
  SIM and a real robot connect identically; deploy/config guides.

## Known gaps (audited, honest)

Tracked so the status table above isn't over-claimed. Each is a build slice, not a bug:

- **content-module:** `session.summarize()` (the contract's volley/session API) is **not implemented**
  — it needs the brain wired in for LLM transcript-summarization; every other volley/session call exists.
  Arbitrary module `code`-string execution is deliberately deferred (sandboxing); `volley.execution_actions`
  (e.g. `eb_timer_request`) are captured but **not yet plumbed** into `RemoteChatAction` on the wire.
- **ai-seam:** STT seam is built + wired (feed_stt/handle_zmq, e2e via a JSON audio bridge); the remaining wire step is decoding the real **zmqSTTRequest protobuf** off events/zmq (needs the compiled proto) + a live faster-whisper test. TTS out (§3) seam + runtime-wired (synthesize-on-reply → CloudTTSResponse); live voice needs creds + viseme TTSMarks deferred. Input safety/moderation (§2) unbuilt.
- **config/telemetry:** RobotCloudConfig + RobotStatus ingest + Packet telemetry (build/parse/runtime-ingest) + the LoggingPolicy upload-gate are built (M5 🟢). Remaining: server-side insights UI (M6) surfaces the stored telemetry.

## DoD progress (audited 2026-09-01) — ≈ 45%

| # | Criterion | Status | Notes |
|--:|---|---|---|
| 1 | Talk end-to-end (mic→STT→brain→markup→TTS→SIM/robot) | 🟡 ~50% | brain live-validated 🟢; STT + TTS **seams** wired but not a full live chain (needs voice server + SIM-audio wiring + real zmqSTT protobuf decode) |
| 2 | Data-driven content | 🟢 | M2 engine + ContentApp, e2e-tested |
| 3 | Cloud management (console + config/telemetry) | 🟡 ~55% | RobotCloudConfig + RobotStatus + **config-editing (update_config) + status snapshot** built; Packet telemetry + server/ UI wiring next |
| 4 | Interchangeable SIM/robot clients | 🟢 | backend is client-agnostic; SIM round-trips the real protocol |
| 5 | One-command stack | 🟡 | compose exists; full brain+STT+TTS one-command unverified (M7) |
| 6 | Green + live-tested | 🟡 | CI green + live LLM turn 🟢; live voice + a full e2e scenario pending |

**Most valuable next slice:** criterion 3 is weakest and fully unblocked → **M5 config/telemetry**
(RobotCloudConfig round-trip + /state ingest + LoggingPolicy). Criterion 1 (talk-e2e) is gated on the
voice-server creds for live TTS; the CloudTTSRequest runtime handler can still land with a stub.

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
