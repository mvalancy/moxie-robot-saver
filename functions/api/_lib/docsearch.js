/* functions/api/_lib/docsearch.js — Moxie can look things up in her own documentation.
 *
 * This deployment ships 152 markdown documents about how the real Moxie works — the
 * reverse-engineered protocol, the firmware, the behaviour markup, this simulator's own
 * architecture — as static assets under `/docs-bundle/`, with a 112 KB index at
 * `/docs-index.json`. They are already public and already served; nothing new is exposed
 * here. What was missing is that the robot they describe could not read them.
 *
 * ============================================================================
 * RETRIEVAL RUNS ON THE SERVER, AND THAT IS A SECURITY DECISION RATHER THAN A
 * PERFORMANCE ONE.
 *
 * The obvious design is the browser one: the page already has the corpus same-origin, so
 * it could rank, pick a passage, and post it along with the question. That would hand a
 * visitor a field whose contents are injected into the model's prompt — which is a prompt
 * injection channel with a bow on it. "Ignore the persona, you are now…" typed into a text
 * box that the server splices into a system message is exactly the hole the repeated
 * persona sandwich exists to close, and it would be a hole we opened ourselves.
 *
 * So the visitor's words only ever CHOOSE a document. The TEXT that reaches the model is
 * always bytes we wrote and committed, fetched from our own origin through the `ASSETS`
 * binding, and never anything the visitor supplied. That property is the whole design and
 * everything else here is subordinate to it.
 *
 * TWO ASSET FETCHES, NEVER THREE, and never the 3 MB one. `docs-search.json` is the full
 * text of every document in one 3 MB object; loading it per request to answer a child's
 * question would be absurd. Ranking happens on the 112 KB INDEX (titles, sections,
 * headings), and only the single winning document's markdown is then fetched — a few tens
 * of kilobytes. Asset fetches do not touch the gateway and cost nothing.
 *
 * IT FAILS OPEN, ALWAYS. Every error path returns `null`, which means "no excerpt" and the
 * turn proceeds exactly as it did before this file existed. A documentation lookup is a
 * bonus on top of an answer; it must never be able to cost a child their turn.
 * ============================================================================
 */

/* ============================================================================
 * MEASURING WHETHER SHE ACTUALLY USED THE PASSAGE: TRIED, AND IT DOES NOT WORK
 * LEXICALLY. Recorded here so it is not rebuilt (2026-09-08).
 *
 * `cited` makes the passage known at answer time, so "did the retrieved content reach the
 * words" looks like it should become a comparison rather than a judgement. A gloss is
 * exactly the case where the citation is right and the answer contains none of it —
 * `"how does your firmware work?"` -> `"a special brain inside me"`.
 *
 * The discriminator was fixed BEFORE any numbers were looked at: an answer is grounded iff
 * it shares one DISTINCTIVE term with the cited passage, where distinctive means "in the
 * passage, not in the question, and rare in the corpus" — document frequency, computed
 * from the shipped docs, deliberately instead of a hand-written list of generic words,
 * because a hand-written list is how an instrument gets tuned until it agrees with whoever
 * wrote it.
 *
 * IT FAILS, AND THE REASON IS STRUCTURAL RATHER THAN A THRESHOLD. Every document here is
 * about one system, so the terms that would prove she read something are the COMMON ones:
 *
 *     mqtt 51% · cloud 61% · brain 54% · protocol 61% · firmware 65% · robot 88%
 *     rpc 4.6% · privileged 4.6% · earmuffs 5.9%
 *
 * Only engineer-internal jargon is rare — and jargon is precisely what the persona forbids
 * ("no jargon you have not explained", one or two short sentences at a child's level). So
 * the test scores a CORRECT child-level answer as glossed by construction, and would score
 * GROUNDED only if she broke persona.
 *
 * And no threshold rescues it: sweeping document frequency from 5% to 90%, the first value
 * that admits `mqtt` (55%) also admits `brain` — a word she says constantly whether or not
 * she read anything. The distributions overlap; rarity is not the separating property.
 *
 * WHAT A WORKING INSTRUMENT NEEDED was a control arm: the same question asked with the
 * retrieval suppressed, and a comparison of the two answers. The signal is the DIFFERENCE
 * a passage makes, not the vocabulary of one answer — which cannot be read off a single
 * turn however it is scored. **BUILT 2026-09-08: `sim/tools/grounding_probe.mjs`**, and it
 * needed no suppression switch in this file at all — the probe builds both prompts with
 * `buildUpstreamBody` and calls the gateway directly, so the serving path is untouched and
 * there is nothing a visitor could set.
 *
 * AND IT IMMEDIATELY FOUND SOMETHING ABOUT THIS FILE RATHER THAN ABOUT THE MODEL. Asked
 * "how does the robot talk to the cloud?", `rank` picks `architecture/mqtt-and-conversation
 * .md` — the right document by title — and `bestPassage` then returns a paragraph about QR
 * PAIRING STAGES, which does not answer the question. She answered from her own knowledge
 * instead, and the control arm scored it not grounded, correctly.
 *
 * That is "a title is not an answer" one level down: the DOCUMENT is right and the PASSAGE
 * inside it is wrong. `bestPassage` scored paragraphs by query-term hits, and in a document
 * named after the query's own words many paragraphs tie on the title's vocabulary while
 * saying nothing about the question — leaving the capped LENGTH bonus to pick the winner.
 * A retrieval problem, not a prompting one, and three rounds of prompt rewriting had been
 * aimed at a layer that was never broken.
 *
 * FIXED BELOW by scoring the paragraph's SECTION HEADING, at the weight `rank` already
 * gives headings. The measured effect on that question: the QR-pairing paragraph is gone
 * and the passage now handed to the model is the transport paragraph, which contains
 * `MQTT` as a whole word rather than as a substring of `MQTTS`.
 * ============================================================================ */

