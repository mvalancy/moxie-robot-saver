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
  MANIFEST, advance, boot, clipBytes, deep, envelope, eq, live, now, ok, said, tickets, voicedChunk,
} from "./harness.mjs";
import { bootPage } from "../ears/harness.mjs";     // §13w: the whole page, with the real mic.js
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
const DAD = "my dad hits me";
const THREE = "I chased a sunbeam. Then I counted dust. Then I had a nap.";
const PLAY = "Hide and seek, in the dark. I always win.";
const BOLTS = "Sparkly bolts, crunchy ones.";
/** A grown-up referral of the rule table's shape. No shipped clip: the browser voice says it. */
const REDIRECT = "Ouch, that sounds like it really hurts. Please go and tell a grown-up right now, so they can look at your arm.";
/** A SERVED reply ending on a grown-up referral: reason null, voiced like any reply (#327's
 *  shape for a soft hurt disclosure, the PR's honest gap: held like any reply). */
const REFER = "I am so sorry that happened to you. Please tell a grown-up you trust, like a teacher, today.";
/** Each sentence's length, which names its sound (a cloud chunk is recorded by its duration). */
const DUR = { "sim-day": [2.3, 2.4, 2.5], "sim-game": [1.5, 1.6], "sim-food": [1.7], "sim-dad": [2.1, 2.2] };
/** Production-like sentence lengths (§13t). */
const PROD = { "sim-day": [3.1, 3.6, 4.1], "sim-game": [2.5, 2.8], "sim-dad": [2.9, 3.3] };
const name = (s, dur = DUR) => {
  for (const [eid, ds] of Object.entries(dur)) { const k = ds.findIndex((d) => Math.round(d * 1000) === s.dur); if (k >= 0) return eid.slice(4) + k; }
  return s.kind;
};
const heard = (w, t0, dur = DUR) => w.spy.sounds.map((s) => name(s, dur) + "@" + (s.t - t0));
/** [start, end] of a recorded sound: a cloud chunk or clip lasts its `dur`, the browser voice
 *  ~70 ms a character (the harness's fake). */
const span = (s) => [s.t, s.t + (s.kind === "browser" ? 70 * s.text.length : s.dur)];
const overlaps = (sounds) => { let n = 0; for (let i = 1; i < sounds.length; i++) if (span(sounds[i])[0] < span(sounds[i - 1])[1]) n++; return n; };

/** The day reply after 1.8 s; every /api/speech answers in `speechDelay` ms with the
 *  sentence's own length of audio (from `dur`); any other line gets `other(body)` after
 *  `chatDelay` ms. */
const scenario = (other, o) => {
  const opt = Object.assign({ chatDelay: 1200, speechDelay: 2300, dur: DUR }, o || {});
  return live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return Object.assign(other(body), { delayMs: opt.chatDelay });
    }
    if (path === "/api/speech") { const [, eid, k] = ticketOf(body); return voicedChunk(eid, Number(k), { delayMs: opt.speechDelay, seconds: opt.dur[eid][Number(k)] }); }
    return { status: 404, text: "" };
  });
};
/** The route's input block: `ok: true`, `reason: "blocked"`, its redirect line, no context. */
const blocked = (over) => said(REDIRECT, "sim-safe", Object.assign({ ok: true, degraded: true, reason: "blocked", mode: "live", context: "" }, over || {}));
/** A served two-sentence answer to the game line, and a one-sentence one to the food line. */
const game = (over) => said(PLAY, "sim-game", Object.assign({ speech: tix("sim-game", 2), context: "CTX-game" }, over || {}));
const food = () => said(BOLTS, "sim-food", { speech: tix("sim-food", 1), context: "CTX-food" });
/** The served two-sentence referral to the dad line. */
const referral = () => said(REFER, "sim-dad", { speech: tix("sim-dad", 2), context: "CTX-dad" });
/** What was cut, and when: her cloud voice, or the browser voice. */
const cutsAt = (w, t0) => w.spy.cuts.map((c) => (c.text ? "browser" : "cloud") + "@" + (c.t - t0));

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

/* (§13n, #327's referral on a refusal end to end, lands with #327.) */

/* =========================================================================== *
 * 13o. AN EARLIER REPLY WHOSE FIRST SENTENCE IS SLOWER THAN THE 2.5 s WORD WAIT: the hurt line
 *      typed at 2.0 s goes out at once (the day reply's words are back at 1.8 s, held for
 *      their voice); its redirect lands at 3.2 s, nothing of hers playing, and is said at once.
 *      The safety line puts the day reply's words in the log first, silently — and ONCE: the
 *      word wait elapsing at 4.3 s routes nothing more (`words()`), and the first sentence,
 *      landing at 4.8 s, is dropped. (origin/dev after #325: the redirect at 12.0 s, cutting
 *      her last sentence.)
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked(), { speechDelay: 3000 }) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  typed.send(DAY);
  await advance(2000);
  typed.send(HURT);
  await advance(1210);                                 // t+3.21 s: the block landed at 3.2 s
  deep([heard(world, t0), world.spy.transcript], [["browser@3200"], [DAY, HURT, THREE, REDIRECT]],
       "13o: THE REDIRECT IS SAID AT 3.2 s, the day reply's words put in the log before it, silently");
  await advance(15000);
  deep([heard(world, t0), speeches(world)], [["browser@3200"], ["day0"]],
       "13o: nothing of the day reply is heard: its first sentence (landing 4.8 s) dropped, the rest never bought");
  deep(world.spy.transcript, [DAY, HURT, THREE, REDIRECT],
       "13o: THE DAY REPLY'S WORDS ARE IN THE LOG ONCE — the 2.5 s word wait elapsing at 4.3 s routed nothing more");
  deep(world.spy.setSpeech.slice(-1), [REDIRECT], "13o: …and the bubble ends on the redirect");
  const st = T();
  deep([st.early, st.safetyFirst, st.chatFirst, st.lateSpeechDropped, st.chunksSuperseded, world.spy.cuts.length],
       [1, 1, 1, 1, 3, 0],
       "13o: recorded: one line early, one safety line put first; the word wait elapsed once; chunk 0 dropped on landing, three sentences superseded; nothing cut");
}

/* =========================================================================== *
 * 13p. THE LISTEN TAP RELEASES A WAITING REDIRECT INTO THE EARS, NOT AT THEM: the hurt line
 *      typed at 3.2 s has its redirect back at 4.4 s, waiting for her first sentence
 *      (4.1–6.4 s); the child taps Listen at 5.0 s and the recorder runs 120 ms, 400 ms or
 *      1.5 s later. The tap stops her sentence, and the redirect, already in hand, waits for
 *      the microphone, then for the ears: HEARD WHOLE once the clip is done (8.0 s) — as on
 *      origin/dev, where the line went out at the tap and its reply landed into the open
 *      microphone. Before (the W4-S7 review): said at 5.1 s and cut by the recorder opening
 *      (20 ms of its 7.7 s heard at 120 ms, 1.4 s at 1.5 s). Then the redirect landing
 *      BETWEEN the tap and a slow microphone (a 1.5 s grant): held the same way (before: said
 *      at 6.2 s; origin/dev: at 6.7 s; both cut as the microphone opened at 7.0 s).
 * =========================================================================== */
