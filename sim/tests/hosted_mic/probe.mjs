/* hosted_mic/probe.mjs — the browser half of `sim/check_hosted_mic.mjs`: the fixtures the
 * fake microphone plays, one recorded turn (`probeTurn`), the clauses asserted over it
 * (`assertHeard`), and the report. The caller decides what is a failure, so `--selftest`
 * demands its reds from the same code path production runs.
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { serveStatic, web, repo, launchBrowser, notable, PHONE, IOS_UA, SPENDING,
         recordCspViolations, instrumentWebAudio, measureBoxes } from "../../browser_harness.mjs";
import { wavDurationMs } from "../../../functions/api/_lib/wav.js";
import { assertWords, wordOverlap, score, readWav, riff, padded, peakOf,
         IDENTITY_VOTE, FIDELITY_FLOOR } from "./score.mjs";

/* ════════════════════════ the fixtures ════════════════════════════════════════ *
 * The spoken clip is read from `sim/web/audio/index.json` — the manifest the SITE speaks
 * from — so re-rendering moves the fixture with it. The committed golden
 * (`goldens/real_voice_22050_mono.wav`) is 0.75 s cut from this very clip: too short to
 * transcribe, so it only proves plumbing. This is prerendered Piper speech, not a child in a
 * room; `MOXIE_MIC_WAV` + `MOXIE_MIC_TEXT` point the harness at a real recording. */
export const SPOKEN_TEXT = "Happy birthday! I hope your day is amazing.";
/** No content word in common with `SPOKEN_TEXT`; scored against the SAME transcript. */
export const DECOY_TEXT = "Can you take a deep breath with me?";

const MANIFEST = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8"));
function manifestClip(text) {
  const rel = (MANIFEST.moxie || {})[text];
  if (!rel) throw new Error(`no shipped clip for ${JSON.stringify(text)} — ` +
                            `sim/web/audio/index.json can no longer speak this line`);
  return rel;
}

/**
 * Decode shipped mp3 clips with the Chrome we already require (`decodeAudioData` on a
 * same-origin loopback page) — not ffmpeg, which a CI runner once did not have.
 * @returns {Promise<Record<string, Int16Array>>} keyed by manifest-relative path
 */
async function decodeClips(puppeteer, chrome, rels, rate) {
  const site = await serveStatic(web);
  const browser = await launchBrowser(puppeteer, chrome);
  try {
    const page = await browser.newPage();
    // The hub, not sim.html: a fetch + an AudioContext need no three.js or WebGL.
    await page.goto(site.url + "/index.html", { waitUntil: "domcontentloaded", timeout: 20000 });
    const got = await page.evaluate(async (list, r) => {
      const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: r });
      const out = {};
      for (const rel of list) {
        const raw = await (await fetch("audio/" + rel, { cache: "no-store" })).arrayBuffer();
        const buf = await ctx.decodeAudioData(raw);
        const ch = buf.getChannelData(0);
        const ints = new Array(ch.length);
        for (let i = 0; i < ch.length; i++) {
          let v = ch[i]; if (v > 1) v = 1; if (v < -1) v = -1;
          ints[i] = Math.round(v * 32767);
        }
        out[rel] = { rate: buf.sampleRate, pcm: ints };
      }
      try { ctx.close(); } catch (e) {}
      return out;
    }, rels, rate);
    const decoded = {};
    for (const rel of rels) {
      if (!got[rel]) throw new Error(`Chrome could not decode audio/${rel}`);
      if (got[rel].rate !== rate)
        throw new Error(`audio/${rel} decoded at ${got[rel].rate} Hz, wanted ${rate}`);
      decoded[rel] = Int16Array.from(got[rel].pcm);
    }
    return decoded;
  } finally {
    try { await browser.close(); } catch {}
    site.close();
  }
}

/**
 * The WAVs the fake microphone plays, in a throwaway directory. Written at 22050 Hz: Chrome
 * resamples the file to the capture rate (measured), which is also the honest test — a
 * real laptop hands `mic.js` 48 kHz and `encodeWav` decimates.
 */
