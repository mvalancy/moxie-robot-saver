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
 * THE EIGHT PROPERTIES THIS FILE EXISTS TO PROVE, above the individual cases:
 *
 *   1. **THE THREE MANDATORY CHECKS ALL REFUSE.** `success`, `action` and `hostname` —
 *      each one on its own, so a green run cannot be satisfied by two of the three. And
 *      each is proven against LOOSENING as well as deletion: a `startsWith` or a case-fold
 *      on the action, a `.endsWith` or an empty-means-allow on the hostname.
 *   2. **THE FAIL-OPEN/FAIL-CLOSED SPLIT IS THE ONE THAT WAS DESIGNED.** A verdict of
 *      "no" refuses; a Cloudflare transport failure does not. Both halves, by name — and
 *      the carve-out between them: a WRONG SECRET arrives as an HTTP 400, which must
 *      refuse anyway, or the whole control switches itself off for a one-character typo.
 *   3. **THE CONCURRENCY SLOT COMES BACK ON THE NEW REFUSAL PATH.** A leaked slot fails
 *      CLOSED — the ceiling drifts down until visitors who should be served are refused —
 *      and that hazard is why a cache-backed concurrency counter was rejected earlier in
 *      this project (`_lib/limits.js`).
 *   4. **A REFUSAL COSTS NOTHING, IN EVERY DIRECTION.** A Turnstile refusal makes zero
 *      GATEWAY calls; every cheaper refusal (safety, caps, rate limit, origin,
 *      unconfigured) makes zero SITEVERIFY calls; and every refusal inside the admitted
 *      section leaves the SHARED UNIT BUDGET where it found it. All three are recorded
 *      facts, not inferences from a stub that may or may not have been reached (playbook
 *      rule 11). The third is the difference between a paid drain and a free one: 200
 *      tokenless requests used to empty the hourly budget and take the demo scripted.
 *   5. **BOTH SPENDING ROUTES ARE GUARDED, AND THEIR TOKENS DO NOT CROSS OVER.**
 *      `/api/chat` and `/api/transcribe` require different `action`s back, in both
 *      directions, because a microphone turn costs up to 15 s of billable
 *      speech-to-text and a typed turn's challenge must not buy one.
 *   6. **THE BROWSER CAN ACTUALLY GET A TOKEN.** `/api/health` publishes the sitekey (its
 *      ONLY delivery path), a failed script load is never memoised, and a challenge on
 *      screen is never reset out from under the visitor.
 *   7. **NOTHING LEAKS.** The widget secret is non-enumerable on the config, is absent
 *      from every response body and header on every path, and no `error-codes` string
 *      Cloudflare returns is ever forwarded — including from the 400 body that is now
 *      parsed.
 *   8. **AND EVERY ONE OF THOSE IS PROVEN IN BOTH DIRECTIONS.**
 *      `sim/tools/turnstile_mutation_check.py` deletes or loosens each guard in turn and
 *      requires THE CHECK THAT NAMES IT to redden.
 *
 *   node sim/test_turnstile.mjs
 */
import { readdirSync, readFileSync } from "node:fs";
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
const transcribe = await import(join(repo, "functions", "api", "transcribe.js"));
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
/** The two widget actions, by route name. `ACT.chat` is what a typed turn mints and
 *  `ACT.transcribe` is what the microphone mints; the server refuses each in the other's
 *  place, which is the cross-route replay check 2 exists for. */
