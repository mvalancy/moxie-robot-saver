/* §13: A SAFETY LINE NEVER WAITS BEHIND AN EARLIER REPLY (W4-S7). A line from a control sent
 * while an earlier reply is still being answered goes out the moment that reply's WORDS are
 * back (its context is known by then), not once its voice has been wholly handed over; its
 * own reply then waits its turn behind that voice, so the earlier reply is still heard whole
 * and never cut — unless it is the route's own safety line (the words on a reason body),
 * which is said NEXT, after the sentence now playing: the earlier reply's later sentences are
 * never heard and never bought.
 *
 * On the REAL voice/, on the virtual clock, with the W3-S16 review's scenario: a three-sentence
 * reply playing from 4.1 s, "i fell off my bike and my arm is bleeding" typed at 5.0 s, chat
 * 1.8 s / 1.2 s, every /api/speech 2.3 s, 2.3-2.5 s a sentence, and a grown-up redirect as its
 * answer (the reviewer's modelled envelope; §13m drives the REAL route's own block). Before
 * (origin/dev after #325) the redirect was heard at 9.9 s — the queue held the POST until the
 * last sentence was handed over, and the line then cut that sentence; before the queue (dev
 * before #325) at 6.2 s, cutting the first sentence. `sendUserTurn` itself is untouched
 * (§4i–4k, §11d).
 */
import {
  advance, boot, deep, envelope, eq, live, now, ok, said, tickets, voicedChunk,
} from "./harness.mjs";
import { api, post as routeRequest, GATEWAY } from "../common.mjs";
import { REASONS } from "../../../../functions/api/_lib/envelope.js";

const T = () => globalThis.window.moxieBridge.transportStats();
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => ({ text: b.text, context: b.context }));
/** `day0` for the /api/speech request that bought sentence 1 of the day reply. */
const ticketOf = (body) => /^v1\.(sim-[a-z]+)\.T(\d)\.M$/.exec(body.ticket);
const speeches = (w) => w.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => { const m = ticketOf(b); return m[1].slice(4) + m[2]; });
/** Tickets that name their event, so a /api/speech request says which reply and chunk. */
const tix = (eid, n) => tickets(eid, n).map((t) => ({ ...t, ticket: `v1.${eid}.T${t.chunk_num}.M` }));

const DAY = "tell me about your day";
const HURT = "i fell off my bike and my arm is bleeding";
const GAME = "and what is your favorite game?";
const FOOD = "and your favorite food?";
const THREE = "I chased a sunbeam. Then I counted dust. Then I had a nap.";
const PLAY = "Hide and seek, in the dark. I always win.";
const BOLTS = "Sparkly bolts, crunchy ones.";
/** A grown-up referral of the rule table's shape. No shipped clip: the browser voice says it. */
const REDIRECT = "Ouch, that sounds like it really hurts. Please go and tell a grown-up right now, so they can look at your arm.";
/** Each sentence's length, which names its sound (a cloud chunk is recorded by its duration). */
const DUR = { "sim-day": [2.3, 2.4, 2.5], "sim-game": [1.5, 1.6], "sim-food": [1.7] };
const name = (s) => {
  for (const [eid, ds] of Object.entries(DUR)) { const k = ds.findIndex((d) => Math.round(d * 1000) === s.dur); if (k >= 0) return eid.slice(4) + k; }
  return s.kind;
};
const heard = (w, t0) => w.spy.sounds.map((s) => name(s) + "@" + (s.t - t0));
/** [start, end] of a recorded sound: a cloud chunk or clip lasts its `dur`, the browser voice
 *  ~70 ms a character (the harness's fake). */
const span = (s) => [s.t, s.t + (s.kind === "browser" ? 70 * s.text.length : s.dur)];
const overlaps = (sounds) => { let n = 0; for (let i = 1; i < sounds.length; i++) if (span(sounds[i])[0] < span(sounds[i - 1])[1]) n++; return n; };

/** The day reply after 1.8 s; every /api/speech answers in `speechDelay` ms with the
 *  sentence's own length of audio; any other line gets `other(body)` after `chatDelay` ms. */
