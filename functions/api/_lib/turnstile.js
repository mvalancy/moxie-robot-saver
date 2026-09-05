/* functions/api/_lib/turnstile.js — the bot control in front of the spending routes.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (every cap and the ordering rule
 * that says a refusal must be free), §4.2 (what the browser may know), §4.5 (the status
 * table), §4.6 ("Counters, honestly" — why a counter is not a bot control).
 *
 * WHAT THIS IS FOR, AND WHAT IT IS NOT FOR.
 *
 * Everything else in this tree bounds the cost of a request that has already been made:
 * the per-IP windows, the unit budget, the concurrency ceiling, `max_tokens`, the input
 * caps, the ticket. None of them can tell a child from a script — `_lib/limits.js`'s own
 * header says so, and `checkOrigin`'s says it in capitals: *"`curl` FORGES THESE HEADERS
 * TRIVIALLY … Bot detection (Turnstile) is P1."* This file is that P1.
 *
 * It is ALSO NOT the thing that bounds the bill. It sits between the free local refusals
 * and the one gateway call, and it removes the cheapest attack — a loop with no browser —
 * from the set of things that can drain the demo's budget at all. A determined attacker
 * with a real browser still gets through it, and is then bounded by everything above.
 *
 * ============================================================================
 * THE THREE CHECKS, AND WHY ALL THREE ARE MANDATORY.
 *
 * Cloudflare's own integration guide is explicit that `success: true` is not the whole
 * answer, and each of the other two closes a specific replay:
 *
 *   1. `success === true`  — the challenge was solved. Obviously required.
 *   2. `action` matches the action WE set on the widget. Without it, a token minted by
 *      any other widget flow on any of our authorized hostnames is accepted here — so a
 *      cheap "subscribe" form somewhere on the domain becomes a token mint for the
 *      expensive route.
 *   3. `hostname` is in a deployment allowlist. Turnstile authorizes a hostname AND ALL
 *      ITS SUBDOMAINS, so the widget's own domain list is a coarser filter than this
 *      route wants; and the allowlist is what makes "a token solved on a dev box" not a
 *      token this deployment accepts. THE ALLOWLIST MUST NEVER CONTAIN `localhost` OR
 *      `127.0.0.1` IN PRODUCTION — which is why the DEFAULT is not a literal list at all
 *      but *the hostname of the request being answered* (see `hostAllowed`). A production
 *      deployment therefore cannot be given a localhost allowance by forgetting to set a
 *      variable, and a fork on any domain needs no configuration (C3).
 *
 * A token also EXPIRES AFTER 300 SECONDS AND IS SINGLE-USE. Both are enforced by
 * Cloudflare, not here, and both surface as `error-codes` — `timeout-or-duplicate` for a
 * replay or a stale token, `invalid-input-response` for a malformed one. That is why the
 * client mints a FRESH token per send (`sim/web/turnstile.js`): one token per page load
 * would work exactly once and then quietly fail for the rest of a multi-turn chat.
 * ============================================================================
 *
 * ============================================================================
 * FAIL CLOSED ON A VERDICT, FAIL OPEN ON A TRANSPORT FAILURE. THE SPLIT IS DELIBERATE.
 *
 *   * `success: false` — Cloudflare looked and said no. **REFUSE.** This is the control;
 *     a control that lets the refused case through is not a control.
 *   * siteverify unreachable, timed out, or answering something that is not JSON — a
 *     Cloudflare TRANSPORT failure, including its own documented `internal-error`
 *     ("retry the request"). **ALLOW.** Two reasons, and the second is the one that
 *     decides it. First, the request is already bounded by everything above this line:
 *     the per-IP windows and the unit budget cap the spend whether this check ran or not,
 *     so failing open costs at most the budget that was already the ceiling. Second, the
 *     alternative is a public demo that goes silent for every visitor because a
 *     third-party endpoint had a bad ten minutes — and the demo going dark is a certainty
 *     in that failure mode, while abuse during it is a possibility. The deadline is short
 *     (`DEMO_TURNSTILE_TIMEOUT_MS`, 2 s) so a hung endpoint cannot hold a concurrency
 *     slot open for the length of the route's own 20 s timeout.
 *
 * Both halves are tested (`sim/test_turnstile.mjs` §3's CHECK 1 and §5) and both are in the
 * mutation table (`sim/tools/turnstile_mutation_check.py`), because a fail-open that
 * quietly became a fail-closed would take the demo down and a fail-closed that quietly
 * became a fail-open would remove the control — and neither shows up in a green suite.
 * ============================================================================
 *
 * ============================================================================
 * WHY THERE ARE TWO REFUSAL REASONS AND NOT ONE (§4.5, and D8 of the brief).
 *
 * `turnstile_failed`         — YOUR token is bad. Missing, malformed, expired, replayed,
 *                              minted for another action, or simply not solved.
 * `turnstile_misconfigured`  — OUR configuration is bad. The secret is wrong or absent
 *                              from Cloudflare's point of view, our request was malformed,
 *                              or the solved hostname is not one this deployment allows.
 *
 * They exist as two reasons because they have opposite fixes and because the second one
 * is **how the secret gets validated in production without anyone printing it.** Nobody
 * may read `DEMO_TURNSTILE_SECRET` — not from a log, not from a response body, not by
 * asking. What an operator CAN do is deploy, type one sentence, and read the reason: a
 * badge that flips to SCRIPTED with `turnstile_misconfigured` means the secret Cloudflare
 * received is not the widget's. That is a complete diagnosis from a value nobody saw.
 *
 * AND NOTHING FROM CLOUDFLARE'S REPLY IS EVER FORWARDED. Not the `error-codes` array,
 * not `hostname`, not `challenge_ts`, not the body of a failure. The codes are read into
 * a local boolean and dropped. `sim/test_demo_proxy.mjs` sweeps every response on every
 * path for the gateway key and base URL; `sim/test_turnstile.mjs` extends that sweep to
 * the Turnstile secret and to the raw code strings, because a "helpful" error field is
 * exactly how a secret-shaped value reaches a browser.
 * ============================================================================
 */

