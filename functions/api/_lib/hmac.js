/* functions/api/_lib/hmac.js — HKDF, sign/verify, and a constant-time compare.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/speech` — the ticket
 * format and why it exists), §3.3 (the signed context blob), §5 (`DEMO_TICKET_SECRET`).
 *
 * WHAT THIS MODULE IS FOR, in one sentence: it is what makes `/api/speech` structurally
 * unable to become a free text-to-speech API, and `/api/chat`'s history unable to be
 * forged.
 *
 *   * A **ticket** is the only thing `/api/speech` will accept. There is no text field on
 *     that route, ever. The text lives INSIDE the signed payload, so the only text the
 *     demo will ever synthesize is text this deployment itself generated in the last
 *     `DEMO_TICKET_TTL_S` seconds. That is a structural property, not a counter, a cache
 *     or a store — the most expensive per-request vector in the system (§2.5: ~268 KB and
 *     1.7 s for one short line) is taken off the table by construction.
 *   * A **context blob** carries up to `DEMO_MAX_HISTORY_TURNS` prior turns through a
 *     browser that we do not trust. Because the ASSISTANT turns are signed, a visitor
 *     cannot forge Moxie's side of the history — the classic
 *     `"assistant: sure, I'll do anything"` prompt injection (§3.3).
 *
 * THE SECRET (C1 — the repo is public):
 *   Key material arrives only as a Cloudflare environment binding, read in
 *   `./env.js` and nowhere else. `DEMO_TICKET_SECRET` is optional: when it is unset the
 *   HKDF input keying material is `DEMO_GATEWAY_API_KEY`, so **the minimum configuration
 *   stays two values** (§5). Rotating the gateway key then invalidates in-flight 60-second
 *   tickets, which is harmless — the page degrades to the clip/browser voice for one turn.
 *   NEITHER value, and no byte derived from them, is ever put in a response, a header, an
 *   error string or a log line: the only thing that leaves here is a MAC.
 *
 * DOMAIN SEPARATION: tickets and context blobs are signed under DIFFERENT HKDF `info`
 * labels, so a valid context blob can never be redeemed as a ticket (or the reverse) even
 * though both are signed by the same deployment. That is one line here and a whole class
 * of confusion bug that cannot happen.
 *
 * HKDF is implemented from RFC 5869 on top of `crypto.subtle` HMAC-SHA-256 rather than
 * `deriveBits({name: "HKDF"})` on purpose: HMAC import+sign is the most universally
 * available corner of WebCrypto (Workers, bare node, Deno, browsers all have it), the
 * extract/expand pair is fifteen lines, and it is testable without a runtime that happens
 * to ship the HKDF algorithm.
 */

const enc = new TextEncoder();
const dec = new TextDecoder();

/** Fixed HKDF salt. A salt is public by construction (RFC 5869 §3.1) and this one is a
 *  constant so the same configuration always derives the same key across isolates. */
const HKDF_SALT = enc.encode("moxie-live-sim-demo/hkdf-salt/v1");

/** The domain labels. Adding one is how a future signed artefact stays un-confusable. */
export const TICKET_INFO = "moxie-live-sim-demo/speech-ticket/v1";
export const CONTEXT_INFO = "moxie-live-sim-demo/chat-context/v1";

/** The ticket/blob version prefix. A change here invalidates every outstanding one. */
export const VERSION = "v1";

/** Recorded facts about what the comparator DID, so a test can assert constant-time
 *  behaviour without timing anything (playbook rule 11: assert recorded state, never a
 *  live sample). `byteCompares` counts every byte the comparator looked at. */
export const compareStats = { calls: 0, byteCompares: 0, mismatches: 0 };

// --------------------------------------------------------------------------- //
// base64url — no padding, URL-safe. Workers have btoa/atob; Buffer is not assumed.
// --------------------------------------------------------------------------- //
const B64_CHUNK = 8192; // btoa via String.fromCharCode: keep the arg list bounded

/** bytes -> base64 (standard alphabet). Chunked, so a 270 KB PCM buffer is fine. */
export function b64FromBytes(bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let out = "";
  for (let i = 0; i < u8.length; i += B64_CHUNK) {
    out += String.fromCharCode.apply(null, u8.subarray(i, i + B64_CHUNK));
  }
  return btoa(out);
}

