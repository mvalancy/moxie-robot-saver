/* §8–10: ONE VOICE PER REPLY, measured where the sound is made — the REAL voice/ over a fake
 * Web Audio stack and speechSynthesis (harness `realVoice`), so every assertion is about what
 * a visitor would HEAR: which voices started, when, and which were cut.
 *
 *   §8  per-event voice: no line goes silent after her first voiced reply, no stand-in voice
 *       on a slow turn, a failed voice speaks locally once, and a voice failure never
 *       degrades the page; the MQTT bus and replays keep their rule.
 *   §9  the thinking filler waits for a LATE voice and never talks over her.
 *   §10 the audio unlock follows the browser's real autoplay rule.
 */
import {
  MANIFEST, advance, boot, chatMsg, chatWire, clipBytes, deep, envelope, eq, live, now, ok, readFileSync,
  join, repo, said, serve, ticket, ttsWire, voiced,
} from "./harness.mjs";

const T = () => globalThis.window.moxieBridge.transportStats();
const M = () => globalThis.window.moxieMode;
const A = () => globalThis.window.moxieAudio;

/** Send one turn and run the clock `ms`. Returns what STARTED and what was CUT meanwhile, as
 *  offsets from the send. The turn's promise is not awaited: an assertion must never hang on
 *  a voice that never comes. */
async function turn(world, text, ms) {
  const t0 = now(), s0 = world.spy.sounds.length, c0 = world.spy.cuts.length;
  globalThis.window.moxieBridge.sendUserTurn(text);
  await advance(ms);
  return {
    sounds: world.spy.sounds.slice(s0).map((s) => ({ ...s, t: s.t - t0 })),
    cuts: world.spy.cuts.slice(c0).map((c) => ({ ...c, t: c.t - t0 })),
  };
}
const kinds = (r) => r.sounds.map((s) => s.kind);
const local = (r) => r.sounds.filter((s) => s.kind !== "cloud");
const fillers = (world) => world.spy.said.filter((s) => s.who === "ambient");

const STUB_JOKE = "Why did the robot cross the road? To recharge on the other side!";
const REDIRECT = "Thank you for telling me. Feelings this big need a grown-up.";
const SPEECH_DOWN = (over) => ({ status: 503, json: envelope(Object.assign(
  { ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }, over || {})) });

/* =========================================================================== *
 * 8a. AFTER HER FIRST VOICED REPLY, EVERY OTHER LINE IS STILL HEARD — a rate-limited turn's
 *     stub answer, a safety redirect, and a reply whose voice the gateway refused. A
 *     session-wide latch once made all three silent (0 sounds each, measured on prod code).
 * =========================================================================== */
{
  let n = 0;
  const world = await boot({ realVoice: true, answer: live((path) => {
    if (path === "/api/chat") {
      n++;
      if (n === 1) return said("Hi there! Want to hear a joke?", "sim-v1", { speech: ticket("sim-v1") });
      if (n === 2) return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }) };
      if (n === 3) return said(REDIRECT, "sim-blk", { ok: true, degraded: true, reason: "blocked", mode: "live" });
      return said("Volcanoes are mountains that can puff out hot melted rock!", "sim-v4", { speech: ticket("sim-v4") });
    }
    if (path === "/api/speech") return n === 1 ? voiced("sim-v1", { seconds: 2 }) : SPEECH_DOWN();
    return { status: 404, text: "" };
  }) });

  const t1 = await turn(world, "hi moxie", 4000);
  deep(kinds(t1), ["cloud"], "8a: turn 1 is spoken in her own (gateway) voice, once, and by nothing else");

  const t2 = await turn(world, "tell me a joke", 6000);
  ok(world.spy.transcript.includes(STUB_JOKE), "8a: the rate-limited turn is answered from stub.js");
  deep(t2.sounds.map((s) => [s.kind, s.bytes]), [["clip", clipBytes(STUB_JOKE)]],
       "8a: …AND THAT STUB ANSWER IS HEARD — exactly one sound, its own shipped clip, after a voiced turn");

  await advance(21000);                       // the 429's Retry-After window lapses
  const t3 = await turn(world, "something the floor blocks", 6000);
  deep(t3.sounds.map((s) => [s.kind, s.text]), [["browser", REDIRECT]],
       "8a: the SAFETY REDIRECT is heard — once, in the local voice (it carries no voice ticket)");

  const t4 = await turn(world, "tell me about volcanoes", 6000);
  deep(t4.sounds.map((s) => [s.kind, s.text]),
       [["browser", "Volcanoes are mountains that can puff out hot melted rock!"]],
       "8a: a reply whose voice was REFUSED is heard — once, locally, never silent");
  eq(M().state(), "live", "8a: …and the refused voice did not degrade the page: a voice failure is not a brain failure");
  const st = T();
  deep([st.blocked, st.fallbacks, st.voiceFallbacks, st.speechReasons, st.reasons.includes("upstream_down")],
       [1, 1, 1, ["upstream_down"], false],
       "8a: recorded as a block, a stub answer and a voice fallback; the speech route's reason kept apart from the brain's");
  deep(world.spy.cuts, [], "8a: and in all four turns (each allowed to finish) no voice was cut short");
}

