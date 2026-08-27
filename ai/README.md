# `ai/` — local AI adapters (Phase 3)

Pluggable **speech-to-text**, **LLM**, and **text-to-speech** for the conversation engine. Not built
yet; this is the plan.

## Principles
- **Local first.** Default to models running on this machine's GPU — no internet at runtime.
- **No vendor lock-in.** The LLM speaks to **any OpenAI-compatible endpoint** (a local LiteLLM
  gateway, vLLM, Ollama, LM Studio, …). OpenAI itself is only an *optional fallback*, never required.
- **Swappable.** STT, LLM, and TTS are independent adapters behind small interfaces.

## Planned adapters
| Slot | Default (local) | Fallback |
|------|-----------------|----------|
| STT | faster-whisper (GPU) | any Whisper-compatible service |
| LLM | local model via OpenAI-compatible API (LiteLLM/vLLM/Ollama/LM Studio) | any OpenAI-compatible endpoint |
| TTS | local voice synthesis | — |

## Status
🔨 Planned. Integration points come from the MQTT/conversation spec. See [`../ROADMAP.md`](../ROADMAP.md) Phase 3.
