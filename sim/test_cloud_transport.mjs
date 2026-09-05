/* test_cloud_transport.mjs — the live turn in the browser, on a virtual clock.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §8.1 test 5, plus §3.4 (the voice-first
 * ordering rule and the double voice it prevents), §3.5 (a wrapper, not a replacement),
 * §6.3 (the fallback state machine).
 *
 * WHAT RUNS HERE: the REAL `sim/web/bridge.js`, the REAL `sim/web/stub.js`, the REAL
 * `sim/web/mode.js` and the REAL `sim/web/cloud-transport.js`, loaded as SOURCE under a
 * stubbed window/document/fetch — the trick `sim/test_bridge.mjs`:31-51 established and
 * `sim/test_mode.mjs` extended. No browser, no network, no Cloudflare account.
 *
 * TIME IS INJECTED. `setTimeout`/`clearTimeout` are replaced by a virtual clock, so the
 * 2500 ms speech wait and the 450 ms fallback beat are exercised in microseconds and
 * DETERMINISTICALLY — a real-timer version of this file would be a race condition with a
 * pass rate. Every assertion is on RECORDED state (`transportStats()`, the spy call logs,
 * the fake DOM's contents) and never on a live sample (playbook rule 11).
 *
 * ============================================================================
 * THE HAZARD THIS FILE IS REALLY ABOUT.
 *
 * `bridge.js`'s `speakLocally` speaks IMMEDIATELY when no MQTT client is connected
 * (`bridge.js`:298-300 — the 900 ms grace window only applies on a live bus). So a naive
 * HTTP transport that routed the chat message first would play the browser/clip voice AND
 * THEN the gateway voice, one over the other. Block 4 below drives that naive order
 * through the real bridge and PROVES the double voice happens — and then block 3 proves
 * the shipped transport's ordering makes it structurally impossible. A test that only
 * showed the fix works would not show the fix was needed.
 * ============================================================================
 *
 *   node sim/test_cloud_transport.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

const fails = [];
let __asserts = 0;
const ok = (c, m) => { __asserts++; if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

const SRC = {
  stub: readFileSync(join(repo, "sim", "web", "stub.js"), "utf8"),
  bridge: readFileSync(join(repo, "sim", "web", "bridge.js"), "utf8"),
  mode: readFileSync(join(repo, "sim", "web", "mode.js"), "utf8"),
  transport: readFileSync(join(repo, "sim", "web", "cloud-transport.js"), "utf8"),
};

/* --------------------------------------------------------------------------- *
 * A virtual clock
 * --------------------------------------------------------------------------- */
let clockNow = 0;
let timerSeq = 0;
let timers = [];
const realSetImmediate = setImmediate;

function installClock() {
  clockNow = 0;
  timerSeq = 0;
  timers = [];
  globalThis.setTimeout = (fn, ms) => {
    const id = ++timerSeq;
    timers.push({ id, at: clockNow + (Number(ms) || 0), fn });
    return id;
  };
  globalThis.clearTimeout = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  };
  globalThis.clearInterval = globalThis.clearTimeout;
  globalThis.setInterval = globalThis.setTimeout;
}

/** Drain the microtask queue and any already-resolved promise chains. */
const flush = () => new Promise((r) => realSetImmediate(r));

/** Advance the virtual clock, firing due timers in order and flushing between each. */
async function advance(ms) {
  const target = clockNow + ms;
  await flush();
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const next = timers.find((t) => t.at <= target);
    if (!next) break;
    timers.splice(timers.indexOf(next), 1);
    clockNow = next.at;
    try { next.fn(); } catch (e) { fails.push("a timer threw: " + e.message); }
    await flush();
  }
  clockNow = target;
  await flush();
}

/* --------------------------------------------------------------------------- *
 * A fake DOM + audio + mqtt, and the page's own globals
 * --------------------------------------------------------------------------- */
