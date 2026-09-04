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
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import net from "node:net";
import { requireBrowser, makeChecks, finish } from "./browser_harness.mjs";

const LABEL = "docs-explorer tests";
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

/* Browser discovery lives in ONE place. This file used to carry its own copy of
 * `loadPuppeteer` + `findChrome`, and a second copy is exactly how the defect this branch
 * exists to fix survived unnoticed: the scan for `node_modules/puppeteer` under `~/Code`
 * is a developer-machine path that cannot exist on a runner, so every CI run skipped and
 * the badge stayed green. `requireBrowser` is that discovery plus the rule that a missing
 * browser is a FAILURE under CI, and it cannot drift from what the other suites do. */
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);

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

/* `makeChecks` rather than a local two-liner, for the COUNT. A conditional assertion that
 * stops running (see check 7 below, which used to sit inside a bare `if`) leaves no trace
 * at all when the only output is "no failures"; "N checks passed" changes when coverage
 * silently drops, which is the failure this whole branch is about. */
const { fails, ok, count } = makeChecks();

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

  /* THE PUBLIC INTERNET IS NOT A TEST DEPENDENCY — the third shape of the same defect.
   *
   * `test_responsive.mjs` and `test_env_hosted.mjs` asserted zero console errors and
   * passed only because the author's :8081/:8082 sidecars happen to answer. This suite
   * asserts zero console errors (check 10, below) and passes only because GITHUB ANSWERS:
   * `README.md` embeds an `<img>` from `github.com/user-attachments`, that README is
   * bundled into `sim/web/docs-bundle/_root/README.md`, and `docs.html` renders it with no
   * CSP to refuse it. On a runner behind an egress proxy — or on the day that attachment
   * URL rots — the fetch fails, Chrome logs an error the page cannot suppress, and this
   * goes red for a reason that has nothing to do with the docs explorer.
   *
   * These are LOCAL-SERVER suites. So every off-origin http(s) request is aborted here,
   * which makes the suite hermetic in BOTH directions: it can no longer depend on the
   * network being up, and it can no longer quietly start depending on a new remote asset.
   * Exactly as many refusals are then forgiven as were provoked, and no more — the rule
   * the two fixes above established — so a console error from anything else still fails.
   * `data:` and `blob:` are untouched: they never leave the page. */
  const blocked = { n: 0, urls: [] };
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/^https?:/.test(u) && !u.startsWith(base)) {
      blocked.n++; if (blocked.urls.length < 5) blocked.urls.push(u);
      return r.abort("blockedbyclient");
    }
    return r.continue();
  });

  // 1) tree populates + home markdown renders
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  const treeCount = await page.$$eval("a.doc", (els) => els.length).catch(() => 0);
  ok(treeCount >= 60, `tree should list the docs (got ${treeCount})`);
  ok(await page.evaluate(() => !!document.querySelector("article h1, article h2, article p")),
     "home document markdown should render");

  // 1b) the reverse-engineering section is sub-grouped by folder (Protocol / Runtime / Firmware / …)
  const subheads = await page.$$eval(".subhead", (els) => els.map((e) => e.textContent));
  ok(subheads.some((t) => /Protocol/.test(t)) && subheads.some((t) => /Runtime/.test(t)) &&
     subheads.some((t) => /Firmware/.test(t)) && subheads.some((t) => /Manifests/.test(t)),
     `tree should show folder sub-group headers incl. Manifests (got ${subheads.join(", ")})`);

  // 2) Mermaid renders on a diagram-heavy doc + the topbar meta shows reading time + diagram count
  await page.goto(base + "/docs.html#reverse-engineering/architecture-diagrams.md", { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("article svg").length>0', { timeout: 8000 }).catch(() => {});
  const svgs = await page.evaluate(() => document.querySelectorAll("article svg").length);
  ok(svgs > 0, "Mermaid diagrams should render to SVG");
  const meta = await page.evaluate(() => (document.getElementById("docmeta") || {}).textContent || "");
  ok(/~\d+ min/.test(meta) && /diagram/.test(meta), `topbar should show reading time + diagram count (got "${meta}")`);

  // 2b) linked non-.md manifests (.tsv/.dts) open in the explorer as a code block (not a 404)
  await page.goto(base + "/docs.html#reverse-engineering/firmware/manifests/init-services.tsv", { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("#content pre code").length>0', { timeout: 8000 }).catch(() => {});
  const manifest = await page.evaluate(() => { const c = document.querySelector("#content pre code"); return { hasCode: !!c, len: c ? c.textContent.length : 0 }; });
  ok(manifest.hasCode && manifest.len > 50, `linked .tsv manifest should render as a code block (got ${JSON.stringify(manifest)})`);

  // 3) code highlighting applies (hljs token spans)
  await page.goto(base + "/docs.html#reverse-engineering/hardware/hardware-map.md", { waitUntil: "domcontentloaded" });
  /* Wait for the TOKENS, not merely for the `<code>` that will hold them. Waiting on the
   * container and then reading the spans assumes highlight.js finishes inside whatever
   * slack the machine happens to leave — this check went red the moment request
   * interception (below) slowed the page down, and would do the same on a slow runner.
   * Waiting on the assertion's own subject removes the machine-speed term without
   * weakening it: if the highlighting never happens the wait simply expires and the
   * assertion still fails. */
  const HLJS = 'article pre code .hljs-keyword, article pre code .hljs-string, ' +
               'article pre code .hljs-comment, article pre code .hljs-number, ' +
               'article pre code .hljs-title, article pre code .hljs-attr';
  await page.waitForFunction((sel) => document.querySelectorAll(sel).length > 0, { timeout: 8000 }, HLJS)
    .catch(() => {});
  const tokenSpans = await page.evaluate((sel) => document.querySelectorAll(sel).length, HLJS);
  ok(tokenSpans > 0, "code blocks should be syntax-highlighted");

  // 4) full-text search filters the tree for a body-only term
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  await page.evaluate(() => { document.getElementById("q").value = ""; });
  await page.type("#q", "projectorfanpid");           // appears only in body text
  await new Promise((r) => setTimeout(r, 800));        // debounce + lazy docs-search.json fetch
  const hits = await page.$$eval("a.doc.hit", (els) => els.length).catch(() => 0);
  ok(hits > 0, "full-text search should filter the tree to matching docs");

  // 4b) search hits are ranked by relevance within a section: a proto message name
  //     should surface the doc that documents it ahead of the section README.
  await page.evaluate(() => { const q = document.getElementById("q"); q.value = ""; q.dispatchEvent(new Event("input")); });
  await page.type("#q", "SystemVolumeModify");         // documented in runtime-control.md; README only lists it
  await new Promise((r) => setTimeout(r, 800));
  const reOrder = await page.evaluate(() => {
    for (const g of document.querySelectorAll(".grp")) {
      const h = g.querySelector(".gh span");
      if (h && /Reverse engineering/.test(h.textContent))
        return [...g.querySelectorAll("a.doc")].map((a) => a.dataset.path.split("/").pop());
    }
    return [];
  });
  const iDoc = reOrder.indexOf("runtime-control.md"), iReadme = reOrder.indexOf("README.md");
  ok(iDoc === 0, `search should rank the documenting doc first in its section (got ${reOrder.slice(0, 3).join(", ")})`);
  /* `iReadme === -1 || iDoc < iReadme` was satisfiable by the README simply not being in
   * the section at all (`-1`), i.e. by the ranking having nothing to rank. Both must be
   * present for "outranks" to mean anything. */
  ok(iReadme > 0 && iDoc < iReadme,
     `the documenting doc should outrank the section README (doc ${iDoc}, README ${iReadme})`);

  // 5) opening a search hit highlights the term in the doc + scrolls to it
  await page.evaluate(() => { const q = document.getElementById("q"); q.value = ""; q.dispatchEvent(new Event("input")); });
  await page.type("#q", "projectorfanpid");
  await new Promise((r) => setTimeout(r, 700));
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
  await page.goto(base + "/docs.html#reverse-engineering/runtime/behavior-input-events.md", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("article a[data-anchor]", { timeout: 8000 }).catch(() => {});
  const clickedAnchor = await page.evaluate(() => {
    const l = [...document.querySelectorAll("article a[data-anchor]")]
      .find((x) => /hardware-map/.test(x.getAttribute("href") || "") && /raw-uart/i.test(x.dataset.anchor));
    if (l) { l.click(); return true; }
    return false;
  });
  /* Not `if (clickedAnchor)` any more. That bare guard meant a link which stopped matching
   * took TWO assertions with it and said nothing — a coverage hole that reports as a pass,
   * which is the precise failure this branch exists to close. */
  ok(clickedAnchor, "the cross-doc heading link should still exist in the rendered doc");
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
  await page.goto(base + "/docs.html#reverse-engineering/hardware/hardware-map.md#raw-uart-command-set-lizzerfacecommands",
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

  /* Forgive exactly the off-origin refusals this suite caused itself, and nothing else. */
  const OFF_ORIGIN = /Failed to load resource: net::ERR_BLOCKED_BY_CLIENT/;
  let forgive = blocked.n;
  const notable = errs.filter((e) => {
    if (forgive > 0 && OFF_ORIGIN.test(e)) { forgive--; return false; }
    return true;
  });
  ok(notable.length === 0, `console errors: ${notable.slice(0, 4).join(" | ")}`);
  /* …and say what was cut off, so a doc that quietly grows a remote dependency is visible
   * in the log rather than silently tolerated. */
  if (blocked.n) console.log(`   (blocked ${blocked.n} off-origin request(s): ${blocked.urls.join(", ")})`);
} finally {
  await browser.close();
  cleanup();
}

finish(LABEL, { fails, count });
