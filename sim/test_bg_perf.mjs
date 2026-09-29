/* test_bg_perf.mjs — the landing page must not pile up work while nobody is looking.
 *
 * The defect: bg.js's producers were setIntervals while the only consumer (the splice that
 * retires entries) ran inside the rAF loop. A hidden tab pauses rAF but not timers, so two
 * arrays filled with nothing draining them and all came due on return ("sluggish after
 * hours"). Now spawns happen inside step().
 *
 * A REAL hidden tab (a second page brought to front; every run asserts hidden and zero
 * frames). The claim is RECORDED PUSHES while `document.hidden` was true, captured by the page
 * at the push; lengths read inside the visibilitychange handler corroborate. TEETH FIRST:
 * block 1 rebuilds the old timer producers from the SHIPPED file and requires the growth to
 * reappear (else this box cannot background a tab and the suite skips loudly). Block 3 puts a
 * burst in flight at the flip; block 4 drives the spawner into the MAX_* cap (read from bg.js).
 *
 *   node sim/test_bg_perf.mjs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, web, watchPage, notable }
  from "./browser_harness.mjs";

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
/* A FAILURE, never a skip: without the in-frame spawner shape the "legacy" variant is the
 * shipped file, so block 2 reports any growth for real. */
const hasShape = SHIPPED.includes(SPAWN_CALL) && SHIPPED.endsWith(BOOT + "\n");
ok(hasShape, "sim/web/bg.js no longer spawns from inside the frame (the `spawn(...)` call " +
             "in step() is gone) — either it was reverted to setInterval producers, which is " +
             "the defect, or it was refactored and this suite's teeth transform needs updating");
const LEGACY = hasShape
  ? SHIPPED.replace(SPAWN_CALL, "").replace(BOOT + "\n", LEGACY_BOOT + "\n")
  : SHIPPED;

/* ---- page instrumentation -------------------------------------------------
 * `packets`/`pings` are closed over in bg.js's IIFE, so they are captured by shape off a
 * temporary Array.prototype.push hook that removes itself once both are found. Each then
 * gets an OWN `push` recording `document.hidden` at the push — the measurement this suite
 * rests on. `__bgHiddenAt` resolves with both lengths taken INSIDE the visibilitychange
 * dispatch, the exact start of the hidden window. */
