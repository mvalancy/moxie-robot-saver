/* test_typed_turn.mjs — the typed line, in a REAL browser, all the way to real sound.
 *
 * test_cloud_transport.mjs covers the transport under a fake DOM, but cannot see a CSP
 * refusal, overlapping elements, or real Web Audio. So every claim here is measured in
 * Chrome: the request that left the page, and the samples that reached an
 * AudioBufferSourceNode. The fixture is a real 440 Hz tone and the PEAK AMPLITUDE is
 * asserted, so a silent clip fails.
 *
 * Blocks: 1 hosted+live "Ask" reaches /api/chat and her voice plays; 2 an over-long line is
 * refused client-side for free; 3 hosted+degraded spends nothing and still answers; 4 the
 * controls that cannot work are disabled and clicking them logs no CSP error; 5 local+Piper
 * is unchanged; 6 local without a sidecar adopts the box only after the probe, scripted;
 * 7 an opener is a real turn while a rail phrase chip sends nothing.
 *
 * No network: `/api/*` and the :8081 sidecar are answered at the browser.
 *
 *   node sim/test_typed_turn.mjs
 */
import { join } from "node:path";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, repo, launchBrowser } from "./browser_harness.mjs";

const LABEL = "typed-turn test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
const LOCAL = `${site.url}/sim.html`;

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

const EID = "sim-typedturn01";
const REPLY = "Hi there! What would you like to play?";
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

