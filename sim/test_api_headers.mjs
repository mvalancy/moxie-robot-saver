/* test_api_headers.mjs — the hardening headers on `/api/*`, over a real socket and in a
 * real browser.
 *
 * Cloudflare Pages does not apply `_headers` to a Function response, so an API reply carries
 * only what `functions/api/_lib/envelope.js` sets. The real handlers run behind a real
 * `node:http` server (the object-level breadth lives in the demo-proxy suite); Chrome then
 * proves the teeth, each with a CONTROL: a navigated `/api/health` document may not `fetch()`
 * (twin route without the CSP must), a CORP-pinned image is refused cross-origin (a bare twin
 * loads), and the page's own same-origin `fetch("/api/health")` still works under the real
 * page CSP. `fetch` is stubbed for the fake gateway host; nothing leaves the machine.
 *
 *   node sim/test_api_headers.mjs
 */
import { join } from "node:path";
import { loadPuppeteer, findChrome, pagesHeaders, makeChecks, finish, launchBrowser, serveStatic,
         repo, web } from "./browser_harness.mjs";
import { KEY } from "./tests/edge/common.mjs";

const LABEL = "/api/* hardening headers";
const { fails, ok, eq, count } = makeChecks();

/* The fake deployment: an unresolvable `.invalid.test` host and a test-shaped key. */
const GW = "https://gw.invalid.test/v1";
const ENV = {
  DEMO_GATEWAY_BASE_URL: GW,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_CHAT_PER_MIN: "2",
};
/** Never allowed to appear in a header value, anywhere (§4.2, C1). */
const FORBIDDEN = [KEY, GW, "gw.invalid.test", "test-brain-model"];

const realFetch = globalThis.fetch;
let upstreamHits = 0;
globalThis.fetch = async (url, opt) => {
  const s = String(url);
  if (!s.includes("invalid.test")) return realFetch(url, opt);   // our own loopback calls
  upstreamHits++;
  return new Response(JSON.stringify({ choices: [{ message: { content: "Hi!" } }] }),
                      { status: 200, headers: { "Content-Type": "application/json" } });
};

const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));

/* The header NAMES are the contract, written out; the VALUES come from `API_SECURITY_HEADERS`
 * (restating a policy value is how a suite passes while the shipped header differs). */
const REQUIRED = Object.freeze([
  "X-Content-Type-Options",
  "Referrer-Policy",
  "Strict-Transport-Security",
  "Content-Security-Policy",
  "Cross-Origin-Resource-Policy",
]);
const SENT = envelope.API_SECURITY_HEADERS || {};
const REJECTED = envelope.REJECTED_SECURITY_HEADERS || {};
ok(!!envelope.API_SECURITY_HEADERS,
   "envelope.js must export API_SECURITY_HEADERS — the one place the /api/* header set lives");
ok(!!envelope.REJECTED_SECURITY_HEADERS,
   "…and REJECTED_SECURITY_HEADERS, so every header NOT sent carries a written reason");
for (const h of REQUIRED) {
  ok(typeof SENT[h] === "string" && SENT[h].length > 0,
     `API_SECURITY_HEADERS must define ${h}`);
}
const health = await import(join(repo, "functions", "api", "health.js"));
const chat = await import(join(repo, "functions", "api", "chat.js"));
const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));

/* A Pages-shaped origin: real Functions on /api/*, the static bundle under the real
 * `_headers` `/*` block everywhere else. */
const PAGE_HEADERS = pagesHeaders();

/** A 1x1 transparent PNG — real image bytes, so the ONLY reason a load can fail is policy. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64");

/** A bare cross-origin page: NO `_headers`, so its own CSP can never be what refuses a load. */
const XORIGIN_HTML = `<!doctype html><meta charset="utf-8"><title>x</title>
<script>
window.probe = (src) => new Promise((res) => {
  const i = new Image();
  i.onload = () => res("loaded");
  i.onerror = () => res("blocked");
  i.src = src;
});
</script>`;

