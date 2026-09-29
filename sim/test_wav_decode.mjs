/* test_wav_decode.mjs — the audio contract, both halves, with no server (live-sim-demo.md §8.1
 * test 3, §3.2, §2.2). The server half (`_lib/wav.js`) and the browser half (`voice/cloud.js::
 * decodeCloudTTS`) are written separately; if they drift Moxie plays noise, silently. So a WAV
 * goes through both, compared sample for sample, with `tts.py::pcm_from_audio` as the oracle. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { VOICE_SRC } from "./bridge_harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

const wav = await import(join(repo, "functions", "api", "_lib", "wav.js"));
const wire = await import(join(repo, "functions", "api", "_lib", "wire.js"));
const hmac = await import(join(repo, "functions", "api", "_lib", "hmac.js"));

/* Load the REAL sim/web/voice/*.js under a minimal fake Web Audio + DOM (only `decodeCloudTTS`). */
const AUDIO_SRC = VOICE_SRC;
globalThis.window = { addEventListener() {}, moxie: null };
globalThis.document = {
  getElementById: () => null,
  addEventListener() {},
  createElement: () => ({ style: {}, addEventListener() {}, appendChild() {}, setAttribute() {} }),
  body: { appendChild() {} },
};
globalThis.location = { hostname: "127.0.0.1", protocol: "http:", origin: "http://127.0.0.1" };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.AudioContext = class {
  constructor() { this.state = "running"; this.currentTime = 0; this.destination = {}; }
  createBuffer(ch, len, rate) {
    const d = Array.from({ length: ch }, () => new Float32Array(len));
    return { numberOfChannels: ch, length: len, sampleRate: rate, duration: len / rate,
             getChannelData: (i) => d[i], copyToChannel: (src, i) => d[i].set(src) };
  }
  createBufferSource() { return { connect() {}, disconnect() {}, start() {}, stop() {} }; }
  createGain() { return { connect() {}, gain: { value: 1 } }; }
  createAnalyser() { return { connect() {}, fftSize: 2048, getByteTimeDomainData() {} }; }
  resume() { return Promise.resolve(); }
};
globalThis.speechSynthesis = { speak() {}, cancel() {}, getVoices: () => [] };
globalThis.SpeechSynthesisUtterance = class {};
globalThis.fetch = () => Promise.reject(new Error("no network in this test"));
(0, eval)(AUDIO_SRC);
const decodeCloudTTS = globalThis.window.moxieAudio && globalThis.window.moxieAudio.decodeCloudTTS;
ok(typeof decodeCloudTTS === "function", "sim/web/voice/ must expose decodeCloudTTS");

/** A deterministic 16-bit signed sine-ish ramp, interleaved across `channels`. */
function makePcm(frames, channels) {
  const out = new Int16Array(frames * channels);
  for (let f = 0; f < frames; f++) {
    for (let c = 0; c < channels; c++) {
      // Values that cover the whole signed range, including the extremes, so a sign or
      // endianness bug cannot hide in the middle of it.
      const v = Math.round(32767 * Math.sin((f / frames) * Math.PI * 2 * (c + 1)));
      out[f * channels + c] = f === 0 ? -32768 : f === 1 ? 32767 : v;
    }
  }
  return new Uint8Array(out.buffer, out.byteOffset, out.byteLength);
}

const asciiAt = (bytes, at, s) => { for (let i = 0; i < s.length; i++) bytes[at + i] = s.charCodeAt(i); };

