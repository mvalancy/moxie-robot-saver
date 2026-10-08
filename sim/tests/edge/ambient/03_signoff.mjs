/* §3 AFTER A GOODBYE: one aside to herself, only after a sign-off (`moxie-signoff`), once per
 * sign-off, a few seconds after her goodbye has been SAID and never over it. The real
 * ambient.js on a virtual clock; her goodbye's voice is a window on the stub speakers, and
 * the transcript observer is stood in for (`visitorLine`, `reply`).
 */
import { BEAT, HOUR, MIN, SEC, ambientPage, eq, notes, ok } from "./harness.mjs";

const asides = BEAT("signoff");
const asideSet = new Set(asides);
const GAP = 3500, VOICE_WAIT = 16000;          // ambient.js SIGNOFF_GAP_MS, SIGNOFF_VOICE_WAIT_MS

ok(asides.length >= 3, `ambient.json has post-goodbye asides (${asides.length})`);

/**
 * A goodbye after two idle minutes: the visitor's line, then 1.5 s later her reply, which
 * signs off (`signals` times) and is voiced over `voice` ([from, to] ms after the reply), or
 * not at all. Returns the reply's instant `R` and the end of her voice `E` on `t`.
 */
async function goodbye(t, { voice = [0, 3000], signals = 1 } = {}) {
  await t.advance(2 * MIN);
  for (let i = 0; i < 200 && t.busy(GAP); i++) await t.advance(100);   // not over one of her quips
  t.visitorLine();
  await t.advance(1500);
  t.R = t.now;
  t.reply();
  for (let i = 0; i < signals; i++) t.signoff();
  if (voice) t.voice(t.R + voice[0], t.R + voice[1]);
  t.E = voice ? t.R + voice[1] : t.R;
}
const asidesOf = (t, from = 0) => t.of(asides).filter((s) => s.at >= from);

/* 3a. Her goodbye voiced at once (the voice-first turn): one aside, a few seconds after her
 *     last syllable, and nothing else from her in between. */
{
  const t = await ambientPage({}, async (t) => { await goodbye(t); await t.advance(2 * MIN); });
  const a = asidesOf(t);
  eq(a.length, 1, "after a goodbye she says exactly one aside");
  ok(a.length === 1 && a[0].at >= t.E + GAP && a[0].at < t.E + GAP + SEC,
     `…${GAP / 1000} s after her goodbye's last syllable (${a.length ? ((a[0].at - t.E) / 1000).toFixed(1) : "-"} s)`);
  ok(a.length === 1 && a[0].group === "ambient", "…in her own-voice clip group (`ambient`)");
  eq(t.said.filter((s) => s.at >= t.R - 1500 && (!a.length || s.at < a[0].at)).length, 0,
     "…and nothing else of hers in between: no quip, no glitch");
  if (a.length) notes.push(`post-goodbye: aside ${((a[0].at - t.E) / 1000).toFixed(1)} s after her goodbye ended`);
}

/* 3b. Her goodbye's voice arriving late (the words first, the voice 6 s on): the aside
 *     waits for it rather than talking first. */
{
  const t = await ambientPage({}, async (t) => { await goodbye(t, { voice: [6000, 9000] }); await t.advance(2 * MIN); });
  const a = asidesOf(t);
  eq(a.length, 1, "a late voice: still exactly one aside");
  ok(a.length === 1 && a[0].at >= t.E + GAP, "…and only after her late goodbye has been said");
}

/* 3c. No voice at all (muted, or the voice failed silently): it waits out the longest a
 *     goodbye's voice can take to start, then speaks. */
{
  const t = await ambientPage({}, async (t) => { await goodbye(t, { voice: null }); await t.advance(2 * MIN); });
  const a = asidesOf(t);
  eq(a.length, 1, "no voice: still exactly one aside");
  ok(a.length === 1 && a[0].at >= t.R + VOICE_WAIT && a[0].at < t.R + VOICE_WAIT + SEC,
     `…after ${VOICE_WAIT / 1000} s, the longest a goodbye's voice can take to start`);
}

/* 3d. One sign-off sent twice (an exit action and end_turn on one reply) is one goodbye. */
{
  const t = await ambientPage({}, async (t) => { await goodbye(t, { signals: 2 }); await t.advance(2 * MIN); });
  eq(asidesOf(t).length, 1, "two signals for one goodbye make one aside, not two");
}