const INSTRUMENT = function () {
  window.__bg = { packets: null, pings: null, inflate: 0, hiddenPushes: [] };
  const op = Array.prototype.push;
  const lens = () => ({
    packets: window.__bg.packets ? window.__bg.packets.length : -1,
    pings: window.__bg.pings ? window.__bg.pings.length : -1,
    hidden: document.hidden,
    at: performance.now(),
  });
  const record = (kind, arr) => {
    if (!document.hidden) return;
    const h = window.__bg.hiddenPushes;
    h[h.length] = { kind, at: performance.now(), len: arr.length };
  };
  const watch = (arr, kind) => {
    record(kind, arr);                       // the identifying push itself counts too
    Object.defineProperty(arr, "push", {
      configurable: true, writable: true,
      value: function () { record(kind, this); return op.apply(this, arguments); },
    });
  };
  Array.prototype.push = function (v) {
    if (arguments.length === 1 && v && typeof v === "object" && !Array.isArray(v)) {
      const k = Object.keys(v).join(",");
      if (k === "a,b,t,sp,c" && !window.__bg.packets) { window.__bg.packets = this; watch(this, "packet"); }
      if (k === "x,y,r,a" && !window.__bg.pings) { window.__bg.pings = this; watch(this, "ping"); }
      if (window.__bg.packets && window.__bg.pings) Array.prototype.push = op;
    }
    return op.apply(this, arguments);
  };
  /* rAF timestamps can be stretched to drive the spawner harder (`inflate` 0 = untouched).
   * An accumulated OFFSET, not a multiplier: resetting a multiplier makes the clock jump
   * backwards, which bg.js (dt clamped only from above) turns into negative arc radii — an
   * instrument artefact, since real rAF timestamps never decrease. */
  const raf = window.requestAnimationFrame.bind(window);
  let warp = 0, prev = null;
  window.requestAnimationFrame = function (cb) {
    return raf(function (ts) {
      if (prev === null) prev = ts;
      if (window.__bg.inflate) warp += (ts - prev) * (window.__bg.inflate - 1);
      prev = ts;
      return cb(ts + warp);
    });
  };
  window.__bgLen = lens;
  /* The lengths AT THE FLIP. Armed at document-start so it cannot miss the event, and it
   * resolves from inside the handler — no `await`, no timer, nothing that could let a
   * frame slip between the visibility change and the reading. */
  window.__bgHiddenAt = new Promise((res) => {
    if (document.hidden) return res(lens());
    document.addEventListener("visibilitychange", function h() {
      if (!document.hidden) return;
      document.removeEventListener("visibilitychange", h);
      res(lens());
    });
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

/**
 * Load index.html, optionally serving a rewritten bg.js, and background it for `ms`.
 * `drive` (block 3) is a NUMBER OF PACKETS: with the clock inflated and the drain frozen,
 * the tab is hidden only once the page reports that many new packets (a fixed sleep
 * produced the burst only ~2 runs in 3).
 *
 * @returns {{warm, before, after, frames, armed, flipped, hiddenPushes}}
 *   `warm` is the pre-hide reading (reported, never asserted); `before` is read inside the
 *   visibilitychange dispatch. `errs` is everything the page said to the console.
 */
async function hiddenRun(variant, ms, { drive = 0 } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const { errs, aborted } = watchPage(page);
  await page.evaluateOnNewDocument(INSTRUMENT);
  if (variant) {
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.url().endsWith("/bg.js")) r.respond({ status: 200, contentType: "text/javascript", body: variant });
      else r.continue();
    });
  }
  await page.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
  /* WAIT for both arrays to exist (spawnPacket succeeds ~2 in 3 tries) rather than sleeping
   * a fixed beat; still fails loudly if nothing spawns. */
  let armed = true;
  try {
    await page.waitForFunction(
      () => { const l = window.__bgLen(); return l.packets >= 0 && l.pings >= 0; },
      { timeout: 25000, polling: 250 });
  } catch { armed = false; }
  const warm = await page.evaluate(() => window.__bgLen());

  if (drive) {
    await page.evaluate(() => {
      window.__bg.driveFrom = window.__bg.packets ? window.__bg.packets.length : 0;
      window.__bg.inflate = 400;              // every frame banks the full SPAWN_CREDIT_MS
      /* Freeze the DRAIN, exactly as block 4 does, so the burst is a monotone increase
       * rather than a race between the spawner and the retire condition. Without this the
       * net delta at the flip is spawns-minus-retires and can legitimately be zero. */
      window.__bg.hold = setInterval(() => {
        const P = window.__bg.packets; if (!P) return;
        for (const p of P) { p.sp = 0; p.t = 0; }
      }, 10);
    });
    try {
      await page.waitForFunction(
        (n) => window.__bg.packets && window.__bg.packets.length >= window.__bg.driveFrom + n,
        { timeout: 15000, polling: 50 }, drive);
    } catch { /* reported by the driveGrowth assertion in block 3, never swallowed */ }
  }

  const other = await browser.newPage();
  await other.goto("about:blank");
  await other.bringToFront();
  /* The hidden window starts HERE, at the page's own visibilitychange, not at whatever
   * moment Node last managed to ask. A 20 s ceiling so a browser that never backgrounds
   * the tab reports `flipped: false` instead of hanging the suite. */
  const before = await page.evaluate(() => Promise.race([
    window.__bgHiddenAt,
    new Promise((r) => setTimeout(() => r(null), 20000)),
  ]));
  const flipped = before !== null;
  if (drive) await page.evaluate(() => { clearInterval(window.__bg.hold); window.__bg.inflate = 0; });
  const frames = await page.evaluate((d) => window.__bgFrames(d), ms);
  const after = await page.evaluate(() => window.__bgLen());
  const hiddenPushes = await page.evaluate(() => window.__bg.hiddenPushes.slice());
  await other.close();
  await page.close();
  return { warm, before: before || warm, after, frames, armed, flipped, hiddenPushes,
           errs, aborted };
}

