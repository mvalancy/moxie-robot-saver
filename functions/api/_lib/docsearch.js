/* functions/api/_lib/docsearch.js — Moxie can look things up in her own documentation.
 *
 * The deployment already serves its markdown docs under `/docs-bundle/` with an index at
 * `/docs-index.json`; nothing new is exposed here.
 *
 * RETRIEVAL RUNS ON THE SERVER, AS A SECURITY DECISION: a browser-supplied passage would be
 * a prompt-injection channel straight into a system message. The visitor's words only
 * CHOOSE a document; the text reaching the model is always committed bytes fetched from
 * our own origin through the `ASSETS` binding.
 *
 * TWO ASSET FETCHES, never the 3 MB full-text object: rank on the ~112 KB index, then fetch
 * only the winning document. Asset fetches never touch the gateway.
 *
 * FAILS OPEN: every error returns `null` ("no excerpt") and the turn proceeds unchanged.
 *
 * Measuring whether she USED the passage cannot be done lexically on one answer (every doc
 * is about one system, so the rare terms are exactly the jargon the persona forbids); it
 * needs a control arm — `sim/tools/grounding_probe.mjs`, which builds both prompts via
 * `buildUpstreamBody` without any switch in this file.
 */

/** Words too common to discriminate between documents about one robot (`moxie` matches
 *  almost all of them). Deliberately tiny: an aggressive list drops the words that pick a
 *  document ("wifi", "audio", "protocol"). */
const STOP = new Set([
  "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "how", "what", "why",
  "when", "who", "your", "you", "yours", "me", "my", "i", "it", "its", "of", "to", "in",
  "on", "for", "and", "or", "can", "could", "would", "tell", "about", "explain", "moxie",
  "robot", "please", "know", "work", "works",
]);

/** Query -> the terms worth scoring: lower-cased, de-punctuated, stop words and anything
 *  under three characters dropped. */
export function terms(query) {
  const out = [];
  for (const w of String(query || "").toLowerCase().split(/[^a-z0-9]+/)) {
    if (w.length >= 3 && !STOP.has(w)) out.push(w);
  }
  return [...new Set(out)];
}

/**
 * Rank the index against a query. Pure, so it is tested on real fixtures.
 *
 * Weights: TITLE (says what the document is) > HEADING (it has a section about the thing)
 * > PATH (catches `firmware/`, `protocol/`, weakest so a directory cannot outvote a
 * document about the subject). A zero score is never returned: "found nothing" beats the
 * least-bad unrelated document.
 */
export function rank(index, query) {
  const want = terms(query);
  if (!want.length) return [];
  const files = index && Array.isArray(index.files) ? index.files : [];
  const scored = [];
  for (const f of files) {
    const title = String((f && f.title) || "").toLowerCase();
    const path = String((f && f.path) || "").toLowerCase();
    const heads = (f && Array.isArray(f.headings) ? f.headings : []).join(" ").toLowerCase();
    let score = 0;
    for (const t of want) {
      if (title.includes(t)) score += 6;
      if (heads.includes(t)) score += 3;
      if (path.includes(t)) score += 1;
    }
    if (score > 0) scored.push({ path: f.path, title: f.title, score });
  }
  // Ties broken by path: the same question must always cite the same document.
  scored.sort((a, b) => b.score - a.score || String(a.path).localeCompare(String(b.path)));
  return scored;
}

/** How much of a document may reach the prompt. Sized to the deployed model's 2,048-token
 *  window: 900 characters overflowed it (every docs question refused); 320 fits and still
 *  carries the fact the answer needs. */
const MAX_EXCERPT = 320;

/**
 * The most relevant passage of a markdown document, as plain-ish text.
 *
 * Paragraph-scored (a lone sentence loses its subject), with markdown furniture stripped
 * because the result is paraphrased aloud to a child.
 */
