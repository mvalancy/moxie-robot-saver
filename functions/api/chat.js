/* functions/api/chat.js — POST /api/chat, one turn.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (route and response shapes), §3.3
 * (signed context blob), §4.1 (caps), §4.2 (what the browser may know), §4.5 (statuses).
 *
 * A typed sentence in; out come the `remote_chat` payload `bridge.js` already renders and
 * the TICKETS — one per sentence, chunk 0 first — the browser redeems at `/api/speech` for
 * the voice.
 *
 * INVARIANTS
 *  - BUILD THE UPSTREAM BODY; NEVER FORWARD THE CLIENT'S (`_lib/prompt.js`). Only `text` and
 *    `context` are read from the request; every other field is ignored, not validated.
 *  - THE KEY NEVER LEAVES THIS PROCESS. It exists only as an outbound header
 *    (`_lib/env.js::upstreamHeaders`); no upstream status, body or header is forwarded.
 *  - ZERO UPSTREAM CALLS ON EVERY REFUSAL PATH, and every refusal inside the admitted
 *    section refunds the units `admit()` charged (`spentNothing`). `noteUpstreamCall()`
 *    sits immediately before the one `fetch()` in this file.
 *  - A SERVED TURN MAKES ONE UPSTREAM CALL, OR TWO — NEVER MORE. A reply that repeats an
 *    earlier line word for word is re-rolled once (`rerollOnce`), charged to the unit
 *    budget via `slot.chargeExtra()` but never to the visitor's per-IP window.
 *  - NEVER A BARE 500, NEVER A 200 WITH AN EMPTY STRING: an empty completion is
 *    `upstream_down` and the page degrades visibly.
 *  - A GOODBYE ENDS THE TURN. When the child's whole line is a leave-taking
 *    (`_lib/turnshape.js::isGoodbye`) the model is cued to say goodbye, `end_turn` is true
 *    and the markup carries the sign-off wave — the one place a client is told to stop.
 *  - THE REPLY IS CHECKED BEFORE ANY TICKET IS MINTED (`_lib/safety.js`, §4.12). A
 *    completion that trips a Moxie-side block never reaches `output.text`, a ticket or the
 *    context blob: the rule's redirect line is served in its place, marked like an input
 *    block, with no extra upstream call. A hurt child's reply that names no trusted
 *    grown-up gets ONE referral sentence appended, spoken as its own last ticket.
 */
