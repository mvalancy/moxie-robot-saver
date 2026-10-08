/* §3–4: a slow /api/speech still lands the words on time and never starts a stand-in voice
 * that the late one would cut, and THE HAZARD ITSELF: the naive chat-first order really does
 * produce two voices through the real bridge, so the per-event expectation is shown to be
 * needed, not decorative. (06_voice_latch.mjs measures the same at the audio layer.)
 * §4b–4h: one ticket per sentence — the chunks are redeemed beside each other and routed in
 * order behind chunk 0; a later chunk's failure ends the voice and never starts a local one.
 */
import {
  advance, boot, chatWire, chunked, deep, envelope, eq, now, ok, said, say, serve, ticket, tickets,
  ttsWire, voiced, voicedChunk,
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