async function pipe(webRes, res, drop = []) {
  const buf = Buffer.from(await webRes.arrayBuffer());
  const h = {};
  for (const [k, v] of webRes.headers) if (!drop.includes(k.toLowerCase())) h[k] = v;
  res.writeHead(webRes.status, h);
  res.end(buf);
}

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;
  // The two probe images: identical bytes, one CORP-pinned and one bare.
  if (p === "/probe/corp.png" || p === "/probe/plain.png") {
    const h = { "Content-Type": "image/png", "Cache-Control": "no-store" };
    if (p === "/probe/corp.png") h["Cross-Origin-Resource-Policy"] = "same-origin";
    res.writeHead(200, h);
    res.end(PNG);
    return true;
  }
  if (p === "/xorigin.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(XORIGIN_HTML);
    return true;
  }
  if (p === "/api/health" || p === "/nocsp/health") {
    const request = new Request(url.href, { method: "GET", headers: req.headers });
    // `/nocsp/health` is the CONTROL for the CSP arm: byte-identical body, no policy.
    await pipe(await health.onRequestGet({ request, env: ENV }), res,
               p === "/nocsp/health" ? ["content-security-policy"] : []);
    return true;
  }
  if (p === "/api/chat" && req.method === "POST") {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const request = new Request(url.href, { method: "POST", headers: req.headers, body: Buffer.concat(chunks) });
    await pipe(await chat.onRequestPost({ request, env: ENV }), res);
    return true;
  }
  return false;
}

const site = await serveStatic(web, { headers: PAGE_HEADERS, handle });
const port = site.port;
const ORIGIN = site.url;

