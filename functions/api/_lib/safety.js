/* functions/api/_lib/safety.js — the pre-inference floor. Compiles ./safety.rules.js,
 * applies it to the child's utterance, and hands the route a verdict.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety'), §2.6
 * ('The safety journal / parent review queue' — what cannot run here).
 *
 * WHY THE CHECK IS BEFORE THE CALL AND NOT AFTER IT. Two reasons, and they point the same
 * way. Safety: `mqtt/moxie_sdk/safety.py`:11-13 assesses the child's utterance "BEFORE the
 * brain is called, so a hard-blocked turn never reaches a model at all", and a public
 * kid-facing demo has no excuse to do less. Cost: a blocked turn spends ZERO gateway units
 * (§4.1 — "spends nothing"), so the cheapest possible request is the one we refuse to make.
 * One rule, two controls.
 *
 * WHAT THIS IS, HONESTLY — and the phrasing is `safety.py`'s own, because it applies here
 * verbatim: **it is a floor, not a filter.** A rule engine cannot understand context or
 * sarcasm or a harmful idea expressed in gentle words; it will miss novel phrasings and
 * every language its tables are not written in; and it will occasionally catch something
 * innocent. It is ONE LAYER UNDER the model's own alignment and the persona system prompt
 * (which `chat.js` places both first AND last, §3.3), never a replacement for either, and
 * never a substitute for a parent.
 *
 * WHAT P0 DOES NOT DO. There is no journal, no parent review queue and no post-inference
 * stage. The hosted demo has no durable store (§2.6: "Nothing persists"), so a `flag`
 * verdict has nowhere to go: it is computed, returned, and otherwise allowed through —
 * exactly what §2.6 promises ("Pre-inference blocking only, with no record kept"). The
 * post-inference per-chunk stage the Python runtime runs is P1+ and needs streaming first.
 *
 * NOTHING HERE TOUCHES THE NETWORK OR THE CLOCK. `assess()` is pure: same text in, same
 * verdict out, so `sim/test_demo_proxy.mjs` can assert the verdict rather than assert that
 * some verdict happened.
 *
 * WHERE THE TABLE LIVES, and why it moved. It shipped as `safety.json`, loaded with
 * `import RULES from "./safety.json" with { type: "json" }`. Node 20 accepts that
 * attribute, so every hermetic test was green — and **the Cloudflare Pages build FAILED**
 * on the branch whose only structural change to this tree was that one line. So the Pages
 * bundler does not accept import attributes, which settles as **false** an item the spec's
 * §10 ledger listed as unverified. The table now lives in `./safety.rules.js` as a plain
 * data module: same content, re-emitted mechanically and compared parsed rather than
 * retyped. The `.json` file is deleted rather than kept beside it — two copies of a safety
 * rule table that nothing keeps in sync is a worse failure than the one that was fixed.
 * `sim/test_demo_proxy.mjs` guards the tree so no `.json` import or import attribute can
 * come back as a deploy-only failure.
 *
 * This file still only COMPILES the table; every rule is in `./safety.rules.js`, and its
 * `_readme` is written for a person to read.
 */
import { RULES } from "./safety.rules.js";

/* ---------------------------------------------------------------------------- *
 * Normalization — one text in, several comparable forms out
 * ---------------------------------------------------------------------------- *
 * Transcribed from `mqtt/moxie_sdk/safety.py::normalize` (:153-171) and `_variants`
 * (:174-192) so the two tables agree about what a word IS. Divergence here would mean a
 * phrase the local stack blocks and the hosted demo does not, which is the worst kind of
 * inconsistency: invisible.
 *
 * THE ONE DIVERGENCE THAT NOW EXISTS, AND WHICH WAY IT POINTS. This side strips the whole
 * Unicode `Cf` category and folds intra-word punctuation; the Python side still strips four
 * zero-width code points and folds none. So the hosted demo now blocks a STRICT SUPERSET of
 * what the local stack blocks. That is the safe direction of the two — the public,
 * child-facing surface is the stricter one — but it is still a divergence, and the fix
 * belongs in `safety.py::normalize`/`_variants` as well. Recorded here rather than left for
 * someone to discover, because the header above promises these two agree and right now they
 * agree only up to this.
 */