function makeWorld(opts) {
  const o = opts || {};
  const spy = {
    speak: [],            // window.moxieAudio.speak — the LOCAL voice
    playCloudTTS: [],     // the GATEWAY voice
    sfx: [],
    setSpeech: [],
    setFace: [],
    transcript: [],       // [role, text]
    fetches: [],          // [path, bodyObject]
    modeNotes: [],
  };
  let speaking = false;

  /* A DOM faithful in the one way that matters here: `getElementById` returns NULL for an
   * id the page does not have. `cloud-transport.js::injectTalkUI` guards on
   * `getElementById("chat-send")`, so an auto-vivifying fake would make it skip the
   * injection entirely and the test would assert against elements nobody wired. Injected
   * children are registered by id as they are inserted, exactly as a real DOM does. */
  const clickHandlers = {};
  const keyHandlers = {};
  const els = {};

  const mkEl = (id) => {
    const el = {
      id: id || "", value: "", textContent: "", innerHTML: "", className: "", type: "",
      scrollTop: 0, scrollHeight: 0, children: [], _attrs: {},
      addEventListener(ev, cb) {
        if (!el.id) return;
        if (ev === "click") clickHandlers[el.id] = cb;
        if (ev === "keydown") keyHandlers[el.id] = cb;
      },
      setAttribute(k, v) { el._attrs[k] = v; },
      getAttribute(k) { return el._attrs[k] === undefined ? null : el._attrs[k]; },
      appendChild(c) { attach(el, c); },
      insertBefore(c) { attach(el, c); },
      querySelector: () => ({ set textContent(v) { el._text = v; }, get textContent() { return el._text || ""; } }),
      querySelectorAll: () => [],
      closest: (sel) => (sel === "section.sub" ? el._section || null : null),
      get parentNode() { return el._parent || null; },
    };
    return el;
  };

  /** Insert a node: register it (and its subtree) by id, and record transcript rows. */
  function attach(parent, child) {
    if (!child) return;
    parent.children.push(child);
    child._parent = parent;
    if (child._text !== undefined) spy.transcript.push(child._text);
    const walk = (n) => {
      if (!n) return;
      if (n.id) els[n.id] = n;
      for (const c of n.children || []) walk(c);
    };
    walk(child);
  }

  // The ids the real page has, and that bridge.js/mode.js look for. Everything else
  // answers null.
  for (const id of ["transcript", "bus-status", "bus-host", "bus-connect", "presence-badge",
                    "presence-state", "presence-status", "presence-toggle", "rec-toggle",
                    "rec-save", "rec-demo", "rec-load", "mic-btn", "mic-status", "topbar"]) {
    els[id] = mkEl(id);
  }
  // The Comms panel shape `injectTalkUI` looks for: `#mic-btn` inside a `section.sub`
  // that has a parent to insert before.
  const micSection = mkEl("");
  const panel = mkEl("");
  micSection._parent = panel;
  els["mic-btn"]._section = micSection;

  globalThis.document = {
    readyState: "complete",
    hidden: false,
    getElementById: (id) => els[id] || null,
    createElement: () => mkEl(""),
    addEventListener() {},
    body: { appendChild() {}, setAttribute() {} },
  };

  globalThis.window = {
    addEventListener() {},
    moxie: {
      setFace: (f) => spy.setFace.push(f),
      setSpeech: (t) => spy.setSpeech.push(t),
      setMotor() {}, getMotor: () => 16384,
      showIcons() {}, clearIcons() {}, setHeartLED() {},
    },
    moxieAudio: {
      speak: (t) => { spy.speak.push(t); speaking = true; },
      stop() { speaking = false; },
      sfx: (n) => spy.sfx.push(n),
      playCloudTTS: (m) => { spy.playCloudTTS.push(m); return Promise.resolve({ played: true }); },
      isSpeaking: () => (o.isSpeaking === undefined ? speaking : o.isSpeaking()),
    },
  };
  globalThis.location = { hostname: "demo.invalid.test", protocol: "https:", origin: "https://demo.invalid.test" };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  // A no-op AbortSignal, so `AbortSignal.timeout` cannot create a REAL timer that outlives
  // the virtual clock (and keeps node alive).
  globalThis.AbortSignal = { timeout: () => ({ aborted: false, addEventListener() {} }) };
  // No broker unless a test asks for one: the whole point is the no-MQTT case, which is
  // where `speakLocally` speaks immediately.
  globalThis.mqtt = { connect: () => ({ connected: false, on() {}, subscribe() {}, end() {}, publish() {} }) };

  globalThis.fetch = (url, init) => {
    const path = String(url).replace("https://demo.invalid.test", "");
    let body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch {}
    spy.fetches.push([path, body]);
    const answer = o.answer || (() => ({ status: 200, json: {} }));
    const res = answer(path, body, spy);
    const settle = (r) => new Response(typeof r.text === "string" ? r.text : JSON.stringify(r.json || {}),
                                       { status: r.status || 200, headers: { "Content-Type": "application/json" } });
    if (res && res.reject) return Promise.reject(new Error("network"));
    if (res && res.delayMs) {
      return new Promise((resolve) => globalThis.setTimeout(() => resolve(settle(res)), res.delayMs));
    }
    return Promise.resolve(settle(res));
  };

  return { spy, clickHandlers, keyHandlers, els, panel };
}

/** The envelope shape `mode.js` and `cloud-transport.js` both read (§3.2). */
function envelope(over) {
  return Object.assign({
    ok: true, degraded: false, reason: null, retry_after_s: 0, message: "",
    mode: "live", load: { level: "ok", inflight: 0, capacity: 4 },
    limits: { max_input_chars: 500, max_tts_chars: 300, max_tokens: 160, chat_per_min: 5 },
    messages: [], speech: [], context: "", voice: true, ears: false,
  }, over || {});
}

const chatWire = (text, eventId) => JSON.stringify({
  command: "remote_chat", result: "SUCCESS", backend: "router", event_id: eventId,
  output: {
    text,
    markup: '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>' +
            '<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_Talk+,+behaviour+:++}"/>' + text,
  },
  end_turn: false,
});

const ttsWire = (eventId) => JSON.stringify({
  request_source: "ROBOT_TTS_REQUEST",
  audio: { buffer: "AAABAAIAAwA=", channels: 1, sample_rate: 22050 },
  marks: [], event_id: eventId, chunk_num: 0,
});

const chatMsg = (text, eid) => ({ topic: "/devices/d_sim/commands/remote_chat", payload: chatWire(text, eid) });
const ttsMsg = (eid) => ({ topic: "/devices/d_sim/commands/tts", payload: ttsWire(eid) });

/** Boot the page: stub.js, bridge.js, mode.js, cloud-transport.js — sim.html's order. */
async function boot(opts) {
  installClock();
  const world = makeWorld(opts);
  (0, eval)(SRC.stub);
  (0, eval)(SRC.bridge);
  (0, eval)(SRC.mode);
  (0, eval)(SRC.transport);
  await advance(1);          // let mode.js's first /api/health poll settle
  return world;
}

/**
 * Take one turn and let the virtual clock run.
 *
 * NEVER `await sendUserTurn(...)` before advancing: the turn's promise can only settle
 * once virtual timers fire (the 2500 ms speech wait, the 450 ms fallback beat), and
 * `advance()` is the only thing that fires them. Awaiting first deadlocks the test — which
 * is a property of the injected clock, not of the code under test.
 */
