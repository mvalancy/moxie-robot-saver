# 🔁 Current session-loop control

> **Active policy from 2026-09-12.** This page is the compact control document for recurring Codex
> work. It supersedes the model, attribution, cadence, and direct-to-`dev` instructions embedded in the
> historical [`orchestration-plan.md`](orchestration-plan.md). It does **not** supersede that plan's
> clean-room, secrets, verification, PR, promotion, or release safeguards.

The long orchestration log is valuable evidence and poor live state: it records corrected hypotheses,
superseded priorities, and older Claude Opus/Fable operating rules in one 2,000-line document. A fresh
session reads this page first, inspects the current code and GitHub state, and treats historical status
claims as things to verify.

## Objectives, in order

1. Make the public Sim an alive, playful, safe, affordable 90-second meeting with Moxie.
2. Complete the self-hosted cloud: brain, voice, ears, content, management, and interchangeable clients.
3. Adopt the best evidenced OpenMoxie behavior behind our contracts.
4. Advance the brain-agnostic platform without claiming physical-robot proof we do not have.

The owner's 2026-09-09 product decisions are settled: **meet-Moxie toy**, effortless chat rather than
structured turns or missions, and only the existing privacy-first aggregate Cloudflare measurement.
Do not reopen them merely because an older paragraph says gamification is unspecified.

## One serialized loop, four duties

The local scheduler checks these cadences and queues every due duty into **one existing session**. When
cadences coincide, the session performs one reconnaissance pass and at most one implementation slice.
It does not create four competing writers.

| Cadence | Duty |
|---|---|
| **1 hour** | Deliver one bounded, verified increment toward the highest ready objective. |
| **2 hours** | Ask basic questions, trace one real user path, and find one evidenced feature, structure, or source-of-truth gap. |
| **3 hours** | Rotate one security, spending, performance, privacy, browser, lifecycle, or test-evidence boundary. |
| **4 hours** | Build a status packet and require an independent, read-only **`gpt-6-astra`** strategy review; accept only validated, versioned prompt refinements. |

Cadence means *eligible to run*. A busy session, sleeping host, or unavailable model may delay a fire.
Missed duties coalesce once after recovery; they never become a catch-up storm.

## Rules per queued batch

- Refresh git, worktrees, PRs, checks, scheduled monitors, and the relevant deployed state before choosing
  work. Discover mutable identifiers; never preserve a PR number or run id in a prompt.
- State the question, evidence, objective benefit, acceptance criterion, owned files, and stopping condition
  before editing.
- Use one `feat/*` worktree and a PR into `dev` for every change. **Recurring loops do not commit directly
  to `dev` or `main`.** Merge only after the repository's green gate; promote by `RELEASING.md`; never tag
  because a timer fired.
- Run at most one heavy local browser or suite workload. Reuse green scheduled evidence covering the same
  content instead of creating contention and calling the result a performance measurement.
- Hermetic checks run without deployment credentials. A live probe must enforce a timeout and a ceiling on
  **actual outbound attempts including retries**. The default coalesced-batch ceiling is six attempts and
  is not a target. Routine paid monitoring is forbidden.
- Conclude with `shipped`, `verified`, `hypothesis-falsified`, `blocked`, or `clean-noop`. A commit is not
  evidence of progress, and no work is manufactured to fill an interval.
- Dynamic receipts, questions, blocker counts, and four-hour reports live in machine-local scheduler state.
  Do not push a status-only commit, and do not open a promotion PR except for an owner-approved major milestone.

## Anti-stagnation controls

- One outstanding queued batch and one active implementation owner maximum.
- Two unchanged failures require a changed hypothesis, instrument, or environment before another attempt.
- Three encounters with the same external blocker park it until its explicit reopening condition changes.
- Two four-hour reviews without verified objective movement require a smaller or different executable path.
- Two rediscoveries of the same stale active claim make source-of-truth reconciliation the next relevant
  slice instead of adding another status paragraph.
- A slice active for 90 minutes checkpoints evidence, files, and next action; another owner does not take it
  automatically.
- If Astra is unavailable, retry once with backoff, retain the last valid policy, and record the missed
  review. Three missed review windows become a visible blocker without stopping already-approved work.

The four-hour reviewer returns recommendations and an exact prompt patch; it cannot directly edit its own
scheduler. Cadences, fixed objectives, review model, privacy rules, budgets, and release authority are not
self-modifying.

## Current ranked evidence gaps (2026-09-12)

1. **Hosted grounding quality:** first make the probe's retry-amplified spend and fetch timeout enforceable,
   then run a production-shaped positive case and a real negative control. PR #248 proved fit within the
   2,048-token window; it did not prove the whole answer-quality claim forever.
2. **Absolute spend protection:** the Cache API counters in `functions/api/_lib/limits.js` are per-colo,
   lose increments under a burst, fail open, and explicitly are not a global ceiling. Establish whether the
   deployment credential has a gateway-enforced hard budget/RPM/TPM limit before strengthening claims.
3. **First-visit evidence:** exercise instruction, microphone permission/refusal, waiting, reply playback,
   interruption, second turn, goodbye, and degraded mode; fix the highest observed stranger-facing defect.
4. **Head-sweep instrumentation:** the old dt-clamp premise was falsified. Measure animation steps separately
   from `requestAnimationFrame` callbacks before changing the wait again.
5. **MQTT action adherence:** re-measure goodbye `<exit>` behavior with a targeted hard-capped test. This is
   the SDK system prompt, separate from hosted Functions grounding.

Two limitations must remain visible. `/api/health` deliberately does not call the gateway, so `mode=live`
means configured and locally within limits, not *upstream just answered*. A second provider needs a second
credential and an owner cost decision. Physical-robot claims remain blocked until a real Moxie is available.

---
📖 [Implementation status](implementation-plan.md) · [Historical orchestration log](orchestration-plan.md) · [Release process](../../RELEASING.md)