/* ---- EYES ----------------------------------------------------------------- *
 * Without console/pageerror listeners a 404'd script or a first-frame exception would pass:
 * an array nothing pushes to grows by zero. This fixture produces ZERO console errors, so
 * nothing is forgiven; count a future refused request into `aborted.n`, don't widen the
 * pattern. The legacy run is asserted first, before the environment skip, so a broken text
 * transform cannot masquerade as "this box cannot background a tab". */
const eyes = (label, run) => {
  const left = notable(run.errs, run.aborted);
  eq(left.length, 0,
     `${label}: the page raised console errors nobody asked for — ${left.length}, ` +
     `first: ${left.slice(0, 3).join(" | ")}`);
};

const HIDDEN_MS = 8000;        // legacy timers still fire ~1/s in a hidden tab: >=8 pushes
const pushSummary = (h) => h.length
  ? `${h.length} (${h.filter((p) => p.kind === "packet").length} packet / ` +
    `${h.filter((p) => p.kind === "ping").length} ping)`
  : "0";

/* ---- 1. TEETH: the old shape must still grow, or this box cannot see the bug ---
 * Reads the RECORDED pushes; on a box that cannot background a tab it is 0 and the suite
 * stands down loudly. */
const legacy = await hiddenRun(LEGACY, HIDDEN_MS);
eyes("teeth (the rebuilt pre-fix bg.js)", legacy);
const legacyGrowth = (legacy.after.packets - legacy.before.packets) +
                     (legacy.after.pings - legacy.before.pings);
if (!legacy.after.hidden || !legacy.flipped || legacy.frames > 0 || legacy.hiddenPushes.length < 3) {
  // A static failure already found (a missing cap, a reverted spawner) is a fact about
  // the FILE, not about this box — it must not be swallowed by an environment skip.
  if (fails.length) finish(LABEL, { fails, count });
  skip(`this browser will not background a tab (hidden=${legacy.after.hidden}, ` +
       `flipped=${legacy.flipped}, frames-while-hidden=${legacy.frames}, ` +
       `legacy pushes-while-hidden=${legacy.hiddenPushes.length}, legacy growth=${legacyGrowth}) — ` +
       "with rAF still running there is no producer/consumer gap to observe, so a PASS " +
       "here would mean nothing. Nothing is wrong with sim/web/bg.js; this box cannot test it.");
}
ok(true, "teeth: the pre-fix producer shape spawns while hidden");

/* ---- 2. the SHIPPED file: nothing spawns while hidden ---------------------- */
const now = await hiddenRun(null, HIDDEN_MS);
eyes("the shipped landing page", now);
ok(now.after.hidden === true, "the page under test was not actually hidden");
ok(now.flipped, "the page never reported a visibilitychange to hidden — the window under measurement " +
                "never started, so the numbers below describe nothing");
eq(now.frames, 0, "requestAnimationFrame kept running while hidden — the run proves nothing");
ok(now.armed && now.after.packets >= 0 && now.after.pings >= 0,
   "bg.js never created its packets/pings arrays within 25 s of load — nothing is spawning at all");
/* THE claim, asserted as recorded state (rule 11): not one entry may be created while
 * `document.hidden` is true. Unlike the two length reads below, this cannot be shifted by
 * anything that happens on either side of the visibility flip. */
eq(now.hiddenPushes.length, 0,
   `sim/web/bg.js spawned while the tab was hidden — ${pushSummary(now.hiddenPushes)} push(es) ` +
   `recorded with document.hidden===true; the guard in spawn() has been beaten or removed`);
/* `<= 0`, not `=== 0`: an increase would be a real hidden spawn (caught above); a decrease
 * needs a frame to have run, which `frames > 0` already refuses. */
