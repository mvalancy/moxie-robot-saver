/* test_bg_perf.mjs — the landing page must not pile up work while nobody is looking.
 *
 * THE DEFECT THIS FILE CLOSES (owner report, 2026-09-04): "leave the main page running in
 * a browser for hours and it gets really sluggish — some kind of leak or garbage pile over
 * time with the background effects."
 *
 * The mechanism, reproduced and measured before it was fixed. In `sim/web/bg.js` the
 * PRODUCERS were two `setInterval`s (`spawnPacket` every 900 ms, a radar ping every
 * 2600 ms) and the only CONSUMER — the `packets.splice` / `pings.splice` that retire an
 * entry — lives inside `step()`, which re-arms through `requestAnimationFrame`. A browser
 * PAUSES rAF in a hidden tab and keeps timers running. So a backgrounded tab filled two
 * arrays that nothing drained, and every entry came due on the frame the visitor returned
 * to: each packet is a `shadowBlur` arc and each ping a stroked circle, redrawn EVERY
 * frame until it retires. Measured in this harness on the pre-fix file: 0 -> 44 packets
 * and 0 -> 25 pings in ONE minute hidden, growing linearly, with rAF confirmed at zero
 * frames throughout.
 *
 * WHY IT IS TESTED THE WAY IT IS.
 *
 * · A REAL hidden tab, not a simulation. `document.hidden` stays false in a lone headless
 *   page, which is exactly how a first attempt at this measurement talked itself into a
 *   result it had not got. Opening a SECOND page and calling `bringToFront()` genuinely
 *   backgrounds the first: `document.hidden` goes true and rAF stops delivering. Every
 *   block that concludes anything from a hidden tab first asserts that both of those
 *   actually happened — `hidden === true` and ZERO frames delivered.
 *
 * · ARRAY LENGTH, not wall-clock. A frame-time assertion is a coin toss on a shared CI
 *   runner. The quantity that actually causes the sluggishness is how many entries are
 *   waiting to be drawn — it is upstream of the frame time, it is an integer, and it is
 *   the same number whether the runner is busy or idle. Counting `shadowBlur` draws per
 *   frame was considered and dropped: on this page that number IS the array length plus a
 *   constant, so it measures the same thing later and less directly.
 *
 * · THE TEETH RUN FIRST. Block 1 rebuilds the OLD producer shape out of the SHIPPED file
 *   (a text transform, never a second copy that could drift) and requires the growth to
 *   REAPPEAR. If it does not, this environment cannot background a tab at all — so the
 *   suite skips green with a loud notice instead of reporting a pass it did not earn.
 *   Every later block runs only on an environment that has just proven it can see the bug.
 *
 * · THE CAP IS TESTED SEPARATELY (block 3), by inflating the rAF timestamp the page sees
 *   and freezing both retire conditions, so nothing can leave the arrays and the ceiling
 *   is the only thing left holding the line. It starts three short of the cap so a pass
 *   must show the arrays GROW and then stop exactly there — a block that merely asserted
 *   `<= cap` would pass just as well on a page that spawns nothing at all.
 *
 * · A MISSING in-frame spawner is a FAILURE, not a skip. The one thing this suite must
 *   never do is stand quietly down on a revert of the very change it guards.
 *
 * `MAX_PACKETS` / `MAX_PINGS` are read out of `sim/web/bg.js`, never restated here — a
 * hard-coded 48 could pass while the shipped file said something else.
 *
 *   node sim/test_bg_perf.mjs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, web } from "./browser_harness.mjs";

const LABEL = "background-effects growth test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

/* ---- the shipped file, and the caps it declares --------------------------- */
const SHIPPED = readFileSync(join(web, "bg.js"), "utf8");
const capOf = (name) => {
  const m = SHIPPED.match(new RegExp(name + "\\s*=\\s*(\\d+)"));
  return m ? parseInt(m[1], 10) : NaN;
};
const MAX_PACKETS = capOf("MAX_PACKETS");
const MAX_PINGS = capOf("MAX_PINGS");
ok(Number.isFinite(MAX_PACKETS), "sim/web/bg.js declares no MAX_PACKETS");
ok(Number.isFinite(MAX_PINGS), "sim/web/bg.js declares no MAX_PINGS");

