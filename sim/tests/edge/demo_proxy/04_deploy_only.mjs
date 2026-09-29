/* test_demo_proxy §12: deploy-only failures converted into local ones, and the /api/*
 * hardening headers on every response shape. Run via the entry file. */
import {
  FULL, P, call, chat, deep, eq, env0, fresh, join, ok, readFileSync, repo,
} from "./harness.mjs";

/* 12a. The Pages build rejects JSON imports and import attributes that node accepts, so one
 * was once visible ONLY to a real deploy. Data lives in `.js` modules; fail it here instead.
 * Also §8.1 test 9: nothing under functions/ carries a key, a deployment host or an account id. */
{
  const { readdirSync, statSync } = await import("node:fs");
  const walk = (dir) => readdirSync(dir).flatMap((n) => statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]);
  const files = walk(join(repo, "functions"));
  const rel = (f) => f.slice(repo.length + 1);
  ok(files.filter((f) => /\.m?js$/.test(f)).length >= 8, "functions/ carries the expected modules");
  deep(files.filter((f) => f.endsWith(".json")).map(rel), [],
       "no .json under functions/ — a Function cannot import one, so it could only drift from the .js copy");
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    ok(!/\.m?js$/.test(f) || !/\b(?:with|assert)\s*\{\s*type\s*:/.test(code) &&
       !/\bimport\s[^;]*?from\s*["'][^"']+\.json["']|\bimport\s*\(\s*["'][^"']+\.json["']|\brequire\s*\(\s*["'][^"']+\.json["']/.test(code),
       `${rel(f)} imports JSON or uses an IMPORT ATTRIBUTE — the Pages build rejects these; inline a .js module`);
    ok(!/\bsk-[A-Za-z0-9_-]{16,}/.test(src) && !/graphlings|mattvalancy|pages\.dev/i.test(src) && !/\b[0-9a-f]{32}\b/.test(src),
       `${rel(f)} carries a key-shaped literal, a deployment hostname or a 32-hex account id (C3)`);
  }
  const wrangler = readFileSync(join(repo, "wrangler.toml"), "utf8");
  ok(!/^\s*\[vars\]/m.test(wrangler) && !/\bsk-[A-Za-z0-9_-]{16,}/.test(wrangler),
     "wrangler.toml is world-readable: no [vars] block, no key");
}

/* 12b. `_headers` IS INERT FOR FUNCTIONS (settled on a real preview), so every /api/* header
 * it names must be set by envelope.js — checked on a REAL response, not the source (a source
 * regex was once satisfied by the KEYS of REJECTED_SECURITY_HEADERS, i.e. a header never sent).
 * Every page security header is either sent here or explained away in REJECTED_SECURITY_HEADERS. */
{
  const lines = readFileSync(join(repo, "sim", "web", "_headers"), "utf8").split("\n");
  const block = (path) => {
    const out = {};
    let inside = false;
    for (const raw of lines) {
      const l = raw.replace(/\s+$/, "");
      if (!l || l.trimStart().startsWith("#")) continue;
      if (!/^\s/.test(l)) { inside = l.trim() === path; continue; }
      const i = l.indexOf(":");
      if (inside && i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim();
    }
    return out;
  };
  const declared = Object.keys(block("/api/*"));
  const pageBlock = block("/*");
  ok(declared.length >= 3, `_headers' /api/* block names at least 3 headers, found ${declared.length}`);

  const sample = env0.respond({ ok: true, mode: "live" });
  const sent = [...sample.headers.keys()];
  deep(declared.filter((h) => !sent.includes(h.toLowerCase())), [],
       "every header in _headers' /api/* block is also set by envelope.js (Pages ignores _headers for Functions)");

  const rejected = Object.keys(env0.REJECTED_SECURITY_HEADERS).map((h) => h.toLowerCase());
  const pageSecurity = Object.keys(pageBlock).filter((h) =>
    /^(content-security-policy|strict-transport-security|permissions-policy|referrer-policy|x-content-type-options|x-frame-options|cross-origin-)/i.test(h));
  ok(pageSecurity.length >= 4, `the /* block ships at least 4 security headers, found ${pageSecurity.length}`);
  deep(pageSecurity.filter((h) => !sent.includes(h.toLowerCase()) && !rejected.includes(h.toLowerCase())), [],
       "every page security header is set on /api/* or listed in REJECTED_SECURITY_HEADERS with its reason");
  for (const [h, why] of Object.entries(env0.REJECTED_SECURITY_HEADERS)) {
    ok(why.length >= 60 && !sent.includes(h.toLowerCase()), `REJECTED ${h} has a real reason and is really absent`);
  }
  // One origin, one HSTS policy: a shorter API max-age would shorten the pin for a probe-first visitor.
  if (pageBlock["Strict-Transport-Security"]) {
    eq(sample.headers.get("Strict-Transport-Security"), pageBlock["Strict-Transport-Security"],
       "the API's HSTS must match the pages' exactly");
  }
  // The API CSP is a lockdown, not a copy of the page's (a JSON body loads nothing).
  const apiCsp = sample.headers.get("Content-Security-Policy") || "";
  ok(["default-src", "frame-ancestors", "base-uri"].every((d) => new RegExp(d + "\\s+'none'").test(apiCsp)),
     `the /api/* CSP is default-src/frame-ancestors/base-uri 'none' (the latter two do not fall back) — got ${apiCsp}`);
  ok(apiCsp !== pageBlock["Content-Security-Policy"], "the API CSP is not a copy of the page CSP");
}

/* 12c. The hardening set rides a REFUSAL too — what a hostile caller sees most — through the
 * real handlers on 200/400/403/429/503 and /api/health. The NAMES are written out (they are the
 * contract); the VALUES come from the module. */
{
  fresh();
  const cases = [
    ["200 success", await call(chat, "/api/chat", { text: "hi" })],
    ["400 bad_request", await call(chat, "/api/chat", { text: "" })],
    ["403 forbidden_origin", await call(chat, "/api/chat", { text: "hi" }, { Origin: "https://evil.invalid.test" })],
  ];
  fresh();
  P.plan = { chat: { status: 500, body: "boom" } };
  cases.push(["503 upstream_down", await call(chat, "/api/chat", { text: "hi" })]);
  fresh();
  const one = { ...FULL, DEMO_CHAT_PER_MIN: "1" };
  await call(chat, "/api/chat", { text: "hi" }, null, one);
  cases.push(["429 rate_limited", await call(chat, "/api/chat", { text: "hi" }, null, one)]);
  const health = await import(join(repo, "functions", "api", "health.js"));
  cases.push(["health 200", { res: health.onRequestGet({ env: {} }) }]);

  const SENT = env0.API_SECURITY_HEADERS;
  for (const [label, { res }] of cases) {
    for (const h of ["X-Content-Type-Options", "Referrer-Policy", "Strict-Transport-Security",
                     "Content-Security-Policy", "Cross-Origin-Resource-Policy"]) {
      ok(res.headers.get(h) && res.headers.get(h) === SENT[h], `${label} carries ${h} unchanged`);
    }
    ok(Object.keys(env0.REJECTED_SECURITY_HEADERS).every((h) => res.headers.get(h) === null), `${label} carries no rejected header`);
    eq(res.headers.get("Cache-Control"), "no-store", `${label} is no-store`);
  }
  deep(cases.map(([, c]) => c.res.status), [200, 400, 403, 503, 429, 200], "the set was proved on every status");

  // A caller may not weaken the set through the `opts.headers` hatch.
  const forced = env0.respond({ ok: true },
    { headers: { "Content-Security-Policy": "default-src *", "Cross-Origin-Resource-Policy": "cross-origin" } });
  deep([forced.headers.get("Content-Security-Policy"), forced.headers.get("Cross-Origin-Resource-Policy")],
       [SENT["Content-Security-Policy"], "same-origin"], "opts.headers cannot weaken the API CSP or CORP");
}
