/* journey/turns.mjs — a child's conversation on a phone, end to end, SPENDING (ledgered):
 *   1 typed first turn · 2 the mic (fake capture plays a WAV) · 3 goodbye + 50 s after it ·
 *   4 "I'm back" in the same tab · reload (coming back) · 5 "do you remember me?"
 *   node sim/tools/journey/turns.mjs --base=URL --mic=WAV [--tag=prod] [--desktop] [--only=1,2]
 * With no --base it aims at the site's own origin, where the guard caps chat turns at 5
 * across ALL runs sharing the ledger (lib.mjs), plus the per-run caps below. Every turn also
 * carries its cue report (the longest stretch of the wait with nothing of her working).
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { browserFor, visitor, shot, sleep, simReady, pnow, waitTurnDone, turnSummary, turnCues,
         recorderStopT, audioCuts, fillersCutByVoice, sendTime, PROD, PHONE, IOS_UA, DESKTOP, DESKTOP_UA,
         OUT, prodChatSpent } from "./lib.mjs";

const flag = (n, d) => { const h = process.argv.find((a) => a.startsWith("--" + n + "=")); return h ? h.slice(n.length + 3) : d; };
const base = String(flag("base", PROD || "")).replace(/\/$/, "");
if (!base) { console.error("--base= is required (no canonical origin in sim/web/index.html)"); process.exit(2); }
const mic = flag("mic", "");
const tag = flag("tag", "prod");
const desktop = process.argv.includes("--desktop");
const only = flag("only", "");          // e.g. "1,2" to run a subset (debugging)
const u = new URL(base);
const hosts = {}, secure = [];
if (u.hostname.endsWith(".test")) { hosts[u.hostname] = Number(u.port); secure.push(u.origin); }
const isProd = !!PROD && u.origin === PROD;
if (isProd) console.log(`production chat turns already spent: ${prodChatSpent()} / 5`);

const browser = await browserFor({ hosts, mic: mic || null, secureOrigins: secure });
const run = `turns-${tag}`;
const v = await visitor(browser, { run, viewport: desktop ? DESKTOP : PHONE, ua: desktop ? DESKTOP_UA : IOS_UA,
                                    grantMic: u.origin, caps: { chat: 5, speech: 16, transcribe: 2 } });
const { page } = v;
const touch = !desktop;
const results = { base, tag, steps: [] };
const want = (n) => !only || only.split(",").includes(String(n));

async function tap(sel) {
  const b = await page.$eval(sel, (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.evaluate((s) => window.__tlog("tap", s), sel);
  if (touch) await page.touchscreen.tap(b.x, b.y); else await page.mouse.click(b.x, b.y);
}
async function typeAndAsk(text) {
  await tap("#speech-input");
  await page.type("#speech-input", text, { delay: 45 });
  const t = await pnow(page);
  await tap("#speech-btn");
  return t;
}
async function record(name, t0, extra = {}) {
  const d = await v.dump(`${run}-${name}`);
  const s = turnSummary(d.state, t0);
  const step = { name, ...extra, ...s };
  const stopT = extra.kind === "mic" ? recorderStopT(d.state, t0) : null;
  step.recorder_stop_at = stopT != null ? Math.round(stopT - t0) : null;
  step.cue = turnCues(d.state, stopT != null ? stopT : sendTime(d.state, t0), t0 + ((extra.waited && extra.waited.waited) || 0));
  step.cuts = audioCuts(d.state, t0);
  results.steps.push(step);
  const { timeline, ...rest } = s;
  const brief = { name, ...extra, ...rest, cue: { longest_ms: step.cue.longest_ms, total_ms: step.cue.total_ms, until_is: step.cue.until_is, runs: step.cue.runs },
                  cuts: step.cuts };
  console.log(JSON.stringify(brief));
  writeFileSync(join(OUT, `${run}-results.json`), JSON.stringify(results, null, 1));
  return step;
}

await page.goto(base + "/sim", { waitUntil: "domcontentloaded", timeout: 90000 });
const readyMs = await simReady(page);
console.log(`sim ready in ${readyMs} ms`);
await shot(page, `${run}-0-ready`);

// ---- 1. the first typed turn
if (want(1)) {
  const t1 = await typeAndAsk("Hi Moxie! My name is Sam and I'm seven.");
  await sleep(600); await shot(page, `${run}-1-thinking`);
  const w = await waitTurnDone(page, { sinceT: t1 });
  await shot(page, `${run}-1-answered`);
  await record("1-typed", t1, { waited: w });
}

// ---- 2. the mic
if (want(2) && mic) {
  await sleep(1500);
  const t2 = await pnow(page);
  await tap("#mic-btn");
  await sleep(1200); await shot(page, `${run}-2-listening`);
  // the clip auto-stops after a breath of silence; then transcribe -> chat -> voice
  await page.waitForFunction(() => window.moxieMic && !window.moxieMic.isRecording(), { timeout: 20000, polling: 100 }).catch(() => {});
  const stopT = await pnow(page);
  await sleep(800); await shot(page, `${run}-2-transcribing`);
  const w = await waitTurnDone(page, { sinceT: t2, timeout: 60000 });
  await shot(page, `${run}-2-answered`);
  await record("2-mic", t2, { kind: "mic", waited: w, recording_ms: stopT - t2 });
}

// ---- 3. goodbye, then 50 s of what the page does after it
if (want(3)) {
  await sleep(1500);
  const t3 = await typeAndAsk("ok bye Moxie, I have to go now!");
  const w = await waitTurnDone(page, { sinceT: t3 });
  await shot(page, `${run}-3-goodbye`);
  await sleep(5000); await shot(page, `${run}-3-after-5s`);
  await sleep(45000); await shot(page, `${run}-3-after-50s`);
  await record("3-goodbye", t3, { waited: w });
}

// ---- 4. coming back in the same tab
if (want(4)) {
  const t4 = await typeAndAsk("Wait, I'm back! Do you remember my name?");
  const w = await waitTurnDone(page, { sinceT: t4 });
  await shot(page, `${run}-4-back`);
  await record("4-back-same-tab", t4, { waited: w });
}

// ---- reload = coming back later; 5. does she remember?
if (want(5)) {
  await page.reload({ waitUntil: "domcontentloaded" });
  await simReady(page);
  await sleep(1500);
  await shot(page, `${run}-5-reloaded`);
  const t5 = await typeAndAsk("Hi Moxie, it's Sam again! Do you remember me?");
  const w = await waitTurnDone(page, { sinceT: t5 });
  await shot(page, `${run}-5-answered`);
  await record("5-after-reload", t5, { waited: w });
}

await v.dump(`${run}-final`);
results.summary = {
  longest_cue_free_ms: results.steps.map((s) => [s.name, s.cue.longest_ms]),
  sounds_cut: results.steps.reduce((n, s) => n + s.cuts.length, 0),
  fillers_cut_by_voice_within_1000ms: results.steps.reduce((n, s) => n + fillersCutByVoice(s.cuts), 0),
};
console.log("summary:", JSON.stringify(results.summary));
writeFileSync(join(OUT, `${run}-results.json`), JSON.stringify(results, null, 1));
if (isProd) console.log(`production chat turns spent now: ${prodChatSpent()} / 5`);
await browser.close();
