/* The markup floor, seen from the only renderer we can assert against.
 *
 * Drives the EIGHT byte-exact goldens from sim/tests/goldens/annotate.json (pinned by
 * sim/tests/test_automarkup.py) through the REAL sim/web/bridge/ and asserts the avatar
 * does something different for each — a face per mood, motors for arm gestures, badges for
 * icons. An id the floor emits but the SIM does not animate fails here.
 *
 * No browser, no network. Run: node sim/test_automarkup_render.mjs
 */
import { loadBridge, readGolden } from "./bridge_harness.mjs";

const goldens = readGolden("annotate.json");
const { calls, reset, client } = loadBridge();

const play = (markup, text) => {
  reset();
  client._emit("message", "/devices/d_test/commands/remote_chat",
    Buffer.from(JSON.stringify({ command: "remote_chat", output: { text, markup } })));
};

// What each golden must make the avatar do. The faces come from bridge/'s MOOD_TO_FACE,
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
