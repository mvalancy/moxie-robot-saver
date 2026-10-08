/* functions/api/_lib/wav.js — whatever `/audio/speech` returned -> raw 16-bit PCM.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/speech`), §2.2.
 * The edge transcription of `mqtt/moxie_sdk/tts.py::pcm_from_audio`. The ears' header
 * checks live here too: `wavDurationMs` (the duration cap) and `sttWavProblem` (is this a
 * WAV the gateway's STT can decode at all).
 *
 *   1. SNIFF THE BYTES, NEVER THE CONTENT-TYPE. The gateway labels a valid Piper WAV
 *      `audio/mpeg` (a LiteLLM quirk); branching on the header would play noise.
 *   2. CARRY THE HEADER'S OWN rate and channels out, not the configured ones.
 *      `DEMO_TTS_SAMPLE_RATE` is used only for a headerless raw-PCM reply.
 *   3. AN ERROR BODY IS NEVER HANDED TO A VISITOR AS NOISE. JSON, HTML, a named foreign
 *      container, or any non-RIFF body when `wav` was asked for raises; the route answers
 *      503 `upstream_down`. The headerless branch opens only under `DEMO_TTS_FORMAT=pcm`
 *      (and a caller that does not say gets the strict `wav` reading).
 *
 * Under `pcm`, "is this audio?" is undecidable: an odd length or mostly-printable body is
 * refused, but a short binary error blob would pass. That is why `wav` is the default.
 *
 * 16-bit only: `audio.js` decodes with `getInt16` and no width branch, so an 8/24-bit WAV
 * would play as garbage. It is refused, not converted.
 */

/** The one error this module raises. `message` is server-side only and never reaches a
 *  response body (§4.2). */
export class AudioBodyError extends Error {
  constructor(message, kind) {
    super(message);
    this.name = "AudioBodyError";
    /** `empty` · `json` · `html` · `unreadable` · `bit_depth` — an internal word. */
    this.kind = kind || "unreadable";
  }
}

/** Index of the first non-whitespace byte. */
function skipWs(bytes) {
  let i = 0;
  while (i < bytes.length && (bytes[i] === 0x20 || bytes[i] === 0x09 || bytes[i] === 0x0a || bytes[i] === 0x0d)) i++;
  return i;
}

/** Does this body look like a JSON document rather than audio? A body that starts with
 *  `{` or `[` is not audio, whether or not it parses (a truncated error is still an error). */
function jsonError(bytes) {
  const i = skipWs(bytes);
  if (i >= bytes.length) return null;
  return bytes[i] === 0x7b || bytes[i] === 0x5b ? true : null; // '{' or '['
}

/**
 * Does this body look like an HTML document? A diagnosis: a Cloudflare Access-protected
 * tunnel answers an unauthenticated fetch with an HTML login page at 200, which would
 * otherwise fall through to the raw branch as static.
 */
function htmlBody(bytes) {
  const i = skipWs(bytes);
  if (i >= bytes.length || bytes[i] !== 0x3c) return false; // '<'
  const head = new TextDecoder().decode(bytes.subarray(i, Math.min(bytes.length, i + 512))).toLowerCase();
  return /^<(?:!doctype|html|head|meta|title|\?xml|script|body)\b/.test(head) || head.includes("<html");
}

function fourcc(bytes, at) {
  return String.fromCharCode(bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]);
}

const ascii = (b, at, s) => {
  for (let i = 0; i < s.length; i++) if (b[at + i] !== s.charCodeAt(i)) return false;
  return true;
};

/**
 * Name a container we can RECOGNISE but not decode, by magic number — the same byte tests
 * as `transcribe.js::audioKind`. ONLY exact literal magics: the MPEG frame-sync test
 * (`ff fb…`) is deliberately absent, since raw 16-bit PCM can start with those bytes.
 *
 * @returns {string|null} a word for the server-side message, never for the wire
 */
function foreignContainer(bytes) {
  if (bytes.length < 12) return null;
  if (bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3) return "a webm/Matroska stream";
  if (ascii(bytes, 0, "OggS")) return "an Ogg stream";
  if (ascii(bytes, 0, "fLaC")) return "a FLAC stream";
  if (ascii(bytes, 0, "ID3")) return "an mp3";
  if (ascii(bytes, 4, "ftyp")) return "an MPEG-4 stream";
  return null;
}

/**
 * Is this body, to a first approximation, TEXT? The last guard on the headerless `pcm`
 * path: an SSE `data: {"error":…}` frame or a `text/plain` error slips past `jsonError`
 * and is ~100 % printable, while 16-bit PCM's high bytes never are. Bounded to 8 KB.
 */
function mostlyText(bytes) {
  if (bytes.length < 32) return false;
  const n = Math.min(bytes.length, 8192);
  let printable = 0;
  for (let i = 0; i < n; i++) {
    const b = bytes[i];
    if ((b >= 0x20 && b <= 0x7e) || b === 0x09 || b === 0x0a || b === 0x0d) printable++;
  }
  return printable > n * 0.9;
}

