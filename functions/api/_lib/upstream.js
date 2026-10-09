/* functions/api/_lib/upstream.js — what the three spending routes share around their one
 * gateway `fetch()`, and the refusal envelope they all answer.
 *
 * The `fetch()` itself stays in each route file, with `limits.js::noteUpstreamCall()`
 * immediately before it, so "every refusal path makes zero upstream calls" remains a
 * recorded fact per route. Nothing from an upstream status line, body or header is ever
 * forwarded: those routinely echo model names, org ids and key prefixes (§4.2).
 *
 * WHY `redirect: "manual"` ON EVERY UPSTREAM CALL. The request carries the deployment's only
 * credential (plus the `CF-Access-*` pair when configured). Following a 3xx re-issues it at
 * whatever host `Location` names; a same-origin redirect keeps `Authorization` and a 307/308
 * replays the body. And a gateway 3xx is a DOOR problem — an Access login flow, a moved
 * endpoint, an `http://` base bounced to `https://` — which is what
 * `gateway_unreachable_or_gated` tells an operator, where `upstream_down` would send them
 * to restart a healthy model server.
 */
import { publicLimits } from "./env.js";
import { logRefusal, respond } from "./envelope.js";
import { budgetState, coloOf, hostRefused, loadOf } from "./limits.js";

/**
 * A bounded integer from an upstream 429's `Retry-After`, or `dflt` when it names none
 * (10 unless the route knows better). Never the raw string.
 *
 * `/api/transcribe` passes 60: the gateway's speech-to-text group answers 429 with NO
 * `Retry-After` during a measured 60 s cooldown, so a 10 s hint only sent the page back
 * into the same 429 five times. Chat and speech keep 10.
 */
export function retryAfterOf(res, dflt) {
  const n = Number(res.headers.get("Retry-After"));
  if (Number.isFinite(n) && n > 0) return Math.min(300, Math.ceil(n));
  return Number.isFinite(dflt) && dflt > 0 ? Math.min(300, Math.ceil(dflt)) : 10;
}

/** A thrown `fetch`: our own AbortSignal timeout, or an unreachable gateway. The error's
 *  message is never inspected — it can carry the URL. */
export function fetchFailure(err) {
  const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
  return timedOut ? { ok: false, reason: "timeout" } : { ok: false, reason: "upstream_down" };
}

/** The two statuses every route answers before looking at a body: the gateway's own limiter,
 *  and an unfollowed redirect (checked before `res.ok`, which is false for a 3xx too).
 *  `null` means neither. `retryDefaultS` is `retryAfterOf`'s default for this route. */
export function limitedOrRedirected(res, retryDefaultS) {
  if (res.status === 429) return { ok: false, reason: "rate_limited", retryAfterS: retryAfterOf(res, retryDefaultS) };
  if (res.status >= 300 && res.status < 400) return { ok: false, reason: "gateway_unreachable_or_gated" };
  return null;
}

/**
 * A refusal, in §4/§7's envelope with §4.5's status and `Retry-After`, so the page DEGRADES
 * instead of erroring — `sim/web/mode.js::note` knows every reason in the closed set.
 * `message` stays empty: the visitor-facing copy lives in `mode.js`, next to the badge,
 * so it is honest in `offline` too and no upstream text can become visitor-facing text.
 *
 * Every refusal of the three spending routes passes here exactly once, so this is where the
 * one log line per refusal is written (`envelope.js::logRefusal`: route, reason, status,
 * colo — nothing the visitor sent).
 *
 * @param {string} route  `chat` | `speech` | `transcribe` — whose load to report
 * @param {object} [extra] `{retryAfterS, load, rateLimit}`
 * @param {object} [fields] extra envelope fields (the chat route adds `turnstile`)
 */
export function refusal(cfg, route, reason, extra, fields) {
  const x = extra || {};
  // A host `DEMO_SERVE_HOSTS` does not list is a deployment with no gateway: no voice, no ears.
  const unserved = hostRefused(cfg);
  const res = respond(
    {
      ok: false,
      degraded: true,
      reason,
      retry_after_s: x.retryAfterS || (reason === "budget_exhausted" ? budgetState(cfg).retryAfterS : 0),
      mode: "degraded",
      load: x.load || loadOf(cfg, route),
      limits: publicLimits(cfg),
      ...(fields || {}),
      messages: [],
      speech: [],
      context: "",
      voice: cfg.voice && !unserved,
      ears: cfg.ears && !unserved,
    },
    { rateLimit: x.rateLimit || null },
  );
  logRefusal(route, reason, res.status, coloOf(cfg));
  return res;
}
