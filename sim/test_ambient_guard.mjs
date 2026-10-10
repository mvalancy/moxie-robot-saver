/* test_ambient_guard.mjs — Moxie's ambient self-talk must never talk over her own answer.
 *
 * The defect: ambient.js's tick (every 11–24 s) called `moxieAudio.speak()`, which
 * `stop()`s unconditionally, so a multi-second answer was likely cut mid-sentence.
 *
 * Assertions are at the WEB AUDIO layer (a file-reading suite with Web Audio stubbed once
 * passed a silent clip): which AudioBuffer reached a source node and when, whether `.stop()`
 * cut it early, and its peak amplitude. The two voices are told apart structurally:
 * "pcm" = createBuffer + hand-filled samples (the gateway answer); "clip" =
 * decodeAudioData of a fetched file (ambient and scripted replies).
 *
 * Blocks: 1 hosted+live end to end (ambient fires idle, the answer is never cut, the hold,
 * resume); 2 NEGATIVE CONTROL (the guard bypassed really does cut — what makes 1 mean
 * something); 3 hosted+degraded (the narrow `isSpeaking()` is false while she talks) and,
 * on the same page, 4 the ~400 ms seam between stop() and the next clip; 5 the loading seam
 * (a quip still fetching when the answer lands).
 *
 * Not asserted: silence while a turn is in flight before any audio exists — she is genuinely
 * silent there (gap recorded in ROADMAP.md).
 * No gateway or network: `/api/*` is answered at the browser.
 *
 *   node sim/test_ambient_guard.mjs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, web, notable, launchBrowser,
         openSim, liveFixture, instrumentWebAudio } from "./browser_harness.mjs";

const LABEL = "ambient-guard test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;

/* THE ANSWER at a real turn's measured length (105 332 frames @ 22 050 Hz = 4.78 s): a
 * short tone would hide the collision with the ambient window. */
const RATE = 22050, WANT_FRAMES = 105332;
const TONE = pcmToneBase64({ seconds: WANT_FRAMES / RATE, rate: RATE, freq: 440, amp: 0.8 });
const ANSWER_MS = (TONE.frames / RATE) * 1000;
const FX = await liveFixture({ eid: "sim-ambientguard01", reply: "Hi there! What would you like to play?", tone: TONE });
const LIVE = { health: FX.health, chat: FX.chat, speech: FX.speech };

/* A real ambient line, read from the shipped file — so the negative control speaks exactly
 * what `perform()` would have spoken, and cannot drift from what the site ships. */
const AMBIENT_LINE = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8")).lines[0].text;

const browser = await launchBrowser(puppeteer, chrome,
  { autoplay: true, hosts: { "moxie.hosted.test": site.port } });

/** sim.html on the hosted origin, `/api/*` answered at the browser (openSim), Web Audio
 * instrumented where sound is made (`__audio.plays`: "pcm" = the gateway answer, "clip" = a
 * decoded file; `__audio.stops`: the literal mechanism of the defect). Clip FETCHES can be
 * held open (`clipNet.stall`): block 5 needs a quip still in flight when the answer starts. */
