/* Cloud-TTS playback tests — the browser SIM's half of AI seam ③.
 *
 * The supervisor publishes a `CloudTTSResponse` on `/devices/{id}/commands/tts`
 * (base64 RAW little-endian 16-bit PCM + `marks[]` + `event_id`/`chunk_num`).
 * sim/web/audio.js decodes that wire ITSELF — like robot firmware, never importing
 * the server SDK — and plays it through the shared Web Audio context.
 *
 * This matters: if the decode drifts (endianness, the /32768 scale, the frame
 * count, the sample rate) Moxie plays noise, or nothing, and the failure is silent
 * — the audio just sounds wrong on a machine nobody is listening to. So we test
 * the pure decode against hand-built PCM AND against the REAL server encoder
 * (mqtt/moxie_sdk/tts.py), then drive the whole playback path on a fake
 * AudioContext: queueing, chunk ordering, the autoplay policy, mouth animation,
 * the speaking state, and mute.
 *
 * Wire shape: docs/architecture/ai-seam.md §3 (embodied/unity/CloudTTS.proto).
 * Run: node sim/test_audio.mjs
 */
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
const near = (a, b, eps = 1e-4) => Math.abs(a - b) <= eps;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --------------------------------------------------------------------------- //
// A fake Web Audio + DOM environment (audio.js is a classic-script IIFE).
// --------------------------------------------------------------------------- //
class FakeBuffer {
  constructor(ch, len, rate) {
    this.numberOfChannels = ch; this.length = len; this.sampleRate = rate;
    this.duration = len / rate;
    this._d = Array.from({ length: ch }, () => new Float32Array(len));
  }
  getChannelData(i) { return this._d[i]; }
  copyToChannel(src, i) { this._d[i].set(src); }
}

const CTX = { startState: "running", allowResume: true, made: null };

class FakeCtx {
  constructor() {
    this.state = CTX.startState;
    this.currentTime = 0;
    this.destination = { _dest: true };
    this.sources = [];
    CTX.made = this;
  }
  createBuffer(ch, len, rate) { return new FakeBuffer(ch, len, rate); }
  createBufferSource() {
    const src = {
      buffer: null, onended: null, started: false, stopped: false,
      connect() {}, disconnect() {},
      start() { this.started = true; },
      stop() { this.stopped = true; if (this.onended) this.onended(); },
    };
    this.sources.push(src);
    return src;
  }
  // constant, non-silent waveform → the envelope pump sees a real signal
  createAnalyser() {
    return {
      fftSize: 256, frequencyBinCount: 128,
      connect() {}, getByteTimeDomainData(a) { a.fill(148); },   // peak 20 → open 0.5
    };
  }
  resume() { if (CTX.allowResume) this.state = "running"; return Promise.resolve(); }
}

const listeners = {};
const mouthCalls = [];
const ttsEvents = [];
const el = { textContent: "optional local Piper service" };
const bodyClasses = new Set();

globalThis.window = {
  AudioContext: FakeCtx,
  moxie: { setMouthOpen: (v) => mouthCalls.push(v) },
  addEventListener: (ev, cb) => { (listeners[ev] ||= []).push(cb); },
  dispatchEvent: (e) => { ttsEvents.push(e.type); return true; },
};
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.location = { protocol: "http:", hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: (id) => (id === "tts-status" ? el : null),
  body: { classList: { toggle: (c, on) => (on ? bodyClasses.add(c) : bodyClasses.delete(c)) } },
};
const rafTimers = new Map();
let rafId = 1;
globalThis.requestAnimationFrame = (cb) => {
  const id = rafId++;
  rafTimers.set(id, setTimeout(() => { rafTimers.delete(id); cb(Date.now()); }, 6));
  return id;
};
globalThis.cancelAnimationFrame = (id) => {
  const t = rafTimers.get(id);
  if (t) { clearTimeout(t); rafTimers.delete(id); }
};

const fire = (ev) => (listeners[ev] || []).slice().forEach((cb) => cb({ type: ev }));

// ---- load the real module ---------------------------------------------------
const audioSrc = readFileSync(join(here, "web", "audio.js"), "utf8");
new Function(audioSrc)();
const A = globalThis.window.moxieAudio;
ok(!!A, "audio.js must expose window.moxieAudio");
if (!A) { console.log("❌ audio tests FAILED:\n   - " + fails.join("\n   - ")); process.exit(1); }
for (const fn of ["playCloudTTS", "decodeCloudTTS", "isSpeaking", "ttsPending"])
  ok(typeof A[fn] === "function", `moxieAudio.${fn} must exist`);