/**
 * `(pcm16, sampleRate, channels)` from whatever an `/audio/speech` call returned.
 *
 * @param {Uint8Array} raw  the response body, verbatim
 * @param {{sampleRate:number, channels?:number, format?:string}} fallback
 *   `sampleRate`/`channels` are used ONLY for a headerless body. `format` is the format
 *   that was ASKED FOR (`DEMO_TTS_FORMAT`, i.e. `cfg.ttsFormat`): only `"pcm"` opens the
 *   headerless branch at all. ABSENT MEANS STRICT — a caller that does not say gets the
 *   `wav` reading, because the fail-safe direction is "refuse", not "play it and see".
 * @returns {{pcm: Uint8Array, sampleRate: number, channels: number, container: string}}
 * @throws {AudioBodyError}
 */
export function pcmFromAudio(raw, fallback) {
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw || 0);
  if (!bytes.length) throw new AudioBodyError("the voice server returned an empty body", "empty");
  if (jsonError(bytes)) throw new AudioBodyError("the voice server returned JSON, not audio", "json");
  // Distinguished because the fix differs: configure the Access token (see `htmlBody`).
  if (htmlBody(bytes)) throw new AudioBodyError("the voice server returned an HTML page, not audio", "html");

  const rate = Math.round(Number(fallback && fallback.sampleRate)) || 22050;
  const ch = Math.round(Number(fallback && fallback.channels)) || 1;
  const format = String((fallback && fallback.format) || "wav").toLowerCase();

  if (bytes.length < 12 || fourcc(bytes, 0) !== "RIFF" || fourcc(bytes, 8) !== "WAVE") {
    // A container we can NAME is never raw PCM, whichever format was asked for.
    const named = foreignContainer(bytes);
    if (named) {
      throw new AudioBodyError("the voice server returned " + named + ", not decodable audio", "unreadable");
    }
    if (format !== "pcm") {
      // Rule 3: `wav` was requested and this is not RIFF. Mapped to `upstream_down`.
      throw new AudioBodyError("non-RIFF body where wav was requested", "unreadable");
    }
    // `DEMO_TTS_FORMAT=pcm`: headerless samples at the CONFIGURED rate, after two cheap
    // sanity guards.
    if (bytes.length % 2 !== 0) {
      throw new AudioBodyError("raw body of odd byte length is not 16-bit PCM", "unreadable");
    }
    if (mostlyText(bytes)) {
      throw new AudioBodyError("raw body is almost entirely printable text, not PCM", "unreadable");
    }
    return { pcm: bytes, sampleRate: rate, channels: ch, container: "raw" };
  }

  const { fmt, data } = walkRiff(bytes, false);
  if (!fmt) throw new AudioBodyError("WAV with no fmt chunk", "unreadable");
  if (!data || data.size <= 0) throw new AudioBodyError("WAV with no data chunk", "unreadable");
  if (fmt.bitsPerSample !== 16) {
    // Deliberately NOT converted (see the header).
    throw new AudioBodyError(
      "the voice server sent " + fmt.bitsPerSample + "-bit WAV; CloudTTSResponse.AudioBuffer is 16-bit PCM",
      "bit_depth",
    );
  }

  const channels = Math.max(1, Math.min(8, fmt.channels || ch));
  // The header's own rate, clamped to the window `audio.js` accepts.
  const sampleRate = Math.max(3000, Math.min(384000, fmt.sampleRate || rate));
  return {
    pcm: bytes.subarray(data.at, data.at + data.size),
    sampleRate,
    channels,
    container: "wav",
  };
}

/**
 * A minimal 16-bit RIFF/WAVE writer. Tests build fixtures with it; `_lib/ttscache.js`
 * wraps decoded PCM in it before storing, so a cache hit decodes through `pcmFromAudio`
 * exactly like a miss.
 */
export function writeWav(pcm, { sampleRate, channels, bitsPerSample }) {
  const bits = bitsPerSample || 16;
  const bytesPerSample = bits >> 3;
  const ch = channels || 1;
  const out = new Uint8Array(44 + pcm.length);
  const view = new DataView(out.buffer);
  const ascii = (at, s) => {
    for (let i = 0; i < s.length; i++) out[at + i] = s.charCodeAt(i);
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + pcm.length, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, ch, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * ch * bytesPerSample, true); // byte rate
  view.setUint16(32, ch * bytesPerSample, true); // block align
  view.setUint16(34, bits, true);
  ascii(36, "data");
  view.setUint32(40, pcm.length, true);
  out.set(pcm, 44);
  return out;
}

/**
 * Walk a RIFF/WAVE chunk list (bytes 12+) rather than assuming the canonical 44-byte
 * layout: encoders insert LIST/INFO/fact chunks, and an odd-sized chunk carries an
 * uncounted pad byte. A truncated `data` chunk is measured on what actually ARRIVED — a
 * header may not buy audio (or duration) the body did not pay bytes for.
 *
 * @param {boolean} firstFmt keep the first `fmt ` chunk (else the last one wins)
 * @returns {{fmt: object|null, data: {at:number, size:number}|null}}
 */
