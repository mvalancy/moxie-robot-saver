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
 * something); 3 hosted+degraded (the narrow `isSpeaking()` is false while she talks); 4 the
 * ~400 ms seam between stop() and the next clip; 5 the loading seam (a quip still fetching
 * when the answer lands).
 *
 * Not asserted: silence while a turn is in flight before any audio exists — she is genuinely
 * silent there (gap recorded in docs/architecture/implementation-plan.md).
 * No gateway or network: `/api/*` is answered at the browser.
 *
 *   node sim/test_ambient_guard.mjs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, repo, web, watchPage, notable, launchBrowser } from "./browser_harness.mjs";

const LABEL = "ambient-guard test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;

/* The real Function builds the health envelope, so this suite can never drift from what
 * the route answers (the trick `sim/test_env_hosted.mjs` established). */
const health = await import(join(repo, "functions", "api", "health.js"));
const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const HEALTH_BARE = await (await health.onRequestGet({ env: {} })).text();
const HEALTH_LIVE = await (await health.onRequestGet({
  env: {
    DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
    DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
    DEMO_CHAT_MODEL: "test-brain-model",
    DEMO_TTS_MODEL: "test-voice-model",
    DEMO_STT_MODEL: "test-ears-model",
  },
})).text();

/* THE ANSWER at a real turn's measured length (105 332 frames @ 22 050 Hz = 4.78 s): a
 * short tone would hide the collision with the ambient window. */
const RATE = 22050, WANT_FRAMES = 105332;
const TONE = pcmToneBase64({ seconds: WANT_FRAMES / RATE, rate: RATE, freq: 440, amp: 0.8 });
const ANSWER_MS = (TONE.frames / RATE) * 1000;

const EID = "sim-ambientguard01";
const REPLY = "Hi there! What would you like to play?";

const chatBody = JSON.stringify(envelope.envelope({
  ok: true, mode: "live", voice: true, ears: true,
  messages: [{
    topic: "/devices/d_sim/commands/remote_chat",
    payload: JSON.stringify({
      command: "remote_chat", result: "SUCCESS", backend: "router", event_id: EID,
      output: { text: REPLY, markup: REPLY }, end_turn: false,
    }),
  }],
  speech: [{ ticket: "v1.TESTTICKET.MAC", event_id: EID, chunk_num: 0 }],
  context: "v1.CTX.MAC",
}));
const speechBody = JSON.stringify(envelope.envelope({
  ok: true, mode: "live", voice: true, ears: true,
  messages: [{
    topic: "/devices/d_sim/commands/tts",
    payload: JSON.stringify({
      request_source: "ROBOT_TTS_REQUEST",
      audio: { buffer: TONE.base64, channels: 1, sample_rate: TONE.rate },
      marks: [], event_id: EID, chunk_num: 0,
    }),
  }],
}));

/* A real ambient line, read from the shipped file — so the negative control speaks exactly
 * what `perform()` would have spoken, and cannot drift from what the site ships. */
const AMBIENT_LINE = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8")).lines[0].text;

const browser = await launchBrowser(puppeteer, chrome,
  { autoplay: true, hosts: { "moxie.hosted.test": site.port } });

/** Open sim.html with `/api/*` answered at the browser and Web Audio fully instrumented. */
async function open(url, opts) {
  const page = await browser.newPage();
  // >=900px: below that the rail starts as a CLOSED drawer and nothing in it is clickable.
  await page.setViewport({ width: 1440, height: 900 });
  const { errs, aborted } = watchPage(page);
  /* Hold clip FETCHES open on demand: block 5 needs a clip still in flight when the answer
   * starts, so the fixture creates that condition instead of racing the network for it. */
  const clipNet = { stall: false, held: [] };

  /* THE RECORDER: every buffer source started or stopped, tagged by how its buffer was
   * built. `stop` is the literal mechanism of the defect, so "was the answer cut?" is a fact. */
  await page.evaluateOnNewDocument(() => {
    const rec = (window.__rec = { events: [], seq: 0 });
    const tag = new WeakMap();
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const cb = C.prototype.createBuffer;
    C.prototype.createBuffer = function (...a) {
      const b = cb.apply(this, a); tag.set(b, "pcm"); return b;
    };
    const da = C.prototype.decodeAudioData;
    C.prototype.decodeAudioData = function (...a) {
      const p = da.apply(this, a);
      return p && p.then ? p.then((b) => { tag.set(b, "clip"); return b; }) : p;
    };
    const cbs = C.prototype.createBufferSource;
    C.prototype.createBufferSource = function () {
      const node = cbs.call(this);
      const id = ++rec.seq;
      const start = node.start.bind(node), stop = node.stop.bind(node);
      node.start = function (...a) {
        const b = node.buffer;
        let peak = 0, frames = 0, rate = 0, src = "?";
        if (b) {
          frames = b.length; rate = b.sampleRate; src = tag.get(b) || "?";
          const d = b.getChannelData(0);
          for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > peak) peak = v; }
        }
        rec.events.push({ ev: "start", id, src, frames, rate, peak,
                          dur: rate ? (frames / rate) * 1000 : 0, t: performance.now() });
        return start(...a);
      };
      node.stop = function (...a) {
        rec.events.push({ ev: "stop", id, t: performance.now() });
        return stop(...a);
      };
      return node;
    };
  });

  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/\/api\/health\b/.test(u)) {
      if (opts.health)
        return r.respond({ status: 200, contentType: "application/json", body: opts.health });
      aborted.refused++;
      return r.respond({ status: 404, contentType: "text/plain", body: "not found" });
    }
    if (/\/api\/chat\b/.test(u))
      return opts.chat
        ? r.respond({ status: 200, contentType: "application/json", body: chatBody })
        : r.respond({ status: 404, contentType: "application/json", body: "{}" });
    if (/\/api\/speech\b/.test(u))
      return opts.chat
        ? r.respond({ status: 200, contentType: "application/json", body: speechBody })
        : r.respond({ status: 404, contentType: "application/json", body: "{}" });
    if (/:808[12]\//.test(u)) { aborted.n++; return r.abort("connectionrefused"); }
    if (clipNet.stall && /\/audio\/.+\.(wav|mp3|ogg|m4a)$/i.test(u)) { clipNet.held.push(r); return; }
    return r.continue();
  });

  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!window.moxieAudio && !!window.moxieAmbient && !!window.moxieBridge",
                             { timeout: 15000 });
  // env.js's sidecar probe settles in <2.5 s and mode.js's first /api/health right away.
  await new Promise((r) => setTimeout(r, 3500));
  return { page, errs, aborted, clipNet };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const starts = (evs, src) => evs.filter((e) => e.ev === "start" && (!src || e.src === src));
