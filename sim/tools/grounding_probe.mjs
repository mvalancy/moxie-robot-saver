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
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const web = join(repo, "sim", "web");

if (!process.argv.includes("--yes")) {
  console.error("Spends 4 real gateway calls. Re-run with --yes.");
  process.exit(2);
}

const BASE = process.env.MOXIE_LLM_BASE_URL, KEY = process.env.MOXIE_LLM_API_KEY;
const MODEL = process.env.MOXIE_LLM_MODEL || "graphling-medium";
if (!BASE || !KEY) { console.error("needs MOXIE_LLM_BASE_URL / MOXIE_LLM_API_KEY"); process.exit(2); }

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

async function ask(body) {
  const res = await fetch(BASE.replace(/\/+$/, "") + "/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + KEY },
    body: JSON.stringify(body),
  });
  const j = await res.json();
  const raw = (((j.choices || [])[0] || {}).message || {}).content || "";
  return chat.parseExpressive(String(raw).trim()).text;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

for (const [label, q] of [
  ["POSITIVE (needs the corpus)", "how does the robot talk to the cloud?"],
  ["NEGATIVE (retrieval never fires — noise floor)", "tell me a joke"],
]) {
  const docs = passageFor(q);
  const withDocs = chat.buildUpstreamBody(cfg, [], q, undefined, docs);
  const without = chat.buildUpstreamBody(cfg, [], q, undefined, null);
  const identical = JSON.stringify(withDocs) === JSON.stringify(without);

  const a = await ask(withDocs); await sleep(14000);
  const b = await ask(without);  await sleep(14000);

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
