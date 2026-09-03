# 🎚️ Voice picker — choose the speech and listening models from the console

**Status: ✅ Shipped 2026-09-02** (`feat/voice-picker`) — built as specified: `moxie_sdk/voice_settings.py`,
`config.build_synthesizer/build_transcriber(override=)`, the runtime's 🎚️ region with
`GET /voice` · `POST /voice` · `POST /voice/test`, the console card, and 88 new tests. One deviation
worth naming: **`MOXIE_TTS=off` / `MOXIE_STT=off` still win over a pick** (a deployment that declared
itself voiceless is not talked back into speaking by a dropdown), and a pick that cannot be built on
this box falls through to the env path rather than leaving a child in silence. Local Piper/whisper
entries were exercised with fakes — neither package is installed on the build machine. The live run
also found (and this branch fixed) a cold-start race the brief did not anticipate: a `POST /voice`
issued before the first `GET /v1/models` returns was judged against an empty catalog and refused a
good pick, so a console **write** now waits up to 10 s for that first listing while every read still
answers instantly.
**Corrected 2026-09-03** (`feat/voice-picker`, second pass): as shipped, the pick sat above *every*
env value except `off`, so a console pick of `gateway:piper-ryan` silently overruled an explicit
`MOXIE_TTS=piper` — the owner's "local engines stay first class" said in the one place a deployment
can say it. A picker that overrides an explicit operator setting is a bug, and nothing in the suite
paired an explicit engine with a cross-engine pick. An explicit `MOXIE_TTS`/`MOXIE_STT` now **pins
the engine**: the builders drop a pick that names another engine, `VoiceEngines.available()` offers
only the pinned engine's entries and carries `pins`/`pin_notes`, the card prints the sentence naming
the variable, and a stale page's cross-engine POST is refused with it. The pin names the ENGINE, not
the voice — a pick *within* it still applies. `MOXIE_TTS=tone` deliberately pins nothing (it is the
fallback's permission, and is what both compose files default to; `test_compose.py` guards that
coupling from the other end). +20 tests, plus a live discovery suite
(`sim/tests/test_live_voice_picker.py`, ONE `/v1/models` call, in the deep tier): against the real
gateway on 2026-09-03 the console offers **32 speech entries** (31 gateway voices + the tone) and
**4 listening** (`stt-whisper`, `graphling-stt`, `stt-whisper-base`, `off`), and an untouched
picker resolves to `gateway:piper-amy` / `gateway:stt-whisper`.

Originally: build-ready brief (2026-09-02). **Depends on:** the gateway STT slice (`feat/gateway-stt-live`)
and the telehealth slice (it owns `server/` and the status-HTTP region until it lands).
**Owner outcome:** *full cloud service* — a parent picks Moxie's voice and ears from what is actually
available, in one place, without env edits or restarts.

## What the user asked for (verbatim intent)

> "We should also allow users to pick from the available TTS and STT models, but default to piper-amy
> when possible (drop down for speech and listening)." — and, the same day: local TTS/STT stay a
> first-class option; cloud-hosted deployments (a SIM on Cloudflare, etc.) will routinely want the gateway.

So: two dropdowns on the console — **Speech** (TTS) and **Listening** (STT) — populated from what the
appliance can really use right now, defaulting to `piper-amy` when the gateway lists it.

## What "available" means (no invention)

| Source | How we know it is available | Entries |
|---|---|---|
| Gateway (LiteLLM) | `GET {MOXIE_VOICE_BASE_URL}/models` through the OpenAI client (`client.models.list()`), classified by `moxie_sdk/audio_models.py::classify_audio_models` (lands with the STT slice) (pure, golden-tested against the verified list) | `piper-amy`, `piper-ryan`, `tts-piper-*`, `graphling-tts-*` · `stt-whisper`, `graphling-stt`, `stt-whisper-base` |
| Local Piper | `PiperSynthesizer.available()` + the voices present under `sim/tts/voices/` (or `MOXIE_PIPER_MODEL`) | `local:piper:<voice>` |
| Local whisper | `WhisperTranscriber.available()` | `local:whisper:<size>` |
| Built-in | always | `tone` (speech) · `off` (listening) |

Discovery is cached (5 min, `MOXIE_VOICE_DISCOVERY_TTL_S`) and never blocks a turn: the first `GET /voice`
after boot may return `discovering: true` with the local entries only, and the next call fills in the
gateway list. A gateway that is down yields the local entries plus `gateway_error: "<class>"` — the
dropdowns still render.

## Defaults (the "when possible" rule)

- **Speech:** `piper-amy` if the gateway lists it → else the first gateway TTS id → else local Piper's
  Amy if installed → else `tone`.
- **Listening:** `stt-whisper` if listed → else the first gateway STT id → else local whisper if
  installed → else `off`.
- An explicit choice always wins over the default, and an explicit **local** choice wins even when a
  gateway URL is configured (user rule; pinned by a test in the STT slice and re-asserted here).
- And the converse, which the first pass got wrong (see the 2026-09-03 correction above): an
  explicit `MOXIE_TTS`/`MOXIE_STT` **pins the engine**, and a pick may only choose the model within
  it. The operator chooses the engine; the parent chooses the voice.

## Design

### A settings record, persisted
`fleet/voice.json` via the existing `JsonStore` (atomic writes):
```json
{"speech": {"engine": "gateway", "model": "piper-amy"},
 "listening": {"engine": "gateway", "model": "stt-whisper"},
 "updated_at": 1788400000}
```
`engine ∈ {gateway, piper, tone}` for speech, `{gateway, whisper, off}` for listening. Unset = the
defaults above, computed at read time from the current discovery (so a new gateway model appears without
migration). A pure `moxie_sdk/voice_settings.py`: `normalize_voice_settings(patch, available) -> dict`
(rejects an id that is not in `available` with a reason the console shows), `resolve_defaults(available)`,
`describe_choice(choice)` (the human label the dropdown shows: "Amy (gateway, piper-amy)", "Amy (local
Piper)", "Tone (built-in)").

