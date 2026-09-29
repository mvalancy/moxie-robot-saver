/* test_mode §3: `/api/health`, called with a plain env — the probe that must never spend. */
import { leakSweep } from "../common.mjs";
import { ok, eq, deep, lib, env2, limits, FULL, probe } from "./harness.mjs";

/** The smallest thing `limits.admit()` accepts: same-origin fetch metadata and one fixed IP,
 *  so every admission shares ONE per-IP window and the windows are not what refuses us. */
const admissible = () => ({
  url: "https://probe.invalid.test/api/chat",
  headers: new Headers({ "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" }),
});

{
  limits.__reset();
  // Any network call from the probe is a spend: fail it loudly and count it.
  const realFetch = globalThis.fetch;
  let fetches = 0;
  globalThis.fetch = () => { fetches += 1; return Promise.reject(new Error("health.js called fetch")); };

  const bare = await probe({});
  eq(bare.res.status, 200, "/api/health is always 200, so a NON-200 unambiguously means the route is absent");
  eq(bare.body.mode, "degraded", "no variables => degraded");
  eq(bare.body.reason, "gateway_not_configured", "no variables => gateway_not_configured");
  eq(bare.body.ok, true, "`ok` means the probe answered");
  eq(bare.body.degraded, true, "...and `degraded` says the demo is not live");
  eq(bare.body.voice, false, "no voice when nothing is configured");
  eq(bare.body.ears, false, "no ears when nothing is configured");
  deep([bare.body.messages, bare.body.speech, bare.body.context], [[], [], ""],
       "a probe carries no messages, mints no ticket and no context blob");
  eq(bare.res.headers.get("cache-control"), "no-store", "the probe is never cached");
  ok(/application\/json/.test(bare.res.headers.get("content-type") || ""), "the probe answers JSON");
  eq(bare.res.headers.get("retry-after"), null, "gateway_not_configured sets no Retry-After");
  deep(Object.keys(bare.body), [...env2.PUBLIC_KEYS], "the probe body is exactly PUBLIC_KEYS");
  deep(bare.body.load, { level: "ok", inflight: 0, capacity: 4 },
       "§7: inflight is 0 because THIS ISOLATE is idle (a real read of limits.js's counter)");
  deep(bare.body.limits,
       { max_input_chars: 500, max_tts_chars: 300, max_tokens: 160, chat_per_min: 5,
         max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 },
       "the probe reports §4.1's caps, including the three the microphone needs (P1)");

  const live = await probe(FULL);
  eq(live.res.status, 200, "a configured probe is 200");
  eq(live.body.mode, "live", "the three required values => live");
  eq(live.body.reason, null, "live carries no reason");
  eq(live.body.voice, true, "a configured TTS model => voice");
  eq(live.body.ears, true, "a configured STT model => ears");
  eq(live.res.headers.get("x-moxie-mode"), "live", "X-Moxie-Mode reflects the mode");
  // §4.2: NOTHING secret leaves a FULLY configured deployment — body or any header.
  await leakSweep(ok, live.res, Object.values(FULL), "the live probe");

  const off = await probe({ ...FULL, DEMO_ENABLED: "0" });
  eq(off.res.status, 200, "the kill switch still answers 200");
  eq(off.body.reason, "gateway_not_configured", "DEMO_ENABLED=0 reads as not configured");

  // 3a. The probe reads the REAL counters from limits.js — local stubs once made /api/health
  // unable to answer `budget_exhausted` or BUSY.
  const cfg = lib.readConfig(FULL);
  const a1 = await limits.admit({ request: admissible(), cfg, route: "chat" });
  ok(a1.ok, "the test's own admission must be accepted (otherwise the load numbers mean nothing)");
  deep((await probe(FULL)).body.load, { level: "ok", inflight: 1, capacity: 4 },
       "one turn in flight is REPORTED as one — the stub would still have said 0");
  const a2 = await limits.admit({ request: admissible(), cfg, route: "chat" });
  const a3 = await limits.admit({ request: admissible(), cfg, route: "chat" });
  ok(a2.ok && a3.ok, "three concurrent chat turns fit under the default ceiling of 4");
  deep((await probe(FULL)).body.load, { level: "busy", inflight: 3, capacity: 4 },
       "§7: 3 of 4 is >= 60%, so the probe reports `busy` and the BUSY pill can fire");
  a1.release(); a2.release(); a3.release();
  deep((await probe(FULL)).body.load, { level: "ok", inflight: 0, capacity: 4 },
       "released slots are given back, so the probe does not stick at busy");

  limits.__reset();
  limits.__exhaustBudget(cfg);
  const spent = await probe(FULL);
  eq(spent.res.status, 200, "a spent budget is STILL 200 — non-200 means the route is absent");
  eq(spent.body.mode, "degraded", "a spent budget degrades the probe");
  eq(spent.body.reason, "budget_exhausted", "...and says exactly why");
  eq(spent.body.degraded, true, "...with degraded set, so env.js paints the scripted badge");
  ok(spent.body.retry_after_s > 0,
     `§4.5: budget_exhausted carries seconds-to-window-reset (got ${spent.body.retry_after_s})`);
  eq(spent.res.headers.get("retry-after"), String(spent.body.retry_after_s),
     "the header and the body agree, so a client may pace itself from either");
  eq(spent.res.headers.get("x-moxie-mode"), "degraded", "X-Moxie-Mode follows the real mode");
  eq((await probe({})).body.reason, "gateway_not_configured",
     "an exhausted budget does not overwrite gateway_not_configured — configuration is checked first");

  // THE HARD INVARIANT, at runtime: no probe above reached the network or the gateway seam.
  eq(fetches, 0, "/api/health made NO fetch across every probe in this block (a 30 s poll must cost nothing)");
  eq(limits.__state().stats.upstreamCalls, 0, "...and ZERO upstream calls were noted");
  globalThis.fetch = realFetch;
  limits.__reset();
}
