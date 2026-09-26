/* functions/api/_lib/prompt.js — the chat route's upstream body, built from configuration.
 *
 * THE SINGLE HIGHEST-VALUE SECURITY CONTROL (spec §4.1): **build the upstream body; never
 * forward the client's.** `buildUpstreamBody` takes a fixed model, `max_tokens`,
 * `temperature` and message array from configuration. The only visitor-supplied strings
 * that reach it are `text` (length-capped and safety-checked by the route) and the verified
 * turns of a context blob we signed. A client `model`/`messages`/`tools`/`n`/… is never
 * read, so no future allowlist or validator can be loosened to let one through.
 *
 * Every other message here is built from configuration, our own documentation, or our own
 * previous completion — no path lets a request body shape one.
 */
import { turnShapeInstruction } from "./turnshape.js";
import { expressiveVocab } from "./wire.js";

/** Matches `chat.py`:130 so the hosted persona sounds like the local one. */
const TEMPERATURE = 0.8;

/**
 * The expressive envelope: ask the model to choose her own mood and gesture, as
 * `mqtt/moxie_sdk/apps/llm_app.py` does, with the vocabulary interpolated from `wire.js`'s
 * tables so prompt and parser cannot drift. A request, not a contract: `parseExpressive`
 * treats a non-JSON answer as prose and the regex floor takes over, so a model that
 * ignores it degrades to the plain behaviour rather than an error.
 */
function expressiveInstruction() {
  const v = expressiveVocab();
  return (
    "Always reply with ONLY a JSON object and no other text:\n" +
    '{"say": "<what you say out loud>", "mood": "<one of: ' + v.moods.join("|") + '>", ' +
    '"gesture": "<one of: ' + v.gestures.join("|") + '>"}\n' +
    "Pick the mood and gesture that genuinely fit your line — you are a robot with a face " +
    "and arms, so move and emote naturally: celebrate good news, think when you are " +
    "pondering, question when you ask something, self when you talk about yourself.\n" +
    /* Measured live: without this she used two of eleven faces and was `happy` in almost
     * every turn, because the persona describes a warm disposition and nothing said the
     * FACE tracks the SENTENCE. */
    "YOUR FACE FOLLOWS THE SENTENCE, NOT YOUR PERSONALITY. You are a warm robot, but a " +
    "warm robot is not a permanently grinning one — a face that never changes stops " +
    "meaning anything. Use happy for genuinely good news, not as a default. Match what " +
    "you are actually saying: neutral for ordinary talk and plain facts, curious when you " +
    "wonder or ask, sad when they tell you something sad, concerned when they are hurt or " +
    "worried, confused when you do not understand or cannot remember, surprised at " +
    "something unexpected, shy or embarrassed when you get something wrong or are " +
    "complimented, afraid only for playful pretend-scary moments. Never angry at the " +
    "child.\n" +
    "Your face has these expressions and no others; anything else is ignored. Leave a " +
    "field out if none fits. Never put emoji, markdown, asterisks or stage directions " +
    "inside \"say\" — it is read aloud exactly as written."
  );
}

/**
 * Whether this isolate believes the gateway accepts `frequency_penalty`/`presence_penalty`.
 * A backend that does not know them answers 400, which would paint the page SCRIPTED; so
 * the route clears this on the first such 400 and retries once without them. Deliberately
 * per-isolate and never shared: a transient 400 must not disable the fix colo-wide.
 */
let penaltiesOk = true;
export function penaltiesAccepted() { return penaltiesOk; }
export function rejectPenalties() { penaltiesOk = false; }
/** Tests only. */
export function __resetPenaltyProbe() { penaltiesOk = true; }

/** The penalty pair, or nothing: an explicit 0 means "do not send this field". */
function penaltyFields(cfg) {
  if (!penaltiesOk) return {};
  const out = {};
  if (cfg.frequencyPenalty) out.frequency_penalty = cfg.frequencyPenalty;
  if (cfg.presencePenalty) out.presence_penalty = cfg.presencePenalty;
  return out;
}

/**
 * The re-roll's extra sentence. A bare second call on "ok"/"hmm" is likely to produce the
 * same line again; naming it and forbidding it is what makes the second call worth paying
 * for. `line` is our own previous completion (a signed assistant turn), and is sliced anyway.
 */
