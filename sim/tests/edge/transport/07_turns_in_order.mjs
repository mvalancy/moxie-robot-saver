/* §11: ONE LINE AT A TIME FROM THE CONTROLS. A second line from the Ask box (or an opener,
 * or the mic's transcript) while a turn is still being answered WAITS for it instead of
 * racing it: it goes out carrying the first reply's context, both replies are heard whole and
 * in order on the real voice/, and the third turn's history holds both exchanges.
 *
 * The defect (review lane l6 S3, measured in Chrome on the shipped page): two lines 300 ms
 * apart were both POSTed at once with context "", the replies showed in landing order (the
 * fast "food" answer before the slow "colour" one), and the third turn carried only the
 * exchange that landed last. On origin/dev after #317 the order of the VOICE holds (the newer
 * reply's voice ends the older pipeline), but the older reply's later sentences are never
 * heard and the context race is still there. `sendUserTurn` itself still sends at once:
 * §4i–4k's supersede rule is the backstop for that path, unchanged.
 */
import {
  advance, boot, deep, envelope, eq, live, now, said, tickets, voicedChunk,
} from "./harness.mjs";

const T = () => globalThis.window.moxieBridge.transportStats();
const status = () => globalThis.document.getElementById("chat-status").textContent;
const chats = (world) => world.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => ({ text: b.text, context: b.context }));
const speeches = (world) => world.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => b.ticket);

/** The lane's three lines, with their measured delays, now two sentences each so "heard
 *  whole" means something: the colour answer is SLOW (2.2 s), the food answer FAST (0.4 s). */
const LINES = {
  "what is your favorite color?": { eid: "sim-color", reply: "My favorite color is sunset orange. It glows like a lava lamp.", delay: 2200, ctx: "CTX-after-color", tix: 2, dur: [2.0, 2.1] },
  "and your favorite food?": { eid: "sim-food", reply: "I would eat sparkly bolts if I could. Crunchy ones.", delay: 400, ctx: "CTX-after-food", tix: 2, dur: [1.5, 1.6] },
  "what did I ask first?": { eid: "sim-third", reply: "(third)", delay: 100, ctx: "CTX-3", tix: 0, dur: [] },
};
const byEid = Object.fromEntries(Object.values(LINES).map((l) => [l.eid, l]));
/** Tickets named by event so a `/api/speech` request says which reply and chunk it is for. */
const tix = (l) => tickets(l.eid, l.tix).map((t) => ({ ...t, ticket: `v1.${l.eid}.T${t.chunk_num}.M` }));
const ticketOf = (body) => /^v1\.(sim-[a-z]+)\.T(\d)\.M$/.exec(body.ticket);
const answerLines = (path, body) => {
  if (path === "/api/chat") {
    const l = LINES[body.text];
    return Object.assign(said(l.reply, l.eid, { speech: tix(l), context: l.ctx }), { delayMs: l.delay });
  }
  if (path === "/api/speech") {
    const [, eid, k] = ticketOf(body);
    return voicedChunk(eid, Number(k), { delayMs: 2300, seconds: byEid[eid].dur[Number(k)] });
  }
  return { status: 404, text: "" };
};
/** `sim-color/0` for a speech request, `color0@4500` for a sound (named by its duration). */
const chunkName = (t) => { const m = ticketOf({ ticket: t }); return m[1].slice(4) + m[2]; };
const soundName = (t0) => (s) => {
  for (const l of Object.values(LINES)) { const k = l.dur.findIndex((d) => Math.round(d * 1000) === s.dur); if (k >= 0) return l.eid.slice(4) + k + "@" + (s.t - t0); }
  return s.kind + "@" + (s.t - t0);
};

