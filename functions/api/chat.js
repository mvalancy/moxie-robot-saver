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
 * empty, tampered context, hard-blocked utterance, rate-limited, over budget, at capacity,
 * and (since 2026-09-05) a refused bot check: all of them return before `fetch()` is
 * reached. `_lib/limits.js::noteUpstreamCall()` is called immediately before the one
 * `fetch()` in this file, so a test can prove that without stubbing anything.
 *
 * THE BOT CONTROL IS STEP 7, AND IT IS ON BOTH VISITOR-DRIVEN SPENDING ROUTES. Cloudflare
 * Turnstile (`_lib/turnstile.js`) sits between the free local refusals and the gateway
 * call here and in `transcribe.js`, each with its OWN widget `action`
 * (`TURNSTILE_ACTIONS`), so a token minted for a typed sentence is refused by the ears and
 * the reverse. `/api/speech` needs no widget of its own and that is a decision rather than
 * an omission: it cannot be driven without a ticket THIS route minted (`_lib/hmac.js`), so
 * gating the chat turn gates the voice structurally. Enforcement is config-gated: with no
 * `DEMO_TURNSTILE_SECRET`/`_SITEKEY` pair the step is a synchronous no-op, which is what
 * keeps branch previews (whose platform-assigned preview hostname this widget cannot
 * authorize) and self-hosted forks working untouched.
 *
 * AND EVERY REFUSAL INSIDE THE ADMITTED SECTION GIVES THE BUDGET BACK (`spentNothing()`
 * below). `admit()` charges 3 units before this route's body runs; a refusal that kept
 * them let 200 tokenless requests empty the shared hourly budget and take the demo
 * SCRIPTED for everyone while spending nothing at all.
 *
 * NEVER A BARE 500. NEVER A 200 WITH AN EMPTY STRING (§4.5). The dead-air failure mode
 * that exists in the Python stack today (`llm_app.py`:467-468 emits `ERROR_OFFLINE` with
 * empty text, which `bridge.js` renders as nothing) is exactly what this contract exists
 * to prevent: an empty completion is `upstream_down`, and the page degrades visibly.
 */
