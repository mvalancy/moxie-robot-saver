// test_mermaid.mjs — every Mermaid diagram in the docs explorer must render CLEANLY.
//
// Guards the three failure modes that made diagrams look broken:
//   1. parse errors (a diagram fails to render → a `.err` box),
//   2. clipped labels (multi-line node text spilling past the diagram box — caused by
//      the webfont loading after Mermaid measured the boxes, and by literal `\n`),
//   3. literal "\n" in rendered text (must be `<br/>` in the source).
//
// Loads each doc that the index says has Mermaid, renders it in a real browser via
// docs.html, and asserts a clean SVG with no error box, no clipped label, no "\n".
// Skips cleanly (exit 0) when puppeteer/Chrome are absent, like the other browser tests.
//
//   node sim/test_mermaid.mjs
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
function skip(m) { console.log("ℹ️  mermaid tests skipped —", m); process.exit(0); }

const puppeteer = await loadPuppeteer();
if (!puppeteer) skip("puppeteer not found");
const chrome = findChrome();
if (!chrome) skip("no Chrome binary");

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
if (!(await waitUp())) { cleanup(); skip("serve.py did not come up"); }

const fails = [];
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});
let docs = [], totalSvg = 0;
try {
  const idx = await (await fetch(base + "/docs-index.json")).json();
  docs = idx.files.filter((f) => f.mermaid > 0).map((f) => f.path);
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1200 });

  for (const d of docs) {
    await page.goto(`${base}/docs.html#${d}`, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      'document.querySelectorAll("article .mermaid svg, article .mermaid .err").length>0',
      { timeout: 9000 }).catch(() => {});
    await new Promise((r) => setTimeout(r, 700));
    const res = await page.evaluate(() => {
      const out = { svgs: 0, errs: 0, clipped: 0, literalNL: 0, details: [] };
      out.errs = document.querySelectorAll("article .mermaid .err").length;
      const svgs = document.querySelectorAll("article .mermaid svg");
      out.svgs = svgs.length;
      svgs.forEach((svg, si) => {
        const sb = svg.getBoundingClientRect();
        svg.querySelectorAll("foreignObject").forEach((fo) => {
          const el = fo.querySelector("div, span"); if (!el) return;
          if ((el.textContent || "").includes("\\n")) out.literalNL++;
          const lb = el.getBoundingClientRect();
          if (lb.bottom > sb.bottom + 2 || lb.right > sb.right + 2 || lb.top < sb.top - 2) {
            out.clipped++; out.details.push(`[${si}] "${(el.textContent || "").slice(0, 24)}"`);
          }
        });
        svg.querySelectorAll("text").forEach((t) => { if ((t.textContent || "").includes("\\n")) out.literalNL++; });
      });
      return out;
    });
    totalSvg += res.svgs;
    if (res.errs) fails.push(`${d}: ${res.errs} diagram(s) failed to render (parse error)`);
    if (res.clipped) fails.push(`${d}: ${res.clipped} clipped label(s) ${res.details.slice(0, 3).join(", ")}`);
    if (res.literalNL) fails.push(`${d}: ${res.literalNL} literal "\\n" in a label (use <br/>)`);
    if (res.svgs === 0 && !res.errs) fails.push(`${d}: no diagram rendered`);
  }
} finally {
  await browser.close();
  cleanup();
}

if (fails.length) {
  console.log("❌ mermaid tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ mermaid tests OK — ${totalSvg} diagrams across ${docs.length} docs render clean (no errors, no clipped labels, no literal \\n)`);
