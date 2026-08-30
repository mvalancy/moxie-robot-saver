// test_docs_explorer.mjs — headless functional test of the docs explorer (docs.html).
//
// test_docs.mjs checks the STATIC wiring (bundle built, files indexed, vendored
// renderers present). This checks the RUNTIME behavior in a real browser: the tree
// populates, markdown + Mermaid render, code is highlighted, full-text search
// filters the tree, and opening a search hit highlights the term in the document
// and scrolls to it. Skips gracefully (exit 0) when puppeteer/Chrome are absent,
// so CI passes without a browser.
//
//   node sim/test_docs_explorer.mjs
//   PUPPETEER_PATH=/dir/with/node_modules/puppeteer node sim/test_docs_explorer.mjs
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
  for (const base of bases) {
    try { return createRequire(join(base, "index.js"))("puppeteer"); } catch {}
  }
  return null;
}
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
function skip(msg) { console.log("ℹ️  docs-explorer tests skipped —", msg); process.exit(0); }

const puppeteer = await loadPuppeteer();
if (!puppeteer) skip("puppeteer not found (set PUPPETEER_PATH to a dir containing node_modules/puppeteer)");
const chrome = findChrome();
if (!chrome) skip("no Chrome binary (set PUPPETEER_EXECUTABLE_PATH, or `npx puppeteer browsers install chrome`)");

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

if (!(await waitUp())) { cleanup(); skip("serve.py did not come up"); }

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});

