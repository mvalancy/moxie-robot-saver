/* §3–4: a slow /api/speech still lands the words on time and never starts a stand-in voice
 * that the late one would cut, and THE HAZARD ITSELF: the naive chat-first order really does
 * produce two voices through the real bridge, so the per-event expectation is shown to be
 * needed, not decorative. (06_voice_latch.mjs measures the same at the audio layer.)
 */
import {
  advance, boot, chatWire, deep, envelope, eq, said, say, serve, ticket, ttsWire, voiced,
} from "./harness.mjs";

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
