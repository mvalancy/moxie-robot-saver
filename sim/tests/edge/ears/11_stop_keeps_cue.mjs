/* §B24–B28: STOP KEEPS A WORKING CUE. The recording's end does not settle the body: she is
 * still working on the clip through its 2-3 s upload (`alive.transcribing()`: the listening
 * face and head tilt held), the transcript's send arms `thinking()`, and a dropped clip or a
 * failed upload hands her back. A typed line after a mic turn puts the Listen hint back under
 * the composer (the "heard: …" line used to stay for the rest of the visit), whatever line
 * of mic.js's came first (B27). The Listen tap still interrupts her working pose (B28).
 *
 * The defect (W4 journey harness on production, 2026-10-08): `stop()` called `settled()`, so
 * from the auto-stop to her voice (~9 s from the child's last word) the robot stood still
 * through the 3.2 s upload. Part B (the real mic.js, a spy body) and Part C (the whole page:
 * the real `bridge/alive.js`, `cloud-transport.js` and `voice/`, on the virtual clock).
 */
import { advance, bootMic, bootPage, deep, eq, fails, flush, page } from "./harness.mjs";

/* Part C runs on the PAGE's clock (`bootPage` installs the transport harness's); the
 * `advance`/`flush` imported above are Part B's, for `bootMic`. */
const { envelope, now, said, voicedChunk, advance: advancePage } = page;
const T = () => globalThis.window.moxieBridge.transportStats();
const chatStatus = () => globalThis.document.getElementById("chat-status").textContent;
const aliveState = () => { const a = globalThis.window.moxieAlive; return a && a.__state ? a.__state() : {}; };
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
const tix = (eid, n) => [...Array(n).keys()].map((i) => ({ ticket: `v1.${eid}.T${i}.M`, event_id: eid, chunk_num: i }));
/** A hosted page: health says `ears: true`, so the mic uploads to the same-origin route. */
const hosted = (other) => (path, body, spy) =>
  path === "/api/health" ? { status: 200, json: envelope({ ears: true }) } : other(path, body, spy);
/** The transport harness's own ledger (a timer that threw) folds into this suite's. */
const foldTransportFails = () => { for (const f of page.fails.splice(0)) fails.push("transport harness: " + f); };
const HINT = "Tap Listen and talk — I'll know when you're done.";

/** Part B's spy body: bootMic's window has none. Every call lands in `order`, in order. */
function spyAlive() {
  const order = [];
  globalThis.window.moxieAlive = Object.fromEntries(
    ["listening", "thinking", "settled", "transcribing", "answered"].map((k) => [k, () => { order.push(k); }]));
  return order;
}
/** Count the real alive.js's moments as the page calls them (Part C). A base whose alive.js
 *  lacks a member leaves its count at 0: a counted red, never a TypeError. */
function watchAlive() {
  const a = globalThis.window.moxieAlive, n = { listening: 0, thinking: 0, settled: 0, transcribing: 0, answered: 0 }, order = [];
  for (const k of Object.keys(n)) {
    const f = a[k];
    if (typeof f === "function") a[k] = function () { n[k]++; order.push(k); return f.apply(a, arguments); };
  }
  return { n, order };
}

/* =========================================================================== *
 * B24. stop() DOES NOT SETTLE THE BODY: it hands her to the working cue in the same tick, the
 *      clip uploads under it, and the body is handed back only when the ears are done.
 * =========================================================================== */
{
  const w = bootMic();
  const order = spyAlive();
  await w.mic.start();
  await flush();
  w.level(0.09);
  await advance(300);
  w.mic.stop();                                        // the visitor's own second tap
  deep([order, w.posts.length, w.mic.isRecording()], [["listening", "transcribing"], 1, false],
       "B24: stop() HANDS THE BODY TO THE WORKING CUE in the same tick and does NOT settle it (it did): the clip is uploading under it");
  await flush(); await flush();                        // the transcript landed
  deep([w.published, order], [["hi moxie"], ["listening", "transcribing", "settled"]],
       "B24: …the transcript is published, and the body is handed back only once the ears are done with the clip (on the page the send arms thinking() in that same tick: B26)");
}
{
  // The same through the silence auto-stop, the way a recording usually ends.
  const w = bootMic();
  const order = spyAlive();
  await w.mic.start();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1150);                                 // 1.1 s of silence: the auto-stop sent the clip
  await flush(); await flush();
  deep([w.mic.stats().silenceStops, w.published, order], [1, ["hi moxie"], ["listening", "transcribing", "settled"]],
       "B24: the auto-stop takes the same path: the working cue at the stop, the body back when the transcript lands — never settled at the stop");
}

