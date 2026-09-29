/* The markup floor, seen from the only renderer we can assert against: the EIGHT byte-exact
 * goldens of sim/tests/goldens/annotate.json (pinned by sim/tests/test_automarkup.py) through
 * the REAL sim/web/bridge/. Each must reach its face and move the body; an id the floor emits
 * but the SIM does not animate fails here. No browser. Run: node sim/test_automarkup_render.mjs
 */
import { loadBridge, readGolden, checks } from "./bridge_harness.mjs";

const { calls, reset, client } = loadBridge();
const { ok, report } = checks();
const play = (markup, text) => {
  reset();
  client._emit("message", "/devices/d_test/commands/remote_chat",
    Buffer.from(JSON.stringify({ command: "remote_chat", output: { text, markup } })));
};

// The face each golden must reach (bridge/ MOOD_TO_FACE maps ePlaybackMood onto Bht_Eyeseme_*).
const FACE = { G1: "happy", G2: "curious", G3: "thinking", G4: "happy",
               G5: "surprised", G6: "sad", G7: "shy", G8: "happy" };
const ICON = { G8: "Birthday" };   // a calendar cue shows a screen badge

const cases = readGolden("annotate.json").cases;
const facesSeen = new Set();
ok(JSON.stringify(cases.map((c) => c.id).sort()) === JSON.stringify(Object.keys(FACE).sort()),
   `every golden has exactly one expectation here (goldens: ${cases.map((c) => c.id)})`);
for (const c of cases) {
  play(c.markup, c.text);
  ok(calls.setSpeech.includes(c.text), `${c.id}: the spoken line reached the avatar`);
  ok(calls.setFace.includes(FACE[c.id]), `${c.id}: face '${FACE[c.id]}'; got ${JSON.stringify(calls.setFace)}`);
  ok(calls.setMotor.length > 0, `${c.id}: the body must move; no setMotor calls`);
  if (ICON[c.id]) ok(JSON.stringify(calls.showIcons).includes(ICON[c.id]),
                     `${c.id}: icon '${ICON[c.id]}'; got ${JSON.stringify(calls.showIcons)}`);
  calls.setFace.forEach((f) => facesSeen.add(f));
}
ok(facesSeen.size >= 6, `the goldens must look different; only saw ${[...facesSeen]}`);

// The pre-floor passthrough (MOXIE_AUTOMARKUP=0) stays inert, or every check above is moot.
play("Just words, no markup.", "Just words, no markup.");
ok(calls.setFace.length === 0 && calls.setMotor.length === 0,
   `plain text must not animate anything; got ${JSON.stringify(calls)}`);

report(`✅ automarkup render OK — ${cases.length} goldens drove the real bridge; ` +
       `${facesSeen.size} distinct faces, the body moved on every one`);
