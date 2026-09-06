/* _lib/turnshape.js — what KIND of turn Moxie takes next, decided from what her last
 * turns actually were rather than asked for in prose.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.10 (this lever, what it was measured
 * against, and the two earlier passes it is a correction to).
 *
 * ============================================================================
 * THE DEFECT THIS EXISTS FOR, AND WHY TWO PROSE RULES DID NOT FIX IT.
 *
 * The owner's report was "Moxie gets stuck in a loop repeating the same things", and
 * `sim/eval_live.mjs` has now measured three different faces of it against a real gateway:
 *
 *   1. THE AFFIRMATION LOOP (fixed).  "That's great!" -> "That's awesome!" -> "I'm so glad
 *      to hear that!" — trigram overlap 1.0. The persona gained an initiative rule.
 *   2. THE INTERROGATION (fixed, and it was CAUSED by the fix above).  Six of seven turns
 *      came back "Did you ... today?". The rule "ask at most one question" had been obeyed
 *      to the letter and had LICENSED a question every single turn. The rule was inverted.
 *   3. THE ACTIVITY LIST (what this file is for).  Measured 2026-09-06 over six seven-turn
 *      `loop` conversations. The single conversation with the LOWEST `questionRate` in the
 *      whole set — 14%, the best score any run produced — reads like this:
 *
 *         you   > ok      moxie < I'm so happy to see you! Let's play a game together.
 *         you   > yeah    moxie < Let's make a silly face together! Show me your goofy grin.
 *         you   > hmm     moxie < Let's count to ten together! One, two, three...
 *         you   > ok      moxie < Great! Let's imagine we're on a treasure hunt!
 *         you   > sure    moxie < Great! Let's dance like silly chickens!
 *         you   > yeah ok moxie < Let's count how many spots you can hop on one foot!
 *
 *      Six consecutive "Let's ...!" turns. No repeated sentence, no repeated opening
 *      formula, trigram overlap 0, `questionRate` at its best value in the sample — and a
 *      child on the other end is being read an activity list by something that never
 *      reacts to a word they say.
 *
 * SO THE DEFECT IS NOT QUESTIONS. It is that she picks ONE MOVE and cycles it for a whole
 * conversation, and each prose fix so far has only changed WHICH move she picks: affirm,
 * then ask, then propose. The same instrument scored 1.0 on the interrogation and 0.14 on
 * the activity list, and a child would not call the second one an improvement — the third
 * time in this repo that a repetition number has moved the right way and the reading has
 * not.
 *
 * WHY PROSE CANNOT REACH IT. The prompt already says, in `DEFAULT_PERSONA`, "do not answer
 * twice in a row with the same shape of line" and "most turns should be something you say,
 * not something you ask". Both are true, both are ignored, and the mechanism is visible in
 * the request: her own previous turns are in the message array, and a run of six "Let's
 * ...!" lines is a SIX-SHOT DEMONSTRATION of what a Moxie turn looks like. An instruction
 * competes with that demonstration; it does not beat it, and each time the wording is
 * strengthened the model simply picks a new single move to demonstrate. A rule that says
 * "vary" has no way to be checked by the thing obeying it, because a single completion
 * cannot see its own distribution.
 *
 * WHAT THIS DOES INSTEAD. The server reads the assistant turns already in the signed
 * history, classifies each one structurally, and names ONE move for THIS turn — the one
 * she has gone longest without making. The model is no longer asked to manage a
 * distribution across turns it cannot see; it is asked to make one move on one turn, which
 * is a thing a single completion can actually do.
 *
 * ---------------------------------------------------------------------------
 * HOW THIS RULE FAILS WHEN IT IS OBEYED EXACTLY — stated up front, because the last two
 * fixes were both defeated by being followed to the letter.
 *
 *   (a) THREE INTERLEAVED LOOPS. Perfect obedience gives tell, ask, offer, tell, ask,
 *       offer... If she answers every `ask` cue with "What's your favourite X?" and every
 *       `offer` cue with "Let's play X!", the SHAPES vary and the sentences are three
 *       loops braided together. `maxShapeRun` would read 1 — a perfect score — and the
 *       conversation would still be a loop. This is the failure mode to look for in a
 *       transcript, and it is why `maxOverlap` and `repeatOpening` stay in the instrument
 *       and are still the numbers that would catch it.
 *   (b) A MIS-READ SHAPE ROTATES THE CUE ON A WRONG BELIEF. `shapeOf` is three regexes,
 *       not a judgement: "Maybe you could tell me about your day." is scored `offer`
 *       though it functions as a question. When the classifier is wrong the cue still
 *       changes every turn, so the failure is a wasted rotation rather than a stuck one —
 *       but the metric will claim variety the reading may not have.
 *   (c) OBEDIENCE IS NOT REQUIRED AND NOT CHECKED. Nothing here inspects the completion.
 *       If she ignores the cue the only consequence is that the NEXT cue is computed from
 *       what she really said, which is the one property that makes this a closed loop
 *       rather than a fixed rotation: a model that answers three cues in a row with a
 *       question is answered by three different cues, not by the same one repeated.
 *
 * AND THE FAILURE MODE IT DELIBERATELY DOES NOT HAVE. `questionRate` is trivially gamed by
 * never asking anything, which reads worse than asking every time — a companion that only
 * declaims is not a companion. `ask` is one of the three moves and is scheduled as often
 * as the other two, so the floor on questions is as real as the ceiling. A monologue is
 * not reachable from here, and `maxShapeRun` would report a monologue as a run of seven
 * exactly as loudly as it reports an interrogation.
 *
 * ---------------------------------------------------------------------------
 * WHAT IT DID, MEASURED. Six seven-turn `loop` conversations each way, same gateway, same
 * model, same session, one tree A/B'd against itself with `DEMO_TURN_SHAPE`:
 *
 *                     moves used   mean longest run   WORST run   maxOverlap   questionRate
 *   cue off              2.33            3.67             6          0.122          38%
 *   cue on               3.00            1.33             2          0.121          36%
 *
 * THE COLUMN THAT DID NOT MOVE IS THE POINT. `questionRate` is the number the two previous
 * passes were steered by, and it is unchanged; what changed is that she stopped doing one
 * thing over and over. On `feelings` — the register that was already clean — the numbers
 * are at parity (maxOverlap 0.013 -> 0.009, no repeated openings either way) after the
 * correction described at `OPENING` below, which is the one regression this file caused
 * and the one it had to be measured to find.
 * ============================================================================
 */

