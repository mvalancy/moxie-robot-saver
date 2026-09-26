/* test_mic_spend.mjs — a refused microphone must not spend a live turn. In Chrome.
 *
 * `mic.js` consoles a visitor whose ears failed with a SCRIPTED child line; that line once
 * went out through the live transport and bought a paid `/api/chat` + `/api/speech` nobody
 * said. Billing is a claim about requests that really left a page, so this counts them
 * (`page.on("request")`) — and pairs every "spends nothing" with proof the visitor was still
 * consoled OUT LOUD (the shipped clips, identified by byte size, audibly scheduled), so the
 * fix cannot be "delete the consolation line". `/api/*` is answered at the browser and the
 * recorder injected via `moxieMic.setCapture`: no gateway, no network, no live microphone.
 *
 *   1 refused transcription · 2 clip over max_audio_bytes · 3 transcriber unreachable
 *   4 a real transcript (exactly one chat + speech) · 5 clip under min_audio_bytes
 *   6 a microphone that will not open → typed recovery on desktop and phone
 *
 *   node sim/test_mic_spend.mjs
 */
import { join } from "node:path";
import { readFileSync, statSync } from "node:fs";
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, repo, launchBrowser,
         watchPage, notable, instrumentWebAudio, liveFixture } from "./browser_harness.mjs";

const LABEL = "mic-spend test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;

const REPLY = "That sounds like a wonderful day!";
const TONE = pcmToneBase64({ seconds: 0.3, rate: 22050, freq: 440, amp: 0.8 });
const FX = await liveFixture({ eid: "sim-micspend01", reply: REPLY, tone: TONE });
/** The limits the page is actually operating under — read from the envelope, never guessed. */
const LIMITS = FX.limits;

/* The two utterances a consoled visitor must HEAR: `mic.js::fallback`'s child line and
 * `stub.js`'s answer, both played from the pre-rendered clip the SHIPPED MANIFEST names
 * (so re-rendering moves the assertion with it). Their byte sizes are what `spoke()` matches. */
const CHILD_LINE = "Thank you Moxie!";
const MOXIE_LINE = "You're so welcome. I love celebrating with you!";
const MANIFEST = JSON.parse(readFileSync(join(repo, "sim", "web", "audio", "index.json"), "utf8"));
function shippedClip(group, text) {
  const rel = (MANIFEST[group] || {})[text];
  if (!rel) throw new Error(`no ${group} clip shipped for ${JSON.stringify(text)} — ` +
                            `sim/web/audio/index.json can no longer speak this line`);
  return { group, text, rel, bytes: statSync(join(repo, "sim", "web", "audio", rel)).size };
}
const CHILD_CLIP = shippedClip("child", CHILD_LINE);
const MOXIE_CLIP = shippedClip("moxie", MOXIE_LINE);
const CLIP_NAME = new Map([[CHILD_CLIP.bytes, "the child's line"], [MOXIE_CLIP.bytes, "Moxie's answer"]]);

const browser = await launchBrowser(puppeteer, chrome,
  { autoplay: true, hosts: { "moxie.hosted.test": site.port } });

const json = (body, status = 200) => ({ status, contentType: "application/json", body });

/**
 * Open the hosted, live sim with `/api/*` answered at the browser. `transcribe` is
 * "ok" | "refused" | "dead". `/api/chat` and `/api/speech` ALWAYS succeed, so a turn that
 * should never have been spent shows up as a real answer rather than a second failure.
 */
async function open(opts) {
  const page = await browser.newPage();
  await page.setViewport(opts.viewport || { width: 1440, height: 900 });
  const { errs, aborted } = watchPage(page);
  const reqs = [], bodies = [];
  page.on("request", (r) => {
    reqs.push(r.url());
    if (/\/api\/(chat|speech|transcribe)\b/.test(r.url()))
      bodies.push({ url: r.url(), body: r.postData() || "" });
  });
  await page.evaluateOnNewDocument(instrumentWebAudio);

  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/\/api\/health\b/.test(u)) return r.respond(json(FX.health));
    if (/\/api\/transcribe\b/.test(u)) {
      if (opts.transcribe === "dead") { aborted.n++; return r.abort("connectionrefused"); }
      if (opts.transcribe === "refused")
        return r.respond(json(FX.env({ ok: false, degraded: true, reason: "bad_request" }), 400));
      return r.respond(json(FX.env({ transcript: "I went to the park today" })));
    }
    if (/\/api\/chat\b/.test(u)) return r.respond(json(FX.chat));
    if (/\/api\/speech\b/.test(u)) return r.respond(json(FX.speech));
    // The local sidecars cannot exist on a hosted origin (the CSP refuses them).
    if (/:808[12]\//.test(u)) { aborted.n++; return r.abort("connectionrefused"); }
    return r.continue();
  });

  await page.goto(HOSTED, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!window.moxieMic && !!window.moxieBridge && !!window.moxieMode",
                             { timeout: 15000 });
  await page.waitForFunction("window.moxieMode.canSpendLiveTurn() === true", { timeout: 15000 })
    .catch(() => {});
  await new Promise((r) => setTimeout(r, 500));
  return { page, errs, reqs, bodies, aborted };
}

