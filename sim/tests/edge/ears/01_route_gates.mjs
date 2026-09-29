/* Part A §A1–A5: the fail-safe default, both byte caps, the origin pin, the per-IP windows and
 * budget, our own timeout. Every refusal here makes ZERO upstream calls.
 */
import {
  BASE, FULL, KEY, call, clip, envmod, eq, fresh, health, limits, ok, sent, setPlan,
  upstreamCalls,
} from "./harness.mjs";

/* A1. The fail-safe default (C5) and the unset-model path: ZERO upstream calls. */
{
  fresh();
  const noEars = { ...FULL };
  delete noEars.DEMO_STT_MODEL;
  for (const [label, env] of [
    ["no variables at all", {}],
    ["a base URL but no key", { DEMO_GATEWAY_BASE_URL: BASE }],
    ["a key but no chat model", { DEMO_GATEWAY_BASE_URL: BASE, DEMO_GATEWAY_API_KEY: KEY }],
    ["the kill switch off", { ...FULL, DEMO_ENABLED: "0" }],
    ["a configured gateway with NO DEMO_STT_MODEL", noEars],
    ["half a Cloudflare Access token", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: "id-only.access" }],
  ]) {
    const { res, body } = await call(clip(9000), null, env, label);
    eq(res.status, 503, `${label}: 503`);
    eq(body.reason, "gateway_not_configured", `${label}: reason`);
    eq(body.ears, false, `${label}: ears false — the page is never offered a mic it cannot serve`);
    eq(body.transcript, "", `${label}: no transcript`);
  }
  eq(sent.length, 0, "an unconfigured or ear-less deployment builds no upstream request at all");

  // /api/health agrees, from the same config — the probe and the route cannot disagree.
  const h0 = JSON.parse(await (await health.onRequestGet({ env: noEars })).text());
  eq(h0.ears, false, "/api/health reports ears:false with no DEMO_STT_MODEL");
  const h1 = JSON.parse(await (await health.onRequestGet({ env: FULL })).text());
  eq(h1.ears, true, "/api/health reports ears:true once DEMO_STT_MODEL is set");
  eq(h1.limits.max_record_ms, 15000, "…and publishes the 15 s recording cap to the page");
  eq(upstreamCalls(), 0, "the health probe never calls the gateway either");
}

/* A2. §4.1 — both byte caps, and the FREE floor. */
{
  fresh();
  for (const n of [0, 1999]) {
    const { res, body } = await call(clip(n), null, FULL, `a ${n}-byte clip`);
    eq(res.status, 400, `${n} bytes: 400`);
    eq(body.reason, "too_short", `${n} bytes: too_short`);
  }
  eq(sent.length, 0, "A CLIP UNDER THE FLOOR MAKES NO UPSTREAM CALL AT ALL (§4.1, stt.py:194-197)");

  fresh();
  eq((await call(clip(2000), null, FULL, "exactly 2000 bytes")).res.status, 200,
     "exactly DEMO_MIN_AUDIO_BYTES is accepted — the cap is a floor, not a gap");

  for (const [label, n, headers] of [
    ["a 500001-byte clip", 500001, null],
    ["a declared Content-Length over the cap, refused UNREAD", 3000, { "Content-Length": "900000" }],
  ]) {
    fresh();
    const r = await call(clip(n), headers, FULL, label);
    eq(r.res.status, 400, `${label}: 400`);
    eq(r.body.reason, "too_long", `${label}: too_long`);
    eq(upstreamCalls(), 0, `${label}: no upstream call`);
  }

  fresh();
  const tight = { ...FULL, DEMO_MIN_AUDIO_BYTES: "10000", DEMO_MAX_AUDIO_BYTES: "20000" };
  eq((await call(clip(9000), null, tight, "under a raised floor")).body.reason, "too_short",
     "DEMO_MIN_AUDIO_BYTES is env-overridable");
  eq((await call(clip(25000), null, tight, "over a lowered ceiling")).body.reason, "too_long",
     "DEMO_MAX_AUDIO_BYTES is env-overridable");
  eq(upstreamCalls(), 0, "neither override path calls upstream");
}