/** A complete RIFF/WAVE of the same tone — what a real Piper sidecar answers with. */
function wavOfTone() {
  const pcm = Buffer.from(TONE.base64, "base64");
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVE", 8);
  h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(TONE.rate, 24); h.writeUInt32LE(TONE.rate * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}
const WAV = wavOfTone();

const browser = await launchBrowser(puppeteer, chrome,
  { autoplay: true, hosts: { "moxie.hosted.test": site.port } });

/**
 * Open sim.html with `/api/*` (and optionally the :8081 sidecar) answered at the browser.
 *
 * @param {string} url
 * @param {{health:string, chat?:boolean, piper?:boolean}} opts
 */
async function open(url, opts) {
  const page = await browser.newPage();
  // >=900px so the rail is a side column: below that breakpoint sim.html starts with the
  // drawer CLOSED (`#hud.rail-closed #rail-scroll { display: none }`) and no control in it
  // is clickable at all. `sim/test_mobile_layout.mjs` is where the phone widths are driven.
  await page.setViewport({ width: 1440, height: 900 });
  const errs = [], reqs = [], bodies = [], aborted = { n: 0, probe404: 0 };
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  page.on("request", (r) => {
    reqs.push(r.url());
    if (/\/api\/(chat|speech|transcribe)\b/.test(r.url())) bodies.push({ url: r.url(), body: r.postData() || "" });
  });

  /* Web Audio, instrumented where sound is made: `createBuffer` + wrapped `start()` is the
   * GATEWAY voice (voice/ builds it from int16 PCM), `decodeAudioData` the PIPER voice. The
   * peak amplitude of what was scheduled is recorded, so a silent buffer cannot pass. */
  await page.evaluateOnNewDocument(() => {
    window.__audio = { created: 0, decoded: 0, started: 0, frames: 0, rate: 0, peak: 0 };
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
          window.__audio.frames = b.length;
          window.__audio.rate = b.sampleRate;
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
      { if (opts.health) return r.respond({ status: 200, contentType: "application/json", body: opts.health });
        aborted.probe404++;
        return r.respond({ status: 404, contentType: "text/plain", body: "not found" }); }
    if (/\/api\/chat\b/.test(u))
      return opts.chat
        ? r.respond({ status: 200, contentType: "application/json", body: chatBody })
        : r.respond({ status: 404, contentType: "application/json", body: "{}" });
    if (/\/api\/speech\b/.test(u))
      return opts.chat
        ? r.respond({ status: 200, contentType: "application/json", body: speechBody })
        : r.respond({ status: 404, contentType: "application/json", body: "{}" });
    if (/:8081\//.test(u)) {
      // A sidecar on another port is CROSS-ORIGIN, so a stub of it has to answer like the
      // real one does — `sim/tts/server.py` sends `Access-Control-Allow-Origin: *`, and
      // without it the browser refuses the reply and the probe reads as "no Piper".
      if (!opts.piper) { aborted.n++; return r.abort("connectionrefused"); }
      if (/\/health\b/.test(u))
        return r.respond({ status: 200, contentType: "application/json", headers: CORS,
                           body: '{"ok":true,"voice":"test"}' });
      return r.respond({ status: 200, contentType: "audio/wav", headers: CORS, body: WAV });
    }
    if (/:8082\//.test(u)) { aborted.n++; return r.abort("connectionrefused"); }
    return r.continue();
  });

  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!window.moxieAudio && !!window.moxieBridge", { timeout: 15000 });
  // env.js's sidecar probe settles in <2.5 s and mode.js's first /api/health right away;
  // adoption is downstream of both, so wait for the page to stop changing its mind.
  await new Promise((r) => setTimeout(r, 3500));
  return { page, errs, reqs, bodies, aborted };
}

const CORS = { "Access-Control-Allow-Origin": "*" };

/* Console errors, minus the ones this fixture CAUSED: exactly one forgiven
 * ERR_CONNECTION_REFUSED per aborted :8081/:8082 probe, nothing else. */
const ABORTED = /Failed to load resource: net::ERR_(CONNECTION_REFUSED|FAILED|BLOCKED_BY_CLIENT)/;
const PROBE_404 = /Failed to load resource: the server responded with a status of 404/;
function notable(errs, aborted) {
  let budget = aborted ? aborted.n : 0;
  let probes = aborted ? aborted.probe404 : 0;
  return errs.filter((e) => {
    if (budget > 0 && ABORTED.test(e)) { budget--; return false; }
    // A `/api/health` that 404s IS the offline path working as designed (a static fork
    // with no Functions), and Chrome logs a 404 subresource as a console error. Forgiven
    // exactly as many times as this fixture served one, and no more loosely.
    if (probes > 0 && PROBE_404.test(e)) { probes--; return false; }
    return true;
  });
}

/** What a visitor would see of the two typed controls. */
const snapshot = () => ({
  adopted: !!(window.moxieTypedTurn && window.moxieTypedTurn.adopted()),
  sayText: (document.getElementById("speech-btn") || {}).textContent || "",
  sayDisabled: !!(document.getElementById("speech-btn") || {}).disabled,
  sayMarked: !!(document.getElementById("speech-btn") || { classList: { contains: () => false } })
    .classList.contains("needs-backend"),
  inputMax: (document.getElementById("speech-input") || {}).getAttribute
    ? document.getElementById("speech-input").getAttribute("maxlength") : null,
  // "exactly one typed control": either the injected box was never built (adoption
  // happened while the document was still parsing, which is the normal order) or it is
  // hidden (a page that adopted late).
  talkVisible: !!(document.getElementById("chat-sub") && !document.getElementById("chat-sub").hidden),
  statusInSpeechSection: !!(document.getElementById("chat-status") &&
    document.getElementById("chat-status").closest("section.sub") ===
    document.getElementById("speech-btn").closest("section.sub")),
  ttsTestDisabled: !!(document.getElementById("tts-test") || {}).disabled,
  busDisabled: !!(document.getElementById("bus-connect") || {}).disabled,
  ttsBaseDisabled: !!(document.getElementById("tts-base") || {}).disabled,
  sttBaseDisabled: !!(document.getElementById("stt-base") || {}).disabled,
  micDisabled: !!(document.getElementById("mic-btn") || {}).disabled,
  chatText: (document.getElementById("transcript") || {}).textContent || "",
  status: (document.getElementById("chat-status") || {}).textContent || "",
  audio: window.__audio,
});

async function type(page, text) {
  await page.evaluate((t) => { document.getElementById("speech-input").value = t; }, text);
  await page.click("#speech-btn");
}

const noCsp = (errs) => errs.filter((e) => /Content Security Policy|Refused to connect|violates/i.test(e));

try {
  /* =======================================================================
   * 1. HOSTED + LIVE — the defect's exact scenario, now working end to end.
   * ===================================================================== */
  {
    const { page, errs, reqs, bodies, aborted } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    const before = await page.evaluate(snapshot);
    ok(before.adopted, "live/hosted: the typed turn adopted #speech-input/#speech-btn");
    eq(before.sayText, "Ask", "…and the button says what it now does");
    eq(before.sayDisabled, false, "…and it is NOT disabled — it has a real job here");
    eq(before.sayMarked, false, "…so the needs-backend mark is gone");
    eq(before.inputMax, "500", "…and the client-side cap mirrors DEMO_MAX_INPUT_CHARS");
    eq(before.talkVisible, false, "…no duplicate Talk box is showing (exactly one typed control)");
    ok(before.statusInSpeechSection, "…and #chat-status moved under the control the visitor uses");

    await type(page, "hello moxie");
    await page.waitForFunction("window.__audio.started > 0", { timeout: 15000 })
      .catch(() => {});
    await new Promise((r) => setTimeout(r, 1500));
    const after = await page.evaluate(snapshot);

    const chat = bodies.filter((b) => /\/api\/chat\b/.test(b.url));
    eq(chat.length, 1, "a typed line reaches /api/chat exactly once");
    ok(chat.length === 1 && JSON.parse(chat[0].body).text === "hello moxie",
       `…carrying the words that were typed (got ${chat[0] && chat[0].body})`);
    const speech = bodies.filter((b) => /\/api\/speech\b/.test(b.url));
    eq(speech.length, 1, "…and one /api/speech for the voice");
    ok(speech.length === 1 && JSON.parse(speech[0].body).ticket === "v1.TESTTICKET.MAC",
       "…redeeming the ticket the chat route minted, never raw text");

    ok(after.audio.created >= 1, `the gateway PCM was turned into an AudioBuffer (created=${after.audio.created})`);
    ok(after.audio.started >= 1, `…and a buffer source actually STARTED (started=${after.audio.started})`);
    eq(after.audio.rate, TONE.rate, "…at the sample rate the wire declared");
    ok(after.audio.frames === TONE.frames,
       `…with every frame present (got ${after.audio.frames}, want ${TONE.frames})`);
    ok(after.audio.peak > 0.5,
       `…and it was AUDIBLE, not a silent clip (peak ${after.audio.peak.toFixed(3)} of ${TONE.amp})`);
    ok(after.chatText.includes("What would you like to play"),
       `…and Moxie's answer is on the page (transcript: ${JSON.stringify(after.chatText.slice(-90))})`);
    eq(reqs.filter((u) => /:8081\//.test(u)).length, 0,
       "no port-8081 request was made on a hosted origin — the CSP violation is gone at the root");
    eq(notable(errs, aborted).length, 0,
       `no console errors on the live typed turn: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 2. HOSTED + LIVE — the cap is enforced on the client, BEFORE a request.
   * ===================================================================== */
  {
    const { page, bodies } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    await type(page, "x".repeat(501));
    await new Promise((r) => setTimeout(r, 800));
    const s = await page.evaluate(snapshot);
    eq(bodies.filter((b) => /\/api\/chat\b/.test(b.url)).length, 0,
       "an over-long line spends NO request — the cap is enforced before admit() ever sees it");
    ok(/500/.test(s.status), `…and the page says why (status: ${JSON.stringify(s.status)})`);
    await page.close();
  }

  /* =======================================================================
   * 3. HOSTED + DEGRADED — the fallback answers and spends nothing. (mic.js's scripted child
   * line goes through `sendScriptedTurn`, never `sendUserTurn`; test_mic_spend.mjs counts it.)
   * ===================================================================== */
  {
    const { page, errs, reqs, bodies, aborted } = await open(HOSTED, { health: HEALTH_BARE });
    const before = await page.evaluate(snapshot);
    ok(before.adopted, "degraded: the box is still adopted — typing works with no brain");
    eq(before.sayDisabled, false, "…and stays clickable, because it still does something");

    await type(page, "are you there");
    await new Promise((r) => setTimeout(r, 2000));
    const after = await page.evaluate(snapshot);
    eq(bodies.filter((b) => /\/api\/chat\b/.test(b.url)).length, 0,
       "a degraded page spends NO live turn on a typed line");
    ok(after.chatText.includes("are you there"),
       "…the visitor's own line is still echoed to the transcript");
    ok(after.chatText.replace("are you there", "").trim().length > 0,
       `…and Moxie still answers, from stub.js (transcript ${JSON.stringify(after.chatText.slice(-120))})`);
    eq(reqs.filter((u) => /:8081\//.test(u)).length, 0, "…with no sidecar request either");
    eq(notable(errs, aborted).length, 0,
       `no console errors on the degraded typed turn: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 4. HOSTED — the dead controls: `#speech-btn`, `#tts-test` and `#bus-connect` used to stay
   * clickable and fire a cross-origin request the CSP refused. A click must never log one.
   * ===================================================================== */
  {
    const { page, errs, reqs, aborted } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    const s = await page.evaluate(snapshot);
    ok(s.ttsTestDisabled, "#tts-test cannot work off-localhost, so it is DISABLED, not hinted");
    ok(s.busDisabled, "#bus-connect likewise — ws://host:9001 is refused by connect-src 'self'");
    ok(s.ttsBaseDisabled, "…and the field that arms the Piper request with it");
    ok(s.sttBaseDisabled, "…and the STT one");
    eq(s.micDisabled, false,
       "but #mic-btn is NOT disabled — 'Listen' really does something here, and a mark is right for it");

    // Click every one of them anyway, the way a visitor would.
    for (const id of ["tts-test", "bus-connect", "speech-btn"]) {
      await page.evaluate((i) => document.getElementById(i).click(), id);
      await new Promise((r) => setTimeout(r, 400));
    }
    await new Promise((r) => setTimeout(r, 1200));
    eq(reqs.filter((u) => /:808[12]\//.test(u)).length, 0,
       "clicking the dead controls fires NO sidecar request");
    eq(noCsp(errs).length, 0,
       `clicking them produces NO CSP console error: ${noCsp(errs).slice(0, 3).join(" | ")}`);
    eq(notable(errs, aborted).length, 0,
       `…nor any other console error: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 5. LOCAL + a reachable Piper sidecar — unchanged: the local engines stay first-class, so
   * the button keeps its job and the typed turn does not take it over.
   * ===================================================================== */
  {
    /* `health: null` -> /api/health 404s = `offline` (sim/serve.py, no Functions): the
     * deployment a sidecar belongs to. In `degraded`, `voice/skipProbe` never looks for
     * Piper, so this block would prove nothing. */
    const { page, errs, reqs, bodies, aborted } = await open(LOCAL, { health: null, piper: true });
    const before = await page.evaluate(snapshot);
    eq(before.adopted, false, "local + Piper: the typed turn does NOT take the box over");
    eq(before.sayText, "Say", "…the button keeps its name");
    eq(before.sayDisabled, false, "…and is live");
    eq(before.talkVisible, true, "…and the injected Talk box is still there to type into");
    eq(before.ttsTestDisabled, false, "…and #tts-test works on a local origin");
    eq(before.busDisabled, false, "…as does the live-bus link");

    await type(page, "this is a piper line");
    await page.waitForFunction("window.__audio.started > 0", { timeout: 15000 }).catch(() => {});
    await new Promise((r) => setTimeout(r, 1200));
    const after = await page.evaluate(snapshot);
    const tts = reqs.filter((u) => /:8081\/tts\?/.test(u));
    eq(tts.length, 1, "…the line went to the LOCAL Piper sidecar, exactly as it does today");
    ok(tts[0] && decodeURIComponent(tts[0]).includes("this is a piper line"),
       `…carrying the typed text (got ${tts[0]})`);
    eq(bodies.filter((b) => /\/api\/chat\b/.test(b.url)).length, 0,
       "…and NOT to the brain: the local path is untouched");
    ok(after.audio.decoded >= 1, `…the WAV was decoded by Web Audio (decoded=${after.audio.decoded})`);
    ok(after.audio.started >= 1, `…and played (started=${after.audio.started})`);
    ok(after.audio.peak > 0.5, `…audibly (peak ${after.audio.peak.toFixed(3)})`);
    eq(notable(errs, aborted).length, 0,
       `no console errors on the local Piper path: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 6. LOCAL, no sidecar — adoption waits for the probe, then works offline.
   * ===================================================================== */
  {
    const { page, errs, reqs, bodies, aborted } = await open(LOCAL, { health: null });
    const before = await page.evaluate(snapshot);
    ok(before.adopted, "local with no sidecar: the box is adopted once the probe has ANSWERED");
    eq(before.sayText, "Ask", "…and relabelled");
    eq(before.ttsTestDisabled, false,
       "…but #tts-test stays clickable on a local origin — a sidecar could be started at any moment");

    await type(page, "hello from localhost");
    await new Promise((r) => setTimeout(r, 2000));
    const after = await page.evaluate(snapshot);
    eq(bodies.filter((b) => /\/api\/chat\b/.test(b.url)).length, 0, "…no live turn is spent");
    eq(reqs.filter((u) => /:8081\/tts\?/.test(u)).length, 0,
       "…and no doomed sidecar request is made on the typed path");
    ok(after.chatText.includes("hello from localhost"), "…the line is echoed");
    eq(notable(errs, aborted).length, 0,
       `no console errors: ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =======================================================================
   * 7. AN OPENER IS A REAL TURN — the same path a typed line takes
   * (`moxieTypedTurn.send` -> `sendUserTurn` -> POST /api/chat -> ticket -> her voice), so it
   * spends, queues and is refused under the same rules. test_mobile_layout.mjs block 8 covers
   * the degraded phone layout; this covers the wire.
   * NEGATIVE CONTROL on the same page: a rail `#speech-chips` chip plays shipped audio and
   * must NOT move the /api/chat count — the distinction the feature exists for.
   * ===================================================================== */
  {
    const { page, errs, reqs, bodies, aborted } = await open(HOSTED, { health: HEALTH_LIVE, chat: true });
    const chats = () => bodies.filter((b) => /\/api\/chat\b/.test(b.url));

    const before = await page.evaluate(() => {
      const box = document.getElementById("chat-openers");
      return {
        adopted: !!(window.moxieTypedTurn && window.moxieTypedTurn.adopted()),
        found: !!box,
        inDock: !!(box && box.closest("#chat-dock")),
        labels: box ? [...box.querySelectorAll("button.opener")]
                        .map((b) => b.textContent.replace(/\s+/g, " ").trim()) : [],
      };
    });
    ok(before.adopted, "live/hosted: the typed turn owns the composer (the path an opener uses)");
    ok(before.found, "…and the three openers are on the page");
    ok(before.inDock,
       "…in #chat-dock, beside the box a visitor types into — not in the engineering rail");
    eq(JSON.stringify(before.labels),
       JSON.stringify(["Tell me a silly joke", "What makes you happy?", "Surprise me!"]),
       `…and they are the three openers the brief names — got ${JSON.stringify(before.labels)}`);

    if (before.found) {
      await page.click("#chat-openers .opener:nth-of-type(1)");
      await page.waitForFunction("window.__audio.started > 0", { timeout: 15000 }).catch(() => {});
      await new Promise((r) => setTimeout(r, 1500));
    }
    const after = await page.evaluate(snapshot);

    eq(chats().length, 1, "TAPPING AN OPENER REACHES /api/chat — exactly once, like a typed line");
    ok(chats().length === 1 && JSON.parse(chats()[0].body).text === "Tell me a silly joke",
       `…carrying the opener's own words in the same field a typed line fills ` +
       `(got ${chats()[0] && chats()[0].body})`);
    const speech = bodies.filter((b) => /\/api\/speech\b/.test(b.url));
    eq(speech.length, 1, "…and one /api/speech for the answer's voice");
    ok(speech.length === 1 && JSON.parse(speech[0].body).ticket === "v1.TESTTICKET.MAC",
       "…redeeming the ticket the chat route minted, exactly as the typed path does");
    ok(after.audio.started >= 1,
       `…and a buffer source really STARTED — her answer was spoken (started=${after.audio.started})`);
    ok(after.audio.peak > 0.5,
       `…audibly, not a silent clip (peak ${after.audio.peak.toFixed(3)} of ${TONE.amp})`);
    ok(after.chatText.includes("Tell me a silly joke"),
       `…the visitor's line is in the log — got ${JSON.stringify(after.chatText.slice(0, 120))}`);
    ok(after.chatText.includes("What would you like to play"),
       `…and Moxie's answer under it — got ${JSON.stringify(after.chatText.slice(-90))}`);
    eq(await page.$eval("#speech-input", (e) => e.value), "",
       "…and the message box was never touched: an opener SENDS, it does not pre-fill");

    /* ---- THE NEGATIVE CONTROL: a phrase chip is not an opener ----
     * `moxieAudio.speak` is wrapped (deterministic, no decode wait); `includes`, not a length,
     * because ambient self-talk uses the same function. */
    await page.waitForSelector("#speech-chips .chip", { timeout: 10000 }).catch(() => {});
    await page.evaluate(() => {
      window.__spoke = [];
      const a = window.moxieAudio, orig = a.speak;
      a.speak = function (t) { window.__spoke.push(String(t)); return orig.apply(this, arguments); };
    });
    const chip = await page.$("#speech-chips .chip");
    ok(!!chip, "the rail still ships its pre-cached phrase chips, untouched by this slice");
    const chipText = chip ? await page.$eval("#speech-chips .chip", (e) => e.title || e.textContent) : "";
    if (chip) {
      await page.$eval("#speech-chips .chip", (e) => e.click());
      await new Promise((r) => setTimeout(r, 900));
    }
    const spoke = await page.evaluate(() => (window.__spoke || []).slice());
    ok(spoke.includes(chipText),
       `…tapping one DID fire its handler — it asked for the shipped clip ${JSON.stringify(chipText)} ` +
       `(spoke ${JSON.stringify(spoke)})`);
    eq(chats().length, 1,
       "…and it sent NO TURN: still exactly one /api/chat, the opener's. THIS is the " +
       "difference between a phrase chip and an opener");
    eq(await page.$eval("#speech-input", (e) => e.value), "",
       "…nor did it pre-fill the composer on an adopted page");

    eq(reqs.filter((u) => /:8081\//.test(u)).length, 0,
       "no port-8081 request was made from a hosted origin on the opener path either");
    eq(notable(errs, aborted).length, 0,
       `the whole opener turn raised no console errors — ${notable(errs, aborted).slice(0, 3).join(" | ")}`);
    await page.close();
  }

} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