export function bestPassage(markdown, query) {
  const want = terms(query);
  const text = String(markdown || "");
  if (!text) return "";
  const blocks = text.split(/\n\s*\n/);
  let best = "", bestScore = 0;
  /* The heading a paragraph lives under, tracked BEFORE the length filter (real headings
   * are short, so a check after `length < 60` would only ever see freakishly long ones). */
  let heading = "";
  for (const raw of blocks) {
    const b = raw.trim();
    if (!b) continue;
    if (b.split("\n").every((ln) => /^\s*#/.test(ln))) {
      heading = b.replace(/^#+\s*/gm, "").replace(/\n/g, " ");
      /* A heading is context for what follows, NEVER an excerpt: a long document title once
       * beat every real paragraph and she answered from a title that explains nothing. */
      continue;
    }
    // Skip furniture: fences, tables and front-matter rules read terribly when quoted.
    if (b.length < 60 || b.startsWith("```") || b.startsWith("|") || b.startsWith("---")) continue;

    const low = b.toLowerCase();
    const headLow = heading.toLowerCase();
    let hits = 0, headHits = 0;
    for (const t of want) {
      if (low.includes(t)) hits += 1;
      if (headLow.includes(t)) headHits += 1;
    }
    if (!hits) continue;
    /* Distinct terms dominate, the section heading breaks ties, length is last.
     *
     * Length alone used to break ties, and with a two-term query (`talk`, `cloud`) it
     * picked a paragraph about QR pairing over the transport paragraph — and it double
     * counts, since a longer paragraph already buys hits by chance. A heading match means
     * the section is ABOUT the thing, weighted at `rank`'s own 6:3 title:heading ratio
     * (half a hit): enough to settle a tie, never enough to beat more hits. Pure term
     * density was tried and is worse: it promotes an 88-character citation stub. */
    const score = hits * 10 + headHits * 5 + Math.min(b.length / 200, 4);
    if (score > bestScore) { bestScore = score; best = b; }
  }
  if (!best) return "";
  const clean = best
    .replace(/`{1,3}/g, "")
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*?/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")   // links -> their words
    .replace(/\s+/g, " ")
    .trim();
  return clean.length > MAX_EXCERPT ? clean.slice(0, MAX_EXCERPT).replace(/\s+\S*$/, "") + "…" : clean;
}

/** Questions worth two asset fetches: a cheap gate matching questions ABOUT HER or the
 *  machine (not any topic mention), so "tell me about dogs" never hits the firmware notes
 *  and ordinary turns cost nothing. */
const SELF_QUERY = /\b(how (do|does|did) (you|it|moxie|the robot|this)|what (are|is) (you|your)|your (firmware|hardware|protocol|code|docs?|documentation|brain|memory|motors?|screen|camera|microphone|wifi|design)|how (you|moxie) (work|works|were|was) |made of|built|reverse.?engineer|documentation|the docs?)\b/i;

export function wantsDocs(query) {
  return SELF_QUERY.test(String(query || ""));
}

/**
 * Look one thing up. Returns `{title, path, excerpt}` or `null`.
 *
 * `assets` is the Pages `ASSETS` binding (a `Fetcher` over our own static files), passed
 * in so tests can use a fake.
 */
export async function lookup(assets, origin, query) {
  if (!assets || typeof assets.fetch !== "function") return null;
  if (!wantsDocs(query)) return null;
  try {
    const idxRes = await assets.fetch(new Request(origin + "/docs-index.json"));
    if (!idxRes || !idxRes.ok) return null;
    const index = await idxRes.json();
    const hits = rank(index, query);
    if (!hits.length) return null;
    const top = hits[0];
    const docRes = await assets.fetch(new Request(origin + "/docs-bundle/" + top.path));
    if (!docRes || !docRes.ok) return null;
    const excerpt = bestPassage(await docRes.text(), query);
    if (!excerpt) return null;
    return { title: top.title, path: top.path, excerpt };
  } catch {
    return null;   // fail open: no excerpt, and the turn proceeds as it always did
  }
}