/* =========================================================================== *
 * B25. WHAT HANDS HER BACK: a refused clip (the scripted line stands in) and a clip with
 *      nothing said (dropped unsent) — settled once each, after the working cue.
 * =========================================================================== */
{
  const w = bootMic({ answer: () => ({ status: 503, json: { reason: "upstream_down", retry_after_s: 0 } }) });
  const order = spyAlive();
  await w.mic.start();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1150);
  await flush(); await flush();
  deep([w.published.length, w.mic.stats().fallbacks, order], [1, 1, ["listening", "transcribing", "settled"]],
       "B25: a refused clip: the working cue at the stop, the scripted line (Part B's bridge spy takes it on `sendUserTurn`), and the body handed back once");
}
{
  const w = bootMic();
  const order = spyAlive();
  await w.mic.start();
  await flush();
  w.level(0.001);
  await advance(5100);                                 // 5 s with nothing said: dropped unsent
  deep([w.mic.stats().noSpeech, w.posts.length, order], [1, 0, ["listening", "transcribing", "settled"]],
       "B25: a clip with nothing said is dropped unsent and the body handed back in the same tick as the stop (nothing is coming)");
}

/* =========================================================================== *
 * B26. THE WHOLE PAGE, one mic turn: the working cue through the upload, the body handed back
 *      and ARMED AGAIN BY THE SEND in the same tick, the voice-wait cue through chunk 0, the
 *      settle at her voice — and a typed line after it puts the Listen hint back.
 * =========================================================================== */
{
  const p = await bootPage({ answer: hosted((path) => {
    if (path === "/api/chat") return Object.assign(said("Dogs are great.", "sim-dogs", { speech: tix("sim-dogs", 1) }), { delayMs: 2000 });
    if (path === "/api/speech") return voicedChunk("sim-dogs", 0, { delayMs: 2300, seconds: 2 });
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "and what about dogs" }, delayMs: 2500 };
    return { status: 404, text: "" };
  }) });
  const w = p.world, t0 = now();
  const { n, order } = watchAlive();
  globalThis.document.getElementById("mic-status").textContent = HINT;   // as sim.html ships it
  p.mic.toggle();
  await advancePage(50);
  p.level(0.09);
  await advancePage(300);
  p.level(0.001);
  await advancePage(1150);                                 // the auto-stop at 1.5 s; the clip uploads until 4.0 s
  deep([p.mic.isRecording(), p.attrs["data-mic"], aliveState().pose, n.settled, p.micStatus()],
       [false, "on", "transcribing", 0, "transcribing…"],
       "B26: after the auto-stop the clip is uploading and she HOLDS A WORKING CUE (the ears still marked, the body transcribing, nothing settled)");
  await advancePage(2600);                                 // t+4.1 s: the transcript landed at 4.0 s
  deep([p.attrs["data-mic"], chats(w), n.settled, n.thinking, chatStatus(), order.slice(-3)],
       [undefined, ["and what about dogs"], 1, 1, "thinking…", ["transcribing", "settled", "thinking"]],
       "B26: THE TRANSCRIPT LANDS: the ears are done, the body handed back and ARMED AGAIN BY THE SEND in the same tick — thinking, with the status saying so");
  eq(p.micStatus(), 'heard: "and what about dogs"', "B26: …and the heard line shows what she heard");
  await advancePage(2000);                                 // t+6.1 s: the brain answered at 6.0 s; chunk 0 lands at 8.25 s
  deep([n.answered, n.settled, chatStatus()], [1, 1, "warming up my voice…"],
       "B26: the brain answered (6.0 s): the body is told her voice is coming, the status says so, and nothing settles");
  await advancePage(2400);                                 // t+8.5 s
  deep([n.settled, chatStatus(), w.spy.sounds.map((s) => [s.kind, s.t - t0])], [2, "", [["cloud", 8250]]],
       "B26: HER VOICE (8.25 s): the body settles there, once more, as her words and voice go out");
  await advancePage(3000);                                 // her 2 s sentence is over
  const queuedBefore = T().queued;                         // the transcript waited for the ears: 1
  globalThis.window.moxieTypedTurn.send("and cats?");
  await advancePage(10);
  eq(p.micStatus(), HINT, "B26: A TYPED LINE AFTER A MIC TURN PUTS THE LISTEN HINT BACK under the composer (the heard line used to stay for the rest of the visit)");
  deep([T().queued - queuedBefore, chats(w)], [0, ["and what about dogs", "and cats?"]],
       "B26: …and the typed line went out at once (nothing in flight)");
  foldTransportFails();
}

