/* Unit test for sim/web/bridge.js — loads the REAL bridge with stubbed
 * window/document/mqtt and asserts it drives window.moxie correctly from firmware
 * markup. No browser, no network. Run: node sim/test_bridge.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "web", "bridge.js"), "utf8");

// ---- spies / stubs ----
const calls = { setFace: [], setSpeech: [], setMotor: [], showIcons: [], clearIcons: [], transcript: [] };
const moxie = {
  setFace: (f) => calls.setFace.push(f),
  setSpeech: (t) => calls.setSpeech.push(t),
  setMotor: (i, v) => calls.setMotor.push([i, v]),
  getMotor: () => 16384,
  showIcons: (n) => calls.showIcons.push(n),
  clearIcons: () => calls.clearIcons.push(true),
  setHeartLED: () => {},
};
let clickHandler = null, mqttClient = null;
const fakeEl = () => ({
  value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
  addEventListener: (e, cb) => { if (e === "click") clickHandler = cb; },
  appendChild: (child) => calls.transcript.push(child && child._text),
  querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
});
globalThis.window = { moxie, addEventListener: () => {} };
globalThis.location = { hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: () => fakeEl(),
  createElement: () => {
    const el = fakeEl();
    Object.defineProperty(el, "querySelector", { value: () => ({ set textContent(v) { el._text = v; } }) });
    return el;
  },
};
globalThis.mqtt = {
  connect: () => {
    const h = {};
    mqttClient = { on: (e, cb) => { h[e] = cb; }, subscribe: () => {}, end: () => {}, _emit: (e, ...a) => h[e] && h[e](...a) };
    return mqttClient;
  },
};

// ---- load bridge.js (IIFE runs; window.moxie exists → initUI wires the click) ----
(0, eval)(src);
if (!clickHandler) throw new Error("bridge did not wire the connect button");
clickHandler();                       // → connect() → mqtt.connect → mqttClient
if (!mqttClient) throw new Error("bridge did not connect over mqtt");
mqttClient._emit("connect");

// ---- drive real firmware markup through the message handler ----
const birthdayMarkup =
  '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>' +
  '<mark name="cmd:icons-v2,data:{+command+:0,+icon0+:{+iconType+:1,+value+:+Birthday+,+background+:+Null+},+highlight+:0}"/>' +
  '<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_Celebrate+,+behaviour+:+Bht_Gesture_Celebrate+}"/>' +
  'Happy birthday!' +
  '<mark name="cmd:icons-v2,data:{+command+:2,+icon0+:{+iconType+:1,+value+:+Birthday+,+background+:+Null+},+highlight+:0}"/>';
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Happy birthday!", markup: birthdayMarkup } })));

mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "prompt", speech: "I feel happy today" })));
mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "notify", speech: "echo of Moxie" })));  // must be skipped

// ---- assertions ----
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
ok(calls.setSpeech.includes("Happy birthday!"), "setSpeech('Happy birthday!')");
ok(calls.setFace.includes("happy"), `mood 1 → setFace('happy'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setMotor.length > 0, "Gesture_Celebrate → setMotor(...) called");
ok(JSON.stringify(calls.showIcons).includes("Birthday"), `icons-v2 → showIcons(['Birthday']); got ${JSON.stringify(calls.showIcons)}`);
ok(calls.transcript.includes("I feel happy today"), `child turn → transcript; got ${JSON.stringify(calls.transcript)}`);
ok(!calls.transcript.includes("echo of Moxie"), "notify turn must NOT appear in transcript");
ok(calls.transcript.includes("Happy birthday!"), "Moxie reply → transcript");

if (fails.length) {
  console.log("❌ bridge unit test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ bridge unit test OK — ${Object.values(calls).reduce((a, c) => a + c.length, 0)} avatar calls asserted (mood→face, gesture→motor, icons-v2→badges, transcript, notify-skip)`);
