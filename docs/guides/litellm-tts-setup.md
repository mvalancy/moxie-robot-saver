# 🔊 TTS on the LiteLLM gateway

**Goal:** speak through the **same gateway, same key, same rate limits** as chat — no separate voice
service or key. Registered and **live**; the handoff that asked for it is kept below as the contract.

---

## ✅ Live since 2026-09-02

`GET /v1/models` lists two voices and `POST /v1/audio/speech` returns real speech:

| Model | Voice | Default |
|---|---|:--:|
| `piper-amy` | Piper `en_US-amy-medium` — Moxie's own voice | ✅ |
| `piper-ryan` | a second Piper voice (the gateway owns the mapping) | |

### Turning it on — one variable

```sh
# mqtt/.env (git-ignored) — the LLM key already there is the voice key too
MOXIE_VOICE_BASE_URL=https://gateway.graphlings.net/v1
MOXIE_VOICE_MODEL=piper-amy      # optional; this is the default
```

That is the whole switch. `MOXIE_VOICE_FORMAT` (`wav` default | `pcm`) and
`MOXIE_VOICE_SAMPLE_RATE` (pcm only, default `22050`) are there if you need them; both compose files
forward all four. Precedence is unchanged: **voice server → Piper → tone**.

### What we measured (live, 2026-09-02)

One 13-word sentence, `"Hi Sam, I am Moxie. Do you want to hear a story about a brave little robot?"`:

| Call | Bytes | Audio | Wall clock |
|---|--:|--:|--:|
| `piper-amy`, `wav` | 268 520 B PCM (22050 Hz mono 16-bit) | 6.09 s | **1.69 s** |
| `piper-ryan`, `wav` | 194 280 B | 4.41 s | 1.29 s |
| `piper-amy`, `pcm` | 270 056 B | 6.12 s | 1.25 s |

Whisper (`base.en`, int8/CPU) transcribed the Amy audio back at **word overlap 1.00**, and the same
pipeline reads *nothing* out of our placeholder tone — that contrast is the test
(`sim/tests/test_live_gateway_tts.py`). Local Piper is still roughly **3-5× faster** for the same
sentence (no network, no proxy); the gateway's ~1.3-1.7 s buys you "no 63 MB model on the box".

### Four quirks worth knowing

1. **The `Content-Type` lies.** A `wav` reply is a valid RIFF/WAVE file whose header says
   `audio/mpeg`. **Sniff the bytes** (`RIFF` … `WAVE`), never the header — `moxie_sdk/tts.py`
   `pcm_from_audio()` does, and unwraps it with the stdlib `wave` module so the sample rate that
   reaches `CloudTTSResponse` is **the file's own**, not a constant.
2. **`voice` is required but ignored.** Omitting the field is an HTTP **500**; its value has no
   effect — the *model name* selects the voice. We always send one, defaulted from the model's own
   suffix (`piper-amy` → `amy`); `MOXIE_TTS_VOICE` overrides it for a real OpenAI-shaped endpoint.
3. **Formats.** `wav` → RIFF, 22050 Hz mono 16-bit. `pcm` → raw 16-bit little-endian at the same
   22050 Hz (about 1 500 bytes longer for the same line — the container is not the difference; the
   two renders are not sample-identical). `mp3`/`opus` are *not* decoded by our client.
4. **An unusable model fails as a 400, not as audio.** `model=piper-does-not-exist` →
   `400 … Invalid model name passed in model=…`, raised by the OpenAI SDK. Either way — a 400, an
   outage past the SDK's backoff, or a proxy that answers 200-with-JSON — the synthesizer says so
   **once** and the standby voice finishes the sentence (see below).

### If the gateway hiccups, a child still hears a voice

`config.build_synthesizer()` wraps the gateway voice in a `FallbackSynthesizer` whose standby is
exactly the rung it displaced: **Piper if `MOXIE_PIPER_MODEL` is set and piper is installed, else the
built-in tone**. On the first failure it prints one line —

```
[voice] openai-voice failed (BadRequestError: Error code: 400 - …); speaking with tone for the rest of this run
```

— and latches, so a dead endpoint costs one timeout, not one per turn. It is a *downgrade*, never
silence; `/status` and the tests read `synth.voice_name` to see which voice is actually talking.

### Verify it yourself

```sh
curl -sS https://gateway.graphlings.net/v1/audio/speech \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"piper-amy","input":"Hello from Moxie","voice":"amy","response_format":"wav"}' \
  -o moxie-tts.wav -w "HTTP %{http_code} · %{content_type} · %{size_download}B\n"
# HTTP 200 · audio/mpeg (it is a WAV anyway) · ~100 kB; moxie-tts.wav plays.
```

End to end, through a broker, with the virtual robot:

```sh
MOXIE_VOICE_BASE_URL=https://gateway.graphlings.net/v1 MOXIE_VOICE_MODEL=piper-amy \
  MOXIE_SIL_PORT=2081 bash sim/run_smoke.sh
# [virtual-moxie] 🔊 spoke 93300 B @ 22050 Hz (~2.12s, 0 marks)   ← gateway Amy
# (the built-in tone is 50934 B for the same reply — that is how you tell them apart)
```

---

## The original handoff (the contract we asked for)

## What we already confirmed

