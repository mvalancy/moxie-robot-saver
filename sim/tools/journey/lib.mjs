/* journey/lib.mjs — the visitor-journey instrument: one visitor, measured end to end.
 *
 * One visitor = one incognito browser context with:
 *   - a CDP network log (status, mime, bytes, failures), console + pageerror,
 *   - a page-side TIMELINE (bubble, transcript rows, statuses, mode, audio plays, API calls,
 *     speechSynthesis calls, clip fetches) and perf observers (FCP/LCP/CLS/longtasks),
 *   - the CUE TRACKER (`installCueTracker`, 20 Hz): what a child can SEE or HEAR of Moxie
 *     working — the status line, her thinking face and pose (`moxieAlive.__state()`), the
 *     ears (`body[data-mic]`), Web Audio playing, the browser voice speaking — so `cueGaps`
 *     can name the longest stretch of a turn in which she looked idle,
 *   - a SPEND GUARD: every /api/chat|speech|transcribe fetch first awaits a Node-side
 *     ledger write; chat turns against the site's own origin are capped at PROD_CHAT_CAP
 *     across every run that shares the ledger.
 *
 * Paths are environment variables with public defaults: JOURNEY_LEDGER (the spend ledger,
 * `<tmpdir>/moxie-journey/ledger.jsonl`) and JOURNEY_OUT (dumps and screenshots,
 * `<tmpdir>/moxie-journey/out`). The site's origin is read from sim/web/index.html's
 * canonical link (`browser_harness.mjs::canonicalOrigin`), never typed here.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { requireBrowser, launchBrowser, instrumentWebAudio, recordCspViolations, canonicalOrigin,
         PHONE, IOS_UA, web } from "../../browser_harness.mjs";

export const PROD = canonicalOrigin();
export const PROD_CHAT_CAP = 5;
const HOME = join(tmpdir(), "moxie-journey");
export const LEDGER = process.env.JOURNEY_LEDGER || join(HOME, "ledger.jsonl");
mkdirSync(join(LEDGER, ".."), { recursive: true });
export const OUT = process.env.JOURNEY_OUT || join(HOME, "out");
mkdirSync(OUT, { recursive: true });
export { PHONE, IOS_UA };
export const DESKTOP = { width: 1440, height: 900 };
export const DESKTOP_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36";
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** The cue tracker's sampling period: the resolution of every cue-gap number. */
export const CUE_SAMPLE_MS = 50;

