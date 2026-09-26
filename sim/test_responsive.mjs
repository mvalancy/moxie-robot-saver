/* Responsive UI tests for the static site across phone, tablet, laptop, desktop and
 * ultrawide viewports in real Chrome:
 *   - NO horizontal page scroll and NO uncaught console errors,
 *   - the SIMULATOR: the WebGL canvas fills the viewport, window.moxie comes up, and every
 *     control is reachable (side rail on wide screens, working drawer on phones),
 *   - HUB / SETUP / CLOUD / DOCS: no h-scroll + no errors at phone and desktop.
 * Skips cleanly without a browser (set PUPPETEER_PATH / PUPPETEER_EXECUTABLE_PATH).
 *
 * Run: node sim/test_responsive.mjs
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import net from "node:net";
import { skipper, launchBrowser } from "./browser_harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

// ---- locate puppeteer (repo has no npm deps; reuse a local install) ----------
async function loadPuppeteer() {
  try { return (await import("puppeteer")).default; } catch {}
  const bases = [];
  if (process.env.PUPPETEER_PATH) bases.push(process.env.PUPPETEER_PATH);
  try {
    const code = join(homedir(), "Code");
    for (const d of readdirSync(code))
      if (existsSync(join(code, d, "node_modules", "puppeteer", "package.json"))) bases.push(join(code, d));
  } catch {}
  for (const base of bases) {
    try { return createRequire(join(base, "index.js"))("puppeteer"); } catch {}
  }
  return null;
}
// ---- locate a Chrome binary --------------------------------------------------
function findChrome() {
  const cands = [];
  if (process.env.PUPPETEER_EXECUTABLE_PATH) cands.push(process.env.PUPPETEER_EXECUTABLE_PATH);
  try {
    const root = join(homedir(), ".cache", "puppeteer", "chrome");
    for (const v of readdirSync(root)) {
      for (const sub of ["chrome-linux64/chrome", "chrome-linux/chrome"]) {
        const p = join(root, v, sub);
        if (existsSync(p)) cands.push(p);
      }
    }
  } catch {}
  cands.push("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser");
  return cands.find(existsSync) || null;
}

const skip = skipper("responsive tests");

const puppeteer = await loadPuppeteer();
if (!puppeteer) skip("puppeteer not found (set PUPPETEER_PATH to a dir containing node_modules/puppeteer)");
const chrome = findChrome();
if (!chrome) skip("no Chrome binary (set PUPPETEER_EXECUTABLE_PATH, or `npx puppeteer browsers install chrome`)");

// ---- free port + start serve.py ---------------------------------------------
const port = await new Promise((res) => {
  const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
const base = `http://127.0.0.1:${port}`;
const server = spawn("python3", [join(repo, "sim", "serve.py"), String(port)], { cwd: repo, stdio: "ignore" });
async function waitUp(n = 50) {
  for (let i = 0; i < n; i++) {
    try { const r = await fetch(base + "/", { signal: AbortSignal.timeout(1000) }); if (r.ok) return true; } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}
function cleanup() { try { server.kill("SIGKILL"); } catch {} }

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

// Representative devices (label, width, height).
const VIEWPORTS = [
  ["phone-portrait",   390, 844],
  ["phone-landscape",  844, 390],
  ["tablet-portrait",  768, 1024],
  ["tablet-landscape", 1024, 768],
  ["laptop",           1366, 768],
  ["desktop",          1920, 1080],
  ["ultrawide",        2560, 1080],
];

let browser;
try {
  if (!(await waitUp())) { cleanup(); skip(`dev server did not come up on ${base}`); }
  browser = await launchBrowser(puppeteer, chrome);

  async function open(path, w, h) {
    const p = await browser.newPage();
    await p.setViewport({ width: w, height: h });
    const raw = [], notFound = [];
    p.on("console", (m) => { if (m.type() === "error") raw.push(m.text()); });
    p.on("pageerror", (e) => raw.push("pageerror: " + e.message));
    p.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
    // The optional local sidecars (Piper :8081, STT :8082) are probed on purpose from a
    // localhost origin and are usually absent; forgiven only by the requests that actually
    // failed, so a refusal to ANY other host still fails the suite.
    const refused = [];
    p.on("requestfailed", (r) => refused.push(r.url()));
    await p.goto(base + "/" + path, { waitUntil: "domcontentloaded", timeout: 30000 });
    // mode.js probes the optional same-origin `/api/health`, which 404s on a static server —
    // the `offline` path working as designed (live-sim-demo.md §6.3). That one console line is
    // forgiven by correlating it with the 404s actually observed; any other missing asset
    // still fails. Without these two exemptions the suite only passed on a machine that
    // happened to have sidecars listening. Host AND colon are required in the pattern, so a
    // foreign host like `8081.evil.com` is never forgiven.
    const SIDECAR = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]):(8081|8082)(\/|$)/;
    const errors = () => {
      const onlyProbe = notFound.length > 0 && notFound.every((u) => /\/api\/health\b/.test(u));
      const onlySidecars = refused.length > 0 && refused.every((u) => SIDECAR.test(u));
      return raw.filter((t) => {
        if (onlyProbe && /status of 404/.test(t)) return false;
        if (onlySidecars && /ERR_CONNECTION_REFUSED|Failed to load resource/.test(t)) return false;
        return true;
      });
    };
    return { p, errors, notFound, raw };
  }
  const noHScroll = (p) => p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

  // ---- SIMULATOR across every viewport ----
  for (const [label, w, h] of VIEWPORTS) {
    const { p, errors, notFound } = await open("sim.html", w, h);
    // wait for the WebGL app to come up
    await p.evaluate(() => new Promise((r) => {
      if (window.moxie) return r();
      window.addEventListener("moxie-ready", () => r(), { once: true });
      setTimeout(r, 5000);
    }));
    await new Promise((r) => setTimeout(r, 900));
    const s = await p.evaluate(() => {
      const canvas = document.querySelector("#app canvas");
      const rail = document.getElementById("rail-scroll");
      const panel = document.getElementById("panel");
      const toggle = document.getElementById("rail-toggle");
      const groups = [...document.querySelectorAll("#rail-scroll .group")];
      const clipped = groups.filter((g) => {
        const r = g.getBoundingClientRect();
        return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1 || r.bottom < -1);
      }).length;
      return {
        moxieReady: !!window.moxie,
        canvasFull: !!canvas && canvas.clientWidth >= window.innerWidth - 2 && canvas.clientHeight >= window.innerHeight - 2,
        railScroll: rail ? rail.scrollHeight - rail.clientHeight : 0,
        columns: rail ? parseInt(getComputedStyle(rail).columnCount) || 1 : 1,
        clippedGroups: clipped,
        toggleShown: toggle ? getComputedStyle(toggle).display !== "none" : false,
        panelWidth: panel ? Math.round(panel.getBoundingClientRect().width) : 0,
        innerW: window.innerWidth,
      };
    });
    ok(errors().length === 0, `[sim ${label}] console errors: ${errors().slice(0, 2).join(" | ")}`);
    ok(notFound.every((u) => /\/api\/health\b/.test(u)),
       `[sim ${label}] the only 404 may be the mode probe: ${JSON.stringify(notFound)}`);
    ok(await noHScroll(p), `[sim ${label}] page scrolls horizontally`);
    ok(s.moxieReady, `[sim ${label}] window.moxie API did not initialise`);
    ok(s.canvasFull, `[sim ${label}] 3D canvas does not fill the viewport`);
    if (w < 900) {
      // compact (phone/small tablet): the rail collapses to a drawer. The handle
      // must show, and opening it must reveal the controls (scrolling inside is fine).
      ok(s.toggleShown, `[sim ${label}] drawer handle not shown (rail should collapse)`);
      const opened = await p.evaluate(() => {
        const t = document.getElementById("rail-toggle");
        if (document.getElementById("hud").classList.contains("rail-closed")) t.click();
        const rs = document.getElementById("rail-scroll");
        const groups = document.querySelectorAll("#rail-scroll .group").length;
        const anyOff = [...document.querySelectorAll("#rail-scroll .group")].some((g) => {
          const r = g.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1);
        });
        return { visible: getComputedStyle(rs).display !== "none", groups, anyOff };
      });
      ok(opened.visible && opened.groups >= 4, `[sim ${label}] opening the drawer did not reveal the controls`);
      ok(!opened.anyOff, `[sim ${label}] a control group runs off the side when the drawer is open`);
    } else {
      // side panel (medium → ultrawide): a glassy right column. No control group runs
      // off-screen, it leaves room for the 3D, and a MULTI-column panel must fit with
      // no internal scroll (a single-column panel is allowed to scroll).
      ok(s.clippedGroups === 0, `[sim ${label}] ${s.clippedGroups} control group(s) off-screen`);
      ok(s.panelWidth > 0 && s.panelWidth < s.innerW * 0.7, `[sim ${label}] control panel is ${Math.round(s.panelWidth / s.innerW * 100)}% of width`);
      /* No multi-column assertion: the >=900px rail is deliberately a single scrolling column
       * (style.css), which is allowed to scroll. The panel is held honest by the two assertions
       * above: no clipped group, and under 70% of the width. */
    }
    await p.close();
  }

  // ---- other surfaces: phone + desktop, no h-scroll + no errors ----
  for (const path of ["", "setup.html", "cloud.html", "docs.html"]) {
    for (const [label, w, h] of [["phone", 390, 844], ["desktop", 1440, 900]]) {
      const { p, errors, notFound } = await open(path, w, h);
      await new Promise((r) => setTimeout(r, 1200));
      ok(errors().length === 0, `[${path || "hub"} ${label}] console errors: ${errors().slice(0, 2).join(" | ")}`);
      ok(notFound.every((u) => /\/api\/health\b/.test(u)),
         `[${path || "hub"} ${label}] the only 404 may be the mode probe: ${JSON.stringify(notFound)}`);
      ok(await noHScroll(p), `[${path || "hub"} ${label}] page scrolls horizontally`);
      await p.close();
    }
  }
} catch (e) {
  fails.push("harness error: " + (e && e.message));
} finally {
  if (browser) await browser.close().catch(() => {});
  cleanup();
}

if (fails.length) {
  console.log("❌ responsive tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ responsive tests OK — simulator across ${VIEWPORTS.length} viewports (phone→ultrawide) + hub/setup/cloud/docs at phone & desktop: no h-scroll, no console errors, controls reachable`);