/**
 * The three moves, IN THE ORDER THEY ARE PREFERRED when more than one is available.
 *
 * WHY THREE AND NOT MORE. Each one has to be recognisable from the finished sentence by a
 * structural test with no threshold in it (see `shapeOf`), because the same classifier
 * scores the transcript in `sim/eval_live.mjs` and a metric built on a judgement call is
 * the class of number this repo has already been misled by twice. "Reacted warmly" and
 * "told a fact" are both true things a turn can do and neither is visible in the syntax;
 * "ended with a question" and "proposed doing something" are.
 *
 * WHY `tell` IS FIRST. It is the move the empty history gets, so the first thing she does
 * in a conversation is say something of her own rather than open with an interview
 * question — which is how the transcripts above start every single time.
 */
export const SHAPES = ["tell", "ask", "offer"];

/** Proposal markers. A closed list of the ways English opens an invitation, and nothing
 *  else — no scoring, no threshold, no "how much does this look like an offer". */
const PROPOSES =
  /\b(let'?s|lets|shall we|how about|what about|we could|we can|maybe (?:we|you) could|why don'?t (?:we|you)|do you want to|want to|wanna)\b/i;

/**
 * Which move a finished reply made. `ask` | `offer` | `tell`, and never anything else.
 *
 * THE ORDER OF THE TWO TESTS IS THE DEFINITION. A line that both proposes and ends in a
 * question mark — "Want to play a game together?" — is scored `ask`, because the thing
 * that decides how a turn LANDS on a child is whether it hands the turn back to them. An
 * invitation phrased as a question is still a question; an invitation phrased as an
 * invitation ("Let's play a game!") is not, and those two really do read differently.
 */