/* The pre-fix mechanism, derived from the shipped file rather than kept as a copy:
 * drop the in-frame spawner, re-arm the two interval producers. */
const SPAWN_CALL = "if (!reduce) spawn(Math.max(0, Math.min(elapsed, SPAWN_CREDIT_MS)));";
const BOOT = "  requestAnimationFrame(step);\n})();";
const LEGACY_BOOT =
  "  if (!reduce) { setInterval(spawnPacket, 900); setInterval(spawnPing, 2600); }\n" +
  "  requestAnimationFrame(step);\n})();";
/* A FAILURE, never a skip. If the in-frame spawner is gone, either bg.js was refactored
 * (and this transform needs updating) or it was reverted to the timer producers — and a
 * guard that quietly stands down on the second case is no guard. When the shape is
 * missing the "legacy" variant is just the shipped file, so block 1 below measures the
 * shipped file twice and block 2 reports the growth for real. */
const hasShape = SHIPPED.includes(SPAWN_CALL) && SHIPPED.endsWith(BOOT + "\n");
ok(hasShape, "sim/web/bg.js no longer spawns from inside the frame (the `spawn(...)` call " +
             "in step() is gone) — either it was reverted to setInterval producers, which is " +
             "the defect, or it was refactored and this suite's teeth transform needs updating");
const LEGACY = hasShape
  ? SHIPPED.replace(SPAWN_CALL, "").replace(BOOT + "\n", LEGACY_BOOT + "\n")
  : SHIPPED;

/* ---- page instrumentation -------------------------------------------------
 * `packets` and `pings` are closed over inside bg.js's IIFE. They are captured by
 * shape, off a temporary `Array.prototype.push` hook that removes itself the moment
 * both are found — so nothing else on the page pays for it. */
const INSTRUMENT = function () {
  window.__bg = { packets: null, pings: null, inflate: 0 };
  const op = Array.prototype.push;
  Array.prototype.push = function (v) {
    if (arguments.length === 1 && v && typeof v === "object" && !Array.isArray(v)) {
      const k = Object.keys(v).join(",");
      if (k === "a,b,t,sp,c" && !window.__bg.packets) window.__bg.packets = this;
      if (k === "x,y,r,a" && !window.__bg.pings) window.__bg.pings = this;
      if (window.__bg.packets && window.__bg.pings) Array.prototype.push = op;
    }
    return op.apply(this, arguments);
  };
  // rAF timestamps the page sees can be stretched, to drive the spawner harder than
  // any real clock. `inflate` of 0 leaves the browser's own timestamps untouched.
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = function (cb) {
    return raf(function (ts) { return cb(window.__bg.inflate ? ts * window.__bg.inflate : ts); });
  };
  window.__bgLen = () => ({
    packets: window.__bg.packets ? window.__bg.packets.length : -1,
    pings: window.__bg.pings ? window.__bg.pings.length : -1,
    hidden: document.hidden,
  });
  // frames actually delivered over `ms` — 0 proves rAF really was paused
  window.__bgFrames = (ms) => new Promise((res) => {
    let n = 0; const t = () => { n++; raf(t); }; raf(t);
    setTimeout(() => res(n), ms);
  });
};

