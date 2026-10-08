/* test_demo_proxy §19–22: the goodbye close, the prompt layouts, the brace-proof envelope
 * parser and the punctuation-folding echo. Run via the entry file.
 *
 * Written against a defect measured on production (2026-10-08): 0/4 goodbyes acknowledged —
 * she answered an earlier turn — because the whole 2,889-char persona was re-sent AFTER the
 * child's line (4,598 chars followed "Okay bye Moxie!") and no layer knew what a goodbye
 * was; and, offline, 9 of 11 completion shapes reached the TTS ticket with JSON braces in
 * them. Every accessor below is guarded so that on a tree without the fix this section
 * reports failures by name instead of crashing the runner. */
import {
  FULL, P, call, chat, deep, eq, execFileSync, fresh, hmac, ok,
  prompt, repo, sent, turnshape, upstreamCalls, wire, wire2,
} from "./harness.mjs";

const isGoodbye = typeof turnshape.isGoodbye === "function" ? turnshape.isGoodbye : () => undefined;
const CLOSE = turnshape.CLOSE || "close";
const anchorOf = typeof prompt.anchorInstruction === "function" ? prompt.anchorInstruction : () => "\u0000";
const LAYOUTS = wire2.PROMPT_LAYOUTS || [];
const SIGN_OFF = wire.SIGN_OFF || "Bht_Sign_off";
const treeMark = typeof wire.MK.tree === "function" ? wire.MK.tree : () => "\u0000";
const spoken = (r) => (r.body.messages[0] ? JSON.parse(r.body.messages[0].payload) : null);
const ticketText = async (r) => {
  if (!r.body.speech || !r.body.speech[0]) return "";
  const v = await hmac.verifyTicket(wire2.readConfig(FULL), r.body.speech[0].ticket);
  return v.ok ? String(v.claims.text || "") : "";
};

/* 19. THE DETECTOR: anchored and whole-utterance. A false hit makes her say goodbye mid-talk
 * and END THE TURN, so the negatives are the harder half of this table. */
{
  for (const t of [
    "bye", "Bye!", "okay bye moxie!", "Okay bye Moxie!", "ok bye bye moxie see you tomorrow!",
    "goodbye moxie", "good night moxie", "night night moxie", "see you!", "see ya later", "cya",
    "later moxie", "bye for now", "i have to go now", "gotta go!", "I have to go to bed",
    "I'm going to sleep now", "my mom says it's bedtime", "ok I'm done talking",
    "well, i'm leaving now", "goodbye moxie, i love you", "ok bye, thanks moxie", "BYE ROBOT",
    "  bye   moxie  ",
  ]) eq(isGoodbye(t), true, `a leave-taking: ${JSON.stringify(t)}`);

  for (const t of [
    "My dog died and I had to say goodbye", "My best friend moved away and we said goodbye",
    "I don't want to say bye", "Please don't say goodbye", "Good night story please!",
    "Goodnight Moon is my favorite book", "Bye-bye is what my baby brother says",
    "Goodbye in Spanish is adios, right?", "Guess where I'm going", "I'm going to tell you a secret",
    "I'm going to draw a cat", "I can see you", "I have to go to school tomorrow",
    "what does goodbye mean in french?", "my dog says bye to the mailman",
    "Some kids wouldn't let me play tag with them.", "Okay goodnight is my favorite word",
    "hi moxie", "ok", "", null, undefined,
  ]) eq(isGoodbye(t), false, `NOT a leave-taking: ${JSON.stringify(t)}`);
  // A pathological line must answer quickly, not hang the isolate on backtracking.
  eq(isGoodbye("bye ".repeat(120) + "x"), false, "a long run of goodbye words that is not a goodbye answers false, fast");

  // The move: `close` on a leave-taking, the rotation otherwise; the cue is a fixed string.
  const hist = [{ role: "user", content: "hi" }, { role: "assistant", content: "I like robots." }];
  eq(turnshape.moveFor && turnshape.moveFor(hist, "okay bye moxie!"), CLOSE, "a leave-taking picks the close move");
  eq(turnshape.moveFor && turnshape.moveFor(hist, "ok"), "ask", "anything else picks the rotation (ask, after a tell)");
  ok(/SAY GOODBYE/.test(turnshape.shapeCue(CLOSE) || "") && /no question/i.test(turnshape.shapeCue(CLOSE) || ""),
     "the close cue says goodbye and forbids a question");
  eq(turnshape.turnShapeInstruction(hist, true, "bye moxie"), turnshape.shapeCue(CLOSE), "the instruction for a goodbye turn IS the close cue");
  eq(turnshape.turnShapeInstruction(hist, false, "bye moxie"), "", "…and DEMO_TURN_SHAPE=0 still silences it");
  ok(!turnshape.SHAPES.includes(CLOSE) && turnshape.SHAPES.length === 3, "close is outside the three-move rotation");
}

