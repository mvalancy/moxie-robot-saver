/* test_one_voice.mjs — every reply spoken ONCE, in one voice, in a REAL browser on a phone.
 *
 * test_cloud_transport.mjs §8–10 prove the rules on the real scripts under fakes; this drives
 * the shipped sim.html in Chrome with its REAL autoplay policy (no autoplay flag) and raw
 * touch input, and records sound where it is made: Web Audio buffers started and stopped
 * (`instrumentWebAudio`: "pcm" = her gateway voice, "clip" = a shipped file) and every
 * speechSynthesis utterance (replaced by a recorder: headless Chrome has no voices).
 *   A. a finger's tap unlocks audio on its touchend (an activation): its pointerdown and
 *      touchstart cannot start audio, and the unlock used to run — and be announced — there;
 *   B. a first turn whose voice takes 4 s starts NO browser voice: hers plays once;
 *   C. after that voiced turn, a 429 stub answer, a safety redirect and a reply whose voice
 *      was refused (503) are each heard exactly once, and the 503 leaves the page live;
 *   D. the thinking filler never cuts her previous answer.
 * No gateway and no network: `/api/*` is answered at the browser (openSim).
 *
 *   node sim/test_one_voice.mjs [--report]   (--report prints the measured timeline)
 */
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, launchBrowser, openSim,
         liveFixture, instrumentWebAudio, notable, web, PHONE } from "./browser_harness.mjs";

const LABEL = "one-voice test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const REPORT = process.argv.includes("--report");
const report = {};

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
// Chrome's own autoplay policy: no `--autoplay-policy=no-user-gesture-required`.
const browser = await launchBrowser(puppeteer, chrome, { autoplay: false, hosts: { "moxie.hosted.test": site.port } });

const MANIFEST = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8"));
const clipBytes = (text) => (MANIFEST.moxie[text] ? statSync(join(web, "audio", MANIFEST.moxie[text])).size : null);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const json = (r, body, status = 200) => r.respond({ status, contentType: "application/json", body });

/** PAGE-SIDE: speechSynthesis as a recorder (~70 ms a character, like a browser voice), the
 *  unlock announcements, and the time origin every recorded event is measured from. */
function recordVoices() {
  window.__speech = [];
  window.__unlocks = 0;
  window.addEventListener("moxie-audio-unlocked", () => { window.__unlocks++; });
  let cur = null;
  const t = () => Math.round(performance.now());
  const fake = {
    speaking: false, pending: false, paused: false, getVoices: () => [],
    speak(u) {
      window.__speech.push({ ev: "speak", text: u.text, t: t() });
      cur = u; fake.speaking = true;
      setTimeout(() => { try { u.onstart && u.onstart(); } catch (e) {} }, 0);
      u.__timer = setTimeout(() => {
        if (cur === u) { cur = null; fake.speaking = false; }
        try { u.onend && u.onend(); } catch (e) {}
      }, 70 * u.text.length);
    },
    cancel() {
      if (!cur) return;
      window.__speech.push({ ev: "cut", text: cur.text, t: t() });
      clearTimeout(cur.__timer);
      const u = cur; cur = null; fake.speaking = false;
      try { u.onerror && u.onerror({ error: "interrupted" }); } catch (e) {}
    },
    pause() {}, resume() {}, addEventListener() {}, removeEventListener() {},
  };
  Object.defineProperty(window, "speechSynthesis", { value: fake, configurable: true });
}

/** Page access that grants NO user activation. Puppeteer's evaluate — and `$`, `tap()` and
 *  `setContent`, which use it — runs with `userGesture: true`, which ACTIVATES the page: one
 *  such call before the tap and every AudioContext may start, so block A would pass on any
 *  code (measured: a context made at load reads "running" after one, "suspended" without). */