const site = await serveWeb();
const browser = await puppeteer.launch({
  headless: "new", executablePath: chrome,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

/** Load index.html, optionally serving a rewritten bg.js, and background it for `ms`. */
async function hiddenRun(variant, ms) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.evaluateOnNewDocument(INSTRUMENT);
  if (variant) {
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.url().endsWith("/bg.js")) r.respond({ status: 200, contentType: "text/javascript", body: variant });
      else r.continue();
    });
  }
  await page.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
  /* WAIT for both arrays to exist rather than sleeping a fixed beat. `spawnPacket` only
   * succeeds when it happens to find a nearby destination (~2 in 3 tries), so after a
   * fixed 2.5 s warm-up `packets` has usually — but not always — been created, and the
   * suite fails on the ~1-in-7 run where it has not. That is a flake in the test, not a
   * fact about the page; polling removes it and still fails loudly if nothing spawns. */
  let armed = true;
  try {
    await page.waitForFunction(
      () => { const l = window.__bgLen(); return l.packets >= 0 && l.pings >= 0; },
      { timeout: 25000, polling: 250 });
  } catch { armed = false; }
  const before = await page.evaluate(() => window.__bgLen());

  const other = await browser.newPage();
  await other.goto("about:blank");
  await other.bringToFront();
  const frames = await page.evaluate((d) => window.__bgFrames(d), ms);
  const after = await page.evaluate(() => window.__bgLen());
  await other.close();
  await page.close();
  return { before, after, frames, armed };
}

const HIDDEN_MS = 20000;

/* ---- 1. TEETH: the old shape must still grow, or this box cannot see the bug --- */
const legacy = await hiddenRun(LEGACY, HIDDEN_MS);
const legacyGrowth = (legacy.after.packets - legacy.before.packets) +
                     (legacy.after.pings - legacy.before.pings);
if (!legacy.after.hidden || legacy.frames > 0 || legacyGrowth < 3) {
  // A static failure already found (a missing cap, a reverted spawner) is a fact about
  // the FILE, not about this box — it must not be swallowed by an environment skip.
  if (fails.length) finish(LABEL, { fails, count });
  skip(`this browser will not background a tab (hidden=${legacy.after.hidden}, ` +
       `frames-while-hidden=${legacy.frames}, legacy growth=${legacyGrowth}) — ` +
       "with rAF still running there is no producer/consumer gap to observe, so a PASS " +
       "here would mean nothing. Nothing is wrong with sim/web/bg.js; this box cannot test it.");
}
ok(true, "teeth: the pre-fix producer shape grows while hidden");

/* ---- 2. the SHIPPED file: zero growth while hidden ------------------------- */
const now = await hiddenRun(null, HIDDEN_MS);
ok(now.after.hidden === true, "the page under test was not actually hidden");
eq(now.frames, 0, "requestAnimationFrame kept running while hidden — the run proves nothing");
ok(now.armed && now.after.packets >= 0 && now.after.pings >= 0,
   "bg.js never created its packets/pings arrays within 25 s of load — nothing is spawning at all");
