/* functions/api/_lib/turnstile.js — the bot control in front of the spending routes.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1, §4.2, §4.5, §4.6.
 *
 * Every other control in this tree bounds the COST of a request; none can tell a child
 * from a script (`curl` forges `Origin` trivially). This removes the cheapest attack — a
 * loop with no browser — and sits between the free local refusals and the one gateway
 * call. A real browser still gets through and is then bounded by everything else.
 *
 * It guards BOTH visitor-driven spending routes, each with its own action
 * (`TURNSTILE_ACTIONS`): `/api/chat` (brain) and `/api/transcribe` (ears, the pricier
 * one). `/api/speech` needs no widget — it cannot be driven without a ticket `/api/chat`
 * minted, so gating the turn gates the voice. A refusal here must also REFUND the units
 * `admit()` charged (`slot.refundBudget()`), or a tokenless flood drains the shared budget
 * for free; `_lib/limits.js::grantedSlot` has the argument.
 *
 * THREE MANDATORY CHECKS:
 *   1. `success === true`.
 *   2. `action` equals THIS route's action, exactly — otherwise any widget flow on our
 *      hostnames (or a cheap typed-turn token) mints tokens for the expensive route.
 *   3. `hostname` is allowed. The default allowance is the request's OWN hostname
 *      (`hostAllowed`), so production can never be handed `localhost` by omission.
 * Tokens expire after 300 s and are single-use (enforced by Cloudflare), which is why the
 * client mints a fresh token per send (`sim/web/turnstile.js`).
 *
 * FAIL CLOSED ON A VERDICT, FAIL OPEN ON A TRANSPORT FAILURE:
 *   * `success: false` → refuse (`turnstile_failed`).
 *   * error codes naming OUR fault (`OUR_FAULT_CODES`) → refuse (`turnstile_misconfigured`)
 *     AT ANY STATUS: Cloudflare answers a wrong/missing secret with HTTP 400, so a
 *     fail-open keyed on the status alone silently disabled the control.
 *   * unreachable, timed out, non-JSON, `internal-error`, any other non-2xx → allow. The
 *     spend is already capped above this check, and a third-party outage must not take
 *     the public demo dark. The deadline is short so a hung endpoint cannot hold a slot.
 * Both halves are tested (`sim/test_turnstile.mjs`) and in
 * `sim/tools/turnstile_mutation_check.py`.
 *
 * TWO REASONS, OPPOSITE FIXES: `turnstile_failed` is the visitor's token;
 * `turnstile_misconfigured` is our config — and is how an operator validates the
 * production secret without anyone printing it (deploy, type one sentence, read the reason).
 *
 * NOTHING FROM CLOUDFLARE'S REPLY IS FORWARDED — `error-codes` are read into a local
 * boolean and dropped; the tests sweep every response for the secret and every code string.
 */

/** Cloudflare's verification endpoint. The one host this file talks to. */
export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/**
 * The widget `action` required back in the verdict, ONE PER SPENDING ROUTE, keyed by the
 * route name. One deployment-wide action would pass check 2 in appearance only: a typed
 * turn's token would buy 15 s of STT. The page renders one widget per action.
 * `sim/test_turnstile.mjs` requires the client's copy of this table to be equal.
 */
export const TURNSTILE_ACTIONS = Object.freeze({ chat: "chat", transcribe: "transcribe" });

/**
 * The action for one route, or `""` for a route this table does not know. There is no
 * default: falling back to `chat` is exactly the cross-route replay this table prevents,
 * so `verify` refuses an unknown route instead.
 */
export function actionFor(route) {
  const key = String(route || "");
  return Object.prototype.hasOwnProperty.call(TURNSTILE_ACTIONS, key) ? TURNSTILE_ACTIONS[key] : "";
}

/** The JSON body field the token arrives in — Cloudflare's own form-field name, so it
 *  matches every Cloudflare example. */
export const TOKEN_FIELD = "cf-turnstile-response";

/**
 * The request header the token arrives on when the body is raw audio (`/api/transcribe`).
 * Not a query parameter (tokens in URLs leak into logs/referrers) and not multipart (a
 * parser in front of a hostile upload). `X-` rather than `CF-` because `CF-*` is the
 * edge's own namespace and may be rewritten by the platform.
 */
