/* §1 THE OCTOBER SET: lines with "months": [10] come up only in October by the VISITOR'S
 * local calendar, on the real ambient.js left idle for hours on a virtual clock, on both
 * sides of both month boundaries. The zone is pinned west of UTC (America/Los_Angeles):
 * 22:00 on 31 October there is already November in UTC, so a page that read the UTC month
 * fails here on any machine. The beats (glitch, post-goodbye) never come up as quips.
 */
import { BEAT, HOUR, LINES, OCTOBER, at, ambientPage, eq, notes, ok, seeded, withZone } from "./harness.mjs";

const october = new Set(OCTOBER);
const beats = new Set([...BEAT("glitch"), ...BEAT("signoff")]);
const yearRound = LINES.filter((l) => !l.beat && !Array.isArray(l.months)).map((l) => l.text);
const quipsOf = (t) => t.said.filter((s) => s.group === "ambient" && !beats.has(s.text));

ok(OCTOBER.length >= 8, `the October set has at least eight lines (${OCTOBER.length})`);

await withZone("America/Los_Angeles", async () => {
  /* 1a. Mid-October: every October line comes up, among the year-round ones. */
  {
    const t = await ambientPage({ start: at(2026, 10, 15, 12), random: seeded(1) },
                                (t) => t.advance(3 * HOUR));
    const q = quipsOf(t), oct = q.filter((s) => october.has(s.text));
    ok(q.length > 300, `precondition: three idle hours make hundreds of quips (${q.length})`);
    eq(new Set(oct.map((s) => s.text)).size, OCTOBER.length,
       "in October every October line comes up within three idle hours");
    ok(q.some((s) => !october.has(s.text)), "…alongside the year-round lines");
    eq(t.of(BEAT("signoff")).length, 0, "no post-goodbye aside without a goodbye");
    notes.push(`October: ${oct.length} of ${q.length} quips in three idle hours were October lines ` +
               `(${OCTOBER.length} of ${OCTOBER.length + yearRound.length} bag lines)`);
  }

  /* 1b. September and November: none, and the year-round lines all still come up. */
  for (const [label, start] of [["15 September", at(2026, 9, 15, 12)], ["15 November", at(2026, 11, 15, 12)]]) {
    const t = await ambientPage({ start, random: seeded(2) }, (t) => t.advance(3 * HOUR));
    const q = quipsOf(t);
    eq(q.filter((s) => october.has(s.text)).length, 0, `${label}: no October line comes up`);
    eq(new Set(q.map((s) => s.text)).size, yearRound.length,
       `${label}: every year-round line still comes up (the gate takes nothing else out)`);
  }

  /* 1c. The last evening of October: until local midnight and not a quip after it. */
  {
    const midnight = at(2026, 11, 1, 0);
    const t = await ambientPage({ start: at(2026, 10, 31, 21), random: seeded(3) },
                                (t) => t.advance(5 * HOUR));
    const oct = quipsOf(t).filter((s) => october.has(s.text));
    ok(oct.some((s) => s.at < midnight), "31 October, evening: October lines come up");
    eq(oct.filter((s) => s.at >= midnight).length, 0, "…and none after local midnight");
  }
  /* …even from a bag filled a minute before midnight, most of it still unsaid (at most three
   * of its 78 lines are said by then, so most of its October lines are still in it). */
  {
    const midnight = at(2026, 11, 1, 0);
    const t = await ambientPage({ start: at(2026, 10, 31, 23, 59), random: seeded(5) },
                                (t) => t.advance(2 * HOUR));
    eq(quipsOf(t).filter((s) => october.has(s.text) && s.at >= midnight).length, 0,
       "a bag filled at 23:59 on 31 October says no October line after midnight (a new month refills it)");
  }

  /* 1d. The last minute of September: none before local midnight, then they start at once
   *     rather than when September's bag runs out (about 23 minutes of quips). */
  {
    const midnight = at(2026, 10, 1, 0);
    const t = await ambientPage({ start: at(2026, 9, 30, 23, 59), random: seeded(4) },
                                (t) => t.advance(HOUR));
    const oct = quipsOf(t).filter((s) => october.has(s.text));
    eq(oct.filter((s) => s.at < midnight).length, 0, "30 September, 23:59: no October line");
    ok(oct.some((s) => s.at >= midnight && s.at < midnight + 10 * 60 * 1000),
       "…and they start within ten minutes of 1 October (a new month refills the bag)");
  }
});
