/* browser_harness.mjs — shared plumbing for the headless-browser suites.
 *
 * NOT a test (`sim/tests/test_ci_test_coverage.py` enumerates only `test_*.mjs`). One copy
 * of puppeteer/Chrome discovery, the static server, and the console-error "eyes".
 *
 * Its own static server rather than `sim/serve.py`: `serveWeb({ headers: true })` sends the
 * REAL `sim/web/_headers` `/*` block, so suites test the CSP we ship (only Cloudflare Pages
 * sends it otherwise). With `headers: false` it is a plain static server.
 */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join, normalize, extname } from "node:path";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import http from "node:http";
import net from "node:net";

export const here = dirname(fileURLToPath(import.meta.url));
export const repo = join(here, "..");
export const web = join(repo, "sim", "web");

/* ---- puppeteer + chrome discovery (the shape test_env_hosted.mjs established) ---- */
export async function loadPuppeteer() {
  try { return (await import("puppeteer")).default; } catch {}
  const bases = [];
  if (process.env.PUPPETEER_PATH) bases.push(process.env.PUPPETEER_PATH);
  try {
    const code = join(homedir(), "Code");
    for (const d of readdirSync(code))
      if (existsSync(join(code, d, "node_modules", "puppeteer", "package.json"))) bases.push(join(code, d));
  } catch {}
  for (const base of bases) { try { return createRequire(join(base, "index.js"))("puppeteer"); } catch {} }
  return null;
}

export function findChrome() {
  const cands = [];
  if (process.env.PUPPETEER_EXECUTABLE_PATH) cands.push(process.env.PUPPETEER_EXECUTABLE_PATH);
  try {
    const root = join(homedir(), ".cache", "puppeteer", "chrome");
    for (const v of readdirSync(root))
      for (const sub of ["chrome-linux64/chrome", "chrome-linux/chrome"]) {
        const p = join(root, v, sub); if (existsSync(p)) cands.push(p);
      }
  } catch {}
  cands.push("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser");
  return cands.find(existsSync) || null;
}

/**
 * Skip the whole suite: green on a contributor's laptop, RED under CI. In CI a skip looks
 * like a pass while the suites guarding the live site silently stop running, so a missing
 * browser there is a FAILURE.
 */
export function skipper(label) {
  return (msg) => {
    if (process.env.CI) {
      console.error(`❌ ${label} CANNOT SKIP UNDER CI — ${msg}`);
      console.error(`   This suite guards the live site and must actually run. Install a`);
      console.error(`   browser in the workflow (see "Install a browser for the suites`);
      console.error(`   below" in sim/ci/ci.yml) rather than letting the gate go green blind.`);
      process.exit(1);
    }
    console.log(`ℹ️  ${label} skipped —`, msg);
    process.exit(0);
  };
}

/**
 * Launch headless Chrome the way the SIM suites need it: software GL (swiftshader) so the
 * WebGL stage renders on GPU-less runners, optional autoplay, and optional host mappings
 * (`{ "moxie.hosted.test": port }`) so a loopback server can play a public hostname.
 */
export async function launchBrowser(puppeteer, chrome, { hosts = {}, autoplay = false, args = [], ...opts } = {}) {
  const rules = Object.entries(hosts).map(([h, port]) => `MAP ${h} 127.0.0.1:${port}`).join(",");
  return puppeteer.launch({
    executablePath: chrome, headless: "new", ...opts,
    args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
           ...(autoplay ? ["--autoplay-policy=no-user-gesture-required"] : []),
           ...(rules ? [`--host-resolver-rules=${rules}`] : []), ...args],
  });
}

/**
 * `{ puppeteer, chrome }`, or a clean exit(0) when either is missing.
 * @param {string} label
 */
export async function requireBrowser(label) {
  const skip = skipper(label);
  const puppeteer = await loadPuppeteer();
  if (!puppeteer) skip("puppeteer not found (set PUPPETEER_PATH)");
  const chrome = findChrome();
  if (!chrome) skip("no Chrome binary (set PUPPETEER_EXECUTABLE_PATH)");
  return { puppeteer, chrome, skip };
}

/* ---- the static server ---------------------------------------------------- */
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".tsv": "text/tab-separated-values; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".glb": "model/gltf-binary",
};

