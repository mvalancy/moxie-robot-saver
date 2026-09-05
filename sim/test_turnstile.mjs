/* test_turnstile.mjs — the bot control in front of the spending route, under bare node.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (the guard order and the rule that
 * a refusal is free), §4.2 (what the browser may know), §4.5 (the status table).
 * Implementation: `functions/api/_lib/turnstile.js`, `functions/api/chat.js` step 7,
 * `sim/web/turnstile.js`, `sim/web/mode.js`.
 *
 * Pages Functions are ES modules exporting `onRequestPost({request, env})`, so the real
 * `chat.js` is IMPORTED AND CALLED here with a synthetic `Request` and a plain object as
 * `context.env`, exactly as `sim/test_demo_proxy.mjs` does. `fetch` is stubbed, so nothing
 * leaves the machine: NO CLOUDFLARE ACCOUNT, NO TURNSTILE WIDGET AND NO GATEWAY KEY IS
 * NEEDED BY ANY ASSERTION IN THIS FILE, and none may ever be.
 *
 * ============================================================================
 * THE FIXTURES ARE CLOUDFLARE'S OWN DOCUMENTED DUMMY KEYS, and that is not decoration.
 *
 * Fetched from developers.cloudflare.com/turnstile/troubleshooting/testing/ on 2026-09-05
 * rather than recalled:
 *
 *   sitekeys  1x00000000000000000000AA  always passes, VISIBLE
 *             1x00000000000000000000BB  always passes, INVISIBLE   <- what this page uses
 *             2x00000000000000000000AB  always fails, visible
 *             2x00000000000000000000BB  always fails, invisible
 *             3x00000000000000000000FF  forces an interactive challenge
 *   secrets   1x0000000000000000000000000000000AA  always passes validation
 *             2x0000000000000000000000000000000AA  always fails validation
 *             3x0000000000000000000000000000000AA  returns "token already spent"
 *   token     XXXX.DUMMY.TOKEN.XXXX               what a test sitekey produces
 *
 * The stubbed siteverify below DISPATCHES ON WHICH OF THOSE SECRETS IT WAS SENT, and
 * answers what Cloudflare documents that key answering. So these tests are written against
 * the published contract rather than against my idea of it, and the same fixtures can be
 * pointed at the real endpoint by a future live probe without changing a line.
 *
 * The INVISIBLE always-pass sitekey is the one used as this deployment's stand-in on
 * purpose: `sim/web/turnstile.js` renders `appearance: "interaction-only"`, so the visible
 * variant would be testing a widget shape this site does not ship.
 * ============================================================================
 *
 * THE FIVE PROPERTIES THIS FILE EXISTS TO PROVE, above the individual cases:
 *
 *   1. **THE THREE MANDATORY CHECKS ALL REFUSE.** `success`, `action` and `hostname` —
 *      each one on its own, so a green run cannot be satisfied by two of the three.
 *   2. **THE FAIL-OPEN/FAIL-CLOSED SPLIT IS THE ONE THAT WAS DESIGNED.** A verdict of
 *      "no" refuses; a Cloudflare transport failure does not. Both halves, by name.
 *   3. **THE CONCURRENCY SLOT COMES BACK ON THE NEW REFUSAL PATH.** A leaked slot fails
 *      CLOSED — the ceiling drifts down until visitors who should be served are refused —
 *      and that hazard is why a cache-backed concurrency counter was rejected earlier in
 *      this project (`_lib/limits.js`).
 *   4. **A REFUSAL COSTS NOTHING, IN EITHER DIRECTION.** A Turnstile refusal makes zero
 *      GATEWAY calls, and every cheaper refusal (safety, caps, rate limit, origin,
 *      unconfigured) makes zero SITEVERIFY calls. Both counters are recorded facts, not
 *      inferences from a stub that may or may not have been reached (playbook rule 11).
 *   5. **NOTHING LEAKS.** The widget secret is non-enumerable on the config, is absent
 *      from every response body and header on every path, and no `error-codes` string
 *      Cloudflare returns is ever forwarded.
 *
 *   node sim/test_turnstile.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

const fails = [];
let asserts = 0;
const ok = (c, m) => { asserts++; if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

const chat = await import(join(repo, "functions", "api", "chat.js"));
const health = await import(join(repo, "functions", "api", "health.js"));
const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));
const envlib = await import(join(repo, "functions", "api", "_lib", "env.js"));
const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const ts = await import(join(repo, "functions", "api", "_lib", "turnstile.js"));

/* --------------------------------------------------------------------------- *
 * The fake deployment
 * --------------------------------------------------------------------------- */
