/* test_api_headers.mjs — the hardening headers on `/api/*`, over a real socket, and in a
 * real browser.
 *
 * THE HOLE THIS FILE CLOSES. `sim/test_csp.mjs` (PR #112) proved the STATIC pages' header
 * set by serving it and loading every page under it. The `/api/*` routes had no equivalent
 * — and they are the half where `sim/web/_headers` is INERT. Settled by a preview deploy on
 * 2026-09-03 (§10 assumption 27, first learned the hard way in PR #72): Cloudflare Pages
 * does not apply `_headers` to a Function response at all, so the only headers an API reply
 * carries are the ones `functions/api/_lib/envelope.js` sets in code. A measurement of the
 * live deployment that day found the routes carrying `nosniff`, `no-store` and
 * `Referrer-Policy` and NOTHING else — no HSTS, no CSP, no `Cross-Origin-*` — while the
 * pages had just gained a full set.
 *
 * WHY OVER A SOCKET AND NOT ON THE OBJECT. `sim/test_demo_proxy.mjs` asserts the header set
 * on the `Response` object `respond()` returns, on every status the route table can produce.
 * That is the right place for breadth. It cannot see anything that happens between the
 * object and the wire, and it cannot see what a browser DOES with the result. So this file
 * runs the REAL route handlers behind a real `node:http` server and fetches them like a
 * client, then hands the same origin to Chrome.
 *
 * IT HAS TEETH, in two independent places, each with a CONTROL so a green run cannot be
 * confused with "no header arrived":
 *
 *   · The API CSP: the browser NAVIGATES to `/api/health`, which is exactly the "a browser
 *     ends up treating the JSON body as a document" case the lockdown exists for, and a
 *     `fetch()` from inside that document must be REFUSED by `default-src 'none'`. The
 *     control is a twin route serving the identical body with the CSP stripped, where the
 *     same fetch must SUCCEED.
 *   · CORP: a cross-origin page embeds two identical PNGs, one with
 *     `Cross-Origin-Resource-Policy: same-origin` and one without. The bare one must load
 *     and the CORP one must fail. That demonstrates the mechanism has teeth in this browser
 *     on this origin pair; the API responses are then asserted to carry the same value.
 *     (A JSON body cannot be used for that demonstration: Chrome's Opaque Response Blocking
 *     already refuses a cross-origin no-cors JSON load on its own, so the two arms would be
 *     indistinguishable. CORP is the STANDARDISED, explicit form of the same refusal, which
 *     is why it is worth sending even where ORB happens to cover it.)
 *
 * AND THE HARMLESSNESS CLAIM IS TESTED, NOT ASSERTED. `Cross-Origin-Resource-Policy:
 * same-origin` is only worth adding if it cannot break the page's own calls, so the real
 * `index.html` is loaded from this origin under the real `_headers` page CSP and an in-page
 * `fetch("/api/health")` must succeed and parse. A header that breaks the product is worse
 * than a missing one.
 *
 * ZERO NETWORK. `globalThis.fetch` is stubbed for the fake gateway host and delegates
 * everything else (this suite's own loopback calls) to the real implementation. No
 * Cloudflare account, no gateway key, nothing leaves the machine.
 *
 * Skips cleanly (exit 0) for the browser half when no Chrome is available, like every other
 * browser suite here; the socket half always runs.
 *
 *   node sim/test_api_headers.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, normalize, extname } from "node:path";
import http from "node:http";
import net from "node:net";
import { loadPuppeteer, findChrome, pagesHeaders, makeChecks, finish } from "./browser_harness.mjs";

const LABEL = "/api/* hardening headers";
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const web = join(repo, "sim", "web");

const { fails, ok, eq, count } = makeChecks();

/* --------------------------------------------------------------------------- *
 * The fake deployment — the same shape sim/test_demo_proxy.mjs uses, and for the
 * same reasons: `.invalid.test` is unresolvable (RFC 6761) so a bug that really
 * fired a request could reach nothing, and the key is shaped so the repo's own
 * pre-commit secret grep cannot mistake it for a real one.
 * --------------------------------------------------------------------------- */