/* =========================================================================== *
 * 8b. A FIRST TURN WHOSE VOICE TAKES 4 s: the words land at SPEECH_WAIT_MS as before, but NO
 *     stand-in voice starts; her own voice plays once when it lands. (Prod measured the
 *     browser voice at +6,008 ms, cut by hers at +6,166 ms; 2 of 6 speech round trips > 2.5 s.)
 *     Both stand-ins are covered: the browser voice and a shipped clip.
 * =========================================================================== */
for (const LINE of ["I love building towers out of blocks and knocking them down!", "Hi there! It's so good to see you."]) {
  const world = await boot({ realVoice: true, answer: serve({
    "/api/chat": said(LINE, "sim-slow1", { speech: ticket("sim-slow1") }),
    "/api/speech": voiced("sim-slow1", { delayMs: 4000, seconds: 3 }),
  }) });
  const stand = MANIFEST.moxie[LINE] ? "clip" : "browser voice";
  const t0 = now();
  globalThis.window.moxieBridge.sendUserTurn("what do you like to play?");
  await advance(2600);
  deep(world.spy.setSpeech, [LINE], `8b (${stand}): the words land at 2.5 s, without waiting for the voice`);
  deep(world.spy.sounds, [], `8b (${stand}): …and NO stand-in voice starts while hers is on its way`);
  await advance(5000);
  deep(world.spy.sounds.map((s) => [s.kind, s.t - t0]), [["cloud", 4000]],
       `8b (${stand}): her voice plays ONCE, the moment it lands at 4.0 s`);
  deep(world.spy.cuts, [], `8b (${stand}): …and nothing was cut — never a robot voice restarted in hers`);
  const st = T();
  deep([st.chatFirst, st.voiceFirst, st.lateSpeechPlayed, st.lateSpeechDropped, st.voiceFallbacks], [1, 0, 1, 0, 0],
       `8b (${stand}): recorded as words-first, a late voice PLAYED, nothing dropped, no fallback`);
}

/* =========================================================================== *
 * 8c. A VOICE THAT NEVER COMES speaks the words locally, ONCE, when it is known to have
 *     failed — refused late, unreachable, or no answer by the client's own deadline (15 s,
 *     kept even where AbortSignal.timeout is missing) — and a voice turning up after that is
 *     dropped: the line has been said.
 * =========================================================================== */
{
  const LINE = "Dogs have a super nose that smells a thousand times better than ours.";
  const cases = [
    ["refused at 4 s", Object.assign(SPEECH_DOWN(), { delayMs: 4000 }), 4000],
    ["unreachable at 3 s", { delayMs: 3000, rejectLate: true }, 3000],
    ["no answer by 15 s", voiced("sim-gone1", { delayMs: 20000, seconds: 2 }), 15000],
  ];
  for (const [label, speech, at] of cases) {
    const world = await boot({ realVoice: true, answer: serve({
      "/api/chat": said(LINE, "sim-gone1", { speech: ticket("sim-gone1") }),
      "/api/speech": speech,
    }) });
    const r = await turn(world, "what do dogs smell?", 22000);
    deep(r.sounds.map((s) => [s.kind, s.text, s.t]), [["browser", LINE, at]],
         `8c (${label}): exactly one voice, the local one, starting when the failure is known (${at} ms) — not before`);
    eq(M().state(), "live", `8c (${label}): …and the page stays live`);
    const st = T();
    eq(st.voiceFallbacks, 1, `8c (${label}): recorded as one voice fallback`);
    if (label === "no answer by 15 s")
      deep([st.lateSpeechDropped, st.lateSpeechPlayed], [1, 0], "8c: the voice that arrived at 20 s was DROPPED — the line is never said twice");
  }
}

