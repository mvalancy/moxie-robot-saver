/* test_first_tap.mjs — a tap on Moxie herself, in a REAL browser with its REAL autoplay
 * policy (W4-S6). Before: the stage's only input was OrbitControls, so a tap on her did
 * nothing and her first sound came 6.3 s (desktop) / 9.5 s (phone) after it, from the first
 * idle quip. Now the first tap on her says hello, and no other tap ever makes a sound.
 *
 * Sound is recorded where it is made (`instrumentWebAudio`: "clip" = a shipped file, "pcm" =
 * her gateway voice) and the browser voice by a recorder; taps are real input aimed at her
 * projected centre (`window.__moxieProject`). Reads go through CDP with `userGesture: false`:
 * puppeteer's evaluate ACTIVATES the page (test_one_voice.mjs), and the unlock is the tap's.
 *   A. a phone's first tap on her: one greeting clip, soon, unlocked by that tap, her wave and
 *      the bubble — and nothing in the comms log;
 *   B. a second tap: a face, no sound;
 *   C. desktop: a drag across her is an orbit and a click beside her a miss — neither says
 *      hello — and a click on her after them does (a mouse is a tap too);
 *   D. a tap while she speaks (an idle quip): no sound, the quip is not cut; quiet again, a
 *      tap says hello (the refusal did not spend it);
 *   E. a tap while a typed turn is in flight: no sound, and her reply still plays whole;
 *   F. a tap with the microphone open: no sound, and the recording goes on;
 *   G. every greeting moxie.js can say has its clip in the manifest's `moxie` group.
 * No gateway and no network: `/api/*` is answered at the browser (openSim).
 *
 *   node sim/test_first_tap.mjs [--report]   (--report prints the measured times)
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, launchBrowser, openSim,
         liveFixture, instrumentWebAudio, notable, web, PHONE } from "./browser_harness.mjs";

const LABEL = "first-tap test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const REPORT = process.argv.includes("--report");
const report = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (r, body, status = 200) => r.respond({ status, contentType: "application/json", body });
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

/* G. The greetings, read out of moxie.js itself, and the clip each one plays. */
const MANIFEST = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8"));
const block = (readFileSync(join(web, "moxie.js"), "utf8").match(/const GREETINGS = \[([\s\S]*?)\];/) || [])[1] || "";
const GREETINGS = [...block.matchAll(/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1] ?? m[2]);
eq(GREETINGS.length, 3, "G: moxie.js names three greetings");
const greetingBySize = {};
for (const g of GREETINGS) {
  const rel = (MANIFEST.moxie || {})[g];
  ok(!!rel, `G: the greeting ${JSON.stringify(g)} has a clip in the manifest's moxie group`);
  if (rel && existsSync(join(web, "audio", rel))) greetingBySize[statSync(join(web, "audio", rel)).size] = g;
  else if (rel) ok(false, `G: …and its file ships (${rel})`);
}

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
// Chrome's own autoplay policy: no `--autoplay-policy=no-user-gesture-required`.
const browser = await launchBrowser(puppeteer, chrome, { autoplay: false, hosts: { "moxie.hosted.test": site.port } });

const TONE = pcmToneBase64({ seconds: 1.5, rate: 22050, freq: 440, amp: 0.6 });
const FX = await liveFixture({ eid: "sim-firsttap-1", reply: "Rockets are loud and fast!", tone: TONE });

/** PAGE-SIDE: the browser voice as a recorder, and when each pointer lifted. */
function recordPage() {
  window.__speech = [];
  window.__ups = [];
  window.addEventListener("pointerup", () => { window.__ups.push(performance.now()); }, true);
  const fake = {
    speaking: false, pending: false, paused: false, getVoices: () => [],
    speak(u) { window.__speech.push(u.text); setTimeout(() => { try { u.onend && u.onend(); } catch (e) {} }, 50); },
    cancel() {}, pause() {}, resume() {}, addEventListener() {}, removeEventListener() {},
  };
  Object.defineProperty(window, "speechSynthesis", { value: fake, configurable: true });
}

/** Page access that grants NO user activation (see test_one_voice.mjs::gestureFree). */
async function gestureFree(page) {
  const s = await page.target().createCDPSession();
  const read = async (expr) => {
    const r = await s.send("Runtime.evaluate", { expression: expr, userGesture: false, returnByValue: true });
    if (r.exceptionDetails) throw new Error("gesture-free read failed: " + expr.slice(0, 80));
    return r.result.value;
  };
  const until = async (expr, timeout = 15000) => {
    for (let i = 0; i < timeout / 50; i++, await sleep(50)) if (await read(expr)) return true;
    return false;
  };
  return { read, until, json: async (expr) => JSON.parse(await read(`JSON.stringify(${expr})`)) };
}

