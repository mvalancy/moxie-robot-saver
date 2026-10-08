/* shared_ceilings A–E: the seam, and the hour, day and day budget binding across isolates. */
import {
  ok, eq, deep, section, cfgOf, offOf, fresh, fakeCache, admitWith, limits,
  st, wide, unitsDay, T0, T1, DAY0, DK,
} from "./harness.mjs";
import { post } from "../common.mjs";

/* A. THE SEAM — with no store, `admit()` is the function it was before the tier.
 * Every ceiling is ONE turn (a day budget of 3 units is one chat), so a working tier would
 * refuse the second of everything; "1" units would be refused by the isolate's own map
 * before any cache was consulted, and prove nothing. */
section("A");
{
  const CEIL = { DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1", DEMO_UNIT_BUDGET_DAY: "3" };
  const ON = cfgOf(CEIL);
  const OFF = offOf(CEIL);

  eq(typeof caches, "undefined",
     "bare node HAS no caches global — which is why the absent-store path below is the default one");
  {
    fresh();
    const r = await limits.admit({ request: post("/api/chat", { text: "x" }), cfg: ON, route: "chat", nowS: T0 });
    eq(r.ok, true, "with NO cache reachable at all, admit() admits exactly as it did before the tier existed");
    eq(st().stats.cache.checked, 0, "…having consulted the minute window sub-tier not at all");
    eq(wide().checked, 0, "…nor the HOUR/DAY window sub-tier this slice added");
    eq(st().stats.cache.units.checked, 0, "…nor the unit budget's hour");
    eq(unitsDay().checked, 0, "…nor the unit budget's DAY, which is the other half of this slice");
    eq(st().inflight.chat, 1, "…and the slot it took is the ordinary one");
    r.release();
  }
  {
    fresh();
    const c = fakeCache();
    const r = await admitWith(OFF, c, "203.0.113.9", T0);
    eq(r.ok, true, "DEMO_CACHE_COUNTER=0 admits");
    eq(c.log.match + c.log.put, 0, "…and makes ZERO cache calls — the switch is a seam, not a filter");
    eq(wide().checked + unitsDay().checked, 0, "…recorded as never checked, for BOTH new sub-tiers");
    r.release();
  }
  {
    fresh();
    const r = await admitWith(ON, null, "203.0.113.9", T0);
    eq(r.ok, true, "an explicitly null store admits");
    eq(wide().checked + unitsDay().checked, 0, "…and is indistinguishable from having no cache");
    r.release();
  }
  // TEETH for all three: the same configuration WITH a store refuses.
  {
    fresh();
    const c = fakeCache();
    (await admitWith(ON, c, "203.0.113.9", T0)).release();
    fresh();
    const second = await admitWith(ON, c, "203.0.113.9", T0);
    eq(second.ok, false,
       "TEETH: the same ceilings WITH a store refuse the second turn — so the three admissions above " +
       "are the absent store talking, not a ceiling that never binds");
    eq(second.reason, "rate_limited", "…and the ceiling that bound first is the per-IP one");
  }
}

/* B/C. THE PER-IP HOUR AND DAY BIND ACROSS ISOLATES. Two isolates, one store: B's own map
 * has seen one turn and would allow it; the colo has seen three. The minute cap is high
 * enough never to be the one that refuses. */
section("B");
{
  const CEIL = { DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "3", DEMO_CHAT_PER_DAY: "1000" };
  const ON = cfgOf(CEIL);
  const IP = "198.51.100.7";
  const shared = fakeCache();

  fresh();
  for (let i = 1; i <= 2; i++) {
    const r = await admitWith(ON, shared, IP, T0);
    eq(r.ok, true, `isolate A turn ${i} is admitted — the hour allows three`);
    r.release();
  }

  fresh(); // a different isolate: a fresh Map, the same colo cache
  eq(st().windows, 0, "the new isolate's window map is empty, so its own hour count is ZERO");
  const b1 = await admitWith(ON, shared, IP, T0);
  eq(b1.ok, true, "isolate B's first turn is admitted — the colo has seen 2 of 3");
  b1.release();

  const budgetBefore = JSON.stringify(st().budget);
  const b2 = await admitWith(ON, shared, IP, T0);
  eq(b2.ok, false,
     "isolate B's SECOND is REFUSED — its own map has seen one turn this hour and would allow it; " +
     "the colo has seen three");
  eq(b2.reason, "rate_limited", "…as rate_limited, the same reason the in-isolate hour window gives");
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 3600, `…with a Retry-After inside the hour, got ${b2.retryAfterS}`);
  eq(b2.rateLimit.remaining, 0, "…and remaining: 0, so the browser paces itself the same way");
  eq(st().stats.upstreamCalls, 0, "…having called nothing upstream");
  eq(wide().refused, 1, "…recorded as one refusal by the HOUR/DAY sub-tier specifically");
  eq(st().stats.cache.refused, 0, "…and none by the minute sub-tier, which had nothing to say");
  eq(st().inflight.chat, 0, "a wide-window refusal leaves NO concurrency slot held");
  eq(JSON.stringify(st().budget), budgetBefore,
     "…and refunds the unit budget it charged, exactly as the minute sub-tier's refusal does");

  fresh();
  (await admitWith(offOf(CEIL), shared, IP, T0)).release();
  const off2 = await admitWith(offOf(CEIL), shared, IP, T0);
  eq(off2.ok, true, "CONTROL: with the tier off, that same turn is admitted — the isolate's own map alone allows it");
  off2.release();
}

