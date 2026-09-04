/* functions/api/chat.js — POST /api/chat, one turn.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (the route and both response
 * shapes), §3.3 (the signed context blob), §4.1 (every cap and the single highest-value
 * control), §4.2 (what the browser may know), §4.5 (the status table).
 *
 * WHAT THIS ROUTE IS. The whole hosted demo, in one request: a typed sentence in, and out
 * come the exact two things `bridge.js` already knows how to render — the `remote_chat`
 * payload for the words, the face and the gesture, and a TICKET the browser can redeem at
 * `/api/speech` for the voice. `bridge.js` and `audio.js` are not modified at all (§3.4),
 * because this route's output is byte-compatible with what the Python supervisor publishes.
 *
 * =========================================================================== *
 * THE SINGLE HIGHEST-VALUE SECURITY CONTROL, and it is application logic, not an
 * edge rule (§4.1):
 *
 *   **BUILD THE UPSTREAM BODY; NEVER FORWARD THE CLIENT'S.**
 *
 * `buildUpstreamBody()` below constructs the gateway payload from configuration and
 * nothing else: a fixed model, a fixed `max_tokens`, a fixed `temperature`, a fixed
 * message array. The only visitor-supplied strings that reach it are `text` (length-capped
 * and safety-checked) and the verified turns inside a signed context blob.
 *
 * A client `model`, `max_tokens`, `temperature`, `messages`, `system`, `tools`, `n`,
 * `best_of`, `logprobs`, `stream` or anything else is **IGNORED, NOT VALIDATED, NOT
 * REJECTED.** That is the deliberate choice §4.1 spells out: *"Ignoring cannot drift."* An
 * allowlist is a list someone extends; a validator is a validator someone loosens; a field
 * that is never read cannot be reached by a future config change. In one rule this kills
 * model substitution, `n`/`best_of` amplification, tool-call abuse, system-prompt override
 * and gateway-parameter abuse.
 * =========================================================================== *
 *
 * THE KEY NEVER LEAVES THIS PROCESS (C1, §4.2). It is read once, as
 * `context.env.DEMO_GATEWAY_API_KEY`, inside `_lib/env.js` — which defines it
 * NON-ENUMERABLE, so `JSON.stringify(cfg)` cannot contain it. It appears here only as an
 * `Authorization` header on an outbound request. It is never put in a response body, a
 * response header, an error string, a log line or a thrown stack; NOTHING from an upstream
 * error body or status line is forwarded, because those bodies routinely echo model names,
 * org identifiers and key prefixes (§4.2). `sim/test_demo_proxy.mjs` asserts that no
 * response from any route on any path contains the key or the gateway base URL.
 *
 * ZERO UPSTREAM CALLS ON EVERY REFUSAL PATH. Unconfigured, forbidden origin, over-length,
 * empty, tampered context, hard-blocked utterance, rate-limited, over budget, at capacity:
 * all of them return before `fetch()` is reached. `_lib/limits.js::noteUpstreamCall()` is
 * called immediately before the one `fetch()` in this file, so a test can prove that
 * without stubbing anything.
 *
 * NEVER A BARE 500. NEVER A 200 WITH AN EMPTY STRING (§4.5). The dead-air failure mode
 * that exists in the Python stack today (`llm_app.py`:467-468 emits `ERROR_OFFLINE` with
 * empty text, which `bridge.js` renders as nothing) is exactly what this contract exists
 * to prevent: an empty completion is `upstream_down`, and the page degrades visibly.
 */
