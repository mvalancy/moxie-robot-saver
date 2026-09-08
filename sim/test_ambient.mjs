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

/* THE THINKING FILLERS — three copies of the same eight sentences, pinned together.
 *
 * `mqtt/moxie_sdk/filler.py` is the source (the robot path has had these all along),
 * `sim/web/bridge.js` speaks them, and `audio/index.json` keys its clips BY THE EXACT
 * TEXT. Punctuation is load-bearing: an em dash typed as a hyphen, or "Hmmm" for "Hmmmm",
 * and the lookup misses and she is silently mute at the one moment she is meant to fill.
 * Silence is also the correct failure, which is precisely why nothing would notice — so
 * the three copies are compared here rather than trusted. */
{
  const py = readFileSync(join(here, "..", "mqtt", "moxie_sdk", "filler.py"), "utf8");
  const pyLines = [...py.matchAll(/\("([^"]+)",\s*\n?\s*MOOD/g)].map((m) => m[1]);
  ok(pyLines.length === 8, `filler.py still defines 8 lines (got ${pyLines.length})`);

  const bridge = readFileSync(join(web, "bridge.js"), "utf8");
  const block = /var FILLERS = \[([\s\S]*?)\];/.exec(bridge);
  ok(!!block, "bridge.js carries a FILLERS list");
  const jsLines = block ? [...block[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)]
    .map((m) => m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\")) : [];

  for (const t of pyLines) {
    ok(jsLines.includes(t), `bridge.js speaks filler.py's line: ${JSON.stringify(t)}`);
    ok(!!clips[t], `…and a clip is pre-rendered for it (run prerender_audio.py --ambient)`);
  }
  ok(jsLines.length === pyLines.length,
     `bridge.js has no EXTRA fillers without clips (${jsLines.length} vs ${pyLines.length})`);
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