/** Cloudflare's verification endpoint. The one host this file talks to. */
export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

/**
 * The `action` we set on the widget and require back in the verdict.
 *
 * It is deliberately the ROUTE NAME rather than something decorative: check 2 above is
 * only worth having if the value is specific to the thing being protected, and `/api/chat`
 * is the only route that mints tokens today. If `/api/transcribe` ever grows its own
 * widget it gets its own action, and a chat token stops being spendable on the ears.
 *
 * Turnstile constrains an action to `[A-Za-z0-9_-]{0,32}`; `chat` is well inside that.
 */
export const TURNSTILE_ACTION = "chat";

/** The JSON body field the token arrives in.
 *
 *  IT IS CLOUDFLARE'S OWN FORM-FIELD NAME, on purpose, even though this route reads JSON
 *  and not a form: `cf-turnstile-response` is the name the widget writes into a form, the
 *  name every Cloudflare example verifies, and therefore the name a reader of this code
 *  will already recognise. Inventing `token` here would save six characters and cost the
 *  reader the connection to the documentation. */
export const TOKEN_FIELD = "cf-turnstile-response";

/**
 * Cloudflare `error-codes` that mean **we** are misconfigured, not that the visitor's
 * token is bad. Everything not named here is treated as a bad token.
 *
 * `bad-request` is in this set and that is a judgement call worth writing down: it means
 * Cloudflare could not parse the request *we* built, which is our bug and not the
 * visitor's — the same class of thing as a wrong secret, and fixed in the same place.
 */
const OUR_FAULT_CODES = Object.freeze(["missing-input-secret", "invalid-input-secret", "bad-request"]);

/**
 * Cloudflare `error-codes` that mean **Cloudflare** had a problem and the documented
 * remedy is "retry the request". Treated as a TRANSPORT failure and therefore FAIL OPEN,
 * exactly like an unreachable endpoint — not as a verdict of "no".
 */
const THEIR_FAULT_CODES = Object.freeze(["internal-error"]);

/** The outcome names `verify()` reports. Recorded by `__stats()` so a test can assert
 *  WHICH branch ran rather than inferring it from a status code. */