/* =========================================================================== *
 * 1. A canonical 16-bit mono WAV, end to end, sample for sample
 * =========================================================================== */
{
  const frames = 512;
  const pcm = makePcm(frames, 1);
  const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });

  const out = wav.pcmFromAudio(file, { sampleRate: 99999, channels: 7 });
  eq(out.container, "wav", "a RIFF/WAVE body is recognised");
  eq(out.sampleRate, 22050, "THE HEADER'S OWN RATE wins over the configured 99999");
  eq(out.channels, 1, "THE HEADER'S OWN CHANNEL COUNT wins over the configured 7");
  eq(out.pcm.length, pcm.length, "the data chunk comes back whole");
  ok(out.pcm.every((b, i) => b === pcm[i]), "…byte for byte");

  // Now the browser half, on the exact `CloudTTSResponse` the route would emit.
  const resp = wire.buildCloudTtsResponse({
    buffer: hmac.b64FromBytes(out.pcm), channels: out.channels, sampleRate: out.sampleRate,
    eventId: "sim-aabbccddeeff", chunkNum: 0,
  });
  const dec = decodeCloudTTS(resp);
  eq(dec.channels, 1, "the browser reads back 1 channel");
  eq(dec.sampleRate, 22050, "…the same 22050 rate");
  eq(dec.frames, frames, "…the same frame count");
  eq(dec.eventId, "sim-aabbccddeeff", "…the event_id");
  eq(dec.chunkNum, 0, "…the chunk_num");
  deep(dec.marks, [], "…and no marks, which still lip-syncs from the envelope");

  // SAMPLE FOR SAMPLE. This is the assertion the whole file exists for.
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let worst = 0;
  for (let f = 0; f < frames; f++) {
    const want = view.getInt16(f * 2, true) / 32768;
    worst = Math.max(worst, Math.abs(dec.data[0][f] - want));
  }
  ok(worst === 0, `every sample round-trips EXACTLY (worst delta ${worst})`);
  // The extremes specifically: -32768 and +32767 are where a sign or scale bug shows.
  eq(dec.data[0][0], -1, "-32768 decodes to exactly -1.0");
  ok(Math.abs(dec.data[0][1] - 32767 / 32768) < 1e-9, "+32767 decodes to just under +1.0");

  // `route()` takes a JSON STRING, and the browser decoder accepts one too, so the whole
  // path is proven on the actual wire form rather than on an object.
  const asString = JSON.stringify(resp);
  const decStr = decodeCloudTTS(asString);
  eq(decStr.frames, dec.frames, "the decoder accepts the JSON STRING route() will hand it");
  ok(decStr.data[0].every((v, i) => v === dec.data[0][i]), "…with identical samples");
}

/* =========================================================================== *
 * 2. Stereo, and the interleave order
 * =========================================================================== */
{
  const frames = 128;
  const pcm = makePcm(frames, 2);
  const file = wav.writeWav(pcm, { sampleRate: 48000, channels: 2, bitsPerSample: 16 });
  const out = wav.pcmFromAudio(file, { sampleRate: 22050, channels: 1 });
  eq(out.channels, 2, "a stereo header reports 2 channels");
  eq(out.sampleRate, 48000, "…and its own 48000 rate");

  const dec = decodeCloudTTS(wire.buildCloudTtsResponse({
    buffer: hmac.b64FromBytes(out.pcm), channels: out.channels, sampleRate: out.sampleRate,
    eventId: "e", chunkNum: 0,
  }));
  eq(dec.channels, 2, "the browser reads 2 planar channels");
  eq(dec.frames, frames, "…with the right frame count");
  const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let worst = 0;
  for (let f = 0; f < frames; f++) {
    for (let c = 0; c < 2; c++) {
      worst = Math.max(worst, Math.abs(dec.data[c][f] - view.getInt16((f * 2 + c) * 2, true) / 32768));
    }
  }
  ok(worst === 0, `stereo de-interleaves exactly (worst delta ${worst})`);
}

