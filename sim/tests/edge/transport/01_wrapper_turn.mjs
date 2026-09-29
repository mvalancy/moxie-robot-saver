/* §1–2: the transport WRAPS bridge/ (§3.5) rather than replacing it, and a whole live
 * turn — what reached the network and what reached the avatar.
 */
import {
  SRC, advance, boot, deep, envelope, eq, installClock, makeWorld, ok, said, say, serve, ticket, voiced,
} from "./harness.mjs";

/* =========================================================================== *
 * 1. §3.5 — a WRAPPER, not a replacement
 * =========================================================================== */
{
  installClock();
  makeWorld({ answer: () => ({ status: 200, json: envelope() }) });
  (0, eval)(SRC.stub);
  (0, eval)(SRC.bridge);
  const innerMembers = Object.keys(globalThis.window.moxieBridge).sort();
  const innerRefs = { ...globalThis.window.moxieBridge };
  (0, eval)(SRC.mode);
  (0, eval)(SRC.transport);
  await advance(1);

  const outer = globalThis.window.moxieBridge;
  deep(["route", "sendUserTurn", "isLive", "faceEvent", "presenceStats", "telehealthStats", "hasCloudVoice"]
    .filter((m) => typeof outer[m] !== "function"), [], "the wrapped surface still exposes §3.5's seven members");
  deep(innerMembers.filter((m) => !(m in outer)), [], "the wrap is ADDITIVE — every member bridge/ published survives");
  // Only `sendUserTurn` and `isLive` are replaced; everything else is the IDENTICAL reference.
  deep(innerMembers.filter((m) => outer[m] !== innerRefs[m]), ["isLive", "sendUserTurn"],
       "only sendUserTurn and isLive are wrapped; every other member PASSES THROUGH as the same function");
  eq(typeof outer.transportStats, "function", "the transport adds transportStats()");

  // The honesty guard (`mode.js`): the flag exists, and it is what lets the badge read LIVE.
  const M = globalThis.window.moxieMode;
  deep([globalThis.window.moxieCloudTransport, M.hasTransport(), M.state(), M.badge(), M.canSpendLiveTurn()],
       [true, true, "live", "MOXIE ONLINE", true],
       "window.moxieCloudTransport is set, mode.js sees it, and a configured health reply reads LIVE and spendable");

  // With bridge/ absent the transport must do nothing rather than half-wire a page.
  installClock();
  makeWorld({});
  delete globalThis.window.moxieBridge;
  (0, eval)(SRC.transport);
  eq(globalThis.window.moxieCloudTransport, undefined,
     "with no bridge/, the transport installs nothing and does NOT claim to be live");
}

/* =========================================================================== *
 * 2. A whole live turn: what reached the network, and what reached the avatar
 * =========================================================================== */
{
  const EID = "sim-aaaabbbbcccc";
  const world = await boot({
    answer: serve({
      "/api/chat": said("Hi there! Want to hear a joke?", EID,
                        { speech: ticket(EID, "v1.PAYLOAD.MAC"), context: "v1.CTX.MAC" }),
      "/api/speech": voiced(EID),
    }),
  });

  await say("hi moxie", 10);

  const posts = world.spy.fetches.filter(([p]) => p !== "/api/health");
  deep(posts.map(([p]) => p), ["/api/chat", "/api/speech"], "one /api/chat then one /api/speech");
  deep(posts[0][1], { text: "hi moxie", context: "" }, "the chat request sends exactly the sentence and an empty first-turn context");
  deep(posts[1][1], { ticket: "v1.PAYLOAD.MAC" }, "the speech request sends EXACTLY the minted ticket — no text field");

  // The avatar: the child's turn is echoed, Moxie's line renders, the markup drives the
  // face, and ONLY the gateway voice speaks it.
  deep(world.spy.transcript, ["hi moxie", "Hi there! Want to hear a joke?"], "both turns reached the transcript, child first");
  ok(world.spy.sfx.includes("listen") && world.spy.setFace.includes("happy"), "the `listen` SFX fired and the mood mark drove the face");
  deep(world.spy.setSpeech, ["Hi there! Want to hear a joke?"], "the speech bubble carries Moxie's line");
  deep([world.spy.playCloudTTS.length, world.spy.speak.length], [1, 0], "the gateway voice played once and THE LOCAL VOICE NEVER SPOKE — one voice, not two");
  eq(globalThis.window.moxieBridge.hasCloudVoice(), true, "hasCloudVoice() is true after the turn");

  const st = globalThis.window.moxieBridge.transportStats();
  deep([st.turns, st.live, st.delegated, st.chatOk, st.speechOk, st.voiceFirst, st.chatFirst, st.fallbacks],
       [1, 1, 0, 1, 1, 1, 0, 0], "one live turn, chat and speech ok, voice-first, no fallback");
  deep(st.order, ["tts", "chat"], "THE TTS MESSAGE WAS ROUTED BEFORE THE CHAT MESSAGE (§3.4)");

  // Turn 2 carries the context blob the server minted.
  await say("tell me another", 10);
  const second = world.spy.fetches.filter(([p]) => p === "/api/chat")[1];
  deep(second[1], { text: "tell me another", context: "v1.CTX.MAC" }, "turn 2 echoes the signed context blob verbatim");
}
