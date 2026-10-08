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
 *  document ("wifi", "audio", "protocol").
 *
 *  The second group is what a child says TO her rather than about her, measured on the
 *  committed index: "now" is a word of the broker-auth note's title, so "what are you doing
 *  right now?" cited JWT notes; "name" cited a note on sort order; "that" is in two
 *  design-note titles, and "how did you know that?" still passes the gate. */
const STOP = new Set([
  "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "how", "what", "why",
  "when", "where", "who", "your", "you", "yours", "me", "my", "i", "it", "its", "of", "to",
  "in", "on", "for", "and", "or", "can", "could", "would", "tell", "about", "explain",
  "moxie", "robot", "please", "know", "work", "works",
  "doing", "right", "now", "name", "today", "sleep", "good", "here", "like", "favorite",
  "favourite", "feel", "think", "that",
]);

/** Self-questions whose own words name nothing the index can rank: "how were you built?"
 *  and "what are you made of?" ask about her body, which the hardware map answers
 *  ("motors, sensors, LEDs, power"; "hardware" alone ties it with the flashing guide and
 *  quotes a to-do list), and "the docs" are what the index calls "Documentation". The
 *  phrase supplies the topic; the visitor's own words still count. */
const IMPLIED = [
  [/\b((are|were) you made of|how (were|was) (you|moxie) (made|built)|how (you|moxie) (were|was) (made|built))\b/i,
   ["hardware", "motors"]],
  [/\b(the|your) docs?\b/i, ["documentation"]],
];

/** Query -> the terms worth scoring: lower-cased, de-punctuated, stop words and anything
 *  under three characters dropped, plus any topic the question's phrasing implies. */
export function terms(query) {
  const q = String(query || "");
  const out = [];
  for (const w of q.toLowerCase().split(/[^a-z0-9]+/)) {
    if (w.length >= 3 && !STOP.has(w)) out.push(w);
  }
  for (const [re, topic] of IMPLIED) if (re.test(q)) out.push(...topic);
  return [...new Set(out)];
}

/** WHOLE-WORD matching: a term matches a word that is the term or the term plus an
 *  inflection, so "motor" still finds "motors" and "remember" finds "remembers". The old
 *  substring match found words INSIDE other words: "cat" hit "Catalog", "here" hit
 *  "where", "play" hit "Playbook". Word edges are `terms`'s own split ([a-z0-9]), and a
 *  term is only ever [a-z0-9]+, so it needs no escaping. One regex per term, tested on
 *  lower-cased text: a word set per paragraph cost 4-6x the CPU of the old match. */
const wordMatcher = (t) => new RegExp("(?<![a-z0-9])" + t + "(?:s|es|ed|d|ing)?(?![a-z0-9])");

/**
 * Rank the index against a query. Pure, so it is tested on real fixtures.
 *
 * Weights: TITLE (says what the document is) > HEADING (it has a section about the thing)
 * > PATH (catches `firmware/`, `protocol/`, weakest so a directory cannot outvote a
 * document about the subject). "Found nothing" beats the least-bad unrelated document, so
 * a document must clear a RELEVANCE FLOOR: its title names a term, or two different terms
 * land somewhere in it. One word in one heading is a coincidence on an index this size,
 * where almost any word is in some heading ("`sleep` is not on the list at any level" in
 * a sandbox note, "Doing it from our local server" in a reset guide).
 */
export function rank(index, query) {
  const want = terms(query).map(wordMatcher);
  if (!want.length) return [];
  const files = index && Array.isArray(index.files) ? index.files : [];
  const scored = [];
  for (const f of files) {
    const title = String((f && f.title) || "").toLowerCase();
    const path = String((f && f.path) || "").toLowerCase();
    const heads = (f && Array.isArray(f.headings) ? f.headings : []).join(" ").toLowerCase();
    let score = 0, inTitle = 0, matched = 0;
    for (const re of want) {
      const a = re.test(title), b = re.test(heads), c = re.test(path);
      score += (a ? 6 : 0) + (b ? 3 : 0) + (c ? 1 : 0);
      if (a) inTitle += 1;
      if (a || b || c) matched += 1;
    }
    if (inTitle || matched >= 2) scored.push({ path: f.path, title: f.title, score });
  }
  // Ties broken by path: the same question must always cite the same document.
  scored.sort((a, b) => b.score - a.score || String(a.path).localeCompare(String(b.path)));
  return scored;
}

/** How much of a document may reach the prompt. Sized in 2026-09, when 900 characters made
 *  the first grounded prompt 2,215 tokens and the gateway refused every docs question (read
 *  then as a 2,048-token window); 320 fit and still carried the fact the answer needs. That
 *  limit is gone: on 2026-10-08 the same `graphling-medium` alias accepted a 6,814-token
 *  prompt. 320 stays until `sim/tools/grounding_probe.mjs` is re-run, because a longer
 *  passage changes what she says and only that probe measures whether she uses it. */
const MAX_EXCERPT = 320;

/**
 * The most relevant passage of a markdown document, as plain-ish text.
 *
 * Paragraph-scored (a lone sentence loses its subject), with markdown furniture stripped
 * because the result is paraphrased aloud to a child.
 */
export function bestPassage(markdown, query) {
  const want = terms(query).map(wordMatcher);
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
    for (const re of want) {
      if (re.test(low)) hits += 1;
      if (re.test(headLow)) headHits += 1;
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

/** Questions worth two asset fetches: a cheap gate matching questions about HER INTERNALS
 *  (not any topic mention), so "tell me about dogs" never hits the firmware notes and
 *  ordinary turns cost nothing.
 *
 *  SMALL TALK ADDRESSED TO HER IS NOT A QUESTION ABOUT HOW SHE WORKS. The gate used to admit
 *  any "what are/is you/your …" and any "how do/does/did you …": on the committed index,
 *  21 of 60 ordinary lines ("what are you doing right now?", "what is your name?", "how did
 *  you sleep?") cited an unrelated page, and its passage went into her prompt. Now "your"
 *  needs a part of her ("your firmware", never "your name") and "how … you" needs a verb for
 *  how she WORKS ("how do you remember", never "how do you feel"). */
const SELF_QUERY = new RegExp("\\b(" + [
  "your (firmware|hardware|protocol|code|docs?|documentation|brain|memory|motors?|screen|camera|microphone|wifi|design)",
  "how (do|does|did) (you|it|moxie|the robot|this) (work|remember|think|see|hear|talk|move|run|learn|know|connect)",
  "how (were|was) (you|moxie) (made|built)",
  "how (you|moxie) (works?|(were|was) (made|built))",
  "made of",
  "reverse.?engineer(s|ed|ing)?",
  "documentation",
  "the docs",
].join("|") + ")\\b", "i");

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
