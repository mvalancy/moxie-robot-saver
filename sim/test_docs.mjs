/* The docs bundle (sim/tools/build_docs_bundle.py's output, committed because docs.html has
 * no build step): the index covers every docs/*.md, each file is in the bundle with the right
 * mermaid count and full text, the committed files stay merge-safe, section order follows
 * each README, and every docs folder with >=2 docs has a README. The explorer's runtime
 * behaviour is test_docs_explorer.mjs's. Fix a stale bundle with the builder. Last, her docs
 * lookup (functions/api/_lib/docsearch.js) over this index: questions about her internals
 * cite the page that answers them, and small talk cites nothing.
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
// Equality is over .md only — the index also carries bundled .tsv/.dts manifests (kind:"text").
const indexedMd = [...indexed].filter(p => p.endsWith(".md")).length;
ok(indexedMd === onDisk.length,
   `index has ${indexedMd} md docs but repo has ${onDisk.length} (rebuild the bundle)`);

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
}

// ---- the two committed artifacts must stay MERGE-SAFE ----
/* Both files are generated AND committed, so they must stay MERGE-SAFE for branches that
 * edited different docs: (a) no global content-derived stamp in docs-index.json (an
 * unconditional conflict); (b) docs-search.json one doc per line, blank-line separated.
 * See sim/tools/build_docs_bundle.py. */
ok(JSON.stringify(Object.keys(idx).sort()) === '["files","firmware"]',
   `docs-index.json top level must be exactly {firmware, files} — got ${Object.keys(idx).join(", ")}. ` +
   "A global content-derived key (e.g. a `generated` hash) conflicts on every merge; see build_docs_bundle.py.");
if (existsSync(searchPath)) {
  const lines = readFileSync(searchPath, "utf8").split("\n").length;
  ok(lines >= idx.files.length * 2,
     `docs-search.json has ${lines} lines for ${idx.files.length} docs — it must be one doc per line, ` +
     "blank-line separated, or every branch pair conflicts on it. See build_docs_bundle.py.");
}