export async function fixtures(puppeteer, chrome) {
  const RATE = 22050;
  const dir = mkdtempSync(join(tmpdir(), "moxie-hostedmic-"));
  const overrideWav = (process.env.MOXIE_MIC_WAV || "").trim();
  const overrideText = (process.env.MOXIE_MIC_TEXT || "").trim();
  const decoyRel = manifestClip(DECOY_TEXT);

  let spokenPcm, spokenRate = RATE, spokenText = SPOKEN_TEXT, decoyPcm;
  if (overrideWav) {
    // An override changes WHOSE voice is proven; it still has to clear every clause.
    if (!overrideText)
      throw new Error("MOXIE_MIC_WAV needs MOXIE_MIC_TEXT — the words in the clip, or " +
                      "there is nothing to score the transcript against");
    const got = readWav(readFileSync(overrideWav));
    spokenPcm = got.pcm; spokenRate = got.rate; spokenText = overrideText;
    ({ [decoyRel]: decoyPcm } = await decodeClips(puppeteer, chrome, [decoyRel], RATE));
  } else {
    const spokenRel = manifestClip(SPOKEN_TEXT);
    const got = await decodeClips(puppeteer, chrome, [spokenRel, decoyRel], RATE);
    spokenPcm = got[spokenRel]; decoyPcm = got[decoyRel];
  }
  const spokenPath = join(dir, "spoken.wav");
  writeFileSync(spokenPath, riff(padded(spokenPcm, spokenRate), spokenRate));
  const decoyPath = join(dir, "decoy.wav");
  writeFileSync(decoyPath, riff(padded(decoyPcm, RATE), RATE));
  const silencePath = join(dir, "silence.wav");
  writeFileSync(silencePath, riff(new Int16Array(RATE * 4), RATE));
  const goldenPath = join(repo, "sim", "tests", "goldens", "real_voice_22050_mono.wav");
  const golden = readWav(readFileSync(goldenPath));

  return {
    dir,
    spoken: { path: spokenPath, pcm: spokenPcm, rate: spokenRate, text: spokenText },
    decoy: { path: decoyPath, pcm: decoyPcm, rate: RATE, text: DECOY_TEXT },
    silence: { path: silencePath },
    golden: { path: goldenPath, pcm: golden.pcm, rate: golden.rate },
  };
}

/** A browser whose microphone is `wavPath` (a LAUNCH property: one clip, one browser). The
 *  fake-UI flag auto-accepts the permission prompt. */
export function launchWithMic(puppeteer, chrome, wavPath, args = [], hosts = {}) {
  return launchBrowser(puppeteer, chrome, {
    autoplay: true, hosts,
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
           `--use-file-for-fake-audio-capture=${wavPath}`, ...args],
  });
}

/* ════════════════════════ the run ═════════════════════════════════════════════ */
/** PAGE-SIDE: read uploads and answers off `fetch` (not interception: `postData()` is a
 *  string and mangles PCM; a `clone()` is what the page actually got). */
function watchApiFetch() {
  window.__mic = { calls: [], upload: null, uploadBytes: 0, pending: [] };
  const b64 = (ab) => {
    const u = new Uint8Array(ab); let s = "";
    for (let i = 0; i < u.length; i += 0x8000)
      s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
    return btoa(s);
  };
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const u = String((input && input.url) || input || "");
    const watched = /\/api\/(chat|speech|transcriptions|transcribe)\b/.test(u);
    let rec = null;
    if (watched) {
      rec = { url: u, status: 0, bodyLen: 0, body: "" };
      window.__mic.calls.push(rec);
      const body = init && init.body;
      if (body && typeof body.arrayBuffer === "function") {
        window.__mic.uploadBytes = body.size || 0;
        window.__mic.pending.push(
          body.arrayBuffer().then((ab) => { window.__mic.upload = b64(ab); }).catch(() => {}));
      }
    }
    const p = orig.apply(this, arguments);
    if (!watched) return p;
    return p.then((r) => {
      let c = null;
      try { c = r.clone(); } catch (e) { c = null; }
      if (c) window.__mic.pending.push(c.text().then((t) => {
        rec.status = r.status; rec.bodyLen = t.length; rec.body = t.slice(0, 3000);
      }).catch(() => {}));
      else rec.status = r.status;
      return r;
    }, (e) => { rec.status = -1; rec.body = String((e && e.message) || e); throw e; });
  };
}

