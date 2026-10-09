// test_ambient.mjs — every ambient self-talk line (sim/web/ambient.json) uses a face and a
// gesture the page implements and has a PRE-CACHED clip (so it speaks on the static deploy);
// the thinking fillers agree across filler.py, bridge/ and the clip manifest. Then the REAL
// ambient.js on a virtual clock (sections in sim/tests/edge/ambient/): the October set only in
// October, the glitch beat bounded and never over a conversation, the post-goodbye aside only
// after a sign-off and once per sign-off, and the bridge's sign-off seam. After adding lines,
// render their clips (prerender_audio.py --ambient, in her one voice: see ambient.json).
//
//   node sim/test_ambient.mjs
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { BRIDGE_SRC, here } from "./bridge_harness.mjs";
import { runSections } from "./tests/edge/common.mjs";
import { C, fails, notes, ok } from "./tests/edge/ambient/harness.mjs";

const web = join(here, "web");
const src = (f) => readFileSync(join(web, f), "utf8");
/** Top-level keys of `NAME = { key: …, … }` in a shipped file — read, never restated. */
const keysOf = (file, name) => {
  const s = src(file), at = s.indexOf(name + " = {");
  const body = s.slice(at, s.indexOf("\n};", at) > 0 ? s.indexOf("\n};", at) : s.indexOf("};", at));
  return new Set([...body.matchAll(/^\s{2,4}(\w+):/gm)].map((m) => m[1]));
};
const FACES = keysOf("moxie/face.js", "EXPRESSIONS");
const GESTURES = keysOf("ambient.js", "GESTURES");
ok(FACES.has("happy") && FACES.size >= 10, `face.js EXPRESSIONS parsed (${[...FACES]})`);
ok(GESTURES.has("wave") && GESTURES.size >= 5, `ambient.js GESTURES parsed (${[...GESTURES]})`);

const amb = JSON.parse(src("ambient.json"));
const clips = JSON.parse(src("audio/index.json")).ambient || {};
ok(Array.isArray(amb.lines) && amb.lines.length > 0, "ambient.json has a non-empty lines[]");
for (const ln of amb.lines || []) {
  const t = (ln.text || "").trim(), at = t.slice(0, 40);
  ok(t.length > 0, `ambient line missing text: ${JSON.stringify(ln)}`);
  ok(!ln.face || FACES.has(ln.face), `unknown face "${ln.face}": ${at}`);
  ok(!ln.heart || /^#[0-9a-fA-F]{6}$/.test(ln.heart), `bad heart color "${ln.heart}": ${at}`);
  ok(!ln.gesture || GESTURES.has(ln.gesture), `unknown gesture "${ln.gesture}": ${at}`);
  ok(!!clips[t] && existsSync(join(web, "audio", clips[t])),
     `no pre-cached clip on disk (run prerender_audio.py --ambient): ${at}`);
  // The two optional fields ambient.js reads: a malformed one silently takes a line out of
  // the bag for good (a month that never comes) or puts a beat into it.
  ok(ln.months === undefined || (Array.isArray(ln.months) && ln.months.length > 0 &&
     ln.months.every((m) => Number.isInteger(m) && m >= 1 && m <= 12)),
     `"months" must be a non-empty list of month numbers 1-12: ${at}`);
  ok(ln.beat === undefined || ln.beat === "glitch" || ln.beat === "signoff",
     `"beat" must be "glitch" or "signoff" (anything else would never be said): ${at}`);
}
const texts = (amb.lines || []).map((l) => (l.text || "").trim());
ok(new Set(texts).size === texts.length, "no line is in ambient.json twice (one clip key per line)");
const october = (amb.lines || []).filter((l) => !l.beat && Array.isArray(l.months) && l.months.includes(10));
const glitch = (amb.lines || []).filter((l) => l.beat === "glitch");
const signoff = (amb.lines || []).filter((l) => l.beat === "signoff");
// Floors, not equalities: they fail when a set is DELETED and let the creature keep growing.
ok(october.length >= 8 && glitch.length >= 1 && signoff.length >= 3,
   `the October set (${october.length}), glitch lines (${glitch.length}) and post-goodbye asides ` +
   `(${signoff.length}) are all there`);
ok(!glitch.some((l) => !GESTURES.has(l.gesture)) && GESTURES.has("twitch") && GESTURES.has("boo"),
   "the glitch's twitch and the October boo are gestures ambient.js plays");

/* THE THINKING FILLERS — clips are keyed BY EXACT TEXT, so filler.py (source), bridge/ (speaks
 * them) and audio/index.json must agree character for character, or she is silently mute. */
const py = readFileSync(join(here, "..", "mqtt", "moxie_sdk", "filler.py"), "utf8");
const pyLines = [...py.matchAll(/\("([^"]+)",\s*\n?\s*MOOD/g)].map((m) => m[1]);
const block = /var FILLERS = \[([\s\S]*?)\];/.exec(BRIDGE_SRC);
const jsLines = block ? [...block[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)]
  .map((m) => m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\")) : [];
ok(pyLines.length > 0 && jsLines.length > 0, `fillers parsed (py ${pyLines.length}, js ${jsLines.length})`);
ok(JSON.stringify([...jsLines].sort()) === JSON.stringify([...pyLines].sort()),
   `bridge/ FILLERS == filler.py's lines\n   js: ${JSON.stringify(jsLines)}\n   py: ${JSON.stringify(pyLines)}`);
for (const t of pyLines) ok(!!clips[t], `no pre-rendered clip for filler ${JSON.stringify(t)}`);

await runSections(new URL("./tests/edge/ambient/", import.meta.url), [
  "01_season.mjs",
  "02_glitch.mjs",
  "03_signoff.mjs",
  "04_seam.mjs",
]);

if (fails.length) {
  console.error(`❌ ${fails.length} failure(s):`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✅ ambient tests OK — ${amb.lines.length} self-talk lines (${october.length} October, ` +
            `${glitch.length} glitch, ${signoff.length} post-goodbye) + ${pyLines.length} fillers, ` +
            `faces/gestures valid, all pre-cached; ${C.asserts} assertions`);
for (const n of notes) console.log("  " + n);
