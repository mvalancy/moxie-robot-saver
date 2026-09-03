# 🎙️ STT on the LiteLLM gateway

**Goal:** *hear* through the **same gateway, same key, same rate limits** as chat and the voice — no
separate ASR service, no key, and no 140 MB model on the box. Registered and **live**; the wire
contract our client speaks is kept at the bottom.

The other half of the seam ([`litellm-tts-setup.md`](litellm-tts-setup.md)) gave Moxie a voice.
This one gives her ears.

---

## ✅ Live since 2026-09-02

`POST /v1/audio/transcriptions` returns a real transcript. Three models are registered:

| Model | What it is | Default | Measured |
|---|---|:--:|---|
| `stt-whisper` | the gateway's Whisper ears | ✅ | 2.8 s for a 6 s clip |
| `graphling-stt` | a second ASR (the gateway owns the mapping) | | 4.4 s for the same clip |
| `stt-whisper-base` | a smaller Whisper | | unprobed |

The same host also lists the voices, now under aliases as well — all of them are just a value for
`MOXIE_VOICE_MODEL`, no code change:

| TTS model | Voice |
|---|---|
| `piper-amy` (default) · `tts-piper-amy` | Piper `en_US-amy-medium` — Moxie's own voice |
| `piper-ryan` · `tts-piper-ryan` | the second Piper voice |
| `graphling-tts-narrator` · `graphling-tts-character` | gateway-side aliases |

### Turning it on — one variable

```sh
# mqtt/.env (git-ignored) — the LLM key already there is the ears' key too
MOXIE_STT=gateway
MOXIE_STT_MODEL=stt-whisper       # optional; this is the default
```

That is the whole switch. There is usually **nothing else to set**: `MOXIE_STT_BASE_URL` defaults to
`MOXIE_VOICE_BASE_URL` and then to `MOXIE_LLM_BASE_URL`, and `MOXIE_STT_API_KEY` falls back the same
way — one gateway, one key. With `MOXIE_STT` left at its default `auto`, a box that already has a
gateway URL **and** a key hears in the cloud on its own; a box with neither keeps using local
faster-whisper exactly as before.

### Deployment matrix — pick the ears that match the box

Neither engine is the "real" one. Which you want is a property of the deployment:

| Deployment | Voice | Ears | Why |
|---|---|---|---|
| **Home appliance / offline** (a Pi or a NUC in the house, no egress) | local Piper (`MOXIE_PIPER_MODEL=…`, no voice URL) | local whisper (`MOXIE_STT=whisper`) | A child's voice never leaves the house; no key, no per-utterance network. Costs ~200 MB of models on the box and some CPU. Both stay selected **even when a gateway URL is configured** — this is a first-class mode, not a fallback. |
| **Cloud-hosted** (the hosted SIM on Cloudflare, a VPS, a slim container) | gateway TTS (`MOXIE_VOICE_BASE_URL=…`) | gateway STT (`MOXIE_STT=gateway`) | There is nowhere to put the model wheels, and no GPU to want. One key covers brain + voice + ears, on one rate-limit budget. Costs ~2.5-2.8 s of network per leg. |
| **Mixed / default** | `auto` | `auto` | The gateway when a URL **and** a key are present, degrading to local when they are not — and each engine is still one env line away in either direction. |

The knob is `MOXIE_STT`: `auto` (default) · `gateway` · `whisper` (alias `local`) · `off`. Explicit
values win over everything, including a fully configured gateway — that is the point of them.
`MOXIE_STT_MODEL` names a model on **whichever engine you selected** (`stt-whisper` for the gateway,
`base.en` for local whisper); left unset, each engine uses its own default.

The voice side now has the symmetric override: `MOXIE_TTS=piper` (alias `local`) forces the local
Piper voice even with a gateway fully configured, exactly as `MOXIE_STT=whisper` does for the ears.
Pinned by `sim/tests/test_stt_gateway.py::test_local_piper_is_selectable_the_same_way_for_the_voice`.

### Pick it in the console

