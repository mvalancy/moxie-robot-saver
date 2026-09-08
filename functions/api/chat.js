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
 * A SERVED TURN MAKES ONE UPSTREAM CALL, OR TWO — NEVER MORE (step 8b). When a completion
 * comes back word for word identical to something Moxie already said in this conversation,
 * the route asks once more and serves the second answer. There is still exactly one
 * `fetch()` site, so the count stays a recorded fact; what changed is that a turn may pass
 * through it twice. It costs the visitor nothing extra in their per-IP window (`admit()`
 * runs once) and costs the deployment a second `UNITS.chat`, charged before the call by
 * `_lib/limits.js::chargeExtra` so no ceiling is spent past and none is under-counted. It
 * is bounded to ONE re-roll structurally, it fits inside the turn timeout that was already
 * promised, and every way it can fail keeps the reply the visitor already had.
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
import { lookup as lookupDocs } from "./_lib/docsearch.js";
import { buildChatResponse, chatMessage, eventId, expressiveVocab, joinUrl, markupFloor, MK } from "./_lib/wire.js";

/** §4.1: matches `chat.py`:130 so the hosted persona sounds like the local one. */
const TEMPERATURE = 0.8;

/** How many turns arrived with a context blob we had minted but that had since expired.
 *  A RECORDED fact (playbook rule 11) so a test can prove the turn was SERVED with its
 *  history dropped, rather than merely that it was not refused. */
