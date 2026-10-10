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
 *   G. every greeting moxie.js can say has its clip in the manifest's `moxie` group;
 *   H. a quick tap on a busy page (900 ms of main-thread work between press and lift) is a tap;
 *   I. ONE VOICE AT A TIME: the hello holds the speakers from the tap, before its clip has
 *      loaded, so an ambient tick inside that load waits, a stub answer takes over cleanly and
 *      the mic opening stops it, and the greeting it drops never falls back to the browser
 *      voice; a line she cannot say at all lets the speakers go; I4, the same on the Piper path;
 *      I5, a live answer's server voice inside that load takes over cleanly too;
 *   J. a page whose brain is out: the first tap says no hello over ambient's degraded line,
 *      and still gets a face (no gateway, a dead one, the hour's cap), even while that line
 *      is still loading;
 *   K. a tap before /api/health has answered: a face, and the hello waits for the next tap.
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
const GREETING_FILES = GREETINGS.map((g) => (MANIFEST.moxie || {})[g]).filter(Boolean);
/* J, K. ambient.json's degraded line: her one sentence on a page whose brain is out, and so
 * her hello there. Told apart from the greetings by size, like them. */
const DEGRADED_TEXT = (JSON.parse(readFileSync(join(web, "ambient.json"), "utf8")).degraded || {}).text || "";
const degradedRel = (MANIFEST.moxie || {})[DEGRADED_TEXT];
const DEGRADED_BYTES = degradedRel && existsSync(join(web, "audio", degradedRel)) ? statSync(join(web, "audio", degradedRel)).size : null;
ok(!!DEGRADED_BYTES, "J: ambient.json's degraded line has its clip in the manifest's moxie group");
ok(!(DEGRADED_BYTES in greetingBySize), "J: …a clip no greeting shares, so the two are told apart by size");
/* I. The scripted answer to "tell me a joke" (stub.js), heard as its own clip. */
const STUB_JOKE = "Why did the robot cross the road? To recharge on the other side!";
const stubRel = (MANIFEST.moxie || {})[STUB_JOKE];
const STUB_BYTES = stubRel && existsSync(join(web, "audio", stubRel)) ? statSync(join(web, "audio", stubRel)).size : null;
ok(!!STUB_BYTES, "I: the stub joke has its clip in the manifest's moxie group");

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
const LOCAL = `${site.url}/sim.html`;   // I4: a self-hoster's page, which may reach a local Piper
// Chrome's own autoplay policy: no `--autoplay-policy=no-user-gesture-required`.
const browser = await launchBrowser(puppeteer, chrome, { autoplay: false, hosts: { "moxie.hosted.test": site.port } });

const TONE = pcmToneBase64({ seconds: 1.5, rate: 22050, freq: 440, amp: 0.6 });
const FX = await liveFixture({ eid: "sim-firsttap-1", reply: "Rockets are loud and fast!", tone: TONE });
/** I4: what a Piper sidecar answers, a RIFF/WAVE of the same tone (as in test_typed_turn.mjs),
 *  for a line no clip covers. */
const WAV = (() => {
  const pcm = Buffer.from(TONE.base64, "base64"), h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVE", 8);
  h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(TONE.rate, 24); h.writeUInt32LE(TONE.rate * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
})();
const PIPER_LINE = "A line no clip covers, so the Piper sidecar says it.";

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

/** A fresh hosted page (or `o.url`), every backend answered at the browser; NOT settled with
 *  evaluate (that would activate it). Live unless `o.health` says otherwise; `o.ready` is what
 *  "booted" means (default: live), `o.boot` runs in the page before its own scripts. */
async function open(label, o = {}) {
  const v = await openSim(browser, o.url || HOSTED, {
    health: o.health === undefined ? FX.health : o.health, viewport: o.viewport || PHONE, settle: false, route: o.route,
    beforeLoad: async (p) => {
      await p.evaluateOnNewDocument(instrumentWebAudio); await p.evaluateOnNewDocument(recordPage);
      if (o.boot) await p.evaluateOnNewDocument(o.boot);
    },
  });
  const g = await gestureFree(v.page);
  ok(await g.until("!!window.moxie && !!window.moxieAudio && !!window.moxieMode && !!window.moxieAmbient && " +
                   "document.readyState === 'complete' && " + (o.ready || "window.moxieMode.canSpendLiveTurn() === true"), 30000),
     `${label}: the hosted page booted ${o.ready ? "(" + o.ready + ")" : "live"}`);
  return { ...v, g };
}

/** Node-side wait: until `fn()` holds, polled every 50 ms. */
const waitFor = async (fn, timeout = 5000) => {
  for (let i = 0; i < timeout / 50; i++, await sleep(50)) if (fn()) return true;
  return false;
};
/** Holds every request for a greeting's clip while `stall` is set, until `free()`: the
 *  hello's load window, as long as a block needs it on any runner. (Request interception
 *  turns the page's cache off, so every fetch of a clip comes through here.) */
function greetingNet() {
  const net = { stall: false, held: [] };
  net.route = (r, u) => (net.stall && GREETING_FILES.some((f) => u.endsWith("/audio/" + f)) ? (net.held.push(r), true) : false);
  net.free = () => {
    net.stall = false;
    for (const r of net.held.splice(0)) { try { r.continue(); } catch (e) {} }
  };
  return net;
}
/** The longest stretch, in ms, during which two of her voices played at once: each runs from
 *  its start to its end, or to the stop() that cut it (the review's probe Z4). */
function overlapMs(s) {
  const runs = s.plays.map((p) => {
    const cut = s.stops.filter((x) => x.id === p.id && x.t >= p.t).map((x) => x.t);
    return { t: p.t, end: Math.min(p.t + p.dur, ...cut) };
  });
  let worst = 0;
  for (let i = 0; i < runs.length; i++) for (let j = i + 1; j < runs.length; j++)
    worst = Math.max(worst, Math.min(runs[i].end, runs[j].end) - Math.max(runs[i].t, runs[j].t));
  return worst;
}
/** `stop()` calls that cut `p` more than 50 ms before its audio ran out. */
const cutsOf = (s, p) => s.stops.filter((x) => x.id === p.id && x.t < p.t + p.dur - 50);
/** Her bubble's text, and every face and Bht_* tree played from here on: RECORDED, never
 *  sampled mid-motion (A). */
const spyOnHer = (g) => g.read("(() => { const B = window.__moxieBridge, bt = B.behaviourTree, m = window.moxie, sf = m.setFace; " +
  "window.__trees = []; window.__faces = []; " +
  "B.behaviourTree = function (n) { window.__trees.push(n); return bt.apply(this, arguments); }; " +
  "m.setFace = function (f) { window.__faces.push(f); return sf.apply(this, arguments); }; return 1; })()");
/** Her idle quips stopped once the unlock has started them (`ambient.js` starts on the
 *  unlock): the blocks below measure taps, and a quip 5-9 s on would be one more voice. */
async function quipsOff(g) {
  await g.until("window.moxieAudio.isUnlocked() && window.__ambient.state().running", 5000);
  await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");
}

/* A TAP TIMED LIKE A FINGER. puppeteer's touchscreen.tap / mouse.click send the lift only once
 * the page has handled the press, so on a loaded runner a page busy at the press made a quick
 * tap look like a 900 ms hold, by the events' own clocks too (measured: timeStamp 911 ms
 * apart). scene.js times a tap by those clocks, as a real phone's finger is timed, so these
 * send the press and the lift back to back, without waiting on the page: the browser stamps
 * both as they arrive (measured: 0 ms apart, trusted, the click and its activation intact)
 * however long the page then takes. No clock is read here. */
async function fingerTap(page, x, y) {
  const s = await page.target().createCDPSession();
  try {
    await Promise.all([
      s.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] }),
      s.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] })]);
  } finally { await s.detach().catch(() => {}); }
}
async function mouseClick(page, x, y) {
  const s = await page.target().createCDPSession();
  try {
    await s.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await Promise.all([
      s.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 }),
      s.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 })]);
  } finally { await s.detach().catch(() => {}); }
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
/** Until the page has RECORDED `n` gestures on the stage (taps on her plus misses): what a tap
 *  did is read after it is recorded, never after a fixed sleep a loaded runner can outlast. */