- The route is live: `POST https://gateway.graphlings.net/v1/audio/speech` returns
  **`400 "Invalid model name passed in model=tts-1"`** — a 400, not a 404. So the LiteLLM proxy already
  handles `/audio/speech`; it just has **no TTS model registered**. Registering one is the whole task.
- `GET /v1/models` lists chat/embed models only (no `tts`/`audio` model yet).

## What we need (the contract)

Our client is OpenAI-audio-compatible. Once a model exists, we call:

```http
POST /v1/audio/speech
Authorization: Bearer <existing key>
Content-Type: application/json

{ "model": "<the model name you register>",
  "input": "Hi friend! What do you want to talk about?",
  "voice": "amy",
  "response_format": "wav" }
```

Requirements:
1. **Model names — one model per voice** is what we want: `piper-amy`, `piper-<xxx>`, `piper-<yyy>`
   (or `tts-piper-amy`, `tts-piper-<xxx>`, … — either prefix is fine, just be consistent). We select a
   voice by calling the matching model. **Piper "Amy" (`en_US-amy-medium`) is the default/primary.**
2. **Voice** — each `piper-<name>` model is bound to that Piper voice. If your backend also takes a
   `voice` request field we'll send the voice name too, but the **model name is the source of truth**.
3. **`response_format`** — we can consume **`wav`** or **`pcm`** (raw 16-bit; tell us the sample rate,
   16k or 24k). `mp3`/`opus` also OK. WAV is the easy default. We set `response_format` to match.
4. **Auth** — the **existing gateway key** (the one used for chat). No new key.
5. **Rate limiting** — **same tier/limits as the chat models.** Our client already backs off + paces on
   `429`/`5xx` (honors `Retry-After`), so normal LiteLLM rate-limit responses are handled gracefully.
6. **Returns** raw audio bytes with HTTP 200 (standard OpenAI `/audio/speech` behavior).

## How to register it (LiteLLM `config.yaml`)

Pick whichever backend you prefer; **Piper (Amy) is our preference**.

### Option A — Piper via an OpenAI-compatible shim (recommended)
Piper isn't a native LiteLLM provider, but an OpenAI-compatible TTS server that wraps Piper works well —
e.g. **[openedai-speech](https://github.com/matatonic/openedai-speech)** (OpenAI `/audio/speech`
compatible, ships Piper voices incl. `en_US-amy-medium`). Run it, then point LiteLLM at it:

```yaml
model_list:
  - model_name: piper-amy                  # one model per voice (piper-<voice>)
    litellm_params:
      model: openai/en_US-amy-medium        # the shim's voice/model id
      api_base: http://openedai-speech:8000/v1
      api_key: "sk-noauth"                  # or the shim's key via os.environ/…
    model_info: { mode: audio_speech }
  - model_name: piper-ryan                 # add as many voices as you like
    litellm_params:
      model: openai/en_US-ryan-high
      api_base: http://openedai-speech:8000/v1
      api_key: "sk-noauth"
    model_info: { mode: audio_speech }
  # …piper-<xxx>, piper-<yyy> — Amy is our default/primary
```
(openedai-speech ships Piper voices; map each `piper-<name>` model to the Piper voice id you want.)

### Option B — a hosted OpenAI-compatible TTS (simplest)
If you'd rather use OpenAI (or Azure/Vertex/ElevenLabs) TTS:

```yaml
model_list:
  - model_name: moxie-tts
    litellm_params:
      model: openai/tts-1                  # or azure/…, vertex_ai/…, elevenlabs/…
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: audio_speech
```
(Voices differ — e.g. OpenAI uses `alloy`/`nova`/…; just tell us the voice name to send.)

### Option C — any OpenAI-`/audio/speech` server you already run
```yaml
model_list:
  - model_name: moxie-tts
    litellm_params:
      model: openai/<its model id>
      api_base: https://<your-tts-host>/v1
      api_key: os.environ/YOUR_TTS_KEY
    model_info:
      mode: audio_speech
```

Add the same **rpm/tpm limits** you use for chat (e.g. under `litellm_settings` or the model's
`model_info`) so it shares the gateway's rate policy.

## Verify it works

```sh
curl -sS https://gateway.graphlings.net/v1/audio/speech \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"piper-amy","input":"Hello from Moxie","voice":"amy","response_format":"wav"}' \
  -o moxie-tts.wav -w "HTTP %{http_code} · %{content_type} · %{size_download}B\n"
# expect: HTTP 200 · audio/wav (or audio/x-wav) · a few KB; moxie-tts.wav should play.
```
Also `GET /v1/models` should now list `piper-amy` (and any other voices).

## What to send back to us

*(Answered 2026-09-02: `piper-amy` → `en_US-amy-medium` (default) and `piper-ryan`; `wav` and `pcm`
at 22050 Hz.)* Just this and we're wired in (we set `MOXIE_VOICE_BASE_URL=https://gateway.graphlings.net/v1` + the
default model name in a git-ignored `.env`; nothing new to deploy on your side):

1. **the model names + the voice each maps to** (e.g. `piper-amy → en_US-amy-medium`, `piper-ryan → …`)
   and **which one is the default** (Amy).
2. **`response_format`(s) supported** + **sample rate** if PCM (e.g. `wav`, or `pcm @ 24000`).

---
📖 [AI seam contract §3 (TTS)](../architecture/ai-seam.md) · [Implementation plan](../architecture/implementation-plan.md) · [Docs index](../README.md)