// ---- helper: build a CloudTTSResponse from int16 samples ---------------------
function wire(samples, opts = {}) {
  const buf = Buffer.alloc(samples.length * 2);
  samples.forEach((s, i) => buf.writeInt16LE(s, i * 2));
  return {
    request_source: "ROBOT_TTS_REQUEST",
    audio: {
      buffer: buf.toString("base64"),
      channels: opts.channels ?? 1,
      sample_rate: opts.rate ?? 22050,
    },
    marks: opts.marks ?? [],
    event_id: opts.eventId ?? "evt",
    chunk_num: opts.chunk ?? 0,
  };
}
const tone = (n) => Array.from({ length: n }, (_, i) => Math.round(12000 * Math.sin(i / 4)));

// --------------------------------------------------------------------------- //
// 1. Pure decode — base64 → int16 LE → Float32, frames / rate / duration maths
// --------------------------------------------------------------------------- //
{
  const d = A.decodeCloudTTS(wire([0, 16384, -16384, 32767, -32768],
    { rate: 22050, marks: [{ time: 10, type: "viseme", value: "a" }], eventId: "e1", chunk: 3 }));
  ok(d.frames === 5, `frames should be 5, got ${d.frames}`);
  ok(d.channels === 1 && d.sampleRate === 22050, "channels/sampleRate must come from the wire");
  ok(near(d.duration, 5 / 22050), `duration must be frames/rate, got ${d.duration}`);
  ok(d.bytes === 10, `bytes should be 10, got ${d.bytes}`);
  ok(d.data.length === 1 && d.data[0] instanceof Float32Array, "data must be planar Float32Array(s)");
  const f = Array.from(d.data[0]);
  ok(near(f[0], 0) && near(f[1], 0.5) && near(f[2], -0.5) && near(f[4], -1),
     `Float32 scale wrong (÷32768): ${f}`);
  ok(f[3] > 0.999 && f[3] <= 1, `+full-scale must land just under 1.0, got ${f[3]}`);
  ok(f.every((v) => v >= -1 && v <= 1), "every sample must be inside [-1, 1]");
  ok(d.eventId === "e1" && d.chunkNum === 3, "event_id / chunk_num must survive the decode");
  ok(d.marks.length === 1 && d.marks[0].value === "a", "marks[] must survive the decode");
}

// little-endian, explicitly: 0x0100 LE == 1, and NOT 256
{
  const d = A.decodeCloudTTS({ audio: { buffer: Buffer.from([0x01, 0x00]).toString("base64") } });
  ok(near(d.data[0][0], 1 / 32768), `PCM must be read little-endian, got ${d.data[0][0] * 32768}`);
  ok(d.sampleRate === 24000, `missing sample_rate must default to 24000, got ${d.sampleRate}`);
  ok(d.channels === 1, "missing channels must default to mono");
}

// stereo de-interleave: L,R,L,R… → one Float32Array per channel
{
  const d = A.decodeCloudTTS(wire([1000, -1000, 2000, -2000], { channels: 2, rate: 16000 }));
  ok(d.channels === 2 && d.frames === 2, `stereo frames should be 2, got ${d.frames}`);
  ok(near(d.data[0][0], 1000 / 32768) && near(d.data[1][0], -1000 / 32768) &&
     near(d.data[0][1], 2000 / 32768) && near(d.data[1][1], -2000 / 32768),
     "stereo must de-interleave L/R correctly");
}

// tolerant: junk/empty/odd-length inputs never throw and never produce NaN
{
  ok(A.decodeCloudTTS(undefined).frames === 0, "undefined payload → 0 frames");
  ok(A.decodeCloudTTS({}).frames === 0, "empty payload → 0 frames");
  ok(A.decodeCloudTTS({ audio: { buffer: "!!!not base64!!!" } }).frames === 0,
     "invalid base64 → 0 frames (never a throw)");
  const odd = A.decodeCloudTTS({ audio: { buffer: Buffer.from([1, 2, 3]).toString("base64") } });
  ok(odd.frames === 1, `an odd trailing byte must be dropped, got ${odd.frames} frames`);
  const str = A.decodeCloudTTS(JSON.stringify(wire([100, 200])));
  ok(str.frames === 2, "a JSON STRING payload must decode too (raw MQTT payloads)");
  const bad = A.decodeCloudTTS({ audio: { sample_rate: 0, channels: 0, buffer: "" } });
  ok(bad.sampleRate === 24000 && bad.channels === 1, "zero rate/channels fall back to sane defaults");
}