/**
 * Load `url` on a phone with a fake microphone, press Listen, speak, press it again, and
 * record everything that happened.
 *
 * @param {{recordMs:number, settleMs?:number, budget:number, dry?:boolean,
 *          stub?:(r:any)=>boolean}} opts
 *   `stub` answers a request locally (`--selftest`); `dry` aborts every spending route.
 */
export async function probeTurn(browser, url, opts) {
  const page = await browser.newPage();
  await page.setViewport(PHONE);
  await page.setUserAgent(IOS_UA);
  await page.evaluateOnNewDocument(recordCspViolations);
  await page.evaluateOnNewDocument(watchApiFetch);
  await page.evaluateOnNewDocument(instrumentWebAudio);

  /* THE BUDGET, as an interceptor: past the ceiling (or always, under `dry`) a spending
   * request is aborted and recorded. The fetch wrapper already has its body either way. */
  const spent = [], refused = [], failed = [];
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (SPENDING.test(u)) {
      if (opts.dry || spent.length >= opts.budget) { refused.push(u); return r.abort("blockedbyclient"); }
      spent.push(u);
    }
    if (opts.stub && opts.stub(r)) return;
    return r.continue();
  });
  page.on("requestfailed", (r) => {
    if (refused.includes(r.url())) return;                     // ours, on purpose
    failed.push({ url: r.url(), why: r.failure()?.errorText || "?" });
  });
  const consoleErrs = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrs.push(m.text()); });
  page.on("pageerror", (e) => consoleErrs.push("PAGEERR " + e.message));

  const res = await page.goto(url, { waitUntil: "load", timeout: 45000 });
  await page.waitForFunction("!!window.moxieMic && !!window.moxieMode", { timeout: 20000 })
            .catch(() => {});
  // Wait for /api/health's ANSWER (whether the page has ears at all), not a clock.
  await page.waitForFunction("!!window.moxieMode.ears && window.moxieMode.ears() === true",
                             { timeout: 20000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, opts.settleMs ?? 1500));

  // Clause 1, measured BEFORE anything is pressed: unscrolled, the drawer shut.
  const boxes = await page.evaluate(measureBoxes, ["#mic-btn", "#speech-btn"]);
  const before = await page.evaluate(() => ({
    scrollY: Math.round(window.scrollY), innerW: innerWidth, innerH: innerHeight,
    /* A microphone needs a SECURE CONTEXT, granted by HOSTNAME — `--selftest`'s mapped
     * `.test` host needs `--unsafely-treat-insecure-origin-as-secure`; https needs nothing. */
    secure: !!window.isSecureContext,
    hasMediaDevices: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    title: document.title, hasHud: !!document.getElementById("hud"),
    railOpen: document.getElementById("rail-toggle")?.getAttribute("aria-expanded") ?? null,
    ears: !!(window.moxieMode && window.moxieMode.ears && window.moxieMode.ears()),
    target: window.moxieMic && window.moxieMic.sttTarget ? window.moxieMic.sttTarget() : null,
    maxRecordMs: window.moxieMic && window.moxieMic.maxRecordMs ? window.moxieMic.maxRecordMs() : null,
  }));
  before.mic = boxes["#mic-btn"];
  before.say = boxes["#speech-btn"];

  // SPEAK — a real click on the real button: the wiring is under test, and it is a gesture.
  let clicked = false;
  try { await page.click("#mic-btn"); clicked = true; } catch (e) {
    consoleErrs.push("CLICK #mic-btn failed: " + ((e && e.message) || e));
  }
  /* Did the device OPEN? Measured early so a refused microphone reports as that, not as two
   * burnt 45 s waits. 25 s: at 8 s a loaded box reported a false "never opened". */
  const opened = await page.waitForFunction(
    () => !!(window.moxieMic && window.moxieMic.isRecording && window.moxieMic.isRecording()),
    { timeout: 25000 }).then(() => true).catch(() => false);

  if (opened) {
    await new Promise((r) => setTimeout(r, opts.recordMs));
    if (clicked) await page.click("#mic-btn").catch(() => {});
    // The OUTCOME: a transcript or any recorded way the ears can fail ends the wait — and so
    // does a clip `mic.js` dropped unsent because none of it was speech (`noSpeech`).
    await page.waitForFunction(() => {
      const s = window.moxieMic && window.moxieMic.stats ? window.moxieMic.stats() : null;
      return !!s && (s.transcripts > 0 || s.fallbacks > 0 || s.tooShort > 0 || s.noSpeech > 0);
    }, { timeout: 45000 }).catch(() => {});
    // Nothing uploaded, nothing to answer: no 90 s of waiting for a reply that cannot come.
    const posted = await page.evaluate(
      () => !(window.moxieMic && window.moxieMic.stats) || window.moxieMic.stats().posts > 0);
    /* Then the ANSWER: every watched request has answered, and a buffer built from gateway
     * PCM (`bytes == null` — no pre-rendered clip can be) was scheduled. A plays-count once
     * let an ambient quip stand in for her answer, and an unanswered request read as 0 B. */
    if (posted) {
      await page.waitForFunction(
        () => window.__mic.calls.length > 0 && window.__mic.calls.every((c) => c.status !== 0),
        { timeout: 45000 }).catch(() => {});
    }
    if (posted && !opts.dry) {
      await page.waitForFunction(
        () => (window.__audio.plays || []).some((p) => p.bytes == null && p.frames > 1000),
        { timeout: 45000 }).catch(() => {});
    }
    await new Promise((r) => setTimeout(r, 2500));
  }

  const after = await page.evaluate(async () => {
    try { await Promise.all(window.__mic.pending); } catch (e) {}
    return {
      calls: window.__mic.calls, plays: window.__audio.plays,
      upload: window.__mic.upload, uploadBytes: window.__mic.uploadBytes,
      csp: window.__csp || [],
      micStatus: (document.getElementById("mic-status") || {}).textContent || "",
      transcriptText: (document.getElementById("transcript") || {}).textContent || "",
      stats: window.moxieMic && window.moxieMic.stats ? window.moxieMic.stats() : null,
    };
  });
  await page.close();

  // The transcript from the ROUTE'S answer — `#mic-status` truncates at 40 characters.
  let transcript = "", chatReply = "", speechBytes = 0;
  for (const c of after.calls) {
    let body = null;
    try { body = JSON.parse(c.body); } catch { body = null; }
    if (/transcribe|transcriptions/.test(c.url) && body && typeof body.transcript === "string")
      transcript = body.transcript.trim();
    if (/\/api\/chat\b/.test(c.url) && body) {
      for (const m of body.messages || []) {
        try {
          const p = JSON.parse(m.payload);
          if (p && p.output && p.output.text) chatReply = String(p.output.text);
        } catch {}
      }
    }
    if (/\/api\/speech\b/.test(c.url)) speechBytes = Math.max(speechBytes, c.bodyLen || 0);
  }
  const count = (re) => spent.filter((u) => re.test(u)).length;
  const spend = {
    transcribe: count(/transcribe|transcriptions/), chat: count(/\/api\/chat\b/),
    speech: count(/\/api\/speech\b/), refused: refused.length, total: spent.length,
  };
  return { url, budget: opts.budget, status: res ? res.status() : 0, before, opened, ...after,
           transcript, chatReply, speechBytes, spend, failed, consoleErrs, refused };
}

