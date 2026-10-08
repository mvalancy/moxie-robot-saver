# 👂 test_demo_ears sections

The sections of [`sim/test_demo_ears.mjs`](../../../test_demo_ears.mjs), in run order, over one
[`harness.mjs`](harness.mjs) (Part A: the real `transcribe.js` behind a stubbed gateway, `clip()`,
`call()` with the no-leak sweep; Part B: the real `sim/web/mic.js` on a virtual clock with a fake
recorder — no microphone is ever opened).

- [`01_route_gates.mjs`](01_route_gates.mjs) — A1–A5: fail-safe default, byte caps and the free floor, origin pin, per-IP windows and budget, our own timeout.
- [`02_route_upstream.mjs`](02_route_upstream.mjs) — A6–A10: hostile upstream, byte sniffing and the container allowlist, the BUILT upstream body, the transcript (a sound label alone, `(machine whirring)`, is silence), the envelope.
- [`03_route_duration.mjs`](03_route_duration.mjs) — A-DUR / A-FMT / A-RDR: the server-side WAV duration ceiling, a WAV header the gateway cannot decode refused for free (it would be a 500 and a strike towards the STT cooldown), no redirect chasing.
- [`04_mic_capture.mjs`](04_mic_capture.mjs) — B1–B4: silence auto-stop, the 15 s hard stop, the published cap, where the clip goes, both reply shapes.
- [`05_mic_degraded.mjs`](05_mic_degraded.mjs) — B5–B7b: never a dead button, a consolation line never spends a live turn, client gates, capture failures, the WAV encoder.
- [`06_no_speech.mjs`](06_no_speech.mjs) — B8–B10: nothing said is nothing sent (the real capture, auto-stop and second tap), one turn at a time (the re-tap guard, a clip or turn in flight), a missing microphone named as one.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