eq(now.after.packets - now.before.packets, 0,
   `packets grew while the tab was hidden (${now.before.packets} -> ${now.after.packets} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.pings - now.before.pings <= 0,
   `pings grew while the tab was hidden (${now.before.pings} -> ${now.after.pings} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.packets <= MAX_PACKETS, `packets over cap while hidden: ${now.after.packets} > ${MAX_PACKETS}`);
ok(now.after.pings <= MAX_PINGS, `pings over cap while hidden: ${now.after.pings} > ${MAX_PINGS}`);
console.log(`   hidden ${HIDDEN_MS / 1000}s — legacy: packets ${legacy.before.packets}->${legacy.after.packets}, ` +
            `pings ${legacy.before.pings}->${legacy.after.pings}   |   shipped: packets ` +
            `${now.before.packets}->${now.after.packets}, pings ${now.before.pings}->${now.after.pings}`);

/* ---- 3. the cap holds however hard the spawner is driven ------------------- *
 * Two knobs, so the ceiling is the ONLY thing that can stop the arrays growing:
 *  · the rAF timestamp the page sees is multiplied, so every frame looks like seconds of
 *    elapsed time and `SPAWN_CREDIT_MS` is the only thing rationing spawns;
 *  · both retire conditions are frozen — `sp = 0` so no packet ever completes its trip,
 *    `a` held up so no ping ever fades — so nothing can ever LEAVE the arrays.
 * They start three short of the cap, so a pass has to show BOTH halves: the arrays grow
 * (the spawner really is live, and this block is not passing on a page that spawns
 * nothing) and they stop at exactly the declared ceiling. Delete the two
 * `length >= MAX_*` guards in bg.js and the peak walks straight past it. */
const capPage = await browser.newPage();
await capPage.setViewport({ width: 1440, height: 900 });
await capPage.evaluateOnNewDocument(INSTRUMENT);
await capPage.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
await capPage.bringToFront();
let capArmed = true;
try {
  await capPage.waitForFunction(
    () => { const l = window.__bgLen(); return l.packets >= 0 && l.pings >= 0; },
    { timeout: 25000, polling: 250 });
} catch { capArmed = false; }
ok(capArmed, "the cap block never saw bg.js create its arrays — it cannot have tested the cap");
const peak = await capPage.evaluate((ms, maxP, maxG) => new Promise((res) => {
  const P = window.__bg.packets, G = window.__bg.pings;
  if (!P || !G) return res({ pk: -1, gk: -1 });
  const xy = () => ({ x: Math.random() * 1400, y: Math.random() * 860 });
  while (P.length < maxP - 3) P.push({ a: xy(), b: xy(), t: 0.5, sp: 0, c: "#05ffa1" });
  while (G.length < maxG - 3) G.push({ x: Math.random() * 1400, y: Math.random() * 860, r: 3, a: 0.5 });
  const start = { p: P.length, g: G.length };
  window.__bg.inflate = 400;                        // every frame looks like seconds of elapsed time
  let pk = P.length, gk = G.length;
  const hold = setInterval(() => {
    for (const p of P) { p.sp = 0; p.t = 0.5; }
    for (const g of G) { g.a = 0.5; g.r = 3; }
    pk = Math.max(pk, P.length); gk = Math.max(gk, G.length);
  }, 25);
  setTimeout(() => { clearInterval(hold); window.__bg.inflate = 0; res({ pk, gk, start }); }, ms);
}), 12000, MAX_PACKETS, MAX_PINGS);
eq(peak.pk, MAX_PACKETS,
   `packets did not settle at MAX_PACKETS under a driven clock with nothing retiring — ` +
   `peak ${peak.pk}, cap ${MAX_PACKETS} (started at ${peak.start && peak.start.p}). ` +
   (peak.pk > MAX_PACKETS ? "The cap does not hold." : "The spawner never reached it — this block proved nothing."));
eq(peak.gk, MAX_PINGS,
   `pings did not settle at MAX_PINGS under a driven clock with nothing retiring — ` +
   `peak ${peak.gk}, cap ${MAX_PINGS} (started at ${peak.start && peak.start.g}). ` +
   (peak.gk > MAX_PINGS ? "The cap does not hold." : "The spawner never reached it — this block proved nothing."));
console.log(`   driven clock — packets ${peak.start && peak.start.p}->${peak.pk} (cap ${MAX_PACKETS}), ` +
            `pings ${peak.start && peak.start.g}->${peak.gk} (cap ${MAX_PINGS})`);
await capPage.close();

/* ---- 4. reduced motion still spawns nothing at all ------------------------- */
const rm = await browser.newPage();
await rm.setViewport({ width: 1440, height: 900 });
await rm.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
await rm.evaluateOnNewDocument(INSTRUMENT);
await rm.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 4000));
const rmLen = await rm.evaluate(() => window.__bgLen());
eq(rmLen.packets, -1, "prefers-reduced-motion:reduce spawned packets — it must spawn none");
eq(rmLen.pings, -1, "prefers-reduced-motion:reduce spawned radar pings — it must spawn none");
const rmSparks = await rm.evaluate(() => document.querySelectorAll(".spark").length);
eq(rmSparks, 0, "prefers-reduced-motion:reduce still injected .spark divs");
await rm.close();

await browser.close();
site.close();
finish(LABEL, { fails, count });