/** How many of each `/api/*` route the browser actually asked for. */
const spend = (reqs) => ({
  transcribe: reqs.filter((u) => /\/api\/transcribe\b/.test(u)).length,
  chat: reqs.filter((u) => /\/api\/chat\b/.test(u)).length,
  speech: reqs.filter((u) => /\/api\/speech\b/.test(u)).length,
});

/**
 * Press, hold and release `#mic-btn` with a FAKE recorder (the `moxieMic.setCapture` seam)
 * that yields exactly `size` bytes; the caps, size gates and fallback are the REAL ones.
 * 3 s is the floor a request that should never happen has to appear; `utterances` then
 * WAITS for that many sounds — a slow runner once snapshotted before Moxie's clip arrived.
 * The wait only extends the window, so the spend assertions lose nothing.
 */
async function press(page, size, opts) {
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
  await new Promise((r) => setTimeout(r, 3000));
  if (opts && opts.utterances)
    await page.waitForFunction((k) => window.__audio.started >= k, { timeout: 15000 }, opts.utterances)
      .catch(() => {});
}

/** Everything that actually reached the speakers, as a visitor would have heard it. */
const heard = (s) => ((s.audio && s.audio.plays) || [])
  .map((p) => `${(p.frames / (p.rate || 1)).toFixed(2)}s@${p.peak.toFixed(2)} ` +
              (p.bytes == null ? "(synthesized)" : `(${CLIP_NAME.get(p.bytes) || p.bytes + "B, unknown clip"})`))
  .join(" + ") || "SILENCE";

/** That EXACT shipped clip reached Web Audio, audibly — which UTTERANCE, not how many sounds
 *  (a count once let an ambient quip stand in for Moxie's answer). */
function spoke(s, clip, why) {
  const hit = ((s.audio && s.audio.plays) || []).find((p) => p.bytes === clip.bytes && p.peak > 0.05);
  ok(!!hit, `${why} — heard: ${heard(s)}`);
}

/** No console errors beyond the ones this fixture provoked (forgiven one for one). */
function quiet(errs, aborted, label, refused = 0) {
  const left = notable(errs, { n: aborted.n, refused }, {
    abortedRe: /Failed to load resource: net::ERR_(CONNECTION_REFUSED|FAILED|BLOCKED_BY_CLIENT)/,
    refusedRe: /Failed to load resource: the server responded with a status of 400/,
  });
  eq(left.length, 0, `${label}: ${left.slice(0, 3).join(" | ")}`);
}

/** What a visitor would see and hear. */
const snapshot = () => ({
  status: (document.getElementById("mic-status") || {}).textContent || "",
  chatText: (document.getElementById("transcript") || {}).textContent || "",
  stats: window.moxieMic.stats(),
  transport: window.moxieBridge.transportStats ? window.moxieBridge.transportStats() : null,
  audio: window.__audio,
});

