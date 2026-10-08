/* functions/api/transcribe.js — POST /api/transcribe, the ears.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2, §4.1 (why a byte cap is not a
 * duration cap), §4.5 (statuses), §6 (fallback), §10 assumptions 15/16.
 *
 * WHAT MAKES THE EARS SAFE, in the order the code checks it:
 *  1. `DEMO_STT_MODEL` unset => no ears and no call (`/api/health` already says `ears: false`).
 *  2. The floor is free: under `DEMO_MIN_AUDIO_BYTES` the answer is `too_short`, uncharged.
 *  3. The ceiling `DEMO_MAX_AUDIO_BYTES` is checked against `Content-Length` before reading.
 *  4. The bytes are SNIFFED, not believed; an unrecognised container is refused for free.
 *  5. The model is server-fixed; nothing but the audio itself reaches the upstream body.
 *  6. STT is priced by DURATION. A WAV's declared playing time is read from its header and
 *     refused over `DEMO_MAX_RECORD_MS`. Compressed containers cannot be timed without a
 *     decoder, so `DEMO_STT_FORMATS` defaults to `wav` alone — a fork that widens it re-opens
 *     the gap with no warning from the code.
 *  6b. A WAV the gateway cannot decode is refused too (`_lib/wav.js::sttWavProblem`): its
 *     STT answers one with a 500, and three 500s cool the STT group down for EVERY visitor
 *     for about a minute.
 *  7. A bot control (`_lib/turnstile.js`) with its OWN action, so a chat token cannot buy
 *     the ears. This is the more expensive route to leave open: 60/hour x 15 s from one
 *     address with no daily window.
 *  8. NOTHING IS STORED, LOGGED OR CACHED. Caching STT is a privacy problem, not a saving.
 *  9. A transcript of sound labels only ("(machine whirring)", "[BLANK_AUDIO]") is silence,
 *     not the child's words (`cleanTranscript`).
 *
 * Zero upstream calls on every refusal path, and each of those refunds the units `admit()`
 * charged. The key never leaves this process; no upstream status, body or header is
 * forwarded (an OpenAI-compatible error names the model and often a key prefix).
 *
 * DELIBERATE DEVIATIONS FROM §3.2
 *  (1) The response is the house envelope with a `transcript` field, not a bare
 *      DeepgramResponse, so a refusal can carry `reason`/`retry_after_s` and `mic.js` can
 *      degrade honestly. `mic.js` keeps its Deepgram parse for the local sidecar.
 *  (2) The gateway answers webm/Opus, ogg/Opus and mp4/AAC with HTTP 500 (measured; 16 kHz
 *      mono WAV transcribes fine), and a 500 would degrade the whole page. Hence the
 *      container allowlist, and `mic.js` encoding WAV in the browser.
 *  (3) An upstream 4xx about the PAYLOAD is `bad_request` (400, per-turn), not
 *      `upstream_down` (503, degrades the page) — see `reasonForUpstreamStatus`.
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { admit, noteUpstreamCall, readAudioBody } from "./_lib/limits.js";
import { tokenFromHeader, verify as verifyTurnstile } from "./_lib/turnstile.js";
import { sttWavProblem, wavDurationMs } from "./_lib/wav.js";
import { joinUrl } from "./_lib/wire.js";
import { fetchFailure, limitedOrRedirected, refusal as refuse } from "./_lib/upstream.js";

const refusal = (cfg, reason, extra) => refuse(cfg, "transcribe", reason, extra, { transcript: "" });

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // 1. Configuration. `ears` derives from the same `cfg` as the probe, so they cannot
  //    disagree.
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") return refusal(cfg, "gateway_not_configured", {});
  if (!cfg.ears) return refusal(cfg, "gateway_not_configured", {});

  // 2. Admission, same helper and order as the other routes; the `finally` hands the slot on.
  const slot = await admit({ request, cfg, route: "transcribe" });
  if (!slot.ok) {
    return refusal(cfg, slot.reason, {
      retryAfterS: slot.retryAfterS,
      rateLimit: slot.rateLimit,
      load: slot.load,
    });
  }

  try {
    // A refusal that spends nothing upstream gives back the units `admit()` charged (see
    // `chat.js`); the upstream failure at step 5 does not.
    const spentNothing = (reason, extra) => {
      slot.refundBudget();
      return refusal(cfg, reason, { load: slot.load, rateLimit: slot.rateLimit, ...(extra || {}) });
    };

    // 3. The body: raw audio, bounded at both ends.
    const body = await readAudioBody(request, cfg);
    if (!body.ok) return spentNothing(body.reason);

    // 4. What is it? Sniffed, with the declared type as fallback, against an allowlist…
    const kind = audioKind(body.bytes, request.headers.get("Content-Type"));
    if (!kind) return spentNothing("bad_request");

    // 4b. …that this gateway takes (`DEMO_STT_FORMATS`; see deviation 2).
    if (!cfg.sttFormats.includes(kind.ext)) return spentNothing("bad_request");

    // 4c. …and how long does it say it is? A header walk over integers, no decoding. Over the
    //     cap is `too_long`, whatever else is wrong with it…
    if (kind.ext === "wav") {
      const dur = wavDurationMs(body.bytes);
      if (dur && dur.ms > cfg.maxRecordMs) return spentNothing("too_long");
      // …and a header it cannot read, or an fmt the gateway cannot decode, is never
      // forwarded: that upload would be an upstream 500 and a strike towards the cooldown (6b).
      if (sttWavProblem(body.bytes)) return spentNothing("bad_request");
    }

    // 4d. The bot control, placed as in `chat.js` step 7: after every free refusal and
    //     immediately before the only `fetch()`. The token rides a HEADER because the body
    //     is raw audio.
    const bot = await verifyTurnstile(cfg, request, tokenFromHeader(request), "transcribe");
    if (!bot.ok) return spentNothing(bot.reason);

    // 5. The one upstream call.
    const upstream = await callGateway(cfg, body.bytes, kind);
    if (!upstream.ok) {
      return refusal(cfg, upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // 6. The transcript. An EMPTY one is a success: the gateway heard silence.
    const clean = cleanTranscript(upstream.text, cfg.maxInputChars);
    return respond(
      {
        ok: true,
        degraded: false,
        reason: null,
        // The one success that uses `message`: the visitor is told the transcript was cut.
        message: clean.truncated ? "transcript truncated to the input cap" : "",
        mode: "live",
        load: slot.load,
        limits: publicLimits(cfg),
        messages: [],
        speech: [],
        context: "",
        transcript: clean.text,
        voice: cfg.voice,
        ears: cfg.ears,
      },
      { rateLimit: slot.rateLimit },
    );
  } finally {
    slot.release();
  }
}

/** The containers this route forwards. The `ext` matters: `/audio/transcriptions` decodes
 *  largely by FILENAME, hence `utterance.<ext>`. */
