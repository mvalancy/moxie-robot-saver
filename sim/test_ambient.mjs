// test_ambient.mjs — guard the ambient self-talk layer (sim/web/ambient.json).
//
// No browser needed. Verifies every ambient line is well-formed, has a valid
// face, and (critically) has a PRE-CACHED audio clip so it actually speaks on
// the static deploy. Growing ambient.json over time stays safe as long as this
// passes (re-run prerender_audio.py --ambient after adding lines).
//
//   node sim/test_ambient.mjs
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "web");
const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

// valid faces = EXPRESSIONS in moxie.js (+ blink)
const FACES = new Set(["sleep", "neutral", "happy", "sad", "surprised", "thinking", "blink"]);
// valid gestures = GESTURES in ambient.js
const GESTURES = new Set(["wave", "raiseBoth", "shrug", "leanIn", "tilt", "point", "peek", "slump"]);

const amb = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8"));
ok(Array.isArray(amb.lines) && amb.lines.length > 0, "ambient.json must have a non-empty lines[]");

const man = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8"));
const clips = man.ambient || {};

for (const ln of amb.lines || []) {
  const t = (ln.text || "").trim();
  ok(t.length > 0, `ambient line missing text: ${JSON.stringify(ln)}`);
  ok(!ln.face || FACES.has(ln.face), `ambient line has unknown face "${ln.face}": ${t.slice(0, 40)}`);
  ok(!ln.heart || /^#[0-9a-fA-F]{6}$/.test(ln.heart), `ambient line has bad heart color "${ln.heart}": ${t.slice(0, 40)}`);
  ok(!ln.gesture || GESTURES.has(ln.gesture), `ambient line has unknown gesture "${ln.gesture}": ${t.slice(0, 40)}`);
  // pre-cached clip present + file exists on disk
  const rel = clips[t];
  ok(!!rel, `no pre-cached clip for ambient line (run prerender_audio.py --ambient): ${t.slice(0, 48)}`);
  if (rel) ok(existsSync(join(web, "audio", rel)), `ambient clip file missing: ${rel}`);
}

// wiring: sim.html loads ambient.js
const sim = readFileSync(join(web, "sim.html"), "utf8");
ok(/src="ambient\.js/.test(sim), "sim.html must load ambient.js");

if (fails.length) {
  console.log("❌ ambient tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ ambient tests OK — ${amb.lines.length} self-talk lines, all faces valid & pre-cached`);