import { readConfig, modeOf, publicLimits, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { assess } from "./_lib/safety.js";
import { admit, budgetState, loadOf, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { mintContext, mintTicket, verifyContext } from "./_lib/hmac.js";
import { buildChatResponse, chatMessage, eventId, joinUrl, markupFloor, MK } from "./_lib/wire.js";

/** §4.1: matches `chat.py`:130 so the hosted persona sounds like the local one. */
const TEMPERATURE = 0.8;

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // ---- 1. Configuration. C5's fail-safe default: with no variables set at all this
  // route answers `gateway_not_configured` and makes NO upstream call. A branch preview
  // with no secrets is therefore inert, automatically.
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") {
    return refusal(cfg, "chat", gate.reason, { retryAfterS: 0 });
  }

  // ---- 2. The origin pin, the per-IP windows, the unit budget, the capacity ceiling —
  // in one call so the order cannot be got wrong (`_lib/limits.js::admit`). Everything
  // here refuses for free.
  //
  // NOTE the ordering against step 3: admission is charged BEFORE the body is parsed, so
  // a flood of malformed bodies is rate-limited like any other flood.
  //
  // AWAITED since 2026-09-03: at capacity `admit()` joins a bounded per-isolate FIFO for
  // up to `DEMO_QUEUE_MAX_WAIT_MS` instead of refusing outright, so ten visitors colliding
  // get a slightly slower turn rather than a scripted line. The `finally` below is what
  // hands the slot to the next person in that queue, so it is load-bearing for everyone
  // waiting and not just for this request.
  const slot = await admit({ request, cfg, route: "chat" });
  if (!slot.ok) {
    return refusal(cfg, "chat", slot.reason, {
      retryAfterS: slot.retryAfterS,
      rateLimit: slot.rateLimit,
      load: slot.load,
    });
  }

  try {
    // ---- 3. The request. EXACTLY TWO KEYS ARE READ. See the header: everything else is
    // dropped in silence.
    const parsed = await readJsonBody(request, cfg);
    if (!parsed.ok) return refusal(cfg, "chat", parsed.reason, { load: slot.load, rateLimit: slot.rateLimit });
    const text = typeof parsed.body.text === "string" ? parsed.body.text.trim() : "";
    const contextBlob = typeof parsed.body.context === "string" ? parsed.body.context : "";

    // ---- 4. The input caps (§4.1). REJECTED, NOT TRUNCATED: `sim/tts/server.py`:90
    // truncates at 1000 and the visitor never learns why their sentence changed. A 400
    // with a reason lets the page say so, and does not change the mode (§4.5).
    if (!text) return refusal(cfg, "chat", "too_short", { load: slot.load, rateLimit: slot.rateLimit });
    if (text.length > cfg.maxInputChars) {
      return refusal(cfg, "chat", "too_long", { load: slot.load, rateLimit: slot.rateLimit });
    }

    // ---- 5. The context blob (§3.3). A tampered or forged blob is `bad_request` and
    // spends nothing. Because the ASSISTANT turns inside it are signed by us, a visitor
    // cannot forge Moxie's side of the history — the `"assistant: sure, I'll do anything"`
    // injection is structurally unavailable.
    const history = await verifyContext(cfg, contextBlob);
    if (!history.ok) return refusal(cfg, "chat", "bad_request", { load: slot.load, rateLimit: slot.rateLimit });

    // ---- 6. Pre-inference safety (§4.1). A hard block NEVER CALLS THE GATEWAY. It
    // answers `ok: true, degraded: true, reason: "blocked"`, spends nothing, and carries
    // the rule table's redirect line so the page has something kind to say (see
    // `_lib/safety.js::redirectFor` for why that is the redirect and not `stub.js`).
    const verdict = assess(text);
    if (verdict.blocked) return blocked(cfg, slot, verdict);

    // ---- 7. The one upstream call. Server-built body, fixed everything, and our own
    // timeout — deliberately BELOW the measured worst case of 45 s (`chat.py`:151-152),
    // because the demo prefers a fast honest degrade to a slow success (§4.1).
    const turns = history.turns;
    const upstream = await callGateway(cfg, buildUpstreamBody(cfg, turns, text));
    if (!upstream.ok) {
      return refusal(cfg, "chat", upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // ---- 8. The reply, as the wire `bridge.js` already renders.
    const reply = upstream.text;
    const eid = eventId();
    const wire = buildChatResponse({ eventId: eid, text: reply, markup: markupFloor(reply) });

    // ---- 9. A ticket for the voice, and a fresh context blob for the next turn. The
    // ticket is minted ONLY when a TTS model is configured: no voice, no ticket, and the
    // page speaks from its clips instead (§5, `DEMO_TTS_MODEL` unset => `voice: false`).
    const speech = [];
    if (cfg.voice) {
      speech.push({
        ticket: await mintTicket(cfg, { text: reply.slice(0, cfg.maxTtsChars), eventId: eid, chunkNum: 0 }),
        event_id: eid,
        chunk_num: 0,
      });
    }
    const nextContext = await mintContext(cfg, [
      ...turns,
      { role: "user", content: text },
      { role: "assistant", content: reply },
    ]);

    return respond(
      {
        ok: true,
        degraded: false,
        reason: null,
        mode: "live",
        load: slot.load,
        limits: publicLimits(cfg),
        messages: [chatMessage(cfg.deviceId, wire)],
        speech,
        context: nextContext,
        voice: cfg.voice,
        ears: cfg.ears,
      },
      { rateLimit: slot.rateLimit },
    );
  } finally {
    // The concurrency slot goes back on EVERY path, including a thrown one. `release()`
    // is idempotent, so a double call cannot under-count the ceiling for ever.
    slot.release();
  }
}

/* ---------------------------------------------------------------------------- *
 * The upstream body — the security control, as code
 * ---------------------------------------------------------------------------- */

/**
 * Construct the gateway request from configuration plus two bounded strings.
 *
 * THE PERSONA IS PLACED BOTH FIRST AND LAST (§3.3). The first copy is the ordinary system
 * prompt; the second is there so that **the final instruction the model reads is always
 * ours**, whatever a visitor managed to put in the middle. It costs a few dozen tokens and
 * it is the cheapest prompt-injection mitigation available to a demo with no classifier on
 * the output side.
 *
 * `stream` is not sent. P0 sends single-chunk turns only (§9's "explicitly out of P0"),
 * which is what makes the reply byte-identical to the pre-streaming wire (`wire.py`:78-81).
 */
export function buildUpstreamBody(cfg, turns, text) {
  const messages = [{ role: "system", content: cfg.persona }];
  for (const t of turns) messages.push({ role: t.role, content: t.content });
  messages.push({ role: "user", content: text });
  messages.push({ role: "system", content: cfg.persona });
  return {
    model: cfg.chatModel, // from DEMO_CHAT_MODEL. NEVER from the request.
    messages,
    max_tokens: cfg.maxTokens, // 160 by default (§4.1): the ceiling on the expensive half
    temperature: TEMPERATURE,
    n: 1,
    stream: false,
  };
}

/**
 * The one `fetch()` in this file.
 *
 * NOTHING FROM THE UPSTREAM RESPONSE IS FORWARDED except the completion text. Not the
 * status, not the body, not a header — with the single exception of a 429's `Retry-After`,
 * which is re-derived as a bounded integer rather than passed through as a string, so a
 * hostile value cannot ride it. The repo's own SDK already parses exactly that header
 * (`chat.py`:49-56), so the browser client and the Python client read the same signal
 * (§4.5).
 *
 * @returns {{ok:boolean, text?:string, reason?:string, retryAfterS?:number}}
 */
async function callGateway(cfg, body) {
  const url = joinUrl(cfg.baseUrl, "chat/completions");
  let res;
  try {
    noteUpstreamCall();
    res = await fetch(url, {
      method: "POST",
      // The ONLY place the credentials appear. Outbound, on request headers, and nowhere
      // else. `upstreamHeaders` also adds the two `CF-Access-*` headers when a complete
      // Cloudflare Access service token is configured, so a gateway behind an
      // Access-protected tunnel is reachable (`_lib/env.js::ACCESS_VARS`).
      headers: Object.assign(upstreamHeaders(cfg, "application/json"), { Accept: "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(cfg.chatTimeoutMs),
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
    // An abort is OUR timeout firing; anything else is an unreachable gateway. Neither
    // one's message is inspected beyond its name — an error string can carry the URL.
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut ? { ok: false, reason: "timeout" } : { ok: false, reason: "upstream_down" };
  }

  if (res.status === 429) {
    return { ok: false, reason: "rate_limited", retryAfterS: retryAfterOf(res) };
  }
  // A redirect, unfollowed — see `redirect: "manual"` above for why this is the door and
  // not the brain. Checked BEFORE `res.ok`, which is false for a 3xx and would otherwise
  // swallow it into `upstream_down`.
  if (res.status >= 300 && res.status < 400) {
    return { ok: false, reason: "gateway_unreachable_or_gated" };
  }
  if (!res.ok) {
    // 4xx and 5xx alike. The body is NOT read: an unknown-model 400 names the model, and
    // an auth 401 can name a key prefix. Both would be a §4.2 violation to even parse into
    // a variable that a later edit might log.
    return { ok: false, reason: "upstream_down" };
  }

  // A 200 that is not JSON is the Cloudflare Access failure mode, and it gets its own
  // reason so an operator is not left guessing (`_lib/envelope.js::REASONS`): a tunnel
  // protected by Access answers an unauthenticated server-side fetch with an HTML LOGIN
  // PAGE at status 200. The Content-Type is used here only as a HINT for the diagnosis —
  // the authority is whether the body actually parses.
  const ctype = String(res.headers.get("Content-Type") || "").toLowerCase();
  const looksGated = ctype.includes("text/html") || ctype.includes("application/xhtml");
  let json;
  try {
    json = await res.json();
  } catch {
    return { ok: false, reason: looksGated ? "gateway_unreachable_or_gated" : "upstream_down" };
  }
  if (looksGated) {
    // JSON served as text/html is odd but harmless; an HTML body that somehow parsed as
    // JSON is not a thing. Belt and braces: if the header says HTML and there is no
    // completion in the body, call it gated rather than down.
    if (!completionText(json)) return { ok: false, reason: "gateway_unreachable_or_gated" };
  }
  const text = completionText(json);
  // An empty completion is a FAILURE, not a turn. §4.5: never a 200 with an empty string.
  if (!text) return { ok: false, reason: "upstream_down" };
  return { ok: true, text };
}

/** The OpenAI chat-completions reply shape, defensively. */
function completionText(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const msg = choice && choice.message;
  const raw = msg && typeof msg.content === "string" ? msg.content : "";
  return raw.replace(/\s+/g, " ").trim();
}

/** A bounded integer from a `Retry-After` header, or a sane default. Never the string. */
function retryAfterOf(res) {
  const raw = res.headers.get("Retry-After");
  const n = Number(raw);
  if (Number.isFinite(n) && n > 0) return Math.min(300, Math.ceil(n));
  return 10;
}

/* ---------------------------------------------------------------------------- *
 * The two non-success shapes
 * ---------------------------------------------------------------------------- */

/**
 * A refusal, in §4/§7's envelope with §4.5's status and `Retry-After`, so the page
 * DEGRADES instead of erroring — `sim/web/mode.js::note` already understands every reason
 * in the closed set and picks the right badge and copy for it.
 *
 * `message` is left empty on purpose: §7's visitor-facing copy lives in `mode.js`, next to
 * the badge it paints, so that it is honest in `offline` too — where there is no server to
 * send a string — and so that no upstream text can ever become visitor-facing text.
 */
function refusal(cfg, route, reason, extra) {
  const budget = budgetState(cfg);
  return respond(
    {
      ok: false,
      degraded: true,
      reason,
      retry_after_s: (extra && extra.retryAfterS) || (reason === "budget_exhausted" ? budget.retryAfterS : 0),
      mode: "degraded",
      load: (extra && extra.load) || loadOf(cfg, route),
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

/**
 * A hard-blocked utterance (§4.1). `ok: true` because nothing went wrong — the floor did
 * its job — and `degraded: true` because this is not the live brain answering. Status 200
 * (`envelope.js::STATUS_FOR.blocked`), zero units spent, and no upstream call was made.
 * `mode.js::note` treats `blocked` as an input outcome and never changes mode for it.
 */
function blocked(cfg, slot, verdict) {
  const messages = [];
  const r = verdict.redirect;
  if (r) {
    const eid = eventId();
    const markup = MK.mood(r.mood) + MK.gesture(r.gesture) + r.text;
    messages.push(chatMessage(cfg.deviceId, buildChatResponse({ eventId: eid, text: r.text, markup })));
  }
  return respond(
    {
      ok: true,
      degraded: true,
      reason: "blocked",
      retry_after_s: 0,
      mode: "live",
      load: slot.load,
      limits: publicLimits(cfg),
      messages,
      // NO TICKET. A blocked turn spends nothing, and that includes the voice: the
      // redirect line is spoken from a clip or the browser voice like any scripted line.
      speech: [],
      context: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { rateLimit: slot.rateLimit },
  );
}
