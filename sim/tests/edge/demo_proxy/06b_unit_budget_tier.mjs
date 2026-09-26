/* test_demo_proxy §15i: the unit budget's shared HOUR (live-sim-demo.md §4.1, §4.6.1, §4.5).
 * Run via the entry file, never alone.
 *
 * A lost `prev - cost` refund write would leave the colo's counter TOO HIGH and refuse a
 * visitor, so there is no refund write at all: units reach the colo only after a request
 * is RELEASED WITHOUT A REFUND, held until then in the isolate's ledger (`__state().units`).
 */
import {
  FULL, ORIGIN, P, call, chat, deep, eq, fresh, limits, ok, upstreamCalls, wire2,
  fakeCache, cacheStats, admitWith,
} from "./harness.mjs";

{
  /** The clamp floor, so a hang costs milliseconds per assertion. */
  const FAST = wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "10" });
  const UNITS_HOUR = 12;                       // 4 chat turns, so the arithmetic is readable
  const TWELVE = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR) });
  const FAST12 = wire2.readConfig({
    ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR), DEMO_CACHE_TIMEOUT_MS: "10",
  });
  /* `nowS` 7200 is hour bucket 2 and day bucket 0. Keys are spelled out, not derived, so a
   * wrong key is a wrong STRING; the day's `d` mark is a letter no hour bucket can spell. */
  const HOUR2 = 7200;
  const UK = ORIGIN + "/__moxie/rl/units/2";
  const DK = ORIGIN + "/__moxie/rl/units/d0";

  // ---- 15i-a. THE KEY: one entry per DEPLOYMENT per hour, and no visitor in it -- //
  {
    const shapes = limits.__keyShapes();
    eq(shapes.prefix, "/__moxie/rl/", "both sub-tiers live under one prefix a reader can grep for");
    ok(!shapes.routes.includes(shapes.units),
       `'${shapes.units}' is not a route name, so a window key can never spell a budget key by route`);
    ok(shapes.windowArity !== shapes.unitsArity,
       "…and the two shapes have different ARITY, which is the byte-level half of the same argument");

    fresh();
    const c = fakeCache();
    (await admitWith(TWELVE, c, "203.0.113.50", "chat", HOUR2)).release();
    // THE READ ORDER, pinned as a whole rather than by index: every per-IP window scale,
    // narrowest first, then every budget scale. Reordering any of it would answer a
    // per-visitor condition with a deployment-wide 503 (15i-h, mutation row U10).
    eq(c.log.keys.length, 4,
       "an admitted turn reads four entries: both window scales, then both budget scales");
    const wk = String(c.log.keys[0] || "");
    const tag = wk.slice((ORIGIN + "/__moxie/rl/chat/").length).split("/")[0];
    deep(c.log.keys, [
      ORIGIN + "/__moxie/rl/chat/" + tag + "/120",   // the per-IP MINUTE, bucket 7200/60
      ORIGIN + "/__moxie/rl/chat/" + tag + "/w0",    // the per-IP HOUR+DAY, one entry, day 0
      UK,                                            // the budget's HOUR
      DK,                                            // the budget's DAY
    ], "…in that exact order: per-IP window scales first, budget scales second");
    eq(c.log.keys[2], UK, "the hour budget entry is origin + prefix + 'units' + the HOUR bucket");
    eq(c.log.keys[3], DK, "…and the day's is the same shape with the 'd' mark and the DAY bucket");
    for (const k of [c.log.keys[2], c.log.keys[3]]) {
      ok(!String(k).includes("203.0.113.50"), "…with no visitor's address in either budget key");
      ok(!/(chat|speech|transcribe)/.test(String(k).slice((ORIGIN + "/__moxie/rl/").length)),
         "…and no route either: the 3-vs-2 unit difference rides in the increment, not the key");
    }

    fresh();
    const c2 = fakeCache();
    (await admitWith(TWELVE, c2, "203.0.113.50", "chat", HOUR2 + 3600)).release();
    ok((c2.log.keys[2] || "") !== UK,
       "…and the next HOUR keys a different entry, so nothing stale is believable (index 1 -> 2: the " +
       "wide window's entry now sits between the minute's and the budget's)");
  }

  // ---- 15i-b. IT IS SHARED: isolate B is refused on isolate A's spend --------- //
  // Four addresses, so the per-IP window never gets a word in and only the budget can
  // refuse.
  {
    const shared = fakeCache();
    fresh();
    for (let i = 1; i <= 4; i++) {
      const r = await admitWith(TWELVE, shared, "198.51.100." + i, "chat", HOUR2);
      eq(r.ok, true, `isolate A turn ${i} is admitted — 12 units is exactly four chat turns`);
      r.release();
    }
    eq(shared.count(UK), 9,
       "isolate A published 9 of the 12 units it spent: the ledger publishes on the NEXT admission, " +
       "so the last turn's 3 are still unpublished. THAT LAG IS THE DESIGN, and it undercounts");
    deep(limits.__state().units, { pending: 3, bucket: 2 },
         "…and those 3 sit in this isolate's ledger as a RECORDED fact, not an inference");
    eq(cacheStats().units.published, 9, "…9 units recorded as handed to the colo");
    eq(cacheStats().units.wrote, 3, "…in three writes: turn 1 owed nothing, turns 2-4 owed 3 each");
    const entry = shared.store.get(UK) || {};
    eq(entry.maxAge, 3600,
       "the budget entry's max-age is ONE HOUR — its own window — so an entry that outlives its own " +
       "hour would be read as this hour's, and this is what stops that");
    deep(JSON.parse(entry.body || "null"), { n: 9 },
         "…and the stored body is a bare count: the entry sits under a URL an outsider could ask for");

    fresh(); // ---- a different isolate: fresh Map, fresh ledger, SAME colo cache.
    deep(limits.__state().units, { pending: 0, bucket: -1 }, "the new isolate's ledger starts empty");
    const b1 = await admitWith(TWELVE, shared, "198.51.100.11", "chat", HOUR2);
    eq(b1.ok, true, "isolate B's first turn is admitted — the colo has been told about 9 of 12");
    b1.release();

    const budgetBefore = JSON.stringify(limits.__state().budget);
    const b2 = await admitWith(TWELVE, shared, "198.51.100.12", "chat", HOUR2);
    eq(b2.ok, false,
       "isolate B's SECOND is REFUSED — its own map has seen 3 units and would allow it; the colo has seen 12");
    eq(b2.reason, "budget_exhausted", "…as budget_exhausted, the reason the in-isolate budget gives for the same fact");
    ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 3600, `…with a Retry-After inside the hour, got ${b2.retryAfterS}`);
    eq(upstreamCalls(), 0, "…having called nothing upstream");
    eq(cacheStats().units.refused, 1, "…recorded as one budget-tier refusal");

    // The refusal costs the visitor nothing and costs the colo nothing.
    eq(limits.__state().inflight.chat, 0, "a budget-tier refusal leaves NO concurrency slot held");
    eq(JSON.stringify(limits.__state().budget), budgetBefore,
       "…and refunds the in-isolate units it charged, exactly as the window tier's refusal does");
    eq(shared.count(UK), 9, "…and PUBLISHES NOTHING: a request that was refused spent nothing to report");
    eq(cacheStats().units.published, 0, "…recorded as zero units published by this isolate");

    // The control that makes the assertion above about the TIER rather than the arithmetic.
    const OFF12 = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR), DEMO_CACHE_COUNTER: "0" });
    fresh();
    (await admitWith(OFF12, shared, "198.51.100.11", "chat", HOUR2)).release();
    const off2 = await admitWith(OFF12, shared, "198.51.100.12", "chat", HOUR2);
    eq(off2.ok, true, "CONTROL: with the tier off, that same second turn is admitted — the map alone allows it");
    off2.release();
  }

  // ---- 15i-c. THE FREE DRAIN, CLOSED STRUCTURALLY ---------------------------- //
  // `test_turnstile.mjs` §12's attack on the SHARED counter: 200 requests admission
  // charges and the route then refuses. Charging the colo at admission would read an
  // exhausted hour for an attack that made no gateway call; here the colo is never told.
  {
    fresh();
    const c = fakeCache();
    const PROD = wire2.readConfig(FULL);
    eq(PROD.unitBudgetHour, 600, "the hourly budget is production's 600…");
    let drainRefusals = 0;
    for (let i = 0; i < 200; i++) {
      const s = await admitWith(PROD, c, "198.51.100." + (i % 250), "chat", HOUR2);
      if (!s.ok) drainRefusals += 1;
      s.refundBudget();   // exactly what `chat.js`'s `spentNothing` does
      s.release();
    }
    eq(drainRefusals, 0, "all 200 are ADMITTED first — the drain is what the ROUTE BODY refuses, not admission");
    eq(c.count(UK), null, "200 charged-then-refunded requests wrote NOTHING to the colo's hour");
    eq(c.log.puts.filter((k) => k.indexOf("/units/") >= 0).length, 0,
       "…not one `put` on the budget entry, which is the structural half of the claim");
    eq(cacheStats().units.published, 0, "…zero units published");
    deep(limits.__state().units, { pending: 0, bucket: 2 }, "…and an empty ledger: nothing settled, so nothing is owed");
    deep(limits.__state().budget, {}, "…while the in-isolate budget is also whole, as §12 already required");

    const visitor = await admitWith(PROD, c, "203.0.113.77", "chat", HOUR2);
    eq(visitor.ok, true, "…and the next real visitor is SERVED rather than budget_exhausted");
    visitor.release();
  }

  // ---- 15i-d. FAIL OPEN — every failure mode, asserted in that direction ------ //
  //
  // Each case stands the colo's hour AT its ceiling, so a WORKING cache refuses. The
  // required answer in every one is `ok: true`.
  {
    // The positive control first: with a working cache the seed really does refuse, so
    // every `ok: true` below is a statement about the failure and not about the fixture.
    fresh();
    const working = fakeCache().seed(UK, UNITS_HOUR, undefined, 3600);
    const refused = await admitWith(FAST12, working, "198.51.100.60", "chat", HOUR2);
    eq(refused.ok, false, "CONTROL: a working cache holding a spent hour REFUSES");
    eq(refused.reason, "budget_exhausted", "…as budget_exhausted");

    const modes = [
      ["a match that THROWS SYNCHRONOUSLY", () => fakeCache({ matchThrowsSync: true }).seed(UK, UNITS_HOUR), { errors: 1 }],
      ["a match that REJECTS", () => fakeCache({ matchRejects: true }).seed(UK, UNITS_HOUR), { errors: 1 }],
      ["a match that HANGS FOR EVER", () => fakeCache({ matchHangs: true }).seed(UK, UNITS_HOUR), { timeouts: 1 }],
      ["an entry whose body is NOT JSON", () => fakeCache({ bodyOverride: "<html>nope" }).seed(UK, UNITS_HOUR), { miss: 1 }],
      ["an entry whose count is not a number", () => fakeCache({ bodyOverride: '{"n":"lots"}' }).seed(UK, UNITS_HOUR), { miss: 1 }],
      ["a STALE entry, past its own max-age", () => fakeCache().seed(UK, UNITS_HOUR, 999999, 3600), { stale: 1 }],
      ["a store with NO METHODS AT ALL", () => ({ log: { match: 0, put: 0, keys: [], puts: [] } }), { errors: 1 }],
      ["a cache MISS — nobody has written the hour yet", () => fakeCache(), { miss: 1 }],
    ];
    for (const [label, make, want] of modes) {
      fresh();
      const c = make();
      const r = await admitWith(FAST12, c, "198.51.100.60", "chat", HOUR2);
      eq(r.ok, true, `BUDGET FAILS OPEN: ${label} must still ADMIT a visitor the working cache refused`);
      eq(r.reason, null, `BUDGET FAILS OPEN: ${label} carries no refusal reason`);
      eq(cacheStats().units.refused, 0, `BUDGET FAILS OPEN: ${label} records no budget-tier refusal`);
      for (const [k, v] of Object.entries(want)) {
        eq(cacheStats().units[k], v, `…and ${label} is recorded as units.${k}`);
      }
      if (r.ok) r.release();
      eq(limits.__state().inflight.chat, 0, `…and ${label} leaks no concurrency slot`);
    }

    // A read that failed must NOT then publish over a live count. The ledger is KEPT when
    // no write was attempted, which is safe precisely because nothing can have landed.
    fresh();
    const c = fakeCache({ matchRejects: true });
    const first = await admitWith(FAST12, c, "198.51.100.61", "chat", HOUR2);
    first.release();
    const second = await admitWith(FAST12, c, "198.51.100.62", "chat", HOUR2);
    eq(second.ok, true, "a failed budget READ admits");
    eq(c.log.puts.filter((k) => k.indexOf("/units/") >= 0).length, 0,
       "…and writes nothing to the budget entry, so a live hour is never reset to this isolate's share");
    deep(limits.__state().units, { pending: 3, bucket: 2 },
         "…and the unpublished units are KEPT, because a read that failed attempted no write to lose");
    second.release();
  }

  // ---- 15i-e. NO LOST WRITE CAN REFUSE A VISITOR WHO SHOULD BE SERVED --------- //
  //
  // The property the whole design is for, from four directions.
  {
    // (1) THERE IS NO REFUND WRITE TO LOSE. The strongest form of the claim: not "the
    //     refund rarely fails" but "no cache operation happens on a refund at all".
    fresh();
    const c = fakeCache();
    const s = await admitWith(TWELVE, c, "198.51.100.70", "chat", HOUR2);
    const at = { match: c.log.match, put: c.log.put };
    s.refundBudget();
    s.release();
    eq(c.log.match, at.match, "a refund performs no cache READ…");
    eq(c.log.put, at.put, "…and no cache WRITE: there is no shared refund that could be lost");
    deep(limits.__state().units, { pending: 0, bucket: 2 },
         "…and nothing reached the ledger either, so the colo will never hear about it");

    // (2) A LOST PUBLISH LOSES SPEND, NEVER GAINS IT. Eight turns really spent across four
    //     isolates; with every `put` rejecting, the colo learns nothing and the ninth
    //     visitor is SERVED. The control shows the same eight turns DO refuse when the
    //     writes land, which is what makes this a statement about the lost write.
    for (const [label, opts, wantLast] of [
      ["with every publish REJECTED", { putRejects: true }, true],
      ["CONTROL, with the publishes landing", {}, false],
    ]) {
      fresh();
      const cc = fakeCache(opts);
      let admitted = 0;
      let last = null;
      for (let iso = 0; iso < 4; iso++) {
        fresh(); // a new isolate every two turns, so the in-isolate map never decides
        for (let t = 0; t < 2; t++) {
          last = await admitWith(FAST12, cc, "198.51.100.1" + iso + t, "chat", HOUR2);
          if (last.ok) {
            admitted += 1;
            last.release();
          }
        }
      }
      if (wantLast) {
        eq(admitted, 8, `${label}: all eight turns are admitted — a lost write can only UNDERCOUNT`);
        eq(cc.count(UK), null, `${label}: and the colo's hour was never written at all`);
      } else {
        eq(admitted, 7, `${label}: the eighth turn is REFUSED, which is what the lost writes above prevented`);
        eq(last.reason, "budget_exhausted", `${label}: as budget_exhausted`);
      }
    }

    // (3) A PUBLISH THAT LANDS AND THEN TIMES OUT IS NOT PUBLISHED TWICE. Retrying the
    //     units would charge the colo twice (an overcount that refuses somebody), so the
    //     ledger is cleared on every ATTEMPT. Run at production's 600-unit ceiling so the
    //     assertion that NAMES the double charge is the one that fails, not an earlier
    //     "turn 4 is admitted" (mutation-table rule).
    fresh();
    const slow = fakeCache({ putStoresThenHangs: true });
    for (let i = 1; i <= 4; i++) {
      (await admitWith(FAST, slow, "198.51.100.7" + i, "chat", HOUR2)).release();
    }
    eq(slow.count(UK), 9,
       "four turns, three publishes that all LANDED and all timed out: the colo holds 9 units, not 18");
    eq(cacheStats().units.timeouts, 3, "…recorded as three timed-out writes");
    eq(cacheStats().units.wrote, 0, "…none of which this file may claim to have confirmed");
    eq(cacheStats().units.published, 9, "…while 9 units really did leave this isolate");
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "…and only the last turn's 3 are still owed");

    // (4) THE HOUR ROLL DROPS RATHER THAN MOVES. Units charged in one hour must never be
    //     published against another: that would be spend recorded against an hour that did
    //     not spend it, which refuses somebody in the hour that inherits it.
    fresh();
    const roll = fakeCache();
    (await admitWith(TWELVE, roll, "198.51.100.40", "chat", HOUR2)).release();
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "an unpublished 3 units, charged in hour 2");
    const next = await admitWith(TWELVE, roll, "198.51.100.41", "chat", HOUR2 + 3600);
    eq(next.ok, true, "…and the first admission of hour 3 is served");
    next.release();
    eq(roll.count(ORIGIN + "/__moxie/rl/units/3"), null,
       "…with hour 2's crumbs DROPPED, never carried into hour 3's entry");
    eq(cacheStats().units.dropped, 3, "…and the drop recorded rather than left silent");
    deep(limits.__state().units, { pending: 3, bucket: 3 }, "…the ledger now belongs to hour 3");

    // (5) `release()` THEN `refundBudget()` — the ordering no route performs today and
    //     nothing structurally prevents. The units come back out of the ledger, which is
    //     safe only because the ledger has not been shown to anybody.
    fresh();
    const late = fakeCache();
    const l = await admitWith(TWELVE, late, "198.51.100.42", "chat", HOUR2);
    l.release();
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "a released turn settles its units into the ledger");
    l.refundBudget();
    deep(limits.__state().units, { pending: 0, bucket: 2 },
         "…and a refund AFTER the release takes them straight back out again");
    eq(late.log.puts.filter((k) => k.indexOf("/units/") >= 0).length, 0, "…having published nothing in between");
  }

  // ---- 15i-h. THE ORDER: the per-IP window first, the deployment's budget second - //
  // Over their own MINUTE in a colo whose HOUR is spent: `rate_limited` (429, the browser
  // paces itself) must win over `budget_exhausted` (503, SCRIPTED for everybody).
  {
    fresh();
    const learn = fakeCache();
    (await admitWith(TWELVE, learn, "198.51.100.90", "chat", HOUR2)).release();
    const WKEY = learn.log.keys[0] || "no-key";

    fresh();
    const both = fakeCache().seed(WKEY, TWELVE.chatPerMin).seed(UK, UNITS_HOUR, undefined, 3600);
    const r = await admitWith(TWELVE, both, "198.51.100.90", "chat", HOUR2);
    eq(r.ok, false, "a request both sub-tiers would refuse is refused…");
    eq(r.reason, "rate_limited",
       "…and a spent colo hour AND a spent minute answers rate_limited: the per-visitor condition wins, " +
       "because a 429 paces one browser where a 503 paints the whole page scripted");
    eq(cacheStats().units.checked, 0,
       "…with the budget entry not even read, the window having already said no");
  }

  // ---- 15i-f. THE SEAMS: off, absent, and uncapped -------------------------- //
  {
    fresh();
    const c = fakeCache();
    const OFF12 = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: "12", DEMO_CACHE_COUNTER: "0" });
    (await admitWith(OFF12, c, "198.51.100.80", "chat", HOUR2)).release();
    eq(c.log.match + c.log.put, 0, "DEMO_CACHE_COUNTER=0 makes ZERO cache calls for the budget half too");
    eq(cacheStats().units.checked, 0, "…recorded as never checked");

    // AN UNCAPPED HOUR MUST NOT SWITCH OFF THE DAY: the hour's early return must not
    // skip the day scale too, so this pin is split per scale.
    fresh();
    const c2 = fakeCache();
    const NOBUDGET = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: "0" });
    (await admitWith(NOBUDGET, c2, "198.51.100.81", "chat", HOUR2)).release();
    eq(cacheStats().units.checked, 0, "with no hourly ceiling there is nothing to mirror, so the sub-tier never runs");
    eq(c2.log.keys.filter((k) => k.indexOf("/units/") >= 0 && k.indexOf("/units/d") < 0).length, 0,
       "…and the HOUR's budget entry is never asked for");
    deep(limits.__state().units, { pending: 0, bucket: -1 },
         "…and an hour-uncapped deployment accrues nothing to the HOUR ledger, so it can never publish it");
    eq(c2.log.keys.filter((k) => k.indexOf("/units/d") >= 0).length, 1,
       "…while the DAY's entry IS still asked for: `DEMO_UNIT_BUDGET_DAY` defaults to 4000 and an " +
       "unrelated variable may not switch a ceiling off (§4.6.3)");
    eq(cacheStats().unitsDay.checked, 1, "…recorded as the day sub-tier having run on its own ceiling");

    // …and the genuinely uncapped deployment, which is the case the line above used to
    // cover on its own: BOTH scales zero, and the budget half of the tier vanishes.
    fresh();
    const c3 = fakeCache();
    const NOBUDGETATALL = wire2.readConfig({
      ...FULL, DEMO_UNIT_BUDGET_HOUR: "0", DEMO_UNIT_BUDGET_DAY: "0",
    });
    (await admitWith(NOBUDGETATALL, c3, "198.51.100.82", "chat", HOUR2)).release();
    eq(c3.log.keys.filter((k) => k.indexOf("/units/") >= 0).length, 0,
       "with NEITHER budget ceiling set, no budget entry of either scale is ever asked for");
    eq(cacheStats().units.checked + cacheStats().unitsDay.checked, 0, "…recorded as neither sub-tier running");
    deep(limits.__state().unitsDay, { pending: 0, bucket: -1 },
         "…and the DAY ledger accrues nothing either, so it can never publish anything");
  }

  // ---- 15i-g. THE WHOLE ROUTE, THROUGH THE REAL `caches.default` ------------- //
  {
    fresh();
    // The colo's hour stands AT its ceiling, as other isolates would have left it —
    // answered for whatever hour the route's own clock names, so this block asserts the
    // same thing with no wall-clock read of its own.
    const c = fakeCache({ unitsCount: UNITS_HOUR });
    globalThis.caches = { default: c };
    try {
      P.plan = { chat: { content: "hi" } };
      const env12 = { ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR) };
      const spent = await call(chat, "/api/chat", { text: "hello" }, {}, env12);
      eq(spent.res.status, 503, "/api/chat answers 503 when the COLO's shared hour is spent");
      eq(spent.body.reason, "budget_exhausted", "…with the §4.5 reason");
      eq(spent.body.mode, "degraded", "…and a degraded page, which is what §7 says a spent budget looks like");
      ok(Number(spent.res.headers.get("Retry-After")) >= 1, "…and a Retry-After the client can obey");
      eq(upstreamCalls(), 0, "…having spent nothing upstream");
      eq(limits.__state().inflight.chat, 0, "…and leaking no concurrency slot");
    } finally {
      delete globalThis.caches;
    }
    eq(typeof caches, "undefined", "the global is put back, so no later block inherits a cache");
  }
}
