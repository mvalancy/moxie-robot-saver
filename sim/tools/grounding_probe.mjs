/* grounding_probe.mjs — does the retrieved passage change what she says?
 *
 * ============================================================================
 * THE INSTRUMENT THE LEXICAL ONE COULD NOT BE.
 *
 * `functions/api/_lib/docsearch.js` records why scoring a SINGLE answer fails here: every
 * document is about one system, so the terms that prove she read something are the common
 * ones, and only jargon the persona forbids is rare. No threshold separates them.
 *
 * The signal is not the vocabulary of one answer. It is THE DIFFERENCE A PASSAGE MAKES —
 * which needs the same question asked twice, once with retrieval suppressed.
 *
 *   arm A — the prompt production builds, WITH the retrieved passage
 *   arm B — byte-identical, WITHOUT it
 *
 *   grounded := some term is in A, not in B, not in the question, and IS in the passage.
 *
 * CONFOUND, NAMED BEFORE THE NUMBERS: temperature is 0.8, so A and B differ by sampling as
 * well as by the passage. That is exactly why the last clause is "in the passage" — noise
 * invents words, but not words that happen to be in the document she was shown. Passage
 * membership is what separates signal from sampling, and the negative control below
 * measures the noise floor directly: when retrieval does not fire, the two prompts are
 * IDENTICAL, so anything this reports there is pure sampling and must be zero.
 *
 * SUPPRESSION HAS NO PRODUCTION SURFACE, and that is a design decision rather than an
 * omission. There is no flag on `/api/chat`, no env var, nothing a visitor could set and
 * nothing that could leak into a live turn — because this file builds both prompts itself
 * with `buildUpstreamBody` and calls the gateway directly, exactly as the route does.
 * The route is unchanged. A measurement that required a switch in the serving path would
 * be a worse trade than one that reproduces the path.
 *
 * VALIDATED IN BOTH DIRECTIONS, which is what the lexical attempt could not manage:
 *   · noise floor ZERO — identical prompts, pure sampling, no false positive;
 *   · mechanism FIRES — an answer carrying passage content scores GROUNDED.
 *
 * KNOWN FALSE-POSITIVE CHANNEL, found while validating and left un-tuned. A common word
 * that happens to be in the passage ("that", "tells", "which") satisfies all four clauses
 * by chance, so a single shared term is weaker evidence than it looks. The obvious fix —
 * ignore common words — is the hand-written stop list this whole line of work exists to
 * avoid, and requiring N>1 shared terms would be a threshold moved after seeing results.
 * So the discriminator stands as stated and the weakness is recorded instead. Read a
 * GROUNDED verdict by looking at WHICH terms fired, which is why they are printed.
 *
 *   node sim/tools/grounding_probe.mjs --yes        # 4 gateway calls
 * ============================================================================
 */
import { readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const web = join(repo, "sim", "web");

if (!process.argv.includes("--yes")) {
  console.error("Spends 4 real gateway calls. Re-run with --yes.");
  process.exit(2);
}

/* CREDENTIALS COME FROM `mqtt/.env` THE WAY EVERY OTHER LIVE TEST HERE GETS THEM.
 *
 * This read only `process.env`, so running it without hand-sourcing the file printed
 * "needs MOXIE_LLM_BASE_URL / MOXIE_LLM_API_KEY" — and I read that as "this environment has
 * no credentials" and reported the gate as unrunnable. They were in `mqtt/.env` the whole
 * time, which is exactly where `sim/tests/helpers_runtime.py:load_repo_dotenv` looks: this
 * tree's copy first, then the MAIN worktree's, since the file is git-ignored and a linked
 * worktree never gets one. Every `test_live_*` had been finding it automatically; this
 * probe was the one instrument that could not, so its absence looked like the repo's.
 *
 * Mirrors the Python loader deliberately, including the two rules that matter: only the
 * live-tier keys cross, and THE EXISTING ENVIRONMENT WINS, so an explicit override on the
 * command line still beats the file. Values are never printed. */
function loadRepoDotenv() {
  const roots = [repo];
  try {                                   // a linked worktree's `.git` is a file, not a dir
    const dotgit = join(repo, ".git");
    if (statSync(dotgit).isFile()) {
      const m = /gitdir:\s*(.+)/.exec(readFileSync(dotgit, "utf8"));
      if (m) roots.push(dirname(dirname(dirname(m[1].trim()))));   // …/.git/worktrees/<n>
    }
  } catch { /* not a worktree, or unreadable — the main-tree fallback is a bonus */ }
  const ALLOW = new Set(["MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY", "MOXIE_LLM_BASE_URL",
                         "MOXIE_LLM_MODEL"]);
  for (const root of roots) {
    let body;
    try { body = readFileSync(join(root, "mqtt", ".env"), "utf8"); } catch { continue; }
    for (const line of body.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#") || !t.includes("=")) continue;
      const i = t.indexOf("="), k = t.slice(0, i).trim(), v = t.slice(i + 1).trim();
      if (ALLOW.has(k) && process.env[k] === undefined) process.env[k] = v;   // setdefault
    }
    return join(root, "mqtt", ".env");
  }
  return null;
}
const dotenvPath = loadRepoDotenv();

const BASE = process.env.MOXIE_LLM_BASE_URL;
const KEY = process.env.MOXIE_LLM_API_KEY || process.env.LITELLM_MASTER_KEY;
const MODEL = process.env.MOXIE_LLM_MODEL || "graphling-medium";
if (!BASE || !KEY) {
  console.error("needs MOXIE_LLM_BASE_URL / MOXIE_LLM_API_KEY — set them in mqtt/.env " +
                `(searched: ${dotenvPath || "no mqtt/.env in this tree or the main worktree"})`);
  process.exit(2);
}

const env = await import(join(repo, "functions/api/_lib/env.js"));
const chat = await import(join(repo, "functions/api/chat.js"));
const docsearch = await import(join(repo, "functions/api/_lib/docsearch.js"));

const cfg = env.readConfig({ DEMO_GATEWAY_BASE_URL: BASE, DEMO_GATEWAY_API_KEY: KEY,
                             DEMO_CHAT_MODEL: MODEL });
const index = JSON.parse(readFileSync(join(web, "docs-index.json"), "utf8"));

const tok = (s) => new Set(String(s || "").toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3));