/** Curly apostrophes onto `'` (so `don't`/`don’t` are one word) and the invisible
 *  characters used to split a word so a word list cannot see it.
 *
 *  WHY THE WHOLE `Cf` CATEGORY AND NOT A HAND-PICKED FEW. This list used to name exactly
 *  four code points — U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+FEFF — and every other
 *  invisible formatting character walked straight into the matcher. That is not a
 *  theoretical hole: `"suicide"` blocked, and the same word with a U+00AD SOFT HYPHEN or a
 *  U+2060 WORD JOINER between each letter did not, while rendering identically on the
 *  page. `self_harm` is the FIRST blocking category and this floor runs BEFORE the gateway
 *  is called, so one invisible character pasted into a public, child-facing demo defeated
 *  the entire pre-inference block. Naming code points one at a time is how that happened;
 *  the category is the closed set, so the category is what we strip.
 *
 *  VERIFIED IN THIS ENGINE, NOT ASSUMED. `\p{Cf}` is only worth trusting if it covers what
 *  we think it covers, and one member has moved between categories across Unicode versions
 *  — U+180E MONGOLIAN VOWEL SEPARATOR was `Zs` until Unicode 6.3. Probed against V8
 *  11.3 (Node 20 / the Workers runtime), `\p{Cf}` matches all of: U+00AD, U+061C,
 *  **U+180E**, U+200B–U+200F, U+202A–U+202E, U+2060–U+2064, U+2066–U+2069, U+FEFF and
 *  U+FFF9–U+FFFB. The four this list used to name are a subset of it.
 *
 *  THE FOUR THAT ARE NOT `Cf`, added by hand because the category misses them. U+115F and
 *  U+1160 (Hangul choseong/jungseong filler) and their compatibility twins U+3164 and
 *  U+FFA0 are category **`Lo`** — letters — but they have no glyph, which is exactly why
 *  they are the standard way to make a "blank" name in a chat client. They are placeholders
 *  for an absent jamo, they carry no meaning in the English the table is written in, and
 *  they split a word as invisibly as a ZWSP does. (U+3164 and U+FFA0 already NFKD-fold onto
 *  U+1160 one line above, so the class only has to catch two; all four are written out
 *  because a reader should not have to know that.)
 *
 *  WHAT IS DELIBERATELY LEFT ALONE. **U+034F COMBINING GRAPHEME JOINER** is `Mn`, not `Cf`
 *  — and it needs nothing here, because `normalize` already drops every `\p{M}` on the
 *  line above; `test_demo_proxy.mjs` pins that so the coverage cannot be lost by accident.
 *  The **`Zs` space separators** (U+00A0, U+2000–U+200A, U+202F, U+205F, U+3000) are not
 *  stripped either, and must not be: NFKD folds every one of them onto an ordinary U+0020
 *  and U+1680 falls to the `\s+` collapse below (both probed, both pinned by a test), so an
 *  exotic space becomes a REAL SPACE. That is the right answer — a no-break space IS a
 *  space — and it means `"i want to\u00a0kill myself"` blocks exactly like the plain
 *  sentence. It also means `s\u00a0u\u00a0i\u00a0c\u00a0i\u00a0d\u00a0e` does not block,
 *  and that is not a hole this fix pretends to close: it renders as `s u i c i d e`, so it
 *  is a VISIBLE evasion identical to typing real spaces, which this floor has never caught
 *  and cannot catch without deleting spaces from every utterance. Kept out of scope on
 *  purpose rather than half-closed for the one variant that happens to be exotic. */
const ALWAYS = [
  [/[’‘ʼ]/g, "'"],
  [/[\p{Cf}\u115F\u1160\u3164\uFFA0]/gu, ""],
];

/** Substitutions people use to slip past a word list (`sh1t`, `$hit`, `f@ck`). Applied
 *  ONLY where the next character is a letter — substituting a trailing `!` would turn
 *  `shoot!` into `shooti` and BREAK a match rather than catch one. */
const LEET = {
  0: "o", 1: "i", 3: "e", 4: "a", 5: "s", 7: "t", 8: "b", 9: "g",
  "@": "a", $: "s", "!": "i", "|": "i", "+": "t",
};
const LEET_RE = /[01345789@$!|+](?=[a-z])/g;

/** Three or more of the same character — `fuuuuck`, `killlll`. */
const RUN_RE = /(.)\1{2,}/g;

