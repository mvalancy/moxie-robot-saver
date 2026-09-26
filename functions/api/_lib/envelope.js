/* functions/api/_lib/envelope.js — the one response shape, and its status + `Retry-After`
 * mapping.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (envelope), §4.2 (what the browser
 * may know), §4.5 (status table), §7 (capacity signal).
 *
 * One envelope, so the client has ONE branch: success or failure, every body has the same
 * keys. THE BROWSER MAY NEVER SEE the gateway base URL, the key, a model id, an upstream
 * status or an upstream body (they echo org ids and key prefixes), so every response is
 * built from a FIXED KEY ALLOWLIST (`PUBLIC_KEYS`) — unknown keys are never copied — and
 * the one free-text field, `message`, is also scrubbed. Never a bare 500; never a 200 with
 * an empty string.
 */

/**
 * The closed reason set (§3.2). Adding one is a CONTRACT CHANGE made in two places: here
 * and `sim/web/mode.js`, which coerces an unknown reason to `null` (a healthy turn).
 *
 * `gateway_unreachable_or_gated` — an HTML/non-JSON upstream reply, typically a Cloudflare
 *   Access login page served at 200. Same status and visitor copy as `upstream_down`, but a
 *   different operator fix (configure `./env.js::ACCESS_VARS`).
 * `turnstile_failed` (403) — the VISITOR's token is bad; the page answers that one turn
 *   from the stub and stays live.
 * `turnstile_misconfigured` (503, Retry-After 60) — OUR config is bad; fails every visitor
 *   identically, so it degrades the page. How an operator validates the secret without
 *   printing it. Nothing from Cloudflare's reply is forwarded to produce it.
 */
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
  "turnstile_failed",
  "turnstile_misconfigured",
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
  // The public Turnstile sitekey, or `""` when not enforced (`./env.js::publicTurnstile`).
  // On the envelope, not in `limits`: `limits` is a closed set of caps, and a sitekey is
  // not a cap.
  "turnstile",
  "messages",
  "speech",
  "context",
  "transcript",
  "voice",
  "ears",
  /* A mermaid diagram Moxie drew, as SOURCE TEXT, or `""`. On the envelope rather than the
   * robot wire's `output`, which must stay byte-compatible with `mqtt/moxie_sdk/wire.py`.
   * Untrusted: `sim/web/diagram.js` renders it with a vendored mermaid under the page CSP.
   * `chat.js::splitDiagram` has already removed it from the spoken line. */
  "diagram",
  /* The document she answered from, as `"<title>|<path>"`, or `""`. Lets a visitor read
   * the source and makes server-side retrieval observable from outside. Our own index
   * metadata, already public in `/docs-index.json`. */
  "cited",
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
  // 403, not 400: a refused bot check is not a complaint about the visitor's sentence.
  turnstile_failed: 403,
  // A wrong secret refuses every visitor identically: degrade like `upstream_down`.
  turnstile_misconfigured: 503,
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
  // No header: the page mints a fresh token on the next send.
  turnstile_failed: null,
  // The fix is a deployment change; re-asking sooner cannot help.
  turnstile_misconfigured: 60,
});

/*
 * THE HARDENING HEADER SET FOR /api/*. It lives in code because `sim/web/_headers` is NOT
 * applied to a Pages Function response (settled by a preview deploy, §10 assumption 27):
 * this object is the only thing that ships on a route response.
 *
 * `X-Content-Type-Options: nosniff` — a cross-origin `<script src=/api/…>` fails the strict
 *   MIME check instead of executing JSON.
 * `Referrer-Policy: same-origin` — a route URL can carry a ticket.
 * `Strict-Transport-Security` — byte-identical to the pages' `/*` value so the origin
 *   speaks with one voice (`sim/test_api_headers.mjs` asserts equality). Ignored over plain
 *   http, so it cannot trap `wrangler pages dev`. No `preload`: that is the owner's call.
 * `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'` —
 *   NOT the page policy: a JSON body loads nothing, so this is a lockdown for the case where
 *   a browser treats the response as a document. Every fetch directive falls back to
 *   `default-src`; `frame-ancestors` and `base-uri` do not, so they are named. Cost: a
 *   browser JSON viewer may show raw text.
 * `Cross-Origin-Resource-Policy: same-origin` — closes `no-cors` loads (`<img>`, `<audio>`,
 *   opaque fetch) from other sites, which the origin pin and absent ACAO do not. Only
 *   consulted cross-origin, so the page's own same-origin fetches are unaffected;
 *   `sim/test_api_headers.mjs` proves both in a real browser.
 *
 * What is deliberately NOT here is `REJECTED_SECURITY_HEADERS`, machine-checked by
 * `sim/test_demo_proxy.mjs` (each needs a written reason and must be absent from a reply).
 */
export const API_SECURITY_HEADERS = Object.freeze({
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  "Cross-Origin-Resource-Policy": "same-origin",
});

/** Headers considered for `/api/*` and REJECTED, each with its reason (checked: each must
 *  be absent from a real response). */
export const REJECTED_SECURITY_HEADERS = Object.freeze({
  "X-Frame-Options":
    "Redundant and weaker: `frame-ancestors 'none'` in the CSP says the same and wins where " +
    "they disagree; a JSON body has no UI to clickjack.",
  "Permissions-Policy":
    "Inert: it governs which features a DOCUMENT may use, and an API response is never one. " +
    "The mic's policy is the one on the page, which already ships.",
  "Cross-Origin-Opener-Policy":
    "Only meaningful on a top-level DOCUMENT response (it severs the opener relationship); " +
    "an /api/* response never becomes one.",
  "Cross-Origin-Embedder-Policy":
    "Governs what a document may EMBED; an API reply embeds nothing. Cross-origin isolation, " +
    "if ever wanted, is a decision for the pages' `_headers` together with COOP.",
  "Access-Control-Allow-Origin":
    "NEVER, in any form (§4.3): with no ACAO a cross-origin caller cannot read a reply even " +
    "if it got past the origin pin. Listed so nobody adds it while tidying.",
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
  // Only the two fields `bridge.js::route()` needs, as strings.
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
    // String fields use `""` for absent, never null — one convention for the client.
    turnstile: typeof p.turnstile === "string" ? p.turnstile : "",
    messages: wireList(p.messages),
    speech: speechList(p.speech),
    context: typeof p.context === "string" ? p.context : "",
    // `/api/transcribe`'s result — on the envelope rather than a bare Deepgram body so a
    // refusal still carries `reason`/`mode`/`retry_after_s` (see transcribe.js).
    transcript: typeof p.transcript === "string" ? p.transcript : "",
    voice: !!p.voice,
    ears: !!p.ears,
    // Capped here, where the shape is guaranteed, rather than trusted from the caller.
    diagram: typeof p.diagram === "string" ? p.diagram.slice(0, 1200) : "",
    cited: typeof p.cited === "string" ? p.cited.slice(0, 300) : "",
  };
  // Built in PUBLIC_KEYS order: the allowlist is the construction, not a filter.
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
 * `Cache-Control: no-store` on every reply (a cached refusal is a lie with a TTL); never
 * `Access-Control-Allow-Origin` (§4.3). `API_SECURITY_HEADERS` goes on EVERY reply,
 * refusals included, and is applied LAST so `opts.headers` cannot weaken it.
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
    // On every response so the page can pace itself before it is refused (§4.5).
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
  // LAST, from the frozen set — never from the request, so nothing is echoed back.
  for (const [k, v] of Object.entries(API_SECURITY_HEADERS)) headers.set(k, v);
  return new Response(JSON.stringify(body), { status, headers });
}
