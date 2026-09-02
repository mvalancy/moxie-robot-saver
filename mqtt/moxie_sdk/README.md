# 🧠 Moxie SDK

The developer-facing SDK: drive Moxie as an avatar from any app or AI, without touching the raw MQTT wire
protocol. The [supervisor](../supervisor/) translates the robot's MQTT traffic into these clean calls.

- [`app.py`](app.py) — the `MoxieApp` interface, the heart of the SDK (what an app implements to control Moxie).
- [`types.py`](types.py) — shared data types passed to/from apps.
- [`actions.py`](actions.py) — parses robot-control tags (`<exit>`, `<launch:MOD:CID>`, …) out of a brain's own text into real `Reply.actions`, and the prompt paragraph that teaches a model to use them.
- [`apps/`](apps/) — ready-made `MoxieApp` implementations (echo, LLM brain, webhook).
- [`schedule.py`](schedule.py) — builds the day plan (`ContentSchedule`) the robot pulls at session
  start: onboarding, a rotation of on-board activities that skips what the child already finished,
  and interleaved chats. Pure + deterministic.
- [`store.py`](store.py) — the durable per-robot store (JSON under `MOXIE_DATA_DIR`, default
  [`../data/`](../data/)) that remembers reported `mentor_behaviors` across restarts.
- [`wire.py`](wire.py) — the JSON encoders/decoders for the robot-cloud bus (chat responses,
  `query_result`, mentor-behavior reports). A chat response can be one chunk of several
  (`chunk_num` + `consistency_control.is_completed`) — that is how a slow turn answers twice.
- [`tts.py`](tts.py) — the voice seam: `strip_markup` (behavior marks **and** emoji off, so a
  TTS engine never reads "grinning face" aloud), the `Synthesizer` interface (Piper, an
  OpenAI-compatible voice server, the built-in tone) and the `CloudTTSResponse` encoder.
- [`filler.py`](filler.py) — the short "let me think" lines, with thinking markup, that the
  runtime speaks when the brain outlives its latency budget (see
  [`../supervisor/`](../supervisor/)).
- [`segment.py`](segment.py) — the sentence segmenter a streaming brain talks through:
  dependency-free, pure, and careful about decimals, abbreviations, ellipses and lines too
  short to speak alone. Each finished sentence becomes one `RemoteChatResponse` chunk, so a
  child hears the first line at first-token latency instead of at whole-answer latency.
- [`chat.py`](chat.py) — the LLM boundary: `make_openai_chat` (a whole completion),
  `make_openai_stream` / `stream_completion` (text deltas), plus the rate-limit
  classification, `Pacer` and `call_with_backoff` both share.

---
📖 [Back to top](../../README.md)
