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
- [`faces.py`](faces.py) — 🎨 **Moxie's look**: the frozen appearance catalog and how a
  selection becomes `child_pii.face_options` + the `child_pii.id` texture cache-buster. Pure.
  Our recovered docs name all 14 `MoxieCustomizationType` slots but list options for only two
  (the eye/face colour enums, with hex), so it ships **12 cited options and no invented asset
  ids** — a parent supplies their own through `face.custom`. Read the module docstring before
  touching it: it carries the citation trail and the two flagged assumptions. Parent-facing
  summary: [Moxie's look guide](../../docs/guides/moxies-look.md).
- [`wire.py`](wire.py) — the JSON encoders/decoders for the robot-cloud bus (chat responses,
  `query_result`, mentor-behavior reports). A chat response can be one chunk of several
  (`chunk_num` + `consistency_control.is_completed`) — that is how a slow turn answers twice.
- [`tts.py`](tts.py) — the voice seam: `strip_markup` (behavior marks **and** emoji off, so a
  TTS engine never reads "grinning face" aloud), the `Synthesizer` interface (Piper, an
  OpenAI-compatible voice server, the built-in tone) and the `CloudTTSResponse` encoder.
- [`filler.py`](filler.py) — the short "let me think" lines, with thinking markup, that the
  runtime speaks when the brain outlives its latency budget (see
  [`../supervisor/`](../supervisor/)).
- [`presence.py`](presence.py) — what Moxie's own eyes tell the server. The robot runs vision
  on-device and emits semantic events only (`eb-found-face`, `eb-lost-target`, QR/ArUco/book —
  **no pixels, no bounding box, no identity**), delivered as the `speech` of a chat request once
  the brain subscribes. This is the pure state machine that folds them into a per-robot presence
  record + derived `arrived`/`left` signals, with hysteresis so a face flickering at the edge of
  the frame cannot spam the brain, plus the short prompt line and the greeting lines the runtime
  speaks. Design + honesty: [vision.md §7](../../docs/architecture/vision.md).
- [`segment.py`](segment.py) — the sentence segmenter a streaming brain talks through:
  dependency-free, pure, and careful about decimals, abbreviations, ellipses and lines too
  short to speak alone. Each finished sentence becomes one `RemoteChatResponse` chunk, so a
  child hears the first line at first-token latency instead of at whole-answer latency.
- [`safety.py`](safety.py) + [`safety_rules.json`](safety_rules.json) — the child-safety seam
  (`InputSafety`, [ai-seam](../../docs/architecture/ai-seam.md) §2): `assess(text, role=…)`
  returns the moderation verdict the runtime enforces **before** the brain is called and
  **before** every streamed chunk is published. v1 is a transparent local rule engine — the
  whole table is the JSON file, which a parent can read — behind a `Classifier` protocol a
  local model classifier can drop into. Parent-facing summary:
  [child-safety guide](../../docs/guides/child-safety.md).
- [`chat.py`](chat.py) — the LLM boundary: `make_openai_chat` (a whole completion),
  `make_openai_stream` / `stream_completion` (text deltas), plus the rate-limit
  classification, `Pacer` and `call_with_backoff` both share.

---
📖 [Back to top](../../README.md)