/* =========================================================================== *
 * B27. THE HINT IS THE PAGE'S LINE, WHATEVER CAME FIRST: a Listen tap while the ears rest
 *      writes mic.js's own line over the hint before any recording; the heard line of the
 *      next mic turn still steps aside for the hint, never for a blank — and never for a line
 *      someone else wrote after it.
 * =========================================================================== */
{
  let rested = true;
  const w = bootMic({ mode: { canUseEars: () => !rested, earsRetryAfterS: () => 40 } });
  w.els["mic-status"].textContent = HINT;              // as sim.html ships it
  await w.mic.toggle();                                // the ears rest: mic.js's own line
  const restLine = w.statusText();
  rested = false;
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.mic.stop();
  await flush(); await flush();
  const heard = w.statusText();
  if (typeof w.mic.typedLine === "function") w.mic.typedLine();
  deep([restLine !== HINT, heard, w.statusText()], [true, 'heard: "hi moxie"', HINT],
       "B27: a rest line, then a mic turn: the typed line puts THE HINT back (not a blank, not the rest line)");
  // Someone else's line written after the heard line (env.js on a mode change) stays.
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.mic.stop();
  await flush(); await flush();
  w.els["mic-status"].textContent = "hosted demo — Listen plays a scripted child line";
  if (typeof w.mic.typedLine === "function") w.mic.typedLine();
  eq(w.statusText(), "hosted demo — Listen plays a scripted child line",
     "B27: …and a line the page wrote after the heard line is not ours to replace");
}

/* =========================================================================== *
 * B28. THE LISTEN TAP STILL INTERRUPTS THE WORKING POSE: tapped while she waits for her
 *      voice (the brain answered, chunk 0 on its way), the reply ends through #317's path,
 *      its cue ends with it — settled once, the voice-wait line cleared — and she listens;
 *      the voice landing afterwards is never heard.
 * =========================================================================== */
{
  const p = await bootPage({ answer: hosted((path) => {
    if (path === "/api/chat") return Object.assign(said("Cats are great.", "sim-cats", { speech: tix("sim-cats", 1) }), { delayMs: 300 });
    if (path === "/api/speech") return voicedChunk("sim-cats", 0, { delayMs: 2300, seconds: 2 });
    return { status: 404, text: "" };
  }) });
  const w = p.world;
  const { n } = watchAlive();
  globalThis.window.moxieTypedTurn.send("tell me about cats");
  await advancePage(1000);                                 // the brain at 0.3 s; chunk 0 lands at 2.6 s
  deep([chatStatus(), aliveState().pose, n.settled], ["warming up my voice…", "voice-wait", 0],
       "B28: at 1 s she visibly waits for her voice (the brain answered at 0.3 s)");
  p.mic.toggle();                                          // the child taps Listen
  await advancePage(50);
  deep([T().interrupted, n.settled, chatStatus(), p.mic.isRecording(), aliveState().pose], [1, 1, "", true, "listening"],
       "B28: THE TAP INTERRUPTS THE WORKING POSE: the reply ends (#317's path), its cue ends with it — settled once, the voice-wait line cleared — and she listens");
  await advancePage(2000);                                 // chunk 0 landed at 2.6 s
  deep([T().lateSpeechDropped, w.spy.sounds.length, n.settled], [1, 0, 1],
       "B28: …the voice landing at 2.6 s is dropped unheard, and nothing settles again");
  foldTransportFails();
}
