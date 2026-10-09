/* §12: SHE IS VISIBLY WORKING UNTIL SHE SPEAKS. The thinking cue — the status line, her face,
 * her pose (`bridge/alive.js`) — lasts until the reply STARTS, not until the brain answers.
 *
 * The defect (W4 journey harness on production, N=5 typed turns, 2026-10-08): `#chat-status`
 * cleared and the body settled the moment /api/chat returned, while `voiceFirst` held the
 * words for chunk 0; her words and voice came 2.0-2.6 s later with an empty status line and
 * arms at rest. On the real `cloud-transport.js` and `bridge/alive.js` under the harness's
 * fakes, on a virtual clock:
 *   12a-12e  WHERE THE BODY SETTLES: a served reply with tickets calls `moxieAlive.settled()`
 *            0 times at chat return and exactly once at the first of chunk 0 routed, the words
 *            released at SPEECH_WAIT_MS, chunk 0 given up, or a newer reply superseding it;
 *            a refusal, a block, an error and a reply with no voice coming settle at chat
 *            return as before.
 *   12f-12h  THE FILLER STAYS KEYED TO THE CHAT NOT YET RETURNED: once the brain has answered
 *            no spoken filler starts however late the voice; before it answers the filler
 *            still fires at 3.5 s and is withheld while she is busy; a quiet beat (a small
 *            turn of her body, no sound) keeps her moving until her voice.
 *   12i      THE NEXT LINE'S CUE IS ITS OWN: a reply's cue ends before its turn settles and
 *            clears only the voice-wait line, so the line queued behind it keeps its status
 *            and its thinking beat.
 */
import {
  advance, boot, chunkOf, chunked, deep, envelope, eq, live, now, ok, said, serve, ticket, tickets, voiced, voicedChunk,
} from "./harness.mjs";

const T = () => globalThis.window.moxieBridge.transportStats();
const status = () => globalThis.document.getElementById("chat-status").textContent;
const alive = () => globalThis.window.moxieAlive;
const aliveState = () => { const a = alive(); return a && a.__state ? a.__state() : {}; };
const VOICE_WAIT = "warming up my voice…";
const TWO = "I see a tower of blocks. It is taller than me!";

/** Count the body's moments on the REAL alive.js as the page calls them. A base whose
 *  alive.js lacks a member leaves its count at 0, so a pin on it reads as a counted red. */
function watchAlive() {
  const a = alive(), n = { thinking: 0, answered: 0, settled: 0, listening: 0, transcribing: 0 };
  for (const k of Object.keys(n)) {
    const f = a[k];
    if (typeof f === "function") a[k] = function () { n[k]++; return f.apply(a, arguments); };
  }
  return n;
}
/** Every motor the page set, as [index, value, t]. */
function watchMotors() {
  const log = [];
  globalThis.window.moxie.setMotor = (i, v) => log.push([i, v, now()]);
  return log;
}
const send = (text) => globalThis.window.moxieBridge.sendUserTurn(text);

