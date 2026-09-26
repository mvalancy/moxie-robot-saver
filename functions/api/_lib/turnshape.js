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
 * The model is told only what to do NOW, never that a rotation exists.
 */
export function turnShapeInstruction(turns, enabled) {
  if (!enabled) return "";
  return shapeCue(nextShape(turns));
}