import { readConfig, modeOf, publicLimits, publicTurnstile, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { assess, disclosesHurt, MOXIE, withReferral } from "./_lib/safety.js";
import { admit, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { mintContext, mintTickets, verifyContext } from "./_lib/hmac.js";
import { TOKEN_FIELD, verify as verifyTurnstile } from "./_lib/turnstile.js";
import { lookup as lookupDocs } from "./_lib/docsearch.js";
import { isGoodbye } from "./_lib/turnshape.js";
import { buildChatResponse, chatMessage, eventId, joinUrl, markupFloor, MK, SIGN_OFF } from "./_lib/wire.js";
import { buildUpstreamBody, penaltiesAccepted, rejectPenalties } from "./_lib/prompt.js";
import { completionText, echoOf, MAX_DIAGRAM_CHARS, parseExpressive, rerollBudgetMs, splitDiagram } from "./_lib/reply.js";
import { fetchFailure, limitedOrRedirected, refusal as refuse } from "./_lib/upstream.js";

// The pure helpers moved to `_lib/`; re-exported so every existing importer keeps working.
export { buildUpstreamBody, wantsDiagram, __resetPenaltyProbe } from "./_lib/prompt.js";
export { echoOf, parseExpressive, rerollBudgetMs, splitDiagram } from "./_lib/reply.js";
/** Tests only: what the isolate currently believes about the penalty fields. */
export function __penaltiesAccepted() { return penaltiesAccepted(); }

/** Turns served with a context blob we minted that had since expired — recorded so a test
 *  can prove the turn was SERVED with history dropped, not merely not refused. */
let stats_expiredContext = 0;
/** Tests only. */
export function __expiredContexts() { return stats_expiredContext; }
export function __resetExpiredContexts() { stats_expiredContext = 0; }

/** This route's refusal. The sitekey rides every envelope shape, not only the probe's: the
 *  envelope is one shape for every outcome, so no client has to ask which fields a given
 *  answer carries. */
function refusal(cfg, reason, extra) {
  return refuse(cfg, "chat", reason, extra, { turnstile: publicTurnstile(cfg) });
}

export async function onRequestPost(context) {
  const request = context.request;
  const cfg = readConfig(context.env);

  // 1. Configuration. Unset => `gateway_not_configured` and no upstream call, so a keyless
  //    branch preview is inert.
  const gate = modeOf(cfg, null);
  if (gate.mode !== "live") {
    return refusal(cfg, gate.reason, { retryAfterS: 0 });
  }

  // 2. Admission — origin pin, per-IP windows, unit budget, capacity (with a bounded FIFO
  //    wait) — in one call so the order cannot be got wrong. Charged BEFORE the body is
  //    parsed, so a flood of malformed bodies is rate-limited like any other flood.
  const slot = await admit({ request, cfg, route: "chat" });
  if (!slot.ok) {
    return refusal(cfg, slot.reason, {
      retryAfterS: slot.retryAfterS,
      rateLimit: slot.rateLimit,
      load: slot.load,
    });
  }

  try {
    // A refusal that spends nothing upstream gives back the units `admit()` charged, or 200
    // free refusals empty the shared hourly budget (`_lib/limits.js::grantedSlot`). The
    // upstream refusal at step 8 must NOT use this: that call was really made.
    const spentNothing = (reason, extra) => {
      slot.refundBudget();
      return refusal(cfg, reason, { load: slot.load, rateLimit: slot.rateLimit, ...(extra || {}) });
    };

    // 3. The request. Exactly two keys are read.
    const parsed = await readJsonBody(request, cfg);
    if (!parsed.ok) return spentNothing(parsed.reason);
    const text = typeof parsed.body.text === "string" ? parsed.body.text.trim() : "";
    const contextBlob = typeof parsed.body.context === "string" ? parsed.body.context : "";

    // 4. Input caps: REJECTED, not truncated, so the page can say why.
    if (!text) return spentNothing("too_short");
    if (text.length > cfg.maxInputChars) return spentNothing("too_long");

    // 5. The context blob. A forged one is `bad_request` (Moxie's side of history is signed
    //    by us and cannot be injected). An EXPIRED one is time passing, not an attack: the
    //    conversation is forgotten and the turn served — refusing it wedged a tab left open
    //    for an hour, because the client only replaces its blob on success.
    const history = await verifyContext(cfg, contextBlob);
    if (!history.ok && history.why !== "expired") return spentNothing("bad_request");
    if (!history.ok) stats_expiredContext++;

    // 6. Pre-inference safety. A hard block never calls the gateway and spends nothing.
    //    The verdict is kept: a `hurt_disclosure` flag decides at step 9 whether the reply
    //    must point the child to a grown-up.
    const verdict = assess(text);
    if (verdict.blocked) {
      slot.refundBudget();
      return await blocked(cfg, slot, verdict);
    }

    // 7. The bot control, and its POSITION is the design: after every free refusal
    //    (cheapest first), after `admit()` (so the per-IP windows protect siteverify rather
    //    than making us an amplifier aimed at it), and after the safety floor (a blocked
    //    line must not buy a round trip). Returning inside the `try` keeps the slot release.
    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD], "chat");
    if (!bot.ok) {
      return spentNothing(bot.reason);
    }

    // 8. The first upstream call. The docs lookup is two same-origin asset fetches with no
    //    gateway cost, and fails open to `null`. Whether the child is leaving is decided
    //    here too, from the whole line and nothing else; it cues the model (inside
    //    `buildUpstreamBody`) and closes the turn at step 9.
    const turns = history.turns;
    const startedAt = Date.now();
    const closing = isGoodbye(text);
    const docs = await lookupDocs(context.env && context.env.ASSETS,
                                  new URL(request.url).origin, text);
    const upstream = await callGateway(cfg, buildUpstreamBody(cfg, turns, text, undefined, docs));
    if (!upstream.ok) {
      return refusal(cfg, upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // 8b. The re-roll.
    const served = await rerollOnce(cfg, slot, { turns, text, first: upstream, startedAt, docs });

    // 8c. The output floor (§4.12): her own words, assessed on the Moxie side of the table
    //     BEFORE a ticket is minted or the context is signed. A hard block swaps in the
    //     rule's redirect line, spoken from tickets of its own, and marks the turn the way
    //     an input block is marked. The upstream call was really made, so the units stay
    //     charged. A soft flag changes nothing.
    const own = assess(served.text, MOXIE);
    if (own.blocked) {
      return await blocked(cfg, slot, own, { speak: true });
    }

    // 9. The reply. `served.chosen`, `served.diagram` and `served.text` travel together, so
    //    a re-rolled line never wears the face, or shows the picture, the model chose for
    //    the other one. `markupFloor` validates each field against its closed table; on a
    //    goodbye it is asked for the sign-off wave and the wire says the turn is over.
    //    A hurt child's reply (step 6's flag) that names no trusted grown-up gets ONE
    //    referral sentence appended here, before the markup, the tickets and the blob,
    //    so it is shown, spoken as its own last ticket, and remembered as hers.
    const reply = disclosesHurt(verdict) ? withReferral(served.text, text).text : served.text;
    const eid = eventId();
    const wire = buildChatResponse({
      eventId: eid, text: reply, endTurn: closing,
      markup: markupFloor(reply, served.chosen, closing ? SIGN_OFF : ""),
    });

    // 10. The voice tickets — one per sentence, chunk 0 first, so her first words need
    //     only a short synthesis (`_lib/hmac.js::mintTickets`) — only when a TTS model is
    //     configured; and the next context blob.
    const speech = cfg.voice ? await mintTickets(cfg, { text: reply, eventId: eid }) : [];
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
        turnstile: publicTurnstile(cfg),
        messages: [chatMessage(cfg.deviceId, wire)],
        speech,
        diagram: served.diagram || "",
        // `"<title>|<path>"` when she looked something up. See `PUBLIC_KEYS`.
        cited: docs ? (docs.title + "|" + docs.path) : "",
        context: nextContext,
        voice: cfg.voice,
        ears: cfg.ears,
      },
      { rateLimit: slot.rateLimit },
    );
  } finally {
    // The slot goes back on EVERY path, including a thrown one; `release()` is idempotent.
    slot.release();
  }
}

