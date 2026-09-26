/* test_turnstile — §5–5c: fail open on transport failure, a wrong secret is HTTP 400, unknown route. Run via the entry file, never alone. */
import {
  ACT, ARMED, GATEWAY, P, SECRET_PASS, TOKEN, chat, envlib,
  eq, fresh, gatewayCalls, limits, ok, outcomes, req, transcribe,
  ts, turn, verifyCalls,
} from "./harness.mjs";

/* =========================================================================== *
 * 5. FAIL OPEN ON A TRANSPORT FAILURE — every shape of it
 * =========================================================================== *
 * A fail-open that became fail-closed would take the demo down whenever a third-party
 * endpoint has a bad ten minutes. The spend is already capped, so failing open is bounded.
 */
{
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
    /* LOOKS LIKE A VERDICT AND IS NOT: a 500 whose body parses as `{"success": false}` is a
     * Cloudflare failure, not a failed challenge (mutation row D3c — with an empty body the
     * missing `res.ok` check fell open only by accident). */
    ["it answers 500 with a body that PARSES as a failed verdict",
     { status: 500, text: JSON.stringify({ success: false, "error-codes": ["invalid-input-response"] }) }],
  ]) {
    fresh();
    P.plan = { turnstile: p };
    const r = await turn("hello");
    eq(r.status, 200, `FAIL OPEN — ${label}: the turn is still served`);
    eq(r.body.reason, null, `FAIL OPEN — ${label}: with no reason`);
    eq(gatewayCalls(), 1, `FAIL OPEN — ${label}: and the gateway WAS called`);
    eq(outcomes().unreachable, 1, `FAIL OPEN — ${label}: recorded as \`unreachable\``);
  }

  // The deadline is real, bounded, and cannot be configured out of usefulness.
  eq(envlib.readConfig(ARMED).turnstileTimeoutMs, 2000, "the siteverify deadline defaults to 2 s");
  eq(envlib.readConfig(Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "999999" })).turnstileTimeoutMs,
     2000, "…and an out-of-range override falls back to the default rather than out-waiting the route");
  eq(envlib.readConfig(Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "1" })).turnstileTimeoutMs,
     2000, "…in both directions: a deadline nothing could meet would switch the check off by stealth");
  eq(envlib.readConfig(Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "500" })).turnstileTimeoutMs,
     500, "…while a sane override is honoured");

  /* ---- AND THE DEADLINE IS WIRED, not merely configured -------------------- *
   * The check runs with a concurrency slot held, so a siteverify that never answers would
   * keep the slot (and the FIFO behind it) for the route's 20 s timeout. The stub HONOURS
   * `opt.signal`, so an unwired deadline hangs here (mutation row D3e). */
  fresh();
  const quick = Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "120" });
  P.plan = { turnstile: { hang: true } };
  /* A REF'D TIMER for the length of this assertion: node's `AbortSignal.timeout()` timer
   * is unref'd, so with nothing else pending node would exit 13 before the 120 ms deadline
   * fires. On Cloudflare the pending request plays this interval's role. */
  const keepAlive = setInterval(() => {}, 25);
  const started = Date.now();
  const hung = await turn("hello", quick);
  const elapsed = Date.now() - started;
  clearInterval(keepAlive);
  eq(hung.status, 200, "a siteverify that NEVER answers still lets the turn through (fail open)");
  eq(gatewayCalls(), 1, "…and the gateway was reached");
  ok(elapsed >= 100, `…after our own deadline fired rather than immediately (${elapsed} ms)`);
  ok(elapsed < 5000, `…and long before the route's 20 s upstream timeout (${elapsed} ms)`);
  eq(limits.__state().inflight.chat || 0, 0, "…with the concurrency slot given back");
}