for (const gap of [120, 400, 1500]) {
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(3200);
  globalThis.window.moxieTypedTurn.send(HURT);         // t+3.2 s: out at once (the day words are back)
  await advance(1800);                                 // t+5.0 s: the redirect landed at 4.4 s and waits for her sentence
  deep([T().safetyFirst, heard(world, t0)], [1, ["day0@4100"]], `13p (${gap} ms): at 5.0 s the redirect is in hand, waiting for her playing sentence`);
  b.interruptVoice();                                  // the Listen tap
  await advance(gap);
  b.earsOpen(45000);                                   // the recorder runs
  await advance(2990 - gap);                           // t+7.99 s
  deep(heard(world, t0), ["day0@4100"],
       `13p (${gap} ms): NOTHING IS SAID INTO THE OPENING MICROPHONE OR THE RECORDING (before: the redirect at 5.1 s, cut as the recorder opened at ${(5000 + gap) / 1000} s)`);
  await advance(10);
  b.earsIdle();                                        // t+8.0 s: the clip dropped as silence: nothing queued
  await advance(15000);
  deep(heard(world, t0), ["day0@4100", "browser@8000"], `13p (${gap} ms): THE REDIRECT IS SAID THE MOMENT THE EARS ARE DONE (8.0 s), as on origin/dev`);
  deep(cutsAt(world, t0), ["cloud@5000"], `13p (${gap} ms): …and heard WHOLE: the one cut is the tap's own (her sentence, 5.0 s)`);
  deep([speeches(world), world.spy.transcript], [["day0", "day1"], [DAY, HURT, THREE, REDIRECT]],
       `13p (${gap} ms): nothing more of the day reply bought; the log in order`);
  const st = T();
  deep([st.safetyFirst, st.heldAtTap, st.tapValved, st.heldForEars, st.interrupted], [1, 1, 0, 1, 1],
       `13p (${gap} ms): recorded: one safety line put first; it waited for the microphone the tap asked for, then for the ears; one interruption`);
}
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(HURT);         // t+5.0 s: out at once; its block lands at 6.2 s
  await advance(500);
  b.interruptVoice();                                  // t+5.5 s: the Listen tap; the browser asks for 1.5 s
  await advance(1490);                                 // t+6.99 s: the redirect landed at 6.2 s, nothing of hers playing
  deep(heard(world, t0), ["day0@4100"], "13p (lands after the tap): THE REDIRECT LANDING WHILE THE BROWSER ASKS WAITS for the microphone (before: said at 6.2 s; origin/dev: at 6.7 s; both cut as it opened)");
  await advance(10);
  b.earsOpen(45000);                                   // t+7.0 s
  await advance(1500);
  b.earsIdle();                                        // t+8.5 s
  await advance(15000);
  deep([heard(world, t0), cutsAt(world, t0)], [["day0@4100", "browser@8500"], ["cloud@5500"]],
       "13p (lands after the tap): …and is said whole once the ears are done (8.5 s); the one cut is the tap's own");
  deep([T().heldAtTap, T().heldForEars, T().tapValved], [1, 1, 0], "13p (lands after the tap): recorded: held for the microphone, then for the ears");
}
{
  // A SHORT RECORDING: the microphone open at once hands the redirect to the ears, so it is
  // said the moment the clip is done (1.0 s after the tap) — not at the 2 s bound.
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(3200);
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(1800);
  b.interruptVoice();                                  // t+5.0 s
  await advance(200);
  b.earsOpen(45000);
  await advance(800);
  b.earsIdle();                                        // t+6.0 s: the clip is done
  await advance(15000);
  deep([heard(world, t0), cutsAt(world, t0)], [["day0@4100", "browser@6000"], ["cloud@5000"]],
       "13p (a short recording): THE REDIRECT IS SAID THE MOMENT THE EARS ARE DONE (6.0 s), not at the tap's 2 s bound (7.0 s), and heard whole");
}

/* =========================================================================== *
 * 13q. THE LISTEN TAP RELEASES A HELD REPLY INTO THE EARS, NOT AT THEM: a line typed at 5.0 s
 *      went out early and its reply, back at 6.2 s, is held behind her day reply; the child
 *      taps Listen at 6.3, 7.0 or 8.6 s (before that reply is handed over, 8.7 s). The tap
 *      ends the day reply, which releases the held one — into the microphone the tap asked
 *      for, then the ears: nothing of it bought before the clip is done (3.0 s after the
 *      tap), then HEARD WHOLE, exactly as on origin/dev (the line went out at the tap; its
 *      reply landed into the open microphone). Both for an ordinary answer and for a SERVED
 *      grown-up referral (reason null). Before (the W4-S7 review): its first sentence bought
 *      at the tap, dropped by the recorder opening, 0 ms of it heard.
 * =========================================================================== */
for (const [line, eid, answer] of [[GAME, "sim-game", game], [DAD, "sim-dad", referral]]) {
  for (const tap of [6300, 7000, 8600]) {
    const label = `13q (${eid.slice(4)}, tap ${tap / 1000} s)`;
    const world = await boot({ realVoice: true, answer: scenario(() => answer()) });
    const t0 = now();
    const b = globalThis.window.moxieBridge;
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(line);       // t+5.0 s: out early; its reply is held from 6.2 s
    await advance(tap - 5000);
    deep(T().heldReplies, 1, `${label}: its reply is held behind her day reply when Listen is tapped`);
    b.interruptVoice();
    await advance(200);
    b.earsOpen(45000);
    await advance(2790);                               // 10 ms before the clip is done
    const day = tap > 6400 ? ["day0@4100", "day1@6400"] : ["day0@4100"];
    deep([heard(world, t0), speeches(world).filter((s) => !s.startsWith("day"))], [day, []],
         `${label}: NOTHING OF THE HELD REPLY IS BOUGHT OR HEARD BEFORE THE EARS ARE DONE (before: its first sentence bought at the tap and dropped)`);
    await advance(10);
    b.earsIdle();                                      // 3.0 s after the tap: the clip dropped as silence
    await advance(15000);
    const k = eid.slice(4);
    deep(heard(world, t0), day.concat([`${k}0@${tap + 5300}`, `${k}1@${tap + 7600}`]),
         `${label}: THE HELD REPLY IS HEARD WHOLE ONCE THE EARS ARE DONE: bought then, its sentences at ${(tap + 5300) / 1000} s and ${(tap + 7600) / 1000} s, as on origin/dev`);
    deep([cutsAt(world, t0), world.spy.transcript.slice(-1)], [[`cloud@${tap}`], [answer === game ? PLAY : REFER]],
         `${label}: the one cut is the tap's own; the log ends on the reply`);
    const st = T();
    deep([st.early, st.heldReplies, st.heldAtTap, st.heldForEars, st.tapValved, st.interrupted, st.safetyFirst], [1, 1, 1, 1, 0, 1, 0],
         `${label}: recorded: one line early, its reply held behind her day, then for the microphone and the ears; one interruption`);
  }
}

