/* Part A §A1–A5: the fail-safe default, both byte caps, the origin pin, the per-IP windows and
 * budget, our own timeout. Every refusal here makes ZERO upstream calls.
 */
import {
  BASE, FULL, KEY, call, clip, envmod, eq, fresh, health, limits, ok, sent, setPlan,
  upstreamCalls,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * A1. The fail-safe default (C5) and the unset-model path: ZERO upstream calls
 * --------------------------------------------------------------------------- */
{
  fresh();
  const noEars = { ...FULL };
  delete noEars.DEMO_STT_MODEL;
  const cases = [
    ["no variables at all", {}],
    ["a base URL but no key", { DEMO_GATEWAY_BASE_URL: BASE }],
    ["a key but no chat model", { DEMO_GATEWAY_BASE_URL: BASE, DEMO_GATEWAY_API_KEY: KEY }],
    ["the kill switch off", { ...FULL, DEMO_ENABLED: "0" }],
    ["a configured gateway with NO DEMO_STT_MODEL", noEars],
    ["half a Cloudflare Access token", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: "id-only.access" }],
  ];
  for (const [label, env] of cases) {
    const { res, body } = await call(clip(9000), null, env, label);
    eq(res.status, 503, `${label}: 503`);
    eq(body.reason, "gateway_not_configured", `${label}: reason`);
    eq(body.ears, false, `${label}: ears false — the page is never offered a mic it cannot serve`);
    eq(body.transcript, "", `${label}: no transcript`);
  }
  eq(upstreamCalls(), 0, "an unconfigured or ear-less deployment makes ZERO upstream calls");
  eq(sent.length, 0, "…and does not even build an upstream request");

  // /api/health agrees, from the same config — the probe and the route cannot disagree.
  const hres = await health.onRequestGet({ env: noEars });
  const hbody = JSON.parse(await hres.clone().text());
  eq(hbody.ears, false, "/api/health reports ears:false with no DEMO_STT_MODEL");
  const hres2 = await health.onRequestGet({ env: FULL });
  const hbody2 = JSON.parse(await hres2.clone().text());
  eq(hbody2.ears, true, "/api/health reports ears:true once DEMO_STT_MODEL is set");
  eq(hbody2.limits.max_record_ms, 15000, "…and publishes the 15 s recording cap to the page");
  eq(upstreamCalls(), 0, "the health probe never calls the gateway either");
}

/* --------------------------------------------------------------------------- *
 * A2. §4.1 — both byte caps, and the FREE floor
 * --------------------------------------------------------------------------- */
{
  fresh();
  // Below DEMO_MIN_AUDIO_BYTES: no audio -> no request, no cost, no latency.
  for (const n of [0, 1, 799, 1999]) {
    const { res, body } = await call(clip(n), null, FULL, `a ${n}-byte clip`);
    eq(res.status, 400, `${n} bytes: 400`);
    eq(body.reason, "too_short", `${n} bytes: too_short`);
  }
  eq(upstreamCalls(), 0, "A CLIP UNDER THE FLOOR MAKES NO UPSTREAM CALL AT ALL (§4.1, stt.py:194-197)");
  eq(sent.length, 0, "…and builds no upstream request");

  // Exactly at the floor is allowed: the cap is a floor, not a gap.
  fresh();
  const at = await call(clip(2000), null, FULL, "exactly 2000 bytes");
  eq(at.res.status, 200, "exactly DEMO_MIN_AUDIO_BYTES is accepted");
  eq(upstreamCalls(), 1, "…and is the one call");

  // Above DEMO_MAX_AUDIO_BYTES, by the real byte count.
  fresh();
  const big = await call(clip(500001), null, FULL, "a 500001-byte clip");
  eq(big.res.status, 400, "over the byte cap: 400");
  eq(big.body.reason, "too_long", "over the byte cap: too_long");
  eq(upstreamCalls(), 0, "an oversized clip makes no upstream call");

  // …and by the DECLARED Content-Length, refused UNREAD.
  fresh();
  const declared = await call(clip(3000), { "Content-Length": "900000" }, FULL, "an over-declared clip");
  eq(declared.body.reason, "too_long", "a declared Content-Length over the cap is refused");
  eq(upstreamCalls(), 0, "…without reading the body or calling upstream");

  // An override moves both caps, so a fork can tighten them.
  fresh();
  const tight = { ...FULL, DEMO_MIN_AUDIO_BYTES: "10000", DEMO_MAX_AUDIO_BYTES: "20000" };
  eq((await call(clip(9000), null, tight, "under a raised floor")).body.reason, "too_short",
     "DEMO_MIN_AUDIO_BYTES is env-overridable");
  eq((await call(clip(25000), null, tight, "over a lowered ceiling")).body.reason, "too_long",
     "DEMO_MAX_AUDIO_BYTES is env-overridable");
  eq(upstreamCalls(), 0, "neither override path calls upstream");
}

