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
 * IT IS IN FRONT OF **BOTH** VISITOR-DRIVEN SPENDING ROUTES, AND THAT TOOK A SECOND
 * ACTION. `/api/chat` is the brain and `/api/transcribe` is the ears; `/api/speech` needs
 * no widget of its own because it cannot be driven without a ticket `/api/chat` minted
 * (`_lib/hmac.js`), so gating the turn gates the voice structurally. An earlier draft of
 * this slice guarded the chat route alone and deferred the ears "to a later slice" — which
 * left the more expensive of the two open to precisely the loop-with-no-browser this file
 * claims to remove: 60 requests an hour from one address is 15 minutes of billable STT,
 * ~1,440 calls a day, for ever. `TURNSTILE_ACTIONS` is the fix and the reason there is a
 * table rather than a constant.
 *
 * AND A REFUSAL HERE GIVES THE BUDGET BACK. `admit()` charges the route's units before
 * the route body runs, so a refusal that kept them would let a tokenless flood empty the
 * SHARED hourly budget and take the demo scripted for everybody while spending nothing
 * itself — a free drain in place of a paid one, with the same availability outcome. The
 * routes call `slot.refundBudget()` on this path; `_lib/limits.js::grantedSlot` carries
 * the whole argument, including why the per-IP window is deliberately NOT refunded.
 *
 * ============================================================================
 * THE THREE CHECKS, AND WHY ALL THREE ARE MANDATORY.
 *
 * Cloudflare's own integration guide is explicit that `success: true` is not the whole
 * answer, and each of the other two closes a specific replay:
 *
 *   1. `success === true`  — the challenge was solved. Obviously required.
 *   2. `action` matches the action WE set on the widget FOR THIS ROUTE. Without it, a
 *      token minted by any other widget flow on any of our authorized hostnames is
 *      accepted here — so a cheap "subscribe" form somewhere on the domain becomes a
 *      token mint for the expensive route, and (because the two spending routes do not
 *      cost the same) a typed-sentence token becomes 15 seconds of billable STT. The
 *      comparison is EXACT: `TURNSTILE_ACTIONS` says why a prefix match is not a check.
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
 *   * `error-codes` naming OUR OWN fault — `invalid-input-secret`,
 *     `missing-input-secret`, `bad-request` — **REFUSE**, and refuse as
 *     `turnstile_misconfigured`, *whatever the status code was.* This one has to be
 *     called out separately because Cloudflare answers those three with **HTTP 400**
 *     (measured, see the `!res.ok` branch), so a fail-open keyed on the status alone
 *     switched the entire control off for a secret that was wrong by one character —
 *     silently, permanently, and while reporting a perfectly healthy turn.
 *   * siteverify unreachable, timed out, or answering something that is not JSON — a
 *     Cloudflare TRANSPORT failure, including its own documented `internal-error`
 *     ("retry the request") and any other non-2xx. **ALLOW.** Two reasons, and the second is the one that
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
 * The `action` we set on the widget and require back in the verdict — **ONE PER SPENDING
 * ROUTE, keyed by the route's own name.**
 *
 * It is deliberately the ROUTE NAME rather than something decorative: check 2 above is
 * only worth having if the value is specific to the thing being protected. A single
 * deployment-wide action would satisfy check 2 in appearance and fail it in substance,
 * because the two routes do not cost the same and are not driven the same way — a token
 * the page mints for a typed sentence would be spendable on the ears, and 15 seconds of
 * billable STT is a far better thing to steal than 160 tokens of chat.
 *
 * SO THERE ARE TWO, AND THE PAGE MINTS THEM FROM TWO SEPARATE WIDGETS
 * (`sim/web/turnstile.js` renders one widget per action, lazily). A `chat` token presented
 * to `/api/transcribe` is refused by check 2 exactly as a stranger's token would be.
 *
 * Turnstile constrains an action to `[A-Za-z0-9_-]{0,32}`; both of these are well inside
 * that. `sim/test_turnstile.mjs` §10 reads the client's copy out of the source and
 * requires the two tables to be equal, because a silent drift refuses every visitor with
 * `turnstile_failed` and looks like a Cloudflare fault.
 */
export const TURNSTILE_ACTIONS = Object.freeze({ chat: "chat", transcribe: "transcribe" });