export const AUDIO_KINDS = Object.freeze({
  webm: { ext: "webm", mime: "audio/webm" },
  ogg: { ext: "ogg", mime: "audio/ogg" },
  wav: { ext: "wav", mime: "audio/wav" },
  mp4: { ext: "mp4", mime: "audio/mp4" },
  mp3: { ext: "mp3", mime: "audio/mpeg" },
  flac: { ext: "flac", mime: "audio/flac" },
});

/** The declared-`Content-Type` fallback, used ONLY when the bytes are unrecognised. */
const TYPE_TO_KIND = Object.freeze({
  "audio/webm": "webm",
  "video/webm": "webm", // Chrome labels some `MediaRecorder` blobs this way
  "audio/ogg": "ogg",
  "application/ogg": "ogg",
  "audio/wav": "wav",
  "audio/wave": "wav",
  "audio/x-wav": "wav",
  "audio/vnd.wave": "wav",
  "audio/mp4": "mp4",
  "audio/x-m4a": "mp4",
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/flac": "flac",
  "audio/x-flac": "flac",
});

const ascii = (b, at, s) => {
  for (let i = 0; i < s.length; i++) if (b[at + i] !== s.charCodeAt(i)) return false;
  return true;
};

/**
 * Identify the container from its magic number; the VISITOR's Content-Type is a second
 * opinion against the same allowlist. Everything else — JSON, HTML, an image, raw PCM — is
 * refused for free.
 *
 * @param {Uint8Array} b
 * @param {string|null} declaredType
 * @returns {{ext:string, mime:string, sniffed:boolean}|null}
 */
export function audioKind(b, declaredType) {
  const bytes = b || new Uint8Array(0);
  let id = null;
  if (bytes.length >= 12) {
    // EBML — Matroska, and therefore webm.
    if (bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3) id = "webm";
    else if (ascii(bytes, 0, "OggS")) id = "ogg";
    else if (ascii(bytes, 0, "RIFF") && ascii(bytes, 8, "WAVE")) id = "wav";
    else if (ascii(bytes, 4, "ftyp")) id = "mp4"; // Safari records mp4/AAC
    else if (ascii(bytes, 0, "fLaC")) id = "flac";
    else if (ascii(bytes, 0, "ID3")) id = "mp3";
    // An MPEG frame sync: 11 set bits. Last, because it is the loosest test here.
    else if (bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0) id = "mp3";
  }
  if (id) return { ...AUDIO_KINDS[id], sniffed: true };

  const declared = String(declaredType || "").split(";")[0].trim().toLowerCase();
  const fromType = TYPE_TO_KIND[declared];
  if (fromType) return { ...AUDIO_KINDS[fromType], sniffed: false };
  return null;
}

/** Whisper's labels for sounds, `(machine whirring)` or `[BLANK_AUDIO]`: one bracketed run. */
const SOUND_LABEL = /\([^()]*\)|\[[^[\]]*\]/g;

