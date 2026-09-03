/* test_fallback_coverage.mjs — the degraded page has a real voice for the lines it plays.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §8.1 test 6, §6.1 (what is reused
 * as-is), §6.2 (the four things P1 owes), §6.3 (the state machine), §2.4 (the fallback
 * assets that already exist).
 *
 * WHY THIS FILE EXISTS. When the live brain is unreachable — unconfigured, over budget, at
 * capacity, rate-limited, or simply down — the hosted page answers from `stub.js` and
 * speaks from `sim/web/audio/index.json`. That fallback is the whole reason a public demo
 * can ship at all, and it is made of two things that can silently drift apart: a text
 * string in a source file, and an MP3 on disk keyed by that EXACT string. Change the
 * punctuation of a line and the clip is orphaned — no error, no test failure, just a
 * different, non-Moxie browser voice.
 *
 * This is the shape of `sim/test_ambient.mjs`:29-39, applied to every other fallback
 * asset at once.
 *
 * ============================================================================
 * WHAT IT COVERS NOW (P1 landed 2026-09-03 — the four §6.2 rows are built).
 *
 * P0 shipped this file asserting the SESSIONS only, and MEASURING the rest: nine of the
 * eleven `stub.js` replies and all eight `filler.py` lines had no clip, so the numbers
 * were printed rather than enforced behind a `REQUIRE_STUB_CLIPS` constant, on the honest
 * grounds that "a red build everyone learns to ignore is worse than an honest number".
 *
 * The clips exist now, so the constant is gone and the measurement is an ASSERTION.
 * §5 below builds ONE inventory — every string the degraded page can utter, from every
 * source that can produce one — and requires a clip for each. A new line anywhere in
 * `stub.js`, `filler.py` or `ambient.json` with no clip turns this red, which is exactly
 * the regression the P0 version could not catch.
 *
 * Two of the checks are BEHAVIOURAL rather than textual, because a grep for a function
 * name proves nothing about what runs (playbook rule 11): §7 loads the real `ambient.js`
 * under a stubbed window and drives the mode machine through boot -> degraded -> live ->
 * degraded, and §8 loads the real `audio.js` five times and watches whether the Piper
 * probe actually goes out on the wire.
 *
 * Every assertion here was checked by MUTATION rather than by passing: a new uncached stub
 * line, a new uncached filler line, a manifest that lost its `ambient` group, the degraded
 * line pushed into the random bag, the probe skip deleted, the probe skip widened to
 * `offline`, the announcer widened to `offline`, the once-only latch removed, the
 * renderer's merge reverted, and this file's own filler extractor regressed to the sloppy
 * regex. Each one turns this red on the assertion named for it.
 * ============================================================================
 *
 *   node sim/test_fallback_coverage.mjs
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "web");
const audioDir = join(web, "audio");

const fails = [];
const notes = [];
let asserts = 0;
const ok = (c, m) => { asserts++; if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);

/* Floors, not equalities. A floor fails when content is DELETED (the clips, or the lines
 * that reach them) while still letting the repo grow — the ambient layer is an explicit
 * "keep growing it" task, so an exact count here would be a tax on every new quip. The
 * coverage assertions in §5 are what fail when content is ADDED without a clip, so both
 * directions of "silently regress" are closed. */
const FLOORS = { moxie: 30, child: 2, ambient: 56, stubReplies: 11, fillerLines: 8 };

/* --------------------------------------------------------------------------- *
 * 1. The manifest
 * --------------------------------------------------------------------------- */
const manifest = JSON.parse(readFileSync(join(audioDir, "index.json"), "utf8"));
ok(manifest && typeof manifest === "object", "audio/index.json must be an object");

/* Every group must be PRESENT and NON-EMPTY, and this is not a formality: the renderer
 * merged the existing manifest group-by-NAME, so one run of
 * `prerender_audio.py --phrases …` rewrote index.json with no `ambient` key at all — 56
 * committed MP3s orphaned on disk, the whole self-talk layer muted, and not one error
 * printed. Reproduced while building P1's clips. §9 guards the tool; this guards the
 * artefact, because the artefact is what ships. */
