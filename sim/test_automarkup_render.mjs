/* The markup floor, seen from the only renderer we can assert against.
 *
 * No hardware has ever played our markup: everything we believe about how a robot
 * performs a `<mark cmd:…>` is inferred from the recovered generators
 * (docs/reverse-engineering/runtime/behavior-markup.md). The browser SIM is the one place
 * the inference is executable, so this drives the EIGHT byte-exact goldens from
 * sim/tests/goldens/annotate.json through the REAL sim/web/bridge.js and asserts the
 * avatar actually does something different for each of them — a face per mood, motors for
 * the arm gestures, badges for the icons.
 *
 * The goldens file is written by the Python side (sim/tests/test_automarkup.py pins it
 * byte for byte), so this is a genuine cross-language contract check: if the floor emits
 * an id the SIM does not animate, this fails rather than the robot silently doing nothing.
 *
 * No browser, no network. Run: node sim/test_automarkup_render.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "web", "bridge.js"), "utf8");
const goldens = JSON.parse(readFileSync(join(here, "tests", "goldens", "annotate.json"), "utf8"));

// ---- stubs: the same minimal window/document/mqtt shims test_bridge.mjs uses ----
let calls = { setFace: [], setSpeech: [], setMotor: [], showIcons: [], clearIcons: [] };
const reset = () => { calls = { setFace: [], setSpeech: [], setMotor: [], showIcons: [], clearIcons: [] }; };
const moxie = {
  setFace: (f) => calls.setFace.push(f),
  setSpeech: (t) => calls.setSpeech.push(t),
  setMotor: (i, v) => calls.setMotor.push([i, v]),
  getMotor: () => 16384,
  showIcons: (n) => calls.showIcons.push(n),
  clearIcons: () => calls.clearIcons.push(true),
  setHeartLED: () => {},
};
const clickHandlers = {}, mqttClientRef = { c: null }, els = {};
const fakeEl = (id) => ({
  id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
  addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
  appendChild: () => {},
  querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
});
globalThis.window = { moxie, addEventListener: () => {} };
globalThis.location = { hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: (id) => (els[id] ||= fakeEl(id)),
  createElement: () => fakeEl(),
};
globalThis.mqtt = {
  connect: () => {
    const h = {};
    mqttClientRef.c = {
      on: (e, cb) => { h[e] = cb; }, subscribe: () => {}, end: () => {},
      _emit: (e, ...a) => h[e] && h[e](...a),
    };
    return mqttClientRef.c;
  },
};

(0, eval)(src);
clickHandlers["bus-connect"]();
const client = mqttClientRef.c;
if (!client) throw new Error("bridge did not connect over mqtt");
client._emit("connect");

const play = (markup, text) => {
  reset();
  client._emit("message", "/devices/d_test/commands/remote_chat",
    Buffer.from(JSON.stringify({ command: "remote_chat", output: { text, markup } })));
};

// What each golden must make the avatar do. The faces come from bridge.js's MOOD_TO_FACE,
// which maps the authoritative ePlaybackMood 1:1 onto the 11 Bht_Eyeseme_* expressions.
const EXPECT = {
  G1: { face: "happy",     motors: true,  why: "'!' -> Happy, and Gesture_Self moves an arm" },
  G2: { face: "curious",   motors: true,  why: "an open question -> Curious + Gesture_Question" },
  G3: { face: "thinking",  motors: true,  why: "Bht_Active_Thinking drives the thinking pose" },
  G4: { face: "happy",     motors: true,  why: "praise -> Happy, Gesture_Higher + Gesture_Celebrate" },
  G5: { face: "surprised", motors: true,  why: "'Oh!' -> Surprised (mood 5, 14x in shipped content)" },
  G6: { face: "sad",       motors: true,  why: "'I am sorry' -> Sad (mood 2, 8x in shipped content)" },
  G7: { face: "shy",       motors: true,  why: "'Oops.' -> Shy (mood 4) — and no arm gesture but the rest pose" },
  G8: { face: "happy",     motors: true,  icons: "Birthday", why: "a calendar cue shows a screen badge" },
};

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
const facesSeen = new Set();
let asserted = 0;

for (const c of goldens.cases) {
  const want = EXPECT[c.id];
  if (!want) { fails.push(`golden ${c.id} has no expectation in this test`); continue; }
  play(c.markup, c.text);
  ok(calls.setSpeech.some((t) => t === c.text),
     `${c.id}: the spoken line reached the avatar; got ${JSON.stringify(calls.setSpeech)}`);
  ok(calls.setFace.includes(want.face),
     `${c.id}: expected face '${want.face}' (${want.why}); got ${JSON.stringify(calls.setFace)}`);
  ok(!want.motors || calls.setMotor.length > 0,
     `${c.id}: expected the body to move (${want.why}); no setMotor calls`);
  if (want.icons) {
    ok(JSON.stringify(calls.showIcons).includes(want.icons),
       `${c.id}: expected icon '${want.icons}'; got ${JSON.stringify(calls.showIcons)}`);
  }
  calls.setFace.forEach((f) => facesSeen.add(f));
  asserted += 1;
}

// The whole point of the floor is that the eight lines do NOT look the same.
ok(facesSeen.size >= 6,
   `the goldens must reach visibly different faces; only saw ${JSON.stringify([...facesSeen])}`);

// A line the floor never touched (the pre-floor passthrough) must still be inert: plain
// text drives the speech bubble and nothing else. That is the MOXIE_AUTOMARKUP=0 shape.
play("Just words, no markup.", "Just words, no markup.");
ok(calls.setFace.length === 0 && calls.setMotor.length === 0,
   `plain text must not animate anything; got ${JSON.stringify(calls)}`);

if (fails.length) {
  console.log("❌ automarkup render test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ automarkup render OK — ${asserted} goldens drove the real bridge; `
  + `${facesSeen.size} distinct faces (${[...facesSeen].sort().join(", ")}), `
  + `arms moved on every one, icons-v2 rendered a badge`);
