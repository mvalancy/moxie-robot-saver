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
 *  1. IT MAKES NO GATEWAY CALL. EVER. Not a models list, not a ping. The mode is derived
 *     from configuration alone (functions/api/_lib/env.js `modeOf`), so a probe is free,
 *     spends nothing, and cannot itself be the thing that exhausts a budget. A page that
 *     polls every 30 s must never cost a request upstream.
 *
 *  2. IT IS ALWAYS 200 — even when the answer is `gateway_not_configured`. That is what
 *     makes a NON-200 unambiguous: it means the ROUTE IS ABSENT (a fork with no
 *     Functions, a plain CDN, file://), which `mode.js` maps to `offline` — the state
 *     whose behaviour and copy are byte-identical to today's static site. The mode
 *     machine cannot distinguish "absent" from "present and unhappy" if this route ever
 *     answers 503 (§4.5's 503 rows are the *spending* routes, which do not exist in
 *     P0-a).
 *
 *  3. NOTHING SECRET LEAVES. The body is built by `respond()` from a fixed key allowlist
 *     (functions/api/_lib/envelope.js `PUBLIC_KEYS`), so the gateway base URL, the
 *     gateway key in any form, and every model id are structurally absent — not filtered
 *     out afterwards, but never copied in (C1, §4.2). `voice`/`ears` say only WHETHER a
 *     TTS/STT model is configured, never which one.
 *
 * Deliberately NOT here: the origin pin of §4.3. This route spends nothing and answers
 * nothing private, and a 403 would break rule 2 above — the page would read a pinned-out
 * probe as "no Functions". The pin belongs on the routes that can spend money (P0-b).
 *
 * FAIL-SAFE DEFAULT (C5): with no variables set at all this answers
 * `gateway_not_configured`, and the page is exactly today's static demo. A branch preview
 * with no secrets is therefore automatically safe.
 */
import { readConfig, modeOf, publicLimits } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";

/** Only GET is exported, so Pages answers 405 for every other method by itself. */
export function onRequestGet(context) {
  const cfg = readConfig(context && context.env);
  const { mode, reason } = modeOf(cfg, budgetState());

  return respond(
    {
      // `ok` here means "the probe answered", which it always does. Whether the demo is
      // live is `mode`, and why it is not is `reason` — that is what mode.js reads.
      ok: true,
      degraded: mode !== "live",
      reason,
      retry_after_s: 0,
      // The visitor-facing copy of §7 lives in sim/web/mode.js, next to the badge it
      // paints, and is therefore honest in `offline` too — where there is no server to
      // send a string. This field stays empty on the health route on purpose; P0-b's
      // routes use it for the inline "why your turn was refused" line (§4.5's 400s).
      message: "",
      mode,
      load: loadState(cfg),
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

/**
 * Budget state. P0-a ships no counter — the best-effort per-IP/global counters and the
 * unit budget are functions/api/_lib/limits.js in P0-b (§4.6) — and this route refuses to
 * invent one: `null` means "no counter consulted", so `modeOf` reports no budget reason
 * rather than a guess. Honest, and the wiring point is one line when limits.js lands.
 */
function budgetState() {
  return null;
}

/**
 * §7's capacity signal. `inflight` is 0 because in P0-a it *is* 0: no route that spends
 * gateway time is deployed yet, so nothing can be in flight. `capacity` is the configured
 * ceiling, so the page can already show the honest "0 of 4" on hover. P0-b replaces this
 * with the real counter, and §4.6 says out loud that the counter is best-effort (an
 * in-isolate map plus the per-colo Cache API), NOT a hard global ceiling.
 */
function loadState(cfg) {
  return { inflight: 0, capacity: cfg.maxConcurrentChat };
}