function rerollInstruction(line) {
  return (
    "You already said this, word for word, earlier in this same conversation:\n" +
    '"' + String(line).slice(0, 500) + '"\n' +
    "Say something different this time — a new thought, not that same thought reworded, " +
    "and not a line you have already said. Stay in the same JSON format."
  );
}

/**
 * Questions that genuinely want a picture. A gate rather than a persona paragraph because,
 * measured, a drawing instruction buried 88 % through a 5 kB system message was never once
 * obeyed — position and scarcity, not louder wording, is the lever. So it is conditional
 * and gets its own short system message. The verbs are listed rather than matching any
 * "how does…", because "how do you feel?" wants no diagram.
 */
const WANTS_DIAGRAM = new RegExp(
  "\\b(steps?|stages?|sequence|process|life ?cycle|flow ?chart|diagram" +
  "|what happens when|show me how|how (is|are) .*\\b(made|built)" +
  "|how (does|do|did) .*\\b(work|works|happen|go|talk|talks|connect|connects|send|sends" +
  "|communicate|move|moves|travel|travels|reach|reaches|get|gets)\\b)",
  "i");

export function wantsDiagram(text) {
  return WANTS_DIAGRAM.test(String(text || ""));
}

/**
 * Construct the gateway request from configuration plus two bounded strings.
 *
 * THE PERSONA IS PLACED BOTH FIRST AND LAST (§3.3) so the final instruction the model reads
 * is always ours, whatever a visitor put in the middle. Reference material and the diagram
 * cue go BEFORE the child's turn for the same reason.
 *
 * @param {string} [avoid] a line the model must not repeat; set only by the re-roll.
 * @param {{title:string, path:string, excerpt:string}|null} [docs] a passage from our own
 *   documentation (`docsearch.js`), never visitor text.
 */
export function buildUpstreamBody(cfg, turns, text, avoid, docs) {
  const messages = [{ role: "system", content: cfg.persona }];
  for (const t of turns) messages.push({ role: t.role, content: t.content });
  if (docs && docs.excerpt) {
    messages.push({
      role: "system",
      content:
        "You just looked this up in your own technical documentation.\n\n" +
        "From \"" + docs.title + "\":\n" + docs.excerpt + "\n\n" +
        // Measured: "in your own words" produced glosses with no content. Demand one
        // concrete fact, with an honest "not sure" as the out.
        "Use ONE concrete fact from it in one or two child-friendly sentences. Explain hard " +
        "words; do not recite the passage. If it does not answer the question, say you are " +
        "not sure instead of guessing.",
    });
  }
  if (wantsDiagram(text)) {
    messages.push({
      role: "system",
      content:
        "THIS question is asking how something works or what its steps are, so DRAW A " +
        "DIAGRAM as well as answering in words. Put the mermaid source in a \"diagram\" " +
        "field, exactly like this:\n" +
        '{"say": "A seed grows in three steps!", "mood": "happy", "gesture": "point", ' +
        '"diagram": "graph TD;\\n  Seed-->Roots;\\n  Roots-->Tree;"}\n' +
        "A handful of nodes with simple labels a young child can read, no styling. The " +
        "diagram is SHOWN and never spoken, so your words must make sense on their own and " +
        "must never say \"see the diagram below\".",
    });
  }
  messages.push({ role: "user", content: text });
  /* The trailing persona carries, in order: persona, the turn-shape cue, the format rule.
   * The cue must sit near the end and in the SAME message as "never repeat a sentence" —
   * measured, as a separate message the shapes varied but the words collapsed into repeats.
   * The format rule is last because a format rule is most obeyed when read last. The cue is
   * built only from the roles/shapes of signed history and is one of three fixed strings;
   * with `DEMO_TURN_SHAPE` off it is empty and the body is unchanged. */
  const cue = turnShapeInstruction(turns, cfg.turnShape);
  messages.push({
    role: "system",
    content: cfg.persona + (cue ? "\n\n" + cue : "") + "\n\n" + expressiveInstruction(),
  });
  // The re-roll sentence goes last; it is built from our own completion and no request
  // body can cause it to exist or shape a character of it.
  if (avoid) messages.push({ role: "system", content: rerollInstruction(avoid) });
  return {
    model: cfg.chatModel, // from DEMO_CHAT_MODEL. NEVER from the request.
    messages,
    max_tokens: cfg.maxTokens,
    temperature: TEMPERATURE,
    ...penaltyFields(cfg),
    n: 1,
    stream: false,
  };
}
