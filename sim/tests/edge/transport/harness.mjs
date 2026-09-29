/* Harness for `sim/test_cloud_transport.mjs`: the REAL `stub.js`, `bridge/`, `mode.js` and
 * `cloud-transport.js` loaded as source under a fake DOM/audio/mqtt/fetch, on a VIRTUAL CLOCK
 * so the 2500 ms speech wait and 450 ms fallback beat run deterministically. Assertions read
 * recorded state (`transportStats()`, spy logs), never live samples.
 */
import { BRIDGE_SRC, VOICE_SRC } from "../../../bridge_harness.mjs";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repo, ledger } from "../common.mjs";

export { readFileSync, join, repo };
export const { fails, C, ok, eq, deep } = ledger();

export const SRC = {
  stub: readFileSync(join(repo, "sim", "web", "stub.js"), "utf8"),
  bridge: BRIDGE_SRC,
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

export function installClock() {
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
export const flush = () => new Promise((r) => realSetImmediate(r));

/** Advance the virtual clock, firing due timers in order and flushing between each. */
export async function advance(ms) {
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
export function makeWorld(opts) {
  const o = opts || {};
  const spy = {
    speak: [],            // window.moxieAudio.speak — the LOCAL voice
    playCloudTTS: [],     // the GATEWAY voice
    sfx: [],
    setSpeech: [],
    setFace: [],
    transcript: [],       // [role, text]
    fetches: [],          // [path, bodyObject, init]
    modeNotes: [],
  };
  let speaking = false;

  /* `getElementById` returns NULL for an id the page lacks (`injectTalkUI` guards on
   * `#chat-send`, so an auto-vivifying fake would skip the injection); injected children are
   * registered by id as they are inserted, as a real DOM does. */
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

  // The ids the real page has, and that bridge//mode.js look for. Everything else
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
    spy.fetches.push([path, body, init || {}]);
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
export function envelope(over) {
  return Object.assign({
    ok: true, degraded: false, reason: null, retry_after_s: 0, message: "",
    mode: "live", load: { level: "ok", inflight: 0, capacity: 4 },
    limits: { max_input_chars: 500, max_tts_chars: 300, max_tokens: 160, chat_per_min: 5 },
    messages: [], speech: [], context: "", voice: true, ears: false,
  }, over || {});
}

export const chatWire = (text, eventId) => JSON.stringify({
  command: "remote_chat", result: "SUCCESS", backend: "router", event_id: eventId,
  output: {
    text,
    markup: '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>' +
            '<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_Talk+,+behaviour+:++}"/>' + text,
  },
  end_turn: false,
});

export const ttsWire = (eventId) => JSON.stringify({
  request_source: "ROBOT_TTS_REQUEST",
  audio: { buffer: "AAABAAIAAwA=", channels: 1, sample_rate: 22050 },
  marks: [], event_id: eventId, chunk_num: 0,
});

export const chatMsg = (text, eid) => ({ topic: "/devices/d_sim/commands/remote_chat", payload: chatWire(text, eid) });
export const ttsMsg = (eid) => ({ topic: "/devices/d_sim/commands/tts", payload: ttsWire(eid) });

/** An `answer` for a LIVE page: a healthy `/api/health`, and `other` for every other path
 *  (a reply, or `(path, body, spy) => reply`). */
export const live = (other) => (path, body, spy) =>
  path === "/api/health" ? { status: 200, json: envelope() }
    : typeof other === "function" ? other(path, body, spy) : other;

/** A live page with a fixed reply per path (`{ "/api/chat": reply, … }`); anything else 404s. */
export const serve = (map) => live((path) => map[path] || { status: 404, text: "" });

/** A 200 reply in which Moxie says `text` (event `eid`); `over` merges into the envelope. */
export const said = (text, eid, over) =>
  ({ status: 200, json: envelope(Object.assign({ messages: [chatMsg(text, eid)], speech: [] }, over)) });

/** One voice ticket for `eid`, as `/api/chat` mints it. */
export const ticket = (eid, t = "v1.T.M") => [{ ticket: t, event_id: eid, chunk_num: 0 }];

/** The `/api/speech` reply carrying `eid`'s audio; `over` adds e.g. `delayMs`. */
export const voiced = (eid, over) => Object.assign({ status: 200, json: envelope({ messages: [ttsMsg(eid)] }) }, over);

/** A 200 `/api/chat` reply with Moxie saying "Hi!" and no voice ticket. */
export const HI = Object.freeze({ status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) });

/** Boot the page: stub.js, bridge/, mode.js, cloud-transport.js — sim.html's order. */
export async function boot(opts) {
  installClock();
  const world = makeWorld(opts);
  (0, eval)(SRC.stub);
  (0, eval)(SRC.bridge);
  (0, eval)(SRC.mode);
  (0, eval)(SRC.transport);
  await advance(1);          // let mode.js's first /api/health poll settle
  return world;
}

/** Take one turn and let the virtual clock run. Never await `sendUserTurn` BEFORE advancing:
 *  it settles only once virtual timers fire, so awaiting first deadlocks the test. */
export async function say(text, ms) {
  const p = globalThis.window.moxieBridge.sendUserTurn(text);
  await advance(ms === undefined ? 10000 : ms);
  await p;
}