/* 1. THE SET SURVIVES THE WIRE — every status, refusals included (the reply a hostile
 *    caller sees most), read off a socket. */
{
  limits.__reset();
  const post = (body, extra) =>
    realFetch(`${ORIGIN}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: extra && extra.origin ? extra.origin : ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "CF-Connecting-IP": (extra && extra.ip) || "203.0.113.7",
      },
      body: typeof body === "string" ? body : JSON.stringify(body),
    });

  const cases = [];
  cases.push(["GET /api/health (200)", await realFetch(`${ORIGIN}/api/health`)]);
  cases.push(["POST /api/chat (200)", await post({ text: "hi" })]);
  cases.push(["bad_request (400)", await post({ text: "" })]);
  cases.push(["forbidden_origin (403)", await post({ text: "hi" }, { origin: "https://evil.invalid.test" })]);
  // DEMO_CHAT_PER_MIN is 2 and one turn is already spent from this IP, so the third
  // is refused by the window rather than by anything upstream.
  await post({ text: "hi" });
  cases.push(["rate_limited (429)", await post({ text: "hi" })]);

  const seen = new Set();
  for (const [label, res] of cases) {
    seen.add(res.status);
    for (const h of REQUIRED) {
      const got = res.headers.get(h);
      ok(got !== null && got !== "", `${label} — ${h} is MISSING from the served response`);
      if (SENT[h]) eq(got, SENT[h], `${label} — ${h} survived the wire unchanged`);
    }
    for (const h of Object.keys(REJECTED)) {
      eq(res.headers.get(h), null, `${label} — the rejected ${h} is genuinely absent`);
    }
    eq(res.headers.get("Cache-Control"), "no-store", `${label} — still no-store`);
    // §4.2: no header may carry the key, the gateway base or a model id, ever.
    for (const [, v] of res.headers) {
      for (const bad of FORBIDDEN) {
        ok(!String(v).includes(bad), `${label} — a header leaked a forbidden value`);
      }
    }
    await res.arrayBuffer();
  }
  ok(seen.has(200) && seen.has(400) && seen.has(403) && seen.has(429),
     `proved on 200/400/403/429 over the wire, saw ${[...seen].sort().join("/")}`);

  /* HSTS must AGREE with the pages (one origin, one pin), read from the real `_headers`. */
  eq(cases[0][1].headers.get("Strict-Transport-Security"),
     PAGE_HEADERS["Strict-Transport-Security"] || null,
     "the API's HSTS is byte-identical to the pages'");

  /* …and the API CSP is NOT the page CSP: a JSON body loads nothing. */
  ok(cases[0][1].headers.get("Content-Security-Policy") !== PAGE_HEADERS["Content-Security-Policy"],
     "the API CSP is its own lockdown, not a copy of the page policy");
}

/* 2. THE BROWSER HALF — teeth, controls, and the harmlessness claim */
const puppeteer = await loadPuppeteer();
const chrome = findChrome();
if (!puppeteer || !chrome) {
  site.close();
  // A PARTIAL skip: still a failure under CI, where the browser half must run.
  if (process.env.CI) {
    console.error(`❌ ${LABEL}: no Chrome under CI — the socket half ran (${count()} checks) but`);
    console.error(`   the browser half is the one that proves the page can still fetch its own API.`);
    process.exit(1);
  }
  console.log(`⏭  ${LABEL}: no Chrome available — socket half ran (${count()} checks), browser half skipped`);
  process.exit(0);
}

/* A public-looking hostname (a local one makes env.js probe the :8081/:8082 sidecars);
 * `other.test` is the second origin. */
const SITE = `http://moxie.hosted.test:${port}`;
const OTHER = `http://other.test:${port}`;
const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": port, "other.test": port } });

try {
  /* 2a. HARMLESSNESS: under the REAL page CSP, the page's own fetch still works with CORP. */
  {
    const page = await browser.newPage();
    const errs = [];
    page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
    page.on("pageerror", (e) => errs.push(String(e)));
    await page.goto(`${SITE}/index.html`, { waitUntil: "networkidle2", timeout: 30000 });
    const got = await page.evaluate(async () => {
      try {
        const r = await fetch("/api/health");
        return {
          ok: r.ok, status: r.status,
          corp: r.headers.get("cross-origin-resource-policy"),
          csp: r.headers.get("content-security-policy"),
          body: await r.json(),
        };
      } catch (e) { return { error: String(e) }; }
    });
    ok(!got.error, `the page's own same-origin fetch("/api/health") must work — got ${got.error}`);
    eq(got.ok, true, "…with an ok response");
    eq(got.status, 200, "…a 200");
    eq(got.corp, "same-origin", "…carrying CORP, which therefore did not block it");
    ok(/default-src\s+'none'/.test(got.csp || ""),
       "…and the lockdown CSP, which is likewise not in the page's way");
    ok(got.body && typeof got.body.mode === "string",
       "…and a parseable envelope, so nothing stripped the body either");
    const blocked = errs.filter((e) => /Refused to (connect|load)|Cross-Origin-Resource-Policy/i.test(e));
    eq(blocked.length, 0, `no policy refused the page's own call — ${blocked.join(" | ")}`);
    await page.close();
  }

  /* 2b. TEETH: a NAVIGATED `/api/health` document may not fetch (`default-src 'none'`);
   *     the CSP-stripped twin must, or a refusal could be for any reason. */
  {
    const run = async (path) => {
      const page = await browser.newPage();
      await page.goto(`${SITE}${path}`, { waitUntil: "domcontentloaded", timeout: 30000 });
      const r = await page.evaluate(async () => {
        try { const x = await fetch("/api/health"); return { ok: x.ok }; }
        catch (e) { return { error: String(e) }; }
      });
      await page.close();
      return r;
    };
    const locked = await run("/api/health");
    const control = await run("/nocsp/health");
    ok(!!control.ok && !control.error,
       `CONTROL: with no CSP the same fetch from the same document succeeds — got ${JSON.stringify(control)}`);
    ok(!!locked.error,
       `default-src 'none' must refuse a fetch from a navigated /api/health document — got ${JSON.stringify(locked)}`);
  }

  /* 2c. TEETH: CORP refuses a cross-origin embed; the identical bare PNG loads. (A JSON
   *     body cannot show this: Chrome's ORB already blocks it.) */
  {
    const page = await browser.newPage();
    await page.goto(`${OTHER}/xorigin.html`, { waitUntil: "domcontentloaded", timeout: 30000 });
    const plain = await page.evaluate((u) => window.probe(u), `${SITE}/probe/plain.png`);
    const corp = await page.evaluate((u) => window.probe(u), `${SITE}/probe/corp.png`);
    eq(plain, "loaded", "CONTROL: the same image without CORP loads cross-origin");
    eq(corp, "blocked", "…and Cross-Origin-Resource-Policy: same-origin refuses it");
    await page.close();
  }
} finally {
  await browser.close();
  site.close();
}

eq(upstreamHits > 0, true, "the stub answered the live turns — and it is the ONLY thing that did");
finish(LABEL, { fails, count });