async function say(text, ms) {
  const p = globalThis.window.moxieBridge.sendUserTurn(text);
  await advance(ms === undefined ? 10000 : ms);
  await p;
}

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
  // The seven members §3.5 names, and every other one bridge.js publishes.
  for (const m of ["route", "sendUserTurn", "isLive", "faceEvent", "presenceStats", "telehealthStats", "hasCloudVoice"]) {
    eq(typeof outer[m], "function", `the wrapped surface still exposes ${m} (§3.5's seven)`);
  }
  for (const m of innerMembers) {
    ok(m in outer, `every member bridge.js published survives the wrap: ${m}`);
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
  eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · LIVE",
     "…and the badge finally reads LIVE, which is exactly what P0-a withheld");
  eq(globalThis.window.moxieMode.canSpendLiveTurn(), true, "…and a live turn is spendable");

  // `sim.html` loads them in the order this file evals them.
  const html = readFileSync(join(repo, "sim", "web", "sim.html"), "utf8");
  ok(html.indexOf("bridge.js") < html.indexOf("mode.js"), "sim.html loads bridge.js before mode.js");
  ok(html.indexOf("mode.js") < html.indexOf("cloud-transport.js"),
     "sim.html loads cloud-transport.js AFTER mode.js (it wraps what bridge.js published)");

  // With bridge.js absent the transport must do nothing rather than half-wire a page.
  installClock();
  makeWorld({});
  delete globalThis.window.moxieBridge;
  (0, eval)(SRC.transport);
  eq(globalThis.window.moxieCloudTransport, undefined,
     "with no bridge.js, the transport installs nothing and does NOT claim to be live");
}

