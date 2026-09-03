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
- [`broker_acl.py`](broker_acl.py) — 🔐 renders a mosquitto ACL from the pairing gate's
  `fleet/permits.json`: the `%c` device floor plus one `user d_<uuid>` block per permitted
  robot. **Generated now and inert until P1** — no robot authenticates yet, so no `user`
  block can match, and a `user` block is exactly what a broker matches only on a *verified*
  username. It exists so that when the broker gains a way to verify a device, the permit
  list stays the one place that says which robots are ours. Pure, stdlib only, byte-stable;
  `python3 -m moxie_sdk.broker_acl <permits.json>` prints it.
  ([`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md) §2.3)
- [`store.py`](store.py) — the durable per-robot store (JSON under `MOXIE_DATA_DIR`, default
  [`../data/`](../data/)) that remembers reported `mentor_behaviors` across restarts.
- [`faces.py`](faces.py) — 🎨 **Moxie's look**: the appearance catalog and how a selection
  becomes `child_pii.face_options` + the `child_pii.id` texture cache-buster. Pure (the catalog
  is data: [`face_assets.json`](face_assets.json), loaded through a `catalog=` seam). **72
  options across 11 of the 14 `MoxieCustomizationType` slots and no invented asset ids** — the
  12 our docs cite with hex (`origin: recovered-enum`) plus 60 asset ids ingested as *cited
  data* from OpenMoxie (`origin: openmoxie-manifest`, all `caution`-flagged). An id outside the
  catalog still goes through `face.custom`. Read the module docstring before touching it: it
  carries the citation trail, the origin-dependent wire spelling and the two flagged
  assumptions. Parent-facing
  summary: [Moxie's look guide](../../docs/guides/moxies-look.md).
- [`wire.py`](wire.py) — the JSON encoders/decoders for the robot-cloud bus (chat responses,
  `query_result`, mentor-behavior reports). A chat response can be one chunk of several
  (`chunk_num` + `consistency_control.is_completed`) — that is how a slow turn answers twice.
- [`tts.py`](tts.py) — the voice seam: `strip_markup` (behavior marks **and** emoji off, so a
  TTS engine never reads "grinning face" aloud), the `Synthesizer` interface (Piper, an
  OpenAI-compatible voice server, the built-in tone) and the `CloudTTSResponse` encoder.
- [`stt.py`](stt.py) — the ears ([ai-seam](../../docs/architecture/ai-seam.md) §1): the
  dependency-free `zmqSTTRequest` protobuf reader, `SttSession` (accumulate one utterance's
  VAD-tagged frames, transcribe on `END_OF_SPEECH`, at the bus's 16 kHz) and two **first-class**
  engines behind one `Transcriber` interface — `WhisperTranscriber` (local faster-whisper: no
  network, no key, the home-appliance answer) and `OpenAITranscriber` (an OpenAI-shaped
  `/audio/transcriptions`; live on our gateway since 2026-09-02, the answer for a hosted box with
  nowhere to put a model). `FallbackTranscriber` puts one behind the other and latches on the
  first failure, so an outage is a downgrade rather than a traceback mid-sentence. Setup + the
  deployment matrix: [litellm-stt-setup.md](../../docs/guides/litellm-stt-setup.md).
- [`audio_models.py`](audio_models.py) — pure name rules that split a gateway's flat
  `GET /v1/models` list into voices and ears (`classify_audio_models`, `default_tts_model`,
  `default_stt_model`). LiteLLM's listing says nothing about *mode*, so the names are the only
  contract; pinned by a golden test against the ids the gateway really served.
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
- [`brains.py`](brains.py) — 🧠 **which brain answers this child.** A closed *positive list* of the
  brains this appliance knows (`llm`, `content`, `webhook`, `echo`) — a name in it resolves to a
  builder in [`config.BRAIN_BUILDERS`](../config.py), a name that is not is **refused, never
  guessed** — plus the resolution of `defaults ⊕ fleet ⊕ per-robot` (the scalar case of
  `cloud_config.merge_config_layers`, not a second layering) and the rule that an explicit
  `MOXIE_APP` **pins** the appliance's brain over any per-child pick. Dependency-free, like
  `voice_settings.py`: the runtime builds through a seam, so every test runs with no endpoint and
  no key. Design + gaps: [brain-picker.md](../../docs/architecture/backlog/brain-picker.md).
- [`chat.py`](chat.py) — the LLM boundary: `make_openai_chat` (a whole completion),
  `make_openai_stream` / `stream_completion` (text deltas), plus the rate-limit
  classification, `Pacer` and `call_with_backoff` both share.

---
📖 [Back to top](../../README.md)
