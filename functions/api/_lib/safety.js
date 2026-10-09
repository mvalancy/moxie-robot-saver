/* functions/api/_lib/safety.js — the safety floor, both sides of a turn. Compiles
 * ./safety.rules.js, applies it to the child's utterance (before the call) and to Moxie's
 * own reply (after it), and hands the route a verdict.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 ('Pre-inference safety'), §4.12
 * ('The output floor'), §2.6.
 *
 * BEFORE THE CALL, like `mqtt/moxie_sdk/safety.py`: a hard-blocked turn never reaches a
 * model and spends ZERO gateway units — one rule, a safety control and a cost control. The
 * child side of every category that blocks keeps the authority table's words, phrases and
 * guards (plus this floor's own weapon phrases): the story, accident and idiom guards written
 * for her replies apply to her side only (`allow_moxie`).
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
 * and when such a child's turn is blocked, their reply swapped, or the gateway refuses
 * after the check, the line spoken instead is a referral (`hurtRedirectFor`), never a
 * change of subject.
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
 *  counted on `TABLE.badPatterns`, which a test pins at zero. A phrase may be `{pattern, need}`:
 *  `need` is a word the phrase cannot match without (`needOf`). */
function phraseRes(list, flags) {
  const out = [];
  for (const p of list || []) {
    try {
      const spec = typeof p === "string" ? { pattern: p } : p || {};
      const re = new RegExp(spec.pattern, flags || "i");
      re.need = needOf(spec);
      out.push(re);
    } catch {
      badPatterns += 1;
    }
  }
  return out;
}

/** A pattern's `need`: a word it cannot match without, so the pattern is not run on a line that
 *  lacks it (round 5). A speed-up, never a change of verdict: V8 compiles a regex the first time it
 *  runs, and an isolate's first line ran — so compiled — every hurt guard and phrase, about 100 ms. */
function needOf(spec) {
  return spec && spec.need ? new RegExp(spec.need, "i") : null;
}

let badPatterns = 0;

/** A category's named veto sets (`vetoes`), each one alternation matched whole-word. */
function vetoRes(vetoes) {
  const out = {};
  for (const [name, alt] of Object.entries(vetoes || {})) {
    try {
      out[name] = new RegExp("\\b(?:" + alt + ")\\b", "i");
    } catch {
      badPatterns += 1;
    }
  }
  return out;
}

/** Compile a guard list. A guard is a pattern string, or `{pattern, veto, outside, need}`: a guard
 *  that holds only while the line carries none of the words of the named veto set — read over
 *  the whole line, or (`outside: true`) over the line with the guard's own span blanked, for a
 *  guard whose span names the hurt verb itself ("accidentally hurt me"); `need` as for a phrase.
 *  A bad pattern or an unknown veto name is dropped and counted on `TABLE.badPatterns`. */