/* A3. §4.3 — the origin pin (shared `admit()`; its variants are pinned by test_demo_proxy §2,
 * so two cases prove this route is wired through it). */
{
  fresh();
  for (const [label, headers] of [
    ["a foreign Origin", { Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" }],
    ["no Origin and no fetch metadata", { Origin: undefined, "Sec-Fetch-Site": undefined }],
  ]) {
    const { res, body } = await call(clip(9000), headers, FULL, label);
    eq(res.status, 403, `${label}: 403`);
    eq(body.reason, "forbidden_origin", `${label}: forbidden_origin`);
  }
  eq(sent.length, 0, "A4: a forged or foreign origin makes ZERO upstream calls");
}

/* A4. §4.1 — the per-IP windows (10/min, then the hour), and the budget. */
{
  fresh();
  for (let i = 0; i < 10; i++) {
    eq((await call(clip(4000), null, FULL, `turn ${i + 1}`)).res.status, 200,
       `turn ${i + 1} of 10 is inside DEMO_STT_PER_MIN`);
  }
  const refused = await call(clip(4000), null, FULL, "the eleventh turn");
  eq(refused.res.status, 429, "the ELEVENTH turn in a minute is 429 (DEMO_STT_PER_MIN = 10)");
  eq(refused.body.reason, "rate_limited", "…with reason rate_limited");
  ok(Number(refused.res.headers.get("Retry-After")) > 0, "…and a Retry-After");
  ok(refused.res.headers.get("X-RateLimit-Limit") !== null, "X-RateLimit-Limit rides the refusal");
  eq(upstreamCalls(), 10, "a rate-limited turn makes NO upstream call");
  eq((await call(clip(4000), { "CF-Connecting-IP": "198.51.100.4" }, FULL, "another visitor")).res.status, 200,
     "another IP is not caught by the first one's window");

  fresh();
  const perHour = { ...FULL, DEMO_STT_PER_MIN: "100", DEMO_STT_PER_HOUR: "3" };
  for (let i = 0; i < 3; i++) await call(clip(4000), null, perHour, `hour turn ${i}`);
  eq((await call(clip(4000), null, perHour, "the fourth in an hour")).body.reason, "rate_limited",
     "DEMO_STT_PER_HOUR is enforced too");
  eq(upstreamCalls(), 3, "…and the refused one costs nothing");

  fresh();
  limits.__exhaustBudget(envmod.readConfig(FULL));
  const broke = await call(clip(4000), null, FULL, "over budget");
  eq(broke.res.status, 503, "an exhausted budget is 503");
  eq(broke.body.reason, "budget_exhausted", "…with reason budget_exhausted");
  ok(Number(broke.res.headers.get("Retry-After")) > 0, "…and a Retry-After to the window reset");
  eq(upstreamCalls(), 0, "an over-budget turn makes NO upstream call");
}

/* A5. §4.1 — the timeout is OURS, via AbortSignal; a network failure is not a timeout. */
{
  fresh();
  setPlan({ throw: "TimeoutError" });
  const t = await call(clip(4000), null, FULL, "an upstream that never answers");
  eq(t.res.status, 504, "our own AbortSignal.timeout is a 504");
  eq(t.body.reason, "timeout", "…with reason timeout");
  eq(t.res.headers.get("Retry-After"), "10", "…and §4.5's Retry-After of 10");

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

  fresh();
  setPlan({ throw: "TypeError" });
  const down = await call(clip(4000), null, FULL, "an unreachable gateway");
  eq(down.res.status, 503, "an unreachable gateway is 503");
  eq(down.body.reason, "upstream_down", "…with reason upstream_down");
}
