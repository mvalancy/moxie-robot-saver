/* functions/api/transcribe.js — POST /api/transcribe, the ears.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/transcribe` — the ears
 * (P1)), §4.1 (the caps, and the paragraph about why a byte cap is not a duration cap),
 * §4.5 (the status table), §6 (the fallback), §9's P1 paragraph, §10 assumptions 15/16.
 *
 * This is the third and last leg of "a stranger opens the production domain and TALKS to
 * Moxie": `/api/chat` is the brain, `/api/speech` is the voice, and this is what lets a
 * visitor SPEAK instead of type. Today `sim/web/mic.js` posts to a local sidecar on port
 * 8082 that a hosted visitor has never run, every request fails, and the page quietly
 * answers with a scripted child line — honest, but never actually listening.
 *
 * ===========================================================================
 * WHAT MAKES THE EARS SAFE, in the order the code checks it.
 *
 *  1. `DEMO_STT_MODEL` UNSET ⇒ THERE ARE NO EARS, AND NO CALL IS MADE. `/api/health`
 *     already reports `ears: false` from the same config (`_lib/env.js`), so the page
 *     never offers a microphone it cannot serve, and this route agrees with the probe
 *     rather than second-guessing it.
 *  2. THE FLOOR IS FREE. Under `DEMO_MIN_AUDIO_BYTES` (2 000) the route returns
 *     `too_short` and **the gateway is never touched** — the rule transcribed from
 *     `mqtt/moxie_sdk/stt.py`:194-197, *"no audio → no request, no cost, no latency"*.
 *     A hosted demo gets a lot of accidental 300-byte clips.
 *  3. THE CEILING IS CHECKED BEFORE THE BODY IS READ. `DEMO_MAX_AUDIO_BYTES` (500 000) is
 *     compared against the declared `Content-Length` first (`_lib/limits.js`).
 *  4. THE BYTES ARE SNIFFED, NOT BELIEVED. The visitor's `Content-Type` is a claim; the
 *     magic number is evidence. Anything that is not a recognised audio container is
 *     `bad_request` with **zero** upstream calls — 500 KB of JPEG costs nothing.
 *  5. THE MODEL IS SERVER-FIXED. Nothing from the request reaches the upstream body except
 *     the audio itself: no `model`, no `language`, no `prompt`, no `temperature`,
 *     no `response_format`. §4.1's single highest-value control, applied here too.
 *  6. THE DURATION CEILING IS NOT HERE, AND CANNOT BE. §4.1 is explicit that 500 KB is
 *     ~15 s of 16 kHz PCM but **minutes** of webm/Opus, which is what `MediaRecorder`
 *     actually produces. A Function only ever sees a finished upload, so the honest
 *     ceiling on STT duration is `DEMO_MAX_RECORD_MS` — published in this route's own
 *     `limits` and enforced by `sim/web/mic.js`'s hard stop. **The byte cap alone is not
 *     a cost ceiling for the ears, and this comment exists so nobody later thinks it is.**
 *  7. NOTHING IS STORED, LOGGED OR CACHED. §9's P1 line says it for the caching idea and
 *     it is worth repeating here: **do not cache STT — that is a privacy problem, not a
 *     saving.** A child's voice arrives, becomes text, and is forgotten. There is no
 *     store to write to anyway (§2.6).
 * ===========================================================================
 *
 * THE KEY NEVER LEAVES THIS PROCESS (C1, §4.2), exactly as in `chat.js` and `speech.js`:
 * read once as `context.env.DEMO_GATEWAY_API_KEY` inside `_lib/env.js`, used only as an
 * outbound `Authorization` header, never in a body, a header, an error string or a log
 * line. No upstream status, body or header is forwarded — an upstream error body from an
 * OpenAI-compatible gateway names the model and often the key prefix.
 *
 * ZERO UPSTREAM CALLS ON EVERY REFUSAL PATH: unconfigured, no STT model, forbidden origin,
 * rate-limited, over budget, at capacity, over the byte cap, under the byte floor, and an
 * unrecognised container. All return before the one `fetch()`.
 *
 * ===========================================================================
 * TWO DELIBERATE DEVIATIONS FROM §3.2, both documented at their site.
 *
 * (1) **The response is the HOUSE ENVELOPE with a `transcript` field, not the bare
 *     `DeepgramResponse` §3.2 sketched.** §3.2's appeal was that `mic.js`:44-45 already
 *     parses `{channel:{alternatives:[{transcript}]}}` — but that shape carries no
 *     `reason`, no `mode`, no `retry_after_s` and no `limits`. A rate-limited visitor
 *     would be indistinguishable from a deployment with no ears, an over-budget one from
 *     a dead gateway, and `mic.js` could not do the one thing §6 requires of it: degrade
 *     HONESTLY and say which. So this route answers the same envelope as the other three,
 *     `mic.js` reads `transcript` from it, and `mic.js` KEEPS its Deepgram parse for the
 *     local sidecar (`sim/stt/server.py`:69-70), which is untouched. One extra branch in
 *     the client buys the whole §4.5 status table.
 *
 * (2) **THE GATEWAY DOES NOT ACCEPT webm/Opus, AND THAT CHANGED THIS ROUTE.** §10
 *     assumption 15, settled live on 2026-09-03: a 16 kHz mono RIFF/WAVE transcribes
 *     word-perfect in 2.58 s, while the SAME UTTERANCE as webm/Opus, ogg/Opus and mp4/AAC
 *     all answer **HTTP 500**. Since 500 maps to `upstream_down` — a 503, which degrades
 *     the whole page — forwarding a browser's default recording would have taken the brain
 *     and the voice down every time someone pressed the microphone, after paying 1.6-4.3 s
 *     for the privilege. So the route carries a container allowlist (`DEMO_STT_FORMATS`,
 *     default `wav`, step 4b below) and refuses the rest for free and per-turn; and
 *     `sim/web/mic.js` now ENCODES 16 kHz mono WAV in the browser rather than shipping
 *     whatever `MediaRecorder` felt like producing. The full evidence table is in
 *     `_lib/env.js::sttFormats`.
 *
 * (3) **An upstream 4xx about the PAYLOAD answers `bad_request` (400), not
 *     `upstream_down` (503).** The distinction is not pedantry: `mode.js` degrades the
 *     WHOLE PAGE off a 503 (§6.3, `live --> degraded`), so a gateway that rejects one
 *     audio container would take the brain and the voice down with it — while §4.5's
 *     `bad_request` row says explicitly *"does not change mode"*. A gateway refusing our
 *     bytes is a fact about the bytes; a gateway that is down is a fact about the gateway.
 *     `reasonForUpstreamStatus` is the whole table, and 401/403 stay `upstream_down`
 *     because a revoked key IS an operator problem. **This is also the shape of the answer
 *     if assumption 15 turns out badly:** a gateway that refuses webm/Opus produces a
 *     degradable per-turn reason and a scripted line, never a 502 and never a dead page.
 * ===========================================================================
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { admit, budgetState, loadOf, noteUpstreamCall, readAudioBody } from "./_lib/limits.js";
import { joinUrl } from "./_lib/wire.js";

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // ---- 1. Configuration (C5). No variables at all => `gateway_not_configured`, no call.
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") return refusal(cfg, "gateway_not_configured", {});
  // A configured gateway with no STT model is not a pair of ears (§5). Same reason as the
  // voice's equivalent, so `mode.js` degrades identically — and `/api/health` has already
  // told the page `ears: false`, so a well-behaved page never gets here at all. This is
  // the belt to that braces: `ears` is derived from the same `cfg`, so the probe and the
  // route CANNOT disagree.
  if (!cfg.ears) return refusal(cfg, "gateway_not_configured", {});

  // ---- 2. Admission: origin pin, per-IP windows (10/min, 60/hour), unit budget,
  // capacity ceiling. Same order and same helper as the other two routes, so no route can
  // spend a unit before checking the pin (`_lib/limits.js::admit`).
  const slot = admit({ request, cfg, route: "transcribe" });
  if (!slot.ok) {
    return refusal(cfg, slot.reason, {
      retryAfterS: slot.retryAfterS,
      rateLimit: slot.rateLimit,
      load: slot.load,
    });
  }

  try {
    // ---- 3. The body: raw audio bytes, bounded at both ends. `too_short` is the
    // no-cost floor and is the most common refusal a real demo will serve.
    const body = await readAudioBody(request, cfg);
    if (!body.ok) return refusal(cfg, body.reason, { load: slot.load, rateLimit: slot.rateLimit });

    // ---- 4. What IS this? Sniffed from the bytes, with the declared type as a fallback
    // and an allowlist as the answer. An unrecognised body never becomes a paid request.
    const kind = audioKind(body.bytes, request.headers.get("Content-Type"));
    if (!kind) return refusal(cfg, "bad_request", { load: slot.load, rateLimit: slot.rateLimit });

    // ---- 4b. …and is it a container THIS GATEWAY takes? `DEMO_STT_FORMATS` defaults to
    // `wav` alone because that is what was measured (see `_lib/env.js::sttFormats` for the
    // four-container probe of 2026-09-03). This check is free, per-turn, and — crucially —
    // keeps a rejected container from becoming an upstream **500**, which would map to
    // `upstream_down` and degrade the brain and the voice along with the ears.
    if (!cfg.sttFormats.includes(kind.ext)) {
      return refusal(cfg, "bad_request", { load: slot.load, rateLimit: slot.rateLimit });
    }

    // ---- 5. The one upstream call.
    const upstream = await callGateway(cfg, body.bytes, kind);
    if (!upstream.ok) {
      return refusal(cfg, upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // ---- 6. The transcript. An EMPTY one is a success, not an error: the gateway heard
    // silence, `mic.js` says "(nothing heard)" exactly as it does today, and nothing is
    // published to the bus. Turning silence into a 4xx would make the page shout at a
    // visitor who simply did not speak.
    const clean = cleanTranscript(upstream.text, cfg.maxInputChars);
    return respond(
      {
        ok: true,
        degraded: false,
        reason: null,
        // The one place `message` is used on a success: the visitor is entitled to know
        // that what comes back is not all of what they said. It is scrubbed of URLs and
        // key-shaped tokens by `envelope.js::sanitizeMessage` like every other message.
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

/* ---------------------------------------------------------------------------- *
 * What kind of audio is this?
 * ---------------------------------------------------------------------------- */

