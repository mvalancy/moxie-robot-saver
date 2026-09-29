/* test_demo_proxy §15a–h: the Cache API tier's per-IP windows, shared across a colo's
 * isolates, against `fakeCache` (node has no `caches`; the fake is how it hangs, goes stale
 * or throws ON PURPOSE). Every error must be an undercount: off, absent, broken, slow or
 * lying, the visitor is ADMITTED. Not asserted: a global ceiling (it is per-colo, §4.6.1 f).
 * Run via the entry file. */
import {
  FULL, ORIGIN, P, call, chat, deep, eq, fresh, limits, ok, req, upstreamCalls, wire2,
  fakeCache, cacheStats, admitWith,
} from "./harness.mjs";

{
  const ON = wire2.readConfig(FULL);
  const OFF = wire2.readConfig({ ...FULL, DEMO_CACHE_COUNTER: "0" });
  const FAST = wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: "10" });   // clamp floor: a hang costs ms

  // 15a. THE SEAM: defaults, clamps, and no cache => admit() is the function it was before.
  deep([ON.cacheCounter, OFF.cacheCounter, ON.cacheTimeoutMs], [true, false, 250],
       "DEMO_CACHE_COUNTER defaults ON, =0 switches it off; the deadline defaults to 250 ms");
  for (const v of ["999999", "1"]) {
    eq(wire2.readConfig({ ...FULL, DEMO_CACHE_TIMEOUT_MS: v }).cacheTimeoutMs, 250,
       `DEMO_CACHE_TIMEOUT_MS=${v} falls back to the default (a 1 ms deadline would switch the tier off by stealth)`);
  }
  ok(!Object.keys(wire2.publicLimits(ON)).some((k) => /cache/i.test(k)), "the tier is server-side only: not published to the browser");

  eq(typeof caches, "undefined", "bare node HAS no caches global — the absent-cache path is the default one");
  for (const [label, run] of [
    ["NO cache key at all", () => limits.admit({ request: req("/api/chat", { text: "x" }), cfg: ON, route: "chat" })],
    ["an explicitly null store", () => admitWith(ON, null, "203.0.113.9")],
    ["DEMO_CACHE_COUNTER=0 with a cache present", () => admitWith(OFF, fakeCache(), "203.0.113.9")],
  ]) {
    fresh();
    const r = await run();
    eq(`${r.ok} ${cacheStats().checked} ${limits.__state().inflight.chat}`, "true 0 1",
       `${label}: admits exactly as before the tier, consulting nothing`);
    r.release();
  }
  {
    fresh();
    const c = fakeCache();
    (await admitWith(OFF, c, "203.0.113.9")).release();
    eq(c.log.match + c.log.put, 0, "DEMO_CACHE_COUNTER=0 makes ZERO cache calls — the switch is a seam, not a filter");
  }

  // 15b. IT ADDS A REFUSAL THE IN-ISOLATE MAP WOULD NOT MAKE. `fresh()` is the isolate
  // boundary (new Map, SAME cache): isolate B's map alone would allow the third turn.
  const IP = "198.51.100.7";
  {
    const shared = fakeCache();
    fresh();
    for (let i = 1; i <= 3; i++) {
      const a = await admitWith(ON, shared, IP);
      eq(a.ok, true, `isolate A turn ${i} is admitted`);
      a.release();
    }
    eq(shared.count(shared.log.keys[0]), 3, "the shared entry counts all three of isolate A's turns");
    fresh();
    for (let i = 1; i <= 2; i++) {
      const b = await admitWith(ON, shared, IP);
      eq(b.ok, true, `isolate B's turn ${i} is admitted — shared count ${3 + i} of 5`);
      b.release();
    }
    const budgetBefore = JSON.stringify(limits.__state().budget);
    const b3 = await admitWith(ON, shared, IP);
    eq(`${b3.ok} ${b3.reason} ${b3.rateLimit.remaining}`, "false rate_limited 0",
       "isolate B's THIRD is REFUSED — its own map has seen two, the colo five — as rate_limited with remaining 0");
    ok(b3.retryAfterS >= 1 && b3.retryAfterS <= 60, `…with a Retry-After inside the minute, got ${b3.retryAfterS}`);
    deep([upstreamCalls(), cacheStats().refused], [0, 1], "…calling nothing upstream, recorded as one tier refusal");
    // 15c. THE REFUSAL COSTS NOTHING.
    eq(limits.__state().inflight.chat, 0, "a tier refusal leaves NO concurrency slot held");
    eq(JSON.stringify(limits.__state().budget), budgetBefore, "…refunds the unit budget it charged");
    eq(shared.count(shared.log.keys[0]), 5, "…and does not count itself");

    // CONTROL: the same sequence with the tier off is admitted — so the refusal is the tier's.
    fresh();
    const control = fakeCache();
    for (let i = 1; i <= 3; i++) (await admitWith(ON, control, IP)).release();
    fresh();
    for (let i = 1; i <= 2; i++) (await admitWith(OFF, control, IP)).release();
    const off3 = await admitWith(OFF, control, IP);
    eq(off3.ok, true, "CONTROL: with the tier off, that same third turn is admitted — the map alone allows it");
    off3.release();
  }

  // 15d. THE LATENCY BUDGET, AS AN EXACT COUNT OF OPS (admit() is in every turn's path).
  // Four sub-tiers — per-IP minute; per-IP hour+day in ONE entry; the budget's hour; its day.
  {
    fresh();
    const c = fakeCache();
    (await admitWith(ON, c, "198.51.100.20")).release();
    eq(c.log.match, 4, "an admitted request reads FOUR shared entries: minute window, hour+day window, budget hour, budget day");
    eq(c.log.put, 2, "…and writes back BOTH windows and neither budget (a first admission owes nothing yet, §15i)");
    deep([cacheStats().ops, cacheStats().wrote, cacheStats().units.ops, cacheStats().units.wrote], [2, 1, 1, 0],
         "…recorded as window ops 2 (1 write) and budget ops 1 (0 writes): six ops, as §4.6.3 accepted");

    const c2 = fakeCache();
    fresh();
    (await admitWith(ON, c2, "198.51.100.22")).release();
    const before = c2.log.match + c2.log.put;
    (await admitWith(ON, c2, "198.51.100.23")).release();
    eq(c2.log.match + c2.log.put - before, 8,
       "a turn whose isolate owes units costs EIGHT ops: four reads, two window writes, two budget publishes");

    fresh();
    const learn = fakeCache();
    (await admitWith(ON, learn, "198.51.100.21")).release();
    fresh();
    const atLimit = fakeCache().seed(learn.log.keys[0], ON.chatPerMin);
    const refused = await admitWith(ON, atLimit, "198.51.100.21");
    deep([refused.ok, atLimit.log.match, atLimit.log.put, cacheStats().ops], [false, 1, 0, 1],
         "a tier refusal reads once and writes NOTHING: a refusal spends nothing, so it counts nothing");
  }

  // 15e. FAIL OPEN. Each case seeds the entry AT the limit, so a WORKING cache would refuse.
  {
    fresh();
    const learn = fakeCache();
    (await admitWith(FAST, learn, "198.51.100.30", "chat", 1000)).release();
    const KEY = learn.log.keys[0];

    const modes = [
      ["a cache MISS", () => fakeCache(), { miss: 1 }],
      ["a STALE entry, past its own max-age", () => fakeCache().seed(KEY, 99, 999, 60), { stale: 1 }],
      ["a match that THROWS SYNCHRONOUSLY", () => fakeCache({ matchThrowsSync: true }).seed(KEY, 99), { errors: 1 }],
      ["a match that REJECTS", () => fakeCache({ matchRejects: true }).seed(KEY, 99), { errors: 1 }],
      ["a match that HANGS FOR EVER", () => fakeCache({ matchHangs: true }).seed(KEY, 99), { timeouts: 1 }],
      ["an entry whose body is NOT JSON", () => fakeCache({ bodyOverride: "<html>nope" }).seed(KEY, 99), { miss: 1 }],
      ["an entry whose count is not a number", () => fakeCache({ bodyOverride: '{"n":"many"}' }).seed(KEY, 99), { miss: 1 }],
      ["a store with NO METHODS AT ALL", () => ({ log: { match: 0, put: 0, keys: [], puts: [] } }), { errors: 1 }],
      // THE OUTER SEATBELT: a throw OUTSIDE every cache op, caught only by sharedThenGrant's own try.
      ["a config that throws on read", () => fakeCache().seed(KEY, 99), { errors: 1 },
       new Proxy(FAST, { get(t, k) { if (k === "cacheTimeoutMs") throw new Error("boom"); return t[k]; } })],
    ];
    for (const [label, make, want, cfg] of modes) {
      fresh();
      const r = await admitWith(cfg || FAST, make(), "198.51.100.30", "chat", 1000);
      eq(r.ok, true, `FAIL OPEN: ${label} must still ADMIT a visitor the working cache would have refused`);
      eq(`${r.reason} ${cacheStats().refused}`, "null 0", `FAIL OPEN: ${label} carries no refusal reason and records none`);
      for (const [k, v] of Object.entries(want)) eq(cacheStats()[k], v, `…and ${label} is recorded as ${k}`);
      if (r.ok) r.release();
      eq(limits.__state().inflight.chat, 0, `…and ${label} leaks no concurrency slot`);
    }
    // The write half: a failed WRITE neither refuses nor throws.
    for (const [label, opts, want] of [
      ["a put that THROWS SYNCHRONOUSLY", { putThrowsSync: true }, { errors: 1 }],
      ["a put that REJECTS", { putRejects: true }, { errors: 1 }],
      ["a put that HANGS FOR EVER", { putHangs: true }, { timeouts: 1 }],
    ]) {
      fresh();
      const r = await admitWith(FAST, fakeCache(opts), "198.51.100.31", "chat", 1000);
      eq(r.ok, true, `FAIL OPEN: ${label} must not refuse the visitor whose turn it was writing`);
      deep([cacheStats().wrote, cacheStats().ops], [0, 1], `…${label} stored nothing, completing only the read`);
      for (const [k, v] of Object.entries(want)) eq(cacheStats()[k], v, `…and ${label} is recorded as ${k}`);
      r.release();
    }
    // A failed READ must not then write `1` over a live count (resetting the colo's window).
    fresh();
    const broken = fakeCache({ matchRejects: true }).seed(KEY, 4);
    const r = await admitWith(FAST, broken, "198.51.100.30", "chat", 1000);
    deep([r.ok, broken.log.put, broken.count(KEY)], [true, 0, 4], "a failed READ admits and writes nothing: a live 4 is not reset to 1");
    r.release();
  }

  // 15f. THE FREE REFUSALS STAY FREE: the in-isolate map and the origin pin decide before
  // any network round trip.
  {
    fresh();
    const c = fakeCache();
    for (let i = 1; i <= ON.chatPerMin; i++) (await admitWith(ON, c, "198.51.100.40", "chat", 2000)).release();
    const sixth = await admitWith(ON, c, "198.51.100.40", "chat", 2000);
    eq(`${sixth.ok} ${sixth.reason}`, "false rate_limited", "the 6th turn in a minute is refused by the in-isolate map");
    eq(c.log.match, ON.chatPerMin * 4, "…and the tier was consulted for 5 turns, not 6: a free refusal never pays a round trip");
    fresh();
    const c2 = fakeCache();
    const hot = await limits.admit({
      request: req("/api/chat", { text: "x" }, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }),
      cfg: ON, route: "chat", cache: c2 });
    eq(`${hot.reason} ${c2.log.match + c2.log.put}`, "forbidden_origin 0", "a forbidden origin is refused without touching the cache");
  }

  // 15g. THE KEY: no address in it, keyed per visitor, and it rotates every minute.
  {
    const keyOf = async (ip, nowS) => {
      fresh();
      const c = fakeCache();
      (await admitWith(ON, c, ip, "chat", nowS)).release();
      return { key: c.log.keys[0] || "", c };
    };
    const { key, c } = await keyOf("203.0.113.99", 3000);
    ok(key.startsWith(ORIGIN + "/__moxie/rl/chat/") && !key.includes("203.0.113.99"),
       `the entry lives on our OWN origin under a non-route prefix, with no ADDRESS in it — got ${key}`);
    ok(/^[0-9a-f]{24}$/.test(key.slice((ORIGIN + "/__moxie/rl/chat/").length).split("/")[0]), "…only a 96-bit keyed tag of it");
    deep(JSON.parse((c.store.get(key) || { body: "null" }).body), { n: 1 }, "the stored entry is a bare count: nothing an outsider could learn from");
    eq((await keyOf("203.0.113.99", 3000)).key, key, "the same visitor in the same minute keys the same entry");
    ok((await keyOf("203.0.113.98", 3000)).key !== key, "a DIFFERENT visitor keys a different entry");
    ok((await keyOf("203.0.113.99", 3060)).key !== key, "…and the next MINUTE keys a different entry, so the hot key rotates");
    ok(!(await keyOf("2001:db8:1:2:3:4:5:6", 3000)).key.includes("2001"), "an IPv6 /64 is not in the key either");
  }

  // 15h. THE WHOLE ROUTE through the global `caches.default` (production's branch).
  {
    fresh();
    const c = fakeCache();
    globalThis.caches = { default: c };
    try {
      P.plan = { chat: { content: "hi" } };
      const first = await call(chat, "/api/chat", { text: "hello" });
      eq(`${first.res.status} ${c.log.match}`, "200 4", "a served turn consults the global store for all FOUR sub-tiers");
      // Stand the shared count at the ceiling; reset the in-isolate map so ONLY the tier can refuse.
      fresh();
      c.store.set(c.log.keys[0], { body: JSON.stringify({ n: ON.chatPerMin }), maxAge: 60 });
      const refused = await call(chat, "/api/chat", { text: "hello" });
      eq(`${refused.res.status} ${refused.body.reason} ${upstreamCalls()}`, "429 rate_limited 0",
         "/api/chat answers 429 when the colo's shared window is spent, spending nothing");
      ok(Number(refused.res.headers.get("Retry-After")) >= 1, "…with a Retry-After the client can obey");
    } finally {
      delete globalThis.caches;
    }
  }
}
