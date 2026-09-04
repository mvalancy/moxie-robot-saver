/* test_mic_spend.mjs — a refused microphone must not spend a live turn. In Chrome.
 *
 * THE DEFECT, as an adversarial security audit of the live demo put it (medium, under the
 * availability lens): *"a refused or failed microphone press spends a full chat + speech
 * turn on a line the visitor never said."*
 *
 * `mic.js` consoles a visitor whose ears failed with a SCRIPTED CHILD LINE, so the button
 * is never dead (spec §6). That line is a line the page chose — and it used to go out
 * through `window.moxieBridge.sendUserTurn`, which on a hosted live deployment is
 * `cloud-transport.js`'s wrapper. So a clip the route refused as `bad_request`, or one
 * `mic.js` refused itself for being over `max_audio_bytes` **without ever uploading it**,
 * still bought a `POST /api/chat` AND a `POST /api/speech` out of a budget the whole demo
 * shares. Nobody said the words.
 *
 * WHY THIS SUITE IS A BROWSER SUITE AND NOT A THIRD FAKE-DOM ONE. Two other files already
 * cover the halves under a stubbed window — `sim/test_demo_ears.mjs` B5b (mic.js chooses
 * the free seam) and `sim/test_cloud_transport.mjs` 6b (the seam costs nothing) — and both
 * are worth having, because they are deterministic on a virtual clock. But a claim about
 * what the gateway is BILLED FOR is a claim about requests that actually left a page, and
 * the only honest way to count those is to count them:
 *
 *   · `page.on("request")` — every request the browser really made, and
 *   · a wrapped `AudioBufferSourceNode.start()` — the sample data that really reached the
 *     speakers, with its PEAK AMPLITUDE, because a silent clip passes every structural
 *     check while making no sound (PR #82's 770 assertions read a FILE while Web Audio was
 *     stubbed; #87 corrected it, and `sim/test_typed_turn.mjs` established this shape).
 *
 * That second instrument is what makes this more than a "no requests" test. The fix would
 * be trivially satisfiable by deleting the consolation line — and that would be a WORSE
 * product, a dead button with an honest status line. So every "spends nothing" assertion
 * here is paired with one that the visitor was still consoled OUT LOUD: the child's line
 * on the page, its pre-rendered clip audible, and Moxie's answer after it.
 *
 * SIX SCENARIOS, all on the same hosted+live page:
 *   1. a transcription REFUSED by the route  — consoled, audible, ZERO chat/speech
 *   2. a clip over max_audio_bytes           — consoled, audible, ZERO of everything,
 *                                              not even the upload
 *   3. a transcription that never answers    — consoled, audible, ZERO chat/speech
 *   4. A REAL TRANSCRIPT                     — exactly one chat + one speech, and Moxie's
 *                                              own gateway voice really plays
 *   5. a clip under min_audio_bytes          — "(too short)", zero requests, no line burnt
 *   6. a microphone that will not open       — an honest status line, zero requests
 *
 * No gateway, no Cloudflare account, no network, and NO LIVE MICROPHONE: `/api/*` is
 * answered at the browser and the recorder is injected through `moxieMic.setCapture`, the
 * seam that exists for exactly this (playbook rule 11).
 *
 *   node sim/test_mic_spend.mjs
 */
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, repo } from "./browser_harness.mjs";

const LABEL = "mic-spend test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;

/* The real Function builds the health envelope, so this suite can never drift from what
 * the route answers (the trick `sim/test_env_hosted.mjs` established). */
const health = await import(join(repo, "functions", "api", "health.js"));
const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const HEALTH_LIVE = await (await health.onRequestGet({
  env: {
    DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
    DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
    DEMO_CHAT_MODEL: "test-brain-model",
    DEMO_TTS_MODEL: "test-voice-model",
    DEMO_STT_MODEL: "test-ears-model",
  },
})).text();
/** The limits the page is actually operating under — read from the envelope, never guessed. */
const LIMITS = JSON.parse(HEALTH_LIVE).limits;

const EID = "sim-micspend01";
const REPLY = "That sounds like a wonderful day!";
const TONE = pcmToneBase64({ seconds: 0.3, rate: 22050, freq: 440, amp: 0.8 });

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

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         "--autoplay-policy=no-user-gesture-required",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${site.port}`],
});

/**
 * Open the hosted, live sim with `/api/*` answered at the browser.
 *
 * @param {{transcribe?: "ok"|"refused"|"dead"}} opts — what `POST /api/transcribe` does.
 *   `/api/chat` and `/api/speech` ALWAYS answer successfully here, on purpose: if the
 *   page spends a turn it must be able to complete it, so a request that should never
 *   have happened shows up as a real answer on the page and not as a second failure.
 */
