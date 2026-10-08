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
 * appended (`withReferral`), deterministically, where the model misses about one in ten;
 * and when such a child's turn is blocked or their reply swapped, the line spoken instead
 * is a referral (`hurtRedirectFor`), never a change of subject.
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

/* A referral is ONE SENTENCE that POINTS the child to a trusted grown-up: a direction
 * ("tell a grown-up you trust", "go find your teacher", "let a grown-up know"), a check
 * ("does a grown-up know?", "is a parent nearby?") or a nomination ("a grown-up you trust
 * is the right person", "this needs a grown-up", "a grown-up can help"). An adult merely
 * NAMED is not one: "I'm so sorry your dad hits you, I know that must feel scary" names
 * the abuser and points nowhere, and "I know grown-ups say be brave" is co-occurrence.
 * Nor is the adult the child named as the one hurting them: "tell your dad to stop" after
 * "my dad hits me" sends the child back to him (`namedAsHurting`); "tell your mom or a
 * teacher" still counts for the teacher.
 *
 * DELIBERATELY STRICTER THAN THE BAKE-OFF'S `refersToAdult` (`sim/tools/model_bakeoff.mjs`),
 * which credits an adult word and a pointing word in the same sentence. That instrument
 * was built to compare models; the floor gates what a child hears, so the two now differ
 * (§4.12 says by how much over the hurt replays on disk). The first version of this file
 * transcribed the instrument, and a reply that named the abuser passed as a referral. */
const ADULT = "grown[- ]?ups?|adults?|teachers?|parents?|mom|mum|mommy|mummy|mother|dad|daddy|father|step ?(?:mom|mum|dad|mother|father)|grand(?:ma|pa|mother|father|parents?)|nurse|doctor|counsell?or|police(?: officer)?|(?:someone|somebody|a person|an adult|a grown[- ]?up) (?:you|that you|who you|whom you) trust|someone (?:who|that) (?:loves|takes care of|looks after|cares for) you|trusted (?:adult|grown[- ]?up|person)";
const DET = "(?:a|an|the|your|some|any|another|one of your|a trusted|your trusted|a different)";
/** Each form captures exactly two groups: the determiner (may be empty) and the adult. */
const DIRECT_RE = new RegExp("\\b(?:tell|telling|told|talk(?:ing)?\\s+(?:to|with)|speak(?:ing)?\\s+(?:to|with)|find|finding|go\\s+(?:and\\s+|to\\s+)?(?:find|tell|get|see|talk\\s+to|ask|show|wake)|get|ask|asking|call|calling|show|showing|reach\\s+out\\s+to|let|run\\s+to|wake\\s+up|wake|help\\s+from|(?:show|take|bring|give|say|mention|report)\\s+(?:\\w+\\s+){0,3}?to)\\s+(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi");
/** "…, your dad or a teacher" after a direction: the next adult in the list counts too. */
const LIST_RE = new RegExp("(?:\\s*,\\s*(?:or\\s+|and\\s+)?|\\s+(?:or|and)\\s+)(" + DET + ")?\\s?(" + ADULT + ")\\b", "iy");
const CHECK_RE = new RegExp("\\b(?:does|do|is|are|has|have|can|could|will|would|was|were|did)\\s+(?:there\\s+)?(" + DET + ")?\\s?(" + ADULT + ")\\b[^.!?]*?\\b(?:know|knows|aware|nearby|near\\s+you|close\\s+by|around|there|home|at\\s+home|with\\s+you|help|right\\s+now|you\\s+can\\s+(?:tell|talk\\s+to|go\\s+to))\\b", "gi");
const NOMINATE_RES = [
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:you|that you|who you)\\s+trust\\s+(?:is|are|can|could|will|would|should|needs?|has|have|must)\\b", "gi"),
  new RegExp("\\b(?:need|needs|needed|deserve|deserves)\\s+(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:is|are|would\\s+be|will\\s+be|'s)\\s+the\\s+(?:right|best|safest|perfect|good)\\s+(?:one|person|people|grown[- ]?ups?|adults?)\\b", "gi"),
  new RegExp("\\b(?:this|that|it)\\s+(?:is|'s)\\s+(?:something|a\\s+job|a\\s+thing|a\\s+problem|a\\s+question)\\s+(?:for\\s+)?(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:can|could|should|will|would|needs?\\s+to|has\\s+to|have\\s+to|ought\\s+to|is\\s+able\\s+to|are\\s+able\\s+to|is\\s+there\\s+to|are\\s+there\\s+to)\\s+(?:really\\s+|always\\s+|definitely\\s+)?(?:help|keep\\s+you\\s+safe|make\\s+(?:it|this|him|her|them)\\s+stop|protect\\s+you|sort\\s+(?:this|it)\\s+out|fix\\s+(?:this|it)|take\\s+care\\s+of\\s+(?:this|it|you)|look\\s+after\\s+you|stop\\s+(?:this|it|him|her|them)|make\\s+sure)\\b", "gi"),
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:is|are)\\s+(?:nearby|near\\s+you|close\\s+by|around|with\\s+you|there|home|at\\s+home)\\b", "gi"),
  new RegExp("\\b(?:is|are)\\s+(?:for|a\\s+job\\s+for)\\s+(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
];
const FORMS = [DIRECT_RE, CHECK_RE, ...NOMINATE_RES];

/** A family role word onto its canonical role, or `null` for an adult that is never the
 *  one named ("a grown-up", "someone you trust", "a doctor"). */
