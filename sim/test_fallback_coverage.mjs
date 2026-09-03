/* test_fallback_coverage.mjs — the degraded page has a real voice for the lines it plays.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §8.1 test 6, §6.1 (what is reused
 * as-is), §6.2 (the new content P1 owes), §2.4 (the fallback assets that already exist).
 *
 * WHY THIS FILE EXISTS. When the live brain is unreachable — unconfigured, over budget, at
 * capacity, rate-limited, or simply down — the hosted page answers from `stub.js` and
 * speaks from `sim/web/audio/index.json`. That fallback is the whole reason P0 can ship a
 * public demo at all, and it is made of two things that can silently drift apart: a text
 * string in a JSON file, and an MP3 on disk keyed by that EXACT string. Change the
 * punctuation of a line and the clip is orphaned — no error, no test failure, just a
 * different, non-Moxie browser voice and a 1.4-second stall while `audio.js` gives up on
 * its Piper probe (`audio.js`:177-183).
 *
 * This is the shape of `sim/test_ambient.mjs`:29-39, applied to the other fallback assets.
 *
 * ============================================================================
 * WHAT P0 COVERS, AND WHAT IT DELIBERATELY DOES NOT.
 *
 * **P0 covers the SESSIONS** — every Moxie line in `sim/web/sessions/*.json` must have an
 * `audio/index.json` entry whose file exists on disk. That passes today, so landing it now
 * locks in a property the repo already has.
 *
 * **P0 does NOT require a clip for `stub.js`'s SCRIPT + FALLBACK lines.** §6.2 records the
 * honest reason: nine of the eleven stub replies have no clip, and producing them needs
 * `piper` + `ffmpeg` locally. §9 puts the clips in P1 "in the same commit as the clips —
 * landing it earlier just paints the build red". So the stub lines are MEASURED and
 * REPORTED here rather than asserted, and the number is printed on every run so the P1
 * commit that fixes it can flip one constant (`REQUIRE_STUB_CLIPS`) and get a real guard.
 * A red build that everyone learns to ignore is worse than an honest number.
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

/** Flip to true in the SAME commit that adds the stub/filler clips (§6.2, P1). */
const REQUIRE_STUB_CLIPS = false;

/* --------------------------------------------------------------------------- *
 * The manifest
 * --------------------------------------------------------------------------- */
const manifest = JSON.parse(readFileSync(join(audioDir, "index.json"), "utf8"));
ok(manifest && typeof manifest === "object", "audio/index.json must be an object");
for (const group of ["moxie", "child", "ambient"]) {
  ok(manifest[group] && typeof manifest[group] === "object", `audio/index.json must have a ${group} group`);
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
      ok(size > 0, `${group}: clip file is EMPTY: ${rel}`);
    }
    // The key is the EXACT utterance string `audio.js::speak` looks up, so a leading or
    // trailing space is an orphaned clip that nothing can ever match.
    eq(phrase, phrase.trim(), `${group}: a manifest key has surrounding whitespace: ${JSON.stringify(phrase)}`);
  }
}

/* --------------------------------------------------------------------------- *
 * P0's assertion: every Moxie line in every recorded session has a clip
 * --------------------------------------------------------------------------- */
const sessionsDir = join(web, "sessions");
const sessionFiles = readdirSync(sessionsDir).filter((f) => f.endsWith(".json"));
ok(sessionFiles.length > 0, "there must be at least one recorded session");

let sessionLines = 0;
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
    if (!text) continue;
    sessionLines++;
    const rel = manifest.moxie && manifest.moxie[text];
    ok(!!rel, `no pre-cached clip for a session line — run sim/tools/prerender_audio.py: ` +
              `${JSON.stringify(text.slice(0, 56))} (${file}[${i}])`);
    if (rel) ok(existsSync(join(audioDir, rel)), `session clip file missing: ${rel}`);
  }
}
ok(sessionLines > 0, "the sessions must contain at least one spoken Moxie line");

