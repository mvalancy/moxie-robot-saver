/* §3–4: a slow /api/speech still lands the words on time and never starts a stand-in voice
 * that the late one would cut, and THE HAZARD ITSELF: the naive chat-first order really does
 * produce two voices through the real bridge, so the per-event expectation is shown to be
 * needed, not decorative. (06_voice_latch.mjs measures the same at the audio layer.)
 * §4b–4h: one ticket per sentence — the chunks are redeemed one at a time and routed in
 * order behind chunk 0; a later chunk's failure ends the voice and never starts a local one.
 * §4i–4k: overlapping turns — a newer reply's voice starting ends the older reply's pipeline,
 * so no sentence of an older reply is paid for and lost, or heard after the newer reply.
 */
import {
  advance, boot, chatWire, chunkOf, chunked, deep, envelope, eq, live, now, ok, said, say, serve,
  ticket, tickets, ttsWire, voiced, voicedChunk,
} from "./harness.mjs";

const T = () => globalThis.window.moxieBridge.transportStats();
const M = () => globalThis.window.moxieMode;
/** The chunks `/api/speech` was asked for so far, in request order. */
const redeemed = (world) => world.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => Number(/T(\d+)/.exec(b.ticket)[1]));
/** The chunks handed to the voice so far, in routing order. */
const played = (world) => world.spy.playCloudTTS.map((m) => m.chunk_num);
const SPEECH_DOWN = () => ({ status: 503, json: envelope(
  { ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }) });
const THREE = "One sentence here. Two sentences here. Three sentences here.";

/* =========================================================================== *
 * 3. §3.4 — a SLOW /api/speech: the words still land on time, NO local voice starts,
 *    and the late voice plays once
 * =========================================================================== */
{
  const EID = "sim-slowspeech1";
  const world = await boot({
    answer: serve({
      "/api/chat": said("A slow answer.", EID, { speech: ticket(EID) }),
      "/api/speech": voiced(EID, { delayMs: 4000 }),   // beyond the 2500 ms SPEECH_WAIT_MS
    }),
  });

  const turn = globalThis.window.moxieBridge.sendUserTurn("say something slow");

  // At 2000 ms, nothing has rendered yet: the words are still waiting for the voice.
  await advance(2000);
  eq(world.spy.setSpeech.length, 0, "at t+2.0 s the words are still waiting for the voice");

  // At 2500 ms exactly, the wait elapses and the words go out — still expecting her voice.
  await advance(500);
  deep(world.spy.setSpeech, ["A slow answer."], "AT SPEECH_WAIT_MS (2500 ms) THE WORDS LAND ANYWAY");
  deep([world.spy.speak.length, world.spy.playCloudTTS.length, globalThis.window.moxieBridge.transportStats().order],
       [0, 0, ["chat"]], "…but NO local voice starts: the turn holds a voice ticket, so its words wait for that voice");

  // The audio finally arrives at 4000 ms and is PLAYED: nothing local is saying the line, so
  // there is nothing to double, and dropping it would leave the reply silent.
  await advance(2000);
  await turn;
  deep([world.spy.playCloudTTS.length, world.spy.speak.length], [1, 0],
       "LATE AUDIO PLAYS ONCE, and no local voice was ever started — one voice, not a robot voice cut by hers");
  const st = globalThis.window.moxieBridge.transportStats();
  deep([st.chatFirst, st.voiceFirst, st.lateSpeechPlayed, st.lateSpeechDropped, st.speechOk], [1, 0, 1, 0, 1],
       "the words-first path was taken and the late (successful) audio recorded as PLAYED");
}

