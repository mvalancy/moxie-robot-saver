/* test_demo_proxy — §15: the Cache API tier (shared windows and unit budget). Run via the entry file, never alone. */
import {
  FULL, KEY, ORIGIN, P, call, chat, deep, eq,
  fresh, here, limits, ok, req, speech, upstreamCalls, wire2,
} from "./harness.mjs";

/* =========================================================================== *
 * 15. THE CACHE API TIER — a second per-IP minute window, shared across the
 *     isolates of one colo
 * =========================================================================== *
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.6 ('Counters, honestly') and §4.6.1
 * (the preview measurement that cleared this tier to be built), §4.1 (the per-IP windows),
 * §4.5 (the status and `Retry-After` table).
 *
 * `caches.default` DOES NOT EXIST UNDER BARE NODE, so the tier is driven here against a
 * fake with the same two-method surface. The fake is not a convenience: it is the only way
 * to make the failure modes happen ON PURPOSE. A real cache will not hang for you, will
 * not hand you an entry past its own `max-age`, and will not throw on `put` on the day you
 * are looking. Each of those is simulated below and each one has the SAME required
 * outcome — **the visitor is admitted** — because every error this tier can make is an
 * undercount and it must fail OPEN.
 *
 * The two properties this section exists to prove, above the arithmetic:
 *
 *   1. **IT ONLY EVER ADDS REFUSALS.** With the tier off, absent, broken, slow or lying,
 *      `admit()` answers exactly what it answered before the tier existed. 15a and 15e.
 *   2. **IT NEVER REFUSES A VISITOR WHO SHOULD BE ALLOWED.** Asserted in that direction
 *      explicitly, for each failure mode by name, against a cache entry that a WORKING
 *      cache would have refused on. 15e.
 *
 * And the thing this section deliberately does NOT assert, because it is not true: that
 * the tier is a global ceiling. It is per-colo, a burst loses about two thirds of its
 * writes (§4.6.1 row f), and 15d's op count is the whole of what it costs.
 */
{
  /** A fake `caches.default`. `match`/`put` only — the two methods the tier uses — plus a
   *  log of what it was actually asked, so every assertion below is on a RECORDED fact
   *  rather than on an inference from behaviour (playbook rule 11).
   *
   *  Each failure switch is a DIFFERENT SHAPE of failure on purpose: a synchronous throw
   *  (before any promise exists), a rejected promise, and a promise that never settles.
   *  The first is the one a naive `try { await x() }` still catches and a naive
   *  `Promise.resolve(x()).catch()` does not. */
  function fakeCache(opts) {
    const o = opts || {};
    const store = new Map();
    const log = { match: 0, put: 0, keys: [], puts: [] };
    const hang = () => new Promise(() => {});
    return {
      log,
      store,
      /** Pre-load an entry as if another isolate had written it. `ageS` past `maxAge`
       *  makes it the stale entry a real cache would never serve. */
      seed(key, n, ageS, maxAge) {
        store.set(String(key), { body: JSON.stringify({ n }), maxAge: maxAge === undefined ? 60 : maxAge, ageS });
        return this;
      },
      count(key) {
        const e = store.get(String(key));
        if (!e) return null;
        try { return JSON.parse(e.body).n; } catch { return null; }
      },
      match(key) {
        log.match += 1;
        log.keys.push(String(key));
        if (o.matchThrowsSync) throw new Error("match threw synchronously");
        if (o.matchHangs) return hang();
        if (o.matchRejects) return Promise.reject(new Error("match rejected"));
        // THE BUDGET ENTRY, WITHOUT READING THE CLOCK. `/api/chat` derives its own hour
        // bucket from `Date.now()`, so a test that pre-seeded that exact key would have to
        // read the wall clock too — which `sim/tests/test_clock_dependence.py` refuses on
        // sight, and rightly: the result would then depend on which side of an hour
        // boundary the suite happened to run. `unitsCount` answers whatever hour the route
        // asks for, which is the same assertion with no clock in it.
        if (o.unitsCount !== undefined && String(key).indexOf("/__moxie/rl/units/") >= 0) {
          return Promise.resolve(new Response(JSON.stringify({ n: o.unitsCount }), {
            headers: { "Content-Type": "application/json", "Cache-Control": "max-age=3600" },
          }));
        }
        const e = store.get(String(key));
        if (!e) return Promise.resolve(undefined);
        const h = { "Content-Type": "application/json", "Cache-Control": "max-age=" + e.maxAge };
        if (e.ageS !== undefined) h.Age = String(e.ageS);
        return Promise.resolve(new Response(o.bodyOverride === undefined ? e.body : o.bodyOverride, { headers: h }));
      },
      put(key, res) {
        log.put += 1;
        log.puts.push(String(key));
        if (o.putThrowsSync) throw new Error("put threw synchronously");
        if (o.putHangs) return hang();
        if (o.putRejects) return Promise.reject(new Error("put rejected"));
        const write = (async () => {
          const body = await res.text();
          const cc = /max-age=(\d+)/.exec(res.headers.get("Cache-Control") || "");
          store.set(String(key), { body, maxAge: cc ? Number(cc[1]) : 0 });
        })();
        // THE WRITE THAT LANDS AND THEN NEVER TELLS YOU. This is not a hypothetical
        // shape: a `put` that has committed and whose promise is then lost to a deadline
        // is exactly what makes "retry the unpublished units" a DOUBLE COUNT, which is an
        // overcount, which refuses a visitor who should be served. §15i-f drives it.
        if (o.putStoresThenHangs) return write.then(() => hang());
        return write;
      },
    };
  }

  const cacheStats = () => limits.__state().stats.cache;
  /** One admission, straight at `admit()`, with a cache injected. `cache: null` is the
   *  "there is no cache here" case and is NOT the same as omitting the key. */
  const admitWith = (cfg, cache, ip, route, nowS) =>
    limits.admit({
      request: req("/api/" + (route || "chat"), { text: "x" }, { "CF-Connecting-IP": ip || "203.0.113.9" }),
      cfg,
      route: route || "chat",
      cache,
      nowS,
    });

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
  //
  // Two isolates, one colo. `__reset()` between them is the isolate boundary: a brand new
  // `Map`, the SAME cache. `chat_per_min` is 5, so the second isolate's map alone would
  // allow five more turns. The tier stops it at the shared fifth — which is the entire
  // reason this tier exists, and the measured ×7 isolate multiplier collapsing to ×1.
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
  //
  // §4.6.1 row h measured THREE cache ops at <=44 ms. `admit()` sits in the request path of
  // every turn, so the op count is the budget and it is asserted, not intended.
  //
  // **THE NUMBERS BELOW DOUBLED ON 2026-09-06 AND THAT IS A DESIGN CHANGE, NOT A LOOSENED
  // ASSERTION.** §4.6.3 lifted the remaining ceilings onto this tier: the per-IP HOUR and
  // DAY windows (which SHARE ONE ENTRY, so both scales cost one round trip between them —
  // that is the whole answer to the latency objection §4.6.1 rejected them on) and the
  // unit budget's DAY. Four sub-tiers, so four reads. Each pin below is re-pinned to an
  // EXACT new value rather than relaxed to an inequality, and the one that WAS an
  // inequality (`<= 3`) is now an equality, because the claim it made — "still inside row
  // h's three ops" — is false and a bound that no longer bounds is worse than a red one.
  {
    fresh();
    const c = fakeCache();
    const r = await admitWith(ON, c, "198.51.100.20");
    eq(r.ok, true, "an admitted request");
    eq(c.log.match, 4,
       "…reads FOUR shared entries: the per-IP minute window, the per-IP hour+day window (ONE entry " +
       "for both scales), the unit budget's hour, then its day. It was 2 before §4.6.3");
    eq(c.log.put, 2,
       "…and writes exactly TWO of them back — BOTH windows, and neither budget. That the budget " +
       "half still writes nothing here is the unchanged half of this pin: a budget publishes only " +
       "what this isolate already OWES, and a first admission owes nothing yet (§15i). It was 1 " +
       "before §4.6.3 because there was one window entry; now there are two");
    eq(c.log.match + c.log.put, 6,
       "…so an isolate's FIRST turn costs SIX ops — DOUBLE the three §4.6.1 row h measured, which by " +
       "row h's own ~15 ms per op extrapolates to ~90 ms. Pinned as an equality rather than left as " +
       "the `<= 3` bound it was: that bound is now false, and §4.6.3 accepted the doubling on the " +
       "record rather than by accident");
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
         "budget publishes — the hour's and the day's. It was 4 when there was one window entry and " +
         "one budget ledger (§4.6.3)");
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
  //
  // Each case seeds the shared entry AT the limit, so a WORKING cache would refuse. The
  // required answer in every single one is `ok: true`. This is the assertion that says the
  // tier can cost a refusal that should have happened and can NEVER cost a turn that
  // should have been served.
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

    // THE OUTER SEATBELT. Everything above fails INSIDE a cache op, where `withDeadline`
    // catches it. This one throws OUTSIDE any of them — a config whose deadline cannot even
    // be read, standing in for a `crypto.subtle` that is not there or a URL that will not
    // parse — so it can only be caught by `sharedThenGrant`'s own `try`. Same requirement:
    // the visitor keeps their turn.
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
       "a free refusal never pays for a cache round trip. The multiplier moved from 2 to 4 with " +
       "§4.6.3's two new sub-tiers; the property being guarded — that the 6th turn costs ZERO round " +
       "trips because the in-isolate map refused it first — is unchanged, and it is the reason this " +
       "is a multiple of `chatPerMin` rather than a bare number");

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
       "the budget's hour, the budget's day (§4.6.3; it was 2)");
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
  //
  // Everything above injects the store. This block installs a fake as the GLOBAL
  // `caches.default`, which is the branch production actually takes, and drives
  // `/api/chat` end to end: the §4.5 envelope, the 429, the `Retry-After` header, and zero
  // upstream calls.
  {
    fresh();
    const c = fakeCache();
    globalThis.caches = { default: c };
    try {
      P.plan = { chat: { content: "hi" } };
      const first = await call(chat, "/api/chat", { text: "hello" });
      eq(first.res.status, 200, "a served turn, with the tier reading the real caches.default");
      eq(c.log.match, 4, "…which consulted the global store for all FOUR sub-tiers (§4.6.3; it was 2)");
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

  /* ---- 15i. THE UNIT BUDGET SUB-TIER — the deployment's HOUR, shared across a colo -- //
   *
   * Spec: live-sim-demo.md §4.1 (`DEMO_UNIT_BUDGET_HOUR`), §4.6.1 (which orders this tier
   * second, after the per-IP window), §4.5 (`budget_exhausted` is a 503 with a
   * `Retry-After`).
   *
   * THE ONE THING THIS BLOCK EXISTS TO PROVE, and it is not "the counter adds up".
   * `_lib/limits.js::sharedBudgetVerdict` carries the argument; the assertions are here.
   * The window sub-tier fails open because every write it makes is a `prev + 1`. The unit
   * budget has `slot.refundBudget()` underneath it, and a refund is a `prev - cost`: LOSE
   * ONE AND THE COUNTER IS TOO HIGH, which refuses a visitor who should have been served.
   * That is the direction §15's own notes say this tier may never fail in — it is why the
   * concurrency ceiling was refused a place here.
   *
   * So the shipped design has no refund write at all: units reach the colo only after a
   * request has been RELEASED WITHOUT A REFUND, held until then in this isolate's own
   * ledger (`__state().units`). Everything below is that property, from both sides:
   *
   *   1. the budget really is shared — 15i-b drives it from two isolate-like contexts and
   *      shows the second refused on the first's spend;
   *   2. every failure mode is an UNDERCOUNT — 15i-d, each one by name, against an entry
   *      a WORKING cache would have refused on;
   *   3. NO LOST WRITE CAN REFUSE A VISITOR WHO SHOULD BE SERVED — 15i-e, including the
   *      nastiest shape: a `put` that lands and then times out, which is what makes
   *      "retry the unpublished units" a double charge.
   */
  const UNITS_HOUR = 12;                       // 4 chat turns, so the arithmetic is readable
  const TWELVE = wire2.readConfig({ ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR) });
  const FAST12 = wire2.readConfig({
    ...FULL, DEMO_UNIT_BUDGET_HOUR: String(UNITS_HOUR), DEMO_CACHE_TIMEOUT_MS: "10",
  });
  /** Hour bucket 2 — `nowS` 7200 — and its entry, spelled out rather than derived so a
   *  wrong key shows up as a wrong STRING rather than as an assertion that quietly agrees
   *  with the code it is checking. */
  const HOUR2 = 7200;
  const UK = ORIGIN + "/__moxie/rl/units/2";
  /** The DAY entry (§4.6.3), spelled out for the same reason: `nowS` 7200 is day bucket 0,
   *  and the `d` mark is what stops a day key ever being read as an hour key — a decimal
   *  integer cannot begin with a letter, which is the whole separation inside the budget
   *  family now that both scales live in it. */
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
    // THE READ ORDER, PINNED AS A WHOLE RATHER THAN BY INDEX. Before §4.6.3 the budget
    // entry was `keys[1]`, and re-pinning it to `keys[2]` would have kept a passing
    // assertion while quietly dropping what the index was worth — that the per-IP window
    // is consulted BEFORE the deployment's budget (15i-h, and `unit_budget_mutation_check`
    // row U10). So the whole sequence is asserted: every per-IP window scale, narrowest
    // first, then every budget scale, narrowest first. Reordering ANY of it now reddens
    // here rather than silently answering a per-visitor condition with a 503.
    eq(c.log.keys.length, 4,
       "an admitted turn reads four entries: both window scales, then both budget scales (§4.6.3; it was 2)");
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
  //
  // `__reset()` between them is the isolate boundary, exactly as §15b uses it: a brand new
  // `Map` and a brand new LEDGER, the same colo cache. Four addresses rather than one, so
  // the per-IP window never gets a word in and the only thing that can refuse is the
  // budget.
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
  //
  // `sim/test_turnstile.mjs` §12's attack, aimed at the SHARED counter instead of the
  // isolate's: 200 requests that admission charges and the route body then refuses. Under
  // the design this file rejected — charge the colo at admission, refund only locally —
  // 200 x 3 units is exactly `DEMO_UNIT_BUDGET_HOUR`, and every isolate in the colo would
  // then read an exhausted hour for an attack that made no gateway call. Here the colo is
  // never told, because there is nothing to un-tell.
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

    // (3) A PUBLISH THAT LANDS AND THEN TIMES OUT IS NOT PUBLISHED TWICE.
    //     The failure that would make the obvious "keep the units and retry" design wrong:
    //     the write committed, the promise was lost to the deadline, and retrying the same
    //     units would charge the colo for them a second time — an OVERCOUNT, which refuses
    //     somebody. The ledger is therefore cleared on every ATTEMPT, confirmed or not.
    //     Driven at PRODUCTION's 600-unit ceiling rather than this block's 12, and that is
    //     not incidental: under the retry design the over-publish crosses 12 by the fourth
    //     turn, so the suite reddens on "isolate A turn 4 is admitted" and the assertion
    //     that NAMES the double charge is never the one that fails. A guard whose own check
    //     cannot be the failing one proves nothing (`turnstile_mutation_check.py`'s rule),
    //     and this row is why the ceiling here is out of the way.
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
  //
  // A visitor over their own MINUTE, in a colo whose HOUR is also spent. Both sub-tiers
  // would refuse and the order decides which answer they get, which is not cosmetic:
  // `rate_limited` is a 429 the browser paces itself against (§4.5) while
  // `budget_exhausted` is a 503 that paints the page SCRIPTED for everybody. Answering a
  // per-visitor condition with a deployment-wide verdict is the wrong information, and it
  // would also make the two tiers disagree about what the same request earns.
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

    // AN UNCAPPED HOUR MUST NOT SWITCH OFF THE DAY, and this assertion is why the code
    // says so. It read `…length, 0` — "no budget entry is ever asked for" — until §4.6.3,
    // and it was correct while the hour was the only budget scale on this tier. With a day
    // scale beside it, the same line would have passed only because the hour's early
    // return skipped the day too: one variable silently switching off a ceiling the
    // operator set with a different one. This pin is therefore SPLIT rather than moved.
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