/** The passage production would retrieve for this question, recomputed locally — the same
 *  pure functions the route calls, so arm A is the real prompt and not an approximation. */
function passageFor(q) {
  if (!docsearch.wantsDocs(q)) return null;
  const top = docsearch.rank(index, q)[0];
  if (!top) return null;
  const excerpt = docsearch.bestPassage(readFileSync(join(web, "docs-bundle", top.path), "utf8"), q);
  return excerpt ? { title: top.title, path: top.path, excerpt } : null;
}

/* AN INSTRUMENT THAT CANNOT FAIL LOUDLY IS A FALSE-NEGATIVE GENERATOR, and this one could
 * not. There was no `res.ok` check here: an HTTP error has no `choices`, so `raw` fell back
 * to `""`, an empty answer shares no terms with anything, and the probe printed
 * `VERDICT: not grounded` — the same words it prints for a real measured failure.
 *
 * That is the worst shape a test can take, because it is WRONG IN THE DIRECTION OF THE
 * HYPOTHESIS: a broken call looks exactly like "retrieval did not help her". Caught when a
 * run printed two empty answers, which is not a result but an outage — the gateway was
 * returning `503 no_db_connection`. Had both answers been merely SHORT rather than empty I
 * might have read the verdict and believed it.
 *
 * So: a non-2xx throws, and an empty answer after a 2xx throws too, since there is no
 * question a working model answers with nothing. Transient statuses are retried rather than
 * reported, because an outage is not evidence about grounding either way. */
const TRANSIENT = new Set([408, 429, 500, 502, 503, 504]);
const scrub = (t) => (KEY ? String(t).split(KEY).join("[REDACTED]") : String(t));

async function ask(body, attempt = 1) {
  const res = await fetch(BASE.replace(/\/+$/, "") + "/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + KEY },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = scrub(await res.text().catch(() => "")).slice(0, 200);
    if (TRANSIENT.has(res.status) && attempt < 4) {
      const wait = 15000 * attempt;
      console.log(`   … HTTP ${res.status} (transient), retry ${attempt}/3 in ${wait / 1000}s`);
      await new Promise((r) => setTimeout(r, wait));
      return ask(body, attempt + 1);
    }
    throw new Error(`gateway HTTP ${res.status} — ${detail}`);
  }
  const j = await res.json();
  const raw = (((j.choices || [])[0] || {}).message || {}).content || "";
  const text = chat.parseExpressive(String(raw).trim()).text;
  if (!text) {
    throw new Error(`gateway returned 200 with an EMPTY answer (raw ${JSON.stringify(String(raw).slice(0, 80))}) ` +
                    `— that is an instrument failure, not a verdict`);
  }
  return text;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failed = false;
for (const [label, q] of [
  ["POSITIVE (needs the corpus)", "how does the robot talk to the cloud?"],
  ["NEGATIVE (retrieval never fires — noise floor)", "tell me a joke"],
]) {
  const docs = passageFor(q);
  const withDocs = chat.buildUpstreamBody(cfg, [], q, undefined, docs);
  const without = chat.buildUpstreamBody(cfg, [], q, undefined, null);
  const identical = JSON.stringify(withDocs) === JSON.stringify(without);

  let a, b;
  try {
    a = await ask(withDocs); await sleep(14000);
    b = await ask(without);  await sleep(14000);
  } catch (err) {
    console.log(`\n── ${label}`);
    console.log(`   UNUSABLE — no verdict was produced: ${err.message}`);
    console.log(`   (this is NOT "not grounded"; the measurement did not happen)`);
    failed = true;
    continue;
  }

  const qa = tok(q), tb = tok(b), pass = tok(docs ? docs.excerpt : "");
  const only = [...tok(a)].filter((t) => !tb.has(t) && !qa.has(t));
  const fromPassage = only.filter((t) => pass.has(t));

  console.log(`\n── ${label}`);
  console.log(`   passage      : ${docs ? docs.path : "(none — retrieval did not fire)"}`);
  console.log(`   prompts identical: ${identical}${identical ? "  ← any difference below is pure sampling" : ""}`);
  console.log(`   A (with)     : ${a.slice(0, 120)}`);
  console.log(`   B (without)  : ${b.slice(0, 120)}`);
  console.log(`   new in A     : ${only.slice(0, 10).join(", ") || "(none)"}`);
  console.log(`   …from passage: ${fromPassage.join(", ") || "(none)"}`);
  console.log(`   VERDICT      : ${fromPassage.length ? "GROUNDED" : "not grounded"}`);
}

if (failed) {
  console.log("\nAT LEAST ONE SCENARIO WAS UNUSABLE — exit 1 so a broken run cannot be read as a result.");
  process.exit(1);
}