/* ---- the assertions, over one probe record ---------------------------------- */
/**
 * Clauses: 1 reachable without the engineering rail; 2 the device opened and a 16 kHz mono
 * WAV the SERVER's own reader accepts left the page, audibly; 3 it is the audio we played
 * (`ctx.identity`) and faithful (`ctx.fidelity`); 6 zero CSP violations / console errors;
 * and with `ctx.words`, 4 the transcript resembles the words and 5 she answered, out loud.
 */
export function assertHeard(c, p, tag, ctx) {
  const { ok, eq } = c;

  /* ---- clause 1: reachable, without the engineering rail ---- */
  eq(p.status, 200, `${tag}: HTTP status of ${p.url}`);
  ok(p.before.hasHud, `${tag}: #hud exists — is ${p.url} really the SIM page? ` +
     `(title ${JSON.stringify(p.before.title)})`);
  eq(p.before.scrollY, 0, `${tag}: the page must not have scrolled on its own`);
  ok(p.before.railOpen === null || p.before.railOpen === "false",
     `${tag}: the CONTROLS rail must still be closed (aria-expanded=${p.before.railOpen})`);
  for (const [sel, m] of [["#mic-btn", p.before.mic], ["#speech-btn", p.before.say]]) {
    ok(m.found, `${tag}: ${sel} exists`);
    if (!m.found) continue;
    ok(m.sized, `${tag}: ${sel} has a non-zero box — got ${m.w}×${m.h} ` +
                `(display:${m.display} visibility:${m.visibility})`);
    if (!m.sized) continue;
    ok(m.top >= 0 && m.bottom <= p.before.innerH,
       `${tag}: ${sel} is inside the first viewport — y ${m.top}…${m.bottom} of ${p.before.innerH}`);
    ok(m.self, `${tag}: a tap at the centre of ${sel} reaches it — elementFromPoint gave ${m.hit}`);
  }
  ok(!p.before.mic.disabled, `${tag}: #mic-btn is not disabled — this deployment has ears`);
  ok(p.before.target && p.before.target.kind === "cloud",
     `${tag}: the mic is aimed at the same-origin route, not a local sidecar — ` +
     JSON.stringify(p.before.target));

  /* ---- clause 2: the device opened, and audio really left the page ---- */
  ok(p.before.secure, `${tag}: the page is a SECURE CONTEXT — a microphone is unavailable ` +
     `anywhere else, whatever the button says`);
  ok(p.before.hasMediaDevices, `${tag}: navigator.mediaDevices.getUserMedia exists`);
  ok(p.opened, `${tag}: pressing Listen actually OPENED the microphone — mic.js reports ` +
     `isRecording() (status ${JSON.stringify(p.micStatus)})`);
  // POSTED, not paid for: under --dry-run the request is made and then aborted.
  const posted = p.spend.transcribe +
                 p.refused.filter((u) => /transcribe|transcriptions/.test(u)).length;
  eq(posted, 1, `${tag}: the page POSTed the clip to /api/transcribe exactly once`);
  ok(!!p.upload, `${tag}: the upload body was captured at all`);
  if (!p.upload) return;
  const wav = Buffer.from(p.upload, "base64");
  eq(wav.slice(0, 4).toString(), "RIFF", `${tag}: the upload is a RIFF file`);
  eq(wav.slice(8, 12).toString(), "WAVE", `${tag}: …a WAVE file`);
  // The SERVER's own reader pins `mic.js::encodeWav` and `_lib/wav.js` with one assertion.
  let dur = null;
  try { dur = wavDurationMs(wav); } catch (e) {
    ok(false, `${tag}: the server's own RIFF walker refused the upload — ${(e && e.message) || e}`);
  }
  if (dur) {
    eq(dur.sampleRate, 16000, `${tag}: uploaded at the rate the ears want ` +
       `(mic.js: "the rate that matters is 16000")`);
    eq(dur.channels, 1, `${tag}: mono`);
    eq(dur.bitsPerSample, 16, `${tag}: 16-bit`);
    ok(dur.ms > 1000, `${tag}: the clip is longer than a second — got ${dur.ms} ms`);
    ok(dur.ms < 15500, `${tag}: the clip is inside mic.js's 15 s hard stop — got ${dur.ms} ms`);
  }
  ok(wav.length >= 2000, `${tag}: over min_audio_bytes — ${wav.length} B`);
  ok(wav.length <= 500000, `${tag}: under max_audio_bytes — ${wav.length} B`);

  const got = readWav(wav);
  const peak = peakOf(got.pcm);
  ok(peak > 0.05, `${tag}: the captured audio is AUDIBLE, not a silent buffer — ` +
     `peak ${peak.toFixed(4)}`);

  /* ---- clause 3: it is the audio we played ----
   * Over a BROWSER capture only where the audio path is somebody's to look at (`--dry-run`,
   * the paid run): a CI runner's capture once voted 71 % for the wrong clip. Reported
   * everywhere; the push gate is `scorerProof` over committed bytes. */
  const id = score(got.pcm, got.rate, ctx);
  if (ctx.identity) {
    ok(id.vote >= IDENTITY_VOTE,
       `${tag}: the uploaded audio must be the clip the fake microphone played, not the ` +
       `other one — only ${(id.vote * 100).toFixed(0)}% of its ${id.chunks} chunks matched ` +
       `the clip played better than an unrelated one (need ${(IDENTITY_VOTE * 100).toFixed(0)}%). ` +
       `Median scores: ${id.good.toFixed(3)} played vs ${id.bad.toFixed(3)} unrelated.`);
  }
  if (ctx.fidelity) {
    ok(id.good >= FIDELITY_FLOOR,
       `${tag}: …and it must be a FAITHFUL recording of it — chunked log-RMS envelope ` +
       `score ${id.good.toFixed(3)} (floor ${FIDELITY_FLOOR}). A low score here with the ` +
       `vote above still healthy means the audio arrived but degraded: check the capture ` +
       `peak (${peak.toFixed(4)} — 1.0000 means the device saturated).`);
  }
  p.corr = { good: id.good, bad: id.bad, vote: id.vote, chunks: id.chunks, peak,
             wavBytes: wav.length, ms: dur ? dur.ms : 0, fidelity: !!ctx.fidelity,
             identity: !!ctx.identity };

  /* ---- clause 6, every mode. Requests this run aborted are forgiven one for one. ---- */
  c.eq(p.csp.length, 0, `${tag}: ZERO securitypolicyviolation events — ` +
       JSON.stringify(p.csp.slice(0, 4)));
  const noise = notable(p.consoleErrs, { n: p.refused.length },
    { abortedRe: /Failed to load resource: net::ERR_(BLOCKED_BY_CLIENT|CONNECTION_REFUSED|FAILED)/ });
  c.eq(noise.length, 0,
       `${tag}: ZERO console errors (${p.refused.length} forgiven for the request(s) this ` +
       `run aborted on purpose) — ` + JSON.stringify(noise.slice(0, 4)));

  if (!ctx.words) return;                       // no gateway, no words: `--dry-run`

  /* ---- clause 4: the transcript resembles the words ---- */
  p.scored = assertWords(c, p.transcript, { spoken: ctx.spoken, decoy: ctx.decoy, where: tag });
  eq(p.stats && p.stats.transcripts, 1, `${tag}: mic.js recorded exactly one real transcript`);
  eq(p.stats && p.stats.fallbacks, 0,
     `${tag}: …and burnt NO scripted consolation line (status ${JSON.stringify(p.micStatus)})`);
  const childOnPage = wordOverlap(p.transcript, p.transcriptText);
  ok(childOnPage >= 0.8, `${tag}: the words are on the page, in the comms log — only ` +
     `${childOnPage.toFixed(2)} of the transcript's words are there`);

  /* ---- clause 5: she answered, out loud ---- */
  eq(p.spend.chat, 1, `${tag}: exactly one POST /api/chat — the words a visitor said`);
  eq(p.spend.speech, 1, `${tag}: exactly one POST /api/speech — her own voice`);
  ok(p.chatReply.trim().length > 0, `${tag}: the brain answered with text — ` +
     JSON.stringify(p.chatReply.slice(0, 90)));
  // Scored, not substring-matched: the page renders the MARKUP with behaviour tags stripped.
  const onPage = wordOverlap(p.chatReply, p.transcriptText);
  ok(onPage >= 0.5, `${tag}: …and her answer is on the page too — only ${onPage.toFixed(2)} ` +
     `of its words are in the comms log`);
  // HER voice by identity: `bytes == null` is gateway PCM; a clip carries its byte length.
  const voice = p.plays.filter((x) => x.bytes == null && x.peak > 0.05 && x.frames > 1000);
  ok(voice.length > 0, `${tag}: the page SPOKE HER ANSWER — a buffer built from gateway PCM ` +
     `was scheduled, audibly. Heard: ${heardOf(p)}`);
  const longest = voice.reduce((a, b) => (b.frames / b.rate > a.frames / a.rate ? b : a),
                               voice[0] || { frames: 0, rate: 1, peak: 0 });
  ok(longest.frames / (longest.rate || 1) > 0.4,
     `${tag}: …for a plausible length, not a click — ` +
     `${(longest.frames / (longest.rate || 1)).toFixed(2)}s at ${longest.rate} Hz`);
  ok(p.speechBytes > 5000, `${tag}: /api/speech carried real audio bytes — ` +
     `${p.speechBytes} B of envelope`);

  c.eq(p.spend.refused, 0, `${tag}: nothing was refused by the budget ceiling ` +
       `(${p.spend.total} of ${p.budget} spent)`);
}

