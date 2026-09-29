# ai — where the AI lives

This folder holds no code. The AI adapters live in the SDK, in [`../mqtt/moxie_sdk/`](../mqtt/moxie_sdk/README.md),
behind the three seams specified in the [AI seam contract](../docs/architecture/ai-seam.md).

## The three seams

| Seam | Code | Engines | Chosen by |
|---|---|---|---|
| **Brain** (LLM) | `chat.py`, `brains.py` | Any OpenAI-compatible endpoint: LiteLLM, vLLM, Ollama, LM Studio, or a hosted API. No endpoint is built in. | `MOXIE_APP`, `MOXIE_LLM_BASE_URL`, `MOXIE_LLM_MODEL` |
| **Ears** (speech-to-text) | `stt.py` | Local faster-whisper, or a gateway's `/audio/transcriptions` | `MOXIE_STT` (`auto`, `gateway`, `whisper`) |
| **Voice** (text-to-speech) | `tts.py` | A gateway's `/audio/speech`, local Piper, or a built-in tone | `MOXIE_VOICE_BASE_URL`, `MOXIE_PIPER_MODEL`, `MOXIE_TTS` |

The server synthesizes the speech and sends it to the robot as a `CloudTTSResponse`, with behavior
markup (`automarkup.py`, `performance.py`) that makes Moxie move and emote while she talks.

All settings are listed in [`../mqtt/.env.example`](../mqtt/.env.example). Local engines and
gateways are both first-class: pick whichever runs where you are.

## Principles

- **Local first.** Everything can run on your own machine with no internet.
- **No vendor lock-in.** The brain speaks the OpenAI-compatible API, so any provider or local server
  works.
- **Swappable.** Each seam is a small interface; the console can switch brain and voice per robot.

Setup guide: [voice and ears through a gateway](../docs/guides/gateway-voice-and-ears.md). Research on giving Moxie sight:
[vision](../docs/architecture/vision.md).