/**
 * The `/*` block of `sim/web/_headers`, as a plain object — parsed from the real file, never
 * restated, so a suite cannot pass against a policy we do not ship. (Later blocks set only
 * Cache-Control, which does not affect what a browser refuses.)
 */
export function pagesHeaders() {
  const src = readFileSync(join(web, "_headers"), "utf8");
  const out = {};
  let inGlob = false;
  for (const raw of src.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    if (!line || line.trimStart().startsWith("#")) continue;
    if (!/^\s/.test(line)) { inGlob = line.trim() === "/*"; continue; }
    if (!inGlob) continue;
    const i = line.indexOf(":");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

/**
 * A page's HTML **plus the source of its own scripts** (following relative ES-module
 * imports), as one string to grep — behaviour lives in sibling `.js` files, not inline.
 * `vendor/` is EXCLUDED, or `mermaid.render` inside mermaid.min.js would satisfy
 * "docs.html must render mermaid" for a page that never calls it.
 *
 * @param {string} name e.g. "cloud.html"
 * @returns {string} the HTML followed by each first-party script it references, in order.
 */
export function pageSource(name) {
  const html = readFileSync(join(web, name), "utf8");
  const parts = [html], seen = new Set();
  const add = (ref, by) => {
    ref = normalize(ref.split("?")[0].replace(/^\.\//, ""));
    if (seen.has(ref) || /^[a-z]+:|^\/\//i.test(ref) || ref.startsWith("vendor/") || ref.startsWith("..")) return;
    seen.add(ref);
    const f = join(web, ref);
    if (!existsSync(f)) return;
    const src = readFileSync(f, "utf8");
    parts.push(`\n/* ==== ${ref} (loaded by ${by}) ==== */\n` + src);
    // follow relative ES-module imports (moxie.js -> moxie/*.js)
    for (const m of src.matchAll(/\bfrom\s+["'](\.{1,2}\/[^"']+)["']/g)) add(join(dirname(ref), m[1]), ref);
  };
  for (const m of html.matchAll(/<script[^>]*\bsrc\s*=\s*["']([^"']+)["']/g)) add(m[1], name);
  return parts.join("\n");
}

async function freePort() {
  return new Promise((res) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

/**
 * Serve any directory of static files on a free loopback port (`serveWeb` is this with
 * `sim/web` and the Pages headers; the parent-console suite serves `server/static`).
 * `extIsHtml: false` matches FastAPI's StaticFiles, which does NOT rewrite `/sim` to
 * `/sim.html` the way Cloudflare's `_redirects` does.
 *
 * @param {string} dir absolute path of the directory to serve
 * @param {{headers?: Record<string,string>, extIsHtml?: boolean}} [opts]
 * @returns {Promise<{port:number, url:string, close:()=>void, hits:string[]}>}
 */
export async function serveStatic(dir, opts = {}) {
  const port = await freePort();
  const extra = opts.headers || {};
  const extIsHtml = opts.extIsHtml !== false;
  const hits = [];
  const server = http.createServer((req, res) => {
    let p = decodeURIComponent((req.url || "/").split("?")[0]);
    hits.push(p);
    if (p.endsWith("/")) p += "index.html";
    if (extIsHtml && !extname(p)) p += ".html";          // /sim -> /sim.html, like _redirects
    const file = join(dir, normalize(p).replace(/^(\.\.[/\\])+/, ""));
    let body, code = 200;
    try {
      if (!statSync(file).isFile()) throw new Error("dir");
      body = readFileSync(file);
    } catch { code = 404; body = Buffer.from("not found"); }
    const h = { "Content-Type": MIME[extname(file)] || "application/octet-stream", ...extra };
    res.writeHead(code, h);
    res.end(body);
  });
  await new Promise((r) => server.listen(port, "127.0.0.1", r));
  return {
    port, hits,
    url: `http://127.0.0.1:${port}`,
    close: () => { try { server.close(); } catch {} },
  };
}

/**
 * Serve `sim/web` on a free loopback port.
 * @param {{headers?: boolean}} [opts]
 *   `headers: true` sends the real `_headers` `/*` block (CSP, HSTS, nosniff…).
 * @returns {Promise<{port:number, url:string, close:()=>void, hits:string[]}>}
 */
export async function serveWeb(opts = {}) {
  return serveStatic(web, { headers: opts.headers ? pagesHeaders() : {} });
}

/* ---- EYES: what the browser itself reported ------------------------------- *
 * A suite with no listeners cannot fail on a 404'd script, a CSP refusal or an uncaught
 * exception. Both listeners are needed: `pageerror` sees only uncaught exceptions, while a
 * 404'd script or CSP refusal surfaces only as a CONSOLE message.
 *
 * THE FILTER IS A BUDGET, NOT A PATTERN. The interceptor COUNTS the errors the fixture
 * provoked on purpose (aborted sidecars, deliberate error statuses) and `notable()` forgives
 * exactly that many, so a second, unexplained 404 is still a failure.
 */
export const ABORTED_NOISE =
  /Failed to load resource: net::ERR_(CONNECTION_REFUSED|FAILED|BLOCKED_BY_CLIENT|ABORTED)/;
/* Any status the fixture DELIBERATELY served (e.g. a real 503), not only 404. Safe to match
 * broadly because the count, not the pattern, is what forgives. */
export const REFUSED_NOISE =
  /Failed to load resource: the server responded with a status of \d{3}/;

/**
 * Console errors, minus the ones the FIXTURE caused on purpose — forgiven exactly as many
 * times as they were provoked, never by loosening the pattern.
 *
 * @param {string[]} errs      everything the listeners collected, in arrival order
 * @param {{n?:number, refused?:number}} [aborted]
 *   `n` requests this fixture aborted at the network layer; `refused` requests it
 *   answered with an error status on purpose (or knowingly let 404 at the static server).
 * @returns {string[]} the ones nobody asked for.
 */
export function notable(errs, aborted) {
  let budget = aborted ? (aborted.n || 0) : 0;
  let refused = aborted ? (aborted.refused || 0) : 0;
  return errs.filter((e) => {
    if (budget > 0 && ABORTED_NOISE.test(e)) { budget--; return false; }
    if (refused > 0 && REFUSED_NOISE.test(e)) { refused--; return false; }
    return true;
  });
}

/**
 * Give a page eyes: a console listener, a pageerror listener, and the budget counters the
 * request interceptor increments for the noise it provokes itself.
 *
 * @param {import("puppeteer").Page} page
 * @returns {{errs: string[], aborted: {n:number, refused:number}}}
 */
export function watchPage(page) {
  const errs = [], aborted = { n: 0, refused: 0 };
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  return { errs, aborted };
}

/**
 * One assertion string for a page's unexplained console output. Kept next to `notable()`
 * so every suite reports the same way: the COUNT plus the first few messages verbatim,
 * because "3 console errors" sends nobody anywhere.
 */
export function eyesMsg(label, left) {
  return `${label}: the page must raise no unexplained console errors — ` +
         `${left.length} of them, first: ${left.slice(0, 3).join(" | ")}`;
}

/* ---- assertions ----------------------------------------------------------- */
export function makeChecks() {
  const fails = [];
  let n = 0;
  const ok = (c, m) => { n++; if (!c) fails.push(m); };
  const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
  return { fails, ok, eq, count: () => n };
}

/**
 * Report and exit. Non-zero on any failure — the suites are verified BY EXIT CODE.
 */
export function finish(label, { fails, count }) {
  if (fails.length) {
    console.error(`\n❌ ${label}: ${fails.length} failure(s) of ${count()} checks`);
    for (const f of fails) console.error("   · " + f);
    process.exit(1);
  }
  console.log(`✅ ${label} — ${count()} checks passed`);
  process.exit(0);
}

/* ---- a real, audible PCM clip -------------------------------------------- *
 * Base64 LE int16 PCM like `CloudTTSResponse.audio.buffer`. A real tone, not zeros, so suites
 * can assert the peak amplitude that comes back out of Web Audio rather than a silent pass. */
export function pcmToneBase64({ seconds = 0.25, rate = 22050, freq = 440, amp = 0.8 } = {}) {
  const n = Math.floor(seconds * rate);
  const buf = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++)
    buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * freq * i) / rate) * amp * 32767), i * 2);
  return { base64: buf.toString("base64"), rate, frames: n, amp };
}
