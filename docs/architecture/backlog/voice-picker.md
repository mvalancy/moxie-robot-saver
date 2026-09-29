# Voice picker — choosing speech and listening models in the console

**Status:** shipped — [`mqtt/moxie_sdk/voice_settings.py`](../../../mqtt/moxie_sdk/voice_settings.py),
[`mqtt/config.py`](../../../mqtt/config.py) (`build_synthesizer` / `build_transcriber(override=)`,
`VoiceEngines`), [`mqtt/supervisor/moxie_runtime/voice.py`](../../../mqtt/supervisor/moxie_runtime/voice.py);
tested by `sim/tests/test_voice_settings.py`, `test_voice_runtime.py`, `test_console_voice.py` and the
live `test_live_voice_picker.py`.

## What it does

The parent console has a 🎚️ Voice card with two dropdowns — **Speech** (TTS) and **Listening** (STT) —
listing what this appliance can really use right now, grouped *Gateway* / *Local* / *Built-in*, with the
default marked and a **Test** button that speaks one line on a robot. A pick applies to the **next**
thing Moxie says, needs no restart, and survives one. It is fleet-level: one pick for the appliance.

## What "available" means

| Source | How we know | Entries |
|---|---|---|
| Gateway (LiteLLM) | `GET {MOXIE_VOICE_BASE_URL}/models`, classified by `moxie_sdk/audio_models.py` | e.g. `piper-amy`, `piper-ryan`, `graphling-tts-*` · `stt-whisper`, `graphling-stt`, `stt-whisper-base` |
| Local Piper | voices installed on the box (`sim/tts/voices/` or `MOXIE_PIPER_MODEL`) | `piper:<voice>` |
| Local whisper | the default size and `MOXIE_STT_MODEL` | `whisper:<size>` |
| Built-in | always | `tone` (speech) · `off` (listening) |

Gateway discovery is cached by `GatewayCatalog` for `MOXIE_VOICE_DISCOVERY_TTL_S` (default 300 s,
forwarded in both compose files) and refreshed in the background, so it never blocks a turn. The first
read after boot may say `discovering: true` with local entries only. A gateway that is down yields the
local entries plus `gateway_error` — the card still renders.

## Defaults and precedence

Defaults are computed at read time from current availability (`resolve_defaults`), so a newly served
gateway voice needs no migration:

- **Speech:** gateway `piper-amy` → first gateway voice → local Piper Amy → any local Piper → `tone`.
- **Listening:** gateway `stt-whisper` → first gateway STT → local whisper → `off`.

Precedence, highest first (`config.build_synthesizer` / `build_transcriber`):

1. `MOXIE_TTS=off` / `MOXIE_STT=off` wins outright — a deployment that declared itself voiceless is not
   talked back into speaking by a dropdown.
2. An explicit `MOXIE_TTS` / `MOXIE_STT` **pins the engine** (`voice_settings.ENV_PIN`). A pick naming
   another engine is dropped by the builders, the card offers only the pinned engine's entries and
   prints a sentence naming the variable, and a stale page's cross-engine POST is refused with it. The
   pin names the engine, not the voice: a pick *within* it still applies. The operator chooses the
   engine; the parent chooses the voice.
3. `MOXIE_TTS=tone` deliberately pins **nothing**: it is permission for the beep as the last rung, and
   it is both compose files' default, so pinning on it would shrink every compose deployment's Speech
   list to one entry.
4. The stored pick. An explicit **local** pick wins even with a gateway configured (local engines stay
   first-class).
5. A pick that cannot be built on this box falls through to the env-driven path rather than leaving a
   child in silence. With no pick stored, behaviour is exactly the env-driven one.

## How it works

- **Record:** `fleet/voice.json` via `JsonStore` (collection `voice`):
  `{"speech": {"engine", "model"}, "listening": {"engine", "model"}, "updated_at"}`. A missing side
  means "use the default". Engines: `gateway | piper | tone` for speech, `gateway | whisper | off` for
  listening.
- **Runtime** (`VoiceMixin` in `moxie_runtime/voice.py`): `voice_view()` returns the options, the
  current and default choices, labels, pins, discovery and gateway status. `voice_update(patch)`
  validates against current availability, persists, then rebuilds and swaps the engines through the
  same builders `run.py` uses. The swap rebinds one attribute, so a turn in flight finishes on the old
  engine. `voice_test()` synthesizes one line and publishes it as a `CloudTTSResponse` to the chosen
  robot (the SIM plays it).
- **Read vs write:** a stored pick the gateway cannot currently confirm is still honoured on **read**
  (an outage must not revert a parent's choice); only a **write** is checked against availability.
  A write issued before the first gateway listing returns waits up to 10 s for it
  (`VOICE_SETTLE_S`), otherwise a good pick would be judged against an empty catalog; reads never wait.
- **Status HTTP:** `GET /voice`, `POST /voice`, `POST /voice/test` (`status_http.py`).
- **Boot:** `mqtt/run.py` reads `fleet/voice.json` before building either engine and logs which engine
  was installed and why.
- **Console:** `server/moxie_server/routes/console.py` proxies `GET/POST /local/robots/{id}/voice` and
  `POST …/voice/test`; `server/moxie_server/fleet/cards.py::normalize_voice` shapes the payload;
  the card is in `server/static/index.html` + `js/voice-brain.js`.

## Tests

- `test_voice_settings.py` — normalization (listed ids accepted, unlisted refused with a reason),
  defaults for every availability combination, labels, env pins, persistence, the discovery cache
  against a fake `models.list()` (including a failing one).
- `test_voice_runtime.py` — the card's contents with and without a gateway, the live swap on the
  next turn (swapping the ears drops a half-heard utterance), the explicit-local-wins rule, pins,
  `/voice` HTTP and `voice_test`, and that a save settles discovery while the card never waits.
- `test_console_voice.py` — the console normalizer and proxy routes.
- `test_live_voice_picker.py` (deep tier, one `/v1/models` call) — the real gateway still lists
  `piper-amy`, the classifier still splits voices from ears, and an environment naming no engine pins
  nothing. Skips without `MOXIE_VOICE_BASE_URL` and a key.

## Known gaps

- Local Piper and whisper entries are exercised with fakes in CI; neither package is required on the
  build machine.
- The pick is fleet-wide; there is no per-child voice.

---
📖 [Backlog index](README.md) · [AI seam](../ai-seam.md) · [TTS guide](../../guides/litellm-tts-setup.md) · [STT guide](../../guides/litellm-stt-setup.md)