/**
 * The action for one route, or `""` for a route this table does not know.
 *
 * A ROUTE NAME THIS TABLE DOES NOT KNOW IS A PROGRAMMING ERROR AND IS TREATED AS ONE (see
 * `verify`): it refuses. The alternative — defaulting to `chat` when the caller forgets
 * the argument — is precisely the bug this split exists to prevent, and it would ship
 * green, because a route that accepts chat tokens looks identical in every test that does
 * not think to mint the wrong one.
 */
export function actionFor(route) {
  const key = String(route || "");
  return Object.prototype.hasOwnProperty.call(TURNSTILE_ACTIONS, key) ? TURNSTILE_ACTIONS[key] : "";
}

/** The JSON body field the token arrives in.
 *
 *  IT IS CLOUDFLARE'S OWN FORM-FIELD NAME, on purpose, even though this route reads JSON
 *  and not a form: `cf-turnstile-response` is the name the widget writes into a form, the
 *  name every Cloudflare example verifies, and therefore the name a reader of this code
 *  will already recognise. Inventing `token` here would save six characters and cost the
 *  reader the connection to the documentation. */
export const TOKEN_FIELD = "cf-turnstile-response";

/**
 * ...and the REQUEST HEADER the token arrives on when there is no JSON body to put it in.
 *
 * `/api/transcribe`'s body is raw audio bytes — `readAudioBody` reads a `Uint8Array`, not
 * an object — so there is no field to add. The two rejected alternatives, and why:
 *
 *   · a QUERY PARAMETER (`?cf-turnstile-response=…`). Rejected: tokens in URLs end up in
 *     access logs, referrers and analytics, and a single-use credential is exactly the
 *     kind of thing that must not be written down by three systems that were not asked.
 *   · MULTIPART, so the token could keep Cloudflare's own form-field name. Rejected: it
 *     would put a parser in front of a hostile upload on the one route whose whole design
 *     note is *"the bytes are sniffed, not believed"*, to move a 40-byte string.
 *
 * `X-` AND NOT `CF-`, deliberately, even though `CF-Turnstile-Response` would read better
 * next to `TOKEN_FIELD`: the `CF-` prefix is Cloudflare's own edge namespace (`CF-Ray`,
 * `CF-Connecting-IP`, `CF-IPCountry` — this file reads one of them a few lines below), and
 * a client-supplied header inside a namespace the platform in front of us rewrites is a
 * header that can vanish or be replaced for reasons nothing in this repo controls.
 */
export const TOKEN_HEADER = "X-Turnstile-Response";

/** The token off a request's headers, or `""`. One line at the call site, and the header
 *  name lives in exactly one place. */
export function tokenFromHeader(request) {
  try {
    return String((request && request.headers && request.headers.get(TOKEN_HEADER)) || "").trim();
  } catch {
    return "";
  }
}

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
 * Reading Cloudflare's reply — the only two things ever taken out of it
 * ---------------------------------------------------------------------------- */

/** The reply as a plain object, or `null` for anything that is not one.
 *
 *  NEVER THROWS AND NEVER LOGS. A body that will not parse is a transport failure at every
 *  call site, so a `try` here saves the same `try` twice and, more importantly, keeps the
 *  parse error itself out of scope — an error string from a fetch body can carry the URL. */
async function readJsonBody(res) {
  try {
    const body = await res.json();
    return body && typeof body === "object" && !Array.isArray(body) ? body : null;
  } catch {
    return null;
  }
}

/** `error-codes` as an array of strings, defensively. THE ONLY FIELD READ OFF A FAILURE.
 *  It is compared against two frozen lists and dropped; `sim/test_turnstile.mjs` sweeps
 *  every response on every path for each of these strings. */
