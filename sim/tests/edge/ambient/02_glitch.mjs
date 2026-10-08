/* §2 THE GLITCH: rare, bounded, and never over a conversation. The real ambient.js idles on
 * a virtual clock with Math.random pinned to 0, so EVERY eligible quip would glitch: the
 * only thing left to space them out is the rule under test (one per ten minutes, never
 * before her fourth quip, never while she is speaking, listening or answering a turn).
 */
import { BEAT, GLITCH_LED, HOUR, MIN, SEC, ambientPage, eq, notes, ok, seeded } from "./harness.mjs";

const glitchLines = BEAT("glitch");
const glitchSet = new Set(glitchLines);
const always = () => 0;
const tenMinApart = (ts) => ts.every((x, i) => i === 0 || x - ts[i - 1] >= 10 * MIN);
const gaps = (ts) => ts.slice(1).map((x, i) => ((x - ts[i]) / MIN).toFixed(1) + " min").join(", ");
/** Anything she said in [from, to). */
const saidIn = (t, from, to) => t.said.filter((s) => s.at >= from && s.at < to);
/** The glitch lines she said in [from, to). */
const glitchedIn = (t, from, to) => saidIn(t, from, to).filter((s) => glitchSet.has(s.text));

ok(glitchLines.length >= 1, `ambient.json has glitch lines (${glitchLines.length})`);

/* 2a. Bounded at maximum pressure: at most one per ten minutes over an hour, none before her
 *     fourth quip, and each one is the flicker first, then the line. */
{
  const t = await ambientPage({ random: always }, (t) => t.advance(HOUR));
  const g = t.of(glitchLines).map((s) => s.at);
  ok(g.length >= 1, `the glitch really happens (${g.length} in a simulated hour)`);
  ok(g.length <= 6 && tenMinApart(g), `…at most once per ten minutes (${g.length} in the hour; gaps ${gaps(g)})`);
  const first = g.length ? g[0] : Infinity;
  const before = t.said.filter((s) => s.at < first && !glitchSet.has(s.text)).length;
  ok(before >= 3, `…never before her fourth quip (${before} quips came first)`);
  ok(g.length > 0 && g.every((x) => t.hearts.filter((h) => h.on && h.color === GLITCH_LED &&
                                                     h.at >= x - 2 * SEC && h.at < x).length >= 2),
     "…and every glitch line comes after the heart has flickered green");
  ok(g.length > 0 && g.every((x) => new Set(t.faces.filter((f) => f.at >= x - 2 * SEC && f.at < x)
                                                      .map((f) => f.face)).size >= 3),
     "…and after her face has flickered through at least three looks");
}

/* 2b. The real rate, with an ordinary seeded random: twelve idle hours. */
{
  const t = await ambientPage({ random: seeded(7) }, (t) => t.advance(12 * HOUR));
  const g = t.of(glitchLines).map((s) => s.at);
  const quips = t.said.filter((s) => s.group === "ambient").length;
  ok(g.length >= 1 && tenMinApart(g), `seeded visit: rare and spaced (${g.length} in 12 h; gaps ${gaps(g)})`);
  notes.push(`glitch: ${g.length} in 12 idle hours (${(g.length / 12).toFixed(1)}/h of ${(quips / 12).toFixed(0)} ` +
             `quips/h) with an ordinary random; at most 6/h with every chance taken`);
}