export function ledgerLines() {
  if (!existsSync(LEDGER)) return [];
  return readFileSync(LEDGER, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}
export function prodChatSpent() {
  return ledgerLines().filter((e) => e.origin === PROD && e.route === "chat" && !e.denied).length;
}
export function ledger(entry) {
  appendFileSync(LEDGER, JSON.stringify({ at: new Date().toISOString(), ...entry }) + "\n");
}

/* ---------------- page-side instruments (run before any page script) --------------- */
function installTimeline() {
  window.__tl = [];
  const now = () => Math.round(performance.now());
  const log = (k, v) => { window.__tl.push({ t: now(), k, v }); };
  window.__tlog = log;
  window.__perf = { lcp: null, fcp: null, cls: 0, longtasks: [] };
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.lcp = { t: Math.round(e.startTime), size: e.size, el: e.element ? (e.element.id || e.element.tagName) : null, url: e.url || "" }; }).observe({ type: "largest-contentful-paint", buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.name === "first-contentful-paint") window.__perf.fcp = Math.round(e.startTime); }).observe({ type: "paint", buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value; }).observe({ type: "layout-shift", buffered: true }); } catch (e) {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__perf.longtasks.push([Math.round(e.startTime), Math.round(e.duration)]); }).observe({ type: "longtask", buffered: true }); } catch (e) {}

  // speechSynthesis: the browser's own voice (NOT hers)
  try {
    const ss = window.speechSynthesis;
    if (ss && ss.speak) {
      const sp = ss.speak.bind(ss);
      ss.speak = function (u) { log("speechSynthesis", { text: u && u.text, voice: u && u.voice ? u.voice.name : null }); return sp(u); };
    }
  } catch (e) {}

  // fetch: /api/* (with the spend guard) and audio clip fetches
  const of = window.fetch;
  window.__api = [];
  window.fetch = async function (input, init) {
    const url = String((input && input.url) || input || "");
    const m = /\/api\/(chat|speech|transcribe|health)\b/.exec(url);
    if (!m) {
      if (/\/audio\/[^?]+\.(mp3|wav)/.test(url)) log("clip-fetch", url.replace(/^.*\/audio\//, ""));
      return of.call(this, input, init);
    }
    const method = (init && init.method) || "GET";
    const rec = { route: m[1], method, t: now(), doneAt: null, status: null, req: null, res: null };
    if (m[1] === "chat" && init && typeof init.body === "string") rec.req = init.body.slice(0, 600);
    if (m[1] === "transcribe" && init && init.body && init.body.size != null) rec.req = "[audio " + init.body.size + " bytes " + (init.body.type || "") + "]";
    window.__api.push(rec);
    if (m[1] !== "health") {
      log("api>", m[1]);
      if (typeof window.__guard === "function") {
        const ok = await window.__guard(m[1], url);
        if (!ok) { rec.status = "DENIED_BY_BUDGET"; throw new TypeError("journey budget guard"); }
      }
    }
    try {
      const r = await of.call(this, input, init);
      rec.doneAt = now(); rec.status = r.status;
      if (m[1] !== "health") log("api<", m[1] + " " + r.status + " " + (rec.doneAt - rec.t) + "ms");
      try {
        const c = r.clone();
        c.text().then((tx) => {
          if (m[1] === "speech") {
            let n = 0; try { const j = JSON.parse(tx); n = (j.messages || []).length; rec.reason = j.reason || null; } catch (e) {}
            rec.res = "[speech " + tx.length + " chars, " + n + " msgs]";
          } else rec.res = tx.slice(0, 6000);
        }, () => {});
      } catch (e) {}
      return r;
    } catch (e) {
      rec.doneAt = now(); rec.status = "error:" + (e && e.name);
      if (m[1] !== "health") log("api<", m[1] + " ERR " + (e && e.name));
      throw e;
    }
  };

  const watch = () => {
    const watchText = (id, k, typewriter) => {
      const el = document.getElementById(id); if (!el) return false;
      let last = el.textContent; let entry = null;
      if (last) { entry = { t: now(), k, v: last }; window.__tl.push(entry); }
      new MutationObserver(() => {
        const v = el.textContent; if (v === last) return;
        // a typewriter grows the same line: keep ONE entry holding the whole line
        if (typewriter && entry && last && v.startsWith(last)) { entry.v = v; last = v; return; }
        last = v; entry = { t: now(), k, v }; window.__tl.push(entry);
      }).observe(el, { childList: true, characterData: true, subtree: true });
      return true;
    };
    watchText("bubble-text", "bubble", true);
    watchText("mic-status", "mic-status");
    watchText("link-label", "link");
    // #chat-status is injected later by cloud-transport.js; the pill + banner by env.js
    const late = setInterval(() => {
      const done = ["chat-status"].every((id) => !document.getElementById(id) || document.getElementById(id).__w);
      for (const id of ["chat-status"]) {
        const el = document.getElementById(id);
        if (el && !el.__w) { el.__w = 1; watchText(id, id); }
      }
      const pill = document.querySelector(".mode-pill");
      if (pill && !pill.__w) { pill.__w = 1; let l = ""; new MutationObserver(() => { const v = pill.hidden ? "" : pill.textContent; if (v !== l) { l = v; log("pill", v); } }).observe(pill, { childList: true, characterData: true, subtree: true, attributes: true }); }
      const badge = document.querySelector(".env-badge");
      if (badge && !badge.__w) { badge.__w = 1; let l = badge.textContent; log("badge", l); new MutationObserver(() => { const v = badge.textContent; if (v !== l) { l = v; log("badge", v); } }).observe(badge, { childList: true, characterData: true, subtree: true }); }
      const ban = document.querySelector("#env-banner .eb-text");
      if (ban && !ban.__w) { ban.__w = 1; let l = ban.textContent; log("banner", l); new MutationObserver(() => { const v = ban.textContent; if (v !== l) { l = v; log("banner", v); } }).observe(ban, { childList: true, characterData: true, subtree: true }); }
      if (done && pill && badge) clearInterval(late);
    }, 100);
    setTimeout(() => clearInterval(late), 20000);
    const tr = document.getElementById("transcript");
    if (tr) new MutationObserver((recs) => {
      for (const r of recs) {
        for (const n of r.addedNodes) if (n.nodeType === 1) log("row+", { cls: n.className, text: (n.textContent || "").slice(0, 400) });
        if (r.type === "characterData" || (r.target && r.target.classList && r.target.classList.contains("msg")))
          log("row~", (r.target.textContent || "").slice(0, 400));
      }
    }).observe(tr, { childList: true, subtree: true, characterData: true });
    new MutationObserver((recs) => {
      for (const r of recs) log("body@" + r.attributeName, document.body.getAttribute(r.attributeName));
    }).observe(document.body, { attributes: true, attributeFilter: ["data-mode", "data-mic", "data-env", "class"] });
    const hud = document.getElementById("hud");
    if (hud) new MutationObserver(() => log("hud.class", hud.className)).observe(hud, { attributes: true, attributeFilter: ["class"] });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch, { once: true });
  else watch();
  // the moment the page itself saw the input (capture phase, before any handler)
  const desc = (el) => el ? (el.id ? "#" + el.id : el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : el.tagName) : "?";
  window.addEventListener("click", (e) => { const b = e.target && e.target.closest ? e.target.closest("button,a,input") : null; log("click", desc(b || e.target)); }, true);
  window.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === "Tab" || e.key === " ") log("key", e.key + "@" + desc(document.activeElement)); }, true);
  window.addEventListener("moxie-ready", () => log("moxie-ready", null));
  window.addEventListener("moxie-audio-unlocked", () => log("audio-unlocked", null));
  window.addEventListener("moxie-tts-start", (e) => log("tts-start", e.detail ? { dur: +(e.detail.duration || 0).toFixed(2), ev: e.detail.eventId, chunk: e.detail.chunkNum } : null));
  window.addEventListener("moxie-tts-end", () => log("tts-end", null));
  // mode transitions, once mode.js exists
  const mm = setInterval(() => {
    if (window.moxieMode && window.moxieMode.onChange) {
      clearInterval(mm);
      window.moxieMode.onChange((s) => log("mode", { state: s.state, reason: s.reason, badge: s.badge, live: s.liveTurns, msg: s.message }));
    }
  }, 20);
  setTimeout(() => clearInterval(mm), 20000);
  // composer adoption
  const ad = setInterval(() => {
    try { if (window.moxieTypedTurn && window.moxieTypedTurn.adopted()) { clearInterval(ad); log("composer-adopted", null); } } catch (e) {}
  }, 20);
  setTimeout(() => clearInterval(ad), 30000);
}

