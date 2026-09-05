/* functions/api/speech.js — POST /api/speech, the voice, and ONLY for words we wrote.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/speech` — the ticket
 * and why it exists), §2.2 (the gateway lies about its Content-Type), §4.1 (the caps),
 * §4.5 (the status table).
 *
 * =========================================================================== *
 * THERE IS NO TEXT FIELD ON THIS ROUTE. EVER.
 *
 * The request has exactly one key — `ticket` — and the text lives INSIDE the ticket's
 * signed payload. So the only text this deployment will ever synthesize is text this
 * deployment itself generated in the last `DEMO_TICKET_TTL_S` (60) seconds.
 *
 * That is what makes `/api/speech` STRUCTURALLY UNABLE to become a free text-to-speech
 * API, and it matters more than any counter: TTS is the most expensive per-request abuse
 * vector in the whole system — ~268 KB of egress and 1.7 s of gateway time for ONE short
 * sentence, linear in attacker-supplied characters (`docs/guides/litellm-tts-setup.md`:47,
 * spec §2.5). A cap would bound each call; a ticket removes the ability to make the call
 * at all without first paying for a chat turn. No store, no cache, no counter needed for
 * the property to hold — just a signature (`_lib/hmac.js`).
 *
 * The character cap is enforced TWICE, at minting (`chat.js` step 9) and at redemption
 * (step 4 below), so a ticket minted under a looser configuration cannot be redeemed under
 * a tighter one.
 * =========================================================================== *
 *
 * THE KEY NEVER LEAVES THIS PROCESS (C1, §4.2), exactly as in `chat.js`: read once as
 * `context.env.DEMO_GATEWAY_API_KEY` inside `_lib/env.js`, used only as an outbound
 * `Authorization` header, never in a body, a header, an error string or a log line. No
 * upstream status, body or header is forwarded. The gateway base URL is never echoed.
 *
 * ZERO UPSTREAM CALLS ON EVERY REFUSAL PATH: unconfigured, no TTS model, forbidden origin,
 * malformed body, forged ticket, expired ticket, over-length ticket text, replayed ticket,
 * rate-limited, over budget, at capacity. All return before the one `fetch()`.
 *
 * AND SINCE 2026-09-05, ZERO UPSTREAM CALLS ON A CACHE HIT TOO (`_lib/ttscache.js`, §4.8).
 * The audio for a given (gateway, model, voice, format, rate, exact text) is the same audio
 * every time, and making it is the most expensive thing here — 131 348 B and 1 091 ms for
 * one 30-character line, measured. So it is kept in `caches.default` and served back.
 * **The cache sits AFTER every cap on this route, never before one:** admission, the
 * ticket, the TTL and the `DEMO_MAX_TTS_CHARS` re-check all still decide first, so a hit
 * is a cheaper way to serve a request that was already going to be served — never a way to
 * serve one that was not. Every failure it can have — miss, stale, a `match` or `put` that
 * throws, rejects or hangs, no `caches` global, an entry that will not decode — falls
 * through to exactly the `callGateway` below. Nothing but a successful synthesis is ever
 * stored. `DEMO_TTS_CACHE=0` removes it entirely, with no cache call at all.
 *
 * What it is NOT: global. It is per-colo, so a colo that has not heard a line pays for it;
 * and the hit rate is NOT measured, because a preview is keyless and this route refuses
 * before it reaches the cache (the same limitation §4.6.1 recorded for the counter tier).
 *
 * MEASURED AGAINST THE REAL GATEWAY (`sim/tools/probe_demo_gateway.mjs`, 2026-09-02):
 * a 30-character line returned **131 348 B of RIFF/WAVE in 1 091 ms** — 22 050 Hz mono,
 * 131 304 B of PCM, 2.98 s of audio — labelled `audio/mpeg`, confirming §2.2's
 * Content-Type lie live. Two consequences worth knowing before touching a cap:
 *
 *   * that is ~4.4 KB of PCM per input character, so `DEMO_MAX_TTS_CHARS = 300` implies a
 *     worst case near **1.3 MB of PCM ⇒ ~1.75 MB of base64** in one JSON response body.
 *     §4.1's own estimate of "≈800 KB worst-case egress per call" is LOW by about 2x.
 *   * the same ratio puts a 300-character line near **11 s** of gateway time, against a
 *     `DEMO_SPEECH_TIMEOUT_MS` of 12 000. In practice replies are far shorter — the
 *     persona asks for "one or two short spoken sentences" and `max_tokens` is 160 — but
 *     anyone RAISING `DEMO_MAX_TTS_CHARS` must raise the timeout with it, or the demo
 *     starts timing out on its own longest lines.
 *
 * AND THE LAST RULE, which is the one that keeps a child from hearing static: **SNIFF THE
 * BYTES, NEVER THE CONTENT-TYPE** (`_lib/wav.js`, transcribed from `tts.py`:110-145). A
 * JSON body where audio was expected is `upstream_down`, never handed to a visitor as
 * noise — and, since 2026-09-03, so is EVERY other body that is not the format this route
 * asked for. `cfg.ttsFormat` is now passed to `pcmFromAudio`, because until then it was
 * not: under the shipped `DEMO_TTS_FORMAT=wav` default a 200 that was neither RIFF, JSON
 * nor HTML — a `text/plain` proxy error, an SSE `data: {"error":…}` frame, an mp3 — took
 * the headerless-PCM branch that is only correct under `DEMO_TTS_FORMAT=pcm`, and was
 * base64'd into `messages[0].payload.audio.buffer` and shipped at status 200 with
 * `reason: null` and `degraded: false`. That is both a body-disclosure hole and, with an
 * mp3, several seconds of full-scale static in a child's ear. `sim/test_demo_proxy.mjs`
 * §10c pins each shape, and its `assertClean` sweep now DECODES the buffer, which is why
 * the hole survived ~1000 sweeps that all reported clean: base64 defeats substring search.
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { admit, budgetState, loadOf, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { b64FromBytes, b64urlFromBytes, bytesFromB64url, verifyTicket } from "./_lib/hmac.js";
import { buildCloudTtsResponse, joinUrl, ttsMessage } from "./_lib/wire.js";
import { AudioBodyError, pcmFromAudio } from "./_lib/wav.js";
import { readCachedAudio, ttsCacheKey, ttsStore, writeCachedAudio } from "./_lib/ttscache.js";

/**
 * Best-effort single-redemption, per isolate.
 *
 * **This is NOT the anti-replay control and must never be described as one.** Like every
 * counter in this slice (§4.6) it is per-isolate, so a replay that lands on another
 * isolate is not seen. The property that actually holds is STRUCTURAL: the text is inside
 * the signature, so a ticket can only ever produce the one line we already wrote, and the
 * 60-second TTL bounds how long even that is possible. This set is a cheap extra that
 * stops the obvious loop (one ticket, a thousand redemptions) inside one isolate.
 *
 * Keyed on the ticket's CANONICAL BYTES (`replayKey`), not on the string the caller sent,
 * and bounded so a long-lived isolate cannot grow it without limit.
 */
