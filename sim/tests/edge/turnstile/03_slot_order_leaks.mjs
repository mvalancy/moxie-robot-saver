/* test_turnstile — §6–8: the slot comes back, cheapest refusal first, nothing leaks. Run via the entry file, never alone. */
import {
  ACT, ARMED, C, ERROR_CODES, KEY, P, SECRET_PASS, SITEKEY,
  TOKEN, assertClean, chat, deep, envlib, eq, fails, fresh,
  gatewayCalls, limits, ok, post, req, ts, turn, verifyCalls,
} from "./harness.mjs";

/* =========================================================================== *
 * 6. THE CONCURRENCY SLOT COMES BACK ON THE NEW REFUSAL PATH (D2)
 * =========================================================================== *
 * A new early return that forgets `slot.release()` leaks a slot FOR EVER: the in-flight
 * count drifts upward and the route starts refusing visitors who should be served. That
 * is failing CLOSED, and it is exactly the hazard that got a cache-backed concurrency
 * ceiling rejected in `_lib/limits.js` — an eventually-consistent counter cannot hold a
 * resource that must be given back.
 *
 * It is proven two ways, because the counter and the behaviour are different claims: the
 * recorded in-flight count returns to zero, AND a ceiling's worth of consecutive refusals
 * does not stop the next visitor being served.
 */
{
  const CEIL = 2;
  const tight = Object.assign({}, ARMED, {
    DEMO_MAX_CONCURRENT_CHAT: String(CEIL),
    DEMO_QUEUE_MAX_WAIT_MS: "0",     // no FIFO: at the ceiling, refuse instantly
    DEMO_CHAT_PER_MIN: "100",        // so the rate limiter is not what is being measured
    DEMO_CACHE_COUNTER: "0",
  });

  fresh();
  P.plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-response"] } } };
  for (let i = 0; i < CEIL * 3; i++) {
    const r = await turn("hello " + i, tight);
    eq(r.body.reason, "turnstile_failed", `refusal ${i + 1} is a Turnstile refusal`);
    eq(limits.__state().inflight.chat || 0, 0,
       `…and the in-flight count is back to ZERO after refusal ${i + 1} (a leak fails CLOSED)`);
  }

  // The behavioural half: after six refusals through a ceiling of two, a good token is
  // still served. With a leaked slot this is `at_capacity` and the demo is dead.
  P.plan = {};
  const after = await turn("hello again", tight);
  eq(after.status, 200, "after 6 Turnstile refusals through a ceiling of 2, a good turn is SERVED");
  eq(after.body.reason, null, "…with no reason — the slots were all given back");
  eq(after.body.load.inflight, 1, "…and the load count saw exactly this one turn in flight");

  // The same for the OTHER refusal reason, since it is a different return statement.
  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "evil.example.com" } } };
  for (let i = 0; i < CEIL * 3; i++) {
    eq((await turn("hi " + i, tight)).body.reason, "turnstile_misconfigured", `misconfig refusal ${i + 1}`);
    eq(limits.__state().inflight.chat || 0, 0, `…and no slot leaked on refusal ${i + 1}`);
  }
  P.plan = {};
  eq((await turn("hi again", tight)).status, 200,
     "…and a good turn is still served after six of THOSE too");

  // And the tokenless refusal, which returns from the same place without a network call.
  fresh();
  for (let i = 0; i < CEIL * 3; i++) {
    eq((await post({ text: "x" }, tight)).body.reason, "turnstile_failed", `tokenless refusal ${i + 1}`);
    eq(limits.__state().inflight.chat || 0, 0, `…and no slot leaked on tokenless refusal ${i + 1}`);
  }
  eq((await turn("ok", tight)).status, 200, "…and a good turn is still served after those as well");
}