/**
 * The visitor's words on their way back to the browser and then into `/api/chat`, so they
 * are bounded and stripped of control characters here. TRUNCATED, NOT REFUSED — unlike
 * typed input, speech cannot be shortened after the fact — and the visitor is told.
 *
 * NOT WORDS: the gateway's STT labels room tone and silence ("(machine whirring)",
 * "[BLANK_AUDIO]") instead of returning nothing. A transcript with no letter or digit left
 * once those labels are gone is silence, returned EMPTY, so the page shows "(nothing heard)"
 * rather than sending it as the child's turn. Words with a label ("(laughs) hi") are kept.
 */
export function cleanTranscript(text, maxChars) {
  // eslint-disable-next-line no-control-regex
  const flat = String(text === undefined || text === null ? "" : text)
    .replace(/[\u0000-\u001F\u007F-\u009F]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!/[\p{L}\p{N}]/u.test(flat.replace(SOUND_LABEL, " "))) return { text: "", truncated: false };
  const max = Number.isFinite(Number(maxChars)) && Number(maxChars) > 0 ? Number(maxChars) : 500;
  return flat.length > max ? { text: flat.slice(0, max).trim(), truncated: true } : { text: flat, truncated: false };
}

/** A whisper `{"text": …}` for 15 s is a few hundred bytes; anything this large is not one. */
const MAX_UPSTREAM_REPLY_BYTES = 1000000;

/**
 * An upstream status -> reason, answering "whose problem is it, and should the whole page
 * degrade?" (a 503 degrades the page; a 400 is per-turn).
 *
 *   429              -> rate_limited
 *   413              -> too_long       our cap was looser than the gateway's
 *   401 / 403 / 407  -> upstream_down  a revoked key is an OPERATOR problem
 *   other 4xx        -> bad_request    the gateway refused THESE BYTES
 *   5xx and the rest -> upstream_down
 */
export function reasonForUpstreamStatus(status) {
  const s = Number(status);
  if (s === 429) return "rate_limited";
  if (s === 413) return "too_long";
  if (s === 401 || s === 403 || s === 407) return "upstream_down";
  if (s >= 400 && s < 500) return "bad_request";
  return "upstream_down";
}

/**
 * `POST {base}/audio/transcriptions`, multipart, server-fixed model.
 *
 * @returns {Promise<{ok:boolean, text?:string, reason?:string, retryAfterS?:number}>}
 */
async function callGateway(cfg, bytes, kind) {
  const form = buildTranscribeForm(cfg, bytes, kind);

  // `fetch` writes the multipart boundary itself for a FormData body; a hand-set
  // Content-Type would name no boundary and read upstream as a malformed body.
  const headers = upstreamHeaders(cfg, "multipart/form-data");
  delete headers["Content-Type"];

  let res;
  try {
    noteUpstreamCall();
    res = await fetch(joinUrl(cfg.baseUrl, "audio/transcriptions"), {
      method: "POST",
      headers,
      body: form,
      signal: AbortSignal.timeout(cfg.sttTimeoutMs),
      redirect: "manual", // a 3xx is a door problem; see `_lib/upstream.js`
    });
  } catch (err) {
    return fetchFailure(err);
  }

  // 429 and 3xx before `reasonForUpstreamStatus`, whose catch-all would call a redirect
  // `upstream_down`.
  const early = limitedOrRedirected(res);
  if (early) return early;

  let text;
  try {
    text = await res.text();
  } catch {
    return { ok: false, reason: "upstream_down" };
  }
  if (text.length > MAX_UPSTREAM_REPLY_BYTES) return { ok: false, reason: "upstream_down" };

  // A Cloudflare Access login page answers 200, 302 and 403 alike, so it is recognised
  // before the status table.
  if (looksLikeHtml(text, res.headers.get("Content-Type"))) {
    return { ok: false, reason: "gateway_unreachable_or_gated" };
  }

  // The error body only classifies the failure and is then dropped.
  if (!res.ok) return { ok: false, reason: reasonForUpstreamStatus(res.status) };

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, reason: "upstream_down" };
  }
  // A 200 with no `text` is a gateway answering something else — not silence.
  if (!parsed || typeof parsed !== "object" || typeof parsed.text !== "string") {
    return { ok: false, reason: "upstream_down" };
  }
  return { ok: true, text: parsed.text };
}

/**
 * The multipart body: `model` from `DEMO_STT_MODEL`, `response_format: json`, and the one
 * file. No `language`/`prompt`/`temperature` — nothing a visitor could steer. Exported so
 * `sim/tools/probe_demo_gateway.mjs` can post exactly this body to a real gateway.
 */
export function buildTranscribeForm(cfg, bytes, kind) {
  const form = new FormData();
  form.append("model", cfg.sttModel); // from DEMO_STT_MODEL. NEVER from the request.
  form.append("response_format", "json");
  form.append("file", new Blob([bytes], { type: kind.mime }), "utterance." + kind.ext);
  return form;
}

/** Markup, by header or by leading bytes. Loose on purpose: it only separates "a login
 *  page" from "an API error" for the operator. */
function looksLikeHtml(text, contentType) {
  if (/^\s*text\/html/i.test(String(contentType || ""))) return true;
  return /^\s*(?:<!doctype html|<html\b)/i.test(String(text || ""));
}
