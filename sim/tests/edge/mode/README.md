# 🚦 test_mode sections

The sections of [`sim/test_mode.mjs`](../../../test_mode.mjs), in run order, over one
[`harness.mjs`](harness.mjs) (the ledger, the fake deployment, the `/api/health` call, a fake
DOM element for `env.js`).

- [`01_config_envelope.mjs`](01_config_envelope.mjs) — §1–2: `_lib/env.js` defaults, clamps and
  fail-safe; `_lib/envelope.js`'s closed shape, reasons, statuses and headers.
- [`02_health.mjs`](02_health.mjs) — §3: `/api/health` reads the real counters and never spends.
- [`03_mode_machine.mjs`](03_mode_machine.mjs) — §4: `sim/web/mode.js` on injected time and timers.
- [`04_indicator_lint.mjs`](04_indicator_lint.mjs) — §5–6: `sim/web/env.js` driven by the mode on
  a fake DOM, and the public-repo secret lint.
- [`05_grounding_budget.mjs`](05_grounding_budget.mjs) — §7: the paid grounding probe's attempt
  budget, with zero network calls.
- [`06_outage_honesty.mjs`](06_outage_honesty.mjs) — §8: outages, timeouts and the hour cap
  replayed on a clock, with envelopes production can send; and what env.js paints for a rest.
- [`07_ears_apart.mjs`](07_ears_apart.mjs) — §9: the ears apart — what `/api/transcribe` reports
  (`route: "ears"`) opens the ears' own window and never moves the brain's state, strikes or window;
  every `/api/chat` rule of §4 and §8 unchanged beside it; the ears' default windows (§9f).

The sections run in order: §4–5 and §8–9 replace `fetch`, timers and `Date.now` for good.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
