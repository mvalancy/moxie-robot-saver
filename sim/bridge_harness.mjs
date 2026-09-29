/* bridge_harness.mjs — load the REAL sim/web/bridge/*.js under bare node.
 *
 * NOT a test (sim/tests/test_ci_test_coverage.py enumerates only `test_*.mjs`). The
 * node-only bridge suites (test_bridge, test_action_payload, test_automarkup_render,
 * test_performance_render, test_preview_render) share these
 * window/document/mqtt shims instead of each carrying its own copy.
 *
 * `(0, eval)` runs the bridge parts in global scope, so every call resets the globals and
 * each load gets its own closure state.
 *
 * `scriptGroup()` is how EVERY node suite reads a split classic-script group (bridge/,
 * voice/): the parts sim.html loads, in its order, as one source — what the page runs.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const here = dirname(fileURLToPath(import.meta.url));

/** sim.html's `<script src="{group}/*.js">` parts, in load order, concatenated. */
export function scriptGroup(group) {
  const html = readFileSync(join(here, "web", "sim.html"), "utf8");
  const files = [...html.matchAll(new RegExp(`<script src="(${group}/[\\w-]+\\.js)`, "g"))].map((m) => m[1]);
  if (!files.length) throw new Error(`sim.html loads no ${group}/*.js`);
  return files.map((f) => readFileSync(join(here, "web", f), "utf8")).join("\n");
}
export const BRIDGE_SRC = scriptGroup("bridge");
export const VOICE_SRC = scriptGroup("voice");
export const readGolden = (name) => JSON.parse(readFileSync(join(here, "tests", "goldens", name), "utf8"));

/**
 * @param {object} [o]
 * @param {string} [o.src]      bridge source (default: the shipped file) — a mutated copy
 *                              is how a suite proves it can fail
 * @param {object} [o.audio]    window.moxieAudio (omitted by default, like a page without voice/)
 * @param {object} [o.moxie]    overrides merged onto the recording window.moxie spy
 * @param {boolean} [o.connect] click #bus-connect and emit "connect" (default true)
 */
export function loadBridge({ src = BRIDGE_SRC, audio, moxie = {}, connect = true } = {}) {
  const calls = { setFace: [], setSpeech: [], setMotor: [], showIcons: [], clearIcons: [], transcript: [] };
  const reset = () => { for (const k of Object.keys(calls)) calls[k] = []; };   // fresh arrays: earlier snapshots survive
  const clickHandlers = {}, els = {}, attrs = {}, published = [], subscribed = [];
  const ref = { client: null };

  const fakeEl = (id) => ({
    id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
    setAttribute: (k, v) => { attrs[id + "/" + k] = v; },
    addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
    appendChild: (child) => calls.transcript.push(child && child._text),
    querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
  });
  globalThis.window = {
    moxie: {
      setFace: (f) => calls.setFace.push(f),
      setSpeech: (t) => calls.setSpeech.push(t),
      setMotor: (i, v) => calls.setMotor.push([i, v]),
      getMotor: () => 16384,
      showIcons: (n) => calls.showIcons.push(n),
      clearIcons: () => calls.clearIcons.push(true),
      setHeartLED: () => {},
      ...moxie,
    },
    addEventListener: () => {},
  };
  if (audio) globalThis.window.moxieAudio = audio;
  globalThis.location = { hostname: "127.0.0.1" };
  globalThis.document = {
    getElementById: (id) => (els[id] ||= fakeEl(id)),
    createElement: () => {
      const el = fakeEl();
      Object.defineProperty(el, "querySelector", { value: () => ({ set textContent(v) { el._text = v; } }) });
      return el;
    },
  };
  globalThis.mqtt = {
    connect: () => {
      const h = {};
      ref.client = {
        connected: true,
        on: (e, cb) => { h[e] = cb; },
        subscribe: (t) => subscribed.push(t),
        end: () => {},
        publish: (topic, payload) => published.push({ topic, payload }),
        _emit: (e, ...a) => h[e] && h[e](...a),
      };
      return ref.client;
    },
  };

  (0, eval)(src);
  if (connect) {
    if (!clickHandlers["bus-connect"]) throw new Error("bridge did not wire the connect button");
    clickHandlers["bus-connect"]();
    if (!ref.client) throw new Error("bridge did not connect over mqtt");
    ref.client._emit("connect");
  }
  const client = ref.client;
  /** Deliver one MQTT message to the bridge (payload objects are JSON-encoded). */
  const emit = (topic, payload) => client._emit("message", topic,
    Buffer.from(typeof payload === "string" ? payload : JSON.stringify(payload)));
  return { calls, reset, clickHandlers, els, attrs, published, subscribed, client, emit,
           bridge: globalThis.window.moxieBridge };
}

/** A recording window.moxieAudio: plays nothing, remembers what was asked of it. */
export function audioSpy() {
  const voice = { speak: [], speakClipOnly: [], sfx: [], stop: 0, cloudTTS: [] };
  const audio = {
    speak: (t) => voice.speak.push(t),
    speakClipOnly: (t, who) => voice.speakClipOnly.push([t, who]),
    stop: () => { voice.stop++; },
    sfx: (n) => voice.sfx.push(n),
    playCloudTTS: (p) => { voice.cloudTTS.push(p); return Promise.resolve({ played: true }); },
  };
  return { voice, audio };
}

/** Minimal assertion collector for node suites: ok(cond, msg) + report(label). */
export function checks() {
  const fails = [];
  let n = 0;
  const ok = (cond, msg) => { n++; if (!cond) fails.push(msg); };
  const report = (okLine) => {
    if (fails.length) {
      console.error(`❌ ${fails.length} failure(s):`);
      for (const f of fails) console.error("  - " + f);
      process.exit(1);
    }
    console.log(okLine);
  };
  return { ok, fails, report, count: () => n };
}