/** The containers this route will forward, by magic number. Every one of these is
 *  something a browser's `MediaRecorder` or the local sidecar can actually produce, plus
 *  wav for a hand-made control clip. The `ext` matters: an OpenAI-compatible
 *  `/audio/transcriptions` decides how to decode largely from the FILENAME, which is why
 *  `mqtt/moxie_sdk/stt.py`:254 sends `("utterance.wav", …)` rather than a bare stream. */
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
  "video/webm": "webm", // what Chrome labels a `MediaRecorder` blob on some versions
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
 * Identify the container. **Sniff the bytes, never the Content-Type** — the same rule
 * `_lib/wav.js` applies to the gateway's replies (`mqtt/moxie_sdk/tts.py`:110-145), and it
 * matters even more here because this Content-Type comes from a VISITOR. `mic.js`:77 only
 * ever falls back to the *string* `"audio/webm"`; whether that string describes the blob
 * is not something the browser guarantees (§10 assumption 16).
 *
 * The declared type is consulted only as a second opinion, and only against the same
 * allowlist. Everything else — JSON, HTML, an image, raw headerless PCM — is refused, and
 * that refusal is free.
 *
 * EXPORTED so `sim/test_demo_ears.mjs` and `sim/tools/probe_demo_gateway.mjs` can exercise
 * the real classifier rather than a copy of it.
 *
 * @param {Uint8Array} b
 * @param {string|null} declaredType
 * @returns {{ext:string, mime:string, sniffed:boolean}|null}
 */
