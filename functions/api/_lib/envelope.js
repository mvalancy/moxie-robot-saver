/* functions/api/_lib/envelope.js — the one response shape, and the status +
 * `Retry-After` mapping that goes with it.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (the envelope), §4.2 (what the
 * browser is allowed to know), §4.5 (the status table), §7 (the capacity signal).
 *
 * Why one envelope: the client gets ONE branch. Success or failure, 200 or 503, the body
 * has the same keys, so `mode.js` and the transport never have to guess whether a reply
 * is an error shape or a data shape.
 *
 * The constraint that bites here, restated (§4.2, C1):
 *   THE BROWSER MAY NEVER SEE the gateway base URL, the gateway key in any form, a model
 *   id, an upstream status code or an upstream error body. Upstream errors can echo model
 *   names, org identifiers and key prefixes, so this module builds every response from a
 *   FIXED KEY ALLOWLIST (`PUBLIC_KEYS`) rather than by spreading a caller's object. An
 *   unknown key cannot ride along, because nothing copies unknown keys. `message` — the
 *   one free-text field — is additionally scrubbed of URLs and key-shaped tokens.
 *
 * Never a bare 500. Never a 200 with an empty string (§4.5): the dead-air failure mode
 * that exists today is exactly what this contract exists to prevent.
 */

/** The closed reason set (§3.2), plus ONE addition P0-b makes and the reason for it.
 *
 * `gateway_unreachable_or_gated` is not in §3.2's list. It exists because the gateway is
 * expected to sit behind a **Cloudflare Tunnel**, and a tunnel protected by **Cloudflare
 * Access** answers an unauthenticated server-side `fetch` with an **HTML login page and a
 * 200 status**. Folding that into `upstream_down` would be technically true and
 * operationally useless: the two have completely different fixes (restart the gateway vs.
 * configure `DEMO_GATEWAY_ACCESS_CLIENT_ID`/`_SECRET`, see `./env.js::ACCESS_VARS`), and
 * from a bare 502 they are indistinguishable. So it gets its own reason, with the same
 * 503 status and the same visitor-facing copy as `upstream_down` — the visitor's
 * experience is identical, and only the operator learns anything new.
 *
 * Adding a reason is a CONTRACT CHANGE, so it is made in exactly two places and nowhere
 * else: here, and `sim/web/mode.js`'s matching list — where an unknown reason is coerced
 * to `null` and would therefore be misread as a HEALTHY turn. That is why the client half
 * is not optional. */
export const REASONS = Object.freeze([
  "rate_limited",
  "at_capacity",
  "budget_exhausted",
  "upstream_down",
  "gateway_unreachable_or_gated",
  "gateway_not_configured",
  "timeout",
  "bad_request",
  "too_long",
  "too_short",
  "bad_ticket",
  "blocked",
  "forbidden_origin",
]);

/** Exactly the keys a response body may contain. The allowlist IS the security control. */
export const PUBLIC_KEYS = Object.freeze([
  "ok",
  "degraded",
  "reason",
  "retry_after_s",
  "message",
  "mode",
  "load",
  "limits",
  "messages",
  "speech",
  "context",
  "transcript",
  "voice",
  "ears",
]);

/** §4.5's table. A reason absent here is a programming error, not a 500 (see `respond`). */
export const STATUS_FOR = Object.freeze({
  rate_limited: 429,
  at_capacity: 503,
  budget_exhausted: 503,
  upstream_down: 503,
  gateway_unreachable_or_gated: 503,
  gateway_not_configured: 503,
  timeout: 504,
  bad_request: 400,
  too_long: 400,
  too_short: 400,
  bad_ticket: 400,
  // A blocked turn is not an error: it answers `ok: true, degraded: true` and spends
  // nothing (§4.1). The client answers from the scripted repertoire.
  blocked: 200,
  forbidden_origin: 403,
});

/** §4.5's `Retry-After` column. `null` = send no header. `rate_limited` and
 *  `budget_exhausted` are window-derived, so the caller supplies the number. */
