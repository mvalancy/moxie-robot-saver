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
 * · RECORDED PUSHES, not a sampled length difference. See "THE MEASUREMENT BOUNDARY"
 *   below: the primary assertion is the number of pushes into the two arrays that
 *   happened while `document.hidden` was true, captured at the push, by the page. The
 *   before/after lengths are still reported and still asserted, but they are a corroborating
 *   read, not the claim.
 *
 * · ARRAY LENGTH, not wall-clock, for that corroborating read. A frame-time assertion is a
 *   coin toss on a shared CI runner. The quantity that actually causes the sluggishness is
 *   how many entries are waiting to be drawn — it is upstream of the frame time, it is an
 *   integer, and it is the same number whether the runner is busy or idle. Counting
 *   `shadowBlur` draws per frame was considered and dropped: on this page that number IS
 *   the array length plus a constant, so it measures the same thing later and less directly.
 *
 * · THE TEETH RUN FIRST. Block 1 rebuilds the OLD producer shape out of the SHIPPED file
 *   (a text transform, never a second copy that could drift) and requires the growth to
 *   REAPPEAR. If it does not, this environment cannot background a tab at all — so the
 *   suite skips green with a loud notice instead of reporting a pass it did not earn.
 *   Every later block runs only on an environment that has just proven it can see the bug.
 *
 * · THE CAP IS TESTED SEPARATELY (block 4), by inflating the rAF timestamp the page sees
 *   and freezing both retire conditions, so nothing can leave the arrays and the ceiling
 *   is the only thing left holding the line. It starts three short of the cap so a pass
 *   must show the arrays GROW and then stop exactly there — a block that merely asserted
 *   `<= cap` would pass just as well on a page that spawns nothing at all.
 *
 * · A MISSING in-frame spawner is a FAILURE, not a skip. The one thing this suite must
 *   never do is stand quietly down on a revert of the very change it guards.
 *
 * THE MEASUREMENT BOUNDARY (2026-09-06, and the reason block 3 exists).
 *
 * This suite reddened twice on PRs whose diffs could not reach `sim/web/bg.js` — once as
 * `pings grew while the tab was hidden (1 -> 2 in 20s)` on 2026-09-05, and again as
 * `packets grew while the tab was hidden (1 -> 2 in 20s)` on 2026-09-06 (job
 * 101442923492, PR #172) — while passing every time it was re-run by hand. It was not a
 * flake and it was not `bg.js`: it was THIS FILE measuring across a boundary it did not
 * control.
 *
 * `before` used to be sampled here, from Node, and only THEN did the harness open the
 * second page and bring it to the front. Everything in between — a CDP round trip, a
 * `Target.createTarget`, a navigation to about:blank — happens with the page under test
 * still VISIBLE and its rAF loop still running, so `bg.js` spawns during it exactly as it
 * is supposed to. Those legitimate visible-tab packets then never retire, because the tab
 * hides moments later and rAF stops, so they are still in the array 20 s later and the
 * "growth while hidden" arithmetic charges them to the hidden window.
 *
 * Measured directly, with every push into both arrays recorded together with
 * `document.hidden` and a page-clock timestamp: on an idle box that gap is 7-26 ms, which
 * is why it passes by hand; widened to 1 200 ms it reproduces the CI failure 4 runs in 12,
 * with the offending pushes timestamped INSIDE the gap and `document.hidden === false` at
 * every one of them. Across 32 such runs, the number of pushes that happened while
 * `document.hidden` was true was ZERO — `bg.js`'s guard has never once been beaten. A
 * loaded CI runner starting a second Chrome target is simply slower than an idle laptop.
 *
 * That is rule 23's shape — a check whose subject can change between the check and the
 * action is not a check, it is a memory — and note which direction the old code had already
 * conceded: the `<= 0` below tolerated a DECREASE for precisely this reason, without
 * noticing that the same gap produces an increase just as easily. So:
 *
 *   1. `before` is now captured BY THE PAGE, inside the `visibilitychange` handler itself
 *      (`__bgHiddenAt`), so the measured window begins exactly at the flip. No frame can
 *      run between the flip and that snapshot — the handler runs synchronously in the
 *      dispatch — and every frame after it sees `document.hidden === true`.
 *   2. The claim under test is asserted directly, as recorded state: zero pushes while
 *      hidden. That assertion cannot be moved by any boundary at all.
 *   3. Block 3 CONSTRUCTS the interleaving rather than waiting for it: it drives the
 *      spawner with the drain frozen and hides the tab only once the page has reported
 *      three brand-new packets, so a burst is provably in flight at the flip. Against the
 *      pre-fix boundary that block fails every single run, with the CI message verbatim;
 *      against this one it passes, and it still fails if `bg.js` ever spawns while hidden.
 *
 * `MAX_PACKETS` / `MAX_PINGS` are read out of `sim/web/bg.js`, never restated here — a
 * hard-coded 48 could pass while the shipped file said something else.
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
 * both are found — so nothing else on the page pays for it.
 *
 * The moment an array IS found it gets an OWN `push` (shadowing the prototype, so only
 * these two arrays pay anything) that records `document.hidden` at the push. That is the
 * measurement this suite actually rests on: "did anything spawn while hidden" answered by
 * the page, at the instant it happened, instead of inferred afterwards from two lengths
 * sampled on either side of a boundary Node cannot see. See THE MEASUREMENT BOUNDARY above.
 *
 * `__bgHiddenAt` resolves with the two lengths taken INSIDE the `visibilitychange`
 * dispatch, which is the only instant that is exactly the start of the hidden window. */
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
  /* rAF timestamps the page sees can be stretched, to drive the spawner harder than any
   * real clock. `inflate` of 0 leaves the browser's own timestamps untouched.
   *
   * THE STRETCH IS AN ACCUMULATED OFFSET, NOT A MULTIPLIER, and that is not a refinement.
   * `ts * inflate` is monotonic only while `inflate` is held constant: the moment blocks 3
   * and 4 put it back to 0 the page sees the clock JUMP BACKWARDS by minutes. `bg.js`
   * clamps `dt` from above (`Math.min(2.4, elapsed / 16.7)`) and not from below, so a
   * negative elapsed drives `pg.r += 0.6 * dt` negative and every subsequent frame throws
   * `IndexSizeError: arc(): The radius provided (-53809.6) is negative` — which the
   * console/pageerror listeners added on 2026-09-06 caught the first time they ran. It is
   * an artefact of this instrument and NOT a defect in `bg.js`: a real `requestAnimationFrame`
   * timestamp never decreases, so the page cannot reach that state on its own. The fix is
   * here, where the fault is. Banking the extra time as a running offset makes the clock
   * monotonic whatever `inflate` does, while still handing the spawner 400 frames' worth
   * of elapsed time per frame, which is all either block ever needed. */
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
 *
 * `drive` (see block 3) is a NUMBER OF PACKETS, not a duration, and that is the whole
 * point: the harness inflates the rAF timestamp so every frame banks a full
 * `SPAWN_CREDIT_MS`, freezes the drain so the array can only grow, and then WAITS until
 * the page reports that many new packets before it hides the tab. A fixed sleep was tried
 * first and produced the burst only about two runs in three — a construction that
 * sometimes does not happen is the very thing this file is being fixed for.
 *
 * @returns {{warm, before, after, frames, armed, flipped, hiddenPushes}}
 *   `warm` is the pre-hide reading (reported, never asserted on); `before` is the reading
 *   taken inside the visibilitychange dispatch, which is where the hidden window starts.
 *   `errs` is everything the page said to the console (see EYES below).
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
 *
 * WHY THIS SUITE NEEDED THEM. Until 2026-09-06 this file installed no `console` and no
 * `pageerror` listener, so the ONE property it reads — how many objects were pushed into
 * two arrays inside `bg.js`'s closure — was the only thing that could ever fail it. Serve
 * `index.html` with a script 404'd, a CSP that refuses `home.js`, or an exception thrown
 * on the first frame, and every assertion below still passes on whatever is left: an
 * array that nothing pushes to grows by zero while hidden, which reads as a PASS.
 *
 * WHAT `notable()` IS FORGIVING HERE: nothing, and that is the measured answer, not an
 * assumption. `index.html` under this fixture (`serveWeb()`, no CSP header, no route
 * refused, no `/api` call) produced ZERO console errors and ZERO failed requests across
 * every page this suite opens — only WebGL software-rendering warnings, which are `warn`
 * and never collected. So `aborted` stays at 0 and the assertion is a plain "the page said
 * nothing was wrong". If a future fixture starts refusing a request, count it into
 * `aborted.n` at the interceptor rather than widening the pattern.
 *
 * THE LEGACY RUN IS ASSERTED FIRST, and deliberately before the environment skip below:
 * block 1 rebuilds the pre-fix `bg.js` by a TEXT TRANSFORM, and a transform that produced
 * broken JavaScript would spawn nothing, background nothing, and take the skip — reporting
 * "this box cannot background a tab" for a fault that is entirely in this file. */
const eyes = (label, run) => {
  const left = notable(run.errs, run.aborted);
  eq(left.length, 0,
     `${label}: the page raised console errors nobody asked for — ${left.length}, ` +
     `first: ${left.slice(0, 3).join(" | ")}`);
};

const HIDDEN_MS = 20000;
const pushSummary = (h) => h.length
  ? `${h.length} (${h.filter((p) => p.kind === "packet").length} packet / ` +
    `${h.filter((p) => p.kind === "ping").length} ping)`
  : "0";

/* ---- 1. TEETH: the old shape must still grow, or this box cannot see the bug ---
 * The teeth now read the RECORDED pushes, not the length difference. Same intent, one
 * less inference: what has to be observable here is a producer running while the tab is
 * hidden, and that is now a thing the page reports rather than a thing arithmetic on two
 * lengths implies. On a box that cannot background a tab it is 0 and the suite stands
 * down loudly. */
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
/* `<= 0`, not `=== 0`, and the pings line below has always said so. `before` is now read
 * inside the visibilitychange dispatch, so an INCREASE here is a real spawn in the hidden
 * window (and the recorded-push check above would already have caught it); a DECREASE
 * still cannot be a failure — it would mean a frame ran and retired an entry, which needs
 * `frames > 0`, which the assertion above already refuses. */
ok(now.after.packets - now.before.packets <= 0,
   `packets grew while the tab was hidden (${now.before.packets} -> ${now.after.packets} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.pings - now.before.pings <= 0,
   `pings grew while the tab was hidden (${now.before.pings} -> ${now.after.pings} in ${HIDDEN_MS / 1000}s)`);
ok(now.after.packets <= MAX_PACKETS, `packets over cap while hidden: ${now.after.packets} > ${MAX_PACKETS}`);
ok(now.after.pings <= MAX_PINGS, `pings over cap while hidden: ${now.after.pings} > ${MAX_PINGS}`);
console.log(`   hidden ${HIDDEN_MS / 1000}s — legacy: packets ${legacy.before.packets}->${legacy.after.packets}, ` +
            `pings ${legacy.before.pings}->${legacy.after.pings}, pushes-while-hidden ` +
            `${pushSummary(legacy.hiddenPushes)}   |   shipped: packets ` +
            `${now.before.packets}->${now.after.packets}, pings ${now.before.pings}->${now.after.pings}, ` +
            `pushes-while-hidden ${pushSummary(now.hiddenPushes)}`);

/* ---- 3. THE CONSTRUCTED INTERLEAVING: a burst in flight as the tab hides ----
 *
 * Block 2 hides an idle page, so on a fast box it usually spawns nothing in the moments
 * before the flip and the boundary bug stays invisible — three consecutive green runs on
 * clean `origin/dev` is what sent the 2026-09-06 CI failure back as "just a flake". This
 * block removes the luck: while the page is still VISIBLE the rAF timestamp is inflated
 * 400x, so every frame banks a full `SPAWN_CREDIT_MS` (four frames buy a packet), and the
 * retire condition is held frozen so the array can only grow. The tab is then hidden not
 * after a fixed sleep but as soon as the page reports `DRIVE_PACKETS` new entries — so
 * the interleaving is a precondition the harness WAITS for, never one it hopes for. Those
 * packets are brand new at the instant the tab goes hidden, and every one of them was
 * spawned, legitimately, by a VISIBLE page.
 *
 * Against the old boundary (`before` sampled from Node before the second page was even
 * created) this fails every run, reporting exactly the CI message. Against a `before`
 * read at the flip it passes, because those packets are on the correct side of it.
 *
 * The `driveGrowth` assertion is this block's own teeth: if the burst did not happen, the
 * block proved nothing and must say so rather than pass. */
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
console.log(`   constructed interleaving — driven while visible until +${DRIVE_PACKETS}: packets ` +
            `${burst.warm.packets}->${burst.before.packets} at the flip (+${driveGrowth}), then ` +
            `${burst.before.packets}->${burst.after.packets} while hidden, pushes-while-hidden ` +
            `${pushSummary(burst.hiddenPushes)}`);

/* ---- 4. the cap holds however hard the spawner is driven ------------------- *
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
eyes("the driven-clock page", capEyes);
await capPage.close();

/* ---- 5. reduced motion still spawns nothing at all ------------------------- */
const rm = await browser.newPage();
await rm.setViewport({ width: 1440, height: 900 });
const rmEyes = watchPage(rm);
await rm.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
await rm.evaluateOnNewDocument(INSTRUMENT);
await rm.goto(site.url + "/index.html", { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 4000));
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