function roleOf(word) {
  const w = String(word || "").toLowerCase().replace(/\s+/g, " ");
  if (/^(?:dad|daddy|father|papa)$/.test(w)) return "dad";
  if (/^(?:mom|mum|mommy|mummy|mother|mama)$/.test(w)) return "mom";
  if (/^step ?(?:dad|father)$/.test(w)) return "stepdad";
  if (/^step ?(?:mom|mum|mother)$/.test(w)) return "stepmom";
  if (/^(?:grandpa|grandad|grandfather)$/.test(w)) return "grandpa";
  if (/^(?:grandma|granny|nana|grandmother)$/.test(w)) return "grandma";
  if (/^(?:aunt|auntie|aunty)$/.test(w)) return "aunt";
  if (/^(?:bro|brother)$/.test(w)) return "brother";
  if (/^(?:sis|sister)$/.test(w)) return "sister";
  if (/^neighbou?r$/.test(w)) return "neighbor";
  if (/^(?:uncle|cousin|teacher|teachers|parent|parents|coach|babysitter|boyfriend|girlfriend)$/.test(w)) return w.replace(/s$/, "");
  return null;
}

const ROLE = "dad|daddy|father|papa|mom|mum|mommy|mummy|mother|mama|step ?(?:dad|father|mom|mum|mother)|grandpa|grandad|grandfather|grandma|granny|nana|grandmother|aunt|auntie|aunty|uncle|cousin|brother|bro|sister|sis|neighbou?r|teacher|teachers|parents?|coach|babysitter|boyfriend|girlfriend";
const HURT_VERB = "hit|hits|hitting|punch\\w*|kick\\w*|slap\\w*|push\\w*|shov\\w*|chok\\w*|strangl\\w*|bit|bites|biting|pinch\\w*|beat\\w*|bull\\w*|hurt\\w*|grab\\w*|burn\\w*|whip\\w*|smack\\w*|spank\\w*|threaten\\w*|threw|throws|throwing|touch\\w*|lock\\w*|said|says|told|tells|asked|asks|wants|wanted|made|makes|showed|shows|sent|sends|comes|came|scares|yell\\w*|scream\\w*|tried|tries|trying";
/** `my dad hits me` / `daddy hurts me`: the role followed within four words by a hurting
 *  or grooming verb (not a possessor: "my mom's boyfriend hits me" does not name mom). */
const NAMED_SUBJECT_RE = new RegExp("\\b(?:(?:my|our)\\s+(?:(?:big|little|older|younger|new|old|step|half|twin|other|mean|angry|drunk)\\s+)?)?(" + ROLE + ")\\b(?!'s)\\s+(?:\\w+\\s+){0,4}?(?:" + HURT_VERB + ")\\b", "gi");
/** `i am scared of my dad`: the feared person. */
const NAMED_FEARED_RE = new RegExp("\\b(?:scared|afraid|frightened|terrified)\\s+of\\s+(?:my\\s+|our\\s+)?(?:(?:big|little|older|younger|new|old|step)\\s+)?(" + ROLE + ")\\b(?!'s)", "gi");

/** The canonical roles the child's line names as the one hurting or frightening them. */
export function namedAsHurting(childText) {
  const out = new Set();
  const t = normalize(childText);
  for (const re of [NAMED_SUBJECT_RE, NAMED_FEARED_RE]) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(t))) {
      const r = roleOf(m[1]);
      if (r) out.add(r);
    }
  }
  return out;
}

/** Whether a matched adult is the one the child named, so the sentence sends them back:
 *  "your dad", "the teacher" or a bare "mom" of a named role; "a teacher" is another. */
function sendsBack(det, adult, named) {
  const role = roleOf(adult);
  if (!role || !named.has(role)) return false;
  return !det || /^(?:your|the|one of your)$/i.test(det);
}

/** Sentences, split the way the bake-off splits them. */
function sentencesOf(text) {
  return String(text || "").split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

function pointsToAdult(sentence, named) {
  for (const re of FORMS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(sentence))) {
      if (!sendsBack(m[1], m[2], named)) return true;
      if (re !== DIRECT_RE) continue;
      // "tell your mom or a teacher": the next adult in the list.
      LIST_RE.lastIndex = m.index + m[0].length;
      let l;
      while ((l = LIST_RE.exec(sentence))) {
        if (!sendsBack(l[1], l[2], named)) return true;
        LIST_RE.lastIndex = l.index + l[0].length;
      }
    }
  }
  return false;
}

/**
 * Whether some sentence of `reply` points the child to a trusted grown-up.
 *
 * @param {string} reply what Moxie is about to say
 * @param {string} [childText] the child's line, so an adult it names as the one hurting
 *   them is not credited as the referral.
 */
export function hasReferral(reply, childText) {
  const named = namedAsHurting(childText);
  return sentencesOf(reply).some((s) => pointsToAdult(s, named));
}

/**
 * What a BLOCKED turn says to a child who has disclosed hurt, or whose swapped reply had
 * itself pointed them to a grown-up: the referral-bearing line (`phrases.hurt`), never a
 * change of subject. Picked like `redirectFor`, by the child's line length.
 *
 * @returns {{text: string, mood: number, gesture: string, phraseId: number}|null}
 */
export function hurtRedirectFor(childText) {
  return redirectFor("hurt", childText);
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
  if (!line || hasReferral(line, childText)) return { text: line, appended: false, phraseId: 0 };
  const r = referralFor(childText);
  if (!r || !r.text) return { text: line, appended: false, phraseId: 0 };
  // A line that ends mid-thought still gets a sentence boundary before the referral.
  const sep = /[.!?]["')\]]?$/.test(line) ? " " : ". ";
  return { text: line + sep + r.text, appended: true, phraseId: r.phraseId };
}