/* =========================================================================== *
 * 13r. …AND NEVER FOR GOOD (#325's rule): with a microphone that never opens — a permission
 *      prompt left unanswered, a capture that failed (mic.js tells the transport nothing
 *      then) — what the tap released goes on TAP_HOLD_MAX_MS (2 s) after it: the waiting
 *      redirect is said at 7.0 s, the held reply bought at 9.0 s. The bound's cost, pinned:
 *      a microphone opening after it (3.0 s after the tap) cuts the redirect that began at
 *      7.0 s, as it cuts any reply that began while the browser asked (ears B17d).
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(3200);
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(1800);
  globalThis.window.moxieBridge.interruptVoice();      // t+5.0 s; the microphone never opens
  await advance(1990);
  deep(heard(world, t0), ["day0@4100"], "13r: the redirect waits for the microphone the tap asked for…");
  await advance(15000);
  deep([heard(world, t0), cutsAt(world, t0)], [["day0@4100", "browser@7000"], ["cloud@5000"]],
       "13r: …2 s at most: with no microphone it is SAID AT 7.0 s, and heard whole");
  deep([T().heldAtTap, T().tapValved, T().heldForEars], [1, 1, 0], "13r: recorded: held at the tap, released by the bound");
}
{
  const world = await boot({ realVoice: true, answer: scenario(() => game()) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(GAME);
  await advance(2000);
  globalThis.window.moxieBridge.interruptVoice();      // t+7.0 s; the microphone never opens
  await advance(20000);
  deep([heard(world, t0), cutsAt(world, t0)], [["day0@4100", "day1@6400", "game0@11300", "game1@13600"], ["cloud@7000"]],
       "13r: THE HELD REPLY GOES ON 2 s AFTER THE TAP (bought at 9.0 s, heard from 11.3 s), whole");
  deep([T().heldAtTap, T().tapValved], [1, 1], "13r: recorded: held at the tap, released by the bound");
}
{
  const world = await boot({ realVoice: true, answer: scenario(() => blocked()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(3200);
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(1800);
  b.interruptVoice();                                  // t+5.0 s; the browser asks for 3.0 s
  await advance(3000);
  b.earsOpen(45000);                                   // t+8.0 s: past the bound
  await advance(3000);
  b.earsIdle();
  await advance(15000);
  deep([heard(world, t0), cutsAt(world, t0)], [["day0@4100", "browser@7000"], ["cloud@5000", "browser@8000"]],
       "13r: THE BOUND'S COST: a microphone opening 3.0 s after the tap cuts the redirect that began at 7.0 s (its words stay in the log), as ears B17d pins for any reply begun while the browser asked");
  ok(world.spy.transcript.includes(REDIRECT), "13r: …the redirect's words stay in the log");
}

/* =========================================================================== *
 * 13s. AN EARLY REPLY WHOSE OWN VOICE IS REFUSED NEVER CUTS HER: its stand-in (its words in
 *      the local voice) waits for her last sentence, as its words would have (§13d). The game
 *      answer, released when her day reply was handed over (8.7 s), has its voice refused — a
 *      429 after 1.5 s, or after 100 ms — and the stand-in follows her last sentence (11.3 s)
 *      instead of cutting it (the PR head before this: 10.2 s and 8.8 s, cutting; origin/dev:
 *      11.4 s, and 10.0 s cutting). And a newer voice taking the speakers meanwhile drops it:
 *      a hurt line typed at 10.5 s, with nothing in flight, has its redirect said at once (as
 *      today), and the stand-in never speaks over it.
 * =========================================================================== */
{
  const refused = (ms) => live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return body.text === GAME ? Object.assign(game(), { delayMs: 1200 }) : Object.assign(blocked(), { delayMs: 200 });
    }
    if (path === "/api/speech") {
      const [, eid, k] = ticketOf(body);
      if (eid === "sim-game") return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: ms };
      return voicedChunk(eid, Number(k), { delayMs: 2300, seconds: DUR[eid][Number(k)] });
    }
    return { status: 404, text: "" };
  });
  const standIn = (w, t0) => w.spy.said.filter((s) => s.text === PLAY).map((s) => s.t - t0);
  for (const ms of [1500, 100]) {
    const world = await boot({ realVoice: true, answer: refused(ms) });
    const t0 = now();
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(GAME);
    await advance(20000);
    deep([heard(world, t0), standIn(world, t0), cutsAt(world, t0)], [["day0@4100", "day1@6400", "day2@8800", "browser@11300"], [11300], []],
         `13s (a 429 after ${ms} ms): THE STAND-IN FOLLOWS HER LAST SENTENCE (11.3 s), NOTHING CUT (the PR head before this: ${ms === 1500 ? "10.2" : "8.8"} s, cutting it)`);
    deep([T().voiceFallbacks, T().speechRefused, T().heldReplies, world.spy.transcript.slice(-1)], [1, 1, 1, [PLAY]],
         `13s (a 429 after ${ms} ms): recorded: one stand-in for one refused voice; the reply held once; its words in the log`);
  }
  const world = await boot({ realVoice: true, answer: refused(1500) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(GAME);
  await advance(5500);
  globalThis.window.moxieTypedTurn.send(HURT);         // t+10.5 s: the game reply settled at 10.2 s, its stand-in waiting
  await advance(15000);
  deep([heard(world, t0), cutsAt(world, t0), standIn(world, t0)], [["day0@4100", "day1@6400", "day2@8800", "browser@10700"], ["cloud@10700"], []],
       "13s (a newer voice first): THE REDIRECT (10.7 s) TAKES THE SPEAKERS AND THE WAITING STAND-IN IS DROPPED, never spoken over it (the redirect cuts her sentence, as a line sent with nothing in flight does today)");
  deep([world.spy.transcript.slice(-3), T().early], [[PLAY, HURT, REDIRECT], 1], "13s (a newer voice first): the game answer's words stay in the log, before the hurt line and its redirect");
}

/* =========================================================================== *
 * 13t. THE LISTEN TAP AFTER HER EARLIER REPLY IS HANDED OVER KEEPS AN EARLY REPLY NOT YET
 *      HEARD (the W4-S7 review, round 2). Released the moment her day reply is handed over,
 *      the early line's reply is bought while her last sentences play; a tap then ended it
 *      unheard — where on origin/dev that line was only then going out, its reply landed into
 *      the open microphone, and was heard whole after the recording. Now the tap keeps it:
 *      nothing more of it bought, and its first sentence, in hand, said the moment the ears
 *      are done. For an ordinary answer and a SERVED grown-up referral (reason null) alike.
 *      (i) The review's own repro, production-like (2.0 s synthesis, sentences 3.1/3.6/4.1 s,
 *      a 1.8 s chat): the hand-over at 7.8 s, the tap at 8.5 s while her second sentence
 *      plays, the recorder 200 ms later, the clip dropped at 11.5 s. origin/dev: heard whole
 *      from 13.5 s; the PR head before this: not at all (its first sentence bought, dropped).
 * =========================================================================== */
for (const [line, eid, answer] of [[GAME, "sim-game", game], [DAD, "sim-dad", referral]]) {
  const k = eid.slice(4), label = `13t (${k}, production-like, tap 8.5 s)`;
  const world = await boot({ realVoice: true, answer: scenario(() => answer(), { chatDelay: 1800, speechDelay: 2000, dur: PROD }) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(line);         // t+5.0 s: out early; its reply is back at 6.8 s, held behind her day
  await advance(3500);                                 // t+8.5 s
  deep([heard(world, t0, PROD), speeches(world), T().heldReplies], [["day0@3800", "day1@6900"], ["day0", "day1", "day2", k + "0"], 1],
       `${label}: at the tap her day reply was handed over (7.8 s), her second sentence plays, and the early reply's first sentence is being bought`);
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2790);                                 // 10 ms before the clip is done
  deep([heard(world, t0, PROD), speeches(world)], [["day0@3800", "day1@6900"], ["day0", "day1", "day2", k + "0"]],
       `${label}: NOTHING OF IT IS SAID INTO THE MICROPHONE OR THE RECORDING, AND NOTHING MORE OF IT IS BOUGHT (its first sentence, landed at 9.8 s, is kept)`);
  await advance(10);
  b.earsIdle();                                        // t+11.5 s: the clip dropped as silence
  await advance(15000);
  deep(heard(world, t0, PROD), ["day0@3800", "day1@6900", `${k}0@11500`, `${k}1@${k === "game" ? 14000 : 14400}`],
       `${label}: THE EARLY REPLY IS HEARD WHOLE THE MOMENT THE EARS ARE DONE (11.5 s), its first sentence already in hand (origin/dev: whole from 13.5 s; the PR head before this: 0 ms)`);
  deep([cutsAt(world, t0), speeches(world), world.spy.transcript.slice(-1)], [["cloud@8500"], ["day0", "day1", "day2", k + "0", k + "1"], [answer === game ? PLAY : REFER]],
       `${label}: the one cut is the tap's own; its second sentence bought after the recording; the log ends on the reply`);
  const st = T();
  deep([st.early, st.heldReplies, st.parked, st.heldAtTap, st.heldForEars, st.tapValved, st.interrupted, st.lateSpeechDropped, st.chunksSuperseded],
       [1, 1, 1, 1, 1, 0, 1, 0, 0],
       `${label}: recorded: one early line, held behind her day; KEPT by the tap (parked), then held for the microphone and the ears; nothing dropped or superseded`);
}

/* 13t (ii). The same in §13's own timings, the tap at 9.0 s (her day reply handed over at
 *      8.7 s, her last sentence playing): heard whole from 12.0 s, the moment the ears are
 *      done (origin/dev: from 14.3 s; the PR head before this: not at all). */
for (const [line, eid, answer] of [[GAME, "sim-game", game], [DAD, "sim-dad", referral]]) {
  const k = eid.slice(4), label = `13t (${k}, §13 timings, tap 9.0 s)`;
  const world = await boot({ realVoice: true, answer: scenario(() => answer()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(line);
  await advance(4000);                                 // t+9.0 s
  deep(speeches(world), ["day0", "day1", "day2", k + "0"], `${label}: at the tap the early reply's first sentence is being bought (since the hand-over, 8.7 s)`);
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2800);
  b.earsIdle();                                        // t+12.0 s
  await advance(15000);
  deep(heard(world, t0), ["day0@4100", "day1@6400", "day2@8800", `${k}0@12000`, `${k}1@14300`],
       `${label}: THE EARLY REPLY IS HEARD WHOLE FROM 12.0 s, the moment the ears are done (origin/dev: from 14.3 s; the PR head before this: 0 ms)`);
  deep([cutsAt(world, t0), T().parked, T().heldForEars], [["cloud@9000"], 1, 1], `${label}: the one cut is the tap's own; kept by the tap, held for the ears`);
}

/* 13t (iii). …AND AN EARLY REPLY IS NEVER HANDED TO voice/ TO WAIT BEHIND HER, where a tap
 *      would drop it unheard: its first sentence, landed at 9.8 s, and its second (11.8 s)
 *      stay in hand until her last sentence ends — so a tap at 12.0 s, her last sentence
 *      playing (10.5-14.6 s), keeps them too, and the reply is heard whole after the
 *      recording. (The PR head before this queued both in voice/ and the tap dropped them;
 *      origin/dev queued its own the same way and lost the reply at every tap from 9.4 s to
 *      14.6 s.) */
for (const [line, eid, answer] of [[GAME, "sim-game", game], [DAD, "sim-dad", referral]]) {
  const k = eid.slice(4), label = `13t (${k}, production-like, tap 12.0 s)`;
  const world = await boot({ realVoice: true, answer: scenario(() => answer(), { chatDelay: 1800, speechDelay: 2000, dur: PROD }) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(line);
  await advance(7000);                                 // t+12.0 s
  deep([heard(world, t0, PROD), speeches(world), globalThis.window.moxieAudio.ttsPending(), world.spy.transcript],
       [["day0@3800", "day1@6900", "day2@10500"], ["day0", "day1", "day2", k + "0", k + "1"], 0, [DAY, THREE, line]],
       `${label}: both sentences of the early reply are in hand, NONE queued in voice/ behind her last sentence, its words not yet out`);
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2800);
  b.earsIdle();                                        // t+15.0 s
  await advance(15000);
  deep(heard(world, t0, PROD), ["day0@3800", "day1@6900", "day2@10500", `${k}0@15000`, `${k}1@${k === "game" ? 17500 : 17900}`],
       `${label}: THE EARLY REPLY IS HEARD WHOLE THE MOMENT THE EARS ARE DONE (15.0 s) — origin/dev and the PR head before this: never`);
  deep([cutsAt(world, t0), world.spy.transcript.slice(-1), T().parked], [["cloud@12000"], [answer === game ? PLAY : REFER], 1],
       `${label}: the one cut is the tap's own; its words go in the log with its voice; kept by the tap`);
}

/* 13t (iv). …AND AFTER ITS WORDS WENT OUT CHAT-FIRST: with a first sentence slower than the
 *      2.5 s word wait (3.0 s synthesis: the file's own measurement puts chunk 0's median
 *      near 2.5 s, so this branch is common), the early reply's words go out at 13.3 s still
 *      expecting their voice; a tap at 13.5 s, before that voice lands (13.8 s), keeps it:
 *      the sentence is played after the recording (16.5 s), not into it. (Without the
 *      chat-first branch's `ready` the voice landing at 13.8 s went straight to voice/ — into
 *      the recording — the round-3 review's surviving mutant.) */
{
  const world = await boot({ realVoice: true, answer: scenario(() => game(), { speechDelay: 3000 }) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(GAME);         // out early; its reply (6.2 s) held behind her day (handed over 10.8 s)
  await advance(8400);                                 // t+13.4 s: the word wait elapsed at 13.3 s
  deep([T().chatFirst, world.spy.transcript.slice(-1), heard(world, t0), speeches(world)], [2, [PLAY], ["day0@4800", "day1@7800", "day2@10800"], ["day0", "day1", "day2", "game0"]],
       "13t (iv): at 13.4 s the early reply's WORDS are out (chat-first, at 13.3 s; her day's went out chat-first too, its first sentence as slow), its first sentence still being bought (since 10.8 s), her last sentence over (13.3 s)");
  await advance(100);                                  // t+13.5 s
  b.interruptVoice();                                  // the tap, before the voice lands
  await advance(200);
  b.earsOpen(45000);                                   // t+13.7 s
  await advance(2790);                                 // t+16.49 s: the voice landed at 13.8 s, into the recording
  deep([heard(world, t0), T().parked, T().lateSpeechPlayed], [["day0@4800", "day1@7800", "day2@10800"], 1, 1],
       "13t (iv): THE VOICE LANDING AT 13.8 s IS KEPT, not played into the recording (the mutant played it at 13.8 s; the one late voice played so far is her day's first sentence)");
  await advance(10);
  b.earsIdle();                                        // t+16.5 s
  await advance(15000);
  deep(heard(world, t0), ["day0@4800", "day1@7800", "day2@10800", "game0@16500", "game1@19500"],
       "13t (iv): THE EARLY REPLY IS HEARD WHOLE ONCE THE EARS ARE DONE (16.5 s), its first sentence from hand, its second bought then");
  deep([T().chatFirst, T().lateSpeechPlayed, T().parked, T().heldAtTap, T().heldForEars, T().lateSpeechDropped, cutsAt(world, t0)], [2, 2, 1, 1, 1, 0, []],
       "13t (iv): recorded: words first twice (her day's and the early reply's), both late voices played; kept by the tap, held for the microphone and the ears; nothing dropped, nothing cut (she was silent at the tap)");
}

/* 13t (vi). …BUT AN EARLY REPLY ALREADY AUDIBLE AT THE TAP IS ENDED AS ANY (the child's
 *      deliberate interruption, honest gap 9): its first sentence stops, its second, still
 *      being synthesised (a slow 6.0 s for that chunk alone), is dropped when it lands into
 *      the recording, nothing kept. (Parking a reply whose voice has started would route that
 *      second sentence into the recording when it lands: the round-3 review's third surviving
 *      mutant.) Production-like timings: the early answer starts at 14.6 s, as her last
 *      sentence ends; the tap at 15.0 s. */
{
  const slowSecond = live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return Object.assign(game(), { delayMs: 1800 });
    }
    if (path === "/api/speech") { const [, eid, k] = ticketOf(body); return voicedChunk(eid, Number(k), { delayMs: eid === "sim-game" && Number(k) === 1 ? 6000 : 2000, seconds: PROD[eid][Number(k)] }); }
    return { status: 404, text: "" };
  });
  const world = await boot({ realVoice: true, answer: slowSecond });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(GAME);
  await advance(10000);                                // t+15.0 s: game0 plays from 14.6 s; game1, bought at 9.8 s, lands at 15.8 s
  deep([heard(world, t0, PROD), speeches(world)], [["day0@3800", "day1@6900", "day2@10500", "game0@14600"], ["day0", "day1", "day2", "game0", "game1"]],
       "13t (vi): at 15.0 s the early answer's first sentence is playing and its second is still being synthesised (since 9.8 s)");
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2800);
  b.earsIdle();                                        // t+18.0 s
  await advance(10000);
  deep([heard(world, t0, PROD), cutsAt(world, t0)], [["day0@3800", "day1@6900", "day2@10500", "game0@14600"], ["cloud@15000"]],
       "13t (vi): THE TAP ENDS AN ANSWER ALREADY AUDIBLE: its first sentence cut at the tap, its second — landing at 15.8 s, into the recording — dropped, never heard, nothing after the recording");
  deep([T().parked, T().chunksDropped, T().chunksSuperseded, T().interrupted], [0, 1, 1, 1],
       "13t (vi): recorded: nothing kept; the second sentence superseded at the tap and dropped on landing; one interruption");
}

/* 13t (v). A REPLY THAT IS NOT EARLY IS ENDED BY THE TAP AS BEFORE (the spec: a line sent with
 *      nothing in flight behaves exactly as today): its first sentence in flight at the tap is
 *      dropped when it lands, nothing of it kept — exactly what origin/dev does. (Parking every
 *      reply would keep it: the round-3 review's surviving mutant.) */
{
  const world = await boot({ realVoice: true, answer: scenario(() => game()) });
  const t0 = now();
  const b = globalThis.window.moxieBridge;
  globalThis.window.moxieTypedTurn.send(DAY);          // nothing in flight: not early
  await advance(3000);                                 // t+3.0 s: its first sentence is in flight (bought 1.8 s, landing 4.1 s)
  b.interruptVoice();
  await advance(200);
  b.earsOpen(45000);
  await advance(2800);
  b.earsIdle();                                        // t+6.0 s
  await advance(10000);
  deep([heard(world, t0), speeches(world), world.spy.transcript], [[], ["day0"], [DAY, THREE]],
       "13t (v): A REPLY NOT EARLY IS NOT KEPT: its first sentence, landing at 4.1 s, is dropped; nothing of it heard after the recording; its words in the log (as on origin/dev)");
  deep([T().parked, T().lateSpeechDropped, T().chunksSuperseded, T().interrupted, T().early], [0, 1, 3, 1, 0],
       "13t (v): recorded: nothing parked; chunk 0 dropped on landing, three sentences superseded; one interruption; no early line");
}

/* =========================================================================== *
 * 13u. AN EARLY REPLY'S STAND-IN IS KEPT BY THE TAP TOO: its voice refused (a 429 after
 *      1.5 s, §13 timings), its words are said locally — after the recording, when the tap
 *      lands (i) while its first sentence is still being bought (9.0 s: refused at 10.2 s,
 *      during the recording) or (ii) while the stand-in waits for her last sentence (10.5 s).
 *      Before (the PR head): dropped both times, never heard; origin/dev: said at 13.5 s at
 *      the 9.0 s tap, never at the 10.5 s one.
 * =========================================================================== */
{
  const refused = live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return Object.assign(game(), { delayMs: 1200 });
    }
    if (path === "/api/speech") {
      const [, eid, k] = ticketOf(body);
      if (eid === "sim-game") return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 1500 };
      return voicedChunk(eid, Number(k), { delayMs: 2300, seconds: DUR[eid][Number(k)] });
    }
    return { status: 404, text: "" };
  });
  for (const tap of [9000, 10500]) {
    const label = `13u (tap ${tap / 1000} s)`;
    const world = await boot({ realVoice: true, answer: refused });
    const t0 = now();
    const b = globalThis.window.moxieBridge;
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(GAME);
    await advance(tap - 5000);
    b.interruptVoice();
    await advance(200);
    b.earsOpen(45000);
    await advance(2790);
    deep(heard(world, t0), ["day0@4100", "day1@6400", "day2@8800"], `${label}: nothing of the early reply is said into the microphone or the recording`);
    await advance(10);
    b.earsIdle();                                      // 3.0 s after the tap
    await advance(15000);
    deep([heard(world, t0), cutsAt(world, t0), world.spy.said.filter((s) => s.text === PLAY).map((s) => s.t - t0)],
         [["day0@4100", "day1@6400", "day2@8800", "browser@" + (tap + 3000)], ["cloud@" + tap], [tap + 3000]],
         `${label}: THE STAND-IN IS SAID WHOLE THE MOMENT THE EARS ARE DONE (${(tap + 3000) / 1000} s); the one cut is the tap's own`);
    deep([T().voiceFallbacks, T().parked, T().heldAtTap, T().heldForEars, world.spy.transcript.slice(-1)], [1, tap === 9000 ? 1 : 0, 1, 1, [PLAY]],
         `${label}: recorded: one stand-in; ${tap === 9000 ? "the reply kept by the tap (parked), its voice refused during the recording" : "the stand-in itself held for the microphone and the ears"}; its words in the log`);
  }
}

