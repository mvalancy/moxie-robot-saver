// test_docs_explorer.mjs — headless functional test of the docs explorer (docs.html).
//
// test_docs.mjs checks the bundle; this checks RUNTIME behavior in a real browser: the tree
// populates, markdown renders (with its hero image), code is highlighted, search filters and
// ranks, a hit highlights and scrolls to the term, keyboard shortcuts and deep links work,
// and every link in every doc goes where it says. Every diagram's rendering is
// test_mermaid.mjs's.
//
//   node sim/test_docs_explorer.mjs
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, posix } from "node:path";
import { requireBrowser, makeChecks, finish, launchBrowser, serveWeb, web } from "./browser_harness.mjs";

const LABEL = "docs-explorer tests";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, count } = makeChecks();
const site = await serveWeb();
const base = site.url;
const browser = await launchBrowser(puppeteer, chrome);

try {
  const page = await browser.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));

  /* THE PUBLIC INTERNET IS NOT A TEST DEPENDENCY. Every off-origin http(s) request is
   * aborted, so the suite neither depends on the network nor quietly starts depending on a
   * new remote asset; exactly as many refusals are forgiven as were provoked.
   * `data:`/`blob:` are untouched. */
  const blocked = { n: 0, urls: [] };
  /* A click that LEAVES the explorer for another page of this site is answered with a stub
   * and recorded, so check 11 can prove where it went without loading (and logging) that page.
   * The stub names an icon: a page without one makes Chrome ask for /favicon.ico, which this
   * server 404s, and whether that console error lands before the next goto is a race. */
  const left = [];
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/^https?:/.test(u) && !u.startsWith(base)) {
      blocked.n++; if (blocked.urls.length < 5) blocked.urls.push(u);
      return r.abort("blockedbyclient");
    }
    if (r.isNavigationRequest() && r.frame() === page.mainFrame() && new URL(u).pathname !== "/docs.html") {
      left.push(u);
      return r.respond({ status: 200, contentType: "text/html",
                         body: '<!doctype html><link rel="icon" href="data:,"><title>left the explorer</title>' });
    }
    return r.continue();
  });

  /* What the NETWORK did with each image, so check 1a can say WHICH cause of
   * `naturalWidth === 0` it hit (aborted vs 404 vs not yet arrived). */
  const imgNet = [];
  page.on("response", (r) => {
    if (/\/img\//.test(r.url())) imgNet.push(`${r.status()} ${new URL(r.url()).pathname}`);
  });
  page.on("requestfailed", (r) => {
    if (/\/img\//.test(r.url()))
      imgNet.push(`FAILED ${new URL(r.url()).pathname} ${(r.failure() || {}).errorText}`);
  });

  // 1) tree populates + home markdown renders
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  const treeCount = await page.$$eval("a.doc", (els) => els.length).catch(() => 0);
  ok(treeCount >= 60, `tree should list the docs (got ${treeCount})`);
  /* Assert a HEADING, not `article p`: docs.html's static "Loading docs…" placeholder is an
   * `article p`, so on a slow runner that check passed with nothing rendered. The placeholder
   * has no heading; if the prose never renders the wait expires and the assertion fails. */
  await page.waitForSelector("article h1, article h2", { timeout: 8000 }).catch(() => {});
  ok(await page.evaluate(() => {
    const a = document.querySelector("article");
    return !!a && !!a.querySelector("h1, h2") && !/^\s*Loading/.test(a.textContent);
  }), "home document markdown should render (not the “Loading docs…” placeholder)");

  /* 1a) …INCLUDING its hero image, from this origin, actually decoded. The README writes it
   * repo-relative (`sim/web/img/…`) so docs.js must REMAP it, and the bytes must DECODE.
   * `naturalWidth` is 0 when broken, blocked — or NOT YET LOADED (the 612 KB hero is requested
   * only after README renders). Wait on `img.complete`, which flips on load AND error, so a
   * 404/refusal/abort still has to clear `naturalWidth > 0`. */
  await page.waitForFunction(() => {
    const i = document.querySelector("article img");
    return !!i && i.complete;
  }, { timeout: 8000 }).catch(() => {});
  const hero = await page.evaluate(() => {
    const i = document.querySelector("article img");
    return i ? { src: i.getAttribute("src"), complete: i.complete, w: i.naturalWidth, h: i.naturalHeight } : null;
  });
  ok(hero && /^img\//.test(hero.src || ""),
     `the README hero should be remapped onto the site root (got ${hero && hero.src})`);
  /* `complete` IS PART OF THE ASSERTION: the wait above swallows expiry, and a PNG still on
   * the wire reports real dimensions from its IHDR header before its pixels arrive. */
  ok(hero && hero.complete && hero.w > 0 && hero.h > 0,
     `the README hero should actually decode (got ${JSON.stringify(hero)}; ` +
     `image responses: ${imgNet.join(" | ") || "NONE — no request was ever issued"})`);

  // 1b) the reverse-engineering section is sub-grouped by folder (Protocol / Runtime / Firmware / …)
  const subheads = await page.$$eval(".subhead", (els) => els.map((e) => e.textContent));
  ok(subheads.some((t) => /Protocol/.test(t)) && subheads.some((t) => /Runtime/.test(t)) &&
     subheads.some((t) => /Firmware/.test(t)) && subheads.some((t) => /Manifests/.test(t)),
     `tree should show folder sub-group headers incl. Manifests (got ${subheads.join(", ")})`);

  // 2) the topbar meta shows reading time + diagram count
  await page.goto(base + "/docs.html#reverse-engineering/architecture-diagrams.md", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => /diagram/.test((document.getElementById("docmeta") || {}).textContent || ""),
                             { timeout: 8000 }).catch(() => {});
  const meta = await page.evaluate(() => (document.getElementById("docmeta") || {}).textContent || "");
  ok(/~\d+ min/.test(meta) && /diagram/.test(meta), `topbar should show reading time + diagram count (got "${meta}")`);

  // 2b) linked non-.md manifests (.tsv/.dts) open in the explorer as a code block (not a 404)
  await page.goto(base + "/docs.html#reverse-engineering/firmware/manifests/init-services.tsv", { waitUntil: "domcontentloaded" });
  await page.waitForFunction('document.querySelectorAll("#content pre code").length>0', { timeout: 8000 }).catch(() => {});
  const manifest = await page.evaluate(() => { const c = document.querySelector("#content pre code"); return { hasCode: !!c, len: c ? c.textContent.length : 0 }; });
  ok(manifest.hasCode && manifest.len > 50, `linked .tsv manifest should render as a code block (got ${JSON.stringify(manifest)})`);

  // 3) code highlighting applies (hljs token spans)
  await page.goto(base + "/docs.html#reverse-engineering/hardware/hardware-map.md", { waitUntil: "domcontentloaded" });
  /* Wait for the highlight TOKENS, not the `<code>` container: highlight.js may finish later
   * on a slow runner; if it never happens the wait expires and the check fails. */
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
  // debounce + the lazy docs-search.json fetch: wait for the filter to land
  await page.waitForSelector("a.doc.hit", { timeout: 30000 }).catch(() => {});
  const hits = await page.$$eval("a.doc.hit", (els) => els.length).catch(() => 0);
  ok(hits > 0, "full-text search should filter the tree to matching docs");

  // 4b) search hits are ranked by relevance within a section: a proto message name
  //     should surface the doc that documents it ahead of the section README.
  await page.evaluate(() => { const q = document.getElementById("q"); q.value = ""; q.dispatchEvent(new Event("input")); });
  await page.waitForFunction(() => !document.querySelector("a.doc.hit"), { timeout: 5000 }).catch(() => {});
  await page.type("#q", "SystemVolumeModify");         // documented in runtime-control.md; README only lists it
  await page.waitForFunction(() => [...document.querySelectorAll("a.doc.hit")]
    .some((a) => /runtime-control\.md$/.test(a.dataset.path || "")), { timeout: 10000 }).catch(() => {});
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
  await page.waitForFunction(() => !document.querySelector("a.doc.hit"), { timeout: 5000 }).catch(() => {});
  await page.type("#q", "projectorfanpid");
  await page.waitForSelector("a.doc.hit", { timeout: 10000 }).catch(() => {});
  await page.evaluate(() => { const a = document.querySelector("a.doc.hit") || document.querySelector("a.doc"); a && a.click(); });
  await page.waitForFunction(() => !!document.querySelector("article mark.qmatch-first") &&
    document.getElementById("main").scrollTop > 30, { timeout: 8000 }).catch(() => {});
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
  await page.waitForFunction(() => !document.querySelector("a.doc.hit"), { timeout: 5000 }).catch(() => {});
  await page.keyboard.press("Slash");
  ok(await page.evaluate(() => document.activeElement && document.activeElement.id === "q"),
     '"/" should focus the search box');
  await page.evaluate(() => document.getElementById("q").blur());
  const beforeHash = await page.evaluate(() => location.hash);
  await page.keyboard.press("BracketRight");
  await page.waitForFunction((h) => location.hash !== h, { timeout: 5000 }, beforeHash).catch(() => {});
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
    await page.waitForFunction(() => /hardware-map/.test(location.hash) &&
      document.getElementById("main").scrollTop > 200, { timeout: 8000 }).catch(() => {});
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
  await page.waitForFunction('document.querySelectorAll("article h2[id]").length>0 && ' +
    'document.getElementById("main").scrollTop > 200', { timeout: 9000 }).catch(() => {});
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

  /* 10) EVERY link in EVERY doc goes where it says. The defect (production, 2026-10-08): docs
   * write links for GitHub, which resolves them against the doc's place in the repo; the
   * explorer resolved them against /docs.html, and Pages answers a path with no file with
   * the hub. 681 of 2,644 links across 87 of 145 docs, and every "source" link, landed there
   * (32 of them on docs the bundle HAS: the README's own links into docs/), and 30 links meant
   * for the project README (`../../README.md`) opened the docs index instead.
   * Each doc is rendered by the explorer, and its links are paired, in order, with the hrefs
   * the same Markdown yields through the page's own `marked` (parsed inert, so nothing loads).
   * Every relative href is resolved against the doc's REPO path, the way GitHub resolves it,
   * and must arrive: a bundled doc routes to exactly that doc (and heading); anything else
   * goes to GitHub at that same path, or, for the few repo paths that ARE pages of this site
   * (`sim/web/**`, the site root; the simulator's README), to that page. An in-page
   * "#heading" keeps its doc in the URL. Never a same-origin path the site does not serve. */
  const idx = JSON.parse(readFileSync(join(web, "docs-index.json"), "utf8"));
  const docPaths = new Set(idx.files.map((f) => f.path));
  const repoOf = (p) => (p.startsWith("_root/") ? p.slice(6) : "docs/" + p);
  const bundleOf = (repo) => { const p = repo.startsWith("docs/") ? repo.slice(5) : "_root/" + repo; return docPaths.has(p) ? p : null; };
  /* What Pages serves for a path (a directory's index.html; extensionless -> .html). */
  const served = (pathname) => {
    let p = decodeURIComponent(pathname);
    if (p.endsWith("/")) p += "index.html";
    const f = join(web, posix.normalize(p));
    return (existsSync(f) && statSync(f).isFile()) || (!extname(p) && existsSync(f + ".html"));
  };
  const sitePath = (repo, dir) => repo === "sim/README.md" ? "/sim"
    : (repo === "sim/web" && dir) ? "/" : (/^sim\/web\/.+\.html$/.test(repo) ? "/" + repo.slice(8) : null);
  const norm = (u) => new URL(u, base + "/docs.html").href;
  await page.goto(base + "/docs.html", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("a.doc", { timeout: 8000 }).catch(() => {});
  await page.waitForSelector("article h1, article h2", { timeout: 8000 }).catch(() => {});
  const REPO = await page.evaluate(() => {
    const t = document.getElementById("docfoot");
    const a = t && t.content.querySelector('a[href^="https://github.com/"]');
    return a ? a.getAttribute("href").replace(/\/$/, "") : null;
  });
  ok(!!REPO, "docs.html's footer template should link the repo on GitHub — docs.js reads the repo from it");
  const wrong = [], wrongSrc = [];
  let swept = 0, pairs = 0, inPage = null;
  for (const f of idx.files.filter((x) => x.kind === "md")) {
    await page.evaluate(() => { const a = document.querySelector("#content article"); if (a) a.dataset.stale = "1"; });
    const opened = await page.evaluate((p) => {
      const a = document.querySelector('a.doc[data-path="' + CSS.escape(p) + '"]');
      if (a) a.click();
      return !!a;
    }, f.path);
    if (!opened) { wrong.push(`${f.path}: not in the tree`); continue; }
    const rendered = await page.waitForFunction((p) => {
      const a = document.querySelector("#content article");
      return !!a && !a.dataset.stale && location.hash.startsWith("#" + p);
    }, { timeout: 15000 }, f.path).then(() => true, () => false);
    if (!rendered) { wrong.push(`${f.path}: never rendered`); continue; }
    const got = await page.evaluate(async (p) => {
      const keep = (a) => !a.classList.contains("hlink") && !a.closest(".pager, .docfoot, .mermaid, svg");
      /* How far down the reading pane an in-page "#id" lands (its id, or a heading whose slug
       * it is), or -1 when it has nowhere to land. */
      const main = document.getElementById("main");
      const depth = (id) => {
        const el = document.getElementById(id) ||
          [...document.querySelectorAll("#content article h1, #content article h2, #content article h3, #content article h4")]
            .find((h) => h.textContent.toLowerCase().replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").replace(/-+/g, "-") === id);
        return el ? el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop : -1;
      };
      const links = [...document.querySelectorAll("#content article a[href]")].filter(keep).map((a) => {
        const attr = a.getAttribute("href"), own = attr.indexOf("#" + p + "#") === 0;
        return { href: a.href, attr, text: a.textContent.trim().slice(0, 40),
                 depth: own ? depth(decodeURIComponent(attr.slice(p.length + 2))) : -1 };
      });
      const md = await (await fetch("docs-bundle/" + encodeURI(p))).text();
      const inert = new DOMParser().parseFromString(marked.parse(md), "text/html");
      return { links, original: [...inert.querySelectorAll("a[href]")].map((a) => a.getAttribute("href")),
               src: document.getElementById("src").href };
    }, f.path);
    swept++;
    if (got.src !== `${REPO}/blob/main/${repoOf(f.path)}`) wrongSrc.push(`${f.path}: ${got.src}`);
    if (got.links.length !== got.original.length) {
      wrong.push(`${f.path}: ${got.original.length} links in its Markdown, ${got.links.length} rendered`);
      continue;
    }
    got.original.forEach((orig, i) => {
      const r = got.links[i], bad = (why) => wrong.push(`${f.path} [${r.text}](${orig}) -> ${r.attr}: ${why}`);
      pairs++;
      if (/^(https?:|mailto:)/i.test(orig)) { if (r.attr !== orig) bad("an absolute link was rewritten"); return; }
      if (/^#./.test(orig)) {
        if (r.href !== norm(`#${f.path}${orig}`)) bad("an in-page heading link should keep its doc in the URL");
        else if (!inPage && r.depth > 1800) inPage = { doc: f.path, attr: r.attr };   // far below the fold
        return;
      }
      if (!orig || orig === "#" || /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(orig)) return;
      const [, rel, hash = ""] = orig.match(/^([^#]*)(#.*)?$/);
      const dir = rel.endsWith("/");
      const repo = posix.join(rel.startsWith("/") ? "/" : "/" + posix.dirname(repoOf(f.path)), rel)
        .replace(/^\/+/, "").replace(/\/+$/, "");
      const doc = bundleOf(repo);
      if (doc) { if (r.href !== norm(`#${doc}${hash}`)) bad(`should open ${doc}${hash} here`); return; }
      const github = `${REPO}/${dir ? "tree" : "blob"}/main/${repo}${hash}`, site = sitePath(repo, dir);
      const u = new URL(r.href);
      if (r.href === norm(github)) return;
      if (site && u.origin === base && u.pathname === site && served(u.pathname)) return;
      bad(u.origin === base && !served(u.pathname)
        ? `lands on ${u.pathname}, which this site does not serve (Pages answers with the hub)`
        : `should open ${github}${site ? ` or this site's ${site}` : ""}`);
    });
    if (f.mermaid) {      // let this doc's diagrams finish, so two docs' renders never overlap
      await page.waitForFunction((n) => {
        const blocks = document.querySelectorAll("#content article .mermaid");
        return blocks.length >= n && [...blocks].every((b) => b.querySelector("svg, .err"));
      }, { timeout: 20000 }, f.mermaid).catch(() => {});
    }
  }
  console.log(`   (link sweep: ${swept} docs, ${pairs} links, ${wrong.length} wrong, ` +
              `${wrongSrc.length} wrong "source" links)`);
  ok(swept === idx.files.filter((x) => x.kind === "md").length && pairs > 2000,
     `the link sweep should cover every doc (swept ${swept} docs, ${pairs} links)`);
  ok(wrong.length === 0,
     `${wrong.length} doc link(s) do not go where they say:\n      ${wrong.slice(0, 12).join("\n      ")}`);
  ok(wrongSrc.length === 0,
     `every doc's "source" link should be its file on GitHub (${wrongSrc.length} not: ${wrongSrc.slice(0, 3).join(" | ")})`);

  /* 11) The instances the plan named, by their words. "simulator" in the revival guide OPENS
   * /sim: the click is followed to the request it makes (answered with a stub by the
   * interceptor above, so the sim never loads). */
  await page.goto(base + "/docs.html#guides/revive-your-moxie.md", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => /Revive your Moxie/.test((document.querySelector("article h1") || {}).textContent || ""),
                             { timeout: 8000 }).catch(() => {});
  const byText = (t) => page.evaluate((s) => {
    const a = [...document.querySelectorAll("#content article a[href]")].find((x) => x.textContent.trim() === s);
    return a ? { href: a.href, target: a.target } : null;
  }, t);
  const setup = await byText("setup page"), sim = await byText("simulator");
  ok(setup && setup.href === base + "/setup.html", `the revival guide's "setup page" should open setup.html (got ${JSON.stringify(setup)})`);
  ok(sim && sim.href === base + "/sim", `the revival guide's "simulator" should open /sim (got ${JSON.stringify(sim)})`);
  const leftBefore = left.length;
  await Promise.all([
    page.waitForNavigation({ timeout: 8000 }).catch(() => {}),
    page.evaluate(() => {
      const a = [...document.querySelectorAll("#content article a[href]")].find((x) => x.textContent.trim() === "simulator");
      if (a) a.click();
    }),
  ]);
  ok(left.slice(leftBefore).includes(base + "/sim") && page.url() === base + "/sim",
     `clicking "simulator" should take the reader to /sim (requests ${JSON.stringify(left.slice(leftBefore))}, now at ${page.url()})`);

  // STRUCTURE.md and LICENSE in the README are not in the bundle: they open on GitHub.
  await page.goto(base + "/docs.html#_root/README.md", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#content article h1, #content article h2", { timeout: 8000 }).catch(() => {});
  for (const name of ["STRUCTURE.md", "LICENSE"]) {
    const l = await byText(name);
    ok(l && l.href === `${REPO}/blob/main/${name}` && l.target === "_blank",
       `the README's ${name} link should open ${REPO}/blob/main/${name} in a new tab (got ${JSON.stringify(l)})`);
  }

  // An in-page heading link, clicked, scrolls to its heading AND leaves the doc in the URL,
  // so a reload lands on the same doc (it used to land on the home doc). The link is one the
  // sweep found whose heading starts far below the fold, so no scroll means no pass.
  ok(!!inPage, "the sweep should have found an in-page heading link far down its doc to click");
  if (inPage) {
    await page.goto(base + "/docs.html#" + inPage.doc, { waitUntil: "domcontentloaded" });
    await page.waitForFunction((s) => !!document.querySelector(`#content article a[href="${s}"]`), { timeout: 8000 }, inPage.attr)
      .catch(() => {});
    await page.evaluate((s) => { const a = document.querySelector(`#content article a[href="${s}"]`); if (a) a.click(); }, inPage.attr);
    const target = decodeURIComponent(inPage.attr.slice(inPage.attr.indexOf("#", 1) + 1));
    /* Landed = the hash names the doc AND the heading, and the heading sits at the top of the
     * reading pane (or the pane is scrolled to its end, for a heading too low to reach it). */
    const landed = await page.waitForFunction((h, id) => {
      if (location.hash !== h) return false;
      const el = document.getElementById(id) || [...document.querySelectorAll("#content article h1, #content article h2, #content article h3, #content article h4")]
        .find((x) => x.textContent.toLowerCase().replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").replace(/-+/g, "-") === id);
      const main = document.getElementById("main");
      if (!el || main.scrollTop < 1) return false;
      const top = el.getBoundingClientRect().top - main.getBoundingClientRect().top;
      return top > -5 && (top < 150 || main.scrollTop + main.clientHeight >= main.scrollHeight - 2);
    }, { timeout: 8000 }, inPage.attr, target).then(() => true, () => false);
    ok(landed, `clicking ${inPage.attr} in ${inPage.doc} should scroll to its heading with the doc kept in the URL ` +
               `(hash ${await page.evaluate(() => location.hash)})`);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction((p) => !!document.querySelector(`a.doc.active[data-path="${p}"]`), { timeout: 8000 }, inPage.doc)
      .catch(() => {});
    const after = await page.evaluate(() => { const a = document.querySelector("a.doc.active"); return a ? a.dataset.path : null; });
    ok(after === inPage.doc, `a reload after that click should reopen ${inPage.doc} (got ${after})`);
  }

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
  /* Stronger than "cannot depend on the network": the explorer must not REACH for it at all.
   * A doc that grows a remote asset fails here instead of being forgiven. */
  ok(blocked.n === 0,
     `the docs explorer should make ZERO off-origin requests (blocked ${blocked.n}: ${blocked.urls.join(", ")})`);
} finally {
  await browser.close();
  site.close();
}

finish(LABEL, { fails, count });