async function open(o) {
  const clipNet = { stall: false, held: [] };
  const v = await openSim(browser, HOSTED, { ...o,
    beforeLoad: (p) => p.evaluateOnNewDocument(instrumentWebAudio),
    route: (r, u) => (clipNet.stall && /\/audio\/.+\.(wav|mp3|ogg|m4a)$/i.test(u)) ? (clipNet.held.push(r), true) : false,
  });
  await v.page.waitForFunction("!!window.moxieAudio && !!window.moxieAmbient", { timeout: 15000 });
  return { ...v, clipNet };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* THE AUTOPLAY UNLOCK: a click on the stage CLEAR OF HER. A first tap on Moxie herself says
 * hello (moxie.js, A TAP ON HER, W4-S6), and `page.click("body")` lands on her, at the centre
 * of the viewport: the greeting it started was the clip 1a counted as an ambient quip, and
 * the voice block 5's tick found her busy with. These blocks need a gesture, not a hello. */
async function unlockAudio(page) {
  await page.waitForFunction("typeof window.__moxieProject === 'function'", { timeout: 15000 });
  const at = await page.evaluate(() => {
    const me = window.__moxieProject(0, 1.15, 0);
    const st = document.getElementById("stage").getBoundingClientRect();
    for (const [x, y] of [[st.left + 16, st.top + 16], [st.right - 16, st.top + 16],
                          [st.left + 16, st.top + st.height / 2], [st.right - 16, st.top + st.height / 2]]) {
      const el = document.elementFromPoint(x, y);
      if (el && el.tagName === "CANVAS" && Math.hypot(x - me.x, y - me.y) > 120)
        return { x: Math.round(x), y: Math.round(y) };
    }
    return null;
  });
  if (!at) throw new Error("no point on the stage clear of her for the autoplay unlock");
  await page.mouse.click(at.x, at.y);
  eq(await page.evaluate(() => (window.moxie.tapStats ? window.moxie.tapStats().hellos : 0)), 0,
     "the autoplay unlock was not a tap on her: no hello in what this block measures");
}
const starts = (A, src) => A.plays.filter((e) => !src || e.src === src);
const timeline = (page) => page.evaluate(() => window.__audio);
/** `stop()` calls that cut `node` more than 50 ms before its audio ran out. */
const cutsOf = (A, node) => A.stops.filter((e) => e.id === node.id && e.t < node.t + node.dur - 50);

/** Fire one ambient tick the way the scheduler does, recording what the guard could see. */
const tickAmbient = (page) => page.evaluate(() => {
  const a = window.moxieAudio;
  const read = (f, ...x) => { try { return !!(a && a[f] && a[f](...x)); } catch (e) { return null; } };
  const pred = { narrow: read("isSpeaking"), broad: read("isMoxieSpeaking"),
                 busy: read("isMoxieBusy", 1600) };
  window.moxieAmbient.say();          // -> tick(), i.e. the exact path the timer takes
  return { pred, t: performance.now() };
});

async function type(page, text) {
  await page.evaluate((t) => { document.getElementById("speech-input").value = t; }, text);
  await page.click("#speech-btn");
}

/* Wait out the degraded page's one-time announcement (ambient.js §6.2): the flag flips at
 * dispatch but the clip starts later, so wait for the clip to start, finish, and its grace
 * beat to lapse — or the next clip measured would be the announcement. */
async function settleDegradedLine(page) {
  await page.waitForFunction("window.moxieAmbient.degradedState().said === true", { timeout: 15000 });
  await page.waitForFunction(
    `window.__audio.plays.some(e => e.src === "clip")`, { timeout: 15000 });

  /* STOP THE SCHEDULER *BEFORE* WAITING OUT THE ANNOUNCEMENT: the wait ends at exactly the
   * instant ambient may speak again, so stopping after it raced (~1 in 5) and block 3 then
   * measured an ambient line as "the reply". `stop()` only prevents FUTURE ticks. */
  await page.evaluate(() => window.moxieAmbient.stop());
  await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });
  /* Callers then drive `tick()` explicitly instead of racing the free-running timer. A
   * degraded reply's fetch/decode beat is genuine silence (a recorded gap); the live path's
   * loading gap is block 5, closed by voice/'s `floor`. */
}

