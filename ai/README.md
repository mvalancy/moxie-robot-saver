# ai — where the AI lives

For anyone choosing a brain, ears or voice for their Moxie. This folder holds no code: the AI adapters
live in the SDK, in [`../mqtt/moxie_sdk/`](../mqtt/moxie_sdk/README.md), behind the three seams
specified in the [AI seam contract](../docs/architecture/ai-seam.md).

## The three seams

| Seam | Code | Engines | Chosen by |
|---|---|---|---|
| **Brain** (LLM) | `chat.py`, `brains.py` | Any OpenAI-compatible endpoint: LiteLLM, vLLM, Ollama, LM Studio, or a hosted API. No endpoint is built in. | `MOXIE_APP`, `MOXIE_LLM_BASE_URL`, `MOXIE_LLM_MODEL` |
| **Ears** (speech-to-text) | `stt.py` | Local faster-whisper, or a gateway's `/audio/transcriptions` | `MOXIE_STT`: `auto` (default), `gateway`, `whisper` (alias `local`), `off` |
| **Voice** (text-to-speech) | `tts.py` | A gateway's `/audio/speech`, local Piper, or a built-in tone | `MOXIE_TTS`: empty (auto), `piper` (alias `local`), `gateway` (alias `openai`), `tone`, `off`; plus `MOXIE_VOICE_BASE_URL`, `MOXIE_PIPER_MODEL` |

The server synthesizes the speech and sends it to the robot as a `CloudTTSResponse`, with behavior
markup (`automarkup.py`, `performance.py`) that makes Moxie move and emote while she talks.
Unverified on a physical robot: [sim-as-a-client.md](../docs/architecture/sim-as-a-client.md#the-one-divergence-text-to-speech)
says a real robot synthesizes on-device from text and markup, while the firmware study
([perception pipeline](../docs/reverse-engineering/runtime/perception-pipeline.md)) says the server
renders the audio and on-device CereVoice is the fallback. The two pages disagree and the owner has not
ruled; treat the on-robot path as unverified.

All settings are listed in [`../mqtt/.env.example`](../mqtt/.env.example). Local engines and gateways
are both first-class: everything can run on your own machine with no internet, any OpenAI-compatible
provider works, and the console switches brain and voice per robot.

Setup guide: [voice and ears through a gateway](../docs/guides/gateway-voice-and-ears.md). Research on
giving Moxie sight: [vision](../docs/architecture/vision.md).

---
📖 [Project README](../README.md) · [AI seam contract](../docs/architecture/ai-seam.md) · [Moxie SDK](../mqtt/moxie_sdk/README.md)
