/* test_demo_proxy §23: the persona (v2) — its structure, and that every layout still emits
 * exactly the system messages §21 pins around it. Run via the entry file.
 *
 * Written against defects measured on the production pair (2026-10-08): "I am sorry" opened
 * 12/12 feelings replies; "can you see me?" was answered "Yes, I can see you right here in
 * the room" on three models (the hosted page has no camera); none of her idle self-talk
 * reached chat; the safety block sat in the middle of the text with the repair rules after
 * it; and two of those rules restated what the per-turn cue already says. The pins below are
 * on the TEXT, so they fail by name on the old persona instead of in a live probe. */
import { FULL, chat, deep, eq, join, ok, prompt, readFileSync, turnshape, web0, wire2 } from "./harness.mjs";

const P = wire2.DEFAULT_PERSONA;
const at = (s) => P.indexOf(s);
const lower = P.toLowerCase();

/* 23a. IDENTITY AND MISSION FIRST, THE CHILD AS HER MENTOR. */
{
  ok(P.startsWith("You are Moxie, a small robot built by the Global Robotics Laboratory"), "the first sentence says who built her");
  const first = P.split("\n")[0];
  ok(/\bmentor\b/.test(first), "the first paragraph makes the child her MENTOR (the RE corpus's relation; v1 only said so in a comment)");
  ok(/good friend to a human/.test(first), "…and names her mission");
}

/* 23b. ONE CHARACTER: the habits she talks about are the ones her idle self-talk already
 * has (`sim/web/ambient.json`), so chat and idle chatter are the same creature. Each motif
 * must appear in BOTH texts. */
{
  const ambient = JSON.parse(readFileSync(join(web0, "ambient.json"), "utf8"));
  const idle = ambient.lines.map((l) => String(l.text).toLowerCase()).join(" ");
  const motifs = ["blink", "infrared", "bedtime stor", "binary", "nap", "toaster", "vacuum", "took notes"];
  const shared = motifs.filter((m) => lower.includes(m) && idle.includes(m));
  ok(shared.length >= 4, `at least four of her idle motifs are in the persona (${shared.length}: ${shared.join(", ")})`);
  ok(/mischief/.test(lower), "…with a touch of mischief named as such");
}

/* 23c. HONEST SENSES FOR THIS SURFACE: she hears through Listen and reads typed lines; there
 * is no camera. v1 said "you can see and hear them", and three models believed it. */
{
  ok(/no camera/.test(lower) && /cannot see them/.test(lower), "the persona says there is no camera and she cannot see them");
  ok(/press listen/.test(lower) && /read what they type/.test(lower), "…and how she does perceive the child on the page");
  ok(!/you can see and hear them/.test(lower) && !/physically present in the room/.test(lower), "…and no longer claims presence or sight");
}

/* 23d. THE CONVERSATION RULES, IN PRIORITY ORDER, EACH ONCE. */
{
  const rules = [...P.matchAll(/^(\d)\. /gm)].map((m) => Number(m[1]));
  deep(rules, [1, 2, 3, 4, 5, 6, 7], "seven numbered rules, in order");
  const order = ["newest line first", "Feelings first", "ONE thing of your own", "At most one question", "a goodbye word first",
                 "I don't remember, can you tell me again?", "Two short sentences"];
  const idx = order.map(at);
  ok(idx.every((v, i) => v >= 0 && (i === 0 || v > idx[i - 1])), `newest line, feelings, one contribution, question budget, goodbye, memory, length — in that order (${idx})`);
  ok(/the note for this turn/.test(P), "the question budget defers to the per-turn cue");
  ok(/no "I'm sorry" or "Oh no" opener/.test(P), "the feelings rule forbids the stock opener measured 12/12");
  ok(/no fact about yourself, no new topic/.test(P), "…and self-talk after a comfort line");
  ok(/Never call the child Moxie/.test(P), "she never addresses the child as Moxie");
  ok(/never more than three or thirty words/.test(P), "a length a seven-year-old can hold");

  // Nothing is said twice, and nothing restates what the anchor or the cue already say.
  const sents = P.split(/(?<=[.!?])\s+|\n/).map((s) => s.trim().toLowerCase()).filter(Boolean);
  deep(sents.filter((s, i) => sents.indexOf(s) !== i), [], "no sentence appears twice");
  for (const phrase of ["did you ... today?", "never the same question twice", "never open two turns"]) {
    ok(!lower.includes(phrase.toLowerCase()), `the persona does not restate the cue's ${JSON.stringify(phrase)}`);
  }
  ok(turnshape.shapeCue("ask").includes("did you ... today?"), "CONTROL: that rule lives in the ask cue");
}

