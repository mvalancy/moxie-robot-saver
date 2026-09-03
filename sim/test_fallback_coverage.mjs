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
 * 2. Every line in every recorded session has a clip — BOTH speakers
 *
 * This section used to read the Moxie half only, and that omission was the bug: the two
 * child clips shipped, the manifest listed them, nothing in the page ever asked for them,
 * and no test noticed that half of the shipped conversation was silent. A child line is
 * now inventoried like a Moxie line, but with a STRICTER rule — see `strict` below.
 * --------------------------------------------------------------------------- */
const sessionsDir = join(web, "sessions");
const sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"));
ok(sessionFiles.length > 0, "there must be at least one recorded session");

/* A child utterance rides `/events/remote-chat`. Not every one is a child SPEAKING: a
 * perception event uses the same `speech` slot (`bridge.js::notePresence`) and is Moxie's
 * eye, not a voice, and a `notify` turn is the robot echoing itself. Both are skipped by
 * the handler and must be skipped here too, or the inventory would demand clips for lines
 * nobody can ever hear. */
const PERCEPTION = /^(eb-)?(found|lost)[-_]?(face|target|person)?$/i;
const childSpeech = (msg) => {
  if (!msg || msg.command === "notify") return "";
  let speech = msg.speech || "";
  for (const ln of msg.extra_lines || [])
    if (ln.context_type === "input" && ln.text) speech = ln.text;
  speech = String(speech).trim();
  return PERCEPTION.test(speech) ? "" : speech;
};

const sessionLines = [];
const childSessionLines = [];
const sessions = [];
for (const file of sessionFiles) {
  const events = JSON.parse(readFileSync(join(sessionsDir, file), "utf8"));
  ok(Array.isArray(events), `${file} must be an array of {t, topic, payload} events`);
  if (!Array.isArray(events)) continue;
  sessions.push({ file, events });

  for (const [i, ev] of events.entries()) {
    ok(ev && typeof ev === "object", `${file}[${i}] must be an object`);
    ok(typeof ev.topic === "string" && ev.topic.length > 0, `${file}[${i}] must carry a topic`);
    // `replay()` hands `payload` to `route()`, which calls JSON.parse itself — so a
    // payload that is not a STRING would silently render nothing (`bridge.js`:599-610).
    eq(typeof ev.payload, "string", `${file}[${i}] payload must be a STRING (route() parses it)`);
    ok(Number.isFinite(Number(ev.t)), `${file}[${i}] must carry a numeric t`);

    if (ev.topic.endsWith("/events/remote-chat")) {
      let msg = null;
      try { msg = JSON.parse(ev.payload); } catch {}
      ok(msg !== null, `${file}[${i}] remote-chat payload must be valid JSON`);
      const text = childSpeech(msg);
      if (text) childSessionLines.push({ text, where: `${file}[${i}]`, t: Number(ev.t), i });
      continue;
    }

    if (!ev.topic.endsWith("/commands/remote_chat")) continue;
    let msg = null;
    try { msg = JSON.parse(ev.payload); } catch {}
    ok(msg !== null, `${file}[${i}] remote_chat payload must be valid JSON`);
    const text = ((msg && msg.output && msg.output.text) || "").trim();
    if (text) sessionLines.push({ text, where: `${file}[${i}]` });
  }
}
ok(sessionLines.length > 0, "the sessions must contain at least one spoken Moxie line");
ok(childSessionLines.length > 0,
   "the sessions must contain at least one CHILD line — a demo conversation with only one " +
   "voice in it is the thing this section exists to stop shipping again");

/* --------------------------------------------------------------------------- *
 * 2b. The script leaves the child room to FINISH her line
 *
 * `audio.js::speak` calls `stop()` before Moxie says anything, so the moment Moxie's turn
 * lands the child's clip is cut dead — deliberately (the robot is never talked over by a
 * prop) but destructively if the script does not allow for it. The shipped session was
 * timed when the child was silent: her first clip runs ~2.6 s from t=300 and Moxie
 * answered at t=1800, so giving her a voice would have shipped "…it's my birthd—".
 *
 * Duration is estimated from FILE SIZE rather than decoded, so this guard needs no codec
 * and no ffprobe. `prerender_audio.py` writes mono ~64 kbit MP3 (the shipped clips measure
 * 8.10-8.20 kB/s); dividing by 8000 rounds the estimate UP, which is the safe direction
 * for a "leaves room" assertion.
 * --------------------------------------------------------------------------- */