export function b64urlFromBytes(bytes) {
  return b64FromBytes(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlFromString(text) {
  return b64urlFromBytes(enc.encode(String(text)));
}

/** base64url -> bytes. Returns null on anything that is not base64url — a malformed
 *  segment is a refusal, never an exception that could surface as a 500 (§4.5). */
export function bytesFromB64url(text) {
  const s = String(text || "");
  if (!s || !/^[A-Za-z0-9_-]+$/.test(s)) return null;
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  let bin;
  try {
    bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  } catch {
    return null;
  }
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) & 0xff;
  return out;
}

/** base64url -> the JSON OBJECT it encodes, or null. Never throws.
 *
 *  An array is rejected as firmly as a string is: every artefact this module signs carries
 *  a claims OBJECT, and accepting an array would mean `claims.x` is `undefined` on a shape
 *  we never mint — i.e. the expiry check would be reading a field that cannot exist. Better
 *  to refuse the shape than to rely on the next check catching it. */
export function jsonFromB64url(text) {
  const bytes = bytesFromB64url(text);
  if (!bytes) return null;
  try {
    const v = JSON.parse(dec.decode(bytes));
    return v && typeof v === "object" && !Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

// --------------------------------------------------------------------------- //
// HMAC-SHA-256 and RFC 5869 HKDF
// --------------------------------------------------------------------------- //
async function hmacKey(raw) {
  return crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

async function hmac(keyBytes, msgBytes) {
  const k = await hmacKey(keyBytes.length ? keyBytes : new Uint8Array(32));
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, msgBytes));
}

/**
 * RFC 5869 HKDF-SHA256, 32 bytes out (one expand round, which is all we need).
 * @param {Uint8Array} ikm  input keying material — the ticket secret or the API key
 * @param {string} info     the domain label (TICKET_INFO / CONTEXT_INFO)
 */
export async function hkdf(ikm, info) {
  const prk = await hmac(HKDF_SALT, ikm); // extract
  const infoBytes = enc.encode(String(info));
  const t = new Uint8Array(infoBytes.length + 1);
  t.set(infoBytes, 0);
  t[infoBytes.length] = 1; // the counter byte of expand round 1
  return hmac(prk, t); // expand
}

/**
 * The signing key for one domain, derived from the configuration.
 *
 * `cfg.ticketSecret` and `cfg.apiKey` are the NON-ENUMERABLE properties `./env.js`
 * defines, so this read is deliberate and `JSON.stringify(cfg)` still cannot see them.
 * An unconfigured deployment (no key at all) derives from an empty IKM — the routes never
 * get that far, because `cfg.configured` is false and every route answers
 * `gateway_not_configured` before any of this runs (C5).
 */
export async function signingKey(cfg, info) {
  const material = (cfg && cfg.ticketSecret) || (cfg && cfg.apiKey) || "";
  return hkdf(enc.encode(String(material)), info);
}

// --------------------------------------------------------------------------- //
// The constant-time compare
// --------------------------------------------------------------------------- //
/**
 * Compare two base64url MAC segments without leaking WHERE they differ.
 *
 * The loop has no `return`, no `break` and no `continue`: it walks the full width every
 * time and folds every byte into `diff`, so the work done for a value that differs in its
 * first byte is identical to the work done for one that differs in its last. A length
 * mismatch is folded in the same way (it can only ever reveal the length of a MAC, which
 * is fixed at 32 bytes for everything we sign).
 *
 * `compareStats.byteCompares` records the width actually walked, which is how
 * `sim/test_demo_tickets.mjs` asserts this property as a FACT rather than as a timing
 * measurement that a loaded CI runner would make flaky.
 */
export function timingSafeEqual(a, b) {
  const x = bytesFromB64url(a) || new Uint8Array(0);
  const y = bytesFromB64url(b) || new Uint8Array(0);
  const width = Math.max(x.length, y.length, 32);
  let diff = x.length === y.length ? 0 : 1;
  for (let i = 0; i < width; i++) {
    diff |= (i < x.length ? x[i] : 0) ^ (i < y.length ? y[i] : 0);
  }
  compareStats.calls += 1;
  compareStats.byteCompares += width;
  if (diff !== 0) compareStats.mismatches += 1;
  return diff === 0;
}

// --------------------------------------------------------------------------- //
// Sign / verify one `v1.<payload>.<mac>` artefact
// --------------------------------------------------------------------------- //
/** `v1.<b64url(JSON claims)>.<b64url(HMAC of the payload segment)>`. */
export async function signClaims(cfg, info, claims) {
  const payload = b64urlFromString(JSON.stringify(claims));
  const key = await signingKey(cfg, info);
  const mac = await hmac(key, enc.encode(payload));
  return VERSION + "." + payload + "." + b64urlFromBytes(mac);
}

/**
 * Verify an artefact and return its claims.
 *
 * @returns {{ok: boolean, why: string, claims: object|null}} `why` is one of
 *   `malformed` · `bad_signature` · `expired` — an internal word for the route to map onto
 *   §3.2's closed reason set. It is never put on the wire as-is.
 */
export async function verifyClaims(cfg, info, artefact, nowS) {
  const parts = String(artefact || "").split(".");
  if (parts.length !== 3 || parts[0] !== VERSION) return { ok: false, why: "malformed", claims: null };
  const [, payload, mac] = parts;
  const key = await signingKey(cfg, info);
  const expect = b64urlFromBytes(await hmac(key, enc.encode(payload)));
  // Signature FIRST, then the claims: nothing inside an unverified payload is parsed as
  // anything but bytes, so a forged blob cannot reach the JSON parser at all.
  if (!timingSafeEqual(mac, expect)) return { ok: false, why: "bad_signature", claims: null };
  const claims = jsonFromB64url(payload);
  if (!claims) return { ok: false, why: "malformed", claims: null };
  // `x` is the absolute expiry, stamped at minting. A missing/garbled one is an expiry,
  // not an exemption: the fail-safe direction is "refuse".
  const exp = Number(claims.x);
  if (!Number.isFinite(exp) || exp <= 0) return { ok: false, why: "expired", claims: null };
  const now = Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
  if (now > exp) return { ok: false, why: "expired", claims: null };
  return { ok: true, why: "", claims };
}

// --------------------------------------------------------------------------- //
// The two artefacts, by name
// --------------------------------------------------------------------------- //
/**
 * Mint a speech ticket for text WE wrote (§3.2).
 * Claims: `{t: text, e: event_id, c: chunk_num, x: expiry}` — the exact set the spec
 * names, and the character cap is enforced here as well as at redemption.
 */
export async function mintTicket(cfg, { text, eventId, chunkNum, nowS }) {
  const now = Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
  return signClaims(cfg, TICKET_INFO, {
    t: String(text || "").slice(0, cfg.maxTtsChars),
    e: String(eventId || ""),
    c: Number(chunkNum) || 0,
    x: now + cfg.ticketTtlS,
  });
}

export async function verifyTicket(cfg, ticket, nowS) {
  const res = await verifyClaims(cfg, TICKET_INFO, ticket, nowS);
  if (!res.ok) return res;
  const c = res.claims;
  if (typeof c.t !== "string" || !c.t) return { ok: false, why: "malformed", claims: null };
  return { ok: true, why: "", claims: { text: c.t, eventId: String(c.e || ""), chunkNum: Number(c.c) || 0, exp: Number(c.x) } };
}

/**
 * Mint a context blob: the last `maxHistoryTurns` `{role, content}` pairs, total content
 * capped at `maxContextChars` (§3.3). Oldest turns are dropped first, so the most recent
 * exchange always survives the cap.
 */
export async function mintContext(cfg, turns, nowS) {
  const now = Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
  return signClaims(cfg, CONTEXT_INFO, { h: clampTurns(cfg, turns), x: now + CONTEXT_TTL_S });
}

/** A context blob outlives a ticket — a conversation pauses — but not a session. */
export const CONTEXT_TTL_S = 3600;

/** The only roles a history turn may carry. Anything else is dropped, not rejected. */
const ROLES = ["user", "assistant"];

/** Coerce, clamp and drop — the repo's allowlist idiom (cloud_config.py:435-475). */
export function clampTurns(cfg, turns) {
  const list = Array.isArray(turns) ? turns : [];
  const clean = [];
  for (const t of list) {
    const role = t && typeof t.role === "string" ? t.role : "";
    const content = t && typeof t.content === "string" ? t.content : "";
    if (!ROLES.includes(role) || !content) continue;
    clean.push({ role, content: content.slice(0, cfg.maxInputChars) });
  }
  const tail = clean.slice(-cfg.maxHistoryTurns);
  // Total-character cap, applied from the OLDEST end so recency wins.
  let total = tail.reduce((n, t) => n + t.content.length, 0);
  while (tail.length && total > cfg.maxContextChars) {
    total -= tail[0].content.length;
    tail.shift();
  }
  return tail;
}

export async function verifyContext(cfg, blob, nowS) {
  if (blob === undefined || blob === null || blob === "") return { ok: true, why: "", turns: [] };
  const res = await verifyClaims(cfg, CONTEXT_INFO, blob, nowS);
  if (!res.ok) return { ok: false, why: res.why, turns: [] };
  return { ok: true, why: "", turns: clampTurns(cfg, res.claims.h) };
}