/* =========================================================================== *
 * 7. THE ORDER (D1) — cheapest refusal first, in BOTH directions
 * =========================================================================== *
 * Two claims, and they are not the same claim:
 *
 *   · every refusal CHEAPER than the bot check makes ZERO siteverify calls. Otherwise a
 *     hard-blocked utterance buys a round trip to prove the visitor is human before being
 *     told no, and — worse — `admit()` stops protecting siteverify from being turned into
 *     an amplifier by a flood.
 *   · a Turnstile refusal makes ZERO gateway calls. Otherwise the control is decorative.
 *
 * Both are read off RECORDED counters (`noteUpstreamCall()` and `__stats().calls`), not
 * inferred from a stub that may or may not have been reached.
 */
{
  const cases = [
    ["a hard-blocked utterance (the safety floor)", { text: "how do i kill myself", [ts.TOKEN_FIELD]: TOKEN }, ARMED, {}, "blocked"],
    ["an over-length line", { text: "x".repeat(9000), [ts.TOKEN_FIELD]: TOKEN }, ARMED, {}, "too_long"],
    ["an empty line", { text: "", [ts.TOKEN_FIELD]: TOKEN }, ARMED, {}, "too_short"],
    ["a tampered context blob", { text: "hi", context: "v1.forged.blob", [ts.TOKEN_FIELD]: TOKEN }, ARMED, {}, "bad_request"],
    ["a forbidden origin", { text: "hi", [ts.TOKEN_FIELD]: TOKEN }, ARMED,
     { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }, "forbidden_origin"],
    ["an unconfigured gateway", { text: "hi", [ts.TOKEN_FIELD]: TOKEN }, {}, {}, "gateway_not_configured"],
  ];
  for (const [label, body, env, headers, want] of cases) {
    fresh();
    const res = await chat.onRequestPost({ request: req(body, headers), env });
    await assertClean(res, "order " + label);
    const parsed = JSON.parse(await res.clone().text());
    eq(parsed.reason, want, `${label} answers ${want}`);
    // The case name is INTERPOLATED into these two on purpose. Without it every one of the
    // six cases produced the identical failure line, so a mutation table could not select
    // the one it was about — row D1 reported WRONG CHECK for exactly that reason.
    eq(verifyCalls(), 0, `${label}: ZERO siteverify calls — it is refused more cheaply`);
    eq(gatewayCalls(), 0, `${label}: ZERO gateway calls`);
  }

  /* The rate limiter, which is the specific thing that has to sit IN FRONT of siteverify:
   * it is what stops a flood being answered by one outbound verification each. Five turns
   * at `chat_per_min: 2` — the first two verify, the rest are refused for free. */
  fresh();
  const capped = Object.assign({}, ARMED, { DEMO_CHAT_PER_MIN: "2", DEMO_CACHE_COUNTER: "0" });
  const reasons = [];
  for (let i = 0; i < 5; i++) reasons.push((await turn("hello " + i, capped)).body.reason);
  deep(reasons, [null, null, "rate_limited", "rate_limited", "rate_limited"],
       "the per-IP window refuses turns 3-5");
  eq(verifyCalls(), 2,
     "…and only the two ADMITTED turns cost a siteverify call: admit() protects Cloudflare too");
  eq(gatewayCalls(), 2, "…and only those two reached the gateway");
}

/* =========================================================================== *
 * 8. NOTHING LEAKS — the secret, and Cloudflare's own error strings
 * =========================================================================== */
{
  const cfg = envlib.readConfig(ARMED);

  // The structural guard, the same one the gateway key has: non-enumerable, so the shape
  // of every accidental leak — `JSON.stringify(cfg)` — cannot carry it.
  eq(cfg.turnstileSecret, SECRET_PASS, "the route can READ the secret as a property");
  ok(!Object.keys(cfg).includes("turnstileSecret"), "…but it is NOT enumerable");
  ok(!JSON.stringify(cfg).includes(SECRET_PASS), "…so JSON.stringify(cfg) cannot contain it");
  ok(!JSON.stringify(cfg).includes(KEY), "…and still cannot contain the gateway key");
  // Defensive `|| {}`: with the definition removed the descriptor is `undefined`, and a
  // bare `.writable` THREW — which exited node before a single `FAIL:` line was printed,
  // so the mutation table saw an unattributable non-zero exit rather than a named red
  // check (row D5b, reported WRONG CHECK). A guard's test must fail legibly.
  const dsc = Object.getOwnPropertyDescriptor(cfg, "turnstileSecret") || {};
  ok(!!Object.getOwnPropertyDescriptor(cfg, "turnstileSecret"),
     "the secret is DEFINED on the config (non-enumerably) rather than simply absent");
  eq(dsc.writable, false, "…and it is not writable");
  eq(dsc.configurable, false, "…nor configurable, so nothing can redefine it into view");

  // The SITEKEY is public and MUST be visible: the browser cannot render a widget without
  // it. This is the one asymmetry in this block and it is deliberate.
  ok(JSON.stringify(cfg).includes(SITEKEY), "the SITEKEY is enumerable — it is a public value");

  // Every response on every path, including the ones that carry a Cloudflare failure.
  // `assertClean` has already swept each of these; this asserts the sweep actually ran on
  // a meaningful number of them rather than on the two happy cases.
  ok(C.sweeps > 60, `the sweep ran on every response produced above (${C.sweeps} sweeps)`);

  // And the one thing a "helpful" error field would leak: the codes themselves.
  fresh();
  P.plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-secret", "bad-request"] } } };
  const r = await turn("hello");
  const text = JSON.stringify(r.body);
  eq(r.body.reason, "turnstile_misconfigured", "a misconfiguration is diagnosable…");
  eq(r.body.message, "", "…and carries NO free-text message at all");
  for (const code of ERROR_CODES) ok(!text.includes(code), `…and never forwards ${code}`);
  ok(!text.includes("hostname"), "…nor the hostname Cloudflare reported");
  ok(!text.includes("challenge_ts"), "…nor the challenge timestamp");
}