### Runtime (`moxie_runtime.py`)
- `voice_view()` → `{ok, available: {speech: [...], listening: [...]}, current: {...}, defaults: {...},
  discovering, gateway_error}`.
- `voice_update(patch)` → normalize → persist → **rebuild and swap** the engines through the same
  builders `run.py` uses (`config.build_synthesizer` / `config.build_transcriber` grow an optional
  `override=` argument; unset keeps today's env-driven behaviour byte-for-byte) → `set_synthesizer` /
  `set_transcriber` (add `set_synthesizer` mirroring `set_transcriber` if it does not exist) → the *next*
  turn uses the new engine; an in-flight turn finishes on the old one (document this; no locks in the
  turn loop).
- `voice_test(text="Hi, I'm Moxie.")` → synthesizes one line with the *current* speech engine and
  publishes it as a `CloudTTSResponse` to the selected robot (the SIM plays it) — the "Test" button.
- Status-HTTP: `GET /voice`, `POST /voice` (patch), `POST /voice/test`. Same idiom as `/schedule`,
  `/memory`, `/telehealth`.
- Boot: `run.py` reads `fleet/voice.json` first and passes the override into the builders, so a choice
  survives a restart; the log line says which engine was installed and why (`speech: piper-amy (gateway,
  chosen)` / `speech: tone (built-in, default — gateway unreachable)`).

### Console
`server/moxie_server/fleet.py::normalize_voice` (pure) · `main.py` `GET/POST /local/robots/{id}/voice`,
`POST …/voice/test` (thin proxies) · `server/static/{index.html,app.js,style.css}`: a **🎚️ Voice** card —
two `<select>`s labelled **Speech** and **Listening**, grouped `<optgroup>`s *Gateway* / *Local* /
*Built-in*, the current choice selected, a "Default" marker on the default entry, a **Test** button that
calls `/voice/test` and shows "played on <robot>" or the error, and a one-line status
("Discovering gateway models…" / "Gateway unreachable — local options only"). Mirror the 🎨 look card's
fetch/refresh idiom. No emoji in new copy except the card glyph convention already used.

### Compose / env
Nothing new is required for the picker itself; it reads `MOXIE_VOICE_BASE_URL`/`MOXIE_STT_BASE_URL`
already forwarded. `MOXIE_VOICE_DISCOVERY_TTL_S` is optional — if added, forward it in BOTH compose files
(parity guard).

## Tests (a test for every feature)
- Pure: `normalize_voice_settings` (accepts listed ids, rejects unlisted with a reason, engine/model
  consistency), `resolve_defaults` for every availability combination (gateway with/without piper-amy,
  local only, nothing), `describe_choice` labels, persistence round-trip through `JsonStore`.
- Discovery: a fake OpenAI client whose `models.list()` returns the verified list → `voice_view()`
  classifies it; a fake that raises → `gateway_error` set, local entries still present; the TTL cache
  (fake clock) makes one call per window.
- Runtime swap: `voice_update` installs a fake synthesizer/transcriber and the next fake turn uses it
  (extend the `helpers_runtime.py` harness additively); the explicit-local-wins rule.
- HTTP: `GET/POST /voice`, `POST /voice/test` publishes a `CloudTTSResponse` on the right topic (fake MQTT).
- Console: `test_console_roundtrip.py` (importorskip fastapi) round-trips the card's endpoints; any
  browser test asserts recorded state, never live audio samples (playbook rule 11).
- Live (≤3 gateway calls, one explicit step): `POST /voice {"speech": {"engine": "gateway", "model":
  "piper-ryan"}}` → `POST /voice/test` → the SIL smoke's decoded audio is 22050 Hz real speech (spectral
  flatness ≫ tone), then switch back to `piper-amy`.
- Both venv shapes green; doc guards green; SIL smoke on a free port.

## Acceptance
1. With the gateway configured, both dropdowns list the gateway's real audio models plus the local
   engines that are installed; the defaults are `piper-amy` and `stt-whisper`.
2. Picking another entry takes effect on the next turn with no restart, and survives a restart.
3. With no gateway URL, the dropdowns show local + built-in entries only and default to local Piper /
   local whisper when installed.
4. An explicit local choice is honoured even with a gateway URL set.
5. A gateway outage never blanks the card or blocks a turn.

## Files
`mqtt/moxie_sdk/voice_settings.py` (new) · `mqtt/moxie_sdk/audio_models.py` (from the STT slice) ·
`mqtt/config.py` (builders grow `override=`) · `mqtt/run.py` (boot read + log line) ·
`mqtt/supervisor/moxie_runtime.py` (voice region + 3 status-HTTP routes + `set_synthesizer`) ·
`server/moxie_server/{fleet,main}.py` · `server/static/{index.html,app.js,style.css}` ·
`sim/tests/test_voice_settings.py`, `test_voice_runtime.py` (new), `test_console_roundtrip.py` (+cases) ·
docs: `ai-seam.md` (§1 + §3 "choosing an engine"), `guides/litellm-tts-setup.md` + `litellm-stt-setup.md`
(a "Pick it in the console" paragraph), `implementation-plan.md` (TTS/STT rows), this file's status line.

---
📖 [Backlog index](README.md) · [AI seam](../ai-seam.md) · [TTS guide](../../guides/litellm-tts-setup.md) · STT guide (`docs/guides/litellm-stt-setup.md`, lands with the STT slice)
