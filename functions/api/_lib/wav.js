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
 * That rule used to be an OVERCLAIM, and the fix is the reason `format` exists on this
 * function's second argument. Until 2026-09-03 the non-RIFF branch was reached under EVERY
 * configuration, so the module's guarantee held for exactly three shapes — empty, `{`/`[`,
 * and `<` — and a 200 that was none of them (`text/plain`, an SSE `data: {"error":…}`
 * frame whose `data: ` prefix defeats the `{` sniff, an `ID3` mp3, a webm EBML header) was
 * returned as `container:"raw"`, base64'd and shipped to the visitor at status 200 with
 * `degraded:false`. §5 of the spec restricts `DEMO_TTS_FORMAT` to `wav` (the shipped
 * default) or `pcm`, and the raw branch is only ever CORRECT under `pcm` — where the body
 * genuinely has no header to read (spec §3.2's "anything else → treat as raw PCM"). So the
 * branch is now gated on the format that was actually ASKED FOR, and a caller that does
 * not say gets the strict reading. An mp3 from a gateway that quietly ignored
 * `response_format` is not a leak but it is still full-scale static in a child's ear,
 * which is the same harm from the other direction.
 *
 * WHAT THE RULE DOES **NOT** COVER, said plainly: under `DEMO_TTS_FORMAT=pcm` there is no
 * header and no magic number, so "is this audio?" is undecidable. Two cheap sanity guards
 * run there (an odd byte length is not 16-bit PCM; a body that is almost entirely
 * printable ASCII is text) and a known container is named and refused, but a short binary
 * error blob would still pass. `wav` is the default for that reason.
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
    /** `empty` · `json` · `html` · `unreadable` · `bit_depth` — an internal word. */
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

/**
 * Does this body look like an HTML document?
 *
 * This one is not about audio formats at all — it is a DIAGNOSIS. The gateway is expected
 * to sit behind a Cloudflare Tunnel, and a tunnel protected by Cloudflare Access answers
 * an unauthenticated server-side fetch with an HTML LOGIN PAGE carrying a 200 status. An
 * HTML page is not RIFF, so without this check it would fall through to the raw-PCM branch
 * and a child would hear several seconds of loud static made out of markup. Byte-sniffed
 * like everything else here (`<` first, after whitespace), so no Content-Type is trusted.
 */
function htmlBody(bytes) {
  let i = 0;
  while (i < bytes.length && (bytes[i] === 0x20 || bytes[i] === 0x09 || bytes[i] === 0x0a || bytes[i] === 0x0d)) i++;
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
 * Name a container we can RECOGNISE but not decode, by magic number.
 *
 * The same shape as `transcribe.js::audioKind` (:222-250) and deliberately not a second
 * invention of it — that route sniffs a VISITOR's upload against an allowlist, this one
 * sniffs a GATEWAY's reply against a denylist, but "sniff the bytes, never the
 * Content-Type" is one rule and the byte tests are the same tests.
 *
 * ONLY exact literal magics are used. `transcribe.js`'s loosest test — an MPEG frame sync,
 * `bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0` — is deliberately ABSENT: raw 16-bit
 * PCM starts `ff fb` any time its first sample is near -1 300, so that test would refuse
 * perfectly good audio under `DEMO_TTS_FORMAT=pcm`. A 4-byte literal collides with real
 * PCM at about 2^-32, which is a rate we can live with; a 11-bit one does not.
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
 * Is this body, to a first approximation, TEXT?
 *
 * The last line of defence on the `pcm` path, where there is no header and no magic number
 * to read. An SSE frame (`data: {"error":…}`) and a `text/plain` proxy error both slip past
 * `jsonError` — the `data: ` prefix means the `{` sniff never fires — and both are ~100 %
 * printable. 16-bit PCM is not: every other byte is a sample's HIGH byte, which sits at
 * 0x00/0xff for quiet audio and spreads over the whole range for loud audio, so even a
 * pathological signal lands far under this threshold. Bounded to 8 KB like `jsonError`, and
 * only consulted on bodies long enough for the ratio to mean anything.
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
  // An HTML body where audio was expected is almost always an Access login page in front
  // of the tunnel — see `htmlBody`. Distinguished from every other failure because the fix
  // is completely different: configure the service token, do not restart the gateway.
  if (htmlBody(bytes)) throw new AudioBodyError("the voice server returned an HTML page, not audio", "html");

  const rate = Math.round(Number(fallback && fallback.sampleRate)) || 22050;
  const ch = Math.round(Number(fallback && fallback.channels)) || 1;
  const format = String((fallback && fallback.format) || "wav").toLowerCase();

  if (bytes.length < 12 || fourcc(bytes, 0) !== "RIFF" || fourcc(bytes, 8) !== "WAVE") {
    // A container we can NAME is never raw PCM, whichever format was asked for. Under
    // `wav` it is a gateway that ignored `response_format`; under `pcm` it is the same
    // gateway ignoring the same field. Base64'ing either as "PCM" plays full-scale static.
    const named = foreignContainer(bytes);
    if (named) {
      throw new AudioBodyError("the voice server returned " + named + ", not decodable audio", "unreadable");
    }
    if (format !== "pcm") {
      // THE THIRD RULE, now actually enforced. `wav` was requested and this is not RIFF:
      // it is an error body, a container we do not know, or a gateway that ignored
      // `response_format` — and not one of those is something a child should hear. The
      // route maps `unreadable` onto `upstream_down` and the page degrades (§3.2, §4.5).
      throw new AudioBodyError("non-RIFF body where wav was requested", "unreadable");
    }
    // `DEMO_TTS_FORMAT=pcm`: headerless samples at the CONFIGURED rate — the only case
    // where the configured rate is the right answer (spec §3.2, §5). Two cheap sanity
    // guards, because there is no header here to be wrong about.
    if (bytes.length % 2 !== 0) {
      throw new AudioBodyError("raw body of odd byte length is not 16-bit PCM", "unreadable");
    }
    if (mostlyText(bytes)) {
      throw new AudioBodyError("raw body is almost entirely printable text, not PCM", "unreadable");
    }
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
