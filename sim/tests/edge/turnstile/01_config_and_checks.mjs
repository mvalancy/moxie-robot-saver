/* test_turnstile — §1–4: config gate, half a pair, the three mandatory checks, the two reasons. Run via the entry file, never alone. */
import {
  ACT, ARMED, GATEWAY, HOSTNAME, P, SECRET_PASS, SITEKEY, TOKEN, deep, envlib, eq,
  fresh, gatewayCalls, health, ok, outcomes, post, req, sent, ts, turn, verifyCalls,
} from "./harness.mjs";

const healthOf = async (env) => JSON.parse(await health.onRequestGet({ env }).clone().text());
/** A siteverify verdict differing from the passing one only in `over`. */
const verdict = (over) => ({ turnstile: { body: Object.assign({ success: true, action: ACT.chat, hostname: HOSTNAME }, over) } });

/* =========================================================================== *
 * 1. NOT CONFIGURED IS NOT ENFORCED (D4) — previews and forks keep working, and unset
 *    variables mean NO siteverify call at all, not a lenient one.
 * =========================================================================== */
{
  fresh();
  const cfg = envlib.readConfig(GATEWAY);
  eq(cfg.turnstile, false, "with no Turnstile variables, enforcement is OFF");
  eq(cfg.configured, true, "…and the gateway is still configured — the two are independent");
  eq(envlib.publicTurnstile(cfg), "", "…and no sitekey is published to the browser");
  // A sitekey but no secret (row D5): a widget whose tokens nobody checks must not render.
  const sitekeyOnly = Object.assign({}, GATEWAY, { DEMO_TURNSTILE_SITEKEY: SITEKEY });
  eq(envlib.readConfig(sitekeyOnly).turnstileSitekey, SITEKEY, "a sitekey-only deployment has the sitekey in config…");
  eq(envlib.publicTurnstile(envlib.readConfig(sitekeyOnly)), "",
     "…and STILL publishes \"\": a widget the server will not check must never be rendered");
  eq((await healthOf(sitekeyOnly)).turnstile, "", "…and /api/health withholds it too");

  const a = await turn("hello", GATEWAY);
  deep([a.status, a.body.reason, a.body.turnstile], [200, null, ""], "an unenforced deployment answers the turn normally, with an empty sitekey");
  eq(verifyCalls(), 0, "ZERO siteverify calls: the check is a no-op, not a lenient check");
  eq(gatewayCalls(), 1, "…and the gateway was called exactly once");
  eq(outcomes().skipped, 1, "the recorded outcome says `skipped`");

  // A token sent to a deployment that does not enforce is IGNORED, not rejected.
  fresh();
  eq((await post({ text: "hi", [ts.TOKEN_FIELD]: "whatever-garbage" }, GATEWAY)).status, 200,
     "an unexpected token on an unenforced deployment is ignored, not refused");
  eq(verifyCalls(), 0, "…and still costs no siteverify call");
}

/* =========================================================================== *
 * 2. HALF A PAIR IS A MISCONFIGURATION — a secret with no sitekey refuses everyone, a
 *    sitekey with no secret renders a widget nothing checks. Both read as unconfigured.
 * =========================================================================== */
{
  for (const [label, extra, missing] of [
    ["secret without sitekey", { DEMO_TURNSTILE_SECRET: SECRET_PASS }, "DEMO_TURNSTILE_SITEKEY"],
    ["sitekey without secret", { DEMO_TURNSTILE_SITEKEY: SITEKEY }, "DEMO_TURNSTILE_SECRET"],
  ]) {
    fresh();
    const env = Object.assign({}, GATEWAY, extra);
    const cfg = envlib.readConfig(env);
    eq(cfg.configured, false, `${label}: the deployment reads as UNCONFIGURED`);
    ok(cfg.missing.includes(missing) && cfg.notes.some((n) => n.includes(missing)),
       `${label}: \`missing\` and a note name the absent half (${missing})`);
    eq(cfg.turnstile, false, `${label}: enforcement is off — half a pair enforces nothing`);
    eq((await turn("hello", env)).body.reason, "gateway_not_configured", `${label}: the route answers gateway_not_configured`);
    deep([gatewayCalls(), verifyCalls()], [0, 0], `${label}: and spends NOTHING upstream or at siteverify`);
  }

  const cfg = envlib.readConfig(ARMED);
  deep([cfg.turnstile, cfg.configured, cfg.missing.length], [true, true, 0], "both halves present: enforcement is ON");
  eq(envlib.publicTurnstile(cfg), SITEKEY, "…and the PUBLIC sitekey is published to the browser");
  // `/api/health` is the browser's ONLY source of the sitekey (row H1).
  const armed = await healthOf(ARMED);
  eq(armed.turnstile, SITEKEY, "/api/health PUBLISHES the sitekey when the control is enforced — the widget's only source");
  eq(armed.mode, "live", "…on a healthy probe");
}

