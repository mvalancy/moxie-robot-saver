/* hosted_mic/score.mjs — the pure maths behind `sim/check_hosted_mic.mjs`: transcript
 * overlap, the waveform identity vote, the scorer proof, the degradation gauntlet, and WAV
 * I/O. No browser, no clock, no network — identical on every machine.
 */

/* ════════════════════════ transcript maths ════════════════════════════════════ *
 * A PORT of `sim/tests/helpers_audio.py::word_overlap` (same regex, same curly-apostrophe
 * normalisation, MULTISET RECALL): a stray "um" is fine, a dropped half-sentence is not, and
 * the looping fake microphone legitimately repeats the sentence. Floors are that file's. */
export const STT_FLOOR = 0.7;
export const DECOY_CEIL = 0.35;

const WORD_RE = /[a-z0-9']+/g;
export function normalizeWords(text) {
  return String(text || "").toLowerCase().replace(/’/g, "'").match(WORD_RE) || [];
}
export function wordOverlap(reference, hypothesis) {
  const ref = normalizeWords(reference);
  if (!ref.length) return 0;
  const pool = new Map();
  for (const w of normalizeWords(hypothesis)) pool.set(w, (pool.get(w) || 0) + 1);
  let hits = 0;
  for (const w of ref) {
    const n = pool.get(w) || 0;
    if (n > 0) { pool.set(w, n - 1); hits++; }
  }
  return hits / ref.length;
}

/** Clause 4 — its own function so `--selftest` can watch it FAIL on a reference nobody said. */
export function assertWords(c, heard, { spoken, decoy, where }) {
  const right = wordOverlap(spoken, heard);
  const wrong = wordOverlap(decoy, heard);
  c.ok(String(heard || "").trim().length > 0,
       `${where}: the route returned an EMPTY transcript for real speech`);
  c.ok(right >= STT_FLOOR,
       `${where}: recovered only ${right.toFixed(2)} of the words (floor ${STT_FLOOR})\n` +
       `        said : ${JSON.stringify(spoken)}\n        heard: ${JSON.stringify(heard)}`);
  c.ok(wrong < DECOY_CEIL,
       `${where}: scored ${wrong.toFixed(2)} against a sentence that was NEVER SPOKEN ` +
       `(ceiling ${DECOY_CEIL}) — the overlap measure is not discriminating, so the floor ` +
       `above proves nothing\n        decoy: ${JSON.stringify(decoy)}\n` +
       `        heard: ${JSON.stringify(heard)}`);
  return { right, wrong };
}

/* ════════════════════════ waveform maths ══════════════════════════════════════ *
 * "Is this the recording we played", through a path that resamples, runs AGC/noise
 * suppression and decimates — so compare ENERGY ENVELOPES, not samples. Shape of the measure,
 * each part forced by a CI failure on a saturating runner:
 *   · RMS, not peak, per 10 ms frame, and its LOG: a compressor is roughly a gain, a gain is
 *     an offset in log space, and Pearson removes offsets;
 *   · ~1 s chunks each matched ANYWHERE in the (tiled) template, so dropped blocks cannot
 *     break one global alignment;
 *   · a VOTE of chunks (played beats unrelated), never a magnitude: both halves come from one
 *     recording on one machine, so an environment degrades them together. Measured over nine
 *     modelled conditions: played 0.750-0.875, decoy 0.208-0.429.
 */
/** Frames per second of the envelope. 10 ms is finer than anything that matters here. */
const ENV_HZ = 100;

/** RMS energy per frame, 0..1 (RMS survives clipping; peak is a flat top). */
function envelope(pcm16, rate, hz = ENV_HZ) {
  const hop = Math.max(1, Math.round(rate / hz));
  const out = [];
  for (let i = 0; i + hop <= pcm16.length; i += hop) {
    let s = 0;
    for (let j = i; j < i + hop; j++) { const v = pcm16[j] / 32768; s += v * v; }
    out.push(Math.sqrt(s / hop));
  }
  return out;
}

/** Log of the envelope — saturation-proof; the epsilon puts silence at -4, not -Infinity. */
const logEnv = (e) => e.map((v) => Math.log10(v + 1e-4));

/** Pearson correlation of `a` against `b[at … at+a.length]`, or -1 where either is flat. */
function corrAt(a, b, at) {
  const n = a.length;
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[at + i]; }
  ma /= n; mb /= n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - ma, y = b[at + i] - mb;
    num += x * y; da += x * x; db += y * y;
  }
  if (da <= 0 || db <= 0) return -1;
  return num / Math.sqrt(da * db);
}

