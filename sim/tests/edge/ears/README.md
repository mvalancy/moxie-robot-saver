# 👂 test_demo_ears sections

The sections of [`sim/test_demo_ears.mjs`](../../../test_demo_ears.mjs), in run order, over one
[`harness.mjs`](harness.mjs) (Part A: the real `transcribe.js` behind a stubbed gateway, `clip()`,
`call()` with the no-leak sweep; Part B: the real `sim/web/mic.js` on a virtual clock with a fake
recorder — no microphone is ever opened; Part C: `bootPage`, the whole hosted page — the transport
harness's world of the real `stub.js`, `bridge/`, `mode.js`, `cloud-transport.js` and `voice/` with the
real `mic.js` loaded after them, as sim.html does).

- [`01_route_gates.mjs`](01_route_gates.mjs) — A1–A5: fail-safe default, byte caps and the free floor, origin pin, per-IP windows and budget, our own timeout.
- [`02_route_upstream.mjs`](02_route_upstream.mjs) — A6–A10: hostile upstream, byte sniffing and the container allowlist, the BUILT upstream body, the transcript (a sound label alone, `(machine whirring)`, is silence), the envelope.
- [`03_route_duration.mjs`](03_route_duration.mjs) — A-DUR / A-FMT / A-RDR: the server-side WAV duration ceiling, a WAV header the gateway cannot decode refused for free (it would be a 500 and a strike towards the STT cooldown), no redirect chasing.
- [`04_mic_capture.mjs`](04_mic_capture.mjs) — B1–B4: silence auto-stop, the 15 s hard stop, the published cap, where the clip goes, both reply shapes.
- [`05_mic_degraded.mjs`](05_mic_degraded.mjs) — B5–B7b: never a dead button, a consolation line never spends a live turn, client gates, capture failures, the WAV encoder.
- [`06_no_speech.mjs`](06_no_speech.mjs) — B8–B10: nothing said is nothing sent (the real capture, auto-stop and second tap), one turn at a time (the re-tap guard, a clip or turn in flight), a missing microphone named as one.
- [`07_barge_in.mjs`](07_barge_in.mjs) — B11–B13 (Part C): deliberate barge-in — the tap cuts the playing sentence at once, no further ticket of that reply is redeemed, a sentence in flight is dropped, nothing of hers sounds until the clip is transcribed, the interrupted text stays in the log; a reply landing into an open mic is held; a stub clip is cut too; without the transport's seam `mic.js` stops her voice itself; `body[data-mic]` lasts through the upload.
- [`08_ears_not_brain.mjs`](08_ears_not_brain.mjs) — B14–B16 (Part C, `Date.now` on the virtual clock): a chat-only deployment's four refused uploads at 45 s and 90 s leave the brain live while three chat errors still degrade it; transcribe refusals and failures stay the ears' own; an STT 429 holds the mic for its wait and says how long ("about a minute", the hour cap's minutes, "a few seconds") while typed lines go out; a chat 429 still pauses chat and leaves the ears alone.
- [`09_hold_bounds.mjs`](09_hold_bounds.mjs) — B17–B19 (Part C): what the hold covers and what bounds it — a permission prompt left unanswered holds nothing (a typed line is sent and answered, a reply in flight is heard, the grown-up redirect reaches a hurt child typed before or after the tap, `body[data-mic]` is never set, a second tap says what to do); a `blocked` redirect, a refused turn's stub line and the stub line for a line nothing live could take all wait for an open mic; an upload that never answers releases the ears at 30 s; a recording opened during an upload keeps them until it ends.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