for (const group of ["moxie", "child", "ambient"]) {
  ok(manifest[group] && typeof manifest[group] === "object",
     `audio/index.json must have a ${group} group — a run of prerender_audio.py that drops one orphans every clip in it`);
  const n = Object.keys(manifest[group] || {}).length;
  ok(n >= FLOORS[group],
     `audio/index.json's ${group} group has ${n} entries, floor is ${FLOORS[group]} — clips were removed, or a manifest write dropped them`);
}

/** Every clip the manifest claims must actually be on disk and non-empty. A manifest entry
 *  pointing at a missing file is worse than no entry: `audio.js` tries it, fails, and only
 *  then falls back — so the visitor pays the latency of a 404 before hearing anything. */
let clipCount = 0;
let clipBytes = 0;
for (const [group, entries] of Object.entries(manifest)) {
  if (!entries || typeof entries !== "object") continue;
  for (const [phrase, rel] of Object.entries(entries)) {
    clipCount++;
    const path = join(audioDir, rel);
    ok(typeof rel === "string" && rel.length > 0, `${group}: empty path for ${JSON.stringify(phrase.slice(0, 40))}`);
    ok(existsSync(path), `${group}: clip file missing on disk: ${rel} (for ${JSON.stringify(phrase.slice(0, 40))})`);
    if (existsSync(path)) {
      const size = statSync(path).size;
      clipBytes += size;
      // 2 KB is well under the smallest real line (the shortest shipped clip is ~14 KB at
      // mono 64 kbit) and well over an empty or header-only file. A zero-length file was
      // already caught; this catches a truncated write.
      ok(size > 2048, `${group}: clip file is implausibly small (${size} B) — truncated or silent: ${rel}`);
    }
    // The key is the EXACT utterance string `audio.js::speak` looks up, so a leading or
    // trailing space is an orphaned clip that nothing can ever match.
    eq(phrase, phrase.trim(), `${group}: a manifest key has surrounding whitespace: ${JSON.stringify(phrase)}`);
  }
}

/* --------------------------------------------------------------------------- *
 * 2. Every Moxie line in every recorded session has a clip
 * --------------------------------------------------------------------------- */
const sessionsDir = join(web, "sessions");
const sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"));
ok(sessionFiles.length > 0, "there must be at least one recorded session");

const sessionLines = [];
for (const file of sessionFiles) {
  const events = JSON.parse(readFileSync(join(sessionsDir, file), "utf8"));
  ok(Array.isArray(events), `${file} must be an array of {t, topic, payload} events`);
  if (!Array.isArray(events)) continue;

  for (const [i, ev] of events.entries()) {
    ok(ev && typeof ev === "object", `${file}[${i}] must be an object`);
    ok(typeof ev.topic === "string" && ev.topic.length > 0, `${file}[${i}] must carry a topic`);
    // `replay()` hands `payload` to `route()`, which calls JSON.parse itself — so a
    // payload that is not a STRING would silently render nothing (`bridge.js`:599-610).
    eq(typeof ev.payload, "string", `${file}[${i}] payload must be a STRING (route() parses it)`);
    ok(Number.isFinite(Number(ev.t)), `${file}[${i}] must carry a numeric t`);

    if (!ev.topic.endsWith("/commands/remote_chat")) continue;
    let msg = null;
    try { msg = JSON.parse(ev.payload); } catch {}
    ok(msg !== null, `${file}[${i}] remote_chat payload must be valid JSON`);
    const text = ((msg && msg.output && msg.output.text) || "").trim();
    if (text) sessionLines.push({ text, where: `${file}[${i}]` });
  }
}
ok(sessionLines.length > 0, "the sessions must contain at least one spoken Moxie line");

/* --------------------------------------------------------------------------- *
 * 3. §6.1 — the fallback's parts are all present and wired
 * --------------------------------------------------------------------------- */
