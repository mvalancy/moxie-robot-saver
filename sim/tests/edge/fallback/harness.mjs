/* Harness for `sim/test_fallback_coverage.mjs`: the shipped fallback assets (the clip manifest,
 * `stub.js`, `ambient.js`/`ambient.json`, `voice/`), a ledger that also collects the summary
 * notes, and the fake browser pieces the behavioural sections boot the real scripts under.
 */
import { BRIDGE_SRC, VOICE_SRC } from "../../../bridge_harness.mjs";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repo, ledger } from "../common.mjs";

export { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
export { join };

export const { fails, C, ok, eq } = ledger();
/** One-line findings printed under the summary. */
export const notes = [];

export const here = join(repo, "sim");
export const web = join(here, "web");
export const audioDir = join(web, "audio");
export const sessionsDir = join(web, "sessions");

/* Floors, not equalities: they fail when content is DELETED while letting the ambient layer
 * keep growing. §5's coverage rule is what fails when content is ADDED without a clip. */
export const FLOORS = { moxie: 30, child: 2, ambient: 56, stubReplies: 11, fillerLines: 8 };

export const manifest = JSON.parse(readFileSync(join(audioDir, "index.json"), "utf8"));
export const ambient = JSON.parse(readFileSync(join(web, "ambient.json"), "utf8"));
export const stubSrc = readFileSync(join(web, "stub.js"), "utf8");
export const ambientSrc = readFileSync(join(web, "ambient.js"), "utf8");
export const audioSrc = VOICE_SRC;

/** The line ambient.js says once on entering `degraded`. */
export const degradedText = ((ambient.degraded || {}).text || "").trim();

/** Run `fn` with the named globals saved, and restore them afterwards whatever happens. */
export async function withGlobals(keys, fn) {
  const g = globalThis;
  const saved = Object.fromEntries(keys.map((k) => [k, g[k]]));
  try { return await fn(g); } finally { for (const k of keys) g[k] = saved[k]; }
}

export const FakeCustomEvent = class { constructor(t, i) { this.type = t; this.detail = i && i.detail; } };

/**
 * A fake Web Audio stack that records which clip URL each source STARTED and STOPPED.
 * `byLen` maps a fetched ArrayBuffer's byteLength back to its URL, so a decoded buffer can
 * be traced to its file without assuming anything about call order.
 */
export function fakeWebAudio(log, byLen) {
  class Src {
    constructor() { this.onended = null; this.buffer = null; }
    connect() {}
    start() { log.started.push((this.buffer && this.buffer.url) || "?"); }
    stop() { (log.stopped || []).push((this.buffer && this.buffer.url) || "?"); }
  }
  return class Ctx {
    constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
    resume() {}
    createBufferSource() { return new Src(); }
    createAnalyser() { return { fftSize: 256, frequencyBinCount: 8, connect() {}, getByteTimeDomainData() {} }; }
    decodeAudioData(buf) { return Promise.resolve({ url: byLen.get(buf.byteLength) || "?" }); }
    createOscillator() { return { type: "", frequency: { setValueAtTime() {} }, connect() {}, start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  };
}