/** A fresh hosted page, live, every backend answered at the browser; NOT settled with
 *  evaluate (that would activate it). */
async function open(label, o = {}) {
  const v = await openSim(browser, HOSTED, {
    health: FX.health, viewport: o.viewport || PHONE, settle: false, route: o.route,
    beforeLoad: async (p) => { await p.evaluateOnNewDocument(instrumentWebAudio); await p.evaluateOnNewDocument(recordPage); },
  });
  const g = await gestureFree(v.page);
  ok(await g.until("!!window.moxie && !!window.moxieAudio && !!window.moxieMode && " +
                   "document.readyState === 'complete' && window.moxieMode.canSpendLiveTurn() === true", 30000),
     `${label}: the hosted page booted live`);
  return { ...v, g };
}

/** Where she is on screen (her centre, the orbit target) and what a tap there lands on. */
const her = (g) => g.json(`(() => { const p = window.__moxieProject(0, 1.15, 0);
  const el = document.elementFromPoint(p.x, p.y);
  return { x: Math.round(p.x), y: Math.round(p.y), on: el ? el.tagName : null }; })()`);
/** A point on the 3-D stage well clear of her. */
const offHer = (g) => g.json(`(() => { const me = window.__moxieProject(0, 1.15, 0);
  const st = document.getElementById('stage').getBoundingClientRect();
  const at = [[st.left + 16, st.top + 16], [st.right - 16, st.top + 16],
              [st.left + 16, st.top + st.height / 2], [st.right - 16, st.top + st.height / 2]];
  for (const [x, y] of at) { const el = document.elementFromPoint(x, y);
    if (el && el.tagName === 'CANVAS' && Math.hypot(x - me.x, y - me.y) > 120) return { x: Math.round(x), y: Math.round(y) }; }
  return null; })()`);
/** What the taps did (null on a page without them), and every sound since page time `t0`. */
const state = (g, t0 = 0) => g.json(`({
  stats: window.moxie.tapStats ? window.moxie.tapStats() : null,
  plays: window.__audio.plays.filter((p) => p.t >= ${t0}).map((p) => ({ id: p.id, src: p.src, bytes: p.bytes, t: Math.round(p.t), dur: Math.round(p.dur) })),
  stops: window.__audio.stops.filter((s) => s.t >= ${t0}).map((s) => ({ id: s.id, t: Math.round(s.t) })),
  speech: window.__speech.slice(), up: window.__ups.length ? Math.round(window.__ups[window.__ups.length - 1]) : null,
  turns: document.querySelectorAll('#transcript .turn').length, now: Math.round(performance.now()) })`);
const clipsOf = (s) => s.plays.filter((p) => p.src === "clip");
/** A miss on the stage: it unlocks audio like any tap and must say nothing; then her idle
 *  quips are stopped, since this suite measures taps (`ambient.js` starts on the unlock). */
async function unlockWithAMiss(page, g, label) {
  const off = await offHer(g);
  ok(!!off, `${label}: found a point on the stage clear of her`);
  if (off) await page.touchscreen.tap(off.x, off.y);
  ok(await g.until("window.moxieAudio.isUnlocked()", 5000), `${label}: precondition — a tap unlocked audio`);
  await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");
}

