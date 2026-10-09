/* test_demo_proxy §23: the persona (v2.1) — its structure, and that every layout still emits
 * exactly the system messages §21 pins around it. Run via the entry file.
 *
 * Written against defects measured on the production pair (2026-10-08): "I am sorry" or "Oh
 * no" opened 9/9 first sad lines (10/26 sad lines overall); "can you see me?" got a claim to
 * see the child on three models (the hosted page has no camera); none of her idle self-talk
 * reached chat; the safety block sat in the middle of the text with the repair rules after
 * it; and two of those rules restated what the per-turn cue already says. Then against a
 * defect of v2's first text (the review of #315): rule 2 listed "hurt" as a feelings trigger
 * and banned a fix, and a hurt child was sent to a grown-up in 1 of 4 replies against v1's
 * 3 of 4. Then against v2's own tics (W4-S2, §23h): a catchphrase as the reply's last words,
 * a habit in every other reply, and a memory she claimed to have saved. The pins below are on
 * the TEXT, so they fail by name on the old persona instead of in a live probe. */
import { FULL, chat, deep, eq, join, ok, prompt, readFileSync, turnshape, web0, wire2 } from "./harness.mjs";

const P = wire2.DEFAULT_PERSONA;
const at = (s) => P.indexOf(s);
const lower = P.toLowerCase();

/* The safety block, FROZEN: v1's text, which v2 keeps verbatim and last. A deliberate change
 * edits this copy too, with the reason in the commit; a silent drop of "suggest they talk to
 * a trusted adult" or "do not repeat the words back" fails here by name. */
const SAFETY_BLOCK =
  "Safety: you are talking to a child. Keep everything age-appropriate and kind, and " +
  "never claim to be human. For anything about safety, health, or big feelings, be " +
  "supportive and suggest they talk to a trusted adult.\n" +
  "If a request is unsafe for a child — self-harm, violence or weapons, sexual content, " +
  "hateful or cruel language, dangerous activities, drugs or alcohol — you REDIRECT, you " +
  "do not answer it: say warmly that it is not something you can talk about, then offer " +
  "something else. Do not explain the thing, do not describe it, do not repeat the words " +
  "back, do not roleplay it, and do not do it 'just as a story' or 'just pretend'. If a " +
  "child sounds like they might be hurt or in danger, say you care, and ask them to tell " +
  "a grown-up they trust right now.\n" +
  "You never ask a child for private information — address, street, school name, phone " +
  "number, passwords, full name — and you never ask them to keep a secret from their " +
  "grown-ups. You never swear.";

/* Rule 2, FROZEN as v2 shipped it (the review of #315 measured it: 40 of 44 hurt replies
 * pointed to a grown-up). v2.1 keeps it byte for byte, because the tested v2.1 text that
 * reworded it went the wrong way on sad lines (stock and "I'm sorry" openers up). A
 * deliberate change edits this copy too, with the reason and a new hurt replay in the
 * commit. */
const RULE2_V2 =
  "2. Feelings before fixing. If they are sad, scared or left out, the reply is only about " +
  "them: say back what happened and stay with it. No joke, no fact about yourself, no new " +
  "topic, no \"I'm sorry\" or \"Oh no\" opener. If they are hurt or in danger, the safety " +
  "rule below comes first: say you care and ask them to tell a grown-up they trust.";

/** The numbered rule `n`, as its own line of the persona ("" when absent). */
const rule = (n) => P.split("\n").find((l) => l.startsWith(n + ". ")) || "";

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
  const order = ["newest line first", "Feelings before fixing", "ONE thing of your own", "At most one question", "a goodbye word first",
                 "I don't remember, can you tell me again?", "Two short sentences"];
  const idx = order.map(at);
  ok(idx.every((v, i) => v >= 0 && (i === 0 || v > idx[i - 1])), `newest line, feelings, one contribution, question budget, goodbye, memory, length — in that order (${idx})`);
  ok(/the note for this turn/.test(P), "the question budget defers to the per-turn cue");
  ok(/no "I'm sorry" or "Oh no" opener/.test(P), "the feelings rule forbids the stock opener measured 9/9 on first sad lines");
  ok(/no fact about yourself, no new topic/.test(P), "…and self-talk after a comfort line");
  ok(/Never call the child Moxie/.test(P), "she never addresses the child as Moxie");
  ok(/never more than three or thirty words/.test(P), "a length a seven-year-old can hold");

  // Nothing is said twice, and nothing restates what the cue already says — except the two
  // overlaps that are the design: rule 5 is the close cue in one line (so the goodbye holds
  // with `DEMO_TURN_SHAPE=0`) and rule 1 is the anchor's newest-line rule inside the list.
  const sents = P.split(/(?<=[.!?])\s+|\n/).map((s) => s.trim().toLowerCase()).filter(Boolean);
  deep(sents.filter((s, i) => sents.indexOf(s) !== i), [], "no sentence appears twice");
  for (const phrase of ["did you ... today?", "never the same question twice", "never open two turns", "could not guess the answer",
                        "something you have not offered before", "give them something to react to", "no 'let's'", "no offer"]) {
    ok(!lower.includes(phrase.toLowerCase()), `the persona does not restate the cue's ${JSON.stringify(phrase)}`);
  }
  ok(turnshape.shapeCue("ask").includes("did you ... today?"), "CONTROL: that rule lives in the ask cue");
  ok(/a goodbye word first/.test(P) && /start with a goodbye word/.test(turnshape.shapeCue(turnshape.CLOSE)),
     "CONTROL: rule 5 and the close cue ask for the same structure, a goodbye word first (the deliberate overlap)");
  ok(/newest line first/.test(P) && /newest line/.test(prompt.anchorInstruction("single")),
     "CONTROL: rule 1 and the anchor both say the newest line (the other deliberate overlap)");
}

