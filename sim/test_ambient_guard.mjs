/* test_ambient_guard.mjs — Moxie's ambient self-talk must never talk over her own answer.
 *
 * THE DEFECT THIS GUARDS. `ambient.js::tick()` fires every 11–24 s and `perform()` calls
 * `moxieAudio.speak()`, which calls `stop()` UNCONDITIONALLY. Nothing checked whether
 * Moxie was mid-answer. A live turn is ~1.2 s of `/api/chat` plus 2–3 s of `/api/speech`,
 * and the reply audio itself measured 4.78 s (105 332 frames @ 22 050 Hz) on a real turn
 * against the hosted site — so a visitor's answer sat squarely inside the ambient window
 * and was very likely cut off mid-sentence and replaced by a non-sequitur. Everything up
 * to that point works: brain, voice, mouth. Then she talks over herself, and a stranger's
 * reasonable read is "this is broken".
 *
 * WHY THE ASSERTIONS ARE AT THE WEB AUDIO LAYER. This repo shipped 770 assertions in
 * PR #82 that all read a FILE while Web Audio was stubbed, and a silent clip passed every
 * one of them. So nothing here is asserted on a label, a status line or a counter the page
 * keeps about itself. Every claim is read off the objects Chrome was actually handed:
 *
 *   · which AudioBuffer reached an AudioBufferSourceNode, and WHEN it started;
 *   · whether `.stop()` was ever called on that node before its own audio ran out;
 *   · the PEAK SAMPLE AMPLITUDE of the buffer, so a silent clip cannot pass.
 *
 * The two voices are told apart at that same layer, by how their buffer was BUILT — which
 * is a structural fact, not a name the page chose:
 *   · "pcm"  — `createBuffer()` + a hand-filled Float32Array. Only `audio.js`'s
 *              CloudTTSResponse path does this; it is the GATEWAY ANSWER.
 *   · "clip" — `decodeAudioData()` of a fetched file. That is the pre-cached clip path,
 *              which is what ambient self-talk (and the degraded/scripted reply) plays.
 *
 * WHAT IS PROVEN, one block each:
 *   1. hosted + live — ambient fires when Moxie is IDLE (the feature still works), then
 *      the answer plays as ONE uninterrupted utterance with ambient ticking throughout,
 *      then the grace beat holds and ambient resumes. The whole defect, end to end.
 *   2. NEGATIVE CONTROL — the same drive with the guard bypassed really does cut the
 *      answer. Without this, block 1 could be passing because the fixture never fired
 *      ambient at all. This is the check that makes block 1 mean something.
 *   3. hosted + degraded — the SCRIPTED path, which plays a clip. The narrow exported
 *      `isSpeaking()` is asserted FALSE while she is plainly speaking, which is the
 *      whole reason the guard uses the broad predicate instead.
 *   5. THE LOADING SEAM — a quip already FETCHING when the answer landed. Neither
 *      existing guard can see it: the tick was taken while she was silent, and there is
 *      no node yet for the answer to stop. Held open deliberately, not waited for.
 *   4. THE SEAM — the measured ~385 ms between `speak()` cutting the old clip and the new
 *      one finishing its fetch-and-decode, in which even the BROAD predicate reads false
 *      while Moxie is mid-reply. This is why the guard needed a timestamped grace beat and
 *      not just a boolean.
 *
 * WHAT IS DELIBERATELY NOT ASSERTED. That ambient never starts during the ~450 ms + decode
 * a scripted reply spends before it has any audio, or the ~4 s a live turn spends in
 * flight. She is genuinely SILENT there, so a tick is the guard working as specified.
 * Closing that needs a turn-in-flight signal from `bridge.js` — not this slice's file, and
 * recorded as an honest gap in docs/architecture/implementation-plan.md. On the LIVE path
 * it is moot: `ttsPump` cuts a local voice the moment the server voice arrives.
 *
 * No gateway, no Cloudflare account, no network: `/api/*` is answered at the browser and
 * the site is served from a loopback static server.
 *
 *   node sim/test_ambient_guard.mjs
 *
 * DELIBERATELY NOT WIRED INTO CI (sim/ci/ci.yml, .github/workflows/ci.yml) — a concurrent
 * audit is rewriting those files and the nine existing browser suites. Wire it after that
 * lands; it passes locally today.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, repo, web }
  from "./browser_harness.mjs";

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

/* THE ANSWER, at the length that was actually measured. 105 332 frames @ 22 050 Hz is the
 * real turn recorded against moxie.mattvalancy.com — 4.78 s of speech, which is what makes
 * the collision with the 11–24 s ambient window so likely. Using the measured figure rather
 * than a convenient short tone is the point: a 0.3 s fixture would hide the bug. */
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

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         "--autoplay-policy=no-user-gesture-required",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${site.port}`],
});

/** Open sim.html with `/api/*` answered at the browser and Web Audio fully instrumented. */
async function open(url, opts) {
  const page = await browser.newPage();
  // >=900px so the rail is a side column; below that sim.html starts with the drawer
  // CLOSED and no control in it is clickable (see sim/test_mobile_layout.mjs).
  await page.setViewport({ width: 1440, height: 900 });
  const errs = [], aborted = { n: 0, probe404: 0 };
  /* Hold clip FETCHES open on demand. Block 5 needs a clip that is still in flight at the
   * instant the answer starts; racing the real network for that would be a test that fails
   * a few runs in ten, so the fixture creates the condition instead of hoping for it. */
  const clipNet = { stall: false, held: [] };
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));

  /* THE RECORDER. A timeline of every buffer source that started or was stopped, tagged
   * by how its buffer was built (see the header). `stop` is recorded because that is the
   * literal mechanism of the defect — `speak()` -> `stop()` on the node carrying the
   * answer — so "was the answer cut?" is a fact about the node, not an inference. */
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
      aborted.probe404++;
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

