/* test_hub_weight.mjs — what a first visit to the hub costs, measured in a real browser.
 *
 * The defect (production, 2026-10-08): the hub's hero was a 1,083,154-byte PNG, 81 % of the
 * 1.33 MB a phone pulled on its first visit, and nothing asked for it early. The hero is now
 * a <picture> (AVIF, then WebP, then that PNG) whose AVIF is preloaded from <head>.
 *
 * The hub is loaded cold at the two viewports the site is checked at, from a loopback server
 * that gzips text the way Cloudflare Pages does (served raw, three.js alone would count
 * 670 KB instead of the ~167 KB it costs on the wire), and once the load has settled the
 * suite asserts what was RECORDED:
 *   1. every byte of the first load, headers included, fits BUDGET;
 *   2. the largest contentful paint is the hero, in a format lighter than the PNG, and the
 *      request that fetched it was the preload (not the parser finding the <img> late);
 *   3. one copy of the hero crosses the wire: the PNG is a fallback, never a second download;
 *   4. a browser without AVIF, which skips the AVIF-typed preload and <source>, paints the
 *      WebP, one copy, and still fits BUDGET (so the WebP cannot quietly grow past it). It
 *      skips the preload only because of its `type`: untyped, it would fetch the AVIF it
 *      cannot show and then the WebP, and 4 fails on both counts.
 * TEETH: the same load with the old hero rebuilt from the shipped markup (no preload, no
 * <source>) must fail 1 and 2, so neither can pass by measuring nothing. 3 has no tooth there
 * (the old hub fetched its one PNG once); it counts the resource entries the teeth read for 2.
 *
 *   node sim/test_hub_weight.mjs
 */
import http from "node:http";
import { readFileSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";
import { gzipSync } from "node:zlib";
import { requireBrowser, launchBrowser, makeChecks, finish, web } from "./browser_harness.mjs";

const LABEL = "hub weight test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, count } = makeChecks();

/* The plan's target is 400 KB for the whole first load. Production adds what no local server
 * can: the analytics beacon Pages injects into every HTML response (10,438 B) and its report
 * (458 B), both measured on the live hub. The rest of the budget is the page's own. */
const TARGET = 400_000;
const BEACON = 10_896;
const BUDGET = TARGET - BEACON;
const HERO = /\/img\/hero-moxie-cute\.(png|webp|avif)$/;

/* ---- a loopback "Pages": gzip for text, bytes as-is for fonts and images ---------------- */
const TEXT = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml" };
const BINARY = { ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp",
  ".avif": "image/avif", ".jpg": "image/jpeg" };

const SHIPPED = readFileSync(join(web, "index.html"), "utf8");
/* The old hub, derived from the shipped file rather than kept as a copy: drop the preload
 * and every <source>, which leaves the <img> and its PNG exactly as they were. */
const PRELOAD = /[ \t]*<link rel="preload" as="image"[^>]*>\n/g;
const AVIF_PRELOAD = /[ \t]*<link rel="preload" as="image"[^>]*\btype="image\/avif"[^>]*>\n/g;
const SOURCE = /[ \t]*<source [^>]*>\n/g;
const AVIF_SOURCE = /[ \t]*<source type="image\/avif"[^>]*>\n/g;
const hasShape = (SHIPPED.match(PRELOAD) || []).length === 1 && (SHIPPED.match(SOURCE) || []).length >= 1 &&
                 (SHIPPED.match(AVIF_SOURCE) || []).length === 1;
ok(hasShape, "sim/web/index.html has no `<link rel=\"preload\" as=\"image\">` line or no " +
             "`<source type=\"image/avif\">` line for the hero — the preload or the light formats are " +
             "gone, or the markup was reshaped and this suite's teeth need updating");
const OLD_HUB = SHIPPED.replace(PRELOAD, "").replace(SOURCE, "");
/* What a browser without AVIF is served, in effect: it skips a preload typed `image/avif` (one
 * with no type it fetches, format unseen) and the AVIF <source>, so the <picture> hands it the
 * WebP. */
const NO_AVIF_HUB = SHIPPED.replace(AVIF_PRELOAD, "").replace(AVIF_SOURCE, "");
const VARIANTS = { old: OLD_HUB, noavif: NO_AVIF_HUB };

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://hub.invalid");
  let p = decodeURIComponent(url.pathname);
  if (p.endsWith("/")) p += "index.html";
  if (!extname(p)) p += ".html";
  const file = join(web, normalize(p).replace(/^(\.\.[/\\])+/, ""));
  let body;
  try {
    if (!statSync(file).isFile()) throw new Error("dir");
    const variant = p === "/index.html" && Object.keys(VARIANTS).find((k) => url.searchParams.has(k));
    body = variant ? Buffer.from(VARIANTS[variant]) : readFileSync(file);
  } catch { res.writeHead(404); return res.end(); }
  const ext = extname(file);
  const h = { "Content-Type": TEXT[ext] || BINARY[ext] || "application/octet-stream" };
  if (TEXT[ext] && /\bgzip\b/.test(req.headers["accept-encoding"] || "")) {
    body = gzipSync(body);
    h["Content-Encoding"] = "gzip";
  }
  h["Content-Length"] = body.length;
  res.writeHead(200, h);
  res.end(body);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

const VIEWPORTS = {
  phone: { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 },
  desktop: { width: 1440, height: 900, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
};

/** One cold load (a fresh browser context, so nothing is cached): the bytes every response
 *  cost on the wire, the final LCP entry, and the resource-timing entries for the hero. */
async function firstLoad(browser, viewport, path) {
  const ctx = await browser.createBrowserContext();
  try {
    const page = await ctx.newPage();
    await page.setViewport(viewport);
    const cdp = await page.createCDPSession();
    await cdp.send("Network.enable");
    const urls = new Map(), wire = new Map();
    cdp.on("Network.requestWillBeSent", (e) => urls.set(e.requestId, e.request.url));
    cdp.on("Network.loadingFinished", (e) => {
      const u = urls.get(e.requestId) || e.requestId;
      wire.set(u, (wire.get(u) || 0) + e.encodedDataLength);
    });
    await page.goto(base + path, { waitUntil: "load", timeout: 30000 });
    await page.waitForNetworkIdle({ idleTime: 600, timeout: 15000 }).catch(() => {});
    const seen = await page.evaluate(() => new Promise((resolve) => {
      let last = null;
      new PerformanceObserver((l) => { const es = l.getEntries(); last = es[es.length - 1]; })
        .observe({ type: "largest-contentful-paint", buffered: true });
      requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => resolve({
        lcp: last && {
          url: last.url, size: last.size,
          tag: last.element ? last.element.tagName : null,
          inStage: !!(last.element && last.element.closest("#stage")),
        },
        hero: performance.getEntriesByType("resource").filter((e) => /\/img\/hero-moxie-cute\./.test(e.name))
          .map((e) => ({ url: e.name, initiator: e.initiatorType, bytes: e.encodedBodySize })),
      }), 50)));
    }));
    let total = 0;
    for (const n of wire.values()) total += n;
    return { total, wire, ...seen };
  } finally {
    await ctx.close();
  }
}

