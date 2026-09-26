/* test_demo_proxy — §12: deploy-only failures converted into local ones. Run via the entry file, never alone. */
import {
  FULL, P, call, chat, deep, env0, eq, existsSync,
  fresh, join, ok, readFileSync, repo, sent,
} from "./harness.mjs";

/* =========================================================================== *
 * 12. THE DEPLOY-ONLY FAILURE, CONVERTED INTO A LOCAL ONE
 * =========================================================================== *
 * The Pages build rejects `import … from "./x.json" with { type: "json" }` while node
 * accepts it, so a JSON import under `functions/` was once visible ONLY to a real deploy.
 * Data lives in plain `.js` modules (`_lib/safety.rules.js`); this block fails a JSON
 * import or import attribute anywhere under `functions/` locally, in about a second.
 */
{
  const { readdirSync, statSync } = await import("node:fs");
  const fnDir = join(repo, "functions");

  const walk = (dir) => {
    const out = [];
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) out.push(...walk(full));
      else out.push(full);
    }
    return out;
  };
  const files = walk(fnDir);
  const rel = (f) => f.slice(repo.length + 1);
  ok(files.length > 0, "there are files under functions/ to check");

  const sources = files.filter((f) => f.endsWith(".js") || f.endsWith(".mjs"));
  ok(sources.length >= 8, `functions/ carries the expected modules, found ${sources.length}`);

  for (const f of sources) {
    const src = readFileSync(f, "utf8");
    // Strip block and line comments so this file's OWN explanatory prose — and every
    // comment quoting the offending syntax, including the ones written above — cannot
    // trip the guard. Only real code is scanned.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

    // 1. NO IMPORT ATTRIBUTES. `with { type: ... }` (ES2025) and the older `assert
    //    { type: ... }`. Cloudflare Pages' bundler rejects them; node does not, which is
    //    precisely why this needs asserting here rather than trusting a green suite.
    ok(!/\b(?:with|assert)\s*\{\s*type\s*:/.test(code),
       `${rel(f)} uses an IMPORT ATTRIBUTE — the Cloudflare Pages build rejects these ` +
       `(settled by a real deploy, 2026-09-03). Inline the data as a .js module instead.`);

    // 2. NO .json IMPORTS AT ALL, with or without an attribute — a bare JSON import is a
    //    bundler-specific extension and the next thing someone would reach for.
    const jsonImports = [
      ...code.matchAll(/\bimport\s[^;]*?from\s*["']([^"']+\.json)["']/g),
      ...code.matchAll(/\bimport\s*\(\s*["']([^"']+\.json)["']/g),
      ...code.matchAll(/\brequire\s*\(\s*["']([^"']+\.json)["']/g),
    ].map((m) => m[1]);
    deep(jsonImports, [],
         `${rel(f)} IMPORTS A JSON FILE. A Pages Function cannot rely on that; put the ` +
         `data in a .js module exporting a const (see functions/api/_lib/safety.rules.js).`);
  }

  // 3. …and no .json file under functions/ at all, so there is nothing to import. Keeping
  //    one beside a .js copy is the two-sources-of-truth failure that is worse than the
  //    bug this replaced: a reviewer reads one, the Function enforces the other.
  const jsonFiles = files.filter((f) => f.endsWith(".json")).map(rel);
  deep(jsonFiles, [],
       "there must be no .json file under functions/ — a Function cannot import one, so " +
       "its only possible role is to drift out of sync with the .js module that is real");

  // The rule table really is the one the Function compiles, and it is the ONLY copy.
  const rulesPath = join(repo, "functions", "api", "_lib", "safety.rules.js");
  ok(existsSync(rulesPath), "functions/api/_lib/safety.rules.js exists");
  ok(!existsSync(join(repo, "functions", "api", "_lib", "safety.json")),
     "functions/api/_lib/safety.json is GONE — one source of truth, not two");
  const safetyMod = await import(rulesPath);
  eq(safetyMod.RULES.categories.length, 8, "the table still carries its 8 categories");
  deep(Object.keys(safetyMod.RULES.phrases).sort(), ["generic", "hate", "privacy", "self_harm"],
       "…and its 4 redirect phrase sets");
  deep(safetyMod.RULES.categories.map((c) => c.id),
       ["self_harm", "violence", "sexual", "hate", "personal_info", "dangerous",
        "violence_talk", "profanity"],
       "…in the order that decides which redirect a multi-category utterance gets");

  // 4. §8.1 test 9, on the tree it is cheapest and most important to hold: nothing under
  //    functions/ may carry a key, a deployment hostname or an account id. This tree is
  //    small and entirely ours, so there is no vendor code to produce a false positive.
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    ok(!/\bsk-[A-Za-z0-9_-]{16,}/.test(src), `${rel(f)} contains a key-shaped literal`);
    ok(!/graphlings|mattvalancy|pages\.dev/i.test(src),
       `${rel(f)} names a deployment hostname — both are deployment CONFIG (C3)`);
    ok(!/\b[0-9a-f]{32}\b/.test(src), `${rel(f)} contains a 32-hex account-id-shaped literal`);
  }

  // wrangler.toml is committed and world-readable, so it must never gain a [vars] block.
  const wrangler = readFileSync(join(repo, "wrangler.toml"), "utf8");
  ok(!/^\s*\[vars\]/m.test(wrangler), "wrangler.toml must have no [vars] block — it is world-readable");
  ok(!/\bsk-[A-Za-z0-9_-]{16,}/.test(wrangler), "wrangler.toml carries no key");
}

/* --------------------------------------------------------------------------- *
 * `_headers` IS INERT FOR FUNCTIONS — the code must carry every /api/* header.
 * ===========================================================================
 * Settled on a real preview: Pages applies `sim/web/_headers` to static files but not to
 * Function responses (`/sim.html` carried the `/*` block's referrer-policy, `/api/health`
 * only the headers `envelope.js` sets itself). So every header the `/api/*` block names
 * must ALSO be set in code; the file may document intent but not be the only source.
 */
{
  const headersFile = readFileSync(join(repo, "sim", "web", "_headers"), "utf8");
  const envelopeSrc = readFileSync(
    join(repo, "functions", "api", "_lib", "envelope.js"), "utf8");

  // the /api/* block: its indented "Name: value" lines, up to the next unindented line
  const lines = headersFile.split("\n");
  const start = lines.findIndex((l) => l.trim() === "/api/*");
  ok(start !== -1, "_headers still declares an /api/* block");
  const declared = [];
  for (let i = start + 1; i < lines.length; i++) {
    const l = lines[i];
    if (!l.trim() || l.trim().startsWith("#")) continue;
    if (!/^\s+/.test(l)) break;                       // next path rule
    const m = l.match(/^\s*([A-Za-z-]+)\s*:/);
    if (m) declared.push(m[1]);
  }
  ok(declared.length >= 3,
     `the /api/* block names at least 3 headers, found ${declared.length}`);

  /* THE CHECK IS ON A REAL RESPONSE, NOT ON THE SOURCE TEXT: a regex over the source was
   * satisfied by the header-name KEYS of `REJECTED_SECURITY_HEADERS`, i.e. by a header
   * that is never sent. */
  const sample = env0.respond({ ok: true, mode: "live" });
  const sent = [...sample.headers.keys()].map((h) => h.toLowerCase());
  const missing = declared.filter((h) => !sent.includes(h.toLowerCase()));
  deep(missing, [],
       `_headers does NOT apply to Pages Functions (settled 2026-09-03), so every header ` +
       `in its /api/* block must also be set in functions/api/_lib/envelope.js. Missing ` +
       `from a real response: ${missing.join(", ")}. Add them to API_SECURITY_HEADERS.`);

  // …and the specific one that was actually absent in production-shaped traffic.
  ok(sent.includes("referrer-policy"),
     "envelope.js must set Referrer-Policy itself — the preview proved _headers will not");

  /* ---------------------------------------------------------------------------- *
   * EVERY SECURITY HEADER THE PAGES SHIP IS EITHER SENT HERE OR EXPLAINED AWAY.
   * ---------------------------------------------------------------------------- *
   * Some page headers are pointless on a JSON API, but "pointless" must be machine-held
   * (a reason in `REJECTED_SECURITY_HEADERS`) or it is indistinguishable from "forgotten".
   */
  const pageBlock = {};
  {
    let inGlob = false;
    for (const raw of lines) {
      const l = raw.replace(/\s+$/, "");
      if (!l || l.trimStart().startsWith("#")) continue;
      if (!/^\s/.test(l)) { inGlob = l.trim() === "/*"; continue; }
      if (!inGlob) continue;
      const i = l.indexOf(":");
      if (i > 0) pageBlock[l.slice(0, i).trim()] = l.slice(i + 1).trim();
    }
  }
  const SECURITY = /^(content-security-policy|strict-transport-security|permissions-policy|referrer-policy|x-content-type-options|x-frame-options|cross-origin-)/i;
  const pageSecurity = Object.keys(pageBlock).filter((h) => SECURITY.test(h));
  ok(pageSecurity.length >= 4,
     `the /* block ships at least 4 security headers, found ${pageSecurity.length}`);
  ok(!!env0.API_SECURITY_HEADERS && !!env0.REJECTED_SECURITY_HEADERS,
     "envelope.js must export API_SECURITY_HEADERS and REJECTED_SECURITY_HEADERS — one place " +
     "for the /api/* header set, and one place for the reason each omission is deliberate");
  const rejected = Object.keys(env0.REJECTED_SECURITY_HEADERS || {}).map((h) => h.toLowerCase());
  const unexplained = pageSecurity.filter(
    (h) => !sent.includes(h.toLowerCase()) && !rejected.includes(h.toLowerCase()));
  deep(unexplained, [],
       `every security header the PAGES ship must be either set on an /api/* response or ` +
       `listed in envelope.js's REJECTED_SECURITY_HEADERS with the reason. Neither: ` +
       `${unexplained.join(", ")}`);

  // A rejection must be a real decision, not a placeholder…
  for (const [h, why] of Object.entries(env0.REJECTED_SECURITY_HEADERS || {})) {
    ok(typeof why === "string" && why.length >= 60,
       `REJECTED_SECURITY_HEADERS["${h}"] must carry a real reason, not a stub`);
    // …and it must actually be absent, or the map is documenting a fiction.
    ok(!sent.includes(h.toLowerCase()),
       `${h} is listed as REJECTED but a real response carries it`);
  }

  /* HSTS must be BYTE-IDENTICAL to the pages'. "Both set HSTS" is not the property —
   * one origin, one max-age. A shorter max-age on the API would silently shorten the
   * pin for anyone whose first (or only) touch is a probe or a bookmarked route. */
  if (pageBlock["Strict-Transport-Security"]) {
    eq(sample.headers.get("Strict-Transport-Security"), pageBlock["Strict-Transport-Security"],
       "the API's HSTS must match the pages' exactly — one origin, one policy");
  }

  /* The API CSP is NOT the page CSP, deliberately: `script-src`/`connect-src`/`img-src`
   * govern a document's loads and a JSON body loads nothing. What it must be is a
   * lockdown, and `default-src 'none'` is the whole point of it. */
  const apiCsp = sample.headers.get("Content-Security-Policy") || "";
  ok(/default-src\s+'none'/.test(apiCsp),
     `the /api/* CSP must be a lockdown (default-src 'none') — got ${JSON.stringify(apiCsp)}`);
  ok(/frame-ancestors\s+'none'/.test(apiCsp),
     "…with frame-ancestors 'none', which does NOT fall back to default-src");
  ok(/base-uri\s+'none'/.test(apiCsp),
     "…and base-uri 'none', which does not fall back either");
  ok(apiCsp !== pageBlock["Content-Security-Policy"],
     "the API CSP must not be a copy of the page CSP — a page policy is meaningless on JSON");

  // The values are constants, never anything derived from a request (§4.2, C1).
  const envelopeCode = envelopeSrc.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  ok(!/headers\.set\([^)]*request\.headers/.test(envelopeCode),
     "no request header may ever be echoed back into a response header");
}

/* --------------------------------------------------------------------------- *
 * THE HARDENING SET RIDES A REFUSAL, NOT JUST A SUCCESS
 * ===========================================================================
 * A refusal is what a hostile caller sees most. Checked on 200, 403, 429, 503 and 400,
 * through the REAL route handlers.
 */
{
  fresh();
  const cases = [];
  cases.push(["200 success", await call(chat, "/api/chat", { text: "hi" })]);
  cases.push(["400 bad_request", await call(chat, "/api/chat", { text: "" })]);
  cases.push(["403 forbidden_origin",
              await call(chat, "/api/chat", { text: "hi" }, { Origin: "https://evil.invalid.test" })]);
  fresh();
  P.plan = { chat: { status: 500, body: "boom" } };
  cases.push(["503 upstream_down", await call(chat, "/api/chat", { text: "hi" })]);
  fresh();
  {
    const cfgEnv = { ...FULL, DEMO_CHAT_PER_MIN: "1" };
    await call(chat, "/api/chat", { text: "hi" }, null, cfgEnv);
    cases.push(["429 rate_limited", await call(chat, "/api/chat", { text: "hi" }, null, cfgEnv)]);
  }
  // /api/health too: it is the only GET, the only always-200 route, and the one the
  // page polls every 30 s — so it is the response most often in a proxy's hands.
  const healthMod = await import(join(repo, "functions", "api", "health.js"));
  cases.push(["health 200", { res: healthMod.onRequestGet({ env: {} }), body: null }]);

  /* The names are written out rather than read back from the module: the NAMES are the
   * contract, so a build that stopped exporting the set must fail as a named assertion
   * here rather than crash. The VALUES still come from the module — restating a policy
   * value in a test is how a suite passes while the shipped header says something else. */
  const REQUIRED = ["X-Content-Type-Options", "Referrer-Policy", "Strict-Transport-Security",
                    "Content-Security-Policy", "Cross-Origin-Resource-Policy"];
  const SENT = env0.API_SECURITY_HEADERS || {};
  const seen = new Set();
  for (const [label, { res }] of cases) {
    seen.add(res.status);
    for (const h of REQUIRED) {
      const got = res.headers.get(h);
      ok(got !== null && got !== "", `${label} is MISSING ${h}`);
      if (SENT[h]) eq(got, SENT[h], `${label} carries ${h} unchanged`);
    }
    for (const h of Object.keys(env0.REJECTED_SECURITY_HEADERS || {})) {
      eq(res.headers.get(h), null, `${label} does NOT carry the rejected ${h}`);
    }
    eq(res.headers.get("Cache-Control"), "no-store", `${label} is still no-store`);
  }
  ok(seen.has(200) && seen.has(400) && seen.has(403) && seen.has(429) && seen.has(503),
     `the hardening set was proved on 200/400/403/429/503, saw ${[...seen].sort().join("/")}`);

  /* A caller may not weaken the set through the `opts.headers` hatch. Nothing passes it
   * today; the hatch is what makes the guarantee worth asserting rather than assuming. */
  const forced = env0.respond(
    { ok: true },
    { headers: { "Content-Security-Policy": "default-src *", "Cross-Origin-Resource-Policy": "cross-origin" } });
  eq(forced.headers.get("Content-Security-Policy"), SENT["Content-Security-Policy"] || null,
     "opts.headers cannot weaken the API CSP");
  eq(forced.headers.get("Cross-Origin-Resource-Policy"), "same-origin",
     "opts.headers cannot weaken CORP");
}
