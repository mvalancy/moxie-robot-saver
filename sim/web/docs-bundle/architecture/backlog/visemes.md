# `TTSMark[]` visemes — research findings and proposal

**Status:** proposed (research slice, nothing built) — `marks` is still always empty: constructed from an
unset parameter in [`mqtt/moxie_sdk/tts.py`](../../../mqtt/moxie_sdk/tts.py) and hardcoded `marks: []` in
[`functions/api/_lib/wire.js`](../../../functions/api/_lib/wire.js). The browser consumer
(`markTrack` in [`sim/web/voice/cloud.js`](../../../sim/web/voice/cloud.js)) already exists.

This answers the [OpenMoxie feature audit](../openmoxie-feature-audit.md)'s §4.4 #4 ("`TTSMark[]`
visemes"), which asked for a research slice rather than a build. The short answer: **Piper can produce
exact phoneme timing, but the renderer, not the producer, is the bottleneck.** Moxie's SIM mouth has one
drivable degree of freedom, and the rule that combines marks with the audio envelope can only open the
mouth further, never close it. Real marks on today's face would look nearly the same as the envelope.

**Clean-room.** Findings come from this repository and the behavior of the installed Piper package
(`piper1-gpl`); no Piper source is copied. OpenMoxie has no viseme implementation, so it is not prior art
here. The `TTSMark` shape is our own recovered proto ([`ai-seam.md`](../ai-seam.md)).

## 1. What we know

### 1.1 The pipeline today

| Stage | Fact | Where |
|---|---|---|
| Wire shape | `TTSMark {uint32 time, start, end; string type, value}`, `repeated TTSMark marks` on `CloudTTSResponse` | `tts.py` header, `ai-seam.md` |
| Producer (SDK) | `build_cloud_tts_response(…, marks=None)` passes marks through, but no caller supplies any. `Synthesizer.synthesize(text, voice) -> bytes` has no channel for alignment | [`tts.py`](../../../mqtt/moxie_sdk/tts.py) |
| Producer (hosted) | `buildCloudTtsResponse` hardcodes `marks: []` | [`functions/api/_lib/wire.js`](../../../functions/api/_lib/wire.js) |
| Producer (gateway) | an OpenAI-compatible `/v1/audio/speech` call returning audio bytes; the protocol has no alignment field | `tts.py` |
| Producer (local browser Piper) | `GET /tts?text=…` returns `audio/wav` directly — no envelope to carry marks; shells out to the `piper` CLI | [`sim/tts/server.py`](../../../sim/tts/server.py) |
| Hosted TTS cache | stores a bare 16-bit WAV per entry; key is version-tagged `lp("v1")`, whose documented meaning is the entry format — bumping it abandons all entries | [`functions/api/_lib/ttscache.js`](../../../functions/api/_lib/ttscache.js) |
| SIL robot | decodes marks and logs the count (always 0) | [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py) |
| Browser consumer | `VISEME_OPEN` (17-entry viseme → openness table) and `markTrack()` turn marks into a `{t, open}` track | [`sim/web/voice/cloud.js`](../../../sim/web/voice/cloud.js) |
| Combine rule | `open = Math.max(open, track[mi].open)` — the track can only raise the mouth | `cloud.js` |
| Renderer | one scalar input: `setMouthOpen(v)` → `mouthDrive.v`, combined as `Math.max(P.mouthOpen, mouthDrive.v)`; width, curve and position come only from the expression preset | [`sim/web/moxie.js`](../../../sim/web/moxie.js), [`sim/web/moxie/face.js`](../../../sim/web/moxie/face.js) |
| Spectrum | the audio `AnalyserNode`s call only `getByteTimeDomainData`; the FFT they compute is discarded | `sim/web/voice/cloud.js`, `local.js` |
| Voices | `phoneme_type: espeak`, 22,050 Hz, IPA `phoneme_id_map`, `noise_w = 0.8`; the `.onnx` weights are **not in the repo** (only `.onnx.json` configs) | [`sim/tts/voices/`](../../../sim/tts/voices/) |

A real robot synthesizes speech on-device, so it never receives our `marks`; today only our SIM would.

### 1.2 What Piper can do (measured on the build box, Piper 1.7.0)

- A stock Piper `.onnx` has **one output**, the waveform; phoneme durations are computed inside the graph
  and discarded.
- Piper ships a patcher (`patch_voice_with_alignment.add_alignment_output()`) that exposes the duration
  predictor's `Ceil` output as a second graph output. Patched, `synthesize(..., include_alignments=True)`
  returns **exact** per-phoneme sample counts — they summed to the audio's sample count exactly.
- Durations are **stochastic** (`noise_w = 0.8`): the same sentence times differently each run. Marks are
  valid only for the audio produced in the same inference; they can never be regenerated later for cached
  audio.
- The phoneme sequence is free via Piper's bundled espeak bridge (IPA, NFD-decomposed — stress and length
  marks arrive as separate symbols).
- Word timings are not emitted, but `' '` separator phonemes carry durations, so word boundaries can be
  derived; mapping phonemes back to source-text characters is on us.
- The Piper CLI has no alignment flag; `piper.http_server` does serve alignments. The in-memory patch
  needs the `onnx` package; patching once offline and shipping the patched voice avoids that at runtime.

### 1.3 The finding

1. **The consumer is already built.** This is a producer gap, not a browser gap.
2. **The combine rule forbids the most legible viseme.** Bilabial closure (`p b m`) needs the mouth to
   close while the audio is loud; `Math.max` against the envelope makes that impossible. `VISEME_OPEN`
   even has a near-closed value for `p`; it is thrown away every frame.