/**
 * PAGE-SIDE (pass to `evaluateOnNewDocument`, after `instrumentWebAudio`): THE CUE TRACKER.
 * Every CUE_SAMPLE_MS it records what a child could see or hear of Moxie working:
 *   status  `#chat-status`'s text ("thinking…", the voice-wait line, "")
 *   stage   `moxieAlive.__state().stage` — the thinking beat reached (0 = no thinking face)
 *   pose    `moxieAlive.__state().pose` — what the body holds, where alive.js reports it
 *   mic     `body[data-mic]` — the ears are working (recording, or the clip uploading)
 *   audio   a Web Audio buffer is playing now (from `instrumentWebAudio`'s plays and stops)
 *   synth   the browser voice is speaking
 *   rec     `moxieMic.isRecording()` — so a mic turn's auto-stop can be found at 50 ms
 * `cueGaps` reads the samples back. `__cue` is capped at CUE_MAX samples (the oldest drop).
 */
function installCueTracker() {
  window.__cue = [];
  const CUE_MAX = 40000;
  const sample = () => {
    const t = performance.now();
    const st = document.getElementById("chat-status");
    let alive = null;
    try { alive = window.moxieAlive && window.moxieAlive.__state ? window.moxieAlive.__state() : null; } catch (e) {}
    const A = window.__audio || { plays: [], stops: [] };
    let audio = false;
    for (let i = A.plays.length - 1; i >= 0; i--) {
      const p = A.plays[i];
      if (p.t > t) continue;
      if (p.t + p.dur <= t) { if (A.plays.length - i > 8) break; continue; }
      if (!A.stops.some((s) => s.id === p.id && s.t <= t)) { audio = true; break; }
    }
    let synth = false;
    try { synth = !!(window.speechSynthesis && window.speechSynthesis.speaking); } catch (e) {}
    let rec = null;
    try { rec = window.moxieMic ? !!window.moxieMic.isRecording() : null; } catch (e) {}
    window.__cue.push({
      t: Math.round(t),
      status: st ? st.textContent : "",
      stage: alive ? Number(alive.stage) || 0 : 0,
      pose: alive && alive.pose ? String(alive.pose) : null,
      mic: document.body ? document.body.getAttribute("data-mic") : null,
      audio, synth, rec,
    });
    if (window.__cue.length > CUE_MAX) window.__cue.splice(0, window.__cue.length - CUE_MAX);
  };
  setInterval(sample, 50);
}

