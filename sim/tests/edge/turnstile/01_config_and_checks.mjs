/* test_turnstile — §1–4: config gate, half a pair, the three mandatory checks, the two reasons. Run via the entry file, never alone. */
import {
  ACT, ARMED, GATEWAY, HOSTNAME, P, SECRET_FAIL, SECRET_PASS, SECRET_SPENT,
  SITEKEY, TOKEN, assertClean, chat, deep, envelope, envlib, eq,
  fails, fresh, gatewayCalls, health, join, ok, outcomes, post,
  readFileSync, repo, req, sent, transcribe, ts, turn, verifyCalls,
} from "./harness.mjs";

/* =========================================================================== *
 * 1. NOT CONFIGURED IS NOT ENFORCED (D4, and C5's fail-safe default)
 * =========================================================================== *
 * Keeps previews and forks working: a preview hostname is not on the widget's list, so a
 * challenge there could never pass. Unset variables mean NO call at all, not a lenient one.
 */
{
  fresh();
  const cfg = envlib.readConfig(GATEWAY);
  eq(cfg.turnstile, false, "with no Turnstile variables, enforcement is OFF");
  eq(cfg.configured, true, "…and the gateway is still configured — the two are independent");
  eq(envlib.publicTurnstile(cfg), "", "…and no sitekey is published to the browser");
  /* THE CASE THAT TESTS `publicTurnstile`'s CONDITION (mutation row D5): a sitekey but no
   * secret. The browser must not be handed a sitekey the server will not check — it would
   * render a widget and mint tokens that are all ignored. */
  const halfCfg = envlib.readConfig(Object.assign({}, GATEWAY, { DEMO_TURNSTILE_SITEKEY: SITEKEY }));
  eq(halfCfg.turnstileSitekey, SITEKEY, "a sitekey-only deployment has the sitekey in config…");
  eq(envlib.publicTurnstile(halfCfg), "",
     "…and STILL publishes \"\": a widget the server will not check must never be rendered");
  const halfProbe = JSON.parse(await health.onRequestGet({ env: Object.assign({}, GATEWAY,
    { DEMO_TURNSTILE_SITEKEY: SITEKEY }) }).clone().text());
  eq(halfProbe.turnstile, "", "…and /api/health withholds it too");

  const a = await turn("hello", GATEWAY);
  eq(a.status, 200, "an unenforced deployment answers the turn normally");
  eq(a.body.reason, null, "…with no reason");
  eq(verifyCalls(), 0, "ZERO siteverify calls: the check is a no-op, not a lenient check");
  eq(gatewayCalls(), 1, "…and the gateway was called exactly once");
  eq(outcomes().skipped, 1, "the recorded outcome says `skipped`");
  eq(a.body.turnstile, "", "the envelope publishes an EMPTY sitekey when not enforced");

  // A token sent to a deployment that does not enforce is IGNORED, not rejected — the
  // same "ignoring cannot drift" rule the upstream body follows (chat.js's header).
  fresh();
  const b = await post({ text: "hi", [ts.TOKEN_FIELD]: "whatever-garbage" }, GATEWAY);
  eq(b.status, 200, "an unexpected token on an unenforced deployment is ignored, not refused");
  eq(verifyCalls(), 0, "…and still costs no siteverify call");

  // The probe, which is how the browser learns there is nothing to render.
  const hres = health.onRequestGet({ env: GATEWAY });
  const hbody = JSON.parse(await hres.clone().text());
  await assertClean(hres, "health unenforced");
  eq(hbody.turnstile, "", "/api/health publishes \"\" when the control is not enforced");
  ok(envelope.PUBLIC_KEYS.includes("turnstile"), "`turnstile` is in the envelope's key allowlist");
}

