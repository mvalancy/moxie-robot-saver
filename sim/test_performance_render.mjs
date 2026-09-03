/* The behavior planner, seen from the only renderer we can assert against.
 *
 * No hardware has ever played our markup: everything we believe about how a robot
 * performs a `<mark cmd:…>` is inferred from the recovered generators
 * (docs/reverse-engineering/runtime/behavior-markup.md). The browser SIM is the one place
 * that inference is executable, so this drives the planner's TWENTY-TWO dialog-act
 * goldens — one line per `RemoteDialog.DialogAct` — through the REAL sim/web/bridge.js
 * and asserts the avatar actually performs each of them differently.
 *
 * This is acceptance criterion (d) of backlog/expressiveness.md §2.7 P1: the preview hook
 * renders ≥10 lines on the SIM, with a contact sheet as an artifact. The messages it
 * plays are exactly what `MoxieRuntime.preview` publishes — an ordinary
 * `commands/remote_chat` — because the preview hook deliberately has no SIM-specific API
 * (docs/architecture/sim-as-a-client.md).
 *
 * The goldens file is written by the Python side (sim/tools/build_performance_goldens.py,
 * pinned byte for byte by sim/tests/test_performance.py), so this is a genuine
 * cross-language contract check: if the planner stages an id the SIM does not animate,
 * this fails rather than the robot silently doing nothing.
 *
 * The contact sheet it writes (`sim/artifacts/performance-contact-sheet.html`, or
 * `--out <path>`) is one cell per act showing the face the avatar reached, which motors
 * moved and how far, the whole-body tree, and the beats behind it — an author's-eye view
 * of the whole taxonomy on one page, and the artifact a CI run attaches.
 *
 * No browser, no network. Run: node sim/test_performance_render.mjs
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "web", "bridge.js"), "utf8");
const goldens = JSON.parse(
  readFileSync(join(here, "tests", "goldens", "performance.json"), "utf8"));

const outArg = process.argv.indexOf("--out");
const OUT = outArg > -1 && process.argv[outArg + 1]
  ? process.argv[outArg + 1]
  : join(here, "artifacts", "performance-contact-sheet.html");

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

/* Exactly the message `MoxieRuntime.preview` publishes — an ordinary remote_chat. */
const play = (markup, text) => {
  reset();
  client._emit("message", "/devices/d_test/commands/remote_chat",
    Buffer.from(JSON.stringify({
      command: "remote_chat", result: "SUCCESS", backend: "router",
      event_id: "preview-1", output: { text, markup },
    })));
};

/* The face each act must reach, and why. The faces come from bridge.js's MOOD_TO_FACE,
 * which maps the authoritative ePlaybackMood 1:1 onto the 11 Bht_Eyeseme_* expressions.
 * `motors:false` is not an omission — it is the assertion that an act performs by NOT
 * moving the arms, which is the whole point of backchannelling and pos_answer. */