async function open(opts) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const errs = [], reqs = [], aborted = { n: 0 };
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  page.on("request", (r) => reqs.push(r.url()));

  /* Web Audio, instrumented where sound is actually made. `createBuffer` + a wrapped
   * `start()` is where the GATEWAY voice lands (`audio.js` builds the buffer by hand from
   * int16 PCM), `decodeAudioData` is where a pre-rendered CLIP lands — the child's
   * consolation line and Moxie's stubbed answer both arrive that way. The peak amplitude
   * of every buffer that was scheduled is recorded, so a silent one cannot pass. */
  await page.evaluateOnNewDocument(() => {
    window.__audio = { created: 0, decoded: 0, started: 0, peak: 0, rate: 0, frames: 0 };
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const cb = C.prototype.createBuffer;
    C.prototype.createBuffer = function (...a) { window.__audio.created++; return cb.apply(this, a); };
    const da = C.prototype.decodeAudioData;
    C.prototype.decodeAudioData = function (...a) { window.__audio.decoded++; return da.apply(this, a); };
    const cbs = C.prototype.createBufferSource;
    C.prototype.createBufferSource = function () {
      const node = cbs.call(this);
      const start = node.start.bind(node);
      node.start = function (...a) {
        const b = node.buffer;
        if (b) {
          window.__audio.started++;
          window.__audio.rate = b.sampleRate;
          window.__audio.frames = b.length;
          const d = b.getChannelData(0);
          let p = 0;
          for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > p) p = v; }
          if (p > window.__audio.peak) window.__audio.peak = p;
        }
        return start(...a);
      };
      return node;
    };
  });

  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/\/api\/health\b/.test(u))
      return r.respond({ status: 200, contentType: "application/json", body: HEALTH_LIVE });
    if (/\/api\/transcribe\b/.test(u)) {
      if (opts.transcribe === "dead") { aborted.n++; return r.abort("connectionrefused"); }
      if (opts.transcribe === "refused")
        return r.respond({ status: 400, contentType: "application/json",
                           body: JSON.stringify(envelope.envelope({
                             ok: false, degraded: true, reason: "bad_request", mode: "live",
                             voice: true, ears: true })) });
      return r.respond({ status: 200, contentType: "application/json",
                         body: JSON.stringify(envelope.envelope({
                           ok: true, mode: "live", voice: true, ears: true,
                           transcript: "I went to the park today" })) });
    }
    if (/\/api\/chat\b/.test(u))
      return r.respond({ status: 200, contentType: "application/json", body: chatBody });
    if (/\/api\/speech\b/.test(u))
      return r.respond({ status: 200, contentType: "application/json", body: speechBody });
    // The local sidecars cannot exist on a hosted origin. `_headers`' own CSP refuses them
    // and `env.js` disables the controls that would ask; a fixture that answered would be
    // testing a deployment nobody has.
    if (/:808[12]\//.test(u)) { aborted.n++; return r.abort("connectionrefused"); }
    return r.continue();
  });

  await page.goto(HOSTED, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!window.moxieMic && !!window.moxieBridge && !!window.moxieMode",
                             { timeout: 15000 });
  // mode.js's first /api/health, and env.js's sidecar probe, both settle well inside this.
  await page.waitForFunction("window.moxieMode.canSpendLiveTurn() === true", { timeout: 15000 })
    .catch(() => {});
  await new Promise((r) => setTimeout(r, 500));
  return { page, errs, reqs, aborted };
}

/** How many of each `/api/*` route the browser actually asked for. */
const spend = (reqs) => ({
  transcribe: reqs.filter((u) => /\/api\/transcribe\b/.test(u)).length,
  chat: reqs.filter((u) => /\/api\/chat\b/.test(u)).length,
  speech: reqs.filter((u) => /\/api\/speech\b/.test(u)).length,
});

/**
 * Press the microphone button, hold it, and press it again — with a FAKE recorder that
 * yields a clip of exactly `size` bytes.
 *
 * `moxieMic.setCapture` is the seam `mic.js` documents for precisely this: no device is
 * ever opened, and the caps, the size gates and the fallback are the REAL ones. Passing
 * `size: null` opens nothing at all, which is what a denied permission looks like from
 * `start()`'s point of view.
 */