try {
  /* =======================================================================
   * A–B. A PHONE: the first tap on her, then a second one
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("A");
    const s0 = await g.json("({ unlocked: window.moxieAudio.isUnlocked(), active: navigator.userActivation.hasBeenActive })");
    deep([s0.unlocked, s0.active], [false, false],
         "A: before the tap, audio is locked and the page was never activated (the tap is what unlocks it)");
    // The Bht_* player, spied on: the wave is RECORDED, never sampled mid-motion.
    await g.read("(() => { const B = window.__moxieBridge, bt = B.behaviourTree; window.__trees = []; " +
                 "B.behaviourTree = function (n) { window.__trees.push(n); return bt.apply(this, arguments); }; return 1; })()");
    const at = await her(g);
    eq(at.on, "CANVAS", `A: her centre (${at.x}, ${at.y}) is the 3-D stage, not page chrome`);
    await page.touchscreen.tap(at.x, at.y);
    ok(await g.until("window.__audio.plays.some((p) => p.src === 'clip')", 8000), "A: a clip plays after the first tap on her");
    const a = await state(g);
    const clips = clipsOf(a);
    eq(clips.length, 1, "A: exactly one clip");
    const said = clips.length ? greetingBySize[clips[0].bytes] : undefined;
    ok(!!said, `A: …and it is one of the three greetings, by size (${clips.length ? clips[0].bytes : "-"} bytes)`);
    eq(a.stats && a.stats.said, said, "A: …the one moxie.js says it chose");
    const ms = clips.length && a.up !== null ? clips[0].t - a.up : null;
    ok(ms !== null && ms >= 0 && ms <= 1500,
       `A: her first sound comes ${ms} ms after the finger lifts (bar: 1,500 ms; before, an idle quip 5-9 s on)`);
    deep(a.stats && [a.stats.taps, a.stats.hellos, a.stats.faces, a.stats.last], [1, 1, 0, "hello"],
         "A: tapStats — one tap on her, one hello");
    ok(await g.read("window.moxieAudio.isUnlocked()"), "A: the tap's own gesture unlocked audio");
    const trees = await g.json("window.__trees");
    ok(trees.includes("Bht_Gesture_Greet"), `A: she waves (Bht_Gesture_Greet; played ${JSON.stringify(trees)})`);
    eq(await g.read("(document.querySelector('#faces button.active') || { dataset: {} }).dataset.expr"), "happy",
       "A: …with a happy face while she says it");
    ok(await g.until(`document.getElementById('bubble-text').textContent === ${JSON.stringify(said || "")}`, 6000),
       "A: her bubble says the greeting");
    eq(a.speech.length, 0, "A: no browser voice — the hello is her own shipped clip");
    eq(a.turns, 0, "A: nothing in the comms log — a hello is not a turn, so the openers stay");
    report.A = { first_sound_ms: ms, said, bytes: clips.length ? clips[0].bytes : null };
    await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");

    // B. A second tap, once the hello is over: a face, never a sound.
    ok(await g.until("!window.moxieAudio.isMoxieBusy(600)", 12000), "B: precondition — the hello is over");
    const t1 = await g.read("Math.round(performance.now())");
    const at2 = await her(g);
    await page.touchscreen.tap(at2.x, at2.y);
    await sleep(1500);
    const b = await state(g, t1);
    eq(b.plays.length, 0, `B: the second tap makes no sound (${JSON.stringify(b.plays)})`);
    deep(b.stats && [b.stats.taps, b.stats.hellos, b.stats.faces, b.stats.last], [2, 1, 1, "said"],
         "B: …it is a face (tapStats: the hello was already said)");
    eq(notable(errs, aborted).length, 0, `A-B: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * C. A DESKTOP MOUSE: a drag is an orbit, a click beside her a miss
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("C", { viewport: { width: 1280, height: 800 } });
    let at = await her(g);
    await page.mouse.move(at.x - 40, at.y);
    await page.mouse.down();
    await page.mouse.move(at.x + 60, at.y, { steps: 10 });
    await page.mouse.up();
    await sleep(800);                                   // the orbit's damping settles
    const off = await offHer(g);
    ok(!!off, "C: found a point on the stage clear of her");
    if (off) await page.mouse.click(off.x, off.y);
    await sleep(1200);
    await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");
    let c = await state(g);
    deep(c.stats && [c.stats.taps, c.stats.misses, c.stats.hellos], [0, 1, 0],
         "C: a drag across her is no tap and a click beside her is a miss — no hello");
    eq(clipsOf(c).length, 0, "C: …and no sound");
    at = await her(g);                                  // she turned with the orbit
    await page.mouse.click(at.x, at.y);
    ok(await g.until("window.__audio.plays.some((p) => p.src === 'clip')", 8000), "C: a click ON her makes a sound");
    c = await state(g);
    deep(c.stats && [c.stats.taps, c.stats.hellos, c.stats.last], [1, 1, "hello"], "C: …the hello (a mouse is a tap too)");
    ok(clipsOf(c).length === 1 && !!greetingBySize[clipsOf(c)[0].bytes], "C: …in one greeting clip");
    report.C = { first_sound_ms: clipsOf(c).length && c.up !== null ? clipsOf(c)[0].t - c.up : null };
    eq(notable(errs, aborted).length, 0, `C: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * D. A TAP WHILE SHE SPEAKS
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("D");
    await unlockWithAMiss(page, g, "D");
    await g.read("(() => { window.moxieAmbient.say(); return 1; })()");      // one idle quip, now
    ok(await g.until("window.moxieAudio.isMoxieSpeaking() && window.__audio.plays.some((p) => p.src === 'clip')", 8000),
       "D: precondition — she is saying an idle quip");
    const quip = clipsOf(await state(g))[0];
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await page.touchscreen.tap(at.x, at.y);
    await sleep(400);
    let d = await state(g, t1);
    eq(d.stats && d.stats.last, "speaking", "D: a tap while she speaks is refused for that");
    eq(d.plays.length, 0, "D: …makes no sound");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(700)", 15000), "D: her quip ends");
    d = await state(g, t1);
    eq(d.stops.filter((s) => quip && s.id === quip.id && s.t < quip.t + quip.dur - 50).length, 0,
       "D: …uncut by the tap");
    const at2 = await her(g);
    await page.touchscreen.tap(at2.x, at2.y);
    ok(await g.until("window.moxie.tapStats && window.moxie.tapStats().hellos === 1", 3000) &&
       await g.until(`window.__audio.plays.filter((p) => p.src === 'clip' && p.t >= ${t1}).length === 1`, 8000),
       "D: quiet again, a tap says hello: the refusal did not spend it");
    eq(notable(errs, aborted).length, 0, `D: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * E. A TAP WHILE A TURN IS IN FLIGHT (the chat answers 2.5 s after the send)
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("E", {
      route: (r, u) => {
        if (/\/api\/chat\b/.test(u)) { setTimeout(() => json(r, FX.chat), 2500); return true; }
        if (/\/api\/speech\b/.test(u)) { json(r, FX.speech); return true; }
        return false;
      },
    });
    await unlockWithAMiss(page, g, "E");
    const t0 = await g.read("Math.round(performance.now())");
    await g.read("(() => { window.moxieTypedTurn.send('tell me about rockets'); return 1; })()");
    await sleep(600);
    const at = await her(g);
    await page.touchscreen.tap(at.x, at.y);
    await sleep(300);
    let e = await state(g, t0);
    eq(e.stats && e.stats.last, "talking", "E: a tap while her answer is on its way is refused for that");
    ok(await g.until("window.__audio.plays.some((p) => p.src === 'pcm')", 15000), "E: her reply's voice arrives");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(400)", 15000), "E: …and ends");
    e = await state(g, t0);
    const pcm = e.plays.filter((p) => p.src === "pcm");
    eq(clipsOf(e).length, 0, "E: the tap made no sound");
    eq(pcm.length, 1, "E: her reply was heard, once");
    eq(e.stops.filter((s) => pcm.some((p) => p.id === s.id && s.t < p.t + p.dur - 50)).length, 0, "E: …whole");
    eq(e.stats && e.stats.hellos, 0, "E: no hello once a conversation exists");
    eq(notable(errs, aborted).length, 0, `E: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * F. A TAP WITH THE MICROPHONE OPEN (a stand-in capture: no device, no upload)
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("F");
    await unlockWithAMiss(page, g, "F");
    await g.read(`(() => { window.__recStops = 0;
      window.moxieMic.setCapture(() => Promise.resolve({ stream: null, recorder: {
        state: "inactive", mimeType: "audio/wav", ondataavailable: null, onstop: null,
        start() { this.state = "recording"; },
        stop() { if (this.state === "inactive") return; window.__recStops++; this.state = "inactive";
                 if (this.onstop) this.onstop(); } } }));
      window.moxieMic.start(); return 1; })()`);
    ok(await g.until("document.body.getAttribute('data-mic') === 'on'", 5000), "F: precondition — the microphone is open");
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await page.touchscreen.tap(at.x, at.y);
    await sleep(800);
    const f = await state(g, t1);
    eq(f.stats && f.stats.last, "mic", "F: a tap with the mic open is refused for that");
    eq(f.plays.length, 0, "F: …and nothing plays into the open microphone");
    deep(await g.json("[window.moxieMic.isRecording(), window.__recStops]"), [true, 0], "F: …and the recording goes on");
    await g.read("(() => { window.moxieMic.stop(); return 1; })()");
    eq(notable(errs, aborted).length, 0, `F: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

if (REPORT) console.log(JSON.stringify(report, null, 1));
finish(LABEL, { fails, count });