const stubSrc = readFileSync(join(web, "stub.js"), "utf8");
const ambientSrc = readFileSync(join(web, "ambient.js"), "utf8");
const audioSrc = readFileSync(join(web, "audio.js"), "utf8");
{
  const html = readFileSync(join(web, "sim.html"), "utf8");
  // The files a degraded turn actually needs, in the order sim.html must load them:
  // stub.js publishes the offline brain, bridge.js consumes it, mode.js decides which
  // mode we are in, cloud-transport.js delegates to bridge.js when it is not `live`.
  for (const f of ["stub.js", "bridge.js", "mode.js", "cloud-transport.js", "audio.js", "ambient.js"]) {
    ok(html.includes(f), `sim.html must load ${f} — the fallback is not wired without it`);
    ok(existsSync(join(web, f)), `${f} must exist`);
  }
  ok(html.indexOf("stub.js") < html.indexOf("bridge.js"), "stub.js loads before bridge.js");
  ok(html.indexOf("bridge.js") < html.indexOf("cloud-transport.js"),
     "cloud-transport.js loads after bridge.js (it wraps what bridge.js published)");
  // §7 below drives `ambient.js`'s degraded announcer through `window.moxieMode`, which
  // only exists because mode.js ran first. In the page that is load ORDER, not luck.
  ok(html.indexOf("mode.js") < html.indexOf("ambient.js"),
     "mode.js must load before ambient.js — the degraded line subscribes to window.moxieMode at load");

  // `stub.js` must still be ENABLED. `bridge.js`:693 and `cloud-transport.js` both gate
  // the degraded answer on `window.moxieStub.enabled`, so a `false` here would turn every
  // refusal into dead air — the exact failure mode this contract exists to prevent (§4.5).
  ok(/enabled:\s*true/.test(stubSrc), "window.moxieStub.enabled must be TRUE or a refused turn is silent");
  ok(stubSrc.includes("window.moxieStub"), "stub.js must publish window.moxieStub");

  // The transport must delegate to the inner bridge rather than answer for itself when the
  // mode is not live — §3.5's guarantee that today's page cannot regress.
  const transportSrc = readFileSync(join(web, "cloud-transport.js"), "utf8");
  ok(transportSrc.includes("inner.sendUserTurn"), "cloud-transport.js must delegate to inner.sendUserTurn");
  ok(transportSrc.includes("window.moxieStub"), "…and must be able to answer one turn from the stub itself");

  // The markup every stub reply carries must be the three families `applyMarkup` parses,
  // or a degraded turn renders words with a dead face — which reads as broken, not
  // degraded. This is the fallback's *visual* half.
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
 *
 * The P0 version of this file matched EVERY quoted string in the block and got 34: it
 * counted `Bht_Active_Thinking`, `Gesture_Think_Subtle` and `BehaviourTree` as things
 * Moxie says. A coverage number computed over 34 strings, 26 of which are identifiers, is
 * not a coverage number. This takes the first string after each tuple's open-paren, which
 * is the shape `_LINES` is written in, and then PROVES it understood the block by
 * checking that every string it did NOT take is one of the identifier families.
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

const ambient = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8"));
ok(Array.isArray(ambient.lines) && ambient.lines.length > 0, "ambient.json must carry lines");

/* --------------------------------------------------------------------------- *
 * 5. THE INVENTORY — every line the degraded page can utter, and its clip
 *
 * One list, five sources, one rule: if the degraded page can say it, there is an MP3
 * keyed by that exact string. This is the assertion the P0 version deferred, and it is
 * the one that makes a NEW uncached line turn the build red.
 *
 * `group` is the manifest group `audio.js::playClip(text, who)` looks in first. `playClip`
 * falls back moxie -> child, but never to `ambient`, so an ambient line in the wrong group
 * really is silent.
 * --------------------------------------------------------------------------- */
const inventory = [];
for (const t of stubReplies()) inventory.push({ text: t, group: "moxie", source: "stub.js reply" });
for (const t of filler.texts) inventory.push({ text: t, group: "moxie", source: "filler.py thinking line" });
for (const ln of ambient.lines) inventory.push({ text: (ln.text || "").trim(), group: "ambient", source: "ambient.json quip" });
for (const s of sessionLines) inventory.push({ text: s.text, group: "moxie", source: `session ${s.where}` });
if (ambient.degraded) {
  inventory.push({ text: (ambient.degraded.text || "").trim(), group: "moxie", source: "ambient.json degraded line" });
}

const counts = { "stub.js reply": 0, "filler.py thinking line": 0, "ambient.json quip": 0,
                 "ambient.json degraded line": 0, session: 0 };
const uncovered = [];
for (const item of inventory) {
  const key = item.source.startsWith("session") ? "session" : item.source;
  counts[key] = (counts[key] || 0) + 1;
  ok(item.text.length > 0, `an inventory entry from ${item.source} has no text`);
  const rel = (manifest[item.group] || {})[item.text] ||
              (item.group !== "ambient" ? (manifest.moxie || {})[item.text] || (manifest.child || {})[item.text] : null);
  ok(!!rel,
     `NO PRE-CACHED CLIP for a line the degraded page can say (${item.source}): ` +
     `${JSON.stringify(item.text.slice(0, 60))} — run sim/tools/prerender_audio.py, or the visitor hears a ` +
     `browser voice mid-conversation`);
  if (!rel) { uncovered.push(item); continue; }
  ok(existsSync(join(audioDir, rel)), `${item.source}: clip file missing on disk: ${rel}`);
}
ok(counts["stub.js reply"] >= FLOORS.stubReplies,
   `stub.js should carry at least ${FLOORS.stubReplies} replies, found ${counts["stub.js reply"]}`);
ok(counts["filler.py thinking line"] >= FLOORS.fillerLines,
   `filler.py should carry at least ${FLOORS.fillerLines} thinking lines, found ${counts["filler.py thinking line"]}`);
eq(uncovered.length, 0, "every line the degraded page can utter must have a clip");

// The birthday lines are load-bearing beyond coverage: they are what makes the shipped
// `sessions/demo.json` replay in Moxie's own voice.
const birthday = stubReplies().filter((t) => /birthday/i.test(t));
ok(birthday.length > 0, "stub.js must still answer a birthday");
for (const t of birthday)
  ok(!!(manifest.moxie || {})[t.trim()],
     `the birthday stub reply must keep its clip (it is the shipped demo's voice): ${JSON.stringify(t.slice(0, 40))}`);

/* `mic.js`'s degraded "Listen" picks a CHILD line out of `Object.keys(index.child)`
 * (`stub.js::scriptedLines`) and publishes it as a child utterance, which the stub then
 * ANSWERS. So the reachable set of Moxie replies from a degraded mic is exactly
 * `stub.reply()`'s range, which the inventory above already covers in full — but only as
 * long as the child group is non-empty, or `scriptedLines()` returns [] and the button
 * says "stt unavailable" instead of running the conversation. */
ok(Object.keys(manifest.child || {}).length > 0,
   "the child group must not be empty — mic.js's degraded fallback picks its scripted line from it");

/* --------------------------------------------------------------------------- *
 * 6. §2.4 — the ambient layer is server-free, and stays that way
 * --------------------------------------------------------------------------- */
{
  // Covered line-by-line by sim/test_ambient.mjs; asserted here as a COUNT too, so that a
  // change which quietly empties the ambient layer fails the fallback test as well.
  // "Alive while idle" is what stops a degraded page from feeling dead.
  ok(Object.keys(manifest.ambient || {}).length >= ambient.lines.length,
     `every ambient line needs a clip: ${ambient.lines.length} lines vs ` +
     `${Object.keys(manifest.ambient || {}).length} clips`);
  // "Server-free" means it needs no BACKEND, not that it makes no request: it fetches its
  // own committed `ambient.json`, which is a static asset served by the CDN like any
  // other. (Rule 17: the first version of this guard banned `fetch` outright and fired on
  // that line — the GUARD was wrong, not the code.) What must never appear is an /api/
  // path, an absolute URL or a port: those would make the one layer that works in every
  // mode depend on something that might not be there.
  const ambientFetches = [...ambientSrc.matchAll(/fetch\s*\(\s*"([^"]*)"/g)].map((m) => m[1]);
  ok(ambientFetches.length > 0, "ambient.js loads its own line list");
  for (const url of ambientFetches) {
    ok(!/^[a-z]+:\/\//i.test(url), `ambient.js must not fetch an absolute URL: ${url}`);
    ok(!url.startsWith("/api/") && !url.includes(":80") && !url.includes(":90"),
       `ambient.js must not depend on a backend: ${url}`);
  }
}

/* --------------------------------------------------------------------------- *
 * 7. §6.2 row 3 — the degraded line, driven for real
 *
 * The real `ambient.js` is loaded under a stubbed window and a stub mode machine is walked
 * through boot -> degraded -> live -> degraded. What is asserted is what came OUT of
 * `moxieAudio.speak`, not that the file contains a function with a promising name.
 * --------------------------------------------------------------------------- */
const degradedText = ((ambient.degraded || {}).text || "").trim();
{
  ok(!!ambient.degraded, "ambient.json must carry a `degraded` entry (§6.2 row 3)");
  ok(degradedText.length > 0, "the degraded line must have text");
  // It must NOT be in the random bag, or Moxie announces a dead cloud as a quip at a
  // healthy moment. This is why it lives beside `lines[]` rather than in it.
  ok(!ambient.lines.some((l) => (l.text || "").trim() === degradedText),
     "the degraded line must NOT also be in ambient.json's lines[] — it would become a random quip");
  // Its clip is in the `moxie` group on purpose: it is a thing she says TO you, and
  // `playClip` never falls back to `ambient`.
  ok(!!(manifest.moxie || {})[degradedText],
     "the degraded line's clip must be in the manifest's `moxie` group");
  ok(!(manifest.ambient || {})[degradedText],
     "the degraded line must not also sit in the `ambient` group — one line, one clip");
  // The presentation it asks for must be one ambient.js can actually play, or the line
  // arrives with a dead face — the same failure the stub-markup checks guard against.
  const FACES = new Set(["sleep", "neutral", "happy", "sad", "surprised", "thinking", "blink"]);
  const GESTURES = new Set(["wave", "raiseBoth", "shrug", "leanIn", "tilt", "point", "peek", "slump"]);
  const d = ambient.degraded || {};
  ok(!d.face || FACES.has(d.face), `the degraded line has an unknown face: ${d.face}`);
  ok(!d.gesture || GESTURES.has(d.gesture), `the degraded line has an unknown gesture: ${d.gesture}`);
  ok(!d.heart || /^#[0-9a-fA-F]{6}$/.test(d.heart), `the degraded line has a bad heart colour: ${d.heart}`);
}

/** A stub browser just big enough to run the real `ambient.js`. Returns what it heard. */
async function runAmbient(script) {
  const g = globalThis;
  const savedKeys = ["window", "document", "fetch", "CustomEvent"];
  const saved = Object.fromEntries(savedKeys.map((k) => [k, g[k]]));

  const said = [];             // [text, group] handed to moxieAudio.speak
  const bubbles = [];          // setSpeech
  const faces = [];
  const winListeners = {};
  const docListeners = {};
  const modeListeners = [];
  let snap = { state: "boot" };
  const idle = { checked: script.liveness !== false, addEventListener() {} };

  const fire = (reg, ev) => (reg[ev] || []).slice().forEach((fn) => { try { fn({ type: ev }); } catch {} });

  g.CustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };
  g.window = {
    addEventListener: (ev, cb) => { (winListeners[ev] ||= []).push(cb); },
    removeEventListener: () => {},
    moxie: {
      setFace: (f) => faces.push(f), setHeartLED() {}, showIcons() {},
      setMotor() {}, setSpeech: (t) => bubbles.push(t), centerAll() {},
    },
    moxieAudio: {
      speak: (t, group) => { said.push([t, group]); return Promise.resolve(true); },
      isUnlocked: () => script.unlocked !== false,
    },
    moxieMode: {
      state: () => snap.state,
      onChange(fn) {
        modeListeners.push(fn);
        try { fn(snap); } catch {}
        return () => { const i = modeListeners.indexOf(fn); if (i !== -1) modeListeners.splice(i, 1); };
      },
    },
  };
  g.document = {
    hidden: !!script.hidden,
    getElementById: (id) => (id === "idle-on" ? idle : null),
    addEventListener: (ev, cb) => { (docListeners[ev] ||= []).push(cb); },
  };
  g.fetch = (url) => (String(url) === "ambient.json"
    ? Promise.resolve({ ok: true, json: () => Promise.resolve(ambient) })
    : Promise.resolve({ ok: false, json: () => Promise.resolve(null) }));

  new Function(ambientSrc)();
  const api = g.window.moxieAmbient;
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const t = {
    said, bubbles, faces, api,
    heard: () => said.filter((s) => s[0] === degradedText),
    setMode: async (state) => {
      snap = { state };
      modeListeners.slice().forEach((fn) => { try { fn(snap); } catch {} });
      await settle(); await settle();
    },
    unlock: async () => {
      g.window.moxieAudio.isUnlocked = () => true;
      fire(winListeners, "moxie-audio-unlocked");
      await settle(); await settle();
    },
    show: async () => {
      g.document.hidden = false;
      fire(docListeners, "visibilitychange");
      await settle(); await settle();
    },
    state: () => api.degradedState(),
  };
  await settle();
  await script.run(t);
  try { api.stop(); } catch {}                       // release ambient.js's own timers
  for (const k of savedKeys) g[k] = saved[k];
  return t;
}

{
  // (a) boot -> degraded says it, once, in the `moxie` group, with the right face — and
  //     (b) never again: not on a recovery, not on a second failure, not ever.
  await runAmbient({ run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 1, "entering `degraded` must say the degraded line exactly once");
    eq(t.heard().length ? t.heard()[0][1] : null, "moxie",
       "the degraded line must be spoken from the `moxie` clip group (playClip never falls back to `ambient`)");
    ok(t.bubbles.includes(degradedText), "…and it must reach the speech bubble, not only the speakers");
    ok(t.faces.includes((ambient.degraded || {}).face), "…with the face ambient.json asked for");
    eq(t.state().said, true, "the once-only latch must be set after it is spoken");

    await t.setMode("live");
    await t.setMode("degraded");
    await t.setMode("degraded");
    eq(t.heard().length, 1,
       "the degraded line must NEVER be said a second time (§6.2: 'spoken once … never repeated')");
  }});

  // (c) `offline` must stay byte-identical to today's page: no new line, ever. This is
  //     §6.3's promise that a fork with no Functions cannot be regressed by any of this.
  await runAmbient({ run: async (t) => {
    await t.setMode("offline");
    await t.setMode("offline");
    eq(t.heard().length, 0,
       "`offline` must NOT say the degraded line — §6.3 promises that page is unchanged");
  }});

  // (d) autoplay still locked: armed, not lost, and it lands on the unlock.
  await runAmbient({ unlocked: false, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "nothing may be spoken while the browser's autoplay lock is still on");
    eq(t.state().pending, true, "…but the line must be ARMED rather than dropped");
    await t.unlock();
    eq(t.heard().length, 1, "the armed degraded line must land the moment audio is unlocked");
  }});

  // (e) a hidden tab is not talked at, and the line survives until it is looked at.
  await runAmbient({ hidden: true, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "a hidden tab must not be spoken to");
    eq(t.state().pending, true, "…and the line must still be armed");
    await t.show();
    eq(t.heard().length, 1, "the armed line must land when the tab becomes visible");
  }});

  // (f) the liveness toggle is respected — unticking it means "stop talking to yourself".
  await runAmbient({ liveness: false, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "a visitor who unticked liveness has asked for quiet, degraded or not");
  }});
}

