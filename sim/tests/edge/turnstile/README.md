# 🤖 test_turnstile sections

The sections of [`sim/test_turnstile.mjs`](../../../test_turnstile.mjs), in run order, over one
[`harness.mjs`](harness.mjs) (Cloudflare's documented dummy keys, a stubbed siteverify, `post()`,
`postAudio()`, the leak sweep).

- [`01_config_and_checks.mjs`](01_config_and_checks.mjs) — §1–4: config gate, half a pair, the three mandatory checks, the two reasons.
- [`02_fail_open.mjs`](02_fail_open.mjs) — §5–5c: fail open on transport failure, a wrong secret is HTTP 400, unknown route.
- [`03_slot_order_leaks.mjs`](03_slot_order_leaks.mjs) — §6–8: the slot comes back, cheapest refusal first, nothing leaks.
- [`04_browser.mjs`](04_browser.mjs) — §9: `sim/web/turnstile.js` under a stub window.
- [`05_contracts_ears_refund.mjs`](05_contracts_ears_refund.mjs) — §10–12: cross-file contracts, the ears, the budget refund.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