const ACT = ts.TURNSTILE_ACTIONS;

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
        // WHICHEVER ACTION THIS TEST IS ABOUT. There are two now — one per spending route
        // — and siteverify's request body carries only the secret, the token and the IP,
        // so the stub cannot infer which route asked. `plan.turnstile.action` is how a
        // test says, and it defaults to the chat turn because that is what most of this
        // file exercises. A DEFAULT IS SAFE HERE and nowhere else: the production code
        // has none, on purpose (`_lib/turnstile.js::actionFor`).
        action: p.action || ACT.chat,
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
  // The ears' upstream is a different endpoint with a different reply shape, and it has to
  // be answered here or every transcribe assertion would be measuring `upstream_down`.
  if (u.includes("/audio/transcriptions")) {
    return new Response(JSON.stringify({ text: "i am a bot" }), {
      status: p.status || 200, headers: { "Content-Type": "application/json" },
    });
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

/* --------------------------------------------------------------------------- *
 * The ears' fixtures — a real RIFF/WAVE, because the route sniffs the bytes
 * --------------------------------------------------------------------------- *
 * `/api/transcribe` does not believe the `Content-Type`: it reads the magic number and
 * then reads the WAV header's own declared duration (`_lib/wav.js`). So a fixture of
 * `"x".repeat(2000)` would be refused `bad_request` before the bot control was ever
 * reached, and every assertion about the bot control would be measuring the sniffer. This
 * builds the same 16 kHz mono PCM `sim/web/mic.js::encodeWav` produces.
 */
function wavBytes(ms) {
  const rate = 16000;
  const samples = Math.round((rate * ms) / 1000);
  const bytes = new Uint8Array(44 + samples * 2);
  const view = new DataView(bytes.buffer);
  const wr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
  wr(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); wr(8, "WAVE");
  wr(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  wr(36, "data"); view.setUint32(40, samples * 2, true);
  return bytes;
}
/** Well over `DEMO_MIN_AUDIO_BYTES` (2 000) and well under `DEMO_MAX_RECORD_MS` (15 000). */
const CLIP = wavBytes(1000);

/** The ears' env: the same ARMED gateway plus an STT model, which is what `cfg.ears` is.
 *
 *  THE ADMISSION QUEUE IS SWITCHED OFF (`DEMO_QUEUE_MAX_WAIT_MS: 0`, the documented escape
 *  hatch back to the pre-queue behaviour) and that is a diagnosis decision rather than a
 *  behaviour one. A mutation that LEAKS a concurrency slot — rows D2 and D2b — fills the
 *  ceiling, and with the queue on every later request in this file then waits
 *  `DEMO_QUEUE_MAX_WAIT_MS` before being refused. Across the flood in §12 that is minutes,
 *  so the suite HUNG instead of reddening and the mutation table reported "caught (hung)"
 *  where a named red check was available. Refusing instantly turns those rows back into
 *  legible failures. §11 still proves the slot comes back, with the ceiling set explicitly. */
const EARS = Object.assign({}, ARMED, {
  DEMO_STT_MODEL: "test-ears-model",
  DEMO_QUEUE_MAX_WAIT_MS: "0",
});

/** POST to `/api/transcribe`, sweep the reply, and hand back what a test asserts on. */
async function postAudio(headers, env) {
  const request = new Request(ORIGIN + "/api/transcribe", {
    method: "POST",
    headers: Object.assign({
      "Content-Type": "audio/wav",
      Origin: ORIGIN,
      "Sec-Fetch-Site": "same-origin",
      "CF-Connecting-IP": "203.0.113.9",
    }, headers || {}),
    body: CLIP,
  });
  const res = await transcribe.onRequestPost({ request, env: env || EARS });
  await assertClean(res, "transcribe " + JSON.stringify(headers || {}).slice(0, 40));
  let parsed = null;
  try { parsed = JSON.parse(await res.clone().text()); } catch {}
  return { res, body: parsed, status: res.status };
}

/** A microphone turn with the dummy token on the header the route reads. */
const clip = (env) => postAudio({ [ts.TOKEN_HEADER]: TOKEN }, env);

const gatewayCalls = () => limits.__state().stats.upstreamCalls;
const refundedUnits = () => limits.__state().stats.refundedUnits;
/** Every unit the budget currently thinks is spent, at whichever scale. One number,
 *  because both windows are charged the same amount by the same call. */
const unitsSpent = () => Math.max(0, ...Object.values(limits.__state().budget), 0);
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

  /* ---- AND `/api/health` ACTUALLY PUTS IT ON THE WIRE --------------------- *
   * THE ONE GUARD IN THIS SLICE THAT HAD NO TEST AT ALL, and it is the load-bearing one:
   * `/api/health` is the browser's ONLY source of the sitekey. `turnstile.js::sitekey()`
   * reads `window.moxieMode.turnstile()`; `mode.js` assigns that variable in exactly one
   * place (`applyEnvelope`), which has exactly one caller (`poll()`, the `/api/health`
   * fetch). `note()` — the only thing that ever sees a `/api/chat` reply — is handed just
   * `{reason, retry_after_s}`, so the copies of this field on the chat envelopes are the
   * envelope's shape and NOT a second delivery path.
   *
   * Both of this file's existing probe calls passed an UNARMED env, so DELETING
   * `health.js`'s `turnstile:` line left this suite green at 1886 checks — along with
   * test_mode, test_cloud_transport, test_demo_proxy, test_env_hosted, test_typed_turn,
   * test_mic_spend, test_api_headers, test_demo_tickets, test_demo_ears,
   * test_fallback_coverage and test_bridge — while the live demo rendered no widget, sent
   * no token, and answered `turnstile_failed` to every visitor on every turn under a LIVE
   * badge. Mutation row H1 is this assertion's teeth. */
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

  /* ---- ON ALL THREE SHAPES, not only the successful one ------------------- *
   * `publicTurnstile(cfg)` appears on the success envelope, the refusal envelope and the
   * blocked envelope, and only the first was asserted — so removing it from either of the
   * others left the suite green. The field is not a second delivery path (`mode.js` learns
   * the sitekey from the `/api/health` poll and from nowhere else, which §2 pins); it is
   * there because §3.2's envelope is ONE shape for every route and every outcome, and a
   * field that appears only on success cannot later be relied on. An unasserted claim
   * about the wire is not a claim about the wire. */
  fresh();
  plan = { turnstile: { body: { success: false, action: ACT.chat, hostname: HOSTNAME,
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
  plan = { turnstile: { body: { success: false, action: ACT.chat, hostname: HOSTNAME,
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

  /* THE COMPARISON IS EXACT, AND THAT IS ASSERTED SEPARATELY FROM ITS EXISTENCE.
   *
   * Check 3 always had two loosening cases (`.endsWith`, an empty hostname) and check 2
   * had none — so both of the plausible relaxations of THIS line passed the whole suite
   * green. Measured with the `startsWith` form applied: a verdict of
   * `{success:true, action:"chat-newsletter", hostname:<ours>}` was SERVED, with a real
   * gateway call. That is exactly the replay check 2 exists to close — a token minted by
   * another widget flow on an authorized hostname becoming spendable on the expensive
   * route — and it would have shipped green. Rows C2b/C2c are these two assertions' teeth. */
  for (const [label, action] of [
    ["a PREFIX of ours (`chat-newsletter`) — a startsWith would serve it", "chat-newsletter"],
    ["a SUFFIX around ours (`x-chat`)", "x-chat"],
    ["ours in the WRONG CASE (`CHAT`) — a toLowerCase would serve it", "CHAT"],
    ["ours with whitespace (` chat `) — a trim would serve it", " chat "],
    ["the OTHER route's action (`transcribe`) on the chat route", ACT.transcribe],
  ]) {
    fresh();
    plan = { turnstile: { body: { success: true, action, hostname: HOSTNAME } } };
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
  plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "evil.example.com" } } };
  const c3 = await turn("hello");
  eq(c3.body.reason, "turnstile_misconfigured",
     "CHECK 3 — a challenge solved on a foreign hostname is refused");
  eq(c3.status, 503, "…with 503: it will refuse every visitor until a variable changes");
  eq(c3.res.headers.get("Retry-After"), "60", "…and a 60 s Retry-After, like upstream_down");
  eq(gatewayCalls(), 0, "…with zero gateway calls");
  eq(outcomes().misconfigured, 1, "the recorded outcome says `misconfigured`");

  fresh();
  plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "" } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — an EMPTY hostname is refused, not treated as 'unknown, allow'");

  /* NO SUFFIX MATCHING. A `.endsWith()` here would accept `evil-demo.invalid.test` for a
   * suffix of `demo.invalid.test`, and would throw away the whole difference between
   * Cloudflare's subdomain-wide authorization and this deployment's narrow allowance. */
  fresh();
  plan = { turnstile: { body: { success: true, action: ACT.chat,
                                hostname: "evil-" + HOSTNAME } } };
  eq((await turn("hello")).body.reason, "turnstile_misconfigured",
     "CHECK 3 — a hostname that merely ENDS WITH ours is refused: the match is exact");
  fresh();
  plan = { turnstile: { body: { success: true, action: ACT.chat,
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
  plan = { turnstile: { body: { success: true, action: ACT.chat,
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
    plan = { turnstile: { status: 400,
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
  plan = { turnstile: { body: { success: false, "error-codes": ["invalid-input-secret"] } } };
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
    plan = { turnstile: p2 };
    const r = await turn("hello");
    eq(r.body.reason, null, `FAIL OPEN — ${label}: the turn is still served`);
    eq(gatewayCalls(), 1, `FAIL OPEN — ${label}: and the gateway WAS called`);
    eq(outcomes().unreachable, 1, `FAIL OPEN — ${label}: recorded as \`unreachable\``);
  }

  /* ---- and nothing from a 400 body is ever forwarded ---------------------- *
   * The 400 body is now PARSED, which it never used to be, so the leak sweep matters more
   * here than anywhere: `error-codes` goes into a boolean and is dropped. */
  fresh();
  plan = { turnstile: { status: 400, text: JSON.stringify({
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
  plan = { turnstile: { body: { success: true, action: ACT.chat, hostname: "evil.example.com" } } };
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
 * FOUR behaviours that matter and that nothing else can check:
 *
 *   · A FRESH TOKEN PER SEND. Tokens are single-use and live 300 s, so a module that
 *     minted one per page load would work for exactly the first turn of a conversation
 *     and then refuse every later one — a demo that breaks after one sentence, in a way
 *     that looks like the brain failing.
 *   · ONE WIDGET PER ACTION. The two spending routes require different actions back, so a
 *     token minted for a typed sentence must not be what the microphone sends.
 *   · A FAILED SCRIPT LOAD IS NEVER MEMOISED. One `onerror` — or one load that merely
 *     arrived after the 8 s deadline — used to disable every live turn for the rest of the
 *     page session while the page told the visitor to retry, which could never work.
 *   · A CHALLENGE ON SCREEN IS NEVER RESET OUT FROM UNDER THE VISITOR. The page says "try
 *     me once more"; doing that mid-challenge used to discard the half-finished puzzle.
 */
{
  const SRC = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");

  /** A fake page. `sitekey` is what `mode.js` would have published.
   *
   *  Richer than a stub needs to be for one assertion, and deliberately: the module now
   *  injects a `<style>`, creates a holder AND a per-action child box, so a document fake
   *  that only counted `<script>` tags would silently stop exercising most of it. */
  function world(sitekey) {
    const made = { scripts: [], styles: [], body: [], head: [] };
    const el = (tag) => ({
      tag, id: "", className: "", attrs: {}, children: [], style: {},
      setAttribute(k, v) { this.attrs[k] = String(v); },
      appendChild(c) { this.children.push(c); return c; },
    });
    globalThis.document = {
      getElementById: () => null,
      createElement: (tag) => {
        const e = el(tag);
        if (tag === "script") made.scripts.push(e);
        if (tag === "style") made.styles.push(e);
        return e;
      },
      createTextNode: (t) => ({ text: String(t) }),
      head: { appendChild(e) { made.head.push(e); return e; } },
      body: { appendChild(e) { made.body.push(e); return e; } },
      documentElement: { appendChild(e) { made.head.push(e); return e; } },
      addEventListener() {},
    };
    globalThis.window = {
      // Faithful to `mode.js`: `onChange` invokes the listener IMMEDIATELY with the
      // current snapshot and again on every change, and returns an unsubscribe. That
      // immediate call is how the real page renders the chat widget before the first Send
      // rather than during it, so a fake that never called back would be testing a
      // different module.
      moxieMode: { turnstile: () => sitekey, onChange: (fn) => { fn({}); return () => {}; } },
    };
    return made;
  }

  /** Cloudflare's widget API, faked to the documented surface — and PER WIDGET, because
   *  the module now renders one for each action and a single-widget fake could not tell a
   *  chat token from a microphone one.
   *
   *  `held` is what `getResponse()` answers — the token a widget is sitting on. It is a
   *  mutable field rather than a constant because the interactive case is precisely a
   *  widget whose token appears LATER, and a fake that could only ever answer "" would
   *  make that path untestable. */
  function fakeApi(behaviour) {
    const widgets = {};
    const calls = { render: 0, reset: 0, execute: 0, opts: {}, widgets, order: [] };
    let seq = 0;
    globalThis.window.turnstile = {
      render(box, opts) {
        calls.render++;
        const id = "widget-" + ++seq;
        widgets[id] = { id, box, opts, held: "" };
        calls.opts[opts.action] = opts;
        calls.order.push(opts.action);
        return id;
      },
      reset(id) { calls.reset++; if (widgets[id]) widgets[id].held = ""; },
      execute(id) {
        calls.execute++;
        const w = widgets[id];
        if (!w) return;
        if (behaviour === "error") { w.opts["error-callback"](); return; }
        if (behaviour === "silent") return;                  // never calls back at all
        const tok = w.opts.action + "-token-" + calls.execute;
        w.held = tok;
        w.opts.callback(tok);
      },
      getResponse: (id) => (widgets[id] ? widgets[id].held : ""),
    };
    return calls;
  }

  const T = () => globalThis.window.moxieTurnstile;
  /** Race a pending mint against a short timer: the module's own 8 s deadline is the thing
   *  under test in the `silent` cases, so it cannot be shortened from here. */
  const soon = (pr) => Promise.race([pr, new Promise((r) => setTimeout(() => r("__pending"), 50))]);

  /* ---- not enforced: inert, and no third-party script is even requested --- */
  {
    const w = world("");
    (0, eval)(SRC);
    const tok = await T().getToken("chat");
    eq(tok, "", "no sitekey published => getToken() resolves \"\" (send as-is)");
    eq(w.scripts.length, 0, "…and Cloudflare's script is NEVER requested on an unenforced page");
    eq(w.body.length, 0, "…and NOTHING is added to the page — no holder, no box");
    eq(T().enforced(), false, "…and the module says it is not enforced");
    eq(T().stats().skipped, 1, "…recorded as skipped");
  }

  /* ---- enforced and working: ONE widget per action, a FRESH token per send - */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    const t1 = await T().getToken("chat");
    const t2 = await T().getToken("chat");
    const t3 = await T().getToken("chat");
    eq(calls.render, 1, "the chat widget is rendered ONCE, however many sends there are");
    eq(calls.execute, 3, "…and executed once per send");
    eq(calls.reset, 3, "…with reset() before EVERY execute: tokens are single-use");
    ok(t1 && t2 && t3, "every send got a token");
    ok(t1 !== t2 && t2 !== t3, "…and they are DIFFERENT tokens — not one token reused");

    // The render options, which are the UX decision and the server contract in one object.
    /* `|| {}` on purpose. With the client's action table drifted (mutation row D6e) there
      * is no entry under `ACT.chat` at all, and a bare property read THREW — which exits
      * node before a single `FAIL:` line is printed, so the mutation table saw an
      * unattributable crash instead of a named red check. A guard's test must fail
      * legibly; the same defensive read is applied everywhere below for the same reason. */
    const opts = calls.opts[ACT.chat] || {};
    eq(opts.sitekey, SITEKEY, "the widget is rendered with the PUBLISHED sitekey");
    eq(opts.action, ACT.chat,
       "…and the action the server requires back for THIS route (`TURNSTILE_ACTIONS.chat`)");
    eq(opts.appearance, "interaction-only",
       "…invisible unless Cloudflare wants an interaction: NO checkbox in front of a child");
    eq(opts.execution, "execute",
       "…and the challenge runs when we ASK, which is what makes a per-send token possible");
  }

  /* ---- TWO ACTIONS, TWO WIDGETS, AND NO CROSSING OVER -------------------- *
   * The client half of the cross-route replay the server's check 2 refuses. If this page
   * minted one token type for both routes, check 2 would refuse every microphone turn on
   * a correctly-configured deployment — and if it minted a `chat` token for the ears, the
   * ears would be spendable with a typed turn's challenge. */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    const chatTok = await T().getToken("chat");
    const earTok = await T().getToken("transcribe");
    eq(calls.render, 2, "asking for both actions renders TWO widgets");
    deep(calls.order, [ACT.chat, ACT.transcribe],
         "…the chat one first (it is rendered eagerly at boot), the ears on first use");
    eq((calls.opts[ACT.chat] || {}).action, ACT.chat, "…each with its OWN action: chat");
    eq((calls.opts[ACT.transcribe] || {}).action, ACT.transcribe, "…and transcribe");
    ok(String(chatTok).startsWith(ACT.chat + "-"), `the chat send got a chat token (${chatTok})`);
    ok(String(earTok).startsWith(ACT.transcribe + "-"), `…and the mic send got a mic token (${earTok})`);
    ok(chatTok !== earTok, "…and they are not the same string");
    deep(T().actions(), { chat: ACT.chat, transcribe: ACT.transcribe },
         "…and the module publishes the table it mints from");

    // A second chat send resets ONLY the chat widget: the mic's solved challenge is not
    // collateral damage.
    const before = calls.reset;
    await T().getToken("chat");
    eq(calls.reset, before + 1, "a chat send resets one widget, not both");
  }

  /* ---- AN ACTION THIS MODULE DOES NOT KNOW IS `null`, NEVER A CHAT TOKEN -- *
   * The client's half of `_lib/turnstile.js::actionFor`'s "there is no default". A missing
   * or misspelt action must refuse, because the default that reads best (`chat`) is
   * exactly how a microphone turn would come to be paid for with a typed turn's token. */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    for (const [label, arg] of [["no action at all", undefined], ["an empty string", ""],
                                ["a misspelt one", "chatt"], ["a non-string", 7]]) {
      eq(await T().getToken(arg), null, `${label}: getToken() resolves null, not a token`);
    }
    deep(calls.order, [ACT.chat],
         "…and no NEW widget is rendered for an action we do not have (only the eager chat one)");
    eq(T().stats().unknownAction, 4, "…recorded, so the caller's bug is diagnosable");
    eq(T().stats().tokens, 0, "…and no token was minted by any of them");
  }

  /* ---- enforced and broken: `null`, never a hang and never a lie ---------- */
  for (const [label, behaviour] of [["the widget errors", "error"], ["the widget never answers", "silent"]]) {
    world(SITEKEY);
    fakeApi(behaviour);
    (0, eval)(SRC);
    const got = await soon(T().getToken("chat"));
    if (behaviour === "error") {
      eq(got, null, `${label}: getToken() resolves null — the caller must not send`);
    } else {
      eq(got, "__pending", `${label}: getToken() has NOT resolved a token (the deadline owns it)`);
    }
  }

  /* ---- THE INTERACTIVE CASE: a solve that lands past the deadline ---------- *
   * The first of the two paths by which a visitor who actually had to click something ever
   * gets a turn. Their solve arrives after `getToken()` already resolved `null` and the
   * page already said "try me once more", so the widget is left holding a perfectly good
   * unspent token — and the NEXT send must spend that rather than reset it and start the
   * challenge over. An earlier draft called `reset()` unconditionally, which would have
   * made the page unusable for exactly the visitors Turnstile decided to challenge. */
  {
    world(SITEKEY);
    const calls = fakeApi("silent");                 // execute() never calls back
    (0, eval)(SRC);
    /* Mints are serialised (one `pending` resolver per widget), so the second send below
     * queues behind the first until the first gives up — which is correct behaviour and,
     * at the shipped 8 s, eight seconds of suite. Shortened here for the same reason as
     * the rejoin case, with the shipped constant pinned from the source separately. It is
     * 200 ms rather than 40 so that `soon()`'s own 50 ms race still observes the mint as
     * PENDING — the assertion below is that it has not resolved a token, which a deadline
     * shorter than the race would satisfy by resolving `null` instead. */
    T().__deadlineMs(200);
    const first = await soon(T().getToken("chat"));
    eq(first, "__pending", "the first send is still waiting on an interactive challenge…");
    // The human finishes clicking, late. Cloudflare hands the widget a token.
    (Object.values(calls.widgets)[0] || {}).held = "solved-late";
    const second = await T().getToken("chat");
    eq(second, "solved-late",
       "…and the NEXT send spends the token the widget was left holding");
    eq(T().stats().reused, 1, "…recorded as reused, not minted");

    /* AND IT IS NEVER HANDED OUT TWICE. `getResponse()` still answers `solved-late`, so a
     * module that trusted it blindly would replay the token and the server would refuse
     * the turn as `timeout-or-duplicate` — a bug that appears only on the turn AFTER an
     * interactive challenge, which is about as hard to notice as a bug gets. */
    const third = await soon(T().getToken("chat"));
    ok(third !== "solved-late", `a spent token is NEVER handed out again (got ${JSON.stringify(third)})`);
    T().__deadlineMs(0);
  }

  /* ---- A SEND DURING A LIVE CHALLENGE WAITS FOR IT; IT DOES NOT RESTART IT - *
   * The second interactive path, and the one the page's own copy walks a visitor straight
   * into: send #1's deadline fires, Moxie says "try me once more", and the visitor does
   * exactly that WHILE still working through the challenge on screen. An unconditional
   * `reset()` there discarded the half-finished puzzle and drew a new one — every single
   * time they followed the instruction — so the only way to complete a turn was to ignore
   * the page. Now the second send becomes the waiter for the challenge already running. */
  {
    world(SITEKEY);
    const calls = fakeApi("silent");
    (0, eval)(SRC);
    /* THE REAL SEQUENCE, at 1/200th of the wall clock. The visitor's second Send happens
     * AFTER the page told them to try again — which happens when the first mint's deadline
     * fires — so this case is unreachable without waiting one deadline out. `__deadlineMs`
     * is the test hook that buys that for 40 ms instead of 8 s; the shipped constant is
     * pinned from the source at the end of this block, so shortening it here cannot hide a
     * change to it. */
    eq(T().__deadlineMs(40), 40, "the mint deadline is shortened for this one case");
    eq(await T().getToken("chat"), null, "send #1 gives up when its deadline fires…");
    eq(calls.reset, 1, "…after one reset");
    eq(calls.execute, 1, "…and one execute");
    eq(T().stats().timeouts, 1, "…recorded as a timeout, not as an error");

    // The visitor is still working through the challenge and does what the page said.
    const second = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(calls.reset, 1, "send #2 mid-challenge does NOT reset the live challenge");
    eq(calls.execute, 1, "…and does NOT start a second one");
    eq(T().stats().rejoined, 1, "…it JOINS the one already on screen (recorded)");

    // They finish. The waiting send is the one that gets the token.
    const w = Object.values(calls.widgets)[0] || { opts: { callback() {} } };
    w.held = "solved-at-last";
    w.opts.callback("solved-at-last");
    eq(await second, "solved-at-last", "…and the waiting send is answered by that solve");
    eq(calls.reset, 1, "…still one reset in total: nothing was thrown away");

    // AND ONCE THE CHALLENGE HAS CONCLUDED, THE NEXT SEND DOES ASK FOR A NEW ONE. Without
    // this, "never reset" would be indistinguishable from "reset is broken".
    await soon(T().getToken("chat"));
    eq(calls.reset, 2, "a send AFTER the challenge concluded resets and asks again");
    eq(calls.execute, 2, "…so `outstanding` is cleared by the callback, not sticky");
    T().__deadlineMs(0);                             // back to the shipped value
    ok(/var EXECUTE_TIMEOUT_MS = 8000;/.test(SRC),
       "…and the SHIPPED deadline is still 8 s, read out of the source");
  }

  /* ---- Cloudflare's script cannot load, AND THE NEXT SEND MAY TRY AGAIN ---- *
   * THE BUG THIS BLOCK EXISTS FOR. The first version memoised the promise, so ONE failed
   * load — an ad-blocker rule, a captive portal, a cell handoff, a single edge 5xx — or a
   * load that merely took longer than the 8 s deadline, disabled every live turn for the
   * REST OF THE PAGE SESSION: the cached `false` was handed to every later caller, no
   * second request was ever made, no widget was ever rendered, and the page kept telling
   * the visitor to retry something that could not succeed. Only a reload recovered, and
   * nothing said so. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);                       // no window.turnstile: nothing to render with
    eq(w.scripts.length, 1, "the script IS requested when a sitekey is published");
    ok(/^https:\/\/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js\?render=explicit$/.test(w.scripts[0].src),
       `…from Cloudflare's documented explicit-render URL (${w.scripts[0].src})`);
    w.scripts[0].onerror();               // what a CSP refusal fires
    eq(await T().getToken("chat"), null,
       "a script that cannot load resolves null — never a silent dead send");
    eq(T().stats().scriptErrors, 1, "…and it is recorded");

    // THE PART THAT WAS BROKEN: the next send asks again.
    const retry = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(w.scripts.length, 2, "…and the NEXT send REQUESTS THE SCRIPT AGAIN (no cached failure)");
    // This time it arrives, and the send that asked for it is served.
    const calls = fakeApi("ok");
    // `|| {onload(){}}` for the same reason as the defensive reads above: with the memo
    // bug (row D6h) there IS no second tag, and indexing it threw before any named red
    // could print.
    (w.scripts[1] || { onload() {} }).onload();
    const tok = await retry;
    ok(!!tok && tok !== null, `…and that send gets a token once the script lands (${tok})`);
    eq(calls.render, 1, "…the widget renders on the retry rather than never");

    // ...and it is BOUNDED: a permanently blocked host does not get a tag per send.
    eq(T().stats().scriptTries, 2, "the script has been requested exactly twice so far");
  }

  /* ---- IT GIVES UP *ASKING*, AND STILL USES AN API THAT TURNS UP ANYWAY --- *
   * The bound on the retry above is real — a host that is blocked permanently (an
   * extension, a DNS filter, a corporate policy) must not get one `<script>` tag per Send.
   * But "stop asking" and "give up" are different things: if `window.turnstile` is present
   * for ANY reason once the budget is spent, a page that refuses to look is refusing turns
   * it could serve. `loadApi()` answers from `api()` BEFORE it consults its own memo or its
   * own counter, which is what makes both true at once. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);
    for (let i = 1; i <= 3; i++) {
      const pr = T().getToken("chat");
      await new Promise((r) => setTimeout(r, 5));
      eq(w.scripts.length, i, `attempt ${i}: exactly ${i} script request(s) so far`);
      (w.scripts[i - 1] || { onerror() {} }).onerror();
      eq(await pr, null, `attempt ${i}: …resolves null when that request fails`);
      await new Promise((r) => setTimeout(r, 0));    // let the memo clear
    }
    eq(await T().getToken("chat"), null, "a fourth send still resolves null…");
    eq(w.scripts.length, 3, "…and does NOT append a fourth tag: the retry is BOUNDED");
    eq(T().stats().scriptTries, 3, "…at exactly MAX_SCRIPT_TRIES requests");

    // The API turns up anyway — another copy of the script, a late execution, an extension
    // that injected it. The page must use it.
    const calls = fakeApi("ok");
    const tok = await T().getToken("chat");
    ok(!!tok, `…yet an API that turns up anyway IS used, budget spent or not (${tok})`);
    eq(calls.render, 1, "…the widget renders from it");
    eq(w.scripts.length, 3, "…without asking for the script again");
  }

  /* ---- A SCRIPT THAT LOADS *LATE* IS STILL USED --------------------------- *
   * The other half of the same bug, and the sneakier half: the request SUCCEEDS but takes
   * longer than the 8 s deadline, so the deadline resolved `false` while the API was on
   * its way. The module's own stats read `scriptLoads: 1, renders: 0` — the script
   * demonstrably loaded and was never used. What decides must be whether `window.turnstile`
   * is HERE, not what a boolean said eight seconds ago. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);
    eq(w.scripts.length, 1, "the script was requested");
    /* The load that goes quiet: the first request is settled as failed (so the memo is
     * clear) and the SECOND is left to the deadline with neither `onload` nor `onerror`
     * ever firing — which is what an extension or a proxy that swallows the events looks
     * like, and what the slow-load case looks like from this module's point of view. The
     * deadline is shortened for the same reason as the rejoin case above: reaching this
     * state honestly costs eight seconds and the shipped constant is pinned from the
     * source separately. */
    w.scripts[0].onerror();
    // One tick, so the failed load clears its own memo before the next ask (the memo is
    // cleared in a `.then`, which is a microtask — a send issued in the very same tick as
    // the failure legitimately still sees it).
    await new Promise((r) => setTimeout(r, 0));
    T().__deadlineMs(40);
    const pending = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(w.scripts.length, 2, "…and asked again");
    // The API turns up without either event: only `api()` knows.
    const calls = fakeApi("ok");
    const tok = await pending;
    ok(!!tok, `a script whose API arrives with no onload is USED, not written off (${tok})`);
    eq(calls.render, 1, "…the widget renders from the API that is PRESENT");
    eq(w.scripts.length, 2, "…and no third request was needed: `api()` is what decides");
    eq(T().stats().scriptLoads, 0, "…even though `onload` never fired at all");
    T().__deadlineMs(0);
  }

  /* ---- THE HOLDER: it cannot swallow the control under the visitor's thumb - *
   * The structural half of a defect measured in a real browser (the behavioural half is
   * `sim/test_mobile_layout.mjs`): a 300x65 challenge at `bottom: 16px` sat exactly on top
   * of `#rail-toggle`, the only way to open the drawer that holds the text box on a phone,
   * and `elementFromPoint()` at the toggle's centre returned the widget. Two properties
   * are asserted from the SOURCE here because they are cheap, they are the whole fix, and
   * this suite runs in the fast tier with no browser at all. */
  {
    const w = world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    // The holder and its stylesheet are built LAZILY, on the first render — an unenforced
    // page must add nothing to the document at all (asserted at the top of this section) —
    // so one send has to happen before there is any geometry to look at.
    await T().getToken("chat");
    eq(calls.render, 1, "one send rendered the widget that needs somewhere to draw");
    const css = (w.styles[0] && w.styles[0].children[0] && w.styles[0].children[0].text) || "";
    ok(w.styles.length === 1, "the module injects exactly one <style> for its holder");
    ok(/#turnstile-holder\{[^}]*pointer-events:none/.test(css),
       `the holder layer itself is pointer-events:none — an empty box cannot swallow a tap (${css.slice(0, 80)})`);
    ok(/#turnstile-holder>\*\{[^}]*pointer-events:auto/.test(css),
       "…while the challenge inside it IS clickable: an unusable challenge is a dead page");
    ok(!/#turnstile-holder\{[^}]*bottom:/.test(css),
       "…and it is NOT anchored to the bottom, where every control on this page lives");
    ok(/#turnstile-holder\{[^}]*align-items:center/.test(css) &&
       /#turnstile-holder\{[^}]*justify-content:center/.test(css),
       "…it is centred in the viewport, which holds no controls at any width");
    // The per-action boxes are children of the holder, so the `> *` rule reaches them.
    const holderEl = w.body.find((e) => e.id === "turnstile-holder");
    ok(!!holderEl, "the holder is appended to document.body, outside every scrolling panel");
    eq((holderEl || { children: [] }).children.length, 1,
       "…and each action's widget gets its own child box");
    eq(((holderEl || { children: [{ attrs: {} }] }).children[0] || { attrs: {} }).attrs["data-action"],
       ACT.chat, "…tagged with the action it is for");
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
  const transportSrc = readFileSync(join(repo, "sim", "web", "cloud-transport.js"), "utf8");
  const micSrc = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");
  const headers = readFileSync(join(repo, "sim", "web", "_headers"), "utf8");
  const simHtml = readFileSync(join(repo, "sim", "web", "sim.html"), "utf8");

  // THE ACTION TABLE. Read out of the client source, compared with the server's, KEY BY
  // KEY. A drift refuses every visitor with `turnstile_failed` and looks like a Cloudflare
  // fault; a MISSING key silently sends `null` from the browser and kills one route.
  const table = /var ACTIONS = \{([^}]*)\}/.exec(clientSrc);
  ok(!!table, "sim/web/turnstile.js declares its actions as a single table");
  const clientActions = {};
  for (const pair of (table ? table[1] : "").split(",")) {
    const m = /([A-Za-z_]+)\s*:\s*"([^"]*)"/.exec(pair);
    if (m) clientActions[m[1]] = m[2];
  }
  deep(clientActions, { chat: ACT.chat, transcribe: ACT.transcribe },
       "the client's ACTIONS table equals the server's TURNSTILE_ACTIONS — check 2 compares them");
  deep(Object.keys(ts.TURNSTILE_ACTIONS).sort(), ["chat", "transcribe"],
       "…and there is one action per SPENDING ROUTE, keyed by the route's own name");

  // AND EACH CALLER NAMES ITS OWN ROUTE'S ACTION. This is the assertion that would have
  // caught the whole `/api/transcribe` gap: a page that asked for a `chat` token on the
  // microphone path would be refused by check 2 on every clip.
  ok(/getToken\("chat"\)/.test(transportSrc),
     "cloud-transport.js mints for the CHAT action");
  ok(/getToken\("transcribe"\)/.test(micSrc),
     "…and mic.js mints for the TRANSCRIBE action, so a typed token cannot pay for the ears");
  ok(!/getToken\("chat"\)/.test(micSrc), "…and mic.js never asks for a chat token");

  // AND EACH ROUTE VERIFIES AGAINST ITS OWN. The server half of the same contract, read
  // off the two route files, because `verify()` has NO DEFAULT and a route that passed the
  // wrong name would accept the other route's tokens with nothing else noticing.
  for (const [file, route] of [["chat.js", "chat"], ["transcribe.js", "transcribe"]]) {
    const src = readFileSync(join(repo, "functions", "api", file), "utf8");
    // Nested parens in the argument list are real (`tokenFromHeader(request)`), so the
    // pattern allows exactly one level of them rather than banning `)` outright.
    const call = new RegExp('verifyTurnstile\\((?:[^()]|\\([^()]*\\))*,\\s*"([a-z]+)"\\s*\\)').exec(src);
    ok(!!call, `${file} calls verifyTurnstile with an explicit route name`);
    eq(call && call[1], route, `…and ${file} names its OWN route (${route}), not the other one`);
    ok(Object.prototype.hasOwnProperty.call(ts.TURNSTILE_ACTIONS, (call && call[1]) || ""),
       `…which is a key TURNSTILE_ACTIONS knows (an unknown one refuses every visitor)`);
    ok(new RegExp('admit\\(\\{ request, cfg, route: "' + route + '" \\}\\)').test(src),
       `…and it is the same route name \`admit()\` charges under`);
  }

  // THE FIELD NAMES, likewise: the token must arrive where each route reads it.
  ok(clientSrc.includes(ts.TOKEN_FIELD) === false && clientSrc.includes(ts.TOKEN_HEADER) === false,
     "…and turnstile.js names NEITHER wire name: its callers own the wire shape");
  ok(transportSrc.includes('"' + ts.TOKEN_FIELD + '"'),
     `cloud-transport.js sends the token under the field the chat route reads (${ts.TOKEN_FIELD})`);
  ok(micSrc.includes('"' + ts.TOKEN_HEADER + '"'),
     `mic.js sends it on the header the transcribe route reads (${ts.TOKEN_HEADER})`);
  /* THE HEADER IS NOT IN CLOUDFLARE'S OWN `CF-` NAMESPACE, and that is deliberate: the
   * edge in front of these Functions owns that prefix and rewrites members of it. */
  ok(!/^CF-/i.test(ts.TOKEN_HEADER),
     `the token header stays out of Cloudflare's own CF- namespace (${ts.TOKEN_HEADER})`);

  // THE SITEKEY IS NOT IN THE REPO. Not a secrecy claim — a sitekey is public — but a C3
  // one: this deployment's sitekey baked into shipped HTML or JS would hand every fork and
  // every branch preview a widget bound to a domain list they are not on.
  for (const f of ["sim.html", "index.html", "setup.html", "cloud.html", "docs.html",
                   "turnstile.js", "cloud-transport.js", "mic.js", "mode.js", "env.js"]) {
    const src = readFileSync(join(repo, "sim", "web", f), "utf8");
    ok(!/\b[0-9]x[0-9A-Za-z]{20,}\b/.test(src),
       `${f} carries NO Turnstile sitekey — the browser learns it from /api/health (C3)`);
    ok(!/data-sitekey/.test(src),
       `${f} has no hard-coded data-sitekey attribute either`);
  }

  /* ---- TRAP B, AS A CLASS AND NOT AS ONE FILENAME ------------------------- *
   * The app-script no-cache list is THE WHOLE MECHANISM: a client script missing from it
   * is served with Pages' default caching, so a redeploy can leave a visitor running
   * yesterday's token minter against today's route.
   *
   * IT IS ENUMERATED rather than spot-checked, and that is this pass's fix. A guard that
   * names `turnstile.js` proves only that THIS slice remembered; the next new client
   * script gets nothing, which was demonstrated by adding a `zz-probe.js` to `sim.html`
   * and watching every suite in the repo stay green. `sim/test_csp.mjs` block 9 asserts
   * the same property against the same file in a real browser run; this copy is here so
   * the fast tier (no Chrome) catches it too. */
  {
    const listed = new Set();
    for (const m of headers.matchAll(/^\/([A-Za-z0-9._-]+\.js)\n\s+Cache-Control:\s*no-cache$/gm)) {
      listed.add(m[1]);
    }
    ok(listed.size > 15, `the no-cache list was actually parsed (${listed.size} scripts)`);
    const shipped = readdirSync(join(repo, "sim", "web")).filter((f) => f.endsWith(".js")).sort();
    const missing = shipped.filter((f) => !listed.has(f));
    deep(missing, [],
         `EVERY script in sim/web has its own no-cache entry — missing: ${JSON.stringify(missing)}`);
    ok(listed.has("turnstile.js"), "…including this slice's own turnstile.js");
  }

  // The CSP needs the widget host in THREE directives, and each absence fails silently.
  const csp = (/^\s+Content-Security-Policy:[ \t]*(.+)$/m.exec(headers) || [])[1] || "";
  for (const d of ["script-src", "frame-src", "connect-src"]) {
    const directive = csp.split(";").map((x) => x.trim()).find((x) => x.startsWith(d)) || "";
    ok(directive.includes("https://challenges.cloudflare.com"),
       `the shipped CSP allows the widget host in ${d} (without it the widget fails SILENTLY)`);
  }
  ok(!/frame-src\s+'none'/.test(csp),
     "frame-src is no longer 'none' — Turnstile draws its challenge in an iframe");

  /* Load order: the module must exist before either send path can call it.
   *
   * MEASURED ON THE `<script src>` TAGS since 2026-09-05, not on the first mention of a
   * filename anywhere in the document. `indexOf("mic.js")` was only ever a PROXY for load
   * order, and it stopped being one the moment `sim.html`'s composer comment started
   * naming the files whose listeners bind to the controls it moved — the prose now comes
   * before the tags, so the proxy reported that `mic.js` loads first. A load-order check a
   * COMMENT can flip is not measuring load order. `src="…"` is, and each file is asserted
   * present first so a typo cannot pass as `-1 < n`. */
  const loadsAt = (f) => simHtml.indexOf('src="' + f);
  for (const f of ["mode.js", "turnstile.js", "cloud-transport.js", "mic.js"])
    ok(loadsAt(f) > -1, `sim.html has a <script src> for ${f}`);
  ok(loadsAt("mode.js") < loadsAt("turnstile.js"),
     "…after mode.js, which is where the sitekey comes from");
  ok(loadsAt("turnstile.js") < loadsAt("cloud-transport.js"),
     "…and before cloud-transport.js, which calls it on the typed send path");
  ok(loadsAt("turnstile.js") < loadsAt("mic.js"),
     "…and before mic.js, which calls it on the microphone send path");
}

/* =========================================================================== *
 * 11. THE EARS — `/api/transcribe`, the OTHER route that spends money
 * =========================================================================== *
 * WHAT THIS BLOCK EXISTS FOR. The first version of this slice guarded `/api/chat` and
 * deferred the ears "to a later slice with its own widget action" — and the ears are the
 * MORE expensive half. Driven in-process against the shipped module, with production's
 * exact Turnstile variables set, a plain `curl` reached the paid gateway:
 *
 *     POST /api/transcribe, Origin: <ours>, Sec-Fetch-Site: same-origin,
 *     Content-Type: audio/wav, a 16 kHz mono RIFF body
 *     -> 200, ok:true, reason:null, a transcript, ONE upstream call, ZERO siteverify calls
 *
 * No browser, no widget, no token. What was left bounding it is what this tree itself says
 * is not a bot control: the forgeable origin pin, per-IP 10/min and 60/hour with NO daily
 * window, and a per-isolate unit budget. 60 x 15 s is 15 minutes of billable
 * speech-to-text per hour from one address, ~1,440 calls a day, for ever.
 *
 * So every property §3, §6 and §7 prove for the chat turn is proven here for the ears,
 * plus the one that only exists because there are two of them: A TOKEN MINTED FOR ONE
 * ROUTE IS NOT SPENDABLE ON THE OTHER.
 */
{
  /* ---- the attack, refused ------------------------------------------------ */
  fresh();
  const bare = await postAudio({});
  eq(bare.body.reason, "turnstile_failed",
     "a tokenless clip is REFUSED by the ears — this is the curl loop that used to be served");
  eq(bare.status, 403, "…with 403");
  eq(gatewayCalls(), 0, "…and ZERO calls to the paid STT gateway");
  eq(verifyCalls(), 0, "…for FREE: a missing token costs no siteverify call either");
  eq(outcomes().no_token, 1, "…recorded as `no_token`");
  eq(bare.body.transcript, "", "…and no transcript came back");

  /* ---- the ordinary case, so the refusals mean something ------------------ */
  fresh();
  plan = { turnstile: { action: ACT.transcribe } };
  const good = await clip();
  eq(good.status, 200, "a clip with a valid TRANSCRIBE token is served");
  eq(good.body.reason, null, "…with no reason");
  eq(good.body.transcript, "i am a bot", "…and the transcript comes back");
  eq(verifyCalls(), 1, "…after exactly one siteverify call");
  eq(gatewayCalls(), 1, "…and one upstream call");
  eq(outcomes().verified, 1, "…recorded as `verified`");
  const svCall = sent.find((x) => x.url === ts.SITEVERIFY_URL) || { opt: {} };
  ok(!!sent.find((x) => x.url === ts.SITEVERIFY_URL),
     "…and a verification actually went to Cloudflare for this clip");
  eq(new URLSearchParams(String(svCall.opt.body)).get("response"), TOKEN,
     "…and it verified the token off the header, not off the audio body");

  /* ---- NO CROSSING OVER, IN EITHER DIRECTION ----------------------------- *
   * The whole reason there are two actions. A chat token is a cheap token: it is minted by
   * typing, which every visitor does, and if it bought a microphone turn then 15 s of
   * billable STT would cost a challenge solved for 160 tokens of completion. */
  fresh();
  plan = { turnstile: { action: ACT.chat } };
  const crossed = await clip();
  eq(crossed.body.reason, "turnstile_failed",
     "a CHAT token presented to the ears is refused — check 2 compares the action");
  eq(gatewayCalls(), 0, "…with zero upstream calls");
  eq(outcomes().failed, 1, "…recorded as `failed`, i.e. the visitor's token is wrong for here");

  fresh();
  plan = { turnstile: { action: ACT.transcribe } };
  eq((await turn("hello")).body.reason, "turnstile_failed",
     "…and a MICROPHONE token presented to the chat route is refused too: it is symmetric");

  /* ---- the other two mandatory checks, on this route too ----------------- */
  fresh();
  plan = { turnstile: { body: { success: false, action: ACT.transcribe, hostname: HOSTNAME,
                                "error-codes": ["invalid-input-response"] } } };
  eq((await clip()).body.reason, "turnstile_failed", "CHECK 1 refuses on the ears as well");
  eq(gatewayCalls(), 0, "…for free");

  fresh();
  plan = { turnstile: { body: { success: true, action: ACT.transcribe, hostname: "evil.example.com" } } };
  const badHost = await clip();
  eq(badHost.body.reason, "turnstile_misconfigured", "CHECK 3 refuses on the ears as well");
  eq(badHost.status, 503, "…with the deployment-level status");
  eq(gatewayCalls(), 0, "…for free");

  /* ---- fail open, and fail closed, both halves ---------------------------- */
  fresh();
  plan = { turnstile: { throw: "TypeError" } };
  const openEars = await clip();
  eq(openEars.status, 200, "a siteverify outage does NOT take the ears down (fail open)");
  eq(gatewayCalls(), 1, "…the clip is transcribed");
  eq(outcomes().unreachable, 1, "…recorded as `unreachable`");

  fresh();
  plan = { turnstile: { status: 400,
                        text: JSON.stringify({ "error-codes": ["invalid-input-secret"], success: false }) } };
  eq((await clip()).body.reason, "turnstile_misconfigured",
     "…while a wrong secret refuses the ears too, at the 400 Cloudflare really sends");
  eq(gatewayCalls(), 0, "…spending nothing");

  /* ---- UNENFORCED IS UNTOUCHED, which is every fork and every preview ----- */
  fresh();
  const forkEars = Object.assign({}, GATEWAY, { DEMO_STT_MODEL: "test-ears-model" });
  const forked = await postAudio({}, forkEars);
  eq(forked.status, 200, "with no Turnstile pair the ears answer exactly as they always did");
  eq(forked.body.transcript, "i am a bot", "…with the transcript");
  eq(verifyCalls(), 0, "…and make NO siteverify call: the check is a no-op, not a lenient check");
  eq(outcomes().skipped, 1, "…recorded as `skipped`");

  /* ---- THE ORDER: everything cheaper than the bot check is still free ----- *
   * D1, on this route. The ears have three free local refusals of their own — the byte
   * floor, the container sniff and the WAV duration ceiling — and not one of them may buy
   * a round trip to Cloudflare first. `too_short` in particular is *the most common
   * refusal a real demo will serve* (this route's own header says so), and it would have
   * been the single biggest source of siteverify traffic if the order were wrong. */
  for (const [label, body, ctype, want] of [
    ["a 300-byte accidental clip (the byte floor)", new Uint8Array(300), "audio/wav", "too_short"],
    ["500 KB of JPEG (not a container we know)",
     (() => { const b = new Uint8Array(3000); b[0] = 0xff; b[1] = 0xd8; b[2] = 0xff; return b; })(),
     "image/jpeg", "bad_request"],
    ["a WAV whose own header declares 30 s", wavBytes(30000), "audio/wav", "too_long"],
  ]) {
    fresh();
    const request = new Request(ORIGIN + "/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": ctype, Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
                 "CF-Connecting-IP": "203.0.113.9", [ts.TOKEN_HEADER]: TOKEN },
      body,
    });
    const res = await transcribe.onRequestPost({ request, env: EARS });
    await assertClean(res, "ears order " + label);
    const parsed = JSON.parse(await res.clone().text());
    eq(parsed.reason, want, `${label} answers ${want}`);
    eq(verifyCalls(), 0, `${label}: ZERO siteverify calls — it is refused more cheaply`);
    eq(gatewayCalls(), 0, `${label}: ZERO upstream calls`);
  }

  /* ---- and the rate limiter still stands in front of siteverify ---------- */
  fresh();
  plan = { turnstile: { action: ACT.transcribe } };
  const cappedEars = Object.assign({}, EARS, { DEMO_STT_PER_MIN: "2", DEMO_CACHE_COUNTER: "0" });
  const earReasons = [];
  for (let i = 0; i < 4; i++) earReasons.push((await clip(cappedEars)).body.reason);
  deep(earReasons, [null, null, "rate_limited", "rate_limited"],
       "the per-IP window refuses clips 3-4");
  eq(verifyCalls(), 2, "…and only the ADMITTED clips cost a siteverify call");

  /* ---- THE SLOT COMES BACK, on this route's new refusal path too (D2) ----- */
  fresh();
  const tightEars = Object.assign({}, EARS, {
    DEMO_MAX_CONCURRENT_CHAT: "2",     // `transcribe` shares the chat ceiling on purpose
    DEMO_QUEUE_MAX_WAIT_MS: "0",
    DEMO_STT_PER_MIN: "100",
    DEMO_CACHE_COUNTER: "0",
  });
  for (let i = 0; i < 6; i++) {
    eq((await postAudio({}, tightEars)).body.reason, "turnstile_failed", `ears refusal ${i + 1}`);
    eq(limits.__state().inflight.transcribe || 0, 0,
       `…and the in-flight count is back to ZERO after ears refusal ${i + 1}`);
  }
  plan = { turnstile: { action: ACT.transcribe } };
  eq((await clip(tightEars)).status, 200,
     "after 6 refusals through a ceiling of 2, a good clip is STILL SERVED (no slot leaked)");
}

/* =========================================================================== *
 * 12. A REFUSAL GIVES THE SHARED BUDGET BACK (and keeps the per-IP window)
 * =========================================================================== *
 * THE ATTACK THIS BLOCK EXISTS FOR, measured against the shipped code:
 *
 *   200 POSTs to /api/chat with body {"text":"hello moxie"}, no token, one per source IP
 *   (so nothing rate-limits). All 200 correctly refused 403 `turnstile_failed`, ZERO
 *   gateway calls, ZERO siteverify calls — and `DEMO_UNIT_BUDGET_HOUR` (600) GONE, because
 *   `admit()` charges `UNITS.chat` (3) before the route body runs and the refusal kept it.
 *   The next request — a real visitor whose token verifies — came back 503
 *   `budget_exhausted`, mode `degraded`, and `mode.js` painted SCRIPTED until the hour
 *   rolled. Cost to the attacker: 200 requests with an empty JSON body and no token.
 *
 * So the bot control had turned a PAID drain into a FREE drain and left the availability
 * outcome exactly as it was — while `_lib/turnstile.js`'s own header claimed it "removes
 * the cheapest attack from the set of things that can drain the demo's budget at all".
 * `slot.refundBudget()` is the fix, and `_lib/limits.js::grantedSlot` carries the argument
 * for refunding the budget and NOT the per-IP window.
 */
{
  const UNITS_CHAT = 3;
  const UNITS_TRANSCRIBE = 2;

  /* ---- one refusal, one refund, and the counter says so ------------------ */
  fresh();
  eq((await post({ text: "hi" })).body.reason, "turnstile_failed", "a tokenless turn is refused…");
  eq(unitsSpent(), 0, "…and leaves the SHARED unit budget exactly where it found it");
  eq(refundedUnits(), UNITS_CHAT, `…having given back all ${UNITS_CHAT} units admission charged`);

  /* ---- a SERVED turn still pays, which is the other half of the claim ----- */
  fresh();
  eq((await turn("hello")).body.reason, null, "a served turn goes through…");
  eq(unitsSpent(), UNITS_CHAT, `…and DOES spend its ${UNITS_CHAT} units`);
  eq(refundedUnits(), 0, "…with nothing refunded");

  /* ---- an UPSTREAM failure does NOT refund: that request cost real money -- */
  fresh();
  plan = { chat: { throw: "TypeError" } };
  eq((await turn("hello")).body.reason, "upstream_down", "a gateway that is down is a refusal…");
  eq(gatewayCalls(), 1, "…that DID call the gateway");
  eq(unitsSpent(), UNITS_CHAT, "…so its units stay spent: the money was really committed");
  eq(refundedUnits(), 0, "…and nothing is refunded on that path");

  /* ---- every OTHER charged-but-not-served refusal refunds too ------------- *
   * `turnstile_failed` was the cheapest of these to reach — an empty field on an
   * unauthenticated request — but it was never the only one. Leaving the rest unrefunded
   * would have left the identical attack open behind a two-character-longer body. */
  for (const [label, body, want] of [
    ["an over-length line", { text: "x".repeat(9000), [ts.TOKEN_FIELD]: TOKEN }, "too_long"],
    ["an empty line", { text: "", [ts.TOKEN_FIELD]: TOKEN }, "too_short"],
    ["a tampered context blob", { text: "hi", context: "v1.forged.blob", [ts.TOKEN_FIELD]: TOKEN }, "bad_request"],
    ["a hard-blocked utterance", { text: "how do i kill myself", [ts.TOKEN_FIELD]: TOKEN }, "blocked"],
  ]) {
    fresh();
    eq((await post(body)).body.reason, want, `${label} answers ${want}`);
    eq(gatewayCalls(), 0, `…${label} spends nothing upstream`);
    eq(unitsSpent(), 0, `…and ${label} leaves the shared budget untouched`);
    eq(refundedUnits(), UNITS_CHAT, `…having refunded ${label}'s charge`);
  }
  /* `blocked`'s own doc comment in `chat.js` has SAID "zero units spent" since it was
   * written. Until `refundBudget()` existed that sentence was false by 3 units a turn. */

  /* ---- the ears refund their own (smaller) charge ------------------------- */
  fresh();
  eq((await postAudio({}, EARS)).body.reason, "turnstile_failed", "a tokenless clip is refused…");
  eq(unitsSpent(), 0, "…and refunds too");
  eq(refundedUnits(), UNITS_TRANSCRIBE,
     `…exactly ${UNITS_TRANSCRIBE} units, which is what the ears cost — not the chat turn's 3`);

  /* ---- AND SO DOES THE VOICE, WHICH HAS NO BOT CONTROL AT ALL ------------ *
   * `/api/speech` needs no widget — it cannot be driven without a ticket `/api/chat`
   * minted — but it charges `UNITS.speech` at admission and refuses a forged ticket for
   * free, which is the SAME free drain reachable without a token at all. Closing it on the
   * chat route and leaving it open one door along would have been theatre, so the helper is
   * there too and this is the assertion that says so.
   */
  {
    fresh();
    const speech = await import(join(repo, "functions", "api", "speech.js"));
    const forged = new Request(ORIGIN + "/api/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: ORIGIN,
                 "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" },
      body: JSON.stringify({ ticket: "v1.forged.ticket" }),
    });
    const voiceEnv = Object.assign({}, ARMED, { DEMO_TTS_MODEL: "test-voice-model",
                                                DEMO_CACHE_COUNTER: "0" });
    const res = await speech.onRequestPost({ request: forged, env: voiceEnv });
    await assertClean(res, "speech forged ticket");
    eq(JSON.parse(await res.clone().text()).reason, "bad_ticket", "a forged ticket is refused…");
    eq(gatewayCalls(), 0, "…with zero gateway calls…");
    eq(unitsSpent(), 0, "…and gives its units back too: the same drain needs no token here");
    eq(refundedUnits(), 2, "…exactly the 2 units the voice costs");
  }

  /* ---- THE PER-IP WINDOW IS DELIBERATELY *NOT* GIVEN BACK ---------------- *
   * The one asymmetry with `refundCharges()`'s other two call sites, and it is a decision
   * rather than an oversight: the unit budget is SHARED (its exhaustion is everybody
   * else's problem) while the per-IP window is SELF-INFLICTED and is the only thing that
   * makes a flood of free refusals from one address eventually go quiet. Refunding it
   * would make a tokenless refusal unlimited per IP — a new abuse channel opened to close
   * one. */
  fresh();
  const oneIp = Object.assign({}, ARMED, { DEMO_CHAT_PER_MIN: "3", DEMO_CACHE_COUNTER: "0",
                                           DEMO_QUEUE_MAX_WAIT_MS: "0" });
  const seq = [];
  for (let i = 0; i < 5; i++) seq.push((await post({ text: "hi" }, oneIp)).body.reason);
  deep(seq, ["turnstile_failed", "turnstile_failed", "turnstile_failed", "rate_limited", "rate_limited"],
       "tokenless refusals from ONE address still count against that address's window");
  eq(unitsSpent(), 0, "…while the shared budget is still untouched by all five");

  /* ---- THE WHOLE ATTACK, END TO END -------------------------------------- *
   * 200 tokenless requests from 200 addresses — the shape that took the demo scripted for
   * an hour — followed by one real visitor. The numbers are the production defaults.
   */
  fresh();
  // The queue is off for the same reason `EARS` switches it off: a leaked slot must produce
  // a red check here, not two hundred consecutive 2.5-second waits.
  const spread = Object.assign({}, ARMED, { DEMO_CACHE_COUNTER: "0", DEMO_QUEUE_MAX_WAIT_MS: "0" });
  eq(envlib.readConfig(spread).unitBudgetHour, 600, "the hourly budget is production's 600…");
  let refused = 0;
  for (let i = 0; i < 200; i++) {
    const r = await post({ text: "hello moxie" }, spread, { "CF-Connecting-IP": "198.51.100." + (i % 250) });
    if (r.body.reason === "turnstile_failed") refused += 1;
  }
  eq(refused, 200, "…200 tokenless requests from 200 addresses are all refused");
  eq(gatewayCalls(), 0, "…with zero gateway calls");
  eq(verifyCalls(), 0, "…and zero siteverify calls");
  eq(unitsSpent(), 0, "…AND THE BUDGET IS STILL WHOLE (it used to be 600/600 at this point)");
  eq(refundedUnits(), 600, "…600 units charged and 600 given back");

  // The visitor the attack used to lock out.
  plan = {};
  const visitor = await post({ text: "hello moxie", [ts.TOKEN_FIELD]: TOKEN }, spread,
                             { "CF-Connecting-IP": "203.0.113.77" });
  eq(visitor.status, 200, "…and the next real visitor is SERVED, not `budget_exhausted`");
  eq(visitor.body.reason, null, "…with no reason");
  eq(visitor.body.mode, "live", "…and a LIVE page rather than a scripted one");

  /* ---- THE REFUND IS IDEMPOTENT, and that is not cosmetic ---------------- *
   * A second call would credit away a DIFFERENT request's charge, which is money in the
   * wrong direction. Driven through `admit()` directly because no route can reach a double
   * refund — which is exactly why the guard needs its own test rather than a comment.
   */
  fresh();
  const cfg = envlib.readConfig(ARMED);
  const s1 = await limits.admit({ request: req({}), cfg, route: "chat" });
  eq(unitsSpent(), UNITS_CHAT, "one admission charges its units");
  s1.refundBudget();
  eq(unitsSpent(), 0, "…and one refund gives them back");
  const s2 = await limits.admit({ request: req({}), cfg, route: "chat" });
  eq(unitsSpent(), UNITS_CHAT, "a SECOND admission charges again");
  s1.refundBudget();                                  // the double call
  eq(unitsSpent(), UNITS_CHAT,
     "…and refunding the FIRST slot twice does NOT credit away the second's charge");
  eq(refundedUnits(), UNITS_CHAT, "…the refund counter counted one refund, not two");
  s1.release();
  s2.release();
  eq(limits.__state().inflight.chat || 0, 0, "…and both slots came back");

  /* ---- and a refused ADMISSION still has both methods -------------------- *
   * `refuse()` returns a slot-shaped object; a caller that cannot tell which kind it got
   * must be able to call either method without a `TypeError` on a refusal path. */
  fresh();
  const denied = await limits.admit({
    request: req({}, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }),
    cfg, route: "chat",
  });
  eq(denied.ok, false, "a refused admission is not a slot…");
  eq(typeof denied.release, "function", "…but it still has release()");
  eq(typeof denied.refundBudget, "function", "…and refundBudget()");
  denied.refundBudget();
  denied.release();
  eq(unitsSpent(), 0, "…both of which are no-ops that cannot go negative");
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
