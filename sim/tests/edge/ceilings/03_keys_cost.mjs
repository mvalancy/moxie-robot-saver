/* shared_ceilings H–K: the keys, the cost in round trips, the uncapped seam, and which
 * direction each refusal errs in. */
import {
  ok, eq, deep, section, cfgOf, fresh, fakeCache, admitWith, limits,
  st, wide, unitsDay, ORIGIN, T0, T1, DAY0, DK,
} from "./harness.mjs";

const PREFIX = ORIGIN + "/__moxie/rl/";
const wideKeyOf = (c) => c.log.keys.find((k) => k.indexOf("/w") >= 0) || "";

/* H. THE KEYS: no address or route in the budget key; a wide entry's mark is a LETTER,
 * which no decimal integer can begin with. */
section("H");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000", DEMO_UNIT_BUDGET_DAY: "100000",
  });
  const shapes = limits.__keyShapes();
  eq(shapes.prefix, "/__moxie/rl/", "every sub-tier lives under one prefix a reader can grep for");
  eq(shapes.windowArity, 3, "the window family's arity is unchanged by this slice");
  eq(shapes.unitsArity, 2, "…and so is the budget family's, so `unitsKeyUrl`'s collision argument still stands");
  ok(/^[a-z]$/.test(shapes.wideMark) && /^[a-z]$/.test(shapes.dayMark), "the two wide shapes are marked by a LETTER");
  ok(!/^[0-9]/.test(shapes.wideMark) && !/^[0-9]/.test(shapes.dayMark),
     "…and a decimal integer cannot begin with a letter, which is the whole separation " +
     "between a narrow key and a wide one INSIDE a family");

  fresh();
  const c = fakeCache();
  (await admitWith(ON, c, "203.0.113.99", T0)).release();
  const wideKey = c.log.keys.find((k) => k.indexOf("/" + shapes.wideMark + DAY0) >= 0) || "";
  const dayKey = c.log.keys.find((k) => k.indexOf("/units/" + shapes.dayMark) >= 0) || "";

  ok(wideKey.startsWith(PREFIX + "chat/"), `the wide entry lives on our OWN origin, under a non-route prefix — got ${wideKey}`);
  ok(!wideKey.includes("203.0.113.99"), "the visitor's ADDRESS is never in the wide key");
  const tag = wideKey.slice((PREFIX + "chat/").length).split("/")[0];
  ok(new RegExp("^[0-9a-f]{" + shapes.tagHex + "}$").test(tag), `…only a keyed tag of it, got ${JSON.stringify(tag)}`);
  eq(wideKey.slice(PREFIX.length).split("/").length, shapes.windowArity,
     "…and it has the window family's arity, so nothing about the budget family changed");

  eq(dayKey, DK, "the day budget entry is origin + prefix + 'units' + d + the DAY bucket");
  ok(!dayKey.includes("203.0.113.99"), "…with no visitor's address in it");
  ok(!/(chat|speech|transcribe)/.test(dayKey.slice(PREFIX.length)),
     "…and no route either: the 3-vs-2 unit difference rides in the increment, not the key");

  /** The wide key one admission asks for, from a fresh isolate and store. */
  const keyFor = async (ip, nowS) => {
    fresh();
    const cx = fakeCache();
    (await admitWith(ON, cx, ip, nowS)).release();
    return wideKeyOf(cx);
  };
  eq(await keyFor("203.0.113.99", T0 + 3600), wideKey,
     "an hour later is the SAME wide entry — one entry per day is the point of it");
  ok((await keyFor("203.0.113.99", T1)) !== wideKey,
     "…and the next DAY keys a different entry, so the widest bucket still rotates the key");
  ok((await keyFor("203.0.113.98", T0)) !== wideKey, "a DIFFERENT visitor keys a different wide entry");
  ok(!(await keyFor("2001:db8:1:2:3:4:5:6", T0)).includes("2001"), "an IPv6 /64 is not in the wide key either");

  fresh();
  const c6 = fakeCache();
  (await admitWith(ON, c6, "203.0.113.97", T0)).release();
  const wideK = wideKeyOf(c6);
  deep(c6.body(wideK), { h: 1, hb: 2, d: 1, db: 0 },
       "the wide entry is two counts and the bucket each belongs to: no address, no route history, nothing");
  // Its lifetime: the key rotates daily, so an entry that outlived its day could be read
  // as today's; a shorter one throws the hour count away (the ceiling silently not binding).
  eq((c6.store.get(wideK) || {}).maxAge, 86400,
     "…and it lives exactly ONE DAY — the widest scale it holds, which is the shortest life it can have");
}