/* =========================================================================== *
 * 3. §2.2 — SNIFF THE BYTES, NEVER THE CONTENT-TYPE
 * =========================================================================== */
{
  // (That the ROUTE ignores a lying upstream Content-Type is test_demo_proxy §10c's job.)
  // A raw-PCM body has no header, so the CONFIGURED rate is right — only here. `format` is
  // DEMO_TTS_FORMAT (what we ORDERED): headerless PCM and an opaque error blob are the same bytes.
  const pcm = makePcm(64, 1);
  const raw = wav.pcmFromAudio(pcm, { sampleRate: 16000, channels: 1, format: "pcm" });
  eq(raw.container, "raw", "under DEMO_TTS_FORMAT=pcm a non-RIFF body is the raw PCM we asked for");
  eq(raw.sampleRate, 16000, "…at the CONFIGURED rate");
  eq(raw.channels, 1, "…and the configured channel count");
  ok(raw.pcm.every((b, i) => b === pcm[i]), "…with the bytes untouched");

  // The same bytes under the shipped `wav` default (or an ABSENT format) are REFUSED: returning
  // them as raw would ship full-scale static to a child.
  for (const [label, fb] of [
    ["DEMO_TTS_FORMAT=wav", { sampleRate: 16000, channels: 1, format: "wav" }],
    ["an ABSENT format", { sampleRate: 16000, channels: 1 }],
  ]) {
    let threw = null;
    try { wav.pcmFromAudio(pcm, fb); } catch (e) { threw = e; }
    ok(threw instanceof wav.AudioBodyError, `under ${label} a non-RIFF body RAISES`);
    eq(threw && threw.kind, "unreadable", `…of kind unreadable (${label})`);
  }
}