export const TOKEN_HEADER = "X-Turnstile-Response";

/** The token off a request's headers, or `""`. */
export function tokenFromHeader(request) {
  try {
    return String((request && request.headers && request.headers.get(TOKEN_HEADER)) || "").trim();
  } catch {
    return "";
  }
}

/** `error-codes` meaning WE are misconfigured. `bad-request` is here because it means
 *  Cloudflare could not parse the request we built. Anything else is a bad token. */
const OUR_FAULT_CODES = Object.freeze(["missing-input-secret", "invalid-input-secret", "bad-request"]);

/** `error-codes` meaning Cloudflare had a problem ("retry the request"): a transport
 *  failure, so FAIL OPEN — not a verdict of "no". */
const THEIR_FAULT_CODES = Object.freeze(["internal-error"]);

/** The outcome names `verify()` reports, recorded so tests assert WHICH branch ran. */
export const OUTCOMES = Object.freeze([
  "skipped",        // enforcement is off for this deployment (no secret configured)
  "no_token",       // enforcement is on and the request carried no token: refused for FREE
  "verified",       // all three checks passed
  "failed",         // success:false, or action/hostname mismatch
  "misconfigured",  // our secret or our request is wrong
  "unreachable",    // transport failure: allowed through (see the header)
]);

/* Recorded facts, for the tests. `calls` is what proves a refusal path made ZERO
 * siteverify calls — the Turnstile analogue of `limits.js::noteUpstreamCall()`. */
const stats = { calls: 0, outcomes: {} };

function record(outcome) {
  stats.outcomes[outcome] = (stats.outcomes[outcome] || 0) + 1;
  return outcome;
}

/** Every recorded Turnstile fact for this isolate. Test-only. */
export function __stats() {
  return { calls: stats.calls, outcomes: Object.assign({}, stats.outcomes) };
}

/** Reset the recorded facts. Test-only; nothing in a route calls it. */
export function __reset() {
  stats.calls = 0;
  stats.outcomes = {};
}

/**
 * Is the hostname that solved the challenge one this deployment accepts?
 *
 * The default is the REQUEST'S OWN HOSTNAME: a fork on any domain needs no configuration,
 * production can never get a `localhost` allowance by omission, and `wrangler pages dev`
 * on localhost still works. An explicit `DEMO_TURNSTILE_HOSTS` REPLACES the default.
 * Comparison is exact and lower-cased — no suffix matching (Turnstile's own subdomain
 * authorization is the coarse filter; `.endsWith` would also accept `evil-example.com`).
 *
 * @param {object} cfg
 * @param {Request} request
 * @param {string} hostname the `hostname` field of the siteverify reply
 */
export function hostAllowed(cfg, request, hostname) {
  const got = String(hostname || "").trim().toLowerCase();
  if (!got) return false;
  const configured = (cfg && cfg.turnstileHosts) || [];
  if (configured.length) return configured.includes(got);
  let self = "";
  try {
    self = new URL(request.url).hostname.toLowerCase();
  } catch {
    return false;
  }
  return !!self && got === self;
}

/** The reply as a plain object, or `null`. Never throws and never logs — a parse error
 *  string can carry the URL. */
async function readJsonBody(res) {
  try {
    const body = await res.json();
    return body && typeof body === "object" && !Array.isArray(body) ? body : null;
  } catch {
    return null;
  }
}

/** `error-codes` as strings — the ONLY field read off a failure, compared and dropped. */
function codesOf(body) {
  return Array.isArray(body && body["error-codes"]) ? body["error-codes"].map((c) => String(c)) : [];
}

/**
 * Verify one Turnstile token.
 *
 * @param {object} cfg      from `./env.js::readConfig`
 * @param {Request} request the request being answered (for `remoteip` and the default
 *                          hostname allowance)
 * @param {unknown} token   the body's `cf-turnstile-response` field, or the
 *                          `X-Turnstile-Response` header for a route with no JSON body
 * @param {string} route    `"chat"` or `"transcribe"` — which action the token must carry.
 *                          An unknown name refuses (see `actionFor`).
 * @returns {Promise<{ok: boolean, reason: string|null, outcome: string}>}
 *   `ok: true` covers three worlds — enforcement off, verified, or failing open on a
 *   transport failure; `outcome` says which.
 */