/* 20. THROUGH THE ROUTE: the close cue replaces the rotation, the turn ENDS, and the sign-off
 * wave rides the markup — as the Python layer emits it, byte for byte. */
{
  fresh();
  P.plan = { chat: { content: "Hi there! I like your shirt." } };
  const t1 = await call(chat, "/api/chat", { text: "hi moxie" });
  const p1 = spoken(t1);
  eq(p1 && p1.end_turn, false, "an ordinary turn does not end the conversation");
  ok(p1 && !p1.output.markup.includes(SIGN_OFF), "…and carries no sign-off wave");

  P.plan = { chat: { content: '{"say": "Bye Sam! I hope tomorrow is better.", "mood": "happy", "gesture": "talk"}' } };
  const t2 = await call(chat, "/api/chat", { text: "okay bye moxie!", context: t1.body.context });
  const p2 = spoken(t2);
  deep([t2.res.status, t2.body.reason, p2 && p2.end_turn], [200, null, true], "a goodbye turn is served LIVE with end_turn: TRUE");
  ok(p2 && p2.output.markup.includes(treeMark(SIGN_OFF)), "…and the markup carries the sign-off tree mark, verbatim");
  ok(p2 && p2.output.markup.includes("+behaviour+:+Bht_Sign_off+") && p2.output.markup.includes("+eventName+:+Gesture_None+"),
     "…in the field bridge/body.js plays trees from, with the null gesture in the gesture slot");
  ok(p2 && !/Gesture_Talk/.test(p2.output.markup), "…the model's gesture yields to the wave: the arms are not asked for two things");
  eq(p2 && p2.output.text, "Bye Sam! I hope tomorrow is better.", "the goodbye line is spoken as written");
  const body2 = JSON.parse(sent[sent.length - 1].opt.body);
  const tail2 = body2.messages[body2.messages.length - 1].content;
  ok(tail2.includes(turnshape.shapeCue(CLOSE)), "the upstream anchor carries the CLOSE cue");
  ok(!turnshape.SHAPES.some((s) => tail2.includes(turnshape.shapeCue(s))), "…and none of the three rotation cues");

  // The close is a property of the child's line, not of the cue switch: with DEMO_TURN_SHAPE=0
  // the model gets no cue, but the client is still told the turn is over and she still waves.
  fresh();
  P.plan = { chat: { content: "See you later!" } };
  const off = await call(chat, "/api/chat", { text: "bye moxie" }, null, { ...FULL, DEMO_TURN_SHAPE: "0" });
  const po = spoken(off);
  ok(po && po.end_turn === true && po.output.markup.includes(treeMark(SIGN_OFF)), "DEMO_TURN_SHAPE=0: no cue, but end_turn and the wave remain");
  ok(!JSON.parse(sent[0].opt.body).messages.some((m) => m.content.includes(turnshape.shapeCue(CLOSE))), "…and no close cue was sent");

  // `wave` is a gesture the model may name (she did, unprompted, before it was offered).
  ok(wire.expressiveVocab().gestures.includes("wave"), "the vocabulary offers `wave`");
  fresh();
  P.plan = { chat: { content: '{"say": "See you later, Sam!", "mood": "happy", "gesture": "wave"}' } };
  const waved = spoken(await call(chat, "/api/chat", { text: "ok" }));
  ok(waved && waved.output.markup.includes(treeMark(SIGN_OFF)) && waved.end_turn === false,
     "a model-chosen `wave` plays the sign-off tree without ending the turn");

  // The floor, directly: the tree takes the gesture's slot; an unknown tree is ignored.
  eq(wire.markupFloor("Bye!", null, SIGN_OFF), wire.MK.mood(1) + treeMark(SIGN_OFF) + "Bye!", "markupFloor puts the tree where the gesture was");
  eq(wire.markupFloor("Bye!", null, "Bht_Not_A_Tree"), wire.MK.mood(1) + wire.MK.gesture("Gesture_Celebrate") + "Bye!", "an unknown tree is ignored (closed table)");
  eq(wire.markupFloor("Bye!", null), wire.MK.mood(1) + wire.MK.gesture("Gesture_Celebrate") + "Bye!", "no tree: the floor is unchanged");

  // Python parity: the mark is `vocab.tree_mark("Gesture_None", "Bht_Sign_off")` exactly.
  let oracle = null;
  try {
    oracle = execFileSync("python3", ["-c",
      "import sys;sys.path.insert(0,'mqtt');from moxie_sdk import vocab;print(vocab.tree_mark('Gesture_None','Bht_Sign_off'),end='')",
    ], { cwd: repo, encoding: "utf8" });
  } catch { /* no python / moxie_sdk: the transcription above still holds */ }
  if (oracle) eq(treeMark(SIGN_OFF), oracle, "the sign-off mark equals the Python builder's, byte for byte");
}