The console's 🎚️ **Voice** card has a **Listening** dropdown beside the Speech one: the gateway's
ears (`stt-whisper`, `graphling-stt`, `stt-whisper-base`, discovered from `GET /v1/models`), the
local whisper sizes that are actually installed, and `off`. `stt-whisper` is marked as the default.
A pick takes effect on the **next** utterance without a restart and is remembered in
`fleet/voice.json` across one; an explicit **local whisper** pick is honoured even with a gateway
URL set, which is the whole point of the home-appliance row in the matrix above. Design:
[ai-seam §③ *Choosing an engine*](../architecture/ai-seam.md).

### What we measured (live, 2026-09-02)

One 13-word sentence spoken by `piper-amy` and read back by `stt-whisper`
(`sim/tests/test_live_gateway_stt.py`, `[gw-stt]` lines):

| Leg | Bytes | Audio | Wall clock | Word overlap |
|---|--:|--:|--:|--:|
| TTS `piper-amy` → WAV | 266 472 B @ 22050 Hz | 6.04 s | 2.55 s | — |
| STT at the WAV's own 22050 Hz | — | 6.04 s | **2.84 s** | **1.00** |
| STT at the robot's 16 kHz | 193 358 B | 6.04 s | **2.55 s** | **1.00** |

And one whole turn through the real `MoxieRuntime` with **nothing local in the loop** — child audio
(`piper-ryan`) → `zmqSTTRequest` frames → gateway ears → gateway brain → gateway voice:

```
[gw-stt] [run] STT enabled: openai-stt (stt-whisper) (standby: faster-whisper (base.en))
[gw-stt] e2e runtime heard: 'Hi, Moxie. Can you tell me a joke about a robot?'  (overlap 1.00, 2.82s)
[gw-stt] e2e moxie replied: 'Sure! Why did the robot go to school? To become a better friend!'  (15.42s)
[gw-stt] e2e 🔊 spoke 223580 B @ 22050 Hz (~5.07s, 0 marks)
```

Local faster-whisper (`base.en`, int8/CPU) is still roughly **2-3× faster** for the same clip once
its weights are loaded; the gateway's ~2.5-2.8 s buys you "no ASR wheels and no model file on the
box", which is exactly what a hosted deployment cannot have.

### Four quirks worth knowing

1. **It takes a FILE, not frames.** The robot's perception bus carries headerless 16-bit PCM, so
   `moxie_sdk/stt.py::wav_bytes()` wraps it in a RIFF/WAVE container in memory (stdlib `wave`) before
   the multipart upload. **The header must carry the audio's true rate** — a WAV that claims 16000 for
   22050 Hz audio pitch-shifts it and wrecks the transcript.
2. **The rate that matters is 16000.** `SttSession` (and therefore `MoxieRuntime.feed_stt`) hands the
   transcriber the perception bus's own 16 kHz. Both rates are proven live above; the 16 kHz one is
   the path a real robot takes.
3. **Silence costs nothing.** Anything under 120 ms is dropped before the request — a robot's VAD
   closes on breaths and door slams, and the gateway would charge a request and ~2.5 s to say so.
4. **An unusable model fails as a 400, not as an empty transcript.**
   `model=stt-does-not-exist` → `400 … Invalid model name passed in model=…`, raised by the OpenAI
   SDK. That, an outage past the SDK's backoff, or a revoked key — the ears say so **once** and the
   standby hears the rest of the run.

### If the gateway hiccups, a child is still heard

`config.build_transcriber()` wraps the gateway ears in a `FallbackTranscriber` whose standby is
exactly the rung it displaced: **local faster-whisper when it is installed, else a `NullTranscriber`
that returns `""`**. On the first failure it prints one line —

```
[stt] openai-stt failed (BadRequestError: Error code: 400 - …); hearing with faster-whisper for the rest of this run
```

— and latches, so a dead endpoint costs one timeout, not one per utterance. It is a *downgrade*,
never a traceback in the middle of a child's sentence; the startup log and the tests read
`describe()` to see which engine is actually listening:

```
[run] STT enabled: openai-stt (stt-whisper) (standby: faster-whisper (base.en))
```

The standby is built at startup, so a box that must not load the whisper weights should simply not
install `faster-whisper` — the standby is then the `NullTranscriber`, and a gateway outage means
Moxie hears nothing until it returns, which the log says out loud.

### Verify it yourself

```sh
curl -sS https://gateway.graphlings.net/v1/audio/transcriptions \
  -H "Authorization: Bearer $KEY" \
  -F model=stt-whisper -F response_format=json -F file=@moxie-tts.wav
# {"text":"Hi Sam, I am Moxie. Do you want to hear a story about a brave little robot?","usage":null}
```

(`moxie-tts.wav` is the file the [TTS guide](litellm-tts-setup.md#verify-it-yourself) has you make —
speaking a sentence and hearing it back is the cheapest full check of both halves.)

End to end, through a broker, with the virtual robot:

```sh
MOXIE_STT=gateway MOXIE_STT_BASE_URL=https://gateway.graphlings.net/v1 \
  MOXIE_SIL_PORT=2101 bash sim/run_smoke.sh
# [run] STT enabled: openai-stt (stt-whisper) (standby: faster-whisper (base.en))
# (this one costs no gateway calls: the smoke drives a text turn, so nothing is transcribed)
```

---

## The contract, for reference (and for a second gateway)

Kept in the same shape as the TTS guide's handoff, so anyone standing up **another** OpenAI-shaped
proxy — or checking that this one still behaves — knows exactly what our client sends and needs back.
Our client is OpenAI-audio-compatible and already backs off + paces on `429`/`5xx` (honors
`Retry-After`), exactly like the chat and TTS paths.

## What we send (the contract)

```http
POST /v1/audio/transcriptions
Authorization: Bearer <existing key>
Content-Type: multipart/form-data

model=<the model name you register>
file=@utterance.wav          ; RIFF/WAVE, 16-bit mono, 16000 Hz (the robot's mic rate)
response_format=json
```

Requirements:
1. **Model name(s)** — `stt-whisper` (or `stt-<engine>`); whichever you register, tell us the name
   and which is the default.
2. **Audio in** — 16-bit mono WAV. **16000 Hz is the rate that matters** (the robot's perception bus);
   22050 Hz also has to work, because our own TTS renders there.
3. **`response_format=json`** → `{"text": "..."}`. Verbose/segment formats are fine to support; we
   only read `text`.
4. **Auth** — the **existing gateway key** (the one used for chat and TTS). No new key.
5. **Rate limiting** — same tier/limits as the chat models.
6. **Latency** — a few seconds for a ~6 s clip is fine; a child's turn budget is dominated by the
   brain, not the ears.

## How to register it (LiteLLM `config.yaml`)

```yaml
model_list:
  - model_name: stt-whisper                  # the name we call
    litellm_params:
      model: openai/whisper-1                # or a local faster-whisper / whisper.cpp shim
      api_base: http://whisper-shim:8000/v1  # omit for hosted OpenAI
      api_key: os.environ/YOUR_STT_KEY
    model_info: { mode: audio_transcription }
```

Any OpenAI-`/audio/transcriptions`-compatible server works the same way (a
[`faster-whisper-server`](https://github.com/fedirz/faster-whisper-server)-style shim, whisper.cpp's
server, or a hosted provider). Give it the same rpm/tpm limits as chat so it shares the gateway's
rate policy.

## What a gateway has to tell us

*(Answered for ours on 2026-09-02: `stt-whisper` (default), `graphling-stt`, `stt-whisper-base`;
multipart WAV in, `{"text": …, "usage": null}` out.)*

1. **the model name(s)** and **which is the default** → `MOXIE_STT_MODEL`.
2. **the audio formats + sample rates** accepted.

---
📖 [AI seam contract §1 (STT)](../architecture/ai-seam.md) · [TTS on the gateway](litellm-tts-setup.md) · [Implementation plan](../architecture/implementation-plan.md) · [Docs index](../README.md)