try {
  const page = await browser.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));

  // 1) tree populates + home markdown renders
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  const treeCount = await page.$$eval("a.doc", (els) => els.length).catch(() => 0);
  ok(treeCount >= 60, `tree should list the docs (got ${treeCount})`);
  ok(await page.evaluate(() => !!document.querySelector("article h1, article h2, article p")),
     "home document markdown should render");

  // 2) Mermaid renders on a diagram-heavy doc + the topbar meta shows reading time + diagram count
  await page.goto(base + "/docs.html#reverse-engineering/architecture-diagrams.md", { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("article svg").length>0', { timeout: 8000 }).catch(() => {});
  const svgs = await page.evaluate(() => document.querySelectorAll("article svg").length);
  ok(svgs > 0, "Mermaid diagrams should render to SVG");
  const meta = await page.evaluate(() => (document.getElementById("docmeta") || {}).textContent || "");
  ok(/~\d+ min/.test(meta) && /diagram/.test(meta), `topbar should show reading time + diagram count (got "${meta}")`);

  // 3) code highlighting applies (hljs token spans)
  await page.goto(base + "/docs.html#reverse-engineering/hardware-map.md", { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("article pre code").length>0', { timeout: 8000 }).catch(() => {});
  const tokenSpans = await page.evaluate(() =>
    document.querySelectorAll("article pre code .hljs-keyword, article pre code .hljs-string, article pre code .hljs-comment, article pre code .hljs-number, article pre code .hljs-title, article pre code .hljs-attr").length);
  ok(tokenSpans > 0, "code blocks should be syntax-highlighted");

  // 4) full-text search filters the tree for a body-only term
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  await page.evaluate(() => { document.getElementById("q").value = ""; });
  await page.type("#q", "projectorfanpid");           // appears only in body text
  await new Promise((r) => setTimeout(r, 800));        // debounce + lazy docs-search.json fetch
  const hits = await page.$$eval("a.doc.hit", (els) => els.length).catch(() => 0);
  ok(hits > 0, "full-text search should filter the tree to matching docs");

  // 5) opening a search hit highlights the term in the doc + scrolls to it
  await page.evaluate(() => { const a = document.querySelector("a.doc.hit") || document.querySelector("a.doc"); a && a.click(); });
  await new Promise((r) => setTimeout(r, 700));
  const hl = await page.evaluate(() => ({
    marks: document.querySelectorAll("article mark.qmatch").length,
    first: !!document.querySelector("article mark.qmatch-first"),
    scroll: document.getElementById("main").scrollTop,
  }));
  ok(hl.marks > 0 && hl.first, "search term should be highlighted in the opened doc");
  ok(hl.scroll > 30, "the view should scroll to the first match");

  // 6) keyboard shortcuts: "/" focuses search; "]" / "[" move to next / prev doc.
  // Clear the search first so the tree (and its nav order) is the full doc set.
  await page.evaluate(() => {
    const q = document.getElementById("q"); q.value = ""; q.dispatchEvent(new Event("input")); q.blur();
    location.hash = "_root/README.md";
  });
  await new Promise((r) => setTimeout(r, 300));
  await page.keyboard.press("Slash");
  ok(await page.evaluate(() => document.activeElement && document.activeElement.id === "q"),
     '"/" should focus the search box');
  await page.evaluate(() => document.getElementById("q").blur());
  const beforeHash = await page.evaluate(() => location.hash);
  await page.keyboard.press("BracketRight");
  await new Promise((r) => setTimeout(r, 250));
  const afterHash = await page.evaluate(() => location.hash);
  ok(afterHash && afterHash !== beforeHash, `"]" should open the next doc (got ${beforeHash} → ${afterHash})`);

  // 7) a cross-doc link to a heading anchor opens that doc AND scrolls to the heading
  await page.goto(base + "/docs.html#reverse-engineering/behavior-input-events.md", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("article a[data-anchor]", { timeout: 8000 }).catch(() => {});
  const clickedAnchor = await page.evaluate(() => {
    const l = [...document.querySelectorAll("article a[data-anchor]")]
      .find((x) => /hardware-map/.test(x.getAttribute("href") || "") && /raw-uart/i.test(x.dataset.anchor));
    if (l) { l.click(); return true; }
    return false;
  });
  if (clickedAnchor) {
    await new Promise((r) => setTimeout(r, 1400));
    const anc = await page.evaluate(() => ({
      hash: location.hash, scroll: document.getElementById("main").scrollTop,
    }));
    ok(/hardware-map/.test(anc.hash) && anc.scroll > 200,
       `cross-doc heading link should scroll to the section (hash ${anc.hash}, scroll ${Math.round(anc.scroll)})`);
  }

  // 8) a shareable section URL (#doc#heading) deep-loads to that section, and each
  //    section heading has a copyable "#" permalink.
  await page.goto(base + "/docs.html#reverse-engineering/hardware-map.md#raw-uart-command-set-lizzerfacecommands",
                  { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("article h2[id]").length>0', { timeout: 9000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 1400));
  const deep = await page.evaluate(() => ({
    scroll: document.getElementById("main").scrollTop,
    permalinks: document.querySelectorAll("article h2 .hlink, article h3 .hlink").length,
  }));
  ok(deep.scroll > 200, `section deep-link URL should scroll to the section (scroll ${Math.round(deep.scroll)})`);
  ok(deep.permalinks > 3, `section headings should have copyable permalinks (got ${deep.permalinks})`);

  // 9) code blocks get a Copy button (and Mermaid sources don't)
  const copyInfo = await page.evaluate(() => {
    const btns = [...document.querySelectorAll("article .codewrap .copy-btn")];
    const onMermaid = document.querySelectorAll(".mermaid .copy-btn").length;
    if (btns.length) { btns[0].click(); }
    return { count: btns.length, label: btns[0] ? btns[0].textContent : "", onMermaid };
  });
  ok(copyInfo.count > 0, "code blocks should have a Copy button");
  ok(copyInfo.label === "Copied", `clicking Copy should give feedback (got "${copyInfo.label}")`);
  ok(copyInfo.onMermaid === 0, "Mermaid diagrams must not get a Copy button");

  ok(errs.length === 0, `console errors: ${errs.slice(0, 4).join(" | ")}`);
} finally {
  await browser.close();
  cleanup();
}

if (fails.length) {
  console.log("❌ docs-explorer tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ docs-explorer tests OK — tree, markdown, Mermaid, code highlighting, full-text search + in-doc highlight/scroll all work, no console errors");