3. **One scalar cannot carry a viseme.** `/i/` (wide, nearly shut) and `/u/` (rounded, nearly shut) are
   the same picture. Openness alone also correlates with loudness, which is why the envelope works well
   enough to go unquestioned.
4. **The spectrum is free.** Band-energy ratios from the FFT the analyser already computes can separate
   open vowels, fricatives and closures on **every** path — hosted demo, cache hits, gateway, tone synth —
   with no wire or cache change.

### 1.4 Is it worth it?

A typical hosted reply is capped at `DEMO_MAX_TTS_CHARS = 300` ([`functions/api/_lib/env.js`](../../../functions/api/_lib/env.js)),
roughly 1.7–3 s and 12–20 merged visemes. On a one-dimensional mouth that cannot close, a viseme track and
an envelope produce similar traces. So "populate `marks[]`" alone is a small visible win; the visible step
change is a mouth that can **close** and **round**, and that needs no marks at all.

**Unknowns.** U1: whether a visitor can tell the difference — never measured; it should gate the expensive
work. U2: whether anything but our SIM will ever read our marks — if not, the viseme alphabet is a private
detail; if so, it is a compatibility surface to freeze in `ai-seam.md`.

## 2. Proposal

### P0 — a mouth that can be wrong (S, SIM only)

- `moxie.setMouthShape({open, wide})` and `getMouthShape()` beside the existing scalar API
  (`setMouthOpen(v)` kept as a shim); `wide` drives `mouthWidth` under lip-sync.
- Change the combine rule so a present track **replaces** the envelope (with a short attack) instead of
  flooring it.
- A spectral estimator (two or three band-energy ratios → `{open, wide}`) as the default driver whenever
  there are no marks.

Touches only `sim/web/`; improves every path; testable today with synthetic tracks.

### P1 — real marks on the local Piper path (M)

- An opt-in `Synthesizer.synthesize_aligned(text, voice) -> (bytes, marks)` whose base implementation
  returns `[]`, so no backend breaks. Piper overrides it; gateway and tone inherit `[]` permanently. A
  fallback synthesizer must **drop** marks on downgrade.
- Mark record: `time` = ms from the start of **this chunk's** audio; `start`/`end` = character offsets or
  an honest `0`; `type = "viseme"`; `value` = a viseme id, never raw IPA.
- An IPA → viseme table (extending `VISEME_OPEN`'s 17 keys with a `wide` column); stress/length
  diacritics fold into the preceding phoneme and emit no mark.
- Ship alignment-patched voices; move `sim/tts/server.py` off the CLI or leave it on the P0 estimator.

### P2 — marks on the hosted demo (L, only if P1's A/B says visitors can tell)

Store marks **inside the same cache entry** as the audio (e.g. a custom RIFF chunk appended to the WAV, so
the existing decoder still reads the audio half), bump the key to `lp("v2")` (abandoning all entries, as
that tag is documented to mean), add `o.marks` to `buildCloudTtsResponse`, and find a gateway that can
return alignment (a non-standard endpoint). Regenerating marks on a cache hit is ruled out: durations are
stochastic, so they would describe a different utterance. A separately keyed marks entry is ruled out for
the same reason `ttscache.js` refuses weak keys — audio from one sentence, mouth from another.

### Fallback rules

Marks are an enhancement, never a precondition: playback must never stall for want of them. There is one
mouth driver with an optional marks input, not a branch per deployment. A partial track (marks for chunk 0
only) degrades per chunk. Stale marks are worse than none.

### Testing

Order, not magnitude — a `marks.length > 0` check passes a shuffled, reversed or late track.

- **Discrimination:** sentence A's track must score better against A's phoneme ground truth than against
  B's. No absolute floor.
- **Mutations:** time-reversed, +200 ms shifted, shuffled, constant, and swapped tracks must each score
  worse.
- **Closure (red today):** on "mama"/"puppy", some rendered frame has `open < 0.1` while the envelope is
  loud.
- **Width (red today):** `/i/` and `/u/` render different `wide` at equal `open`.
- Assertions read back `getMouthShape()` (what was rendered), as
  [`sim/test_audio.mjs`](../../../sim/test_audio.mjs)'s existing viseme check reads the rendered mouth.
- `faster-whisper` word timestamps ([`mqtt/moxie_sdk/stt.py`](../../../mqtt/moxie_sdk/stt.py)) are a
  suitable independent oracle, too expensive to ship.
- CI has no Piper weights, so alignment tests need a committed golden WAV + timing; stochastic durations
  mean it can never be regenerated byte-for-byte.

### Open questions for the owner

1. Does the mouth get a second dimension? If not, close the viseme row as "not worth it" and keep only the
   combine-rule fix.
2. Is `marks` a private detail or a compatibility surface (U2)?
3. Is P2 worth a cache flush and a non-standard gateway endpoint? Recommended: not until P1's A/B answers
   U1.
4. May a patched `.onnx` be shipped? Without it P1 gets phoneme sequence but not true durations.

**Recommendation:** build P0, keep P1 specified, do not build P2.

---

📖 [Backlog index](README.md) · [Architecture index](../README.md) ·
[OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[AI seam](../ai-seam.md) · [The SIM as a client](../sim-as-a-client.md) ·
[Expressiveness](expressiveness.md) · [Live Sim demo](live-sim-demo.md) ·
[OTA push — the other spec-only brief](ota-push.md) ·
[Docs index](../../README.md)
