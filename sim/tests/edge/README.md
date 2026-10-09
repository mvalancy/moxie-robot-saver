# 🧪 `sim/tests/edge/` — Pages Functions suite sections

Section modules for the node suites that exercise the Cloudflare Pages Functions in
[`functions/`](../../../functions/README.md). They are **not** run on their own: each entry
file imports its harness, then runs the sections in order and prints one summary.

- [`common.mjs`](common.mjs) — the shared ledger (`ok`/`eq`/`deep`), the fake deployment
  (`.invalid.test` host, test-only key), same-origin request builder, WAV fixture, section runner,
  and `fakeCache` (a `caches.default` with every failure shape).
- [`demo_proxy/`](demo_proxy/README.md) — run by `node sim/test_demo_proxy.mjs`: the caps, origin
  pin, no-leak sweep, admission, cache tier, audio cache and re-roll of `/api/chat` + `/api/speech`.
- [`mode/`](mode/README.md) — run by `node sim/test_mode.mjs`: `_lib/env.js`, the envelope,
  `/api/health`, and the browser's mode machine and honest indicator.
- [`ceilings/`](ceilings/README.md) — run by `node sim/tests/helpers_shared_ceilings.mjs` (via
  `test_shared_ceilings.py`): the shared per-IP hour/day windows and the unit budget's day.
- [`turnstile/`](turnstile/README.md) — run by `node sim/test_turnstile.mjs`: the bot control on
  `/api/chat` and `/api/transcribe`, server and browser halves.
- [`ears/`](ears/README.md) — run by `node sim/test_demo_ears.mjs`: `/api/transcribe` and the real
  `sim/web/mic.js` on a virtual clock with a fake recorder.
- [`transport/`](transport/README.md) — run by `node sim/test_cloud_transport.mjs`: the live turn in
  the browser (voice-first ordering, every degraded path, the Turnstile send seam).
- [`fallback/`](fallback/README.md) — run by `node sim/test_fallback_coverage.mjs`: every line the
  degraded page can utter has a clip; the child's voice is clip-or-nothing.
- [`ambient/`](ambient/README.md) — run by `node sim/test_ambient.mjs`: the real `sim/web/ambient.js`
  on a virtual clock (the October set, the glitch beat, the post-goodbye aside) and the bridge's
  sign-off seam.

Assertion labels are load-bearing: `sim/tools/turnstile_mutation_check.py` and
`unit_budget_mutation_check.py` match substrings of failing labels, so rename one only together
with its mutation row. `sim/tests/test_clock_dependence.py` and `test_node_global_stubs.py`
scan these modules as well as `sim/test_*.mjs`.

---
📖 [Tests index](../README.md) · [Back to top](../../../README.md)