const GW = "https://gw.invalid.test/v1";
const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
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

/* The header NAMES this slice contracts for, written out rather than read back from the
 * module. Values still come from `API_SECURITY_HEADERS` — restating a policy value in a
 * test is how a suite ends up passing while the shipped header says something else — but
 * the NAMES are the contract itself, so a build that simply stopped exporting the set has
 * to fail as a named assertion here rather than crash on an undefined. */
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

/* --------------------------------------------------------------------------- *
 * A Pages-shaped server: real Functions on /api/*, the real static bundle with the
 * real `_headers` `/*` block everywhere else — i.e. the two halves of the origin,
 * served the way the deployment serves them.
 * --------------------------------------------------------------------------- */
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
  ".svg": "image/svg+xml", ".woff2": "font/woff2", ".glb": "model/gltf-binary",
  ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ico": "image/x-icon", ".txt": "text/plain",
  ".webmanifest": "application/manifest+json", ".map": "application/json",
};
const PAGE_HEADERS = pagesHeaders();

/** A 1x1 transparent PNG. Real image bytes, so the ONLY reason a load can fail is policy. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64");

/** A bare cross-origin page: NO `_headers`, so its own CSP can never be what refuses a
 *  load and the CORP arm is measuring CORP. */
const XORIGIN_HTML = `<!doctype html><meta charset="utf-8"><title>x</title>
<script>
window.probe = (src) => new Promise((res) => {
  const i = new Image();
  i.onload = () => res("loaded");
  i.onerror = () => res("blocked");
  i.src = src;
});
</script>`;

async function pipe(webRes, res, { drop } = {}) {
  const buf = Buffer.from(await webRes.arrayBuffer());
  const h = {};
  for (const [k, v] of webRes.headers) {
    if (drop && drop.includes(k.toLowerCase())) continue;
    h[k] = v;
  }
  res.writeHead(webRes.status, h);
  res.end(buf);
}

function freePort() {
  return new Promise((r) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); });
  });
}

const port = await freePort();
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;

  // The two probe images: identical bytes, one CORP-pinned and one bare. The control.
  if (p === "/probe/corp.png" || p === "/probe/plain.png") {
    const h = { "Content-Type": "image/png", "Cache-Control": "no-store" };
    if (p === "/probe/corp.png") h["Cross-Origin-Resource-Policy"] = "same-origin";
    res.writeHead(200, h);
    return res.end(PNG);
  }
  if (p === "/xorigin.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(XORIGIN_HTML);
  }

  if (p === "/api/health" || p === "/nocsp/health") {
    const request = new Request(url.href, { method: "GET", headers: req.headers });
    const out = health.onRequestGet({ request, env: ENV });
    // `/nocsp/health` is the CONTROL for the CSP arm: byte-identical body, no policy.
    return pipe(out, res, { drop: p === "/nocsp/health" ? ["content-security-policy"] : [] });
  }
  if (p === "/api/chat" && req.method === "POST") {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const request = new Request(url.href, {
      method: "POST", headers: req.headers, body: Buffer.concat(chunks),
    });
    return pipe(await chat.onRequestPost({ request, env: ENV }), res);
  }

  // Everything else: the static bundle, under the real page `_headers` `/*` block.
  let f = decodeURIComponent(p);
  if (f.endsWith("/")) f += "index.html";
  if (!extname(f)) f += ".html";
  const file = join(web, normalize(f).replace(/^(\.\.[/\\])+/, ""));
  let body, code = 200;
  try { body = readFileSync(file); } catch { code = 404; body = Buffer.from("not found"); }
  res.writeHead(code, {
    "Content-Type": MIME[extname(file)] || "application/octet-stream", ...PAGE_HEADERS,
  });
  res.end(body);
});
await new Promise((r) => server.listen(port, "127.0.0.1", r));
const ORIGIN = `http://127.0.0.1:${port}`;