/* =========================================================================== *
 * 12a. THE HEADLINE: a two-sentence reply with two tickets, the brain at 300 ms, chunk 0 at
 *      2.3 s. The body is told the voice is coming at chat return and settles exactly when
 *      chunk 0 is routed — not at chat return (it was), not at close() after chunk 1.
 * =========================================================================== */
{
  const world = await boot({ answer: chunked(
    Object.assign(said(TWO, "sim-cue1", { speech: tickets("sim-cue1", 2) }), { delayMs: 300 }),
    (k) => voicedChunk("sim-cue1", k, { delayMs: 2000 })) });
  const n = watchAlive(), motors = watchMotors(), t0 = now();
  send("what do you see?");
  await advance(10);
  deep([n.thinking, status()], [1, "thinking…"], "12a: the send arms the thinking cue, and the status says so");
  await advance(400);                                  // t+410: the brain answered at 300 ms
  deep([T().chatOk, n.settled, n.answered, status()], [1, 0, 1, VOICE_WAIT],
       "12a: THE BRAIN ANSWERED (300 ms) AND THE BODY IS NOT SETTLED: settled() 0 times at chat return (it was called there), the body told her voice is coming, and the status says what she is doing instead of going blank");
  await advance(600);                                  // t+1010: beat 1 (900 ms) put the thinking face up
  deep([aliveState().stage, world.spy.setFace.length > 0], [1, true], "12a: her thinking face is up at 900 ms…");
  await advance(1200);                                 // t+2210: chunk 0 still synthesising (it lands at 2.3 s)
  deep([n.settled, aliveState().stage, status(), T().order], [0, 1, VOICE_WAIT, []],
       "12a: …and 2.2 s in, chunk 0 still on its way, the face, the pose and the status all HOLD: nothing routed, nothing settled");
  deep(motors.filter(([i, v, t]) => i <= 3 && v === 16384 && t - t0 >= 300 && t - t0 < 2300).map(([, , t]) => t - t0), [],
       "12a: …the arms did not go home at chat return (they did: four home sets at 300 ms)");
  await advance(200);                                  // t+2410: chunk 0 landed at 2.3 s and was routed
  deep([n.settled, status(), T().order, T().voiceFirst, aliveState().stage], [1, "", ["tts", "chat"], 1, 0],
       "12a: CHUNK 0 ROUTED (2.3 s): the body settles EXACTLY THERE, once; the status clears as her words and voice go out together");
  ok(motors.some(([i, v, t]) => i <= 3 && v === 16384 && t - t0 === 2300), "12a: …and the arms come home at that moment, before the reply's own markup");
  await advance(3000);                                 // chunk 1 landed at 4.3 s and was routed; the pipeline closed
  deep([n.settled, T().chunksRouted, T().order], [1, 1, ["tts", "chat", "tts"]],
       "12a: …and the rest of the reply (chunk 1 at 4.3 s, the pipeline closing) settles nothing again: the body's point is chunk 0, never close()");
}

/* =========================================================================== *
 * 12b. CHUNK 0 REFUSED (a 503 at 1.1 s): the words go out with the local voice, and the body
 *      settles there, once.
 * =========================================================================== */
{
  const world = await boot({ answer: serve({
    "/api/chat": Object.assign(said(TWO, "sim-cue2", { speech: ticket("sim-cue2") }), { delayMs: 300 }),
    "/api/speech": { status: 503, json: envelope({ ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }), delayMs: 800 },
  }) });
  const n = watchAlive();
  send("what do you see?");
  await advance(1000);                                 // the chat at 300 ms; the refusal lands at 1.1 s
  deep([n.settled, status(), T().order], [0, VOICE_WAIT, []], "12b: chunk 0 refused at 1.1 s: until then the cue holds");
  await advance(200);                                  // t+1.2 s
  deep([n.settled, status(), T().voiceFallbacks, world.spy.speak, T().order], [1, "", 1, [TWO], ["chat"]],
       "12b: CHUNK 0 GIVEN UP (refused at 1.1 s): the body settles there, once, as the words go out in the local voice");
  await advance(5000);
  eq(n.settled, 1, "12b: …and only there");
}

/* =========================================================================== *
 * 12c. CHUNK 0 HANGING: the words are released at SPEECH_WAIT_MS (2.5 s after the speech
 *      request) still expecting their voice; the body settles there, once; the voice given up
 *      at the client's 15 s deadline settles nothing again.
 * =========================================================================== */
{
  await boot({ answer: serve({
    "/api/chat": Object.assign(said(TWO, "sim-cue3", { speech: ticket("sim-cue3") }), { delayMs: 300 }),
    "/api/speech": voiced("sim-cue3", { delayMs: 20000 }),
  }) });
  const n = watchAlive();
  send("what do you see?");
  await advance(2790);                                 // the wait runs from the speech POST at 300 ms: the words at 2.8 s
  deep([n.settled, status(), T().order], [0, VOICE_WAIT, []], "12c: chunk 0 hanging: at 2.79 s the cue still holds");
  await advance(20);                                   // t+2.81 s
  deep([n.settled, status(), T().chatFirst, T().order], [1, "", 1, ["chat"]],
       "12c: THE WORDS RELEASED AT SPEECH_WAIT_MS: the body settles there, once, as they go out");
  await advance(13000);                                // t+15.8 s: the 15 s deadline gave the voice up at 15.3 s
  deep([n.settled, T().voiceFallbacks], [1, 1], "12c: …and the voice given up at the deadline settles nothing again");
}