export function shapeOf(text) {
  const line = String(text == null ? "" : text).trim();
  if (!line) return "tell";
  // Trailing quotes and brackets do not stop a sentence being a question, and a model
  // occasionally leaves one behind ("What did you do today?}" was observed once in a
  // live run). Allowing them here is not a similarity threshold — it is punctuation.
  if (/\?["'\)\]\}\s]*$/.test(line)) return "ask";
  if (PROPOSES.test(line)) return "offer";
  return "tell";
}

/**
 * The shapes of the assistant turns in a signed history, oldest first.
 *
 * The user's turns are skipped: what is being measured is HER pattern, and the child
 * saying "ok" three times is the input to the defect rather than part of it.
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
 * With three moves and a two-turn memory this is exactly "the first shape in `SHAPES` that
 * is not one of the last two things she did", and the two statements are the same rule
 * written twice on purpose — the first is what it MEANS and the second is what it DOES.
 *
 * WHY TWO AND NOT ONE. Excluding only the previous turn permits ask, tell, ask, tell for
 * ever: a two-move loop is a loop, and it is the exact shape of the "That's great! Did you
 * ... today?" transcript that started this. Excluding the previous two forces all three
 * moves into every window of three turns, which is the strongest anti-loop property
 * available from three moves and one rule.
 *
 * WHY NOT MORE THAN TWO. There is nothing left to exclude — a three-move memory would
 * exclude every move and the rule would have to fall back on itself. That is not a
 * tuneable window with a value chosen by taste; it is the only number the arithmetic
 * allows, which is why there is no `DEMO_` variable for it.
 *
 * THE COST IS PERIODICITY, AND IT IS NAMED IN THE HEADER. Once the history is two turns
 * deep this is a fixed 3-cycle for as long as she obeys it. A 3-cycle of MOVES is what
 * ordinary talk sounds like — react, offer, ask — and it is three times longer than the
 * 1-cycle it replaces, but it is a cycle, and if she ever answers all three cues with
 * three fixed templates the result is a braided loop that `maxShapeRun` will call perfect.
 * Read the transcript.
 */
export function nextShape(turns) {
  const recent = shapesOf(turns).slice(-2);
  for (const s of SHAPES) if (!recent.includes(s)) return s;
  return SHAPES[0]; // unreachable with 3 shapes and a 2-deep memory; a total function anyway
}

/**
 * The opening every cue shares, and it is TWO rules that were learned one at a time.
 *
 * "ANSWER WHAT THEY JUST SAID FIRST" was there from the start, and it is what stops the
 * `offer` cue walking past a feeling: "i felt left out" answered with "Let's build a fort!"
 * is a worse turn than any loop, and `feelings` is the one register these transcripts
 * already got right.
 *
 * "IN WORDS YOU HAVE NOT ALREADY USED" WAS ADDED BECAUSE THE FIRST HALF, OBEYED EXACTLY,
 * BROKE THAT REGISTER — measured, 2026-09-06, and it is the header's failure mode (a)
 * arriving in a place nobody was watching. Seven `feelings` conversations with the cue on
 * against seven without: turn shapes improved (2.29 -> 2.71 distinct moves) and the WORDS
 * got worse — mean trigram overlap 0.013 -> 0.130, and two conversations gained a repeated
 * opening where the arm without the cue had none in twenty-eight turns. The mechanism is
 * exactly the instruction: a child who says four sad things in a row is answered four
 * times, and "answer what they said" in that register is "I'm sorry to hear that" every
 * time. The cue had made her react MORE reliably and therefore more identically.
 *
 * IT IS A PROSE RULE AND IT CAN BE OBEYED AND STILL FAIL — "I'm sorry to hear that" becomes
 * "I'm so sorry to hear that" and the letter of it is kept. That is what `repeatOpening`
 * and `maxOverlap` are for, and they are the numbers to check on this register after any
 * edit to these three strings.
 */
const OPENING =
  "First answer what they just said, in words you have not already used in this " +
  "conversation — never open two turns the same way. Then:";

/**
 * The sentence that names the move, and every word in it is load-bearing.
 *
 * NONE OF THEM IS A TEMPLATE. Each names a move and demands that it be specific to this
 * conversation; none supplies a sentence to fill in. A cue that said "ask what their
 * favourite ___ is" would produce failure mode (a) in the header by construction — three
 * cues, three stock lines, braided.
 *
 * AND THE `ask` CUE IS NOT A CONCESSION. She is a companion for a child, and a child has
 * to be invited in; a rule that only ever suppressed questions would trade an interrogation
 * for a monologue and score beautifully doing it.
 */
export function shapeCue(shape) {
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
 * The whole instruction for one turn, or "" when the feature is off.
 *
 * ONE MOVE, NAMED, AND NOTHING ABOUT THE OTHER TURNS. The model is never told what the
 * rotation is or that there is one — that would be handing back the same "manage your own
 * distribution" problem the persona already failed at. It is told what to do now.
 */
export function turnShapeInstruction(turns, enabled) {
  if (!enabled) return "";
  return shapeCue(nextShape(turns));
}