export function audioKind(b, declaredType) {
  const bytes = b || new Uint8Array(0);
  let id = null;
  if (bytes.length >= 12) {
    // EBML — Matroska and therefore webm, which is what Chrome and Firefox record.
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

/* ---------------------------------------------------------------------------- *
 * The transcript
 * ---------------------------------------------------------------------------- */

/**
 * The visitor's own words, on their way back to the visitor's own browser — and then
 * straight into `/api/chat` as the next turn. So it is bounded and stripped of control
 * characters here rather than trusted downstream.
 *
 * TRUNCATED, NOT REFUSED, and this is the one place that rule differs from §4.1's
 * treatment of typed input. A typed sentence over `DEMO_MAX_INPUT_CHARS` is `too_long`
 * because the visitor can see it and shorten it. A SPOKEN one cannot be shortened after
 * the fact, and refusing the whole utterance because an ASR produced 501 characters would
 * throw away everything the child said. With `DEMO_MAX_RECORD_MS` at 15 s this is a
 * defensive edge (15 s of speech is ~40 words), and the visitor is told via `message`.
 */
export function cleanTranscript(text, maxChars) {
  // eslint-disable-next-line no-control-regex
  const flat = String(text === undefined || text === null ? "" : text)
    // C0 and C1 control characters: a JSON string carries them through happily, and a
    // transcript row would then render them as nothing at all. Written as escapes so the
    // source file itself stays plain text.
    .replace(/[\u0000-\u001F\u007F-\u009F]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const max = Number.isFinite(Number(maxChars)) && Number(maxChars) > 0 ? Number(maxChars) : 500;
  return flat.length > max ? { text: flat.slice(0, max).trim(), truncated: true } : { text: flat, truncated: false };
}

/* ---------------------------------------------------------------------------- *
 * The upstream call
 * ---------------------------------------------------------------------------- */

/** An upstream reply larger than this is not a transcript, and reading all of it into a
 *  string is the only harm it could do us. A whisper `{"text": …}` for a 15-second clip is
 *  a few hundred bytes. */
const MAX_UPSTREAM_REPLY_BYTES = 1000000;

/**
 * §4.5's status table, for an upstream status — and deviation (2) of the header.
 *
 * The question this answers is not "what went wrong" but **"whose problem is it, and
 * should the whole page degrade?"** `mode.js` takes a 503 as evidence the deployment is
 * unhealthy and degrades the brain and the voice with it (§6.3); a 400 is per-turn and
 * changes nothing (§4.5: *"does not change mode"*).
 *
 *   429            → `rate_limited`   the gateway's own limiter; the caller adds Retry-After
 *   413            → `too_long`       our cap was looser than the gateway's
 *   401 / 403 / 407 → `upstream_down` a revoked or unauthorised key: an OPERATOR problem
 *   other 4xx      → `bad_request`    the gateway refused THESE BYTES (400, 415, 422 …)
 *   5xx and the rest → `upstream_down`
 *
 * The `other 4xx` row is the one that matters for §10 assumption 15. If a gateway rejects
 * webm/Opus it does so with a 400 or a 415, and this table turns that into one refused
 * turn with a scripted answer instead of a page that declares itself broken.
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
 * `POST {base}/audio/transcriptions` as a multipart upload, with a SERVER-FIXED model.
 *
 * The body is built here and never forwarded: `model` comes from `DEMO_STT_MODEL`, and the
 * only visitor-supplied part is the file itself. No `language`, no `prompt`, no
 * `temperature`, no `timestamp_granularities` — every one of those is a parameter a
 * visitor could otherwise steer, and none of them is needed to hear a child say hello.
 *
 * `response_format` is `json`, which is what `mqtt/moxie_sdk/stt.py`:256 asks for and what
 * `docs/guides/litellm-stt-setup.md` records the gateway answering: `{"text": …}`.
 *
 * @returns {{ok:boolean, text?:string, reason?:string, retryAfterS?:number}}
 */
async function callGateway(cfg, bytes, kind) {
  const form = buildTranscribeForm(cfg, bytes, kind);

  // ONE credential function for all three routes (`_lib/env.js::upstreamHeaders`), so they
  // cannot drift on what they present — but the Content-Type it sets has to GO. `fetch`
  // generates the multipart boundary itself when the body is a FormData, and a
  // hand-written `Content-Type` would override it with one that names no boundary, which
  // an upstream reads as a malformed body. This is the one route where that applies.
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
    });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut ? { ok: false, reason: "timeout" } : { ok: false, reason: "upstream_down" };
  }

  // The gateway's own limiter, before anything else: a 429 is a 429 whatever body it
  // carries, and it is the one upstream status with a number worth passing on.
  if (res.status === 429) return { ok: false, reason: "rate_limited", retryAfterS: retryAfterOf(res) };

  let text;
  try {
    text = await res.text();
  } catch {
    return { ok: false, reason: "upstream_down" };
  }
  if (text.length > MAX_UPSTREAM_REPLY_BYTES) return { ok: false, reason: "upstream_down" };

  // An HTML body where JSON was expected is a **Cloudflare Access login page** in front of
  // the tunnel — famously served with a 200 (`_lib/env.js::ACCESS_VARS`). Its fix is a
  // service token, not a gateway restart, so it keeps its own reason. Checked before the
  // status table because Access answers 200, 302 and 403 alike with the same page.
  if (looksLikeHtml(text, res.headers.get("Content-Type"))) {
    return { ok: false, reason: "gateway_unreachable_or_gated" };
  }

  // The body is deliberately NOT read for a message here: an OpenAI-compatible error names
  // the model (`docs/guides/litellm-stt-setup.md`:  `Invalid model name passed in model=…`)
  // and sometimes a key prefix. It classifies the failure and is then dropped on the floor.
  if (!res.ok) return { ok: false, reason: reasonForUpstreamStatus(res.status) };

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, reason: "upstream_down" };
  }
  // `{"text": "…"}` — the shape `stt.py::transcript_text` accepts and the one the gateway
  // was measured returning. A 200 with no `text` is a gateway that answered something else
  // entirely, which is `upstream_down`, not an empty transcript: silence must not be
  // indistinguishable from a broken endpoint.
  if (!parsed || typeof parsed !== "object" || typeof parsed.text !== "string") {
    return { ok: false, reason: "upstream_down" };
  }
  return { ok: true, text: parsed.text };
}