const MP3_BYTES_PER_SEC = 8000;
// Only these topics make MOXIE speak, and only speaking calls stop(). A config frame or
// another child line does not cut her off.
const MOXIE_SPEAKS = ["/commands/remote_chat", "/commands/tts", "/commands/telehealth"];
{
  let checked = 0;
  for (const { file, events } of sessions) {
    for (const ln of childSessionLines.filter((c) => c.where.startsWith(file + "["))) {
      const rel = (manifest.child || {})[ln.text];
      if (!rel || !existsSync(join(audioDir, rel))) continue;   // silent line: nothing to cut
      const needMs = (statSync(join(audioDir, rel)).size / MP3_BYTES_PER_SEC) * 1000;
      const next = events.find((e, j) => j > ln.i && Number(e.t) > ln.t &&
                                         MOXIE_SPEAKS.some((t) => String(e.topic).endsWith(t)));
      if (!next) continue;                    // nothing follows: she finishes in peace
      const gapMs = Number(next.t) - ln.t;
      checked++;
      ok(gapMs >= needMs,
         `${ln.where}: Moxie speaks ${Math.round(gapMs)} ms after the child starts, but the child's ` +
         `clip needs about ${Math.round(needMs)} ms — speak() calls stop(), so the shipped demo would ` +
         `cut her off mid-word on ${JSON.stringify(ln.text.slice(0, 40))}. Move the reply later.`);
    }
  }
  ok(checked > 0, "no scripted child line was timing-checked — the extractor above stopped finding them");
  notes.push(`session timing: ${checked} scripted child line(s) have room to finish before Moxie answers`);
}

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
 *
 * `strict: true` means NO group fallthrough is acceptable for that line. The child's lines
 * are strict, because `audio.js::speakClipOnly` looks in the `child` group and nowhere
 * else: a child line found in the `moxie` group would be Moxie's voice saying the child's
 * words, which is not "covered", it is wrong.
 * --------------------------------------------------------------------------- */
const inventory = [];
for (const t of stubReplies()) inventory.push({ text: t, group: "moxie", source: "stub.js reply" });
for (const t of filler.texts) inventory.push({ text: t, group: "moxie", source: "filler.py thinking line" });
for (const ln of ambient.lines) inventory.push({ text: (ln.text || "").trim(), group: "ambient", source: "ambient.json quip" });
for (const s of sessionLines) inventory.push({ text: s.text, group: "moxie", source: `session ${s.where}` });
for (const s of childSessionLines)
  inventory.push({ text: s.text, group: "child", strict: true, source: `session-child ${s.where}` });
if (ambient.degraded) {
  inventory.push({ text: (ambient.degraded.text || "").trim(), group: "moxie", source: "ambient.json degraded line" });
}