/** A run of non-alphanumerics with a letter or digit on BOTH sides, so `s.u.i.c.i.d.e` and
 *  `s-u-i-c-i-d-e` collapse onto the word they spell. Written with a lookahead and a
 *  captured left flank rather than a lookbehind, because consuming both flanks would make
 *  the alternating matches overlap and only fold every other separator.
 *
 *  WHY THIS AND NOT `[^a-z0-9 ]` EVERYWHERE, which is the obvious version and the one the
 *  audit proposed. Stripping ALL punctuation also deletes the boundary BETWEEN sentences,
 *  and the phrase regexes are written across `\s+`. Measured against a corpus of innocent
 *  child-shaped sentences, the broad form turned two of them into `self_harm` blocks:
 *
 *      "that's what i want. To die of laughter would be great"
 *      "i don't know what i want. To not be so shy would be nice"
 *
 *  both fold onto `... i want to die ...` / `... i want to not be ...` and trip
 *  `\bi\s+want\s+to\s+(?:die|...)\b`. A child saying either of those is told to go find a
 *  grown-up, by a robot, for saying something completely ordinary — that is a real harm in
 *  its own right, not a safe default, and it is the exact failure the module header calls
 *  out ("it will occasionally catch something innocent"). Requiring a letter or digit on
 *  both sides keeps every sentence boundary intact (`want.` is followed by a SPACE, so it
 *  is left alone) while still folding the intra-word separators, which is the whole evasion
 *  this variant exists for. Same corpus, narrow form: zero false positives. */
const INWORD_PUNCT_RE = /([a-z0-9])[^a-z0-9 ]+(?=[a-z0-9])/g;

export function normalize(text) {
  if (!text) return "";
  let t = String(text).normalize("NFKD").replace(/\p{M}/gu, "");
  t = t.toLowerCase();
  for (const [re, to] of ALWAYS) t = t.replace(re, to);
  t = t.replace(LEET_RE, (c) => LEET[c] || c);
  return t.replace(/\s+/g, " ").trim();
}

/** The normalized text plus its de-elongated and de-punctuated forms.
 *
 *  A run of 3+ identical characters is collapsed to ONE and to TWO, because either may be
 *  the real word (`fuuuuck` → `fuck`, `killlll` → `kill`). The fourth form folds the
 *  separators a writer put INSIDE a word (`s.u.i.c.i.d.e`, `s-u-i-c-i-d-e`) — see
 *  `INWORD_PUNCT_RE` for why it is not the broader "drop all punctuation".
 *
 *  ADDING A FORM CAN ONLY ADD MATCHES. `matches()` ORs across every form and returns on the
 *  first hit, so a new variant can never LOSE a block that the base form already had — it
 *  can only find one more, or (the whole reason each one is measured before it ships) one
 *  it should not have. That asymmetry is why the false-positive corpus is the gate here and
 *  the evasion table is not: a missed evasion is the same floor we had, a false positive is
 *  a new harm. Four cheap regex scans instead of one. */
export function variants(text) {
  const base = normalize(text);
  if (!base) return [""];
  const out = [base];
  for (const v of [
    base.replace(RUN_RE, "$1"),
    base.replace(RUN_RE, "$1$1"),
    base.replace(INWORD_PUNCT_RE, "$1"),
  ]) {
    if (!out.includes(v)) out.push(v);
  }
  return out;
}

/* ---------------------------------------------------------------------------- *
 * Compiling the table
 * ---------------------------------------------------------------------------- */