/**
 * Re-roll at most once, and never make the turn worse.
 *
 * Only when the reply repeats an earlier line (`echoOf`), only if what is left of the
 * turn's timeout allows (`rerollBudgetMs`), and only if a unit ceiling allows
 * (`slot.chargeExtra()`, charged BEFORE the call). One call, no loop. Every failure — a
 * timeout, a 500, an empty completion, a second echo — keeps the first reply.
 *
 * The second body is the first body plus the one forbidding sentence: the same docs
 * passage rides it (it once did not, and the served reply could cite a passage its model
 * never saw), and the diagram served is the one drawn FOR the served line.
 *
 * @returns {Promise<{text: string, chosen: object|null, diagram: string, rerolled: boolean}>}
 */
async function rerollOnce(cfg, slot, o) {
  const first = { text: o.first.text, chosen: o.first.chosen, diagram: o.first.diagram || "", rerolled: false };
  if (!cfg.reroll) return first;
  const echo = echoOf(first.text, o.turns);
  if (!echo) return first;
  const budgetMs = rerollBudgetMs(cfg, Date.now() - o.startedAt);
  if (!budgetMs) return first;
  if (!slot.chargeExtra()) return first;
  const again = await callGateway(cfg, buildUpstreamBody(cfg, o.turns, o.text, echo, o.docs), budgetMs);
  if (!again.ok) return first;
  // Not an echo of ANYTHING she said — dodging the named line onto an older one is the
  // same defect.
  if (echoOf(again.text, o.turns)) return first;
  return { text: again.text, chosen: again.chosen, diagram: again.diagram || "", rerolled: true };
}

