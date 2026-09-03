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

/** The closed reason set (§3.2). Nothing else may ever appear in `reason`. */
export const REASONS = Object.freeze([
  "rate_limited",
  "at_capacity",
  "budget_exhausted",
  "upstream_down",
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
  "voice",
  "ears",
]);

/** §4.5's table. A reason absent here is a programming error, not a 500 (see `respond`). */
export const STATUS_FOR = Object.freeze({
  rate_limited: 429,
  at_capacity: 503,
  budget_exhausted: 503,
  upstream_down: 503,
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
 * @param {object} partial   the envelope fields
 * @param {object} [opts]    {status, rateLimit:{limit,remaining,reset}, headers}
 */
export function respond(partial, opts) {
  const body = envelope(partial);
  const status = statusFor(body, opts);
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
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
  return new Response(JSON.stringify(body), { status, headers });
}
