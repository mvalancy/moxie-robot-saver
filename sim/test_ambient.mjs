// test_ambient.mjs — every ambient self-talk line (sim/web/ambient.json) uses a face and a
// gesture the page implements and has a PRE-CACHED clip (so it speaks on the static deploy);
// the thinking fillers agree across filler.py, bridge/ and the clip manifest. After adding
// lines, re-run prerender_audio.py --ambient.
//
//   node sim/test_ambient.mjs
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { BRIDGE_SRC, here, checks } from "./bridge_harness.mjs";

const web = join(here, "web");
const { ok, report } = checks();
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
}

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

report(`✅ ambient tests OK — ${amb.lines.length} self-talk lines + ${pyLines.length} fillers, ` +
       "faces/gestures valid, all pre-cached");