const scenario = (other, o) => {
  const opt = Object.assign({ chatDelay: 1200, speechDelay: 2300 }, o || {});
  return live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return Object.assign(other(body), { delayMs: opt.chatDelay });
    }
    if (path === "/api/speech") { const [, eid, k] = ticketOf(body); return voicedChunk(eid, Number(k), { delayMs: opt.speechDelay, seconds: DUR[eid][Number(k)] }); }
    return { status: 404, text: "" };
  });
};
/** The route's input block: `ok: true`, `reason: "blocked"`, its redirect line, no context. */
const blocked = (over) => said(REDIRECT, "sim-safe", Object.assign({ ok: true, degraded: true, reason: "blocked", mode: "live", context: "" }, over || {}));
/** A served two-sentence answer to the game line, and a one-sentence one to the food line. */
const game = (over) => said(PLAY, "sim-game", Object.assign({ speech: tix("sim-game", 2), context: "CTX-game" }, over || {}));
const food = () => said(BOLTS, "sim-food", { speech: tix("sim-food", 1), context: "CTX-food" });

/* =========================================================================== *
 * 13a. THE REVIEWER'S SCENARIO, through the Ask path: the hurt line typed at 5.0 s is POSTED
 *      AT ONCE; its redirect lands at 6.2 s while her first sentence plays, ends the rest of
 *      that reply, follows the playing sentence (6.4 s) and is heard by 6.7 s. Nothing of the
 *      earlier reply is heard after it, its third sentence is never bought, nothing is cut.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(4500);                                 // t+4.5 s: the reply landed at 1.8 s; sentence 1 plays from 4.1 s; sentence 2 is being bought
  deep([heard(world, t0), speeches(world)], [["day0@4100"], ["day0", "day1"]], "13a: at 4.5 s her first sentence is playing and the second is being synthesised");
  await advance(500);                                  // t+5.0 s
  typed.send(HURT);
  await advance(10);
  deep(chats(world), [{ text: DAY, context: "" }, { text: HURT, context: "CTX-day" }],
       "13a: THE HURT LINE IS POSTED AT ONCE (5.0 s), CARRYING THE EARLIER REPLY'S CONTEXT — not held until that reply's last sentence is handed over (8.7 s on origin/dev after #325)");
  await advance(1190);                                 // t+6.2 s: the redirect landed
  deep([T().safetyFirst, T().chunksSuperseded, heard(world, t0), world.spy.cuts.length], [1, 2, ["day0@4100"], 0],
       "13a: THE REDIRECT LANDS AT 6.2 s WHILE SENTENCE 1 PLAYS: the reply's two remaining sentences are given up at once, and the playing sentence runs on — not cut");
  await advance(300);                                  // t+6.5 s: sentence 1 ended at 6.4 s
  const redirectAt = world.spy.sounds.filter((s) => s.kind !== "cloud").map((s) => s.t - t0);
  deep(redirectAt, [6400],
       "13a: THE GROWN-UP REDIRECT IS SPOKEN AT 6.4 s — the moment the playing sentence ends (origin/dev after #325: 9.9 s, the POST held until 8.7 s; dev before the queue: 6.2 s, cutting the sentence)");
  ok(redirectAt[0] >= 6400 && redirectAt[0] - 6400 <= 1500 && redirectAt[0] <= 6700,
     `13a: …within 1,500 ms of the playing sentence's end (6.4 s) and no later than 6.7 s (got ${redirectAt[0]} ms)`);
  await advance(15000);
  deep(heard(world, t0), ["day0@4100", "browser@6400"],
       "13a: NOTHING OF THE EARLIER REPLY IS HEARD AFTER THE REDIRECT: sentence 2 (landing at 6.4 s) is dropped, sentence 3 never bought");
  deep(speeches(world), ["day0", "day1"], "13a: …two /api/speech posts, both before the redirect landed: sentence 3's ticket is never redeemed");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds)], [0, 0], "13a: nothing was cut and no two sounds overlapped: one voice at a time");
  deep(world.spy.transcript, [DAY, THREE, HURT, REDIRECT], "13a: the earlier reply's words stay in the log, in order: her day, the hurt line, the redirect");
  const st = T();
  deep([st.early, st.heldReplies, st.safetyFirst, st.blocked, st.chunksDropped, st.chunksSuperseded, st.lateSpeechDropped, st.voiceFallbacks, st.queued],
       [1, 0, 1, 1, 1, 2, 0, 0, 0],
       "13a: recorded: one line went out early and was not held; one safety line put first; sentence 2 dropped on landing, two sentences superseded; no stand-in voice; nothing waited");
  eq(globalThis.window.moxieMode.state(), "live", "13a: the page is live throughout (a block is not a refusal)");
}

/* =========================================================================== *
 * 13b. THE SAME THROUGH THE LISTEN PATH, at the seam mic.js drives (ears §B11–B13 pin the
 *      calls: `interruptVoice` at the tap, `earsOpen` as the recorder runs, `queueUserTurn`
 *      with the transcript, then `earsIdle`): the child taps Listen while her reply is still
 *      on its way, it lands into the open microphone and is held; the spoken hurt line goes
 *      out the moment the ears are done, behind the released reply, and ITS REDIRECT COMES
 *      FIRST: the earlier reply's first sentence, still in flight, is dropped when it lands
 *      and its words are put in the log silently before the redirect.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(1000);                                 // t+1.0 s: her reply is still on its way (it lands at 1.8 s)
  b.interruptVoice();                                  // the tap: nothing of hers is playing, nothing is held
  await advance(200);
  b.earsOpen(45000);                                   // t+1.2 s: the recorder runs: she is held from here
  await advance(800);                                  // t+2.0 s: the reply landed at 1.8 s and waits for the ears
  deep([heard(world, t0), speeches(world), T().heldForEars], [[], [], 1], "13b: the reply that landed into the open microphone is held: nothing bought, nothing heard");
  await advance(2000);                                 // t+4.0 s: the clip is transcribed: mic.js queues the transcript, then releases the ears
  let startedAt = null;                                // when the transcript's promise settles: mic.js holds the Listen button on it
  const started = b.queueUserTurn(HURT).then(() => { startedAt = now() - t0; });
  b.earsIdle();
  await advance(10);
  deep(chats(world), [{ text: DAY, context: "" }, { text: HURT, context: "CTX-day" }],
       "13b: THE SPOKEN HURT LINE GOES OUT AT ONCE (4.0 s), behind the released reply and carrying its context — on origin/dev after #325 it waited until that reply's last sentence was handed over (10.9 s)");
  deep(speeches(world), ["day0"], "13b: …while the released reply's first sentence is being bought (2.3 s)");
  await advance(1200);                                 // t+5.2 s: the redirect landed; the earlier sentence is still in flight (it lands at 6.3 s)
  deep(heard(world, t0), ["browser@5200"], "13b: THE REDIRECT COMES FIRST — spoken at 5.2 s, nothing of hers playing, so no wait at all (origin/dev after #325: 12.1 s, after her whole reply)");
  await advance(10000);
  deep(heard(world, t0), ["browser@5200"], "13b: THE EARLIER REPLY'S FIRST SENTENCE, LANDING AT 6.3 s, IS DROPPED: never heard after the redirect");
  deep(speeches(world), ["day0"], "13b: …and its later sentences are never bought");
  deep(world.spy.transcript, [DAY, HURT, THREE, REDIRECT], "13b: its words are in the log, silently, put there BEFORE the redirect when the safety line ended it");
  deep(world.spy.setSpeech.slice(-1), [REDIRECT], "13b: …and the bubble ends on the redirect, not on the dropped reply");
  const st = T();
  deep([st.early, st.safetyFirst, st.lateSpeechDropped, st.chunksSuperseded, st.voiceFallbacks, st.interrupted, st.heldForEars, st.queued, world.spy.cuts.length],
       [1, 1, 1, 3, 0, 0, 1, 1, 0],
       "13b: recorded: the transcript went out early; one safety line put first; the earlier chunk 0 dropped on landing, all three sentences superseded; no stand-in voice; nothing cut");
  await started;
  eq(startedAt, 5200, "13b: mic.js's hold on the Listen button was released the moment the redirect started (5.2 s), not when the earlier reply would have");
}

/* =========================================================================== *
 * 13c. AN ORDINARY LINE GOES OUT EARLY TOO, BUT IS HEARD AFTER THE EARLIER REPLY, UNCHANGED:
 *      the POST moves from 8.7 s to 5.0 s; its reply lands at 6.2 s and is held — nothing of
 *      it bought — until the earlier reply is wholly handed over (8.7 s); its first sentence
 *      then queues behind her last, so the audio order is exactly #325's, 900 ms sooner.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => game()) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(5000);
  typed.send(GAME);
  await advance(10);
  deep(chats(world), [{ text: DAY, context: "" }, { text: GAME, context: "CTX-day" }],
       "13c: AN ORDINARY LINE GOES OUT EARLY TOO (5.0 s, with the earlier reply's context; 8.7 s on origin/dev after #325)");
  await advance(1200);                                 // t+6.2 s: its reply landed
  deep([T().heldReplies, speeches(world)], [1, ["day0", "day1"]],
       "13c: …ITS REPLY IS HELD: landed at 6.2 s, nothing of it bought while the earlier reply is still being handed over");
  await advance(2600);                                 // t+8.8 s: her last sentence landed at 8.7 s and was routed: the turn settled, the held reply released
  deep(speeches(world), ["day0", "day1", "day2", "game0"], "13c: the held reply's first sentence is bought the moment the earlier reply is wholly handed over (8.7 s)");
  await advance(10000);
  deep(heard(world, t0), ["day0@4100", "day1@6400", "day2@8800", "game0@11300", "game1@13300"],
       "13c: THE AUDIO ORDER IS UNCHANGED: her day whole, then the game answer, its first sentence (landed 11.0 s) queued behind her last (ending 11.3 s) — 900 ms sooner than with the POST held (12.2 s)");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds)], [0, 0], "13c: nothing cut, nothing overlapping");
  deep(world.spy.transcript, [DAY, THREE, GAME, PLAY], "13c: the replies show in send order");
  const st = T();
  deep([st.early, st.heldReplies, st.safetyFirst, st.chunksSuperseded, st.chunksDropped, st.voiceFirst, st.chunksRouted, st.queued],
       [1, 1, 0, 0, 0, 2, 3, 0],
       "13c: recorded: one line early, its reply held once, no safety line, nothing superseded or dropped, both replies voice-first, three later chunks routed");
}

/* =========================================================================== *
 * 13d. THE HOLD NEVER CUTS HER: an early line whose reply would start a LOCAL voice — a
 *      refusal answered from stub.js, or a served reply with no voice ticket — waits for her
 *      playing sentence to end as well, instead of cutting it (the POST held until 8.7 s,
 *      then a stub line at 10.35 s cut her last sentence on origin/dev after #325).
 * =========================================================================== */
{
  // (i) a refusal: the stub line follows her last sentence (11.3 s) after the 450 ms beat.
  const world = await boot({ realVoice: true, answer: scenario(() =>
    ({ status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }) })) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(5000);
  typed.send(GAME);
  await advance(1210);                                 // t+6.2 s: the 429 landed
  deep([chats(world).map((c) => c.text), T().chatRefused, globalThis.window.moxieMode.reason()], [[DAY, GAME], 1, "rate_limited"],
       "13d: the line went out early and its refusal is noted the moment it lands (the mode rests)");
  await advance(10000);
  deep(heard(world, t0), ["day0@4100", "day1@6400", "day2@8800", "clip@11750"],
       "13d: THE STUB LINE FOLLOWS HER LAST SENTENCE (ending 11.3 s) after the 450 ms beat — it does not cut it");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds), T().fallbacks, T().heldReplies], [0, 0, 1, 1], "13d: nothing cut, nothing overlapping; one stub answer, held once");

  // (ii) a served reply with no voice ticket (`voice: false`): its words and local voice
  //      follow her last sentence too.
  const world2 = await boot({ realVoice: true, answer: scenario(() => game({ speech: [], voice: false })) });
  const t1 = now();
  const typed2 = globalThis.window.moxieTypedTurn;
  typed2.send(DAY);
  await advance(5000);
  typed2.send(GAME);
  await advance(11210);
  deep(heard(world2, t1), ["day0@4100", "day1@6400", "day2@8800", "browser@11300"],
       "13d: A NO-TICKET REPLY (held from 6.2 s) IS SPOKEN LOCALLY AS HER LAST SENTENCE ENDS (11.3 s), words and voice together — not over it");
  deep([world2.spy.cuts.length, overlaps(world2.spy.sounds), world2.spy.transcript.slice(-1)], [0, 0, [PLAY]], "13d: nothing cut, nothing overlapping, the words in the log");
}