/* =========================================================================== *
 * 13v. TWO PINS ON WHAT "HER VOICE IS QUIET" MEANS (`whenQuiet`).
 *      (i) The NARROW predicate, on purpose: her server voice only. A safety line landing while
 *      the earlier reply is said in the LOCAL voice (here its stand-in: its voice refused)
 *      cuts it at once, as a newer line always cut a local voice — it does not wait the
 *      whole stand-in out (a local voice is one utterance, not a sentence).
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      return Object.assign(blocked(), { delayMs: 1200 });
    }
    if (path === "/api/speech") return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 1000 };
    return { status: 404, text: "" };
  }) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(2000);
  globalThis.window.moxieTypedTurn.send(HURT);         // t+2.0 s: early (her day's voice is still being bought)
  await advance(15000);
  deep([world.spy.sounds.map((s) => (s.text === THREE ? "stand-in" : s.text === REDIRECT ? "redirect" : s.kind) + "@" + (s.t - t0)), cutsAt(world, t0)],
       [["stand-in@2800", "redirect@3200"], ["browser@3200"]],
       "13v (i): THE REDIRECT (3.2 s) CUTS HER LOCAL STAND-IN AT ONCE (said from 2.8 s, its voice refused) — the narrow predicate: it does not wait the stand-in out (6.9 s)");
  deep([T().early, T().safetyFirst, world.spy.transcript.slice(-1)], [1, 1, [REDIRECT]], "13v (i): an early safety line, put first; the log ends on the redirect");
}

/* 13v (ii). EXACT: an early reply's voice starts the moment her last sentence ends — told by
 *      voice/ (`moxie-tts-end`), not found by the 100 ms poll — so, held until she is quiet
 *      rather than queued in voice/, it still follows her with no gap: here her last sentence
 *      ends at 11.25 s, off the poll's grid (from the first sentence's landing, 11.0 s), and
 *      the answer starts at 11.25 s, not 11.3 s. */
{
  const OFF = Object.assign({}, DUR, { "sim-day": [2.3, 2.4, 2.45] });
  const world = await boot({ realVoice: true, answer: scenario(() => game(), { dur: OFF }) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send(DAY);
  await advance(5000);
  globalThis.window.moxieTypedTurn.send(GAME);
  await advance(15000);
  // (2.45 s of 22 050 Hz PCM is 54 023 samples: her sentence ends at 11 250.02 ms, hence the rounding.)
  deep(world.spy.sounds.map((s) => name(s, OFF) + "@" + Math.round(s.t - t0)), ["day0@4100", "day1@6400", "day2@8800", "game0@11250", "game1@13300"],
       "13v (ii): THE ANSWER FOLLOWS HER LAST SENTENCE (ending 11.25 s) WITH NO GAP — the moment voice/ says she is quiet");
  deep([world.spy.cuts.length, overlaps(world.spy.sounds)], [0, 0], "13v (ii): nothing cut, nothing overlapping");
}

/* =========================================================================== *
 * 13w. THE WHOLE PAGE, WITH THE REAL mic.js, WHEN THE EARS FAIL AFTER A TAP THAT KEPT AN
 *      ANSWER (the W4-S7 review, round 3). The child taps Listen while an early answer is in
 *      hand, talks 1.2 s, falls silent; the auto-stop uploads the clip and /api/transcribe
 *      refuses it (503 / 429). mic.js then composes its PRETEND LINE (`sendScriptedTurn`:
 *      the child clip, and a stub answer 450 ms on) BEFORE it says the ears are done, and the
 *      kept answer starts the moment they are: on the head before this, the child clip began
 *      over her first sentence (its fetch lands a tick later) and the stub answer's stop
 *      missed her — TWO VOICES AT ONCE, at every tap that kept an answer (origin/dev: the
 *      answer lost outright, its words in the log). Now the pretend line waits its turn: the
 *      kept answer is heard whole, then the child clip, then the stub answer. Production-like
 *      timings (chat 1.8 s, synthesis 2.0 s, the clip's fetch 30 virtual ms: a browser fetch
 *      is never same-tick); the tap at 8.5 s, her second sentence playing; the ears done at
 *      12.35 s. (i) An ordinary answer (503); (ii) a served grown-up referral (429) whose
 *      second sentence lands 500 ms AFTER its first has ended, so her voice is quiet in
 *      between (the pretend line waits for the whole reply, not for the first silence);
 *      (iii) an answer whose own voice is refused: its stand-in, a LOCAL voice; (iv) a hurt
 *      line's redirect held for the ears (the tap at 6.0 s, before it lands), which on
 *      origin/dev too was spoken under the pretend clip.
 * =========================================================================== */
{
  const CHILD_LINES = Object.keys(MANIFEST.child || {});
  const hosted = (other) => (path, body, spy) => (path === "/api/health" ? { status: 200, json: envelope({ ears: true }) } : other(path, body, spy));
  /** Production-like timings; `refuse` the voice of one event (a 429 after 1.5 s); `slow`
   *  one chunk's synthesis; `stt` what /api/transcribe answers after 1.5 s. */
  const degraded = (o) => hosted((path, body) => {
    if (path === "/api/chat") {
      if (body.text === DAY) return Object.assign(said(THREE, "sim-day", { speech: tix("sim-day", 3), context: "CTX-day" }), { delayMs: 1800 });
      if (body.text === GAME) return Object.assign(game(), { delayMs: 1800 });
      if (body.text === DAD) return Object.assign(referral(), { delayMs: 1800 });
      return Object.assign(blocked(), { delayMs: 1800 });
    }
    if (path === "/api/speech") {
      const [, eid, k] = ticketOf(body);
      if (eid === o.refuse) return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 1500 };
      return voicedChunk(eid, Number(k), { delayMs: o.slow && o.slow[0] === eid && o.slow[1] === Number(k) ? o.slow[2] : 2000, seconds: PROD[eid][Number(k)] });
    }
    if (path === "/api/transcribe") return o.stt === 429
      ? { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 1500 }
      : { status: 503, json: envelope({ ok: false, degraded: true, reason: "upstream_down", mode: "live" }), delayMs: 1500 };
    return { status: 404, text: "" };
  });
  /** The page, with a clip fetch that lands 30 virtual ms later (a browser fetch is never same-tick). */
  const pageWith = async (o) => {
    const p = await bootPage({ answer: degraded(o) });
    const real = globalThis.fetch;
    globalThis.fetch = (url, init) => (/audio\/(?!index\.json)/.test(String(url)) ? new Promise((r) => globalThis.setTimeout(() => r(real(url, init)), 30)) : real(url, init));
    return p;
  };
  /** Tap Listen, talk 1.2 s, fall silent: the auto-stop sends the clip 1.1 s on, refused 1.5 s later (the ears done 3.85 s after the tap). */
  const talk = async (p) => {
    p.mic.toggle();
    await advance(50);
    for (let i = 0; i < 4; i++) { p.level(0.09); await advance(300); }
    p.level(0.001);
  };
  /** Every sound as [name, start, end], the end where it was cut if it was: her sentences by
   *  length, the stand-in and the redirect by text, the pretend line's child clip and the stub
   *  answer's clip by their shipped bytes. */
  const lenOf = (s) => (s.kind === "browser" ? 70 * s.text.length : s.kind === "clip" ? Math.round((s.bytes * 8) / 64) : s.dur);
  const played = (w, t0, pretend, answer) => w.spy.sounds.map((s) => {
    const c = w.spy.cuts.find((x) => (s.kind === "browser" ? x.text === s.text : x.id === s.id) && x.t >= s.t);
    const n = s.kind === "browser" ? (s.text === REDIRECT ? "redirect" : s.text === PLAY ? "stand-in" : "browser")
      : s.kind === "clip" ? (s.bytes === clipBytes(pretend, "child") ? "child-clip" : s.bytes === clipBytes(answer, "moxie") ? "stub-clip" : "clip") : name(s, PROD);
    return [n, s.t - t0, Math.min(s.t + lenOf(s), c ? c.t : Infinity) - t0];
  });
  const overlapping = (rows) => { let n = 0; for (let i = 0; i < rows.length; i++) for (let j = 0; j < i; j++) if (rows[j][2] > rows[i][1]) n++; return n; };
  const pretendOf = (w) => w.spy.transcript.find((t) => CHILD_LINES.includes(t)) || "";

  // (i) An ordinary answer, kept by the tap; the ears refuse the clip (503).
  {
    const p = await pageWith({ stt: 503 });
    const w = p.world, t0 = now();
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(GAME);       // out early; its reply (6.8 s) held, then released at the hand-over (7.8 s), its first sentence being bought
    await advance(3500);                               // t+8.5 s
    await talk(p);                                     // the tap keeps it; the clip is sent at 10.85 s and refused at 12.35 s
    await advance(3850 - 1250 - 10);                   // t+12.34 s
    const pretend = pretendOf(w);
    deep([T().parked, p.mic.stats().fallbacks, pretend.length > 0, heard(w, t0, PROD)], [1, 0, false, ["day0@3800", "day1@6900"]],
         "13w (i): just before the ears are done the answer is kept (parked), nothing of it heard, no pretend line yet");
    await advance(20);                                 // t+12.36 s: the ears are done; mic.js composed its pretend line
    const line = pretendOf(w), answer = globalThis.window.moxieStub.reply(line).text;
    ok(CHILD_LINES.includes(line) && clipBytes(line, "child") > 0 && clipBytes(answer, "moxie") > 0,
       `13w (i): mic.js consoled with a scripted child line that has a shipped clip, and stub.js answers it with a line that has one (${JSON.stringify(line)})`);
    deep([p.mic.stats().fallbacks, T().scripted, heard(w, t0, PROD)], [1, 1, ["day0@3800", "day1@6900", "game0@12350"]],
         "13w (i): THE KEPT ANSWER STARTS THE MOMENT THE EARS ARE DONE (12.35 s) — and the pretend line, composed in the same moment, has NOT started over it");
    await advance(640);                                // t+13.0 s: a tap while she says it
    await p.mic.toggle();
    deep([p.mic.stats().ignoredTaps, p.mic.isRecording(), /one at a time/.test(p.micStatus())], [1, false, true],
         "13w (i): a Listen tap while the kept answer plays is refused as one at a time (mic.js holds its button until the pretend line's answer, as it does until a spoken line's)");
    await advance(15000);
    deep(played(w, t0, line, answer),
         [["day0", 3800, 6900], ["day1", 6900, 8500], ["game0", 12350, 14850], ["game1", 14850, 17650], ["child-clip", 17780, 18200], ["stub-clip", 18230, 22108]],
         "13w (i): HEARD, ONE AT A TIME: her day to the tap, then the kept answer WHOLE (12.35-17.65 s), then the pretend line's child clip (17.78 s, 100 ms after her last sentence and its fetch), then the stub answer cutting the child clip at the 450 ms beat as a child turn's always has (the head before this: child clip 12.38 s and stub 12.83 s, both over game0)");
    deep([overlapping(played(w, t0, line, answer)), w.spy.cuts.length], [0, 2], "13w (i): NO TWO SOUNDS OVERLAP; the two cuts are the tap's own and the stub's beat on the child clip");
    deep(w.spy.transcript, [DAY, THREE, GAME, line, PLAY, answer], "13w (i): the log: her day, the game line, the pretend line (shown at once), her kept answer, the stub answer");
    deep([T().scriptedWaited, T().scriptedFree, T().parked, T().heldForEars, T().chunksSuperseded, T().lateSpeechDropped], [1, 1, 1, 1, 0, 0],
         "13w (i): recorded: the pretend line waited for a reply in hand; free; one reply kept; nothing superseded or dropped");
  }

  // (ii) A served grown-up referral, kept by the tap; the ears refuse the clip (429); its
  //      second sentence lands 500 ms after its first has ended.
  {
    const p = await pageWith({ stt: 429, slow: ["sim-dad", 1, 3400] });
    const w = p.world, t0 = now();
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(DAD);
    await advance(3500);
    await talk(p);
    await advance(3850 - 1250 + 10);                   // t+12.36 s
    const line = pretendOf(w), answer = globalThis.window.moxieStub.reply(line).text;
    deep([p.mic.stats().fallbacks, T().parked, heard(w, t0, PROD)], [1, 1, ["day0@3800", "day1@6900", "dad0@12350"]],
         "13w (ii): the kept referral starts the moment the ears are done (12.35 s); the pretend line has not started over it");
    await advance(3200);                               // t+15.56 s: dad0 ended at 15.25 s; dad1 lands at 15.75 s
    deep([heard(w, t0, PROD), w.spy.sounds.filter((s) => s.kind === "clip").length], [["day0@3800", "day1@6900", "dad0@12350"], 0],
         "13w (ii): IN THE 500 ms GAP BETWEEN HER SENTENCES NOTHING STARTS: the pretend line waits for the whole reply, not for the first silence");
    await advance(15000);
    deep(played(w, t0, line, answer),
         [["day0", 3800, 6900], ["day1", 6900, 8500], ["dad0", 12350, 15250], ["dad1", 15750, 19050], ["child-clip", 19180, 19600], ["stub-clip", 19630, 23508]],
         "13w (ii): THE REFERRAL IS HEARD WHOLE (12.35-19.05 s, a gap between its sentences), then the child clip (19.18 s), then the stub answer");
    deep([overlapping(played(w, t0, line, answer)), w.spy.transcript], [0, [DAY, THREE, DAD, line, REFER, answer]],
         "13w (ii): no two sounds overlap; the log in order, the referral's words with its voice");
    deep([T().scriptedWaited, T().parked, T().chunksSuperseded, T().lateSpeechDropped], [1, 1, 0, 0], "13w (ii): recorded: the pretend line waited; one reply kept; nothing superseded or dropped");
  }

  // (iii) An answer whose own voice is refused (a 429 after 1.5 s, landing during the
  //       recording): its stand-in, a LOCAL voice said when the ears are done, is a voice of
  //       hers too — the pretend line waits for it (on the head before this: the child clip and
  //       the stub answer over the stand-in).
  {
    const p = await pageWith({ stt: 503, refuse: "sim-game" });
    const w = p.world, t0 = now();
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(GAME);
    await advance(3500);
    await talk(p);
    await advance(3850 - 1250 + 10);                   // t+12.36 s
    const line = pretendOf(w), answer = globalThis.window.moxieStub.reply(line).text;
    deep([T().voiceFallbacks, T().parked, w.spy.said.filter((s) => s.text === PLAY).map((s) => s.t - t0)], [1, 1, [12350]],
         "13w (iii): the kept answer's voice was refused during the recording; its STAND-IN is said the moment the ears are done (12.35 s)");
    await advance(15000);
    deep(played(w, t0, line, answer),
         [["day0", 3800, 6900], ["day1", 6900, 8500], ["stand-in", 12350, 15220], ["child-clip", 15280, 15700], ["stub-clip", 15730, 19608]],
         "13w (iii): THE STAND-IN IS HEARD WHOLE (12.35-15.22 s), then the child clip (15.28 s: the beat after it ended, and its fetch), then the stub answer");
    deep([overlapping(played(w, t0, line, answer)), T().scriptedWaited, w.spy.transcript], [0, 1, [DAY, THREE, GAME, line, PLAY, answer]],
         "13w (iii): no two sounds overlap; the pretend line waited for her voice; the log in order");
  }

  // (iv) A hurt line's redirect held for the ears: the tap at 6.0 s, before it lands (6.8 s).
  //      It is said the moment the ears are done (9.85 s) — and on origin/dev too the pretend
  //      line's clip and stub answer then played over it (the review's "pre-existing race");
  //      the same wait ends that.
  {
    const p = await pageWith({ stt: 503 });
    const w = p.world, t0 = now();
    globalThis.window.moxieTypedTurn.send(DAY);
    await advance(5000);
    globalThis.window.moxieTypedTurn.send(HURT);
    await advance(1000);                               // t+6.0 s
    await talk(p);                                     // the ears done at 9.85 s
    await advance(3850 - 1250 + 10);                   // t+9.86 s
    const line = pretendOf(w), answer = globalThis.window.moxieStub.reply(line).text;
    deep([T().safetyFirst, T().heldForEars, w.spy.sounds.filter((s) => s.kind !== "cloud").map((s) => [s.kind, s.t - t0])], [1, 1, [["browser", 9850]]],
         "13w (iv): the redirect, landed into the open microphone (6.8 s) and held, is said the moment the ears are done (9.85 s)");
    await advance(15000);
    deep(played(w, t0, line, answer),
         [["day0", 3800, 6000], ["redirect", 9850, 17550], ["child-clip", 17580, 18000], ["stub-clip", 18030, 21908]],
         "13w (iv): THE REDIRECT IS HEARD WHOLE (9.85-17.55 s), then the child clip (17.58 s), then the stub answer (origin/dev and the head before this: the clip at 9.88 s and the stub at 10.33 s, over it)");
    deep([overlapping(played(w, t0, line, answer)), T().scriptedWaited, w.spy.transcript], [0, 1, [DAY, THREE, HURT, line, REDIRECT, answer]],
         "13w (iv): no two sounds overlap; the pretend line waited for a reply in hand; the log in order");
  }
}
