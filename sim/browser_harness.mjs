/* browser_harness.mjs — the shared plumbing for the headless-browser suites.
 *
 * NOT a test. `sim/tests/test_ci_test_coverage.py` enumerates `sim/test_*.mjs` and
 * `sim/run_*.sh`; this file is neither, on purpose — it is imported by the suites that
 * are, and it exists because three of them needed the same 60 lines of puppeteer
 * discovery and the same static server, and a third hand-rolled copy is how those drift.
 *
 * WHY ITS OWN STATIC SERVER rather than `sim/serve.py`. `sim/test_csp.mjs` has to load the
 * site with the REAL `sim/web/_headers` policy applied, because that policy is only ever
 * sent by Cloudflare Pages — every browser suite in this repo until now served the pages
 * with NO CSP at all, which means none of them could have caught a policy that breaks the
 * page. `serveWeb({ headers: true })` parses `_headers` and sends the `/*` block, so the
 * suite tests the header we actually ship. With `headers: false` it is a plain static
 * server and behaves like `serve.py` did.
 *
 * Everything here SKIPS CLEANLY (exit 0) when no browser is available, like the suites it
 * serves — CI runners without Chrome must stay green rather than red-for-the-wrong-reason.
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
 * Skip the whole suite. Green on a contributor's laptop; RED under CI.
 *
 * WHY THE ASYMMETRY. A clean skip is right for someone who just cloned the repo and has
 * no browser — the other 18 suites still tell them something. It is exactly WRONG in CI,
 * where a skip is indistinguishable from a pass in the badge, and where the browser suites
 * are the ones guarding the live public site. For months `npm install puppeteer` was in no
 * workflow file, so nine suites printed "skipped — puppeteer not found" on every green run
 * and the merge gate quietly lost its best assertions — the same shape as PR #82, whose 770
 * assertions all read a file while Web Audio was stubbed. `test_typed_turn.mjs` was written
 * to close exactly that hole and had never once executed here.
 *
 * So: under `CI`, a missing browser is a FAILURE, not a skip. If the install step above ever
 * breaks, the tier reddens and says so instead of deleting five minutes of coverage in
 * silence. A test that cannot fail is not a test, and a skip that cannot be seen is not a skip.
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
 * The `/*` block of `sim/web/_headers`, as a plain object.
 *
 * Only that block is read, and that is the honest scope: the later blocks in the file set
 * `Cache-Control` only, which is irrelevant to what a browser will REFUSE to run. Parsing
 * the real file rather than restating the policy is the point — a suite that hard-coded
 * the CSP string could pass while the shipped header said something else.
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
 * A page's HTML **plus the source of its own scripts**, as one string to grep.
 *
 * WHY THIS EXISTS. Until 2026-09-04 every page carried its behaviour in an inline
 * `<script>`, so a suite could assert "cloud.html fetches fixtures/cloud.json" by grepping
 * the .html file. Dropping `'unsafe-inline'` from `script-src` moved all of that into
 * sibling `.js` files, and those greps would have gone quietly false — the CODE still does
 * the thing, the FILE no longer mentions it.
 *
 * So the unit of inspection is now the page *and what it loads*. That is strictly stronger
 * than the old grep: a page that dropped the `<script src>` tag entirely would keep its
 * behaviour "in the repo" but lose it on screen, and this notices, because it follows the
 * tags the page actually carries.
 *
 * `vendor/` is DELIBERATELY EXCLUDED. Concatenating the minified libraries would make the
 * assertions vacuous in the worst way — `mermaid.render` appears inside `mermaid.min.js`,
 * so "docs.html must render mermaid" would pass for a page that never called it.
 *
 * @param {string} name e.g. "cloud.html"
 * @returns {string} the HTML followed by each first-party script it references, in order.
 */
export function pageSource(name) {
  const html = readFileSync(join(web, name), "utf8");
  const parts = [html];
  for (const m of html.matchAll(/<script[^>]*\bsrc\s*=\s*["']([^"']+)["']/g)) {
    const ref = m[1].split("?")[0].replace(/^\.\//, "");
    if (/^[a-z]+:|^\/\//i.test(ref) || ref.startsWith("vendor/") || ref.includes("..")) continue;
    const f = join(web, ref);
    if (existsSync(f)) parts.push(`\n/* ==== ${ref} (loaded by ${name}) ==== */\n` + readFileSync(f, "utf8"));
  }
  return parts.join("\n");
}

async function freePort() {
  return new Promise((res) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

/**
 * Serve any directory of static files on a free loopback port.
 *
 * WHY IT IS SEPARATE FROM `serveWeb`. Every browser suite in this repo until now loaded
 * `sim/web` — the public simulator — and `grep -rln "server/static" sim/test_*.mjs` came
 * back empty, so the PARENT CONSOLE (`server/static/index.html` + `app.js`, ~2,470 lines
 * of the thing a parent actually uses) had no headless coverage at all. Its cards are
 * asserted only through Python route tests, which cannot see a button that never wires
 * up. `serveWeb` is now a thin call to this with `web` and the Pages headers.
 *
 * `extIsHtml: false` is the console's shape: it is served by FastAPI's StaticFiles, which
 * does NOT rewrite `/sim` to `/sim.html` the way Cloudflare's `_redirects` does, so
 * inventing that here would let a suite pass against a route the real server 404s.
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
 * The gateway voice arrives as `CloudTTSResponse.audio.buffer`: base64 little-endian
 * int16 PCM (see `sim/web/audio.js::decodeCloudTTS`). A test fixture of zeros would decode,
 * play, and pass every structural check while being SILENT — so the fixture is a real tone
 * and the suites assert the peak sample amplitude that comes back out of the Web Audio
 * buffer, not merely that something was scheduled. */
export function pcmToneBase64({ seconds = 0.25, rate = 22050, freq = 440, amp = 0.8 } = {}) {
  const n = Math.floor(seconds * rate);
  const buf = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++)
    buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * freq * i) / rate) * amp * 32767), i * 2);
  return { base64: buf.toString("base64"), rate, frames: n, amp };
}