const BASE = "https://gw.invalid.test/v1";
const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
const ORIGIN = "https://demo.invalid.test";
const HOSTNAME = "demo.invalid.test";

/** Cloudflare's documented dummy values — see the header. */
const SITEKEY = "1x00000000000000000000BB";          // always passes, invisible
const SECRET_PASS = "1x0000000000000000000000000000000AA";
const SECRET_FAIL = "2x0000000000000000000000000000000AA";
const SECRET_SPENT = "3x0000000000000000000000000000000AA";
const TOKEN = "XXXX.DUMMY.TOKEN.XXXX";

const GATEWAY = {
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
};

/** The gateway configured AND the bot control enforced: the production shape. */
const ARMED = Object.assign({}, GATEWAY, {
  DEMO_TURNSTILE_SECRET: SECRET_PASS,
  DEMO_TURNSTILE_SITEKEY: SITEKEY,
});

/** Every string that must never appear in a response, anywhere, on any path. The widget
 *  SECRET is in this list; the SITEKEY deliberately is not — it is a public value the
 *  browser cannot render a widget without, and §8 asserts it IS published. */
const FORBIDDEN = [KEY, BASE, "gw.invalid.test", "test-brain-model",
                   SECRET_PASS, SECRET_FAIL, SECRET_SPENT];

/** Every `error-codes` string Cloudflare can return. NONE may reach a response body:
 *  they are read into a local boolean and dropped (`_lib/turnstile.js`). */
const ERROR_CODES = ["missing-input-secret", "invalid-input-secret", "missing-input-response",
                     "invalid-input-response", "bad-request", "timeout-or-duplicate",
                     "internal-error"];

/* --------------------------------------------------------------------------- *
 * The stubbed world: one gateway, one siteverify
 * --------------------------------------------------------------------------- */
let sent = [];      // every outbound request, in order
let plan = {};      // { chat: {...}, turnstile: {...} } — what the stubs answer next

/** What Cloudflare documents each dummy secret answering, as a function of the secret it
 *  was actually sent. `plan.turnstile` overrides it for the cases no dummy key produces
 *  (an action mismatch, a foreign hostname, a transport failure). */
function siteverifyAnswer(form, opt) {
  const p = plan.turnstile || {};
  /* NEVER ANSWERS — but honours `opt.signal`, exactly as a real `fetch` does. A stub that
   * ignored the signal would make an unset deadline indistinguishable from a set one: the
   * route would hang either way and the assertion could not tell them apart. */
  if (p.hang) {
    return new Promise((resolve, reject) => {
      const sig = opt && opt.signal;
      if (!sig) return;                       // no deadline wired: hang for ever, on purpose
      const bail = () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      };
      if (sig.aborted) return bail();
      sig.addEventListener("abort", bail, { once: true });
    });
  }
  if (p.throw) {
    const e = new Error("stub");
    e.name = p.throw;
    throw e;
  }
  if (p.status && p.status !== 200) {
    return new Response(p.text || "", {
      status: p.status, headers: { "Content-Type": "application/json" },
    });
  }
  if (p.text !== undefined) {
    return new Response(p.text, { status: 200, headers: { "Content-Type": "application/json" } });
  }
  const body = p.body || (() => {
    const secret = form.get("secret");
    if (secret === SECRET_PASS) {
      return {
        success: true,
        action: form.get("__action") || ts.TURNSTILE_ACTION,
        hostname: HOSTNAME,
        challenge_ts: "2026-09-05T00:00:00.000Z",
      };
    }
    if (secret === SECRET_SPENT) return { success: false, "error-codes": ["timeout-or-duplicate"] };
    // 2x…AA, and anything else a test invents: Cloudflare's "always fails validation".
    return { success: false, "error-codes": ["invalid-input-response"] };
  })();
  return new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