/* =========================================================================== *
 *    …and a late voice is never dropped because SOMETHING ELSE is speaking: the transport
 *    no longer asks the narrow `isSpeaking()` (server voice only) whether to play it. A voice
 *    still in the air is voice/'s to queue behind (or cut, if it is a local one); dropping
 *    this reply's voice made the reply silent.
 * =========================================================================== */
{
  const EID = "sim-lateplays01";
  const world = await boot({
    isSpeaking: () => true,                     // her previous answer is still in the air
    answer: serve({
      "/api/chat": said("Late but welcome.", EID, { speech: ticket(EID) }),
      "/api/speech": voiced(EID, { delayMs: 4000 }),
    }),
  });
  await say("hello", 6000);
  deep([world.spy.playCloudTTS.length, world.spy.speak.length], [1, 0],
       "with her server voice still speaking, LATE AUDIO IS PLAYED rather than lost — and nothing local stood in");
  const st = globalThis.window.moxieBridge.transportStats();
  deep([st.lateSpeechPlayed, st.lateSpeechDropped, st.order], [1, 0, ["chat", "tts"]], "…recorded as played, arriving after the words");
}

/* =========================================================================== *
 * 4. THE HAZARD ITSELF — proof the ordering rule is needed, not decorative
 * =========================================================================== */
{
  const EID = "sim-naiveorder1";
  const world = await boot({ answer: () => ({ status: 200, json: envelope() }) });
  const inner = globalThis.window.moxieBridge;

  // Drive the NAIVE order straight through the real bridge: chat message first, then the
  // TTS message. This is what a transport that simply routed the responses in the order
  // they arrived would do.
  inner.route("/devices/d_sim/commands/remote_chat", chatWire("Two voices at once.", EID));
  eq(world.spy.speak.length, 1, "chat-first: `speakLocally` SPOKE IMMEDIATELY (no broker connected)");
  inner.route("/devices/d_sim/commands/tts", ttsWire(EID));
  eq(world.spy.playCloudTTS.length, 1, "THE NAIVE ORDER REALLY DOES PRODUCE TWO VOICES — the gateway audio played too (§3.4)");

  // And the shipped order, on the same bridge, produces one.
  const world2 = await boot({ answer: () => ({ status: 200, json: envelope() }) });
  const inner2 = globalThis.window.moxieBridge;
  inner2.route("/devices/d_sim/commands/tts", ttsWire(EID));
  inner2.route("/devices/d_sim/commands/remote_chat", chatWire("One voice.", EID));
  deep([world2.spy.playCloudTTS.length, world2.spy.speak.length], [1, 0], "tts-first: the gateway voice played and the local voice stood down");

  // So does chat-first, once the event's voice is EXPECTED (what the transport declares for a
  // ticketed turn): the words wait silently, and their own voice is the only one.
  const world3 = await boot({ answer: () => ({ status: 200, json: envelope() }) });
  const inner3 = globalThis.window.moxieBridge;
  const perEvent = typeof inner3.expectCloudVoice === "function" && typeof inner3.releaseCloudVoice === "function";
  eq(perEvent, true, "bridge/ publishes the per-event voice seam: expectCloudVoice / releaseCloudVoice");
  if (perEvent) {
    inner3.expectCloudVoice(EID);
    inner3.route("/devices/d_sim/commands/remote_chat", chatWire("Expected voice.", EID));
    eq(world3.spy.speak.length, 0, "expected voice, chat-first: the words do NOT start a local voice");
    inner3.route("/devices/d_sim/commands/tts", ttsWire(EID));
    deep([world3.spy.playCloudTTS.length, world3.spy.speak.length], [1, 0], "…and the voice that lands is the only one");

    // …while an expected voice that FAILS hands the held words to the local voice, once.
    const world4 = await boot({ answer: () => ({ status: 200, json: envelope() }) });
    const inner4 = globalThis.window.moxieBridge;
    inner4.expectCloudVoice("sim-fails1");
    inner4.route("/devices/d_sim/commands/remote_chat", chatWire("Held, then spoken.", "sim-fails1"));
    inner4.releaseCloudVoice("sim-fails1");
    inner4.releaseCloudVoice("sim-fails1");
    deep(world4.spy.speak, ["Held, then spoken."], "released: the held words are spoken locally ONCE (a second release says nothing)");
  }
}

