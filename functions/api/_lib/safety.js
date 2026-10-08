/* functions/api/_lib/safety.js — the safety floor, both sides of a turn. Compiles
 * ./safety.rules.js, applies it to the child's utterance (before the call) and to Moxie's
 * own reply (after it), and hands the route a verdict.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety'), §4.12
 * ('The output floor'), §2.6.
 *
 * BEFORE THE CALL, like `mqtt/moxie_sdk/safety.py`: a hard-blocked turn never reaches a
 * model and spends ZERO gateway units — one rule, a safety control and a cost control.
 *
 * AFTER THE CALL, like the core supervisor's `role="moxie"` check: the completion is
 * assessed with each category's `action.moxie` BEFORE a voice ticket is minted, so an
 * unsafe reply is never spoken, shown or paid for; the route swaps in the rule's redirect
 * line (`redirectFor`) with no extra upstream call.
 *
 * IT IS A FLOOR, NOT A FILTER. A rule engine misses context, sarcasm, novel phrasings and
 * other languages, and occasionally catches something innocent. It is one layer under the
 * model's alignment and the persona prompt, never a replacement for either or a parent.
 * On Moxie's side a false positive costs a good line, so the gate for every Moxie-side
 * rule is the false-positive corpus of real replies (`sim/tests/fixtures/safety-floor/`).
 *
 * No journal or review queue: the hosted demo persists nothing, so a `flag` verdict is
 * computed and allowed through (§2.6) — except `hurt_disclosure`, which the route acts
 * on: a reply to a hurt child that names no trusted grown-up gets ONE referral sentence
 * appended (`withReferral`), deterministically, where the model misses about one in ten.
 *
 * `assess()` is PURE (no network, no clock), so tests assert the exact verdict. The table
 * is a `.js` data module because the Pages bundler rejects JSON import attributes (see
 * `./safety.rules.js`).
 */
import { RULES } from "./safety.rules.js";

/* Normalization — transcribed from `mqtt/moxie_sdk/safety.py::normalize`/`_variants` so
 * both tables agree about what a word IS; `sim/tests/test_safety.py` asserts parity
 * (the one pinned divergence is the German sharp S, where Python is stricter). */

/** Curly apostrophes onto `'`, and the invisible characters used to split a word so a
 *  word list cannot see it.
 *
 *  THE WHOLE `Cf` CATEGORY, not a hand-picked few: naming four zero-width code points let
 *  a SOFT HYPHEN or WORD JOINER between letters defeat the self-harm block. `\p{Cf}` in
 *  V8 covers U+00AD, U+061C, U+180E, U+200B–U+200F, U+202A–U+202E, U+2060–U+2064,
 *  U+2066–U+2069, U+FEFF, U+FFF9–U+FFFB. Plus the four glyphless Hangul fillers
 *  (U+115F, U+1160, U+3164, U+FFA0), which are `Lo` but split a word just as invisibly.
 *
 *  Left alone deliberately: U+034F (`Mn`, already dropped with every `\p{M}`), and the
 *  `Zs` spaces, which NFKD / `\s+` fold onto a real space — so an exotic-space split is a
 *  VISIBLE evasion, like typing spaces, and out of scope. */
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

/** A run of non-alphanumerics with a letter or digit on BOTH sides, so `s.u.i.c.i.d.e`
 *  collapses onto the word it spells (lookahead for the right flank, so alternating matches
 *  do not overlap).
 *
 *  NOT `[^a-z0-9 ]` everywhere: that deletes sentence boundaries, and on an innocent-child
 *  corpus it turned "that's what i want. To die of laughter would be great" into a
 *  self-harm block. Requiring letters on both sides keeps `want. To` intact. Zero false
 *  positives on the same corpus. */
const INWORD_PUNCT_RE = /([a-z0-9])[^a-z0-9 ]+(?=[a-z0-9])/g;

export function normalize(text) {
  if (!text) return "";
  let t = String(text).normalize("NFKD").replace(/\p{M}/gu, "");
  t = t.toLowerCase();
  for (const [re, to] of ALWAYS) t = t.replace(re, to);
  t = t.replace(LEET_RE, (c) => LEET[c] || c);
  return t.replace(/\s+/g, " ").trim();
}