/* =========================================================================== *
 * 2. HALF A PAIR IS A MISCONFIGURATION, NOT A PARTIAL CONTROL
 * =========================================================================== *
 * As with `ACCESS_VARS`: a secret with no sitekey refuses every visitor (no browser can
 * mint a token); a sitekey with no secret renders a widget nothing verifies. Both read as
 * unconfigured, which spends nothing.
 */
{
  for (const [label, env, missing] of [
    ["secret without sitekey", Object.assign({}, GATEWAY, { DEMO_TURNSTILE_SECRET: SECRET_PASS }),
     "DEMO_TURNSTILE_SITEKEY"],
    ["sitekey without secret", Object.assign({}, GATEWAY, { DEMO_TURNSTILE_SITEKEY: SITEKEY }),
     "DEMO_TURNSTILE_SECRET"],
  ]) {
    fresh();
    const cfg = envlib.readConfig(env);
    eq(cfg.configured, false, `${label}: the deployment reads as UNCONFIGURED`);
    ok(cfg.missing.includes(missing), `${label}: \`missing\` names the absent half (${missing})`);
    ok(cfg.notes.some((n) => n.includes(missing)),
       `${label}: …and a note says which half, so an operator never has to read the secret`);
    eq(cfg.turnstile, false, `${label}: enforcement is off — half a pair enforces nothing`);

    const r = await turn("hello", env);
    eq(r.body.reason, "gateway_not_configured", `${label}: the route answers gateway_not_configured`);
    eq(gatewayCalls(), 0, `${label}: and spends NOTHING upstream`);
    eq(verifyCalls(), 0, `${label}: and makes no siteverify call either`);
  }

  // Both halves present is the only configuration that enforces.
  const cfg = envlib.readConfig(ARMED);
  eq(cfg.turnstile, true, "both halves present: enforcement is ON");
  eq(cfg.configured, true, "…and the deployment is configured");
  eq(envlib.publicTurnstile(cfg), SITEKEY, "…and the PUBLIC sitekey is published to the browser");
  ok(cfg.missing.length === 0, "…with nothing missing");

  /* ---- AND `/api/health` ACTUALLY PUTS IT ON THE WIRE --------------------- *
   * `/api/health` is the browser's ONLY source of the sitekey (`mode.js::applyEnvelope`,
   * called only from the health poll; chat envelopes carry the field for shape, not
   * delivery). Deleting `health.js`'s `turnstile:` line once left every suite green while
   * the live demo rendered no widget and refused every turn. Mutation row H1. */
  const armed = health.onRequestGet({ env: ARMED });
  const armedBody = JSON.parse(await armed.clone().text());
  await assertClean(armed, "health armed");
  eq(armedBody.turnstile, SITEKEY,
     "/api/health PUBLISHES the sitekey when the control is enforced — the widget's only source");
  eq(armedBody.mode, "live", "…on a healthy probe");
  ok(!armedBody.turnstile.includes(SECRET_PASS), "…and nothing but the sitekey");
}