/* =========================================================================== *
 * 12d. WHAT STILL SETTLES AT CHAT RETURN: a 429, a 503, a safety block, a reply with no voice
 *      coming (`voice: false`) and an unreachable brain — their words go out there, as before.
 * =========================================================================== */
{
  const R429 = { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }) };
  const DOWN = { status: 503, json: envelope({ ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }) };
  const BLOCKED = said("Thank you for telling me. Feelings this big need a grown-up.", "sim-blk", { ok: true, degraded: true, reason: "blocked", mode: "live" });
  const NOVOICE = said(TWO, "sim-nv", { voice: false });
  const cases = [["a 429", R429], ["a 503", DOWN], ["a safety block", BLOCKED],
                 ["a reply with no voice coming", NOVOICE], ["an unreachable brain", { rejectLate: true }]];
  for (const [label, chat] of cases) {
    await boot({ answer: serve({ "/api/chat": Object.assign({}, chat, { delayMs: 300 }) }) });
    const n = watchAlive();
    send("tell me a joke");
    await advance(290);
    eq(n.settled, 0, `12d (${label}): thinking until the brain answers`);
    await advance(20);                                 // t+310 ms
    eq(n.settled, 1, `12d (${label}): …settled AT CHAT RETURN, as before: no voice is on the way, its words go out there`);
    await advance(6000);
    eq(n.settled, 1, `12d (${label}): …once`);
  }
}

/* =========================================================================== *
 * 12e. SUPERSEDED: A's chunk 0 is slow (6 s); B, sent 300 ms later through `sendUserTurn`
 *      (the bridge's own API, which sends at once: §11d), lands fast and its chunk 0 routes
 *      at 1.1 s, ending A's pipeline (§4j). A settles at the supersede, B at its chunk 0:
 *      two settles for two replies, and nothing more when A's words go out silently at its
 *      2.5 s wait or its voice lands at 6.3 s and is dropped.
 * =========================================================================== */
{
  await boot({ answer: live((path, body) => {
    if (path === "/api/chat") {
      const a = body.text === "first", eid = a ? "sim-A" : "sim-B";
      return Object.assign(said(a ? "A slow one." : "A quick one.", eid, { speech: [{ ticket: "v1." + eid + ".T0.M", event_id: eid, chunk_num: 0 }] }), { delayMs: 300 });
    }
    if (path === "/api/speech") { const a = /sim-A/.test(body.ticket); return voicedChunk(a ? "sim-A" : "sim-B", 0, { delayMs: a ? 6000 : 500 }); }
    return { status: 404, text: "" };
  }) });
  const n = watchAlive();
  send("first");
  await advance(300);
  send("second");
  await advance(700);                                  // t+1.0 s: A's brain at 0.3 s, B's at 0.6 s; B's chunk 0 lands at 1.1 s
  deep([n.settled, T().chatOk], [0, 2], "12e: both answered, neither settled: both voices are on their way");
  await advance(150);                                  // t+1.15 s
  deep([n.settled, T().chunksSuperseded, T().voiceFirst], [2, 1, 1],
       "12e: B'S VOICE STARTS (1.1 s) AND SUPERSEDES A: A settles at the supersede and B at its chunk 0 — two settles for two replies");
  await advance(8000);                                 // A's words went out silently at 2.8 s; A0 landed at 6.3 s and was dropped
  deep([n.settled, T().chatFirst, T().lateSpeechDropped], [2, 1, 1],
       "12e: …A's words at its 2.5 s wait and its dropped voice settle nothing more: once each");
}

