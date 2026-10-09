/* journey/states.mjs — the limit and failure states a visitor can meet, on LOCAL copies wired
 * to the zero-spend mock gateway (journey/mockgw.mjs). One scenario per run:
 *   --scenario=minute  a copy with prod-like limits: 7 quick turns (per-minute cap is 5)
 *   --scenario=hour    a copy with DEMO_CHAT_PER_HOUR=3: 5 turns, then a Listen tap while resting
 *   --scenario=kill    a copy with DEMO_ENABLED=0: load, a typed turn, a Listen tap
 *   --scenario=budget  a copy with a tiny DEMO_UNIT_BUDGET_HOUR: turns until the budget refuses
 *   --scenario=outage  a copy whose mock is driven through 500s, a hang, a login page, a dead
 *                      voice, dead ears and an empty reply (--mock=PORT)
 *   --scenario=voice   her voice fails (500, a hang) while the brain is fine; the ears hang
 *   --scenario=gap     THE CUE TRACKER: with the mock at chat ~2.0 s / speech ~2.5 s, N typed
 *                      turns (--turns=5) and, with --mic, N mic turns (--micturns=3); per turn
 *                      the longest interval with nothing of Moxie working to see or hear,
 *                      from the send (or the recorder's auto-stop) to her first word
 *   --scenario=rapid   a child who does not wait: six lines ~1.5 s apart
 *   --scenario=back    the default copy: a turn, the Hub link, the browser Back button
 *   node sim/tools/journey/states.mjs --base=http://moxie.hosted.test:PORT --scenario=NAME [--mock=PORT] [--mic=WAV]
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { browserFor, visitor, shot, sleep, simReady, pnow, waitTurnDone, turnSummary, turnCues,
         recorderStopT, PHONE, IOS_UA, OUT, PROD } from "./lib.mjs";

const flag = (n, d) => { const h = process.argv.find((a) => a.startsWith("--" + n + "=")); return h ? h.slice(n.length + 3) : d; };
const base = String(flag("base", "")).replace(/\/$/, "");
const scenario = flag("scenario", "");
const mockPort = flag("mock", "");
const mic = flag("mic", "");
const turnsWanted = Math.max(1, Number(flag("turns", 5)) || 5);
const micTurnsWanted = Math.max(0, Number(flag("micturns", 3)) || 0);
const u = new URL(base);
if (PROD && u.origin === PROD) { console.error("states.mjs drives limits and outages: never production"); process.exit(2); }
const hosts = {}, secure = [];
if (u.hostname.endsWith(".test")) { hosts[u.hostname] = Number(u.port); secure.push(u.origin); }
const PH = { ...PHONE, deviceScaleFactor: 2 };
const run = `states-${scenario}`;

const browser = await browserFor({ hosts, mic: mic || null, secureOrigins: secure });
const v = await visitor(browser, { run, viewport: PH, ua: IOS_UA, grantMic: u.origin,
                                    caps: { chat: 14, speech: 40, transcribe: 6 } });
const { page } = v;
const R = { base, scenario, at: new Date().toISOString(), steps: [] };
const save = () => writeFileSync(join(OUT, `${run}-results.json`), JSON.stringify(R, null, 1));

async function ctl(q) {
  if (!mockPort) return null;
  const r = await fetch(`http://127.0.0.1:${mockPort}/__ctl?${q}`);
  return r.json();
}
async function tap(sel) {
  const b = await page.$eval(sel, (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.touchscreen.tap(b.x, b.y);
}
async function ask(text) {
  await tap("#speech-input");
  await page.type("#speech-input", text, { delay: 15 });
  const t = await pnow(page);
  await tap("#speech-btn");
  return t;
}
/** What a visitor can SEE right now: badge, pill, banner, statuses, the composer, the last rows. */
async function surface() {
  return page.evaluate(() => {
    const vis = (el) => { if (!el) return false; const s = getComputedStyle(el); const r = el.getBoundingClientRect(); return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity) > 0 && r.width > 0 && r.height > 0; };
    const q = (s) => document.querySelector(s);
    const ban = q("#env-banner");
    const rows = [...document.querySelectorAll("#transcript > div")].slice(-4).map((d) => d.className + ": " + d.textContent.replace(/\s+/g, " ").slice(0, 160));
    const snap = window.moxieMode ? window.moxieMode.snapshot() : null;
    return {
      mode: snap && { state: snap.state, reason: snap.reason, badge: snap.badge, liveTurns: snap.liveTurns, retryAfterS: snap.retryAfterS, message: snap.message },
      badge_text: q(".env-badge") ? q(".env-badge").textContent : null,
      pill: q(".mode-pill") && vis(q(".mode-pill")) ? q(".mode-pill").textContent : null,
      banner: ban && vis(ban) ? ban.textContent.replace(/\s+/g, " ").slice(0, 300) : null,
      chat_status: q("#chat-status") ? q("#chat-status").textContent : null,
      mic_status: q("#mic-status") ? q("#mic-status").textContent : null,
      ask_disabled: q("#speech-btn") ? q("#speech-btn").disabled : null,
      mic_disabled: q("#mic-btn") ? q("#mic-btn").disabled : null,
      rows,
      transport: window.moxieBridge && window.moxieBridge.transportStats ? (({ live, delegated, fallbacks, chatOk, chatRefused, chatErrors, speechOk, speechRefused, speechErrors, voiceFallbacks, reasons, speechReasons }) => ({ live, delegated, fallbacks, chatOk, chatRefused, chatErrors, speechOk, speechRefused, speechErrors, voiceFallbacks, reasons, speechReasons }))(window.moxieBridge.transportStats()) : null,
    };
  });
}
async function step(name, t0, extra = {}, waitOpts = {}) {
  const w = await waitTurnDone(page, { sinceT: t0, timeout: waitOpts.timeout || 40000, quietMs: waitOpts.quietMs || 2000 });
  const d = await v.dump(`${run}-${name}`);
  const s = turnSummary(d.state, t0);
  const sf = await surface();
  await shot(page, `${run}-${name}`);
  const { timeline, markup, ...rest } = s;
  const st = { name, ...extra, waited: w, ...rest, surface: sf,
               statuses: timeline.filter((e) => ["chat-status", "mic-status", "pill", "badge", "banner", "mode"].includes(e.k)).map((e) => ({ t: e.t, k: e.k, v: e.v })),
               spoken: timeline.filter((e) => e.k === "speechSynthesis").map((e) => e.v),
               rowsAdded: timeline.filter((e) => e.k === "row+").map((e) => ({ t: e.t, ...e.v })) };
  // The cue report: from the send (a typed turn) or the recorder's auto-stop (a mic turn) to
  // her first word. A mic turn also says how soon after the stop a cue was visible.
  if (extra.kind === "mic") {
    const stopT = recorderStopT(d.state, t0);
    st.recorder_stop_at = stopT != null ? Math.round(stopT - t0) : null;
    if (stopT != null) st.cue = turnCues(d.state, stopT, t0 + (w.waited || 0));
  } else {
    st.cue = turnCues(d.state, t0, t0 + (w.waited || 0));
  }
  R.steps.push(st);
  save();
  console.log(JSON.stringify({ name, reply: rest.reply, reason: rest.reason, api: rest.api, click_to_reply_row: rest.click_to_reply_row, click_to_first_sound: rest.click_to_first_sound,
                               clips: rest.clips.length, synth: st.spoken.length, badge: sf.badge_text, pill: sf.pill, chat_status: sf.chat_status, mic_status: sf.mic_status, banner: sf.banner && sf.banner.slice(0, 120), mode: sf.mode && sf.mode.state + ":" + sf.mode.reason, rows: sf.rows.slice(-2),
                               cue: st.cue && { longest_ms: st.cue.longest_ms, total_ms: st.cue.total_ms, until: st.cue.until - st.cue.from, until_is: st.cue.until_is, first_cue_ms: st.cue.first_cue_ms, runs: st.cue.runs },
                               recorder_stop_at: st.recorder_stop_at }));
  return st;
}
async function micTurn(name) {
  const t = await pnow(page);
  await tap("#mic-btn");
  await sleep(800);
  await page.waitForFunction(() => window.moxieMic && !window.moxieMic.isRecording(), { timeout: 25000, polling: 100 }).catch(() => {});
  return step(name, t, { kind: "mic" }, { timeout: 45000 });
}