const kb = (n) => (n / 1000).toFixed(1) + " KB";
const breakdown = (wire) => [...wire.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
  .map(([u, n]) => `${u.replace(base, "")} ${kb(n)}`).join(", ");

const browser = await launchBrowser(puppeteer, chrome);
try {
  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    const r = await firstLoad(browser, viewport, "/");
    console.log(`   ${name}: ${kb(r.total)} over ${r.wire.size} responses; LCP ${r.lcp && r.lcp.tag} ` +
                `${r.lcp && r.lcp.url.replace(base, "")}; hero fetched by ${r.hero.map((h) => h.initiator).join("+") || "nothing"}`);
    ok(r.total <= BUDGET,
       `${name}: the hub's first load is ${kb(r.total)}, over its ${kb(BUDGET)} budget ` +
       `(${kb(TARGET)} less the ${kb(BEACON)} beacon production adds) — biggest: ${breakdown(r.wire)}`);
    ok(r.lcp && r.lcp.tag === "IMG" && r.lcp.inStage,
       `${name}: the largest contentful paint should be the hero picture (got ${JSON.stringify(r.lcp)})`);
    const lcpUrl = (r.lcp && r.lcp.url) || "";
    ok(HERO.test(lcpUrl) && !/\.png$/.test(lcpUrl),
       `${name}: the hero should paint from its AVIF or WebP, not the PNG fallback (LCP url ${lcpUrl || "none"})`);
    const lcpFetch = r.hero.filter((h) => h.url === lcpUrl);
    ok(lcpFetch.length === 1 && lcpFetch[0].initiator === "link",
       `${name}: the LCP image should be fetched once, by the <link rel=preload> in <head> ` +
       `(got ${JSON.stringify(lcpFetch)})`);
    ok(r.hero.length === 1,
       `${name}: exactly one copy of the hero should cross the wire (got ${JSON.stringify(r.hero)})`);
  }

  /* 4. A browser without AVIF gets the WebP, and it has to fit the same budget. */
  if (hasShape) {
    const w = await firstLoad(browser, VIEWPORTS.phone, "/?noavif");
    const wUrl = (w.lcp && w.lcp.url) || "";
    console.log(`   a browser without AVIF (phone): ${kb(w.total)}; LCP ${wUrl.replace(base, "")}`);
    ok(w.total <= BUDGET,
       `a browser without AVIF: the first load is ${kb(w.total)}, over the ${kb(BUDGET)} budget — the WebP ` +
       `fallback is too heavy (biggest: ${breakdown(w.wire)})`);
    ok(/\.webp$/.test(wUrl) && w.hero.length === 1,
       `a browser without AVIF should paint the WebP, one copy (LCP url ${wUrl || "none"}, hero ${JSON.stringify(w.hero)})`);
  }

  /* TEETH: the old hero, rebuilt from the shipped markup, must redden clauses 1-3. */
  if (hasShape) {
    const t = await firstLoad(browser, VIEWPORTS.phone, "/?old");
    const tUrl = (t.lcp && t.lcp.url) || "";
    const tFetch = t.hero.filter((h) => h.url === tUrl);
    console.log(`   teeth (old hero, phone): ${kb(t.total)}; LCP ${tUrl.replace(base, "")}; ` +
                `fetched by ${tFetch.map((h) => h.initiator).join("+") || "nothing"}`);
    ok(t.total > BUDGET, `teeth: the old PNG hero should blow the budget (${kb(t.total)} <= ${kb(BUDGET)}) — ` +
                         "the byte count is not measuring the hero");
    ok(/\.png$/.test(tUrl), `teeth: the old hub should paint the PNG (LCP url ${tUrl || "none"})`);
    ok(tFetch.length === 1 && tFetch[0].initiator !== "link",
       `teeth: with no preload the hero must NOT be reported as preload-fetched (got ${JSON.stringify(tFetch)}) — ` +
       "the preload check cannot tell a preload from the parser");
  }
} finally {
  await browser.close();
  server.close();
}

finish(LABEL, { fails, count });