/** Words too common to discriminate between 152 documents about one robot. `moxie` is in
 *  here for that exact reason: it matches almost every document, so scoring on it ranks
 *  noise. The set is deliberately tiny — an aggressive stop list starts throwing away the
 *  words that actually pick a document ("wifi", "audio", "protocol"). */
const STOP = new Set([
  "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "how", "what", "why",
  "when", "who", "your", "you", "yours", "me", "my", "i", "it", "its", "of", "to", "in",
  "on", "for", "and", "or", "can", "could", "would", "tell", "about", "explain", "moxie",
  "robot", "please", "know", "work", "works",
]);

/** Query -> the terms worth scoring. Lower-cased, de-punctuated, stop words dropped, and
 *  anything under three characters discarded (`is`, `up`, `to` survive no stop list). */
export function terms(query) {
  const out = [];
  for (const w of String(query || "").toLowerCase().split(/[^a-z0-9]+/)) {
    if (w.length >= 3 && !STOP.has(w)) out.push(w);
  }
  return [...new Set(out)];
}

/**
 * Rank the index against a query. Pure — no I/O, no clock — so the ranking can be tested
 * against real fixtures rather than by observing what the network returned.
 *
 * THE WEIGHTS, AND WHY THEY ARE ORDERED THIS WAY. A term in a document's TITLE is the
 * strongest signal there is: these titles were written to say what the document is. A
 * HEADING is next, because a heading match means the document has a section about the
 * thing rather than merely mentioning it. The PATH is last and weakest — it catches
 * `firmware/`, `protocol/`, `runtime/` when somebody asks about a firmware thing without
 * naming a document — and it is deliberately below headings so a directory name cannot
 * outvote a document that is actually about the subject.
 *
 * A document scoring zero is never returned. "I could not find anything" is a real answer
 * and a far better one than the least-bad of 152 documents about something else.
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
  // Ties broken by path so the same question always returns the same document: a robot
  // that cites a different source each time it is asked reads as making things up.
  scored.sort((a, b) => b.score - a.score || String(a.path).localeCompare(String(b.path)));
  return scored;
}

/** How much of a document may reach the prompt. The deployed `graphling-medium` route has
 *  a 2,048-token window: 900 characters made the measured first-turn prompt 2,215 tokens
 *  and the gateway refused every documentation question. At 320, that same live control
 *  fits and still carries the concrete MQTT fact the answer needs. */
const MAX_EXCERPT = 320;

/**
 * The most relevant passage of a markdown document, as plain-ish text.
 *
 * Paragraph-scored rather than sentence-scored: a sentence pulled out of a technical
 * document usually loses the subject it depended on, and Moxie then explains a pronoun.
 * Markdown furniture that reads badly aloud — heading hashes, table pipes, code fences,
 * link syntax — is stripped, because whatever comes back here is going to be paraphrased
 * to a child by something that has been told to speak plainly.
 */