/** The best score for one chunk anywhere in `template`. */
function bestIn(chunk, template) {
  let best = -1;
  for (let j = 0; j + chunk.length <= template.length; j++) {
    const v = corrAt(chunk, template, j);
    if (v > best) best = v;
  }
  return best;
}

/** Score the capture against BOTH templates, chunk by chunk.
 * @returns {{good:number, bad:number, margin:number, vote:number, chunks:number}} */
export function score(capturePcm, captureRate, ctx) {
  const sig = logEnv(envelope(capturePcm, captureRate));
  const a0 = logEnv(envelope(ctx.source, ctx.sourceRate));
  const b0 = logEnv(envelope(ctx.decoyPcm, ctx.decoyRate));
  const shortest = Math.min(a0.length, b0.length, sig.length);
  const L = Math.min(ENV_HZ, Math.max(20, Math.floor(shortest * 0.6)));
  const stride = Math.max(1, Math.round(L / 2));
  const none = { good: -1, bad: -1, margin: 0, vote: -1, chunks: 0 };
  if (sig.length < L || a0.length < L || b0.length < L) return none;
  /* TILED templates: the fake device loops the file, so a chunk that straddles a loop seam
   * has no home in one copy; tiling BOTH keeps their position counts equal. */
  const A = a0.concat(a0), B = b0.concat(b0);
  const ga = [], gb = [];
  let win = 0, n = 0;
  for (let i = 0; i + L <= sig.length; i += stride) {
    const chunk = sig.slice(i, i + L);
    const a = bestIn(chunk, A), b = bestIn(chunk, B);
    ga.push(a); gb.push(b);
    if (a > b) win++;
    n++;
  }
  if (!n) return none;
  const med = (x) => { const y = x.slice().sort((p, q) => p - q); return y[Math.floor(y.length / 2)]; };
  const good = med(ga), bad = med(gb);
  return { good, bad, margin: good - bad, vote: win / n, chunks: n };
}

/** The vote clause 3 asserts everywhere — between the worst true positive (0.750) and worst
 *  inversion (0.429), deliberately not a coin-flip 0.5. */
export const IDENTITY_VOTE = 0.60;

/** The absolute fidelity magnitude — asserted only where the audio path is a known quantity
 *  (`--dry-run`, the paid run); a CI runner's microphone saturates and halves it. */
export const FIDELITY_FLOOR = 0.60;

/* ═══════════════ the scorer proof — what actually gates every push ════════════ *
 * The same scorer over COMMITTED FIXTURES, each fed in as though it were the capture. It
 * replaced browser-capture identity as the push gate after a runner's capture let the decoy
 * vote 71 % for the sentence — unreproducible anywhere else. Proves the MEASURE still
 * discriminates; says nothing about a browser capture's fidelity. */
