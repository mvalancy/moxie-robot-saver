/* test_turnstile — §5–5c: fail open on transport failure, a wrong secret is HTTP 400, unknown route. Run via the entry file, never alone. */
import {
  ACT, ARMED, GATEWAY, P, SECRET_PASS, TOKEN, deep, envlib, eq, fresh, gatewayCalls, limits, ok,
  outcomes, req, ts, turn, verifyCalls,
} from "./harness.mjs";

/** A turn under siteverify behaviour `p`; `facts` is [status, reason, gateway calls]. */
async function underSiteverify(p, env) {
  fresh();
  P.plan = { turnstile: p };
  const r = await turn("hello", env);
  return { r, facts: [r.status, r.body.reason, gatewayCalls()] };
}
const fourHundred = (codes) => ({ status: 400, text: JSON.stringify({ success: false, "error-codes": codes }) });

/* =========================================================================== *
 * 5. FAIL OPEN ON A TRANSPORT FAILURE — a third-party outage must not take the demo down;
 *    the spend is already capped, so failing open is bounded. Includes the non-2xx shapes
 *    that are Cloudflare's problem, not ours (5b's other half, row C4b).
 * =========================================================================== */
for (const [label, p] of [
  ["the endpoint is unreachable", { throw: "TypeError" }],
  ["our 2 s deadline fires", { throw: "TimeoutError" }],
  ["the fetch is aborted", { throw: "AbortError" }],
  ["it answers 500", { status: 500 }],
  ["it answers 403", { status: 403 }],
  ["it answers a redirect we did not follow", { status: 302 }],
  ["it answers HTML instead of JSON", { text: "<html>go away</html>" }],
  ["it answers a JSON array", { text: "[]" }],
  ["it answers `null`", { text: "null" }],
  ["it answers Cloudflare's own internal-error", { body: { success: false, "error-codes": ["internal-error"] } }],
  // A 500 whose body parses as a verdict is still a Cloudflare failure (row D3c).
  ["it answers 500 with a body that PARSES as a failed verdict",
   { status: 500, text: JSON.stringify({ success: false, "error-codes": ["invalid-input-response"] }) }],
  ["a 400 whose codes name the VISITOR's token, not our secret", fourHundred(["invalid-input-response"])],
  ["a 400 with no error-codes at all", { status: 400, text: JSON.stringify({ success: false }) }],
  ["a 400 that is not JSON", { status: 400, text: "<html>bad request</html>" }],
  ["a 500 naming internal-error", { status: 500, text: JSON.stringify({ success: false, "error-codes": ["internal-error"] }) }],
  ["a 429 from the endpoint itself", { status: 429, text: "" }],
]) {
  const { facts } = await underSiteverify(p);
  deep([...facts, outcomes().unreachable], [200, null, 1, 1],
       `FAIL OPEN — ${label}: the turn is still served (no reason, gateway called, recorded \`unreachable\`)`);
}

/* The deadline is bounded both ways (one nothing could meet switches the check off by stealth). */
deep(["", "999999", "1", "500"].map((v) => envlib.readConfig(Object.assign({}, ARMED, v ? { DEMO_TURNSTILE_TIMEOUT_MS: v } : {})).turnstileTimeoutMs),
     [2000, 2000, 2000, 500], "the siteverify deadline defaults to 2 s, out-of-range overrides fall back, a sane one is honoured");

/* …and WIRED (row D3e): the check holds a concurrency slot, so a siteverify that never answers
 * must be cut off by our own deadline. The stub honours `opt.signal`, so an unwired one hangs. */
{
  const keepAlive = setInterval(() => {}, 25);   // AbortSignal.timeout's timer is unref'd
  const started = Date.now();
  const { facts } = await underSiteverify({ hang: true }, Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "120" }));
  const elapsed = Date.now() - started;
  clearInterval(keepAlive);
  deep(facts, [200, null, 1], "a siteverify that NEVER answers still lets the turn through (fail open)");
  ok(elapsed >= 100 && elapsed < 5000, `…after our own deadline fired, well before the route's 10 s timeout (${elapsed} ms)`);
  eq(limits.__state().inflight.chat || 0, 0, "…with the concurrency slot given back");
}

/* =========================================================================== *
 * 5b. A WRONG SECRET IS **HTTP 400** (measured against the real endpoint; every genuine
 *     verdict is a 200). Failing open on `!res.ok` would let a mistyped secret switch the
 *     control off silently, so our-fault codes refuse at ANY status (row C4).
 * =========================================================================== */
for (const code of ["invalid-input-secret", "missing-input-secret", "bad-request"]) {
  const { facts } = await underSiteverify(fourHundred([code]));
  deep([...facts, outcomes().misconfigured], [503, "turnstile_misconfigured", 0, 1],
       `a 400 naming ${code} is OUR fault and REFUSES (503, zero gateway calls, recorded misconfigured)`);
}
eq((await underSiteverify({ body: { success: false, "error-codes": ["invalid-input-secret"] } })).facts[1], "turnstile_misconfigured",
   "…and the same code at status 200 maps the same way: the reason is the CODE's, not the status's");
// The 400 body is now PARSED: one that echoes the secret back is still diagnosed, and the
// sweep proves nothing of it reaches the response.
eq((await underSiteverify({ status: 400, text: JSON.stringify({ "error-codes": ["invalid-input-secret"], success: false,
  messages: ["your secret " + SECRET_PASS + " is wrong"] }) })).facts[1], "turnstile_misconfigured", "a hostile 400 body is still diagnosed");

/* =========================================================================== *
 * 5c. AN UNKNOWN ROUTE NAME REFUSES (row C5) — `verify()` has NO default action, or a typed
 *     turn's token could pay for a microphone turn; only after the config gate, though.
 * =========================================================================== */
{
  const cfg = envlib.readConfig(ARMED);
  for (const bad of ["speech", "", null, undefined, "CHAT", "chat ", 7, {}]) {
    fresh();
    const v = await ts.verify(cfg, req({}), TOKEN, bad);
    deep([v.ok, v.reason, verifyCalls()], [false, "turnstile_misconfigured", 0],
         `verify() for route ${JSON.stringify(bad)} REFUSES rather than guessing (our fault, Cloudflare not asked)`);
  }
  deep(["chat", "transcribe", "speech"].map(ts.actionFor), [ACT.chat, ACT.transcribe, ""],
       "actionFor() maps each spending route to its action, and a route with no widget to none");
  fresh();
  const v = await ts.verify(envlib.readConfig(GATEWAY), req({}), TOKEN, "nonsense");
  deep([v.ok, v.outcome], [true, "skipped"], "with no secret configured even a bad route name is a clean no-op");
}