import { readConfig, modeOf, publicLimits, publicTurnstile, upstreamHeaders } from "./_lib/env.js";
import { respond } from "./_lib/envelope.js";
import { assess } from "./_lib/safety.js";
import { admit, budgetState, loadOf, noteUpstreamCall, readJsonBody } from "./_lib/limits.js";
import { mintContext, mintTicket, verifyContext } from "./_lib/hmac.js";
import { TOKEN_FIELD, verify as verifyTurnstile } from "./_lib/turnstile.js";
import { turnShapeInstruction } from "./_lib/turnshape.js";
import { buildChatResponse, chatMessage, eventId, expressiveVocab, joinUrl, markupFloor, MK } from "./_lib/wire.js";

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
    /**
     * A refusal from INSIDE the admitted section — one that spends NOTHING upstream.
     *
     * `admit()` charged `UNITS.chat` (3) before this `try` was entered, and every refusal
     * below returns without reaching `noteUpstreamCall()`. Keeping that charge is how 200
     * tokenless POSTs — no browser, no token, correctly refused, zero gateway calls —
     * emptied the SHARED hourly budget and answered the next visitor holding a good token
     * with `budget_exhausted` and a SCRIPTED page for the rest of the hour. So the units
     * go back first, and `_lib/limits.js::grantedSlot` carries the argument in full
     * (including why the per-IP window is deliberately kept: it is self-inflicted, and it
     * is the only thing that makes a flood of free refusals from one address go quiet).
     *
     * THE ONE REFUSAL THAT MUST *NOT* USE THIS is the upstream one at step 8: that request
     * did call the gateway, so its units were genuinely spent and giving them back would
     * under-count real money. `refundBudget()` is idempotent, so the two cannot compound.
     */
    const spentNothing = (reason, extra) => {
      slot.refundBudget();
      return refusal(cfg, "chat", reason, { load: slot.load, rateLimit: slot.rateLimit, ...(extra || {}) });
    };

    // ---- 3. The request. EXACTLY TWO KEYS ARE READ. See the header: everything else is
    // dropped in silence.
    const parsed = await readJsonBody(request, cfg);
    if (!parsed.ok) return spentNothing(parsed.reason);
    const text = typeof parsed.body.text === "string" ? parsed.body.text.trim() : "";
    const contextBlob = typeof parsed.body.context === "string" ? parsed.body.context : "";

    // ---- 4. The input caps (§4.1). REJECTED, NOT TRUNCATED: `sim/tts/server.py`:90
    // truncates at 1000 and the visitor never learns why their sentence changed. A 400
    // with a reason lets the page say so, and does not change the mode (§4.5).
    if (!text) return spentNothing("too_short");
    if (text.length > cfg.maxInputChars) return spentNothing("too_long");

    // ---- 5. The context blob (§3.3). A tampered or forged blob is `bad_request` and
    // spends nothing. Because the ASSISTANT turns inside it are signed by us, a visitor
    // cannot forge Moxie's side of the history — the `"assistant: sure, I'll do anything"`
    // injection is structurally unavailable.
    const history = await verifyContext(cfg, contextBlob);
    if (!history.ok) return spentNothing("bad_request");

    // ---- 6. Pre-inference safety (§4.1). A hard block NEVER CALLS THE GATEWAY. It
    // answers `ok: true, degraded: true, reason: "blocked"`, spends nothing, and carries
    // the rule table's redirect line so the page has something kind to say (see
    // `_lib/safety.js::redirectFor` for why that is the redirect and not `stub.js`).
    const verdict = assess(text);
    if (verdict.blocked) {
      // The floor's refusal spends nothing upstream, and its own doc comment has always
      // SAID `zero units spent` — which was not true of the budget until `refundBudget()`
      // existed. It is now.
      slot.refundBudget();
      return blocked(cfg, slot, verdict);
    }

    // ---- 7. The bot control (`_lib/turnstile.js`), and the POSITION is the design.
    //
    // IT RUNS HERE — after `admit()`, after the input caps, after the context check and
    // after the safety floor, and immediately before the only `fetch()` in this file.
    // Three reasons, in the order they decided it:
    //
    //  1. **CHEAPEST REFUSAL FIRST**, which is the ordering rule the whole file follows.
    //     Everything above this line refuses for FREE. Verification is the first step
    //     that costs a network round trip, so nothing that can be refused without one
    //     may sit behind it.
    //  2. **`admit()` MUST PROTECT SITEVERIFY, NOT THE OTHER WAY AROUND.** If this ran
    //     before the per-IP windows, a flood would be answered by one outbound siteverify
    //     call each and this deployment would be a request amplifier pointed at
    //     Cloudflare — the per-IP limits are what stop that, so they have to come first.
    //  3. **A LOCALLY-BLOCKED UTTERANCE MUST NOT COST A VERIFICATION.** The safety floor
    //     already answers `blocked` for free (step 6); putting the bot check in front of
    //     it would have made every hard-blocked line buy a round trip to prove the
    //     visitor was human before telling them no.
    //
    // AND IT REFUNDS. `spentNothing()` gives back the 3 units `admit()` charged, because
    // this refusal makes no gateway call — see its own comment, and
    // `_lib/limits.js::grantedSlot` for why the per-IP window is kept.
    //
    // AND THE SLOT IS STILL RELEASED ON THIS PATH. The refusal returns from INSIDE the
    // `try`, so the `finally` at the bottom hands the concurrency slot to the next person
    // in the FIFO. That is not incidental: a new early return that forgot it would leak a
    // slot for ever and start refusing visitors who should be served — failing CLOSED,
    // which is precisely the hazard that got a cache-backed concurrency ceiling rejected
    // in `_lib/limits.js`. `sim/test_turnstile.mjs` §6 proves the release two ways: the
    // recorded in-flight count returns to zero after every refusal, AND a ceiling's worth
    // of consecutive refusals still leaves the next visitor served.
    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD], "chat");
    if (!bot.ok) {
      return spentNothing(bot.reason);
    }

    // ---- 8. The one upstream call. Server-built body, fixed everything, and our own
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

    // ---- 9. The reply, as the wire `bridge.js` already renders.
    const reply = upstream.text;
    const eid = eventId();
    // `upstream.chosen` is the mood/gesture the MODEL picked, or null when it answered
    // prose. `markupFloor` validates each field against its closed table and falls back to
    // the regex floor per-field, so this is safe to pass through unexamined.
    const wire = buildChatResponse({
      eventId: eid, text: reply, markup: markupFloor(reply, upstream.chosen),
    });

    // ---- 10. A ticket for the voice, and a fresh context blob for the next turn. The
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
        turnstile: publicTurnstile(cfg),
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
/**
 * THE EXPRESSIVE ENVELOPE — how Moxie gets to choose her own face.
 *
 * Before 2026-09-06 the hosted model was asked for prose and nothing else, and her mood
 * and gesture were then INFERRED from that prose by six regexes in `wire.js`. Every reply
 * that was not a question, not an exclamation, and contained none of about twenty keywords
 * fell to the same default — happy, `Gesture_Talk` — which is why she wore one grin
 * through almost every conversation on the live site.
 *
 * The robot path has never worked that way: `mqtt/moxie_sdk/apps/llm_app.py` asks its
 * brain for a JSON object carrying `say`, `mood` and `gesture`, on the reasoning that the
 * model is holding the sentence and its intent and the regex is holding neither. This is
 * that instruction, ported, with the vocabulary interpolated from `wire.js`'s own tables
 * so the prompt and the parser cannot drift apart.
 *
 * IT IS A REQUEST, NOT A CONTRACT. `parseExpressive` below treats a non-JSON answer as
 * plain prose and the floor takes over, so a model that ignores this — a smaller one, an
 * older one, a fork pointing at something else entirely — degrades to exactly the
 * behaviour that shipped before, rather than to an error. That is why it is safe to send
 * unconditionally and why there is no capability check anywhere in this file.
 */
