/* Docs-explorer test. The static site ships a docs explorer (sim/web/docs.html)
 * that browses every Markdown doc with Mermaid rendered — deployable to Cloudflare
 * Pages with NO build step, which means the bundle must be committed and current.
 * This asserts:
 *   1. docs-index.json exists and covers every docs/*.md in the repo (no drift),
 *   2. each indexed file was actually copied into docs-bundle/,
 *   3. mermaid counts are right, and the vendored renderers are present,
 *   4. docs.html is wired to the vendored marked + mermaid and the index.
 * If the bundle is stale, `python3 sim/tools/build_docs_bundle.py` fixes it.
 * Run: node sim/test_docs.mjs
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const web = join(here, "web");
const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

function walk(dir, base, out) {
  for (const n of readdirSync(dir)) {
    const full = join(dir, n), st = statSync(full);
    if (st.isDirectory()) walk(full, base, out);
    else if (n.endsWith(".md")) out.push(full.slice(base.length + 1).replace(/\\/g, "/"));
  }
  return out;
}

// ---- index exists & parses ----
const idxPath = join(web, "docs-index.json");
let idx = null;
if (!existsSync(idxPath)) {
  console.log("❌ docs tests FAILED:\n   - docs-index.json missing — run python3 sim/tools/build_docs_bundle.py");
  process.exit(1);
}
idx = JSON.parse(readFileSync(idxPath, "utf8"));
ok(idx.firmware && idx.firmware.includes("24.10.803"), "index must be firmware-stamped v24.10.803");
ok(Array.isArray(idx.files) && idx.files.length > 0, "index.files must be non-empty");

// ---- coverage: every docs/*.md is indexed ----
const docsDir = join(repo, "docs");
const onDisk = walk(docsDir, docsDir, []);          // paths relative to docs/
const indexed = new Set(idx.files.filter(f => f.section !== "_root").map(f => f.path));
for (const rel of onDisk)
  ok(indexed.has(rel), `docs/${rel} is not in docs-index.json (stale bundle — rebuild it)`);
ok(indexed.size === onDisk.length,
   `index has ${indexed.size} docs but repo has ${onDisk.length} (rebuild the bundle)`);

// ---- each indexed file copied into the bundle, mermaid count correct ----
let mermaidTotal = 0;
for (const f of idx.files) {
  const bundled = join(web, "docs-bundle", f.path);
  ok(existsSync(bundled), `docs-bundle missing ${f.path}`);
  if (existsSync(bundled)) {
    const txt = readFileSync(bundled, "utf8");
    const nm = (txt.match(/```mermaid/g) || []).length;
    ok(nm === f.mermaid, `${f.path}: index says ${f.mermaid} mermaid, bundle has ${nm}`);
    mermaidTotal += nm;
  }
}
ok(mermaidTotal > 0, "expected at least one mermaid diagram across the docs");

// ---- full-text search index (lazily fetched by docs.html) ----
const searchPath = join(web, "docs-search.json");
ok(existsSync(searchPath), "docs-search.json missing — rebuild the bundle");
if (existsSync(searchPath)) {
  const search = JSON.parse(readFileSync(searchPath, "utf8"));
  for (const f of idx.files)
    ok(typeof search[f.path] === "string" && search[f.path].length > 0,
       `docs-search.json missing full text for ${f.path}`);
  // a known term from the firmware docs must be findable in the body text
  const hay = Object.values(search).join("\n").toLowerCase();
  ok(hay.includes("dlpc3430"), "full-text index should contain body prose (e.g. 'DLPC3430')");
}

// ---- vendored renderers present ----
for (const v of ["marked.min.js", "mermaid.min.js", "highlight.min.js"])
  ok(existsSync(join(web, "vendor", v)), `vendored ${v} missing`);
// the protobuf language must be bundled alongside hljs (30+ proto code blocks)
{
  const hl = readFileSync(join(web, "vendor", "highlight.min.js"), "utf8");
  ok(/registerLanguage\(["']protobuf["']/.test(hl), "highlight.min.js must include the protobuf language");
}

// ---- docs.html wiring ----
const html = readFileSync(join(web, "docs.html"), "utf8");
ok(html.includes("vendor/marked.min.js") && html.includes("vendor/mermaid.min.js"),
   "docs.html must load the vendored marked + mermaid");
ok(html.includes("docs-index.json"), "docs.html must fetch docs-index.json");
ok(html.includes("docs-search.json"), "docs.html must fetch the full-text docs-search.json");
ok(html.includes("mermaid.render") || html.includes("mermaid.init"), "docs.html must render mermaid");
ok(html.includes("vendor/highlight.min.js") && html.includes("highlightElement"),
   "docs.html must load + apply the vendored highlighter");
ok(html.includes("docs-bundle/"), "docs.html must fetch docs from docs-bundle/");

// ---- within-section reading order follows each section's README ----
// The bundler orders docs in a section by that section's README link list (README
// first, then the curated order, unlisted docs after). This guards that the tree +
// pager read in the intended narrative order, and that no doc silently fell out of
// the README (which would dump it into the alphabetical tail).
{
  const reFiles = idx.files.filter(f => f.section === "reverse-engineering" && f.path.split("/").length === 2);
  ok(reFiles[0] && reFiles[0].path.endsWith("/README.md"),
     `reverse-engineering must lead with its README (got ${reFiles[0] && reFiles[0].path})`);
  const readme = readFileSync(join(repo, "docs", "reverse-engineering", "README.md"), "utf8");
  const linkOrder = [...readme.matchAll(/\]\(([A-Za-z0-9._-]+\.md)\)/g)].map(m => m[1]);
  const rank = new Map(linkOrder.map((b, i) => [b, i]));
  // every top-level RE doc (besides README) must be linked from the README (else it
  // silently sorts into the A–Z tail instead of the curated order)
  const unlisted = reFiles.map(f => f.path.split("/").pop())
    .filter(b => b !== "README.md" && !rank.has(b));
  ok(unlisted.length === 0, `reverse-engineering docs missing from README (fall to A–Z tail): ${unlisted.join(", ")}`);
  // and the listed docs must appear in non-decreasing README order
  const ranks = reFiles.slice(1).map(f => f.path.split("/").pop()).filter(b => rank.has(b)).map(b => rank.get(b));
  ok(ranks.every((r, i) => i === 0 || r >= ranks[i - 1]),
     "reverse-engineering docs must be ordered by the README link list");
}

// ---- report ----
if (fails.length) {
  console.log("❌ docs tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ docs tests OK — ${idx.files.length} docs indexed & bundled, ${mermaidTotal} mermaid diagrams, explorer wired`);