/* =========================================================================== *
 * 2. A whole live turn: what reached the network, and what reached the avatar
 * =========================================================================== */
{
  const EID = "sim-aaaabbbbcccc";
  const world = await boot({
    answer: (path, body) => {
      if (path === "/api/health") return { status: 200, json: envelope() };
      if (path === "/api/chat") {
        return { status: 200, json: envelope({
          messages: [chatMsg("Hi there! Want to hear a joke?", EID)],
          speech: [{ ticket: "v1.PAYLOAD.MAC", event_id: EID, chunk_num: 0 }],
          context: "v1.CTX.MAC",
        }) };
      }
      if (path === "/api/speech") return { status: 200, json: envelope({ messages: [ttsMsg(EID)] }) };
      return { status: 404, text: "" };
    },
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

/* =========================================================================== *
 * 3. §3.4 — a SLOW /api/speech: the words still land on time, and the late voice
 *    is dropped rather than layered
 * =========================================================================== */
{
  const EID = "sim-slowspeech1";
  const world = await boot({
    answer: (path) => {
      if (path === "/api/health") return { status: 200, json: envelope() };
      if (path === "/api/chat") {
        return { status: 200, json: envelope({
          messages: [chatMsg("A slow answer.", EID)],
          speech: [{ ticket: "v1.T.M", event_id: EID, chunk_num: 0 }],
        }) };
      }
      // 4000 ms — beyond the transport's 2500 ms SPEECH_WAIT_MS.
      if (path === "/api/speech") return { status: 200, delayMs: 4000, json: envelope({ messages: [ttsMsg(EID)] }) };
      return { status: 404, text: "" };
    },
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
    answer: (path) => {
      if (path === "/api/health") return { status: 200, json: envelope() };
      if (path === "/api/chat") {
        return { status: 200, json: envelope({
          messages: [chatMsg("Late but welcome.", EID)],
          speech: [{ ticket: "v1.T.M", event_id: EID, chunk_num: 0 }],
        }) };
      }
      if (path === "/api/speech") return { status: 200, delayMs: 4000, json: envelope({ messages: [ttsMsg(EID)] }) };
      return { status: 404, text: "" };
    },
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

/* =========================================================================== *
 * 5. §6.3 — every degraded path answers, and none of them goes quiet
 * =========================================================================== */
{
  // (a) `gateway_not_configured`: the mode is degraded, the transport delegates, and
  // `bridge.js` + `stub.js` answer exactly as they do on today's site.
  {
    const world = await boot({
      answer: (path) => (path === "/api/health"
        ? { status: 200, json: envelope({ mode: "degraded", reason: "gateway_not_configured", ok: true, degraded: true, voice: false }) }
        : { status: 503, json: envelope({ mode: "degraded", reason: "gateway_not_configured" }) }),
    });
    eq(globalThis.window.moxieMode.state(), "degraded", "an unconfigured deployment reads as degraded");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO", "…with today's badge, unchanged");
    await say("hi moxie", 1000);
    const posts = world.spy.fetches.filter(([p]) => p !== "/api/health");
    deep(posts, [], "NO /api/chat request is made at all when the mode is not live");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.delegated, 1, "the turn was delegated to bridge.js");
    eq(st.live, 0, "…and no live turn was attempted");
    deep(world.spy.transcript, ["hi moxie", "Hi there! It's so good to see you."],
         "…and stub.js answered, through the real bridge");
    eq(world.spy.speak.length, 1, "…spoken by the local voice, as today");
  }

  // (b) `/api/health` absent (404): `offline` — byte-identical to today's page.
  {
    const world = await boot({ answer: () => ({ status: 404, text: "not found" }) });
    eq(globalThis.window.moxieMode.state(), "offline", "a 404 health probe reads as offline");
    await say("hi moxie", 1000);
    deep(world.spy.fetches.filter(([p]) => p !== "/api/health"), [], "offline makes no /api/* request");
    ok(world.spy.transcript.length === 2, "…and the stub still answers");
  }

  // (c) A 429 mid-conversation: the mode STAYS live (a rate-limited visitor is not a
  // broken deployment), this turn is answered from the stub, and the page is not quiet.
  {
    let refuse = false;
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope() };
        if (path === "/api/chat" && refuse) {
          return { status: 429, json: envelope({
            ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "degraded" }) };
        }
        if (path === "/api/chat") {
          return { status: 200, json: envelope({ messages: [chatMsg("Sure!", "sim-e1")] }) };
        }
        return { status: 404, text: "" };
      },
    });
    await say("first", 10);
    refuse = true;
    await say("tell me a joke", 1000);

    eq(globalThis.window.moxieMode.state(), "live", "a 429 does NOT leave the live state (§6.3 soft degrade)");
    eq(globalThis.window.moxieMode.reason(), "rate_limited", "…the reason is recorded");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · LIVE", "…the badge stays LIVE");
    eq(globalThis.window.moxieMode.message(), "One at a time! Give Moxie a few seconds.",
       "…and §7's transient chip copy is shown");
    ok(globalThis.window.moxieMode.retryAfterS() > 0, "…with a Retry-After window open");
    eq(globalThis.window.moxieMode.canSpendLiveTurn(), false, "…so live turns are suppressed");
    ok(world.spy.transcript.includes("Why did the robot cross the road? To recharge on the other side!"),
       "THE REFUSED TURN IS STILL ANSWERED, from stub.js — the page never goes silent (A5)");
    eq(globalThis.window.moxieBridge.transportStats().fallbacks, 1, "…recorded as one fallback");

    // And while the window is open, the next turn is delegated without a request.
    const before = world.spy.fetches.length;
    await say("and another", 1000);
    eq(world.spy.fetches.length, before, "no /api/chat is spent while the Retry-After window is open");
  }

  // (d) `upstream_down`: a full degrade, still answered.
  {
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope() };
        return { status: 503, json: envelope({
          ok: false, degraded: true, reason: "upstream_down", retry_after_s: 60, mode: "degraded" }) };
      },
    });
    await say("hi moxie", 1000);
    eq(globalThis.window.moxieMode.state(), "degraded", "upstream_down degrades the mode");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED", "…with §7's SCRIPTED badge");
    eq(globalThis.window.moxieMode.message(), "Moxie’s brain is unreachable right now — she’s running on what she remembers.",
       "…and §7's copy");
    ok(world.spy.transcript.length === 2, "…and the turn is still answered from the stub");
  }

  // (d2) `gateway_unreachable_or_gated` — a Cloudflare Access login page in front of the
  // tunnel. The VISITOR sees exactly what `upstream_down` shows (the brain is unreachable,
  // she runs on what she remembers); only an operator reading the reason learns the door
  // is locked rather than the room empty. Crucially, `mode.js` must RECOGNISE the reason:
  // an unknown one is coerced to null and would be read as a healthy turn.
  {
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope() };
        return { status: 503, json: envelope({
          ok: false, degraded: true, reason: "gateway_unreachable_or_gated",
          retry_after_s: 60, mode: "degraded" }) };
      },
    });
    await say("hi moxie", 1000);
    eq(globalThis.window.moxieMode.state(), "degraded",
       "a gated gateway degrades the mode — NOT read as a healthy turn");
    eq(globalThis.window.moxieMode.reason(), "gateway_unreachable_or_gated",
       "…and the reason survives mode.js's closed-set filter");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED", "…with the SCRIPTED badge");
    eq(globalThis.window.moxieMode.message(),
       "Moxie’s brain is unreachable right now — she’s running on what she remembers.",
       "…and exactly upstream_down's copy: a visitor learns nothing about our plumbing");
    ok(world.spy.transcript.length === 2, "…and the turn is still answered from the stub");
    eq(globalThis.window.moxieBridge.transportStats().fallbacks, 1, "…recorded as one fallback");
  }

  // (e) A transport error with no envelope at all — the browser could not even reach the
  // route. Three of those degrade the mode (§6.3), and every one still answers.
  {
    const world = await boot({
      answer: (path) => (path === "/api/health" ? { status: 200, json: envelope() } : { reject: true }),
    });
    for (const t of ["one", "two", "three"]) {
      await say(t, 1000);
    }
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.chatErrors, 3, "three transport errors recorded");
    eq(st.fallbacks, 3, "…and three stub answers, so the page answered every time");
    eq(globalThis.window.moxieMode.state(), "degraded", "…and the 3-strike rule degraded the mode (§6.3)");
  }

  // (f) A safety BLOCK: `ok: true`, `reason: "blocked"`, and the route's own redirect line
  // is spoken rather than a stub line about the weather.
  {
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope() };
        if (path === "/api/chat") {
          return { status: 200, json: envelope({
            ok: true, degraded: true, reason: "blocked", mode: "live",
            messages: [chatMsg("Thank you for telling me. Feelings this big need a grown-up.", "sim-blocked1")],
            speech: [],
          }) };
        }
        return { status: 404, text: "" };
      },
    });
    await say("something the floor blocks", 1000);
    eq(globalThis.window.moxieMode.state(), "live", "a block does NOT change the mode (§4.5)");
    ok(world.spy.transcript.includes("Thank you for telling me. Feelings this big need a grown-up."),
       "the redirect line is what Moxie says");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.blocked, 1, "…recorded as a block");
    eq(st.fallbacks, 0, "…and the stub was NOT used, because a kind line was supplied");
    eq(world.spy.playCloudTTS.length, 0, "…and no gateway voice was requested (a block spends nothing)");
    deep(world.spy.fetches.filter(([p]) => p === "/api/speech"), [], "…no /api/speech call at all");
  }

  // (g) No voice configured: the words render and speak locally, with no speech request.
  {
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope({ voice: false }) };
        if (path === "/api/chat") {
          return { status: 200, json: envelope({ voice: false, messages: [chatMsg("No voice here.", "sim-novoice1")], speech: [] }) };
        }
        return { status: 404, text: "" };
      },
    });
    await say("hi", 1000);
    deep(world.spy.fetches.filter(([p]) => p === "/api/speech"), [], "no ticket => no /api/speech request");
    deep(world.spy.setSpeech, ["No voice here."], "…the words still render");
    eq(world.spy.speak.length, 1, "…and speak from the clips, which is today's behaviour");
  }

  // (h) A connected MQTT broker ALWAYS wins, even when the hosted mode is live.
  {
    const world = await boot({
      answer: (path) => (path === "/api/health" ? { status: 200, json: envelope() } : { status: 404, text: "" }),
    });
    const published = [];
    globalThis.mqtt.connect = () => ({
      connected: true, on() {}, subscribe() {}, end() {},
      publish: (t, p) => published.push([t, p]),
    });
    world.clickHandlers["bus-connect"] && world.clickHandlers["bus-connect"]();
    await say("over the bus please", 1000);
    deep(world.spy.fetches.filter(([p]) => p !== "/api/health"), [],
         "with a broker connected, NO /api/chat request is made — the supervisor gets the turn");
    ok(published.some(([t]) => t.endsWith("/events/remote-chat")), "…and the turn went onto the bus");
    eq(globalThis.window.moxieBridge.transportStats().delegated, 1, "…delegated to bridge.js");
  }
}