export async function verify(cfg, request, token, route) {
  // Config-gated: no secret+sitekey pair, no enforcement. Keeps previews (whose hostname
  // is not on the widget's list), keyless forks and hermetic tests working.
  if (!cfg || !cfg.turnstile) return { ok: true, reason: null, outcome: record("skipped") };

  // An unknown route name on an ENFORCING deployment fails closed as OUR fault — guessing
  // `chat` would be the cross-route replay. Read after the config gate so an unconfigured
  // deployment never refuses anyone.
  const wantAction = actionFor(route);
  if (!wantAction) return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };

  // A missing token is refused without asking Cloudflare: a free refusal, and a tokenless
  // flood cannot turn this deployment into an amplifier pointed at siteverify.
  const response = typeof token === "string" ? token.trim() : "";
  if (!response) return { ok: false, reason: "turnstile_failed", outcome: record("no_token") };

  const form = new URLSearchParams();
  form.set("secret", cfg.turnstileSecret);
  form.set("response", response);
  // `remoteip` is read from the header directly, not from `clientip.js::clientIp()`, which
  // returns a rate-limit KEY (an IPv6 /64 prefix), not an address.
  const ip = request && request.headers ? request.headers.get("CF-Connecting-IP") : null;
  if (ip) form.set("remoteip", ip);

  let res;
  try {
    stats.calls += 1;
    res = await fetch(SITEVERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: form.toString(),
      // Short, because this runs with a concurrency slot held; a slow answer is no answer.
      signal: AbortSignal.timeout(cfg.turnstileTimeoutMs),
      // The body carries a SECRET, so a 3xx must not be re-issued at another host.
      redirect: "manual",
    });
  } catch {
    // A timeout or an unreachable endpoint. The error's message is not inspected at all —
    // an error string can carry the URL, and there is nothing here worth the risk.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  if (!res.ok) {
    // A non-2xx is a transport failure (fail open) EXCEPT when its codes name our fault:
    // `invalid-input-secret` / `missing-input-secret` arrive as HTTP 400, and failing open
    // on those would silently switch the control off for a secret wrong by one character.
    // Only `error-codes` is read, into a boolean; the 400 bodies carry nothing secret.
    const failCodes = codesOf(await readJsonBody(res));
    if (failCodes.some((c) => OUR_FAULT_CODES.includes(c))) {
      return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };
    }
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  const body = await readJsonBody(res);
  if (!body) {
    // A 200 that is not a JSON object (interception page, truncated body): transport.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  const codes = codesOf(body);

  // Read BEFORE the verdict: `internal-error` arrives with `success: false`, and must not
  // be mistaken for a visitor's failed challenge.
  if (codes.some((c) => THEIR_FAULT_CODES.includes(c))) {
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  // ---- CHECK 1: did it pass?
  if (body.success !== true) {
    const ours = codes.some((c) => OUR_FAULT_CODES.includes(c));
    return ours
      ? { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") }
      : { ok: false, reason: "turnstile_failed", outcome: record("failed") };
  }

  // ---- CHECK 2: is it THIS route's action? A mismatch is the visitor's token being wrong
  // for this route (`turnstile_failed`): the actions are set in code on both sides, so
  // they cannot drift through configuration. EXACT comparison — no prefix, trim or case
  // fold (`chat` and `chat-newsletter` are different widgets).
  if (String(body.action || "") !== wantAction) {
    return { ok: false, reason: "turnstile_failed", outcome: record("failed") };
  }

  // ---- CHECK 3: a hostname this deployment accepts? The allowance comes from our
  // configuration, so a mismatch is OUR fault and fails every visitor identically.
  if (!hostAllowed(cfg, request, body.hostname)) {
    return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };
  }

  return { ok: true, reason: null, outcome: record("verified") };
}