/**
 * The one `fetch()` in this file. Only the completion text is taken from the response;
 * a 429's `Retry-After` is re-derived as a bounded integer.
 *
 * @param {number} [timeoutMs] defaults to `DEMO_CHAT_TIMEOUT_MS`; the re-roll passes what
 *   is LEFT of that budget, so two calls cannot outlast the one promised timeout.
 * @returns {Promise<{ok:boolean, text?:string, chosen?:object|null, diagram?:string,
 *   reason?:string, retryAfterS?:number}>}
 */
async function callGateway(cfg, body, timeoutMs) {
  const deadline = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : cfg.chatTimeoutMs;
  const url = joinUrl(cfg.baseUrl, "chat/completions");
  let res;
  try {
    noteUpstreamCall();
    res = await fetch(url, {
      method: "POST",
      headers: Object.assign(upstreamHeaders(cfg, "application/json"), { Accept: "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(deadline),
      redirect: "manual", // a 3xx is a door problem; see `_lib/upstream.js`
    });
  } catch (err) {
    return fetchFailure(err);
  }

  const early = limitedOrRedirected(res);
  if (early) return early;
  /* A 400 on a request that CARRIED the penalty fields means "this gateway does not know
   * them", not an outage: remember that for the isolate and retry once without them, on the
   * same deadline. The flag is already false on the retry, so this cannot loop. */
  if (res.status === 400 && penaltiesAccepted() &&
      ("frequency_penalty" in body || "presence_penalty" in body)) {
    rejectPenalties();
    const { frequency_penalty, presence_penalty, ...plain } = body;
    void frequency_penalty; void presence_penalty;
    return callGateway(cfg, plain, deadline);
  }
  // Body deliberately unread: an unknown-model 400 names the model, a 401 a key prefix.
  if (!res.ok) return { ok: false, reason: "upstream_down" };

  // A 200 that is not JSON is the Cloudflare Access login page. The Content-Type is only a
  // hint for the diagnosis; whether the body parses is the authority.
  const ctype = String(res.headers.get("Content-Type") || "").toLowerCase();
  const looksGated = ctype.includes("text/html") || ctype.includes("application/xhtml");
  let json;
  try {
    json = await res.json();
  } catch {
    return { ok: false, reason: looksGated ? "gateway_unreachable_or_gated" : "upstream_down" };
  }
  const raw = completionText(json);
  if (looksGated && !raw) return { ok: false, reason: "gateway_unreachable_or_gated" };
  if (!raw) return { ok: false, reason: "upstream_down" };
  const parsed = parseExpressive(raw);
  const { spoken, diagram } = splitDiagram(parsed.text);
  if (!spoken) return { ok: false, reason: "upstream_down" };
  // The `diagram` field wins; a ```mermaid fence is the fallback for prose replies.
  const drew = (parsed.diagram || diagram || "").slice(0, MAX_DIAGRAM_CHARS);
  return { ok: true, text: spoken, chosen: parsed.chosen, diagram: drew };
}

/**
 * A hard-blocked turn: `ok: true` (the floor did its job), `degraded: true` (not the live
 * brain), status 200, the redirect line in `messages`, no context (the turn is not
 * remembered). `mode.js` never changes mode for it.
 *
 * An INPUT block (`verdict` from the child's line) mints no ticket: nothing was spent and
 * the redirect is spoken from a clip or the browser voice like any scripted line. An
 * OUTPUT swap (`speak: true`, `verdict` from Moxie's own reply) mints tickets for the
 * redirect line only — the completion it replaces never reaches a ticket — so the line
 * can be spoken in her voice; the unsafe completion is in no field of the response.
 */
async function blocked(cfg, slot, verdict, o) {
  const messages = [];
  let speech = [];
  const r = verdict.redirect;
  if (r) {
    const eid = eventId();
    const markup = MK.mood(r.mood) + MK.gesture(r.gesture) + r.text;
    messages.push(chatMessage(cfg.deviceId, buildChatResponse({ eventId: eid, text: r.text, markup })));
    if (o && o.speak && cfg.voice) speech = await mintTickets(cfg, { text: r.text, eventId: eid });
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
      turnstile: publicTurnstile(cfg),
      messages,
      speech,
      context: "",
      voice: cfg.voice,
      ears: cfg.ears,
    },
    { rateLimit: slot.rateLimit },
  );
}
