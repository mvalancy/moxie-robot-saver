/* shared_ceilings F–G: the wide window and the day budget FAIL OPEN, every failure mode by
 * name, each measured against an entry a WORKING store refuses on (the CONTROL first). */
import {
  ok, eq, deep, section, cfgOf, fresh, fakeCache, admitWith,
  st, wide, unitsDay, ORIGIN, T0, T1, DAY0, DK,
} from "./harness.mjs";

const WIDE = { tag: "WIDE WINDOW FAILS OPEN", noun: "wide-window", name: "wide", stat: wide };
const DAY = { tag: "DAY BUDGET FAILS OPEN", noun: "day-budget", name: "unitsDay", stat: unitsDay };

/** One fail-open case: admitted, no refusal recorded, and each expected stat. */
async function failsOpen(t, cfg, c, ip, label, expect) {
  const r = await admitWith(cfg, c, ip, T0);
  eq(r.ok, true, `${t.tag}: ${label} still ADMITS`);
  eq(t.stat().refused, 0, `…and ${label} records no ${t.noun} refusal`);
  for (const [k, v] of Object.entries(expect)) eq(t.stat()[k], v, `…and ${label} is recorded as ${t.name}.${k}`);
  if (r.ok) r.release();
}

/* F. THE WIDE WINDOW FAILS OPEN. */
section("F");
{
  const ON = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1000", DEMO_CACHE_TIMEOUT_MS: "10" });
  const IP = "203.0.113.60";
  const SPENT = { h: 1, hb: 2, d: 1, db: 0 };   // this visitor has used their whole hour

  let realKey = "";
  {
    fresh();
    const probe = fakeCache();
    (await admitWith(ON, probe, IP, T0)).release();
    realKey = probe.log.keys.find((k) => k.indexOf("/w0") >= 0) || "no-key";
    fresh();
    const r = await admitWith(ON, fakeCache().seed(realKey, SPENT), IP, T0);
    eq(r.ok, false, "CONTROL: a WORKING store holding a spent hour refuses — every case below is measured on this");
    eq(r.reason, "rate_limited", "…as rate_limited");
  }

  // `only` aims each switch at the WIDE entry alone: otherwise the MINUTE read breaks
  // first, and a narrower read that fails open skips the wider scales (asserted below).
  const AT_WIDE = "/w" + DAY0;
  for (const [label, opts, expect] of [
    ["a match that HANGS FOR EVER", { matchHangs: true }, { timeouts: 1 }],
    ["a match that REJECTS", { matchRejects: true }, { errors: 1 }],
    ["a match that throws SYNCHRONOUSLY", { matchThrowsSync: true }, { errors: 1 }],
    ["a body that is not JSON at all", { bodyOverride: "<html>a proxy error page</html>" }, { miss: 1 }],
    ["a body that is JSON but not an object", { bodyOverride: "42" }, { miss: 1 }],
  ]) {
    fresh();
    await failsOpen(WIDE, ON, fakeCache({ ...opts, only: AT_WIDE }).seed(realKey, SPENT),
                    IP, label, expect);
  }
  {
    fresh();
    const r = await admitWith(ON, fakeCache().seed(realKey, SPENT, 90000, 86400), IP, T0);
    eq(r.ok, true, "WIDE WINDOW FAILS OPEN: an entry served past its own max-age still ADMITS");
    eq(wide().stale, 1, "…recorded as stale rather than believed");
    if (r.ok) r.release();
  }
  // A narrower read that fails open SKIPS the wider scales on purpose: a store that just
  // timed out will time out again, and skipping can only ADMIT.
  {
    fresh();
    const r = await admitWith(ON, fakeCache({ matchHangs: true }).seed(realKey, SPENT), IP, T0);
    eq(r.ok, true, "a store that hangs on the MINUTE entry admits");
    eq(wide().checked, 0, "…and the wider scales are not consulted at all when the narrower read failed open");
    if (r.ok) r.release();
  }
  // A body stamped with ANOTHER bucket — the value, not the key, carries the bucket here.
  {
    fresh();
    const r = await admitWith(ON, fakeCache().seed(realKey, { h: 99, hb: 1, d: 99, db: 7 }), IP, T0);
    eq(r.ok, true,
       "WIDE WINDOW FAILS OPEN: a count stamped with a DIFFERENT bucket reads as zero, not as this hour's");
    eq(wide().hit, 1, "…the entry was read (so this is the stamp talking, not a miss)");
    eq(wide().refused, 0, "…and refused nobody");
    if (r.ok) r.release();
  }
  // Both scales spent: `scales` is narrowest-first, so the SHORTEST Retry-After wins.
  {
    const BOTH = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1" });
    fresh();
    const c = fakeCache();
    (await admitWith(BOTH, c, IP, T0)).release();   // one turn fills BOTH scales
    fresh();                                        // a second isolate: only the colo refuses
    const r = await admitWith(BOTH, c, IP, T0);
    eq(r.ok, false, "a visitor who has spent BOTH their hour and their day is refused");
    ok(r.retryAfterS <= 3600,
       "…by the HOUR, the narrower of the two: the shortest Retry-After that applies wins, got " + r.retryAfterS);
  }
  // A READ THAT FAILED MUST NOT WRITE (row W6): seeded with bytes a fresh write would not
  // produce, so a reset of the live window is visible.
  {
    fresh();
    const LIVE = { h: 5, hb: 2, d: 5, db: 0 };
    const c = fakeCache({ matchRejects: true, only: AT_WIDE }).seed(realKey, LIVE);
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, "a wide read that REJECTS against a busy window still admits");
    deep(c.body(realKey), LIVE, "…and writes NOTHING: publishing after a failed read would RESET a live hour to one");
    if (r.ok) r.release();
  }
  // A failed write is an undercount, which admits the NEXT request too.
  for (const [label, opts] of [
    ["a put that HANGS", { putHangs: true }],
    ["a put that REJECTS", { putRejects: true }],
    ["a put that throws SYNCHRONOUSLY", { putThrowsSync: true }],
  ]) {
    fresh();
    const c = fakeCache({ ...opts, only: AT_WIDE });
    (await admitWith(ON, c, IP, T0)).release();
    fresh();
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, `WIDE WINDOW FAILS OPEN: after ${label}, the next request is ADMITTED, never refused`);
    if (r.ok) r.release();
  }
}

