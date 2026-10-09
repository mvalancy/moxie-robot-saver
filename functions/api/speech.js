/* functions/api/speech.js — POST /api/speech, the voice, and ONLY for words we wrote.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (the ticket), §2.2 (the gateway
 * mislabels its Content-Type), §4.1 (caps), §4.5 (statuses), §4.8 (the audio cache).
 *
 * THERE IS NO TEXT FIELD ON THIS ROUTE. EVER. The one request key is `ticket`, and the text
 * lives inside its signed payload (`_lib/hmac.js`), so the only text this deployment will
 * synthesize is text it generated itself in the last `DEMO_TICKET_TTL_S` seconds. That makes
 * the route structurally unable to become a free TTS API — the most expensive per-request
 * abuse vector in the system. `DEMO_MAX_TTS_CHARS` is enforced at minting AND redemption,
 * so a ticket minted under a looser config cannot be redeemed under a tighter one.
 *
 * INVARIANTS
 *  - The key never leaves this process; no upstream status, body or header is forwarded.
 *  - Zero upstream calls on every refusal path, each of which refunds the units `admit()`
 *    charged. A forged ticket needs no bot token to be refused, so without the refund ~300
 *    of them would empty the shared hourly budget for free.
 *  - Zero upstream calls on a cache hit (`_lib/ttscache.js`). The cache sits AFTER every
 *    cap, so a hit is a cheaper way to serve a request that was already going to be served;
 *    every cache failure falls through to the gateway, and only a successful synthesis is
 *    stored. Per-colo, not global.
 *  - SNIFF THE BYTES, NEVER THE CONTENT-TYPE (`_lib/wav.js`), and tell the sniffer which
 *    format we asked for: a 200 that is not that format — a proxy's text/plain error, an
 *    SSE error frame, an mp3 — is `upstream_down`, never base64'd to a child as static.
 *
 * Sizing (measured): ~4.4 KB of PCM per input character and ~36 ms of gateway time, so
 * raising `DEMO_MAX_TTS_CHARS` above 300 needs `DEMO_SPEECH_TIMEOUT_MS` raised with it.
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { admit, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { b64FromBytes, b64urlFromBytes, bytesFromB64url, verifyTicket } from "./_lib/hmac.js";
import { buildCloudTtsResponse, joinUrl, ttsMessage } from "./_lib/wire.js";
import { AudioBodyError, pcmFromAudio } from "./_lib/wav.js";
import { readCachedAudio, ttsCacheKey, ttsStore, writeCachedAudio } from "./_lib/ttscache.js";
import { fetchFailure, limitedOrRedirected, refusal as refuse } from "./_lib/upstream.js";

/**
 * Best-effort single redemption, per isolate. NOT the anti-replay control — a replay on
 * another isolate is not seen. What actually holds is structural: the text is inside the
 * signature and the TTL bounds it. This only stops one ticket looping inside one isolate.
 * Bounded so a long-lived isolate cannot grow it without limit.
 */
const spent = new Set();
const SPENT_MAX = 2000;

/** Tests only. */
export function __resetSpent() {
  spent.clear();
}

/**
 * The canonical form of a ticket, for the `spent` set only — never for verification.
 * A 32-byte MAC is 43 base64url characters whose last character has two unread bits, so
 * four spellings decode to the same MAC and all verify; keying on the raw string allowed
 * four redemptions per ticket. Decoding and re-encoding both segments collapses them.
 * Never throws: a malformed segment decodes to `null` and has already failed verification.
 */
function replayKey(ticket) {
  const parts = String(ticket || "").split(".");
  const canon = (s) => b64urlFromBytes(bytesFromB64url(s) || new Uint8Array(0));
  return canon(parts[1]) + "." + canon(parts[2]);
}