/* =========================================================================== *
 * 1. THE SET SURVIVES THE WIRE — every status, fetched like a client
 * =========================================================================== *
 * Not the object `respond()` returned: the bytes a client read off a socket. The
 * refusals are in here on purpose. A refusal is the reply a hostile caller sees
 * most often, and a header set that only applies when things go well is not one.
 */
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

  /* HSTS is the one value that must AGREE with the pages: one origin, one policy. A
   * shorter max-age on the API would quietly shorten the pin for a visitor whose only
   * touch is a bookmarked probe. Read from the real `_headers`, never restated here. */
  eq(cases[0][1].headers.get("Strict-Transport-Security"),
     PAGE_HEADERS["Strict-Transport-Security"] || null,
     "the API's HSTS is byte-identical to the pages'");

  /* …and the API CSP must NOT be the page CSP. `script-src`/`connect-src`/`img-src`
   * describe what a DOCUMENT may load; a JSON body loads nothing, so copying the page
   * policy here would be decoration. */
  ok(cases[0][1].headers.get("Content-Security-Policy") !== PAGE_HEADERS["Content-Security-Policy"],
     "the API CSP is its own lockdown, not a copy of the page policy");
}

/* =========================================================================== *
 * 2. THE BROWSER HALF — teeth, controls, and the harmlessness claim
 * =========================================================================== */
const puppeteer = await loadPuppeteer();
const chrome = findChrome();
if (!puppeteer || !chrome) {
  server.close();
  // The socket half genuinely ran, so this is a PARTIAL skip — but under CI it is still a
  // failure. The browser half is the part that proves the page's own `fetch("/api/health")`
  // survives CORP, which no socket test can show. A run that quietly drops it while the
  // badge stays green is the hole this repo already fell into once (see browser_harness.mjs).
  if (process.env.CI) {
    console.error(`❌ ${LABEL}: no Chrome under CI — the socket half ran (${count()} checks) but`);
    console.error(`   the browser half is the one that proves the page can still fetch its own API.`);
    process.exit(1);
  }
  console.log(`⏭  ${LABEL}: no Chrome available — socket half ran (${count()} checks), browser half skipped`);
  process.exit(0);
}

/* A non-local hostname mapped to the loopback server, for the same reason test_csp.mjs
 * does it: `_headers` is a Pages artifact and Pages serves a public hostname, and on a
 * LOCAL host `env.js` additionally probes the :8081/:8082 sidecars, whose refusals would
 * be folded into every console assertion below. `other.test` is the second origin. */
const SITE = `http://moxie.hosted.test:${port}`;
const OTHER = `http://other.test:${port}`;

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${port},` +
         `MAP other.test 127.0.0.1:${port}`],
});

try {
  /* ---- 2a. HARMLESSNESS: the page's own fetch still works ------------------ *
   * The whole case against `Cross-Origin-Resource-Policy: same-origin` would be that it
   * breaks the site. It cannot — CORP is consulted only for a CROSS-origin response, and
   * every call this site makes to these routes is same-origin by construction (the origin
   * pin would already have refused anything else) — but "cannot" is worth a socket and a
   * browser rather than a paragraph. Loaded under the REAL page CSP, so `connect-src
   * 'self'` is in force at the same time. */
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

  /* ---- 2b. TEETH: the API CSP, on the case it exists for ------------------- *
   * A direct NAVIGATION to `/api/health` is the "browser treats the JSON body as a
   * document" class the lockdown is for. In that document `default-src 'none'` must
   * refuse a `fetch()` (connect-src falls back to default-src). `/nocsp/health` serves the
   * identical body with the policy stripped and must NOT be refused — without that arm a
   * green result would be equally consistent with "the fetch failed for another reason". */
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

  /* ---- 2c. TEETH: CORP blocks a cross-origin embed ------------------------- *
   * Two identical PNGs from `moxie.hosted.test`, embedded by a page on `other.test`: the
   * bare one must load, the `Cross-Origin-Resource-Policy: same-origin` one must not.
   * Real image bytes, so policy is the only thing that can decide the outcome. This is the
   * mechanism; block 1 already proved the /api/* replies carry the same value. */
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
  server.close();
}

eq(upstreamHits > 0, true, "the stub answered the live turns — and it is the ONLY thing that did");
finish(LABEL, { fails, count });