/* =========================================================================== *
 * 6. The injected "Talk" box — the control the definition of done needs
 * =========================================================================== */
{
  const EID = "sim-typedturn1";
  const world = await boot({
    answer: (path) => {
      if (path === "/api/health") return { status: 200, json: envelope() };
      if (path === "/api/chat") {
        return { status: 200, json: envelope({ messages: [chatMsg("Typed and answered.", EID)], speech: [] }) };
      }
      return { status: 404, text: "" };
    },
  });
  const input = globalThis.document.getElementById("chat-input");
  const send = globalThis.document.getElementById("chat-send");
  ok(input && send, "the transport injected #chat-input and #chat-send");
  eq(input.getAttribute("maxlength"), "500", "the input mirrors DEMO_MAX_INPUT_CHARS");
  eq(globalThis.document.getElementById("chat-status").getAttribute("aria-live"), "polite",
     "the status line is announced politely, per §7");
  ok(world.panel.children.length > 0, "…into the Comms panel, before the Mic section");

  input.value = "  a typed sentence  ";
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(input.value, "", "sending clears the box");
  const posts = world.spy.fetches.filter(([p]) => p === "/api/chat");
  eq(posts.length, 1, "clicking Send spends exactly one turn");
  eq(posts[0][1].text, "a typed sentence", "…with the sentence TRIMMED");
  ok(world.spy.transcript.includes("Typed and answered."), "…and Moxie answered it");

  // Enter sends too; an empty box does not.
  input.value = "enter works";
  world.keyHandlers["chat-input"]({ key: "Enter" });
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2, "Enter sends");
  input.value = "   ";
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2, "a blank box sends nothing");

  // The client-side length check explains itself instead of spending a request.
  input.value = "x".repeat(501);
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2,
     "an over-length sentence is explained locally, not spent");
  ok(globalThis.document.getElementById("chat-status").textContent.includes("500"),
     "…and the page says what the limit is");

  // And `sendUserTurn` itself ignores an empty turn, whoever calls it (mic.js does).
  const before = world.spy.fetches.length;
  await say("", 10);
  await say(null, 10);
  eq(world.spy.fetches.length, before, "sendUserTurn('') spends nothing");
}

