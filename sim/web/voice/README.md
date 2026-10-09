# `sim/web/voice/` — Moxie's voice and sound effects

Classic scripts loaded by [`../sim.html`](../sim.html) in this order, sharing
`window.__moxieVoice` (created fresh by `core.js`) for the same reasons as
[`../bridge/`](../bridge/README.md). `index.js` publishes `window.moxieAudio`.

- **`core.js`** — shared state, the Web Audio context, synthesized SFX, the speaking
  predicates (`isMoxieSpeaking` / `isMoxieBusy`), `stop()`, and THE THIRD SEAM (`floor`:
  nothing starts on top of Moxie answering).
- **`local.js`** — the local voices in priority order: pre-cached clip
  (`../audio/index.json`), a reachable Piper sidecar, `speechSynthesis`; and
  `speakClipOnly`, the child's clip-or-nothing voice.
- **`cloud.js`** — the SERVER voice: `CloudTTSResponse` decode (base64 int16 PCM, no
  SDK), the chunk-ordering player (ORDERING / GAP / EVENT rules), marks→mouth lip-sync,
  playback records, and ownership of `#tts-status`. `stopCloudTTS` stops the playing chunk
  and drops the queue; `dropQueuedTTS` drops only the queue, so a safety line can follow
  the sentence now playing (`../cloud-transport.js`, W4-S7).
- **`index.js`** — the `window.moxieAudio` surface and the first-gesture unlock.

Not to be confused with [`../audio/`](../audio/), the pre-rendered clip directory. Covered
by the `/voice/*` `no-cache` rule in [`../_headers`](../_headers). Tests:
`sim/test_audio.mjs`, `test_wav_decode.mjs`, `test_fallback_coverage.mjs`, `test_voice.mjs`,
and the real-`voice/` sections of `test_cloud_transport.mjs`.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