/* --------------------------------------------------------------------------- *
 * §6.1 — the fallback's parts are all present and wired
 * --------------------------------------------------------------------------- */
{
  const html = readFileSync(join(web, "sim.html"), "utf8");
  // The four files a degraded turn actually needs, in the order sim.html must load them:
  // stub.js publishes the offline brain, bridge.js consumes it, mode.js decides which
  // mode we are in, cloud-transport.js delegates to bridge.js when it is not `live`.
  for (const f of ["stub.js", "bridge.js", "mode.js", "cloud-transport.js", "audio.js", "ambient.js"]) {
    ok(html.includes(f), `sim.html must load ${f} — the fallback is not wired without it`);
    ok(existsSync(join(web, f)), `${f} must exist`);
  }
  ok(html.indexOf("stub.js") < html.indexOf("bridge.js"), "stub.js loads before bridge.js");
  ok(html.indexOf("bridge.js") < html.indexOf("cloud-transport.js"),
     "cloud-transport.js loads after bridge.js (it wraps what bridge.js published)");

  // `stub.js` must still be ENABLED. `bridge.js`:693 and `cloud-transport.js` both gate
  // the degraded answer on `window.moxieStub.enabled`, so a `false` here would turn every
  // refusal into dead air — the exact failure mode this contract exists to prevent (§4.5).
  const stubSrc = readFileSync(join(web, "stub.js"), "utf8");
  ok(/enabled:\s*true/.test(stubSrc), "window.moxieStub.enabled must be TRUE or a refused turn is silent");
  ok(stubSrc.includes("window.moxieStub"), "stub.js must publish window.moxieStub");

  // The transport must delegate to the inner bridge rather than answer for itself when the
  // mode is not live — §3.5's guarantee that today's page cannot regress.
  const transportSrc = readFileSync(join(web, "cloud-transport.js"), "utf8");
  ok(transportSrc.includes("inner.sendUserTurn"), "cloud-transport.js must delegate to inner.sendUserTurn");
  ok(transportSrc.includes("window.moxieStub"), "…and must be able to answer one turn from the stub itself");
}

