# 🧪 Edge suite modules

Section modules for the node suites that exercise the Cloudflare Pages Functions in
[`functions/`](../../../functions/README.md). They are **not** run on their own: each entry
file imports its harness, then runs the sections in order and prints one summary.

- [`common.mjs`](common.mjs) — the shared ledger (`ok`/`eq`/`deep`), the fake deployment
  (`.invalid.test` host, test-only key), same-origin request builder, WAV fixture, section runner.
- [`demo_proxy/`](demo_proxy/README.md) — run by `node sim/test_demo_proxy.mjs`: the caps, origin
  pin, no-leak sweep, admission, cache tier, audio cache and re-roll of `/api/chat` + `/api/speech`.
- [`turnstile/`](turnstile/README.md) — run by `node sim/test_turnstile.mjs`: the bot control on
  `/api/chat` and `/api/transcribe`, server and browser halves.

Assertion labels are load-bearing: `sim/tools/turnstile_mutation_check.py` and
`unit_budget_mutation_check.py` match substrings of failing labels, so rename one only together
with its mutation row. `sim/tests/test_clock_dependence.py` and `test_node_global_stubs.py`
scan these modules as well as `sim/test_*.mjs`.

---
📖 [Tests index](../README.md) · [Back to top](../../../README.md)
