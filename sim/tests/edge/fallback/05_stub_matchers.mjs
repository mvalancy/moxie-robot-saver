/* §10–12: when the brain is busy or down, `stub.js` answers what a visitor says first (the
 * three openers on the page, a goodbye, her name) with a MATCHED line in her own voice, says
 * goodbye only where the hosted brain would, and falls back to lines that fit any turn.
 *
 * Written against the shipped stub (measured 2026-10-08): "What makes you happy?" got "Tell
 * me more about that!", "Surprise me!" got "That's really interesting. What else?" and "Okay
 * bye Moxie!" got "I like hearing about this.": no matcher knew two of the openers, a goodbye
 * or her name, so they fell through to lines written for a conversation in full swing.
 */
import { api } from "../common.mjs";
import { eq, join, notes, ok, readFileSync, stubSrc, web } from "./harness.mjs";

const turnshape = await api("_lib", "turnshape.js");
const wire = await api("_lib", "wire.js");
/** The hosted brain's own rule: it closes the turn exactly when this says so. */
const isGoodbye = typeof turnshape.isGoodbye === "function" ? turnshape.isGoodbye : () => false;
/** The sign-off wave a hosted goodbye carries, byte for byte. */
const WAVE = typeof wire.MK?.tree === "function" ? wire.MK.tree(wire.SIGN_OFF || "Bht_Sign_off") : "\u0000";

/** A fresh REAL stub.js: its fallback rotation is per load. */
function loadStub() {
  const saved = globalThis.window;
  globalThis.window = {};
  try {
    new Function(stubSrc)();
    return globalThis.window.moxieStub;
  } finally {
    globalThis.window = saved;
  }
}
const stub = loadStub();
const say = (s) => stub.reply(s).text;
const waves = (s) => stub.reply(s).markup.includes(WAVE);

/** What the stub says to a line NO matcher knows, observed rather than parsed: a rotation
 *  of N lines repeats inside 12 replies for any N up to 6. */
const FALLBACKS = new Set(Array.from({ length: 12 }, (_, i) => say(`zqx ${i} vbn`)));
ok(FALLBACKS.size >= 2 && FALLBACKS.size <= 6,
   `the fallback rotation was observed (${FALLBACKS.size} line(s)): ${JSON.stringify([...FALLBACKS])}`);
const matched = (s) => !FALLBACKS.has(say(s));

/* 10. THE THREE OPENERS, read from sim.html's #chat-openers: the buttons a visitor taps first,
 * and the turn most likely to land while the brain is busy. */
const html = readFileSync(join(web, "sim.html"), "utf8");
const box = html.slice(html.indexOf('id="chat-openers"'));
const OPENERS = [...box.slice(0, box.indexOf("</div>")).matchAll(/<button class="opener"[^>]*>([^<]+)<\/button>/g)]
  .map((m) => m[1].trim());
eq(OPENERS.length, 3, "sim.html's #chat-openers carries the three openers");
for (const o of OPENERS) {
  ok(matched(o), `the opener ${JSON.stringify(o)} gets a MATCHED line, not a fallback — got ${JSON.stringify(say(o))}`);
}
const jokeOpener = OPENERS.find((o) => /joke/i.test(o)) || "Tell me a silly joke";
ok(/\?\s+\S[^?]*[.!]$/.test(say(jokeOpener)),
   `the joke opener gets a WHOLE joke, its question and then its punchline — got ${JSON.stringify(say(jokeOpener))}`);
eq(new Set(OPENERS.map(say)).size, OPENERS.length, "each opener gets its own line");

/* 11. A GOODBYE is answered as one: a goodbye word first, no question, and her wave (the
 * hosted close: functions/api/_lib/turnshape.js::shapeCue(CLOSE) and the sign-off tree). */
const GOODBYES = ["Okay bye Moxie!", "bye", "See you later!", "I have to go now", "Good night Moxie",
                  "goodbye moxie, i love you", "it’s bedtime 🌙", "bye bye moxie, see you tomorrow!"];
