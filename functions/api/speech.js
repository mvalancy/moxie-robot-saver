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
 * AND THE LAST RULE, which is the one that keeps a child from hearing static: **SNIFF THE
 * BYTES, NEVER THE CONTENT-TYPE** (`_lib/wav.js`, transcribed from `tts.py`:110-145). A
 * JSON body where audio was expected is `upstream_down`, never handed to a visitor as
 * noise.
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { admit, budgetState, loadOf, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { b64FromBytes, verifyTicket } from "./_lib/hmac.js";
import { buildCloudTtsResponse, joinUrl, ttsMessage } from "./_lib/wire.js";
import { AudioBodyError, pcmFromAudio } from "./_lib/wav.js";

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
 * Keyed on the MAC segment, not the whole ticket, and bounded so a long-lived isolate
 * cannot grow it without limit.
 */
const spent = new Set();
const SPENT_MAX = 2000;

/** Tests only. */
export function __resetSpent() {
  spent.clear();
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

  // ---- 2. Admission: origin pin, per-IP windows, unit budget, capacity ceiling.
  const slot = admit({ request, cfg, route: "speech" });
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

    const mac = ticket.split(".")[2] || "";
    if (spent.has(mac)) return refusal(cfg, "bad_ticket", { load: slot.load, rateLimit: slot.rateLimit });
    if (spent.size >= SPENT_MAX) spent.clear(); // bounded; see the note on `spent`
    spent.add(mac);

    // ---- 5. The one upstream call.
    const upstream = await callGateway(cfg, v.claims.text);
    if (!upstream.ok) {
      return refusal(cfg, upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // ---- 6. The `CloudTTSResponse` `audio.js` decodes itself, carrying the WAV header's
    // OWN rate and channels (§2.2) — that is how the payload stays truthful when the voice
    // changes under us.
    const wire = buildCloudTtsResponse({
      buffer: b64FromBytes(upstream.pcm),
      channels: upstream.channels,
      sampleRate: upstream.sampleRate,
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
  const body = {
    model: cfg.ttsModel, // from DEMO_TTS_MODEL. NEVER from the request.
    input: text,
    response_format: cfg.ttsFormat,
  };
  if (cfg.ttsVoice) body.voice = cfg.ttsVoice;

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
    });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut ? { ok: false, reason: "timeout" } : { ok: false, reason: "upstream_down" };
  }

  if (res.status === 429) return { ok: false, reason: "rate_limited", retryAfterS: retryAfterOf(res) };
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
    const out = pcmFromAudio(raw, { sampleRate: cfg.ttsSampleRate, channels: 1 });
    if (!out.pcm.length) return { ok: false, reason: "upstream_down" };
    return { ok: true, pcm: out.pcm, sampleRate: out.sampleRate, channels: out.channels };
  } catch (err) {
    // A JSON error body, an HTML page, an unreadable WAV, or a bit depth `audio.js` cannot
    // decode. The page degrades and says the same thing to a VISITOR in every case, and
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