const spent = new Set();
const SPENT_MAX = 2000;

/** Tests only. */
export function __resetSpent() {
  spent.clear();
}

/**
 * The canonical form of a ticket, for the `spent` set only — never for verification.
 *
 * **A base64url segment is not a canonical encoding of its bytes**, and the set used to key
 * on the raw string. An HMAC-SHA-256 is 32 bytes, which is 43 base64url characters, and 43
 * characters carry 258 bits — so the FINAL CHARACTER has two bits nothing reads. Four
 * distinct strings therefore decode to the same MAC, and `_lib/hmac.js::timingSafeEqual`
 * compares DECODED BYTES (:174-176), so all four verify. Measured against the real module
 * on 2026-09-03: one minted ticket, four spellings, four `spent` entries, four redemptions
 * off one paid chat turn per isolate. Re-encoding with `+`/`/`/`=` does NOT work — the
 * `/^[A-Za-z0-9_-]+$/` gate in `bytesFromB64url` (:86-89) refuses those outright — so the
 * bypass was exactly 4x, not unbounded.
 *
 * Decoding and re-encoding collapses those four onto one key. BOTH segments are
 * canonicalised, not just the MAC: the payload is signed and so cannot vary in practice,
 * but keying on the whole artefact means that stops being a fact this function relies on.
 *
 * Never throws: `bytesFromB64url` answers `null` on anything malformed (:86-89), and a
 * ticket that malformed has already failed `verifyTicket` above.
 */
