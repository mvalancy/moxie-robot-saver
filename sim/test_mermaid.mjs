// test_mermaid.mjs — every Mermaid diagram in the docs explorer must render CLEANLY:
// no parse error (an `.err` box), no clipped label (text measured before the webfont
// loaded), no literal "\n" (use `<br/>`). Renders, in a real browser, each doc the index
// marks as having Mermaid.
//
//   node sim/test_mermaid.mjs
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, launchBrowser, serveWeb, makeChecks, finish, web } from "./browser_harness.mjs";

const LABEL = "mermaid tests";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, count } = makeChecks();
const site = await serveWeb();
const browser = await launchBrowser(puppeteer, chrome);

const docs = JSON.parse(readFileSync(join(web, "docs-index.json"), "utf8"))
  .files.filter((f) => f.mermaid > 0);
let totalSvg = 0;
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1200 });
  for (const { path: d, mermaid: want } of docs) {
    // A fresh document per doc, so a wait can never be satisfied by the previous doc's diagrams.
    await page.goto("about:blank");
    await page.goto(`${site.url}/docs.html#${d}`, { waitUntil: "domcontentloaded" });
    // Settled = as many diagram blocks as the index promises, each holding an <svg> or an error.
    await page.waitForFunction((n) => {
      const blocks = document.querySelectorAll("article .mermaid");
      return blocks.length >= n && [...blocks].every((b) => b.querySelector("svg, .err"));
    }, { timeout: 15000, polling: 100 }, want).catch(() => {});
    const res = await page.evaluate(() => {
      const out = { svgs: 0, errs: 0, clipped: [], literalNL: 0 };
      out.errs = document.querySelectorAll("article .mermaid .err").length;
      const svgs = document.querySelectorAll("article .mermaid svg");
      out.svgs = svgs.length;
      svgs.forEach((svg, si) => {
        const sb = svg.getBoundingClientRect();
        svg.querySelectorAll("foreignObject").forEach((fo) => {
          const el = fo.querySelector("div, span"); if (!el) return;
          if ((el.textContent || "").includes("\\n")) out.literalNL++;
          const lb = el.getBoundingClientRect();
          if (lb.bottom > sb.bottom + 2 || lb.right > sb.right + 2 || lb.top < sb.top - 2)
            out.clipped.push(`[${si}] "${(el.textContent || "").slice(0, 24)}"`);
        });
        svg.querySelectorAll("text").forEach((t) => { if ((t.textContent || "").includes("\\n")) out.literalNL++; });
      });
      return out;
    });
    totalSvg += res.svgs;
    ok(res.errs === 0, `${d}: ${res.errs} diagram(s) failed to render (parse error)`);
    ok(res.clipped.length === 0, `${d}: ${res.clipped.length} clipped label(s) ${res.clipped.slice(0, 3).join(", ")}`);
    ok(res.literalNL === 0, `${d}: ${res.literalNL} literal "\\n" in a label (use <br/>)`);
    ok(res.svgs + res.errs >= want, `${d}: the index promises ${want} diagram(s), ${res.svgs} rendered`);
  }
} finally {
  await browser.close();
  site.close();
}

/* A collapse tripwire, well under today's counts: an index that stops writing `mermaid`
 * would otherwise make this loop "pass" over nothing. */
ok(docs.length >= 25, `only ${docs.length} docs claim a Mermaid diagram — docs-index.json's "mermaid" field looks broken`);
ok(totalSvg >= 35, `only ${totalSvg} diagrams actually rendered — the page produced no SVG`);
finish(LABEL, { fails, count });