/** Every buffer scheduled, as a visitor heard it. */
const heardOf = (p) => p.plays.map((x) => `${(x.frames / (x.rate || 1)).toFixed(2)}s@` +
  `${x.peak.toFixed(2)}/${x.rate}Hz${x.bytes == null ? " (gateway)" : " (clip)"}`).join(" + ") || "SILENCE";

/** One line per measurement, so a run leaves numbers behind rather than a verdict. */
export function report(p, tag) {
  const box = (m) => m.found
    ? (m.sized ? `${m.w}×${m.h} at y=${m.top}…${m.bottom} hit=${m.hit}${m.self ? " (self)" : " ⚠ NOT SELF"}`
               : `${m.w}×${m.h} display:${m.display}`)
    : "ABSENT";
  console.log(`\n  ${tag}  ${p.url}`);
  console.log(`    HTTP ${p.status}  viewport ${p.before.innerW}×${p.before.innerH}  scrollY ${p.before.scrollY}` +
              `  rail aria-expanded=${p.before.railOpen}  ears=${p.before.ears}` +
              `  target=${p.before.target ? p.before.target.kind : "?"}  cap=${p.before.maxRecordMs}ms`);
  console.log(`    microphone      secureContext=${p.before.secure}  mediaDevices=${p.before.hasMediaDevices}` +
              `  opened=${p.opened}`);
  console.log(`    #mic-btn        ${box(p.before.mic)}`);
  console.log(`    #speech-btn     ${box(p.before.say)}`);
  if (p.corr)
    console.log(`    uploaded        ${p.corr.wavBytes} B  ${p.corr.ms} ms  peak ${p.corr.peak.toFixed(4)}` +
                `${p.corr.peak > 0.999 ? " (SATURATED)" : ""}` +
                `\n    identity        ${(p.corr.vote * 100).toFixed(0)}% of ${p.corr.chunks} chunks chose the clip played` +
                ` (${p.corr.identity ? "need " + (IDENTITY_VOTE * 100).toFixed(0) + "%" : "REPORTED ONLY"})` +
                `   medians ${p.corr.good.toFixed(3)} played` +
                ` / ${p.corr.bad.toFixed(3)} unrelated   fidelity floor ` +
                `${p.corr.fidelity ? FIDELITY_FLOOR + " ASSERTED" : "reported only"}`);
  else
    console.log(`    uploaded        ${p.uploadBytes} B (not parsed)`);
  console.log(`    transcript      ${JSON.stringify(p.transcript)}` +
              (p.scored ? `   overlap ${p.scored.right.toFixed(2)} decoy ${p.scored.wrong.toFixed(2)}` : ""));
  console.log(`    reply           ${JSON.stringify(p.chatReply.slice(0, 100))}`);
  console.log(`    spoken back     ${heardOf(p)}   /api/speech ${p.speechBytes} B`);
  console.log(`    mic-status      ${JSON.stringify(p.micStatus)}`);
  console.log(`    mic stats       ${JSON.stringify(p.stats)}`);
  console.log(`    SPEND           transcribe ${p.spend.transcribe}  chat ${p.spend.chat}` +
              `  speech ${p.spend.speech}   (ceiling ${p.budget}, refused ${p.spend.refused})`);
  console.log(`    CSP violations  ${p.csp.length}${p.csp.length ? "  " + JSON.stringify(p.csp.slice(0, 3)) : ""}` +
              `   console errors ${p.consoleErrs.length}   failed requests ${p.failed.length}`);
  for (const e of p.consoleErrs.slice(0, 5)) console.log(`      · console ${e.slice(0, 150)}`);
  for (const f of p.failed.slice(0, 5)) console.log(`      · failed ${f.url} — ${f.why}`);
}
