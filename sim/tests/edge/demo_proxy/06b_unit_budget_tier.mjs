/* test_demo_proxy §15i: the unit budget's shared HOUR (§4.1, §4.6.1, §4.5). A lost refund
 * write would leave the colo's counter TOO HIGH and refuse a visitor, so there is no refund
 * write at all: units reach the colo only after a request is RELEASED WITHOUT A REFUND, held
 * until then in the isolate's ledger (`__state().units`). Labels are selected by
 * unit_budget_mutation_check.py. Run via the entry file. */
import {
  FULL, ORIGIN, P, call, chat, deep, eq, fresh, limits, ok, upstreamCalls, wire2,
  fakeCache, cacheStats, admitWith,
} from "./harness.mjs";

{
  const FAST = wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "10" });
  const UNITS_HOUR = 12;                       // 4 chat turns, so the arithmetic is readable
  const TWELVE = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR) });
  const FAST12 = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR), DEMO_CACHE_TIMEOUT_MS: "10" });
  // nowS 7200 is hour bucket 2 and day bucket 0; keys are spelled out so a wrong key is a wrong STRING.
  const HOUR2 = 7200;
  const UK = ORIGIN + "/__moxie/rl/units/2";
  const DK = ORIGIN + "/__moxie/rl/units/d0";
  const unitPuts = (c) => c.log.puts.filter((k) => k.indexOf("/units/") >= 0).length;

  // 15i-a. THE KEY: one entry per DEPLOYMENT per hour, no visitor in it, read in a fixed order.
  {
    const shapes = limits.__keyShapes();
    eq(shapes.prefix, "/__moxie/rl/", "both sub-tiers live under one prefix");
    ok(!shapes.routes.includes(shapes.units) && shapes.windowArity !== shapes.unitsArity,
       `'${shapes.units}' is not a route name, so a window key can never spell a budget key by route (and the arities differ)`);

    fresh();
    const c = fakeCache();
    (await admitWith(TWELVE, c, "203.0.113.50", "chat", HOUR2)).release();
    const tag = String(c.log.keys[0] || "").slice((ORIGIN + "/__moxie/rl/chat/").length).split("/")[0];
    // Reordering would answer a per-visitor condition with a deployment-wide 503 (15i-h, U10).
    deep(c.log.keys, [
      ORIGIN + "/__moxie/rl/chat/" + tag + "/120",   // the per-IP MINUTE, bucket 7200/60
      ORIGIN + "/__moxie/rl/chat/" + tag + "/w0",    // the per-IP HOUR+DAY, one entry, day 0
      UK,                                            // the budget's HOUR
      DK,                                            // the budget's DAY ('d' is a letter no hour can spell)
    ], "four entries in that exact order: per-IP window scales first, budget scales second");
    ok([UK, DK].every((k) => !k.includes("203.0.113.50") && !/(chat|speech|transcribe)/.test(k.slice((ORIGIN + "/__moxie/rl/").length))),
       "budget keys carry no visitor address and no route: the 3-vs-2 unit difference rides in the increment");
    fresh();
    const c2 = fakeCache();
    (await admitWith(TWELVE, c2, "203.0.113.50", "chat", HOUR2 + 3600)).release();
    ok(c2.log.keys[2] !== UK, "…and the next HOUR keys a different entry");
  }

  // 15i-b. IT IS SHARED: isolate B is refused on isolate A's spend (four addresses, so only
  // the budget can refuse).
  {
    const shared = fakeCache();
    fresh();
    for (let i = 1; i <= 4; i++) {
      const r = await admitWith(TWELVE, shared, "198.51.100." + i, "chat", HOUR2);
      eq(r.ok, true, `isolate A turn ${i} is admitted — 12 units is exactly four chat turns`);
      r.release();
    }
    eq(shared.count(UK), 9,
       "isolate A published 9 of the 12 units: the ledger publishes on the NEXT admission — that lag is the design, and it undercounts");
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "…and those 3 sit in this isolate's ledger as a RECORDED fact");
    deep([cacheStats().units.published, cacheStats().units.wrote], [9, 3], "…9 units in three writes (turn 1 owed nothing)");
    const entry = shared.store.get(UK) || {};
    eq(entry.maxAge, 3600, "the budget entry's max-age is ONE HOUR, so an entry that outlives its own hour is never read as this hour's");
    deep(JSON.parse(entry.body || "null"), { n: 9 }, "…and the stored body is a bare count");

    fresh(); // a different isolate: fresh Map, fresh ledger, SAME colo cache
    deep(limits.__state().units, { pending: 0, bucket: -1 }, "the new isolate's ledger starts empty");
    const b1 = await admitWith(TWELVE, shared, "198.51.100.11", "chat", HOUR2);
    eq(b1.ok, true, "isolate B's first turn is admitted — the colo has been told about 9 of 12");
    b1.release();
    const budgetBefore = JSON.stringify(limits.__state().budget);
    const b2 = await admitWith(TWELVE, shared, "198.51.100.12", "chat", HOUR2);
    eq(b2.ok, false, "isolate B's SECOND is REFUSED — its own map would allow it; the colo has seen 12");
    eq(b2.reason, "budget_exhausted", "…as budget_exhausted, the reason the in-isolate budget gives for the same fact");
    ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 3600, `…with a Retry-After inside the hour, got ${b2.retryAfterS}`);
    deep([upstreamCalls(), cacheStats().units.refused, limits.__state().inflight.chat], [0, 1, 0],
         "…calling nothing, recorded as one budget-tier refusal, holding no slot");
    eq(JSON.stringify(limits.__state().budget), budgetBefore, "…and refunds the in-isolate units it charged");
    deep([shared.count(UK), cacheStats().units.published], [9, 0], "…and PUBLISHES NOTHING: a refused request spent nothing to report");

    // CONTROL: with the tier off, the same second turn is admitted.
    const OFF12 = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR), DEMO_CACHE_COUNTER: "0" });
    fresh();
    (await admitWith(OFF12, shared, "198.51.100.11", "chat", HOUR2)).release();
    const off2 = await admitWith(OFF12, shared, "198.51.100.12", "chat", HOUR2);
    eq(off2.ok, true, "CONTROL: with the tier off, that same second turn is admitted — the map alone allows it");
    off2.release();
  }

  // 15i-c. THE FREE DRAIN, CLOSED STRUCTURALLY: 200 requests admission charges and the route
  // then refuses (test_turnstile §12's attack). The colo is never told.
  {
    fresh();
    const c = fakeCache();
    const PROD = wire2.readConfig(FULL);
    eq(PROD.unitBudgetHour, 600, "the hourly budget is production's 600");
    let refusals = 0;
    for (let i = 0; i < 200; i++) {
      const s = await admitWith(PROD, c, "198.51.100." + (i % 250), "chat", HOUR2);
      if (!s.ok) refusals += 1;
      s.refundBudget();   // exactly what chat.js's `spentNothing` does
      s.release();
    }
    eq(refusals, 0, "all 200 are ADMITTED first — the drain is what the ROUTE BODY refuses");
    eq(c.count(UK), null, "200 charged-then-refunded requests wrote NOTHING to the colo's hour");
    eq(unitPuts(c), 0, "…not one `put` on the budget entry, which is the structural half of the claim");
    deep([cacheStats().units.published, limits.__state().units, limits.__state().budget], [0, { pending: 0, bucket: 2 }, {}],
         "…zero published, an empty ledger, and a whole in-isolate budget");
    const visitor = await admitWith(PROD, c, "203.0.113.77", "chat", HOUR2);
    eq(visitor.ok, true, "…and the next real visitor is SERVED rather than budget_exhausted");
    visitor.release();
  }

  // 15i-d. FAIL OPEN — each case stands the colo's hour AT its ceiling.
  {
    fresh();
    const refused = await admitWith(FAST12, fakeCache().seed(UK, UNITS_HOUR, undefined, 3600), "198.51.100.60", "chat", HOUR2);
    eq(`${refused.ok} ${refused.reason}`, "false budget_exhausted", "CONTROL: a working cache holding a spent hour REFUSES");

    for (const [label, make, want] of [
      ["a match that THROWS SYNCHRONOUSLY", () => fakeCache({ matchThrowsSync: true }).seed(UK, UNITS_HOUR), { errors: 1 }],
      ["a match that REJECTS", () => fakeCache({ matchRejects: true }).seed(UK, UNITS_HOUR), { errors: 1 }],
      ["a match that HANGS FOR EVER", () => fakeCache({ matchHangs: true }).seed(UK, UNITS_HOUR), { timeouts: 1 }],
      ["an entry whose body is NOT JSON", () => fakeCache({ bodyOverride: "<html>nope" }).seed(UK, UNITS_HOUR), { miss: 1 }],
      ["an entry whose count is not a number", () => fakeCache({ bodyOverride: '{"n":"lots"}' }).seed(UK, UNITS_HOUR), { miss: 1 }],
      ["a STALE entry, past its own max-age", () => fakeCache().seed(UK, UNITS_HOUR, 999999, 3600), { stale: 1 }],
      ["a store with NO METHODS AT ALL", () => ({ log: { match: 0, put: 0, keys: [], puts: [] } }), { errors: 1 }],
      ["a cache MISS — nobody has written the hour yet", () => fakeCache(), { miss: 1 }],
    ]) {
      fresh();
      const r = await admitWith(FAST12, make(), "198.51.100.60", "chat", HOUR2);
      eq(r.ok, true, `BUDGET FAILS OPEN: ${label} must still ADMIT a visitor the working cache refused`);
      eq(`${r.reason} ${cacheStats().units.refused}`, "null 0", `BUDGET FAILS OPEN: ${label} carries and records no refusal`);
      for (const [k, v] of Object.entries(want)) eq(cacheStats().units[k], v, `…and ${label} is recorded as units.${k}`);
      if (r.ok) r.release();
      eq(limits.__state().inflight.chat, 0, `…and ${label} leaks no concurrency slot`);
    }

    // A failed READ must not then publish over a live count; the ledger is KEPT (no write was attempted).
    fresh();
    const c = fakeCache({ matchRejects: true });
    (await admitWith(FAST12, c, "198.51.100.61", "chat", HOUR2)).release();
    const second = await admitWith(FAST12, c, "198.51.100.62", "chat", HOUR2);
    eq(second.ok, true, "a failed budget READ admits");
    eq(unitPuts(c), 0, "…and writes nothing to the budget entry, so a live hour is never reset to this isolate's share");
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "…and the unpublished units are KEPT");
    second.release();
  }

  // 15i-e. NO LOST WRITE CAN REFUSE A VISITOR WHO SHOULD BE SERVED.
  {
    // (1) There is no refund write to lose: a refund performs no cache op at all.
    fresh();
    const c = fakeCache();
    const s = await admitWith(TWELVE, c, "198.51.100.70", "chat", HOUR2);
    const at = c.log.match + c.log.put;
    s.refundBudget();
    s.release();
    eq(c.log.match + c.log.put, at, "a refund performs no cache READ or WRITE: there is no shared refund that could be lost");
    deep(limits.__state().units, { pending: 0, bucket: 2 }, "…and nothing reached the ledger either");

    // (2) A lost publish loses spend, never gains it — against a control where it lands.
    for (const [label, opts, wantAdmitted] of [["with every publish REJECTED", { putRejects: true }, 8],
                                               ["CONTROL, with the publishes landing", {}, 7]]) {
      fresh();
      const cc = fakeCache(opts);
      let admitted = 0, last = null;
      for (let iso = 0; iso < 4; iso++) {
        fresh(); // a new isolate every two turns, so the in-isolate map never decides
        for (let t = 0; t < 2; t++) {
          last = await admitWith(FAST12, cc, "198.51.100.1" + iso + t, "chat", HOUR2);
          if (last.ok) { admitted += 1; last.release(); }
        }
      }
      eq(admitted, wantAdmitted, `${label}: ${wantAdmitted} of eight turns admitted — a lost write can only UNDERCOUNT`);
      if (opts.putRejects) eq(cc.count(UK), null, `${label}: and the colo's hour was never written at all`);
      else eq(last.reason, "budget_exhausted", `${label}: the eighth is budget_exhausted`);
    }

    // (3) A publish that LANDS AND THEN TIMES OUT is not retried (that would double-charge the
    // colo): the ledger clears on every ATTEMPT. At production's 600 units, so this label is
    // the one that reddens, not an earlier "turn N is admitted".
    fresh();
    const slow = fakeCache({ putStoresThenHangs: true });
    for (let i = 1; i <= 4; i++) (await admitWith(FAST, slow, "198.51.100.7" + i, "chat", HOUR2)).release();
    eq(slow.count(UK), 9, "four turns, three publishes that all LANDED and timed out: the colo holds 9 units, not 18");
    deep([cacheStats().units.timeouts, cacheStats().units.wrote, cacheStats().units.published], [3, 0, 9],
         "…three timed-out writes, none confirmed, 9 units really left");
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "…and only the last turn's 3 are still owed");

    // (4) The hour roll DROPS rather than moves: units charged in hour 2 never count against hour 3.
    fresh();
    const roll = fakeCache();
    (await admitWith(TWELVE, roll, "198.51.100.40", "chat", HOUR2)).release();
    const next = await admitWith(TWELVE, roll, "198.51.100.41", "chat", HOUR2 + 3600);
    eq(next.ok, true, "the first admission of hour 3 is served");
    next.release();
    eq(roll.count(ORIGIN + "/__moxie/rl/units/3"), null, "…with hour 2's crumbs DROPPED, never carried into hour 3's entry");
    deep([cacheStats().units.dropped, limits.__state().units], [3, { pending: 3, bucket: 3 }], "…the drop recorded; the ledger now hour 3's");

    // (5) release() THEN refundBudget(): safe only because the ledger has not been shown to anyone.
    fresh();
    const late = fakeCache();
    const l = await admitWith(TWELVE, late, "198.51.100.42", "chat", HOUR2);
    l.release();
    deep(limits.__state().units, { pending: 3, bucket: 2 }, "a released turn settles its units into the ledger");
    l.refundBudget();
    deep(limits.__state().units, { pending: 0, bucket: 2 }, "…and a refund AFTER the release takes them straight back out again");
    eq(unitPuts(late), 0, "…having published nothing in between");
  }

  // 15i-h. THE ORDER: over their own MINUTE in a colo whose HOUR is spent, `rate_limited`
  // (429, one browser paces itself) wins over `budget_exhausted` (503, scripted for everybody).
  {
    fresh();
    const learn = fakeCache();
    (await admitWith(TWELVE, learn, "198.51.100.90", "chat", HOUR2)).release();
    fresh();
    const both = fakeCache().seed(learn.log.keys[0], TWELVE.chatPerMin).seed(UK, UNITS_HOUR, undefined, 3600);
    const r = await admitWith(TWELVE, both, "198.51.100.90", "chat", HOUR2);
    eq(r.ok, false, "a request both sub-tiers would refuse is refused…");
    eq(r.reason, "rate_limited", "…and a spent colo hour AND a spent minute answers rate_limited: the per-visitor condition wins");
    eq(cacheStats().units.checked, 0, "…with the budget entry not even read");
  }

  // 15i-f. THE SEAMS: off, and uncapped per scale (an uncapped HOUR must not switch off the DAY).
  {
    fresh();
    const c = fakeCache();
    (await admitWith(wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: "12", DEMO_CACHE_COUNTER: "0" }), c, "198.51.100.80", "chat", HOUR2)).release();
    deep([c.log.match + c.log.put, cacheStats().units.checked], [0, 0], "DEMO_CACHE_COUNTER=0 makes ZERO cache calls for the budget half too");

    fresh();
    const c2 = fakeCache();
    (await admitWith(wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: "0" }), c2, "198.51.100.81", "chat", HOUR2)).release();
    eq(cacheStats().units.checked, 0, "with no hourly ceiling the hour sub-tier never runs");
    eq(c2.log.keys.filter((k) => k.indexOf("/units/") >= 0 && k.indexOf("/units/d") < 0).length, 0, "…the HOUR's entry is never asked for");
    deep(limits.__state().units, { pending: 0, bucket: -1 }, "…and an hour-uncapped deployment accrues nothing to the HOUR ledger");
    eq(c2.log.keys.filter((k) => k.indexOf("/units/d") >= 0).length, 1,
       "…while the DAY's entry IS still asked for: DEMO_UNIT_BUDGET_DAY defaults to 4000 (§4.6.3)");
    eq(cacheStats().unitsDay.checked, 1, "…recorded as the day sub-tier having run on its own ceiling");

    fresh();
    const c3 = fakeCache();
    (await admitWith(wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: "0", DEMO_UNIT_BUDGET_DAY: "0" }), c3, "198.51.100.82", "chat", HOUR2)).release();
    eq(c3.log.keys.filter((k) => k.indexOf("/units/") >= 0).length, 0, "with NEITHER ceiling set, no budget entry is ever asked for");
    eq(cacheStats().units.checked + cacheStats().unitsDay.checked, 0, "…neither sub-tier runs");
    deep(limits.__state().unitsDay, { pending: 0, bucket: -1 }, "…and the DAY ledger accrues nothing either");
  }

  // 15i-g. THE WHOLE ROUTE through caches.default, the colo's hour at its ceiling for
  // whatever hour the route's own clock names (no wall-clock read here).
  {
    fresh();
    globalThis.caches = { default: fakeCache({ unitsCount: UNITS_HOUR }) };
    try {
      P.plan = { chat: { content: "hi" } };
      const spent = await call(chat, "/api/chat", { text: "hello" }, {}, { ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR) });
      eq(`${spent.res.status} ${spent.body.reason} ${spent.body.mode}`, "503 budget_exhausted degraded",
         "/api/chat answers 503 budget_exhausted (a degraded page) when the COLO's shared hour is spent");
      ok(Number(spent.res.headers.get("Retry-After")) >= 1, "…with a Retry-After the client can obey");
      deep([upstreamCalls(), limits.__state().inflight.chat], [0, 0], "…having spent nothing upstream and leaking no slot");
    } finally {
      delete globalThis.caches;
    }
  }
}