/* --------------------------------------------------------------------------- *
 * 8. §6.2 row 4 — the 1.4 s Piper probe is skipped when degraded, and ONLY then
 *
 * The real `audio.js` is loaded under a stubbed window and asked to speak a line with no
 * clip. What is asserted is whether a request to the sidecar port actually left the page.
 * --------------------------------------------------------------------------- */
async function probeFired(modeState, opts = {}) {
  const g = globalThis;
  const savedKeys = ["window", "document", "localStorage", "location", "fetch",
                     "CustomEvent", "requestAnimationFrame", "cancelAnimationFrame"];
  const saved = Object.fromEntries(savedKeys.map((k) => [k, g[k]]));
  const urls = [];

  g.CustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };
  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    moxieMode: modeState ? { state: () => modeState } : undefined,
  };
  g.document = { getElementById: () => null, body: { classList: { toggle() {} } } };
  g.localStorage = {
    getItem: (k) => (k === "moxie.ttsBase" ? (opts.ttsBase || null) : null),
    setItem() {},
  };
  g.location = { protocol: "https:", hostname: "moxie.example" };
  g.fetch = (url) => {
    urls.push(String(url));
    if (String(url).endsWith("audio/index.json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ moxie: {}, child: {}, ambient: {} }) });
    return Promise.reject(new Error("nothing is listening"));
  };

  new Function(audioSrc)();
  await g.window.moxieAudio.speak("a line that no manifest anywhere has a clip for");
  for (const k of savedKeys) g[k] = saved[k];
  return urls.some((u) => u.includes(":8081"));
}

{
  eq(await probeFired("degraded"), false,
     "`degraded` must go clip -> browser voice DIRECTLY: the 1.4 s probe is dead air on a deployment " +
     "that answered /api/health and therefore has no Piper sidecar (§6.2 row 4)");
  eq(await probeFired("offline"), true,
     "`offline` must KEEP the probe — that is exactly what a self-hoster running sim/serve.py gets, " +
     "and their local Piper on :8081 is the whole reason it exists");
  eq(await probeFired("live"), true,
     "`live` keeps the probe too — that path is only reached when the gateway voice did not arrive");
  eq(await probeFired(null), true,
     "with no mode machine at all (audio.js loaded standalone) the probe must still run");
  eq(await probeFired("degraded", { ttsBase: "http://127.0.0.1:8081" }), true,
     "an explicit moxie.ttsBase beats the mode — somebody who typed a TTS address asked for the probe");
  ok(/skipProbe/.test(audioSrc) && /moxieMode/.test(audioSrc),
     "the skip must be gated on window.moxieMode, not on a hostname regex");
}

/* --------------------------------------------------------------------------- *
 * 9. The renderer cannot silently drop a manifest group again
 *
 * §1 guards the artefact; this guards the tool that writes it. The merge used to name
 * `moxie` and `child` explicitly, so any group it did not know about was erased on the
 * next write. It must be group-agnostic.
 * --------------------------------------------------------------------------- */
{
  const toolPath = join(here, "tools", "prerender_audio.py");
  ok(existsSync(toolPath), "sim/tools/prerender_audio.py must exist");
  const tool = readFileSync(toolPath, "utf8");
  const i = tool.indexOf("idx_path = os.path.join");
  const merge = i === -1 ? "" : tool.slice(i, tool.indexOf("total = 0", i));
  ok(merge.length > 0, "prerender_audio.py must still merge an existing manifest before writing it");
  ok(/\.items\(\)/.test(merge),
     "prerender_audio.py's manifest merge must iterate the groups it FINDS — naming them one by one " +
     "silently erased the whole `ambient` group (56 clips) on any run that did not pass --ambient");
  ok(!/cur\.get\("ambient"/.test(merge),
     "…and it must not need to know a group's name to keep it");
}

/* --------------------------------------------------------------------------- */
notes.push(`inventory: ${inventory.length} utterable line(s), ALL pre-rendered — ` +
           `${counts["stub.js reply"]} stub · ${counts["filler.py thinking line"]} filler · ` +
           `${counts["ambient.json quip"]} ambient · ${counts["ambient.json degraded line"]} degraded · ` +
           `${counts.session} session`);
notes.push(`degraded line: ${JSON.stringify(degradedText)} — once on entering degraded, never repeated`);
notes.push(`manifest: ${clipCount} clips, ${(clipBytes / 1024 / 1024).toFixed(2)} MiB on disk ` +
           `(${Object.keys(manifest.moxie || {}).length} moxie / ${Object.keys(manifest.child || {}).length} child / ` +
           `${Object.keys(manifest.ambient || {}).length} ambient)`);
notes.push("piper probe: skipped in `degraded`; kept in `offline`, `live`, standalone, and with an explicit ttsBase");

if (fails.length) {
  console.error(`✗ test_fallback_coverage: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_fallback_coverage: ${asserts} assertions`);
for (const n of notes) console.log("  " + n);