/* =========================================================================== *
 * 3. THE THREE MANDATORY CHECKS — each one, on its own
 * =========================================================================== *
 * `success: true` is not the whole answer. Each case differs from the passing one in
 * exactly one field, so two checks out of three cannot satisfy the suite.
 */
{
  /* ---- the passing case, so the three refusals mean something ------------- */
  fresh();
  const good = await turn("hello moxie");
  eq(good.status, 200, "all three checks pass: the turn is served");
  eq(good.body.reason, null, "…with no reason");
  eq(verifyCalls(), 1, "…after exactly ONE siteverify call");
  eq(gatewayCalls(), 1, "…and exactly one gateway call");
  eq(outcomes().verified, 1, "the recorded outcome says `verified`");
  eq(good.body.turnstile, SITEKEY, "the reply carries the public sitekey for the next turn");

  // The outbound request, which is the only place the secret may appear.
  const call = sent.find((s) => s.url === ts.SITEVERIFY_URL);
  ok(!!call, "the verification went to Cloudflare's documented siteverify URL");
  eq(call.opt.method, "POST", "…as a POST");
  eq(call.opt.headers["Content-Type"], "application/x-www-form-urlencoded",
     "…form-encoded, as the contract requires");
  const form = new URLSearchParams(String(call.opt.body));
  eq(form.get("secret"), SECRET_PASS, "…carrying the configured secret");
  eq(form.get("response"), TOKEN, "…and the visitor's token");
  eq(form.get("remoteip"), "203.0.113.9", "…and the visitor's real address, not a rate-limit key");
  deep([...form.keys()].sort(), ["remoteip", "response", "secret"],
       "…and NOTHING else: no text, no context, no model id leaves for Cloudflare");
  eq(call.opt.redirect, "manual",
     "redirects are NOT followed — this request carries a secret in its body");

  /* ---- ON ALL THREE SHAPES, not only the successful one ------------------- *
   * §3.2's envelope is ONE shape for every outcome, so `publicTurnstile(cfg)` is asserted
   * on the success, refusal and blocked envelopes alike. */
  fresh();
  P.plan = { turnstile: { body: { success: false, action: ACT.chat, hostname: HOSTNAME,
                                "error-codes": ["invalid-input-response"] } } };
  eq((await turn("hello")).body.turnstile, SITEKEY,
     "a REFUSAL envelope carries the sitekey too — the shape does not depend on the outcome");
  fresh();
  const blockedTurn = await post({ text: "how do i kill myself", [ts.TOKEN_FIELD]: TOKEN });
  eq(blockedTurn.body.reason, "blocked", "…and a safety-blocked turn is the third shape…");
  eq(blockedTurn.body.turnstile, SITEKEY, "…which carries it as well");

  /* ---- CHECK 1: success ---------------------------------------------------- */
  fresh();
  /* A VALID action AND a valid hostname alongside `success: false`, deliberately: this
   * case must differ from the passing case in EXACTLY ONE field, or deleting check 1
   * leaves checks 2 and 3 to refuse it and the assertion goes on passing over a removed
   * guard. (It did. `sim/tools/turnstile_mutation_check.py` row C1 reported WRONG CHECK
   * against the first draft of this block, which used a body with no `action` at all.) */
  P.plan = { turnstile: { body: { success: false, action: ACT.chat, hostname: HOSTNAME,
                                "error-codes": ["invalid-input-response"] } } };
  const c1 = await turn("hello");
  eq(c1.body.reason, "turnstile_failed", "CHECK 1 — success:false REFUSES (fail closed)");
  eq(c1.status, 403, "…with 403: nothing is wrong with what the visitor typed");
  eq(gatewayCalls(), 0, "…and ZERO gateway calls: the refusal is before the money");
  eq(c1.res.headers.get("Retry-After"), null, "…and no Retry-After: a fresh token is a tap away");
  eq(outcomes().failed, 1, "the recorded outcome says `failed`");

  /* ---- CHECK 2: action ----------------------------------------------------- *
   * The refusal that stops a token minted by ANY OTHER widget flow on an authorized
   * hostname from being spendable on the expensive route. */
  fresh();
  P.plan = { turnstile: { body: { success: true, action: "newsletter", hostname: HOSTNAME } } };
  const c2 = await turn("hello");
  eq(c2.body.reason, "turnstile_failed",
     "CHECK 2 — a token minted for ANOTHER action is refused even though success:true");
  eq(gatewayCalls(), 0, "…with zero gateway calls");

  fresh();
  P.plan = { turnstile: { body: { success: true, hostname: HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_failed",
     "CHECK 2 — an ABSENT action is refused too (the field is required, not optional)");

  /* THE COMPARISON IS EXACT, asserted separately from its existence (rows C2b/C2c). With
   * `startsWith`, a verdict for action `chat-newsletter` was SERVED: a token minted by
   * another widget flow becoming spendable on the expensive route. */
  for (const [label, action] of [
    ["a PREFIX of ours (`chat-newsletter`) — a startsWith would serve it", "chat-newsletter"],
    ["a SUFFIX around ours (`x-chat`)", "x-chat"],
    ["ours in the WRONG CASE (`CHAT`) — a toLowerCase would serve it", "CHAT"],
    ["ours with whitespace (` chat `) — a trim would serve it", " chat "],
    ["the OTHER route's action (`transcribe`) on the chat route", ACT.transcribe],
  ]) {
    fresh();
    P.plan = { turnstile: { body: { success: true, action, hostname: HOSTNAME } } };
    const r = await turn("hello");
    eq(r.body.reason, "turnstile_failed", `CHECK 2 — an action that is ${label} is refused`);
    eq(gatewayCalls(), 0, `…and ${label} reaches the gateway ZERO times`);
  }

  /* ---- CHECK 3: hostname --------------------------------------------------- *
   * Turnstile authorizes a hostname AND ALL ITS SUBDOMAINS, so the widget's own domain
   * list is coarser than this route wants. A foreign hostname is `turnstile_misconfigured`
   * rather than `turnstile_failed`, because the allowance comes from configuration and a
   * mismatch refuses EVERY visitor identically until someone fixes it. */
  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "evil.example.com" } } };
  const c3 = await turn("hello");
  eq(c3.body.reason, "turnstile_misconfigured",
     "CHECK 3 — a challenge solved on a foreign hostname is refused");
  eq(c3.status, 503, "…with 503: it will refuse every visitor until a variable changes");
  eq(c3.res.headers.get("Retry-After"), "60", "…and a 60 s Retry-After, like upstream_down");
  eq(gatewayCalls(), 0, "…with zero gateway calls");
  eq(outcomes().misconfigured, 1, "the recorded outcome says `misconfigured`");

  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "" } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — an EMPTY hostname is refused, not treated as 'unknown, allow'");

  /* NO SUFFIX MATCHING. A `.endsWith()` here would accept `evil-demo.invalid.test` for a
   * suffix of `demo.invalid.test`, and would throw away the whole difference between
   * Cloudflare's subdomain-wide authorization and this deployment's narrow allowance. */
  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.chat,
                                hostname: "evil-" + HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — a hostname that merely ENDS WITH ours is refused: the match is exact");
  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.chat,
                                hostname: "sub." + HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — …and so is a SUBDOMAIN of ours, which Turnstile itself would authorize");

  /* THE DEFAULT ALLOWANCE IS THE REQUEST'S OWN HOSTNAME, which is what makes it impossible
   * to hand production a `localhost` allowance by forgetting a variable, and what lets a
   * fork on any domain work with zero configuration (C3). */
  const bare = envlib.readConfig(ARMED);
  deep(bare.turnstileHosts, [], "with DEMO_TURNSTILE_HOSTS unset the list is empty…");
  ok(ts.hostAllowed(bare, req({}), HOSTNAME), "…and the allowance is the request's own hostname");
  ok(!ts.hostAllowed(bare, req({}), "localhost"),
     "…so `localhost` is NOT allowed on a production host by omission");
  ok(!ts.hostAllowed(bare, req({}), "127.0.0.1"), "…nor is 127.0.0.1");

  /* AN EXPLICIT LIST REPLACES THE DEFAULT rather than extending it. */
  const listed = envlib.readConfig(Object.assign({}, ARMED, {
    DEMO_TURNSTILE_HOSTS: "moxie.example.com, https://other.example.com/sim ,MOXIE.EXAMPLE.COM",
  }));
  deep(listed.turnstileHosts, ["moxie.example.com", "other.example.com"],
       "DEMO_TURNSTILE_HOSTS is parsed, lower-cased, de-duplicated, and a URL is reduced to its host");
  ok(ts.hostAllowed(listed, req({}), "moxie.example.com"), "a listed host is allowed");
  ok(!ts.hostAllowed(listed, req({}), HOSTNAME),
     "…and the request's own host is NOT, once a list is given: it REPLACES the default");
  /* NO SUFFIX MATCHING ON THE EXPLICIT LIST EITHER, and this is asserted separately from
   * the default-allowance case above because they are two different branches of
   * `hostAllowed`. Only the default branch was covered at first, so a `.endsWith()` on the
   * CONFIGURED list went undetected — row C3b of the mutation table. */
  ok(!ts.hostAllowed(listed, req({}), "evil-moxie.example.com"),
     "a listed host ENDS WITH check is refused: the configured match is exact too");
  ok(!ts.hostAllowed(listed, req({}), "sub.moxie.example.com"),
     "…and so is a subdomain of a listed host");
  fresh();
  const listedEnv = Object.assign({}, ARMED, { DEMO_TURNSTILE_HOSTS: "moxie.example.com" });
  P.plan = { turnstile: { body: { success: true, action: ACT.chat,
                                hostname: "evil-moxie.example.com" } } };
  eq((await turn("hello", listedEnv)).body.reason, "turnstile_misconfigured",
     "…and the ROUTE refuses a suffix match against DEMO_TURNSTILE_HOSTS, end to end");

  /* ---- A MISSING TOKEN IS REFUSED WITHOUT ASKING CLOUDFLARE --------------- *
   * There is nothing to verify, so this refusal must be FREE — and, just as important,
   * a flood of tokenless requests must not turn this deployment into a traffic amplifier
   * pointed at siteverify. */
  for (const [label, body] of [
    ["no field at all", { text: "hello" }],
    ["an empty string", { text: "hello", [ts.TOKEN_FIELD]: "" }],
    ["whitespace", { text: "hello", [ts.TOKEN_FIELD]: "   " }],
    ["a non-string", { text: "hello", [ts.TOKEN_FIELD]: 42 }],
  ]) {
    fresh();
    const r = await post(body);
    eq(r.body.reason, "turnstile_failed", `a request with ${label} is refused`);
    eq(verifyCalls(), 0, `…for FREE: ${label} costs no siteverify call`);
    eq(gatewayCalls(), 0, `…and no gateway call`);
    // Recorded per case rather than summed after the loop: `fresh()` resets the counters
    // at the top of each iteration, so a total would only ever have seen the last one.
    eq(outcomes().no_token, 1, `…and ${label} recorded the \`no_token\` outcome`);
  }
}