try {
  /* =======================================================================
   * 1. HOSTED + LIVE — the defect, end to end: alive when idle, not interrupted while
   * answering, a beat to finish, back to life afterwards.
   * ===================================================================== */
  {
    const { page, errs, aborted } = await open(LIVE);

    /* --- 1a. AMBIENT STILL FIRES WHEN SHE IS IDLE (a fix that kills ambient is worse than the
     * bug). Also warms ambient.json and the manifest so later ticks are not cold fetches. */
    await unlockAudio(page);                     // browser autoplay unlock
    const idle = await tickAmbient(page);
    await page.waitForFunction(
      `window.__audio.plays.some(e => e.src === "clip")`, { timeout: 15000 })
      .catch(() => {});                      // a miss must FAIL the check below, not throw
    let evs = await timeline(page);
    const idleClips = starts(evs, "clip");
    ok(idleClips.length >= 1,
       `ambient still fires when Moxie is idle — a clip really started (got ${idleClips.length})`);
    ok(idleClips.length >= 1 && idleClips[0].peak > 0.01,
       `…and it was AUDIBLE, not a silent clip (peak ${(idleClips[0] || {}).peak})`);
    eq(idle.pred.busy, false, "…and the guard correctly saw an idle robot before it fired");

    // Let that quip finish and its grace beat lapse, so phase 1c starts from silence.
    await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });

    /* --- 1b/1c. THE ANSWER IS NEVER INTERRUPTED -----------------------
     * Drive a real turn and tick ambient twice while the answer is in the air — at ~0.6 s
     * and ~2.4 s into 4.78 s of speech, both squarely inside it. */
    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__audio.plays.some(e => e.src === "pcm")`, { timeout: 20000 });

    await sleep(600);
    const mid1 = await tickAmbient(page);
    await sleep(1800);
    const mid2 = await tickAmbient(page);

    // Wait for the audio to RUN OUT (not a computed sleep) so the probe stays inside the grace beat.
    await sleep(Math.max(0, ANSWER_MS - 2400 - 900));
    await page.waitForFunction("!window.moxieAudio.isMoxieSpeaking()", { timeout: 20000 });
    evs = await timeline(page);

    const pcm = starts(evs, "pcm");
    eq(pcm.length, 1, "the gateway answer produced exactly one buffer source");
    const ans = pcm[0] || {};
    eq(ans.frames, WANT_FRAMES,
       `…carrying every frame of the measured 4.78 s turn (rate ${ans.rate})`);
    eq(ans.rate, RATE, "…at the sample rate the wire declared");
    ok(ans.peak > 0.5,
       `…and it was AUDIBLE, not a silent buffer (peak ${(ans.peak || 0).toFixed(3)} of ${TONE.amp})`);

    // THE ASSERTION THAT MATTERS: one uninterrupted utterance.
    const cut = cutsOf(evs, ans);
    eq(cut.length, 0,
       `the answer's own node was never stop()ed before its audio ran out — ` +
       `it played as ONE uninterrupted utterance (${(ans.dur / 1000).toFixed(2)} s)`);
    const inside = starts(evs, "clip").filter((e) => e.t >= ans.t && e.t < ans.t + ans.dur);
    eq(inside.length, 0,
       `no ambient line started between the answer's start and end — ` +
       `${inside.length} of ${starts(evs, "clip").length} clip(s) landed inside the ` +
       `${(ans.dur / 1000).toFixed(2)} s window`);
    // …and that silence was the GUARD refusing, not the fixture failing to ask.
    eq(mid1.pred.busy, true, "the first mid-answer tick saw a busy robot and stood down");
    eq(mid2.pred.busy, true, "…and so did the second, 1.8 s later");
    eq(mid1.pred.broad, true, "…the BROAD predicate is what saw her (isMoxieSpeaking)");

    /* --- 1d. THE TAIL, then ambient resumes ---------------------------
     * `onended` fires at the end of the AUDIO, not the end of the sentence, so a quip
     * landing in that window still reads as stepping on her. The grace beat is 1600 ms. */
    const before = starts(evs, "clip").length;
    const tail = await tickAmbient(page);
    const sinceEnd = Math.round(tail.t - (ans.t + ans.dur));
    eq(tail.pred.broad, false, "once the answer's audio ends she is no longer speaking…");
    eq(tail.pred.busy, true,
       `…but the 1.6 s grace beat still holds ambient off the tail ` +
       `(probed ${sinceEnd} ms after her last sample)`);
    await sleep(1200);
    const ansEnd = ans.t + ans.dur;
    const inGrace = starts(await timeline(page), "clip")
      .filter((e) => e.t >= ansEnd && e.t < ansEnd + 1600);
    eq(inGrace.length, 0,
       "…and no quip landed on the tail of her last syllable — nothing started inside the " +
       "1.6 s grace window (a quip AFTER it is the feature working, so the window is what " +
       "is asserted, not a count)");

    // Past the grace beat — waited for, not assumed, for the same reason as above.
    await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 15000 });
    const after = await tickAmbient(page);
    eq(after.pred.busy, false, "past the grace beat she is free again…");

    /* --- 1e. …AND STILL SILENT: the conversation hold (45 s past the last turn) outlives the
     * 1600 ms audio grace, because a visitor who just typed is about to read and type again.
     * Both halves: quiet while the hold is on, and she really comes back once it lapses
     * (released via `__ambient.quietMs()`, not by sleeping 45 s). */
    await sleep(2500);
    const heldTotal = starts(await timeline(page), "clip").length;
    eq(heldTotal, before,
       `…and she is STILL quiet, because a turn was typed and the conversation hold ` +
       `outlives the 1.6 s audio grace (${heldTotal} total vs ${before} before)`);
    const holding = await page.evaluate(() => window.__ambient.state().conversing);
    eq(holding, true, "…which the page records as an active conversation hold");

    // Now let the conversation go quiet, and she comes back.
    await page.evaluate(() => window.__ambient.quietMs(1));
    await page.waitForFunction("window.__ambient.state().conversing === false", { timeout: 5000 });
    await tickAmbient(page);
    await sleep(2500);
    const total = starts(await timeline(page), "clip").length;
    ok(total > before,
       `…and ambient RESUMES once the conversation is over (${total} total vs ${before} before)`);

    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 2. NEGATIVE CONTROL — the same drive speaking the ambient line through
   * `moxieAudio.speak(text, "ambient")` (what `perform()` does, minus the guard) must visibly
   * cut the answer, or block 1 proves nothing.
   * ===================================================================== */
  {
    const { page } = await open(LIVE);
    await unlockAudio(page);
    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__audio.plays.some(e => e.src === "pcm")`, { timeout: 20000 });
    await sleep(700);
    await page.evaluate((t) => window.moxieAudio.speak(t, "ambient"), AMBIENT_LINE);
    await sleep(2000);

    const evs = await timeline(page);
    const ans = starts(evs, "pcm")[0] || {};
    const cut = cutsOf(evs, ans);
    ok(cut.length >= 1,
       "NEGATIVE CONTROL: an UNGUARDED ambient line does cut the answer's node — " +
       "so block 1's silence is the guard working, not the fixture failing to fire");
    const inside = starts(evs, "clip").filter((e) => e.t >= ans.t && e.t < ans.t + ans.dur);
    ok(inside.length >= 1,
       "…and the quip really lands inside the answer's window, which block 1 asserts is empty");
    await page.close();
  }

  /* =======================================================================
   * 3. HOSTED + DEGRADED — the stub reply is a pre-cached CLIP, so the exported
   * `isSpeaking()` reads FALSE while she talks. A guard built on it would leave every
   * fallback deployment unguarded. Both halves asserted.
   * ===================================================================== */
  {
    const { page, errs, aborted } = await open({ health: FX.bareHealth });
    /* A degraded page first speaks its own ~5 s `degraded` line (live-sim-demo.md §6.2); it
     * must play out or it would be measured as the reply. */
    await unlockAudio(page);
    await settleDegradedLine(page);

    const mark = (await timeline(page)).plays.length;
    await type(page, "tell me a joke");
    await page.waitForFunction(
      `window.__audio.plays.slice(${mark}).some(e => e.src === "clip")`,
      { timeout: 20000 });
    await sleep(200);

    const probe = await tickAmbient(page);
    eq(probe.pred.narrow, false,
       "the NARROW isSpeaking() reports false while the scripted reply is audibly playing — " +
       "this is the case a guard built on it would have missed");
    eq(probe.pred.broad, true, "…the BROAD isMoxieSpeaking() sees the clip, which is why it is used");
    eq(probe.pred.busy, true, "…so the guard stands down on the degraded path too");

    const reply = (await timeline(page)).plays.slice(mark).filter((e) => e.src === "clip")[0] || {};
    ok(reply.peak > 0.01, `the scripted reply is audible (peak ${(reply.peak || 0).toFixed(3)})`);
    // say() re-armed the scheduler, but its next tick is 11–24 s out, past this ~4.3 s reply.
    await sleep(Math.max(600, reply.dur - 200) + 400);
    const after = await timeline(page);
    const cut = cutsOf(after, reply);
    eq(cut.length, 0,
       `the scripted reply also plays as one uninterrupted utterance ` +
       `(${(reply.dur / 1000).toFixed(2)} s, never stop()ed)`);
    const inside = starts(after, "clip").filter((e) => e.t > reply.t && e.t < reply.t + reply.dur);
    eq(inside.length, 0, "…with no ambient line started inside it");

    eq(notable(errs, aborted).length, 0,
       `no console errors on the degraded path: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);

    /* ---- 4. THE SEAM (same page) — `speak()` stops the old clip and only THEN fetches/decodes
     * the next, so for ~400 ms the broad predicate reads FALSE mid-reply. A bare
     * `if (isMoxieSpeaking())` guard would let a tick through; the `spokeUntil` grace beat
     * closes it. The page is held inside the seam and the predicates asserted. */
    await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });
    // Speak a long clip, then cut it the way a reply does, and read the predicates in the
    // gap before the replacement can possibly have decoded.
    await page.evaluate((t) => window.moxieAudio.speak(t, "ambient"), AMBIENT_LINE);
    await page.waitForFunction("window.moxieAudio.isMoxieSpeaking()", { timeout: 15000 });
    const seam = await page.evaluate(() => {
      window.moxieAudio.stop();                     // exactly what speak() does first
      return { narrow: window.moxieAudio.isSpeaking(),
               broad: window.moxieAudio.isMoxieSpeaking(),
               busy: window.moxieAudio.isMoxieBusy(1600) };
    });
    eq(seam.broad, false,
       "in the seam between stop() and the next clip, even the BROAD predicate reads false…");
    eq(seam.busy, true,
       "…but isMoxieBusy still holds ambient off, because the end was timestamped on the way in — " +
       "this is the ~385 ms hole a bare isMoxieSpeaking() guard would have left open");
    await page.close();
  }

  /* =======================================================================
   * 5. THE LOADING SEAM — a quip already FETCHING when the answer landed. The tick-time
   * `moxieBusy()` check and the answer-time `ttsPump` stop both miss it (no node yet). The
   * clip's fetch is stalled, the turn driven to real audio, then the clip released: without
   * `floor` (voice/, THE THIRD SEAM) it starts on top of the answer every time.
   * ===================================================================== */
  {
    const { page, errs, aborted, clipNet } = await open(LIVE);
    await unlockAudio(page);
    await page.evaluate(() => window.moxieAmbient.stop());   // drive tick() explicitly
    await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });

    clipNet.stall = true;
    const held = await tickAmbient(page);                    // guard passes: she IS silent
    eq(held.pred.busy, false,
       "the tick was taken while Moxie was genuinely silent — the guard had no reason to refuse");

    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__audio.plays.some(e => e.src === "pcm")`, { timeout: 20000 });
    const ansT = starts(await timeline(page), "pcm")[0].t;

    ok(clipNet.held.length >= 1,
       `the quip really was still in flight when the answer started (${clipNet.held.length} request(s) held)`);
    clipNet.held.forEach((r) => { try { r.continue(); } catch (e) {} });
    clipNet.held = []; clipNet.stall = false;

    // Give the released clip every chance to start: fetch + decode is well under a second.
    await sleep(2500);
    const late = starts(await timeline(page), "clip").filter((e) => e.t >= ansT);
    eq(late.length, 0,
       "the quip that finished loading DURING the answer never reached the speakers — " +
       `it lost the floor while it was decoding (${late.length} late clip start(s))`);

    // And the answer itself was not collateral damage.
    const evs5 = await timeline(page);
    const ans5 = starts(evs5, "pcm")[0] || {};
    eq(cutsOf(evs5, ans5).length, 0,
       "…and the answer still played as one uninterrupted utterance");

    eq(notable(errs, aborted).length, 0, "…with no unexplained console errors");
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
