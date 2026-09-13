# The grounding gate has never run

**Status (2026-09-12): DO NOT RUN — transport bounded; scoring-control repair awaiting CI.** The retrieval fix it was meant to
judge **merged to `dev` as `2a4a32d`** ([PR #247](https://github.com/mvalancy/moxie-robot-saver/pull/247),
all four checks green including the browser suite) with its own narrower proof. The old `wt-passage2`
watcher worktree no longer exists; run the gate only from a fresh branch at the current `dev` SHA.
**This brief exists so the gap between those two proofs is not quietly forgotten.**

## The two claims, which are not the same claim

`functions/api/_lib/docsearch.js` `bestPassage` used to hand the model a paragraph about **QR pairing
stages** when asked *"how does the robot talk to the cloud?"* — right document, wrong paragraph. The
fix scores the paragraph's section heading at the same 6:3 title-to-heading ratio `rank` already uses.

| Claim | Layer | Status |
|---|---|---|
| The right paragraph is selected | retrieval | **PROVEN** — `sim/test_demo_proxy.mjs`, and the fixture reports `2 failure(s)` against `origin/dev`'s `docsearch.js` |
| Her *answer* stops glossing | end to end | **UNPROVEN** — the gate below has never executed |

Shipping the first and describing it as the second is exactly the substitution this repo keeps
catching: **an assertion that is true but aimed one inch left of the risk.**

## The gate

`sim/tools/grounding_probe.mjs`, opt-in behind `--yes`, compares one answer with and without a retrieved
passage. Its old negative claim was invalid: *"tell me a joke"* retrieved no passage, so the passage-token
set was empty and `fromPassage` was mathematically empty whatever either answer said. The repaired control
asks the same production question twice with the real candidate passage withheld from both identical
prompts, while scoring out of band against that non-empty passage. Any reported overlap is therefore the
acknowledged sampling/common-word false-positive channel, not grounding. `grounding_score.mjs` exposes the
pure discriminator, and `sim/test_mode.mjs` proves a real positive, a real negative, and a deliberately
induced false positive without loading credentials.

The operator must also provide `--max-attempts 4..6` and `--timeout-ms 1000..60000`. One shared
counter wraps the actual `fetch`, increments before every outbound attempt (including retries and
timeouts), refuses redirects rather than allowing a second uncounted request, and holds the deadline until
the complete success or error body has been consumed. The final line reports used/allowed attempts. The
four logical calls therefore cannot silently amplify to sixteen, forward authorization across a redirect,
or hang after response headers. The focused grounding-budget block in `sim/test_mode.mjs` proves these
boundaries against real loopback HTTP without loading credentials or contacting a gateway.

## Why it has not run

`gateway.graphlings.net` returned **503 `no_db_connection`** on every poll across several hours on
2026-09-08 — 16 in the first ~12 minutes, still failing hours later, on both urllib and the OpenAI
SDK. Intermittent at first (a pytest run minutes earlier got real replies), then sustained. This is
upstream of everything in this repo.

The public demo degrades correctly through it: `/api/chat` answers `mode=degraded`,
`reason=upstream_down`, and the page paints its badge and speaks its one degraded line. **Note that
`/api/health` reports `mode=live` throughout** — it derives mode from configuration and never calls
the gateway, which is what makes a 30-second poll free. See
[the deploy guide §6](../../guides/deploy-cloudflare.md) for the call that actually answers the
question, and the two ways it looks like an outage when it is not.

## What has to happen

1. Merge the scoring-control repair only after its exact-head checks pass.
2. Run the repaired gate with an explicit remaining batch budget and deadline.
3. Record the result here, pass **or fail**. A fail is the useful outcome: it would mean the passage
   reaching the model was never the binding constraint, and the three prompting attempts that came
   before it were aimed at the right layer after all.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Deploy guide](../../guides/deploy-cloudflare.md)
