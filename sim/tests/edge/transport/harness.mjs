/* Harness for `sim/test_cloud_transport.mjs`: the REAL `stub.js`, `bridge/`, `mode.js` and
 * `cloud-transport.js` loaded as source under a fake DOM/audio/mqtt/fetch, on a VIRTUAL CLOCK
 * so the 2500 ms speech wait and 450 ms fallback beat run deterministically. Assertions read
 * recorded state (`transportStats()`, spy logs), never live samples.
 *
 * Two audio worlds. By default `window.moxieAudio` is a SPY (what the bridge ASKED for). With
 * `realVoice: true` it is the REAL `voice/` over a fake Web Audio stack and speechSynthesis
 * that record every sound that STARTS (`spy.sounds`) and every one cut short (`spy.cuts`):
 * what a visitor would HEAR, which a spy cannot show (its broad fake `isSpeaking` once let a
 * double voice pass as one).
 */
import { BRIDGE_SRC, VOICE_SRC } from "../../../bridge_harness.mjs";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { repo, ledger } from "../common.mjs";

export { readFileSync, join, repo };
export const { fails, C, ok, eq, deep } = ledger();

export const SRC = {
  stub: readFileSync(join(repo, "sim", "web", "stub.js"), "utf8"),
  bridge: BRIDGE_SRC,
  mode: readFileSync(join(repo, "sim", "web", "mode.js"), "utf8"),
  transport: readFileSync(join(repo, "sim", "web", "cloud-transport.js"), "utf8"),
  voice: VOICE_SRC,
};

/** The shipped clip manifest: `{moxie|child|ambient: {text: file}}`. */
export const MANIFEST = JSON.parse(readFileSync(join(repo, "sim", "web", "audio", "index.json"), "utf8"));

/* --------------------------------------------------------------------------- *
 * A virtual clock
 * --------------------------------------------------------------------------- */
let clockNow = 0;
let timerSeq = 0;
let timers = [];
const realSetImmediate = setImmediate;
const realDateNow = Date.now;
const T0 = 1_800_000_000_000;

/** The virtual time, in ms since `installClock()`. */
export const now = () => clockNow;

/** `virtualDate`: `Date.now()` follows the virtual clock too (voice/'s grace beats, mode.js's
 *  Retry-After windows). Off by default, so the older sections keep their meaning. */