export function scorerProof(c, fx) {
  const vsDecoy = { source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
                    decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate };
  const goldenVsDecoy = { source: fx.golden.pcm, sourceRate: fx.golden.rate,
                          decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate };
  const PROOFS = [
    ["the sentence fixture", fx.spoken.pcm, fx.spoken.rate, vsDecoy, true],
    ["the DECOY fixture (must lose)", fx.decoy.pcm, fx.decoy.rate, vsDecoy, false],
    ["the committed golden", fx.golden.pcm, fx.golden.rate, goldenVsDecoy, true],
  ];
  console.log(`\n  the scorer, over committed fixtures — no browser, no device, no machine variance`);
  console.log(`    capture                          vote   played  unrelated   verdict`);
  for (const [name, pcm, rate, ctx, shouldWin] of PROOFS) {
    const r = score(pcm, rate, ctx);
    const won = r.vote >= IDENTITY_VOTE;
    console.log(`    ${name.padEnd(32)} ${(r.vote * 100).toFixed(0).padStart(3)}%   ` +
                `${r.good.toFixed(3)}    ${r.bad.toFixed(3)}    ` +
                `${won === shouldWin ? "as expected ✓" : "WRONG ✗"}`);
    c.ok(won === shouldWin,
         `scorer proof "${name}": expected the scorer to ${shouldWin ? "PICK" : "REJECT"} ` +
         `the reference clip; it voted ${(r.vote * 100).toFixed(0)}% over ${r.chunks} chunks ` +
         `(threshold ${(IDENTITY_VOTE * 100).toFixed(0)}%; medians ${r.good.toFixed(3)} ` +
         `played vs ${r.bad.toFixed(3)} unrelated)`);
  }
}

/* ════════════════ the degradation gauntlet ════════════════════════════════════ *
 * The committed fixture degraded the way a runner degrades a capture; for each, the played
 * clip must still win the vote AND the same audio must FAIL with the templates swapped. */
const DEGRADATIONS = [
  ["as captured", (f) => f],
  ["saturated (the runner's own microphone)", (f) => compress(f, 0.30)],
  ["hard saturated", (f) => compress(f, 0.60)],
  // A starved main thread: ScriptProcessor skips whole 4096-frame blocks (a time warp).
  ["5% of ScriptProcessor blocks dropped", (f) => dropBlocks(f, 0.05)],
  ["15% dropped", (f) => dropBlocks(f, 0.15)],
  /* Half the evidence (~12 chunks, not ~24): must still CHOOSE the right clip (0.5), not clear
   * the full bar. The swapped control below is still held to the full bar. */
  ["30% dropped + saturated", (f) => compress(dropBlocks(f, 0.30), 0.30), 0.5],
  ["very quiet input", (f) => scale(f, 0.05)],
];

/** A compressor with attack/release, clipping at full scale. */
function compress(pcm, target) {
  const out = new Int16Array(pcm.length);
  const win = Math.round(0.02 * 16000);
  let g = 1;
  for (let i = 0; i < pcm.length; i += win) {
    const end = Math.min(pcm.length, i + win);
    let r = 0;
    for (let j = i; j < end; j++) { const v = pcm[j] / 32768; r += v * v; }
    r = Math.sqrt(r / Math.max(1, end - i));
    const want = r > 1e-4 ? Math.min(30, target / r) : g;
    g = g + (want - g) * (want > g ? 0.5 : 0.15);
    for (let j = i; j < end; j++)
      out[j] = Math.max(-32768, Math.min(32767, Math.round(pcm[j] * g)));
  }
  return out;
}

/** Whole 4096-frame blocks never delivered, so what remains is spliced together. */
function dropBlocks(pcm, rate) {
  const keep = [];
  for (let i = 0; i < pcm.length; i += 4096)
    if (((i / 4096) % Math.max(2, Math.round(1 / rate))) !== 0)
      keep.push(pcm.subarray(i, Math.min(pcm.length, i + 4096)));
  const total = keep.reduce((a, b) => a + b.length, 0);
  const out = new Int16Array(total);
  let at = 0;
  for (const k of keep) { out.set(k, at); at += k.length; }
  return out;
}

/** The fixture LOOPED to `seconds`, as the fake device delivers it — enough chunks survive a
 *  30 % drop for the vote to mean something. */
export function loopTo(pcm, rate, seconds) {
  const n = Math.round(seconds * rate);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) out[i] = pcm[i % pcm.length];
  return out;
}

/** Peak |sample| of int16 PCM, 0..1. */
export function peakOf(pcm) {
  let pk = 0;
  for (let i = 0; i < pcm.length; i++) { const v = Math.abs(pcm[i]); if (v > pk) pk = v; }
  return pk / 32768;
}