/* --------------------------------------------------------------------------- *
 * §6.2 — the stub repertoire, MEASURED (see the header for why it is not asserted)
 * --------------------------------------------------------------------------- */
{
  const stubSrc = readFileSync(join(web, "stub.js"), "utf8");
  // Pull the `say:` strings straight out of the source. Both SCRIPT and FALLBACK entries
  // use the same key, and these are the exact strings `bridge.js` passes to `speak()`.
  const lines = [...stubSrc.matchAll(/say:\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1].replace(/\\"/g, '"'));
  ok(lines.length >= 11, `stub.js should carry at least 11 replies, found ${lines.length}`);

  const missing = lines.filter((t) => !(manifest.moxie && manifest.moxie[t.trim()]));
  const covered = lines.length - missing.length;
  notes.push(`stub.js replies with a pre-rendered clip: ${covered}/${lines.length}` +
             (missing.length ? ` — ${missing.length} still speak in a browser voice (§6.2, P1)` : ""));
  for (const t of missing) {
    notes.push(`  · no clip: ${JSON.stringify(t.slice(0, 60))}`);
    if (REQUIRE_STUB_CLIPS) {
      fails.push(`no pre-cached clip for a stub reply: ${JSON.stringify(t.slice(0, 56))}`);
      asserts++;
    }
  }
  // The two lines that DO have clips are the birthday pair, and they must keep them:
  // they are what makes the shipped `sessions/demo.json` replay in Moxie's own voice.
  const birthday = lines.filter((t) => /birthday/i.test(t));
  ok(birthday.length > 0, "stub.js must still answer a birthday");
  for (const t of birthday) {
    ok(!!(manifest.moxie && manifest.moxie[t.trim()]),
       `the birthday stub reply must keep its clip (it is the shipped demo's voice): ${JSON.stringify(t.slice(0, 40))}`);
  }

  // The markup every stub reply carries must be the three families `applyMarkup` parses,
  // or a degraded turn renders words with a dead face — which reads as broken, not
  // degraded. This is the fallback's *visual* half.
  ok(stubSrc.includes("cmd:playback-mood"), "stub replies carry a mood mark");
  ok(stubSrc.includes("+eventName+:+"), "…and a gesture eventName");
  ok(stubSrc.includes("cmd:icons-v2"), "…and can carry an icon mark");
}

/* --------------------------------------------------------------------------- *
 * §6.2 — the filler lines, likewise measured (P1 owes them clips too)
 * --------------------------------------------------------------------------- */
{
  const fillerPath = join(here, "..", "mqtt", "moxie_sdk", "filler.py");
  if (existsSync(fillerPath)) {
    const src = readFileSync(fillerPath, "utf8");
    const block = src.slice(src.indexOf("_LINES"));
    const lines = [...block.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1])
      .filter((t) => t.length > 8 && /[a-z]/.test(t) && !t.includes("\\n"));
    const missing = lines.filter((t) => !(manifest.moxie && manifest.moxie[t.trim()]));
    notes.push(`filler.py thinking lines with a clip: ${lines.length - missing.length}/${lines.length}` +
               (missing.length ? " (§6.2, P1)" : ""));
  } else {
    notes.push("mqtt/moxie_sdk/filler.py not found — skipped");
  }
}

/* --------------------------------------------------------------------------- *
 * §2.4 — the ambient layer is server-free, and stays that way
 * --------------------------------------------------------------------------- */
{
  const ambient = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8"));
  ok(Array.isArray(ambient.lines) && ambient.lines.length > 0, "ambient.json must carry lines");
  // Covered line-by-line by sim/test_ambient.mjs; asserted here only as a COUNT, so that a
  // change which quietly empties the ambient layer fails the fallback test too. "Alive
  // while idle" is what stops a degraded page from feeling dead.
  eq(Object.keys(manifest.ambient || {}).length >= ambient.lines.length, true,
     `every ambient line needs a clip: ${ambient.lines.length} lines vs ` +
     `${Object.keys(manifest.ambient || {}).length} clips`);
  // "Server-free" means it needs no BACKEND, not that it makes no request: it fetches its
  // own committed `ambient.json`, which is a static asset served by the CDN like any
  // other. (Rule 17: the first version of this guard banned `fetch` outright and fired on
  // that line — the GUARD was wrong, not the code.) What must never appear is an /api/
  // path, an absolute URL or a port: those would make the one layer that works in every
  // mode depend on something that might not be there.
  const ambientSrc = readFileSync(join(web, "ambient.js"), "utf8");
  const ambientFetches = [...ambientSrc.matchAll(/fetch\s*\(\s*"([^"]*)"/g)].map((m) => m[1]);
  ok(ambientFetches.length > 0, "ambient.js loads its own line list");
  for (const url of ambientFetches) {
    ok(!/^[a-z]+:\/\//i.test(url), `ambient.js must not fetch an absolute URL: ${url}`);
    ok(!url.startsWith("/api/") && !url.includes(":80") && !url.includes(":90"),
       `ambient.js must not depend on a backend: ${url}`);
  }
  notes.push(`ambient self-talk lines: ${ambient.lines.length}, all pre-rendered`);
}

/* --------------------------------------------------------------------------- */
notes.push(`manifest: ${clipCount} clips, ${(clipBytes / 1024 / 1024).toFixed(2)} MiB on disk`);
notes.push(`sessions: ${sessionFiles.length} file(s), ${sessionLines} spoken Moxie line(s), all pre-rendered`);

if (fails.length) {
  console.error(`✗ test_fallback_coverage: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_fallback_coverage: ${asserts} assertions`);
for (const n of notes) console.log("  " + n);
