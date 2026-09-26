/* §1–2: the transport WRAPS bridge/ (§3.5) rather than replacing it, and a whole live
 * turn — what reached the network and what reached the avatar.
 */
import {
  SRC, advance, boot, deep, envelope, eq, installClock, join, makeWorld, ok, readFileSync,
  repo, said, say, serve, ticket, voiced,
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
  // The seven members §3.5 names, and every other one bridge/ publishes.
  for (const m of ["route", "sendUserTurn", "isLive", "faceEvent", "presenceStats", "telehealthStats", "hasCloudVoice"]) {
    eq(typeof outer[m], "function", `the wrapped surface still exposes ${m} (§3.5's seven)`);
  }
  for (const m of innerMembers) {
    ok(m in outer, `every member bridge/ published survives the wrap: ${m}`);
  }
  ok(Object.keys(outer).length >= innerMembers.length,
     "the wrap is ADDITIVE — it removes nothing");

  // Only `sendUserTurn` and `isLive` are replaced; every other member is the IDENTICAL
  // function reference, so nothing about their behaviour can have changed.
  for (const m of innerMembers) {
    if (m === "sendUserTurn" || m === "isLive") {
      ok(outer[m] !== innerRefs[m], `${m} is wrapped`);
    } else {
      ok(outer[m] === innerRefs[m], `${m} PASSES THROUGH as the same function reference`);
    }
  }
  eq(typeof outer.transportStats, "function", "the transport adds transportStats()");

  // The honesty guard (`mode.js`:29-35): the flag exists, and it is what flips the badge.
  eq(globalThis.window.moxieCloudTransport, true, "window.moxieCloudTransport === true");
  eq(globalThis.window.moxieMode.hasTransport(), true, "…and mode.js can see it");
  eq(globalThis.window.moxieMode.state(), "live", "a configured health reply puts the mode live");
  eq(globalThis.window.moxieMode.badge(), "MOXIE ONLINE",
     "…and the badge finally reads LIVE, which is exactly what P0-a withheld");
  eq(globalThis.window.moxieMode.canSpendLiveTurn(), true, "…and a live turn is spendable");

  /* `sim.html` loads them in the order this file evals them — measured on the
   * `<script src>` tags, since prose in an HTML comment may name the files first. */
  const html = readFileSync(join(repo, "sim", "web", "sim.html"), "utf8");
  const loadsAt = (f) => html.indexOf('src="' + f);
  for (const f of ["bridge/index.js", "mode.js", "cloud-transport.js"])
    ok(loadsAt(f) > -1, `sim.html has a <script src> for ${f}`);
  ok(loadsAt("bridge/index.js") < loadsAt("mode.js"), "sim.html loads bridge/ before mode.js");
  ok(loadsAt("mode.js") < loadsAt("cloud-transport.js"),
     "sim.html loads cloud-transport.js AFTER mode.js (it wraps what bridge/ published)");

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
  deep(Object.keys(posts[0][1]).sort(), ["context", "text"], "the chat request sends exactly text + context");
  eq(posts[0][1].text, "hi moxie", "…the visitor's sentence");
  eq(posts[0][1].context, "", "…and an empty context on the first turn");
  deep(Object.keys(posts[1][1]), ["ticket"], "the speech request sends EXACTLY the ticket — no text field");
  eq(posts[1][1].ticket, "v1.PAYLOAD.MAC", "…the ticket the chat reply minted");

  // The avatar: the child's turn is echoed (transcript + listen SFX), Moxie's line renders,
  // the markup drives the face, and the GATEWAY voice speaks it.
  deep(world.spy.transcript, ["hi moxie", "Hi there! Want to hear a joke?"],
       "both turns reached the transcript, child first");
  ok(world.spy.sfx.includes("listen"), "the `listen` SFX fired for the child's turn");
  deep(world.spy.setSpeech, ["Hi there! Want to hear a joke?"], "the speech bubble carries Moxie's line");
  ok(world.spy.setFace.includes("happy"), "the mood mark drove the face");
  eq(world.spy.playCloudTTS.length, 1, "the gateway voice played exactly once");
  eq(world.spy.speak.length, 0, "THE LOCAL VOICE NEVER SPOKE — one voice, not two");
  eq(globalThis.window.moxieBridge.hasCloudVoice(), true, "hasCloudVoice() is true after the turn");

  const st = globalThis.window.moxieBridge.transportStats();
  eq(st.turns, 1, "one turn recorded");
  eq(st.live, 1, "…taken by the live transport");
  eq(st.delegated, 0, "…not delegated");
  eq(st.chatOk, 1, "…chat ok");
  eq(st.speechOk, 1, "…speech ok");
  eq(st.voiceFirst, 1, "…and the VOICE went first");
  eq(st.chatFirst, 0, "…so the words did not go out alone");
  eq(st.fallbacks, 0, "…and the stub was not needed");
  deep(st.order, ["tts", "chat"], "THE TTS MESSAGE WAS ROUTED BEFORE THE CHAT MESSAGE (§3.4)");

  // Turn 2 carries the context blob the server minted.
  await say("tell me another", 10);
  const second = world.spy.fetches.filter(([p]) => p === "/api/chat")[1];
  eq(second[1].context, "v1.CTX.MAC", "turn 2 echoes the signed context blob verbatim");
  eq(second[1].text, "tell me another", "…with the new sentence");
}