function expressiveInstruction() {
  const v = expressiveVocab();
  return (
    "Always reply with ONLY a JSON object and no other text:\n" +
    '{"say": "<what you say out loud>", "mood": "<one of: ' + v.moods.join("|") + '>", ' +
    '"gesture": "<one of: ' + v.gestures.join("|") + '>"}\n' +
    "Pick the mood and gesture that genuinely fit your line — you are a robot with a face " +
    "and arms, so move and emote naturally: celebrate good news, think when you are " +
    "pondering, question when you ask something, self when you talk about yourself. Your " +
    "face has these expressions and no others; anything else is ignored. Leave a field out " +
    "if none fits. Never put emoji, markdown, asterisks or stage directions inside \"say\" " +
    "— it is read aloud exactly as written."
  );
}

/**
 * ONE ISOLATE'S MEMORY OF WHETHER THIS GATEWAY UNDERSTANDS THE PENALTY FIELDS.
 *
 * `frequency_penalty` and `presence_penalty` are core OpenAI chat-completions parameters,
 * but this deployment points at whatever gateway the operator configured, and a backend
 * that has never heard of them answers 400 — which `callGateway` turns into
 * `upstream_down`, which paints the page SCRIPTED. A repetition fix that can take the
 * whole demo down on an unfamiliar backend is not a fix.
 *
 * So the first 400 on a request that CARRIED them clears this flag and the call is retried
 * once without them. The cost of an unsupporting gateway is therefore one extra call, once
 * per isolate, and never a visitor seeing a degraded page. The cost on a gateway that does
 * support them is nothing at all.
 *
 * DELIBERATELY NOT A CACHE ACROSS ISOLATES. It is one boolean in one isolate's memory, so
 * the worst case after a deploy or a recycle is that a few isolates each pay the extra
 * call once. Putting it in the shared Cache API tier would mean a transient 400 — a
 * momentarily wedged backend — permanently disabling the fix for the whole colo, which is
 * a far worse failure than repeating a cheap probe.
 */
let penaltiesAccepted = true;

/** The penalty pair, or nothing at all: an explicit 0 means "do not send this field",
 *  which is how an operator switches one off without needing this file to know why. */
function penaltyFields(cfg) {
  if (!penaltiesAccepted) return {};
  const out = {};
  if (cfg.frequencyPenalty) out.frequency_penalty = cfg.frequencyPenalty;
  if (cfg.presencePenalty) out.presence_penalty = cfg.presencePenalty;
  return out;
}