/**
 * The multipart body, built from configuration plus the one file.
 *
 * EXPORTED for the same reason `speech.js::buildSpeechBody` is: `sim/tools/probe_demo_gateway.mjs`
 * posts the body THIS function builds to a real gateway, because "the route works against a
 * stub" and "the body the route builds is accepted upstream" are two different claims — and
 * P0-b learned that the hard way when `/audio/speech` answered 500 to a body missing a
 * `voice` field no hermetic test required.
 */
export function buildTranscribeForm(cfg, bytes, kind) {
  const form = new FormData();
  form.append("model", cfg.sttModel); // from DEMO_STT_MODEL. NEVER from the request.
  form.append("response_format", "json");
  form.append("file", new Blob([bytes], { type: kind.mime }), "utterance." + kind.ext);
  return form;
}

/** A body that starts with markup, or says it is markup. Cheap and deliberately loose:
 *  its only job is to separate "a login page" from "an API error" for the OPERATOR. */
function looksLikeHtml(text, contentType) {
  if (/^\s*text\/html/i.test(String(contentType || ""))) return true;
  return /^\s*(?:<!doctype html|<html\b)/i.test(String(text || ""));
}

function retryAfterOf(res) {
  const n = Number(res.headers.get("Retry-After"));
  if (Number.isFinite(n) && n > 0) return Math.min(300, Math.ceil(n));
  return 10;
}

/** §4/§7's envelope with §4.5's status and `Retry-After`. `message` stays empty for the
 *  same reason as in `chat.js` and `speech.js`: the visitor-facing copy lives in
 *  `sim/web/mode.js`, next to the badge it paints, so it is honest in `offline` too —
 *  where there is no server to send a string. */
function refusal(cfg, reason, extra) {
  const budget = budgetState(cfg);
  return respond(
    {
      ok: false,
      degraded: true,
      reason,
      retry_after_s: (extra && extra.retryAfterS) || (reason === "budget_exhausted" ? budget.retryAfterS : 0),
      mode: "degraded",
      load: (extra && extra.load) || loadOf(cfg, "transcribe"),
      limits: publicLimits(cfg),
      messages: [],
      speech: [],
      context: "",
      transcript: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { rateLimit: (extra && extra.rateLimit) || null },
  );
}