async function gestureFree(page) {
  const s = await page.target().createCDPSession();
  const read = async (expr) => {
    const r = await s.send("Runtime.evaluate", { expression: expr, userGesture: false, returnByValue: true });
    if (r.exceptionDetails) throw new Error("gesture-free read failed: " + expr.slice(0, 60));
    return r.result.value;
  };
  /** A bounded poll: at most `timeout / 50` reads, 50 ms apart. */
  const until = async (expr, timeout = 20000) => {
    for (let i = 0; i < timeout / 50; i++, await sleep(50)) if (await read(expr)) return true;
    return false;
  };
  /** The centre of `sel`'s border box, from the DOM domain (no script runs). */
  const centre = async (sel) => {
    const { root } = await s.send("DOM.getDocument", { depth: 0 });
    const { nodeId } = await s.send("DOM.querySelector", { nodeId: root.nodeId, selector: sel });
    const q = (await s.send("DOM.getBoxModel", { nodeId })).model.border;
    return { x: (q[0] + q[4]) / 2, y: (q[1] + q[5]) / 2 };
  };
  return { read, until, centre };
}

/** Everything that STARTED or was CUT since `t0` (page time), as offsets from it. */
const heard = (page, t0) => page.evaluate((t0) => ({
  speech: window.__speech.filter((e) => e.t >= t0).map((e) => ({ ...e, t: e.t - t0 })),
  plays: window.__audio.plays.filter((p) => p.t >= t0)
    .map((p) => ({ id: p.id, src: p.src, bytes: p.bytes, t: Math.round(p.t - t0), dur: Math.round(p.dur) })),
  stops: window.__audio.stops.filter((s) => s.t >= t0).map((s) => ({ id: s.id, t: Math.round(s.t - t0) })),
  mode: window.moxieMode.state(),
}), t0);
const pageNow = (page) => page.evaluate(() => Math.round(performance.now()));
/** One typed turn through the same seam the Ask button uses; returns the send time. */
async function send(page, text) {
  const t0 = await pageNow(page);
  await page.evaluate((x) => window.moxieTypedTurn.send(x), text);
  return t0;
}
const until = (page, fn, arg, timeout = 15000) =>
  page.waitForFunction(fn, { timeout, polling: 50 }, arg).then(() => true, () => false);
/** Wait until no voice of hers is in the air (or within its grace beat). */
const quiet = (page) => until(page, () => !window.moxieAudio.isMoxieBusy(400), undefined, 20000);
/** A sound that is not her gateway voice: a shipped clip, or a browser-voice utterance. */
const local = (h) => h.plays.filter((p) => p.src === "clip").length + h.speech.filter((e) => e.ev === "speak").length;

const TONE3 = pcmToneBase64({ seconds: 3.0, rate: 22050, freq: 440, amp: 0.6 });
const TONE6 = pcmToneBase64({ seconds: 6.0, rate: 22050, freq: 330, amp: 0.6 });
const msg = (eid, text) => ({ topic: "/devices/d_sim/commands/remote_chat", payload: JSON.stringify({
  command: "remote_chat", result: "SUCCESS", backend: "router", event_id: eid,
  output: { text, markup: text }, end_turn: false }) });

