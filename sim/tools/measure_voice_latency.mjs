/* measure_voice_latency.mjs — send-to-first-audio of typed turns, in a real browser, against
 * a REAL deployment of the hosted Sim (a local `wrangler pages dev` or the site itself).
 *
 * SPENDS MONEY; NOT A TEST. Every turn is one /api/chat and one /api/speech per voice chunk
 * (at most `MAX_SPEECH_CHUNKS`, 3). Refuses to start without `--yes`; `--turns=N` is a hard
 * cap on chat POSTs (default 10, the W2-S12 measurement).
 *
 *   node sim/tools/measure_voice_latency.mjs --yes --base=http://127.0.0.1:8804 --turns=10 --out=/tmp/arm.json
 *   node sim/tools/measure_voice_latency.mjs --yes --base=https://moxie.example --turns=5 --label=prod
 *
 * What it records, per turn, where the sound is made (`browser_harness.mjs::instrumentWebAudio`):
 *   send_to_chat_ms     the /api/chat round trip (fetch instrumented on the page)
 *   send_to_first_audio the headline: `moxieTypedTurn.send()` to her first gateway-voice
 *                       buffer STARTING (chunk 0), in ms
 *   chunks              how many /api/speech round trips the turn made, and each one's RTT
 *   gaps_ms             silence between consecutive chunks (next start − previous end; a
 *                       negative value is overlap, which voice/ forbids, so it is a bug)
 *   voice_ms            the whole voice, first start to last end
 *   reply_chars         the reply's length, so arms can be compared on like replies
 * and prints the medians. Audio is unlocked by Chrome's flag; nothing here taps the page.
 */
import { writeFileSync } from "node:fs";
import { requireBrowser, launchBrowser, instrumentWebAudio } from "../browser_harness.mjs";

const flag = (name, dflt) => {
  const hit = process.argv.find((a) => a.startsWith("--" + name + "="));
  return hit ? hit.slice(name.length + 3) : dflt;
};
if (!process.argv.includes("--yes")) {
  console.error("measure_voice_latency.mjs drives a REAL deployment and SPENDS REAL GATEWAY CALLS.\n" +
                "Re-run with --yes if that is what you want.\n" +
                "  node sim/tools/measure_voice_latency.mjs --yes --base=URL [--turns=10] [--out=FILE] [--label=arm]");
  process.exit(2);
}
const BASE = String(flag("base", "http://127.0.0.1:8788")).replace(/\/+$/, "");
const TURNS = Math.max(1, Math.min(40, Number(flag("turns", 10)) || 10));
const OUT = flag("out", "");
const LABEL = flag("label", "arm");

/** A stranger's ten lines: greetings, questions that earn a longer answer, a goodbye. */
const LINES = [
  "hi moxie! what are you doing right now?",
  "I'm Sam and I'm 7. Do you like dinosaurs?",
  "tell me a silly joke",
  "why is the sky blue?",
  "what's your favorite food?",
  "can you tell me about volcanoes?",
  "I had a bad day at school today",
  "what should we play together?",
  "do you remember my name?",
  "ok bye moxie, see you later!",
  "what do rainbows come from?",
  "how do birds fly?",
  "what is the moon made of?",
  "do robots dream?",
  "tell me something about the ocean",
  "what is your favorite color?",
  "can you count to five?",
  "what makes thunder?",
  "who built you?",
  "sing me a song",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const median = (xs) => {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!s.length) return null;
  return s.length % 2 ? s[(s.length - 1) / 2] : Math.round((s[s.length / 2 - 1] + s[s.length / 2]) / 2);
};
const p90 = (xs) => {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor(0.9 * (s.length - 1)))] : null;
};

/** PAGE-SIDE, before any page script: every /api/* fetch with its timings. */
function instrumentFetch() {
  window.__api = [];
  const of = window.fetch;
  window.fetch = function (input, init) {
    const url = String((input && input.url) || input || "");
    const m = /\/api\/(chat|speech|transcribe)\b/.exec(url);
    if (!m) return of.call(this, input, init);
    const rec = { route: m[1], t: performance.now(), doneAt: null, status: null };
    window.__api.push(rec);
    return of.call(this, input, init).then((r) => { rec.doneAt = performance.now(); rec.status = r.status; return r; },
                                           (e) => { rec.doneAt = performance.now(); rec.status = "error"; throw e; });
  };
}

const { puppeteer, chrome } = await requireBrowser("measure_voice_latency");
const browser = await launchBrowser(puppeteer, chrome, { autoplay: true });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });
await page.evaluateOnNewDocument(instrumentWebAudio);
await page.evaluateOnNewDocument(instrumentFetch);
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.message || e)));