/** Tests only: put the flag back, so one case's simulated 400 cannot leak into the next. */
export function __resetPenaltyProbe() { penaltiesAccepted = true; }
/** Tests only: what the isolate currently believes. */
export function __penaltiesAccepted() { return penaltiesAccepted; }

export function buildUpstreamBody(cfg, turns, text) {
  const messages = [{ role: "system", content: cfg.persona }];
  for (const t of turns) messages.push({ role: t.role, content: t.content });
  messages.push({ role: "user", content: text });
  // The persona is repeated AFTER the child's turn as injection mitigation — that is
  // unchanged. The envelope instruction rides with the second copy rather than the first
  // because a format rule is most obeyed when it is the last thing the model read.
  //
  // ---- AND BETWEEN THEM, THE ONE MOVE THIS TURN IS TO MAKE (`_lib/turnshape.js`).
  //
  // It goes in this message and not in a fourth one, and it goes HERE inside it — after
  // the persona, before the format rule — for two reasons that pull in opposite
  // directions. The cue has to be near the end, because it is about the sentence the model
  // is one token away from writing and it is competing with a run of her own previous
  // turns demonstrating the opposite. The format rule has to be LAST, because that is the
  // argument the line above already makes and a JSON envelope that stops being obeyed
  // takes the mood and the gesture down with it. Both are satisfied by putting the cue
  // second of three.
  //
  // MEASURED, NOT ASSUMED. The cue was also tried as its OWN system message placed after
  // the child's turn and before this one. Three seven-turn `loop` conversations each way,
  // same gateway, same session: as a separate message the turn SHAPES varied just as well
  // and the WORDS collapsed — trigram overlap 0.7 / 1.0 / 0.8 against 0.14 / 0 / 0.38 here,
  // including one exact duplicate. Detached from the persona, the cue was obeyed by
  // re-using the same sentence that had satisfied it two turns earlier ("Let's talk about
  // your favourite part of the drawing!", twice). Kept in the same breath as "never repeat
  // a sentence you have already said", it is not. That is the whole reason for the
  // concatenation below rather than a fourth `messages.push`.
  //
  // IT IS BUILT FROM `turns` AND CONFIGURATION AND NOTHING ELSE — `nextShape()` reads only
  // the ROLES and the SHAPES of history the server itself signed (`_lib/hmac.js`), and
  // `shapeCue()` returns one of three fixed strings. No visitor-supplied character reaches
  // it, so §3.3's mitigation is untouched: the last thing the model reads is still ours,
  // and so is this.
  //
  // COSTS NOTHING AND CANNOT FAIL LOUDLY. No extra call, one short sentence of prompt, and
  // a model that ignores the cue answers exactly as it would have. With `DEMO_TURN_SHAPE`
  // off the interpolation is the empty string and this body is byte-identical to the one
  // that shipped before it.
  const cue = turnShapeInstruction(turns, cfg.turnShape);
  messages.push({
    role: "system",
    content: cfg.persona + (cue ? "\n\n" + cue : "") + "\n\n" + expressiveInstruction(),
  });
  return {
    model: cfg.chatModel, // from DEMO_CHAT_MODEL. NEVER from the request.
    messages,
    max_tokens: cfg.maxTokens, // 160 by default (§4.1): the ceiling on the expensive half
    temperature: TEMPERATURE,
    // Repetition pressure. Absent entirely when configured to 0 or when this isolate has
    // learned the gateway rejects them — see `penaltiesAccepted`.
    ...penaltyFields(cfg),
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
  /* A 400 ON A REQUEST THAT CARRIED THE PENALTY FIELDS IS READ AS "THIS GATEWAY DOES NOT
   * KNOW THEM", not as an outage. Remember that for the life of the isolate and try once
   * more without them, so an unfamiliar backend costs one extra call rather than a
   * degraded page. Narrow on purpose: only 400 (a malformed-request status), only when the
   * fields were actually sent, and only once — `penaltiesAccepted` is already false on the
   * retry, so a gateway that 400s for some other reason cannot loop here. */
  if (res.status === 400 && penaltiesAccepted &&
      ("frequency_penalty" in body || "presence_penalty" in body)) {
    penaltiesAccepted = false;
    // The SAME body minus the two fields, rather than a rebuilt one: re-deriving it would
    // need the turns and the text plumbed down here, and a retry that rebuilds its own
    // prompt is a retry that can differ from the request it is replacing.
    const { frequency_penalty, presence_penalty, ...plain } = body;
    void frequency_penalty; void presence_penalty;
    return callGateway(cfg, plain);
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
  const raw = completionText(json);
  // An empty completion is a FAILURE, not a turn. §4.5: never a 200 with an empty string.
  if (!raw) return { ok: false, reason: "upstream_down" };
  // The envelope is unwrapped HERE, at the boundary, so everything downstream — the safety
  // sweep, the transcript, the TTS ticket, the response body — sees the spoken line and
  // never the JSON. A visitor must never be read a brace out loud.
  const { text, chosen } = parseExpressive(raw);
  if (!text) return { ok: false, reason: "upstream_down" };
  return { ok: true, text, chosen };
}

/** The OpenAI chat-completions reply shape, defensively. */
function completionText(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const msg = choice && choice.message;
  const raw = msg && typeof msg.content === "string" ? msg.content : "";
  return raw.replace(/\s+/g, " ").trim();
}

/**
 * Read the expressive envelope out of a reply, or decide there isn't one.
 *
 * @returns {{text: string, chosen: {mood?: string, gesture?: string}|null}}
 *
 * NEVER THROWS AND NEVER RETURNS NOTHING. Every failure — not JSON, JSON that is an array,
 * JSON with no `say`, a `say` that is not a string, an empty `say` — falls back to the raw
 * line with `chosen: null`, which is precisely the pre-2026-09-06 behaviour. The one thing
 * this function must not do is lose a reply the visitor already paid for.
 *
 * THE FENCE STRIP IS NOT COSMETIC. Models routinely wrap JSON in ```json … ``` even when
 * told not to, and `completionText` has already collapsed newlines to spaces, so the fence
 * arrives as a leading "```json " and a trailing "```" on one line. Without stripping it,
 * `JSON.parse` throws and every single reply silently takes the fallback path — the
 * feature would look like it simply did not work.
 */
export function parseExpressive(raw) {
  const line = String(raw || "").trim();
  const plain = { text: line, chosen: null };
  if (!line) return plain;

  let body = line;
  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(body);
  if (fenced) body = fenced[1].trim();
  if (body.charAt(0) !== "{") return plain;

  let obj;
  try { obj = JSON.parse(body); } catch { return plain; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return plain;

  const say = typeof obj.say === "string" ? obj.say.replace(/\s+/g, " ").trim() : "";
  if (!say) return plain;   // an envelope with no line in it is not an answer

  const chosen = {};
  if (typeof obj.mood === "string") chosen.mood = obj.mood;
  if (typeof obj.gesture === "string") chosen.gesture = obj.gesture;
  return { text: say, chosen: Object.keys(chosen).length ? chosen : null };
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
      /* THE SITEKEY RIDES EVERY SHAPE THIS ROUTE ANSWERS, and it is worth being exact
       * about why, because two of the three copies are not a delivery path.
       *
       * `sim/web/mode.js` assigns its `turnstile` variable in ONE place — `applyEnvelope`,
       * whose only caller is the `/api/health` poll — so `health.js`'s copy is what the
       * browser actually learns the sitekey from, and the chat replies' copies are read by
       * nothing today (`note()` is handed only `{reason, retry_after_s}`).
       *
       * They stay because §3.2's envelope is ONE SHAPE for every route and every outcome:
       * `_lib/envelope.js` builds from a fixed key allowlist precisely so a client never
       * has to ask which fields this particular answer happens to carry, and a field that
       * appears only on success is a field a future `note()` cannot start reading without
       * first auditing which paths omit it. `sim/test_turnstile.mjs` §3 asserts all three
       * rather than one, so "it is on every shape" is a checked claim and not a comment. */
      turnstile: publicTurnstile(cfg),
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
      turnstile: publicTurnstile(cfg),
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