/* =========================================================================== *
 * 12f. ONCE THE BRAIN HAS ANSWERED, NO SPOKEN FILLER STARTS, however late her voice: the
 *      brain at 1.0 s, chunk 0 at 6.0 s. The words go out at 3.5 s (SPEECH_WAIT_MS after the
 *      speech request) — exactly where the filler beat would have fired: it is passed over,
 *      not merely held — and the quiet beat 2 s after the answer kept her moving until the
 *      words (the wait for chunk 0 is at most 2.5 s, so one quiet beat fits in it).
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, answer: serve({
    "/api/chat": Object.assign(said("A late voice.", "sim-late", { speech: ticket("sim-late") }), { delayMs: 1000 }),
    "/api/speech": voiced("sim-late", { delayMs: 5000, seconds: 1 }),
  }) });
  const n = watchAlive(), motors = watchMotors(), a = alive(), t0 = now();
  send("a question");
  await advance(2000);
  deep([n.answered, aliveState().pose, aliveState().answered], [1, "voice-wait", true],
       "12f: at 2 s the brain has answered (1.0 s): the body holds a voice-wait pose and knows no filler may start");
  await advance(1600);                                 // t+3.6 s: the words went out at 3.5 s, where the filler beat was due
  deep([n.settled, status(), T().chatFirst, a.stats.spoke, a.stats.held, world.spy.said.filter((s) => s.who === "ambient").length], [1, "", 1, 0, 0, 0],
       "12f: ONCE THE BRAIN HAS ANSWERED NO SPOKEN FILLER STARTS: the 3.5 s beat is passed over (not held), and the body settles there as the words go out");
  deep(motors.filter(([i, , t]) => i === 5 && t - t0 > 1000 && t - t0 < 3500).map(([, , t]) => t - t0), [3000],
       "12f: …while the quiet beat (a small head move, no sound) 2 s after the answer kept her visibly working until the words");
  await advance(2900);                                 // t+6.5 s: her voice landed at 6.0 s
  deep([world.spy.sounds.map((s) => [s.kind, s.t - t0]), aliveState().pose, n.settled, a.stats.spoke], [[["cloud", 6000]], null, 1, 0],
       "12f: the only sound of the turn is her answer, however late (6.0 s); nothing settled again, nothing was said meanwhile");
}

/* =========================================================================== *
 * 12g. BEFORE THE BRAIN ANSWERS THE FILLER STILL FIRES AT 3.5 s (a 4.5 s brain, as §9b), and
 *      after it answers nothing more is said until her voice: one filler, never two.
 * =========================================================================== */
{
  await boot({ realVoice: true, answer: serve({
    "/api/chat": Object.assign(said("That took a while!", "sim-slow", { speech: ticket("sim-slow") }), { delayMs: 4500 }),
    "/api/speech": voiced("sim-slow", { delayMs: 3000, seconds: 1 }),
  }) });
  const a = alive();
  send("a hard question");
  await advance(4000);
  eq(a.stats.spoke, 1, "12g: before the brain answers, the filler still fires at 3.5 s (a 4.5 s brain)");
  await advance(5000);                                 // the brain at 4.5 s, her voice at 7.5 s
  eq(a.stats.spoke, 1, "12g: …and once it has answered nothing more is said until her voice: one filler, not two");
}

/* =========================================================================== *
 * 12h. THE FILLER IS WITHHELD WHILE SHE IS BUSY: she is 4.5 s into a 6 s answer when the next
 *      turn's filler beat comes (§9c's scenario): held +1, spoke +0.
 * =========================================================================== */
{
  let nChat = 0;
  await boot({ realVoice: true, answer: live((path) => {
    if (path === "/api/chat") {
      nChat++;
      return nChat === 1 ? said("A long story about the moon.", "sim-m1", { speech: ticket("sim-m1") })
        : Object.assign(said("Sure!", "sim-m2", { speech: ticket("sim-m2") }), { delayMs: 4500 });
    }
    if (path === "/api/speech") return nChat === 1 ? voiced("sim-m1", { seconds: 6 }) : voiced("sim-m2", { seconds: 1 });
    return { status: 404, text: "" };
  }) });
  const a = alive();
  send("tell me about the moon");
  await advance(1000);                                 // 1 s into her 6 s answer…
  send("can you sing instead?");
  await advance(4000);                                 // …the next turn's filler beat at +3.5 s
  deep([a.stats.spoke, a.stats.held], [0, 1], "12h: the filler beat that comes while she is still speaking is WITHHELD (held +1), never spoken over her");
}