async function press(page, size) {
  await page.evaluate((n) => {
    window.moxieMic.setCapture(() => {
      if (n === null) return Promise.reject(new Error("NotAllowedError"));
      const r = {
        state: "inactive", mimeType: "audio/wav", ondataavailable: null, onstop: null,
        start() { r.state = "recording"; },
        stop() {
          if (r.state === "inactive") return;
          r.state = "inactive";
          if (r.ondataavailable) r.ondataavailable({ data: new Blob([new Uint8Array(n)], { type: "audio/wav" }) });
          if (r.onstop) r.onstop();
        },
      };
      return Promise.resolve({ recorder: r, stream: { getTracks: () => [] } });
    });
  }, size);
  await page.click("#mic-btn");
  await new Promise((r) => setTimeout(r, 250));
  await page.click("#mic-btn");
  // The whole degraded turn is bounded by cloud-transport's 450 ms stub beat plus the
  // clip fetch; the live one by the 2.5 s speech wait. 3 s covers both with room.
  await new Promise((r) => setTimeout(r, 3000));
}

/** What a visitor would see and hear. */
const snapshot = () => ({
  status: (document.getElementById("mic-status") || {}).textContent || "",
  chatText: (document.getElementById("transcript") || {}).textContent || "",
  stats: window.moxieMic.stats(),
  transport: window.moxieBridge.transportStats ? window.moxieBridge.transportStats() : null,
  audio: window.__audio,
});

const ABORTED = /Failed to load resource: net::ERR_(CONNECTION_REFUSED|FAILED|BLOCKED_BY_CLIENT)/;
const REFUSED_400 = /Failed to load resource: the server responded with a status of 400/;
/** Console errors, minus the ones this fixture caused on purpose (the trick
 *  `sim/test_typed_turn.mjs` uses: forgive exactly as many as we provoked, and no more). */
function notable(errs, aborted, refusals) {
  let budget = aborted ? aborted.n : 0;
  let four = refusals || 0;
  return errs.filter((e) => {
    if (budget > 0 && ABORTED.test(e)) { budget--; return false; }
    if (four > 0 && REFUSED_400.test(e)) { four--; return false; }
    return true;
  });
}

