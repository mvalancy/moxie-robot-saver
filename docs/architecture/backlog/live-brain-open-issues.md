# Live brain: open issues

**Status:** open. The guard rails around each measurement are built and tested hermetically, but
none of the three questions below has a current answer from the live gateway. This note merges the
former `action-tag-drift`, `grounding-gate-unrun` and `one-brain-no-failover` briefs.

All three depend on the same thing: a working model gateway (`DEMO_GATEWAY_BASE_URL` for the hosted
demo, `MOXIE_LLM_*` for the Python runtime). On 2026-09-08 `gateway.graphlings.net` returned
`503 no_db_connection` for hours. That outage blocked the first two measurements and prompted the third.

## 1. Goodbyes that omit `<exit>`

**Problem.** One historical sample recorded 0/3 goodbye turns emitting `<exit>`. The replies were
warm and correct as speech, but they had no tag. Without the tag the runtime never ends the module,
so a child who says goodbye is not let go. Nobody has measured this again since.

**The wiring is intact.** In [`mqtt/moxie_sdk/apps/llm_app.py`](../../../mqtt/moxie_sdk/apps/llm_app.py),
`_LAST_CHECK` restates the tag rule and ends both `_TAG_EXAMPLES` and `_TAG_EXAMPLES_PLAIN`.
`_system()` returns `persona + who + fmt + tags`, so that rule is the last thing the model reads.

**Candidate cause (untested).** In expressive mode, `fmt` says *"Your face has these eleven
expressions and no others; anything else is ignored"*. The three tagged examples that follow (two
`<exit>`, one `<launch:DRAW>`) all use `"mood": "positive"`, which is not one of the eleven. At
runtime this does no harm: `positive` is an alias for mood 1 (`happy`) in
[`mqtt/moxie_sdk/vocab.py`](../../../mqtt/moxie_sdk/vocab.py) `MOOD_ALIASES`. But the model is
shown examples that break the rule stated just above them. The proposed fix changes those three
`"positive"` values to `"happy"`. It has not been applied, because a prompt change without a live
measurement proves nothing.

**How to measure.** Run `sim/tools/run_live_action_tags.sh` and nothing else. The Python supervisor
(`sim/tools/run_live_action_tags.py`, `sim/tools/action_tag_campaign.py`):
- runs only `test_the_model_ends_a_goodbye_with_a_real_exit_action` (3 trials);
- sets `MOXIE_MODEL_CALL_LIMIT=6`. [`mqtt/moxie_sdk/chat.py`](../../../mqtt/moxie_sdk/chat.py)
  checks that limit before every request, retries included, and an invalid limit fails closed;
- enforces a 360 s total deadline (`MOXIE_CAMPAIGN_TIMEOUT_SECONDS`), then SIGTERM, a 5 s grace,
  and SIGKILL;
- counts completed model responses separately from the returned `Reply`. `LLMApp.respond()` turns
  exhausted retries into friendly fallback speech, so non-empty speech does not prove the model
  answered. Scoring stops at the first incomplete trial;
- prints one JSON line of allow-listed counts. Model text, action identifiers and exception text
  are thrown away.

Controls in `sim/tests/test_action_tag_campaign.py` and `sim/tests/test_backoff.py` cover these
cases: 2/3 passes, 1/3 fails, two successes plus exhaustion is inconclusive, and a timeout or
missing prerequisite reports as a failure, never as a pass. To test the candidate cause, run once
as-is and once with the three edits, then compare.

**Open.** `ci-deep.yml` still runs the whole `sim/tests/test_live_action_tags.py` file (all three
tests) in its live-gateway step, which the bounded runner was built to avoid. DRAW-launch adherence
has no bounded runner yet.

## 2. The grounding gate has no usable result

**Problem.** [`functions/api/_lib/docsearch.js`](../../../functions/api/_lib/docsearch.js)
`bestPassage` used to pick a paragraph about QR pairing when asked how the robot talks to the cloud.
The fix (PR #247) scores the paragraph's section heading at `rank`'s 6:3 title-to-heading ratio.

| Claim | Status |
|---|---|
| The right paragraph is selected | proven by `sim/test_demo_proxy.mjs` |
| Her spoken answer actually uses it | unproven |

**The gate.** `sim/tools/grounding_probe.mjs` needs `--yes`, `--max-attempts 4..6` and
`--timeout-ms 1000..60000`. It asks the same production question with and without the candidate
passage. The negative control withholds the passage from two identical prompts and scores both
answers against it out of band. Any overlap there is sampling or common-word noise, not grounding.
One counter wraps the real `fetch`, counts every attempt including retries and timeouts, refuses
redirects, and holds the deadline until the whole body is read. `sim/tools/grounding_score.mjs` is
the pure scorer. `sim/test_mode.mjs` (`sim/tests/edge/mode/05_grounding_budget.mjs`) tests the
budget, the scorer's positive and negative cases, and one deliberately induced false positive, all
against loopback HTTP.

**History.** The one run so far happened during the 503 outage. It came back empty, not negative.
Since then, a non-2xx response or empty content throws and the probe reports `UNUSABLE`.

**Open.** Run the gate from a fresh branch at current `dev`, with an explicit budget. Report the
positive pair and the control separately. A hit on the control makes that run inconclusive. A clean
control with no positive evidence supports only a narrow conclusion: retrieval did not change that
answer in that sample.

## 3. One brain, no failover (proposal)

**Problem.** [`functions/api/_lib/env.js`](../../../functions/api/_lib/env.js) reads exactly one
`DEMO_GATEWAY_BASE_URL` / `DEMO_GATEWAY_API_KEY` pair. Every upstream failure in
[`functions/api/chat.js`](../../../functions/api/chat.js) and `_lib/upstream.js` ends at
`upstream_down`, `timeout`, or `gateway_unreachable_or_gated`. So one vendor's outage takes down
the whole brain of `moxie.mattvalancy.com`.

**What already works.** During the outage `/api/chat` returned `mode=degraded`,
`reason=upstream_down`. The page painted its badge, and `sim/web/ambient.js` spoke its `degraded`
line once. See [the live-Sim spec](live-sim-demo.md). One known trap: `/api/health` derives mode
from configuration and never calls the gateway, so it keeps reporting `live` through an upstream
outage ([deploy guide §6](../../guides/deploy-cloudflare.md)).

**Options.** Each needs an owner decision; cheapest first:

1. **Do nothing.** The scripted fallback already keeps the page honest.
2. **A second gateway pair** (for example `DEMO_GATEWAY_BASE_URL_2` / `_API_KEY_2`), tried once,
   and only on `upstream_down`. Never retry on `too_long`, `blocked`, or a rate limit, or refusals
   start costing double.
3. **A local engine as last resort**, keeping Piper and whisper first-class. This helps only
   self-hosted deployments; the Cloudflare demo has no machine to run it on.

**Constraint.** Base URL and key are resolved in one place (C3 in `env.js`: nothing hard-coded to
our gateway or domain), so a fallback means a second pair of variables and a retry at the call site.
Any retry must fit inside the existing wait budget. `DEMO_CHAT_TIMEOUT_MS` defaults to 20 000, and
`env.js` already clamps the admission queue so it never rivals the upstream timeout. Two attempts in
series must not double what a visitor waits.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Live-Sim spec](live-sim-demo.md) · [Deploy guide](../../guides/deploy-cloudflare.md)