globalThis.fetch = async (url, opt) => {
  const u = String(url);
  sent.push({ url: u, opt });
  if (u === ts.SITEVERIFY_URL) {
    return siteverifyAnswer(new URLSearchParams(String((opt && opt.body) || "")), opt);
  }
  const p = plan.chat || {};
  if (p.throw) {
    const e = new Error("stub");
    e.name = p.throw;
    throw e;
  }
  const content = p.content === undefined ? "Hi there! Want to hear a joke?" : p.content;
  return new Response(JSON.stringify({ choices: [{ message: { content } }] }), {
    status: p.status || 200, headers: { "Content-Type": "application/json" },
  });
};

/* --------------------------------------------------------------------------- *
 * Harness
 * --------------------------------------------------------------------------- */
function req(body, headers, path) {
  return new Request(ORIGIN + (path || "/api/chat"), {
    method: "POST",
    headers: Object.assign({
      "Content-Type": "application/json",
      Origin: ORIGIN,
      "Sec-Fetch-Site": "same-origin",
      "CF-Connecting-IP": "203.0.113.9",
    }, headers || {}),
    body: JSON.stringify(body),
  });
}

function fresh() {
  limits.__reset();
  ts.__reset();
  sent = [];
  plan = {};
}

let sweeps = 0;

/** The §4.2 sweep, extended to the widget secret and to every Cloudflare error code.
 *  Runs on EVERY response this file produces. */
