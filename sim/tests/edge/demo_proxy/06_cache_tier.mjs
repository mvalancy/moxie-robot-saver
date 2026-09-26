/* test_demo_proxy §15a–h: the Cache API tier's per-IP windows, shared across a colo's
 * isolates. Run via the entry file, never alone.
 *
 * `caches.default` does not exist under node, so the tier runs against `fakeCache` — the
 * only way to make it hang, go stale or throw ON PURPOSE. Every error it can make must be
 * an undercount: off, absent, broken, slow or lying, the visitor is ADMITTED. Not asserted,
 * because it is not true: that the tier is a global ceiling (it is per-colo, §4.6.1 f).
 */
import {
  FULL, ORIGIN, P, call, chat, deep, eq, fresh, limits, ok, req, upstreamCalls, wire2,
  fakeCache, cacheStats, admitWith,
} from "./harness.mjs";

{
  const ON = wire2.readConfig(FULL);
  const OFF = wire2.readConfig({ ...FULL, DEMO_CACHE_COUNTER: "0" });
  /** The clamp floor, so a hang is measured in milliseconds rather than in a quarter of a
   *  second per assertion. */
  const FAST = wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "10" });

  // ---- 15a. THE SEAM: no cache, and `admit()` is the function it was before -- //
  eq(ON.cacheCounter, true, "DEMO_CACHE_COUNTER defaults ON — the tier ships enabled");
  eq(OFF.cacheCounter, false, "DEMO_CACHE_COUNTER=0 switches the tier off with no code change");
  eq(ON.cacheTimeoutMs, 250, "DEMO_CACHE_TIMEOUT_MS defaults to 250 ms — ~5x the measured cost of THREE ops");
  eq(wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "999999" }).cacheTimeoutMs, 250,
     "…and an out-of-range deadline falls back to the default rather than becoming a bigger one");
  eq(wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "1" }).cacheTimeoutMs, 250,
     "…in both directions: a 1 ms deadline would switch the tier off by stealth");
  for (const k of ["cache_counter", "cache_timeout_ms", "DEMO_CACHE_COUNTER", "DEMO_CACHE_TIMEOUT_MS"]) {
    ok(!(k in wire2.publicLimits(ON)), `the tier is server-side only: ${k} is not published to the browser`);
  }

  eq(typeof caches, "undefined",
     "bare node HAS no caches global — which is exactly why the absent-cache path below is the default one");
  {
    fresh();
    // No `cache` key at all: the production shape on a runtime with no Cache API.
    const r = await limits.admit({ request: req("/api/chat", { text: "x" }), cfg: ON, route: "chat" });
    eq(r.ok, true, "with NO cache reachable at all, admit() admits exactly as it did before the tier existed");
    eq(cacheStats().checked, 0, "…having consulted no tier at all");
    eq(limits.__state().inflight.chat, 1, "…and the slot it took is the ordinary one");
    r.release();
  }
  {
    fresh();
    const c = fakeCache();
    const r = await admitWith(OFF, c, "203.0.113.9");
    eq(r.ok, true, "DEMO_CACHE_COUNTER=0 admits");
    eq(c.log.match + c.log.put, 0, "…and makes ZERO cache calls — the switch is a seam, not a filter");
    eq(cacheStats().checked, 0, "…recorded as never checked");
    r.release();
  }
  {
    fresh();
    const r = await admitWith(ON, null, "203.0.113.9");
    eq(r.ok, true, "an explicitly null store admits");
    eq(cacheStats().checked, 0, "…and is indistinguishable from having no cache");
    r.release();
  }

  // ---- 15b. IT ADDS A REFUSAL THE IN-ISOLATE MAP WOULD NOT MAKE ------------ //
  // Two isolates, one colo: `__reset()` is the isolate boundary (new `Map`, SAME cache).
  // The second isolate's map alone would allow five more turns; the tier stops it at the
  // shared fifth — the measured ×7 isolate multiplier collapsing to ×1.
  const IP = "198.51.100.7";
  {
    const shared = fakeCache();
    fresh();
    const held = [];
    for (let i = 1; i <= 3; i++) {
      const r = await admitWith(ON, shared, IP);
      eq(r.ok, true, `isolate A turn ${i} is admitted`);
      held.push(r);
    }
    for (const h of held) h.release();
    eq(shared.count(shared.log.keys[0]), 3, "the shared entry counts all three of isolate A's turns");

    fresh(); // ---- a different isolate: a fresh Map, the same colo cache.
    const b1 = await admitWith(ON, shared, IP);
    eq(b1.ok, true, "isolate B's first turn is admitted — shared count 4 of 5");
    b1.release();
    const b2 = await admitWith(ON, shared, IP);
    eq(b2.ok, true, "isolate B's second is admitted — shared count 5 of 5");
    b2.release();

    const budgetBefore = JSON.stringify(limits.__state().budget);
    const b3 = await admitWith(ON, shared, IP);
    eq(b3.ok, false, "isolate B's THIRD is REFUSED — its own map has only seen two, the colo has seen five");
    eq(b3.reason, "rate_limited", "…as rate_limited, the same reason the in-isolate window gives");
    ok(b3.retryAfterS >= 1 && b3.retryAfterS <= 60, `…with a Retry-After inside the minute, got ${b3.retryAfterS}`);
    eq(b3.rateLimit.remaining, 0, "…and remaining: 0, so the browser paces itself the same way");
    eq(upstreamCalls(), 0, "…having called nothing upstream");
    eq(cacheStats().refused, 1, "…recorded as one tier refusal");

    // 15c. THE REFUSAL COSTS NOTHING: the slot is given back and the charge refunded.
    eq(limits.__state().inflight.chat, 0, "a tier refusal leaves NO concurrency slot held");
    eq(JSON.stringify(limits.__state().budget), budgetBefore,
       "…and refunds the unit budget it charged, exactly as the at_capacity path does");
    eq(shared.count(shared.log.keys[0]), 5, "…and does not count itself: a refused request spent nothing");

    // …and the SAME sequence with the tier off is admitted, which is what makes the
    // assertion above a statement about the tier rather than about the arithmetic.
    fresh();
    const control = fakeCache();
    for (let i = 1; i <= 3; i++) (await admitWith(ON, control, IP)).release();
    fresh();
    for (let i = 1; i <= 2; i++) (await admitWith(OFF, control, IP)).release();
    const off3 = await admitWith(OFF, control, IP);
    eq(off3.ok, true, "CONTROL: with the tier off, that same third turn is admitted — the map alone allows it");
    off3.release();
  }

  // ---- 15d. THE LATENCY BUDGET, AS A COUNT OF OPS -------------------------- //
  // `admit()` is in every turn's path, so the op count IS the latency budget and is pinned
  // EXACTLY: four sub-tiers (per-IP minute; per-IP hour+day sharing ONE entry; the unit
  // budget's hour; its day), four reads. An inequality that no longer bounds anything is
  // worse than a red check.
  {
    fresh();
    const c = fakeCache();
    const r = await admitWith(ON, c, "198.51.100.20");
    eq(r.ok, true, "an admitted request");
    eq(c.log.match, 4,
       "…reads FOUR shared entries: the per-IP minute window, the per-IP hour+day window (ONE entry " +
       "for both scales), the unit budget's hour, then its day");
    eq(c.log.put, 2,
       "…and writes exactly TWO of them back — BOTH windows, and neither budget: a budget publishes " +
       "only what this isolate already OWES, and a first admission owes nothing yet (§15i)");
    eq(c.log.match + c.log.put, 6,
       "…so an isolate's FIRST turn costs SIX ops (~90 ms at §4.6.1 row h's ~15 ms per op), pinned " +
       "as an equality because §4.6.3 accepted that cost on the record");
    eq(cacheStats().ops, 2, "…recorded as two completed cache ops for the window half");
    eq(cacheStats().wrote, 1, "…one of them a write");
    eq(cacheStats().units.ops, 1, "…and one for the budget half: the read, with nothing to publish");
    eq(cacheStats().units.wrote, 0, "…which wrote nothing");
    r.release();

    // The SECOND turn in the same isolate is the four-op case: the first one's 3 units are
    // now in the ledger, so the budget half publishes them. This is the whole latency cost
    // of the second sub-tier, asserted rather than intended.
    {
      const c2 = fakeCache();
      fresh();
      (await admitWith(ON, c2, "198.51.100.22")).release();
      const before = c2.log.match + c2.log.put;
      (await admitWith(ON, c2, "198.51.100.23")).release();
      eq(c2.log.match + c2.log.put - before, 8,
         "a turn whose isolate owes units costs EIGHT ops: four reads, two window writes, and two " +
         "budget publishes — the hour's and the day's");
    }

    fresh();
    const full = fakeCache().seed(ORIGIN + "/__moxie/rl/chat/x/0", 99);
    // Drive the refusal through the real key by seeding whatever key the tier asks for.
    const probe = await admitWith(ON, full, "198.51.100.21");
    probe.release();
    const realKey = full.log.keys[0] || "no-key";
    fresh();
    const atLimit = fakeCache().seed(realKey, ON.chatPerMin);
    const refused = await admitWith(ON, atLimit, "198.51.100.21");
    eq(refused.ok, false, "a request the tier refuses");
    eq(atLimit.log.match, 1, "…still reads once");
    eq(atLimit.log.put, 0, "…and writes NOTHING: a refusal spends nothing, so it counts nothing");
    eq(cacheStats().ops, 1, "…one cache op for a refusal, two for an admission");
  }

  // ---- 15e. FAIL OPEN — every failure mode, asserted in that direction ----- //
  // Each case seeds the entry AT the limit, so a WORKING cache would refuse; the required
  // answer is `ok: true` every time. The tier may miss a refusal, never cost a turn.
  {
    // First learn the key this IP/route/minute uses, so every case below can seed it.
    fresh();
    const learn = fakeCache();
    (await admitWith(FAST, learn, "198.51.100.30", "chat", 1000)).release();
    const KEY = learn.log.keys[0] || "no-key";

    const modes = [
      ["a cache MISS", () => fakeCache(), { miss: 1 }],
      ["a STALE entry, past its own max-age", () => fakeCache().seed(KEY, 99, 999, 60), { stale: 1 }],
      ["a match that THROWS SYNCHRONOUSLY", () => fakeCache({ matchThrowsSync: true }).seed(KEY, 99), { errors: 1 }],
      ["a match that REJECTS", () => fakeCache({ matchRejects: true }).seed(KEY, 99), { errors: 1 }],
      ["a match that HANGS FOR EVER", () => fakeCache({ matchHangs: true }).seed(KEY, 99), { timeouts: 1 }],
      ["an entry whose body is NOT JSON", () => fakeCache({ bodyOverride: "<html>nope" }).seed(KEY, 99), { miss: 1 }],
      ["an entry whose count is not a number", () => fakeCache({ bodyOverride: '{"n":"many"}' }).seed(KEY, 99), { miss: 1 }],
      ["a store with NO METHODS AT ALL", () => ({ log: { match: 0, put: 0, keys: [], puts: [] } }), { errors: 1 }],
    ];
    for (const [label, make, want] of modes) {
      fresh();
      const c = make();
      const r = await admitWith(FAST, c, "198.51.100.30", "chat", 1000);
      eq(r.ok, true, `FAIL OPEN: ${label} must still ADMIT a visitor the working cache would have refused`);
      eq(r.reason, null, `FAIL OPEN: ${label} carries no refusal reason`);
      eq(cacheStats().refused, 0, `FAIL OPEN: ${label} records no tier refusal`);
      for (const [k, v] of Object.entries(want)) {
        eq(cacheStats()[k], v, `…and ${label} is recorded as ${k}`);
      }
      if (r.ok) r.release();
      eq(limits.__state().inflight.chat, 0, `…and ${label} leaks no concurrency slot`);
    }

    // The write half fails the same way — and here the visitor was going to be admitted
    // anyway, so what is being asserted is that a failed WRITE neither refuses nor throws.
    for (const [label, opts, want] of [
      ["a put that THROWS SYNCHRONOUSLY", { putThrowsSync: true }, { errors: 1 }],
      ["a put that REJECTS", { putRejects: true }, { errors: 1 }],
      ["a put that HANGS FOR EVER", { putHangs: true }, { timeouts: 1 }],
    ]) {
      fresh();
      const c = fakeCache(opts);
      const r = await admitWith(FAST, c, "198.51.100.31", "chat", 1000);
      eq(r.ok, true, `FAIL OPEN: ${label} must not refuse the visitor whose turn it was writing`);
      eq(cacheStats().wrote, 0, `…${label} stored nothing`);
      for (const [k, v] of Object.entries(want)) eq(cacheStats()[k], v, `…and ${label} is recorded as ${k}`);
      eq(cacheStats().ops, 1, `…${label} completed only the read`);
      r.release();
    }

    // THE OUTER SEATBELT: a throw OUTSIDE any cache op (an unreadable deadline, standing
    // in for a missing `crypto.subtle`), caught only by `sharedThenGrant`'s own `try`.
    fresh();
    const hostileCfg = new Proxy(FAST, {
      get(t, k) {
        if (k === "cacheTimeoutMs") throw new Error("the config itself blew up");
        return t[k];
      },
    });
    const seat = await admitWith(hostileCfg, fakeCache().seed(KEY, 99), "198.51.100.30", "chat", 1000);
    eq(seat.ok, true, "FAIL OPEN: a throw OUTSIDE every cache op still admits — the outer seatbelt holds");
    eq(seat.reason, null, "…with no refusal reason");
    eq(cacheStats().errors, 1, "…recorded as a tier error");
    eq(cacheStats().refused, 0, "…and never as a refusal");
    seat.release();

    // A read that failed must NOT then write `1` over a live count: that would reset the
    // colo's window to one and is a far larger undercount than simply not writing.
    fresh();
    const broken = fakeCache({ matchRejects: true }).seed(KEY, 4);
    const r = await admitWith(FAST, broken, "198.51.100.30", "chat", 1000);
    eq(r.ok, true, "a failed READ admits");
    eq(broken.log.put, 0, "…and writes nothing at all, so a live count of 4 is not reset to 1");
    eq(broken.count(KEY), 4, "…the stored count is untouched");
    r.release();
  }

  // ---- 15f. THE FREE REFUSALS STAY FREE ----------------------------------- //
  //
  // The in-isolate map decides FIRST. Its refusal is synchronous and costs nothing, and
  // the tier must not have put a network round trip in front of it.
  {
    fresh();
    const c = fakeCache();
    for (let i = 1; i <= ON.chatPerMin; i++) (await admitWith(ON, c, "198.51.100.40", "chat", 2000)).release();
    const sixth = await admitWith(ON, c, "198.51.100.40", "chat", 2000);
    eq(sixth.ok, false, "the 6th turn in a minute is refused by the in-isolate map, as before");
    eq(sixth.reason, "rate_limited", "…as rate_limited");
    eq(c.log.match, ON.chatPerMin * 4,
       "…and the tier was consulted for 5 turns, not 6 (FOUR reads each, one per sub-tier): " +
       "a free refusal never pays for a cache round trip");

    // The same for the origin pin, which is the cheapest refusal of all.
    fresh();
    const c2 = fakeCache();
    const hotlinked = await limits.admit({
      request: req("/api/chat", { text: "x" }, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }),
      cfg: ON, route: "chat", cache: c2,
    });
    eq(hotlinked.ok, false, "a forbidden origin is refused");
    eq(hotlinked.reason, "forbidden_origin", "…as forbidden_origin");
    eq(c2.log.match + c2.log.put, 0, "…without touching the cache at all");
  }

  // ---- 15g. THE KEY: no address in it, and it rotates every minute --------- //
  {
    fresh();
    const c = fakeCache();
    (await admitWith(ON, c, "203.0.113.99", "chat", 3000)).release();
    eq(c.log.keys.length, 4,
       "one admitted turn asks the cache for FOUR keys: the minute window's, the hour+day window's, " +
       "the budget's hour, the budget's day");
    const key = c.log.keys[0] || "";
    ok(key.startsWith(ORIGIN + "/__moxie/rl/chat/"),
       `the entry lives on our OWN origin, under a non-route prefix — got ${key}`);
    ok(!key.includes("203.0.113.99"), "the visitor's ADDRESS is never in a cache key");
    const tag = key.slice((ORIGIN + "/__moxie/rl/chat/").length).split("/")[0];
    ok(/^[0-9a-f]{24}$/.test(tag), `…only a 96-bit keyed tag of it, got ${JSON.stringify(tag)}`);

    fresh();
    const c2 = fakeCache();
    (await admitWith(ON, c2, "203.0.113.99", "chat", 3000)).release();
    eq(c2.log.keys[0] || "", key, "the same visitor in the same minute keys the same entry, or nothing would count");

    fresh();
    const c3 = fakeCache();
    (await admitWith(ON, c3, "203.0.113.98", "chat", 3000)).release();
    ok((c3.log.keys[0] || "") !== key, "a DIFFERENT visitor keys a different entry");

    fresh();
    const c4 = fakeCache();
    (await admitWith(ON, c4, "203.0.113.99", "chat", 3060)).release();
    ok((c4.log.keys[0] || "") !== key, "…and the next MINUTE keys a different entry, so the hot key rotates itself");

    fresh();
    const c5 = fakeCache();
    (await admitWith(ON, c5, "2001:db8:1:2:3:4:5:6", "chat", 3000)).release();
    ok(!String(c5.log.keys[0] || "").includes("2001"), "an IPv6 /64 is not in the key either");

    // The stored body is a bare count and nothing else — the entry sits under a URL an
    // outsider could in principle ask for, so what it can tell them has to be nothing.
    fresh();
    const c6 = fakeCache();
    (await admitWith(ON, c6, "203.0.113.97", "chat", 3000)).release();
    const stored = c6.store.get(c6.log.keys[0]) || { body: "null" };
    deep(JSON.parse(stored.body), { n: 1 }, "the stored entry is a bare count: no address, no route history, nothing");
  }

  // ---- 15h. THE WHOLE ROUTE, THROUGH THE REAL `caches.default` LOOKUP ------ //
  // A fake installed as the GLOBAL `caches.default` (production's branch), driving
  // `/api/chat` end to end: the envelope, the 429, `Retry-After`, zero upstream calls.
  {
    fresh();
    const c = fakeCache();
    globalThis.caches = { default: c };
    try {
      P.plan = { chat: { content: "hi" } };
      const first = await call(chat, "/api/chat", { text: "hello" });
      eq(first.res.status, 200, "a served turn, with the tier reading the real caches.default");
      eq(c.log.match, 4, "…which consulted the global store for all FOUR sub-tiers");
      const key = c.log.keys[0] || "no-key";

      // Now stand the shared count at the ceiling, as five turns from another isolate
      // in this colo would have, and reset the in-isolate map so ONLY the tier can refuse.
      fresh();
      c.store.set(key, { body: JSON.stringify({ n: ON.chatPerMin }), maxAge: 60 });
      const refused = await call(chat, "/api/chat", { text: "hello" });
      eq(refused.res.status, 429, "/api/chat answers 429 when the colo's shared window is spent");
      eq(refused.body.reason, "rate_limited", "…with the §4.5 reason");
      ok(Number(refused.res.headers.get("Retry-After")) >= 1, "…and a Retry-After the client can obey");
      eq(upstreamCalls(), 0, "…having spent nothing upstream");
    } finally {
      delete globalThis.caches;
    }
    eq(typeof caches, "undefined", "the global is put back, so no later block inherits a cache");
  }
}