/* =========================================================================== *
 * 8d. A VOICE FAILURE IS NOT A BRAIN FAILURE: three failed voices in a row (503, network,
 *     504) leave the page live and spending; each reply is still heard once. One speech 503
 *     used to flip the page to "scripted" for ~30 s.
 * =========================================================================== */
{
  let n = 0;
  const failures = [SPEECH_DOWN(), { reject: true },
                    { status: 504, json: envelope({ ok: false, degraded: true, reason: "timeout", retry_after_s: 0, mode: "live" }) }];
  const world = await boot({ realVoice: true, answer: live((path) => {
    if (path === "/api/chat") { n++; return said("Line " + n + ", said out loud.", "sim-d" + n, { speech: ticket("sim-d" + n) }); }
    if (path === "/api/speech") return n === 1 ? voiced("sim-d1", { seconds: 1 }) : failures[n - 2];
    return { status: 404, text: "" };
  }) });
  await turn(world, "hello", 3000);
  const heard = [];
  for (let i = 0; i < 3; i++) {
    const r = await turn(world, "and again " + i, 3000);
    heard.push(local(r).length + "/" + (M().state() === "live" && M().canSpendLiveTurn() ? "live" : M().state()));
  }
  deep(heard, ["1/live", "1/live", "1/live"],
       "8d: 503, network error and 504 on /api/speech: each reply heard once, and the page STAYS live and spending");
  deep([T().chatOk, T().speechReasons], [4, ["upstream_down", "timeout"]],
       "8d: …every turn reached the brain; the speech route's reasons are recorded, not reported to the mode machine");
}

/* =========================================================================== *
 * 8e. THE BUS AND REPLAYS. A live MQTT supervisor publishes its words, THEN synthesizes the
 *     voice (turns.py), often past the 900 ms grace: once the bus has voiced a line, a later
 *     line waits for its voice (as before). A replay follows its own recording — so the
 *     shipped demo replays ALOUD even after a voiced live turn (it went silent with the latch).
 * =========================================================================== */
{
  // (i) a live bus: line 2's voice is 2 s late, and still nothing local starts.
  const world = await boot({ realVoice: true, answer: live({ status: 404, text: "" }) });
  globalThis.mqtt.connect = () => ({ connected: true, on() {}, subscribe() {}, end() {}, publish() {} });
  world.clickHandlers["bus-connect"]();
  const inner = globalThis.window.moxieBridge;
  inner.route("/devices/d_sim/commands/remote_chat", chatWire("Bus line one.", "bus-1"));
  await advance(300);
  inner.route("/devices/d_sim/commands/tts", ttsWire("bus-1", 1));
  await advance(3000);
  const s0 = world.spy.sounds.length;
  inner.route("/devices/d_sim/commands/remote_chat", chatWire("Bus line two, voiced late.", "bus-2"));
  await advance(2000);
  inner.route("/devices/d_sim/commands/tts", ttsWire("bus-2", 1));
  await advance(3000);
  deep(world.spy.sounds.map((s) => s.kind), ["cloud", "cloud"],
       "8e (bus): two bus lines, two voices, both the supervisor's — line 2's 2 s-late voice was waited for, as before");
  eq(world.spy.sounds.length - s0, 1, "8e (bus): …no local voice started under line 2");
}
{
  // (ii) the shipped demo, replayed after a voiced live turn.
  const demo = JSON.parse(readFileSync(join(repo, "sim", "web", "sessions", "demo.json"), "utf8"));
  const world = await boot({ realVoice: true, answer: live((path) => {
    if (path === "/api/chat") return said("Hi there! Want to hear a joke?", "sim-r1", { speech: ticket("sim-r1") });
    if (path === "/api/speech") return voiced("sim-r1", { seconds: 1 });
    if (path === "sessions/demo.json") return { status: 200, json: demo };
    return { status: 404, text: "" };
  }) });
  await turn(world, "hi moxie", 3000);
  const s0 = world.spy.sounds.length;
  world.clickHandlers["rec-demo"]();
  await advance(25000);
  const moxieLines = demo.map((e) => JSON.parse(e.payload)).filter((p) => p.output && p.output.text).map((p) => p.output.text);
  const heard = world.spy.sounds.slice(s0).filter((s) => moxieLines.some((t) => clipBytes(t) === s.bytes));
  eq(heard.length, moxieLines.length,
     `8e (demo): replayed after a voiced live turn, every one of Moxie's ${moxieLines.length} demo lines is HEARD`);
}
{
  // (iii) a replayed bus recording whose voices follow their words: from its 2nd line on, the
  //      replay waits for the recorded voice rather than starting a local one first.
  const rec = [
    { t: 0, topic: "/devices/d_sim/commands/remote_chat", payload: chatWire("Recorded line one.", "rec-1") },
    { t: 1500, topic: "/devices/d_sim/commands/tts", payload: ttsWire("rec-1", 1) },
    { t: 5000, topic: "/devices/d_sim/commands/remote_chat", payload: chatWire("Recorded line two.", "rec-2") },
    { t: 6500, topic: "/devices/d_sim/commands/tts", payload: ttsWire("rec-2", 1) },
  ];
  const world = await boot({ realVoice: true, answer: (path) => (path === "sessions/demo.json"
    ? { status: 200, json: rec } : { status: 404, text: "" }) });
  world.clickHandlers["rec-demo"]();
  await advance(4000);
  const s0 = world.spy.sounds.length;
  await advance(6000);
  deep(world.spy.sounds.slice(s0).map((s) => s.kind), ["cloud"],
       "8e (recording): line 2 of a replayed bus recording is heard once, in its recorded voice");
}

