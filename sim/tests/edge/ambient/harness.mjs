/* Harness for `sim/test_ambient.mjs`: the shipped `ambient.js`/`ambient.json`, and a stub page
 * that runs the REAL ambient.js (and, on request, the real bridge/) on a VIRTUAL CLOCK, its
 * timers and `Date` both, so an hour of idle self-talk runs in a blink and the date is
 * whatever a section says it is.
 *
 * No wall clock is read here: every instant is built from literals (`at(2026, 10, 31, 22)`),
 * in the zone `withZone` pins, which is the zone ambient.js reads its month in.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repo, ledger } from "../common.mjs";
import { BRIDGE_SRC } from "../../../bridge_harness.mjs";

export { BRIDGE_SRC };
export const { fails, C, ok, eq } = ledger();
/** One-line findings printed under the summary. */
export const notes = [];

export const web = join(repo, "sim", "web");
export const ambientSrc = readFileSync(join(web, "ambient.js"), "utf8");
export const AMB = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8"));
export const LINES = AMB.lines || [];
/** The texts of the `"beat": kind` lines, and of the October set (bag lines for month 10). */
export const BEAT = (kind) => LINES.filter((l) => l.beat === kind).map((l) => l.text);
export const OCTOBER = LINES.filter((l) => !l.beat && Array.isArray(l.months) && l.months.includes(10))
  .map((l) => l.text);

export const SEC = 1000, MIN = 60 * SEC, HOUR = 60 * MIN;
/** ambient.js's GLITCH_LED: a flicker frame is the heart going this colour. */
export const GLITCH_LED = "#39ff14";

const RealDate = Date;
/** A local instant from literals; `mo` is 1-12 (October = 10). */
export const at = (y, mo, d, h = 0, mi = 0) => new RealDate(y, mo - 1, d, h, mi).getTime();

/** Run `fn` with the process in time zone `tz`, restored afterwards. A zone west of UTC
 *  tells the visitor's LOCAL month from UTC's on the last evening of a month. */
export async function withZone(tz, fn) {
  const saved = process.env.TZ;
  process.env.TZ = tz;
  try { return await fn(); } finally {
    if (saved === undefined) delete process.env.TZ; else process.env.TZ = saved;
  }
}

/** A seeded PRNG (mulberry32): the same "random" visit on every run. */
export function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Timers and `Date` on a hand-stepped clock. `advance(ms)` runs every timer that falls due,
 *  in time order, and lets promises settle after each, so a `fetch().then()` lands where it
 *  would on a page. A throw inside a timer is collected, never lost. */
export function virtualClock(startMs) {
  let now = startMs, seq = 0;
  const timers = new Map(), errors = [];
  class VDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }
  const settle = () => new Promise((r) => setImmediate(r));
  return {
    Date: VDate,
    errors,
    get now() { return now; },
    setTimeout: (fn, ms) => {
      const id = ++seq;
      timers.set(id, { at: now + Math.max(0, Number(ms) || 0), fn, id });
      return id;
    },
    clearTimeout: (id) => { timers.delete(id); },
    async advance(ms) {
      const end = now + ms;
      await settle();                          // a browser runs the caller's microtasks first
      for (;;) {
        let next = null;
        for (const t of timers.values())
          if (t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
        if (!next) break;
        timers.delete(next.id);
        now = next.at;
        try { next.fn(); } catch (e) { errors.push(e); }
        await settle();
      }
      now = end;
      await settle();
    },
  };
}

const GLOBALS = ["window", "document", "fetch", "CustomEvent", "setTimeout", "clearTimeout", "Date", "location"];

/**
 * Open a stub page running the REAL ambient.js, drive it with `run(t)`, and return `t`.
 *   start     the virtual instant the page opens (default: noon, 15 October 2026)
 *   random    Math.random for the visit (default: seeded(1))
 *   mode      what window.moxieMode.state() answers (default "live")
 *   bridge    also load the real bridge/ into the page, before ambient.js as sim.html does
 *
 * Her voice is modelled as voice/ reports it: `isMoxieBusy(grace)` is true while a voice
 * window is open and for `grace` ms after the last one closed. Every `speak()` opens one
 * (about 60 ms a character, a kristin clip's pace) and `t.voice(from, to)` opens one for a
 * reply. There is no MutationObserver under node, so `t.visitorLine()` and `t.reply()` stand
 * in for the transcript observer (`__ambient.noteTurn`).
 */
