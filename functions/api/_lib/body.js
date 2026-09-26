/* functions/api/_lib/body.js — bounded request-body readers. One place decides "how big is
 * too big", so no route can invent its own. Neither reader throws. */

/**
 * The JSON body ceiling, DERIVED from the caps so an override scales it: one utterance plus
 * one context blob, with base64 + JSON escaping under 3x, plus a flat 4096 for syntax and
 * small fields (the Turnstile token is up to 2048 bytes and fits inside it). Re-check this
 * arithmetic before adding another large field: the failure is a `too_long` that blames the
 * visitor's sentence for bytes it did not contribute.
 */
export function maxJsonBodyBytes(cfg) {
  return 4096 + 3 * (cfg.maxInputChars + cfg.maxContextChars);
}

/**
 * Read a JSON request body, bounded.
 * @returns {{ok: boolean, reason: string|null, body: object}}
 */
export async function readJsonBody(request, cfg) {
  const max = maxJsonBodyBytes(cfg);
  const declared = Number(request.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > max) return { ok: false, reason: "too_long", body: {} };
  let text;
  try {
    text = await request.text();
  } catch {
    return { ok: false, reason: "bad_request", body: {} };
  }
  // A chunked body can exceed the declared length, so the real bytes are checked too.
  if (text.length > max) return { ok: false, reason: "too_long", body: {} };
  if (!text.trim()) return { ok: true, reason: null, body: {} };
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return { ok: false, reason: "bad_request", body: {} };
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, reason: "bad_request", body: {} };
  return { ok: true, reason: null, body };
}

/**
 * Read a RAW AUDIO body (`/api/transcribe`), bounded at both ends. Both caps are cost
 * controls:
 *
 *   * `DEMO_MAX_AUDIO_BYTES` bounds one upload. The declared `Content-Length` is checked
 *     first so an oversized upload is refused UNREAD; the real count is checked after,
 *     because a chunked body can exceed what it declared.
 *   * `DEMO_MIN_AUDIO_BYTES` mirrors `mqtt/moxie_sdk/stt.py`'s `MIN_MS` — no audio, no
 *     request, no cost: a blip too short to be speech never becomes a paid round trip.
 *
 * 500 KB is ~15 s of 16 kHz PCM but MINUTES of webm/Opus, so the duration ceiling is
 * `DEMO_MAX_RECORD_MS`, enforced in `sim/web/mic.js` — a Function only sees the upload.
 *
 * @returns {{ok: boolean, reason: string|null, bytes: Uint8Array|null, declared: number}}
 */
export async function readAudioBody(request, cfg) {
  const max = cfg.maxAudioBytes;
  const declared = Number(request.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > max) {
    return { ok: false, reason: "too_long", bytes: null, declared }; // refused UNREAD
  }
  let buf;
  try {
    buf = await request.arrayBuffer();
  } catch {
    return { ok: false, reason: "bad_request", bytes: null, declared: 0 };
  }
  const bytes = new Uint8Array(buf);
  if (bytes.length > max) return { ok: false, reason: "too_long", bytes: null, declared: bytes.length };
  // THE NO-CALL FLOOR. Below it the route returns and the gateway is never touched.
  if (bytes.length < cfg.minAudioBytes) {
    return { ok: false, reason: "too_short", bytes: null, declared: bytes.length };
  }
  return { ok: true, reason: null, bytes, declared: bytes.length };
}