/** Is Moxie visibly or audibly working in this sample? */
export const cued = (s) => !!(s.status || s.stage > 0 || s.pose || s.mic === "on" || s.audio || s.synth);

/**
 * The cue-free stretches of `[fromT, untilT]` from the tracker's samples: `longest_ms` is the
 * widest interval between two cued samples (or a bound and the nearest cued sample) in which
 * no sample showed a cue, at CUE_SAMPLE_MS resolution; `runs` lists every such stretch.
 * `untilT` is normally her first word (the first pcm play): from there she is audibly working.
 */
export function cueGaps(samples, fromT, untilT) {
  const list = (samples || []).filter((s) => s.t >= fromT && s.t <= untilT).sort((a, b) => a.t - b.t);
  const runs = [];
  let lastCued = fromT, open = null;
  for (const s of list) {
    if (cued(s)) {
      if (open) { runs.push({ from: Math.round(open - fromT), to: Math.round(s.t - fromT), ms: Math.round(s.t - lastCued) }); open = null; }
      lastCued = s.t;
    } else if (!open) open = s.t;
  }
  if (open) runs.push({ from: Math.round(open - fromT), to: Math.round(untilT - fromT), ms: Math.round(untilT - lastCued) });
  const longest = runs.reduce((m, r) => Math.max(m, r.ms), 0);
  return { samples: list.length, from: Math.round(fromT), until: Math.round(untilT),
           longest_ms: longest, total_ms: runs.reduce((a, r) => a + r.ms, 0), runs,
           first_cue_ms: list.length ? (list.find(cued) ? Math.round(list.find(cued).t - fromT) : null) : null };
}

