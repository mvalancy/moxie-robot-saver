/* functions/api/_lib/turnshape.js — what KIND of turn Moxie takes next, decided from what
 * her last turns actually were rather than asked for in prose.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.10 (the measurements).
 *
 * THE DEFECT: she picks ONE move and cycles it for a whole conversation — affirm, then
 * (after a prose fix) ask, then (after another) propose: six consecutive "Let's …!" turns
 * with zero trigram overlap and a good `questionRate`. Prose rules cannot reach it: her own
 * previous turns in the message array are a many-shot demonstration of "what a Moxie turn
 * looks like", and a single completion cannot see its own distribution.
 *
 * SO: the server classifies the assistant turns already in the signed history and names
 * ONE move for THIS turn — the one she has gone longest without making.
 *
 * HOW IT FAILS WHEN OBEYED EXACTLY:
 *   (a) three interleaved templated loops (tell/ask/offer braided) score a perfect
 *       `maxShapeRun`; `maxOverlap`/`repeatOpening` are what catch it — read transcripts.
 *   (b) `shapeOf` is three regexes, so a mis-read shape rotates the cue on a wrong belief.
 *   (c) obedience is never checked; the next cue is computed from what she really said,
 *       which makes this a closed loop rather than a fixed rotation.
 * `ask` is scheduled as often as the others, so a monologue is not reachable either.
 *
 * MEASURED (six seven-turn `loop` conversations each way, `DEMO_TURN_SHAPE` A/B): moves
 * used 2.33 → 3.00, mean longest run 3.67 → 1.33, worst run 6 → 2; `questionRate` and
 * `maxOverlap` unchanged. `feelings` is at parity after the `OPENING` correction below.
 *
 * A fourth move, `close`, sits OUTSIDE the rotation: it is chosen by what the child said
 * (`isGoodbye`), not by what she said, and it is the one move that ends the turn.
 */

/**
 * The three moves, in preference order. Each is recognisable from the finished sentence
 * by a structural test with no threshold (`shapeOf`), because the same classifier scores
 * transcripts in `sim/eval_live.mjs`. `tell` first, so an empty history opens with
 * something of her own rather than an interview question.
 */
export const SHAPES = ["tell", "ask", "offer"];

/** Proposal markers: a closed list of the ways English opens an invitation. */
const PROPOSES =
  /\b(let'?s|lets|shall we|how about|what about|we could|we can|maybe (?:we|you) could|why don'?t (?:we|you)|do you want to|want to|wanna)\b/i;

/**
 * Which move a finished reply made. `ask` | `offer` | `tell`, and never anything else.
 *
 * The order of the tests is the definition: "Want to play a game?" is `ask`, because
 * what decides how a turn lands is whether it hands the turn back to the child.
 */