export const OUTCOMES = Object.freeze([
  "skipped",        // enforcement is off for this deployment (no secret configured)
  "no_token",       // enforcement is on and the request carried no token: refused for FREE
  "verified",       // all three checks passed
  "failed",         // success:false, or action/hostname mismatch
  "misconfigured",  // our secret or our request is wrong
  "unreachable",    // transport failure: allowed through (see the header)
]);

/* ---------------------------------------------------------------------------- *
 * Recorded facts, for the tests
 * ---------------------------------------------------------------------------- *
 * The same idiom as `_lib/limits.js::__state()`, and for the same reason (playbook rule
 * 11): a test asserts on what HAPPENED, not on what a stub was probably asked. In
 * particular `calls` is what proves a refusal path made ZERO siteverify calls — the
 * Turnstile analogue of `noteUpstreamCall()`, and the reason a locally-blocked utterance
 * can be shown not to cost one.
 */
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

/* ---------------------------------------------------------------------------- *
 * The hostname allowlist
 * ---------------------------------------------------------------------------- */

/**
 * Is the hostname Cloudflare says solved the challenge one this deployment accepts?
 *
 * THE DEFAULT IS THE REQUEST'S OWN HOSTNAME, and that is the whole reason this function
 * exists rather than a `cfg.turnstileHosts.includes(...)` at the call site. Three
 * properties fall out of it and all three are load-bearing:
 *
 *   * a fork on any domain works with **zero** configuration (C3 — nothing in this repo
 *     may know a deployment hostname);
 *   * a PRODUCTION deployment can never be handed a `localhost` allowance by forgetting
 *     to set a variable, because the default is not a literal at all;
 *   * `wrangler pages dev` on `http://localhost:8788` still works, because there the
 *     request's own hostname IS `localhost` — the allowance follows the deployment
 *     instead of being written into it.
 *
 * An explicit `DEMO_TURNSTILE_HOSTS` REPLACES that default rather than extending it, so a
 * deployment that names hosts gets exactly the hosts it named. Comparison is on the exact
 * hostname, lower-cased: **no suffix matching.** Turnstile itself authorizes a hostname
 * and all of its subdomains, which is the coarse filter; this is the narrow one, and a
 * `.endsWith()` here would throw that difference away (and would accept
 * `evil-example.com` for a suffix of `example.com` into the bargain).
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

/* ---------------------------------------------------------------------------- *
 * The verification itself
 * ---------------------------------------------------------------------------- */

/**
 * Verify one Turnstile token.
 *
 * @param {object} cfg      from `./env.js::readConfig`
 * @param {Request} request the request being answered (for `remoteip` and the default
 *                          hostname allowance)
 * @param {unknown} token   whatever was in the body's `cf-turnstile-response` field
 * @returns {Promise<{ok: boolean, reason: string|null, outcome: string}>}
 *   `ok: true` means "let this request continue" — which covers three different worlds:
 *   enforcement is off, the token verified, or the endpoint could not be reached and we
 *   are failing open. `outcome` is which one, and it is recorded for the tests.
 */