export const RETRY_AFTER_FOR = Object.freeze({
  at_capacity: 15,
  upstream_down: 60,
  gateway_unreachable_or_gated: 60,
  timeout: 10,
  gateway_not_configured: null,
  rate_limited: null,
  budget_exhausted: null,
  bad_request: null,
  too_long: null,
  too_short: null,
  bad_ticket: null,
  blocked: null,
  forbidden_origin: null,
});

/* ============================================================================ *
 * THE HARDENING HEADER SET FOR /api/* — and, just as load-bearing, the headers
 * deliberately LEFT OFF it, each with the reason it was left off.
 * ============================================================================ *
 *
 * WHY THIS LIVES IN CODE AND NOT IN `sim/web/_headers`. Settled by a real preview deploy
 * (2026-09-03, §10 assumption 27, first proven the hard way in PR #72): `_headers` is NOT
 * applied to a Pages *Function* response. The same preview served `/sim.html` with the
 * `/*` block's `Referrer-Policy` and served `/api/health` with none at all. So the `/api/*`
 * block in that file is documentation of intent and nothing more; THIS OBJECT is the only
 * thing that actually ships on a route response.
 *
 * The measurement that motivated this pass, taken against the live deployment on
 * 2026-09-03 (the host is deployment CONFIG and is deliberately not named here, C3):
 *
 *   GET <deployment>/api/health
 *   present: content-type, cache-control: no-store, x-content-type-options: nosniff,
 *            referrer-policy: same-origin, x-moxie-mode
 *   absent:  strict-transport-security, content-security-policy, cross-origin-*
 *
 * The pages had just gained a real header set (HSTS + a CSP with `script-src`); the routes
 * that can SPEND MONEY had almost none of it.
 *
 * -------- WHAT IS HERE, AND WHY EACH ONE EARNS ITS BYTES -------------------------------
 *
 * `X-Content-Type-Options: nosniff` — pre-existing. With `Content-Type: application/json`
 *   it is what makes a cross-origin `<script src="…/api/health">` fail the strict MIME
 *   check instead of executing a JSON body as script.
 *
 * `Referrer-Policy: same-origin` — pre-existing, and the header the preview proved was
 *   missing. A route URL can carry a ticket; it must not travel off-origin in a Referer.
 *
 * `Strict-Transport-Security: max-age=31536000; includeSubDomains` — ADDED. The exact
 *   value `sim/web/_headers`' `/*` block sends, deliberately, so the ORIGIN speaks with one
 *   voice: a visitor who only ever touched `/api/health` (a bookmarked probe, a curl, a
 *   fetch from a pinned page) should be pinned to https just as firmly as one who loaded a
 *   page. `sim/test_api_headers.mjs` asserts the two strings are byte-identical rather than
 *   merely both present, because "both set HSTS, with different max-ages" is the drift that
 *   would otherwise go unnoticed. NOT a localhost trap: a browser ignores HSTS received
 *   over plain http, so `wrangler pages dev` on http://localhost cannot be locked to https
 *   by this line. No `preload` token — preload is an origin-wide, hard-to-reverse
 *   submission and is the site owner's decision, not a header this file may make for them.
 *
 * `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'`
 *   — ADDED, and it is NOT the page policy. Copying the page's CSP here would be
 *   cargo-culting: `script-src 'self' 'unsafe-inline'`, `connect-src 'self'`, `img-src` and
 *   friends govern what a DOCUMENT may load, and a JSON body loads nothing. The useful
 *   form for a JSON response is the lockdown above, and it is worth having for exactly one
 *   class of bug: a browser that ends up treating the response AS a document — a direct
 *   navigation to the route, an inherited-CSP context, an old sniffing quirk, a future
 *   content-type slip. In that case `default-src 'none'` means the document may fetch,
 *   frame, script or connect to nothing at all.
 *
 *   Why only three directives: every FETCHING directive falls back to `default-src`, so
 *   `script-src`, `object-src`, `connect-src`, `img-src`, `style-src`, `font-src`,
 *   `media-src`, `worker-src` and `frame-src` are all already `'none'` and naming them
 *   would be noise. The three that do NOT fall back are `frame-ancestors`, `base-uri` and
 *   `form-action`; the first two are named explicitly, and `form-action` is omitted because
 *   a JSON body contains no form to submit — see the rejection list for the same reasoning
 *   applied to a whole header.
 *
 *   The one observable cost, stated honestly: a browser's built-in JSON viewer is a
 *   document, so a direct navigation to `/api/health` may render as raw text rather than a
 *   pretty tree in some browsers. The body is unchanged and `curl` is unaffected.
 *
 * `Cross-Origin-Resource-Policy: same-origin` — ADDED. These routes are already
 *   origin-pinned (`_lib/limits.js::checkOrigin` refuses anything whose `Sec-Fetch-Site` is
 *   not `same-origin`) and NO `Access-Control-Allow-Origin` is ever sent (§4.3), so a
 *   cross-origin reader was already impossible. What CORP closes is the half neither of
 *   those covers: a `no-cors` load from another site — `fetch(url, {mode:"no-cors"})`,
 *   `<img>`, `<audio>`, `<link rel=preload>` — which succeeds opaquely today and hands the
 *   other site a timing and cacheability oracle on a route it must not touch. With
 *   `same-origin` the browser refuses the load outright.
 *
 *   VERIFIED IT CANNOT BREAK THE PAGE'S OWN FETCH, and not by reasoning alone: CORP is only
 *   consulted for a CROSS-origin response, and every fetch this site makes to these routes
 *   is same-origin by construction — the origin pin would already have refused anything
 *   else. `sim/test_api_headers.mjs` loads the real `index.html` in Chrome under the real
 *   `_headers` page CSP and requires an in-page `fetch("/api/health")` to succeed with the
 *   header present, then requires a cross-origin `no-cors` fetch of the same URL to FAIL.
 *   A header set with no proof of teeth and no proof of harmlessness is a guess.
 *
 * -------- WHAT IS DELIBERATELY NOT HERE ------------------------------------------------
 * See `REJECTED_SECURITY_HEADERS` below. Every entry there is machine-checked: the guard in
 * `sim/test_demo_proxy.mjs` requires that a security header the PAGES ship is either in the
 * set above or carries a written reason below, and that a rejected header is genuinely
 * absent from a real response. That is the fix for how the page CSP ended up with no
 * `script-src` for months — a header list nobody can explain.
 */