let stats_expiredContext = 0;
/** Tests only. */
export function __expiredContexts() { return stats_expiredContext; }
export function __resetExpiredContexts() { stats_expiredContext = 0; }

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
    /* AN EXPIRED CONTEXT IS TIME PASSING, NOT AN ATTACK — and conflating the two wedged
     * the page (found 2026-09-06 while auditing the context window).
     *
     * `CONTEXT_TTL_S` is one hour. Every blob older than that failed here as
     * `bad_request`, byte-identical to a FORGED one — and `cloud-transport.js` only
     * replaces its stored blob on a SUCCESSFUL reply, so a refusal left the stale blob in
     * place and the very next turn sent it again. A tab left open over lunch was therefore
     * refused on every turn, for ever, until the visitor reloaded. The conversation did not
     * degrade; it stopped.
     *
     * The two cases deserve opposite answers. A bad signature is somebody editing history
     * they were not given — refuse it, and keep refusing. An expiry is a blob we minted
     * ourselves that simply got old: the right response is to forget the conversation and
     * carry on, which is what a companion who has not seen you for an hour would do
     * anyway. `verifyContext` already distinguishes them via `why`; only this line did not.
     *
     * The forgery path is unchanged and still `bad_request`, still spends nothing, and
     * `sim/test_demo_tickets.mjs` still proves a tampered blob is rejected. */
    const history = await verifyContext(cfg, contextBlob);
    if (!history.ok && history.why !== "expired") return spentNothing("bad_request");
    if (!history.ok) stats_expiredContext++;

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

    // ---- 8. The FIRST upstream call. Server-built body, fixed everything, and our own
    // timeout — deliberately BELOW the measured worst case of 45 s (`chat.py`:151-152),
    // because the demo prefers a fast honest degrade to a slow success (§4.1).
    const turns = history.turns;
    const startedAt = Date.now();
    /* THE LOOKUP. Two same-origin asset fetches, no gateway cost, gated by `wantsDocs` so
     * an ordinary turn pays nothing at all. Awaited because it shapes the one prompt we are
     * about to send — but it fails open to `null`, so a missing binding, a failed fetch or
     * a corpus that has moved costs a citation and never a turn. */
    const docs = await lookupDocs(context.env && context.env.ASSETS,
                                  new URL(request.url).origin, text);
    const upstream = await callGateway(cfg, buildUpstreamBody(cfg, turns, text, undefined, docs));
    if (!upstream.ok) {
      return refusal(cfg, "chat", upstream.reason, {
        retryAfterS: upstream.retryAfterS,
        load: slot.load,
        rateLimit: slot.rateLimit,
      });
    }

    // ---- 8b. THE RE-ROLL — the last of the repetition levers, and the only one that
    // spends. `rerollOnce()` carries the whole argument: what it costs, what bounds it,
    // and why it can only ever improve the answer or leave it exactly as it is.
    const served = await rerollOnce(cfg, slot, { turns, text, first: upstream, startedAt });

    // ---- 9. The reply, as the wire `bridge.js` already renders.
    const reply = served.text;
    const eid = eventId();
    // `upstream.chosen` is the mood/gesture the MODEL picked, or null when it answered
    // prose. `markupFloor` validates each field against its closed table and falls back to
    // the regex floor per-field, so this is safe to pass through unexamined.
    const wire = buildChatResponse({
      // `served.chosen` and `served.text` travel TOGETHER: a re-roll that replaced the
      // words replaced the face the model picked for those words, and pairing one turn's
      // sentence with another turn's mood is exactly the mismatch the expressive envelope
      // exists to remove.
      eventId: eid, text: reply, markup: markupFloor(reply, served.chosen),
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
        diagram: upstream.diagram || "",
        // `"<title>|<path>"` when she looked something up. See `PUBLIC_KEYS`.
        cited: docs ? (docs.title + "|" + docs.path) : "",
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
    "pondering, question when you ask something, self when you talk about yourself.\n" +
    /* MEASURED 2026-09-07: across three live conversations she used TWO of eleven faces
     * and was `happy` in almost every turn — including "I'm sorry you felt left out".
     * The gesture channel varied fine (six of twelve), so the model was reading the
     * instruction; it just had no reason to pick anything but happy, because the persona
     * describes a warm, playful, encouraging robot and nothing said the FACE tracks the
     * SENTENCE rather than the disposition. Eleven expressions the avatar can render and
     * two it ever shows is a waste of the whole expressive channel. */
    "YOUR FACE FOLLOWS THE SENTENCE, NOT YOUR PERSONALITY. You are a warm robot, but a " +
    "warm robot is not a permanently grinning one — a face that never changes stops " +
    "meaning anything. Use happy for genuinely good news, not as a default. Match what " +
    "you are actually saying: neutral for ordinary talk and plain facts, curious when you " +
    "wonder or ask, sad when they tell you something sad, concerned when they are hurt or " +
    "worried, confused when you do not understand or cannot remember, surprised at " +
    "something unexpected, shy or embarrassed when you get something wrong or are " +
    "complimented, afraid only for playful pretend-scary moments. Never angry at the " +
    "child.\n" +
    "Your face has these expressions and no others; anything else is ignored. Leave a " +
    "field out if none fits. Never put emoji, markdown, asterisks or stage directions " +
    "inside \"say\" — it is read aloud exactly as written."
    /* The DRAWING instruction used to live here and no longer does — it is conditional and
     * gets its own system message now. See `wantsDiagram` for the measurement that moved
     * it: buried at 88 % through a 5 337-character message, it was never once obeyed. */
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

/**
 * The re-roll's extra sentence, and it is the difference between rolling the dice again
 * and actually asking for something else.
 *
 * A bare second call sends the IDENTICAL body: same history, same sentence, same
 * temperature. On the inputs that produce a duplicate in the first place — "ok", "hmm",
 * "yeah", where the child has given the model almost nothing to work with — the
 * distribution that just produced that line is very likely to produce it again, so a bare
 * re-roll would buy a second completion and a decent chance of the same words. Naming the
 * line and forbidding it is what makes the second call worth its money.
 *
 * `line` IS OUR OWN GATEWAY'S PREVIOUS OUTPUT, not visitor text — it is already in this
 * conversation as a signed assistant turn (`_lib/hmac.js`), so quoting it back introduces
 * no string the model has not already read from us. It is sliced anyway, because a bound
 * on what we echo is cheaper than an argument about why it cannot be long.
 */
function rerollInstruction(line) {
  return (
    "You already said this, word for word, earlier in this same conversation:\n" +
    '"' + String(line).slice(0, 500) + '"\n' +
    "Say something different this time — a new thought, not that same thought reworded, " +
    "and not a line you have already said. Stay in the same JSON format."
  );
}

/**
 * @param {string} [avoid] a line the model must not repeat. Empty on the first call of a
 *   turn and set only by `rerollOnce()`, which is the only caller that has one.
 */
/**
 * Questions that genuinely want a picture, and the reason this is a GATE rather than a
 * paragraph in the persona.
 *
 * ============================================================================
 * MEASURED, TWICE, AND THE SECOND MEASUREMENT IS THE INTERESTING ONE.
 *
 * She never drew. Not once, across every probe, including "can you show me the STEPS of
 * how a seed becomes a tree?" — which is as direct an invitation as the language offers.
 * The first fix made the wording imperative instead of permissive. She still never drew.
 *
 * So I measured the prompt instead of rewriting it again. The trailing system message is
 * 5 337 characters, and the drawing instruction sat at 88 % through it — one paragraph
 * behind the whole persona, eleven mood triggers, the gesture vocabulary, the turn-shape
 * rules and a JSON format spec. It was not being refused. It was being drowned.
 *
 * Two prompt rewrites failed for the same reason, which is worth stating plainly: LOUDER
 * WORDING IN A DILUTED POSITION IS NOT A FIX. The lever is position and scarcity, not
 * emphasis.
 *
 * So the instruction is now CONDITIONAL and lives in its OWN system message — exactly how
 * the documentation excerpt is delivered, for exactly the same reason. On a turn that
 * wants a picture it is a short, unmissable message of its own; on every other turn it is
 * ABSENT, which also shortens the common prompt for the ~95 % of turns that are "i had a
 * bad day".
 * ============================================================================
 */
/* The mechanism verbs. Listed rather than matching any "how does…", because "how do you
 * feel?" and "how do you like school?" are the same grammar and want no picture at all —
 * a diagram in the middle of a conversation about feelings is the exact noise the old
 * discouragement was written to prevent. Widened once already: the first version required
 * work|happen|go and so missed "how does the robot TALK TO the cloud", which is the
 * canonical case this feature exists for. */
const WANTS_DIAGRAM = new RegExp(
  "\\b(steps?|stages?|sequence|process|life ?cycle|flow ?chart|diagram" +
  "|what happens when|show me how|how (is|are) .*\\b(made|built)" +
  "|how (does|do|did) .*\\b(work|works|happen|go|talk|talks|connect|connects|send|sends" +
  "|communicate|move|moves|travel|travels|reach|reaches|get|gets)\\b)",
  "i");

export function wantsDiagram(text) {
  return WANTS_DIAGRAM.test(String(text || ""));
}

export function buildUpstreamBody(cfg, turns, text, avoid, docs) {
  const messages = [{ role: "system", content: cfg.persona }];
  for (const t of turns) messages.push({ role: t.role, content: t.content });
  /* SHE LOOKED IT UP. A passage from this deployment's OWN documentation, fetched from our
   * own origin through the `ASSETS` binding — never anything the visitor typed. See
   * `_lib/docsearch.js` for why retrieval is server-side: the browser alternative hands a
   * visitor a field that gets spliced straight into a system message.
   *
   * It sits BEFORE the child's turn so the persona still comes LAST and is still the final
   * instruction in the prompt (§3.3). Putting reference material after the question would
   * make the last thing the model read a wall of technical prose, which is how a warm robot
   * starts reciting a protocol specification at a seven-year-old. */
  if (docs && docs.excerpt) {
    messages.push({
      role: "system",
      content:
        "You looked this up in your own documentation just now. It was written by the " +
        "people who took you apart to work out how you function, so it is true — but it is " +
        "written for engineers.\n\n" +
        "From \"" + docs.title + "\":\n" + docs.excerpt + "\n\n" +
        /* MEASURED ON THE LIVE SITE, and the fix is "one real fact", not "try harder".
         *
         * With `cited` proving the passage arrived, she still answered "what is your
         * protocol?" with "it's like the rules we follow when we talk. I have lots of ways
         * to answer and help you!" — a gloss anyone could produce without reading anything.
         * The old wording asked for her own words at a child's level and got exactly that:
         * simplification all the way down to no content. Simplifying is the easy half; the
         * whole point of looking something up is the part that is NEW to the listener.
         *
         * So the instruction now demands one CONCRETE thing from the passage and gives her
         * an explicit out — "I'm not sure" — for when the passage genuinely does not
         * answer. Between a vague gloss and an honest miss, the miss is worth more. */
        "Use ONE concrete thing from that passage — a name, a number, a part, something it " +
        "actually does — and put it in your own words at a child's level, in one or two " +
        "short sentences. Explain any hard word you use. Never read the passage out.\n" +
        "A vague answer that could have been given WITHOUT reading it is a failure: " +
        "\"it's like the rules we follow\" is not an answer, \"I talk to my brain in the " +
        "cloud one turn at a time\" is. You may say you looked it up. If the passage really " +
        "does not answer what they asked, say you are not sure — that is better than a " +
        "gloss.",
    });
  }
  /* THE DRAWING INSTRUCTION, only when the question wants one, and in its own message so
   * it is not competing with five thousand characters of persona. See `wantsDiagram`. */
  if (wantsDiagram(text)) {
    messages.push({
      role: "system",
      content:
        "THIS question is asking how something works or what its steps are, so DRAW A " +
        "DIAGRAM as well as answering in words. Put it inside \"say\" as a ```mermaid " +
        "fenced block, exactly like this:\n" +
        '{"say": "A seed grows in three steps! ```mermaid\\ngraph TD;\\n  Seed-->Roots;\\n  ' +
        'Roots-->Tree;\\n```", "mood": "happy", "gesture": "point"}\n' +
        "A handful of nodes with simple labels a young child can read, no styling. The " +
        "diagram is SHOWN and never spoken, so your words must make sense on their own and " +
        "must never say \"see the diagram below\".",
    });
  }
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
  // THE RE-ROLL'S SENTENCE GOES LAST, AND THAT DOES NOT WEAKEN THE MITIGATION ABOVE. The
  // property §3.3 asks for is that the final instruction the model reads is OURS; this one
  // is built here, from configuration and from our own previous completion, and there is no
  // path by which a request body can reach it. A visitor cannot cause this message to
  // exist, cannot choose whether it exists, and cannot influence a character of it.
  if (avoid) messages.push({ role: "system", content: rerollInstruction(avoid) });
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

/* ---------------------------------------------------------------------------- *
 * Step 8b — the re-roll
 * ---------------------------------------------------------------------------- *
 *
 * WHAT IT IS. When the completion that just came back is WORD FOR WORD something Moxie
 * already said in this same conversation, ask the gateway once more — naming the line and
 * forbidding it — and answer with the second reply instead. Nothing else about the turn
 * changes.
 *
 * WHY IT IS HERE AND NOT IN THE PROMPT. It is the third and last lever against the defect
 * the owner reported, and the first two are free ones that have already been pulled: the
 * persona's own initiative rule, and `frequency_penalty` / `presence_penalty` (see
 * `penaltiesAccepted`). `sim/eval_live.mjs` — which drives real multi-turn conversations
 * at the real deployment because a stub cannot show a loop — recorded what those two did
 * and what they left behind: the affirmation spiral gone, trigram overlap down from 1.0 to
 * 0.2, and STILL an exact duplicate in the `loop` scenario, the one where the child answers
 * "ok", "yeah", "hmm" and gives the model nothing to work with. A prompt rule cannot fix
 * that case, because the prompt already says it; the model simply lands on the same
 * sentence twice. Catching it after the fact is the only lever left that acts on the actual
 * output.
 *
 * ============================================================================
 * WHAT A RE-ROLLED TURN COSTS. Stated exactly, because a second gateway call inside one
 * visitor request is the kind of thing that quietly doubles a bill.
 *
 *   · THE VISITOR'S PER-IP WINDOW: **NOTHING EXTRA.** `admit()` runs once, before the body,
 *     and is not re-entered. A re-rolled turn eats exactly the same one slice of
 *     `DEMO_CHAT_PER_MIN` (5) as any other turn. This is the half that must not be charged
 *     twice: the visitor typed one sentence, and taking a second of their five turns a
 *     minute away because the MODEL repeated itself would punish them for our defect.
 *   · THE UNIT BUDGET: **`UNITS.chat` AGAIN — 3 more units, charged BEFORE the call.**
 *     This is the half that must be charged, and `limits.js::chargeExtra` carries the
 *     argument: the budget counts money, a second completion is real money, and a re-roll
 *     that spent silently would make `DEMO_UNIT_BUDGET_HOUR` describe up to twice the
 *     spend it names. At the defaults an hour is 600 units, so the worst imaginable hour —
 *     every single turn re-rolling — is 100 turns instead of 200 rather than 200 turns at
 *     double the true cost. Fewer turns is a visible, honest limit; an undercounted budget
 *     is not.
 *   · GATEWAY CALLS: **two, both recorded.** Both go through `callGateway`, so both hit
 *     `noteUpstreamCall()` and `__state().stats.upstreamCalls` is the true number.
 *   · LATENCY: **up to double, and never past the timeout this route already promised.**
 *     `rerollBudgetMs` below is the bound.
 * ============================================================================
 *
 * AND EVERY ONE OF THOSE CEILINGS CAN SAY NO. `chargeExtra()` returning false — the hour
 * or the day is spent — means the re-roll simply does not happen. It is a quality
 * improvement that yields to every limit and is never a way to spend past one. What it
 * must never do is turn a served turn into a refusal: the visitor already has a reply in
 * hand, and if anything at all goes wrong they keep it.
 */

/**
 * HOW MUCH TIME A RE-ROLL MAY HAVE, or 0 for "not now".
 *
 * THE BOUND IS THE ROUTE'S OWN TIMEOUT AND NOT A NEW NUMBER. `DEMO_CHAT_TIMEOUT_MS`
 * (20 000) is already this route's promise about the worst a visitor waits, chosen in §4.1
 * because *the demo prefers a fast honest degrade to a slow success*. A re-roll that could
 * push past it would be quietly rewriting that promise, and picking any fresh constant —
 * "re-roll only under 4 s" — would be inventing a threshold with no measurement behind it,
 * which is exactly the mistake `eval_live.mjs`'s own header refuses to make.
 *
 * So: the second call may have what is LEFT of the first call's budget, and it is only
 * attempted when what is left is at least what the first call took. The first condition
 * makes the total wall clock of a re-rolled turn <= `DEMO_CHAT_TIMEOUT_MS`, exactly as an
 * ordinary turn is. The second is the fast-degrade rule applied to the same clock: a first
 * call that took 12 of the 20 seconds is a gateway already struggling, and the honest
 * answer there is a duplicate reply now rather than a fresh one that may not arrive. In
 * practice the live deployment answers in 1.5-2.6 s, so a re-roll is available on
 * essentially every turn that wants one, and disappears precisely when the site is slow.
 *
 * @param {object} cfg
 * @param {number} elapsedMs how long the first call took
 * @returns {number} ms the re-roll may use, or 0 if it may not run
 */
export function rerollBudgetMs(cfg, elapsedMs) {
  const spent = Number.isFinite(elapsedMs) && elapsedMs > 0 ? elapsedMs : 0;
  const left = (cfg.chatTimeoutMs || 0) - spent;
  return left >= spent ? left : 0;
}

/**
 * The line `reply` repeats, or "" when it repeats nothing.
 *
 * ============================================================================
 * EXACT, NOT NEAR — AND THIS IS THE DELIBERATE CHOICE, NOT THE LAZY ONE.
 *
 * A near-match test needs a similarity threshold, and a threshold is a number somebody has
 * to defend. This repo already has the measurement that shows why one would be indefensible
 * here: the first repetition fix took `eval_live.mjs`'s trigram overlap from 1.0 to 0.2 and
 * exact duplicates to zero, and the conversation STILL read as a loop, because six of seven
 * turns were "Did you … today?" — same shape, different words. A lexical similarity number
 * moved in the right direction while the behaviour did not. Wiring a spend decision to that
 * same class of number would be spending real money on the strength of a signal already
 * known to mislead. The register-level repetition it would try to catch is a PROMPT problem
 * and has been treated as one (the persona's initiative rule, and `questionRate` in the
 * instrument so the next person can see whether it worked).
 *
 * What is left after the prompt has done its work is the case a prompt cannot reach: the
 * model landing on the identical sentence twice. That is a fact, not a judgement — no
 * threshold, no argument, and it is the one the instrument still reports as `exactDupes`.
 *
 * NOT MERELY THE PREVIOUS TURN: EVERY ASSISTANT TURN IN THE SIGNED HISTORY. "A, B, A" is
 * the same loop with one turn of camouflage, and comparing against all of them costs a
 * string compare over a window `_lib/hmac.js` has already bounded at
 * `DEMO_MAX_HISTORY_TURNS` (12) and `DEMO_MAX_CONTEXT_CHARS` (4000).
 *
 * CASE- AND SPACE-INSENSITIVE, AND NOTHING ELSE. Those two are not a similarity threshold:
 * "That's great!" and "that's great!" are the same line read aloud, and the whitespace has
 * already been collapsed upstream by `completionText`. Anything further — stripping
 * punctuation, stemming, dropping a leading name — starts making judgements about how
 * different two sentences are, which is the thing this function refuses to do.
 * ============================================================================
 */
export function echoOf(reply, turns) {
  const norm = (v) => String(v == null ? "" : v).replace(/\s+/g, " ").trim().toLowerCase();
  const key = norm(reply);
  if (!key) return "";
  for (const t of turns || []) {
    if (!t || t.role !== "assistant") continue;
    if (norm(t.content) === key) return String(t.content);
  }
  return "";
}

/**
 * Re-roll at most once, and never make the turn worse.
 *
 * @returns {{text: string, chosen: object|null, rerolled: boolean}} the reply to serve.
 *
 * ONE. NOT A LOOP. There is no recursion here and no `while`: the second call's answer is
 * taken or it is not, and either way this function returns. A loop of re-rolls is how a
 * repetition fix becomes an unbounded spend on a model having a bad day, and the bound has
 * to be structural rather than a counter somebody could raise. Consequently:
 *
 *   IF THE RE-ROLL ALSO REPEATS, THE FIRST REPLY IS SERVED AND THE TURN ENDS. Both answers
 *   are the same duplicate, so the visitor loses nothing by getting the one they would have
 *   got anyway — and keeping the FIRST rather than the second makes the rule easy to state
 *   and easy to test: **the second call may only ever replace the reply, never degrade it.**
 *   The turn is a duplicate that cost two calls; the instrument still counts it as a
 *   duplicate, which is the honest thing for it to do.
 *
 * EVERY FAILURE KEEPS THE FIRST REPLY. A timeout, an unreachable gateway, a 500, an empty
 * completion — all of them come back from `callGateway` as `ok: false` and are simply
 * ignored here. A re-roll may never turn a turn the visitor had already won into a
 * `degraded` page; that would be a repetition fix that can take the demo down, which is the
 * same objection that shaped `penaltiesAccepted` above.
 */
async function rerollOnce(cfg, slot, o) {
  const first = { text: o.first.text, chosen: o.first.chosen, rerolled: false };
  if (!cfg.reroll) return first;

  // 1. THE DECISION. Free, and it is the only thing that can start a spend.
  const echo = echoOf(first.text, o.turns);
  if (!echo) return first;

  // 2. THE LATENCY BOUND. Also free, and checked before the money so a slow gateway does
  //    not have units taken off the budget for a call that was never going to be made.
  const budgetMs = rerollBudgetMs(cfg, Date.now() - o.startedAt);
  if (!budgetMs) return first;

  // 3. THE MONEY. Charged BEFORE the call, in `admit()`'s own order — charge, then spend —
  //    so a crash between the two under-serves rather than under-counts. `false` means a
  //    ceiling said no, and a ceiling saying no ends it.
  if (!slot.chargeExtra()) return first;

  // 4. The second call. `noteUpstreamCall()` fires inside it, as for the first.
  const again = await callGateway(cfg, buildUpstreamBody(cfg, o.turns, o.text, echo), budgetMs);
  if (!again.ok) return first;
  // NOT MERELY "DIFFERENT FROM THE FIRST": not an echo of ANYTHING she has said. A second
  // reply that dodges the line we named and lands on one from three turns ago is the same
  // defect wearing a different sentence.
  if (echoOf(again.text, o.turns)) return first;
  return { text: again.text, chosen: again.chosen, rerolled: true };
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
 * @param {number} [timeoutMs] the deadline for THIS call, defaulting to
 *   `DEMO_CHAT_TIMEOUT_MS`. Only the re-roll passes one, and it passes what is LEFT of that
 *   same budget (`rerollBudgetMs`), so two calls in one turn still cannot outlast the one
 *   timeout §4.1 promises a visitor.
 * @returns {{ok:boolean, text?:string, reason?:string, retryAfterS?:number}}
 */
async function callGateway(cfg, body, timeoutMs) {
  const deadline = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : cfg.chatTimeoutMs;
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
      signal: AbortSignal.timeout(deadline),
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
    // THE SAME DEADLINE, carried into the retry rather than defaulted. A re-roll runs on
    // what is left of the turn's budget, and a retry that quietly reset itself to the full
    // `DEMO_CHAT_TIMEOUT_MS` would let one turn outlast the timeout this route promises.
    return callGateway(cfg, plain, deadline);
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
  // The diagram leaves the spoken line here, so the safety sweep, the TTS ticket, the wire
  // text and the transcript all see words only. See `splitDiagram`.
  const { spoken, diagram } = splitDiagram(text);
  if (!spoken) return { ok: false, reason: "upstream_down" };
  return { ok: true, text: spoken, chosen, diagram };
}

/** The OpenAI chat-completions reply shape, defensively. */
function completionText(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const msg = choice && choice.message;
  const raw = msg && typeof msg.content === "string" ? msg.content : "";
  /* TRIMMED, NOT FLATTENED. This used to collapse every run of whitespace to one space,
   * which was harmless while a reply was only ever prose — and silently destroys a mermaid
   * diagram, whose syntax is newline-delimited. Flattening now happens once, on the SPOKEN
   * half only, inside `splitDiagram`, which is the last point at which anything still cares
   * about line breaks. */
  return raw.trim();
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
/**
 * Pull a mermaid diagram out of a reply, and give back the words WITHOUT it.
 *
 * ============================================================================
 * THE HALF THAT MATTERS IS THE STRIPPING, NOT THE EXTRACTING.
 *
 * `say` is read aloud. A fenced mermaid block left in it is synthesised verbatim, so a
 * child hears "backtick backtick backtick mermaid graph T D semicolon A arrow B" in
 * Moxie's voice. It is also minted into the TTS ticket and charged for, and it lands in
 * the transcript as syntax. Every one of those is downstream of this function, which is
 * why the split happens HERE — at the gateway boundary, in the same place the JSON
 * envelope is unwrapped — rather than in the browser. Nothing after this point can
 * accidentally speak a diagram, because nothing after this point has one.
 *
 * WHAT COUNTS AS A DIAGRAM: a ```mermaid fence. Bare ``` is NOT taken as one — a model
 * that fences a word for emphasis would otherwise have its sentence silently truncated,
 * and a wrong diagram is better than a missing sentence. The body is returned raw and
 * UNVALIDATED: mermaid's parser lives in the browser, so validity is the renderer's
 * question (`sim/web/diagram.js`), and this function's only job is that the words and the
 * syntax stop travelling together.
 *
 * SIZE-CAPPED, because this rides the response envelope and is attacker-adjacent in the
 * ordinary sense that a model can be talked into writing a lot. A diagram longer than a
 * screen is not a diagram a child is reading.
 * ============================================================================
 */
const MAX_DIAGRAM_CHARS = 1200;
const MERMAID_FENCE = /```mermaid\s*([\s\S]*?)```/i;

export function splitDiagram(text) {
  const s = String(text || "");
  // The one place prose is flattened, and it happens AFTER the diagram is out — see
  // `completionText`. Everything downstream speaks or displays single-spaced words.
  const flat = (x) => String(x).replace(/\s+/g, " ").trim();
  const m = MERMAID_FENCE.exec(s);
  if (!m) return { spoken: flat(s), diagram: "" };
  const body = String(m[1] || "").trim();
  // The words with the fence cut out, whitespace repaired so the seam is not audible.
  const spoken = flat(s.slice(0, m.index) + " " + s.slice(m.index + m[0].length));
  if (!body || body.length > MAX_DIAGRAM_CHARS) return { spoken, diagram: "" };
  return { spoken, diagram: body };
}

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

  // NOT flattened here either: `say` may legitimately contain a fenced diagram, and
  // `splitDiagram` collapses the words after the fence has been taken out.
  const say = typeof obj.say === "string" ? obj.say.trim() : "";
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