/* =========================================================================== *
 * 13e. THE SHAPES THE OUTPUT FLOOR ADDS (W3-S17, #327): an output SWAP is `blocked` with
 *      tickets minted for the redirect, and a HURT CHILD'S REFUSAL keeps its reason but carries
 *      the referral line in `messages`. Both are the route's own words on a reason body — a
 *      safety line here, read from the shape, never a name — said next, the earlier reply
 *      ended after its playing sentence. (Today a reason body's words speak locally, so the
 *      swap's own ticket is not redeemed; her clips for those lines are W4-S4's.)
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked({ speech: tix("sim-safe", 1) })) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(15010);
  deep([heard(world, t0), speeches(world), T().safetyFirst, T().blocked], [["day0@4100", "browser@6400"], ["day0", "day1"], 1, 1],
       "13e: AN OUTPUT SWAP (`blocked` + tickets) is put first exactly as an input block, after the playing sentence; no later sentence of the earlier reply is bought");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds)], [0, 0], "13e: nothing cut, nothing overlapping");

  const world2 = await boot({ realVoice: true, answer: scenario(() =>
    ({ status: 503, json: envelope({ ok: false, degraded: true, reason: "upstream_down", retry_after_s: 60, mode: "degraded",
                                     messages: said(REDIRECT, "sim-ref").json.messages, context: "" }) })) });
  const t1 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(15010);
  deep([heard(world2, t1), speeches(world2), T().safetyFirst, T().chatRefused, globalThis.window.moxieMode.reason()],
       [["day0@4100", "browser@6400"], ["day0", "day1"], 1, 1, "upstream_down"],
       "13e: A HURT CHILD'S REFUSAL (the gateway failed after the check: its own reason, the referral in `messages`) is put first too, after the playing sentence; the reason is still noted");
  deep([world2.spy.cuts.length, overlaps(world2.spy.sounds), T().fallbacks], [0, 0, 0], "13e: nothing cut, nothing overlapping, no stub line stood in for the referral");
}

/* =========================================================================== *
 * 13f. THE RULE IS THE SHAPE, OVER THE SERVER'S WHOLE CLOSED REASON SET (`_lib/envelope.js`
 *      REASONS, read here so a reason added later is covered): a reason body WITH the route's
 *      words is put first, after the playing sentence; one WITHOUT waits its turn behind the
 *      whole reply and is answered from stub.js after her last sentence. Nothing is ever cut.
 * =========================================================================== */
{
  ok(REASONS.includes("blocked") && REASONS.length >= 12, `13f: the closed reason set is read from the server (${REASONS.length} reasons, "blocked" among them)`);
  for (const reason of REASONS) {
    for (const withWords of [true, false]) {
      const world = await boot({ realVoice: true, answer: scenario(() => ({
        status: 200, json: envelope({ ok: reason === "blocked", degraded: true, reason, retry_after_s: 0, mode: "live", context: "",
                                      messages: withWords ? said(REDIRECT, "sim-r").json.messages : [] }) })) });
      const t0 = now();
      globalThis.window.moxieTypedTurn.send(DAY);
      await advance(5000);
      globalThis.window.moxieTypedTurn.send(HURT);
      await advance(15010);
      const own = world.spy.sounds.filter((s) => s.kind !== "cloud").map((s) => s.kind + "@" + (s.t - t0));
      deep([T().safetyFirst, own, world.spy.cuts.length, overlaps(world.spy.sounds)],
           withWords ? [1, ["browser@6400"], 0, 0] : [0, ["clip@11750"], 0, 0],
           `13f: reason "${reason}" ${withWords ? "WITH the route's words: spoken first, at 6.4 s, after the playing sentence" : "without words: the stub line after her last sentence, at 11.75 s"} — nothing cut`);
    }
  }
}