function replayKey(ticket) {
  const parts = String(ticket || "").split(".");
  const canon = (s) => b64urlFromBytes(bytesFromB64url(s) || new Uint8Array(0));
  return canon(parts[1]) + "." + canon(parts[2]);
}

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // ---- 1. Configuration (C5). No variables at all => `gateway_not_configured`, no call.
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") return refusal(cfg, gate.reason, {});
  // A configured gateway with no TTS model is not a voice (§5): the page keeps its clips
  // and its browser voice, and says so. Same reason, so `mode.js` degrades identically.
  if (!cfg.voice) return refusal(cfg, "gateway_not_configured", {});

  // ---- 2. Admission: origin pin, per-IP windows, unit budget, capacity ceiling — and,
  // since 2026-09-03, a bounded wait behind that ceiling rather than an instant refusal
  // (`_lib/limits.js::admit`). Awaited; the `finally` below hands the slot on.
  const slot = await admit({ request, cfg, route: "speech" });
  if (!slot.ok) {
    return refusal(cfg, slot.reason, {
      retryAfterS: slot.retryAfterS,
      rateLimit: slot.rateLimit,
      load: slot.load,
    });
  }

  try {
    // ---- 3. The request. ONE key is read; everything else is dropped (§3.2).
    const parsed = await readJsonBody(request, cfg);
    if (!parsed.ok) return refusal(cfg, parsed.reason, { load: slot.load, rateLimit: slot.rateLimit });
    const ticket = typeof parsed.body.ticket === "string" ? parsed.body.ticket : "";
    if (!ticket) return refusal(cfg, "bad_ticket", { load: slot.load, rateLimit: slot.rateLimit });

    // ---- 4. The ticket. A forged signature, a malformed artefact and an expired one are
    // all `bad_ticket`, and none of them reaches the gateway. The signature is checked
    // BEFORE the payload is parsed (`_lib/hmac.js::verifyClaims`), so a forged blob never
    // reaches the JSON parser.
    const v = await verifyTicket(cfg, ticket);
    if (!v.ok) return refusal(cfg, "bad_ticket", { load: slot.load, rateLimit: slot.rateLimit });

    // The re-check §3.2 asks for. A `too_long` rather than a `bad_ticket` because the
    // ticket is perfectly valid — the CONFIGURATION got tighter, and the page deserves to
    // be told which of the two it is.
    if (v.claims.text.length > cfg.maxTtsChars) {
      return refusal(cfg, "too_long", { load: slot.load, rateLimit: slot.rateLimit });
    }

    const key = replayKey(ticket);
    if (spent.has(key)) return refusal(cfg, "bad_ticket", { load: slot.load, rateLimit: slot.rateLimit });
    if (spent.size >= SPENT_MAX) spent.clear(); // bounded; see the note on `spent`
    spent.add(key);

    // ---- 4b. THE AUDIO CACHE (`_lib/ttscache.js`, §4.8). AFTER every cap above, so a
    // cache hit can never be a way past `DEMO_MAX_TTS_CHARS`, the origin pin, the per-IP
    // windows, the unit budget or the ticket — a visitor still has to be admitted and
    // still has to hold a valid, unspent, in-date ticket to hear anything at all. All this
    // removes is the SECOND payment for a sentence we have already made.
    //
    // With `DEMO_TTS_CACHE=0`, or on any runtime without a `caches` global (bare `node`),
    // `ttsStore` returns null and the three lines below cost one comparison: no key is
    // derived, no cache is called, and step 5 is the function it has always been.
    const store = ttsStore(cfg);
    const cacheKey = store ? await ttsCacheKey(cfg, request, v.claims.text) : "";
    let audio = store ? await readCachedAudio(store, cfg, cacheKey) : null;

    // ---- 5. The one upstream call — NOT MADE AT ALL on a cache hit.
    if (!audio) {
      const upstream = await callGateway(cfg, v.claims.text);
      if (!upstream.ok) {
        return refusal(cfg, upstream.reason, {
          retryAfterS: upstream.retryAfterS,
          load: slot.load,
          rateLimit: slot.rateLimit,
        });
      }
      audio = { pcm: upstream.pcm, sampleRate: upstream.sampleRate, channels: upstream.channels };
      // ONLY A SUCCESSFUL SYNTHESIS IS EVER STORED. This line is unreachable from every
      // refusal, every upstream error, every undecodable body and every empty one: each of
      // those returned above. Never awaited for its result — a failed write is not the
      // visitor's problem, they already have their audio.
      if (store) await writeCachedAudio(store, cfg, cacheKey, audio);
    }

    // ---- 6. The `CloudTTSResponse` `audio.js` decodes itself, carrying the WAV header's
    // OWN rate and channels (§2.2) — that is how the payload stays truthful when the voice
    // changes under us.
    const wire = buildCloudTtsResponse({
      buffer: b64FromBytes(audio.pcm),
      channels: audio.channels,
      sampleRate: audio.sampleRate,
      eventId: v.claims.eventId,
      chunkNum: v.claims.chunkNum,
    });

    return respond(
      {
        ok: true,
        degraded: false,
        reason: null,
        mode: "live",
        load: slot.load,
        limits: publicLimits(cfg),
        messages: [ttsMessage(cfg.deviceId, wire)],
        speech: [],
        context: "",
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
 * The upstream call
 * ---------------------------------------------------------------------------- */

/**
 * `POST {base}/audio/speech`, with a server-built body exactly as in `chat.js`: the model,
 * the format and the voice come from configuration, and the ONLY visitor-influenced value
 * is `input` — which is the text we ourselves wrote and signed.
 *
 * `response_format` is `DEMO_TTS_FORMAT`, and §5 restricts that to `wav` or `pcm` because
 * those are the only two `audio.js` can decode (`_lib/env.js::TTS_FORMATS`, mirroring
 * `mqtt/config.py`:101). Asking a gateway for mp3 would produce a body this Function
 * refuses and a visitor hears nothing — so it is refused at configuration time instead.
 *
 * `DEMO_TTS_VOICE` is sent only when set: our gateway encodes the voice in the model id,
 * so empty is correct there (`config.py`:91-92) and an empty `voice` field upsets some
 * OpenAI-compatible servers.
 *
 * @returns {{ok:boolean, pcm?:Uint8Array, sampleRate?:number, channels?:number,
 *            reason?:string, retryAfterS?:number}}
 */
async function callGateway(cfg, text) {
  const body = buildSpeechBody(cfg, text);

  let res;
  try {
    noteUpstreamCall();
    res = await fetch(joinUrl(cfg.baseUrl, "audio/speech"), {
      method: "POST",
      // The ONLY place the credentials appear — the same one function `chat.js` uses, so
      // the two routes cannot drift on what they present (`_lib/env.js::upstreamHeaders`).
      headers: upstreamHeaders(cfg, "application/json"),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(cfg.speechTimeoutMs),
      // ---- REDIRECTS ARE NOT FOLLOWED, AND A 3xx IS A DOOR PROBLEM.
      //
      // `fetch`'s default is `follow`. This request carries the deployment's ONLY
      // credential on an `Authorization` header (plus the `CF-Access-*` pair when a
      // service token is configured), so following a 3xx means re-issuing it at whatever
      // host the `Location` names. The Fetch standard does strip `Authorization` across an
      // origin change — but a same-origin redirect keeps it, a 307/308 replays the BODY
      // with it, and none of that is a property this file should be depending on a runtime
      // to get right for it. `manual` removes the question: the 3xx is returned as-is and
      // is answered below, with nothing re-sent anywhere.
      //
      // And a 3xx from the gateway is not an ambiguous signal. **A tunnel that redirects
      // is a door problem, not a brain problem** — an Access login flow, a moved or
      // renamed endpoint, a `DEMO_GATEWAY_BASE_URL` configured as `http://` that the host
      // bounces to `https://`. Every one of those is fixed at the door, which is exactly
      // what `gateway_unreachable_or_gated` tells an operator (`_lib/envelope.js`), and
      // none is fixed by restarting a model server, which is what `upstream_down` would
      // have sent them off to do.
      redirect: "manual",
    });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut ? { ok: false, reason: "timeout" } : { ok: false, reason: "upstream_down" };
  }

  if (res.status === 429) return { ok: false, reason: "rate_limited", retryAfterS: retryAfterOf(res) };
  // A redirect, unfollowed. Before the `res.ok` test, which is false for a 3xx too.
  if (res.status >= 300 && res.status < 400) return { ok: false, reason: "gateway_unreachable_or_gated" };
  if (!res.ok) return { ok: false, reason: "upstream_down" }; // body deliberately unread

  let raw;
  try {
    raw = new Uint8Array(await res.arrayBuffer());
  } catch {
    return { ok: false, reason: "upstream_down" };
  }

  try {
    // SNIFF THE BYTES. `Content-Type` is not consulted anywhere in this file, on purpose:
    // our gateway labels a valid Piper WAV `audio/mpeg` (§2.2, observed live 2026-09-02).
    // ...AND TELL IT WHAT WE ASKED FOR. `cfg.ttsFormat` is the `response_format` on the
    // body two lines above; without it `wav.js` cannot tell "the headerless PCM I ordered"
    // from "a text/plain error the proxy invented", and until 2026-09-03 it took the
    // second for the first under the shipped `wav` default (see `_lib/wav.js`'s header).
    const out = pcmFromAudio(raw, { sampleRate: cfg.ttsSampleRate, channels: 1, format: cfg.ttsFormat });
    if (!out.pcm.length) return { ok: false, reason: "upstream_down" };
    return { ok: true, pcm: out.pcm, sampleRate: out.sampleRate, channels: out.channels };
  } catch (err) {
    // A JSON error body, an HTML page, a container we can name but not decode, a
    // non-RIFF body where `wav` was asked for, an unreadable WAV, or a bit depth
    // `audio.js` cannot decode. The page degrades and says the same thing to a VISITOR in every case, and
    // NOTHING from the error — which for a JSON body would contain a model id — reaches
    // the response. The one distinction that is drawn is for the OPERATOR: an HTML body
    // where audio was expected is a Cloudflare Access login page in front of the tunnel,
    // whose fix is a service token rather than a gateway restart
    // (`_lib/envelope.js::REASONS`, `_lib/env.js::ACCESS_VARS`).
    if (err instanceof AudioBodyError && err.kind === "html") {
      return { ok: false, reason: "gateway_unreachable_or_gated" };
    }
    return { ok: false, reason: "upstream_down" };
  }
}

/**
 * The `/audio/speech` request body, built from configuration plus the one signed string.
 *
 * EXPORTED so it can be exercised directly: `sim/tools/probe_demo_gateway.mjs` posts the
 * body THIS function builds to the real gateway, which is the only way to prove the shape
 * is accepted — Cloudflare Pages Functions do not run under a plain static server, so
 * "the route works" and "the body the route builds is accepted" are two different claims.
 */
export function buildSpeechBody(cfg, text) {
  const body = {
    model: cfg.ttsModel, // from DEMO_TTS_MODEL. NEVER from the request.
    input: String(text || ""),
    response_format: cfg.ttsFormat,
  };
  // ALWAYS SENT. The gateway REQUIRES `voice` and ignores its value — omitting it is an
  // HTTP 500 on every call (`_lib/env.js::voiceForModel` records how that was found, and
  // `mqtt/moxie_sdk/tts.py`:80-90 says it in its own docstring). `cfg.ttsVoice` is
  // `DEMO_TTS_VOICE` when set and derived from the model name otherwise, so it is
  // non-empty whenever a TTS model is configured — and this route only runs when one is.
  if (cfg.ttsVoice) body.voice = cfg.ttsVoice;
  return body;
}

function retryAfterOf(res) {
  const n = Number(res.headers.get("Retry-After"));
  if (Number.isFinite(n) && n > 0) return Math.min(300, Math.ceil(n));
  return 10;
}

/** §4/§7's envelope with §4.5's status and `Retry-After`. See `chat.js::refusal` for why
 *  `message` stays empty (the visitor-facing copy lives in `sim/web/mode.js`). */
function refusal(cfg, reason, extra) {
  const budget = budgetState(cfg);
  return respond(
    {
      ok: false,
      degraded: true,
      reason,
      retry_after_s: (extra && extra.retryAfterS) || (reason === "budget_exhausted" ? budget.retryAfterS : 0),
      mode: "degraded",
      load: (extra && extra.load) || loadOf(cfg, "speech"),
      limits: publicLimits(cfg),
      messages: [],
      speech: [],
      context: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { rateLimit: (extra && extra.rateLimit) || null },
  );
}