/* G. THE DAY BUDGET FAILS OPEN. */
section("G");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "1000", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12", DEMO_CACHE_TIMEOUT_MS: "10",
  });
  const IP = "203.0.113.61";
  {
    fresh();
    const r = await admitWith(ON, fakeCache().seed(DK, { n: 12 }), IP, T0);
    eq(r.ok, false, "CONTROL: a WORKING store holding a spent day refuses — every case below is measured on this");
    eq(r.reason, "budget_exhausted", "…as budget_exhausted");
  }
  // `only` aims each switch at the DAY BUDGET's entry alone.
  for (const [label, opts, expect] of [
    ["a match that HANGS FOR EVER", { matchHangs: true }, { timeouts: 1 }],
    ["a match that REJECTS", { matchRejects: true }, { errors: 1 }],
    ["a match that throws SYNCHRONOUSLY", { matchThrowsSync: true }, { errors: 1 }],
  ]) {
    fresh();
    await failsOpen(DAY, ON, fakeCache({ ...opts, only: "/units/d" }).seed(DK, { n: 12 }),
                    IP, label, expect);
  }
  {
    fresh();
    const r = await admitWith(ON, fakeCache().seed(DK, { n: 12 }, 90000, 86400), IP, T0);
    eq(r.ok, true, "DAY BUDGET FAILS OPEN: an entry served past its own max-age still ADMITS");
    eq(unitsDay().stale, 1, "…recorded as stale rather than believed");
    if (r.ok) r.release();
  }
  {
    fresh();
    const c = fakeCache({ bodyOverride: "<html>not json</html>", only: "/units/d" }).seed(DK, { n: 12 });
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, "DAY BUDGET FAILS OPEN: an unparseable body still ADMITS");
    if (r.ok) r.release();
  }
  // A `put` that lands and never answers: retrying would publish TWICE (an overcount).
  // The third turn is what makes "not 6" load-bearing (row D2: deleting
  // `clearDayPending()` only shows on the NEXT publish).
  {
    fresh();
    const c = fakeCache({ putStoresThenHangs: true, only: "/units/d" });
    (await admitWith(ON, c, IP, T0)).release();          // owes nothing yet
    const a2 = await admitWith(ON, c, IP, T0);           // publishes 3, and the put hangs
    deep(st().unitsDay, { pending: 0, bucket: DAY0 },
         "a put that LANDS AND THEN HANGS still clears the ledger — the units are never re-published");
    a2.release();                                        // its OWN 3 units then accrue
    eq(unitsDay().timeouts, 1, "…recorded as a timed-out write");
    eq(unitsDay().wrote, 0, "…which this file may not claim to have confirmed");
    eq(unitsDay().published, 3, "…while 3 units really did leave this isolate");
    eq(c.count(DK), 3, "…and the colo holds 3 units, not 6");
    const a3 = await admitWith(ON, c, IP, T0);
    eq(c.count(DK), 6, "…and a third turn publishes its OWN 3, not the retained 3 again: the colo holds 6, never 9");
    eq(unitsDay().published, 6, "…recorded as 6 units having left this isolate in total");
    a3.release();
  }
  // A DAY boundary DROPS the ledger rather than moving it.
  {
    fresh();
    const c = fakeCache();
    (await admitWith(ON, c, IP, T0)).release();
    (await admitWith(ON, c, IP, T0)).release();
    deep(st().unitsDay, { pending: 3, bucket: DAY0 }, "3 units owed for day 0");
    (await admitWith(ON, c, IP, T1)).release();          // the next day
    eq(c.count(ORIGIN + "/__moxie/rl/units/d1"), null, "day 0's unpublished units are never carried into day 1's entry");
    eq(unitsDay().dropped, 3, "…and the drop is recorded rather than left silent");
  }
}