section("C");
{
  const CEIL = { DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "3" };
  const ON = cfgOf(CEIL);
  const IP = "198.51.100.8";
  const shared = fakeCache();

  fresh();
  for (let i = 1; i <= 2; i++) (await admitWith(ON, shared, IP, T0)).release();

  fresh();
  const b1 = await admitWith(ON, shared, IP, T0);
  eq(b1.ok, true, "isolate B's first turn is admitted — the colo has seen 2 of 3 today");
  b1.release();
  const b2 = await admitWith(ON, shared, IP, T0);
  eq(b2.ok, false, "isolate B's SECOND is REFUSED on a DAY count neither isolate's map has seen");
  eq(b2.reason, "rate_limited", "…as rate_limited");
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 86400, `…with a Retry-After inside the day, got ${b2.retryAfterS}`);
  ok(b2.retryAfterS > 3600,
     `…and a LONGER one than the hour's, because it is the day's window that has closed, got ${b2.retryAfterS}`);

  // The next DAY is a different bucket (and a different key): both halves at once.
  fresh();
  const b3 = await admitWith(ON, shared, IP, T1);
  eq(b3.ok, true, "…and tomorrow the same visitor is admitted again: the day's count is stamped, not eternal");
  b3.release();

  fresh();
  (await admitWith(offOf(CEIL), shared, IP, T0)).release();
  const off2 = await admitWith(offOf(CEIL), shared, IP, T0);
  eq(off2.ok, true, "CONTROL: with the tier off, that same turn is admitted");
  off2.release();

  // The voice's and the ears' DAY bind across isolates the same way (DEMO_SPEECH_PER_DAY /
  // DEMO_STT_PER_DAY): an address hopping isolates cannot spend either around the clock.
  for (const [route, knobs] of [
    ["speech", { DEMO_SPEECH_PER_MIN: "60", DEMO_SPEECH_PER_HOUR: "1000", DEMO_SPEECH_PER_DAY: "3" }],
    ["transcribe", { DEMO_STT_MODEL: "test-ears-model", DEMO_STT_PER_MIN: "60", DEMO_STT_PER_HOUR: "1000", DEMO_STT_PER_DAY: "3" }],
  ]) {
    const RON = cfgOf(knobs);
    const RIP = route === "speech" ? "198.51.100.9" : "198.51.100.10";
    const store = fakeCache();
    fresh();
    for (let i = 1; i <= 2; i++) (await admitWith(RON, store, RIP, T0, route)).release();
    fresh();
    const r1 = await admitWith(RON, store, RIP, T0, route);
    eq(r1.ok, true, `/${route}: isolate B's first call is admitted — the colo has seen 2 of 3 today`);
    r1.release();
    const r2 = await admitWith(RON, store, RIP, T0, route);
    eq(`${r2.ok} ${r2.reason}`, "false rate_limited",
       `/${route}: isolate B's SECOND is REFUSED on a DAY count its own map never saw`);
    ok(r2.retryAfterS > 3600 && r2.retryAfterS <= 86400, `/${route}: …until the DAY's end, got ${r2.retryAfterS}`);
    fresh();
    const r3 = await admitWith(RON, store, RIP, T1, route);
    eq(r3.ok, true, `/${route}: …and tomorrow it is admitted again`);
    r3.release();
    fresh();
    (await admitWith(offOf(knobs), store, RIP, T0, route)).release();
    const off3 = await admitWith(offOf(knobs), store, RIP, T0, route);
    eq(off3.ok, true, `/${route}: CONTROL: with the tier off, that same call is admitted`);
    off3.release();
  }
}

/* D. THE UNIT BUDGET'S DAY, by charge-on-completion: the colo never hears of a charge that
 * might be given back. 12 units = four chat turns; the HOUR is wide open; four addresses,
 * so the per-IP window never gets a word in. */