export const API_SECURITY_HEADERS = Object.freeze({
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  "Cross-Origin-Resource-Policy": "same-origin",
});

/** Headers considered for `/api/*` and REJECTED, each with the reason. A rejection is a
 *  decision on the merits, and the merits are written down; `sim/test_demo_proxy.mjs`
 *  fails if any of these ever appears on a real response, so a "let us add it back for
 *  symmetry" edit has to change this map and read the reason first. */
export const REJECTED_SECURITY_HEADERS = Object.freeze({
  "X-Frame-Options":
    "Redundant and weaker. `frame-ancestors 'none'` in the CSP above says the same thing " +
    "and is honoured by every browser that would honour the CSP at all; where the two " +
    "disagree the spec says frame-ancestors wins. It is also aimed at clickjacking, and a " +
    "JSON body has no UI to clickjack. `sim/web/_headers` made the same call for the pages.",
  "Permissions-Policy":
    "Inert on this response. It governs which powerful features a DOCUMENT and its iframes " +
    "may use; an API response is never a document that uses a feature, and it embeds " +
    "nothing. Copying the pages' `microphone=(self), camera=(), geolocation=()` here would " +
    "add ~50 bytes to a route the page polls every 30 s and change nothing a browser does. " +
    "The header that matters for the mic is the one on the PAGE, which already ships.",
  "Cross-Origin-Opener-Policy":
    "Only meaningful on a top-level DOCUMENT response — it severs the opener relationship " +
    "of a browsing context. An /api/* response never becomes one (and if a navigation " +
    "somehow rendered it, there is nothing in a JSON body for an opener to reach).",
  "Cross-Origin-Embedder-Policy":
    "Governs what a document is allowed to EMBED, which is a statement about a page, not " +
    "about an API reply that embeds nothing. `require-corp` here would constrain nobody; " +
    "if this origin ever wants cross-origin isolation, that is a decision for the pages' " +
    "`_headers`, taken together with COOP, and it would need every subresource re-checked.",
  "Access-Control-Allow-Origin":
    "NEVER, in any form — a standing rule (§4.3), listed here so it cannot be added by " +
    "someone tidying the header set. The wildcard in sim/tts/server.py is the pattern this " +
    "must not repeat: with no ACAO a cross-origin caller cannot read a reply even if it " +
    "somehow got past the origin pin, and that belt stays on.",
});