const counts = { "stub.js reply": 0, "filler.py thinking line": 0, "ambient.json quip": 0,
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
 * 8b. The CHILD's voice — clip, or nothing, driven for real
 *
 * The child's two lines in `sessions/demo.json` were mute for the whole life of this
 * repo: `bridge.js::handleUserTurn` wrote the transcript row and stopped, and nothing
 * anywhere passed `who === "child"` to `audio.js`. Half of the shipped demo conversation
 * had no sound in it.
 *
 * Making it audible is not "call speak()". The SAME handler carries whatever a visitor
 * typed into the Talk box or said into the microphone, and `speak()` guarantees sound —
 * clip -> Piper -> the browser voice — so it would read a visitor's own sentence back at
 * them in a stranger's voice, and on the mic path talk over them. `speakClipOnly` is the
 * entry point with no route to a synthesizer at all.
 *
 * §8 above proved the Piper probe by watching the wire; this proves the child's voice the
 * same way — the REAL `audio.js` under a stubbed window, asserting which URLs were
 * fetched, which buffers were STARTED and STOPPED, whether the mouth moved and whether
 * anything reached speechSynthesis. Not a grep for a function name.
 * --------------------------------------------------------------------------- */

/** Boot the real audio.js against a fake Web Audio stack and report what it did. */
async function voiceRig(run, manifestOverride) {
  const g = globalThis;
  const savedKeys = ["window", "document", "localStorage", "location", "fetch", "CustomEvent",
                     "requestAnimationFrame", "cancelAnimationFrame", "AudioContext",
                     "SpeechSynthesisUtterance"];
  const saved = Object.fromEntries(savedKeys.map((k) => [k, g[k]]));

  const log = { urls: [], started: [], stopped: [], mouth: [], synthesized: [] };
  // Each URL gets its own byteLength, so a decoded buffer can be traced back to the file
  // it came from without assuming anything about call ordering.
  const byLen = new Map();
  let nextLen = 64;
  const bufFor = (url) => {
    if (![...byLen.entries()].some(([, u]) => u === url)) { byLen.set((nextLen += 8), url); }
    const len = [...byLen.entries()].find(([, u]) => u === url)[0];
    return new ArrayBuffer(len);
  };

  class Src {
    constructor() { this.onended = null; this.buffer = null; }
    connect() {}
    start() { log.started.push((this.buffer && this.buffer.url) || "?"); }
    stop() { log.stopped.push((this.buffer && this.buffer.url) || "?"); }
  }
  class Ctx {
    constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
    resume() {}
    createBufferSource() { return new Src(); }
    createAnalyser() {
      return { fftSize: 256, frequencyBinCount: 8, connect() {}, getByteTimeDomainData() {} };
    }
    decodeAudioData(buf) { return Promise.resolve({ url: byLen.get(buf.byteLength) || "?" }); }
    createOscillator() {
      return { type: "", frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} };
    }
    createGain() {
      return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} };
    }
  }

  // Never actually re-enters: `pump()` is called once and its next frame is dropped, so a
  // clip that DOES drive the mouth records exactly one sample and a clip that does not
  // records none. That is the whole assertion for `opts.mouth:false`.
  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.CustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };
  g.AudioContext = Ctx;
  g.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    AudioContext: Ctx,
    moxie: { setMouthOpen: (v) => log.mouth.push(v) },
    /* Records the utterance and stops there. `u.onstart()` is deliberately NOT fired:
     * `speakBrowser` starts a `setInterval` there to wobble the mouth, and this rig tears
     * its globals down synchronously — a timer surviving that fires against a `window`
     * that no longer exists and crashes the whole run several tests later. */
    speechSynthesis: { cancel() {}, getVoices: () => [], speak: (u) => log.synthesized.push(u.text) },
  };
  g.document = { getElementById: () => null, body: { classList: { toggle() {} } } };
  g.localStorage = { getItem: () => null, setItem() {} };
  g.location = { protocol: "https:", hostname: "moxie.example" };
  g.fetch = (url) => {
    url = String(url);
    log.urls.push(url);
    if (url.endsWith("audio/index.json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(manifestOverride || RIG_MANIFEST) });
    if (url.startsWith("audio/"))
      return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(bufFor(url)) });
    return Promise.reject(new Error("nothing is listening"));   // :8081 and anything else
  };

  new Function(audioSrc)();
  try { await run(g.window.moxieAudio, log); }
  finally { for (const k of savedKeys) g[k] = saved[k]; }
  return log;
}

// A miniature manifest with one line per interesting case.
const RIG_MANIFEST = {
  moxie: { "Happy birthday!": "moxie/m1.mp3", "Only Moxie has this one.": "moxie/m2.mp3" },
  child: { "Guess what, it's my birthday today!": "child/c1.mp3", "Thank you Moxie!": "child/c2.mp3" },
  ambient: {},
};
const C1 = "audio/child/c1.mp3", C2 = "audio/child/c2.mp3", M1 = "audio/moxie/m1.mp3";
const probed = (log) => log.urls.some((u) => u.includes(":8081"));

