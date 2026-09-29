/* test_typed_turn.mjs — the typed line, in a REAL browser, all the way to real sound.
 *
 * test_cloud_transport.mjs covers the transport under a fake DOM; this measures in Chrome the
 * request that left the page and the samples that reached an AudioBufferSourceNode (a real
 * 440 Hz tone, peak asserted, so a silent clip fails). Pages:
 *   A hosted+live: the controls, the client-side cap, an opener, a typed turn, the dead controls;
 *   B hosted+degraded: spends nothing and still answers;
 *   C local+Piper: unchanged — the local engine keeps the button;
 *   D local, no sidecar: adopts the box once the probe answers, scripted.
 * No network: `/api/*` and the :8081 sidecar are answered at the browser (openSim).
 *
 *   node sim/test_typed_turn.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, launchBrowser, openSim,
         liveFixture, instrumentWebAudio, notable } from "./browser_harness.mjs";

const LABEL = "typed-turn test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
const LOCAL = `${site.url}/sim.html`;
const REPLY = "Hi there! What would you like to play?";
const TONE = pcmToneBase64({ seconds: 0.3, rate: 22050, freq: 440, amp: 0.8 });
const FX = await liveFixture({ eid: "sim-typedturn01", reply: REPLY, tone: TONE });
const HEALTH_BARE = FX.bareHealth;

/** A complete RIFF/WAVE of the same tone — what a real Piper sidecar answers with. */
const WAV = (() => {
  const pcm = Buffer.from(TONE.base64, "base64"), h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVE", 8);
  h.write("fmt ", 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(TONE.rate, 24); h.writeUInt32LE(TONE.rate * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
})();

const browser = await launchBrowser(puppeteer, chrome,
  { autoplay: true, hosts: { "moxie.hosted.test": site.port } });
// >=900px: below that the rail starts as a CLOSED drawer (test_mobile_layout drives phones).
const open = (url, o) => openSim(browser, url, { ...o,
  beforeLoad: (p) => p.evaluateOnNewDocument(instrumentWebAudio) });

/** What a visitor would see of the typed controls, plus what reached the speakers. */
const snapshot = () => {
  const $ = (id) => document.getElementById(id) || {};
  const pcm = window.__audio.plays.filter((p) => p.src === "pcm");
  return {
    adopted: !!(window.moxieTypedTurn && window.moxieTypedTurn.adopted()),
    sayText: $("speech-btn").textContent || "", sayDisabled: !!$("speech-btn").disabled,
    sayMarked: !!($("speech-btn").classList && $("speech-btn").classList.contains("needs-backend")),
    inputMax: $("speech-input").getAttribute ? $("speech-input").getAttribute("maxlength") : null,
    inputValue: $("speech-input").value,
    // exactly one typed control: the injected box never built, or hidden
    talkVisible: !!(document.getElementById("chat-sub") && !$("chat-sub").hidden),
    statusInSpeechSection: !!(document.getElementById("chat-status") &&
      $("chat-status").closest("section.sub") === $("speech-btn").closest("section.sub")),
    ttsTest: !!$("tts-test").disabled, bus: !!$("bus-connect").disabled,
    ttsBase: !!$("tts-base").disabled, sttBase: !!$("stt-base").disabled, mic: !!$("mic-btn").disabled,
    chatText: $("transcript").textContent || "",
    whos: [...document.querySelectorAll("#transcript .turn .who")].map((w) => w.textContent),
    status: $("chat-status").textContent || "",
    audio: { ...window.__audio, pcm: pcm.length, last: pcm[pcm.length - 1] || null },
  };
};
async function type(page, text) {
  await page.evaluate((t) => { document.getElementById("speech-input").value = t; }, text);
  await page.click("#speech-btn");
}
const posts = (bodies, route) => bodies.filter((b) => new RegExp(`/api/${route}\\b`).test(b.url));
const waitFor = (page, fn, arg) => page.waitForFunction(fn, { timeout: 15000, polling: 50 }, arg).catch(() => {});
const moxieAnswered = (page, after) => waitFor(page,
  (n) => document.querySelectorAll("#transcript .turn.moxie").length > n, after);
const eyes = (label, v) => {
  const left = notable(v.errs, v.aborted);
  eq(left.length, 0, `${label}: no unexplained console errors — ${left.slice(0, 3).join(" | ")}`);
};

try {
  /* =======================================================================
   * A. HOSTED + LIVE — the defect's scenario (a typed line reached no brain), working.
   * ===================================================================== */
  {
    const v = await open(HOSTED, { health: FX.health, chat: FX.chat, speech: FX.speech });
    const { page, bodies, reqs } = v;
    const s0 = await page.evaluate(snapshot);
    ok(s0.adopted, "live/hosted: the typed turn adopted #speech-input/#speech-btn");
    eq(s0.sayText, "Ask", "…the button says what it now does");
    ok(!s0.sayDisabled && !s0.sayMarked, "…and it is live, with no needs-backend mark");
    eq(s0.inputMax, "500", "…the client-side cap mirrors DEMO_MAX_INPUT_CHARS");
    eq(s0.talkVisible, false, "…exactly one typed control is showing");
    ok(s0.statusInSpeechSection, "…and #chat-status sits under the control the visitor uses");
    // The dead controls (they used to stay clickable and fire a CSP-refused request).
    ok(s0.ttsTest && s0.bus && s0.ttsBase && s0.sttBase,
       "#tts-test, #bus-connect and the sidecar address fields are DISABLED off-localhost");
    eq(s0.mic, false, "…but #mic-btn is not: 'Listen' really works here");

    // The cap is enforced on the client, BEFORE a request (sendTyped refuses synchronously).
    await type(page, "x".repeat(501));
    const capped = await page.evaluate(snapshot);
    eq(posts(bodies, "chat").length, 0, "an over-long line spends NO request");
    ok(/500/.test(capped.status), `…and the page says why (status ${JSON.stringify(capped.status)})`);
    await page.$eval("#speech-input", (e) => { e.value = ""; });

    /* An OPENER is a real turn on the typed path: POST /api/chat -> ticket -> her voice. */
    eq(await page.$$eval("#chat-dock #chat-openers button.opener", (b) => b.length), 3,
       "the three openers are in #chat-dock, beside the box");
    await page.click("#chat-openers .opener:nth-of-type(1)");
    await waitFor(page, () => window.__audio.plays.some((p) => p.src === "pcm"));
    await moxieAnswered(page, 0);
    let s = await page.evaluate(snapshot);
    eq(posts(bodies, "chat").length, 1, "tapping an opener reaches /api/chat exactly once");
    eq(JSON.parse((posts(bodies, "chat")[0] || {}).body || "{}").text, "Tell me a silly joke",
       "…carrying the opener's words in the field a typed line fills");
    eq(posts(bodies, "speech").map((b) => JSON.parse(b.body).ticket).join(), "v1.TESTTICKET.MAC",
       "…and one /api/speech redeeming the ticket the chat route minted, never raw text");
    ok(s.audio.last && s.audio.last.rate === TONE.rate && s.audio.last.frames === TONE.frames &&
       s.audio.last.peak > 0.5,
       `…her voice PLAYED: every frame, at the wire's rate, audibly (${JSON.stringify(s.audio.last)})`);
    ok(s.chatText.includes("Tell me a silly joke") && s.chatText.includes("What would you like to play"),
       `…the line and her answer are in the log (${JSON.stringify(s.chatText.slice(-120))})`);
    eq(s.inputValue, "", "…and the box was never touched: an opener SENDS, it does not pre-fill");

    /* NEGATIVE CONTROL: a rail phrase chip plays shipped audio and sends NO turn. */
    await page.evaluate(() => {
      window.__spoke = [];
      const a = window.moxieAudio, orig = a.speak;
      a.speak = function (t) { window.__spoke.push(String(t)); return orig.apply(this, arguments); };
    });
    const chipText = await page.$eval("#speech-chips .chip", (e) => { e.click(); return e.title || e.textContent; });
    ok((await page.evaluate(() => window.__spoke)).includes(chipText),
       `a phrase chip DID fire its handler (asked for the clip ${JSON.stringify(chipText)})…`);
    eq(posts(bodies, "chat").length, 1, "…and sent NO turn — the difference between a chip and an opener");

    /* THE TYPED LINE, end to end. */
    await type(page, "hello moxie");
    await waitFor(page, () => window.__audio.plays.filter((p) => p.src === "pcm").length >= 2);
    s = await page.evaluate(snapshot);
    eq(posts(bodies, "chat").length, 2, "a typed line reaches /api/chat exactly once");
    eq(JSON.parse((posts(bodies, "chat")[1] || {}).body || "{}").text, "hello moxie",
       "…carrying the words that were typed");
    eq(posts(bodies, "speech").length, 2, "…and one /api/speech for the voice");
    ok(s.audio.pcm >= 2 && s.audio.last.peak > 0.5, `…which PLAYED, audibly (${JSON.stringify(s.audio.last)})`);

    // Clicking the dead controls anyway fires nothing and logs no CSP refusal.
    for (const id of ["tts-test", "bus-connect", "speech-btn"])
      await page.evaluate((i) => document.getElementById(i).click(), id);
    await new Promise((r) => setTimeout(r, 500));   // an absence: nothing to wait FOR
    eq(reqs.filter((u) => /:808[12]\//.test(u)).length, 0,
       "no sidecar request ever left the hosted origin — not on load, the turns, nor the dead clicks");
    eyes("hosted+live", v);
    await page.close();
  }

  /* =======================================================================
   * B. HOSTED + DEGRADED — the fallback answers and spends nothing. (mic.js's scripted
   * child line never uses `sendUserTurn`; test_mic_spend.mjs counts that.)
   * ===================================================================== */
  {
    const v = await open(HOSTED, { health: HEALTH_BARE });
    const { page, bodies, reqs, spent } = v;
    const s0 = await page.evaluate(snapshot);
    ok(s0.adopted && !s0.sayDisabled, "degraded: the box is still adopted and clickable — typing works with no brain");
    await type(page, "are you there");
    await moxieAnswered(page, 0);
    const s = await page.evaluate(snapshot);
    eq(posts(bodies, "chat").length + spent.length, 0, "a degraded page spends NO live turn on a typed line");
    ok(s.chatText.includes("are you there"), "…the visitor's own line is echoed");
    // The visitor is often a grown-up: named like every chat names them, not by protocol role.
    eq(s.whos[0], "You", `…under "You", not "Child" (labels ${JSON.stringify(s.whos)})`);
    ok(s.chatText.replace("are you there", "").trim().length > 0, "…and Moxie answers, from stub.js");
    eq(reqs.filter((u) => /:8081\//.test(u)).length, 0, "…with no sidecar request");
    eyes("hosted+degraded", v);
    await page.close();
  }

  /* =======================================================================
   * C. LOCAL + a reachable Piper — the local engine stays first-class: the typed turn does
   * not take the box. `/api/health` 404s = `offline` (sim/serve.py), where a sidecar belongs.
   * ===================================================================== */
  {
    const v = await open(LOCAL, { piper: WAV });
    const { page, bodies, reqs } = v;
    await waitFor(page, () => /piper/i.test((document.getElementById("tts-status") || {}).textContent || ""));
    const s0 = await page.evaluate(snapshot);
    eq(s0.adopted, false, "local + Piper: the typed turn does NOT take the box over (probe answered first)");
    eq(s0.sayText, "Say", "…the button keeps its name");
    ok(!s0.sayDisabled && s0.talkVisible && !s0.ttsTest && !s0.bus,
       "…it, the injected Talk box, #tts-test and the bus link all stay live on a local origin");
    await type(page, "this is a piper line");
    await waitFor(page, () => window.__audio.plays.some((p) => p.src === "clip"));
    const s = await page.evaluate(snapshot);
    const tts = reqs.filter((u) => /:8081\/tts\?/.test(u));
    ok(tts.length === 1 && decodeURIComponent(tts[0]).includes("this is a piper line"),
       `…the line went to the LOCAL Piper sidecar, carrying the typed text (got ${tts})`);
    eq(posts(bodies, "chat").length, 0, "…and NOT to the brain");
    const clip = s.audio.plays.filter((p) => p.src === "clip").pop();
    ok(clip && clip.peak > 0.5, `…the WAV was decoded and played audibly (${JSON.stringify(clip)})`);
    eyes("local+Piper", v);
    await page.close();
  }

  /* =======================================================================
   * D. LOCAL, no sidecar — adoption waits for the probe, then works offline.
   * ===================================================================== */
  {
    const v = await open(LOCAL, {});
    const { page, bodies, reqs } = v;
    await waitFor(page, () => window.moxieTypedTurn && window.moxieTypedTurn.adopted());
    const s0 = await page.evaluate(snapshot);
    ok(s0.adopted && s0.sayText === "Ask",
       "local with no sidecar: the box is adopted and relabelled once the probe has ANSWERED");
    eq(s0.ttsTest, false, "…but #tts-test stays clickable — a sidecar could be started at any moment");
    await type(page, "hello from localhost");
    await moxieAnswered(page, 0);
    const s = await page.evaluate(snapshot);
    eq(posts(bodies, "chat").length, 0, "…no live turn is spent");
    eq(reqs.filter((u) => /:8081\/tts\?/.test(u)).length, 0, "…and no doomed sidecar request is made");
    ok(s.chatText.includes("hello from localhost"), "…the line is echoed");
    eyes("local, no sidecar", v);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