/* =========================================================================== *
 * 9. THE THINKING FILLER waits for a LATE voice (the expected time-to-voice, ~3.5 s: chat
 *    2.0–2.3 s + speech 1.6–1.9 s live), and never talks over her or flushes her queued answer.
 *    At 1.8 s it preceded 3 of 3 measured answers and cut a 6 s answer at 5,042 ms.
 * =========================================================================== */
{
  // (a) the measured live turn (chat 2.3 s, voice 1.9 s later): no filler at all.
  const world = await boot({ realVoice: true, answer: serve({
    "/api/chat": Object.assign(said("A quick one!", "sim-f1", { speech: ticket("sim-f1") }), { delayMs: 2300 }),
    "/api/speech": voiced("sim-f1", { delayMs: 1900, seconds: 2 }),
  }) });
  const r = await turn(world, "hi moxie", 8000);
  deep([fillers(world).length, globalThis.window.moxieAlive.stats.spoke], [0, 0],
       "9a: an answer landing at 2.3 s gets NO spoken filler — she is not talked over by her own 'hmm'");
  deep(kinds(r), ["cloud"], "9a: …the only sound of the turn is her answer");
}
{
  // (b) a slow brain (4.5 s): the filler still comes, once, at the expected time-to-voice.
  const world = await boot({ realVoice: true, answer: serve({
    "/api/chat": Object.assign(said("That took a while!", "sim-f2", { speech: ticket("sim-f2") }), { delayMs: 4500 }),
    "/api/speech": voiced("sim-f2", { seconds: 2 }),
  }) });
  const t0 = now();
  await turn(world, "a hard question", 8000);
  const f = fillers(world);
  eq(f.length, 1, "9b: a 4.5 s brain DOES get one spoken filler — the feature survives");
  ok(f.length === 1 && f[0].t - t0 >= 3500 && f[0].t - t0 < 4500,
     `9b: …at the expected time-to-voice, 3.5 s, not before (at ${f.length ? f[0].t - t0 : "never"} ms)`);
}
{
  // (c) she is still saying her previous 6 s answer when the filler beat comes.
  let n = 0;
  const world = await boot({ realVoice: true, answer: live((path) => {
    if (path === "/api/chat") {
      n++;
      return n === 1 ? said("A long story about the moon.", "sim-f3", { speech: ticket("sim-f3") })
        : Object.assign(said("Sure!", "sim-f4", { speech: ticket("sim-f4") }), { delayMs: 4500 });
    }
    if (path === "/api/speech") return n === 1 ? voiced("sim-f3", { seconds: 6 }) : voiced("sim-f4", { seconds: 1 });
    return { status: 404, text: "" };
  }) });
  await turn(world, "tell me about the moon", 1000);       // 1 s into her 6 s answer…
  const answer = world.spy.sounds.find((s) => s.kind === "cloud");
  await turn(world, "can you sing instead?", 4000);         // …the filler beat at +3.5 s = 4.5 s in
  eq(fillers(world).length, 0, "9c: no filler while she is still saying her previous answer");
  await advance(6000);                                      // …and let the next answer land too
  deep(world.spy.cuts.filter((c) => c.id === answer.id), [],
       "9c: …and that answer is NOT cut mid-sentence (it was, at 5,042 ms of 6,000, in a real browser)");
  eq(world.spy.sounds.filter((s) => s.kind === "cloud").length, 2, "9c: both answers are heard, one after the other");
}
{
  // (d) her answer is WAITING in the queue (audio still locked): neither the filler of the
  //     next turn nor any ambient line may flush it.
  let n = 0;
  const world = await boot({ realVoice: true, autoplay: "policy", answer: live((path) => {
    if (path === "/api/chat") {
      n++;
      return n === 1 ? said("Queued until you tap.", "sim-q1", { speech: ticket("sim-q1") })
        : Object.assign(said("And the next one.", "sim-q2", { speech: ticket("sim-q2") }), { delayMs: 4500 });
    }
    if (path === "/api/speech") return n === 1 ? voiced("sim-q1", { seconds: 1 }) : voiced("sim-q2", { seconds: 2 });
    return { status: 404, text: "" };
  }) });
  await turn(world, "hello?", 3000);             // no activation gesture: her voice lands, and waits
  eq(A().ttsPending(), 1, "9d: with audio still locked, her answer waits in the queue");
  await turn(world, "are you there?", 4000);     // the next turn's filler beat comes at +3.5 s
  eq(A().ttsPending(), 1, "9d: the next turn's thinking filler did NOT flush it");
  const refused = await A().speak("Hmm, let me think about that one.", "ambient");
  deep([refused, A().ttsPending()], [false, 1],
       "9d: an ambient line refuses to start rather than FLUSH a queued answer (no speaking predicate can see one)");
  world.fire("click", true);
  await advance(4000);
  deep(world.spy.sounds.filter((s) => s.running).map((s) => [s.kind, s.dur]), [["cloud", 1000], ["cloud", 2000]],
       "9d: …so the first tap plays her first answer, then the second — nothing lost");
}