/* =========================================================================== *
 * 5b. A WRONG SECRET IS **HTTP 400**, AND THAT IS NOT A TRANSPORT FAILURE
 * =========================================================================== *
 * Measured against the real endpoint:
 *
 *     secret=<garbage>   -> 400 {"error-codes":["invalid-input-secret"],"success":false}
 *     (no secret field)  -> 400 {"error-codes":["missing-input-secret"],"success":false}
 *     always-fails 2x…AA -> 200 {"error-codes":["invalid-input-response"],…}
 *     already-spent 3x…AA-> 200 {"error-codes":["timeout-or-duplicate"],…}
 *     missing response   -> 200 {"error-codes":["missing-input-response"],…}
 *
 * Every genuine VERDICT is a 200; the 400s are our configuration. Failing open on `!res.ok`
 * would let a mistyped secret switch the control off silently, so our-fault codes refuse at
 * ANY status and everything else still fails open.
 */
{
  /* ---- our fault, at the status Cloudflare really uses -------------------- */
  for (const code of ["invalid-input-secret", "missing-input-secret", "bad-request"]) {
    fresh();
    P.plan = { turnstile: { status: 400,
                          text: JSON.stringify({ "error-codes": [code], success: false, messages: [] }) } };
    const r = await turn("hello");
    eq(r.body.reason, "turnstile_misconfigured",
       `a 400 naming ${code} is OUR fault and REFUSES — it is not a transport failure`);
    eq(r.status, 503, `…with 503, like any other deployment-level fault (${code})`);
    eq(gatewayCalls(), 0, `…and ZERO gateway calls: a wrong secret spends nothing (${code})`);
    eq(outcomes().misconfigured, 1, `…recorded as \`misconfigured\`, not \`unreachable\` (${code})`);
  }

  /* ---- and the SAME body at 200, because the mapping must not depend on the status --- */
  fresh();
  P.plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-secret"] } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "…and the same code at status 200 maps the same way: the reason is the CODE's, not the status's");

  /* ---- EVERYTHING ELSE AT A NON-2xx STILL FAILS OPEN --------------------- *
   * D3's split is unchanged for every shape that is genuinely Cloudflare's problem. These
   * are the cases that would break if the fix above had been "read the body and believe
   * it", which is the over-correction: a 500 is not a verdict however it is spelled. */
  for (const [label, p2] of [
    ["a 400 whose codes name the VISITOR's token, not our secret",
     { status: 400, text: JSON.stringify({ success: false, "error-codes": ["invalid-input-response"] }) }],
    ["a 400 with no error-codes at all", { status: 400, text: JSON.stringify({ success: false }) }],
    ["a 400 that is not JSON", { status: 400, text: "<html>bad request</html>" }],
    ["a 500 naming our secret (Cloudflare failing, not us)",
     { status: 500, text: JSON.stringify({ success: false, "error-codes": ["internal-error"] }) }],
    ["a 429 from the endpoint itself", { status: 429, text: "" }],
  ]) {
    fresh();
    P.plan = { turnstile: p2 };
    const r = await turn("hello");
    eq(r.body.reason, null, `FAIL OPEN — ${label}: the turn is still served`);
    eq(gatewayCalls(), 1, `FAIL OPEN — ${label}: and the gateway WAS called`);
    eq(outcomes().unreachable, 1, `FAIL OPEN — ${label}: recorded as \`unreachable\``);
  }

  /* ---- and nothing from a 400 body is ever forwarded ---------------------- *
   * The 400 body is now PARSED, which it never used to be, so the leak sweep matters more
   * here than anywhere: `error-codes` goes into a boolean and is dropped. */
  fresh();
  P.plan = { turnstile: { status: 400, text: JSON.stringify({
    "error-codes": ["invalid-input-secret"], success: false,
    messages: ["your secret " + SECRET_PASS + " is wrong"] }) } };
  const leaky = await turn("hello");
  eq(leaky.body.reason, "turnstile_misconfigured", "a hostile 400 body is still diagnosed…");
  ok(!JSON.stringify(leaky.body).includes(SECRET_PASS),
     "…and even a body that ECHOES THE SECRET BACK cannot put it in a response");
  ok(!JSON.stringify(leaky.body).includes("invalid-input-secret"),
     "…nor the raw code");
}

/* =========================================================================== *
 * 5c. AN UNKNOWN ROUTE NAME REFUSES (the client's `getToken(action)`, server side)
 * =========================================================================== *
 * `verify()` has NO DEFAULT action: defaulting to `chat` would let a typed turn's token pay
 * for a microphone turn. An unknown route fails CLOSED as `turnstile_misconfigured` (no
 * token can fix it) — but only after the config gate, so an unconfigured fork is unaffected.
 */
{
  const cfg = envlib.readConfig(ARMED);
  const rq = req({});
  for (const bad of ["speech", "", null, undefined, "CHAT", "chat ", 7, {}]) {
    fresh();
    const v = await ts.verify(cfg, rq, TOKEN, bad);
    eq(v.ok, false, `verify() for route ${JSON.stringify(bad)} REFUSES rather than guessing`);
    eq(v.reason, "turnstile_misconfigured", `…as our fault, not the visitor's (${JSON.stringify(bad)})`);
    eq(verifyCalls(), 0, `…without asking Cloudflare anything (${JSON.stringify(bad)})`);
  }
  for (const good of ["chat", "transcribe"]) {
    eq(ts.actionFor(good), ACT[good], `actionFor(${good}) is the action that route requires`);
  }
  eq(ts.actionFor("speech"), "", "…and a route with no widget has no action");

  // The unconfigured case, which must be untouched by any of the above.
  fresh();
  const off = envlib.readConfig(GATEWAY);
  const v = await ts.verify(off, rq, TOKEN, "nonsense");
  eq(v.ok, true, "with no secret configured even a bad route name is a clean no-op…");
  eq(v.outcome, "skipped", "…recorded as skipped: a fork cannot be broken by our bug");
}