async function assertClean(res, label) {
  sweeps += 1;
  const text = await res.clone().text();
  let headerText = "";
  for (const [k, v] of res.headers.entries()) headerText += k + ": " + v + "\n";
  for (const secret of FORBIDDEN) {
    ok(!text.includes(secret), `${label}: the BODY leaked ${JSON.stringify(secret.slice(0, 14))}…`);
    ok(!headerText.includes(secret), `${label}: a HEADER leaked ${JSON.stringify(secret.slice(0, 14))}…`);
  }
  for (const code of ERROR_CODES) {
    ok(!text.includes(code), `${label}: the body forwarded Cloudflare's raw error code ${code}`);
  }
  ok(!/https?:\/\//.test(text.replace(/"topic":"[^"]*"/g, "")), `${label}: the body contains a URL`);
}

/** POST to `/api/chat`, sweep the reply, and hand back everything a test asserts on. */
async function post(body, env, headers) {
  const res = await chat.onRequestPost({ request: req(body, headers), env: env || ARMED });
  await assertClean(res, "chat " + JSON.stringify(body).slice(0, 48));
  let parsed = null;
  try { parsed = JSON.parse(await res.clone().text()); } catch {}
  return { res, body: parsed, status: res.status };
}

/** A turn with the dummy token attached — the ordinary case. */
const turn = (text, env) => post({ text: text || "hello moxie", [ts.TOKEN_FIELD]: TOKEN }, env);

const gatewayCalls = () => limits.__state().stats.upstreamCalls;
const verifyCalls = () => ts.__stats().calls;
const outcomes = () => ts.__stats().outcomes;

/* =========================================================================== *
 * 1. NOT CONFIGURED IS NOT ENFORCED (D4, and C5's fail-safe default)
 * =========================================================================== *
 * The property that keeps every branch preview and every self-hosted fork working. A
 * preview MUST be inert: Turnstile authorizes a hostname and all of its subdomains, and
 * `*.pages.dev` is not on this widget's list, so a challenge there could never pass. With
 * the Turnstile variables unset the check must be a synchronous no-op — not a lenient
 * check, not a check that phones home and forgives, but no call at all.
 */
{
  fresh();
  const cfg = envlib.readConfig(GATEWAY);
  eq(cfg.turnstile, false, "with no Turnstile variables, enforcement is OFF");
  eq(cfg.configured, true, "…and the gateway is still configured — the two are independent");
  eq(envlib.publicTurnstile(cfg), "", "…and no sitekey is published to the browser");
  /* THE CASE THAT ACTUALLY TESTS `publicTurnstile`'s CONDITION: a deployment that HAS a
   * sitekey but no secret. With the gateway-only config above, `turnstileSitekey` is empty
   * anyway, so publishing unconditionally would still have produced "" — which is why
   * mutation row D5 was NOT CAUGHT until this assertion existed. The browser must not be
   * handed a sitekey the server is not going to check: it would render a widget, mint
   * tokens, and have every one of them ignored. */
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
 * The same rule `ACCESS_VARS` already establishes for a Cloudflare Access service token,
 * and it is here because the two halves fail in opposite, equally silent directions: a
 * secret with no sitekey refuses every visitor (no browser can mint a token), and a
 * sitekey with no secret renders a widget nothing verifies — a bot control in appearance
 * only. Both read as unconfigured, which spends nothing.
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
}

/* =========================================================================== *
 * 3. THE THREE MANDATORY CHECKS — each one, on its own
 * =========================================================================== *
 * Cloudflare's guide is explicit that `success: true` is not the whole answer. These are
 * asserted SEPARATELY so that a green run cannot be satisfied by two checks out of three:
 * each case below differs from the passing case in exactly one field.
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

  /* ---- CHECK 1: success ---------------------------------------------------- */
  fresh();
  /* A VALID action AND a valid hostname alongside `success: false`, deliberately: this
   * case must differ from the passing case in EXACTLY ONE field, or deleting check 1
   * leaves checks 2 and 3 to refuse it and the assertion goes on passing over a removed
   * guard. (It did. `sim/tools/turnstile_mutation_check.py` row C1 reported WRONG CHECK
   * against the first draft of this block, which used a body with no `action` at all.) */
  plan = { turnstile: { body: { success: false, action: ts.TURNSTILE_ACTION, hostname: HOSTNAME,
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
  plan = { turnstile: { body: { success: true, action: "newsletter", hostname: HOSTNAME } } };
  const c2 = await turn("hello");
  eq(c2.body.reason, "turnstile_failed",
     "CHECK 2 — a token minted for ANOTHER action is refused even though success:true");
  eq(gatewayCalls(), 0, "…with zero gateway calls");

  fresh();
  plan = { turnstile: { body: { success: true, hostname: HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_failed",
     "CHECK 2 — an ABSENT action is refused too (the field is required, not optional)");

  /* ---- CHECK 3: hostname --------------------------------------------------- *
   * Turnstile authorizes a hostname AND ALL ITS SUBDOMAINS, so the widget's own domain
   * list is coarser than this route wants. A foreign hostname is `turnstile_misconfigured`
   * rather than `turnstile_failed`, because the allowance comes from configuration and a
   * mismatch refuses EVERY visitor identically until someone fixes it. */
  fresh();
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION, hostname: "evil.example.com" } } };
  const c3 = await turn("hello");
  eq(c3.body.reason, "turnstile_misconfigured",
     "CHECK 3 — a challenge solved on a foreign hostname is refused");
  eq(c3.status, 503, "…with 503: it will refuse every visitor until a variable changes");
  eq(c3.res.headers.get("Retry-After"), "60", "…and a 60 s Retry-After, like upstream_down");
  eq(gatewayCalls(), 0, "…with zero gateway calls");
  eq(outcomes().misconfigured, 1, "the recorded outcome says `misconfigured`");

  fresh();
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION, hostname: "" } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — an EMPTY hostname is refused, not treated as 'unknown, allow'");

  /* NO SUFFIX MATCHING. A `.endsWith()` here would accept `evil-demo.invalid.test` for a
   * suffix of `demo.invalid.test`, and would throw away the whole difference between
   * Cloudflare's subdomain-wide authorization and this deployment's narrow allowance. */
  fresh();
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION,
                                hostname: "evil-" + HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — a hostname that merely ENDS WITH ours is refused: the match is exact");
  fresh();
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION,
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
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION,
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
 * "Our config is wrong" and "your token is bad" have opposite fixes, and the second
 * reason is the ONLY way the production secret gets validated without anyone reading it:
 * deploy, type one sentence, read the reason. This block pins the mapping code by code.
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
    plan = { turnstile: { body: { success: false, "error-codes": [code] } } };
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
    plan = { turnstile: p };
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
  plan = { turnstile: { hang: true } };
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
  plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-response"] } } };
  for (let i = 0; i < CEIL * 3; i++) {
    const r = await turn("hello " + i, tight);
    eq(r.body.reason, "turnstile_failed", `refusal ${i + 1} is a Turnstile refusal`);
    eq(limits.__state().inflight.chat || 0, 0,
       `…and the in-flight count is back to ZERO after refusal ${i + 1} (a leak fails CLOSED)`);
  }

  // The behavioural half: after six refusals through a ceiling of two, a good token is
  // still served. With a leaked slot this is `at_capacity` and the demo is dead.
  plan = {};
  const after = await turn("hello again", tight);
  eq(after.status, 200, "after 6 Turnstile refusals through a ceiling of 2, a good turn is SERVED");
  eq(after.body.reason, null, "…with no reason — the slots were all given back");
  eq(after.body.load.inflight, 1, "…and the load count saw exactly this one turn in flight");

  // The same for the OTHER refusal reason, since it is a different return statement.
  fresh();
  plan = { turnstile: { body: { success: true, action: ts.TURNSTILE_ACTION, hostname: "evil.example.com" } } };
  for (let i = 0; i < CEIL * 3; i++) {
    eq((await turn("hi " + i, tight)).body.reason, "turnstile_misconfigured", `misconfig refusal ${i + 1}`);
    eq(limits.__state().inflight.chat || 0, 0, `…and no slot leaked on refusal ${i + 1}`);
  }
  plan = {};
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
  ok(sweeps > 60, `the sweep ran on every response produced above (${sweeps} sweeps)`);

  // And the one thing a "helpful" error field would leak: the codes themselves.
  fresh();
  plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-secret", "bad-request"] } } };
  const r = await turn("hello");
  const text = JSON.stringify(r.body);
  eq(r.body.reason, "turnstile_misconfigured", "a misconfiguration is diagnosable…");
  eq(r.body.message, "", "…and carries NO free-text message at all");
  for (const code of ERROR_CODES) ok(!text.includes(code), `…and never forwards ${code}`);
  ok(!text.includes("hostname"), "…nor the hostname Cloudflare reported");
  ok(!text.includes("challenge_ts"), "…nor the challenge timestamp");
}

/* =========================================================================== *
 * 9. THE BROWSER HALF — `sim/web/turnstile.js`, under a stub window
 * =========================================================================== *
 * Loaded as SOURCE under a fake window/document with a fake Cloudflare API, the idiom
 * `sim/test_bridge.mjs` established. No browser, no network, no widget.
 *
 * The behaviour that matters and that nothing else can check: A FRESH TOKEN PER SEND.
 * Tokens are single-use and live 300 s, so a module that minted one per page load would
 * work for exactly the first turn of a conversation and then refuse every later one — a
 * demo that breaks after one sentence, in a way that looks like the brain failing.
 */
{
  const SRC = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");

  /** A fake page. `sitekey` is what `mode.js` would have published. */
  function world(sitekey) {
    const holder = { id: "", children: [], setAttribute() {}, appendChild(c) { holder.children.push(c); } };
    const scripts = [];
    globalThis.document = {
      getElementById: () => null,
      createElement: (tag) => {
        const el = { tag, setAttribute() {}, appendChild() {}, children: [] };
        if (tag === "script") scripts.push(el);
        return el;
      },
      head: { appendChild(el) { holder.children.push(el); } },
      body: { appendChild(el) { holder.children.push(el); } },
      documentElement: { appendChild() {} },
      addEventListener() {},
    };
    globalThis.window = {
      // Faithful to `mode.js`: `onChange` invokes the listener IMMEDIATELY with the
      // current snapshot and returns an unsubscribe. That immediate call is how the real
      // page renders the widget before the first Send rather than during it, so a fake
      // that never called back would be testing a different module.
      moxieMode: { turnstile: () => sitekey, onChange: (fn) => { fn({}); return () => {}; } },
    };
    return { scripts, holder };
  }

  /** Cloudflare's widget API, faked to the documented surface.
   *
   *  `held` is what `getResponse()` answers — the token a widget is sitting on. It is a
   *  mutable field rather than a constant because the interactive case is precisely a
   *  widget whose token appears LATER, and a fake that could only ever answer "" would
   *  make that path untestable. */
  function fakeApi(behaviour) {
    const calls = { render: 0, reset: 0, execute: 0, opts: null, held: "" };
    let cb = null, errCb = null;
    globalThis.window.turnstile = {
      render(el, opts) {
        calls.render++;
        calls.opts = opts;
        cb = opts.callback;
        errCb = opts["error-callback"];
        return "widget-1";
      },
      reset() { calls.reset++; calls.held = ""; },
      execute() {
        calls.execute++;
        if (behaviour === "error") { errCb(); return; }
        if (behaviour === "silent") return;                  // never calls back at all
        const tok = "token-" + calls.execute;
        calls.held = tok;
        cb(tok);
      },
      getResponse: () => calls.held,
    };
    return calls;
  }

  /* ---- not enforced: inert, and no third-party script is even requested --- */
  {
    const w = world("");
    (0, eval)(SRC);
    const tok = await globalThis.window.moxieTurnstile.getToken();
    eq(tok, "", "no sitekey published => getToken() resolves \"\" (send as-is)");
    eq(w.scripts.length, 0, "…and Cloudflare's script is NEVER requested on an unenforced page");
    eq(globalThis.window.moxieTurnstile.enforced(), false, "…and the module says it is not enforced");
    eq(globalThis.window.moxieTurnstile.stats().skipped, 1, "…recorded as skipped");
  }

  /* ---- enforced and working: ONE widget, a FRESH token per send ----------- */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    const t1 = await globalThis.window.moxieTurnstile.getToken();
    const t2 = await globalThis.window.moxieTurnstile.getToken();
    const t3 = await globalThis.window.moxieTurnstile.getToken();
    eq(calls.render, 1, "the widget is rendered ONCE, however many sends there are");
    eq(calls.execute, 3, "…and executed once per send");
    eq(calls.reset, 3, "…with reset() before EVERY execute: tokens are single-use");
    ok(t1 && t2 && t3, "every send got a token");
    ok(t1 !== t2 && t2 !== t3, "…and they are DIFFERENT tokens — not one token reused");

    // The render options, which are the UX decision and the server contract in one object.
    eq(calls.opts.sitekey, SITEKEY, "the widget is rendered with the PUBLISHED sitekey");
    eq(calls.opts.action, ts.TURNSTILE_ACTION,
       "…and the action the server requires back (`_lib/turnstile.js::TURNSTILE_ACTION`)");
    eq(calls.opts.appearance, "interaction-only",
       "…invisible unless Cloudflare wants an interaction: NO checkbox in front of a child");
    eq(calls.opts.execution, "execute",
       "…and the challenge runs when we ASK, which is what makes a per-send token possible");
  }

  /* ---- enforced and broken: `null`, never a hang and never a lie ---------- */
  for (const [label, behaviour] of [["the widget errors", "error"], ["the widget never answers", "silent"]]) {
    world(SITEKEY);
    fakeApi(behaviour);
    (0, eval)(SRC);
    const p = globalThis.window.moxieTurnstile.getToken();
    // The 8 s deadline is a real timer here; `silent` is the case it exists for, so the
    // promise is raced against a short one to keep the suite fast while still proving it
    // never resolves to a TOKEN. `error` settles immediately.
    const got = await Promise.race([p, new Promise((r) => setTimeout(() => r("__pending"), 50))]);
    if (behaviour === "error") {
      eq(got, null, `${label}: getToken() resolves null — the caller must not send`);
    } else {
      eq(got, "__pending", `${label}: getToken() has NOT resolved a token (the deadline owns it)`);
    }
  }

  /* ---- THE INTERACTIVE CASE: a solve that lands past the deadline ---------- *
   * The only path by which a visitor who actually had to click something ever gets a
   * turn. Their solve arrives after `getToken()` already resolved `null` and the page
   * already said "try me once more", so the widget is left holding a perfectly good
   * unspent token — and the NEXT send must spend that rather than reset it and start the
   * challenge over. An earlier draft called `reset()` unconditionally, which would have
   * made the page unusable for exactly the visitors Turnstile decided to challenge. */
  {
    world(SITEKEY);
    const calls = fakeApi("silent");                 // execute() never calls back
    (0, eval)(SRC);
    const first = await Promise.race([
      globalThis.window.moxieTurnstile.getToken(),
      new Promise((r) => setTimeout(() => r("__pending"), 50)),
    ]);
    eq(first, "__pending", "the first send is still waiting on an interactive challenge…");
    // The human finishes clicking, late. Cloudflare hands the widget a token.
    calls.held = "solved-late";
    const second = await globalThis.window.moxieTurnstile.getToken();
    eq(second, "solved-late",
       "…and the NEXT send spends the token the widget was left holding");
    eq(globalThis.window.moxieTurnstile.stats().reused, 1, "…recorded as reused, not minted");

    /* AND IT IS NEVER HANDED OUT TWICE. `getResponse()` still answers `solved-late`, so a
     * module that trusted it blindly would replay the token and the server would refuse
     * the turn as `timeout-or-duplicate` — a bug that appears only on the turn AFTER an
     * interactive challenge, which is about as hard to notice as a bug gets. */
    const resets = calls.reset;
    const third = await Promise.race([
      globalThis.window.moxieTurnstile.getToken(),
      new Promise((r) => setTimeout(() => r("__pending"), 50)),
    ]);
    ok(third !== "solved-late", `a spent token is NEVER handed out again (got ${JSON.stringify(third)})`);
    eq(calls.reset, resets + 1, "…the widget is reset and a fresh challenge asked for instead");
  }

  /* ---- Cloudflare's script cannot load (a CSP refusal, an extension) ------ */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);                       // no window.turnstile: nothing to render with
    eq(w.scripts.length, 1, "the script IS requested when a sitekey is published");
    ok(/^https:\/\/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js\?render=explicit$/.test(w.scripts[0].src),
       `…from Cloudflare's documented explicit-render URL (${w.scripts[0].src})`);
    w.scripts[0].onerror();               // what a CSP refusal fires
    const got = await globalThis.window.moxieTurnstile.getToken();
    eq(got, null, "a script that cannot load resolves null — never a silent dead send");
    eq(globalThis.window.moxieTurnstile.stats().scriptErrors, 1, "…and it is recorded");
  }
  delete globalThis.window;
  delete globalThis.document;
}