/* --------------------------------------------------------------------------- *
 * A3. §4.3 — the origin pin, and zero upstream calls behind it
 * --------------------------------------------------------------------------- */
{
  fresh();
  const cases = [
    ["a foreign Origin", { Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" }],
    ["a foreign Origin with no fetch metadata", { Origin: "https://evil.example", "Sec-Fetch-Site": undefined }],
    ["cross-site fetch metadata", { "Sec-Fetch-Site": "cross-site" }],
    ["no Origin and no fetch metadata", { Origin: undefined, "Sec-Fetch-Site": undefined }],
    ["a foreign Referer", { Origin: undefined, Referer: "https://evil.example/x", "Sec-Fetch-Site": undefined }],
  ];
  for (const [label, headers] of cases) {
    const { res, body } = await call(clip(9000), headers, FULL, label);
    eq(res.status, 403, `${label}: 403`);
    eq(body.reason, "forbidden_origin", `${label}: forbidden_origin`);
  }
  eq(upstreamCalls(), 0, "A4: a forged or foreign origin makes ZERO upstream calls");
  eq(sent.length, 0, "…and builds no upstream request");
}

/* --------------------------------------------------------------------------- *
 * A4. §4.1 — the per-IP windows (10/min, 60/hour), and the budget
 * --------------------------------------------------------------------------- */
{
  fresh();
  let last = null;
  for (let i = 0; i < 10; i++) {
    last = await call(clip(4000), null, FULL, `turn ${i + 1}`);
    eq(last.res.status, 200, `turn ${i + 1} of 10 is inside DEMO_STT_PER_MIN`);
  }
  eq(upstreamCalls(), 10, "ten admitted turns are ten upstream calls");
  const refused = await call(clip(4000), null, FULL, "the eleventh turn");
  eq(refused.res.status, 429, "the ELEVENTH turn in a minute is 429 (DEMO_STT_PER_MIN = 10)");
  eq(refused.body.reason, "rate_limited", "…with reason rate_limited");
  ok(Number(refused.res.headers.get("Retry-After")) > 0, "…and a Retry-After");
  eq(upstreamCalls(), 10, "a rate-limited turn makes NO upstream call");
  ok(refused.res.headers.get("X-RateLimit-Limit") !== null, "X-RateLimit-Limit rides the refusal");

  // A different IP has its own window.
  const other = await call(clip(4000), { "CF-Connecting-IP": "198.51.100.4" }, FULL, "another visitor");
  eq(other.res.status, 200, "another IP is not caught by the first one's window");

  // The hour window is tighter than 10/min x 60.
  fresh();
  const perHour = { ...FULL, DEMO_STT_PER_MIN: "100", DEMO_STT_PER_HOUR: "3" };
  for (let i = 0; i < 3; i++) await call(clip(4000), null, perHour, `hour turn ${i}`);
  const hourly = await call(clip(4000), null, perHour, "the fourth in an hour");
  eq(hourly.body.reason, "rate_limited", "DEMO_STT_PER_HOUR is enforced too");
  eq(upstreamCalls(), 3, "…and the refused one costs nothing");

  // The unit budget. A transcribe call is 2 units (§4.1's denomination).
  fresh();
  const cfg = envmod.readConfig(FULL);
  limits.__exhaustBudget(cfg);
  const broke = await call(clip(4000), null, FULL, "over budget");
  eq(broke.res.status, 503, "an exhausted budget is 503");
  eq(broke.body.reason, "budget_exhausted", "…with reason budget_exhausted");
  ok(Number(broke.res.headers.get("Retry-After")) > 0, "…and a Retry-After to the window reset");
  eq(upstreamCalls(), 0, "an over-budget turn makes NO upstream call");
}

/* --------------------------------------------------------------------------- *
 * A5. §4.1 — the timeout is OURS, via AbortSignal
 * --------------------------------------------------------------------------- */
{
  fresh();
  setPlan({ throw: "TimeoutError" });
  const t = await call(clip(4000), null, FULL, "an upstream that never answers");
  eq(t.res.status, 504, "our own AbortSignal.timeout is a 504");
  eq(t.body.reason, "timeout", "…with reason timeout");
  eq(t.res.headers.get("Retry-After"), "10", "…and §4.5's Retry-After of 10");
  eq(upstreamCalls(), 1, "the call was made — this is a timeout, not a refusal");

  // The signal really is attached, and really carries DEMO_STT_TIMEOUT_MS.
  fresh();
  let seenMs = null;
  const realTimeout = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => { seenMs = ms; return realTimeout.call(AbortSignal, ms); };
  await call(clip(4000), null, FULL, "the default timeout");
  eq(seenMs, 12000, "DEMO_STT_TIMEOUT_MS defaults to 12 000 ms");
  await call(clip(4000), null, { ...FULL, DEMO_STT_TIMEOUT_MS: "5000" }, "an overridden timeout");
  eq(seenMs, 5000, "…and is env-overridable");
  ok(sent.every((s) => s.opt && s.opt.signal), "every upstream call carries an AbortSignal");
  AbortSignal.timeout = realTimeout;

  // A plain network failure is upstream_down, not a timeout, and never a bare 500.
  fresh();
  setPlan({ throw: "TypeError" });
  const down = await call(clip(4000), null, FULL, "an unreachable gateway");
  eq(down.res.status, 503, "an unreachable gateway is 503");
  eq(down.body.reason, "upstream_down", "…with reason upstream_down");
}