/* =========================================================================== *
 * 4. Bit depth — 8 and 24 are REFUSED, not converted
 * =========================================================================== */
{
  for (const bits of [8, 24, 32]) {
    const file = wav.writeWav(makePcm(64, 1), { sampleRate: 22050, channels: 1, bitsPerSample: bits });
    let threw = null;
    try { wav.pcmFromAudio(file, { sampleRate: 22050 }); } catch (e) { threw = e; }
    ok(threw instanceof wav.AudioBodyError, `a ${bits}-bit WAV raises AudioBodyError`);
    eq(threw && threw.kind, "bit_depth", `…of kind bit_depth (${bits}-bit)`);
    ok(threw && String(threw.message).includes(String(bits)), `…naming the depth (${bits})`);
  }
  // 16-bit is the one that passes, because `voice/cloud.js::decodeCloudTTS` reads getInt16 with no width
  // branch — a silent conversion here would be inaudible to us and audible to a child.
  const good = wav.writeWav(makePcm(64, 1), { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
  eq(wav.pcmFromAudio(good, { sampleRate: 22050 }).container, "wav", "16-bit passes");
}

/* =========================================================================== *
 * 5. A JSON body where audio was expected RAISES — it is never handed on as noise
 * =========================================================================== */
{
  const bodies = [
    ['{"error":{"message":"model not found","type":"invalid_request_error"}}', "an OpenAI error object"],
    ['{"detail":"Not Found"}', "a FastAPI 404 body"],
    ["  \n  {\"error\":\"x\"}", "a JSON body with leading whitespace"],
    ["[]", "a JSON array"],
    ['{"truncated": ', "a TRUNCATED JSON error — still not audio"],
  ];
  for (const [body, label] of bodies) {
    let threw = null;
    try { wav.pcmFromAudio(new TextEncoder().encode(body), { sampleRate: 22050 }); } catch (e) { threw = e; }
    ok(threw instanceof wav.AudioBodyError, `${label} raises`);
    eq(threw && threw.kind, "json", `…of kind json (${label})`);
  }

  // An EMPTY body raises too — a 200 with no bytes is not silence, it is a failure.
  let empty = null;
  try { wav.pcmFromAudio(new Uint8Array(0), { sampleRate: 22050 }); } catch (e) { empty = e; }
  eq(empty && empty.kind, "empty", "an empty body raises kind `empty`");

  // AN HTML BODY gets its own kind: a Cloudflare Access-protected tunnel answers an
  // unauthenticated fetch with an HTML login page AT 200, which would otherwise be played as
  // raw PCM static.
  for (const [body, label] of [
    ["<!DOCTYPE html><html><head><title>Sign in · Cloudflare Access</title></head><body></body></html>",
     "a Cloudflare Access login page"],
    ["<html><body>Access denied</body></html>", "a bare HTML page"],
    ["  \n<HTML>", "HTML with leading whitespace and upper case"],
    ["<meta http-equiv=\"refresh\" content=\"0;url=/cdn-cgi/access/login\">", "an HTML redirect stub"],
    ["<?xml version=\"1.0\"?><Error><Code>AccessDenied</Code></Error>", "an XML error document"],
  ]) {
    let threw = null;
    try { wav.pcmFromAudio(new TextEncoder().encode(body), { sampleRate: 22050 }); } catch (e) { threw = e; }
    ok(threw instanceof wav.AudioBodyError, `${label} raises`);
    eq(threw && threw.kind, "html", `…of kind html (${label}) — the diagnosis is the point`);
  }
  // A `<` that is not a document is NOT html: PCM whose first byte happens to be 0x3c must
  // still be treated as audio, so the sniff checks for a real tag rather than one byte.
  const ltFirst = new Uint8Array([0x3c, 0x00, 0x7f, 0x01, 0x02, 0x03]);
  eq(wav.pcmFromAudio(ltFirst, { sampleRate: 22050, format: "pcm" }).container, "raw",
     "PCM starting with 0x3c is not mistaken for HTML");
  // Under `wav` it is refused like any non-RIFF body, but the kind stays `unreadable`, never
  // `html` — the route maps them to different operator fixes.
  let ltThrew = null;
  try { wav.pcmFromAudio(ltFirst, { sampleRate: 22050 }); } catch (e) { ltThrew = e; }
  eq(ltThrew && ltThrew.kind, "unreadable", "…and under wav it is `unreadable`, NOT a false `html` diagnosis");

  // A body that merely CONTAINS a brace is fine — the sniff is on the first byte, not a
  // substring search, so PCM whose first sample happens to be 0x7b is not misread.
  const braceFirst = new Uint8Array([0x7b, 0x00, 0x01, 0x02, 0x03, 0x04]);
  let braceThrew = null;
  try { wav.pcmFromAudio(braceFirst, { sampleRate: 22050 }); } catch (e) { braceThrew = e; }
  ok(braceThrew instanceof wav.AudioBodyError, "PCM starting with 0x7b is (conservatively) refused");
  const braceInside = new Uint8Array([0x00, 0x7b, 0x01, 0x02]);
  eq(wav.pcmFromAudio(braceInside, { sampleRate: 22050, format: "pcm" }).container, "raw",
     "…but a brace BYTE inside PCM is not a JSON sniff");
  let insideThrew = null;
  try { wav.pcmFromAudio(braceInside, { sampleRate: 22050 }); } catch (e) { insideThrew = e; }
  eq(insideThrew && insideThrew.kind, "unreadable", "…and under wav it is `unreadable`, not a false `json` diagnosis");
}

/* =========================================================================== *
 * 6. Real-world RIFF variations the canonical-44-byte assumption would break on
 * =========================================================================== */
{
  const pcm = makePcm(100, 1);

  // A LIST/INFO chunk before `data` — a very common encoder habit. A reader that assumed
  // the data started at byte 44 would return the LIST text as audio.
  {
    // `\u0000` as an ESCAPE: a raw NUL byte makes grep treat this file as binary and skip it.
    const list = new TextEncoder().encode("INFOISFT" + "\u0000".repeat(4) + "Lavf");
    const total = 4 + 24 + (8 + list.length) + (8 + pcm.length);
    const file = new Uint8Array(8 + total);
    const view = new DataView(file.buffer);
    asciiAt(file, 0, "RIFF"); view.setUint32(4, total, true); asciiAt(file, 8, "WAVE");
    asciiAt(file, 12, "fmt "); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 22050, true); view.setUint32(28, 44100, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    asciiAt(file, 36, "LIST"); view.setUint32(40, list.length, true); file.set(list, 44);
    const dataAt = 44 + list.length;
    asciiAt(file, dataAt, "data"); view.setUint32(dataAt + 4, pcm.length, true);
    file.set(pcm, dataAt + 8);

    const out = wav.pcmFromAudio(file, { sampleRate: 99999 });
    eq(out.sampleRate, 22050, "a LIST chunk before data: the header's rate still wins");
    eq(out.pcm.length, pcm.length, "…and the data chunk is found by WALKING the chunk list");
    ok(out.pcm.every((b, i) => b === pcm[i]), "…with the right bytes, not the LIST text");
  }

  // An ODD-sized chunk before `data`, which RIFF pads with a byte that is NOT counted in
  // the chunk size. Off-by-one here shifts every following chunk id by one byte.
  {
    const odd = new TextEncoder().encode("abc");   // 3 bytes => one pad byte
    const total = 4 + 24 + (8 + 4) + (8 + pcm.length); // 3 + 1 pad
    const file = new Uint8Array(8 + total);
    const view = new DataView(file.buffer);
    asciiAt(file, 0, "RIFF"); view.setUint32(4, total, true); asciiAt(file, 8, "WAVE");
    asciiAt(file, 12, "fmt "); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 24000, true); view.setUint32(28, 48000, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    asciiAt(file, 36, "fact"); view.setUint32(40, 3, true); file.set(odd, 44);
    const dataAt = 48; // 44 + 3 + 1 pad byte
    asciiAt(file, dataAt, "data"); view.setUint32(dataAt + 4, pcm.length, true);
    file.set(pcm, dataAt + 8);

    const out = wav.pcmFromAudio(file, { sampleRate: 99999 });
    eq(out.sampleRate, 24000, "an odd-sized chunk: the RIFF pad byte is honoured");
    eq(out.pcm.length, pcm.length, "…and data is still found");
  }

  // A TRUNCATED file — the stream cut off mid-`data`. What arrived is playable, and
  // playing 90% of a sentence beats a silent failure.
  {
    const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
    const cut = file.subarray(0, file.length - 40);
    const out = wav.pcmFromAudio(cut, { sampleRate: 99999 });
    eq(out.sampleRate, 22050, "a truncated file still reports its header rate");
    eq(out.pcm.length, pcm.length - 40, "…and yields the bytes that did arrive");
  }

  // A `fmt `-less WAVE and a `data`-less WAVE both raise rather than guess.
  for (const [label, drop] of [["fmt ", "fmt "], ["data", "data"]]) {
    const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
    const at = drop === "fmt " ? 12 : 36;
    asciiAt(file, at, "junk");
    let threw = null;
    try { wav.pcmFromAudio(file, { sampleRate: 22050 }); } catch (e) { threw = e; }
    ok(threw instanceof wav.AudioBodyError, `a WAVE with no ${label} chunk raises`);
    eq(threw && threw.kind, "unreadable", `…of kind unreadable (no ${label})`);
  }

  // A RIFF that is not WAVE (an AVI) is refused under the shipped `wav` default — decoding it
  // would be full-scale static; under `pcm` it falls through, since the caller asked for
  // headerless bytes.
  {
    const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
    asciiAt(file, 8, "AVI ");
    let aviThrew = null;
    try { wav.pcmFromAudio(file, { sampleRate: 22050 }); } catch (e) { aviThrew = e; }
    eq(aviThrew && aviThrew.kind, "unreadable", "RIFF-but-not-WAVE under wav is REFUSED, not played as noise");
    eq(wav.pcmFromAudio(file, { sampleRate: 22050, format: "pcm" }).container, "raw",
       "…while under pcm it is still not parsed as a WAV");
  }

  // A wild header rate is clamped into the window `voice/cloud.js::decodeCloudTTS` accepts, so a strange
  // header can never produce a payload the browser decoder would refuse.
  {
    const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
    new DataView(file.buffer).setUint32(24, 1, true);
    eq(wav.pcmFromAudio(file, { sampleRate: 22050 }).sampleRate, 3000, "a 1 Hz header is clamped up to 3000");
    new DataView(file.buffer).setUint32(24, 4000000, true);
    eq(wav.pcmFromAudio(file, { sampleRate: 22050 }).sampleRate, 384000, "a 4 MHz header is clamped to 384000");
  }
  {
    const file = wav.writeWav(makePcm(64, 2), { sampleRate: 22050, channels: 2, bitsPerSample: 16 });
    new DataView(file.buffer).setUint16(22, 64, true);
    eq(wav.pcmFromAudio(file, { sampleRate: 22050 }).channels, 8, "a 64-channel header is clamped to 8");
  }
}

/* =========================================================================== *
 * 7. The Python oracle: `tts.py::pcm_from_audio` is what wav.js transcribes
 * =========================================================================== */
{
  let oracle = null;
  try {
    const dir = mkdtempSync(join(tmpdir(), "moxie-wav-"));
    const file = wav.writeWav(makePcm(256, 1), { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
    const path = join(dir, "fixture.wav");
    writeFileSync(path, file);
    oracle = JSON.parse(
      execFileSync("python3", ["-c",
        "import sys,json,hashlib;sys.path.insert(0,'mqtt');" +
        "from moxie_sdk.tts import pcm_from_audio;" +
        "raw=open(sys.argv[1],'rb').read();" +
        "pcm,rate,ch=pcm_from_audio(raw, sample_rate=99999, channels=7);" +
        "print(json.dumps({'len':len(pcm),'rate':rate,'ch':ch,'sha':hashlib.sha256(pcm).hexdigest()}))",
        path,
      ], { cwd: repo, encoding: "utf8" }).trim(),
    );
    const mine = wav.pcmFromAudio(file, { sampleRate: 99999, channels: 7 });
    eq(mine.sampleRate, oracle.rate, "wav.js and tts.py agree on the sample rate");
    eq(mine.channels, oracle.ch, "…on the channel count");
    eq(mine.pcm.length, oracle.len, "…on the PCM byte length");
    const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", mine.pcm))]
      .map((b) => b.toString(16).padStart(2, "0")).join("");
    eq(digest, oracle.sha, "…AND ON EVERY BYTE (sha256 of the PCM)");

    // The JSON-body refusal is the same on both sides too.
    const jsonRefusal = execFileSync("python3", ["-c", [
      "import sys",
      "sys.path.insert(0, 'mqtt')",
      "from moxie_sdk.tts import pcm_from_audio, VoiceServerError",
      "try:",
      "    pcm_from_audio(b'{\"error\": 1}', sample_rate=22050)",
      "    print('no-raise')",
      "except VoiceServerError:",
      "    print('raised')",
    ].join("\n")], { cwd: repo, encoding: "utf8" }).trim();
    eq(jsonRefusal, "raised", "tts.py refuses a JSON body too — the rule is shared, not invented here");
  } catch {
    // No python3 / moxie_sdk here: every assertion above stands on its own.
  }
}

/* =========================================================================== *
 * 8. `writeWav` (the fixtures' writer, and the TTS cache's in product) emits a sound header
 * =========================================================================== */
{
  const pcm = makePcm(10, 1);
  const file = wav.writeWav(pcm, { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
  const view = new DataView(file.buffer);
  eq(String.fromCharCode(...file.subarray(0, 4)), "RIFF", "the writer emits RIFF");
  eq(String.fromCharCode(...file.subarray(8, 12)), "WAVE", "…WAVE");
  eq(view.getUint32(4, true), 36 + pcm.length, "…the right RIFF size");
  eq(view.getUint16(20, true), 1, "…audioFormat 1 (PCM)");
  eq(view.getUint32(28, true), 22050 * 1 * 2, "…the right byte rate");
  eq(view.getUint16(32, true), 2, "…the right block align");
  eq(view.getUint32(40, true), pcm.length, "…and the right data size");
}


/* =========================================================================== *
 * 9. `wavDurationMs`, the ears' duration cap (§4.1): STT bills by DURATION, and lower
 * rates/widths declare more seconds in the same bytes. A header walk, never a decode.
 * =========================================================================== */
{
  /** A WAV of `dataLen` audio bytes at any rate/width (`writeWav` cannot make 8-bit). */
  const wavAt = (rate, ch, bits, dataLen) => {
    const out = new Uint8Array(44 + dataLen);
    const v = new DataView(out.buffer);
    const a = (at, str) => { for (let i = 0; i < str.length; i++) out[at + i] = str.charCodeAt(i); };
    a(0, "RIFF"); v.setUint32(4, 36 + dataLen, true); a(8, "WAVE");
    a(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, ch, true); v.setUint32(24, rate, true);
    v.setUint32(28, Math.floor(rate * ch * bits / 8), true);
    v.setUint16(32, Math.max(1, Math.floor(ch * bits / 8)), true); v.setUint16(34, bits, true);
    a(36, "data"); v.setUint32(40, dataLen, true);
    return out;
  };

  // ---- 9a. The same 500 KB, four ways ------------------------------------- //
  const CAP_BYTES = 500000;
  const audio = CAP_BYTES - 44;
  const rows = [
    [16000, 1, 16,  15624, "16 kHz 16-bit mono — the ONE format §4.1's ~15 s was derived from"],
    [ 8000, 1, 16,  31247, "8 kHz 16-bit mono — the same bytes, TWICE the billable duration"],
    [ 8000, 1,  8,  62495, "8 kHz 8-bit mono — FOUR times, and still a perfectly ordinary WAV"],
    [ 8000, 1,  4, 124989, "8 kHz 4-bit — EIGHT times; the byte cap bought nothing at all"],
    [16000, 2, 16,   7812, "stereo halves it again: channels are counted, not assumed"],
  ];
  for (const [rate, ch, bits, wantMs, why] of rows) {
    const d = wav.wavDurationMs(wavAt(rate, ch, bits, audio));
    eq(d && d.ms, wantMs, `a ${rate} Hz / ${bits}-bit / ${ch}ch WAV at the byte cap — ${why}`);
  }

  // ---- 9b. The fields it reads, and the one it refuses to ----------------- //
  const d = wav.wavDurationMs(wavAt(16000, 1, 16, 32000));
  eq(d.sampleRate, 16000, "the rate comes from nSamplesPerSec");
  eq(d.channels, 1, "…the channel count from nChannels");
  eq(d.bitsPerSample, 16, "…the width from wBitsPerSample");
  eq(d.dataBytes, 32000, "…and the length from the data chunk");
  eq(d.ms, 1000, "…which multiply out to one second");

  // `nAvgBytesPerSec` is the field a hostile file would inflate to under-declare its length.
  const lying = wavAt(8000, 1, 8, audio);
  new DataView(lying.buffer).setUint32(28, 1000000, true); // "1 MB/s", i.e. "half a second"
  ok(wav.wavDurationMs(lying).ms > 60000,
     "a LYING nAvgBytesPerSec is ignored: duration comes from rate x channels x width");

  // ---- 9c. A truncated data chunk is measured on what ARRIVED ------------- //
  const short = wavAt(16000, 1, 16, 32000).subarray(0, 44 + 16000);
  ok(wav.wavDurationMs(short).ms < 600,
     "a data chunk that CLAIMS more than arrived is measured on the bytes actually sent");

  // ---- 9d. Chunks before `fmt `/`data`, which real encoders insert -------- //
  const base = wavAt(16000, 1, 16, 32000);
  const list = new Uint8Array(base.length + 12);
  list.set(base.subarray(0, 12), 0);
  const lv = new DataView(list.buffer);
  for (let i = 0; i < 4; i++) list[12 + i] = "LIST".charCodeAt(i);
  lv.setUint32(16, 4, true);
  list.set(base.subarray(12), 24);
  lv.setUint32(4, list.length - 8, true);
  const dl = wav.wavDurationMs(list);
  ok(dl && dl.ms === 1000, "a LIST chunk before fmt/data is walked past, not tripped over");

  // ---- 9e. NO OPINION (null) is not "short": "could not measure" != "measured and fine".
  eq(wav.wavDurationMs(new Uint8Array(0)), null, "an empty body is not measurable");
  eq(wav.wavDurationMs(new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3, 4, 5, 6, 7, 8])), null,
     "a webm/Matroska body is not measurable — the duration is in a bitstream, not a header");
  eq(wav.wavDurationMs(new TextEncoder().encode("OggS........................")), null,
     "…nor is an Ogg one");
  eq(wav.wavDurationMs(wavAt(0, 1, 16, 32000)), null, "a zero sample rate yields no opinion, not a divide");
  eq(wav.wavDurationMs(wavAt(16000, 1, 0, 32000)), null, "…and neither does a zero bit width");
  const noData = wavAt(16000, 1, 16, 0);
  eq(wav.wavDurationMs(noData.subarray(0, 44)), null, "a WAV with an empty data chunk yields no opinion");
}

/* --------------------------------------------------------------------------- */
if (fails.length) {
  console.error(`✗ test_wav_decode: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log("✓ test_wav_decode: server WAV→PCM and browser PCM→Float32 agree sample for sample");