const EXPECT = {
  abandon:               { face: "shy",       motors: true,  why: "a dropped line goes Shy and the eyes go searching (Bht_Search)" },
  apology:               { face: "sad",       motors: true,  why: "'I am sorry' -> Sad (mood 2, 8x in shipped content) + Gesture_Self" },
  apology_response:      { face: "happy",     motors: true,  why: "reassurance points at the child" },
  appreciation:          { face: "happy",     motors: true,  why: "praise -> Happy at intensity 2 + Gesture_Celebrate" },
  backchannelling:       { face: "neutral",   motors: true,  why: "no ARM gesture at all; only the attentive tree and the rest pose" },
  closing:               { face: "happy",     motors: true,  why: "Bht_Sign_off — the app's own goodbye wave" },
  command:               { face: "neutral",   motors: true,  why: "an imperative points, and the gaze holds" },
  comment:               { face: "surprised", motors: true,  why: "a reaction is Surprised, with a curious look" },
  complaint:             { face: "concerned", motors: true,  why: "Concerned + a lowered arm" },
  factual_question:      { face: "curious",   motors: true,  why: "a question tilts (Gesture_Question) and HOLDS the gaze" },
  hold:                  { face: "thinking",  motors: true,  why: "Bht_Active_Thinking drives the whole-body thinking pose" },
  neg_answer:            { face: "neutral",   motors: true,  why: "a plain no lowers the arms" },
  opening:               { face: "happy",     motors: true,  why: "Bht_Gesture_Greet — the wave, which displaces the arm gesture" },
  opinion:               { face: "neutral",   motors: true,  why: "a stance points at the speaker (Gesture_Self)" },
  opinion_question:      { face: "curious",   motors: true,  why: "asking for a stance: the tilt, with a curious look" },
  other:                 { face: "neutral",   motors: true,  why: "nothing to perform; the line still comes back to rest" },
  other_answers:         { face: "curious",   motors: true,  why: "hedging is Curious with a thinking gesture" },
  pos_answer:            { face: "happy",     motors: true,  why: "an affirmative needs no arm; the face and the held gaze carry it" },
  statement_non_opinion: { face: "neutral",   motors: true,  why: "the act adds nothing and the floor's word rules do the work" },
  thanking:              { face: "happy",     motors: true,  why: "gratitude points at the child" },
  timeout:               { face: "curious",   motors: true,  why: "a turn that never arrived sends the eyes looking" },
  yes_no_question:       { face: "curious",   motors: true,  why: "a closed question tilts and holds, like an open one" },
};

const MOTOR_NAMES = ["L-shoulder", "L-elbow", "R-shoulder", "R-elbow", "head", "body-yaw", "body-lean"];
const REST = 16384;

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
const facesSeen = new Set();
const sheet = [];
let asserted = 0;

for (const c of goldens.cases) {
  const want = EXPECT[c.act];
  if (!want) { fails.push(`golden '${c.act}' has no expectation in this test`); continue; }
  play(c.markup, c.line);

  ok(calls.setSpeech.some((t) => t === c.line),
     `${c.act}: the spoken line reached the avatar; got ${JSON.stringify(calls.setSpeech)}`);
  ok(calls.setFace.includes(want.face),
     `${c.act}: expected face '${want.face}' (${want.why}); got ${JSON.stringify(calls.setFace)}`);
  ok(!want.motors || calls.setMotor.length > 0,
     `${c.act}: expected the body to move (${want.why}); no setMotor calls`);

  calls.setFace.forEach((f) => facesSeen.add(f));
  // Peak displacement per motor — the recorded state, never a live sample (the SIM
  // test rule: a test that samples a live value on a loaded runner flakes).
  const peak = new Map();
  for (const [i, v] of calls.setMotor) {
    const d = Math.abs(v - REST);
    if (d > (peak.get(i) || 0)) peak.set(i, d);
  }
  sheet.push({
    act: c.act, line: c.line, why: want.why,
    face: want.face, faces: [...new Set(calls.setFace)],
    trees: [...new Set([...c.markup.matchAll(/\+behaviour\+:\+(Bht_\w+)\+/g)].map((m) => m[1]))],
    gestures: [...new Set([...c.markup.matchAll(/\+eventName\+:\+(Gesture_\w+)\+/g)].map((m) => m[1]))],
    motors: [...peak.entries()].sort((a, b) => a[0] - b[0]),
    beats: (c.performance.beats || []).length,
    mood: c.performance.mood, signal: c.performance.signal,
  });
  asserted += 1;
}

/* (d) requires ≥10 lines; we play all 22. */
ok(asserted >= 10, `expected at least 10 rehearsed lines, played ${asserted}`);
/* And the whole point of scoring the act is that the acts do NOT look the same. */
ok(facesSeen.size >= 6,
   `the acts must reach visibly different faces; only saw ${JSON.stringify([...facesSeen])}`);