// --------------------------------------------------------------------------- //
// 2. Parity with the REAL server encoder (mqtt/moxie_sdk/tts.py)
// --------------------------------------------------------------------------- //
let parity = null;
try {
  const script =
    "import sys, json; sys.path.insert(0, 'mqtt')\n" +
    "from moxie_sdk.tts import ToneSynthesizer, synthesize_cloud_tts\n" +
    "s = ToneSynthesizer()\n" +
    "r = synthesize_cloud_tts(s, 'Hello there, I am Moxie.', event_id='evt-parity')\n" +
    "raw = s.synthesize('Hello there, I am Moxie.')\n" +
    "print(json.dumps({'resp': r, 'nbytes': len(raw), 'rate': s.sample_rate}))";
  parity = JSON.parse(execFileSync("python3", ["-c", script], { cwd: repo, encoding: "utf8" }));
} catch (e) {
  console.log("ℹ️  moxie_sdk not importable (parity skipped) —", String(e.message).split("\n").pop());
}
if (parity) {
  const d = A.decodeCloudTTS(parity.resp);
  ok(d.sampleRate === parity.rate, `sample rate must round-trip (${d.sampleRate} vs ${parity.rate})`);
  ok(d.frames === parity.nbytes / 2, `frames must equal PCM bytes/2 (${d.frames} vs ${parity.nbytes / 2})`);
  ok(d.eventId === "evt-parity", "event_id must round-trip through the wire");
  ok(near(d.duration, parity.nbytes / 2 / parity.rate, 1e-6), "duration must match the server's audio length");
  let peak = 0;
  for (const v of d.data[0]) peak = Math.max(peak, Math.abs(v));
  ok(peak > 0.3 && peak <= 1, `decoded tone peak looks wrong (${peak.toFixed(3)}; expect ≈0.366)`);
}

// --------------------------------------------------------------------------- //
// 3. Autoplay policy — a suspended context queues instead of dropping audio
// --------------------------------------------------------------------------- //
CTX.startState = "suspended";
CTX.allowResume = false;
const queued = A.playCloudTTS(wire(tone(64), { eventId: "gate" }));
ok(A.ttsPending() === 1, `suspended context must QUEUE the audio, pending=${A.ttsPending()}`);
ok(A.isSpeaking() === false, "nothing may be 'speaking' while the context is suspended");
ok(CTX.made.sources.length === 0, "no source may start before the context is running");

CTX.allowResume = true;               // the next user gesture unlocks audio
fire("pointerdown");
ok(A.isSpeaking() === true, "the queued audio must start on the first user gesture");
ok(CTX.made.sources.length === 1, "exactly one source should be playing");
ok(bodyClasses.has("tts-speaking"), "body.tts-speaking must be set while speaking");
ok(/speaking/.test(el.textContent), `#tts-status should show the speaking indicator, got ${el.textContent}`);
ok(ttsEvents.includes("moxie-tts-start"), "a moxie-tts-start event must fire");

await sleep(30);                       // let the rAF mouth pump run a few frames
ok(mouthCalls.some((v) => v > 0.2), `the mouth must animate while speaking, saw ${mouthCalls.slice(0, 4)}`);

CTX.made.sources[0].onended();         // the buffer finished
const res = await queued;
ok(res.played === true, `playCloudTTS must resolve {played:true}, got ${JSON.stringify(res)}`);
ok(res.decoded.frames === 64, "the resolution carries the decoded payload");
ok(A.isSpeaking() === false, "speaking must clear when playback ends");
ok(mouthCalls[mouthCalls.length - 1] === 0, "the mouth must close when playback ends");
ok(!bodyClasses.has("tts-speaking"), "body.tts-speaking must be cleared");
ok(el.textContent === "optional local Piper service", "#tts-status must be restored after speaking");
ok(ttsEvents.includes("moxie-tts-end"), "a moxie-tts-end event must fire");