/* =========================================================================== *
 * 4b. ONE TICKET PER SENTENCE. The chunks are redeemed ONE AT A TIME — chunk 1 the moment
 *     chunk 0 lands, chunk 2 the moment chunk 1 lands — so each later sentence synthesises
 *     while the one before it plays, and chunk 0 never shares the gateway with anything
 *     (measured 2026-10-08: beside a second synthesis chunk 0 took 2.4-3.7 s, median 3.3 s,
 *     against medians of 2.0-2.5 s alone). They are
 *     routed behind chunk 0 in order; one voice, no local stand-in. (origin/dev redeemed
 *     speech[0] only: one /api/speech, one chunk, the rest of the reply never spoken.)
 * =========================================================================== */
{
  const EID = "sim-chunks3";
  const AT = [1000, 1500, 600];                        // each delay runs from its own request
  const world = await boot({ answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => voicedChunk(EID, n, { delayMs: AT[n] })) });
  const turn = globalThis.window.moxieBridge.sendUserTurn("say three things");

  await advance(500);
  deep(redeemed(world), [0], "4b: chunk 0 alone is redeemed first — nothing synthesises beside the first words");
  await advance(600);                                  // t+1100: chunk 0 landed at 1000
  deep(T().order, ["tts", "chat"], "4b: chunk 0 first, then the words — voice first, exactly as one ticket");
  deep(redeemed(world), [0, 1], "4b: …and chunk 1 goes out the moment chunk 0 lands, not before");
  await advance(1000);                                 // t+2100: chunk 1 not yet (lands at 2500)
  deep([redeemed(world), played(world)], [[0, 1], [0]], "4b: chunk 2 is not requested until chunk 1 has landed");
  await advance(500);                                  // t+2600: chunk 1 landed at 2500, chunk 2 requested
  deep([redeemed(world), played(world)], [[0, 1, 2], [0, 1]], "4b: chunk 1 is routed as it lands and chunk 2 goes out");
  await advance(700);                                  // t+3300: chunk 2 landed at 3100
  await turn;
  deep(played(world), [0, 1, 2], "4b: the chunks reach the voice as 0, 1, 2");
  deep(T().order, ["tts", "chat", "tts", "tts"], "4b: …routed behind the words, in order");
  eq(world.spy.speak.length, 0, "4b: and no local voice was ever started");
  const st = T();
  deep([st.tickets, st.speechOk, st.chunksRouted, st.chunkFailures, st.chunksDropped, st.voiceFirst, st.voiceFallbacks],
       [3, 3, 2, 0, 0, 1, 0], "4b: recorded: 3 tickets, 3 redemptions, 2 later chunks routed, nothing failed or dropped");
}

/* =========================================================================== *
 * 4c. A LATER CHUNK FAILS: the voice ends there — her first sentence was heard in her
 *     voice — and NO local voice stands in for the rest (the words are on screen). A chunk
 *     that had already landed behind the failure is not played; the page stays live.
 * =========================================================================== */
{
  const EID = "sim-chunkfail";
  const world = await boot({ answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => (n === 1 ? Object.assign(SPEECH_DOWN(), { delayMs: 1200 }) : voicedChunk(EID, n, { delayMs: 1000 }))) });
  await say("say three things", 6000);
  deep(played(world), [0], "4c: chunk 0 played; chunk 1 was refused, so the voice ends there");
  eq(world.spy.speak.length, 0, "4c: A FAILED LATER CHUNK NEVER STARTS A LOCAL VOICE");
  deep(redeemed(world), [0, 1], "4c: …and chunk 2 is never redeemed: a failed voice stops spending");
  const st = T();
  deep([st.chunkFailures, st.chunksDropped, st.chunksRouted, st.voiceFallbacks, st.speechReasons, st.reasons.includes("upstream_down")],
       [1, 0, 0, 0, ["upstream_down"], false],
       "4c: one chunk failure, nothing dropped, no fallback; the reason recorded, never noted");
  eq(M().state(), "live", "4c: …and the page stays live");
}