/* 3e. Two goodbyes, two asides: once per sign-off, never the same one twice running. */
{
  const t = await ambientPage({}, async (t) => {
    await goodbye(t); await t.advance(MIN);
    t.R1 = t.now;
    await goodbye(t); await t.advance(MIN);
  });
  const a = asidesOf(t);
  eq(a.length, 2, "two goodbyes, one aside each");
  ok(a.length === 2 && a[1].at > t.R1 && a[0].text !== a[1].text, "…and not the same aside twice in a row");
}

/* 3f. They did not leave: a line from the visitor after her goodbye drops the aside. */
{
  const t = await ambientPage({}, async (t) => {
    await goodbye(t);
    await t.advance(1000);
    t.visitorLine();                                 // "wait, one more thing!"
    await t.advance(2 * MIN);
  });
  eq(asidesOf(t).length, 0, "a new line from the visitor after the goodbye: no aside");
}

/* 3g. Words in the box hold it; sent words drop it; words cleared unsent let it through. */
{
  const held = await ambientPage({}, async (t) => {
    await goodbye(t);
    t.typing("wait");
    await t.advance(20 * SEC);
    t.cleared = t.now;
    t.typing("");
    await t.advance(MIN);
  });
  const a = asidesOf(held);
  ok(a.length === 1 && a[0].at >= held.cleared, "words in the box hold the aside until they are gone");
  const sent = await ambientPage({}, async (t) => {
    await goodbye(t);
    t.typing("wait");
    await t.advance(10 * SEC);
    t.typing(""); t.visitorLine();                   // …and sent
    await t.advance(2 * MIN);
  });
  eq(asidesOf(sent).length, 0, "…and a line sent from the box drops it");
}

/* 3h. Focus alone does not hold it: after Enter the box keeps focus, and she would never say
 *     it for the conversation hold's 45 s. */
{
  const t = await ambientPage({}, async (t) => {
    globalThis.document.activeElement = t.els["speech-input"];
    await goodbye(t);
    await t.advance(MIN);
  });
  const a = asidesOf(t);
  ok(a.length === 1 && a[0].at < t.E + GAP + SEC, "a focused, empty box does not hold the aside");
}

/* 3i. A hidden tab is not talked at: it waits for the visitor to come back, within a
 *     minute, and is dropped after that. */
{
  const back = await ambientPage({}, async (t) => {
    await goodbye(t);
    t.hide(true);
    await t.advance(30 * SEC);
    t.shown = t.now;
    t.hide(false);
    await t.advance(MIN);
  });
  const a = asidesOf(back);
  ok(a.length === 1 && a[0].at >= back.shown, "a hidden tab: the aside waits until it is visible again");
  const gone = await ambientPage({}, async (t) => {
    await goodbye(t);
    t.hide(true);
    await t.advance(2 * MIN);
    t.hide(false);
    await t.advance(MIN);
  });
  eq(asidesOf(gone).length, 0, "…and is let go after a minute away");
}

/* 3j. Liveness off means quiet: before the goodbye, or while the aside is owed (and turning
 *     it back on does not bring that aside back). */
{
  const before = await ambientPage({}, async (t) => {
    t.liveness(false);
    await goodbye(t);
    await t.advance(MIN);
  });
  eq(asidesOf(before).length, 0, "liveness off: no aside");
  const during = await ambientPage({}, async (t) => {
    await goodbye(t);
    t.liveness(false);
    await t.advance(10 * SEC);
    t.liveness(true);
    await t.advance(MIN);
  });
  eq(asidesOf(during).length, 0, "…and switched off while it was owed: dropped, not saved for later");
}

/* 3k. Without a sign-off there is never an aside: hours of idle, a conversation without a
 *     goodbye. */
{
  const t = await ambientPage({}, async (t) => {
    await t.advance(HOUR);
    for (let i = 0; i < 5; i++) { t.visitorLine(); await t.advance(1500); t.reply(); t.voice(t.now, t.now + 3000); await t.advance(30 * SEC); }
    await t.advance(HOUR);
  });
  eq(t.of(asides).length, 0, "no sign-off, no aside: two idle hours and five ordinary turns");
}