try {
  /* 1. THE DEFECT — the route REFUSES the clip. `bad_request` changes NO mode, so the live
   *    gate is still open when the fallback runs (the other refusals shut it on the way). */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "refused" });
    const live = await page.evaluate(() => window.moxieMode.canSpendLiveTurn());
    eq(live, true, "the page is live and a turn IS spendable — the gate is open, not closed");

    await press(page, 40000, { utterances: 2 });
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
    spoke(s, CHILD_CLIP, "…and the child's line was SPOKEN, from the clip this site ships for it");
    spoke(s, MOXIE_CLIP, "…and so was Moxie's answer — BOTH of them, audibly, not one");
    eq(s.transport && s.transport.scriptedFree, 1, "…recorded as a scripted line answered for free");
    eq(s.transport && s.transport.live, 0, "…and no live turn was ever opened");
    quiet(errs, aborted, "no unexpected console errors", 1);
    await page.close();
  }

  /* 2. A clip over `max_audio_bytes`, refused CLIENT-side: no upload, so nothing may be paid. */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, LIMITS.max_audio_bytes + 1, { utterances: 2 });
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 0, "an over-long clip is never uploaded — the free gate still holds");
    eq(paid.chat, 0, "…AND IT SPENDS NO /api/chat: a refusal that cost nothing stays costing nothing");
    eq(paid.speech, 0, "…nor an /api/speech");
    eq(s.stats.tooLong, 1, "…recorded as too long");
    eq(s.stats.fallbacks, 1, "…and the visitor is still consoled");
    ok(/too long/.test(s.status), `…and told why (got ${JSON.stringify(s.status)})`);
    ok(s.chatText.includes("Thank you Moxie!"), "…with the scripted line on the page");
    spoke(s, CHILD_CLIP, "…spoken aloud, from its own clip");
    spoke(s, MOXIE_CLIP, "…and answered aloud too");
    quiet(errs, aborted, `no console errors`);
    await page.close();
  }

  /* 3. The ears are UNREACHABLE — a transport error is one strike of three, so the gate is
   *    still open. */
  {
    const { page, reqs } = await open({ transcribe: "dead" });
    await press(page, 40000, { utterances: 2 });
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.chat, 0, "an unreachable transcriber spends NO /api/chat");
    eq(paid.speech, 0, "…and no /api/speech");
    eq(s.stats.fallbacks, 1, "…and still consoles the visitor");
    ok(s.chatText.includes("Thank you Moxie!"), "…with a scripted child line on the page");
    spoke(s, CHILD_CLIP, "…that really played");
    await page.close();
  }

  /* 4. A REAL TRANSCRIPT still spends exactly one turn — a fix that stopped the microphone
   *    spending at all would pass everything above. */
  {
    const { page, errs, reqs, aborted } = await open({ transcribe: "ok" });
    await press(page, 40000, { utterances: 1 });
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
    quiet(errs, aborted, `no console errors on the live spoken turn`);
    await page.close();
  }

  /* 5. A clip under `min_audio_bytes` is a slipped button: no spend AND no consolation line. */
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
    quiet(errs, aborted, `no console errors`);
    await page.close();
  }

  /* 6. A microphone that will NOT OPEN, at desktop and phone widths: costs nothing, names
   *    the typed recovery, and that recovery carries a response, a context-bearing second
   *    turn and goodbye. Hermetic Sim evidence, not a physical-device claim. */
  for (const visit of [
    { label: "desktop", viewport: { width: 1440, height: 900 } },
    { label: "phone", viewport: { width: 390, height: 844 } },
  ]) {
    const { page, errs, reqs, bodies, aborted } = await open({
      transcribe: "ok", viewport: visit.viewport,
    });
    // One click is the denial (`press()` clicks twice, which would ask twice).
    await page.evaluate(() => window.moxieMic.setCapture(
      () => Promise.reject(new Error("NotAllowedError"))));
    await page.click("#mic-btn");
    await page.waitForFunction(
      "/permission denied|unsupported/.test(document.getElementById('mic-status').textContent)",
      { timeout: 5000 });
    const s = await page.evaluate(snapshot);
    const paid = spend(reqs);

    eq(paid.transcribe, 0, `${visit.label}: denied microphone uploads nothing`);
    eq(paid.chat, 0, `${visit.label}: …spends no /api/chat — it never did`);
    eq(paid.speech, 0, `${visit.label}: …and no /api/speech`);
    eq(s.stats.fallbacks, 0, `${visit.label}: …and reaches no fallback at all`);
    ok(/type a message/i.test(s.status) && /tap Ask/i.test(s.status),
       `${visit.label}: …names the exact typed recovery (got ${JSON.stringify(s.status)})`);
    eq(await page.evaluate(() => window.moxieMic.isRecording()), false,
       `${visit.label}: …and is not left recording`);

    const reachable = await page.evaluate(() => {
      const input = document.getElementById("speech-input");
      const ask = document.getElementById("speech-btn");
      function hit(el) {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height || r.top < 0 || r.bottom > innerHeight) return false;
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return top === el || el.contains(top);
      }
      return { input: hit(input), ask: hit(ask), askText: (ask || {}).textContent || "" };
    });
    ok(reachable.input && reachable.ask,
       `${visit.label}: the message field and Ask button are both visible and tappable after denial`);
    eq(reachable.askText, "Ask", `${visit.label}: the recovery copy names the button verbatim`);

    const turns = ["hello moxie", "what do you remember?", "goodbye moxie"];
    for (let i = 0; i < turns.length; i++) {
      await page.$eval("#speech-input", (el, text) => { el.value = text; }, turns[i]);
      await page.click("#speech-btn");
      await page.waitForFunction((n) =>
        window.moxieBridge.transportStats().chatOk >= n, { timeout: 10000 }, i + 1);
    }
    const chat = bodies.filter((b) => /\/api\/chat\b/.test(b.url)).map((b) => JSON.parse(b.body));
    const after = await page.evaluate(snapshot);
    eq(chat.length, 3, `${visit.label}: recovery completes response, second turn, and goodbye`);
    eq(JSON.stringify(chat.map((b) => b.text)), JSON.stringify(turns),
       `${visit.label}: all three visitor lines reach the brain in order`);
    eq(chat[0] && chat[0].context, "", `${visit.label}: the first recovered turn starts a session`);
    ok(chat.slice(1).every((b) => b.context === "v1.CTX.MAC"),
       `${visit.label}: second turn and goodbye carry the signed conversation context`);
    eq(spend(reqs).transcribe, 0,
       `${visit.label}: typed recovery never retries the denied microphone behind the visitor's back`);
    ok(after.chatText.includes("goodbye moxie") && after.chatText.includes(REPLY),
       `${visit.label}: goodbye and Moxie's response are visible in the conversation`);
    quiet(errs, aborted, `${visit.label}: no console errors`);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