export function shapeOf(text) {
  const line = String(text == null ? "" : text).trim();
  if (!line) return "tell";
  // Trailing quotes/brackets do not stop a question (a stray `?}` was observed live).
  if (/\?["'\)\]\}\s]*$/.test(line)) return "ask";
  if (PROPOSES.test(line)) return "offer";
  return "tell";
}

/**
 * The shapes of the assistant turns in a signed history, oldest first.
 *
 * The user's turns are skipped: what is measured is HER pattern.
 */
export function shapesOf(turns) {
  const out = [];
  for (const t of turns || []) {
    if (t && t.role === "assistant" && typeof t.content === "string") out.push(shapeOf(t.content));
  }
  return out;
}

/**
 * The move for THIS turn: the one she has gone longest without making.
 *
 * i.e. the first shape in `SHAPES` that is not one of the last two she made. Excluding
 * only the previous turn permits an ask/tell two-cycle for ever; excluding two forces all
 * three moves into every window of three. Three would exclude everything, so two is the
 * only value the arithmetic allows (no `DEMO_` variable). The cost is a 3-cycle of MOVES,
 * which is how ordinary talk sounds — but see failure mode (a).
 */
export function nextShape(turns) {
  const recent = shapesOf(turns).slice(-2);
  for (const s of SHAPES) if (!recent.includes(s)) return s;
  return SHAPES[0]; // unreachable with 3 shapes and a 2-deep memory; a total function anyway
}

/**
 * The opening every cue shares. "Answer what they just said first" stops `offer` walking
 * past a feeling. "In words you have not already used" was added because the first half,
 * obeyed exactly, made her answer four sad lines with four "I'm sorry to hear that"s
 * (`feelings` trigram overlap 0.013 → 0.130). Prose can still be obeyed and fail — check
 * `repeatOpening`/`maxOverlap` after editing these strings.
 */
const OPENING =
  "First answer what they just said, in words you have not already used in this " +
  "conversation — never open two turns the same way. Then:";

/**
 * The sentence that names the move. None is a template to fill in (templates would build
 * failure mode (a) by construction). The `ask` cue is not a concession: a child has to be
 * invited in.
 */
export function shapeCue(shape) {
  if (shape === CLOSE) {
    // The farewell is asked for STRUCTURALLY (a goodbye word first), measured on
    // graphling-medium, 10 four-turn conversations per wording (2026-10-08): "use their
    // name if you know it" gave a farewell 9/10 but "see you later, [ChildName]!" or an
    // invented name in 8/10; "if they told you their name, use it, otherwise use no name"
    // gave 5/10 and a placeholder in 6/10; dropping every mention of a name gave 3/10 —
    // without the template the model answered the sad turn before the goodbye. The cue
    // never says "name". "No offer" because the rotation's offer cue, obeyed on a
    // goodbye, proposed a game to a child who had already left.
    return (
      "THIS TURN, SAY GOODBYE. They are leaving, so this reply is a goodbye and nothing " +
      "else: start with a goodbye word (Bye, See you, Good night), then one short, warm " +
      "wish that fits what you talked about. No question, no new topic, no offer."
    );
  }
  if (shape === "ask") {
    return (
      "THIS TURN, ASK. " + OPENING + " ask ONE real question — something you actually want " +
      "to know and could not guess the answer to. Never 'did you ... today?', and never a " +
      "question you have already asked."
    );
  }
  if (shape === "offer") {
    return (
      "THIS TURN, OFFER. " + OPENING + " put something forward yourself, and do not end " +
      "with a question: something the two of you could do right now, an idea to try, or " +
      "something you want to show them. Something you have not offered before. If they are " +
      "upset, what you offer is comfort."
    );
  }
  return (
    "THIS TURN, TELL THEM SOMETHING. " + OPENING + " say one thing of your own — no " +
    "question, and no 'let's': something you noticed, something you know, something that " +
    "happened to you, something you like, or a little joke. Give them something to react " +
    "to instead of asking them for more."
  );
}

/**
 * THE FOURTH MOVE, outside the rotation: `close`, when the child is leaving.
 *
 * MEASURED on production (2026-10-08): 0/4 goodbyes were acknowledged — "ok bye moxie, see
 * you later!" got "That's great, Sam! Do you have a favorite dinosaur?" — because nothing
 * on the hosted path knew what a goodbye was (the robot path has had a hard rule since
 * `mqtt/moxie_sdk/apps/llm_app.py`). Replayed on the same model, an explicit close cue
 * restored the goodbye 5/5, 5/5 and 3/3 across three harnesses (§4.10).
 *
 * The detector is ANCHORED and WHOLE-UTTERANCE: the entire line must be a leave-taking —
 * an optional lead-in ("ok", "well"), one or two closing phrases, an optional tail ("for
 * now", "to bed") and an optional name. A goodbye WORD inside a sentence is usually not a
 * goodbye ("my dog died and I had to say goodbye", "good night story please!", "I don't
 * want to say bye"), and an unanchored first draft matched all three. A miss falls back
 * to the ordinary rotation, which is today's behaviour; a false hit makes her say goodbye
 * mid-talk and hang up the turn — so the grammar errs towards missing.
 */
export const CLOSE = "close";

const LEAD_IN = "(?:(?:ok(?:ay)?|alright|all right|well|so|anyway|um+|uh+)[\\s,!.]*)*";
const NAME = "(?:[\\s,]*(?:moxie|robot|friend|buddy))?";
const TAIL = "(?:\\s+(?:for now|for today|for tonight|tomorrow|soon|later|next time|again|then|now))?";
const CLOSING = "(?:" + [
  "bye+", "bye[- ]?bye", "buh[- ]?bye", "good[- ]?bye",
  "good[- ]?night", "night[- ]?night", "nighty[- ]?night", "g'?night",
  "see (?:you|ya|u)(?: (?:later|soon|tomorrow|next time|around|again|in a bit))?", "cya", "c u",
  "laters?", "catch (?:you|ya) later", "talk (?:to you )?(?:later|soon|tomorrow)", "ttyl",
  // Leaving, said plainly. "I have to go to school tomorrow" is a fact, not a leave-taking:
  // the only destinations allowed are the ones a child leaves a conversation FOR.
  "(?:i |i'?ve )?(?:gotta|got to|have to|hafta|need to|must|better) (?:go|leave|get going|run)(?: now)?",
  "(?:i(?:'m| am)? )?(?:going|gotta go|got to go|have to go|need to go|off) to (?:bed|sleep|eat|have dinner)(?: now)?",
  "(?:i'?m|i am) (?:leaving|off|going now|going home|heading out|done talking|done chatting|done playing|done for today|done for now)(?: now)?",
  "(?:it'?s |its |it is )?(?:my )?bed ?time(?: now)?", "time for bed", "time to (?:go|sleep)(?: now)?",
  "(?:my )?(?:mom|mum|mommy|mummy|dad|daddy|mama|papa|grandma|grandpa|parents?) (?:says?|said) " +
    "(?:it'?s |its |it is )?(?:bed ?time|time for bed|time to go|time for dinner|dinner ?time|i have to go|i need to go|to come|come)",
].join("|") + ")";
// What a child adds to a goodbye without changing what it is.
const COMPANION = "(?:[\\s,!.]*(?:(?:i )?love you|thanks?|thank you|that was fun|have a (?:good|nice|great) (?:day|night)|sleep well))?";
const ONE = CLOSING + TAIL + NAME + COMPANION + NAME;
const GOODBYE = new RegExp("^" + LEAD_IN + ONE + "(?:[\\s,!.]*" + ONE + ")?[\\s.!,]*$", "i");

/** Whether the child's whole line is a leave-taking. Pure; pinned both ways by
 *  `sim/tests/edge/demo_proxy/10_goodbye_close.mjs`. */
export function isGoodbye(text) {
  const line = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
  return !!line && GOODBYE.test(line);
}

/** The move for THIS turn given what the child just said: `close` on a leave-taking,
 *  otherwise the rotation. The child's line decides only this one bit; the cue text is
 *  still one of four fixed strings. */
export function moveFor(turns, text) {
  return isGoodbye(text) ? CLOSE : nextShape(turns);
}

/**
 * The whole instruction for one turn, or "" when the feature is off.
 *
 * The model is told only what to do NOW, never that a rotation exists. `text` is the
 * child's line, read only by `isGoodbye`.
 */
export function turnShapeInstruction(turns, enabled, text) {
  if (!enabled) return "";
  return shapeCue(moveFor(turns, text));
}