/* =========================================================================== *
 * 4d. CHUNK 0 FAILS: the words are spoken locally, ONCE, exactly as with one ticket, and
 *     chunks 1 and 2 are never redeemed — a failed voice stops spending.
 * =========================================================================== */
{
  const EID = "sim-chunk0fail";
  const world = await boot({ answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => (n === 0 ? Object.assign(SPEECH_DOWN(), { delayMs: 800 }) : voicedChunk(EID, n, { delayMs: 1000 }))) });
  await say("say three things", 6000);
  deep([played(world), world.spy.speak], [[], [THREE]],
       "4d: chunk 0 refused: the line is spoken locally ONCE, and no chunk is played over it");
  deep(redeemed(world), [0], "4d: chunks 1 and 2 were never redeemed");
  const st = T();
  deep([st.voiceFallbacks, st.chunksDropped, st.chunkFailures, st.chunksRouted], [1, 0, 0, 0],
       "4d: recorded: one voice fallback, nothing dropped, no later-chunk failure (the voice was over)");
}

/* =========================================================================== *
 * 4e. CHUNK 0 SLOW (past SPEECH_WAIT_MS): the words land at 2.5 s with no local voice and
 *     nothing else is requested meanwhile; when chunk 0 lands at 3 s it plays, and chunks 1
 *     and 2 follow it in order — the §3 behaviour, per chunk.
 * =========================================================================== */
{
  const EID = "sim-chunkslow0";
  const world = await boot({ answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => voicedChunk(EID, n, { delayMs: n === 0 ? 3000 : 500 })) });
  const turn = globalThis.window.moxieBridge.sendUserTurn("say three things");
  await advance(2600);
  deep([world.spy.setSpeech, played(world), redeemed(world), world.spy.speak.length], [[THREE], [], [0], 0],
       "4e: at 2.5 s the words are out, only chunk 0 has been requested, and nothing local speaks");
  await advance(1600);                                 // chunk 0 at 3.0 s, chunk 1 at 3.5 s, chunk 2 at 4.0 s
  await turn;
  deep([played(world), T().order], [[0, 1, 2], ["chat", "tts", "tts", "tts"]],
       "4e: chunk 0 lands at 3 s and plays first; chunks 1 and 2 follow it in order");
  const st = T();
  deep([st.chatFirst, st.lateSpeechPlayed, st.chunksRouted, st.voiceFallbacks], [1, 1, 2, 0],
       "4e: recorded as words-first, a late chunk 0 PLAYED, two later chunks routed, no fallback");
}

/* =========================================================================== *
 * 4f. A LATER CHUNK THAT NEVER ANSWERS: the voice ends at the client's own 15 s deadline
 *     (from that chunk's request), chunk 2 is never requested, the audio turning up at 20 s
 *     is dropped, and still no local voice.
 * =========================================================================== */
{
  const EID = "sim-chunkhang";
  const world = await boot({ answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => voicedChunk(EID, n, { delayMs: n === 1 ? 20000 : 1000 })) });
  const t0 = now();
  const turn = globalThis.window.moxieBridge.sendUserTurn("say three things");
  await advance(15500);                                // chunk 1 was requested at 1 s: its deadline is 16 s
  deep([played(world), redeemed(world), T().chunkFailures], [[0], [0, 1], 0],
       "4f: at 15.5 s chunk 1 is still awaited — nothing was written off early, and chunk 2 not requested");
  await advance(1000);
  deep([played(world), T().chunkFailures, redeemed(world)], [[0], 1, [0, 1]],
       `4f: at its 15 s deadline (${now() - t0} ms in) chunk 1 is given up and chunk 2 is never requested`);
  await advance(6000);
  await turn;
  deep([played(world), world.spy.speak.length, T().chunksDropped], [[0], 0, 1],
       "4f: chunk 1's audio at 21 s is dropped, and no local voice ever started");
}