/* ---------------- browser + visitor ---------------- */
export async function browserFor({ hosts = {}, mic = null, secureOrigins = [], args = [], autoplay = false } = {}) {
  const { puppeteer, chrome } = await requireBrowser("journey");
  const a = [...args];
  if (mic) a.push("--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${mic}`);
  for (const o of secureOrigins) a.push(`--unsafely-treat-insecure-origin-as-secure=${o}`);
  return launchBrowser(puppeteer, chrome, { hosts, autoplay, args: a });
}

/**
 * A fresh visitor (incognito context). Returns {page, cdp, net, cons, ctx, dump()}.
 * `run` names the ledger rows; `throttle` = {cpu, latency, down, up} for a phone profile;
 * `caps` = {chat, speech, transcribe} this run may spend at most.
 */
export async function visitor(browser, { run, viewport = PHONE, ua = IOS_UA, throttle = null,
                                          reducedMotion = false, ctx = null, grantMic = null,
                                          caps = null } = {}) {
  const context = ctx || await browser.createBrowserContext();
  if (grantMic) await context.overridePermissions(grantMic, ["microphone"]);
  const page = await context.newPage();
  await page.setViewport(viewport);
  if (ua) await page.setUserAgent(ua);
  if (reducedMotion) await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  const cdp = await page.createCDPSession();
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable");
  if (throttle) {
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: throttle.cpu || 4 });
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false, latency: throttle.latency || 150,
      downloadThroughput: (throttle.down || 1.6e6) / 8, uploadThroughput: (throttle.up || 7.5e5) / 8 });
  }
  const net = new Map(); const netList = [];
  const t0wall = Date.now();
  cdp.on("Network.requestWillBeSent", (e) => {
    const r = { id: e.requestId, url: e.request.url, method: e.request.method, type: e.type, t: e.timestamp, status: null, mime: null, bytes: 0, err: null, fromCache: false, initiator: e.initiator && e.initiator.type };
    net.set(e.requestId, r); netList.push(r);
  });
  cdp.on("Network.responseReceived", (e) => { const r = net.get(e.requestId); if (r) { r.status = e.response.status; r.mime = e.response.mimeType; r.fromCache = !!e.response.fromDiskCache; r.protocol = e.response.protocol; } });
  cdp.on("Network.loadingFinished", (e) => { const r = net.get(e.requestId); if (r) { r.bytes = e.encodedDataLength; r.tEnd = e.timestamp; } });
  cdp.on("Network.loadingFailed", (e) => { const r = net.get(e.requestId); if (r) { r.err = e.errorText + (e.blockedReason ? " blocked:" + e.blockedReason : "") + (e.canceled ? " canceled" : ""); r.tEnd = e.timestamp; } });
  const cons = [];
  page.on("console", (m) => cons.push({ t: Date.now() - t0wall, type: m.type(), text: m.text().slice(0, 500), loc: m.location() && m.location().url ? m.location().url + ":" + (m.location().lineNumber || 0) : "" }));
  page.on("pageerror", (e) => cons.push({ t: Date.now() - t0wall, type: "pageerror", text: String(e && e.message || e).slice(0, 500) }));
  await page.evaluateOnNewDocument(installTimeline);
  await page.evaluateOnNewDocument(instrumentWebAudio);
  await page.evaluateOnNewDocument(installCueTracker);
  await page.evaluateOnNewDocument(recordCspViolations);
  const used = { chat: 0, speech: 0, transcribe: 0 };
  await page.exposeFunction("__guard", (route, url) => {
    let origin = ""; try { origin = new URL(url).origin; } catch (e) {}
    if (PROD && origin === PROD && route === "chat" && prodChatSpent() >= PROD_CHAT_CAP) {
      ledger({ run, origin, route, url, denied: true, why: "prod chat cap" });
      console.log(`  [guard] DENIED production ${route}: cap ${PROD_CHAT_CAP} reached`);
      return false;
    }
    if (caps && caps[route] != null && used[route] >= caps[route]) {
      ledger({ run, origin, route, url, denied: true, why: "run cap " + route });
      console.log(`  [guard] DENIED ${route}: run cap ${caps[route]} reached`);
      return false;
    }
    used[route] = (used[route] || 0) + 1;
    ledger({ run, origin, route, url });
    return true;
  });
  const dump = async (label) => {
    let state = null;
    try {
      state = await page.evaluate(() => ({
        url: location.href, tl: window.__tl || [], api: window.__api || [], perf: window.__perf || null,
        audio: window.__audio || null, csp: window.__csp || [], cue: window.__cue || [],
        mode: window.moxieMode ? window.moxieMode.snapshot() : null,
        modeStats: window.moxieMode && window.moxieMode.stats ? window.moxieMode.stats() : null,
        transport: window.moxieBridge && window.moxieBridge.transportStats ? window.moxieBridge.transportStats() : null,
        mic: window.moxieMic && window.moxieMic.stats ? window.moxieMic.stats() : null,
        alive: window.moxieAlive ? window.moxieAlive.stats : null,
        nav: (() => { const n = performance.getEntriesByType("navigation")[0]; return n ? { dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd), ttfb: Math.round(n.responseStart), transfer: n.transferSize } : null; })(),
        transcript: [...document.querySelectorAll("#transcript > div")].map((d) => ({ cls: d.className, text: d.textContent })),
      }));
    } catch (e) { state = { err: String(e) }; }
    const out = { label, run, at: new Date().toISOString(), state, console: cons.slice(), net: netList.map((r) => ({ ...r })) };
    writeFileSync(join(OUT, `${label}.json`), JSON.stringify(out, null, 1));
    return out;
  };
  return { page, cdp, net: netList, cons, context, dump };
}

export async function shot(page, label) {
  const p = join(OUT, `${label}.png`);
  try { await page.screenshot({ path: p }); } catch (e) { console.log("  shot failed", label, String(e).slice(0, 120)); }
  return p;
}

/** Wait until the sim page has decided its mode and adopted the composer. */
export async function simReady(page, timeout = 60000) {
  const t = Date.now();
  await page.waitForFunction(() => !!window.moxie && !!window.moxieMode && window.moxieMode.state() !== "boot" &&
    window.moxieTypedTurn && window.moxieTypedTurn.adopted(), { timeout, polling: 50 });
  return Date.now() - t;
}

