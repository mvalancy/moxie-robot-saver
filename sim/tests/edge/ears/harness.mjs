/* Harness for `sim/test_demo_ears.mjs`. Part A: the real `transcribe.js` behind a stubbed
 * gateway (`setPlan` picks its answer, `sent` records every request). Part B: the real
 * `sim/web/mic.js` under a stubbed window, a VIRTUAL CLOCK and a FAKE RECORDER — no
 * microphone is ever opened.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repo, api, ledger, BASE, KEY, ORIGIN, GATEWAY, leakSweep, jsonOf } from "../common.mjs";

export { BASE, KEY, ORIGIN };

export const { fails, C, ok, eq, deep } = ledger();

/* =========================================================================== *
 * PART A — functions/api/transcribe.js
 * =========================================================================== */

export const route = await api("transcribe.js");
export const health = await api("health.js");
export const limits = await api("_lib", "limits.js");
export const envlib = await api("_lib", "envelope.js");
export const envmod = await api("_lib", "env.js");
export const wavlib = await api("_lib", "wav.js");

/* The fake deployment (see `sim/tests/edge/common.mjs`). */
export const FULL = { ...GATEWAY, DEMO_STT_MODEL: "test-ears-model" };

/** Every secret-shaped string that must never appear in a response, anywhere. */
export const FORBIDDEN = [KEY, BASE, "gw.invalid.test", "test-brain-model", "test-ears-model"];

/** Every outbound request the route built. Cleared in place, never reassigned. */
export const sent = [];
let plan = {};
/** What the stubbed gateway answers next. */
export const setPlan = (p) => { plan = p; };

