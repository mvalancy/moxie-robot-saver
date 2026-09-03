// test_env_hosted.mjs — the hosted deploy must not fire doomed backend probes, and what
// it tells a visitor must be TRUE in every mode.
//
// env.js probes optional local TTS/STT sidecars (:8081/:8082 /health) to annotate the
// simulator, but those ports can't exist on the hosted Cloudflare deploy. This loads
// sim.html under a NON-local hostname (mapped to the local test server via Chrome's
// host-resolver rules) and asserts: env = "hosted", ZERO :8081/:8082 probes fired, the
// badge shows, and there are no console errors. Also sanity-checks that a LOCAL load
// still probes (feature detection intact).
//
// It then does the part that matters for the honest indicator (spec
// docs/architecture/backlog/live-sim-demo.md §6.3/§7): with `/api/health` stubbed at the
// browser, the page is driven through OFFLINE (the route is absent — the guarantee that
// none of this regressed the existing site), DEGRADED (the route answered
// `gateway_not_configured`), LIVE, LIVE-but-BUSY, and a malformed reply. Each case
// asserts the real rendered badge, pill, banner and `needs-backend` marks — not a mock.
//
// The mode probe REPLACED the two sidecar probes on a hosted host; it did not join them
// (spec acceptance criterion A10), and this file is what proves it.
//
// Skips cleanly (exit 0) with no browser, like the other headless tests.
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

// The envelope bodies come from the REAL Function, imported and called, so this test can
// never drift from what the route actually answers. No Cloudflare account involved.
const health = await import(join(repo, "functions", "api", "health.js"));
const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const bodyOf = async (env) => (await health.onRequestGet({ env })).text();
const HEALTH_BARE = await bodyOf({});                       // nothing configured
const HEALTH_LIVE = await bodyOf({
  DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
  DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
  DEMO_STT_MODEL: "test-ears-model",
});
const HEALTH_BUSY = JSON.stringify(envelope.envelope({
  ok: true, mode: "live", voice: true, ears: true, load: { inflight: 4, capacity: 4 },
}));

// A non-local hostname mapped to the loopback test server — makes env.js see a "hosted" host.
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${port}`],
});

/**
 * Load sim.html and report what a visitor would actually see.
 * @param {string} url
 * @param {{health?:{status:number,body:string,contentType?:string}, transport?:boolean}} [opts]
 *   `health` stubs the /api/health reply at the browser (the static test server has no
 *   Functions); `transport` pretends P0-b's cloud-transport.js is loaded.
 */
async function load(url, opts = {}) {
  const page = await browser.newPage();
  const raw = [], sidecar = [], api = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") raw.push(m.text()); });
  page.on("pageerror", (e) => raw.push("PAGEERR " + e.message));
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  page.on("request", (r) => {
    const u = r.url();
    if (/:808[12]\/health\b/.test(u)) sidecar.push(u);       // the doomed sidecar probes
    if (/\/api\/health\b/.test(u)) api.push(u);              // the same-origin mode probe
  });
  if (opts.transport)
    await page.evaluateOnNewDocument(() => { window.moxieCloudTransport = true; });
  if (opts.health) {
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.isInterceptResolutionHandled()) return;
      if (/\/api\/health\b/.test(r.url()))
        return r.respond({ status: opts.health.status, body: opts.health.body,
                           contentType: opts.health.contentType || "application/json" });
      return r.continue();
    });
  }
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 }).catch((e) => errs.push("NAV " + e.message));
  await new Promise((r) => setTimeout(r, 3000));  // give the probes (if any) time to fire
  const info = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel);
    const marked = (id) => {
      const el = document.getElementById(id);
      return !!(el && el.classList.contains("needs-backend"));
    };
    const pill = q(".mode-pill");
    return {
      env: document.body.getAttribute("data-env"),
      mode: document.body.getAttribute("data-mode"),
      badge: (q(".env-badge") || {}).textContent || "",
      pill: pill ? pill.textContent : null,
      pillShown: !!(pill && !pill.hidden),
      banner: (q("#env-banner .eb-text") || {}).textContent || "",
      ttsStatus: (document.getElementById("tts-status") || {}).textContent || "",
      micStatus: (document.getElementById("mic-status") || {}).textContent || "",
      micMarked: marked("mic-btn"),
      busMarked: marked("bus-connect"),
      ttsMarked: marked("tts-test"),
      hasMode: !!window.moxieMode,
      state: window.moxieMode ? window.moxieMode.state() : null,
      polls: window.moxieMode ? window.moxieMode.stats().polls : null,
    };
  });
  await page.close();
  // Chrome logs a 404 SUBRESOURCE as a console error, and a static host with no Pages
  // Functions genuinely 404s the mode probe — once — which IS the offline path working
  // as designed. So that one entry is separated out precisely (by correlating the console
  // text with the 404 responses actually observed) rather than by loosening the guard:
  // `errs` stays strict for everything else, and `notFound` is asserted by URL, so any
  // OTHER missing asset fails both checks instead of hiding behind this one.
  const onlyProbe404 = notFound.length > 0 && notFound.every((u) => /\/api\/health\b/.test(u));
  const errs = raw.filter((t) => !(onlyProbe404 && /status of 404/.test(t)));
  return { errs, raw, notFound, sidecar, api, ...info };
}

const HOSTED = `http://moxie.hosted.test:${port}/sim.html`;