/* =========================================================================== *
 * 3. THE THREE MANDATORY CHECKS — each case differs from the passing one in exactly one
 *    field, so two checks out of three cannot satisfy the suite.
 * =========================================================================== */
{
  fresh();
  const good = await turn("hello moxie");
  deep([good.status, good.body.reason, good.body.turnstile], [200, null, SITEKEY],
       "all three checks pass: the turn is served, carrying the sitekey for the next turn");
  deep([verifyCalls(), gatewayCalls(), outcomes().verified], [1, 1, 1], "…after ONE siteverify call and ONE gateway call, recorded `verified`");

  // The outbound request is the only place the secret may appear.
  const call = sent.find((s) => s.url === ts.SITEVERIFY_URL) || { opt: {} };
  eq(call.opt.method, "POST", "the verification is a POST to Cloudflare's documented siteverify URL");
  eq((call.opt.headers || {})["Content-Type"], "application/x-www-form-urlencoded", "…form-encoded, as the contract requires");
  const form = new URLSearchParams(String(call.opt.body));
  deep(Object.fromEntries(form), { secret: SECRET_PASS, response: TOKEN, remoteip: "203.0.113.9" },
       "…carrying the secret, the token and the visitor's address, and NOTHING else");
  eq(call.opt.redirect, "manual", "redirects are NOT followed — this request carries a secret in its body");

  // §3.2's envelope is ONE shape: the sitekey rides refusals and blocks too (rows D5c/D5d).
  fresh();
  P.plan = verdict({ success: false, "error-codes": ["invalid-input-response"] });
  eq((await turn("hello")).body.turnstile, SITEKEY, "a REFUSAL envelope carries the sitekey too");
  fresh();
  const blockedTurn = await post({ text: "how do i kill myself", [ts.TOKEN_FIELD]: TOKEN });
  deep([blockedTurn.body.reason, blockedTurn.body.turnstile], ["blocked", SITEKEY], "a safety-blocked turn is the third shape, which carries it as well");

  // CHECK 1: success. A valid action AND hostname alongside `success:false` (row C1).
  fresh();
  P.plan = verdict({ success: false, "error-codes": ["invalid-input-response"] });
  const c1 = await turn("hello");
  eq(c1.body.reason, "turnstile_failed", "CHECK 1 — success:false REFUSES (fail closed)");
  deep([c1.status, c1.res.headers.get("Retry-After"), gatewayCalls(), outcomes().failed], [403, null, 0, 1],
       "…403, no Retry-After (a fresh token is a tap away), ZERO gateway calls, recorded `failed`");

  // CHECK 2: action — present, and compared EXACTLY (rows C2, C2b, C2c).
  for (const [label, action] of [
    ["ANOTHER action (`newsletter`)", "newsletter"],
    ["an ABSENT action", undefined],
    ["a PREFIX of ours (`chat-newsletter`)", "chat-newsletter"],
    ["a SUFFIX around ours (`x-chat`)", "x-chat"],
    ["ours in the WRONG CASE (`CHAT`)", "CHAT"],
    ["ours with whitespace (` chat `)", " chat "],
    ["the OTHER route's action (`transcribe`)", ACT.transcribe],
  ]) {
    fresh();
    P.plan = verdict({ action });
    eq((await turn("hello")).body.reason, "turnstile_failed", `CHECK 2 — an action that is ${label} is refused even though success:true`);
    eq(gatewayCalls(), 0, `…and ${label} reaches the gateway ZERO times`);
  }

  // CHECK 3: hostname. A mismatch is `turnstile_misconfigured` (the allowance is config, and
  // it refuses every visitor alike until fixed). Exact match: Turnstile itself authorizes subdomains.
  fresh();
  P.plan = verdict({ hostname: "evil.example.com" });
  const c3 = await turn("hello");
  eq(c3.body.reason, "turnstile_misconfigured", "CHECK 3 — a challenge solved on a foreign hostname is refused");
  deep([c3.status, c3.res.headers.get("Retry-After"), gatewayCalls(), outcomes().misconfigured], [503, "60", 0, 1],
       "…503 with upstream_down's 60 s Retry-After, zero gateway calls, recorded `misconfigured`");
  for (const [label, hostname] of [
    ["an EMPTY hostname is refused, not treated as 'unknown, allow'", ""],
    ["a hostname that merely ENDS WITH ours is refused: the match is exact", "evil-" + HOSTNAME],
    ["a SUBDOMAIN of ours is refused", "sub." + HOSTNAME],
  ]) {
    fresh();
    P.plan = verdict({ hostname });
    eq((await turn("hello")).body.reason, "turnstile_misconfigured", `CHECK 3 — ${label}`);
  }

  // The default allowance is the request's OWN hostname — never `localhost` by omission.
  const bare = envlib.readConfig(ARMED);
  deep(bare.turnstileHosts, [], "with DEMO_TURNSTILE_HOSTS unset the list is empty…");
  deep(["demo.invalid.test", "localhost", "127.0.0.1"].map((h) => ts.hostAllowed(bare, req({}), h)), [true, false, false],
       "…and the allowance is exactly the request's own hostname");
  // An explicit list REPLACES the default, and is matched exactly too (row C3b).
  const hostsEnv = Object.assign({}, ARMED, { DEMO_TURNSTILE_HOSTS: "moxie.example.com, https://other.example.com/sim ,MOXIE.EXAMPLE.COM" });
  const listed = envlib.readConfig(hostsEnv);
  deep(listed.turnstileHosts, ["moxie.example.com", "other.example.com"],
       "DEMO_TURNSTILE_HOSTS is parsed, lower-cased, de-duplicated, and a URL is reduced to its host");
  deep(["moxie.example.com", HOSTNAME].map((h) => ts.hostAllowed(listed, req({}), h)), [true, false],
       "a listed host is allowed, and the request's own host is NOT once a list is given");
  deep(["evil-moxie.example.com", "sub.moxie.example.com"].map((h) => ts.hostAllowed(listed, req({}), h)), [false, false],
       "a suffix or subdomain of a listed host is refused: the configured match is exact too");
  fresh();
  P.plan = verdict({ hostname: "evil-moxie.example.com" });
  eq((await turn("hello", hostsEnv)).body.reason, "turnstile_misconfigured",
     "…and the ROUTE refuses a suffix match against DEMO_TURNSTILE_HOSTS, end to end");

  // A MISSING TOKEN is refused without asking Cloudflare: free, and no amplifier (row D1b).
  for (const [label, token] of [["no field at all", undefined], ["an empty string", ""], ["whitespace", "   "], ["a non-string", 42]]) {
    fresh();
    const body = { text: "hello" };
    if (token !== undefined) body[ts.TOKEN_FIELD] = token;
    eq((await post(body)).body.reason, "turnstile_failed", `a request with ${label} is refused`);
    deep([verifyCalls(), gatewayCalls(), outcomes().no_token], [0, 0, 1],
         `…for FREE: ${label} costs no siteverify or gateway call, recorded \`no_token\``);
  }
}

/* =========================================================================== *
 * 4. THE TWO REASONS ARE THE OPERATOR'S DIAGNOSIS (D8) — "our config is wrong" and "your
 *    token is bad" have opposite fixes.
 * =========================================================================== */
for (const [code, want] of [
  ["invalid-input-secret", "turnstile_misconfigured"],
  ["missing-input-secret", "turnstile_misconfigured"],
  ["bad-request", "turnstile_misconfigured"],
  ["invalid-input-response", "turnstile_failed"],
  ["missing-input-response", "turnstile_failed"],
  ["timeout-or-duplicate", "turnstile_failed"],     // a replayed token
  ["some-code-cloudflare-has-not-invented-yet", "turnstile_failed"],
]) {
  fresh();
  P.plan = { turnstile: { body: { success: false, "error-codes": [code] } } };
  eq((await turn("hello")).body.reason, want, `error-code ${code} maps to ${want}`);
  eq(gatewayCalls(), 0, `…and ${code} spends nothing upstream`);
}
