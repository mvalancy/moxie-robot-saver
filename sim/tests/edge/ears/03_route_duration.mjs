/* Part A §A-DUR / A-RDR: the WAV duration ceiling enforced server-side, and the credential
 * never chasing a redirect.
 */
import {
  FULL, call, clip, envmod, eq, fresh, ok, sent, setPlan, upstreamCalls, wavlib,
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

/* A-RDR. The credential does not chase a `Location`: `redirect: "manual"`, and a 3xx is a door
 * problem (`gateway_unreachable_or_gated`), not `upstream_down`. */
{
  fresh();
  await call(clip(4000), null, FULL, "a normal turn, to read the fetch options");
  eq(sent.length, 1, "one upstream call");
  eq(sent[0].opt.redirect, "manual",
     "/api/transcribe sets redirect:'manual' — the multipart body and the key are never re-sent");

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
