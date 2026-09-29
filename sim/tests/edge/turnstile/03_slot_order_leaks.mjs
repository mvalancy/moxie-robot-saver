/* test_turnstile — §6–8: the slot comes back, cheapest refusal first, nothing leaks. Run via the entry file, never alone. */
import {
  ACT, ARMED, KEY, P, SECRET_PASS, SITEKEY, TOKEN, assertClean, chat, deep, envlib, eq,
  fresh, gatewayCalls, limits, ok, post, req, ts, turn, verifyCalls,
} from "./harness.mjs";

/* =========================================================================== *
 * 6. THE CONCURRENCY SLOT COMES BACK ON EVERY REFUSAL PATH (D2) — a leaked slot fails
 *    CLOSED. Proven by the recorded in-flight count AND by a good turn served after three
 *    ceilings' worth of refusals.
 * =========================================================================== */
{
  const CEIL = 2;
  const tight = Object.assign({}, ARMED, {
    DEMO_MAX_CONCURRENT_CHAT: String(CEIL), DEMO_QUEUE_MAX_WAIT_MS: "0",
    DEMO_CHAT_PER_MIN: "100", DEMO_CACHE_COUNTER: "0",
  });
  for (const [label, plan, send, want] of [
    ["failed-verdict", { turnstile: { body: { success: false, "error-codes": ["invalid-input-response"] } } }, (i) => turn("hello " + i, tight), "turnstile_failed"],
    ["misconfigured", { turnstile: { body: { success: true, action: ACT.chat, hostname: "evil.example.com" } } }, (i) => turn("hi " + i, tight), "turnstile_misconfigured"],
    ["tokenless", {}, () => post({ text: "x" }, tight), "turnstile_failed"],
  ]) {
    fresh();
    P.plan = plan;
    for (let i = 0; i < CEIL * 3; i++) {
      eq((await send(i)).body.reason, want, `${label} refusal ${i + 1} is a ${want}`);
      eq(limits.__state().inflight.chat || 0, 0,
         `…and the in-flight count is back to ZERO after ${label} refusal ${i + 1} (a leak fails CLOSED)`);
    }
    P.plan = {};
    const after = await turn("hello again", tight);
    deep([after.status, after.body.reason, after.body.load.inflight], [200, null, 1],
         `after 6 ${label} refusals through a ceiling of 2, a good turn is SERVED with only itself in flight`);
  }
}

/* =========================================================================== *
 * 7. THE ORDER (D1) — every refusal cheaper than the bot check makes ZERO siteverify calls,
 *    and a Turnstile refusal makes ZERO gateway calls. Read off recorded counters.
 * =========================================================================== */
{
  const tok = { [ts.TOKEN_FIELD]: TOKEN };
  for (const [label, body, env, headers, want] of [
    ["a hard-blocked utterance (the safety floor)", { text: "how do i kill myself", ...tok }, ARMED, {}, "blocked"],
    ["an over-length line", { text: "x".repeat(9000), ...tok }, ARMED, {}, "too_long"],
    ["an empty line", { text: "", ...tok }, ARMED, {}, "too_short"],
    ["a tampered context blob", { text: "hi", context: "v1.forged.blob", ...tok }, ARMED, {}, "bad_request"],
    ["a forbidden origin", { text: "hi", ...tok }, ARMED, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }, "forbidden_origin"],
    ["an unconfigured gateway", { text: "hi", ...tok }, {}, {}, "gateway_not_configured"],
  ]) {
    fresh();
    const res = await chat.onRequestPost({ request: req(body, headers), env });
    await assertClean(res, "order " + label);
    eq(JSON.parse(await res.clone().text()).reason, want, `${label} answers ${want}`);
    // The case name is interpolated so the mutation table can select the one it is about.
    eq(verifyCalls(), 0, `${label}: ZERO siteverify calls — it is refused more cheaply`);
    eq(gatewayCalls(), 0, `${label}: ZERO gateway calls`);
  }

  // The rate limiter sits IN FRONT of siteverify, or a flood buys one verification each.
  fresh();
  const capped = Object.assign({}, ARMED, { DEMO_CHAT_PER_MIN: "2", DEMO_CACHE_COUNTER: "0" });
  const reasons = [];
  for (let i = 0; i < 5; i++) reasons.push((await turn("hello " + i, capped)).body.reason);
  deep(reasons, [null, null, "rate_limited", "rate_limited", "rate_limited"], "the per-IP window refuses turns 3-5");
  deep([verifyCalls(), gatewayCalls()], [2, 2], "…and only the two ADMITTED turns cost a siteverify call and a gateway call");
}

/* =========================================================================== *
 * 8. NOTHING LEAKS — the secret is structurally non-enumerable (so `JSON.stringify(cfg)`,
 *    the shape of every accidental leak, cannot carry it); the sitekey is public.
 * =========================================================================== */
{
  const cfg = envlib.readConfig(ARMED);
  eq(cfg.turnstileSecret, SECRET_PASS, "the route can READ the secret as a property");
  const dsc = Object.getOwnPropertyDescriptor(cfg, "turnstileSecret");
  ok(!!dsc, "the secret is DEFINED on the config (non-enumerably) rather than simply absent");
  deep([dsc && dsc.enumerable, dsc && dsc.writable, dsc && dsc.configurable], [false, false, false],
       "…not enumerable, writable or configurable");
  const flat = JSON.stringify(cfg);
  ok(!flat.includes(SECRET_PASS) && !flat.includes(KEY), "…so JSON.stringify(cfg) carries neither the secret nor the gateway key");
  ok(flat.includes(SITEKEY), "the SITEKEY is enumerable — it is a public value");

  // A 'helpful' error field is the other leak (row D8b); the sweep covers the raw codes.
  fresh();
  P.plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-secret", "bad-request"] } } };
  const r = await turn("hello");
  deep([r.body.reason, r.body.message], ["turnstile_misconfigured", ""], "a misconfiguration is diagnosable, with NO free-text message");
  ok(!/hostname|challenge_ts/.test(JSON.stringify(r.body)), "…nor the hostname or timestamp Cloudflare reported");
}