{
  // (a) a scripted child line PLAYS — from the child group, and from nothing else.
  await voiceRig(async (audio, log) => {
    const ok1 = await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    eq(ok1, true, "a scripted child line with a clip must actually play — this is the whole feature");
    ok(log.started.includes(C1),
       `…from the child clip; started ${JSON.stringify(log.started)}`);
    eq(log.mouth.length, 0,
       "the child's clip must NOT drive Moxie's mouth — a robot lip-syncing the child's words " +
       "is a visibly broken toy (playUrl opts.mouth:false)");
    eq(log.synthesized.length, 0, "a child line must never reach speechSynthesis");
    eq(probed(log), false, "a child line must never probe the Piper sidecar");
  });

  // (b) NO clip -> silence. Not Piper, not the browser voice, not a tone. This is the trap
  //     the whole design exists for: a visitor's own words come through this same call.
  await voiceRig(async (audio, log) => {
    const said = await audio.speakClipOnly("is my mum going to be ok", "child");
    eq(said, false, "a child line with no clip must report that it made no sound");
    eq(log.started.length, 0, "…and must start no audio at all");
    eq(log.synthesized.length, 0,
       "a child line with no clip must NEVER be synthesized — that reads the visitor's own " +
       "sentence back at them in a stranger's voice, which is worse than the silence we started with");
    eq(probed(log), false,
       "…and must not even ask Piper: there is no fallback chain out of speakClipOnly, by construction");
  });

  // (c) the SAME text through `speak()` DOES make sound. Without this, (b) could pass on a
  //     rig where nothing can make sound at all, and would prove nothing.
  await voiceRig(async (audio, log) => {
    await audio.speak("is my mum going to be ok");
    ok(log.synthesized.length > 0 || probed(log),
       "control: speak() must still fall through to a synthesizer for an uncached line — " +
       "otherwise the no-fallback assertions above are vacuous");
  });

  // (d) strict group. A text cached only in the `moxie` group is NOT the child's voice.
  //     `playClip` deliberately falls through moxie -> child; `speakClipOnly` must not.
  await voiceRig(async (audio, log) => {
    const said = await audio.speakClipOnly("Only Moxie has this one.", "child");
    eq(said, false,
       "speakClipOnly must look in the named group and NOWHERE else — falling through to " +
       "`moxie` would answer a child line with a clip of Moxie's voice saying the child's words");
    eq(log.started.length, 0, "…and start nothing");
  });

  // (e) ORDERING, half one: the child never cuts Moxie off. A visitor typing while Moxie
  //     answers must not be able to silence her.
  await voiceRig(async (audio, log) => {
    await audio.speak("Happy birthday!");
    eq(log.started.length, 1, "precondition: Moxie's clip is playing");
    const said = await audio.speakClipOnly("Thank you Moxie!", "child");
    eq(said, false, "a child line must not start while Moxie is speaking");
    eq(log.stopped.length, 0,
       "…and must not stop her: the robot is the subject of the page and is never talked over by a prop");
    eq(log.started.length, 1, "…so no second source was started");
  });

  // (f) ORDERING, half two: Moxie DOES cut the child. `speak()` calls `stop()` first, and
  //     that stays true — it is why §2b has to time the shipped session.
  await voiceRig(async (audio, log) => {
    await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    eq(log.started.length, 1, "precondition: the child's clip is playing");
    await audio.speak("Happy birthday!");
    ok(log.stopped.includes(C1),
       `Moxie starting to speak must stop the child's clip; stopped ${JSON.stringify(log.stopped)}`);
    ok(log.started.includes(M1), "…and Moxie's own clip must play");
  });

  // (g) a newer child line replaces an older one rather than layering over it.
  await voiceRig(async (audio, log) => {
    await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    await audio.speakClipOnly("Thank you Moxie!", "child");
    ok(log.stopped.includes(C1), `the older child clip must be stopped; got ${JSON.stringify(log.stopped)}`);
    ok(log.started.includes(C2), `…and the newer one started; got ${JSON.stringify(log.started)}`);
  });

  // (h) structural, not conditional: there is no synthesizer reachable from the function.
  //     A future `speakClipOnly` that grew a `speakBrowser`/`speakLive` call — or that was
  //     quietly re-pointed at `speak()` — is exactly the loosening the separate entry point
  //     exists to prevent, and the behavioural cases above only catch it for lines the rig
  //     happens to try.
  const body = audioSrc.slice(audioSrc.indexOf("function speakClipOnly"));
  const fnEnd = body.indexOf("\n  }\n");
  const clipOnlyBody = fnEnd === -1 ? body : body.slice(0, fnEnd);
  ok(clipOnlyBody.length > 0, "speakClipOnly must exist in audio.js");
  for (const forbidden of ["speakBrowser", "speakLive", "speechSynthesis", "sfx(", "speak("])
    ok(!clipOnlyBody.includes(forbidden),
       `speakClipOnly's body must not mention ${forbidden} — the no-fallback guarantee is meant to be ` +
       `a property of WHICH FUNCTION you called, not a condition someone can loosen`);
  ok(/window\.moxieAudio\s*=\s*\{[\s\S]{0,400}speakClipOnly/.test(audioSrc),
     "speakClipOnly must be exported on window.moxieAudio — bridge.js calls it by name");

  // (i) …and the caller really is `handleUserTurn`, with the child group named.
  const bridgeSrc = readFileSync(join(web, "bridge.js"), "utf8");
  const turn = bridgeSrc.slice(bridgeSrc.indexOf("function handleUserTurn"));
  const turnBody = turn.slice(0, turn.indexOf("\n  }\n"));
  ok(/speakClipOnly\(\s*speech\s*,\s*"child"\s*\)/.test(turnBody),
     "bridge.js::handleUserTurn must speak the child's line through speakClipOnly(speech, \"child\")");
  ok(!/moxieAudio\.speak\(/.test(turnBody),
     "handleUserTurn must NEVER call speak() — that is the path that synthesizes a visitor's own words");

  notes.push("child voice: clip-only — scripted lines play from the `child` group, uncached lines " +
             "(a visitor's own words) make no sound at all; child yields, Moxie interrupts");
}

/* --------------------------------------------------------------------------- *
 * 8c. End to end, on the REAL assets
 *
 * §8b proves the rule against a hand-written manifest; §2 proves the shipped strings
 * against the shipped manifest. Neither one proves they meet. This boots the REAL
 * `bridge.js` AND the REAL `audio.js` together against the REAL `audio/index.json`,
 * `audio/child/*.mp3` and `sessions/demo.json`, routes the demo's own child events through
 * `route()` exactly as `replay()` does, and asserts the actual MP3 the site ships is the
 * URL that goes out — so a renamed clip, a re-punctuated line or a manifest rewrite is
 * caught by the same test that proves the mechanism.
 * --------------------------------------------------------------------------- */
{
  const g = globalThis;
  const savedKeys = ["window", "document", "localStorage", "location", "fetch", "CustomEvent",
                     "requestAnimationFrame", "cancelAnimationFrame", "AudioContext",
                     "SpeechSynthesisUtterance", "mqtt"];
  const saved = Object.fromEntries(savedKeys.map((k) => [k, g[k]]));
  const urls = [], started = [], mouth = [], synthesized = [];
  const byLen = new Map();

  class Src {
    constructor() { this.onended = null; this.buffer = null; }
    connect() {} start() { started.push((this.buffer && this.buffer.url) || "?"); } stop() {}
  }
  class Ctx {
    constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
    resume() {}
    createBufferSource() { return new Src(); }
    createAnalyser() { return { fftSize: 256, frequencyBinCount: 8, connect() {}, getByteTimeDomainData() {} }; }
    decodeAudioData(buf) { return Promise.resolve({ url: byLen.get(buf.byteLength) || "?" }); }
    createOscillator() { return { type: "", frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  }

  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.CustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };
  g.AudioContext = Ctx;
  g.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
  const el = () => ({ value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0,
                      scrollHeight: 0, addEventListener() {}, appendChild() {},
                      querySelector: () => ({ set textContent(v) {} }), classList: { toggle() {} } });
  g.document = { getElementById: () => el(), createElement: () => el(), body: el() };
  g.localStorage = { getItem: () => null, setItem() {} };
  g.location = { protocol: "https:", hostname: "moxie.example" };
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    AudioContext: Ctx,
    // A minimal avatar: enough for bridge.js to render, and it RECORDS every mouth call.
    moxie: { setFace() {}, setSpeech() {}, setMotor() {}, getMotor: () => 16384, showIcons() {},
             clearIcons() {}, setHeartLED() {}, setMouthOpen: (v) => mouth.push(v) },
    speechSynthesis: { cancel() {}, getVoices: () => [], speak: (u) => synthesized.push(u.text) },
  };
  g.mqtt = { connect: () => { throw new Error("this test never goes on the bus"); } };
  // Serves the site's real files, so the URLs asserted below are the URLs that ship.
  g.fetch = (url) => {
    url = String(url);
    urls.push(url);
    const onDisk = join(web, url);
    if (!url.startsWith("audio/") || !existsSync(onDisk))
      return Promise.reject(new Error("not served: " + url));
    if (url.endsWith(".json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(readFileSync(onDisk, "utf8"))) });
    const bytes = readFileSync(onDisk);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    byLen.set(ab.byteLength, url);
    return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(ab) });
  };

  new Function(audioSrc)();                                    // the real audio.js
  new Function(readFileSync(join(web, "bridge.js"), "utf8"))(); // the real bridge.js
  ok(!!g.window.moxieBridge, "bridge.js must expose window.moxieBridge");

  // The demo's own child events, routed exactly as replay() routes them.
  const demo = JSON.parse(readFileSync(join(sessionsDir, "demo.json"), "utf8"));
  const childEvents = demo.filter((e) => String(e.topic).endsWith("/events/remote-chat"));
  eq(childEvents.length, 2, "sessions/demo.json must still carry the two child turns");

  for (const ev of childEvents) {
    started.length = 0;
    g.window.moxieBridge.route(ev.topic, ev.payload);
    // speakClipOnly is async (manifest fetch -> clip fetch -> decode); let it settle.
    for (let i = 0; i < 20 && started.length === 0; i++) await new Promise((r) => setImmediate(r));

    const text = JSON.parse(ev.payload).speech;
    const want = "audio/" + manifest.child[text];
    ok(urls.includes(want),
       `replaying ${JSON.stringify(text.slice(0, 30))} must fetch the shipped clip ${want}; ` +
       `fetched ${JSON.stringify(urls.filter((u) => u.endsWith(".mp3")))}`);
    ok(started.includes(want), `…and actually start it; started ${JSON.stringify(started)}`);
  }
  eq(mouth.length, 0, "replaying the child's turns must never move Moxie's mouth");
  eq(synthesized.length, 0, "…and must never reach speechSynthesis");
  eq(urls.some((u) => u.includes(":8081")), false, "…and must never probe the Piper sidecar");

  // The same route, with something a VISITOR could have typed: silent, end to end.
  const before = urls.length;
  g.window.moxieBridge.route("/devices/d_demo/events/remote-chat",
    JSON.stringify({ command: "prompt", speech: "my hamster died last night" }));
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
  eq(urls.slice(before).filter((u) => u.endsWith(".mp3")).length, 0,
     "a visitor's own words must fetch no audio at all — this is the trap the design exists for");
  eq(synthesized.length, 0, "…and must not be synthesized");

  for (const k of savedKeys) g[k] = saved[k];
  notes.push(`end to end: both demo.json child turns play their shipped MP3 through the real ` +
             `bridge.js + audio.js; an unscripted line fetches nothing`);
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
           `${counts.session} session moxie · ${counts["session-child"]} session child`);
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