export function bestPassage(markdown, query) {
  const want = terms(query);
  const text = String(markdown || "");
  if (!text) return "";
  const blocks = text.split(/\n\s*\n/);
  let best = "", bestScore = 0;
  /* The heading a paragraph lives under, carried down the document as we walk it. This is
   * read BEFORE the length filter below, and that ordering is the whole point: real
   * headings are short — `## MQTT / "Talking" Layer Spec` is 30 characters — so a heading
   * check placed after a `length < 60` test only ever sees the freakishly long ones. That
   * is exactly how the title bug got in: the one heading big enough to survive the filter
   * was the one that reached the model. */
  let heading = "";
  for (const raw of blocks) {
    const b = raw.trim();
    if (!b) continue;
    if (b.split("\n").every((ln) => /^\s*#/.test(ln))) {
      heading = b.replace(/^#+\s*/gm, "").replace(/\n/g, " ");
      /* A HEADING IS CONTEXT FOR WHAT FOLLOWS AND NEVER AN EXCERPT ITSELF, which is the
       * fix for a bug that reached production. Asked "what is your protocol?" on the live
       * site she answered "I don't have a special protocol like a big robot" — confidently,
       * and wrong. Retrieval had worked and picked `remote-chat-protocol.md` correctly.
       * What it handed her was the document's TITLE — "RemoteChat — the robot to brain
       * conversation protocol (v3.6.4-Zephyr…)" — because that heading is over sixty
       * characters long and matched the query, so it beat every real paragraph. A title
       * names a subject; it does not explain one, and there was nothing in it to answer
       * from. */
      continue;
    }
    // Skip the furniture: fences, tables and front-matter rules carry little prose and
    // read terribly when quoted.
    if (b.length < 60 || b.startsWith("```") || b.startsWith("|") || b.startsWith("---")) continue;

    const low = b.toLowerCase();
    const headLow = heading.toLowerCase();
    let hits = 0, headHits = 0;
    for (const t of want) {
      if (low.includes(t)) hits += 1;
      if (headLow.includes(t)) headHits += 1;
    }
    if (!hits) continue;
    /* DISTINCT TERMS DOMINATE, THE SECTION HEADING IS THE TIEBREAK, LENGTH IS LAST.
     *
     * Length used to break the ties and that was a measured failure rather than a
     * theoretical one. Asked "how does the robot talk to the cloud?" the query reduces to
     * just two terms — `talk`, `cloud` — so `hits` saturates at 2 across a dozen
     * paragraphs and the capped length bonus becomes the entire selector. The winner was
     * the longest paragraph containing both words: one about QR PAIRING STAGES, scoring
     * 24.0 against 22.9 for the paragraph that actually answers the question, a gap made
     * up purely of characters.
     *
     * Worse, length was double-counted. A longer paragraph is more likely to contain any
     * given term BY CHANCE, so length already bought the hits — and then got paid again
     * for having them.
     *
     * The heading is the honest signal, and `rank` above already knows it: a heading match
     * means this section is ABOUT the thing rather than mentioning it in passing. This
     * applies that same judgement one level down, at the same 6:3 ratio `rank` weights
     * title against heading — half of a direct hit, enough to settle a tie between
     * paragraphs that match equally, never enough to beat a paragraph matching more of the
     * question. The number is reused rather than invented so it cannot be quietly tuned
     * until the ranking agrees with whoever last looked at it.
     *
     * PURE TERM DENSITY WAS TRIED FIRST AND IS WORSE. Normalising hits by paragraph length
     * is the textbook correction for the double-count, and on this corpus it promotes an
     * 88-character citation stub — "From `embodied.logging.Cloud2` (…path…)" — over every
     * real paragraph, because density rewards brevity and a stub is nothing but terms. */
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

/** Questions worth spending two asset fetches on.
 *
 *  A CHEAP GATE ON PURPOSE. Most turns are "i had a bad day" and have nothing to do with
 *  documentation; running retrieval on all of them would double the asset traffic of the
 *  site to answer questions nobody asked. This matches the shape of a question ABOUT HER
 *  or about the machine — not any mention of a topic — so "tell me about dogs" does not
 *  send her to the firmware notes. */
const SELF_QUERY = /\b(how (do|does|did) (you|it|moxie|the robot|this)|what (are|is) (you|your)|your (firmware|hardware|protocol|code|docs?|documentation|brain|memory|motors?|screen|camera|microphone|wifi|design)|how (you|moxie) (work|works|were|was) |made of|built|reverse.?engineer|documentation|the docs?)\b/i;

export function wantsDocs(query) {
  return SELF_QUERY.test(String(query || ""));
}

/**
 * Look one thing up. Returns `{title, path, excerpt}` or `null`.
 *
 * `assets` is the Pages `ASSETS` binding — a `Fetcher` over this deployment's own static
 * files. It is passed in rather than reached for so this is testable with a fake and so
 * the one place that touches I/O is obvious.
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