/* =========================================================================== *
 * 6b. THE CONSOLATION LINE IS FREE — `sendScriptedTurn`
 * =========================================================================== *
 * `mic.js` consoles a visitor whose ears failed with a scripted child line. Nobody said
 * those words, so they may not buy a chat + speech turn. This block drives the seam
 * directly on a virtual clock; `sim/test_mic_spend.mjs` drives the same thing through a
 * real microphone press in a real browser and counts the requests that actually left.
 * =========================================================================== */
{
  const answer = (path) => {
    if (path === "/api/health") return { status: 200, json: envelope() };
    if (path === "/api/chat")
      return { status: 200, json: envelope({ messages: [chatMsg("A paid answer.", "sim-paid1")], speech: [] }) };
    return { status: 404, text: "" };
  };

  // (a) A LIVE page: the line is shown, spoken and ANSWERED — for nothing.
  {
    const world = await boot({ answer });
    eq(globalThis.window.moxieMode.canSpendLiveTurn(), true, "the page really is live and spendable");
    eq(typeof globalThis.window.moxieBridge.sendScriptedTurn, "function",
       "the transport exposes sendScriptedTurn for the degraded path");

    const before = world.spy.fetches.length;
    const p = globalThis.window.moxieBridge.sendScriptedTurn("Guess what, it's my birthday today!");
    await advance(1000);
    await p;

    deep(world.spy.fetches.slice(before), [],
         "A SCRIPTED CONSOLATION LINE MAKES NO REQUEST AT ALL on a live page — not /api/chat, not /api/speech");
    ok(world.spy.transcript.includes("Guess what, it's my birthday today!"),
       "…the child's line is still on the page, so the visitor is still consoled");
    ok(world.spy.transcript.includes("Happy birthday! I hope your day is amazing."),
       "…and Moxie still ANSWERS it, from stub.js, after the same 450 ms beat");
    ok(world.spy.sfx.includes("listen"), "…with the same listen SFX a child's turn always fires");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.scripted, 1, "…recorded as one scripted line");
    eq(st.scriptedFree, 1, "…answered for free");
    eq(st.turns, 0, "…and NOT counted as a turn: nobody took one");
    eq(st.live, 0, "…no live turn was opened");
  }

  // (b) A real transcript on the same page still spends exactly one of each. The fix must
  //     not have quietly turned the microphone off.
  {
    const world = await boot({ answer });
    await say("what the visitor actually said", 1000);
    const paid = world.spy.fetches.filter(([pth]) => pth !== "/api/health").map(([pth]) => pth);
    deep(paid, ["/api/chat"], "a REAL transcript still spends its /api/chat, exactly as before");
    eq(globalThis.window.moxieBridge.transportStats().live, 1, "…as a live turn");
    eq(globalThis.window.moxieBridge.transportStats().scripted, 0, "…and not as a scripted one");
  }

  // (c) A page with nothing spendable takes the path it takes today: inner.sendUserTurn,
  //     i.e. stub.js, which echoes AND answers. Byte-for-byte the old behaviour.
  {
    const world = await boot({ answer: () => ({ status: 200, json: envelope({ ok: false, reason: "gateway_not_configured", mode: "degraded" }) }) });
    eq(globalThis.window.moxieMode.canSpendLiveTurn(), false, "an unconfigured deployment spends nothing");
    const before = world.spy.fetches.length;
    globalThis.window.moxieBridge.sendScriptedTurn("Thank you Moxie!");
    await advance(1000);
    deep(world.spy.fetches.slice(before), [], "…so the scripted line makes no request either");
    ok(world.spy.transcript.includes("Thank you Moxie!"), "…the line is still shown");
    ok(world.spy.transcript.includes("You're so welcome. I love celebrating with you!"),
       "…and stub.js still answers it, unchanged");
    eq(globalThis.window.moxieBridge.transportStats().scriptedFree, 0,
       "…through inner.sendUserTurn, not the local assembly — nothing here needed changing");
  }

  // (d) A connected broker is a self-hoster's OWN backend: it still gets the line, exactly
  //     as it does today. This slice is about the shared demo budget, not about them.
  {
    const world = await boot({ answer });
    const published = [];
    globalThis.mqtt.connect = () => ({
      connected: true, on() {}, subscribe() {}, end() {},
      publish: (t, pl) => published.push([t, pl]),
    });
    world.clickHandlers["bus-connect"] && world.clickHandlers["bus-connect"]();
    const before = world.spy.fetches.length;
    globalThis.window.moxieBridge.sendScriptedTurn("Thank you Moxie!");
    await advance(1000);
    ok(published.some(([t]) => t.endsWith("/events/remote-chat")),
       "with a broker connected the scripted line STILL goes onto the bus, unchanged");
    deep(world.spy.fetches.slice(before), [], "…and still costs the hosted gateway nothing");
  }

  // (e) An empty consolation is not a turn.
  {
    const world = await boot({ answer });
    const before = world.spy.fetches.length;
    await globalThis.window.moxieBridge.sendScriptedTurn("");
    await globalThis.window.moxieBridge.sendScriptedTurn(null);
    await advance(1000);
    deep(world.spy.fetches.slice(before), [], "an empty scripted line does nothing at all");
    eq(globalThis.window.moxieBridge.transportStats().scripted, 0, "…and is not recorded as one");
  }

  // (f) The source-level rule, so a future edit cannot quietly put it back: `mic.js`'s
  //     degraded path may not name `sendUserTurn`.
  {
    const mic = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");
    const fb = mic.slice(mic.indexOf("function fallback("), mic.indexOf("/* ---- capture"));
    ok(fb.length > 100, "found mic.js's fallback body");
    ok(!/sendUserTurn/.test(fb),
       "mic.js's fallback never names sendUserTurn — the consolation line cannot reach the paid path");
    ok(/publishScripted/.test(fb), "…it publishes through publishScripted instead");
  }
}