function walkRiff(bytes, firstFmt) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 12;
  let fmt = null;
  let data = null;
  while (pos + 8 <= bytes.length) {
    const id = fourcc(bytes, pos);
    const size = view.getUint32(pos + 4, true);
    const body = pos + 8;
    if (size > bytes.length - body) {
      if (id === "data" && data === null) data = { at: body, size: bytes.length - body };
      break;
    }
    if (id === "fmt " && size >= 16 && !(firstFmt && fmt)) {
      fmt = {
        format: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bitsPerSample: view.getUint16(body + 14, true),
      };
    } else if (id === "data" && data === null) {
      data = { at: body, size };
    }
    pos = body + size + (size % 2); // the RIFF pad byte
  }
  return { fmt, data };
}

/**
 * The playing time a RIFF/WAVE body DECLARES, in ms, or `null` if its header cannot be read.
 *
 * WHY: STT is priced by duration and a byte cap is not a duration cap — 500 KB is ~15 s at
 * 16 kHz/16-bit/mono but ~31 s at 8 kHz/16-bit and ~125 s as 4-bit ADPCM, all legal, and
 * `DEMO_MAX_RECORD_MS` was otherwise enforced only by `mic.js` in the browser.
 * `transcribe.js` refuses `too_long` above the cap.
 *
 * ONLY WAV: compressed containers carry duration in the bitstream, and a Function must
 * not run a decoder on a hostile upload. The cap is total only because
 * `DEMO_STT_FORMATS` defaults to `wav` alone; a fork that widens it re-opens the gap.
 *
 * Computed from rate × channels × bits, NOT the redundant `nAvgBytesPerSec` a hostile file
 * would inflate. `null` means "unknown", never "short". Uses the FIRST `fmt ` chunk and its
 * own walk rather than `pcmFromAudio`, which throws on non-16-bit — the case this catches.
 *
 * @param {Uint8Array|ArrayBuffer} raw
 * @returns {{ms:number, sampleRate:number, channels:number, bitsPerSample:number,
 *            dataBytes:number, formatTag:number}|null}
 */
export function wavDurationMs(raw) {
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw || 0);
  if (bytes.length < 12) return null;
  if (fourcc(bytes, 0) !== "RIFF" || fourcc(bytes, 8) !== "WAVE") return null;

  const { fmt, data } = walkRiff(bytes, true);
  const dataBytes = data ? data.size : null;
  if (!fmt || dataBytes === null || dataBytes <= 0) return null;
  const channels = fmt.channels || 1;
  const bytesPerSecond = (fmt.sampleRate * channels * fmt.bitsPerSample) / 8;
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return null;
  return {
    ms: Math.round((dataBytes / bytesPerSecond) * 1000),
    sampleRate: fmt.sampleRate,
    channels,
    bitsPerSample: fmt.bitsPerSample,
    dataBytes,
    formatTag: fmt.format,
  };
}

/** The WAVs `transcribe.js` forwards: integer PCM (format tag 1), 16-bit, mono or stereo,
 *  8-48 kHz. `mic.js::encodeWav` writes 16 kHz mono; the rest is headroom for a real file. */
export const STT_WAV = Object.freeze({ minRate: 8000, maxRate: 48000, maxChannels: 2 });

/**
 * Why a RIFF/WAVE body is NOT fit for speech-to-text, or `null` when it is.
 *
 * WHY: the gateway's STT answers a body it cannot decode with HTTP 500, and three 500s in a
 * few seconds put the whole STT group into a ~60 s cooldown, so every visitor's microphone
 * fails for a minute. `transcribe.js` therefore forwards only a WAV whose header it can read
 * and whose `fmt ` is plain 16-bit PCM in a sane range (measured to transcribe: 16 and
 * 22.05 kHz mono); anything else is refused for free, before the one upstream call.
 *
 * Reads the FIRST `fmt ` chunk, as `wavDurationMs` does, and its RAW channel count (the
 * duration maths treats 0 as 1; a decoder does not).
 *
 * @param {Uint8Array|ArrayBuffer} raw
 * @returns {string|null} `unreadable` · `format` · `bit_depth` · `channels` · `sample_rate` —
 *   an internal word, never for the wire
 */
export function sttWavProblem(raw) {
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw || 0);
  // No RIFF/WAVE magic, no `fmt `, no audio, or a zero rate or width.
  if (!wavDurationMs(bytes)) return "unreadable";
  const { fmt } = walkRiff(bytes, true);
  if (fmt.format !== 1) return "format";
  if (fmt.bitsPerSample !== 16) return "bit_depth";
  if (fmt.channels < 1 || fmt.channels > STT_WAV.maxChannels) return "channels";
  if (fmt.sampleRate < STT_WAV.minRate || fmt.sampleRate > STT_WAV.maxRate) return "sample_rate";
  return null;
}