/* Console errors, minus the ones this fixture CAUSED on purpose — forgiven exactly as
 * many times as they were provoked, never by loosening the pattern. Same correlation
 * trick as sim/test_typed_turn.mjs and sim/test_env_hosted.mjs. */
const ABORTED = /Failed to load resource: net::ERR_(CONNECTION_REFUSED|FAILED|BLOCKED_BY_CLIENT)/;
const PROBE_404 = /Failed to load resource: the server responded with a status of 404/;
function notable(errs, aborted) {
  let budget = aborted ? aborted.n : 0, probes = aborted ? aborted.probe404 : 0;
  return errs.filter((e) => {
    if (budget > 0 && ABORTED.test(e)) { budget--; return false; }
    if (probes > 0 && PROBE_404.test(e)) { probes--; return false; }
    return true;
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const starts = (evs, src) => evs.filter((e) => e.ev === "start" && (!src || e.src === src));
const timeline = (page) => page.evaluate(() => window.__rec.events);

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

/* Wait out the degraded page's own one-time spoken announcement (ambient.js §6.2).
 * `degradedState().said` flips when the line is DISPATCHED, but `perform()` -> `speak()`
 * -> fetch -> decode means its clip starts a few hundred ms later — so waiting on the
 * flag alone races it, and the next clip measured would be the announcement rather than
 * the reply. Wait for the clip to actually start, then for it to finish and its grace
 * beat to lapse. */
async function settleDegradedLine(page) {
  await page.waitForFunction("window.moxieAmbient.degradedState().said === true", { timeout: 15000 });
  await page.waitForFunction(
    `window.__rec.events.some(e => e.ev === "start" && e.src === "clip")`, { timeout: 15000 });

  /* STOP THE SCHEDULER *BEFORE* WAITING OUT THE ANNOUNCEMENT, NOT AFTER.
   *
   * This used to be the other way round, and the two steps were perfectly correlated: the
   * wait below resolves 1600 ms after the announcement's last sample, which is the exact
   * instant `moxieBusy()` stops holding ambient off — so the test asked the scheduler to
   * stop at precisely the moment the scheduler was first allowed to speak, and lost that
   * race roughly one run in five. Measured on a losing run: `stop()` landed at ~15.1 s and
   * ambient's own timer had fired at 15.173 s.
   *
   * The damage was not a stray quip; it was a MISIDENTIFIED SUBJECT. Block 3 takes "the
   * first clip after `mark`" to be the scripted reply, so it measured the ambient line
   * instead, and then reported the real reply — which correctly `stop()`s ambient when it
   * arrives — as an interruption *of* the reply. Two red assertions about the wrong
   * object, describing the feature working.
   *
   * Stopping first is safe and is not a weakening: `stop()` only prevents FUTURE ticks, it
   * does not cancel the announcement already in the air, so the wait below still waits out
   * exactly what it always waited out. It just cannot race any more. */
  await page.evaluate(() => window.moxieAmbient.stop());
  await page.waitForFunction("!window.moxieAudio.isMoxieBusy(1600)", { timeout: 25000 });
  /* Then QUIET the free-running scheduler, and be honest about why.
   *
   * The guard's promise is "ambient does not start while Moxie is SPEAKING". On the
   * degraded path a reply is a 450 ms fallback beat plus a fetch-and-decode before any
   * audio exists, and through all of that she is genuinely silent — so a scheduler tick
   * landing there is the guard working as specified, not failing. It is also a REAL
   * residual, recorded as an honest gap in docs/architecture/implementation-plan.md:
   * closing it needs a turn-in-flight signal, which lives in `bridge.js` and is not this
   * slice's file.
   *
   * THIS COMMENT USED TO SAY the LIVE path has no such hole, on the grounds that `ttsPump`
   * cuts a local voice when the server voice arrives. That was wrong, and believing it is
   * what let the hole ship: `ttsPump` can only cut a NODE, and a clip still fetching has
   * none. The live path had the same gap one layer earlier — it reddened this suite on
   * 2026-09-04 from the free-running scheduler block 1 deliberately leaves running. Block 5
   * now holds that gap open on purpose, and audio.js's `floor` closes it.
   *
   * So the blocks below drive `tick()` explicitly rather than racing a 11–24 s timer, and
   * assert the promise that was actually made. Asserting the other thing would be a test
   * that fails a few runs in ten for a behaviour nobody claimed. */
  await page.evaluate(() => window.moxieAmbient.stop());   // idempotent; see the note above
}

try {
  /* =======================================================================
   * 1. HOSTED + LIVE — the defect, end to end.
   *
   * Four phases on one page, in the order a visitor lives them: she is alive when idle,
   * she is not interrupted while answering, she is given a beat to finish, and she comes
   * back to life afterwards.
   * ===================================================================== */
  {
    const { page, errs, aborted } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });

    /* --- 1a. AMBIENT STILL FIRES WHEN SHE IS IDLE ---------------------
     * First, because a fix that quietly kills ambient is worse than the bug. This also
     * warms `ambient.json` and the clip manifest, so the later mid-answer ticks are not
     * silently excused by a cold fetch. */
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
    const mark = (await timeline(page)).length;
    await type(page, "hello moxie");
    await page.waitForFunction(
      `window.__rec.events.some(e => e.ev === "start" && e.src === "pcm")`, { timeout: 20000 });

    await sleep(600);
    const mid1 = await tickAmbient(page);
    await sleep(1800);
    const mid2 = await tickAmbient(page);

    /* Wait for the answer's audio to actually RUN OUT rather than sleeping a computed
     * duration: the tail assertions below are about the instant after her last sample,
     * and a few hundred ms of accumulated `evaluate` overhead would silently walk the
     * probe past the 1.6 s grace beat and test nothing. */
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
    const cut = evs.filter((e) => e.ev === "stop" && e.id === ans.id && e.t < ans.t + ans.dur - 50);
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
    await sleep(2500);
    const total = starts(await timeline(page), "clip").length;
    ok(total > before,
       `…and ambient RESUMES — a clip started once she was done (${total} total vs ${before} before)`);

    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 2. NEGATIVE CONTROL — the fixture can see an interruption.
   *
   * Block 1 proves "no ambient line started inside the answer". That claim is only worth
   * something if this fixture WOULD have caught one. So: the same drive, but speaking the
   * ambient line through `moxieAudio.speak(text, "ambient")` — which is precisely what
   * `perform()` does, minus the guard. The answer must be visibly cut.
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
    const cut = evs.filter((e) => e.ev === "stop" && e.id === ans.id && e.t < ans.t + ans.dur - 50);
    ok(cut.length >= 1,
       "NEGATIVE CONTROL: an UNGUARDED ambient line does cut the answer's node — " +
       "so block 1's silence is the guard working, not the fixture failing to fire");
    const inside = starts(evs, "clip").filter((e) => e.t >= ans.t && e.t < ans.t + ans.dur);
    ok(inside.length >= 1,
       "…and the quip really lands inside the answer's window, which block 1 asserts is empty");
    await page.close();
  }

  /* =======================================================================
   * 3. HOSTED + DEGRADED — the scripted path, and why the NARROW predicate is wrong.
   *
   * With no live brain, `bridge.js` answers from `stub.js` and speaks it through
   * `moxieAudio.speak()` — a pre-cached CLIP, not cloud TTS. `speaking` is never set, so
   * the exported `isSpeaking()` reports FALSE while Moxie is plainly talking. A guard
   * built on it would leave every fallback deployment — the ones with least room to look
   * broken — completely unguarded. Asserted directly, both halves.
   * ===================================================================== */
  {
    const { page, errs, aborted } = await open(HOSTED, { health: HEALTH_BARE });
    /* A degraded page speaks ONE thing of its own first — ambient.js's `degraded` line,
     * armed by `mode.js` and fired by the autoplay unlock (live-sim-demo.md §6.2). It is
     * a ~5 s clip, so it has to play out before this block can drive a turn, or the
     * "reply" measured below would be that announcement instead. */
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
    // The probe above already re-armed the scheduler (say() sets running); its next tick is
    // 11–24 s out, well past this ~4.3 s reply, so what follows is the guard and nothing else.
    await sleep(Math.max(600, reply.dur - 200) + 400);
    const after = await timeline(page);
    const cut = after.filter((e) => e.ev === "stop" && e.id === reply.id && e.t < reply.t + reply.dur - 50);
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
   * 4. THE SEAM — the ~400 ms hole a bare `isMoxieSpeaking()` guard would still leave.
   *
   * `speak()` calls `stop()` and only THEN fetches and decodes the next clip, so between
   * one utterance being cut and its replacement starting there is a real gap in which
   * `current` is null and the broad predicate answers FALSE — while Moxie is, to the
   * visitor, plainly mid-reply. Measured on the degraded path: `stop` at t=13633,
   * next clip start at t=14018, a 385 ms window.
   *
   * An ambient tick landing there would slip straight through a guard written as
   * `if (isMoxieSpeaking()) return;` and be cut off half a syllable later by the reply it
   * raced. The grace beat closes it, because `spokeUntil` was stamped on the way into the
   * gap. This block holds the page inside that seam and asserts all three predicates.
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
   * 5. THE LOADING SEAM — a quip that was already FETCHING when the answer landed.
   *
   * Blocks 1-4 cover the two guards that exist, and the defect survives both. ambient.js
   * checks `moxieBusy()` at TICK time; `ttsPump` stops a local voice at ANSWER time. A
   * clip whose fetch-and-decode is still in flight is invisible to each: the tick was
   * legitimately taken while Moxie was silent, and when the answer arrives there is no
   * node yet for it to stop. The clip then starts a few hundred ms later, over her.
   *
   * That is not a hypothesis. It is what reddened this suite in CI on 2026-09-04 — one
   * clip inside the 4.78 s window with BOTH mid-answer ticks correctly standing down, on
   * a branch whose diff was Python and Markdown only. The free-running 11-24 s scheduler,
   * which block 1 deliberately leaves running, happened to land in the loading gap.
   *
   * Here the gap is held open on purpose rather than waited for: the clip's fetch is
   * stalled, the turn is driven to real audio, and only then is the clip released. Before
   * `floor` (audio.js, THE THIRD SEAM) it started on top of the answer every single time.
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
    eq(evs5.filter((e) => e.ev === "stop" && e.id === ans5.id && e.t < ans5.t + ans5.dur - 50).length, 0,
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