/* =========================================================================== *
 * 11a. THE LANE'S SCENARIO, through the Ask path (`moxieTypedTurn.send`, what the Ask button,
 *      Enter and the openers call), on the REAL voice/: the second line is taken at once and
 *      sent when the first reply has been handed to the speakers whole; it carries the first
 *      reply's context; both replies are heard whole, in order, nothing cut, nothing given up;
 *      the third line carries the second reply's context.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live(answerLines) });
  const t0 = now();
  const typed = globalThis.window.moxieTypedTurn;
  eq(typeof (globalThis.window.moxieBridge.queueUserTurn), "function", "11a: the bridge publishes `queueUserTurn`, the one-at-a-time seam the controls use");

  eq(typed.send("what is your favorite color?"), true, "11a: the first line is sent");
  await advance(300);
  eq(typed.send("and your favorite food?"), true, "11a: the second line, 300 ms later, is TAKEN (the box clears)");
  deep(chats(world), [{ text: "what is your favorite color?", context: "" }],
       "11a: …but NOT SENT YET: only the first line is on the wire while its reply is on its way");
  eq(status(), "Moxie will answer that next.", "11a: …and the status says so");
  deep(world.spy.transcript, ["what is your favorite color?", "and your favorite food?"],
       "11a: …while the child's line is already in the log: taken the moment they tapped");
  eq(T().queued, 1, "11a: recorded as one line that waited");

  await advance(6400);                                // t+6.7 s: colour landed at 2.2 s, both its chunks routed by 6.8 s
  deep(chats(world).map((c) => c.context), [""], "11a: at 6.7 s the second line still waits: the colour reply's last sentence is not yet handed over");
  await advance(200);                                 // t+6.9 s: colour's chunk 1 landed at 6.8 s and was routed: the turn is settled
  deep(chats(world), [{ text: "what is your favorite color?", context: "" }, { text: "and your favorite food?", context: "CTX-after-color" }],
       "11a: THE SECOND LINE GOES OUT ONCE THE FIRST REPLY IS WHOLLY HANDED OVER, CARRYING ITS CONTEXT (it carried \"\" at 300 ms)");

  await advance(7100);                                // t+14 s: food landed at 7.2 s, its two chunks heard by 13.4 s
  eq(typed.send("what did I ask first?"), true, "11a: a third line, after both answers");
  await advance(1000);
  deep(chats(world).map((c) => c.context), ["", "CTX-after-color", "CTX-after-food"],
       "11a: THE THIRD TURN CARRIES BOTH EXCHANGES: the food reply's context, which holds the colour exchange before it");
  deep(world.spy.sounds.map(soundName(t0)), ["color0@4500", "color1@6800", "food0@9500", "food1@11800", "browser@14100"],
       "11a: BOTH REPLIES ARE HEARD WHOLE, IN SEND ORDER: colour's two sentences, then food's two, then the third (no voice ticket: the local voice)");
  deep(world.spy.cuts, [], "11a: …nothing was cut short");
  deep(speeches(world).map(chunkName), ["color0", "color1", "food0", "food1"],
       "11a: …every sentence was paid for exactly once, in order — nothing of the older reply given up");
  deep(world.spy.transcript, [
    "what is your favorite color?", "and your favorite food?",
    "My favorite color is sunset orange. It glows like a lava lamp.",
    "I would eat sparkly bolts if I could. Crunchy ones.",
    "what did I ask first?", "(third)",
  ], "11a: the replies SHOW in send order too (the food answer showed first on the shipped page)");
  const st = T();
  deep([st.turns, st.live, st.queued, st.chunksSuperseded, st.chunksDropped, st.voiceFirst, st.chunksRouted, st.chunkFailures, st.voiceFallbacks],
       [3, 3, 1, 0, 0, 2, 2, 0, 0],
       "11a: recorded: three live turns, one waited, NO chunk superseded or dropped, both voiced replies voice-first, two later chunks routed, no failure or fallback");
  eq(globalThis.window.moxieMode.state(), "live", "11a: …and the page is live throughout");
}

/* =========================================================================== *
 * 11b. THE MIC'S TRANSCRIPT TAKES THE SAME QUEUE (`queueUserTurn`): spoken while a typed
 *      line is still being answered, it waits, carries that reply's context, and its own
 *      promise — what mic.js holds the Listen button on — settles only when ITS reply starts.
 * =========================================================================== */
{
  // `/api/speech` carries only the ticket, so each reply's ticket names its event.
  const tixOf = (eid) => [{ ticket: "v1." + eid + ".T0.M", event_id: eid, chunk_num: 0 }];
  const world = await boot({ realVoice: true, answer: live((path, body) => {
    if (path === "/api/chat") {
      const typedLine = body.text === "typed first";
      const eid = typedLine ? "sim-t" : "sim-s";
      return Object.assign(said(typedLine ? "Typed answer here." : "Spoken answer here.", eid,
                                { speech: tixOf(eid), context: typedLine ? "CTX-typed" : "CTX-spoken" }), { delayMs: 2200 });
    }
    if (path === "/api/speech") return voicedChunk(/^v1\.(sim-[ts])\./.exec(body.ticket)[1], 0, { delayMs: 2300, seconds: 2 });
    return { status: 404, text: "" };
  }) });
  const t0 = now();
  globalThis.window.moxieTypedTurn.send("typed first");
  await advance(500);
  let started = null;
  const bridge = globalThis.window.moxieBridge;
  // On a base without the seam the transcript takes `sendUserTurn`, as mic.js did: a counted red.
  const p = (bridge.queueUserTurn || bridge.sendUserTurn).call(bridge, "spoken second").then(() => { started = now() - t0; });
  await advance(10);
  deep(chats(world).map((c) => c.text), ["typed first"], "11b: the transcript waits behind the typed line in flight");
  deep(world.spy.transcript, ["typed first", "spoken second"], "11b: …though it is in the log at once, as a child's words always were");
  await advance(4100);                                // t+4.6 s: typed landed at 2.2 s, its one chunk routed at 4.5 s: settled
  deep(chats(world), [{ text: "typed first", context: "" }, { text: "spoken second", context: "CTX-typed" }],
       "11b: THE TRANSCRIPT GOES OUT WHEN THE TYPED REPLY IS HANDED OVER, carrying its context");
  eq(started, null, "11b: …and mic.js's hold has not been released: her answer to the spoken line has not started");
  await advance(5000);                                // spoken landed at 6.7 s, its chunk at 9.0 s
  await p;
  eq(started, 9000, "11b: the promise settles when the spoken line's OWN reply starts (9.0 s), not when the typed one did");
  deep(world.spy.sounds.map((s) => [s.kind, s.t - t0]), [["cloud", 4500], ["cloud", 9000]],
       "11b: the typed reply is heard at 4.5 s and the spoken reply at 9.0 s, after it, nothing cut");
  deep(world.spy.cuts, [], "11b: nothing was cut");
}