await page.goto(base + "/sim", { waitUntil: "domcontentloaded" });
R.ready_ms = await simReady(page).catch((e) => "not ready: " + e.message);
await sleep(1500);
R.boot_surface = await surface();
await shot(page, `${run}-0-boot`);
console.log("boot:", JSON.stringify(R.boot_surface));

if (scenario === "minute") {
  // the three openers, then typed lines, as fast as a keen child goes (each waits for her answer)
  const lines = ["hi", "lol", "tell me a joke", "another one", "why", "ok", "what is your name"];
  for (let i = 0; i < lines.length; i++) {
    const t = await ask(lines[i]);
    await step(`${i + 1}-${lines[i].replace(/\W+/g, "_")}`, t, { line: lines[i] }, { quietMs: 600 });
  }
} else if (scenario === "hour") {
  for (let i = 1; i <= 5; i++) {
    const t = await ask(i === 4 ? "what's your favorite color?" : i === 5 ? "are you still there?" : "hello number " + i);
    await step(`${i}-typed`, t, {}, { quietMs: 1200 });
  }
  if (mic) await micTurn("6-mic-while-resting");
  const t7 = await ask("bye Moxie");
  await step("7-bye-while-resting", t7, {}, { quietMs: 1200 });
} else if (scenario === "kill") {
  const t1 = await ask("Hi Moxie! Are you there?");
  await step("1-typed", t1, {}, { quietMs: 1500 });
  if (mic) await micTurn("2-mic");
} else if (scenario === "budget") {
  for (let i = 1; i <= 4; i++) {
    const t = await ask(i === 1 ? "Hi Moxie, tell me about your day." : "and then what?");
    await step(`${i}-typed`, t, {}, { quietMs: 1500 });
  }
} else if (scenario === "outage") {
  await ctl("chat=ok&speech=ok&stt=ok&chatDelay=300&speechDelay=250&sttDelay=300");
  // a. the brain answers 500: three turns, then the brain comes back
  await ctl("chat=500");
  for (let i = 1; i <= 3; i++) { const t = await ask(i === 1 ? "Hi Moxie!" : "are you ok?"); await step(`a${i}-chat500`, t, {}, { quietMs: 1500 }); }
  await ctl("chat=ok");
  const back0 = Date.now();
  const tA4 = await ask("Moxie, are you back?");
  await step("a4-after-recovery-immediate", tA4, { since_recovery_ms: Date.now() - back0 }, { quietMs: 1500 });
  // wait for the poll that lets the next turn try (mode.js polls every 30 s at best)
  await sleep(32000);
  const tA5 = await ask("how about now?");
  await step("a5-after-recovery-32s", tA5, { since_recovery_ms: Date.now() - back0 }, { quietMs: 1500 });
  await sleep(31000);
  const tA6 = await ask("and now?");
  await step("a6-after-recovery-63s", tA6, { since_recovery_ms: Date.now() - back0 }, { quietMs: 1500 });
  // b. the gateway hangs (the server's own chat deadline decides the wait)
  await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
  await ctl("chat=hang");
  const tB = await ask("Moxie, what's your favorite food?");
  await step("b1-chat-hang", tB, {}, { timeout: 60000, quietMs: 1500 });
  await ctl("chat=ok");
  // c. a login page instead of the gateway (an access gate), fresh tab
  await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
  await ctl("chat=html");
  const tC = await ask("hello?");
  await step("c1-chat-html", tC, {}, { quietMs: 1500 });
  await ctl("chat=ok");
  // d. the voice is down, the brain is fine
  await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
  await ctl("speech=500");
  const tD = await ask("Tell me something funny.");
  await step("d1-speech500", tD, {}, { quietMs: 2500 });
  await ctl("speech=ok");
  // e. the ears are down: three Listen taps
  if (mic) {
    await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
    await ctl("stt=500");
    for (let i = 1; i <= 3; i++) { await micTurn(`e${i}-stt500`); await sleep(1500); }
    const tE4 = await ask("can you read this instead?");
    await step("e4-typed-after-ears-down", tE4, {}, { quietMs: 1500 });
    await ctl("stt=ok");
  }
  // f. an empty reply
  await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
  await ctl("chat=empty");
  const tF = await ask("Moxie?");
  await step("f1-chat-empty", tF, {}, { quietMs: 1500 });
  await ctl("chat=ok&speech=ok&stt=ok");
} else if (scenario === "voice") {
  // her voice fails while the brain is fine — with replies never synthesised on this copy
  // (the TTS cache would otherwise answer from a hit and hide the outage)
  await ctl("chat=ok&speech=500&stt=ok");
  const t1 = await ask("What makes you happy?");                 // mock: the "happy" reply
  await step("v1-speech500", t1, {}, { quietMs: 3000, timeout: 40000 });
  await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
  await ctl("speech=hang");
  const t2 = await ask("Tell me a joke please");                  // mock: the "joke" reply
  await step("v2-speech-hang", t2, {}, { quietMs: 3000, timeout: 60000 });
  await ctl("speech=ok");
  // the ears hang: the server's own STT deadline decides the wait
  if (mic) {
    await page.reload({ waitUntil: "domcontentloaded" }); await simReady(page); await sleep(1500);
    await ctl("stt=hang");
    await micTurn("v3-stt-hang");
    await ctl("stt=ok");
  }
} else if (scenario === "gap") {
  // The dead air between the brain answering and her voice: what does she look like? The
  // mock's delays measure as chat ~2.0 s and speech ~2.5 s through the Function (2026-10-08).
  await ctl("chat=ok&speech=ok&stt=ok&chatDelay=1800&speechDelay=2300");
  const lines = ["do you remember my name?", "tell me a joke", "what makes you happy?",
                 "what is your favorite animal?", "are you a real robot?"];
  for (let i = 0; i < turnsWanted; i++) {
    const line = lines[i % lines.length];
    const t = await ask(line);
    if (i === 0) {
      // Turn 1 also keeps the two screenshots the defect was first seen in: 1.2 s into the
      // thinking wait, and 0.9 s after the status line cleared.
      await page.waitForFunction(() => { const s = document.getElementById("chat-status"); return s && s.textContent === "thinking…"; }, { timeout: 10000, polling: 50 }).catch(() => {});
      await sleep(1200); await shot(page, `${run}-1-thinking-1200ms`);
      const face1 = await page.evaluate(() => ({ status: (document.getElementById("chat-status") || {}).textContent, mouth: window.moxie.getMouthOpen(), motors: [0,1,2,3,4,5,6].map((i) => window.moxie.getMotor(i)) }));
      await page.waitForFunction(() => { const s = document.getElementById("chat-status"); return s && s.textContent === ""; }, { timeout: 10000, polling: 50 }).catch(() => {});
      await sleep(900); await shot(page, `${run}-1-gap-900ms-after-status-cleared`);
      const face2 = await page.evaluate(() => ({ status: (document.getElementById("chat-status") || {}).textContent, rows: document.querySelectorAll("#transcript .turn.moxie").length, motors: [0,1,2,3,4,5,6].map((i) => window.moxie.getMotor(i)) }));
      R.gap = { face1, face2 };
    }
    await step(`${i + 1}-typed`, t, { line }, { quietMs: 1500 });
  }
  if (mic) for (let k = 1; k <= micTurnsWanted; k++) { await sleep(1000); await micTurn(`m${k}-mic`); }
  const typed = R.steps.filter((s) => s.kind !== "mic" && s.cue), mics = R.steps.filter((s) => s.kind === "mic" && s.cue);
  R.cue_summary = {
    typed_turns: typed.length,
    typed_longest_cue_free_ms: typed.map((s) => s.cue.longest_ms),
    typed_max_longest_ms: Math.max(-1, ...typed.map((s) => s.cue.longest_ms)),
    typed_send_to_first_voice_ms: typed.map((s) => s.send_to_first_voice),
    mic_turns: mics.length,
    mic_longest_cue_free_ms: mics.map((s) => s.cue.longest_ms),
    mic_first_cue_after_stop_ms: mics.map((s) => s.cue.first_cue_ms),
    mic_stop_to_first_voice_ms: mics.map((s) => s.cue.until - s.cue.from),
  };
  console.log("cue summary:", JSON.stringify(R.cue_summary));
  await ctl("chatDelay=300&speechDelay=250");
} else if (scenario === "rapid") {
  // a child who does not wait: the three openers, then three typed lines, ~1.5 s apart
  const t0 = await pnow(page);
  const fired = [];
  for (const sel of ["#chat-openers .opener:nth-child(3)", "#chat-openers .opener:nth-child(1)", "#chat-openers .opener:nth-child(2)"]) {
    const ok = await page.$(sel);
    if (ok && await page.$eval(sel, (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })) { await tap(sel); fired.push(sel); }
    else { await ask("surprise me again"); fired.push("typed (opener hidden)"); }
    await sleep(1500);
  }
  for (const line of ["again!", "more!", "one more"]) { await ask(line); fired.push(line); await sleep(1500); }
  await sleep(20000);
  const d = await v.dump(`${run}-all`);
  const s = turnSummary(d.state, t0);
  R.rapid = { fired, api: s.api, rows: s.timeline.filter((e) => e.k === "row+").map((e) => ({ t: e.t, ...e.v })),
              statuses: s.timeline.filter((e) => ["chat-status", "pill", "badge", "banner", "mode"].includes(e.k)).map((e) => ({ t: e.t, k: e.k, v: e.v })),
              plays: s.voice_chunks, clips: s.clips, surface: await surface() };
  await shot(page, `${run}-all`);
  save();
  console.log(JSON.stringify({ fired, api: s.api, rows: R.rapid.rows.map((r) => r.t + " " + r.cls + ": " + (r.text || "").slice(0, 90)) }, null, 1));
} else if (scenario === "back") {
  const t1 = await ask("Hi Moxie, I'm Sam.");
  await step("1-typed", t1, {}, { quietMs: 1500 });
  const before = (await surface()).rows;
  await Promise.all([page.waitForNavigation({ waitUntil: "load" }), tap("#hub-back")]);
  await sleep(1500);
  await shot(page, `${run}-2-hub`);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }).catch(() => {}), page.goBack()]);
  await sleep(2500);
  const nav = await page.evaluate(() => { const n = performance.getEntriesByType("navigation")[0]; return n ? n.type : null; });
  R.back = { rows_before: before, rows_after: (await surface()).rows, nav_type: nav,
             transport_live: await page.evaluate(() => window.moxieBridge && window.moxieBridge.transportStats ? window.moxieBridge.transportStats().live : null) };
  await shot(page, `${run}-3-back`);
  const t2 = await ask("Do you remember my name?");
  await step("4-after-back", t2, {}, { quietMs: 1500 });
} else {
  console.error("unknown --scenario"); process.exit(2);
}

const fin = await v.dump(`${run}-final`);
R.console = fin.console.filter((c) => c.type === "error" || c.type === "warning" || c.type === "pageerror");
R.http_errors = fin.net.filter((n) => (n.status && n.status >= 400) || n.err).map((n) => ({ url: n.url, status: n.status, err: n.err }));
save();
console.log("console errors:", JSON.stringify(R.console.slice(0, 12)));
console.log("http errors:", JSON.stringify(R.http_errors.slice(0, 20)));
await browser.close();