try {
  // --- 1. OFFLINE: no Functions behind this static server, so /api/health 404s. This is
  //        the case that must be byte-identical to the site as it shipped before.
  const off = await load(HOSTED);
  ok(off.env === "hosted", `hosted host should read env=hosted (got ${off.env})`);
  ok(off.sidecar.length === 0, `hosted deploy must fire NO :8081/:8082 probes (fired ${off.sidecar.length})`);
  ok(off.hasMode === true, "mode.js must be loaded on sim.html");
  ok(off.api.length === 1, `an absent route must be probed ONCE and never again (fired ${off.api.length})`);
  ok(off.state === "offline", `a 404 /api/health must read as offline (got ${off.state})`);
  ok(off.mode === "offline", `body[data-mode] should say offline (got ${off.mode})`);
  ok(off.badge === "HOSTED DEMO", `offline must keep today's badge exactly (got "${off.badge}")`);
  ok(off.pillShown === false, "offline must show no pill — the page is today's, unchanged");
  ok(/only pre.scripted lines have audio/.test(off.ttsStatus),
     `offline must keep today's TTS wording (got "${off.ttsStatus}")`);
  ok(/scripted child line/.test(off.micStatus),
     `offline must keep today's mic wording (got "${off.micStatus}")`);
  ok(off.micMarked && off.busMarked && off.ttsMarked, "offline keeps all three needs-backend marks");
  ok(/need a locally/.test(off.banner), `offline must keep today's banner (got "${off.banner}")`);
  ok(off.errs.length === 0, `offline console errors: ${off.errs.slice(0, 3).join(" | ")}`);
  ok(off.notFound.length === 1 && /\/api\/health\b/.test(off.notFound[0]),
     `the ONLY 404 on the page must be the mode probe itself (got ${JSON.stringify(off.notFound)})`);
  ok(off.raw.length === 1, `...and it must be the only console error either (got ${off.raw.length})`);

  // --- 2. DEGRADED: the route exists and says nothing is configured. Same page, and
  //        exactly ONE request for the whole session (§4.5, "no poll storm").
  const deg = await load(HOSTED, { health: { status: 200, body: HEALTH_BARE } });
  ok(deg.state === "degraded", `gateway_not_configured must read as degraded (got ${deg.state})`);
  ok(deg.api.length === 1, `not-configured must be probed ONCE (fired ${deg.api.length})`);
  ok(deg.badge === "HOSTED DEMO", `degraded/not-configured keeps today's badge (got "${deg.badge}")`);
  ok(deg.pillShown === false, "degraded/not-configured shows no pill — §7 keeps today's copy");
  ok(/only pre.scripted lines have audio/.test(deg.ttsStatus),
     `degraded keeps today's TTS wording (got "${deg.ttsStatus}")`);
  ok(deg.micMarked && deg.busMarked, "degraded keeps the mic and link marks");
  ok(deg.errs.length === 0, `degraded console errors: ${deg.errs.slice(0, 3).join(" | ")}`);
  ok(deg.notFound.length === 0, `a route that answers must produce no 404 at all (got ${JSON.stringify(deg.notFound)})`);
  ok(deg.raw.length === 0, `...and a completely clean console (got ${JSON.stringify(deg.raw.slice(0, 2))})`);

  // --- 3. LIVE, with the transport P0-b brings. The page stops claiming the mic needs a
  //        local server, because with a same-origin route that claim is false.
  const live = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true });
  ok(live.state === "live", `a configured route must read as live (got ${live.state})`);
  ok(live.badge === "HOSTED DEMO · LIVE", `live badge (got "${live.badge}")`);
  ok(live.mode === "live", `body[data-mode] should say live (got ${live.mode})`);
  ok(live.pillShown === false, "live and idle: nothing to apologise for");
  ok(live.micMarked === false, "live ears must REMOVE #mic-btn's needs-backend mark");
  ok(live.busMarked === true, "#bus-connect keeps its mark in EVERY mode — a real broker is not here");
  ok(/own voice is live/.test(live.ttsStatus), `live voice wording (got "${live.ttsStatus}")`);
  ok(/live brain answers on this page/.test(live.banner), `live banner (got "${live.banner}")`);
  ok(live.sidecar.length === 0, "a live hosted page still fires no sidecar probes");
  ok(live.errs.length === 0, `live console errors: ${live.errs.slice(0, 3).join(" | ")}`);

  // --- 3b. LIVE with no transport loaded — which is exactly what P0-a alone ships. The
  //         page must NOT claim LIVE over something that still answers from stub.js.
  const noTr = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE } });
  ok(noTr.state === "live", `the mode is still live (got ${noTr.state})`);
  ok(noTr.badge === "HOSTED DEMO · SCRIPTED",
     `a live mode with no transport must read SCRIPTED (got "${noTr.badge}")`);
  ok(noTr.pillShown === true && /no live transport/.test(noTr.pill || ""),
     `...and say why (got "${noTr.pill}")`);
  ok(noTr.errs.length === 0, `no-transport console errors: ${noTr.errs.slice(0, 3).join(" | ")}`);

  // --- 4. AT CAPACITY: the clear indicator when too many people are on (§7).
  const busy = await load(HOSTED, { health: { status: 200, body: HEALTH_BUSY }, transport: true });
  ok(busy.badge === "HOSTED DEMO · BUSY", `4/4 in flight must read BUSY (got "${busy.badge}")`);
  ok(busy.pillShown === true, "at capacity the pill must be visible");
  ok(/hands full/.test(busy.pill || ""), `...with §7's copy (got "${busy.pill}")`);
  ok(!/\b(429|503|5\d\d)\b/.test(busy.pill || ""), "a visitor must never see a raw status code");
  ok(busy.errs.length === 0, `busy console errors: ${busy.errs.slice(0, 3).join(" | ")}`);

  // --- 5. A malformed reply must leave the page SAFE, not throw and not be believed.
  const bad = await load(HOSTED, {
    health: { status: 200, body: "<!doctype html><html>not the api</html>", contentType: "text/html" },
  });
  ok(bad.state === "offline", `a 200 of HTML must not be believed (got ${bad.state})`);
  ok(bad.badge === "HOSTED DEMO", `...and the page stays today's (got "${bad.badge}")`);
  ok(bad.errs.length === 0, `malformed-reply console errors: ${bad.errs.slice(0, 3).join(" | ")}`);

  // --- 6. local: the sidecar probes still fire (feature detection intact), clean console
  const local = await load(`http://127.0.0.1:${port}/sim.html`);
  ok(local.env === "local", `loopback host should read env=local (got ${local.env})`);
  ok(local.sidecar.length === 2, `local load should probe both sidecars (fired ${local.sidecar.length})`);
  ok(local.badge === "LOCAL", `local badge should stay LOCAL (got "${local.badge}")`);
  ok(local.errs.length === 0, `local console errors: ${local.errs.slice(0, 3).join(" | ")}`);
  ok(local.api.length === 1, `local probes the same-origin route once too (fired ${local.api.length})`);
  ok(local.notFound.every((u) => /\/api\/health\b/.test(u)),
     `the only 404 locally must be the mode probe (got ${JSON.stringify(local.notFound)})`);
} finally {
  await browser.close();
  cleanup();
}

if (fails.length) {
  console.log("❌ env-hosted test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ env-hosted test OK — hosted fires no :8081/:8082 probes and exactly ONE same-origin "
  + "/api/health; an absent or malformed route leaves the page byte-identical to today (offline); "
  + "gateway_not_configured keeps today's copy with no poll storm; live removes #mic-btn's "
  + "needs-backend mark and rewrites the voice line and the banner; live-without-a-transport reads "
  + "SCRIPTED and says why; 4/4 in flight reads BUSY with §7's copy and no status code; "
  + "#bus-connect stays marked in every mode; local still probes both sidecars");
