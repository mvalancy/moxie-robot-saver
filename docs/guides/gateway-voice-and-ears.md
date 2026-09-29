# Voice and ears through a gateway

Moxie can speak (text-to-speech) and listen (speech-to-text) through the **same OpenAI-compatible
gateway, key and rate limits** as its brain — for example a [LiteLLM](https://github.com/BerriAI/litellm)
proxy. That avoids installing voice models on the box, which matters for hosted deployments. Local
Piper and Whisper remain first-class options; pick per deployment.

## Which to use

| Deployment | Voice | Ears | Why |
|---|---|---|---|
| **Home appliance, offline** | local Piper (`MOXIE_TTS=piper`, `MOXIE_PIPER_MODEL=…`) | local Whisper (`MOXIE_STT=whisper`) | A child's voice never leaves the house. About 200 MB of models and some CPU. Stays local even if a gateway URL is configured. |
| **Cloud-hosted** (Cloudflare demo, a VPS, a slim container) | gateway (`MOXIE_VOICE_BASE_URL=…`) | gateway (`MOXIE_STT=gateway`) | No room for model wheels. One key covers brain, voice and ears. Costs about 1.5–3 s of network per leg. |
| **Default** | `auto` | `auto` | Uses the gateway when a URL and a key are present, local engines otherwise. |

Local engines are roughly 2–5× faster once loaded; the gateway saves disk and setup.

## Configuration

In `mqtt/.env` (git-ignored) or the root `.env`. The brain's key is reused; there is no new key.

```sh
# Voice
MOXIE_VOICE_BASE_URL=https://<your-gateway>/v1
MOXIE_VOICE_MODEL=piper-amy        # optional; the default
MOXIE_VOICE_FORMAT=wav             # or pcm
MOXIE_VOICE_SAMPLE_RATE=22050      # pcm only

# Ears
MOXIE_STT=gateway                  # auto (default) | gateway | whisper (alias local) | off
MOXIE_STT_MODEL=stt-whisper        # optional; the engine's own default otherwise
```

- `MOXIE_STT_BASE_URL` and `MOXIE_STT_API_KEY` fall back to the voice settings, then to the LLM
  settings — one gateway, one key.
- `MOXIE_TTS` pins the voice engine: `piper` (alias `local`), `gateway` (alias `openai`), or `off`.
  `tone` selects the built-in test tone; unset means automatic.
- Automatic voice order: gateway voice → local Piper → none (a real robot then speaks with its own
  on-device voice).

## Choosing in the console

The console's **Voice** card has a **Speech** and a **Listening** dropdown. They list what this
appliance can actually use: the gateway's models (discovered from `GET /v1/models`), locally installed
Piper voices and Whisper sizes, the built-in tone, and `off`. A pick takes effect on the next turn with
no restart and is saved in `fleet/voice.json`. **Test** plays the voice on the Sim.

The dropdowns never override an engine pinned by `MOXIE_TTS` or `MOXIE_STT`: they then offer only that
engine's entries and say which variable pinned it. So a box set to `MOXIE_STT=whisper` keeps a child's
voice in the house whatever anyone picks in a browser. Design: [AI seam](../architecture/ai-seam.md).

## When the gateway fails

Both directions degrade instead of going silent. The gateway engine is wrapped with a standby — for the
voice, Piper if installed, else the tone; for the ears, local Whisper if installed, else a transcriber
that hears nothing. On the first failure one line is logged, for example

```
[voice] openai-voice failed (BadRequestError: …); speaking with tone for the rest of this run
```

and the standby is used for the rest of the run, so a dead endpoint costs one timeout, not one per
turn. `/status` and the startup log show which engine is really active.

## Quirks the client handles

- **The TTS `Content-Type` can be wrong.** A LiteLLM proxy may label a WAV body `audio/mpeg`. The client
  sniffs the bytes (`RIFF…WAVE`) and uses the file's own sample rate (`moxie_sdk/tts.py::pcm_from_audio`).
- **TTS `voice` is required but ignored.** Omitting it can return HTTP 500; the model name selects the
  voice. The client sends one derived from the model name (`piper-amy` → `amy`); `MOXIE_TTS_VOICE`
  overrides it for a real OpenAI endpoint.
- **Formats.** `wav` and `pcm` (16-bit, 22050 Hz) are decoded; `mp3` and `opus` are not.
- **STT takes a file, not frames.** The robot's audio is headerless 16-bit PCM at 16 kHz, so
  `moxie_sdk/stt.py::wav_bytes` wraps it in a WAV whose header states the true rate (a wrong rate
  pitch-shifts the audio and ruins the transcript).
- **Silence is free.** Clips under 120 ms are dropped before any request.
- **A bad model name is a 400**, which triggers the fallback above.

## Checking it

```sh
# Speak a sentence, then read it back
curl -sS https://<your-gateway>/v1/audio/speech \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"piper-amy","input":"Hello from Moxie","voice":"amy","response_format":"wav"}' \
  -o moxie-tts.wav
curl -sS https://<your-gateway>/v1/audio/transcriptions \
  -H "Authorization: Bearer $KEY" \
  -F model=stt-whisper -F response_format=json -F file=@moxie-tts.wav
# {"text":"Hello from Moxie.", ...}

# End to end through a broker with the virtual robot (a text turn: no STT calls)
MOXIE_VOICE_BASE_URL=https://<your-gateway>/v1 MOXIE_SIL_PORT=2081 bash sim/run_smoke.sh
```

The live suites `sim/tests/test_live_gateway_tts.py` and `test_live_gateway_stt.py` do the same round
trip and assert the transcript matches.

## What the gateway must provide

For anyone setting up a gateway. The client retries with backoff on `429`/`5xx` and honors
`Retry-After`.

| | Text-to-speech | Speech-to-text |
|---|---|---|
| Endpoint | `POST /v1/audio/speech` (JSON: `model`, `input`, `voice`, `response_format`) | `POST /v1/audio/transcriptions` (multipart: `model`, `file`, `response_format=json`) |
| Models | one per voice, e.g. `piper-amy` (Piper `en_US-amy-medium`, the default), `piper-ryan` | e.g. `stt-whisper` |
| Audio | returns `wav` or `pcm` (16-bit mono, 22050 Hz) | accepts 16-bit mono WAV at **16000 Hz** (the robot's rate) and 22050 Hz |
| Returns | audio bytes, HTTP 200 | `{"text": "…"}` |
| Auth and limits | the chat key and the chat rate limits | the chat key and the chat rate limits |

Registering them in LiteLLM's `config.yaml`, using an OpenAI-compatible Piper server such as
[openedai-speech](https://github.com/matatonic/openedai-speech) and a Whisper server such as
[faster-whisper-server](https://github.com/fedirz/faster-whisper-server):

```yaml
model_list:
  - model_name: piper-amy
    litellm_params:
      model: openai/en_US-amy-medium
      api_base: http://openedai-speech:8000/v1
      api_key: "sk-noauth"
    model_info: { mode: audio_speech }
  - model_name: stt-whisper
    litellm_params:
      model: openai/whisper-1
      api_base: http://whisper-shim:8000/v1   # omit for hosted OpenAI
      api_key: os.environ/YOUR_STT_KEY
    model_info: { mode: audio_transcription }
```

Any hosted OpenAI-compatible TTS or STT works the same way; give it the same rpm/tpm limits as chat.

---
[AI seam](../architecture/ai-seam.md) · [One-command stack](one-command-stack.md) · [Guides](README.md)