export async function ambientPage(o, run) {
  const had = GLOBALS.filter((k) => k in globalThis);
  const saved = Object.fromEntries(GLOBALS.map((k) => [k, globalThis[k]]));
  const savedRandom = Math.random;
  const clock = virtualClock(o.start === undefined ? at(2026, 10, 15, 12) : o.start);
  const winL = {}, docL = {}, idleL = {}, boxL = {};
  const said = [], faces = [], hearts = [], events = [];
  const windows = [];                          // her voice on the speakers: [from, to)
  let mode = o.mode || "live";
  const fire = (reg, type, ev) => (reg[type] || []).slice().forEach((fn) => fn(ev || { type }));
  const speakingAt = (now) => windows.some(([a, b]) => a <= now && now < b);
  const lastEnd = (now) => windows.reduce((m, [, b]) => (b <= now && b > m ? b : m), 0);
  const els = {
    "idle-on": { checked: true, addEventListener: (t, fn) => (idleL[t] ||= []).push(fn) },
    "speech-input": { value: "", addEventListener: (t, fn) => (boxL[t] ||= []).push(fn) },
    "led-on": { checked: false },
  };
  try {
    Math.random = o.random || seeded(1);
    globalThis.Date = clock.Date;
    globalThis.setTimeout = clock.setTimeout;
    globalThis.clearTimeout = clock.clearTimeout;
    globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
    globalThis.location = { hostname: "moxie.example", protocol: "https:" };
    globalThis.fetch = (url) => (String(url) === "ambient.json"
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(AMB))) })
      : Promise.reject(new Error("no network in this stub")));
    globalThis.document = {
      hidden: false,
      activeElement: null,
      body: { attrs: {}, getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; } },
      getElementById: (id) => els[id] || null,
      addEventListener: (t, fn) => (docL[t] ||= []).push(fn),
      createElement: () => ({ setAttribute() {}, appendChild() {}, querySelector: () => null }),
    };
    globalThis.window = {
      addEventListener: (t, fn) => (winL[t] ||= []).push(fn),
      removeEventListener: (t, fn) => { const l = winL[t] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); },
      dispatchEvent: (ev) => { events.push({ type: ev.type, at: clock.now }); fire(winL, ev.type, ev); return true; },
      moxie: {
        setFace: (f) => faces.push({ face: f, at: clock.now }),
        setHeartLED: (on, color) => {
          hearts.push({ on: !!on, color, at: clock.now });
          els["led-on"].checked = !!on;              // as moxie.js mirrors it into #led-on
        },
        setSpeech() {}, showIcons() {}, clearIcons() {}, setMotor() {}, getMotor: () => 16384, centerAll() {},
      },
      moxieAudio: {
        speak: (text, group) => {
          said.push({ text, group: group || "moxie", at: clock.now });
          windows.push([clock.now, clock.now + 600 + 60 * String(text).length]);
          return Promise.resolve(true);
        },
        isUnlocked: () => true,
        isMoxieBusy: (grace) => {
          const now = clock.now, g = +grace || 0, end = lastEnd(now);
          return speakingAt(now) || (g > 0 && end > 0 && now - end < g);
        },
        ttsPending: () => 0,
        sfx() {}, speakClipOnly() {}, stop() {},
      },
      moxieMode: {
        state: () => mode,
        onChange: (fn) => { fn({ state: mode }); return () => {}; },
      },
    };
    if (o.bridge) (0, eval)(BRIDGE_SRC);
    new Function(ambientSrc)();
    const amb = globalThis.window.__ambient;
    const t = {
      said, faces, hearts, events, clock, els,
      get now() { return clock.now; },
      advance: (ms) => clock.advance(ms),
      /** Her voice (a reply) on the speakers from `from` to `to`, virtual ms. */
      voice: (from, to) => { windows.push([from, to]); },
      /** voice/'s isMoxieBusy(grace), as the page sees it now. */
      busy: (grace) => globalThis.window.moxieAudio.isMoxieBusy(grace),
      signoff: () => globalThis.window.dispatchEvent(new globalThis.CustomEvent("moxie-signoff")),
      visitorLine: () => amb.noteTurn(true),
      reply: () => amb.noteTurn(),
      typing: (text) => { els["speech-input"].value = text; },
      /** Focus the message box and type into it (its focus and input events fire). */
      keystroke: () => {
        globalThis.document.activeElement = els["speech-input"];
        fire(boxL, "focus"); fire(boxL, "input");
      },
      mic: (on) => { globalThis.document.body.attrs["data-mic"] = on ? "on" : undefined; },
      hide: (hidden) => { globalThis.document.hidden = hidden; fire(docL, "visibilitychange"); },
      liveness: (on) => { els["idle-on"].checked = on; fire(idleL, "change"); },
      setMode: (m) => { mode = m; },
      state: () => amb.state(),
      api: () => globalThis.window.moxieAmbient,
      bridge: () => globalThis.window.moxieBridge,
      /** What she said that is one of `texts` (an array or Set of line texts). */
      of: (texts) => { const s = new Set(texts); return said.filter((x) => s.has(x.text)); },
      speakingAt,
    };
    await clock.advance(0);                    // ambient.json loads
    await run(t);
    try { globalThis.window.moxieAmbient.stop(); } catch (e) { /* the page never booted */ }
    ok(clock.errors.length === 0, `no timer threw inside the page: ${clock.errors.map(String).join("; ")}`);
    return t;
  } finally {
    Math.random = savedRandom;
    for (const k of GLOBALS) {
      if (had.includes(k)) globalThis[k] = saved[k];
      else delete globalThis[k];
    }
  }
}