for (const g of GOODBYES) {
  ok(isGoodbye(g), `(control) the hosted brain closes the turn on ${JSON.stringify(g)}`);
  const r = stub.reply(g);
  ok(/^(?:bye|see you|good ?night)\b/i.test(r.text) && !r.text.includes("?"),
     `${JSON.stringify(g)} gets a goodbye that starts with a goodbye word and asks nothing — got ${JSON.stringify(r.text)}`);
  ok(r.markup.includes(WAVE), `…and she waves: the hosted goodbye's sign-off tree, byte for byte (${JSON.stringify(g)})`);
}
ok(/^good ?night/i.test(say("Good night Moxie")) && /^bye/i.test(say("Okay bye Moxie!")),
   "a bedtime goodbye gets the good-night line, any other the bye line");

/* 11b. …and ONLY where the hosted brain would close: a goodbye WORD inside a sentence is not a
 * leave-taking, and a false hit waves a child off mid-talk. The negatives are the hosted
 * rule's own table (sim/tests/edge/demo_proxy/10_goodbye_close.mjs). */
const NOT_GOODBYES = [
  "My dog died and I had to say goodbye", "I don't want to say bye", "Good night story please!",
  "Goodnight Moon is my favorite book", "Bye-bye is what my baby brother says",
  "Goodbye in Spanish is adios, right?", "I have to go to school tomorrow", "I can see you",
  "later moxie", "goodbye forever", "i'm leaving forever", "I don't want to say bye 😢",
  "Thank you Moxie!", "hi moxie", "",
];
for (const s of NOT_GOODBYES) {
  eq(isGoodbye(s), false, `(control) the hosted brain does not close on ${JSON.stringify(s)}`);
  ok(!waves(s), `the stub does not wave goodbye at ${JSON.stringify(s)} either — got ${JSON.stringify(say(s))}`);
}
// One direction holds over every line above: the stub never says goodbye where the hosted
// brain would carry on (it may miss one the hosted rule catches: that errs the safe way).
for (const s of [...GOODBYES, ...NOT_GOODBYES, ...OPENERS])
  if (waves(s)) ok(isGoodbye(s), `the stub waved goodbye at ${JSON.stringify(s)}, which the hosted brain would not`);
// Anchored and linear, like the hosted rule: a long run of goodbye words answers at once.
let long = 0;
for (let i = 0; i < 500; i++) if (!waves("bye ".repeat(120) + "x")) long++;
eq(long, 500, "500 long runs of goodbye words that are not a goodbye all return, and none waves");

/* 12. HER NAME, and what she is up to. */
for (const q of ["What's your name?", "what is your name", "Who are you?", "What are you?", "what’s your name moxie"]) {
  ok(matched(q) && /\bMoxie\b/.test(say(q)), `${JSON.stringify(q)} gets her name — got ${JSON.stringify(say(q))}`);
}
const doing = say("What are you doing?");
ok(matched("What are you doing?") && !/\bI am Moxie\b/.test(doing),
   `"What are you doing?" gets its own line, not her name — got ${JSON.stringify(doing)}`);
ok(/^Hi there/.test(say("hi moxie")), "a plain hello still gets the greeting");

/* 12b. THE FALLBACK FITS ANY TURN: it answers a goodbye the rule misses on purpose ("later
 * moxie"), a sad line no keyword caught, a question she cannot answer. So it asks nothing. */
for (const f of FALLBACKS) {
  ok(!f.includes("?"), `a fallback line asks no question (it may be answering a goodbye): ${JSON.stringify(f)}`);
}
ok(FALLBACKS.has(say("later moxie")), "a bare \"later\" stays a miss, answered by a fallback");

/* 12c. HER REGISTER, every line the stub can say: spoken words only (no emoji), a finished
 * sentence, short enough to be one breath of hers. */
const lines = [...stubSrc.matchAll(/say:\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]);
ok(lines.length >= 17, `stub.js says at least 17 lines (found ${lines.length})`);
for (const t of lines) {
  ok(!/\p{Extended_Pictographic}/u.test(t), `no emoji in a spoken line: ${JSON.stringify(t)}`);
  ok(/[.!?]$/.test(t) && t.length <= 120, `a finished sentence of at most 120 characters: ${JSON.stringify(t)}`);
}

notes.push(`stub: ${OPENERS.length}/3 openers, ${GOODBYES.length} goodbyes (with her wave), 5 name questions ` +
           `matched; ${NOT_GOODBYES.length} non-goodbyes never waved off; ${FALLBACKS.size} fallback lines ask nothing`);