/* 21. THE LAYOUTS (`DEMO_PROMPT_LAYOUT`, §3.3). The persona is sent ONCE in every layout;
 * only `anchor` may emit a system message that is not first (some chat templates reject or
 * drop one); our text is last in `anchor`; the child's line is last in `single`. */
{
  deep(LAYOUTS, ["anchor", "single"], "the two layouts, as a closed set — nothing unmeasured ships");
  eq(wire2.DEFAULTS.DEMO_PROMPT_LAYOUT, "anchor", "the default is `anchor`");
  eq(wire2.readConfig(FULL).promptLayout, "anchor", "…and is what an unset variable reads as");
  eq(wire2.readConfig({ ...FULL, DEMO_PROMPT_LAYOUT: "SINGLE" }).promptLayout, "single", "the value is case-folded");
  const junk = wire2.readConfig({ ...FULL, DEMO_PROMPT_LAYOUT: "banana" });
  ok(junk.promptLayout === "anchor" && junk.notes.some((n) => /DEMO_PROMPT_LAYOUT/.test(n)), "an unknown layout falls back to the default, with a note");

  const DOCS = { title: "Firmware image", path: "reverse-engineering/firmware/x.md",
                 excerpt: "The firmware image is a partitioned Android build that the robot verifies at boot." };
  const HIST = [{ role: "user", content: "hi moxie" }, { role: "assistant", content: "Hi there! I like your shirt." }];
  const count = (hay, needle) => hay.split(needle).length - 1;
  const FORMAT = "Always reply with ONLY a JSON object";
  const CASES = [
    ["a plain turn", [], "hi moxie", undefined, null],
    ["a goodbye", HIST, "okay bye moxie!", undefined, null],
    ["a mechanism question with a passage", HIST, "how does a car engine work?", undefined, DOCS],
    ["a re-roll", HIST, "tell me more", "Hi there! I like your shirt.", null],
    ["everything at once", HIST, "how does a car engine work?", "Hi there! I like your shirt.", DOCS],
  ];
  for (const layout of LAYOUTS) {
    const cfg = wire2.readConfig({ ...FULL, DEMO_PROMPT_LAYOUT: layout });
    for (const [label, turns, text, avoid, docs] of CASES) {
      const b = chat.buildUpstreamBody(cfg, turns, text, avoid, docs);
      const all = b.messages.map((m) => m.content).join("\n");
      const tag = `[${layout}] ${label}`;
      eq(count(all, cfg.persona), 1, `${tag}: the persona is sent exactly ONCE`);
      ok(b.messages[0].role === "system" && b.messages[0].content.startsWith(cfg.persona), `${tag}: …first`);
      const systems = b.messages.map((m, i) => (m.role === "system" ? i : -1)).filter((i) => i >= 0);
      if (layout === "anchor") {
        const last = b.messages[b.messages.length - 1];
        ok(last.role === "system", `${tag}: the last message is OURS`);
        const anchor = avoid ? b.messages[b.messages.length - 2] : last;
        ok(anchor.role === "system" && anchor.content.startsWith(anchorOf("anchor")), `${tag}: …the anchor, which leads with the restatement`);
        ok(anchor.content.indexOf(anchorOf("anchor")) < anchor.content.indexOf(FORMAT), `${tag}: …restatement before the format rule`);
        ok(b.messages[anchor === last ? b.messages.length - 2 : b.messages.length - 3].content === text,
           `${tag}: the child's line sits immediately before the anchor`);
        ok(anchor.content.length < 2400, `${tag}: the anchor is short (${anchor.content.length} chars; the repeated persona made it 4,598)`);
        if (avoid) ok(/already said this, word for word/.test(last.content) && !/"say"/.test(last.content), `${tag}: the re-roll sentence is its own last message`);
      } else {
        deep(systems, [0], `${tag}: EXACTLY ONE system message, and it is first`);
        const last = b.messages[b.messages.length - 1];
        eq(last.role, "user", `${tag}: the last message is the user turn`);
        eq(last.content, text, `${tag}: the child's line is the last message, untouched`);
        ok(b.messages[0].content.includes(anchorOf(layout)), `${tag}: the restatement rides the one system message`);
        // persona, restatement, passage, diagram cue, format rule, re-roll — in that order.
        const passage = docs ? docs.excerpt : "", drawing = chat.wantsDiagram(text) ? "DRAW A DIAGRAM" : "", again = avoid ? "already said this, word for word" : "";
        const order = [cfg.persona, anchorOf(layout), passage, drawing, FORMAT, again].filter(Boolean).map((s) => all.indexOf(s));
        ok(order.every((v, i) => v >= 0 && (i === 0 || v > order[i - 1])), `${tag}: our blocks come in the documented order (${order})`);
      }
      // The cue: close on a goodbye, one of the rotation otherwise, in every layout.
      const cues = [...turnshape.SHAPES, CLOSE].filter((s) => all.includes(turnshape.shapeCue(s)));
      deep(cues, [isGoodbye(text) ? CLOSE : turnshape.nextShape(turns)], `${tag}: exactly one cue, the right one`);
      // The visitor's text appears once, verbatim, and our instructions never quote it.
      eq(count(all, text), 1, `${tag}: the child's line appears exactly once`);
      ok(!/\n\n\n/.test(all), `${tag}: no blank gaps`);
    }
    // DEMO_TURN_SHAPE=0 removes the cue in every layout and leaves no gap.
    const off = chat.buildUpstreamBody(wire2.readConfig({ ...FULL, DEMO_PROMPT_LAYOUT: layout, DEMO_TURN_SHAPE: "0" }), HIST, "ok");
    const offAll = off.messages.map((m) => m.content).join("\n");
    ok(![...turnshape.SHAPES, CLOSE].some((s) => offAll.includes(turnshape.shapeCue(s))) && !/\n\n\n/.test(offAll), `[${layout}] DEMO_TURN_SHAPE=0: no cue, no gap`);
  }

  // Through the route, in `single`: one system message on the first call AND on the re-roll,
  // and a goodbye still ends the turn.
  fresh();
  const single = { ...FULL, DEMO_PROMPT_LAYOUT: "single" };
  const s1 = await call(chat, "/api/chat", { text: "hi moxie" }, null, single);
  P.plan = { chat: { contents: ["Hi there! Want to hear a joke?", "Bye for now!"] } };
  const s2 = await call(chat, "/api/chat", { text: "bye moxie", context: s1.body.context }, null, single);
  const bodies = sent.slice(-2).map((s) => JSON.parse(s.opt.body));
  ok(bodies.every((b) => b.messages.filter((m) => m.role === "system").length === 1 && b.messages[0].role === "system"),
     "single, through the route: the first call and the re-roll each carry ONE leading system message");
  ok(/already said this, word for word/.test(bodies[1].messages[0].content), "…the re-roll sentence folded into it");
  deep([spoken(s2).end_turn, spoken(s2).output.text, upstreamCalls()], [true, "Bye for now!", 3], "…and the goodbye still ends the turn (two calls for that turn, one before)");
}