globalThis.fetch = async (url, opt) => {
  sent.push({ url: String(url), opt });
  if (plan.throw) {
    const e = new Error("stub");
    e.name = plan.throw;
    throw e;
  }
  if (plan.body !== undefined || plan.status) {
    return new Response(plan.body === undefined ? "" : plan.body, {
      status: plan.status || 200,
      headers: plan.headers || { "Content-Type": "application/json" },
    });
  }
  const text = plan.text === undefined ? "hi moxie, tell me a joke" : plan.text;
  return new Response(JSON.stringify({ text }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

/** A byte string that sniffs as a real container. `kind` picks the magic number, which is
 *  the whole point of the sniffer under test. A `wav` of 44 bytes or more carries a REAL
 *  16 kHz mono 16-bit header (what `mic.js::encodeWav` writes): the route refuses a RIFF/WAVE
 *  whose `fmt ` it cannot read (03_route_duration.mjs A-FMT keeps that junk shape). */
export function clip(n, kind) {
  const b = new Uint8Array(Math.max(n, 16));
  const magic = {
    webm: [0x1a, 0x45, 0xdf, 0xa3],
    ogg: [0x4f, 0x67, 0x67, 0x53],
    flac: [0x66, 0x4c, 0x61, 0x43],
    mp3: [0x49, 0x44, 0x33, 0x04],               // "ID3"
    junk: [0x7b, 0x22, 0x65, 0x72],              // `{"er` — a JSON body, not audio
  }[kind || "wav"];
  for (let i = 12; i < b.length; i++) b[i] = i & 0xff;
  if (!kind || kind === "wav") {
    b.set([0x52, 0x49, 0x46, 0x46], 0);          // "RIFF"
    b.set([0x57, 0x41, 0x56, 0x45], 8);          // "WAVE"
    if (b.length >= 44) {
      b.set(wavlib.writeWav(new Uint8Array(0), { sampleRate: 16000, channels: 1 }), 0);
      const v = new DataView(b.buffer);
      v.setUint32(4, b.length - 8, true);        // RIFF size
      v.setUint32(40, b.length - 44, true);      // data size: the rest is the "audio"
    }
  } else if (kind === "mp4") {
    b.set([0x66, 0x74, 0x79, 0x70], 4);          // "ftyp" at offset 4
  } else {
    b.set(magic, 0);
  }
  return b;
}

export function req(bytes, headers) {
  const h = Object.assign(
    {
      "Content-Type": "audio/wav",
      Origin: ORIGIN,
      "Sec-Fetch-Site": "same-origin",
      "CF-Connecting-IP": "203.0.113.9",
    },
    headers || {},
  );
  for (const k of Object.keys(h)) if (h[k] === undefined) delete h[k];
  return new Request(ORIGIN + "/api/transcribe", { method: "POST", headers: h, body: bytes });
}

export function fresh() {
  limits.__reset();
  sent.length = 0;
  plan = {};
}

export async function assertClean(res, label) {
  C.sweeps += 1;
  await leakSweep(ok, res, FORBIDDEN, label);
}

export async function call(bytes, headers, env, label) {
  const res = await route.onRequestPost({ request: req(bytes, headers), env: env || FULL });
  await assertClean(res, label || "transcribe");
  return { res, body: await jsonOf(res) };
}

export const upstreamCalls = () => limits.__state().stats.upstreamCalls;

/* =========================================================================== *
 * PART B — sim/web/mic.js, with a fake recorder and a virtual clock
 * =========================================================================== */

export const MIC_SRC = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");

/* ---- a virtual clock ------------------------------------------------------ */
let clockNow = 0, timerSeq = 0, timers = [];
const realSetImmediate = setImmediate;

export function installClock() {
  clockNow = 0; timerSeq = 0; timers = [];
  globalThis.setTimeout = (fn, ms) => {
    const id = ++timerSeq;
    timers.push({ id, at: clockNow + (Number(ms) || 0), fn });
    return id;
  };
  globalThis.clearTimeout = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  };
}
export const flush = () => new Promise((r) => realSetImmediate(r));
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
/** Start `w`, run past the 15 s hard cap, and let the upload and its reply settle. */
export async function recordToCap(w) {
  await w.mic.start();
  await advance(15001);
  await flush();
  await flush();
}
/** Timers still pending — the assertion that a cap was CLEARED, not merely not fired. */
export const pendingTimers = () => timers.length;

/* ---- a fake recorder ------------------------------------------------------ */
/** The `MediaRecorder` surface `mic.js` uses; it logs calls so a test can assert THE RECORDER
 *  WAS STOPPED. No live microphone is opened anywhere in this file. */
export function makeRecorder(o) {
  const opts = o || {};
  const log = [];
  const r = {
    log,
    state: "inactive",
    mimeType: opts.mimeType || "audio/webm;codecs=opus",
    ondataavailable: null,
    onstop: null,
    start() { log.push("start"); r.state = "recording"; },
    stop() {
      log.push("stop");
      r.state = "inactive";
      const size = opts.size === undefined ? 40000 : opts.size;
      if (r.ondataavailable) r.ondataavailable({ data: { size } });
      if (r.onstop) r.onstop();
    },
  };
  return r;
}

/* ---- the page ------------------------------------------------------------- */
export function bootMic(o) {
  const opts = o || {};
  installClock();

  const els = {};
  const mk = (id) => ({
    id, value: "", textContent: "", className: "",
    addEventListener() {}, setAttribute() {}, classList: { add() {}, remove() {}, toggle() {} },
  });
  for (const id of ["mic-status", "bus-status", "mic-btn", "stt-base"]) els[id] = mk(id);

  const bodyAttrs = {};
  globalThis.document = {
    readyState: "complete",
    getElementById: (id) => els[id] || null,
    addEventListener() {},
    body: {
      setAttribute: (k, v) => { bodyAttrs[k] = v; },
      removeAttribute: (k) => { delete bodyAttrs[k]; },
    },
  };
  globalThis.location = { protocol: "https:", hostname: "demo.invalid.test", origin: ORIGIN };
  const store = opts.storage || {};
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  /* A microphone that is NOT a microphone: no device is ever opened, and `MediaRecorder`
   * throws if anything constructs one. `defineProperty` because `navigator` is getter-only
   * on Node 21+ (`sim/tests/test_node_global_stubs.py`). */
  const gum = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: {
    mediaDevices: {
      getUserMedia: (c) => {
        gum.push(c);
        // `gumError`: what a real browser rejects with (a DOMException NAME, e.g. NotFoundError).
        if (opts.gumError) return Promise.reject(opts.gumError);
        return opts.denyMic
          ? Promise.reject(new Error("NotAllowedError"))
          : Promise.resolve({ getTracks: () => [{ stop() {} }] });
      },
    },
  } });
  globalThis.MediaRecorder = function () { throw new Error("a test must never construct a real MediaRecorder"); };
  const audioCtx = { closed: false, processors: [] };
  globalThis.AudioContext = function () {
    const ctx = {
      sampleRate: opts.sampleRate || 48000,
      createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
      createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }),
      createScriptProcessor: (size) => {
        const node = { bufferSize: size, onaudioprocess: null, connect() {}, disconnect() {} };
        audioCtx.processors.push(node);
        return node;
      },
      destination: {},
      close: () => { audioCtx.closed = true; },
    };
    return ctx;
  };
  globalThis.AbortSignal = { timeout: () => ({ aborted: false, addEventListener() {} }) };
  globalThis.Blob = class FakeBlob {
    constructor(parts, o2) {
      const list = parts || [];   // real bytes are kept whole, to assert on what is uploaded
      this.parts = list;
      this.size = list.reduce(
        (n, p) => n + (p && p.size !== undefined ? p.size : (p && p.byteLength) || 0), 0);
      this.type = (o2 && o2.type) || "";
      const bytes = list.find((x) => x && x.byteLength !== undefined);
      this.bytes = bytes || (list[0] && list[0].bytes) || null;
    }
  };

  const published = [];   // reached window.moxieBridge.sendUserTurn — THE PAID PATH
  const scripted = [];    // reached window.moxieBridge.sendScriptedTurn — the free one
  const routed = [];      // reached window.moxieBridge.route — free, and answers nothing
  const spoken = [];
  globalThis.window = {
    addEventListener() {},
    moxieBridge: Object.assign({
      sendUserTurn: (t) => published.push(t),
      route: (topic, payload) => routed.push([topic, payload]),
    }, (typeof opts.bridge === "function" ? opts.bridge({ published, scripted, routed }) : opts.bridge) || {}),
    moxieAudio: { sfx: (n) => spoken.push(n) },
    moxieStub: {
      enabled: true,
      scriptedLines: () => Promise.resolve(opts.scriptedLines || ["Look what I made!", "It's my birthday!"]),
    },
    moxieMode: opts.mode === null ? undefined : Object.assign({
      apiBase: () => ORIGIN,
      ears: () => true,
      limits: () => ({ max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 }),
      note: (n) => notes.push(n),
      noteTransportError: () => notes.push({ reason: "transport_error" }),
    }, opts.mode || {}),
  };

  // mic.js reads `window.AudioContext`, so the fake window carries it.
  globalThis.window.AudioContext = globalThis.AudioContext;

  const notes = [];
  const posts = [];
  globalThis.fetch = (url, init) => {
    posts.push({ url: String(url), init });
    const answer = opts.answer || (() => ({ status: 200, json: { transcript: "hi moxie" } }));
    const a = answer(String(url), init);
    if (a && a.reject) return Promise.reject(new Error("network"));
    return Promise.resolve(new Response(
      typeof a.text === "string" ? a.text : JSON.stringify(a.json || {}),
      { status: a.status || 200, headers: { "Content-Type": "application/json" } },
    ));
  };

  (0, eval)(MIC_SRC);
  const mic = globalThis.window.moxieMic;
  const rec = makeRecorder(opts.recorder);
  // `realCapture` leaves mic.js its OWN capture (the WAV encoder); otherwise a fake recorder.
  // `level(rms)` drives the real `setLevelListener` seam the silence auto-stop reads.
  let levelFn = null;
  if (!opts.realCapture) {
    mic.setCapture(() => Promise.resolve({
      recorder: rec,
      stream: { getTracks: () => [] },
      setLevelListener: (fn) => { levelFn = fn; },
    }));
  }
  return { mic, rec, posts, notes, published, scripted, routed, els, bodyAttrs, audioCtx, gum,
           level: (rms) => { if (levelFn) levelFn(rms); },
           statusText: () => els["mic-status"].textContent };
}

