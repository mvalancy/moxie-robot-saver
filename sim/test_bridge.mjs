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
const clickHandlers = {}, mqttClientRef = { c: null };
const els = {};                                   // id-keyed elements (real DOM is stable per id)
const fakeEl = (id) => ({
  id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
  addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
  appendChild: (child) => calls.transcript.push(child && child._text),
  querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
});
globalThis.window = { moxie, addEventListener: () => {} };
// A no-op audio stub: bridge.js only *calls* these (local TTS on connect, and
// stop() on a telehealth INTERRUPT); nothing here is asserted, so it stays silent.
globalThis.window.moxieAudio = { speak: () => {}, stop: () => {}, sfx: () => {},
                                 playCloudTTS: () => {} };
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
    mqttClientRef.c = { on: (e, cb) => { h[e] = cb; }, subscribe: () => {}, end: () => {}, _emit: (e, ...a) => h[e] && h[e](...a) };
    return mqttClientRef.c;
  },
};

// ---- load bridge.js (IIFE runs; window.moxie exists → initUI wires the clicks) ----
(0, eval)(src);
if (!clickHandlers["bus-connect"]) throw new Error("bridge did not wire the connect button");
clickHandlers["bus-connect"]();       // → connect() → mqtt.connect → mqttClientRef.c
const mqttClient = mqttClientRef.c;
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

// a second reply exercising the authoritative ePlaybackMood map (8 = Confused → thinking)
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Hmm?", markup:
    '<mark name="cmd:playback-mood,data:{+mood+:8,+intensity+:1}"/>Hmm?' } })));

// an authoritative Bht_* behaviour tree — Bht_Spin_360 drives the body-yaw motor (5)
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Wheee!", markup:
    '<mark name="cmd:behaviour-tree,data:{+behaviour+:+Bht_Spin_360+,+eventName+:+Gesture_None+}"/>Wheee!' } })));

mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "prompt", speech: "I feel happy today" })));
mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "notify", speech: "echo of Moxie" })));  // must be skipped

mqttClient._emit("message", "/devices/d_test/commands/motor",
  Buffer.from(JSON.stringify({ motors: { "0": 30000, "4": 24000 } })));  // SIL motor channel

// ---- 🎭 telehealth: the operator's line must drive the avatar exactly like a brain
// reply, because `Output.markup` IS the same behavior language (telehealth.md:16-17).
// Same markup as the "Hmm?" reply above, delivered on the puppet channel instead.
const puppetMarkup =
  '<mark name="cmd:playback-mood,data:{+mood+:2,+intensity+:2}"/>' +
  '<mark name="cmd:behaviour-tree,data:{+behaviour+:+Bht_Spin_360+,+eventName+:+Gesture_None+}"/>' +
  'I missed you.';
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "START_SESSION", session_id: "ths-1" } })));
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "PLAY_OUTPUT", session_id: "ths-1",
    output: { text: "I missed you.", markup: puppetMarkup } } })));
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "INTERRUPT", session_id: "ths-1" } })));
const th = window.moxieBridge.telehealthStats();

// ---- assertions ----
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
ok(calls.setSpeech.includes("Happy birthday!"), "setSpeech('Happy birthday!')");
ok(calls.setFace.includes("happy"), `mood 1 → setFace('happy'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setFace.includes("confused"), `mood 8 (Confused) → setFace('confused'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setMotor.some(([i]) => i === 5), `Bht_Spin_360 → body-yaw motor (5) driven; got ${JSON.stringify(calls.setMotor)}`);
ok(calls.setMotor.length > 0, "Gesture_Celebrate → setMotor(...) called");
ok(JSON.stringify(calls.showIcons).includes("Birthday"), `icons-v2 → showIcons(['Birthday']); got ${JSON.stringify(calls.showIcons)}`);
ok(calls.transcript.includes("I feel happy today"), `child turn → transcript; got ${JSON.stringify(calls.transcript)}`);
ok(!calls.transcript.includes("echo of Moxie"), "notify turn must NOT appear in transcript");
ok(calls.transcript.includes("Happy birthday!"), "Moxie reply → transcript");
ok(calls.setMotor.some(([i, v]) => i === 0 && v === 30000) && calls.setMotor.some(([i, v]) => i === 4 && v === 24000),
   `commands/motor → setMotor(0,30000)+setMotor(4,24000); got ${JSON.stringify(calls.setMotor)}`);

ok(calls.setSpeech.includes("I missed you."), "telehealth PLAY_OUTPUT → setSpeech('I missed you.')");
ok(calls.setFace.includes("sad"), `telehealth mood 2 → setFace('sad'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.transcript.includes("I missed you."), "telehealth line → transcript, like any Moxie reply");
ok(th.lines.length === 1 && th.lines[0].text === "I missed you." && !!th.lines[0].markup,
   `telehealth recorded one line with markup; got ${JSON.stringify(th.lines)}`);
ok(th.session_id === "ths-1", `telehealth session_id recorded; got ${th.session_id}`);
ok(th.interrupts === 1 && th.last_action === "INTERRUPT",
   `INTERRUPT recorded; got ${th.interrupts}/${th.last_action}`);
ok(calls.setSpeech[calls.setSpeech.length - 1] === "",
   `INTERRUPT clears the speech bubble; got ${JSON.stringify(calls.setSpeech.slice(-2))}`);

if (fails.length) {
  console.log("❌ bridge unit test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ bridge unit test OK — ${Object.values(calls).reduce((a, c) => a + c.length, 0)} avatar calls asserted (mood→face, gesture→motor, icons-v2→badges, transcript, notify-skip, 🎭 telehealth)`);
