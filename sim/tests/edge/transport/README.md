# 🔌 test_cloud_transport sections

The sections of [`sim/test_cloud_transport.mjs`](../../../test_cloud_transport.mjs), in run order,
over one [`harness.mjs`](harness.mjs): the REAL `stub.js`, `bridge/`, `mode.js` and
`cloud-transport.js` loaded as source under a fake DOM/audio/mqtt/fetch on a virtual clock, plus
answer builders (`live`, `serve`, `said`, `ticket`, `voiced`).

- [`01_wrapper_turn.mjs`](01_wrapper_turn.mjs) — §1–2: a wrapper, not a replacement; one whole live turn.
- [`02_voice_order.mjs`](02_voice_order.mjs) — §3–4: slow speech, late audio, and the double-voice hazard proven real.
- [`03_degraded.mjs`](03_degraded.mjs) — §5: every degraded path answers.
- [`04_talk_scripted.mjs`](04_talk_scripted.mjs) — §6–6b: the Talk box; the consolation line is free.
- [`05_bot_control.mjs`](05_bot_control.mjs) — §7: one fresh Turnstile token per send, never a dead Send.

`sim/tools/turnstile_mutation_check.py` rows with runner `UX` match labels in `05_bot_control.mjs`.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