/* =========================================================================== *
 * 4. THE TWO REASONS ARE THE OPERATOR'S DIAGNOSIS (D8)
 * =========================================================================== *
 * "Our config is wrong" and "your token is bad" have opposite fixes; the reason is how the
 * production secret is validated without reading it (deploy, type a sentence, read it).
 */
{
  for (const [code, want] of [
    ["invalid-input-secret", "turnstile_misconfigured"],
    ["missing-input-secret", "turnstile_misconfigured"],
    ["bad-request", "turnstile_misconfigured"],
    ["invalid-input-response", "turnstile_failed"],
    ["missing-input-response", "turnstile_failed"],
    ["timeout-or-duplicate", "turnstile_failed"],
    ["some-code-cloudflare-has-not-invented-yet", "turnstile_failed"],
  ]) {
    fresh();
    P.plan = { turnstile: { body: { success: false, "error-codes": [code] } } };
    const r = await turn("hello");
    eq(r.body.reason, want, `error-code ${code} maps to ${want}`);
    eq(gatewayCalls(), 0, `…and ${code} spends nothing upstream`);
  }

  // A replayed token: single-use is enforced by Cloudflare, and this is what it looks
  // like arriving here. Driven through the DOCUMENTED "already spent" dummy secret rather
  // than a hand-written body, so the case is the one Cloudflare actually produces.
  fresh();
  const spentEnv = Object.assign({}, ARMED, { DEMO_TURNSTILE_SECRET: SECRET_SPENT });
  const replay = await turn("hello", spentEnv);
  eq(replay.body.reason, "turnstile_failed",
     "the documented 'token already spent' secret produces turnstile_failed — a replay is refused");
  eq(gatewayCalls(), 0, "…and a replay spends nothing");

  // …and the documented "always fails validation" secret.
  fresh();
  const failEnv = Object.assign({}, ARMED, { DEMO_TURNSTILE_SECRET: SECRET_FAIL });
  eq((await turn("hello", failEnv)).body.reason, "turnstile_failed",
     "the documented 'always fails' secret produces turnstile_failed");

  // Both reasons are in the closed set, on BOTH sides of the contract. An unknown reason
  // is coerced to null in `mode.js`, which would read a refused turn as a healthy one.
  for (const r of ["turnstile_failed", "turnstile_misconfigured"]) {
    ok(envelope.REASONS.includes(r), `${r} is in envelope.js's closed reason set`);
    ok(Number.isFinite(envelope.STATUS_FOR[r]), `${r} has a status in §4.5's table`);
    ok(readFileSync(join(repo, "sim", "web", "mode.js"), "utf8").includes('"' + r + '"'),
       `${r} is ALSO in sim/web/mode.js's list — an unknown reason reads as a healthy turn`);
  }
}