/* =========================================================================== *
 * 4g. ON THE REAL voice/: three chunks of one event are HEARD in order, as one voice, with
 *     nothing cut; chunk 2 is queued behind chunk 1 while it plays (pipelined).
 * =========================================================================== */
{
  const EID = "sim-realchunks";
  const AT = [1000, 1500, 600], SEC = [1, 2, 3];      // chunk 1 lands at 2.5 s, chunk 2 at 3.1 s
  const world = await boot({ realVoice: true, answer: chunked(said(THREE, EID, { speech: tickets(EID, 3) }),
    (n) => voicedChunk(EID, n, { delayMs: AT[n], seconds: SEC[n] })) });
  globalThis.window.moxieBridge.sendUserTurn("say three things");
  await advance(9000);
  deep(world.spy.sounds.map((s) => [s.kind, s.dur]), [["cloud", 1000], ["cloud", 2000], ["cloud", 3000]],
       "4g: the three chunks are heard in chunk order (1 s, 2 s, 3 s), all in her gateway voice");
  deep(world.spy.cuts, [], "4g: …and none was cut short");
  const ps = globalThis.window.moxieAudio.lastPlaybackStats();
  deep([ps.event_id, ps.chunks_played, ps.order], [EID, 3, [0, 1, 2]], "4g: voice/ recorded one event, three chunks, started 0, 1, 2");
  ok(ps.max_pending >= 1, `4g: …with a later chunk queued behind the one playing (pipelined), max_pending ${ps.max_pending}`);
  eq(world.spy.speak.length, 0, "4g: no local voice");
}

/* =========================================================================== *
 * 4h. THE TICKET ARRAY IS READ DEFENSIVELY: chunk 0 is found by number, not position, and
 *     an array with no chunk 0 is no voice at all (the words speak locally, nothing redeemed).
 * =========================================================================== */
{
  const EID = "sim-tixorder";
  const world = await boot({ answer: chunked(
    said("Two. One.", EID, { speech: [tickets(EID, 2)[1], tickets(EID, 2)[0]] }),
    (n) => voicedChunk(EID, n, { delayMs: 500 })) });
  await say("hi", 3000);
  deep([redeemed(world), played(world)], [[0, 1], [0, 1]], "4h: tickets listed 1, 0 are redeemed and routed 0, 1");

  const world2 = await boot({ answer: chunked(
    said("Only chunk one.", "sim-nochunk0", { speech: [tickets("sim-nochunk0", 2)[1]] }),
    (n) => voicedChunk("sim-nochunk0", n, { delayMs: 500 })) });
  await say("hi", 3000);
  deep([redeemed(world2), world2.spy.speak], [[], ["Only chunk one."]],
       "4h: no chunk 0 among the tickets: nothing is redeemed and the words speak locally, as with no voice");
  eq(T().tickets, 0, "4h: …and no ticket is counted");
}