const MAX_MESSAGE_CHARS = 200;

/**
 * Scrub the one free-text field. Belt and braces over the key allowlist: even a caller
 * that hands us an upstream string cannot leak a URL (which would expose the gateway
 * base) or a key-shaped token through it.
 */
export function sanitizeMessage(text) {
  if (typeof text !== "string" || !text) return "";
  return text
    .replace(/[a-z][a-z0-9+.-]*:\/\/\S+/gi, "[url removed]")
    .replace(/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}/g, "[key removed]")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, MAX_MESSAGE_CHARS);
}

/** §7: the capacity level, from the numbers. ok < 60% < busy < ceiling = full. */
export function loadLevel(inflight, capacity) {
  const cap = Number(capacity);
  const n = Number(inflight);
  if (!Number.isFinite(cap) || cap <= 0 || !Number.isFinite(n) || n < 0) return "ok";
  if (n >= cap) return "full";
  if (n / cap >= 0.6) return "busy";
  return "ok";
}

function normalizeLoad(load) {
  const inflight = Number.isFinite(Number(load && load.inflight)) ? Number(load.inflight) : 0;
  const capacity = Number.isFinite(Number(load && load.capacity)) ? Number(load.capacity) : 0;
  const level = load && typeof load.level === "string" ? load.level : loadLevel(inflight, capacity);
  return { level: ["ok", "busy", "full"].includes(level) ? level : "ok", inflight, capacity };
}