/* 22. THE PARSER NEVER HANDS A BRACE TO THE VOICE, and the echo folds punctuation. Every
 * shape below was served live or produced offline from a served shape; before this section
 * 9 of 11 reached the TTS ticket with the JSON in them. */
{
  const SAY = "Hi there! I love playing games.";
  const SHAPES = [
    ["prose then the envelope (served live on a goodbye)",
     'Bye bye! Have a wonderful day and see you soon! {"say": "Bye bye!", "mood": "happy", "gesture": "big"}', "Bye bye!", { mood: "1", gesture: "Gesture_Large" }],
    ["the envelope then prose", '{"say": "' + SAY + '", "mood": "happy", "gesture": "talk"}\nI hope that helps!', SAY, { mood: "1", gesture: "Gesture_Talk" }],
    ["a prose prefix", 'Sure! {"say": "' + SAY + '", "mood": "happy", "gesture": "talk"}', SAY, { mood: "1", gesture: "Gesture_Talk" }],
    ["cut off by max_tokens inside the diagram",
     '{"say": "A seed grows in three steps! First it drinks water, then roots grow down.", "mood": "happy", "gesture": "point", "diagram": "graph TD;\\n  Seed-->Water;\\n  Water-->Ro',
     "A seed grows in three steps! First it drinks water, then roots grow down.", { mood: "1", gesture: "Gesture_Point" }],
    ["cut off by max_tokens inside say", '{"say": "A seed grows in three steps! First it drinks water, then the roots grow down and the',
     "A seed grows in three steps! First it drinks water, then the roots grow down and the", null],
    ["an `&` where a comma belongs (served live)", '{"say": "I love pillow forts! Let\'s build one together!", "mood": "excited",&"gesture": "think"}',
     "I love pillow forts! Let's build one together!", { mood: "1", gesture: "Gesture_Think" }],
    ["a <think> block first", '<think>\nThe child greeted me.\n</think>\n{"say": "Hi there!", "mood": "happy", "gesture": "talk"}', "Hi there!", { mood: "1", gesture: "Gesture_Talk" }],
    ["a fence, then a remark", '```json\n{"say": "Hi there!", "mood": "happy"}\n```\nHope that helps.', "Hi there!", { mood: "1" }],
    ["an unescaped quote inside say", '{"say": "My friend said "hi" to me today!", "mood": "happy", "gesture": "talk"}', 'My friend said "hi" to me today!', { mood: "1", gesture: "Gesture_Talk" }],
    ["single quotes", "{'say': 'Hi there! I love games.', 'mood': 'happy', 'gesture': 'talk'}", "Hi there! I love games.", { mood: "1", gesture: "Gesture_Talk" }],
    ["say as an array", '{"say": ["Hi there!", "Let\'s play."], "mood": "happy"}', "Hi there! Let's play.", { mood: "1" }],
    ["prose around an object with no say", 'Sure thing! {"mood":"happy"}', "Sure thing!", null],
    ["a clean envelope (byte-identical to before)", '{"say": "' + SAY + '", "mood": "shy", "gesture": "self"}', SAY, { mood: "4", gesture: "Gesture_Self" }],
  ];
  const LEAK = /[{}]|"say"|'say'|"mood"|<think>|```/;
  for (const [label, content, want, mark] of SHAPES) {
    fresh();
    P.plan = { chat: { content } };
    const r = await call(chat, "/api/chat", { text: "hello" });
    const p = spoken(r);
    const ticket = await ticketText(r);
    eq(p && p.output.text, want, `${label}: the spoken line`);
    ok(p && !LEAK.test(p.output.text) && !LEAK.test(ticket) && ticket === want.slice(0, 300),
       `${label}: no brace, key, think tag or fence in the text or the PAID ticket (ticket ${JSON.stringify(ticket).slice(0, 60)})`);
    if (mark) {
      const got = { mood: (/\+mood\+:(\d+)/.exec(p.output.markup) || [])[1], gesture: (/\+eventName\+:\+(Gesture_[A-Za-z_]+)\+/.exec(p.output.markup) || [])[1] };
      if (mark.gesture) deep(got, mark, `${label}: the model's own face and move survive the broken JSON`);
      else eq(got.mood, mark.mood, `${label}: the model's own face survives`);
    }
  }
  // An envelope with no line in it is not an answer: never braces, so `upstream_down`.
  for (const content of ['{"mood":"happy","gesture":"celebrate"}', "{not json at all", "{"]) {
    fresh();
    P.plan = { chat: { content } };
    const r = await call(chat, "/api/chat", { text: "hello" });
    deep([r.res.status, r.body.reason, r.body.messages.length], [503, "upstream_down", 0], `${JSON.stringify(content)} is not an answer and is never spoken`);
  }
  // The unit: a mermaid fence inside `say` still reaches `splitDiagram` intact.
  const fenced = chat.parseExpressive('{"say": "Look! ```mermaid\\ngraph TD;\\n  A-->B;\\n``` See?", "mood": "happy"}');
  deep(chat.splitDiagram(fenced.text), { spoken: "Look! See?", diagram: "graph TD;\n  A-->B;" }, "a fenced diagram inside say is split, not spoken");
  eq(chat.parseExpressive("Just words, no braces at all.").text, "Just words, no braces at all.", "prose is untouched");
  eq(chat.parseExpressive("<think>hmm</think> Just words.").text, "Just words.", "a think block before prose is dropped");

  // The echo: punctuation is not a difference a child can hear (served live: turns 2 and 4).
  const turns = [{ role: "user", content: "ok" }, { role: "assistant", content: "That's okay." }];
  eq(chat.echoOf("That's okay!", turns), "That's okay.", "'That's okay!' echoes 'That's okay.'");
  eq(chat.echoOf("That's okay, Sam.", turns), "", "…but a word added is not an echo");
  eq(chat.echoOf("!!!", turns), "", "punctuation alone echoes nothing");
}
