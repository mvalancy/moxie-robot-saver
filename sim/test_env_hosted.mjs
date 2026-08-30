// test_env_hosted.mjs — the hosted static deploy must not fire doomed backend probes.
//
// env.js probes optional local TTS/STT sidecars (:8081/:8082 /health) to annotate the
// simulator, but those ports can't exist on the hosted Cloudflare deploy. This loads
// sim.html under a NON-local hostname (mapped to the local test server via Chrome's
// host-resolver rules) and asserts: env = "hosted", ZERO /health requests fired, the
// "HOSTED DEMO" badge shows, and there are no console errors. Also sanity-checks that a
// LOCAL load still probes (feature detection intact). Skips cleanly (exit 0) with no
// browser, like the other headless tests.
//
//   node sim/test_env_hosted.mjs
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import net from "node:net";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

async function loadPuppeteer() {
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
function findChrome() {
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
function skip(m) { console.log("ℹ️  env-hosted test skipped —", m); process.exit(0); }

const puppeteer = await loadPuppeteer();
if (!puppeteer) skip("puppeteer not found (set PUPPETEER_PATH)");
const chrome = findChrome();
if (!chrome) skip("no Chrome binary (set PUPPETEER_EXECUTABLE_PATH)");

const port = await new Promise((res) => {
  const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
const server = spawn("python3", [join(repo, "sim", "serve.py"), String(port)], { cwd: repo, stdio: "ignore" });
async function waitUp(n = 50) {
  for (let i = 0; i < n; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) }); if (r.ok) return true; } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}
function cleanup() { try { server.kill("SIGKILL"); } catch {} }
if (!(await waitUp())) { cleanup(); skip("serve.py did not come up"); }

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

// A non-local hostname mapped to the loopback test server — makes env.js see a "hosted" host.
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${port}`],
});

async function load(url) {
  const page = await browser.newPage();
  const errs = [], health = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  page.on("request", (r) => { if (/\/health\b/.test(r.url())) health.push(r.url()); });
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 }).catch((e) => errs.push("NAV " + e.message));
  await new Promise((r) => setTimeout(r, 3000));  // give the probe (if any) time to fire
  const info = await page.evaluate(() => ({
    env: document.body.getAttribute("data-env"),
    badge: (document.querySelector(".env-badge") || {}).textContent || "",
  }));
  await page.close();
  return { errs, health, ...info };
}

try {
  // hosted: no probes, env=hosted, badge shown, clean console
  const hosted = await load(`http://moxie.hosted.test:${port}/sim.html`);
  ok(hosted.env === "hosted", `hosted host should read env=hosted (got ${hosted.env})`);
  ok(hosted.health.length === 0, `hosted deploy must fire NO /health probes (fired ${hosted.health.length})`);
  ok(/HOSTED/i.test(hosted.badge), `hosted badge should show (got "${hosted.badge}")`);
  ok(hosted.errs.length === 0, `hosted console errors: ${hosted.errs.slice(0, 3).join(" | ")}`);

  // local: probes still fire (feature detection intact), clean console
  const local = await load(`http://127.0.0.1:${port}/sim.html`);
  ok(local.env === "local", `loopback host should read env=local (got ${local.env})`);
  ok(local.health.length === 2, `local load should probe both sidecars (fired ${local.health.length})`);
  ok(local.errs.length === 0, `local console errors: ${local.errs.slice(0, 3).join(" | ")}`);
} finally {
  await browser.close();
  cleanup();
}

if (fails.length) {
  console.log("❌ env-hosted test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ env-hosted test OK — hosted deploy fires no doomed /health probes (env=hosted, badge shown), "
          + "local still probes both sidecars, no console errors either way");
