/* §3–4: a slow /api/speech still lands the words on time and never layers a late voice, and
 * THE HAZARD ITSELF: the naive chat-first order really does produce two voices through the
 * real bridge, so the voice-first rule is shown to be needed, not decorative.
 */
import {
  advance, boot, chatWire, deep, envelope, eq, said, say, serve, ticket, ttsWire, voiced,
} from "./harness.mjs";

/* =========================================================================== *
 * 3. §3.4 — a SLOW /api/speech: the words still land on time, and the late voice
 *    is dropped rather than layered
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

  // At 2500 ms exactly, the wait elapses and the words go out ALONE.
  await advance(500);
  deep(world.spy.setSpeech, ["A slow answer."], "AT SPEECH_WAIT_MS (2500 ms) THE WORDS LAND ANYWAY");
  eq(world.spy.speak.length, 1, "…and speak from the clip/browser voice, exactly as today");
  eq(world.spy.playCloudTTS.length, 0, "…with no gateway audio yet");
  deep(globalThis.window.moxieBridge.transportStats().order, ["chat"], "…chat routed first this time");

  // The audio finally arrives at 4000 ms. The local voice is already in the air, so it is
  // DROPPED — the double voice §3.4 warns about must not happen on the slow path either.
  await advance(2000);
  await turn;
  eq(world.spy.playCloudTTS.length, 0, "LATE AUDIO IS DROPPED while the local voice is speaking");
  const st = globalThis.window.moxieBridge.transportStats();
  eq(st.chatFirst, 1, "the chat-first path was taken");
  eq(st.voiceFirst, 0, "…not the voice-first one");
  eq(st.lateSpeechDropped, 1, "…and the late audio was recorded as dropped");
  eq(st.speechOk, 1, "…even though the speech call itself succeeded");
}

/* =========================================================================== *
 *    …and the other half of that rule: when NOTHING is speaking, late audio plays.
 *    From turn 2 on `cloudVoice` is latched, `speakLocally` is a no-op, and the late
 *    reply is exactly what the page needs.
 * =========================================================================== */
{
  const EID = "sim-lateplays01";
  const world = await boot({
    isSpeaking: () => false,                    // nothing is in the air
    answer: serve({
      "/api/chat": said("Late but welcome.", EID, { speech: ticket(EID) }),
      "/api/speech": voiced(EID, { delayMs: 4000 }),
    }),
  });
  await say("hello", 6000);
  eq(world.spy.playCloudTTS.length, 1, "with nothing speaking, LATE AUDIO IS PLAYED rather than lost");
  const st = globalThis.window.moxieBridge.transportStats();
  eq(st.lateSpeechPlayed, 1, "…and recorded as played");
  eq(st.lateSpeechDropped, 0, "…not as dropped");
  deep(st.order, ["chat", "tts"], "…arriving after the words, which is the honest order for it");
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
  eq(world.spy.playCloudTTS.length, 1, "…and then the gateway audio played too");
  eq(world.spy.speak.length + world.spy.playCloudTTS.length, 2,
     "THE NAIVE ORDER REALLY DOES PRODUCE TWO VOICES — this is the bug §3.4 designs around");

  // And the shipped order, on the same bridge, produces one.
  const world2 = await boot({ answer: () => ({ status: 200, json: envelope() }) });
  const inner2 = globalThis.window.moxieBridge;
  inner2.route("/devices/d_sim/commands/tts", ttsWire(EID));
  inner2.route("/devices/d_sim/commands/remote_chat", chatWire("One voice.", EID));
  eq(world2.spy.playCloudTTS.length, 1, "tts-first: the gateway voice played");
  eq(world2.spy.speak.length, 0, "…and the local voice stood down (cloudVoice latched)");
}