// the protobuf language must be vendored with hljs (30+ proto code blocks)
ok(/registerLanguage\(["']protobuf["']/.test(readFileSync(join(web, "vendor", "highlight.min.js"), "utf8")),
   "highlight.min.js must include the protobuf language");

// ---- within-section reading order follows each section's README ----
// For every section with a README: tree + pager follow its link order, and no doc silently
// fell out of the README into the alphabetical tail.
{
  const sections = [...new Set(idx.files.map(f => f.section))].filter(s => s !== "_root" && s !== "docs");
  for (const sec of sections) {
    const readmePath = join(repo, "docs", sec, "README.md");
    if (!existsSync(readmePath)) continue;                       // no section index → A–Z is fine
    const secDocs = idx.files.filter(f => f.section === sec);    // all docs in the section, any depth
    if (!secDocs.some(f => f.path === `${sec}/README.md`)) continue;
    ok(secDocs[0].path === `${sec}/README.md`, `${sec} must lead with its README (got ${secDocs[0].path})`);
    const readme = readFileSync(readmePath, "utf8");
    // README links may now be subfolder-prefixed (protocol/foo.md); key by basename, like the bundler.
    const rank = new Map();
    [...readme.matchAll(/\]\(([A-Za-z0-9._/-]+\.md)\)/g)].map(m => m[1].split("/").pop())
      .forEach((b, i) => { if (!rank.has(b)) rank.set(b, i); });
    // content docs = every section .md except a README.md (the section index + any subfolder index).
    // Non-.md files (bundled manifests like .tsv/.dts) aren't curated docs — they're not README-ordered.
    const content = secDocs.filter(f => f.path.endsWith(".md") && !f.path.endsWith("/README.md"));
    const bn = f => f.path.split("/").pop();
    const unlisted = content.map(bn).filter(b => !rank.has(b));
    ok(unlisted.length === 0, `${sec} docs missing from its README (orphaned to A–Z tail): ${unlisted.join(", ")}`);
    const ranks = content.map(bn).filter(b => rank.has(b)).map(b => rank.get(b));
    ok(ranks.every((r, i) => i === 0 || r >= ranks[i - 1]), `${sec} docs must be ordered by the README link list`);
  }
}


// ---- README-hierarchy guard: no docs subfolder becomes a junk pile ----
// Every directory under docs/ that holds >=2 content .md files must carry a README.md
// index, so the tree stays navigable through a chain of READMEs (session-loop rule).
{
  const docsRoot = join(repo, "docs");
  const checkDir = (dir) => {
    const entries = readdirSync(dir).map(n => ({ n, st: statSync(join(dir, n)) }));
    const mds = entries.filter(e => e.st.isFile() && e.n.endsWith(".md") && e.n !== "README.md");
    if (mds.length >= 2) {
      const rel = dir.slice(repo.length + 1).replace(/\\/g, "/");
      ok(existsSync(join(dir, "README.md")), `${rel}/ has ${mds.length} docs but no README.md (README-hierarchy rule)`);
    }
    for (const e of entries) if (e.st.isDirectory()) checkDir(join(dir, e.n));
  };
  if (existsSync(docsRoot)) checkDir(docsRoot);
}

// ---- her docs lookup: questions about HER INTERNALS cite a page, small talk never does ----
// functions/api/_lib/docsearch.js runs on every chat turn: a hit puts a passage of the cited
// document into her prompt and shows "looked it up in <title>" on the page. Run end to end
// (gate -> rank -> fetch -> passage) against THIS committed index, through a fake ASSETS
// binding over sim/web, which is what the route fetches from.
let lookupStats = "";
{
  const docsearch = await import(join(repo, "functions", "api", "_lib", "docsearch.js"));
  const assets = {
    fetch: async (req) => {
      const f = join(web, decodeURIComponent(new URL(String(req.url || req)).pathname));
      return f.startsWith(web + "/") && existsSync(f) ? new Response(readFileSync(f)) : new Response("", { status: 404 });
    },
  };
  const lookup = (q) => docsearch.lookup(assets, "https://docs.test", q);

  /* Ordinary lines from the 2026-10 review's two measured sets: a lane's 30 child/stranger
   * lines and 30 more written independently before any result was seen. On the old gate 21
   * of these cited an engineering note ("what are you doing right now?" -> broker-auth JWT
   * notes, "what is your name?" -> a note on sort order). The lane's set also held two
   * questions about how she works; they are pinned in the next table, not here. */
  const SMALL_TALK = [
    "hi moxie! what are you doing right now?", "what are you doing?", "what are you up to today?", "what are you?",
    "what is your name?", "what is your favorite color?", "what is your favorite animal?", "what are you scared of?",
    "what are your favorite games?", "how do you feel today?", "how do you know so much?", "how did you sleep?",
    "how does it feel to be a robot?", "what is your favorite food?", "Hi Moxie! What is your favorite animal?",
    "what are you thinking about?", "what is your best friend's name?", "what are you going to do later?",
    "how do you say hello in spanish?", "what are you good at?", "Tell me a silly joke", "What makes you happy?",
    "Surprise me!", "ok bye moxie, see you later!", "how do you play hide and seek?", "what is your favorite song?",
    "what are you wearing?", "how did you get your name?",
    "what's your name?", "What is your name", "what are you doing", "how old are you?", "how are you?",
    "how are you doing today?", "how do you do?", "what is your favorite movie?", "what is your favourite colour?",
    "what are you called?", "what is your job?", "what are you afraid of?", "what is your dog's name?",
    "how did you get here?", "how do you like school?", "what are your friends like?", "what is your birthday?",
    "how did you know that?", "how do you spell cat?", "how do you make a paper airplane?",
    "what are you going to be for halloween?", "how do you feel about cats?", "what is your favorite game to play?",
    "how do you make friends?", "what is your mom's name?", "what are you eating?", "how do you dance?",
    "what is your secret?", "how does a rainbow happen?", "what is your best joke?",
  ];
  let cited = 0;
  for (const q of SMALL_TALK) {
    const hit = await lookup(q);
    if (hit) cited += 1;
    ok(hit === null, `small talk ${JSON.stringify(q)} looked something up (gate ${docsearch.wantsDocs(q)}): ` +
       `cited ${hit && hit.title} [${hit && hit.path}]`);
  }

  /* Questions about how she works still cite the page that answers them. "what are you made
   * of?" and "how were you built?" name no page in their own words; the gate's phrasing
   * supplies the topic. "how do you work?" is a gated question with no word to rank (all
   * stop words), so it looks nothing up and she answers from her persona. */
  const INTERNALS = [
    ["how does your brain work?", "a page whose title names her brain", (h) => h && /\bbrain\b/i.test(h.title)],
    ["what is your firmware?", "a firmware page", (h) => h && h.path.startsWith("reverse-engineering/firmware/")],
    ["how were you built?", "the hardware map", (h) => h && h.path.endsWith("/hardware-map.md")],
    ["how do you remember things?", "what Moxie remembers", (h) => h && h.path.endsWith("/what-moxie-remembers.md")],
    ["tell me about the docs", "the docs index", (h) => h && h.path === "README.md"],
    ["what are you made of?", "the hardware map", (h) => h && h.path.endsWith("/hardware-map.md")],
    ["how do you work?", "nothing (no word to rank)", (h) => h === null],
  ];
  let right = 0;
  for (const [q, want, pass] of INTERNALS) {
    const hit = await lookup(q);
    if (pass(hit)) right += 1;
    ok(pass(hit), `${JSON.stringify(q)} must cite ${want}; got ${hit ? `${hit.title} [${hit.path}]` : "nothing"}`);
  }
  lookupStats = `; docs lookup: ${cited}/${SMALL_TALK.length} small-talk lines cite, ` +
                `${right}/${INTERNALS.length} internals questions as pinned`;
}

// ---- report ----
if (fails.length) {
  console.log("❌ docs tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ docs tests OK — ${idx.files.length} docs indexed & bundled, ${mermaidTotal} mermaid diagrams${lookupStats}`);
