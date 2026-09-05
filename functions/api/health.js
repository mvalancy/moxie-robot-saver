/* functions/api/health.js — GET /api/health, the mode and capacity probe.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (the route), §4.2 (what the
 * browser may know), §6.3 (the state machine that consumes this), §7 (capacity).
 *
 * What this route is for: today `sim/web/env.js` decides what the page says from the
 * HOSTNAME, so on any non-local host it claims "hosted demo — only pre-scripted lines
 * have audio" whether that is true or not. This route is how the page stops guessing and
 * asks. `sim/web/mode.js` polls it; `env.js` paints the badge, the pill and the
 * `needs-backend` marks from the answer.
 *
 * Three rules, and each one is load-bearing:
 *
 *  1. IT MAKES NO GATEWAY CALL. EVER. Not a models list, not a ping. The answer is
 *     derived from CONFIGURATION (`_lib/env.js::modeOf`) and from COUNTERS THIS ISOLATE
 *     ALREADY HOLDS IN MEMORY (`_lib/limits.js::budgetState`, `::loadOf`) — two pure,
 *     synchronous map reads that charge nothing. `onRequestGet` is deliberately NOT
 *     `async` and contains no `await`, which makes the promise structural rather than a
 *     habit: a function that never awaits cannot be waiting on an upstream call. A page
 *     that polls every 30 s must never cost a request upstream, and must never be the
 *     thing that exhausts the budget it is reporting on.
 *
 *  2. IT IS ALWAYS 200 — even when the answer is `gateway_not_configured` or
 *     `budget_exhausted`. That is what makes a NON-200 unambiguous: it means the ROUTE IS
 *     ABSENT (a fork with no Functions, a plain CDN, file://), which `mode.js` maps to
 *     `offline` — the state whose behaviour and copy are byte-identical to today's static
 *     site. The mode machine cannot distinguish "absent" from "present and unhappy" if
 *     this route ever answers 503 (§4.5's 503 rows are the *spending* routes).
 *
 *  3. NOTHING SECRET LEAVES. The body is built by `respond()` from a fixed key allowlist
 *     (functions/api/_lib/envelope.js `PUBLIC_KEYS`), so the gateway base URL, the
 *     gateway key in any form, and every model id are structurally absent — not filtered
 *     out afterwards, but never copied in (C1, §4.2). `voice`/`ears` say only WHETHER a
 *     TTS/STT model is configured, never which one.
 *
 * ============================================================================
 * WHOSE VIEW THIS IS, STATED PLAINLY BECAUSE RULE 1 IS ONLY HONEST WITH IT.
 *
 * `budget` and `load` here are **THIS ISOLATE'S VIEW, AND NOTHING WIDER.** The counters
 * live in one Cloudflare Worker isolate's memory (`_lib/limits.js`'s header says why at
 * length), so a visitor whose probe lands on isolate A learns nothing about isolate B:
 * `mode: "live"` means "no budget I can see is spent", not "the deployment is under
 * budget", and `load.inflight` counts the turns THIS isolate is running, not the
 * deployment's.
 *
 * **AND THE 2026-09-05 CACHE API TIER DOES NOT CHANGE THAT ONE WORD.** `_lib/limits.js`
 * now shares the per-IP MINUTE window across the isolates of a colo — and only that. The
 * unit budget and the in-flight count, which are the two things this route reports, are
 * still one isolate's `Map`. Nothing here got wider, and this comment is the place someone
 * would otherwise quietly assume it had (ledger row 25 is what that mistake costs).
 *
 * That is a weaker guarantee than the one §4.6 used to imply, and it is still enormously
 * better than the hard-coded `null`/`0` this route shipped before it: an over-budget
 * isolate now says so, its next visitor's page paints SCRIPTED rather than LIVE, and the
 * BUSY pill of §7 can actually fire. What it cannot do is see across isolates — the fix
 * for that is a KV or Durable Object single-writer counter, which is P1 and blocked on
 * §10 assumption 13. Until then: do not read a `live` from here as a deployment-wide
 * statement, and do not write a doc sentence that does.
 * ============================================================================
 *
 * Deliberately NOT here: the origin pin of §4.3. This route spends nothing and answers
 * nothing private, and a 403 would break rule 2 above — the page would read a pinned-out
 * probe as "no Functions". The pin belongs on the routes that can spend money.
 *
 * FAIL-SAFE DEFAULT (C5): with no variables set at all this answers
 * `gateway_not_configured`, and the page is exactly today's static demo. A branch preview
 * with no secrets is therefore automatically safe.
 */
import { readConfig, modeOf, publicLimits } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { budgetState, loadOf } from "./_lib/limits.js";

/** Only GET is exported, so Pages answers 405 for every other method by itself. */
export function onRequestGet(context) {
  const cfg = readConfig(context && context.env);
  // `budgetState(cfg)` PEEKS: it charges nothing, which is the whole reason it exists
  // separately from `chargeBudget`. Its second argument is the epoch-second clock used to
  // pick the window bucket, and it defaults to `Date.now()` — "now" is exactly what a
  // probe wants, so it is left off rather than passed a redundant literal.
  const budget = budgetState(cfg);
  const { mode, reason } = modeOf(cfg, budget);

  return respond(
    {
      // `ok` here means "the probe answered", which it always does. Whether the demo is
      // live is `mode`, and why it is not is `reason` — that is what mode.js reads.
      ok: true,
      degraded: mode !== "live",
      reason,
      // §4.5 gives `budget_exhausted` a `Retry-After` of "seconds to window reset", and
      // `budgetState` is the only thing that knows it. `mode.js::applyEnvelope` reads this
      // field and schedules its next poll from it, so a spent budget stops the page
      // re-asking every 30 s for an hour. Zero for every other reason, which `envelope.js`
      // then maps to "send no Retry-After header".
      retry_after_s: reason === "budget_exhausted" ? budget.retryAfterS : 0,
      // The visitor-facing copy of §7 lives in sim/web/mode.js, next to the badge it
      // paints, and is therefore honest in `offline` too — where there is no server to
      // send a string. This field stays empty on the health route on purpose; the
      // spending routes use it for the inline "why your turn was refused" line (§4.5).
      message: "",
      mode,
      // §7's capacity signal, read from the REAL in-flight counter. `chat` is the route
      // the page's BUSY pill is about; it is also the ceiling `transcribe` shares
      // (`limits.js::capacityOf` says why). This isolate's count — see the header.
      load: loadOf(cfg, "chat"),
      limits: publicLimits(cfg),
      // The two payload lists exist so the client has one shape (§3.2). A probe carries
      // no messages and mints no ticket.
      messages: [],
      speech: [],
      context: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { status: 200 },
  );
}