try {
  /* =======================================================================
   * A–C. A PHONE, TOUCH, THE REAL AUTOPLAY POLICY
   * ===================================================================== */
  {
    const L1 = "I love building towers out of blocks and knocking them down!";
    const STUB = "Why did the robot cross the road? To recharge on the other side!";
    const REDIRECT = "Thank you for telling me. Feelings this big need a grown-up.";
    const L4 = "Volcanoes are mountains that can puff out hot melted rock!";
    const FX = await liveFixture({ eid: "sim-onevoice1", reply: L1, tone: TONE3 });
    const env = (o) => FX.env(o);
    const plan = [
      { chat: (r) => json(r, FX.chat), speech: (r) => setTimeout(() => json(r, FX.speech), 4000) },
      { chat: (r) => json(r, env({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 1, mode: "live" }), 429) },
      { chat: (r) => json(r, env({ ok: true, degraded: true, reason: "blocked", mode: "live",
                                    messages: [msg("sim-onevoice-blk", REDIRECT)], speech: [] })) },
      { chat: (r) => json(r, env({ ok: true, mode: "live", messages: [msg("sim-onevoice4", L4)],
                                    speech: [{ ticket: "v1.T4.MAC", event_id: "sim-onevoice4", chunk_num: 0 }] })),
        speech: (r) => json(r, env({ ok: false, degraded: true, reason: "upstream_down", retry_after_s: 0, mode: "live" }), 503) },
    ];
    let chats = -1, speeches = 0;
    const v = await openSim(browser, HOSTED, {
      health: FX.health, viewport: PHONE, settle: false,          // settling evaluates: see gestureFree
      beforeLoad: async (p) => { await p.evaluateOnNewDocument(instrumentWebAudio); await p.evaluateOnNewDocument(recordVoices); },
      route: (r, u) => {
        if (/\/api\/chat\b/.test(u)) { chats++; plan[Math.min(chats, plan.length - 1)].chat(r); return true; }
        if (/\/api\/speech\b/.test(u)) { const s = plan.filter((x) => x.speech)[speeches++]; (s || plan[0]).speech(r); return true; }
        return false;
      },
    });
    const { page, errs, aborted } = v;
    const g = await gestureFree(page);
    ok(await g.until("!!window.moxie && !!window.moxieMode && document.readyState === 'complete' && " +
                     "window.moxieMode.canSpendLiveTurn() === true"), "A: the hosted page booted live");
    /* Her idle mutters off, and kept off: an unlock announced too early restarts them, and
     * their first line (5–9 s on) resumes audio by itself once the page has been activated —
     * which would make "the tap unlocked it" pass or fail at random on code that does not. */
    await g.read("(() => { const c = document.getElementById('idle-on'); if (c) c.checked = false; " +
                 "try { window.moxieAmbient.stop(); } catch (e) {} return 1; })()");

    // A. The unlock, by a finger: down, then up.
    const audio = async () => JSON.parse(await g.read("JSON.stringify({ unlocked: window.moxieAudio.isUnlocked(), " +
      "announced: window.__unlocks, ctx: window.__moxieVoice.ctx ? window.__moxieVoice.ctx.state : null, " +
      "activated: navigator.userActivation.hasBeenActive })"));
    const s0 = await audio();
    deep([s0.unlocked, s0.announced, s0.activated], [false, 0, false],
         "A: before any gesture: audio locked, nothing announced, and the page NOT activated (so this measures the policy)");
    const pt = await g.centre("#speech-input");
    await page.touchscreen.touchStart(pt.x, pt.y);
    await sleep(200);
    const s1 = await audio();
    deep([s1.unlocked, s1.announced], [false, 0],
         `A: a finger going DOWN (pointerdown, touchstart: not activations) starts nothing and announces nothing (context ${s1.ctx})`);
    await page.touchscreen.touchEnd();
    await g.until("window.moxieAudio.isUnlocked() && window.__unlocks > 0", 5000);
    const s2 = await audio();
    deep([s2.unlocked, s2.announced, s2.ctx], [true, 1, "running"],
         "A: …and LIFTING it (touchend, an activation) unlocks audio: the context runs, announced exactly once");
    report.A = { beforeGesture: s0, fingerDown: s1, fingerUp: s2 };
    // The unlock announcement restarts her idle mutters; this suite measures replies only.
    await page.evaluate(() => { try { window.moxieAmbient.stop(); } catch (e) {} });

    // B. A first turn whose voice takes 4 s.
    let t0 = await send(page, "what do you like to play?");
    await until(page, () => window.__audio.plays.some((p) => p.src === "pcm"), undefined, 15000);
    await quiet(page);
    let h = await heard(page, t0);
    const pcm = h.plays.filter((p) => p.src === "pcm");
    eq(h.speech.filter((e) => e.ev === "speak").length, 0, "B: a 4 s voice starts NO browser voice while it is on its way");
    eq(h.plays.filter((p) => p.src === "clip").length, 0, "B: …and no clip stands in for it either");
    eq(pcm.length, 1, "B: her own voice plays exactly once");
    ok(pcm.length === 1 && pcm[0].t >= 3900, `B: …when it lands, ~4 s after the send (at ${pcm.length ? pcm[0].t : "never"} ms)`);
    eq(h.stops.filter((s) => pcm.some((p) => p.id === s.id && s.t < p.t + p.dur - 50)).length, 0, "B: …and it is never cut");
    report.B = h;

    // C. After the voiced turn: three lines nobody voices from the gateway.
    t0 = await send(page, "tell me a joke");
    await until(page, (t0) => window.__audio.plays.some((p) => p.src === "clip" && p.t >= t0) ||
                              window.__speech.some((e) => e.ev === "speak" && e.t >= t0), t0, 8000);
    await quiet(page);
    h = await heard(page, t0);
    eq(local(h), 1, `C: the 429 turn's stub answer is HEARD, exactly once (${JSON.stringify(h.plays.map((p) => p.bytes))})`);
    ok(h.plays.some((p) => p.src === "clip" && p.bytes === clipBytes(STUB)), "C: …as its own shipped clip");
    aborted.refused++;
    report.C429 = h;
    await sleep(1200);                                       // the 1 s Retry-After lapses
    await until(page, () => window.moxieMode.canSpendLiveTurn() === true);

    t0 = await send(page, "something the floor blocks");
    await until(page, (t0) => window.__speech.some((e) => e.ev === "speak" && e.t >= t0), t0, 8000);
    await quiet(page);
    h = await heard(page, t0);
    deep(h.speech.filter((e) => e.ev === "speak").map((e) => e.text), [REDIRECT], "C: the SAFETY REDIRECT is heard, once");
    eq(h.plays.filter((p) => p.src === "pcm").length, 0, "C: …with no gateway voice (it has no ticket)");
    report.Cblocked = h;

    t0 = await send(page, "tell me about volcanoes");
    await until(page, (t0) => window.__speech.some((e) => e.ev === "speak" && e.t >= t0), t0, 8000);
    await quiet(page);
    h = await heard(page, t0);
    deep(h.speech.filter((e) => e.ev === "speak").map((e) => e.text), [L4], "C: a reply whose voice was REFUSED (503) is heard, once");
    eq(h.mode, "live", "C: …and one refused voice leaves the page LIVE");
    aborted.refused++;
    report.C503 = h;

    eq(notable(errs, aborted).length, 0, `A–C: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * D. THE THINKING FILLER NEVER CUTS HER PREVIOUS ANSWER (it did, at 5,042 ms of 6,000).
   * ===================================================================== */
  {
    const FX1 = await liveFixture({ eid: "sim-onevoice-f1", reply: "Let me tell you all about the moon and the stars tonight.", tone: TONE6 });
    const FX2 = await liveFixture({ eid: "sim-onevoice-f2", reply: "Sure, I can do that!", tone: TONE3 });
    let chats = 0, speeches = 0;
    const v = await openSim(browser, HOSTED, {
      health: FX1.health, viewport: PHONE,
      beforeLoad: async (p) => { await p.evaluateOnNewDocument(instrumentWebAudio); await p.evaluateOnNewDocument(recordVoices); },
      route: (r, u) => {
        if (/\/api\/chat\b/.test(u)) { chats++; if (chats === 1) json(r, FX1.chat); else setTimeout(() => json(r, FX2.chat), 4500); return true; }
        if (/\/api\/speech\b/.test(u)) { speeches++; json(r, speeches === 1 ? FX1.speech : FX2.speech); return true; }
        return false;
      },
    });
    const { page, errs, aborted } = v;
    await page.evaluate(() => { try { window.moxieAmbient.stop(); } catch (e) {} });
    await until(page, () => window.moxieMode.canSpendLiveTurn() === true);
    // (openSim settles with evaluate, which activates the page: audio runs from the first turn.)
    const t0 = await send(page, "tell me about the moon");
    await until(page, () => window.__audio.plays.some((p) => p.src === "pcm"), undefined, 10000);
    await sleep(1000);                                      // 1 s into her 6 s answer…
    await send(page, "can you sing instead?");              // …a slow next turn: its filler beat comes 3.5 s on
    await until(page, () => window.__audio.plays.filter((p) => p.src === "pcm").length >= 2, undefined, 15000);
    await quiet(page);
    const h = await heard(page, t0);
    const first = h.plays.find((p) => p.src === "pcm") || {};
    const cut = h.stops.filter((s) => s.id === first.id && s.t < first.t + first.dur - 50);
    eq(cut.length, 0, `D: her 6 s answer is never cut by the next turn's filler (${JSON.stringify(cut)})`);
    eq(await page.evaluate(() => window.moxieAlive.stats.spoke), 0, "D: …no filler was spoken over her");
    eq(h.plays.filter((p) => p.src === "pcm").length, 2, "D: …and both answers are heard, one after the other");
    report.D = { ...h, fillers: await page.evaluate(() => window.moxieAlive.stats) };
    eq(notable(errs, aborted).length, 0, `D: no unexplained console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

function deep(a, b, m) { eq(JSON.stringify(a), JSON.stringify(b), m); }
if (REPORT) console.log(JSON.stringify(report, null, 1));
finish(LABEL, { fails, count });