ok(now.after.packets - now.before.packets <= 0,
   `packets grew while the tab was hidden (${now.before.packets} -> ${now.after.packets} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.pings - now.before.pings <= 0,
   `pings grew while the tab was hidden (${now.before.pings} -> ${now.after.pings} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.packets <= MAX_PACKETS, `packets over cap while hidden: ${now.after.packets} > ${MAX_PACKETS}`);
ok(now.after.pings <= MAX_PINGS, `pings over cap while hidden: ${now.after.pings} > ${MAX_PINGS}`);

/* ---- 3. THE CONSTRUCTED INTERLEAVING: a burst in flight as the tab hides ----
 * While still VISIBLE the clock is inflated 400x and the drain frozen; the tab hides as soon
 * as the page reports `DRIVE_PACKETS` new entries, so a burst is provably in flight at the
 * flip. A `before` sampled from Node fails this every run; one read at the flip passes.
 * `driveGrowth` is this block's own teeth: no burst, no proof. */
const DRIVE_PACKETS = 3;
const burst = await hiddenRun(null, 6000, { drive: DRIVE_PACKETS });
eyes("the constructed interleaving", burst);
const driveGrowth = burst.before.packets - burst.warm.packets;
ok(burst.flipped && burst.after.hidden === true,
   "the constructed-interleaving page never went hidden");
eq(burst.frames, 0, "requestAnimationFrame kept running while hidden in the constructed run");
ok(driveGrowth >= DRIVE_PACKETS,
   `the constructed interleaving never happened: driving the spawner with the drain frozen added ` +
   `only ${driveGrowth} of the ${DRIVE_PACKETS} packets asked for in 15 s ` +
   `(${burst.warm.packets} -> ${burst.before.packets}), so this block did not put a spawn in ` +
   `flight and proves nothing`);
eq(burst.hiddenPushes.length, 0,
   `sim/web/bg.js spawned while hidden with a burst in flight — ${pushSummary(burst.hiddenPushes)} ` +
   `push(es) recorded with document.hidden===true`);
ok(burst.after.packets - burst.before.packets <= 0,
   `packets grew while the tab was hidden with a burst in flight ` +
   `(${burst.before.packets} -> ${burst.after.packets})`);
ok(burst.after.pings - burst.before.pings <= 0,
   `pings grew while the tab was hidden with a burst in flight ` +
   `(${burst.before.pings} -> ${burst.after.pings})`);

/* ---- 4. the cap holds however hard the spawner is driven ------------------- *
 * The clock is multiplied (SPAWN_CREDIT_MS is the only ration) and both retire conditions
 * are frozen, so only the ceiling can stop growth. Starting three short of the cap, a pass
 * must show the arrays GROW and then stop exactly at MAX_*. */
const capPage = await browser.newPage();
await capPage.setViewport({ width: 1440, height: 900 });
const capEyes = watchPage(capPage);
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
}), 5000, MAX_PACKETS, MAX_PINGS);
eq(peak.pk, MAX_PACKETS,
   `packets did not settle at MAX_PACKETS under a driven clock with nothing retiring — ` +
   `peak ${peak.pk}, cap ${MAX_PACKETS} (started at ${peak.start && peak.start.p}). ` +
   (peak.pk > MAX_PACKETS ? "The cap does not hold." : "The spawner never reached it — this block proved nothing."));
eq(peak.gk, MAX_PINGS,
   `pings did not settle at MAX_PINGS under a driven clock with nothing retiring — ` +
   `peak ${peak.gk}, cap ${MAX_PINGS} (started at ${peak.start && peak.start.g}). ` +
   (peak.gk > MAX_PINGS ? "The cap does not hold." : "The spawner never reached it — this block proved nothing."));
eyes("the driven-clock page", capEyes);
await capPage.close();

/* ---- 5. reduced motion still spawns nothing at all ------------------------- */
const rm = await browser.newPage();
await rm.setViewport({ width: 1440, height: 900 });
const rmEyes = watchPage(rm);
await rm.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
await rm.evaluateOnNewDocument(INSTRUMENT);
await rm.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 2500));   // an absence: nothing to wait FOR
const rmLen = await rm.evaluate(() => window.__bgLen());
eq(rmLen.packets, -1, "prefers-reduced-motion:reduce spawned packets — it must spawn none");
eq(rmLen.pings, -1, "prefers-reduced-motion:reduce spawned radar pings — it must spawn none");
const rmSparks = await rm.evaluate(() => document.querySelectorAll(".spark").length);
eq(rmSparks, 0, "prefers-reduced-motion:reduce still injected .spark divs");
eyes("prefers-reduced-motion", rmEyes);
await rm.close();

await browser.close();
site.close();
finish(LABEL, { fails, count });