/* =========================================================================== *
 * 10. THE CONTRACTS THAT SPAN TWO FILES
 * =========================================================================== *
 * Each of these is a value that must be identical in two places and that nothing else
 * would notice drifting: the drift's symptom is every visitor being refused, on
 * production, with a reason that looks like somebody else's fault.
 */
{
  const clientSrc = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");
  const headers = readFileSync(join(repo, "sim", "web", "_headers"), "utf8");
  const simHtml = readFileSync(join(repo, "sim", "web", "sim.html"), "utf8");

  // THE ACTION. Read out of the client source, compared with the server constant. A drift
  // refuses every visitor with `turnstile_failed` and looks like a Cloudflare fault.
  const clientAction = /var ACTION = "([^"]+)"/.exec(clientSrc);
  ok(!!clientAction, "sim/web/turnstile.js declares its action as a single constant");
  eq(clientAction[1], ts.TURNSTILE_ACTION,
     "the client's ACTION equals the server's TURNSTILE_ACTION — check 2 compares them");

  // THE FIELD NAME, likewise: the token must arrive where the route reads it.
  ok(clientSrc.includes(ts.TOKEN_FIELD) === false,
     "…and the client does not name the field: `cloud-transport.js` owns the wire shape");
  ok(readFileSync(join(repo, "sim", "web", "cloud-transport.js"), "utf8").includes('"' + ts.TOKEN_FIELD + '"'),
     `cloud-transport.js sends the token under the field the route reads (${ts.TOKEN_FIELD})`);

  // THE SITEKEY IS NOT IN THE REPO. Not a secrecy claim — a sitekey is public — but a C3
  // one: this deployment's sitekey baked into shipped HTML or JS would hand every fork and
  // every branch preview a widget bound to a domain list they are not on.
  for (const f of ["sim.html", "index.html", "setup.html", "cloud.html", "docs.html",
                   "turnstile.js", "cloud-transport.js", "mode.js", "env.js"]) {
    const src = readFileSync(join(repo, "sim", "web", f), "utf8");
    ok(!/\b[0-9]x[0-9A-Za-z]{20,}\b/.test(src),
       `${f} carries NO Turnstile sitekey — the browser learns it from /api/health (C3)`);
    ok(!/data-sitekey/.test(src),
       `${f} has no hard-coded data-sitekey attribute either`);
  }

  // TRAP B: the app-script no-cache list is the whole mechanism. A client script missing
  // from it is served with Pages' default caching, so a redeploy can leave a visitor
  // running yesterday's token minter against today's route.
  ok(/^\/turnstile\.js\n\s+Cache-Control:\s*no-cache$/m.test(headers),
     "sim/web/_headers gives turnstile.js its own no-cache entry");

  // The CSP needs the widget host in THREE directives, and each absence fails silently.
  const csp = (/^\s+Content-Security-Policy:[ \t]*(.+)$/m.exec(headers) || [])[1] || "";
  for (const d of ["script-src", "frame-src", "connect-src"]) {
    const directive = csp.split(";").map((x) => x.trim()).find((x) => x.startsWith(d)) || "";
    ok(directive.includes("https://challenges.cloudflare.com"),
       `the shipped CSP allows the widget host in ${d} (without it the widget fails SILENTLY)`);
  }
  ok(!/frame-src\s+'none'/.test(csp),
     "frame-src is no longer 'none' — Turnstile draws its challenge in an iframe");

  // Load order: the module must exist before the send path can call it.
  ok(simHtml.includes('src="turnstile.js'), "sim.html loads turnstile.js");
  ok(simHtml.indexOf("mode.js") < simHtml.indexOf("turnstile.js"),
     "…after mode.js, which is where the sitekey comes from");
  ok(simHtml.indexOf("turnstile.js") < simHtml.indexOf("cloud-transport.js"),
     "…and before cloud-transport.js, which calls it on the send path");
}

/* --------------------------------------------------------------------------- */
if (fails.length) {
  console.error(`\n❌ Turnstile bot control: ${fails.length} FAILED of ${asserts} checks\n`);
  for (const f of fails) console.error("  FAIL: " + f);
  process.exit(1);
}
console.log(`✅ Turnstile bot control: ${asserts} checks passed (${sweeps} leak sweeps)`);
/* AN EXPLICIT EXIT, because §9 deliberately leaves timers pending.
 *
 * Two of its cases — the widget that never calls back, and the interactive solve that
 * lands late — start `sim/web/turnstile.js`'s own 8 s deadline and then assert on the
 * promise NOT having resolved. Those timers are the thing under test, so they cannot be
 * cleared from here, and node will not exit while they are armed: the assertions all
 * finish in ~250 ms and the process then sat for 16 s doing nothing.
 *
 * That is invisible when the suite is run once and expensive when it is run 28 times —
 * `sim/tools/turnstile_mutation_check.py` runs it once per mutation, and the wait took
 * that table from about a minute to over six. Exiting on the last line is honest here
 * because every assertion above is synchronous or already awaited: there is no work left
 * that could still fail, only a clock nobody is waiting on. */
process.exit(0);
