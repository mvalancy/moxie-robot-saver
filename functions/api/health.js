/* functions/api/health.js — GET /api/health, the mode and capacity probe.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2, §4.2, §6.3 (the state machine
 * that consumes this), §7 (capacity). `sim/web/mode.js` polls it and `env.js` paints the
 * badge, pill and `needs-backend` marks from the answer.
 *
 *  1. IT MAKES NO GATEWAY CALL. EVER. The answer comes from configuration and from counters
 *     this isolate already holds. `onRequestGet` is deliberately not `async`: a function
 *     that never awaits cannot be waiting on an upstream call, and a 30 s poll must never
 *     spend the budget it reports on.
 *  2. IT IS ALWAYS 200, even for `gateway_not_configured` or `budget_exhausted`, so a
 *     non-200 unambiguously means the route is ABSENT, which `mode.js` maps to `offline`.
 *     For the same reason there is no origin pin here: a 403 would read as "no Functions".
 *  3. NOTHING SECRET LEAVES. `respond()` builds from a fixed key allowlist; `voice`/`ears`
 *     say only WHETHER a model is configured, never which.
 *
 * `budget` and `load` are THIS ISOLATE'S VIEW: `budgetState` and `loadOf` read one isolate's
 * memory and never the colo-shared Cache API tier, so `live` means "no budget I can see is
 * spent", not a deployment-wide statement.
 */
import { readConfig, modeOf, publicLimits, publicTurnstile } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { budgetState, loadOf } from "./_lib/limits.js";

/** Only GET is exported, so Pages answers 405 for every other method by itself. */
export function onRequestGet(context) {
  const cfg = readConfig(context && context.env);
  const budget = budgetState(cfg); // a peek: charges nothing
  const { mode, reason } = modeOf(cfg, budget);

  return respond(
    {
      ok: true, // "the probe answered"; liveness is `mode`, and why not is `reason`
      degraded: mode !== "live",
      reason,
      // Seconds to the budget window reset; `mode.js` schedules its next poll from it.
      retry_after_s: reason === "budget_exhausted" ? budget.retryAfterS : 0,
      message: "", // visitor-facing copy lives in `sim/web/mode.js`
      mode,
      // The BUSY pill's signal: the chat ceiling, which `transcribe` shares.
      load: loadOf(cfg, "chat"),
      limits: publicLimits(cfg),
      // The Turnstile sitekey is published HERE, on the surface the page already polls, and
      // never in shipped HTML — a committed sitekey would bind every fork and preview to a
      // domain list they are not on. `""` whenever the control is not enforced. On the
      // probe rather than the chat reply so the widget is solved before the first Send.
      turnstile: publicTurnstile(cfg),
      messages: [],
      speech: [],
      context: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { status: 200 },
  );
}