// --------------------------------------------------------------------------- //
// 4. Chunked responses — same event_id plays in chunk_num order
// --------------------------------------------------------------------------- //
{
  const ctx = CTX.made;
  const before = ctx.sources.length;
  A.playCloudTTS(wire(tone(4), { eventId: "chunky", chunk: 0 }));   // starts at once
  A.playCloudTTS(wire(tone(12), { eventId: "chunky", chunk: 2 }));  // queued
  A.playCloudTTS(wire(tone(8), { eventId: "chunky", chunk: 1 }));   // queued, must jump ahead of 2
  ok(A.ttsPending() === 2, `two chunks should be waiting, got ${A.ttsPending()}`);
  const order = [];
  for (let i = 0; i < 3; i++) {
    const src = ctx.sources[ctx.sources.length - 1];
    order.push(src.buffer.length);
    ok(A.isSpeaking(), `speaking must stay true between chunks (chunk ${i})`);
    src.onended();
    await sleep(2);
  }
  ok(ctx.sources.length === before + 3, "each chunk must get its own source node");
  ok(order.join(",") === "4,8,12", `chunks must play in chunk_num order, got ${order.join(",")}`);
  ok(A.isSpeaking() === false, "speaking clears after the last chunk");
  ok(A.ttsPending() === 0, "the queue must drain");
}

// --------------------------------------------------------------------------- //
// 5. Marks drive the mouth; empty audio and mute are honest no-ops
// --------------------------------------------------------------------------- //
{
  const empty = await A.playCloudTTS(wire([], { eventId: "quiet" }));
  ok(empty.played === false && empty.reason === "empty", "empty audio must resolve, not hang");
  ok(A.isSpeaking() === false, "empty audio never enters the speaking state");

  A.setEnabled(false);
  const muted = await A.playCloudTTS(wire(tone(64)));
  ok(muted.played === false && muted.reason === "muted", "muting must suppress server audio");
  ok(A.isSpeaking() === false, "muted audio never speaks");
  A.setEnabled(true);

  // viseme marks: an open vowel ('a') must open the mouth further than silence
  mouthCalls.length = 0;
  const ctx = CTX.made;
  const withMarks = A.playCloudTTS(wire(tone(64), {
    eventId: "vis",
    marks: [{ time: 0, start: 0, end: 1, type: "viseme", value: "a" }],
  }));
  await sleep(30);
  ok(mouthCalls.some((v) => v >= 0.79), `a 'viseme:a' mark must open the mouth wide, saw ${Math.max(...mouthCalls)}`);
  ctx.sources[ctx.sources.length - 1].onended();
  await withMarks;
}

// --------------------------------------------------------------------------- //
// 6. Wiring — the bridge routes /commands/tts here, and the SIM stays SDK-free
// --------------------------------------------------------------------------- //
{
  const bridge = readFileSync(join(here, "web", "bridge.js"), "utf8");
  ok(bridge.includes('client.subscribe("/devices/+/commands/tts")'),
     "bridge.js must subscribe to /devices/+/commands/tts");
  ok(/topic\.endsWith\("\/commands\/tts"\)/.test(bridge),
     "bridge.js route() must dispatch /commands/tts");
  ok(bridge.includes("playCloudTTS"), "bridge.js must hand the payload to moxieAudio.playCloudTTS");
  ok(bridge.includes("speakLocally"),
     "bridge.js must arbitrate the local voice so a turn is never spoken twice");

  ok(/createBuffer\(/.test(audioSrc),
     "audio.js must build the AudioBuffer by hand (raw PCM is not a container)");
  ok(!/decodeAudioData[\s\S]{0,400}playCloudTTS/.test(audioSrc),
     "the CloudTTS path must not use decodeAudioData (raw PCM has no header)");
  for (const f of ["audio.js", "bridge.js"])
    ok(!readFileSync(join(here, "web", f), "utf8").includes("moxie_sdk"),
       `${f} must decode the wire itself — no server-SDK import (client/server independence)`);

  const html = readFileSync(join(here, "web", "sim.html"), "utf8");
  ok(/src="audio\.js(\?[^"]*)?"/.test(html), "sim.html must load audio.js");
  ok(html.includes('id="tts-status"'), "sim.html must keep the #tts-status indicator");
  ok(html.includes('id="audio-on"'), "sim.html must keep the mute toggle the TTS path honors");

  const moxie = readFileSync(join(here, "web", "moxie.js"), "utf8");
  ok(/getMouthOpen\(\)/.test(moxie), "moxie.js must expose getMouthOpen() for the lip-sync tests");
}

// ---- report ------------------------------------------------------------------
for (const t of rafTimers.values()) clearTimeout(t);
if (fails.length) {
  console.log("❌ audio tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ audio tests OK — CloudTTSResponse decode (LE int16 → Float32, mono + stereo," +
            " odd/junk input)" + (parity ? ", parity with the real moxie_sdk encoder" : " (parity skipped)") +
            ", autoplay queueing, chunk_num ordering, mouth/marks lip-sync, mute, bridge wiring");