function plainObject(v) {
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

function wireList(v) {
  if (!Array.isArray(v)) return [];
  // Only the two fields `route()` needs (bridge.js:366-376), as strings. A stray field
  // on a message object cannot carry anything out of here.
  return v.map((m) => ({ topic: String((m && m.topic) || ""), payload: String((m && m.payload) || "") }));
}

function speechList(v) {
  if (!Array.isArray(v)) return [];
  return v.map((s) => ({
    ticket: String((s && s.ticket) || ""),
    event_id: String((s && s.event_id) || ""),
    chunk_num: Number.isFinite(Number(s && s.chunk_num)) ? Number(s.chunk_num) : 0,
  }));
}

/**
 * Build the envelope. Every key in `PUBLIC_KEYS` is present, every other key is
 * discarded, and `reason` is forced into the closed set.
 */
export function envelope(partial) {
  const p = plainObject(partial);
  let reason = p.reason === undefined || p.reason === null ? null : String(p.reason);
  if (reason !== null && !REASONS.includes(reason)) reason = "bad_request";
  const retry = Number(p.retry_after_s);
  const body = {
    ok: p.ok === undefined ? reason === null || reason === "blocked" : !!p.ok,
    degraded: p.degraded === undefined ? reason !== null : !!p.degraded,
    reason,
    retry_after_s: Number.isFinite(retry) && retry > 0 ? Math.ceil(retry) : 0,
    message: sanitizeMessage(p.message),
    mode: p.mode === "live" ? "live" : "degraded",
    load: normalizeLoad(p.load),
    limits: plainObject(p.limits),
    messages: wireList(p.messages),
    speech: speechList(p.speech),
    context: typeof p.context === "string" ? p.context : "",
    // P1's ears (`/api/transcribe`). It is a FIELD ON THE ONE ENVELOPE rather than the
    // bare `DeepgramResponse` §3.2 sketched, and that is a deliberate deviation recorded
    // at `functions/api/transcribe.js`'s header: a Deepgram body carries no `reason`, no
    // `mode` and no `retry_after_s`, so a rate-limited visitor would be indistinguishable
    // from a deployment with no ears at all and `mic.js` could not degrade honestly. The
    // Deepgram shape is still what `mic.js` parses from the LOCAL sidecar, unchanged.
    transcript: typeof p.transcript === "string" ? p.transcript : "",
    voice: !!p.voice,
    ears: !!p.ears,
  };
  // Reassemble in PUBLIC_KEYS order so the wire shape is stable and the allowlist is the
  // literal construction, not a filter applied after the fact.
  const out = {};
  for (const k of PUBLIC_KEYS) out[k] = body[k];
  return out;
}

/** The HTTP status for a body, honouring §4.5. `opts.status` overrides — `/api/health` is
 *  always 200 so that a probe failure means "route absent", unambiguously (§3.2). */
export function statusFor(body, opts) {
  if (opts && Number.isFinite(Number(opts.status))) return Number(opts.status);
  if (!body.reason) return 200;
  const s = STATUS_FOR[body.reason];
  return Number.isFinite(s) ? s : 400;
}

/** The `Retry-After` seconds for a body, or null for "send no header". */
export function retryAfterFor(body) {
  if (!body.reason) return null;
  if (body.retry_after_s > 0) return body.retry_after_s;
  const fixed = RETRY_AFTER_FOR[body.reason];
  return Number.isFinite(fixed) ? fixed : null;
}

/**
 * Turn a partial envelope into a `Response`.
 *
 * `Cache-Control: no-store` on every reply (§3.2) — a cached mode or a cached refusal is
 * a lie with a TTL. No `Access-Control-Allow-Origin` header is ever sent (§4.3): the
 * wildcard in sim/tts/server.py is the pattern this must NOT repeat.
 *
 * `API_SECURITY_HEADERS` is applied to EVERY reply this function builds — the success, the
 * `forbidden_origin` 403, the `rate_limited` 429, the `upstream_down` 503, all of them.
 * That is not tidiness: a refusal is exactly the response an attacker is most likely to be
 * looking at, and a header set that only applies when things go well is not a header set.
 * It is applied LAST so `opts.headers` cannot weaken it.
 *
 * @param {object} partial   the envelope fields
 * @param {object} [opts]    {status, rateLimit:{limit,remaining,reset}, headers}
 */
export function respond(partial, opts) {
  const body = envelope(partial);
  const status = statusFor(body, opts);
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    // Rides every response, not just the rejections, so the page can pace itself
    // *before* it is refused (§4.5).
    "X-Moxie-Mode": body.mode,
  });
  const retry = retryAfterFor(body);
  if (retry !== null) headers.set("Retry-After", String(retry));
  const rl = opts && opts.rateLimit;
  if (rl) {
    if (Number.isFinite(Number(rl.limit))) headers.set("X-RateLimit-Limit", String(rl.limit));
    if (Number.isFinite(Number(rl.remaining))) headers.set("X-RateLimit-Remaining", String(rl.remaining));
    if (Number.isFinite(Number(rl.reset))) headers.set("X-RateLimit-Reset", String(rl.reset));
  }
  if (opts && opts.headers) for (const [k, v] of Object.entries(opts.headers)) headers.set(k, String(v));
  // LAST, so `opts.headers` cannot weaken them. Nothing passes `opts.headers` today, but
  // the hatch exists, and a hardening set a caller can quietly turn off is not a hardening
  // set. Values come from the frozen `API_SECURITY_HEADERS` above — never from the request,
  // so no request header can be echoed back through this loop (§4.2, C1).
  for (const [k, v] of Object.entries(API_SECURITY_HEADERS)) headers.set(k, v);
  return new Response(JSON.stringify(body), { status, headers });
}
