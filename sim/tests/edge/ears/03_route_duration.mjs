/* Part A §A-DUR / A-RDR: the WAV duration ceiling enforced server-side, and the credential
 * never chasing a redirect.
 */
import {
  FULL, call, clip, deep, envmod, eq, fresh, ok, route, sent, setPlan, upstreamCalls, wavlib,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * A-DUR. THE DURATION CEILING, ENFORCED SERVER-SIDE FOR THE ONE CONTAINER THAT ALLOWS IT
 * --------------------------------------------------------------------------- *
 * Spec: live-sim-demo.md §4.1, §4.5 (`too_long`).
 *
 * STT is billed by DURATION: 500 KB is ≈15 s only at 16 kHz 16-bit mono, and 62 s at
 * 8 kHz 8-bit, while `DEMO_MAX_RECORD_MS` lived only in the browser. A RIFF header
 * declares its playing time, so for WAV the cap is server-side. Other containers would
 * need a decoder; `DEMO_STT_FORMATS` defaults to `wav` ALONE, so on the shipped config
 * nothing else reaches the gateway. Both halves are asserted, the uncomfortable one too.
 * --------------------------------------------------------------------------- */
{
  /** A WAV of a chosen rate/width/length. Not `wav.writeWav`, which only emits 16-bit —
   *  and 8-bit is exactly the case under test. */
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

  // ---- The attack: a long low-rate WAV inside the byte cap ---------------- //
  fresh();
  const sixtySeconds = wavAt(8000, 1, 8, 480000);   // 480 KB: comfortably under the byte cap
  ok(sixtySeconds.length < cfg.maxAudioBytes, "the hostile clip is INSIDE DEMO_MAX_AUDIO_BYTES");
  eq(wavlib.wavDurationMs(sixtySeconds).ms, 60000,
     "…and declares 60 s — FOUR TIMES DEMO_MAX_RECORD_MS, inside the byte cap, in a well-formed WAV");
  const long = await call(sixtySeconds, null, FULL, "a 60-second 8-bit WAV under the byte cap");
  eq(long.body.reason, "too_long", "IT IS REFUSED: the duration ceiling is now enforced server-side");
  eq(long.res.status, 400, "…with §4.5's status for too_long");
  eq(upstreamCalls(), 0, "…AND THE GATEWAY IS NEVER TOUCHED: a duration refusal costs nothing");

  // Every width and rate that buys extra seconds inside the same byte budget.
  for (const [rate, bits, label] of [[8000, 16, "8 kHz 16-bit (31 s)"], [8000, 8, "8 kHz 8-bit (62 s)"],
                                     [8000, 4, "8 kHz 4-bit (125 s)"], [4000, 8, "4 kHz 8-bit (125 s)"]]) {
    fresh();
    const r = await call(wavAt(rate, 1, bits, 480000), null, FULL, label);
    eq(r.body.reason, "too_long", `${label} under the byte cap is refused on DURATION`);
    eq(upstreamCalls(), 0, `${label}: zero upstream calls`);
  }

  // ---- …and an honest recording still goes through ------------------------ //
  fresh();
  const fine = wavAt(16000, 1, 16, 16000 * 2 * 5);  // 5 seconds, the shape `mic.js` encodes
  ok(wavlib.wavDurationMs(fine).ms === 5000, "the control clip really is 5 s");
  const good = await call(fine, null, FULL, "an honest 5-second 16 kHz clip");
  eq(good.res.status, 200, "a clip inside the ceiling is transcribed as before");
  eq(good.body.reason, null, "…with no reason");
  eq(upstreamCalls(), 1, "…and exactly one upstream call");

  // The boundary, both sides of it, so the comparison is `>` and not `>=` by accident.
  fresh();
  const exact = wavAt(16000, 1, 16, 16000 * 2 * 15);   // exactly DEMO_MAX_RECORD_MS
  eq(wavlib.wavDurationMs(exact).ms, cfg.maxRecordMs, "the boundary clip is exactly at the cap");
  eq((await call(exact, null, FULL, "exactly at the cap")).res.status, 200, "AT the cap is allowed");
  fresh();
  const over = wavAt(16000, 1, 16, 16000 * 2 * 15 + 3200);  // +100 ms
  eq((await call(over, null, FULL, "100 ms over the cap")).body.reason, "too_long", "just OVER the cap is not");

  // A shorter configured ceiling is obeyed — the number is a variable, not a constant.
  fresh();
  const SHORT_CAP = { ...FULL, DEMO_MAX_RECORD_MS: "3000" };
  eq((await call(fine, null, SHORT_CAP, "5 s against a 3 s cap")).body.reason, "too_long",
     "DEMO_MAX_RECORD_MS is what the check reads, so a fork can tighten it with no code change");
  eq(upstreamCalls(), 0, "…still with zero upstream calls");

  // ---- THE PART THIS DOES NOT COVER, asserted rather than hoped ----------- //
  // A webm body's duration is unknowable without a decoder. The route therefore has NO
  // opinion on it — and the only reason that is not a live hole is that the same webm is
  // refused one step earlier by `DEMO_STT_FORMATS`, which ships as `wav` alone.
  fresh();
  eq(wavlib.wavDurationMs(clip(480000, "webm")), null,
     "a webm body yields NO duration: the honest answer, and the limit of this fix");
  const webm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, FULL, "a 480 KB webm");
  eq(webm.body.reason, "bad_request",
     "…and it is refused by the CONTAINER allowlist instead, which is what closes the gap today");
  eq(upstreamCalls(), 0, "…for free");
  // Spelled out: widen the allowlist and the duration ceiling stops being total.
  const WIDE = { ...FULL, DEMO_STT_FORMATS: "wav,webm" };
  deep(envmod.readConfig(WIDE).sttFormats, ["wav", "webm"], "a fork CAN widen DEMO_STT_FORMATS");
  fresh();
  const wideWebm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, WIDE, "webm, allowlisted");
  eq(wideWebm.res.status, 200,
     "…and then a 480 KB webm of UNKNOWN duration is forwarded — the residual gap, stated not hidden");
}

/* --------------------------------------------------------------------------- *
 * A-RDR. The credential does not chase a `Location`
 * --------------------------------------------------------------------------- *
 * With `redirect: "manual"` the multipart body (and its `Authorization`) is never re-sent
 * to a `Location`, and a 3xx answers `gateway_unreachable_or_gated` — a door problem —
 * rather than falling through to `upstream_down`, which degrades the whole page.
 * --------------------------------------------------------------------------- */
{
  fresh();
  await call(clip(4000), null, FULL, "a normal turn, to read the fetch options");
  eq(sent.length, 1, "one upstream call");
  eq(sent[0].opt.redirect, "manual",
     "/api/transcribe sets redirect:'manual' — the multipart body and the key are never re-sent");

  eq(route.reasonForUpstreamStatus(302), "upstream_down",
     "the status table itself has no 3xx row — which is why the route answers one before consulting it");

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