/* =========================================================================== *
 * 10. THE AUDIO UNLOCK, under the browser's rule (`autoplay: "policy"`): only an ACTIVATION
 *     gesture (touchend, click, keydown) may start audio; a finger's pointerdown/touchstart
 *     may not. Unlocking there made Chrome warn on every phone run and announced an unlock
 *     that never happened.
 * =========================================================================== */
{
  const world = await boot({ realVoice: true, autoplay: "policy", answer: live({ status: 404, text: "" }) });
  world.fire("pointerdown", false);
  world.fire("touchstart", false);
  deep([world.spy.contexts.length, world.spy.events.filter((e) => e === "moxie-audio-unlocked").length, A().isUnlocked()],
       [0, 0, false],
       "10a: a finger going DOWN creates no AudioContext and announces no unlock (it cannot start audio)");
  world.fire("touchend", true);
  await advance(10);
  deep([A().isUnlocked(), world.spy.events.filter((e) => e === "moxie-audio-unlocked").length], [true, 1],
       "10b: the finger coming UP (an activation) unlocks audio, and the unlock is announced exactly once");
  world.fire("click", true);
  await advance(10);
  eq(world.spy.events.filter((e) => e === "moxie-audio-unlocked").length, 1, "10b: …and never again");
}
{
  const world = await boot({ realVoice: true, autoplay: "policy", refuseGestures: 1, answer: live({ status: 404, text: "" }) });
  world.fire("click", true);                 // a gesture the browser did not honour
  await advance(10);
  deep([A().isUnlocked(), world.spy.events.filter((e) => e === "moxie-audio-unlocked").length], [false, 0],
       "10c: a gesture that failed to start audio announces nothing — the context is still suspended");
  world.fire("keydown", true);
  await advance(10);
  deep([A().isUnlocked(), world.spy.events.filter((e) => e === "moxie-audio-unlocked").length], [true, 1],
       "10c: …and the NEXT gesture is tried again, and unlocks it");
}