/** Page-relative now (performance.now) */
export const pnow = (page) => page.evaluate(() => Math.round(performance.now()));

/** Wait for her reply to a turn to finish: chat answered, audio drained, a beat of quiet. */
export async function waitTurnDone(page, { sinceT, timeout = 45000, quietMs = 2500 } = {}) {
  const t0 = Date.now();
  let quietSince = null;
  while (Date.now() - t0 < timeout) {
    const s = await page.evaluate((since) => {
      const api = (window.__api || []).filter((a) => a.t >= since && a.route !== "health");
      const pending = api.some((a) => a.doneAt == null);
      const speaking = !!(window.moxieAudio && window.moxieAudio.isMoxieSpeaking && window.moxieAudio.isMoxieSpeaking());
      const queued = !!(window.moxieAudio && window.moxieAudio.ttsPending && window.moxieAudio.ttsPending() > 0);
      const chatDone = api.some((a) => a.route === "chat" && a.doneAt != null);
      return { pending, speaking, queued, chatDone, n: api.length };
    }, sinceT);
    const busy = s.pending || s.speaking || s.queued || !s.chatDone;
    if (!busy) { if (quietSince == null) quietSince = Date.now(); if (Date.now() - quietSince >= quietMs) return { ok: true, waited: Date.now() - t0 }; }
    else quietSince = null;
    await sleep(200);
  }
  return { ok: false, waited: Date.now() - t0 };
}