function wordRe(words) {
  if (!words || !words.length) return null;
  const esc = words.map((w) => String(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp("\\b(?:" + esc.join("|") + ")\\b", "i");
}

function phraseRes(list) {
  const out = [];
  for (const p of list || []) {
    try {
      out.push(new RegExp(p, "i"));
    } catch {
      // A bad regex in the table must not take the route down. It is dropped, and the
      // count is exposed on `TABLE.badPatterns` so a test can pin it at zero.
      badPatterns += 1;
    }
  }
  return out;
}

let badPatterns = 0;

function compile(rules) {
  const cats = [];
  for (const c of (rules && rules.categories) || []) {
    cats.push({
      id: String(c.id || ""),
      label: String(c.label || ""),
      // P0 enforces the child side only; there is no Moxie-side (post-inference) stage.
      action: (c.action && c.action.child) === "block" ? "block" : "flag",
      intents: Array.isArray(c.intents) ? c.intents.map(String) : [],
      phraseSet: String(c.phrase_set || "generic"),
      words: wordRe(c.words),
      phrases: phraseRes(c.phrases),
      allow: phraseResGlobal(c.allow),
    });
  }
  return { version: Number(rules && rules.version) || 0, categories: cats, phrases: (rules && rules.phrases) || {} };
}

/** The allow guards are applied by REMOVAL, so they need the global flag. */
function phraseResGlobal(list) {
  const out = [];
  for (const p of list || []) {
    try {
      out.push(new RegExp(p, "gi"));
    } catch {
      badPatterns += 1;
    }
  }
  return out;
}

/** The compiled table. Built once per isolate — regex compilation is the only cost this
 *  module has, and a Worker isolate serves many requests. */
export const TABLE = compile(RULES);
Object.defineProperty(TABLE, "badPatterns", { value: badPatterns, enumerable: true });

/* ---------------------------------------------------------------------------- *
 * The verdict
 * ---------------------------------------------------------------------------- */

/**
 * Assess one child utterance.
 *
 * @param {string} text
 * @returns {{blocked: boolean, flagged: boolean, blockedBy: string[], intents: string[],
 *            phraseSet: string, redirect: {text: string, mood: number, gesture: string,
 *            phraseId: number}|null}}
 *
 * `blockedBy` is in TABLE ORDER, and the FIRST blocking category picks the spoken redirect
 * — the same rule `safety_rules.json`'s own `_readme` states ("Order matters"), so
 * self-harm outranks profanity when a sentence trips both, which is the outcome that
 * actually matters.
 */
export function assess(text) {
  const forms = variants(text);
  const blockedBy = [];
  const flaggedBy = [];
  const intents = [];
  let phraseSet = "";

  for (const cat of TABLE.categories) {
    if (!matches(cat, forms)) continue;
    if (cat.action === "block") {
      blockedBy.push(cat.id);
      if (!phraseSet) phraseSet = cat.phraseSet;
    } else {
      flaggedBy.push(cat.id);
    }
    for (const i of cat.intents) if (!intents.includes(i)) intents.push(i);
  }

  return {
    blocked: blockedBy.length > 0,
    flagged: flaggedBy.length > 0,
    blockedBy,
    flaggedBy,
    intents,
    phraseSet: phraseSet || "",
    redirect: blockedBy.length ? redirectFor(phraseSet, text) : null,
  };
}

function matches(cat, forms) {
  for (const form of forms) {
    // The false-positive guards are applied FIRST and by REMOVAL, so `killing myself
    // laughing` never counts as self-harm and `flag football` never counts as a slur.
    let t = form;
    for (const g of cat.allow) t = t.replace(g, " ");
    if (cat.words && cat.words.test(t)) return true;
    for (const p of cat.phrases) if (p.test(t)) return true;
  }
  return false;
}

/**
 * The line Moxie says instead of the blocked one.
 *
 * DETERMINISTIC by design: the line is picked by the utterance's own length modulo the
 * set size, not at random, so the same input always produces the same redirect and a test
 * can assert it (playbook rule 11). A real rotation wants a store to remember what it last
 * said, and the hosted demo has none.
 *
 * A DELIBERATE DEVIATION FROM §4.1, recorded here because it is the one place this slice
 * does not do what the spec's letter says. §2.6/§4.1 say a blocked turn "answers from the
 * scripted repertoire" — i.e. `stub.js`. But `stub.js`'s repertoire answers a self-harm
 * disclosure with "Tell me more about that!", which is the wrong thing for a child to hear
 * from a robot. So the Function returns the rule table's own redirect line instead, which
 * (a) still spends nothing — it is generated locally, no gateway call, no ticket — and
 * (b) is what the Python side already does on a block (`safety_rules.json`'s `phrase_set`
 * IS "the redirect line Moxie speaks"). `cloud-transport.js` routes the line when one is
 * present and falls back to the stub when it is not, so the honest-degrade guarantee is
 * unchanged.
 */
export function redirectFor(phraseSet, text) {
  const set = TABLE.phrases[phraseSet] || TABLE.phrases.generic || [];
  if (!set.length) return null;
  const pick = set[String(text || "").length % set.length];
  return {
    text: String(pick.text || ""),
    mood: Number(pick.mood) || 0,
    gesture: String(pick.gesture || "Gesture_Think"),
    phraseId: Number(pick.id) || 0,
  };
}
