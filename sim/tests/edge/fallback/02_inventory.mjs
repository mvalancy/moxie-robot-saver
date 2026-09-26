/* §3–6: the fallback's parts are present and wired, every line the degraded page can utter
 * (stub, filler, ambient, sessions — both speakers) has its clip, and the ambient layer
 * stays server-free.
 */
import {
  FLOORS, ambient, ambientSrc, audioDir, eq, existsSync, here, join, manifest, notes, ok,
  readFileSync, stubSrc, web,
} from "./harness.mjs";
import { sessionLines, childSessionLines } from "./01_manifest_sessions.mjs";

/* --------------------------------------------------------------------------- *
 * 3. §6.1 — the fallback's parts are all present and wired
 * --------------------------------------------------------------------------- */
{
  const html = readFileSync(join(web, "sim.html"), "utf8");
  // The files a degraded turn actually needs, in the order sim.html must load them:
  // stub.js publishes the offline brain, bridge.js consumes it, mode.js decides which
  // mode we are in, cloud-transport.js delegates to bridge.js when it is not `live`.
  for (const f of ["stub.js", "bridge.js", "mode.js", "cloud-transport.js", "audio.js", "ambient.js"]) {
    ok(html.includes(f), `sim.html must load ${f} — the fallback is not wired without it`);
    ok(existsSync(join(web, f)), `${f} must exist`);
  }
  // Order is measured on the `<script src>` tags: prose in an HTML comment may name them first.
  const loadsAt = (f) => html.indexOf('src="' + f);
  for (const f of ["stub.js", "bridge.js", "cloud-transport.js"])
    ok(loadsAt(f) > -1, `sim.html has a <script src> for ${f}`);
  ok(loadsAt("stub.js") < loadsAt("bridge.js"), "stub.js loads before bridge.js");
  ok(loadsAt("bridge.js") < loadsAt("cloud-transport.js"),
     "cloud-transport.js loads after bridge.js (it wraps what bridge.js published)");
  // §7 below drives `ambient.js`'s degraded announcer through `window.moxieMode`, which
  // only exists because mode.js ran first. In the page that is load ORDER, not luck.
  ok(loadsAt("mode.js") > -1 && loadsAt("ambient.js") > -1 &&
     loadsAt("mode.js") < loadsAt("ambient.js"),
     "mode.js must load before ambient.js — the degraded line subscribes to window.moxieMode at load");

  // `bridge.js` and `cloud-transport.js` gate the degraded answer on moxieStub.enabled.
  ok(/enabled:\s*true/.test(stubSrc), "window.moxieStub.enabled must be TRUE or a refused turn is silent");
  ok(stubSrc.includes("window.moxieStub"), "stub.js must publish window.moxieStub");

  // The transport must delegate to the inner bridge rather than answer for itself when the
  // mode is not live — §3.5's guarantee that today's page cannot regress.
  const transportSrc = readFileSync(join(web, "cloud-transport.js"), "utf8");
  ok(transportSrc.includes("inner.sendUserTurn"), "cloud-transport.js must delegate to inner.sendUserTurn");
  ok(transportSrc.includes("window.moxieStub"), "…and must be able to answer one turn from the stub itself");

  // Stub replies carry the markup families `applyMarkup` parses, or the face goes dead.
  ok(stubSrc.includes("cmd:playback-mood"), "stub replies carry a mood mark");
  ok(stubSrc.includes("+eventName+:+"), "…and a gesture eventName");
  ok(stubSrc.includes("cmd:icons-v2"), "…and can carry an icon mark");
}

/* --------------------------------------------------------------------------- *
 * 4. Reading the lines out of their sources
 * --------------------------------------------------------------------------- */