/* =========================================================================== *
 * 4i. TWO TYPED TURNS OVERLAP — the second sent 200 ms after the first, as an impatient child
 *     re-sends — on the REAL voice/, with the measured medians (chat 1.8 s, every /api/speech
 *     2.3 s) and three chunks each, told apart by length (A 3.0/3.1/3.2 s, B 2.0/2.1/2.2 s).
 *     THE MOMENT B's CHUNK 0 IS ROUTED, A's PIPELINE IS OVER: nothing more of A is requested
 *     (A1, already in flight, is dropped when it lands), and no sentence of A is heard after B
 *     starts. Before this (PR head 9be2293) the same run heard A0, B0, B1, B2 and then A2 at
 *     14.6 s, out of context, with A1 paid for and flushed by voice/ as `superseded`.
 * =========================================================================== */
{
  const DUR = { A: [3.0, 3.1, 3.2], B: [2.0, 2.1, 2.2] };
  const tix = (who, n) => [...Array(n).keys()].map((i) => ({ ticket: `v1.${who}${i}.M`, event_id: "sim-ov-" + who, chunk_num: i }));
  const whoOf = (body) => (/B/.test(body.text) ? "B" : "A");
  const ticketOf = (body) => /^v1\.([AB])(\d)\.M$/.exec(body.ticket);
  const world = await boot({ realVoice: true, answer: live((path, body) => {
    if (path === "/api/chat") {
      const who = whoOf(body);
      return Object.assign(said(`Reply ${who} one. Reply ${who} two. Reply ${who} three.`, "sim-ov-" + who, { speech: tix(who, 3) }), { delayMs: 1800 });
    }
    if (path === "/api/speech") {
      const [, who, k] = ticketOf(body);
      return voicedChunk("sim-ov-" + who, Number(k), { delayMs: 2300, seconds: DUR[who][Number(k)] });
    }
    return { status: 404, text: "" };
  }) });
  const t0 = now();
  const name = (s) => { for (const w of ["A", "B"]) { const k = DUR[w].findIndex((d) => Math.round(d * 1000) === s.dur); if (k >= 0) return w + k; } return s.kind; };
  const requests = () => world.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => b.ticket.slice(3, 5));
  const heard = () => world.spy.sounds.map((s) => name(s) + "@" + (s.t - t0));

  globalThis.window.moxieBridge.sendUserTurn("turn A");
  await advance(200);
  globalThis.window.moxieBridge.sendUserTurn("turn B");
  await advance(4000);                                 // t+4.2 s: A0 landed at 4.1 s and plays; A1 just requested
  deep([requests(), heard()], [["A0", "B0", "A1"], ["A0@4100"]], "4i: at 4.2 s A0 is heard and A1 is in flight; B0 is still synthesising");
  await advance(200);                                  // t+4.4 s: B0 landed at 4.3 s and was routed, queued behind A0
  eq(T().chunksSuperseded, 2, "4i: B0 ROUTED: A's pipeline is over, its two remaining chunks given up");
  await advance(20000);
  deep(requests(), ["A0", "B0", "A1", "B1", "B2"],
       "4i: NO /api/speech FOR A AFTER B0 WAS ROUTED: A2 is never paid for (A1 was already in flight)");
  deep(heard(), ["A0@4100", "B0@7100", "B1@9100", "B2@11200"],
       "4i: A0 plays out, then B whole and in order, and NO SENTENCE OF A IS HEARD AFTER B STARTS");
  deep(world.spy.cuts, [], "4i: nothing was cut");
  eq(world.spy.speak.length, 0, "4i: no local voice");
  const st = T();
  deep([st.voiceFirst, st.chunksRouted, st.chunksDropped, st.chunkFailures, st.speechOk, st.tickets], [2, 2, 1, 0, 5, 6],
       "4i: recorded: both replies voice-first, B's two later chunks routed, A1's audio dropped, no failure, 5 redemptions answered");
}