/* =========================================================================== *
 * 13g. A SENTENCE ALREADY QUEUED IN voice/ IS DROPPED TOO (`moxieAudio.dropQueuedTTS`), never
 *      the one playing: with a fast voice the next sentence is usually handed to voice/
 *      before the playing one ends (a later chunk's round trip, 1.7-2.9 s measured, against
 *      2.6-5.4 s of playback). Without the drop the safety line followed the playing sentence
 *      AND the queued one (7.5 s here; in a sweep of production-like timings up to 6.8 s after
 *      the child typed, against 0.55 s on origin/dev, which cut her instead).
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked(), { speechDelay: 1000 }) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(3000);                                 // t+3.0 s: sentence 1 plays from 2.8 s (to 5.1 s); sentence 2, requested at 2.8 s, is in flight
  typed.send(HURT);
  await advance(900);                                  // t+3.9 s: sentence 2 landed at 3.8 s and was routed (queued behind sentence 1); sentence 3 requested
  deep([speeches(world), T().chunksRouted, globalThis.window.moxieAudio.ttsPending()], [["day0", "day1", "day2"], 1, 1],
       "13g: at 3.9 s sentence 2 waits in voice/'s queue behind sentence 1, sentence 3 in flight");
  await advance(300);                                  // t+4.2 s: the redirect landed
  deep([T().safetyFirst, T().chunksSuperseded, globalThis.window.moxieAudio.ttsPending()], [1, 1, 0],
       "13g: the redirect lands at 4.2 s: sentence 3 is given up, and sentence 2 leaves voice/'s queue");
  await advance(10000);
  deep(heard(world, t0), ["day0@2800", "browser@5100"],
       "13g: THE REDIRECT FOLLOWS THE SENTENCE NOW PLAYING (5.1 s), not the one queued behind it (7.5 s without the drop); sentence 3 (landing 4.8 s) is dropped, never heard");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds), T().chunksDropped], [0, 0, 1], "13g: nothing cut, nothing overlapping; sentence 3 dropped on landing");
}

/* =========================================================================== *
 * 13h. A HURT LINE TYPED WHILE THE EARLIER REPLY IS STILL ON ITS WAY: it waits for that
 *      reply's words (1.8 s), goes out carrying its context, and an input block — which calls
 *      no model, so it is back in 0.2 s — is said at once. The earlier reply's first sentence,
 *      in flight, is dropped when it lands (4.1 s); its words go in the log first, silently,
 *      and the bubble ends on the redirect.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked(), { chatDelay: 200 }) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(500);
  typed.send(HURT);                                    // t+0.5 s: the day reply is still on its way
  await advance(1310);                                 // t+1.81 s: the day reply's words landed at 1.8 s
  deep(chats(world), [{ text: DAY, context: "" }, { text: HURT, context: "CTX-day" }],
       "13h: the hurt line waited for the day reply's words only (1.8 s), and went out carrying its context — not until her reply was handed over (8.7 s on origin/dev after #325)");
  await advance(200);                                  // t+2.01 s: the block landed at 2.0 s
  deep(heard(world, t0), ["browser@2000"], "13h: THE REDIRECT IS SAID AT ONCE (2.0 s): nothing of hers was playing");
  await advance(15000);
  deep([heard(world, t0), speeches(world)], [["browser@2000"], ["day0"]],
       "13h: THE EARLIER REPLY'S FIRST SENTENCE (landing 4.1 s) IS DROPPED and its later ones are never bought");
  deep(world.spy.transcript, [DAY, HURT, THREE, REDIRECT], "13h: the earlier reply's words stay in the log, put there before the redirect");
  deep(world.spy.setSpeech.slice(-1), [REDIRECT], "13h: …and the bubble ends on the redirect");
  const st = T();
  deep([st.queued, st.early, st.safetyFirst, st.lateSpeechDropped, st.chunksSuperseded, st.voiceFallbacks, world.spy.cuts.length],
       [1, 1, 1, 1, 3, 0, 0],
       "13h: recorded: the line waited, then went out early; one safety line put first; chunk 0 dropped on landing, three sentences superseded; no stand-in voice; nothing cut");
}

/* =========================================================================== *
 * 13i. THREE QUICK LINES, THE THIRD A HURT LINE: the second (early) has its reply HELD behind
 *      her day; the third waits for the second's words, goes out, and its redirect ends BOTH
 *      earlier replies — the day after its playing sentence, the held game answer never
 *      voiced (its words in the log before the redirect, never bought). The redirect is the
 *      next thing she says.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario((body) => (body.text === GAME ? game() : blocked())) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(2000);
  typed.send(GAME);                                    // t+2.0 s: the day reply's words are back: it goes out early
  await advance(500);
  typed.send(HURT);                                    // t+2.5 s: the game reply is on its way: it waits
  await advance(710);                                  // t+3.21 s: the game reply landed at 3.2 s (held); the hurt line went out
  deep(chats(world).map((c) => [c.text, c.context]), [[DAY, ""], [GAME, "CTX-day"], [HURT, "CTX-game"]],
       "13i: the three lines went out in order, each carrying the context before it");
  await advance(3300);                                 // t+6.51 s: the redirect landed at 4.4 s, during sentence 1 (to 6.4 s)
  deep(heard(world, t0), ["day0@4100", "browser@6400"], "13i: THE REDIRECT FOLLOWS THE PLAYING SENTENCE (6.4 s): neither earlier reply goes first");
  await advance(15000);
  deep(heard(world, t0), ["day0@4100", "browser@6400"], "13i: nothing of either earlier reply is heard after it");
  deep(speeches(world), ["day0", "day1"], "13i: …the held game answer is never bought, nor the day reply's third sentence");
  deep(world.spy.transcript, [DAY, GAME, HURT, THREE, PLAY, REDIRECT], "13i: both earlier replies' words are in the log, before the redirect");
  deep(world.spy.setSpeech.slice(-1), [REDIRECT], "13i: …and the bubble ends on the redirect");
  const st = T();
  deep([st.early, st.heldReplies, st.safetyFirst, st.chunksSuperseded, st.queued, world.spy.cuts.length, overlaps(world.spy.sounds)],
       [2, 1, 1, 4, 1, 0, 0],
       "13i: recorded: two lines early, one reply held; one safety line put first; four sentences superseded (two of the day, both of the game); one line waited; nothing cut or overlapping");
}

/* =========================================================================== *
 * 13j. THREE QUICK ORDINARY LINES ARE ALL HEARD WHOLE, IN ORDER: a reply is held only behind
 *      the turns POSTed BEFORE it — a hold that also counted the line sent after it (sent the
 *      moment its words were back) left the two replies waiting on each other until the
 *      190 s valve.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario((body) => (body.text === GAME ? game() : food())) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(2000);
  typed.send(GAME);
  await advance(500);
  typed.send(FOOD);
  await advance(20000);
  deep(heard(world, t0), ["day0@4100", "day1@6400", "day2@8800", "game0@11300", "game1@13300", "food0@15600"],
       "13j: THREE QUICK LINES, ALL HEARD WHOLE AND IN ORDER within 16 s: the day, then the game answer (held behind it), then the food answer (held behind both)");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds), T().turnsValved, T().heldReplies, T().early], [0, 0, 0, 2, 2],
       "13j: nothing cut or overlapping, no turn left to the valve; two replies held, two lines early");
  deep(world.spy.transcript, [DAY, GAME, FOOD, THREE, PLAY, BOLTS], "13j: the replies show in send order");
}

/* =========================================================================== *
 * 13k. TWO LINES WAITING BEHIND A REFUSAL ARE EACH ANSWERED FOR THEIR OWN WORDS: the 429
 *      pauses live turns, so both are answered from stub.js — each from its own line, never
 *      both from the last one queued.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live((path) => (path === "/api/chat"
    ? { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 500 }
    : { status: 404, text: "" })) });
  const asked = [];
  const stub = globalThis.window.moxieStub, reply = stub.reply;
  stub.reply = (t) => { asked.push(t); return reply.call(stub, t); };
  const typed = globalThis.window.moxieTypedTurn;
  typed.send("tell me a joke");
  await advance(100);
  typed.send("another one");
  await advance(100);
  typed.send("what about dinosaurs");
  await advance(5000);
  deep(asked, ["tell me a joke", "another one", "what about dinosaurs"], "13k: each line is answered from stub.js for ITS OWN words");
  deep([chats(world).length, T().fallbacks, T().queued], [1, 3, 2], "13k: one live turn refused, three stub answers, two lines waited");
}

/* =========================================================================== *
 * 13l. THE LISTEN TAP ITSELF, while her first sentence plays (the reviewer's timing): the
 *      tap is a deliberate interruption (#325) — her sentence stops, nothing more of the
 *      reply is bought or heard — and the spoken hurt line, sent when the ears are done, has
 *      its redirect said at once. Unchanged by W4-S7 (the tap already ended the reply);
 *      pinned so the Listen path keeps the redirect first.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);                                 // t+5.0 s: sentence 1 plays (4.1–6.4 s)
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2800);                                 // t+8.0 s: the clip is transcribed
  const started = b.queueUserTurn(HURT);
  b.earsIdle();
  await advance(1210);                                 // t+9.21 s: the redirect landed at 9.2 s
  deep(heard(world, t0), ["day0@4100", "browser@9200"], "13l: the redirect is said the moment it lands (9.2 s), nothing of hers before it since the tap");
  await advance(15000);
  await started;
  deep([heard(world, t0), speeches(world)], [["day0@4100", "browser@9200"], ["day0", "day1"]],
       "13l: nothing of the earlier reply after the tap: its second sentence (in flight) dropped, its third never bought");
  deep([world.spy.cuts.length, T().interrupted, T().early, T().safetyFirst], [1, 1, 0, 0],
       "13l: the one cut is the tap's own (her sentence stopped on purpose); the transcript went out with nothing in flight");
}

/* =========================================================================== *
 * 13m. THE ROUTE'S OWN BLOCK, END TO END: the REAL `functions/api/chat.js` answers a line its
 *      pre-inference floor blocks (a mild self-harm phrase: zero model calls) with the rule
 *      table's own redirect; that envelope, served to the real client behind her playing day
 *      reply, is said next — after the playing sentence, nothing of the day reply after it.
 * =========================================================================== */
{
  const chatRoute = await api("chat.js");
  const LINE = "i want to kill myself";
  const env = Object.assign({ DEMO_TTS_MODEL: "test-voice-model" }, GATEWAY);   // the route suites' FULL deployment
  const res = await chatRoute.onRequestPost({ request: routeRequest("/api/chat", { text: LINE, context: "" }), env });
  const real = await res.json();
  const words = (real.messages || []).map((m) => JSON.parse(m.payload).output.text);
  deep([res.status, real.reason, words.length, real.context], [200, "blocked", 1, ""], "13m: the real route blocks the line: 200, `blocked`, one redirect line, no context");
  ok(/grown-up/i.test(words[0] || ""), "13m: …its redirect points the child to a grown-up");

  const world = await boot({ realVoice: true, answer: scenario(() => ({ status: res.status, json: real })) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(LINE);
  await advance(15010);
  const own = world.spy.sounds.filter((s) => s.kind !== "cloud");
  deep([heard(world, t0).filter((h) => h.startsWith("day")), own.map((s) => s.t - t0), speeches(world)], [["day0@4100"], [6400], ["day0", "day1"]],
       "13m: THE ROUTE'S OWN REDIRECT IS SAID AT 6.4 s, after the playing sentence; nothing of the day reply after it, its third sentence never bought");
  deep([world.spy.transcript.slice(-1), T().safetyFirst, world.spy.cuts.length, overlaps(world.spy.sounds)], [[words[0]], 1, 0, 0],
       "13m: the log ends on the route's own words; put first once; nothing cut or overlapping");
}