/** Un-escape a Python/JS single-line string literal body. */
const ESCAPES = { n: "\n", t: "\t" };
const unescape1 = (s) => s.replace(/\\(["'\\nt])/g, (_, c) => (ESCAPES[c] !== undefined ? ESCAPES[c] : c));

/** `stub.js`'s SCRIPT + FALLBACK replies — the exact strings `bridge.js` hands `speak()`. */
function stubReplies() {
  return [...stubSrc.matchAll(/say:\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => unescape1(m[1]));
}

/**
 * `filler.py`'s eight spoken lines — the first element of each `_LINES` tuple.
 * Takes the first string of each tuple, then PROVES it understood the block: every string it
 * did not take must be a behaviour-tree/gesture identifier, not a missed spoken line.
 */
function fillerLines(src) {
  const start = src.indexOf("_LINES = (");
  if (start === -1) return { texts: [], leftovers: [], found: false };
  const block = src.slice(start, src.indexOf("\n)", start) + 2);
  const texts = [...block.matchAll(/\(\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => unescape1(m[1]));
  const all = [...block.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => unescape1(m[1]));
  const taken = new Set(texts);
  const leftovers = all.filter((s) => !taken.has(s));
  return { texts, leftovers, found: true };
}

const fillerPath = join(here, "..", "mqtt", "moxie_sdk", "filler.py");
ok(existsSync(fillerPath), "mqtt/moxie_sdk/filler.py must exist — it owns the thinking lines");
const filler = existsSync(fillerPath)
  ? fillerLines(readFileSync(fillerPath, "utf8"))
  : { texts: [], leftovers: [], found: false };
ok(filler.found, "filler.py must still declare its lines as `_LINES = (` — the extractor keys on that");
for (const s of filler.leftovers) {
  ok(/^(Bht_|Gesture_|BehaviourTree$)/.test(s),
     `the filler extractor did not understand ${JSON.stringify(s)} — it is neither a spoken line nor a ` +
     `behaviour-tree/gesture/category identifier, so the coverage count below cannot be trusted`);
}
for (const t of filler.texts) {
  ok(/\s/.test(t) && /[.!?…]$/.test(t),
     `a filler line does not look like a sentence: ${JSON.stringify(t)} — the extractor probably grabbed an identifier`);
}

ok(Array.isArray(ambient.lines) && ambient.lines.length > 0, "ambient.json must carry lines");

/* --------------------------------------------------------------------------- *
 * 5. THE INVENTORY — every line the degraded page can utter, and its clip
 * One rule over five sources: if the degraded page can say it, an MP3 is keyed by that exact
 * string. `group` is where `playClip` looks first (it falls back moxie -> child, never to
 * ambient); `strict` lines (the child's) allow no fallthrough, because `speakClipOnly` reads
 * only the `child` group and Moxie's voice saying the child's words is wrong, not covered.
 * --------------------------------------------------------------------------- */
export const inventory = [];
for (const t of stubReplies()) inventory.push({ text: t, group: "moxie", source: "stub.js reply" });
for (const t of filler.texts) inventory.push({ text: t, group: "moxie", source: "filler.py thinking line" });
for (const ln of ambient.lines) inventory.push({ text: (ln.text || "").trim(), group: "ambient", source: "ambient.json quip" });
for (const s of sessionLines) inventory.push({ text: s.text, group: "moxie", source: `session ${s.where}` });
for (const s of childSessionLines)
  inventory.push({ text: s.text, group: "child", strict: true, source: `session-child ${s.where}` });
if (ambient.degraded) {
  inventory.push({ text: (ambient.degraded.text || "").trim(), group: "moxie", source: "ambient.json degraded line" });
}

export const counts = { "stub.js reply": 0, "filler.py thinking line": 0, "ambient.json quip": 0,
                 "ambient.json degraded line": 0, session: 0, "session-child": 0 };
const uncovered = [];
for (const item of inventory) {
  const key = item.source.startsWith("session-child") ? "session-child"
            : item.source.startsWith("session") ? "session" : item.source;
  counts[key] = (counts[key] || 0) + 1;
  ok(item.text.length > 0, `an inventory entry from ${item.source} has no text`);
  const rel = item.strict
    ? (manifest[item.group] || {})[item.text]
    : (manifest[item.group] || {})[item.text] ||
      (item.group !== "ambient" ? (manifest.moxie || {})[item.text] || (manifest.child || {})[item.text] : null);
  ok(!!rel,
     `NO PRE-CACHED CLIP for a line the degraded page can say (${item.source}): ` +
     `${JSON.stringify(item.text.slice(0, 60))} — run sim/tools/prerender_audio.py, or the visitor hears a ` +
     `browser voice mid-conversation${item.strict ? " (or, for a child line, nothing at all)" : ""}`);
  if (!rel) { uncovered.push(item); continue; }
  ok(existsSync(join(audioDir, rel)), `${item.source}: clip file missing on disk: ${rel}`);
}
ok(counts["stub.js reply"] >= FLOORS.stubReplies,
   `stub.js should carry at least ${FLOORS.stubReplies} replies, found ${counts["stub.js reply"]}`);
ok(counts["filler.py thinking line"] >= FLOORS.fillerLines,
   `filler.py should carry at least ${FLOORS.fillerLines} thinking lines, found ${counts["filler.py thinking line"]}`);
eq(uncovered.length, 0, "every line the degraded page can utter must have a clip");

// The birthday lines are what make the shipped `sessions/demo.json` replay in Moxie's voice.
const birthday = stubReplies().filter((t) => /birthday/i.test(t));
ok(birthday.length > 0, "stub.js must still answer a birthday");
for (const t of birthday)
  ok(!!(manifest.moxie || {})[t.trim()],
     `the birthday stub reply must keep its clip (it is the shipped demo's voice): ${JSON.stringify(t.slice(0, 40))}`);

/* `mic.js`'s degraded fallback picks a scripted CHILD line from `index.child`; an empty group
 * would leave the button saying "stt unavailable" instead. */
ok(Object.keys(manifest.child || {}).length > 0,
   "the child group must not be empty — mic.js's degraded fallback picks its scripted line from it");

/* --------------------------------------------------------------------------- *
 * 6. §2.4 — the ambient layer is server-free, and stays that way
 * --------------------------------------------------------------------------- */
{
  // Line-by-line in sim/test_ambient.mjs; a COUNT here too, so an emptied layer fails this suite.
  ok(Object.keys(manifest.ambient || {}).length >= ambient.lines.length,
     `every ambient line needs a clip: ${ambient.lines.length} lines vs ` +
     `${Object.keys(manifest.ambient || {}).length} clips`);
  // "Server-free" = no BACKEND: fetching its own static `ambient.json` is fine; an /api/
  // path, an absolute URL or a port is not.
  const ambientFetches = [...ambientSrc.matchAll(/fetch\s*\(\s*"([^"]*)"/g)].map((m) => m[1]);
  ok(ambientFetches.length > 0, "ambient.js loads its own line list");
  for (const url of ambientFetches) {
    ok(!/^[a-z]+:\/\//i.test(url), `ambient.js must not fetch an absolute URL: ${url}`);
    ok(!url.startsWith("/api/") && !url.includes(":80") && !url.includes(":90"),
       `ambient.js must not depend on a backend: ${url}`);
  }
}

notes.push(`inventory: ${inventory.length} utterable line(s), ALL pre-rendered — ` +
           `${counts["stub.js reply"]} stub · ${counts["filler.py thinking line"]} filler · ` +
           `${counts["ambient.json quip"]} ambient · ${counts["ambient.json degraded line"]} degraded · ` +
           `${counts.session} session moxie · ${counts["session-child"]} session child`);