/* =========================================================================== *
 * 4j. …AND WHEN THE OLDER REPLY'S CHUNK 0 HAS NOT LANDED YET (a slow first synthesis, 6 s):
 *     the newer voice starting gives it up too. A's words go out silently at the 2.5 s wait,
 *     NO local voice stands in for them over B, and when A0 lands at 7.8 s it is dropped:
 *     nothing of A is heard at all, and A1 is never requested.
 * =========================================================================== */
{
  const tix = (who) => [0, 1].map((i) => ({ ticket: `v1.${who}${i}.M`, event_id: "sim-ov2-" + who, chunk_num: i }));
  const world = await boot({ realVoice: true, answer: live((path, body) => {
    if (path === "/api/chat") {
      const who = /B/.test(body.text) ? "B" : "A";
      return Object.assign(said(`Reply ${who} one. Reply ${who} two.`, "sim-ov2-" + who, { speech: tix(who) }), { delayMs: 1800 });
    }
    if (path === "/api/speech") {
      const [, who, k] = /^v1\.([AB])(\d)\.M$/.exec(body.ticket);
      return voicedChunk("sim-ov2-" + who, Number(k), { delayMs: who === "A" ? 6000 : 1900, seconds: who === "A" ? 3 : 2 });
    }
    return { status: 404, text: "" };
  }) });
  const t0 = now();
  globalThis.window.moxieBridge.sendUserTurn("turn A");
  await advance(200);
  globalThis.window.moxieBridge.sendUserTurn("turn B");
  await advance(4300);                                 // B0 landed at 3.9 s and plays; A's wait elapsed at 4.3 s
  deep([world.spy.setSpeech, T().chunksSuperseded, T().chatFirst], [["Reply B one. Reply B two.", "Reply A one. Reply A two."], 2, 1],
       "4j: B's words with its voice at 3.9 s, A's words silently at its 2.5 s wait; A's whole voice given up");
  await advance(12000);                                // A0 lands at 7.8 s, after B's last chunk started
  deep(world.spy.sounds.map((s) => [s.kind, s.dur, s.t - t0]), [["cloud", 2000, 3900], ["cloud", 2000, 5900]],
       "4j: ONLY B IS HEARD, whole and in order; A0 landing at 7.8 s is dropped, not played after B");
  deep([world.spy.speak, world.spy.cuts], [[], []], "4j: NO LOCAL VOICE stood in for A's words over B, and nothing was cut");
  const st = T();
  deep([world.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => b.ticket.slice(3, 5)), st.lateSpeechDropped, st.voiceFallbacks, st.chunksRouted, st.chunksDropped],
       [["A0", "B0", "B1"], 1, 0, 1, 0], "4j: A1 never requested; A0 recorded as dropped, no fallback; B1 routed");
}

/* =========================================================================== *
 * 4k. A NEWER REPLY SPOKEN LOCALLY ENDS THE OLDER PIPELINE TOO: the second turn's chat is
 *     refused (429) and answered from stub.js — a clip, which takes the speakers (voice/ cuts
 *     the cloud chunk and clears its queue, as before). The older reply's chunk in flight is
 *     dropped when it lands and its last chunk is never requested, so nothing of it resurfaces
 *     after the stub line (unguarded, voice/ played A1 1.2 s after the clip).
 * =========================================================================== */
{
  const EID = "sim-ov3";
  let chats = 0;
  const world = await boot({ realVoice: true, answer: live((path, body) => {
    if (path === "/api/chat") {
      chats++;
      if (chats === 1) return Object.assign(said(THREE, EID, { speech: tickets(EID, 3) }), { delayMs: 1800 });
      return { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }) };
    }
    if (path === "/api/speech") return voicedChunk(EID, chunkOf(body), { delayMs: 2300, seconds: 3 });
    return { status: 404, text: "" };
  }) });
  const t0 = now();
  globalThis.window.moxieBridge.sendUserTurn("say three things");
  await advance(4200);                                 // chunk 0 heard at 4.1 s, chunk 1 requested
  globalThis.window.moxieBridge.sendUserTurn("tell me a joke");   // refused at once; the stub answers after 450 ms
  await advance(20000);
  deep(world.spy.sounds.map((s) => [s.kind, s.t - t0]), [["cloud", 4100], ["clip", 4650]],
       "4k: chunk 0, then the stub's clip at 4.65 s, and NOTHING OF THE OLDER REPLY AFTER IT");
  eq(world.spy.cuts.length, 1, "4k: the clip cut chunk 0 short (voice/'s rule: a reply takes the speakers; unchanged)");
  const st = T();
  deep([redeemed(world), st.chunksSuperseded, st.chunksDropped, st.chunksRouted, st.chatRefused, st.fallbacks],
       [[0, 1], 2, 1, 0, 1, 1], "4k: chunk 2 never requested; chunk 1's audio dropped; recorded as a refusal answered by the stub");
}