/* I. WHAT IT COSTS, AS A COUNT OF ROUND TRIPS. */
section("I");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "100000",
  });
  fresh();
  const c = fakeCache();
  const r = await admitWith(ON, c, "198.51.100.30", T0);
  eq(r.ok, true, "an admitted request");
  eq(c.log.match, 4,
     "…reads FOUR shared entries: the per-IP minute, the per-IP hour+day, the budget's hour, the budget's day");
  eq(c.log.put, 2, "…and writes TWO of them — both windows. Neither budget publishes on a first admission");
  r.release();

  const before = c.log.match + c.log.put;
  (await admitWith(ON, c, "198.51.100.31", T0)).release();
  eq(c.log.match + c.log.put - before, 8,
     "a turn whose isolate owes units costs EIGHT ops: four reads, two window writes, two budget publishes");

  // A refusal spends no writes, at whichever scale refuses…
  fresh();
  const HOUR1 = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1000" });
  const c2 = fakeCache();
  (await admitWith(HOUR1, c2, "198.51.100.32", T0)).release();
  fresh();
  const puts = c2.log.put;
  const refused = await admitWith(HOUR1, c2, "198.51.100.32", T0);
  eq(refused.ok, false, "a request the WIDE window refuses");
  eq(c2.log.put - puts, 0, "…writes NOTHING: a refusal spends nothing, so it counts nothing");
  eq(wide().ops, 1, "…and costs the wide sub-tier one op, the read");

  // …and a refusal by a NARROWER scale never pays for the wider one's round trip.
  fresh();
  const MIN1 = cfgOf({ DEMO_CHAT_PER_MIN: "1", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000" });
  const c3 = fakeCache();
  (await admitWith(MIN1, c3, "198.51.100.33", T0)).release();
  fresh();
  const m = c3.log.match;
  const r2 = await admitWith(MIN1, c3, "198.51.100.33", T0);
  eq(r2.ok, false, "a request the MINUTE window refuses");
  eq(c3.log.match - m, 1, "…reads once and stops: the wider scales are never consulted for it");
  eq(wide().checked, 0, "…recorded as the wide sub-tier never having been checked");
}

/* J. THE UNCAPPED DEPLOYMENT — a ceiling of 0 costs nothing at all. */
section("J");
{
  fresh();
  const c = fakeCache();
  const NO_DAY = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000", DEMO_UNIT_BUDGET_DAY: "0",
  });
  (await admitWith(NO_DAY, c, "198.51.100.40", T0)).release();
  eq(unitsDay().checked, 0, "with no DAY ceiling there is nothing to mirror, so that sub-tier never runs");
  ok(!c.log.keys.some((k) => k.indexOf("/units/d") >= 0), "…and its entry is never asked for");
  // `release()` accrues without consulting any sub-tier (row D11): units must not pile up
  // for a ceiling that does not exist and be published the day somebody sets one.
  deep(st().unitsDay, { pending: 0, bucket: -1 },
       "…and the DAY ledger never accrued a unit either: an uncapped scale banks nothing to publish");

  // `speech` has an hour cap and NO day cap, so its wide entry carries one scale.
  fresh();
  const c2 = fakeCache();
  (await admitWith(cfgOf({ DEMO_SPEECH_PER_MIN: "60", DEMO_SPEECH_PER_HOUR: "1000" }), c2, "198.51.100.41", T0, "speech")).release();
  const k = c2.log.keys.find((x) => x.indexOf("/speech/") >= 0 && x.indexOf("/w") >= 0);
  deep(c2.body(k), { h: 1, hb: 2 }, "a route with an hour cap and no day cap stores the hour and nothing else");
}

/* K. WHICH DIRECTION EACH REFUSAL ERRS IN. The tier rests on "every error is an
 * undercount": (1) a wide refusal writes nothing anywhere; (2) the inherited residual — a
 * BUDGET refusal after both windows were written — is bounded and named (§4.6.3). */
section("K");
{
  const HOUR1 = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1000" });
  const IP = "203.0.113.70";
  fresh();
  const c = fakeCache();
  (await admitWith(HOUR1, c, IP, T0)).release();
  const minKey = c.log.keys[0];
  const wideKey = c.log.keys.find((k) => k.indexOf("/w" + DAY0) >= 0);
  eq(c.count(minKey), 1, "one admitted turn, and the minute entry counts it");
  deep(c.body(wideKey), { h: 1, hb: 2, d: 1, db: 0 }, "…and so does the wide entry");

  fresh();
  const puts = c.log.put;
  const refused = await admitWith(HOUR1, c, IP, T0);
  eq(refused.ok, false, "a second turn is REFUSED by the shared hour");
  eq(c.log.put - puts, 0, "…and writes NOTHING: not the wide entry it refused on, and not the minute entry either");
  eq(c.count(minKey), 1, "…the minute count is still 1, not 2 — a refusal may not leave a counter ABOVE the truth");
  deep(c.body(wideKey), { h: 1, hb: 2, d: 1, db: 0 }, "…and the wide entry is untouched");

  const SPENT = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
  });
  fresh();
  const c2 = fakeCache().seed(DK, { n: 12 });
  const r2 = await admitWith(SPENT, c2, "203.0.113.71", T0);
  eq(r2.ok, false, "a turn refused because the colo's DAY budget is spent");
  eq(r2.reason, "budget_exhausted", "…as budget_exhausted");
  eq(c2.count(c2.log.keys[0]), 1,
     "RESIDUAL, INHERITED: its per-IP minute entry was already written, so it counts a turn that never happened");
  deep(c2.body(c2.log.keys.find((k) => k.indexOf("/w" + DAY0) >= 0)), { h: 1, hb: 2, d: 1, db: 0 },
       "…and so was its wide entry. Bounded at ONE increment per refused request, and only while the " +
       "deployment is out of budget — the fix is a read-phase/write-phase split (§4.6.3)");
}