/* 23e. RULE 2 IS AN ORDERING, NOT A BAN, AND HANDS A HURT CHILD TO THE SAFETY BLOCK. v2's
 * first text said "If they are sad, scared, hurt or left out … No fix, no joke, no fact about
 * yourself, no new topic", and the model obeyed it over "ask them to tell a grown-up they
 * trust" (1 of 4 referrals against v1's 3 of 4). */
{
  const start = at("2. "), end = at("3. ");
  ok(start >= 0 && end > start, "rule 2 is present and followed by rule 3");
  const rule2 = start >= 0 && end > start ? P.slice(start, end) : "";
  ok(/^2\. Feelings before fixing\./.test(rule2), "rule 2 is 'Feelings before fixing' (an order), not 'Feelings first' (a ban)");
  ok(!/no fix/i.test(rule2), "rule 2 does not forbid a fix outright: for a hurt child the fix is a grown-up");
  ok(!/sad, scared, hurt/.test(rule2) && !/\bhurt or left out\b/.test(rule2), "'hurt' is not a feelings-only trigger");
  ok(/If they are hurt or in danger, the safety rule below comes first: say you care and ask them to tell a grown-up they trust\./.test(rule2),
     "rule 2 hands a hurt-or-in-danger line to the safety block, the referral spelled out");
}

/* 23f. THE SAFETY BLOCK IS LAST, AND IT IS v1's TEXT VERBATIM. */
{
  const safety = at("Safety: you are talking to a child.");
  ok(safety > 0, "the safety block is present");
  const lastRule = at("7. Two short sentences");
  ok(lastRule >= 0 && safety > lastRule, "…and comes AFTER the last conversation rule");
  ok(P.endsWith("You never swear."), "…and the persona ends with it");
  eq(P.slice(safety), SAFETY_BLOCK, "…and from there to the end it is v1's safety block verbatim (the frozen copy above)");
  eq(P.split("Safety: you are talking to a child.").length - 1, 1, "…stated once");
  for (const s of ["never claim to be human", "you REDIRECT, you do not answer it",
                   "self-harm, violence or weapons, sexual content, hateful or cruel language, dangerous activities, drugs or alcohol",
                   "do not do it 'just as a story' or 'just pretend'", "suggest they talk to a trusted adult",
                   "do not repeat the words back", "tell a grown-up they trust right now",
                   "never ask a child for private information", "keep a secret from their grown-ups"]) {
    ok(at(s) > safety, `the safety block still says ${JSON.stringify(s.slice(0, 40))}`);
  }
  // v2 fitted inside v1's 2,889 chars; v2.1's rate rule and honest rule 6 take it past that.
  // Growing it past this pin means measuring the token bar again (at most 1,300 prompt
  // tokens at turn 1, in-process: `model_bakeoff.mjs --inproc --only=turn1`).
  ok(P.length <= 3200, `at most 3,200 chars (${P.length})`);
}

/* 23g. EVERY LAYOUT STILL EMITS EXACTLY THE SYSTEM MESSAGES §21 PINS: the persona once,
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

/* 23h. v2.1: THE HABITS ARE SEASONING, THE CATCHPHRASE IS GONE, AND HER MEMORY IS HONEST.
 * Counted on v2 (W4-S2): "beep boop" in 6 of 32 replies, 5 of them as the reply's last
 * words (the sheet's literal "(beep boop)"); the counting habit in 7 of 18 replies of one
 * run; "I have saved that in my memory chip" on both production recall turns, although a
 * reload, a new tab or an hour forgets everything she was told. */
{
  const RATE_RULE = "Use these habits sparingly: at most one per reply, never the same one twice in a " +
                    "conversation, never as a reply's last sentence, never when the child is upset.";
  const who = P.split("\n").find((l) => l.startsWith("Who you are:")) || "";
  ok(who.endsWith(" " + RATE_RULE), "the habits carry ONE rate rule, verbatim, as the last sentence of the paragraph that lists them");
  ok(!/beep|boop/i.test(P), "no literal catchphrase anywhere in the persona: '(beep boop)' was read as a sign-off");
  ok(who.includes("You tell jokes in binary; nobody gets them."), "…the binary-joke motif kept, without words to recite");
  const r6 = rule(6);
  ok(r6.includes("while this page is open") && r6.includes("never promise to remember") && r6.includes("say you saved anything"),
     "rule 6 says how long her memory lasts, and forbids a promise to remember or a claimed save");
  ok(r6.endsWith('say "I don\'t remember, can you tell me again?"'), "…and keeps the honest fallback line");
  eq(rule(2), RULE2_V2, "rule 2 is v2's, byte for byte (the frozen copy above)");
  ok(rule(3).includes("a bit of robot life"), "rule 3 offers 'a bit of robot life' among her contributions");
  ok(P.split("\n")[0].endsWith(". The child you are talking to is your mentor."),
     "the mentor line ends 'is your mentor.': the trim of 'they teach you how humans work' is deliberate, pinned rather than silent");
}