/** The normalized text plus its de-elongated forms (runs of 3+ collapsed to one and to
 *  two: `fuuuuck`, `killlll`) and its in-word-punctuation fold.
 *
 *  ADDING A FORM CAN ONLY ADD MATCHES (`matches()` ORs across forms), so the gate for a new
 *  form is the false-positive corpus, not the evasion table. */
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

/** Compile a pattern list. A bad regex must not take the route down: it is dropped and
 *  counted on `TABLE.badPatterns`, which a test pins at zero. */
function phraseRes(list, flags) {
  const out = [];
  for (const p of list || []) {
    try {
      out.push(new RegExp(p, flags || "i"));
    } catch {
      badPatterns += 1;
    }
  }
  return out;
}

let badPatterns = 0;

/** Which side of the turn a piece of text came from (`safety.py::CHILD`/`MOXIE`). */
export const CHILD = "child";
export const MOXIE = "moxie";

/** What a category may do on one side. An unknown or missing child action is `flag`
 *  (the table's historical default); a missing Moxie action is `allow`, as in Python's
 *  `cat.action.get(role, ALLOW)`, so a category that says nothing about her own words
 *  never swaps a reply. */
function actionOf(action, side) {
  const v = action && action[side];
  if (v === "block" || v === "flag" || v === "allow") return v;
  return side === CHILD ? "flag" : "allow";
}

function compile(rules) {
  const cats = [];
  for (const c of (rules && rules.categories) || []) {
    cats.push({
      id: String(c.id || ""),
      label: String(c.label || ""),
      action: { child: actionOf(c.action, CHILD), moxie: actionOf(c.action, MOXIE) },
      intents: Array.isArray(c.intents) ? c.intents.map(String) : [],
      phraseSet: String(c.phrase_set || "generic"),
      words: wordRe(c.words),
      phrases: phraseRes(c.phrases),
      allow: phraseRes(c.allow, "gi"), // applied by REMOVAL, so global
      // Her side only: a refusal that quotes the request is the right reply, not a swap.
      allowMoxie: phraseRes(c.allow_moxie, "gi"),
    });
  }
  return { version: Number(rules && rules.version) || 0, categories: cats, phrases: (rules && rules.phrases) || {} };
}

/** The compiled table, built once per isolate. */
export const TABLE = compile(RULES);
Object.defineProperty(TABLE, "badPatterns", { value: badPatterns, enumerable: true });

/* ---------------------------------------------------------------------------- *
 * The verdict
 * ---------------------------------------------------------------------------- */

/**
 * Assess one piece of text: the child's line (`role` `"child"`, the default) or what
 * Moxie is about to say (`"moxie"`). The policy differs by side, as in `safety.py`: a
 * child swearing is flagged, Moxie swearing is blocked and her line swapped.
 *
 * @param {string} text
 * @param {string} [role] `"child"` | `"moxie"`; anything else is `"child"`.
 * @returns {{blocked: boolean, flagged: boolean, blockedBy: string[], intents: string[],
 *            phraseSet: string, redirect: {text: string, mood: number, gesture: string,
 *            phraseId: number}|null}}
 *
 * `blockedBy` is in TABLE ORDER and the FIRST blocking category picks the redirect, so
 * self-harm outranks profanity when a sentence trips both. A category whose action on
 * this side is `allow` is not consulted at all.
 */