export function installClock(opts) {
  clockNow = 0;
  timerSeq = 0;
  timers = [];
  Date.now = opts && opts.virtualDate ? () => T0 + clockNow : realDateNow;
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
    said: [],             // …the same calls as {t, text, who} ("ambient" = the thinking filler)
    playCloudTTS: [],     // the GATEWAY voice
    sfx: [],
    setSpeech: [],
    setFace: [],
    transcript: [],       // [role, text]
    fetches: [],          // [path, bodyObject, init]
    modeNotes: [],
    // realVoice only: what reached the speakers, recorded where the sound is MADE
    sounds: [],           // {t, kind: "cloud" | "clip" | "browser", id?, bytes?, text?, running?}
    cuts: [],             // {t, id?, text?} — a voice stopped before it ran out
    events: [],           // window events dispatched (e.g. "moxie-audio-unlocked")
    contexts: [],         // every AudioContext the page created
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

  /* Window events: a registry, so a section can fire the gestures voice/ listens for. */
  const listeners = {};
  globalThis.window = {
    addEventListener(ev, fn, opt) { (listeners[ev] ||= []).push({ fn, once: !!(opt && opt.once) }); },
    removeEventListener(ev, fn) { listeners[ev] = (listeners[ev] || []).filter((l) => l.fn !== fn); },
    dispatchEvent(e) { spy.events.push(e.type); fireListeners(e.type, e); return true; },
    moxie: {
      setFace: (f) => spy.setFace.push(f),
      setSpeech: (t) => spy.setSpeech.push(t),
      setMotor() {}, getMotor: () => 16384,
      showIcons() {}, clearIcons() {}, setHeartLED() {},
    },
    /* The SPY voice. Its predicates are as narrow as the real ones: `isSpeaking` is the
     * SERVER voice only (a local speak() never makes it true), `isMoxieBusy` is any voice. */
    moxieAudio: {
      speak: (t, who) => { spy.speak.push(t); spy.said.push({ t: clockNow, text: t, who: who || "moxie" }); speaking = true; },
      stop() { speaking = false; },
      sfx: (n) => spy.sfx.push(n),
      playCloudTTS: (m) => { spy.playCloudTTS.push(m); return Promise.resolve({ played: true }); },
      isSpeaking: () => (o.isSpeaking === undefined ? false : o.isSpeaking()),
      isMoxieSpeaking: () => speaking,
      isMoxieBusy: () => speaking,
      ttsPending: () => 0,
    },
  };
  function fireListeners(type, e) {
    for (const l of (listeners[type] || []).slice()) {
      if (l.once) listeners[type] = listeners[type].filter((x) => x !== l);
      try { l.fn(e); } catch (err) { fails.push(`a ${type} listener threw: ${err.message}`); }
    }
  }
  const audio = o.realVoice ? realVoiceFakes(spy, o) : null;
  /** Fire a window gesture. `activation`: one of the events a browser lets START audio
   *  (touchend, click, keydown); pointerdown/touchstart from a finger are not. While
   *  `refuseGestures` lasts, an activation is not honoured (it starts nothing). */
  const fire = (type, activation) => {
    if (audio) {
      let honoured = !!activation;
      if (honoured && audio.gate.refuse > 0) { audio.gate.refuse--; honoured = false; }
      audio.gate.activation = honoured;
    }
    try { fireListeners(type, { type }); } finally { if (audio) audio.gate.activation = false; }
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
    // The real voice/ reads the shipped clips (not recorded as requests: they cost nothing).
    if (audio && /^\/?audio\//.test(path)) return audio.serveClip(path.replace(/^\//, ""));
    let body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch {}
    spy.fetches.push([path, body, init || {}]);
    const answer = o.answer || (() => ({ status: 200, json: {} }));
    const res = answer(path, body, spy);
    const settle = (r) => new Response(typeof r.text === "string" ? r.text : JSON.stringify(r.json || {}),
                                       { status: r.status || 200, headers: { "Content-Type": "application/json" } });
    if (res && res.reject) return Promise.reject(new Error("network"));
    if (res && res.delayMs) {
      return new Promise((resolve, reject) => globalThis.setTimeout(
        () => (res.rejectLate ? reject(new Error("network")) : resolve(settle(res))), res.delayMs));
    }
    return Promise.resolve(settle(res));
  };

  return { spy, clickHandlers, keyHandlers, els, panel, fire, audio };
}

/* --------------------------------------------------------------------------- *
 * The fakes under the REAL voice/ (`realVoice: true`)
 * --------------------------------------------------------------------------- *
 * Web Audio: a buffer built by hand from gateway PCM is her CLOUD voice; one decoded from a
 * fetched file is a CLIP (its byte length names the file). speechSynthesis: the BROWSER voice
 * (~70 ms a character). Every start lands in `spy.sounds`, every early stop in `spy.cuts`.
 * `autoplay: "policy"` applies the browser rule: a context created or resumed outside an
 * activation gesture stays suspended, and its resume() stays PENDING until a later allowed
 * one (Web Audio spec); `refuseGestures: n` makes the first n activations count for nothing. */
function realVoiceFakes(spy, o) {
  const policy = o.autoplay === "policy";
  const gate = { activation: false, refuse: o.refuseGestures || 0 };
  let ids = 0;

  class FakeAudioContext {
    constructor() {
      this.state = !policy || gate.activation ? "running" : "suspended";
      this.destination = {};
      this.pending = [];
      spy.contexts.push(this);
    }
    get currentTime() { return clockNow / 1000; }
    resume() {
      if (this.state === "running") return Promise.resolve();
      if (policy && !gate.activation) return new Promise((r) => this.pending.push(r));
      this.state = "running";
      this.pending.splice(0).forEach((r) => r());
      return Promise.resolve();
    }
    createBuffer(ch, frames, rate) {
      return { numberOfChannels: ch, length: frames, sampleRate: rate, duration: frames / rate, kind: "cloud",
               copyToChannel() {}, getChannelData: () => new Float32Array(frames) };
    }
    decodeAudioData(buf) {
      const bytes = buf.byteLength;
      return Promise.resolve({ duration: (bytes * 8) / 64000, kind: "clip", bytes });   // ~64 kbit/s mp3
    }
    createAnalyser() {
      return { fftSize: 256, frequencyBinCount: 8, connect() {}, getByteTimeDomainData(a) { a.fill(150); } };
    }
    createOscillator() { return { type: "", frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
    createBufferSource() {
      const ctx = this;
      const src = { buffer: null, onended: null, connect() {} };
      let ended = false, endTimer = 0;
      src.start = () => {
        src.id = ++ids;
        const b = src.buffer || {};
        spy.sounds.push({ t: clockNow, kind: b.kind || "?", id: src.id, bytes: b.bytes || null,
                          dur: Math.round((b.duration || 0) * 1000), running: ctx.state === "running" });
        endTimer = globalThis.setTimeout(() => {
          if (!ended) { ended = true; if (src.onended) src.onended(); }
        }, (b.duration || 0) * 1000);
      };
      src.stop = () => {
        if (ended) return;
        ended = true;
        globalThis.clearTimeout(endTimer);
        spy.cuts.push({ t: clockNow, id: src.id });
        globalThis.setTimeout(() => { if (src.onended) src.onended(); }, 0);
      };
      src.pause = src.stop;
      return src;
    }
  }

  let current = null;
  const synth = {
    speaking: false, pending: false, paused: false,
    getVoices: () => [],
    speak(u) {
      spy.sounds.push({ t: clockNow, kind: "browser", text: u.text });
      current = u;
      synth.speaking = true;
      globalThis.setTimeout(() => { if (u.onstart) u.onstart(); }, 0);
      u.endTimer = globalThis.setTimeout(() => {
        if (current === u) { current = null; synth.speaking = false; }
        if (u.onend) u.onend();
      }, 70 * u.text.length);
    },
    cancel() {
      if (!current) return;
      const u = current;
      current = null;
      synth.speaking = false;
      globalThis.clearTimeout(u.endTimer);
      spy.cuts.push({ t: clockNow, text: u.text });
      if (u.onerror) u.onerror({ error: "interrupted" });
    },
  };

  const w = globalThis.window;
  w.AudioContext = FakeAudioContext;
  w.speechSynthesis = synth;
  globalThis.SpeechSynthesisUtterance = function (text) { this.text = text; };
  globalThis.requestAnimationFrame = (fn) => globalThis.setTimeout(() => fn(clockNow), 50);
  globalThis.cancelAnimationFrame = (id) => globalThis.clearTimeout(id);

  /** `audio/…` from sim/web — a plain promise chain, so the virtual clock's flush sees it settle. */
  function serveClip(rel) {
    const file = join(repo, "sim", "web", rel);
    if (!existsSync(file)) return Promise.resolve({ ok: false, status: 404 });
    const bytes = readFileSync(file);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(JSON.parse(bytes.toString("utf8"))),
      arrayBuffer: () => Promise.resolve(ab),
    });
  }

  return { gate, synth, serveClip };
}

/** The byte length of the shipped clip for `text` in `group`, or null — how a test names
 *  which clip a recorded `spy.sounds` entry was. */
export function clipBytes(text, group = "moxie") {
  const rel = (MANIFEST[group] || {})[text];
  if (!rel) return null;
  return readFileSync(join(repo, "sim", "web", "audio", rel)).length;
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

/** A CloudTTSResponse for `eventId`: four samples, or `seconds` of (silent) 22 050 Hz PCM
 *  when a section needs her voice to last as long as a real answer. */
export const ttsWire = (eventId, seconds) => JSON.stringify({
  request_source: "ROBOT_TTS_REQUEST",
  audio: { buffer: seconds ? Buffer.alloc(Math.round(seconds * 22050) * 2).toString("base64") : "AAABAAIAAwA=",
           channels: 1, sample_rate: 22050 },
  marks: [], event_id: eventId, chunk_num: 0,
});

export const chatMsg = (text, eid) => ({ topic: "/devices/d_sim/commands/remote_chat", payload: chatWire(text, eid) });
export const ttsMsg = (eid, seconds) => ({ topic: "/devices/d_sim/commands/tts", payload: ttsWire(eid, seconds) });

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

/** The `/api/speech` reply carrying `eid`'s audio; `over` adds e.g. `delayMs`, and
 *  `seconds` sets how long the voice lasts. */
export const voiced = (eid, over) => {
  const { seconds, ...rest } = over || {};
  return Object.assign({ status: 200, json: envelope({ messages: [ttsMsg(eid, seconds)] }) }, rest);
};

/** A 200 `/api/chat` reply with Moxie saying "Hi!" and no voice ticket. */
export const HI = Object.freeze({ status: 200, json: envelope({ messages: [chatMsg("Hi!", "e1")], speech: [] }) });

/** Boot the page: stub.js, bridge/, mode.js, cloud-transport.js — sim.html's order — and,
 *  with `realVoice`, voice/ after them (sim.html's order too) on a virtual `Date.now()`. */
export async function boot(opts) {
  const o = opts || {};
  installClock({ virtualDate: !!o.realVoice });
  const world = makeWorld(o);
  (0, eval)(SRC.stub);
  (0, eval)(SRC.bridge);
  (0, eval)(SRC.mode);
  (0, eval)(SRC.transport);
  if (o.realVoice) {
    (0, eval)(SRC.voice);
    // What the bridge and the filler ASK of the real voice, beside what it then plays.
    const A = globalThis.window.moxieAudio, speak = A.speak;
    A.speak = (t, who) => {
      world.spy.speak.push(t);
      world.spy.said.push({ t: clockNow, text: t, who: who || "moxie" });
      return speak(t, who);
    };
  }
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