/* =========================================================================== *
 * 12i. THE NEXT LINE'S CUE IS ITS OWN. The end of one reply's cue clears the voice-wait line
 *      and nothing else, and it comes BEFORE that reply's turn settles — so the line waiting
 *      behind it goes out with its own "thinking…" and its own thinking beat intact.
 *      (a) A's chunk 0 refused at 1.1 s, B typed at 0.5 s and queued: A's turn settles when
 *          its voice is given up, B goes out in that tick — and a draft of this slice that
 *          ended A's cue a microtask later wiped B's status and cancelled B's thinking cue.
 *      (b) B typed while A waits for chunk 0 (2 tickets): A's voice starting ends A's cue, but
 *          "Moxie will answer that next." stays until B goes out behind A's whole voice.
 * =========================================================================== */
{
  const R1 = { ticket: "v1.R1.M", event_id: "sim-r1", chunk_num: 0 };
  const R2 = { ticket: "v1.R2.M", event_id: "sim-r2", chunk_num: 0 };
  const world = await boot({ answer: live((path, body) => {
    if (path === "/api/chat") {
      const first = body.text === "what do you see?";
      return Object.assign(said(first ? TWO : "And a red ball.", first ? "sim-r1" : "sim-r2", { speech: [first ? R1 : R2] }), { delayMs: 300 });
    }
    if (path === "/api/speech") return body.ticket === R1.ticket
      ? { status: 503, json: envelope({ ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }), delayMs: 800 }
      : voiced("sim-r2", { delayMs: 2000 });
    return { status: 404, text: "" };
  }) });
  const n = watchAlive(), typed = globalThis.window.moxieTypedTurn;
  const chats = () => world.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
  typed.send("what do you see?");
  await advance(500);
  typed.send("and what else?");
  await advance(10);
  deep([status(), chats()], ["Moxie will answer that next.", ["what do you see?"]],
       "12i(a): B is taken while A waits for its voice, and the status says it is next");
  await advance(700);                                  // t+1.21 s: A's chunk 0 refused at 1.1 s
  deep([T().voiceFallbacks, chats(), status(), n.settled, n.thinking, aliveState().armed],
       [1, ["what do you see?", "and what else?"], "thinking…", 1, 2, true],
       "12i(a): A'S VOICE GIVEN UP (1.1 s): A's cue ends ONCE, before its turn settles, and B goes out with its OWN status and thinking cue intact");
  await advance(900);                                  // t+2.11 s: B's beat 1 was due at 2.0 s (B's brain answered at 1.4 s)
  deep([aliveState().stage, n.settled, status()], [1, 1, VOICE_WAIT],
       "12i(a): …B's thinking face came up 900 ms after B's send, and B's voice-wait line is showing");
  await advance(1400);                                 // t+3.51 s: B's chunk 0 at 3.4 s
  deep([n.settled, status(), T().voiceFirst], [2, "", 1], "12i(a): B's voice starts (3.4 s): B's cue ends there");
}
{
  const world = await boot({ answer: live((path, body) => {
    if (path === "/api/chat") return body.text === "tell me two things"
      ? Object.assign(said(TWO, "sim-j1", { speech: tickets("sim-j1", 2) }), { delayMs: 300 })
      : Object.assign(said("Sure!", "sim-j2"), { delayMs: 300 });
    if (path === "/api/speech") return voicedChunk("sim-j1", chunkOf(body), { delayMs: 2000 });
    return { status: 404, text: "" };
  }) });
  const n = watchAlive(), typed = globalThis.window.moxieTypedTurn;
  const chats = () => world.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
  typed.send("tell me two things");
  await advance(1000);                                 // A's brain at 0.3 s; chunk 0 lands at 2.3 s
  typed.send("and then?");
  await advance(1310);                                 // t+2.31 s: chunk 0 routed; chunk 1 lands at 4.3 s
  deep([T().voiceFirst, n.settled, status(), chats()], [1, 1, "Moxie will answer that next.", ["tell me two things"]],
       "12i(b): A'S VOICE STARTS (2.3 s) and A's cue ends — but the line typed meanwhile still says it is next (only the voice-wait line was A's to clear)");
  await advance(2000);                                 // t+4.31 s: chunk 1 routed at 4.3 s, A settled, B sent
  deep([T().chunksRouted, chats(), status()], [1, ["tell me two things", "and then?"], "thinking…"],
       "12i(b): …until B goes out behind A's whole voice, thinking");
}