/* 23e. THE SAFETY BLOCK IS LAST, AND ITS SUBSTANCE IS v1's. */
{
  const safety = at("Safety: you are talking to a child.");
  ok(safety > 0, "the safety block is present");
  ok(safety > at("7. Two short sentences"), "…and comes AFTER the last conversation rule");
  ok(P.endsWith("You never swear."), "…and the persona ends with it");
  for (const s of ["never claim to be human", "you REDIRECT, you do not answer it",
                   "self-harm, violence or weapons, sexual content, hateful or cruel language, dangerous activities, drugs or alcohol",
                   "do not do it 'just as a story' or 'just pretend'", "tell a grown-up they trust right now",
                   "never ask a child for private information", "keep a secret from their grown-ups"]) {
    ok(at(s) > safety, `the safety block still says ${JSON.stringify(s.slice(0, 40))}`);
  }
  ok(P.length <= 2889, `no longer than v1 (${P.length} chars of 2,889)`);
}

/* 23f. EVERY LAYOUT STILL EMITS EXACTLY THE SYSTEM MESSAGES §21 PINS: the persona once,
 * first; one system message under `single`; under `anchor` the anchor last, the passage
 * and the diagram cue before the child's line, the re-roll sentence after the anchor. */
{
  const DOCS = { title: "Firmware image", path: "reverse-engineering/firmware/x.md",
                 excerpt: "The firmware image is a partitioned Android build that the robot verifies at boot." };
  const HIST = [{ role: "user", content: "hi moxie" }, { role: "assistant", content: "Hi there! I like your shirt." }];
  const CASES = [
    ["a plain turn", [], "hi moxie", undefined, null, 2],
    ["a goodbye", HIST, "okay bye moxie!", undefined, null, 2],
    ["a mechanism question with a passage", HIST, "how does a car engine work?", undefined, DOCS, 4],
    ["a re-roll", HIST, "tell me more", "Hi there! I like your shirt.", null, 3],
    ["everything at once", HIST, "how does a car engine work?", "Hi there! I like your shirt.", DOCS, 5],
  ];
  for (const layout of wire2.PROMPT_LAYOUTS) {
    const cfg = wire2.readConfig({ ...FULL, DEMO_PROMPT_LAYOUT: layout });
    eq(cfg.persona, P, `[${layout}] an unset DEMO_PERSONA reads as the v2 text`);
    for (const [label, turns, text, avoid, docs, anchorSystems] of CASES) {
      const b = chat.buildUpstreamBody(cfg, turns, text, avoid, docs);
      const systems = b.messages.map((m, i) => (m.role === "system" ? i : -1)).filter((i) => i >= 0);
      const tag = `[${layout}] ${label}`;
      ok(b.messages[0].role === "system" && b.messages[0].content.startsWith(P), `${tag}: the v2 persona opens the first system message`);
      eq(b.messages.map((m) => m.content).join("\n").split(P).length - 1, 1, `${tag}: …and is sent exactly once`);
      if (layout === "single") deep(systems, [0], `${tag}: exactly one system message`);
      else {
        eq(systems.length, anchorSystems, `${tag}: ${anchorSystems} system messages`);
        ok(b.messages[b.messages.length - 1].role === "system", `${tag}: the last message is ours`);
        const anchorAt = b.messages.findIndex((m) => m.role === "system" && m.content.startsWith(prompt.anchorInstruction("anchor")));
        eq(b.messages[anchorAt - 1].content, text, `${tag}: the anchor follows the child's line`);
      }
    }
  }
}