const timeline = (page) => page.evaluate(() => window.__rec.events);
/** `stop()` events that cut `node` more than 50 ms before its audio ran out. */
const cutsOf = (evs, node) => evs.filter((e) => e.ev === "stop" && e.id === node.id && e.t < node.t + node.dur - 50);

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
    `window.__rec.events.some(e => e.ev === "start" && e.src === "clip")`, { timeout: 15000 });

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
    const { page, errs, aborted } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });

    /* --- 1a. AMBIENT STILL FIRES WHEN SHE IS IDLE (a fix that kills ambient is worse than the
     * bug). Also warms ambient.json and the manifest so later ticks are not cold fetches. */
    await page.click("body");                    // browser autoplay unlock
    const idle = await tickAmbient(page);
    await page.waitForFunction(
      `window.__rec.events.some(e => e.ev === "start" && e.src === "clip")`, { timeout: 15000 })
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
      `window.__rec.events.some(e => e.ev === "start" && e.src === "pcm")`, { timeout: 20000 });

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
    const { page } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    await page.click("body");
    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__rec.events.some(e => e.ev === "start" && e.src === "pcm")`, { timeout: 20000 });
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
    const { page, errs, aborted } = await open(HOSTED, { health: HEALTH_BARE });
    /* A degraded page first speaks its own ~5 s `degraded` line (live-sim-demo.md §6.2); it
     * must play out or it would be measured as the reply. */
    await page.click("body");
    await settleDegradedLine(page);

    const mark = (await timeline(page)).length;
    await type(page, "tell me a joke");
    await page.waitForFunction(
      `window.__rec.events.slice(${mark}).some(e => e.ev === "start" && e.src === "clip")`,
      { timeout: 20000 });
    await sleep(200);

    const probe = await tickAmbient(page);
    eq(probe.pred.narrow, false,
       "the NARROW isSpeaking() reports false while the scripted reply is audibly playing — " +
       "this is the case a guard built on it would have missed");
    eq(probe.pred.broad, true, "…the BROAD isMoxieSpeaking() sees the clip, which is why it is used");
    eq(probe.pred.busy, true, "…so the guard stands down on the degraded path too");

    const reply = starts((await timeline(page)).slice(mark), "clip")[0] || {};
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
    await page.close();
  }

  /* =======================================================================
   * 4. THE SEAM — `speak()` stops the old clip and only THEN fetches/decodes the next, so for
   * ~400 ms the broad predicate reads FALSE mid-reply. A bare `if (isMoxieSpeaking())` guard
   * would let a tick through; the `spokeUntil` grace beat closes it. The page is held inside
   * the seam and all three predicates asserted.
   * ===================================================================== */
  {
    const { page } = await open(HOSTED, { health: HEALTH_BARE });
    await page.click("body");
    await settleDegradedLine(page);

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
    const { page, errs, aborted, clipNet } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    await page.click("body");
    await page.evaluate(() => window.moxieAmbient.stop());   // drive tick() explicitly
    await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });

    clipNet.stall = true;
    const held = await tickAmbient(page);                    // guard passes: she IS silent
    eq(held.pred.busy, false,
       "the tick was taken while Moxie was genuinely silent — the guard had no reason to refuse");

    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__rec.events.some(e => e.ev === "start" && e.src === "pcm")`, { timeout: 20000 });
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