function codesOf(body) {
  return Array.isArray(body && body["error-codes"]) ? body["error-codes"].map((c) => String(c)) : [];
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
 * @param {unknown} token   whatever was in the body's `cf-turnstile-response` field, or on
 *                          the `X-Turnstile-Response` header for a route with no JSON body
 * @param {string} route    `"chat"` or `"transcribe"` — WHICH ACTION this token must carry.
 *                          Required, and a name `TURNSTILE_ACTIONS` does not know REFUSES:
 *                          see `actionFor` for why there is no default.
 * @returns {Promise<{ok: boolean, reason: string|null, outcome: string}>}
 *   `ok: true` means "let this request continue" — which covers three different worlds:
 *   enforcement is off, the token verified, or the endpoint could not be reached and we
 *   are failing open. `outcome` is which one, and it is recorded for the tests.
 */
export async function verify(cfg, request, token, route) {
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
  // ---- WHICH ACTION MUST COME BACK, and a route we do not know REFUSES.
  //
  // Read AFTER the config gate above and not before it, deliberately: an unconfigured
  // deployment must never refuse anybody for any reason (D4/C5), so a fork or a preview
  // cannot be broken by this line. On an ENFORCING deployment it fails CLOSED, because
  // the alternative to refusing an unknown route name is guessing one — and the guess
  // that reads best (`chat`) is exactly the cross-route replay `TURNSTILE_ACTIONS`
  // exists to prevent. It is `turnstile_misconfigured` and not `turnstile_failed`: no
  // visitor's token can fix a route name, and the operator reading that reason is being
  // told the truth about whose fault it is.
  const wantAction = actionFor(route);
  if (!wantAction) return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };

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
    // ==========================================================================
    // A NON-2xx IS *MOSTLY* A TRANSPORT FAILURE — BUT NOT WHEN IT IS OUR SECRET.
    //
    // THE BUG THIS BRANCH USED TO HAVE, and it was the whole control:
    // **`invalid-input-secret` AND `missing-input-secret` COME BACK AS HTTP 400.**
    // Measured against the real endpoint on 2026-09-05, not recalled:
    //
    //     secret=<garbage>  -> 400 {"error-codes":["invalid-input-secret"],"success":false}
    //     (no secret field) -> 400 {"error-codes":["missing-input-secret"],"success":false}
    //     2x…AA (bad token) -> 200 {"error-codes":["invalid-input-response"],…}
    //     3x…AA (replayed)  -> 200 {"error-codes":["timeout-or-duplicate"],…}
    //
    // So an early `return ok:true` here — a bare fail-open on `!res.ok`, with the body
    // never read — meant that a `DEMO_TURNSTILE_SECRET` wrong by ONE CHARACTER switched
    // the bot control off completely and silently: every visitor's genuine token answered
    // 400, every request was allowed through, real money was spent on every one of them,
    // the page painted a healthy LIVE badge, and `turnstile_misconfigured` — the reason
    // whose ENTIRE PURPOSE is to diagnose exactly this fault without anyone printing the
    // secret (see the header) — was unreachable for it. The single most likely
    // misconfiguration of this slice was also the one it could not report.
    //
    // SO THE BODY IS READ, AND *ONLY* `error-codes` IS READ FROM IT. Two facts make that
    // safe rather than a §4.2 risk, and both were verified above: the 400 bodies carry
    // `error-codes`, `success` and an empty `messages` and NOTHING resembling the secret
    // we sent; and the codes go into a local boolean and are dropped, exactly as they are
    // on the 200 path a few lines below. Nothing from this body is ever forwarded.
    //
    // EVERYTHING ELSE STILL FAILS OPEN, which keeps D3's split and row D3c intact: a 5xx,
    // a 3xx we did not follow, a body that will not parse, or a 4xx whose codes name the
    // VISITOR's token rather than our configuration. A non-2xx is not a verdict, so a 500
    // that happens to parse as `{"success": false}` is still Cloudflare having a bad ten
    // minutes and still lets the turn through.
    // ==========================================================================
    const failCodes = codesOf(await readJsonBody(res));
    if (failCodes.some((c) => OUR_FAULT_CODES.includes(c))) {
      return { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") };
    }
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  const body = await readJsonBody(res);
  if (!body) {
    // A 200 that is not JSON, or is JSON but not an object — an interception page, a
    // proxy, a truncated body, a bare `null`, an array. Same class as the above, and the
    // same answer.
    return { ok: true, reason: null, outcome: record("unreachable") };
  }

  const codes = codesOf(body);

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
  //
  // THE COMPARISON IS EXACT, AND THAT IS THE WHOLE VALUE OF IT. Not `startsWith`, not a
  // trim, not a case fold: `chat` and `chat-newsletter` are different widgets, and a
  // prefix match would hand every one of them the expensive route. (Both loosenings
  // passed the suite before rows C2b/C2c existed — see the mutation table.)
  if (String(body.action || "") !== wantAction) {
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