/* =========================================================================== *
 * 7. THE BOT-CONTROL SEAM — one fresh token per send, and never a dead Send
 * =========================================================================== *
 * `sim/web/turnstile.js` owns the widget; this file owns the send path; the join between
 * them is ONE line in `liveTurn`. What is proven here is only the join, in all three of
 * its states, because that is the part `sim/test_turnstile.mjs` cannot see:
 *
 *   · the module is ABSENT — every page that does not load `turnstile.js`, which is the
 *     state this whole test file has always run in. The turn must go out exactly as it
 *     did before the bot control existed: the control lives on the SERVER, and a page
 *     with no minter is a page the server is not asking one of.
 *   · the module returns a TOKEN — it must land in the body under Cloudflare's own field
 *     name, and a SECOND turn must carry a SECOND token (they are single-use).
 *   · the module returns NULL — enforcement is on and no token could be got. NO REQUEST
 *     MAY BE MADE, and the page must still say something. A silent dead Send is the exact
 *     failure this transport was written to fix (`#speech-btn` into a missing sidecar),
 *     and re-introducing it through the bot control would be the same bug wearing a hat.
 * =========================================================================== */
{
  /** A fake `window.moxieTurnstile`, installed after boot so the real module is not
   *  needed. `hand` is what `getToken()` resolves to; `calls` counts the asks AND RECORDS
   *  THE ACTION each one named — there are two actions now, one per spending route, and a
   *  send path that asked for the wrong one would be refused by the server's check 2 on
   *  every single turn. */
  function minter(hand) {
    const calls = { n: 0, actions: [] };
    globalThis.window.moxieTurnstile = {
      getToken: function (action) {
        calls.n += 1;
        calls.actions.push(action);
        return Promise.resolve(typeof hand === "function" ? hand(calls.n) : hand);
      },
    };
    return calls;
  }

  /* ---- ABSENT: byte-identical to the behaviour before the control existed --- */
  {
    const world = await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) } });
    ok(!globalThis.window.moxieTurnstile, "no turnstile.js loaded: the module really is absent");
    await say("hello");
    const chatPost = world.spy.fetches.find((f) => f[0] === "/api/chat");
    ok(!!chatPost, "the turn still reaches /api/chat with no minter present");
    deep(Object.keys(chatPost[1]).sort(), ["context", "text"],
         "…and the body is EXACTLY what it was before the bot control: no empty token field");
    eq(globalThis.window.moxieBridge.transportStats().chatOk, 1, "…and the turn succeeded");
    eq(globalThis.window.moxieBridge.transportStats().botUnavailable, 0, "…with nothing refused locally");
  }

  /* ---- A TOKEN: it lands where the route reads it, and it is FRESH each send - */
  {
    const world = await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) } });
    const calls = minter((n) => "tok-" + n);
    await say("hello");
    await say("hello again");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    eq(posts.length, 2, "two turns, two posts");
    eq(calls.n, 2, "…and the minter was asked once per turn, not once per page");
    deep(calls.actions, ["chat", "chat"],
         "…for the CHAT action every time — `mic.js` asks for `transcribe`, and the server " +
         "refuses each in the other's place");
    eq(posts[0][1]["cf-turnstile-response"], "tok-1",
       "the token rides Cloudflare's own field name, which is what the route reads");
    eq(posts[1][1]["cf-turnstile-response"], "tok-2",
       "…and the SECOND turn carries a SECOND token: they are single-use");
    eq(globalThis.window.moxieBridge.transportStats().botTokens, 2, "both sends are recorded");
    // The rest of the body is untouched — the token is additive, not a rewrite.
    deep(Object.keys(posts[0][1]).sort(), ["cf-turnstile-response", "context", "text"],
         "…and nothing else about the request changed");
  }

  /* ---- NULL: no request, and Moxie says one honest sentence ---------------- */
  {
    const world = await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) } });
    minter(null);
    await say("hello");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    eq(posts.length, 0, "a token that could not be minted makes NO /api/chat request at all");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 1, "…it is recorded as a local refusal");
    eq(st.chatOk, 0, "…nothing succeeded");
    eq(st.chatErrors, 0, "…and it is NOT reported as a transport error: nothing was sent");

    /* THE PART THAT MATTERS: the page did not go quiet. The child's line is in the
     * transcript AND so is Moxie's — through the same `route()` a real reply takes. */
    const rows = world.spy.transcript.join(" | ");
    ok(/hello/.test(rows), "the child's line is still echoed to the transcript");
    ok(/visitor check/i.test(rows),
       `…and Moxie ANSWERS with an honest sentence rather than nothing (${JSON.stringify(rows.slice(0, 160))})`);
    ok(/try/i.test((world.els["chat-status"] || {}).textContent || ""),
       "…with the status line under the box telling the visitor what to do");
    ok(world.spy.speak.length > 0 || world.spy.setSpeech.length > 0,
       "…and she says it out loud, like any other line");
  }

  /* ---- REPEATED local failures DEGRADE the page, and stop repeating one line - *
   * THE BUG THIS BLOCK EXISTS FOR, measured with `challenges.cloudflare.com` blocked (an
   * ad-blocker rule, a DNS filter, a `frame-src` refusal — all real, none of them the
   * visitor's doing): five typed messages produced five VERBATIM copies of the same
   * robotic sentence, each inviting a retry that could not work, under a badge that still
   * said LIVE. No strike was recorded, `mode.js` never left `live`, and `stub.js` — this
   * page's own answer for exactly this situation — was never asked. That is strictly worse
   * than the unreachable-gateway path, which flips the badge to SCRIPTED and answers
   * topically.
   *
   * The fix has two halves and both are asserted: every failure is a transport STRIKE (the
   * same 3-strike degrade §6.3 already uses), and from the SECOND consecutive failure the
   * turn is answered from `stub.js` instead of by repeating the line. */
  {
    const world = await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) } });
    minter(null);
    eq(globalThis.window.moxieMode.state(), "live", "the page starts live…");

    await say("one");
    let st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 1, "the FIRST failure is recorded…");
    eq(st.fallbacks, 0, "…and answers with Moxie's own honest line rather than a stub reply");
    ok(/visitor check/i.test(world.spy.transcript.join(" ")),
       "…which is the line that says what happened");

    await say("two");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 2, "the SECOND consecutive failure is recorded…");
    eq(st.fallbacks, 1, "…and is answered from stub.js — not the same sentence again");

    await say("three");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.fallbacks, 2, "…and so is the third");
    eq(st.chatErrors, 0, "…none of them is reported as a transport error: nothing was sent");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0,
       "…and NOT ONE /api/chat request was made by any of them");

    /* THE BADGE. Three strikes is `mode.js`'s existing degrade for a transport that cannot
     * be reached (§6.3), and a widget host that cannot be reached is exactly that. A LIVE
     * badge over a page that can never complete a turn is the lie this fixes. */
    eq(globalThis.window.moxieMode.state(), "degraded",
       "after three local failures the page is DEGRADED, not still claiming LIVE");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED",
       "…with the SCRIPTED badge, like every other unreachable transport");

    /* AND THE DEGRADE HAS TEETH: a degraded page STOPS SPENDING. `canSpendLiveTurn()` is
     * shut, so the fourth message never reaches this transport at all — it is delegated to
     * `bridge.js`'s own offline path, exactly as it would be with a dead gateway. That is
     * the difference between a badge that says SCRIPTED and a page that IS scripted. */
    const before = globalThis.window.moxieBridge.transportStats().botUnavailable;
    await say("four");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.delegated, 1, "a degraded page delegates the next turn instead of trying again…");
    eq(st.botUnavailable, before, "…so the minter is not even asked");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0, "…and nothing is sent");

    /* AND IT RECOVERS THROUGH THE PROBE, not by guessing. `/api/health` answering healthy
     * is what puts the page back to live (§6.3), and the CONSECUTIVE counter then means a
     * later single failure says the honest line again rather than a stub — a page that
     * failed once and has been fine since is not a degraded page, and `turnstile.js` no
     * longer memoises a failed script load, so the retry that line invites can now work. */
    minter("tok-recovered");
    await globalThis.window.moxieMode.refresh();
    eq(globalThis.window.moxieMode.state(), "live", "a healthy probe brings the page back…");
    await say("five");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.botTokens, 1, "…the next turn mints a token and is sent…");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 1, "…as one real request");

    minter(null);
    await say("six");
    eq(globalThis.window.moxieBridge.transportStats().fallbacks, 2,
       "…and the NEXT failure is a FIRST failure again: the honest line, not a stub");
    delete globalThis.window.moxieTurnstile;
  }

  /* ---- a minter that THROWS is the null case, not an exception ------------- */
  {
    const world = await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) } });
    globalThis.window.moxieTurnstile = { getToken: function () { throw new Error("boom"); } };
    await say("hello");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0,
       "a minter that throws sends nothing…");
    eq(globalThis.window.moxieBridge.transportStats().botUnavailable, 1,
       "…and is handled as the same honest refusal, not as an unhandled rejection");
    delete globalThis.window.moxieTurnstile;
  }

  /* ---- and a REFUSAL from the server still answers, as every refusal must --- */
  {
    /* `refusing` is flipped mid-block, so the SAME page sees a refusal and then a good
     * turn. It has to live on the opts object `boot()` was handed — `makeWorld` returns
     * the spy, not its options, so assigning to the returned object would set a field the
     * fetch stub never reads (and the assertion below would then be measuring the refusal
     * a second time while appearing to measure a recovery). */
    let refusing = true;
    const world = await boot({ answer: (path) => {
      if (path === "/api/health") return { status: 200, json: envelope() };
      if (refusing) {
        return { status: 403, json: envelope({ ok: false, degraded: true,
                                               reason: "turnstile_failed", mode: "degraded" }) };
      }
      return { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e2")], speech: [] }) };
    } });
    minter("tok-x");
    await say("hello");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.chatRefused, 1, "a server-side turnstile_failed is a refusal like any other…");
    ok(st.fallbacks >= 1, "…answered from stub.js for this one turn");
    // §6.3: the mode STAYS live — a stale token is not a broken deployment.
    eq(globalThis.window.moxieMode.state(), "live", "…and the page STAYS live");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · LIVE", "…with the LIVE badge intact");
    ok(/real person/i.test(globalThis.window.moxieMode.message()),
       `…and copy that tells the visitor to try again (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    ok(world.spy.transcript.join(" ").length > 0, "…and the transcript is not empty");

    /* AND THE NOTE IS CLEARED BY THE TURN THAT SUCCEEDS, not by the next 30 s poll.
     * `turnstile_failed` carries no suppression window — a fresh token is a tap away — so
     * nothing else would clear it, and the copy would sit under a working box telling the
     * visitor to try again for up to half a minute after they already had. */
    refusing = false;
    await say("hello once more");
    eq(globalThis.window.moxieMode.reason(), null,
       "a successful turn clears the bot-check note immediately");
    eq(globalThis.window.moxieMode.message(), "",
       "…so the copy under the box goes back to saying nothing");
  }

  /* ---- while turnstile_misconfigured degrades the whole page ---------------- */
  {
    await boot({ answer: (path) => path === "/api/health"
      ? { status: 200, json: envelope() }
      : { status: 503, json: envelope({ ok: false, degraded: true, reason: "turnstile_misconfigured",
                                        mode: "degraded", retry_after_s: 60 }) } });
    minter("tok-y");
    await say("hello");
    eq(globalThis.window.moxieMode.state(), "degraded",
       "a MISCONFIGURED control degrades the page — it will refuse every visitor identically");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED", "…with the SCRIPTED badge");
    ok(/isn’t set up right/i.test(globalThis.window.moxieMode.message()),
       `…and copy that names the deployment, not the visitor (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    delete globalThis.window.moxieTurnstile;
  }
}

/* =========================================================================== *
 * 8. Nothing in the client holds a secret, a key or a hostname
 * =========================================================================== */
{
  ok(!/sk-[A-Za-z0-9_-]{8}/.test(SRC.transport), "cloud-transport.js contains no key-shaped string");
  ok(!/mattvalancy|graphlings|pages\.dev/i.test(SRC.transport), "…and no deployment hostname");
  ok(!/https?:\/\//.test(SRC.transport.replace(/^\s*\*.*$/gm, "")),
     "…and no absolute URL outside its header comment — the base is location.origin");
  ok(SRC.transport.includes("window.moxieMode"), "it derives everything it can do from window.moxieMode");
  ok(SRC.transport.includes("credentials: \"omit\""), "…and sends no credentials");
  // A ticket and a context blob are carried opaquely: the transport never parses either.
  ok(!/atob|JSON\.parse\(\s*(ticket|contextBlob)/.test(SRC.transport),
     "the transport never opens a ticket or a context blob — they are opaque to the browser");
}

/* --------------------------------------------------------------------------- */
if (fails.length) {
  console.error(`✗ test_cloud_transport: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_cloud_transport: one voice, always — and every degraded path still answers (${__asserts} assertions)`);