try {
  /* =======================================================================
   * 1. THE DEFECT'S EXACT SCENARIO — the route REFUSES the clip.
   *
   * `bad_request` is chosen deliberately: `mode.js::note` treats it as an input outcome
   * and changes NO mode, so `canSpendLiveTurn()` is still true when the fallback runs.
   * (`rate_limited`, `at_capacity`, `budget_exhausted` and `upstream_down` were free
   * before this fix only by accident — they happen to shut the gate on their way past.)
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "refused" });
    const live = await page.evaluate(() => window.moxieMode.canSpendLiveTurn());
    eq(live, true, "the page is live and a turn IS spendable — the gate is open, not closed");

    await press(page, 40000);
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 1, "the clip was uploaded once");
    eq(paid.chat, 0, "A REFUSED TRANSCRIPTION SPENDS NO /api/chat — nobody said those words");
    eq(paid.speech, 0, "…and no /api/speech either");
    eq(s.stats.fallbacks, 1, "…but the visitor IS consoled: one scripted line");
    ok(/wasn't usable/.test(s.status), `…with an honest status line (got ${JSON.stringify(s.status)})`);
    ok(s.chatText.includes("Thank you Moxie!"),
       `…the child's line is on the page (transcript ${JSON.stringify(s.chatText.slice(-120))})`);
    ok(s.chatText.includes("You're so welcome"), "…and Moxie answers it, from stub.js");
    ok(s.audio.started >= 2,
       `…and BOTH were actually spoken through Web Audio (started=${s.audio.started})`);
    ok(s.audio.peak > 0.05,
       `…audibly, from the pre-rendered clips (peak ${s.audio.peak.toFixed(3)})`);
    eq(s.transport && s.transport.scriptedFree, 1, "…recorded as a scripted line answered for free");
    eq(s.transport && s.transport.live, 0, "…and no live turn was ever opened");
    eq(notable(errs, aborted, 1).length, 0,
       `no unexpected console errors: ${notable(errs, aborted, 1).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 2. THE PUREST CASE — a clip over `max_audio_bytes`, refused CLIENT-side.
   *
   * No upload happens at all: `mic.js` knows the route would refuse it, so it never asks.
   * And yet, before this fix, that free refusal bought a chat AND a speech turn. One
   * over-long recording, two paid requests, nothing said.
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, LIMITS.max_audio_bytes + 1);
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 0, "an over-long clip is never uploaded — the free gate still holds");
    eq(paid.chat, 0, "…AND IT SPENDS NO /api/chat: a refusal that cost nothing stays costing nothing");
    eq(paid.speech, 0, "…nor an /api/speech");
    eq(s.stats.tooLong, 1, "…recorded as too long");
    eq(s.stats.fallbacks, 1, "…and the visitor is still consoled");
    ok(/too long/.test(s.status), `…and told why (got ${JSON.stringify(s.status)})`);
    ok(s.chatText.includes("Thank you Moxie!"), "…with the scripted line on the page");
    ok(s.audio.started >= 2, `…spoken aloud (started=${s.audio.started})`);
    ok(s.audio.peak > 0.05, `…audibly (peak ${s.audio.peak.toFixed(3)})`);
    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 3. THE EARS ARE UNREACHABLE — no envelope at all.
   *
   * `noteTransportError` is a STRIKE, and it takes three to degrade the page — so on the
   * first two the gate is still open and the old code paid, twice, for silence.
   * ===================================================================== */
  {
    const { page, reqs } = await open({ transcribe: "dead" });
    await press(page, 40000);
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.chat, 0, "an unreachable transcriber spends NO /api/chat");
    eq(paid.speech, 0, "…and no /api/speech");
    eq(s.stats.fallbacks, 1, "…and still consoles the visitor");
    ok(s.chatText.includes("Thank you Moxie!"), "…with a scripted child line on the page");
    ok(s.audio.started >= 1, `…that really played (started=${s.audio.started})`);
    await page.close();
  }

  /* =======================================================================
   * 4. A REAL TRANSCRIPT — unchanged, and it had better be.
   *
   * A fix that quietly stopped the microphone from spending would pass every assertion
   * above. This is the one that says the ears still work: the words a visitor SAID are
   * exactly what the demo exists to spend on.
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, 40000);
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 1, "a real clip is uploaded once");
    eq(paid.chat, 1, "…and a REAL TRANSCRIPT still spends exactly one /api/chat");
    eq(paid.speech, 1, "…and exactly one /api/speech for Moxie's own voice");
    eq(s.stats.transcripts, 1, "…recorded as a transcript");
    eq(s.stats.fallbacks, 0, "…with no scripted line burnt");
    ok(s.chatText.includes("I went to the park today"), "…the transcript is on the page");
    ok(s.chatText.includes(REPLY), "…and Moxie's answer with it");
    eq(s.transport && s.transport.live, 1, "…as one live turn");
    ok(s.audio.started >= 1, `…and the gateway voice played (started=${s.audio.started})`);
    eq(s.audio.rate, TONE.rate, "…at the sample rate the wire declared");
    ok(s.audio.peak > 0.5,
       `…AUDIBLY, not a silent clip (peak ${s.audio.peak.toFixed(3)} of ${TONE.amp})`);
    eq(notable(errs, aborted).length, 0,
       `no console errors on the live spoken turn: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 5. A CLIP UNDER `min_audio_bytes` — a slipped button, not a turn.
   *
   * This path never consoled anybody and never spent anything, before or after. It is
   * asserted so that "the fallback is free" can never be mistaken for "everything now
   * fires a fallback": a scripted line is not the answer to every mishap.
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, Math.max(1, LIMITS.min_audio_bytes - 1));
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 0, "a clip under the floor is never uploaded");
    eq(paid.chat, 0, "…spends no /api/chat");
    eq(paid.speech, 0, "…and no /api/speech");
    eq(s.stats.tooShort, 1, "…recorded as too short");
    eq(s.stats.fallbacks, 0, "…and burns NO scripted line — nothing was said to console about");
    eq(s.status, "(too short)", "…the status says exactly that");
    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 6. A MICROPHONE THAT WILL NOT OPEN — the audit's headline case, and the one
   *    place its description did not match the code.
   *
   * A denied permission takes `start()`'s `catch`, which shows an honest status line and
   * stops. It reaches no fallback, so it never reached `sendUserTurn` and never spent
   * anything — before this fix or after. Pinned here so that stays true, and so the claim
   * is on the record rather than in a report nobody can re-run.
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, null);
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 0, "a microphone that will not open uploads nothing");
    eq(paid.chat, 0, "…spends no /api/chat — it never did");
    eq(paid.speech, 0, "…and no /api/speech");
    eq(s.stats.fallbacks, 0, "…and reaches no fallback at all");
    ok(s.status.length > 0, `…but says something honest (got ${JSON.stringify(s.status)})`);
    eq(await page.evaluate(() => window.moxieMic.isRecording()), false, "…and is not left recording");
    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
