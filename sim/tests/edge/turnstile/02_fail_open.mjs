/* test_turnstile — §5–5c: fail open on transport failure, a wrong secret is HTTP 400, unknown route. Run via the entry file, never alone. */
import {
  ACT, ARMED, GATEWAY, P, SECRET_PASS, TOKEN, chat, envlib,
  eq, fresh, gatewayCalls, limits, ok, outcomes, req, transcribe,
  ts, turn, verifyCalls,
} from "./harness.mjs";

/* =========================================================================== *
 * 5. FAIL OPEN ON A TRANSPORT FAILURE — every shape of it
 * =========================================================================== *
 * The other half of the split, and the half a green suite would never notice going wrong:
 * a fail-open that quietly became a fail-closed takes the public demo down for every
 * visitor the moment a third-party endpoint has a bad ten minutes. Per-IP limits and the
 * unit budget already cap the spend, so the cost of failing open is bounded and the cost
 * of failing closed is the whole demo.
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
    /* THE ONE THAT LOOKS LIKE A VERDICT AND IS NOT. A 500 whose body happens to parse as
     * `{"success": false}` is a Cloudflare failure wearing a verdict's clothes; without
     * the `res.ok` check it would be read as "the visitor failed the challenge" and a
     * Cloudflare outage would refuse every visitor. An earlier draft of this block had no
     * such case, so deleting `if (!res.ok)` was NOT CAUGHT (mutation row D3c): a 500 with
     * an EMPTY body simply threw in `res.json()` and fell open by accident. */
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
   * THE FAILURE THIS CATCHES IS A HANG, WHICH IS THE WORST ONE AVAILABLE HERE: the check
   * runs with a concurrency slot held, so a siteverify that never answers would keep that
   * slot — and everyone in the FIFO behind it — until the route's own 20 s timeout. A
   * configured number nothing passes to `fetch` looks identical in every other assertion
   * in this file, which is why mutation row D3e exists and why this stub HONOURS
   * `opt.signal` rather than ignoring it the way a convenient stub would. */
  fresh();
  const quick = Object.assign({}, ARMED, { DEMO_TURNSTILE_TIMEOUT_MS: "120" });
  P.plan = { turnstile: { hang: true } };
  /* A REF'D TIMER, held only for the length of this one assertion, and it is not a hack —
   * it is a property of `AbortSignal.timeout()` under node that has to be worked around
   * HERE because it does not exist in the runtime the code ships to. Node's timeout signal
   * uses an UNREF'D timer: with nothing else pending, the event loop drains and node exits
   * `13` ("unsettled top-level await") BEFORE the 120 ms deadline can fire. A Cloudflare
   * isolate always has the request itself pending, so the signal always fires there. This
   * interval stands in for that pending request, and is cleared immediately after. */
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
 * THE BUG THIS BLOCK EXISTS FOR, and it switched the whole control off.
 *
 * Cloudflare answers `invalid-input-secret` and `missing-input-secret` with **status
 * 400** — measured against the real endpoint on 2026-09-05, not recalled:
 *
 *     secret=<garbage>   -> 400 {"error-codes":["invalid-input-secret"],"success":false}
 *     (no secret field)  -> 400 {"error-codes":["missing-input-secret"],"success":false}
 *     always-fails 2x…AA -> 200 {"error-codes":["invalid-input-response"],…}
 *     already-spent 3x…AA-> 200 {"error-codes":["timeout-or-duplicate"],…}
 *     missing response   -> 200 {"error-codes":["missing-input-response"],…}
 *
 * Every genuine VERDICT is a 200; the 400s are our own configuration. The first version
 * of `verify()` returned `{ok: true}` on any `!res.ok` WITHOUT READING THE BODY, so a
 * `DEMO_TURNSTILE_SECRET` wrong by one character meant: the sitekey published, the widget
 * rendered, every visitor minting a genuine token, every siteverify answering 400, every
 * request ALLOWED THROUGH, real money spent on all of them, a healthy LIVE badge on the
 * page — and `turnstile_misconfigured`, the reason whose entire purpose is to diagnose
 * exactly this without anyone printing the secret, unreachable. The operator's documented
 * validation procedure ("deploy, type one sentence, read the reason") returned a
 * perfectly healthy turn.
 *
 * §5's non-200 cases were 500/403/302 only, and this file's stub served every
 * `plan.turnstile.body` at status 200 — so the whole D8 mapping block exercised
 * `invalid-input-secret` at a status Cloudflare never uses for it. Both halves are fixed
 * here: the codes that mean OUR fault refuse at ANY status, and everything else still
 * fails open.
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
 * `verify()` has NO DEFAULT action, deliberately: the default that reads best (`chat`)
 * would make a microphone turn payable with a typed turn's token, which is the exact
 * cross-route replay `TURNSTILE_ACTIONS` exists to refuse. So a route name the table does
 * not know fails CLOSED — and, because no visitor's token can fix a route name, it is
 * `turnstile_misconfigured` rather than `turnstile_failed`.
 *
 * AND IT STILL DOES NOTHING ON AN UNCONFIGURED DEPLOYMENT, which is why the check sits
 * after the config gate: a fork or a preview may not be broken by a programming error in
 * a route it does not run.
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