/** Summarise one turn from the dumped state: the timeline after `sinceT`. */
export function turnSummary(state, sinceT, untilT = Infinity) {
  const tl = (state.tl || []).filter((e) => e.t >= sinceT && e.t <= untilT);
  const api = (state.api || []).filter((a) => a.t >= sinceT && a.t <= untilT && a.route !== "health");
  const plays = ((state.audio && state.audio.plays) || []).filter((p) => p.t >= sinceT && p.t <= untilT);
  const firstPcm = plays.find((p) => p.src === "pcm");
  const chat = api.find((a) => a.route === "chat");
  let reply = null, endTurn = null, markup = null, reason = null, tickets = 0;
  if (chat && chat.res) {
    try {
      const j = JSON.parse(chat.res);
      reason = j.reason || null; tickets = (j.speech || []).length;
      const m = (j.messages || [])[0];
      if (m) { const p = JSON.parse(m.payload); reply = p.output && p.output.text; markup = p.output && p.output.markup; endTurn = p.end_turn; }
    } catch (e) {}
  }
  const click = tl.find((e) => e.k === "click" && /#speech-btn|#mic-btn|\.opener/.test(String(e.v)));
  const c0 = click ? click.t : null;
  const firstAny = plays[0];
  const userRow = tl.find((e) => e.k === "row+" && e.v && /turn (user|pretend)/.test(e.v.cls));
  const moxRow = tl.find((e) => e.k === "row+" && e.v && /turn moxie/.test(e.v.cls));
  return {
    click_at: c0 != null ? c0 - sinceT : null,
    click_to_user_row: c0 != null && userRow ? userRow.t - c0 : null,
    click_to_chat_start: c0 != null && chat ? chat.t - c0 : null,
    click_to_reply_row: c0 != null && moxRow ? moxRow.t - c0 : null,
    click_to_first_voice: c0 != null && firstPcm ? Math.round(firstPcm.t - c0) : null,
    click_to_first_sound: c0 != null && firstAny ? Math.round(firstAny.t - c0) : null,
    api: api.map((a) => ({ route: a.route, t: a.t - sinceT, rtt: a.doneAt != null ? a.doneAt - a.t : null, status: a.status, reason: a.reason || null })),
    chat_rtt: chat && chat.doneAt != null ? chat.doneAt - chat.t : null,
    send_to_first_voice: firstPcm ? Math.round(firstPcm.t - sinceT) : null,
    voice_chunks: plays.filter((p) => p.src === "pcm").map((p) => ({ t: Math.round(p.t - sinceT), dur: Math.round(p.dur) })),
    clips: plays.filter((p) => p.src === "clip").map((p) => ({ t: Math.round(p.t - sinceT), dur: Math.round(p.dur), bytes: p.bytes })),
    speechSynthesis: tl.filter((e) => e.k === "speechSynthesis").map((e) => ({ t: e.t - sinceT, ...e.v })),
    reply, reason, tickets, endTurn, markup,
    timeline: tl.map((e) => ({ t: e.t - sinceT, k: e.k, v: e.v })),
  };
}

/** When the page itself saw the send: the first click on Ask / Listen / an opener after
 *  `sinceT` (the capture-phase `click` the timeline logs), else `sinceT`. A probe's own clock
 *  reading is taken BEFORE its tap's round trips to the browser, which on a loaded host can
 *  take a second; the page was not waiting for anything until it saw the click. */
export function sendTime(state, sinceT) {
  const c = (state.tl || []).find((e) => e.t >= sinceT && e.k === "click" && /#speech-btn|#mic-btn|\.opener/.test(String(e.v)));
  return c ? c.t : sinceT;
}

/**
 * The cue report of one turn from the dumped state: the longest cue-free interval between
 * `sinceT` (the send, or the auto-stop of a mic turn) and her first word (the first pcm play
 * after `sinceT`; with none, the first sound of any kind, else `fallbackUntil`).
 */
export function turnCues(state, sinceT, fallbackUntil) {
  const plays = ((state.audio && state.audio.plays) || []).filter((p) => p.t >= sinceT);
  const firstPcm = plays.find((p) => p.src === "pcm");
  const until = firstPcm ? firstPcm.t : plays[0] ? plays[0].t : fallbackUntil;
  const g = cueGaps(state.cue, sinceT, until);
  return { ...g, until_is: firstPcm ? "first voice" : plays[0] ? "first sound" : "no sound",
           samples_cued: (state.cue || []).filter((s) => s.t >= sinceT && s.t <= until && cued(s)).length };
}

/** The byte lengths of the thinking-filler clips (`bridge/alive.js`'s FILLERS, played from
 *  the clip manifest's "ambient" group), so a Web Audio play can be named a filler. */
export const FILLER_BYTES = (() => {
  try {
    const src = readFileSync(join(web, "bridge", "alive.js"), "utf8");
    const list = /var FILLERS = \[([\s\S]*?)\];/.exec(src);
    const texts = list ? [...list[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`)) : [];
    const amb = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8")).ambient || {};
    return new Set(texts.filter((t) => amb[t]).map((t) => statSync(join(web, "audio", amb[t])).size));
  } catch (e) { return new Set(); }
})();

/**
 * Every Web Audio play that started in `[sinceT, untilT]` and was STOPPED before it ran out
 * (`instrumentWebAudio`'s plays and stops): `{src, filler, ran_ms, by_voice}`, where `filler`
 * says the clip is one of her thinking fillers and `by_voice` that her own gateway voice
 * started within 100 ms of the stop (voice/ gives the speakers to her answer). The W4-S1 bar:
 * no filler cut by her own voice within 1000 ms of its start.
 */
export function audioCuts(state, sinceT, untilT = Infinity) {
  const A = (state && state.audio) || { plays: [], stops: [] };
  const plays = (A.plays || []).filter((p) => p.t >= sinceT && p.t <= untilT);
  const cuts = [];
  for (const p of plays) {
    const s = (A.stops || []).find((x) => x.id === p.id && x.t >= p.t && x.t < p.t + p.dur - 1);
    if (!s) continue;
    const by = (A.plays || []).some((q) => q.src === "pcm" && q.id !== p.id && Math.abs(q.t - s.t) <= 100);
    cuts.push({ t: Math.round(p.t - sinceT), src: p.src, bytes: p.bytes, filler: p.src === "clip" && FILLER_BYTES.has(p.bytes),
                ran_ms: Math.round(s.t - p.t), of_ms: Math.round(p.dur), by_voice: by });
  }
  return cuts;
}

/** Fillers cut by her own voice within `ms` of their start: the one-voice defect W4-S1 must not bring back. */
export const fillersCutByVoice = (cuts, ms = 1000) => cuts.filter((c) => c.filler && c.by_voice && c.ran_ms < ms).length;

/** When a mic turn's recorder stopped, from the tracker's `rec` samples (the first sample
 *  after `sinceT` that reads false once one has read true), or null. */
export function recorderStopT(state, sinceT) {
  let seen = false;
  for (const s of (state.cue || [])) {
    if (s.t < sinceT) continue;
    if (s.rec === true) seen = true;
    else if (seen && s.rec === false) return s.t;
  }
  return null;
}
