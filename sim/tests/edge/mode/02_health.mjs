/* test_mode §3: `/api/health`, called with a plain env — the probe that must never spend. */
import {
  readFileSync, join, repo, ok, eq, deep, lib, env2, health, limits, FULL, probe,
} from "./harness.mjs";

// --------------------------------------------------------------------------- //
// 3. functions/api/health.js — the probe
// --------------------------------------------------------------------------- //
/** The smallest thing `limits.admit()` will accept: `Sec-Fetch-Site: same-origin` is what the
 *  origin pin asks for when there is no `Origin` header (clientip.js::checkOrigin), and a fixed
 *  IP keeps every admission in ONE per-IP window so the windows are not what refuses us. */
function admissible(url) {
  return { url: url || "https://probe.invalid.test/api/chat",
           headers: new Headers({ "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" }) };
}
{
  limits.__reset();   // a fresh isolate, so the numbers below are this block's and no one else's
  const bare = await probe({});
  // Always 200, so that a NON-200 unambiguously means "the route is absent".
  eq(bare.res.status, 200, "/api/health is always 200, even when nothing is configured");
  eq(bare.body.mode, "degraded", "no variables => degraded");
  eq(bare.body.reason, "gateway_not_configured", "no variables => gateway_not_configured");
  eq(bare.body.ok, true, "`ok` means the probe answered");
  eq(bare.body.degraded, true, "...and `degraded` says the demo is not live");
  eq(bare.body.voice, false, "no voice when nothing is configured");
  eq(bare.body.ears, false, "no ears when nothing is configured");
  deep(bare.body.messages, [], "a probe carries no messages");
  deep(bare.body.speech, [], "a probe mints no ticket");
  eq(bare.body.context, "", "a probe carries no context blob");
  eq(bare.res.headers.get("cache-control"), "no-store", "the probe is never cached");
  ok(/application\/json/.test(bare.res.headers.get("content-type") || ""), "the probe answers JSON");
  eq(bare.res.headers.get("retry-after"), null, "gateway_not_configured sets no Retry-After");
  deep(Object.keys(bare.body), [...env2.PUBLIC_KEYS], "the probe body is exactly PUBLIC_KEYS");
  deep(bare.body.load, { level: "ok", inflight: 0, capacity: 4 },
       "§7: inflight is 0 because THIS ISOLATE is idle — a real read of limits.js's counter after "
       + "__reset(), not the hard-coded 0 the P0-a stub returned regardless of what was in flight");
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

  // §4.2, the whole point: NOTHING secret leaves. Asserted over the raw response text of
  // a FULLY configured deployment, which is the only case where there is anything to leak.
  for (const secret of Object.values(FULL))
    ok(!live.text.includes(secret), `the probe body must not contain ${secret.slice(0, 12)}…`);
  ok(!/gw\.invalid\.test/.test(live.text), "no gateway hostname in the probe body");
  ok(!/sk-/.test(live.text), "no key prefix in the probe body");
  let headerCount = 0;
  for (const [name, value] of live.res.headers) {
    headerCount++;
    ok(!Object.values(FULL).some((s) => String(value).includes(s)), `no secret in header ${name}`);
    ok(!/gw\.invalid\.test|sk-/.test(String(value)), `no URL or key prefix in header ${name}`);
  }
  ok(headerCount >= 4, `the header scan must actually run (saw ${headerCount} headers)`);

  const off = await probe({ ...FULL, DEMO_ENABLED: "0" });
  eq(off.res.status, 200, "the kill switch still answers 200");
  eq(off.body.reason, "gateway_not_configured", "DEMO_ENABLED=0 reads as not configured");
  eq(off.body.mode, "degraded", "DEMO_ENABLED=0 => degraded");

  // The probe must not be a spending route by accident: health.js may not call fetch.
  const src = readFileSync(join(repo, "functions", "api", "health.js"), "utf8");
  ok(!/\bfetch\s*\(/.test(src), "health.js must make NO gateway call, ever (a 30 s poll must cost nothing)");

  // ------------------------------------------------------------------------- //
  // 3a. The probe reads the REAL counters from limits.js — local stubs once made
  // `/api/health` unable to answer `budget_exhausted`, so an over-budget deployment painted
  // LIVE and §7's BUSY pill never fired.
  // ------------------------------------------------------------------------- //

  // No stub may shadow the real implementation again, and the import must be the real one.
  ok(/from\s+"\.\/_lib\/limits\.js"/.test(src),
     "health.js must import its counters from _lib/limits.js, not define its own");
  ok(!/function\s+budgetState\s*\(/.test(src) && !/function\s+loadState\s*\(/.test(src),
     "health.js must define NO local budgetState/loadState stub (that is the P0-a defect)");

  // The strongest form of "no gateway call": `onRequestGet` is not an async function, so it
  // cannot be awaiting one. Both counter reads are synchronous map lookups.
  eq(health.onRequestGet.constructor.name, "Function",
     "health.js's handler must not be async — a probe that cannot await cannot call upstream");
  // …and the probe must stay synchronous while `limits.admit()` is async (the queue may wait,
  // the probe may not).
  eq(limits.admit.constructor.name, "AsyncFunction",
     "limits.admit IS async — it can wait for a concurrency slot (the bounded FIFO of §4.1)");
  ok(!/\basync\b/.test(src.split("export function onRequestGet")[1] || "async"),
     "…and no amount of that may leak into health.js's handler body");

  // Real in-flight, from the same counter admit() increments.
  const a1 = await limits.admit({ request: admissible(), cfg: lib.readConfig(FULL), route: "chat" });
  ok(a1.ok, "the test's own admission must be accepted (otherwise the load numbers mean nothing)");
  const busy1 = await probe(FULL);
  deep(busy1.body.load, { level: "ok", inflight: 1, capacity: 4 },
       "one turn in flight is REPORTED as one — the stub would still have said 0");
  const a2 = await limits.admit({ request: admissible(), cfg: lib.readConfig(FULL), route: "chat" });
  const a3 = await limits.admit({ request: admissible(), cfg: lib.readConfig(FULL), route: "chat" });
  ok(a2.ok && a3.ok, "three concurrent chat turns fit under the default ceiling of 4");
  const busy3 = await probe(FULL);
  deep(busy3.body.load, { level: "busy", inflight: 3, capacity: 4 },
       "§7: 3 of 4 is >= 60%, so the probe reports `busy` and the BUSY pill can finally fire");
  a1.release(); a2.release(); a3.release();
  const idle = await probe(FULL);
  deep(idle.body.load, { level: "ok", inflight: 0, capacity: 4 },
       "released slots are given back, so the probe does not stick at busy");

  // A6: an exhausted budget is REPORTED, which is the whole point of the slice.
  limits.__reset();
  const fullCfg = lib.readConfig(FULL);
  limits.__exhaustBudget(fullCfg);
  const spent = await probe(FULL);
  eq(spent.res.status, 200, "a spent budget is STILL 200 — rule 2: non-200 means the route is absent");
  eq(spent.body.mode, "degraded", "a spent budget degrades the probe");
  eq(spent.body.reason, "budget_exhausted", "...and says exactly why (the stub could never say this)");
  eq(spent.body.degraded, true, "...with degraded set, so env.js paints the scripted badge");
  ok(spent.body.retry_after_s > 0,
     `§4.5: budget_exhausted carries seconds-to-window-reset (got ${spent.body.retry_after_s})`);
  eq(spent.res.headers.get("retry-after"), String(spent.body.retry_after_s),
     "the header and the body agree, so a client may pace itself from either");
  eq(spent.res.headers.get("x-moxie-mode"), "degraded", "X-Moxie-Mode follows the real mode");

  // Configuration outranks the counters: an unconfigured deployment must not start blaming
  // a budget it never had. modeOf checks `configured` first and this pins that order.
  const spentBare = await probe({});
  eq(spentBare.body.reason, "gateway_not_configured",
     "an exhausted budget does not overwrite gateway_not_configured — configuration is checked first");

  // THE HARD INVARIANT, proved at runtime rather than by reading the source: every route
  // calls limits.noteUpstreamCall() immediately before its fetch(), so a non-zero count here
  // would mean some probe above reached the gateway. Every probe in this block, zero calls.
  eq(limits.__state().stats.upstreamCalls, 0,
     "/api/health made ZERO upstream calls across every probe in this block");
  limits.__reset();   // leave no counters behind for section 4
}