/* =========================================================================== *
 * 11c. A LINE THAT WAITED IS RE-DECIDED WHEN ITS TURN COMES: the first reply is a 429 that
 *      pauses live turns, so the waiting line is answered from stub.js — and never echoed a
 *      second time (it is already in the log).
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live((path) => (path === "/api/chat"
    ? { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }), delayMs: 500 }
    : { status: 404, text: "" })) });
  const typed = globalThis.window.moxieTypedTurn;
  typed.send("tell me a joke");
  await advance(300);
  typed.send("another one");
  await advance(3000);
  deep(chats(world).map((c) => c.text), ["tell me a joke"], "11c: only the first line reached the brain: the 429 paused live turns before the second's turn came");
  const rows = world.spy.transcript;
  deep(rows.filter((r) => r === "another one").length, 1, "11c: the waiting line is in the log EXACTLY ONCE (never echoed again when it was answered)");
  deep([rows[0], rows[1]], ["tell me a joke", "another one"], "11c: …taken in order, the moment they were typed");
  eq(rows.length, 4, `11c: and both lines are answered (rows: ${JSON.stringify(rows)})`);
  const st = T();
  deep([st.live, st.chatRefused, st.fallbacks, st.delegated, st.queued], [1, 1, 2, 0, 1],
       "11c: recorded: one live turn refused, two stub answers, nothing delegated to bridge/'s echoing path, one line waited");
  eq(status(), "One at a time! Give Moxie a few seconds.", "11c: the status carries the mode's own chip copy for the waiting line");
}

/* =========================================================================== *
 * 11d. `sendUserTurn` ITSELF STILL SENDS AT ONCE (the bridge's API, not a control): two calls
 *      300 ms apart are two requests in flight, which §4i–4k's supersede rule still governs.
 *      Unchanged — pinned so the backstop stays reachable.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live(answerLines) });
  globalThis.window.moxieBridge.sendUserTurn("what is your favorite color?");
  await advance(300);
  globalThis.window.moxieBridge.sendUserTurn("and your favorite food?");
  await advance(10);
  deep(chats(world).map((c) => c.text), ["what is your favorite color?", "and your favorite food?"],
       "11d: sendUserTurn sends at once: both lines are on the wire 300 ms apart (the §4i–4k backstop's path)");
  eq(T().queued, 0, "11d: …and nothing waited");
}

/* =========================================================================== *
 * 11e. A TURN CANNOT STAY IN FLIGHT FOR EVER: a page script throwing on the reply path (here
 *      the body's `settled()`) leaves the turn's pipeline never closed, which would have held
 *      every later control line for good. `TURN_MAX_MS` (190 s: the chat deadline, the ears'
 *      hold and eight sentences each at the speech deadline) settles it regardless, and the
 *      next line goes out — §4i–4k's rule the backstop from there, as for `sendUserTurn`.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live(answerLines) });
  const typed = globalThis.window.moxieTypedTurn;
  globalThis.window.moxieAlive = { thinking() {}, listening() {}, settled() { throw new Error("a page script threw"); } };
  // On a base without the queue the throw escapes `sendTyped` as an unhandled rejection (a
  // browser logs it; Node would exit): swallowed here, so a base prints a counted red.
  const swallow = () => {};
  process.on("unhandledRejection", swallow);
  typed.send("what is your favorite color?");
  await advance(300);
  typed.send("and your favorite food?");
  await advance(60_000);
  deep(chats(world).map((c) => c.text), ["what is your favorite color?"],
       "11e: the first reply's path threw and its turn never settled on its own: a minute on, the second line still waits");
  await advance(130_000);                             // 190.3 s after the first POST
  deep(chats(world).map((c) => c.text), ["what is your favorite color?", "and your favorite food?"],
       "11e: THE VALVE: 190 s after its POST the turn is settled regardless, and the waiting line goes out (it waited for good)");
  eq(T().turnsValved, 1, "11e: recorded as one turn settled by the valve");
  process.off("unhandledRejection", swallow);
}

/* =========================================================================== *
 * 11f. THE EARS CANNOT BE HELD OPEN FOR EVER. `earsOpen()` with no `earsIdle()` ever (a
 *      mic.js whose recorder never ended, a fork that forgot) would hold every control line
 *      and every reply for good: 45 s after they opened (EARS_HOLD_MAX_MS: the 15 s record
 *      cap plus mic.js's 30 s upload valve) the transport declares the ears idle itself.
 *      mic.js has its own, cap-aware valve first (ears §B23); this is the transport's own
 *      bound on `earsBusy`, the state it keeps.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live(answerLines) });
  const b = globalThis.window.moxieBridge, typed = globalThis.window.moxieTypedTurn;
  if (typeof b.earsOpen === "function") b.earsOpen();  // the microphone opened; nothing ever says it closed
  typed.send("what is your favorite color?");
  await advance(10);
  deep([chats(world).map((c) => c.text), T().queued, status()], [[], 1, "Moxie will answer that next."],
       "11f: a line typed while the ears are open waits for them");
  await advance(44_900);
  deep(chats(world).map((c) => c.text), [], "11f: …44.9 s on, still (the hold was never bounded here: it waited for good)");
  await advance(200);
  deep([chats(world).map((c) => c.text), T().earsValved], [["what is your favorite color?"], 1],
       "11f: THE EARS' OWN VALVE: 45 s after `earsOpen` with no `earsIdle` the transport declares them idle, and the line goes out");
}

/* =========================================================================== *
 * 11g. THE BOUND mic.js NAMES WINS (it knows the served record cap: a 60 s cap is 90 s), and
 *      `earsIdle` clears it, so a hold that ended on its own never fires a stale valve.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: live(answerLines) });
  const b = globalThis.window.moxieBridge, typed = globalThis.window.moxieTypedTurn;
  if (typeof b.earsOpen === "function") b.earsOpen(90_000);
  typed.send("what is your favorite color?");
  await advance(45_100);
  deep([chats(world).map((c) => c.text), T().earsValved], [[], 0],
       "11g: told 90 s, the transport holds past its own 45 s default (a legitimate 60 s recording is never released early)");
  await advance(45_000);                               // 90.1 s
  deep([chats(world).map((c) => c.text), T().earsValved], [["what is your favorite color?"], 1], "11g: …and releases at 90 s");
  await advance(20_000);                               // the colour reply (two sentences) is over
  if (typeof b.earsOpen === "function") { b.earsOpen(5000); b.earsIdle(); }   // a clip dropped at once
  typed.send("and your favorite food?");
  await advance(10);
  deep([chats(world).map((c) => c.text).length, T().earsValved], [2, 1], "11g: `earsIdle` lifts the hold at once: the next line goes out");
  await advance(6000);
  eq(T().earsValved, 1, "11g: …and cleared the valve with it (nothing fired at 5 s)");
}