const result = { base: BASE, label: LABEL, at: new Date().toISOString(), turns: [], errors };
try {
  await page.goto(BASE + "/sim.html", { waitUntil: "load", timeout: 60000 });
  // Live, and the typed turn adopted (env.js adopts after its local-sidecar probe settles).
  const ready = await page.waitForFunction(
    () => window.moxieMode && window.moxieMode.state() === "live" && window.moxieMode.canSpendLiveTurn() &&
          window.moxieTypedTurn && window.moxieTypedTurn.adopted(),
    { timeout: 20000, polling: 100 }).then(() => true, () => false);
  if (!ready) throw new Error("the page never became live with a typed turn (is /api/health live? is .dev.vars set?)");
  // Unlock audio the way a visitor would, once, so no turn is waiting on a gesture.
  await page.mouse.click(10, 10);
  await sleep(1500);

  for (let i = 0; i < TURNS; i++) {
    const line = LINES[i % LINES.length];
    // Her previous voice (and the ambient mutters) must be over, or a cut would be counted.
    await page.waitForFunction(() => !window.moxieAudio.isMoxieBusy(800), { timeout: 30000, polling: 100 }).catch(() => {});
    const before = await page.evaluate(() => ({ api: window.__api.length, plays: window.__audio.plays.length, now: performance.now() }));
    const t0 = await page.evaluate((x) => { const t = performance.now(); window.moxieTypedTurn.send(x); return t; }, line);
    // Wait for the first gateway-voice buffer to start, then for the voice to end.
    const first = await page.waitForFunction((n) => window.__audio.plays.slice(n).some((p) => p.src === "pcm"),
      { timeout: 40000, polling: 20 }, before.plays).then(() => true, () => false);
    if (first) {
      await page.waitForFunction(() => {
        const st = window.moxieBridge.transportStats();
        return !window.moxieAudio.isMoxieBusy(1200) && window.moxieAudio.ttsPending() === 0 && st.tickets >= 0;
      }, { timeout: 60000, polling: 100 }).catch(() => {});
    } else {
      await sleep(3000);
    }
    const tl = await page.evaluate((b) => ({
      api: window.__api.slice(b.api).map((a) => ({ ...a })),
      plays: window.__audio.plays.slice(b.plays).filter((p) => p.src === "pcm").map((p) => ({ t: p.t, dur: p.dur, frames: p.frames })),
      stops: window.__audio.stops.filter((s) => s.t >= b.now).length,
      reply: (() => { const r = document.querySelectorAll("#transcript .turn.moxie .msg"); return r.length ? r[r.length - 1].textContent : ""; })(),
      stats: window.moxieBridge.transportStats(),
    }), before);
    const chat = tl.api.find((a) => a.route === "chat");
    const speeches = tl.api.filter((a) => a.route === "speech");
    const plays = tl.plays.sort((a, b) => a.t - b.t);
    const gaps = [];
    for (let k = 1; k < plays.length; k++) gaps.push(Math.round(plays[k].t - (plays[k - 1].t + plays[k - 1].dur)));
    const row = {
      i: i + 1, line, reply_chars: tl.reply.length, reply: tl.reply.slice(0, 120),
      send_to_chat_ms: chat && chat.doneAt ? Math.round(chat.doneAt - t0) : null,
      chat_rtt_ms: chat && chat.doneAt ? Math.round(chat.doneAt - chat.t) : null,
      chunks: speeches.length,
      speech_rtt_ms: speeches.map((s) => (s.doneAt ? Math.round(s.doneAt - s.t) : null)),
      speech_status: speeches.map((s) => s.status),
      send_to_first_audio_ms: plays.length ? Math.round(plays[0].t - t0) : null,
      chunk_starts_ms: plays.map((p) => Math.round(p.t - t0)),
      chunk_dur_ms: plays.map((p) => Math.round(p.dur)),
      gaps_ms: gaps,
      voice_ms: plays.length ? Math.round(plays[plays.length - 1].t + plays[plays.length - 1].dur - plays[0].t) : null,
      stops: tl.stops,
      reasons: tl.stats.speechReasons.slice(-3),
    };
    result.turns.push(row);
    console.log(`turn ${row.i}: chat ${row.chat_rtt_ms} ms, first audio ${row.send_to_first_audio_ms} ms, ` +
                `${row.chunks} chunk(s) rtt [${row.speech_rtt_ms}], gaps [${row.gaps_ms}], reply ${row.reply_chars} chars`);
    await sleep(1500);
  }
  result.final = await page.evaluate(() => window.moxieBridge.transportStats());
} catch (e) {
  result.error = e.stack || String(e);
  console.error("ERROR", e);
} finally {
  await browser.close().catch(() => {});
}

const T = result.turns;
result.summary = {
  turns: T.length,
  voiced: T.filter((t) => t.send_to_first_audio_ms != null).length,
  median_send_to_first_audio_ms: median(T.map((t) => t.send_to_first_audio_ms)),
  p90_send_to_first_audio_ms: p90(T.map((t) => t.send_to_first_audio_ms)),
  median_chat_rtt_ms: median(T.map((t) => t.chat_rtt_ms)),
  median_chunk0_rtt_ms: median(T.map((t) => (t.speech_rtt_ms[0] != null ? t.speech_rtt_ms[0] : NaN))),
  median_reply_chars: median(T.map((t) => t.reply_chars)),
  chunks_per_turn: T.map((t) => t.chunks),
  max_gap_ms: Math.max(-1, ...T.flatMap((t) => t.gaps_ms)),
  gaps_over_1500_ms: T.flatMap((t) => t.gaps_ms).filter((g) => g > 1500).length,
  chat_posts: T.length,
  speech_posts: T.reduce((n, t) => n + t.chunks, 0),
};
console.log("\n" + LABEL + ": " + JSON.stringify(result.summary, null, 1));
if (OUT) { writeFileSync(OUT, JSON.stringify(result, null, 2)); console.log("saved " + OUT); }
process.exit(result.error ? 1 : 0);