/* 2c. Never while she is speaking, the visitor is talking to her, the mic is open or the box
 *     holds words. Each hold opens a minute BEFORE the next glitch falls due (ten minutes
 *     after the last) and lasts five, so the glitch is due inside it; the one that comes
 *     right after it lets go proves the hold was all that kept it back. */
{
  const spans = {};
  const t = await ambientPage({ random: always }, async (t) => {
    const nextDue = async () => {                    // wait for a glitch; a minute before the next is due
      const n = t.of(glitchLines).length;
      for (let i = 0; i < 7200 && t.of(glitchLines).length === n; i++) await t.advance(SEC);
      const g = t.of(glitchLines).slice(-1)[0].at;
      await t.advance(g + 9 * MIN - t.now);
    };
    await nextDue();                                 // her own long answer
    t.voice(t.now, t.now + 5 * MIN);
    spans["her own voice"] = [t.now, t.now + 5 * MIN + 1600];
    await t.advance(5 * MIN + 1600);
    await nextDue();                                 // a conversation: a line from them every 30 s
    const b0 = t.now;
    for (let i = 0; i < 10; i++) { t.visitorLine(); t.reply(); await t.advance(30 * SEC); }
    spans["a conversation"] = [b0, b0 + 270 * SEC + 45 * SEC];   // the last turn, then the hold
    await t.advance(spans["a conversation"][1] - t.now);
    await nextDue();                                 // the microphone open
    const c0 = t.now;
    t.mic(true);
    await t.advance(5 * MIN);
    t.mic(false);
    spans["an open mic"] = [c0, t.now];
    await nextDue();                                 // words in the box, not sent
    const d0 = t.now;
    t.typing("do you like pumpkins");
    await t.advance(5 * MIN);
    t.typing("");
    spans["words in the box"] = [d0, t.now];
    await t.advance(2 * MIN);
  });
  eq(Object.keys(spans).length, 4, "precondition: all four holds were reached");
  for (const [what, [from, to]] of Object.entries(spans)) {
    eq(saidIn(t, from, to).length, 0, `nothing at all is said while ${what} holds her, glitch due or not`);
    ok(!t.hearts.some((h) => h.color === GLITCH_LED && h.at >= from && h.at < to),
       `…and no glitch flicker starts while ${what} holds her`);
    eq(glitchedIn(t, to, to + 40 * SEC).length, 1, `…and the glitch that was due comes once ${what} lets go`);
  }
}

/* 2d. A reply arriving mid-flicker ends the glitch: no line, no more flicker, and the ten
 *     minutes are spent anyway. Words typed mid-flicker end it too, and her face comes back. */
for (const cut of ["a reply", "typing"]) {
  const t = await ambientPage({ random: always }, async (t) => {
    for (let i = 0; i < 4000 && !t.hearts.some((h) => h.color === GLITCH_LED); i++) await t.advance(50);
    t.cutAt = t.now;
    if (cut === "a reply") { t.reply(); t.voice(t.now, t.now + 4 * SEC); } else t.typing("wait");
    await t.advance(2 * SEC);
    if (cut === "typing") t.typing("");
    await t.advance(9 * MIN);
  });
  ok(t.cutAt !== undefined && t.hearts.some((h) => h.color === GLITCH_LED), `[${cut}] precondition: a flicker began`);
  eq(glitchedIn(t, t.cutAt, t.cutAt + 9 * MIN).length, 0,
     `[${cut}] the glitch line is not said, and no other glitch comes within its ten minutes`);
  ok(!t.hearts.some((h) => h.color === GLITCH_LED && h.on && h.at > t.cutAt + 200),
     `[${cut}] …the flicker stops at the next frame`);
  if (cut === "typing")
    ok(t.faces.some((f) => f.face === "neutral" && f.at >= t.cutAt && f.at <= t.cutAt + 200),
       "[typing] …and her face is put back (nobody else has it)");
  else
    ok(!t.faces.some((f) => f.at > t.cutAt && f.at <= t.cutAt + 200),
       "[a reply] …and her face is left alone: the reply owns it");
}

/* 2e. A degraded page never glitches ("rebooting... I am back" would read as the brain
 *     coming back), and still mutters. */
{
  const t = await ambientPage({ random: always, mode: "degraded" }, (t) => t.advance(HOUR));
  eq(t.of(glitchLines).length, 0, "a degraded page never glitches");
  ok(t.said.filter((s) => s.group === "ambient").length > 100, "…and keeps muttering");
}

/* 2f. moxieAmbient.say(), the tests' poke and a person's, never glitches: only the timer's
 *     tick may. Then the timer's tick does (the control). */
{
  const t = await ambientPage({ random: always }, async (t) => {
    for (let i = 0; i < 30; i++) { t.api().say(); await t.advance(9 * SEC); }  // inside the 11 s timer
    t.poked = t.now;
    await t.advance(30 * SEC);
  });
  eq(glitchedIn(t, 0, t.poked).length, 0, "thirty pokes of moxieAmbient.say() make no glitch");
  eq(glitchedIn(t, t.poked, t.poked + 30 * SEC).length, 1, "…and the timer's next tick, with every chance taken, does");
}
