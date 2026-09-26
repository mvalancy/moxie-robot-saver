// test_mermaid.mjs — every Mermaid diagram in the docs explorer must render CLEANLY.
//
// Guards: 1. parse errors (an `.err` box), 2. clipped labels (text measured before the
// webfont loaded, or literal `\n`), 3. literal "\n" in rendered text (use `<br/>`).
// Renders each doc the index marks as having Mermaid in docs.html in a real browser.
//
//   node sim/test_mermaid.mjs
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import net from "node:net";
import { requireBrowser, launchBrowser } from "./browser_harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

/* Browser discovery lives in ONE place (requireBrowser: a missing browser FAILS under CI). */
const { puppeteer, chrome, skip } = await requireBrowser("mermaid tests");

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
const browser = await launchBrowser(puppeteer, chrome);
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

/* A TEST THAT CANNOT FAIL IS NOT A TEST: if the index stops writing `mermaid`, the filter
 * is empty and the loop "passes" with 0 diagrams. These floors are a tripwire for a
 * COLLAPSE, well under today's counts; move them deliberately if the tree shrinks. */
const FLOOR_DOCS = 25, FLOOR_SVG = 35;
if (docs.length < FLOOR_DOCS)
  fails.push(`only ${docs.length} docs claim a Mermaid diagram (floor ${FLOOR_DOCS}) — ` +
             `docs-index.json's "mermaid" field looks broken, so this suite rendered almost nothing`);
if (totalSvg < FLOOR_SVG)
  fails.push(`only ${totalSvg} diagrams actually rendered (floor ${FLOOR_SVG}) — ` +
             `the loop ran but the page produced no SVG`);

if (fails.length) {
  console.log("❌ mermaid tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ mermaid tests OK — ${totalSvg} diagrams across ${docs.length} docs render clean (no errors, no clipped labels, no literal \\n)`);