section("D");
{
  const CEIL = {
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
  };
  const ON = cfgOf(CEIL);
  const shared = fakeCache();

  fresh();
  for (let i = 1; i <= 4; i++) {
    const r = await admitWith(ON, shared, "198.51.100.1" + i, T0);
    eq(r.ok, true, `isolate A turn ${i} is admitted — 12 units is exactly four chat turns`);
    r.release();
  }
  eq(shared.count(DK), 9,
     "isolate A published 9 of the 12 units it spent: the ledger publishes on the NEXT admission, so " +
     "the last turn's 3 are still unpublished. THAT LAG IS THE DESIGN, and it undercounts");
  deep(st().unitsDay, { pending: 3, bucket: DAY0 },
       "…and those 3 sit in this isolate's DAY ledger as a RECORDED fact, not an inference");
  eq(unitsDay().published, 9, "…9 units recorded as handed to the colo");
  eq(unitsDay().wrote, 3, "…in three writes: turn 1 owed nothing, turns 2-4 owed 3 each");
  eq((shared.store.get(DK) || {}).maxAge, 86400,
     "the day entry's max-age is ONE DAY — its own window — so an entry cannot outlive it and be " +
     "read as today's");
  deep(shared.body(DK), { n: 9 },
       "…and the stored body is a bare count: the entry sits under a URL an outsider could ask for");

  fresh(); // a different isolate: fresh Map, fresh ledgers, SAME colo cache
  deep(st().unitsDay, { pending: 0, bucket: -1 }, "the new isolate's day ledger starts empty");
  const b1 = await admitWith(ON, shared, "198.51.100.21", T0);
  eq(b1.ok, true, "isolate B's first turn is admitted — the colo has been told about 9 of 12");
  b1.release();

  const budgetBefore = JSON.stringify(st().budget);
  const b2 = await admitWith(ON, shared, "198.51.100.22", T0);
  eq(b2.ok, false,
     "isolate B's SECOND is REFUSED — its own map has seen 3 units and would allow it; the colo has seen 12");
  eq(b2.reason, "budget_exhausted",
     "…as budget_exhausted, the reason the in-isolate day budget gives for the same fact");
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 86400, `…with a Retry-After inside the day, got ${b2.retryAfterS}`);
  eq(st().stats.upstreamCalls, 0, "…having called nothing upstream");
  eq(unitsDay().refused, 1, "…recorded as one DAY-budget refusal");
  eq(st().stats.cache.units.refused, 0, "…and none by the hour, which is nowhere near its ceiling");
  eq(st().inflight.chat, 0, "a day-budget refusal leaves NO concurrency slot held");
  eq(JSON.stringify(st().budget), budgetBefore, "…and refunds the in-isolate units it charged");
  eq(shared.count(DK), 9, "…and PUBLISHES NOTHING: a request that was refused spent nothing to report");
  eq(unitsDay().published, 0, "…recorded as zero units published by this isolate");

  fresh();
  (await admitWith(offOf(CEIL), shared, "198.51.100.21", T0)).release();
  const off2 = await admitWith(offOf(CEIL), shared, "198.51.100.22", T0);
  eq(off2.ok, true, "CONTROL: with the tier off, that same turn is admitted — the map alone allows it");
  off2.release();
}

/* E. THE FREE DRAIN, CLOSED STRUCTURALLY — a refunded request publishes NOTHING, on both
 * orderings of `refundBudget()`/`release()`. The drain is `test_turnstile.mjs` §12's attack
 * aimed at the shared DAY. */
section("E");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "1000", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
  });
  const shared = fakeCache();
  fresh();
  for (let i = 0; i < 200; i++) {
    const r = await admitWith(ON, shared, "203.0.113.7", T0);
    if (!r.ok) { ok(false, "the drain's admissions must not be refused by anything else"); break; }
    r.refundBudget();
    r.release();
  }
  eq(shared.count(DK), null, "200 refunded requests wrote NOTHING to the colo's day: there is no entry at all");
  eq(unitsDay().published, 0, "…zero units published");
  eq(unitsDay().wrote, 0, "…and zero writes attempted, which is the structural half of the claim");
  deep(st().unitsDay, { pending: 0, bucket: DAY0 }, "…and the ledger is empty, because nothing ever settled");

  // `release()` THEN `refundBudget()`: the only ordering that reaches `unaccrueDayPending()`.
  fresh();
  const late = fakeCache();
  const l = await admitWith(ON, late, "203.0.113.8", T0);
  eq(l.ok, true, "the release-then-refund turn is admitted in the first place");
  l.release();
  deep(st().unitsDay, { pending: 3, bucket: DAY0 }, "a released turn settles its units into the DAY ledger");
  l.refundBudget();
  deep(st().unitsDay, { pending: 0, bucket: DAY0 },
       "…and a refund AFTER the release takes them straight back out of the DAY ledger too");
  eq(late.count(DK), null, "…with still nothing published to the colo's day, on either ordering");
}