const refusal = (cfg, reason, extra) => refuse(cfg, "speech", reason, extra);

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // 1. Configuration. A configured gateway with no TTS model is not a voice (§5).
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") return refusal(cfg, gate.reason, {});
  if (!cfg.voice) return refusal(cfg, "gateway_not_configured", {});

  // 2. Admission; the `finally` below hands the slot on.
  const slot = await admit({ request, cfg, route: "speech" });
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

    // 3. The request. ONE key is read.
    const parsed = await readJsonBody(request, cfg);
    if (!parsed.ok) return spentNothing(parsed.reason);
    const ticket = typeof parsed.body.ticket === "string" ? parsed.body.ticket : "";
    if (!ticket) return spentNothing("bad_ticket");

    // 4. The ticket: forged, malformed and expired are all `bad_ticket`. The signature is
    //    checked before the payload is parsed.
    const v = await verifyTicket(cfg, ticket);
    if (!v.ok) return spentNothing("bad_ticket");

    // The redemption-time cap. `too_long`, not `bad_ticket`: the ticket is valid, the
    // configuration got tighter.
    if (v.claims.text.length > cfg.maxTtsChars) return spentNothing("too_long");

    const key = replayKey(ticket);
    if (spent.has(key)) return spentNothing("bad_ticket");
    if (spent.size >= SPENT_MAX) spent.clear();
    spent.add(key);

    // 4b. The audio cache, after every cap above. With `DEMO_TTS_CACHE=0` or no `caches`
    //     global, `ttsStore` is null and no key is derived and no cache is called.
    const store = ttsStore(cfg);
    const cacheKey = store ? await ttsCacheKey(cfg, request, v.claims.text) : "";
    let audio = store ? await readCachedAudio(store, cfg, cacheKey) : null;

    // 5. The one upstream call — not made at all on a cache hit.
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
      // Only a successful synthesis is ever stored; every failure returned above. The
      // visitor does not wait for the write (§4.8): it is handed to `waitUntil` and finishes
      // after the response. `writeCachedAudio` never rejects and its own deadline bounds it.
      // Bare node (the hermetic suite, the `--inproc` tools) has no `waitUntil`: awaited there.
      if (store) {
        const write = writeCachedAudio(store, cfg, cacheKey, audio);
        if (typeof context.waitUntil === "function") context.waitUntil(write);
        else await write;
      }
    }

    // 6. The `CloudTTSResponse` `voice/cloud.js` decodes, carrying the WAV header's OWN rate and
    //    channels so the payload stays truthful when the voice changes.
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

/**
 * `POST {base}/audio/speech` with a server-built body; the only visitor-influenced value is
 * `input`, which is text we wrote and signed.
 *
 * @returns {Promise<{ok:boolean, pcm?:Uint8Array, sampleRate?:number, channels?:number,
 *            reason?:string, retryAfterS?:number}>}
 */
async function callGateway(cfg, text) {
  const body = buildSpeechBody(cfg, text);

  let res;
  try {
    noteUpstreamCall();
    res = await fetch(joinUrl(cfg.baseUrl, "audio/speech"), {
      method: "POST",
      headers: upstreamHeaders(cfg, "application/json"),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(cfg.speechTimeoutMs),
      redirect: "manual", // a 3xx is a door problem; see `_lib/upstream.js`
    });
  } catch (err) {
    return fetchFailure(err);
  }

  const early = limitedOrRedirected(res);
  if (early) return early;
  if (!res.ok) return { ok: false, reason: "upstream_down" }; // body deliberately unread

  let raw;
  try {
    raw = new Uint8Array(await res.arrayBuffer());
  } catch {
    return { ok: false, reason: "upstream_down" };
  }

  try {
    // Sniff the bytes, and say which format we asked for (see the header).
    const out = pcmFromAudio(raw, { sampleRate: cfg.ttsSampleRate, channels: 1, format: cfg.ttsFormat });
    if (!out.pcm.length) return { ok: false, reason: "upstream_down" };
    return { ok: true, pcm: out.pcm, sampleRate: out.sampleRate, channels: out.channels };
  } catch (err) {
    // Nothing from the error reaches the response. The one distinction, for the OPERATOR:
    // an HTML body is a Cloudflare Access login page, fixed by a service token.
    if (err instanceof AudioBodyError && err.kind === "html") {
      return { ok: false, reason: "gateway_unreachable_or_gated" };
    }
    return { ok: false, reason: "upstream_down" };
  }
}

/**
 * The `/audio/speech` request body, from configuration plus the one signed string.
 * Exported so `sim/tools/probe_demo_gateway.mjs` can post exactly this body to a real
 * gateway. `response_format` is `wav` or `pcm` only — the two `_lib/wav.js::pcmFromAudio`
 * turns into the raw PCM `voice/cloud.js` plays.
 */
export function buildSpeechBody(cfg, text) {
  const body = {
    model: cfg.ttsModel, // from DEMO_TTS_MODEL. NEVER from the request.
    input: String(text || ""),
    response_format: cfg.ttsFormat,
  };
  // The gateway REQUIRES `voice` (omitting it is a 500) and ignores its value;
  // `cfg.ttsVoice` is derived from the model name when unset, so it is present whenever a
  // TTS model is configured.
  if (cfg.ttsVoice) body.voice = cfg.ttsVoice;
  return body;
}