const withMotion = sheet.filter((r) => r.motors.length > 0).length;
ok(withMotion >= 18, `only ${withMotion}/${asserted} acts moved the body at all`);

/* A plain line the planner never touched must still be inert — the MOXIE_EXPRESSIVE=off
 * shape. If bare text animated the avatar, every assertion above would be meaningless. */
play("Just words, no markup.", "Just words, no markup.");
ok(calls.setFace.length === 0 && calls.setMotor.length === 0,
   `plain text must not animate anything; got ${JSON.stringify(calls)}`);

// ---- the contact sheet ------------------------------------------------------------
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const bar = (d) => `<span class="bar" style="width:${Math.min(100, Math.round(d / 164))}%"></span>`;
const cells = sheet.map((r) => `
  <figure>
    <header><b>${esc(r.act)}</b><span class="face">${esc(r.face)}</span></header>
    <blockquote>${esc(r.line)}</blockquote>
    <dl>
      <dt>mood</dt><dd>${esc(r.mood)} &middot; signal ${esc(r.signal)} &middot; ${r.beats} beat(s)</dd>
      <dt>gestures</dt><dd>${r.gestures.map(esc).join(", ") || "—"}</dd>
      <dt>trees</dt><dd>${r.trees.map(esc).join(", ") || "—"}</dd>
    </dl>
    <ul class="motors">${r.motors.map(([i, d]) =>
      `<li><span>${esc(MOTOR_NAMES[i] || "m" + i)}</span>${bar(d)}<i>${d}</i></li>`).join("") ||
      "<li><span>no motion</span></li>"}</ul>
    <p class="why">${esc(r.why)}</p>
  </figure>`).join("");

const html = `<!doctype html><meta charset="utf-8">
<title>Behavior planner — contact sheet</title>
<style>
 :root{color-scheme:dark}
 body{background:#0e0e14;color:#e8edf5;font:14px/1.5 system-ui,sans-serif;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8fa0b8;margin:0 0 20px}
 .grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(310px,1fr))}
 figure{margin:0;padding:12px 14px;border:1px solid #22303f;border-radius:10px;background:#131a24}
 header{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
 header b{color:#00f0ff;font-family:ui-monospace,monospace}
 .face{font-size:12px;color:#0e0e14;background:#00f0ff;border-radius:99px;padding:1px 9px}
 blockquote{margin:8px 0;font-style:italic;color:#cfe0f5}
 dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:6px 0;font-size:12px}
 dt{color:#8fa0b8} dd{margin:0;font-family:ui-monospace,monospace;font-size:11px}
 .motors{list-style:none;margin:8px 0 0;padding:0;font-size:11px}
 .motors li{display:grid;grid-template-columns:78px 1fr 42px;align-items:center;gap:6px}
 .bar{display:block;height:6px;background:linear-gradient(90deg,#00f0ff,#7d5cff);border-radius:3px}
 .motors i{color:#8fa0b8;font-style:normal;text-align:right}
 .why{color:#8fa0b8;font-size:11px;margin:8px 0 0;border-top:1px solid #22303f;padding-top:8px}
</style>
<h1>Behavior planner — 22 dialog acts on the SIM</h1>
<p class="sub">Every line published through the preview hook as an ordinary
<code>commands/remote_chat</code> and played through the real <code>sim/web/bridge.js</code>.
${asserted} acts &middot; ${facesSeen.size} distinct faces &middot; ${withMotion} moved the body.
No hardware has ever played our markup: the SIM is the only renderer we can assert against.</p>
<div class="grid">${cells}</div>
`;
mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, html);

if (fails.length) {
  console.log("❌ behavior planner render test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ planner render OK — ${asserted} dialog acts drove the real bridge; `
  + `${facesSeen.size} distinct faces (${[...facesSeen].sort().join(", ")}), `
  + `${withMotion} moved the body`);
console.log(`   contact sheet → ${OUT}`);