export async function verify(cfg, request, token) {
  // ---- ENFORCEMENT IS CONFIG-GATED (D4, and C5's fail-safe default restated).
  //
  // No secret, no enforcement. This is the same shape as the gateway's own unconfigured
  // path and it is what keeps three things working with no code change: branch previews
  // (whose Turnstile variables are deliberately empty, because the platform-assigned
  // preview hostname is NOT on this widget's domain list and a real challenge there could
  // never pass), a self-hosted fork that has no Cloudflare account at all, and every
  // hermetic test in this repo that does not opt in.
  //
  // `cfg.turnstile` is `true` only when BOTH the secret and the public sitekey are
  // present — see `./env.js`, which puts half a pair in `missing` for the same reason it
  // does for a Cloudflare Access service token: a bot control with a secret and no
  // sitekey would refuse every visitor, because no browser could ever mint a token.
  if (!cfg || !cfg.turnstile) return { ok: true, reason: null, outcome: record("skipped") };

  // ---- A MISSING TOKEN IS REFUSED WITHOUT ASKING CLOUDFLARE.
  //
  // Sending an empty `response` would earn a `missing-input-response` and the same
  // refusal one network round trip later. There is nothing to verify, so this is a free
  // refusal — and free refusals are the ordering rule of this whole tree (§4.1). It also
  // means a flood of tokenless requests cannot turn this deployment into a traffic
  // amplifier pointed at siteverify.
  const response = typeof token === "string" ? token.trim() : "";
  if (!response) return { ok: false, reason: "turnstile_failed", outcome: record("no_token") };

  const form = new URLSearchParams();
  form.set("secret", cfg.turnstileSecret);
  form.set("response", response);
  // `remoteip` is optional and we send it when we have it: it lets Cloudflare correlate
  // the solve with the caller. NOTE it is read from the header DIRECTLY rather than from
  // `limits.js::clientIp()`, and that is not laziness — `clientIp` returns a rate-limit
  // KEY, which collapses an IPv6 address to its /64 prefix. A prefix is not an address
  // and has no business being presented to an API as one.
  const ip = request && request.headers ? request.headers.get("CF-Connecting-IP") : null;
  if (ip) form.set("remoteip", ip);

  let res;
  try {
    stats.calls += 1;
    res = await fetch(SITEVERIFY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: form.toString(),
      // A SHORT deadline, and the reason is the concurrency slot rather than the
      // visitor's patience: this call happens with a slot held, so a hung endpoint would
      // otherwise keep that slot (and everyone in the FIFO behind it) waiting for the
      // route's own 20 s timeout. Two seconds against a Cloudflare edge endpoint is
      // generous; a slow answer is treated as no answer, and no answer fails OPEN.
      signal: AbortSignal.timeout(cfg.turnstileTimeoutMs),
      // Same reasoning as `chat.js::callGateway`: this request carries a SECRET in its
      // body, so a 3xx must not be re-issued at whatever host `Location` names.
      redirect: "manual",
    });
  } catch {
    // A timeout or an unreachable endpoint. The error's message is not inspected at all —
    // an error string can carry the URL, and there is nothing here worth the risk.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  if (!res.ok) {
    // Any non-2xx, including a 3xx we deliberately did not follow. Cloudflare answering
    // anything but a 200 to a well-formed siteverify is a Cloudflare problem, which is
    // the fail-open half of the split. NOTE the body is not read: a 4xx body here could
    // echo the `secret` we just sent it.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  let body;
  try {
    body = await res.json();
  } catch {
    // A 200 that is not JSON. Same class as the above — an interception page, a proxy, a
    // truncated body — and the same answer.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  const codes = Array.isArray(body["error-codes"]) ? body["error-codes"].map((c) => String(c)) : [];

  // Cloudflare's own "retry the request" class, read BEFORE the verdict: `internal-error`
  // arrives with `success: false`, so a naive reading would treat Cloudflare's outage as
  // a visitor's failed challenge and take the demo down with it.
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

  // ---- CHECK 2: is it OUR widget's action?
  //
  // A mismatch is `turnstile_failed` and not `turnstile_misconfigured`, deliberately: the
  // action is set in code on both sides (`TURNSTILE_ACTION`), so the two cannot drift
  // through configuration. What a mismatch actually means is a token that came from
  // somewhere else — which is the visitor's token being wrong for this route.
  if (String(body.action || "") !== TURNSTILE_ACTION) {
    return { ok: false, reason: "turnstile_failed", outcome: record("failed") };
  }

  // ---- CHECK 3: was it solved on a hostname this deployment accepts?
  //
  // A mismatch is `turnstile_misconfigured`, and that is the opposite call from check 2 —
  // for the same reason. The hostname allowance comes from CONFIGURATION
  // (`DEMO_TURNSTILE_HOSTS`, or the request's own hostname), so a mismatch is
  // overwhelmingly our list being wrong, and it fails for EVERY visitor identically until
  // someone fixes it. Calling that "your token is bad" would send an operator hunting a
  // visitor's browser for a variable they typed.
  if (!hostAllowed(cfg, request, body.hostname)) {
    return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };
  }

  return { ok: true, reason: null, outcome: record("verified") };
}