function scale(pcm, g) {
  const out = new Int16Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = Math.round(pcm[i] * g);
  return out;
}

export function gauntlet(c, pcm, rate, ctx) {
  console.log(`\n  the degradation gauntlet — the CI failure mode, over the committed fixture`);
  console.log(`    degradation                              peak    vote          played  unrelated  swapped`);
  const swapped = { source: ctx.decoyPcm, sourceRate: ctx.decoyRate,
                    decoyPcm: ctx.source, decoyRate: ctx.sourceRate };
  for (const [name, fn, floor] of DEGRADATIONS) {
    const want = floor === undefined ? IDENTITY_VOTE : floor;
    const d = fn(pcm);
    const pk = peakOf(d);
    const got = score(d, rate, ctx);
    const inv = score(d, rate, swapped);
    /* The swapped control is always held to the FULL bar, whatever this row expects of the
     * real one — a mutation judged by a lowered standard is not a mutation. */
    const swapFails = inv.vote < IDENTITY_VOTE;
    console.log(`    ${name.padEnd(40)} ${pk.toFixed(3)}   ${(got.vote * 100).toFixed(0).padStart(3)}%` +
                ` (need ${(want * 100).toFixed(0)}%)  ${got.good.toFixed(3)}    ${got.bad.toFixed(3)}` +
                `   ${swapFails ? "reddens ✓" : "PASSES ✗"}`);
    c.ok(got.vote >= want,
         `gauntlet "${name}": the identity clause must survive it — only ` +
         `${(got.vote * 100).toFixed(0)}% of ${got.chunks} chunks chose the clip played ` +
         `(need ${(want * 100).toFixed(0)}%)`);
    c.ok(swapFails,
         `gauntlet "${name}": with the templates SWAPPED the same audio must FAIL — ` +
         `${(inv.vote * 100).toFixed(0)}% of chunks still chose the "played" clip, so the ` +
         `vote above is not coming from the audio`)
  }
}

/* ════════════════════════ WAV I/O ════════════════════════════════════════════ */
/** Mono PCM16 → a complete RIFF/WAVE file. The one place this file writes a header. */
export function riff(pcm16, rate) {
  const n = pcm16.length;
  const b = Buffer.alloc(44 + n * 2);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n * 2, 4); b.write("WAVE", 8);
  b.write("fmt ", 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) b.writeInt16LE(Math.max(-32768, Math.min(32767, pcm16[i])), 44 + i * 2);
  return b;
}

/** Mono PCM16 out of a RIFF/WAVE file, chunk-walked rather than assuming a 44-byte header. */
export function readWav(buf) {
  if (buf.slice(0, 4).toString() !== "RIFF" || buf.slice(8, 12).toString() !== "WAVE")
    throw new Error("not a RIFF/WAVE file");
  let at = 12, rate = 0, channels = 1, bits = 16, data = null;
  while (at + 8 <= buf.length) {
    const id = buf.slice(at, at + 4).toString();
    const size = buf.readUInt32LE(at + 4);
    if (id === "fmt ") {
      channels = buf.readUInt16LE(at + 10); rate = buf.readUInt32LE(at + 12);
      bits = buf.readUInt16LE(at + 22);
    } else if (id === "data") {
      data = buf.slice(at + 8, Math.min(buf.length, at + 8 + size));
    }
    at += 8 + size + (size % 2);
  }
  if (!data || bits !== 16) throw new Error(`unsupported WAV (bits=${bits})`);
  const n = Math.floor(data.length / 2 / channels);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = data.readInt16LE(i * 2 * channels);   // channel 0
  return { pcm, rate };
}

/** `pcm` with `padS` seconds of silence each end, so a late capture still gets the first word. */
export function padded(pcm, rate, padS = 0.35) {
  const pad = Math.round(padS * rate);
  const out = new Int16Array(pad * 2 + pcm.length);
  out.set(pcm, pad);
  return out;
}