const recorded = (g, n) => g.until(`(() => { const t = window.moxie.tapStats(); return t.taps + t.misses >= ${n}; })()`, 10000);
/** A miss on the stage: it unlocks audio like any tap and must say nothing; then her idle
 *  quips are stopped, since this suite measures taps (`ambient.js` starts on the unlock). */
async function unlockWithAMiss(page, g, label) {
  const off = await offHer(g);
  ok(!!off, `${label}: found a point on the stage clear of her`);
  if (off) await fingerTap(page, off.x, off.y);
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
    // …and so is her face: a later idle drift ("curious") must not be what a slow read sees.
    await g.read("(() => { const m = window.moxie, sf = m.setFace; window.__faces = []; " +
                 "m.setFace = function (f) { window.__faces.push(f); return sf.apply(this, arguments); }; return 1; })()");
    const at = await her(g);
    eq(at.on, "CANVAS", `A: her centre (${at.x}, ${at.y}) is the 3-D stage, not page chrome`);
    await fingerTap(page, at.x, at.y);
    ok(await g.until("window.__audio.plays.some((p) => p.src === 'clip')", 8000), "A: a clip plays after the first tap on her");
    const a = await state(g);
    const clips = clipsOf(a);
    eq(clips.length, 1, "A: exactly one clip");
    const said = clips.length ? greetingBySize[clips[0].bytes] : undefined;
    ok(!!said, `A: …and it is one of the three greetings, by size (${clips.length ? clips[0].bytes : "-"} bytes)`);
    eq(a.stats && a.stats.said, said, "A: …the one moxie.js says it chose");
    /* The 1,500 ms ACCEPTANCE bar is the journey probe's (F1: warm host, N=5 per profile, worst
     * 609 ms of 23). This is one cold first page on a shared CI runner, which once took 1,503 ms;
     * its bar only has to tell the hello from the idle quip it replaced (5-9 s on), with room. */
    const ms = clips.length && a.up !== null ? clips[0].t - a.up : null;
    ok(ms !== null && ms >= 0 && ms <= 2500,
       `A: her first sound comes ${ms} ms after the finger lifts (bar here: 2,500 ms; F1's 1,500 ms is the probe's; before, an idle quip 5-9 s on)`);
    deep(a.stats && [a.stats.taps, a.stats.hellos, a.stats.faces, a.stats.last], [1, 1, 0, "hello"],
         "A: tapStats — one tap on her, one hello");
    ok(await g.read("window.moxieAudio.isUnlocked()"), "A: the tap's own gesture unlocked audio");
    const trees = await g.json("window.__trees");
    ok(trees.includes("Bht_Gesture_Greet"), `A: she waves (Bht_Gesture_Greet; played ${JSON.stringify(trees)})`);
    const faces = await g.json("window.__faces");
    ok(faces.includes("happy"), `A: …with a happy face while she says it (setFace recorded ${JSON.stringify(faces)})`);
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
    await fingerTap(page, at2.x, at2.y);
    ok(await recorded(g, 2), "B: the second tap is recorded");
    await sleep(1500);                                  // and nothing sounds after it
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
    if (off) await mouseClick(page, off.x, off.y);
    ok(await recorded(g, 1), "C: the click beside her is recorded");
    await sleep(1200);                                  // and nothing sounds after it
    await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");
    let c = await state(g);
    deep(c.stats && [c.stats.taps, c.stats.misses, c.stats.hellos], [0, 1, 0],
         "C: a drag across her is no tap and a click beside her is a miss — no hello");
    eq(clipsOf(c).length, 0, "C: …and no sound");
    at = await her(g);                                  // she turned with the orbit
    await mouseClick(page, at.x, at.y);
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
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "D: the tap while she speaks is recorded");
    let d = await state(g, t1);
    eq(d.stats && d.stats.last, "speaking", "D: a tap while she speaks is refused for that");
    eq(d.plays.length, 0, "D: …makes no sound");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(700)", 15000), "D: her quip ends");
    d = await state(g, t1);
    eq(d.stops.filter((s) => quip && s.id === quip.id && s.t < quip.t + quip.dur - 50).length, 0,
       "D: …uncut by the tap");
    const at2 = await her(g);
    await fingerTap(page, at2.x, at2.y);
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
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "E: the tap during the turn is recorded");
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
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "F: the tap with the mic open is recorded");
    await sleep(800);                                   // and nothing plays into it
    const f = await state(g, t1);
    eq(f.stats && f.stats.last, "mic", "F: a tap with the mic open is refused for that");
    eq(f.plays.length, 0, "F: …and nothing plays into the open microphone");
    deep(await g.json("[window.moxieMic.isRecording(), window.__recStops]"), [true, 0], "F: …and the recording goes on");
    await g.read("(() => { window.moxieMic.stop(); return 1; })()");
    eq(notable(errs, aborted).length, 0, `F: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * H. A QUICK TAP ON A BUSY PAGE. A slow phone's first frames can hold the main thread for
   * most of a second, and the first tap lands right then: the finger is down for 10 ms, but
   * the page runs the lift's handler 900 ms after the press's. Timed by when the handlers ran,
   * that was a press-and-hold and the tap was dropped (on a loaded runner, case A once saw its
   * first tap counted as nothing at all); timed by the events' own clocks it is a tap. Here the
   * lift is CREATED 10 ms after the press and DISPATCHED after 900 ms of main-thread work.
   * Synthetic events grant no activation, so the voice is stubbed: tapStats is the record.
   * ===================================================================== */
  {
    const { page, g, errs, aborted } = await open("H", { viewport: { width: 1280, height: 800 } });
    const at = await her(g);
    const r = await g.json(`(() => {
      window.moxieAudio.speak = () => Promise.resolve(false);   // no activation: no sound to make
      const c = document.elementFromPoint(${at.x}, ${at.y});
      const o = { pointerId: 1, pointerType: "mouse", isPrimary: true, button: 0, buttons: 1,
                  clientX: ${at.x}, clientY: ${at.y}, bubbles: true, cancelable: true, composed: true };
      const down = new PointerEvent("pointerdown", o);
      const t0 = performance.now(); while (performance.now() - t0 < 10) {}
      const up = new PointerEvent("pointerup", Object.assign({}, o, { buttons: 0 }));
      c.dispatchEvent(down);
      const t1 = performance.now(); while (performance.now() - t1 < 900) {}   // a long frame
      c.dispatchEvent(up);
      return { on: c.tagName, held: Math.round(up.timeStamp - down.timeStamp),
               busy: Math.round(performance.now() - t1), stats: window.moxie.tapStats() };
    })()`);
    ok(r.on === "CANVAS" && r.held < 100 && r.busy >= 900,
       `H: precondition — on the stage, the finger down ${r.held} ms, the page busy ${r.busy} ms in between`);
    deep(r.stats && [r.stats.taps, r.stats.hellos, r.stats.last], [1, 1, "hello"],
         `H: a quick tap on a busy page is still a tap on her — and her hello (tapStats ${JSON.stringify(r.stats)})`);
    // The NEGATIVE CONTROL: a real 900 ms hold whose two events are then handled back to back
    // (queued behind a long frame) is still a hold, not a tap: the clock is the events'.
    const h = await g.json(`(() => {
      const c = document.elementFromPoint(${at.x}, ${at.y});
      const o = { pointerId: 1, pointerType: "mouse", isPrimary: true, button: 0, buttons: 1,
                  clientX: ${at.x}, clientY: ${at.y}, bubbles: true, cancelable: true, composed: true };
      const down = new PointerEvent("pointerdown", o);
      const t0 = performance.now(); while (performance.now() - t0 < 900) {}   // the finger stays down
      const up = new PointerEvent("pointerup", Object.assign({}, o, { buttons: 0 }));
      c.dispatchEvent(down); c.dispatchEvent(up);
      return { held: Math.round(up.timeStamp - down.timeStamp), stats: window.moxie.tapStats() };
    })()`);
    ok(h.held >= 900, `H: precondition — the second press was held ${h.held} ms`);
    deep(h.stats && r.stats && [h.stats.taps, h.stats.misses], r.stats && [r.stats.taps, r.stats.misses],
         `H: …and a held press is no tap (nor a miss), however late it is handled (tapStats ${JSON.stringify(h.stats)})`);
    eq(notable(errs, aborted).length, 0, `H: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * I. ONE VOICE AT A TIME: the hello holds the speakers from the tap, before its clip has
   * loaded (the W4-S6 review's probe Z4). On a page another gesture had unlocked, a tap on her
   * and then ONE ambient tick while the greeting was still loading: the tick asked "is she
   * speaking?", heard no (nothing had started yet), and its quip and the greeting played
   * together: 3.1 s of two voices in the probe, 3.7 s in this block before the fix. The
   * greeting's fetch is HELD so the tick lands inside the load window on any runner;
   * `moxieAmbient.say()` runs the same tick() a timer would.
   * ===================================================================== */
  {
    const net = greetingNet();
    const { page, g, errs, aborted } = await open("I", { route: net.route });
    await unlockWithAMiss(page, g, "I");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "I: precondition — she is quiet");
    const t1 = await g.read("Math.round(performance.now())");
    net.stall = true;
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "I: the tap on her is recorded");
    ok(await waitFor(() => net.held.length >= 1), "I: precondition — the greeting is still loading (its fetch is held)");
    const tick = await g.json("(() => { const busy = window.moxieAudio.isMoxieBusy(1600); window.moxieAmbient.say(); return { busy }; })()");
    await sleep(1500);                                  // a quip the tick let through loads and starts here
    net.free();
    ok(await g.until(`window.__audio.plays.some((p) => p.src === 'clip' && p.t >= ${t1})`, 8000), "I: a clip plays");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "I: …and she falls quiet");
    await g.read("(() => { try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");
    const s = await state(g, t1);
    eq(overlapMs(s), 0, `I: one voice at a time — no two of her voices overlap (${JSON.stringify(s.plays)})`);
    eq(tick.busy, true, "I: the tick found her busy: the hello held the speakers from the tap, before its clip had loaded");
    deep(clipsOf(s).map((p) => !!greetingBySize[p.bytes]), [true], "I: …so the greeting is the one voice heard, once");
    eq(clipsOf(s).filter((p) => cutsOf(s, p).length).length, 0, "I: …and whole");
    deep(s.stats && [s.stats.hellos, s.stats.last], [1, "hello"], "I: tapStats — the hello");
    // A line she has no way to say at all (no clip, no browser voice) lets the speakers go:
    // held, she would read as speaking for good, and no quip or child line would start again.
    await g.read(`(() => { Object.defineProperty(window, "speechSynthesis", { value: undefined, configurable: true });
      window.__said = null;
      window.moxieAudio.speak("A line with no clip and no voice to say it.").then((ok) => {
        window.__said = { ok, speaking: window.moxieAudio.isMoxieSpeaking() }; });
      return 1; })()`);
    ok(await g.until("window.__said !== null", 8000), "I: a line she cannot say settles");
    deep(await g.json("window.__said"), { ok: false, speaking: false },
         "I: …unsaid, and it lets the speakers go (she does not read as speaking for good)");
    eq(notable(errs, aborted).length, 0, `I: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
  /* I2. A STUB LINE inside the same window: the visitor sends a line while the greeting is
   * still loading, and the turn's refusal (429) is answered from her recorded lines. The newer
   * reply takes the speakers, as it does from a greeting already playing: the answer is heard
   * once, and the greeting, still loading under it, never starts on top of it. */
  {
    const net = greetingNet();
    const { page, g, errs, aborted } = await open("I2", {
      route: (r, u) => {
        if (net.route(r, u)) return true;
        if (/\/api\/chat\b/.test(u)) {
          json(r, FX.env({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 1, mode: "live" }), 429);
          return true;
        }
        return false;
      },
    });
    await unlockWithAMiss(page, g, "I2");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "I2: precondition — she is quiet");
    const t1 = await g.read("Math.round(performance.now())");
    net.stall = true;
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "I2: the tap on her is recorded");
    ok(await waitFor(() => net.held.length >= 1), "I2: precondition — the greeting is still loading (its fetch is held)");
    await g.read("(() => { window.moxieTypedTurn.send('tell me a joke'); return 1; })()");
    ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${STUB_BYTES} && p.t >= ${t1})`, 8000),
       "I2: the stub answer plays while the greeting is still loading");
    net.free();
    await sleep(1500);                                  // the released greeting decodes here
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "I2: …and she falls quiet");
    const s = await state(g, t1);
    eq(overlapMs(s), 0, `I2: one voice at a time — the stub answer and the greeting never overlap (${JSON.stringify(s.plays)})`);
    deep(clipsOf(s).map((p) => p.bytes), [STUB_BYTES],
         "I2: the newer reply has the speakers: the answer heard once, and no greeting started under it");
    eq(clipsOf(s).filter((p) => cutsOf(s, p).length).length, 0, "I2: …the answer whole");
    deep(s.speech, [], "I2: …and the greeting it dropped never falls back to the browser voice under it");
    aborted.refused++;                                  // the 429 is the fixture's
    eq(notable(errs, aborted).length, 0, `I2: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
  /* I3. THE MIC OPENS inside the same window (Listen, right after the tap): opening it stops
   * her, and the greeting still loading is stopped with her, so nothing plays into the open
   * microphone (F's rule, from the other side). */
  {
    const net = greetingNet();
    const { page, g, errs, aborted } = await open("I3", { route: net.route });
    await unlockWithAMiss(page, g, "I3");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "I3: precondition — she is quiet");
    const t1 = await g.read("Math.round(performance.now())");
    net.stall = true;
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "I3: the tap on her is recorded");
    ok(await waitFor(() => net.held.length >= 1), "I3: precondition — the greeting is still loading (its fetch is held)");
    await g.read(`(() => {
      window.moxieMic.setCapture(() => Promise.resolve({ stream: null, recorder: {
        state: "inactive", mimeType: "audio/wav", ondataavailable: null, onstop: null,
        start() { this.state = "recording"; },
        stop() { if (this.state === "inactive") return; this.state = "inactive"; if (this.onstop) this.onstop(); } } }));
      window.moxieMic.start(); return 1; })()`);
    ok(await g.until("document.body.getAttribute('data-mic') === 'on'", 5000), "I3: precondition — the microphone is open");
    net.free();
    await sleep(1500);                                  // the released greeting decodes here
    const s = await state(g, t1);
    deep(await g.json("[window.moxieMic.isRecording(), document.body.getAttribute('data-mic')]"), [true, "on"],
         "I3: …the microphone is still open");
    eq(s.plays.length, 0, `I3: nothing plays into the open microphone — the greeting was stopped with her (${JSON.stringify(s.plays)})`);
    deep(s.speech, [], "I3: …nor in the browser voice: the greeting it dropped never falls back to it");
    eq(notable(errs, aborted).length, 0, `I3: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* I4. THE SAME RULE ON THE PIPER PATH. On a self-hoster's page (sim/serve.py: no
   * /api/health, so `offline`) a line no clip covers goes to the local Piper sidecar and holds
   * the speakers while Piper renders it (voice/local.js waits up to 1.4 s). A newer line in that
   * wait takes the speakers, and the first line, its audio arriving late, neither plays under
   * it nor falls back to the browser voice. The newer line is spoken 50 ms after the page asks
   * Piper, and Piper's answer is HELD until the newer line plays. */
  {
    const hold = { held: [] };
    const { page, g, errs, aborted } = await open("I4", {
      url: LOCAL, health: null, ready: "window.moxieMode.state() === 'offline'",
      route: (r, u) => (/:8081\/tts\b/.test(u) ? (hold.held.push(r), true) : false),
    });
    await unlockWithAMiss(page, g, "I4");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "I4: precondition — she is quiet");
    const t1 = await g.read("Math.round(performance.now())");
    await g.read(`(() => {
      window.__i4 = { asked: 0, busy: null };
      const f = window.fetch;
      window.fetch = function (url) {
        if (String(url).indexOf(":8081/tts") !== -1 && !window.__i4.asked++) setTimeout(() => {
          window.__i4.busy = window.moxieAudio.isMoxieSpeaking();
          window.moxieAudio.speak(${JSON.stringify(STUB_JOKE)});
        }, 50);
        return f.apply(this, arguments);
      };
      window.moxieAudio.speak(${JSON.stringify(PIPER_LINE)});
      return 1; })()`);
    ok(await waitFor(() => hold.held.length >= 1), "I4: precondition — the line went to Piper, and its answer is held");
    ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${STUB_BYTES} && p.t >= ${t1})`, 8000),
       "I4: the newer line plays while Piper is still rendering the first");
    for (const r of hold.held.splice(0)) {
      try { r.respond({ status: 200, contentType: "audio/wav", headers: { "Access-Control-Allow-Origin": "*" }, body: WAV }); } catch (e) {}
    }
    await sleep(1500);                                  // Piper's late answer decodes here
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "I4: …and she falls quiet");
    const s = await state(g, t1);
    eq(await g.json("window.__i4.busy"), true, "I4: the first line held the speakers while Piper rendered it");
    eq(overlapMs(s), 0, `I4: one voice at a time — Piper's late answer never plays under the newer line (${JSON.stringify(s.plays)})`);
    deep(clipsOf(s).map((p) => p.bytes), [STUB_BYTES], "I4: the newer line is the one voice heard");
    deep(s.speech, [], "I4: …and the line it dropped never falls back to the browser voice");
    eq(notable(errs, aborted).length, 0, `I4: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
  /* I5. HER LIVE VOICE inside the same window: the line the visitor sends is answered by the
   * live brain, and the answer's server voice (voice/cloud.js) starts while the greeting is
   * still loading, as on a network where a clip takes longer than a turn. That voice cuts the
   * local voice holding the speakers, the greeting's claim included, so the greeting, released
   * once the answer has begun, never starts under it. I2 is the scripted answer; this is the
   * path a live page takes. */
  {
    const net = greetingNet();
    const { page, g, errs, aborted } = await open("I5", {
      route: (r, u) => {
        if (net.route(r, u)) return true;
        if (/\/api\/chat\b/.test(u)) { json(r, FX.chat); return true; }
        if (/\/api\/speech\b/.test(u)) { json(r, FX.speech); return true; }
        return false;
      },
    });
    await unlockWithAMiss(page, g, "I5");
    ok(await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "I5: precondition — she is quiet");
    const t1 = await g.read("Math.round(performance.now())");
    net.stall = true;
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "I5: the tap on her is recorded");
    ok(await waitFor(() => net.held.length >= 1), "I5: precondition — the greeting is still loading (its fetch is held)");
    await g.read("(() => { window.moxieTypedTurn.send('tell me about rockets'); return 1; })()");
    ok(await g.until(`window.__audio.plays.some((p) => p.src === 'pcm' && p.t >= ${t1})`, 15000),
       "I5: her live answer's voice starts while the greeting is still loading");
    net.free();
    await sleep(1500);                                  // the released greeting decodes here
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "I5: …and she falls quiet");
    const s = await state(g, t1);
    const pcm = s.plays.filter((p) => p.src === "pcm");
    eq(overlapMs(s), 0, `I5: one voice at a time — the live answer and the greeting never overlap (${JSON.stringify(s.plays)})`);
    eq(pcm.length, 1, "I5: her live answer is heard, once");
    eq(clipsOf(s).length, 0, "I5: …and the greeting, dropped by it, never starts under it");
    eq(pcm.filter((p) => cutsOf(s, p).length).length, 0, "I5: …the answer whole");
    deep(s.speech, [], "I5: …nor does the greeting fall back to the browser voice");
    eq(notable(errs, aborted).length, 0, `I5: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * J. A PAGE WHOSE BRAIN IS OUT. ambient.js arms its one degraded line before any gesture
   * and says it on the unlock: that line is her hello there. So the FIRST tap on her, which
   * is the unlock, says no hello over it, and still gets its face. Three ways out: no gateway
   * (from /api/health), a dead one (upstream_down) and the hour's cap (RESTING), the last two
   * as a turn would report them to the mode machine. Before this pin, removing the refusal
   * (`brainOut()`) reddened no suite. J1b: unlocked by a miss, her line said, then a tap on
   * her: still no hello, which without the refusal would greet a second time.
   * ===================================================================== */
  for (const [label, how] of [
    ["J1 gateway_not_configured", { health: FX.bareHealth }],
    ["J2 upstream_down", { note: { status: 503, reason: "upstream_down", retry_after_s: 0 } }],
    ["J3 resting (the hour's cap)", { note: { status: 429, reason: "rate_limited", retry_after_s: 1020 } }],
  ]) {
    const { page, g, errs, aborted } = await open(label, how.health ? { health: how.health, ready: "window.moxieMode.state() === 'degraded'" } : {});
    if (how.note) await g.read(`(() => { window.moxieMode.note(${JSON.stringify(how.note)}); return 1; })()`);
    ok(await g.until("window.moxieMode.state() === 'degraded' && window.moxieAmbient.degradedState().pending === true && " +
                     "!window.moxieAudio.isUnlocked()", 15000),
       `${label}: precondition — the page is degraded, audio still locked, and her degraded line armed for the unlock`);
    await spyOnHer(g);
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 1), `${label}: the tap on her is recorded`);
    ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${DEGRADED_BYTES})`, 8000),
       `${label}: her degraded line plays on the unlock`);
    await quipsOff(g);
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), `${label}: …and she falls quiet`);
    const s = await state(g, t1);
    deep(s.stats && [s.stats.taps, s.stats.hellos, s.stats.faces, s.stats.last], [1, 0, 1, "brain-out"],
         `${label}: the first tap is refused for that: no hello, a face`);
    const seen = await g.json("({ trees: window.__trees, faces: window.__faces })");
    ok(seen.faces.includes("blink"), `${label}: …the face-only response still happens (setFace recorded ${JSON.stringify(seen.faces)})`);
    ok(!seen.trees.includes("Bht_Gesture_Greet"), `${label}: …and no wave (played ${JSON.stringify(seen.trees)})`);
    deep(clipsOf(s).map((p) => p.bytes), [DEGRADED_BYTES], `${label}: her degraded line is the one voice heard, once`);
    eq(clipsOf(s).filter((p) => cutsOf(s, p).length).length, 0, `${label}: …whole`);
    eq(overlapMs(s), 0, `${label}: …with nothing over it (${JSON.stringify(s.plays)})`);
    eq(notable(errs, aborted).length, 0, `${label}: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
  {
    const { page, g, errs, aborted } = await open("J1b", { health: FX.bareHealth, ready: "window.moxieMode.state() === 'degraded'" });
    await unlockWithAMiss(page, g, "J1b");
    ok(await g.until("window.moxieAmbient.degradedState().said === true", 8000), "J1b: precondition — the unlock said her degraded line");
    ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${DEGRADED_BYTES})`, 8000) &&
       await g.until("!window.moxieAudio.isMoxieBusy(1700)", 15000), "J1b: …and it is over");
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 2), "J1b: the tap on her is recorded");
    await sleep(1500);                                  // and nothing sounds after it
    const s = await state(g, t1);
    eq(s.plays.length, 0, `J1b: a tap after her degraded line says no second hello (${JSON.stringify(s.plays)})`);
    deep(s.stats && [s.stats.hellos, s.stats.faces, s.stats.last], [0, 1, "brain-out"], "J1b: …it is a face");
    eq(notable(errs, aborted).length, 0, `J1b: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
  /* J4. THE ANSWER IS IN, HER LINE IS NOT. On a page nobody has touched, ambient.js asks for
   * ambient.json (where her degraded line lives) only once mode.js says `degraded`, and it is
   * served no-cache, so that is one more round trip on every visit. A first tap inside it saw
   * no boot and no armed line, said hello, and the line, once loaded, took the speakers from
   * the greeting. The page already knows her brain is out (mode.js): that is the refusal.
   * ambient.json is HELD until the tap is recorded. */
  {
    const hold = { on: true, held: [] };
    const { page, g, errs, aborted } = await open("J4", {
      health: FX.bareHealth, ready: "window.moxieMode.state() === 'degraded'",
      route: (r, u) => {
        if (!hold.on || !/\/ambient\.json\b/.test(u)) return false;
        hold.held.push(r);
        return true;
      },
    });
    ok(await waitFor(() => hold.held.length >= 1, 10000), "J4: precondition — ambient.json was asked for, and is held");
    deep(await g.json("[window.moxieMode.state(), window.moxieAmbient.degradedState().text, window.moxieAudio.isUnlocked()]"),
         ["degraded", null, false], "J4: precondition — the answer says her brain is out, her line has not loaded, audio is locked");
    await spyOnHer(g);
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 1), "J4: the tap on her is recorded");
    const tapped = await g.json("window.moxie.tapStats()");
    deep(tapped && [tapped.taps, tapped.hellos, tapped.faces, tapped.last], [1, 0, 1, "brain-out"],
         "J4: a tap once the answer says her brain is out, her line still loading, is refused for that: no hello, a face");
    await sleep(1200);                                  // a hello it let through would load and start here
    hold.on = false;
    for (const r of hold.held.splice(0)) { try { r.continue(); } catch (e) {} }
    ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${DEGRADED_BYTES})`, 8000),
       "J4: her degraded line plays once it has loaded");
    await quipsOff(g);
    ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "J4: …and she falls quiet");
    const s = await state(g, t1);
    const seen = await g.json("({ trees: window.__trees, faces: window.__faces })");
    ok(seen.faces.includes("blink"), `J4: …the face-only response (setFace recorded ${JSON.stringify(seen.faces)})`);
    ok(!seen.trees.includes("Bht_Gesture_Greet"), `J4: …and no wave (played ${JSON.stringify(seen.trees)})`);
    deep(clipsOf(s).map((p) => p.bytes), [DEGRADED_BYTES], "J4: her degraded line is the one voice heard: no greeting before it");
    eq(clipsOf(s).filter((p) => cutsOf(s, p).length).length, 0, `J4: …whole (${JSON.stringify(s.stops)})`);
    eq(overlapMs(s), 0, `J4: …with nothing over it (${JSON.stringify(s.plays)})`);
    eq(notable(errs, aborted).length, 0, `J4: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * K. A TAP BEFORE /api/health HAS ANSWERED (the review's probe Z3). Until then the page
   * cannot know whether her brain is out, so whether ambient's degraded line is about to be
   * her hello: such a tap said hello, and the degraded line, once the answer came, cut the
   * greeting about a second in. Now it is a face, and the hello stays for the next tap once
   * the page knows it is live. The answer is HELD until the tap is recorded (the probe's own
   * 6 s abort is lifted, so a slow runner cannot turn the wait into `offline`), then given:
   * K1 no gateway, K2 live.
   * ===================================================================== */
  for (const [label, body] of [["K1 no gateway", FX.bareHealth], ["K2 live", FX.health]]) {
    const hold = { held: [], body: null };
    const { page, g, errs, aborted } = await open(label, {
      ready: "window.moxieMode.state() === 'boot'",
      boot: () => { if (typeof AbortSignal !== "undefined") AbortSignal.timeout = () => new AbortController().signal; },
      route: (r, u) => {
        if (!/\/api\/health\b/.test(u)) return false;
        if (hold.body) json(r, hold.body); else hold.held.push(r);
        return true;
      },
    });
    ok(await waitFor(() => hold.held.length >= 1, 10000), `${label}: precondition — /api/health was asked, and has not answered`);
    ok(!(await g.read("window.moxieAudio.isUnlocked()")), `${label}: precondition — audio still locked: this tap is the first gesture`);
    await spyOnHer(g);
    const t1 = await g.read("Math.round(performance.now())");
    const at = await her(g);
    await fingerTap(page, at.x, at.y);
    ok(await recorded(g, 1), `${label}: the tap on her is recorded`);
    const tapped = await g.json("({ mode: window.moxieMode.state(), stats: window.moxie.tapStats() })");
    eq(tapped.mode, "boot", `${label}: precondition — the tap landed before the answer`);
    deep(tapped.stats && [tapped.stats.taps, tapped.stats.hellos, tapped.stats.faces, tapped.stats.last], [1, 0, 1, "booting"],
         `${label}: a tap before the page knows her state is refused for that: no hello, a face`);
    ok((await g.json("window.__faces")).includes("blink"), `${label}: …the face-only response`);
    await sleep(1200);                                  // a hello it let through would load and start here
    hold.body = body;
    for (const r of hold.held.splice(0)) json(r, body);
    if (label.startsWith("K1")) {
      ok(await g.until(`window.__audio.plays.some((p) => p.bytes === ${DEGRADED_BYTES})`, 8000),
         "K1: the answer says her brain is out, and her degraded line plays");
      await quipsOff(g);
      ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "K1: …and she falls quiet");
      const s = await state(g, t1);
      deep(clipsOf(s).map((p) => p.bytes), [DEGRADED_BYTES], "K1: her degraded line is the one voice heard: no greeting before it");
      eq(clipsOf(s).filter((p) => cutsOf(s, p).length).length, 0, `K1: …whole (${JSON.stringify(s.stops)})`);
      eq(overlapMs(s), 0, `K1: …with nothing over it (${JSON.stringify(s.plays)})`);
    } else {
      ok(await g.until("window.moxieMode.canSpendLiveTurn() === true", 8000), "K2: the answer says she is live");
      await quipsOff(g);
      ok(await g.until("!window.moxieAudio.isMoxieBusy(300)", 15000), "K2: she is quiet");
      eq(clipsOf(await state(g, t1)).length, 0, "K2: nothing has sounded since the refused tap");
      const t2 = await g.read("Math.round(performance.now())");
      const at2 = await her(g);
      await fingerTap(page, at2.x, at2.y);
      ok(await g.until(`window.__audio.plays.some((p) => p.src === 'clip' && p.t >= ${t2})`, 8000),
         "K2: once the page knows she is live, the next tap on her makes a sound");
      const s = await state(g, t2);
      ok(clipsOf(s).length === 1 && !!greetingBySize[clipsOf(s)[0].bytes], "K2: …the hello the refused tap did not spend");
      deep(s.stats && [s.stats.taps, s.stats.hellos, s.stats.last], [2, 1, "hello"], "K2: tapStats — the second tap's hello");
    }
    eq(notable(errs, aborted).length, 0, `${label}: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
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
