/* Part A §A-DUR / A-RDR: the WAV duration ceiling enforced server-side, and the credential
 * never chasing a redirect.
 */
import {
  FULL, call, clip, envmod, eq, fresh, limits, ok, route, sent, setPlan, upstreamCalls, wavlib,
} from "./harness.mjs";

/* A-DUR. STT is billed by DURATION: 500 KB is 15 s at 16 kHz 16-bit but 62 s at 8 kHz 8-bit.
 * A RIFF header declares its playing time, so for WAV the cap is server-side (§4.1, §4.5). */
{
  /** A WAV of a chosen rate/width/length (`wav.writeWav` only emits 16-bit). */
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
    for (let i = 44; i < out.length; i++) out[i] = i & 0xff;
    return out;
  };
  const cfg = envmod.readConfig(FULL);
  eq(cfg.maxRecordMs, 15000, "the block is calibrated to the shipped DEMO_MAX_RECORD_MS");

  // Every width and rate that buys extra seconds inside the same 480 KB byte budget.
  for (const [rate, bits, label] of [[8000, 16, "8 kHz 16-bit (31 s)"], [8000, 8, "8 kHz 8-bit (62 s)"],
                                     [8000, 4, "8 kHz 4-bit (125 s)"], [4000, 8, "4 kHz 8-bit (125 s)"]]) {
    fresh();
    const clipN = wavAt(rate, 1, bits, 480000);
    ok(clipN.length < cfg.maxAudioBytes, `${label}: the hostile clip is INSIDE DEMO_MAX_AUDIO_BYTES`);
    const r = await call(clipN, null, FULL, label);
    eq(r.res.status, 400, `${label}: 400`);
    eq(r.body.reason, "too_long", `${label} under the byte cap is refused on DURATION`);
    eq(upstreamCalls(), 0, `${label}: zero upstream calls`);
  }

  fresh();
  const fine = wavAt(16000, 1, 16, 16000 * 2 * 5);  // 5 seconds, the shape `mic.js` encodes
  eq((await call(fine, null, FULL, "an honest 5-second 16 kHz clip")).res.status, 200,
     "an honest clip inside the ceiling is transcribed as before");

  // The boundary, both sides of it, so the comparison is `>` and not `>=` by accident.
  fresh();
  const exact = wavAt(16000, 1, 16, 16000 * 2 * 15);   // exactly DEMO_MAX_RECORD_MS
  eq(wavlib.wavDurationMs(exact).ms, cfg.maxRecordMs, "the boundary clip is exactly at the cap");
  eq((await call(exact, null, FULL, "exactly at the cap")).res.status, 200, "AT the cap is allowed");
  fresh();
  const over = wavAt(16000, 1, 16, 16000 * 2 * 15 + 3200);  // +100 ms
  eq((await call(over, null, FULL, "100 ms over the cap")).body.reason, "too_long", "just OVER the cap is not");

  fresh();
  const SHORT_CAP = { ...FULL, DEMO_MAX_RECORD_MS: "3000" };
  eq((await call(fine, null, SHORT_CAP, "5 s against a 3 s cap")).body.reason, "too_long",
     "DEMO_MAX_RECORD_MS is what the check reads, so a fork can tighten it with no code change");
  eq(upstreamCalls(), 0, "…still with zero upstream calls");

  // The residual gap, asserted rather than hoped: a webm's duration is unknowable, so only
  // the `DEMO_STT_FORMATS=wav` default keeps it out; widening it forwards an unbounded clip.
  fresh();
  eq(wavlib.wavDurationMs(clip(480000, "webm")), null,
     "a webm body yields NO duration: the honest answer, and the limit of this fix");
  const webm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, FULL, "a 480 KB webm");
  eq(webm.body.reason, "bad_request",
     "…and it is refused by the CONTAINER allowlist instead, which is what closes the gap today");
  eq(upstreamCalls(), 0, "…for free");
  const WIDE = { ...FULL, DEMO_STT_FORMATS: "wav,webm" };
  fresh();
  const wideWebm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, WIDE, "webm, allowlisted");
  eq(wideWebm.res.status, 200,
     "…and then a 480 KB webm of UNKNOWN duration is forwarded — the residual gap, stated not hidden");
}

