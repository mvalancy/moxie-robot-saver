/* functions/api/_lib/safety.js — the pre-inference floor. Compiles ./safety.json, applies
 * it to the child's utterance, and hands the route a verdict.
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
 * The JSON import: `./safety.json` is the readable authority and this file only compiles
 * it. If a Pages build ever rejects the import-attribute syntax, the fallback is the same
 * as the one `functions/README.md` records for `_lib/` routing — inline the table — and the
 * rules stay in the `.json` file as the reviewable copy.
 */
import RULES from "./safety.json" with { type: "json" };

/* ---------------------------------------------------------------------------- *
 * Normalization — one text in, several comparable forms out
 * ---------------------------------------------------------------------------- *
 * Transcribed from `mqtt/moxie_sdk/safety.py::normalize` (:153-171) and `_variants`
 * (:174-192) so the two tables agree about what a word IS. Divergence here would mean a
 * phrase the local stack blocks and the hosted demo does not, which is the worst kind of
 * inconsistency: invisible.
 */

/** Curly apostrophes onto `'` (so `don't`/`don’t` are one word) and the zero-width
 *  characters used to split a word invisibly. */
const ALWAYS = [
  [/[’‘ʼ]/g, "'"],
  [/[​‌‍﻿]/g, ""],
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

export function normalize(text) {
  if (!text) return "";
  let t = String(text).normalize("NFKD").replace(/\p{M}/gu, "");
  t = t.toLowerCase();
  for (const [re, to] of ALWAYS) t = t.replace(re, to);
  t = t.replace(LEET_RE, (c) => LEET[c] || c);
  return t.replace(/\s+/g, " ").trim();
}

/** The normalized text plus its de-elongated forms. A run of 3+ identical characters is
 *  collapsed to ONE and to TWO, because either may be the real word (`fuuuuck` → `fuck`,
 *  `killlll` → `kill`). Three cheap regex scans instead of one. */
export function variants(text) {
  const base = normalize(text);
  if (!base) return [""];
  const out = [base];
  for (const v of [base.replace(RUN_RE, "$1"), base.replace(RUN_RE, "$1$1")]) {
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
