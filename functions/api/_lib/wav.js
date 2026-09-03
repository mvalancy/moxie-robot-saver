/* functions/api/_lib/wav.js — whatever `/audio/speech` returned -> raw 16-bit PCM.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/speech`), §2.2 (the
 * gateway lies about its Content-Type).
 *
 * THE ONE RULE, and it is not a style preference: **SNIFF THE BYTES, NEVER THE
 * CONTENT-TYPE.** Our gateway labels a perfectly good Piper WAV `audio/mpeg` — a LiteLLM
 * quirk observed live on 2026-09-02 and written down twice, at
 * `mqtt/moxie_sdk/tts.py`:110-125 and `docs/guides/litellm-tts-setup.md`:58-60. A client
 * that branched on the header would ship an MP3 decoder at a RIFF file and play noise, or
 * nothing, to a child. This module is the edge transcription of `tts.py::pcm_from_audio`
 * (:110-145) — cited, not imported, because a Pages Function cannot import Python and
 * `wave` does not exist here.
 *
 * THE SECOND RULE: **carry the header's OWN rate and channels out**, not the configured
 * ones. That is how a `CloudTTSResponse` stays truthful when the voice — and with it the
 * sample rate — changes under us. `DEMO_TTS_SAMPLE_RATE` is consulted only for a raw-PCM
 * reply, which has no header to ask (§5).
 *
 * THE THIRD RULE: **an error body is never handed to a visitor as noise.** A proxy
 * answering 200-with-JSON, or an unknown-model 400 surfaced as bytes, raises here and the
 * route answers 503 `upstream_down` (§3.2). The alternative is a page that plays a
 * half-second of static, which is the failure mode this whole contract exists to prevent.
 *
 * 16-bit only. `CloudTTSResponse.AudioBuffer` is 16-bit PCM and `audio.js`'s decoder reads
 * `getInt16` with no width branch (`audio.js`:641-683), so an 8- or 24-bit WAV would play
 * as garbage rather than fail. Refusing it is the honest outcome.
 */

/** The one error this module raises. `reason` maps onto §3.2's closed reason set; the
 *  `message` is for a server-side comment only and never reaches a response body — the
 *  route builds its own visitor-facing copy (§4.2). */
export class AudioBodyError extends Error {
  constructor(message, kind) {
    super(message);
    this.name = "AudioBodyError";
    /** `empty` · `json` · `unreadable` · `bit_depth` — an internal word. */
    this.kind = kind || "unreadable";
  }
}

/** Does this body look like a JSON document rather than audio? Cheap prefix sniff first
 *  (a 268 KB PCM buffer must not be run through JSON.parse on every call), then a real
 *  parse of a bounded prefix to be sure. */
function jsonError(bytes) {
  let i = 0;
  while (i < bytes.length && (bytes[i] === 0x20 || bytes[i] === 0x09 || bytes[i] === 0x0a || bytes[i] === 0x0d)) i++;
  if (i >= bytes.length) return null;
  if (bytes[i] !== 0x7b && bytes[i] !== 0x5b) return null; // '{' or '['
  try {
    // 8 KB is far more than any gateway error body and far less than any audio buffer.
    JSON.parse(new TextDecoder().decode(bytes.subarray(i, Math.min(bytes.length, i + 8192))));
    return true;
  } catch {
    // It began with a brace and did not parse: a truncated JSON error is still not audio.
    return true;
  }
}

function fourcc(bytes, at) {
  return String.fromCharCode(bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]);
}

/**
 * `(pcm16, sampleRate, channels)` from whatever an `/audio/speech` call returned.
 *
 * @param {Uint8Array} raw          the response body, verbatim
 * @param {{sampleRate:number, channels?:number}} fallback  used ONLY for a non-RIFF body
 * @returns {{pcm: Uint8Array, sampleRate: number, channels: number, container: string}}
 * @throws {AudioBodyError}
 */
export function pcmFromAudio(raw, fallback) {
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw || 0);
  if (!bytes.length) throw new AudioBodyError("the voice server returned an empty body", "empty");
  if (jsonError(bytes)) throw new AudioBodyError("the voice server returned JSON, not audio", "json");

  const rate = Math.round(Number(fallback && fallback.sampleRate)) || 22050;
  const ch = Math.round(Number(fallback && fallback.channels)) || 1;

  if (bytes.length < 12 || fourcc(bytes, 0) !== "RIFF" || fourcc(bytes, 8) !== "WAVE") {
    // Not RIFF: this is the raw PCM we asked for (`DEMO_TTS_FORMAT=pcm`), at the
    // CONFIGURED rate — the only case where the configured rate is the right answer.
    return { pcm: bytes, sampleRate: rate, channels: ch, container: "raw" };
  }

  // Walk the chunk list rather than assuming the canonical 44-byte layout: real encoders
  // insert LIST/INFO/fact chunks, and an odd-sized chunk is padded to an even boundary
  // (RIFF requires the pad byte, and it is NOT counted in the chunk size).
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let pos = 12;
  let fmt = null;
  let data = null;
  while (pos + 8 <= bytes.length) {
    const id = fourcc(bytes, pos);
    const size = view.getUint32(pos + 4, true);
    const body = pos + 8;
    if (size > bytes.length - body) {
      // A truncated final chunk. `data` is usable up to what actually arrived; anything
      // else is unreadable.
      if (id === "data" && data === null) data = { at: body, size: bytes.length - body };
      break;
    }
    if (id === "fmt " && size >= 16) {
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

  if (!fmt) throw new AudioBodyError("WAV with no fmt chunk", "unreadable");
  if (!data || data.size <= 0) throw new AudioBodyError("WAV with no data chunk", "unreadable");
  if (fmt.bitsPerSample !== 16) {
    // Deliberately NOT converted. See the header: the browser decoder has no width branch,
    // so a silent conversion bug here would be inaudible to us and audible to a child.
    throw new AudioBodyError(
      "the voice server sent " + fmt.bitsPerSample + "-bit WAV; CloudTTSResponse.AudioBuffer is 16-bit PCM",
      "bit_depth",
    );
  }

  const channels = Math.max(1, Math.min(8, fmt.channels || ch));
  // The header's own rate, clamped to the window `audio.js`:617-618 will accept, so a
  // strange header can never produce a payload the browser decoder would refuse.
  const sampleRate = Math.max(3000, Math.min(384000, fmt.sampleRate || rate));
  return {
    pcm: bytes.subarray(data.at, data.at + data.size),
    sampleRate,
    channels,
    container: "wav",
  };
}

/**
 * A minimal 16-bit RIFF/WAVE writer. NOT used by any route — the routes only ever read —
 * but the tests need to synthesize the exact bodies a gateway sends, and a writer written
 * next to the reader is a writer that pins the same field offsets. `sim/test_wav_decode.mjs`
 * builds its fixtures with it and then feeds the result through `audio.js`'s real decoder,
 * which is how one test pins both halves of the contract with no server.
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