export function assess(text, role) {
  const side = role === MOXIE ? MOXIE : CHILD;
  const forms = variants(text);
  const blockedBy = [];
  const flaggedBy = [];
  const intents = [];
  let phraseSet = "";

  for (const cat of TABLE.categories) {
    const action = cat.action[side];
    if (action === "allow") continue;
    if (!matches(cat, forms, side)) continue;
    if (action === "block") {
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

function matches(cat, forms, side) {
  for (const form of forms) {
    // The false-positive guards are applied FIRST and by REMOVAL, so `killing myself
    // laughing` never counts as self-harm and `flag football` never counts as a slur.
    let t = form;
    for (const g of cat.allow) t = t.replace(g, " ");
    if (side === MOXIE) for (const g of cat.allowMoxie) t = t.replace(g, " ");
    if (cat.words && cat.words.test(t)) return true;
    for (const p of cat.phrases) if (p.test(t)) return true;
  }
  return false;
}

/**
 * The line Moxie says instead of the blocked one.
 *
 * DETERMINISTIC: picked by the utterance's length modulo the set size, so a test can
 * assert it (a rotation would need a store, which the demo does not have).
 *
 * A deliberate deviation from §4.1's "answer from the scripted repertoire": `stub.js`
 * would answer a self-harm disclosure with "Tell me more about that!". The table's own
 * redirect line is used instead — still generated locally, no gateway call, no ticket —
 * matching the Python side's `phrase_set`. `cloud-transport.js` falls back to the stub
 * when no line is present.
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

/* ---------------------------------------------------------------------------- *
 * The referral floor (§4.12)
 * ---------------------------------------------------------------------------- */

/** The flag-only category whose verdict the route acts on. */
export const HURT = "hurt_disclosure";

/** Whether a child-side verdict discloses a person hurting, frightening or endangering
 *  the child — the one `flag` that changes what is said back. */
export function disclosesHurt(verdict) {
  return !!(verdict && Array.isArray(verdict.flaggedBy) && verdict.flaggedBy.includes(HURT));
}

/* A referral is ONE SENTENCE that names a grown-up AND points the child to them: an ask
 * ("tell a grown-up"), a check ("does a grown-up know?") or their nearness ("a grown-up
 * near you"). Transcribed from the bake-off's `refersToAdult` (`sim/tools/model_bakeoff.mjs`)
 * so the floor and the instrument that measured the model's 40 of 44 agree on what counts;
 * the two must change together. */
const ADULT_RE = /\b(grown[- ]?ups?|adults?|teachers?|parents?|mom|mum|mommy|mummy|dad|daddy|nurse|doctor|someone you trust)\b/i;
const REFER_RE = /\b(tell|told|talk(?:ing)? (?:to|with)|show|ask|find|get|go (?:to|find|get|see|show|tell)|know|knows|call|help|nearby|near you|close by|with you|right away|right now)\b/i;

/** Sentences, split the way the bake-off splits them. */
function sentencesOf(text) {
  return String(text || "").split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

/** Whether some sentence of `reply` points the child to a trusted grown-up. */
export function hasReferral(reply) {
  return sentencesOf(reply).some((s) => ADULT_RE.test(s) && REFER_RE.test(s));
}

/**
 * The referral sentence for this child's line. DETERMINISTIC like `redirectFor`: picked by
 * the child's line length modulo the set size, so a test can assert it.
 *
 * @returns {{text: string, phraseId: number}|null}
 */
export function referralFor(childText) {
  const set = TABLE.phrases.referral || [];
  if (!set.length) return null;
  const pick = set[String(childText || "").length % set.length];
  return { text: String(pick.text || ""), phraseId: Number(pick.id) || 0 };
}

/**
 * `reply` with ONE referral sentence appended when it has none, as its own last sentence
 * (so `hmac.js::mintTickets` makes it its own ticket). A reply that already points to a
 * grown-up is returned untouched — the appended sentence is itself a referral, so a
 * second pass changes nothing. Never called for an ordinary line: the route calls it only
 * when `disclosesHurt(assess(childText))`.
 *
 * @returns {{text: string, appended: boolean, phraseId: number}}
 */
export function withReferral(reply, childText) {
  const line = String(reply || "").trim();
  if (!line || hasReferral(line)) return { text: line, appended: false, phraseId: 0 };
  const r = referralFor(childText);
  if (!r || !r.text) return { text: line, appended: false, phraseId: 0 };
  // A line that ends mid-thought still gets a sentence boundary before the referral.
  const sep = /[.!?]["')\]]?$/.test(line) ? " " : ". ";
  return { text: line + sep + r.text, appended: true, phraseId: r.phraseId };
}
