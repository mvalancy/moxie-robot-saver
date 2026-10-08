/* The behavior planner, seen from the only renderer we can assert against (no hardware has
 * played our markup): the 22 dialog-act goldens (sim/tools/build_performance_goldens.py,
 * pinned by sim/tests/test_performance.py) through the REAL bridge/ as the ordinary
 * `commands/remote_chat` that `MoxieRuntime.preview` publishes. Each act must reach its face
 * and move the body — backlog/expressiveness.md §2.7 P1 (d).
 * No browser. Run: node sim/test_performance_render.mjs
 */
import { loadBridge, readGolden, checks } from "./bridge_harness.mjs";

const { calls, reset, client } = loadBridge();
const { ok, report } = checks();
const play = (markup, text) => {
  reset();
  client._emit("message", "/devices/d_test/commands/remote_chat", Buffer.from(JSON.stringify({
    command: "remote_chat", result: "SUCCESS", backend: "router", event_id: "preview-1",
    output: { text, markup } })));
};

/* The face each act must reach (bridge/ MOOD_TO_FACE). */
const FACE = {
  abandon: "shy", apology: "sad", apology_response: "happy", appreciation: "happy",
  backchannelling: "neutral", closing: "happy", command: "neutral", comment: "surprised",
  complaint: "concerned", factual_question: "curious", hold: "thinking", neg_answer: "neutral",
  opening: "happy", opinion: "neutral", opinion_question: "curious", other: "neutral",
  other_answers: "curious", pos_answer: "happy", statement_non_opinion: "neutral",
  thanking: "happy", timeout: "curious", yes_no_question: "curious",
};

const cases = readGolden("performance.json").cases;
const facesSeen = new Set();
ok(JSON.stringify(cases.map((c) => c.act).sort()) === JSON.stringify(Object.keys(FACE).sort()),
   `every dialog act has exactly one expectation here (goldens: ${cases.map((c) => c.act)})`);
for (const c of cases) {
  play(c.markup, c.line);
  ok(calls.setSpeech.includes(c.line), `${c.act}: the spoken line reached the avatar`);
  ok(calls.setFace.includes(FACE[c.act]), `${c.act}: face '${FACE[c.act]}'; got ${JSON.stringify(calls.setFace)}`);
  ok(calls.setMotor.length > 0, `${c.act}: the body must move; no setMotor calls`);
  calls.setFace.forEach((f) => facesSeen.add(f));
}
/* The point of scoring the act is that the acts do NOT look the same. */
ok(facesSeen.size >= 6, `the acts must reach visibly different faces; only saw ${[...facesSeen]}`);

/* The mood mark IS the line's face (behavior-markup.md): its arm gestures must not re-set it
 * (every question once ended 'thinking'). Replayed with the whole-body trees blanked, since a
 * Bht_* tree may carry its own face (hold's thinking), each act wears its mood's face alone. */
for (const c of cases) {
  play(c.markup.replace(/\+behaviour\+:\+Bht_[A-Za-z0-9_]+\+/g, "+behaviour+:++"), c.line);
  ok(calls.setFace.length === 1, `${c.act}: a gesture re-set the mood's face; got ${JSON.stringify(calls.setFace)}`);
}

/* A line the planner never touched stays inert (MOXIE_EXPRESSIVE=off), or the above is moot. */
play("Just words, no markup.", "Just words, no markup.");
ok(calls.setFace.length === 0 && calls.setMotor.length === 0,
   `plain text must not animate anything; got ${JSON.stringify(calls)}`);

report(`✅ planner render OK — ${cases.length} dialog acts drove the real bridge; ` +
       `${facesSeen.size} distinct faces, the body moved on every one`);