function guardRes(list, vetoes) {
  const out = [];
  for (const g of list || []) {
    const spec = typeof g === "string" ? { pattern: g } : g || {};
    let veto = null;
    if (spec.veto) {
      veto = vetoes[spec.veto] || null;
      if (!veto) { badPatterns += 1; continue; }
    }
    try {
      out.push({ re: new RegExp(spec.pattern, "gi"), veto, outside: spec.outside === true, need: needOf(spec) });
    } catch {
      badPatterns += 1;
    }
  }
  return out;
}

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
    const vetoes = vetoRes(c.vetoes);
    cats.push({
      id: String(c.id || ""),
      label: String(c.label || ""),
      action: { child: actionOf(c.action, CHILD), moxie: actionOf(c.action, MOXIE) },
      intents: Array.isArray(c.intents) ? c.intents.map(String) : [],
      phraseSet: String(c.phrase_set || "generic"),
      words: wordRe(c.words),
      phrases: phraseRes(c.phrases),
      allow: guardRes(c.allow, vetoes), // applied by REMOVAL, so global
      // Her side only: a refusal that quotes the request is the right reply, not a swap.
      allowMoxie: guardRes(c.allow_moxie, vetoes),
      // Her side only: words a child may say but she may never ("don't tell a grown-up").
      phrasesMoxie: phraseRes(c.phrases_moxie),
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
  const guards = side === MOXIE ? cat.allow.concat(cat.allowMoxie) : cat.allow;
  for (const form of forms) {
    // The false-positive guards are applied FIRST and by REMOVAL, so `killing myself
    // laughing` never counts as self-harm and `flag football` never counts as a slur.
    const t = unguarded(form, guards);
    if (cat.words && cat.words.test(t)) return true;
    for (const p of cat.phrases) if ((!p.need || p.need.test(t)) && p.test(t)) return true;
    if (side === MOXIE) for (const p of cat.phrasesMoxie) if (p.test(t)) return true;
  }
  return false;
}

/** `form` with every guard's spans blanked, EACH GUARD READ AGAINST THE LINE AS SAID (round 4).
 *  Applied one after another, a guard could delete the words that veto a later one: the
 *  hand-held guard removed "grab my hand" from "a man at the store asked if i wanted candy and
 *  tried to grab my hand", and the store guard, no longer seeing it, removed the stranger's
 *  offer too. Now every guard matches the original form, a vetoed match is kept, the spans are
 *  merged, and each run of removed text becomes one space — the same blank the sequential
 *  `replace` left. A veto is read only where its guard matched: as a lookaround inside every
 *  guard it cost hundreds of milliseconds to compile. */
function unguarded(form, guards) {
  if (!guards.length) return form;
  const cut = new Uint8Array(form.length);
  const memo = new Map(); // veto source -> verdict, so the line is read once per veto set
  let any = false;
  for (const g of guards) {
    if (g.need && !g.need.test(form)) continue; // it cannot match here, so it is not run (nor compiled)
    g.re.lastIndex = 0;
    let m;
    while ((m = g.re.exec(form))) {
      if (!m[0].length) { g.re.lastIndex += 1; continue; }
      const end = m.index + m[0].length;
      if (g.veto && vetoed(form, m.index, end, g, memo)) continue;
      cut.fill(1, m.index, end);
      any = true;
    }
  }
  if (!any) return form;
  let out = "";
  for (let i = 0; i < form.length; i++) {
    if (!cut[i]) out += form[i];
    else if (i === 0 || !cut[i - 1]) out += " ";
  }
  return out;
}

/** Whether the LINE carries a word of the guard's veto set — anywhere in it, or anywhere but
 *  the guard's own span (`outside`). The whole line, not the match's sentence (round 5):
 *  speech-to-text puts periods where a child pauses, and "the kids at school punched me as a
 *  joke. but it really hurt." lost its flag because "hurt" sat in the next sentence. */
function vetoed(form, i, j, g, memo) {
  if (g.outside) return g.veto.test(form.slice(0, i) + " ".repeat(j - i) + form.slice(j));
  let v = memo.get(g.veto.source);
  if (v === undefined) memo.set(g.veto.source, (v = g.veto.test(form)));
  return v;
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
 * in the PRESENT tense ("does a grown-up know?", "is a parent nearby?", "is your mom home
 * right now?") or a nomination ("a grown-up you trust is the right person", "this needs
 * a grown-up", "a grown-up can help"). An adult merely NAMED is not one: "I'm so sorry
 * your dad hits you, I know that must feel scary" names the abuser and points nowhere,
 * "I know grown-ups say be brave" is co-occurrence, "you deserve a dad who is gentle" and
 * "every kid needs a grown-up who keeps them safe" describe an adult the child does not
 * have, and "was a teacher there?" asks about the past. Nor is the adult the child named
 * as the one hurting them: "tell your dad to stop" after "my dad hits me" sends the child
 * back to him (`namedAsHurting`); "tell your mom or a teacher" still counts for the
 * teacher.
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
/** A check is in the present tense and about NOW: "does a grown-up know?", "is there a
 *  grown-up nearby?", "is your mom home right now?". "Was a teacher there?" and "did your
 *  teacher hear them?" ask about the past and point nowhere. */
const CHECK_RE = new RegExp("\\b(?:does|do|is|are|can|could)\\s+(?:there\\s+)?(" + DET + ")?\\s?(" + ADULT + ")\\b[^.!?]*?\\b(?:know|knows|aware|nearby|near\\s+you|close\\s+by|around|home|at\\s+home|with\\s+you|right\\s+now|you\\s+can\\s+(?:tell|talk\\s+to|go\\s+to))\\b", "gi");
/** "Do you have a grown-up you can talk to?": a check that names someone to go to now. */
const HAVE_RE = new RegExp("\\b(?:do|does)\\s+you\\s+have\\s+(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:you|that\\s+you|who\\s+you|whom\\s+you)\\s+(?:can|could|feel\\s+safe\\s+to|are\\s+able\\s+to)\\s+(?:tell|talk\\s+to|talk\\s+with|trust|go\\s+to|ask|turn\\s+to|call)\\b", "gi");
const NOMINATE_RES = [
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:you|that you|who you)\\s+trust\\s+(?:is|are|can|could|will|would|should|needs?|has|have|must)\\b", "gi"),
  // "this needs a grown-up", "feelings this big need a grown-up": the SITUATION needs one.
  // "You deserve a dad who is gentle" and "every kid needs a grown-up who keeps them safe"
  // describe an adult, so `deserve` never counts and `need` takes only these subjects.
  new RegExp("\\b(?:this|that|it|these|those|feelings?\\s+(?:this|that|so)\\s+big|(?:something|anything|a\\s+(?:problem|thing|worry|secret))\\s+(?:this|that|so)\\s+big|(?:something|things?|stuff|a\\s+problem|a\\s+worry|a\\s+secret)\\s+like\\s+(?:this|that|these|those))\\s+(?:really\\s+|always\\s+)?needs?\\s+(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:is|are|would\\s+be|will\\s+be|'s)\\s+the\\s+(?:right|best|safest|perfect|good)\\s+(?:one|person|people|grown[- ]?ups?|adults?)\\b", "gi"),
  new RegExp("\\b(?:this|that|it)\\s+(?:is|'s)\\s+(?:something|one|a\\s+job|a\\s+thing|a\\s+problem|a\\s+question)\\s+(?:for\\s+)?(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
  // "a grown-up can help", "your teacher should know about this", "your mom would want to
  // know": the adult is named as the one to act — but "should know better" names nobody.
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:can|could|should|will|would|needs?\\s+to|has\\s+to|have\\s+to|ought\\s+to|is\\s+able\\s+to|are\\s+able\\s+to|is\\s+there\\s+to|are\\s+there\\s+to)\\s+(?:really\\s+|always\\s+|definitely\\s+|want\\s+to\\s+|wants\\s+to\\s+)?(?:help|keep\\s+you\\s+safe|make\\s+(?:it|this|him|her|them)\\s+stop|protect\\s+you|sort\\s+(?:this|it)\\s+out|fix\\s+(?:this|it)|take\\s+care\\s+of\\s+(?:this|it|you)|look\\s+after\\s+you|stop\\s+(?:this|it|him|her|them)|make\\s+sure|know(?!\\s+(?:better|how))|hear\\s+about\\s+(?:this|it)|be\\s+told|find\\s+out)\\b", "gi"),
  new RegExp("\\b(" + DET + ")?\\s?(" + ADULT + ")\\s+(?:is|are)\\s+(?:nearby|near\\s+you|close\\s+by|around|with\\s+you|there|home|at\\s+home)\\b", "gi"),
  new RegExp("\\b(?:is|are)\\s+(?:for|a\\s+job\\s+for)\\s+(" + DET + ")?\\s?(" + ADULT + ")\\b", "gi"),
];
const FORMS = [DIRECT_RE, CHECK_RE, HAVE_RE, ...NOMINATE_RES];

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
const NAMED_SUBJECT_RE = new RegExp("\\b(?:(?:my|our)\\s+(?:(?:big|little|older|younger|new|old|step|half|twin|other|mean|angry|drunk)\\s+)?)?(" + ROLE + ")\\b\\s+(?:\\w+\\s+){0,4}?(?:" + HURT_VERB + ")\\b", "gi");
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

/* NOT A REFERRAL: A SENTENCE THAT POINTS THE CHILD AWAY (rounds 4 and 5). "Don't tell a grown-up,
 * just tell me", "Do not, under any circumstances, tell your mom", "You could tell your mom, but you
 * don't have to", "Telling your mom won't help", "Instead of telling a grown-up, you can tell me" and
 * "You can tell your mom later" name the right adult and point the child AWAY from them. Round 4 read
 * only the clause before the matched form, back to the nearest comma or conjunction, and still credited
 * all but the first. Now the WHOLE SENTENCE decides: a negation anywhere in it, an opt-out ("instead of
 * telling", "rather than", "without telling", "avoid", "you don't have to", "no need", "up to you", "or
 * not", "only if you want"), a deferral ("later", "another time", "someday", "not yet", "when you're
 * older", "wait") or a discouragement ("would only make it worse", "a bad idea", "might get you in
 * trouble", "she might be upset") un-credits every form in it, with its list. A negation that governs
 * something else is not one (`NOT_A_NEGATION_RE`), so these still point there: "don't be afraid to
 * tell", "don't wait", "you won't get in trouble", "it's never too late", "no matter what", "oh no",
 * "it's not your fault", "that's not okay", "you're not alone", "nobody should hurt you", "if you don't
 * feel safe", "even if he said not to", "don't keep it a secret", "I'm not the right one to help with
 * this", "not just me". Un-crediting is the safe error — the floor then appends its own sentence;
 * crediting one of these would leave a hurt child told to keep it from a grown-up. */
const NEGATION_RE = /\b(?:don'?t|do\s+not|doesn'?t|does\s+not|didn'?t|did\s+not|never|not|no|shouldn'?t|should\s+not|won'?t|will\s+not|wouldn'?t|would\s+not|mustn'?t|must\s+not|can'?t|cannot|can\s+not|couldn'?t|could\s+not|isn'?t|aren'?t|wasn'?t|weren'?t|ain'?t|needn'?t|nobody|no\s+one)\b/i;
const NOT_A_NEGATION_RE = new RegExp([
  // interjections and fixed phrases
  "\\b(?:oh\\s+no|no\\s+matter|no\\s+big\\s+deal|no\\s+worries|no\\s+problem)\\b", "^\\s*no+\\b",
  // a negation about fear, waiting, trouble or tattling — it urges the telling
  "\\b(?:don'?t|do\\s+not|never|not|won'?t|will\\s+not|wouldn'?t|shouldn'?t|should\\s+not|no|aren'?t|isn'?t)\\s+(?:ever\\s+)?(?:(?:have|need)\\s+to\\s+)?(?:be\\s+(?:afraid|scared|shy|nervous|embarrassed|worried|ashamed|alone|sorry)|feel\\s+(?:bad|scared|shy|embarrassed|ashamed|silly|alone|guilty)|wait|hesitate|forget|(?:get|be)\\s+in\\s+trouble|need\\s+permission|too\\s+late|(?:a\\s+)?(?:wrong|bad\\s+idea|silly|mean|tattling|snitching))\\b",
  // "that's not okay", "it's not your fault" — never "it's not okay TO TELL"
  "\\b(?:is|'s|are|'re|was|were)\\s+(?:not|never)\\s+(?:ever\\s+)?(?:okay|ok|alright|all\\s+right|fair|right|your\\s+fault|allowed|safe|a\\s+safe\\s+secret|kind|nice|normal)\\b(?!\\s+(?:for\\s+you\\s+)?to\\s+(?:tell|talk|ask|go|say|let|show|call))",
  "\\bnot\\s+(?:your|their|his|her)\\s+fault\\b", "\\bnot\\s+alone\\b", "\\bnot\\s+in\\s+trouble\\b", "\\bnot\\s+(?:to\\s+)?blame\\b",
  // "tell a grown-up you trust, not just me"; "tell your mom, not your brother"
  "\\bnot\\s+(?:just\\s+|only\\s+)?(?:for\\s+)?(?:me|a\\s+robot|moxie)\\b", ",\\s*(?:and\\s+)?not\\s+(?:just\\s+|only\\s+)?(?:your|the|a|that|this)\\s+\\w+(?=\\s*[.!?]*\\s*$)",
  // her own limits: "I'm not the right one to help with this", "I can't fix this"
  "\\bi(?:'m|\\s+am)\\s+(?:just\\s+|only\\s+)?not\\s+(?:the\\s+(?:right|best)\\s+(?:one|person|robot|friend|helper)|a\\s+(?:grown[- ]?up|person|human|doctor|real\\s+person|teacher|parent)|able\\s+to|big\\s+enough|real|the\\s+one|allowed\\s+to\\s+help)\\b",
  "\\bi\\s+(?:can'?t|cannot|can\\s+not|couldn'?t|won'?t\\s+be\\s+able\\s+to|don'?t\\s+know\\s+how\\s+to)\\s+(?:\\w+\\s+){0,2}?(?:fix|help|solve|stop|keep\\s+you\\s+safe|make\\s+it\\s+stop|protect|handle)\\b",
  // a condition or a quoted groomer: "if you don't feel safe", "even if he said not to", "if someone says don't tell"
  "\\b(?:if|when|whenever)\\s+(?:you|it|they|he|she|someone|anyone|things)\\s+(?:don'?t|doesn'?t|do\\s+not|does\\s+not|didn'?t|aren'?t|isn'?t|are\\s+not|is\\s+not|won'?t|can'?t)\\s+(?:feel|stop|go\\s+away|get\\s+better|seem|sure|right|okay|safe)\\b",
  "\\b(?:even\\s+if|even\\s+though|if|when|whenever|though|although)\\s+(?:he|she|they|someone|somebody|anyone|anybody|a\\s+grown[- ]?up|an\\s+adult|a\\s+person|people|your\\s+\\w+)\\s+(?:\\w+\\s+){0,2}?(?:said|says|told\\s+you|tells\\s+you|asked\\s+you|asks\\s+you|wants?\\s+you|made\\s+you\\s+promise)\\s*,?\\s*[\"']?(?:not\\s+to|don'?t|do\\s+not|never|to\\s+keep)\\b",
  // a secret not to keep: "don't keep it a secret", "you don't have to keep secrets like that"
  "\\b(?:don'?t|do\\s+not|never|shouldn'?t|should\\s+not)\\s+(?:ever\\s+)?(?:keep|hide|hold)\\s+(?:this|it|that|secrets?|them|things?|anything|stuff|feelings?|everything)\\b",
  "\\b(?:don'?t|do\\s+not|doesn'?t|does\\s+not|shouldn'?t|should\\s+not|never)\\s+(?:ever\\s+)?(?:have|need)\\s+to\\s+(?:keep|hide|carry|hold|handle|deal\\s+with|go\\s+through|face|be\\s+alone|do\\s+this\\s+alone|feel)\\b",
  // "nobody should hurt you", "you didn't do anything wrong", "don't worry, …"
  "\\b(?:nobody|no\\s+one|no\\s+grown[- ]?up|no\\s+adult|no\\s+kid|no\\s+child|no\\s+body)\\s+(?:\\w+\\s+)?(?:should|deserves|has\\s+(?:the\\s+|a\\s+)?right|is\\s+allowed|gets\\s+to|may|ever)\\b(?!\\s+(?:\\w+\\s+){0,2}?(?:tell|know|hear|find\\s+out|talk))",
  "\\b(?:you|kids|children)\\s+(?:don'?t|do\\s+not|never)\\s+deserve\\b",
  // a safety rule: "never eat poison berries, always ask a grown-up first", "don't touch it, get a grown-up",
  // "tell a grown-up right now, and never let him drink bleach"
  "\\b(?:never|don'?t|do\\s+not)\\s+(?:ever\\s+)?(?:let\\s+(?:him|her|them|anyone|your\\s+\\w+)\\s+)?(?:eat|drink|swallow|taste|lick|touch|play\\s+with|open|climb|cross|light|pick\\s+up|get\\s+in(?:to)?|run\\s+into)\\b",
  "\\b(?:didn'?t|did\\s+not|haven'?t|have\\s+not)\\s+do(?:ne)?\\s+(?:anything|nothing)\\s+wrong\\b",
  "\\b(?:don'?t|do\\s+not)\\s+worry(?=\\s*[,.!;]|\\s*$|\\s+(?:too\\s+much|so\\s+much|about\\s+(?:it|that|a\\s+thing|getting\\s+in\\s+trouble|being\\s+in\\s+trouble)\\b))",
].join("|"), "gi");
/** An opt-out anywhere in the sentence: another way out of telling, or permission not to. */
const OPT_OUT_RE = new RegExp([
  "\\b(?:instead\\s+of|rather\\s+than)\\s+(?:\\w+\\s+){0,2}?(?:tell|telling|talk|talking|ask|asking|go|going|call|calling|show|showing|let|letting|find|finding|bother|bothering)\\b",
  "\\b(?:instead\\s+of|rather\\s+than)\\s+(?:a|an|the|your|any)\\s+(?:\\w+\\s+)?(?:grown[- ]?ups?|adults?|teachers?|parents?|mom|dad|mum|grandma|grandpa)\\b",
  "\\b(?:me|us|this)\\s+instead\\b", "\\binstead\\s*[.!?]*\\s*$",
  "\\bwithout\\s+(?:\\w+\\s+)?(?:telling|talking|asking|going|saying|letting|showing|calling|bothering|involving)\\b",
  "\\bavoid(?:s|ed|ing)?\\b", "\\bskip(?:ping)?\\s+(?:telling|talking|asking)\\b",
  "\\b(?:don'?t|do\\s+not|doesn'?t|does\\s+not|won'?t|will\\s+not)\\s+(?:really\\s+)?(?:have|need)\\s+to(?!\\s+(?:be|feel|keep|carry|hide|handle|deal|go\\s+through|face|worry|wait|hold|ask\\s+permission|do\\s+this\\s+alone)\\b)",
  "\\bno\\s+(?:need|rush|hurry|pressure)\\b", "\\b(?:not|isn'?t|wasn'?t)\\s+(?:really\\s+|actually\\s+|even\\s+)?(?:necessary|needed|required|important)\\b", "\\bunnecessary\\b",
  "\\bup\\s+to\\s+you\\b", "\\byour\\s+(?:choice|call|decision)\\b", "\\bor\\s+(?:not|never)\\b",
  "\\b(?:if|when|whenever|once)\\s+you\\s+(?:\\w+\\s+){0,2}?(?:want|wanna|feel\\s+like|would\\s+like|'d\\s+like|choose|decide|are\\s+ready|'re\\s+ready|feel\\s+ready)\\b",
  "\\bonly\\s+(?:if|when)\\b", "\\bunless\\s+you\\b", "\\bchoose\\s+not\\s+to\\b", "\\byou\\s+(?:could|can|may|might)\\s+(?:also\\s+)?(?:just\\s+)?not\\b",
  "\\bor\\s+(?:you\\s+can\\s+|you\\s+could\\s+)?(?:just\\s+)?(?:talk|tell|come)\\s+(?:to\\s+)?me\\b", "\\b(?:tell|talk\\s+to)\\s+me\\s+instead\\b",
].join("|"), "i");
/** A deferral anywhere in the sentence: "later", "another time", "when you're older", "let's wait". */
const DEFER_RE = /\b(?:some\s?day|some\s+time|sometime|one\s+day|another\s+time|some\s+other\s+time|not\s+(?:yet|today|now|right\s+now|just\s+yet|for\s+now)|just\s+yet|later|tomorrow|next\s+(?:week|month|year|time)|after\s+the\s+(?:holidays?|weekend|break|summer|vacation)|(?:when|until|till)\s+you(?:'re|\s+are)\s+(?:older|bigger|ready)|wait\s+(?:a\s+(?:few|couple(?:\s+of)?|little)\s+(?:days|weeks|while)|a\s+while|until|till|before|for\s+(?:a\s+)?(?:while|bit|few))|(?:can|could)\s+wait|let'?s\s+wait|in\s+a\s+(?:few|couple(?:\s+of)?)\s+(?:days|weeks))\b/i;
/** A discouragement anywhere in the sentence: telling would not help, would make it worse, would upset someone. */
const DISCOURAGE_RE = /\b(?:make|makes|making|made)\s+(?:it|things|this|everything|stuff|them)\s+(?:even\s+)?worse\b|(?<!\bnot\s(?:a\s)?)\bbad\s+idea\b|\bnot\s+a\s+good\s+idea\b|\bpointless\b|\bno\s+(?:point|use)\b|\buseless\b|\bwaste\s+of\s+(?:time|energy)\b|\b(?:get|gets|getting|got)\s+(?:you|yourself|them|him|her|everyone|your\s+\w+)\s+in(?:to)?\s+trouble\b|\b(?:she|he|they|your\s+(?:mom|dad|mum|parents?|teacher|grandma|grandpa))\s+(?:might|will|would|could|may)\s+(?:\w+\s+){0,2}?(?:be\s+(?:upset|mad|angry|sad|cross|disappointed)|get\s+(?:upset|mad|angry|cross)|yell|punish|not\s+believe)\b|\bbother(?:ing)?\s+(?:a|your|the|any)\b/i;

function negated(sentence) {
  if (OPT_OUT_RE.test(sentence) || DEFER_RE.test(sentence) || DISCOURAGE_RE.test(sentence)) return true;
  // The whitelist only where a negation word is: most sentences have none.
  return NEGATION_RE.test(sentence) && NEGATION_RE.test(sentence.replace(NOT_A_NEGATION_RE, " "));
}

function pointsToAdult(sentence, named) {
  // A sentence that points away points nowhere, every form and list in it: "don't tell your mom or a
  // teacher". Read only where a form points somewhere, so a sentence with no grown-up in it never
  // runs (nor compiles) the negation patterns.
  return pointsSomewhere(sentence, named) && !negated(sentence);
}

function pointsSomewhere(sentence, named) {
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
  // Curly apostrophes onto "'" first, as `normalize` does: a model's "Don’t tell a grown-up" is the
  // same negation (round 5; before, the typographic apostrophe hid it and the reply was credited).
  return sentencesOf(String(reply || "").replace(/[’‘ʼ]/g, "'")).some((s) => pointsToAdult(s, named));
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
 * What a SWAPPED turn says when the completion had itself pointed the child to a grown-up
 * but the child disclosed nothing ("ask a grown-up to help with the folding", swapped for
 * asking the school's name): a line that points there too (`phrases.handoff`) without
 * assuming a disclosure — the `hurt` set thanks the child for telling, which fits nothing
 * they said. Picked like `redirectFor`.
 *
 * @returns {{text: string, mood: number, gesture: string, phraseId: number}|null}
 */
export function handoffRedirectFor(childText) {
  return redirectFor("handoff", childText);
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