/* A-FMT. A WAV the gateway cannot decode is never forwarded. Its STT answers one with HTTP 500,
 * and three 500s within seconds put the STT group into a ~60 s cooldown for EVERY visitor
 * (review lane l4, measured). The header used to be NO OPINION when unreadable; now it must
 * read as 16-bit integer PCM, 1-2 channels, 8-48 kHz, or the clip is a free `bad_request`. */
{
  /** A WAV with every fmt field chosen; `fmtSize` 18 is the cbSize=0 form some encoders write. */
  const wavOf = ({ tag = 1, ch = 1, rate = 16000, bits = 16, ms = 1000, fmtSize = 16 }) => {
    const dataLen = Math.round((rate * Math.max(ch, 1) * bits / 8) * ms / 1000);
    const out = new Uint8Array(28 + fmtSize + dataLen);
    const v = new DataView(out.buffer);
    const a = (at, str) => { for (let i = 0; i < str.length; i++) out[at + i] = str.charCodeAt(i); };
    a(0, "RIFF"); v.setUint32(4, out.length - 8, true); a(8, "WAVE");
    a(12, "fmt "); v.setUint32(16, fmtSize, true);
    v.setUint16(20, tag, true); v.setUint16(22, ch, true); v.setUint32(24, rate, true);
    v.setUint32(28, Math.floor(rate * ch * bits / 8), true);
    v.setUint16(32, Math.max(1, Math.floor(ch * bits / 8)), true); v.setUint16(34, bits, true);
    a(20 + fmtSize, "data"); v.setUint32(24 + fmtSize, dataLen, true);
    for (let i = 28 + fmtSize; i < out.length; i++) out[i] = i & 0xff;
    return out;
  };
  /** The shape the old `clip()` fixture had: RIFF/WAVE magic, then junk where `fmt ` belongs. */
  const junkFmt = (n) => {
    const b = new Uint8Array(n);
    for (let i = 12; i < n; i++) b[i] = i & 0xff;
    b.set([0x52, 0x49, 0x46, 0x46], 0);
    b.set([0x57, 0x41, 0x56, 0x45], 8);
    return b;
  };
  const noFmt = wavOf({});
  noFmt.set([0x4a, 0x55, 0x4e, 0x4b], 12);       // "JUNK", a chunk any walker skips
  const noData = wavOf({});
  noData.set([0x4a, 0x55, 0x4e, 0x4b], 36);
  const zeroRate = wavOf({});                    // a second of audio, then the rate zeroed
  new DataView(zeroRate.buffer).setUint32(24, 0, true);

  fresh();
  const junk = await call(junkFmt(4000), null, FULL, "a RIFF/WAVE with a junk fmt chunk");
  eq(junk.res.status, 400, "a RIFF/WAVE whose fmt chunk is junk is a 400");
  eq(junk.body.reason, "bad_request", "…bad_request, the per-turn refusal that degrades nothing");
  eq(upstreamCalls(), 0, "…with ZERO upstream calls: never a gateway 500, never a cooldown strike");
  ok(limits.__state().stats.refundedUnits > 0, "…and the units admit() charged are refunded");

  for (const [label, body, why] of [
    ["the junk-fmt body (no readable fmt)", junkFmt(4000), "unreadable"],
    ["a WAV with no fmt chunk at all", noFmt, "unreadable"],
    ["a WAV with no data chunk", noData, "unreadable"],
    ["a zero sample rate", zeroRate, "unreadable"],
    ["IEEE float (format tag 3)", wavOf({ tag: 3, bits: 32 }), "format"],
    ["WAVE_FORMAT_EXTENSIBLE (tag 0xFFFE)", wavOf({ tag: 0xfffe }), "format"],
    ["8-bit PCM", wavOf({ bits: 8 }), "bit_depth"],
    ["24-bit PCM", wavOf({ bits: 24 }), "bit_depth"],
    ["0 channels", wavOf({ ch: 0 }), "channels"],
    ["3 channels", wavOf({ ch: 3, ms: 500 }), "channels"],
    ["4 kHz", wavOf({ rate: 4000 }), "sample_rate"],
    ["96 kHz", wavOf({ rate: 96000, ms: 500 }), "sample_rate"],
  ]) {
    eq(wavlib.sttWavProblem?.(body), why, `sttWavProblem names ${label}: ${why}`);
    fresh();
    const r = await call(body, null, FULL, label);
    eq(r.body.reason, "bad_request", `${label}: refused as bad_request`);
    eq(upstreamCalls(), 0, `${label}: zero upstream calls`);
  }

  // Over the duration cap is still `too_long` first (A-DUR), whatever the fmt.
  fresh();
  eq((await call(wavOf({ bits: 8, ms: 20000 }), null, FULL, "a 20 s 8-bit WAV")).body.reason, "too_long",
     "an over-long WAV is too_long even when its fmt is also refused — the more useful word");

  // …and everything a real encoder writes for speech still goes through, once each.
  for (const [label, body] of [
    ["16 kHz mono (what mic.js encodes)", wavOf({})],
    ["22.05 kHz mono (Piper's rate)", wavOf({ rate: 22050 })],
    ["8 kHz mono (a phone line)", wavOf({ rate: 8000 })],
    ["48 kHz stereo, 0.5 s", wavOf({ rate: 48000, ch: 2, ms: 500 })],
    ["an 18-byte fmt chunk (cbSize = 0)", wavOf({ fmtSize: 18 })],
  ]) {
    eq(wavlib.sttWavProblem?.(body), null, `${label}: fit for STT`);
    fresh();
    const r = await call(body, null, FULL, label);
    eq(r.res.status, 200, `${label}: transcribed`);
    eq(upstreamCalls(), 1, `${label}: …with exactly one upstream call`);
  }
}

/* A-RDR. The credential does not chase a `Location`: `redirect: "manual"`, and a 3xx is a door
 * problem (`gateway_unreachable_or_gated`), not `upstream_down`. */
{
  fresh();
  await call(clip(4000), null, FULL, "a normal turn, to read the fetch options");
  eq(sent.length, 1, "one upstream call");
  eq(sent[0].opt.redirect, "manual",
     "/api/transcribe sets redirect:'manual' — the multipart body and the key are never re-sent");
  eq(route.reasonForUpstreamStatus(302), "upstream_down",
     "the status table has no 3xx row — which is why the route answers one before consulting it");

  for (const status of [301, 302, 303, 307, 308]) {
    fresh();
    setPlan({ status, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/audio/transcriptions" } });
    const r = await call(clip(4000), null, FULL, `an upstream ${status}`);
    eq(r.body.reason, "gateway_unreachable_or_gated", `an upstream ${status} is read as a DOOR problem`);
    eq(r.res.status, 503, `…and answers 503 for a ${status}`);
    eq(sent.length, 1, `…having made exactly ONE upstream call — the ${status} was not chased`);
    eq(r.body.transcript, "", `…and says nothing it did not hear (${status})`);
  }
}
