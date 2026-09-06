/* helpers_shared_ceilings.mjs — the per-IP HOUR and DAY windows, and the unit budget's
 * DAY ceiling, on the shared Cache API tier of `functions/api/_lib/limits.js`.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.6.1 (the measurement that cleared
 * this tier to exist, and the latency objection this slice answers), §4.6.2 (the refund
 * argument the budget half must honour), §4.6.3 (this slice), §4.1 (every ceiling and its
 * starting number), §4.5 (the status and `Retry-After` table).
 *
 * ============================================================================
 * WHY THIS FILE IS NOT A SECTION OF `sim/test_demo_proxy.mjs`, WHICH IS WHERE THE REST OF
 * THIS TIER'S PROOF LIVES (§15 and §15i).
 *
 * That file was RESERVED to another agent for the whole of this slice, so this one exists
 * rather than a diff to it. Being a separate file has one real cost and it is stated here
 * rather than discovered later: §15's fixtures (`fakeCache`, `fresh`, `admitWith`) are
 * duplicated below instead of shared. The duplication is deliberate and the shapes are
 * copied faithfully — a fake whose failure switches differ from §15's would be a fake that
 * proves something about ITSELF rather than about the tier.
 *
 * It is run by `sim/tests/test_shared_ceilings.py`, i.e. by `pytest sim/tests`, which is
 * the one family `test_ci_test_coverage.py` records as never having gone silently unrun.
 * A new `sim/test_*.mjs` would have needed a step in `sim/ci/ci.yml`, and that file was
 * reserved too.
 * ============================================================================
 *
 * WHAT IT PROVES, and each one is a claim somebody could otherwise only assert:
 *
 *   A. THE FALLBACK. With no store — no `caches.default`, an explicit `null`, or
 *      `DEMO_CACHE_COUNTER=0` — `admit()` is the function it was before this tier existed,
 *      and that now covers FOUR sub-tiers rather than two.
 *   B/C. THE PER-IP HOUR AND DAY REALLY BIND ACROSS ISOLATES. Two isolates, one injected
 *      store, and a visitor refused on a count neither isolate's own `Map` has seen —
 *      with the same sequence admitted when the tier is off, which is what makes the
 *      assertion about the TIER and not about the arithmetic.
 *   D. THE UNIT BUDGET'S DAY, the same way, and by charge-on-completion: the colo is never
 *      told about a charge that might have to be given back.
 *   E. A REFUSED REQUEST PUBLISHES NOTHING to the day, structurally.
 *   F/G. EVERY FAILURE MODE ADMITS. A store that hangs, throws synchronously, rejects,
 *      serves a stale entry, serves an unparseable body, or serves a body stamped with
 *      another bucket — each one against an entry a WORKING store would have refused on.
 *   H. THE KEYS. No address, no route in the budget key, and a one-character mark that no
 *      decimal integer can spell, so a wide entry can never be read as a narrow one.
 *   I. WHAT IT COSTS, as a count of round trips rather than as an intention.
 *   J. THE LEDGER. What this isolate owes today is a recorded fact; a day boundary DROPS
 *      it rather than moving it; and a `put` that lands and then hangs is not retried.
 *
 * NO WALL CLOCK. Every admission is given an explicit `nowS`, so every bucket in this file
 * is a constant and the result is the same at all 1440 minutes of a day.
 *
 *   node sim/tests/helpers_shared_ceilings.mjs          # human-readable
 *   node sim/tests/helpers_shared_ceilings.mjs --json   # one JSON object, for pytest
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");

const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));
const env = await import(join(repo, "functions", "api", "_lib", "env.js"));

/* --------------------------------------------------------------------------- *
 * Harness — §15's, copied faithfully. See the header for why it is copied.
 * --------------------------------------------------------------------------- */
const fails = [];
/** Section -> {checks, failures}. The pytest wrapper asserts per section, so a whole
 *  section quietly vanishing is a failure rather than a smaller green number. */
const sections = {};
let current = "?";
let checks = 0;

const ok = (c, m) => {
  checks += 1;
  sections[current].checks += 1;
  if (!c) {
    fails.push(`[${current}] ${m}`);
    sections[current].failures += 1;
  }
};
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);
const section = (name) => {
  current = name;
  if (!sections[name]) sections[name] = { checks: 0, failures: 0 };
};

/** The fake deployment. `.invalid.test` is RFC 6761 reserved and unresolvable, and the key
 *  is shaped so the repo's own secret grep cannot mistake it for a real one. */
const BASE = "https://gw.invalid.test/v1";
const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
const ORIGIN = "https://demo.invalid.test";
const BASE_ENV = {
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
};
const cfgOf = (extra) => env.readConfig({ ...BASE_ENV, ...(extra || {}) });

function req(path, headers) {
  return new Request(ORIGIN + path, {
    method: "POST",
    headers: Object.assign(
      {
        "Content-Type": "application/json",
        Origin: ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "CF-Connecting-IP": "203.0.113.9",
      },
      headers || {},
    ),
    body: JSON.stringify({ text: "x" }),
  });
}

/** `__reset()` is the ISOLATE BOUNDARY: a brand new `Map` and brand new ledgers, with
 *  whatever store the caller keeps holding. Two calls around one fake cache are two
 *  isolates in one colo, which is the only way to see this tier do its job. */
const fresh = () => limits.__reset();

/**
 * A fake `caches.default` — `match`/`put` only, plus a log of what it was actually asked,
 * so every assertion is on a RECORDED fact rather than on an inference from behaviour.
 *
 * The failure switches are the same three SHAPES §15 uses, on purpose: a synchronous throw
 * (before any promise exists), a rejected promise, and a promise that never settles. The
 * first is the one a naive `try { await x() }` still catches and a naive
 * `Promise.resolve(x()).catch()` does not.
 */
function fakeCache(opts) {
  const o = opts || {};
  const store = new Map();
  const log = { match: 0, put: 0, keys: [], puts: [] };
  const hang = () => new Promise(() => {});
  return {
    log,
    store,
    /** Pre-load an entry as if another isolate had written it. `ageS` past `maxAge` makes
     *  it the stale entry a real cache would never serve. `body` writes an arbitrary
     *  object, which is how the wide entry's multi-field shape is seeded. */
    seed(key, body, ageS, maxAge) {
      store.set(String(key), {
        body: typeof body === "string" ? body : JSON.stringify(body),
        maxAge: maxAge === undefined ? 86400 : maxAge,
        ageS,
      });
      return this;
    },
    body(key) {
      const e = store.get(String(key));
      if (!e) return null;
      try { return JSON.parse(e.body); } catch { return null; }
    },
    count(key) {
      const b = this.body(key);
      return b ? b.n : null;
    },
    match(key) {
      log.match += 1;
      log.keys.push(String(key));
      if (o.only && String(key).indexOf(o.only) < 0) {
        // A switch aimed at ONE sub-tier's entry: everything else behaves normally, so a
        // "the day budget fails open" assertion cannot be satisfied by the window half
        // having failed open instead.
        const e = store.get(String(key));
        if (!e) return Promise.resolve(undefined);
        return Promise.resolve(new Response(e.body, {
          headers: { "Content-Type": "application/json", "Cache-Control": "max-age=" + e.maxAge },
        }));
      }
      if (o.matchThrowsSync) throw new Error("match threw synchronously");
      if (o.matchHangs) return hang();
      if (o.matchRejects) return Promise.reject(new Error("match rejected"));
      const e = store.get(String(key));
      if (!e) return Promise.resolve(undefined);
      const h = { "Content-Type": "application/json", "Cache-Control": "max-age=" + e.maxAge };
      if (e.ageS !== undefined) h.Age = String(e.ageS);
      return Promise.resolve(new Response(o.bodyOverride === undefined ? e.body : o.bodyOverride, { headers: h }));
    },
    put(key, res) {
      log.put += 1;
      log.puts.push(String(key));
      if (o.only && String(key).indexOf(o.only) < 0) return Promise.resolve();
      if (o.putThrowsSync) throw new Error("put threw synchronously");
      if (o.putHangs) return hang();
      if (o.putRejects) return Promise.reject(new Error("put rejected"));
      const write = (async () => {
        const body = await res.text();
        const cc = /max-age=(\d+)/.exec(res.headers.get("Cache-Control") || "");
        store.set(String(key), { body, maxAge: cc ? Number(cc[1]) : 0 });
      })();
      // THE WRITE THAT LANDS AND THEN NEVER TELLS YOU — what makes "retry the unpublished
      // units" a double charge, which is an overcount, which refuses somebody.
      if (o.putStoresThenHangs) return write.then(() => hang());
      return write;
    },
  };
}

/** One admission, straight at `admit()`. `cache: null` is "there is no cache here" and is
 *  NOT the same as omitting the key. `nowS` is always explicit — see the header. */
const admitWith = (cfg, cache, ip, nowS, route) =>
  limits.admit({
    request: req("/api/" + (route || "chat"), { "CF-Connecting-IP": ip }),
    cfg,
    route: route || "chat",
    cache,
    nowS,
  });

const st = () => limits.__state();
const wide = () => st().stats.cache.wide;
const unitsDay = () => st().stats.cache.unitsDay;

/* Buckets, spelled out rather than derived, so a wrong bucket shows up as a wrong NUMBER
 * rather than as an assertion that quietly agrees with the code it is checking.
 *   T0 = 7200  -> minute 120, hour 2, day 0
 *   T1 = 93600 -> minute 1560, hour 26, day 1  (the next DAY, for the roll tests)      */
const T0 = 7200;
const T1 = 93600;
const DAY0 = 0;
const DAY1 = 1;

/* =========================================================================== *
 * A. THE SEAM — with no store, `admit()` is the function it was before the tier
 * =========================================================================== */
section("A");
{
  // Every ceiling set to exactly ONE turn: one chat per hour, one per day, and a day
  // budget of 3 units, which `UNITS.chat` says is one turn. A working tier therefore
  // refuses the SECOND of everything, and the first is admitted by all of them — which is
  // what makes the assertions below about the absent store rather than about a ceiling
  // that never binds. (`DEMO_UNIT_BUDGET_DAY: "1"` would be refused by the isolate's own
  // map before any cache was consulted, and would have proven nothing.)
  const CEILINGS = {
    DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1", DEMO_UNIT_BUDGET_DAY: "3",
  };
  const ON = cfgOf(CEILINGS);
  const OFF = cfgOf({ ...CEILINGS, DEMO_CACHE_COUNTER: "0" });

  eq(typeof caches, "undefined",
     "bare node HAS no caches global — which is why the absent-store path below is the default one");

  // The ceilings above are all 1, so a WORKING tier would refuse the second of everything.
  // Nothing here may refuse, because nothing here has a tier.
  {
    fresh();
    const r = await limits.admit({ request: req("/api/chat"), cfg: ON, route: "chat", nowS: T0 });
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
  // The teeth for all three: the SAME configuration with a store present really does
  // refuse, so "no store means no refusal" is a statement about the store.
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

/* =========================================================================== *
 * B. THE PER-IP HOUR BINDS ACROSS ISOLATES
 * =========================================================================== */
section("B");
{
  // A minute cap high enough that it can never be the thing that refuses, so the only
  // ceiling with a word to say is the hour.
  const ON = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "3", DEMO_CHAT_PER_DAY: "1000" });
  const OFF = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "3", DEMO_CHAT_PER_DAY: "1000",
    DEMO_CACHE_COUNTER: "0",
  });
  const IP = "198.51.100.7";
  const shared = fakeCache();

  fresh();
  for (let i = 1; i <= 2; i++) {
    const r = await admitWith(ON, shared, IP, T0);
    eq(r.ok, true, `isolate A turn ${i} is admitted — the hour allows three`);
    r.release();
  }

  fresh(); // ---- a different isolate: a fresh Map, the same colo cache.
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
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 3600,
     `…with a Retry-After inside the hour, got ${b2.retryAfterS}`);
  eq(b2.rateLimit.remaining, 0, "…and remaining: 0, so the browser paces itself the same way");
  eq(st().stats.upstreamCalls, 0, "…having called nothing upstream");
  eq(wide().refused, 1, "…recorded as one refusal by the HOUR/DAY sub-tier specifically");
  eq(st().stats.cache.refused, 0, "…and none by the minute sub-tier, which had nothing to say");

  // The refusal costs the visitor nothing: the slot goes back and the charge is refunded.
  eq(st().inflight.chat, 0, "a wide-window refusal leaves NO concurrency slot held");
  eq(JSON.stringify(st().budget), budgetBefore,
     "…and refunds the unit budget it charged, exactly as the minute sub-tier's refusal does");

  // The control that makes all of the above a statement about the TIER.
  fresh();
  (await admitWith(OFF, shared, IP, T0)).release();
  const off2 = await admitWith(OFF, shared, IP, T0);
  eq(off2.ok, true,
     "CONTROL: with the tier off, that same turn is admitted — the isolate's own map alone allows it");
  off2.release();
}

/* =========================================================================== *
 * C. THE PER-IP DAY BINDS ACROSS ISOLATES
 * =========================================================================== */
section("C");
{
  const ON = cfgOf({ DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "3" });
  const OFF = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "3",
    DEMO_CACHE_COUNTER: "0",
  });
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
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 86400,
     `…with a Retry-After inside the day, got ${b2.retryAfterS}`);
  ok(b2.retryAfterS > 3600,
     `…and a LONGER one than the hour's, because it is the day's window that has closed, got ${b2.retryAfterS}`);

  // The next DAY is a different bucket, and the body's stamp is what says so — the KEY
  // rotates daily too, so this asserts both halves at once.
  fresh();
  const b3 = await admitWith(ON, shared, IP, T1);
  eq(b3.ok, true, "…and tomorrow the same visitor is admitted again: the day's count is stamped, not eternal");
  b3.release();

  fresh();
  (await admitWith(OFF, shared, IP, T0)).release();
  const off2 = await admitWith(OFF, shared, IP, T0);
  eq(off2.ok, true, "CONTROL: with the tier off, that same turn is admitted");
  off2.release();
}

/* =========================================================================== *
 * D. THE UNIT BUDGET'S DAY BINDS ACROSS ISOLATES — by charge-on-completion
 * =========================================================================== */
section("D");
{
  /** 12 units is exactly four chat turns, so the arithmetic is readable. The HOUR is left
   *  wide open, so the only budget ceiling with anything to say is the day. */
  const DAY_UNITS = 12;
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: String(DAY_UNITS),
  });
  const OFF = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: String(DAY_UNITS),
    DEMO_CACHE_COUNTER: "0",
  });
  const shared = fakeCache();
  const DK = ORIGIN + "/__moxie/rl/units/d0";

  // Four different addresses, so the per-IP window never gets a word in.
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
  const entry = shared.store.get(DK) || {};
  eq(entry.maxAge, 86400,
     "the day entry's max-age is ONE DAY — its own window — so an entry cannot outlive it and be " +
     "read as today's");
  deep(shared.body(DK), { n: 9 },
       "…and the stored body is a bare count: the entry sits under a URL an outsider could ask for");

  fresh(); // ---- a different isolate: fresh Map, fresh ledgers, SAME colo cache.
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
  ok(b2.retryAfterS >= 1 && b2.retryAfterS <= 86400,
     `…with a Retry-After inside the day, got ${b2.retryAfterS}`);
  eq(st().stats.upstreamCalls, 0, "…having called nothing upstream");
  eq(unitsDay().refused, 1, "…recorded as one DAY-budget refusal");
  eq(st().stats.cache.units.refused, 0, "…and none by the hour, which is nowhere near its ceiling");

  eq(st().inflight.chat, 0, "a day-budget refusal leaves NO concurrency slot held");
  eq(JSON.stringify(st().budget), budgetBefore, "…and refunds the in-isolate units it charged");
  eq(shared.count(DK), 9, "…and PUBLISHES NOTHING: a request that was refused spent nothing to report");
  eq(unitsDay().published, 0, "…recorded as zero units published by this isolate");

  fresh();
  (await admitWith(OFF, shared, "198.51.100.21", T0)).release();
  const off2 = await admitWith(OFF, shared, "198.51.100.22", T0);
  eq(off2.ok, true, "CONTROL: with the tier off, that same turn is admitted — the map alone allows it");
  off2.release();
}

/* =========================================================================== *
 * E. THE FREE DRAIN, CLOSED STRUCTURALLY — a refunded request publishes NOTHING
 * =========================================================================== */
section("E");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "1000", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
  });
  const shared = fakeCache();
  const DK = ORIGIN + "/__moxie/rl/units/d0";
  fresh();
  // 200 admissions whose route body then refuses — `sim/test_turnstile.mjs` §12's attack,
  // aimed at the shared DAY. Every one refunds, so none of them ever settles.
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
}

/* =========================================================================== *
 * F. THE WIDE WINDOW FAILS OPEN — every failure mode, by name
 * =========================================================================== */
section("F");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1000",
    DEMO_CACHE_TIMEOUT_MS: "10",
  });
  const IP = "203.0.113.60";

  /** The entry a WORKING store would have refused on: this visitor has already used their
   *  whole hour. Every case below is measured against exactly this. */
  const seedWide = (c, key) => c.seed(key, { h: 1, hb: 2, d: 1, db: 0 });

  // First, the control: a working store WITH that entry really does refuse.
  let realKey = "";
  {
    fresh();
    const probe = fakeCache();
    (await admitWith(ON, probe, IP, T0)).release();
    realKey = probe.log.keys.find((k) => k.indexOf("/w0") >= 0) || "no-key";
    fresh();
    const working = seedWide(fakeCache(), realKey);
    const r = await admitWith(ON, working, IP, T0);
    eq(r.ok, false, "CONTROL: a WORKING store holding a spent hour refuses — every case below is measured on this");
    eq(r.reason, "rate_limited", "…as rate_limited");
  }

  // `only` aims each switch at the WIDE entry alone. Without it the MINUTE window's read
  // would break first, and a narrower read that fails open SKIPS the wider scales
  // deliberately (see below), so the admission would prove nothing about this sub-tier.
  const AT_WIDE = "/w" + DAY0;
  const cases = [
    ["a match that HANGS FOR EVER", { matchHangs: true, only: AT_WIDE }, { timeouts: 1 }],
    ["a match that REJECTS", { matchRejects: true, only: AT_WIDE }, { errors: 1 }],
    ["a match that throws SYNCHRONOUSLY", { matchThrowsSync: true, only: AT_WIDE }, { errors: 1 }],
    ["a body that is not JSON at all", { bodyOverride: "<html>a proxy error page</html>", only: AT_WIDE }, { miss: 1 }],
    ["a body that is JSON but not an object", { bodyOverride: "42", only: AT_WIDE }, { miss: 1 }],
  ];
  for (const [label, opts, expect] of cases) {
    fresh();
    const c = seedWide(fakeCache(opts), realKey);
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, `WIDE WINDOW FAILS OPEN: ${label} still ADMITS`);
    eq(wide().refused, 0, `…and ${label} records no wide-window refusal`);
    for (const [k, v] of Object.entries(expect)) {
      eq(wide()[k], v, `…and ${label} is recorded as wide.${k}`);
    }
    if (r.ok) r.release();
  }

  // A STALE entry — served past its own max-age. A real cache would not; the fake does, and
  // the required answer is "treat it as absent", which admits.
  {
    fresh();
    const c = fakeCache().seed(realKey, { h: 1, hb: 2, d: 1, db: 0 }, 90000, 86400);
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, "WIDE WINDOW FAILS OPEN: an entry served past its own max-age still ADMITS");
    eq(wide().stale, 1, "…recorded as stale rather than believed");
    if (r.ok) r.release();
  }

  // A NARROWER READ THAT FAILS OPEN SKIPS THE WIDER SCALES, and that is a decision rather
  // than an oversight: a store that just timed out on one entry will almost certainly time
  // out on the next, so consulting it again buys nothing and costs another whole deadline
  // in the request path. Skipping can only ADMIT, which is the direction this tier is
  // allowed to be wrong in. Asserted here so nobody has to infer it from an op count.
  {
    fresh();
    const c = seedWide(fakeCache({ matchHangs: true }), realKey);
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, "a store that hangs on the MINUTE entry admits");
    eq(wide().checked, 0, "…and the wider scales are not consulted at all when the narrower read failed open");
    if (r.ok) r.release();
  }

  // A body stamped with ANOTHER bucket. This is the one the minute sub-tier gets for free
  // from its key and this sub-tier has to do in the value, so it is asserted directly.
  {
    fresh();
    const c = fakeCache().seed(realKey, { h: 99, hb: 1, d: 99, db: 7 });
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true,
       "WIDE WINDOW FAILS OPEN: a count stamped with a DIFFERENT bucket reads as zero, not as this hour's");
    eq(wide().hit, 1, "…the entry was read (so this is the stamp talking, not a miss)");
    eq(wide().refused, 0, "…and refused nobody");
    if (r.ok) r.release();
  }

  // A write that fails. The count is not incremented, which is an undercount, which admits
  // the NEXT request too — the direction this tier is allowed to be wrong in.
  for (const [label, opts] of [
    ["a put that HANGS", { putHangs: true, only: AT_WIDE }],
    ["a put that REJECTS", { putRejects: true, only: AT_WIDE }],
    ["a put that throws SYNCHRONOUSLY", { putThrowsSync: true, only: AT_WIDE }],
  ]) {
    fresh();
    const c = fakeCache(opts);
    (await admitWith(ON, c, IP, T0)).release();
    fresh();
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, `WIDE WINDOW FAILS OPEN: after ${label}, the next request is ADMITTED, never refused`);
    if (r.ok) r.release();
  }
}

/* =========================================================================== *
 * G. THE DAY BUDGET FAILS OPEN — every failure mode, by name
 * =========================================================================== */
section("G");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "1000", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
    DEMO_CACHE_TIMEOUT_MS: "10",
  });
  const DK = ORIGIN + "/__moxie/rl/units/d0";
  const IP = "203.0.113.61";

  {
    fresh();
    const working = fakeCache().seed(DK, { n: 12 });
    const r = await admitWith(ON, working, IP, T0);
    eq(r.ok, false, "CONTROL: a WORKING store holding a spent day refuses — every case below is measured on this");
    eq(r.reason, "budget_exhausted", "…as budget_exhausted");
  }

  // `only` aims each switch at the DAY BUDGET's entry alone, so an admission here cannot be
  // explained by some other sub-tier having fallen open first.
  const cases = [
    ["a match that HANGS FOR EVER", { matchHangs: true, only: "/units/d" }, { timeouts: 1 }],
    ["a match that REJECTS", { matchRejects: true, only: "/units/d" }, { errors: 1 }],
    ["a match that throws SYNCHRONOUSLY", { matchThrowsSync: true, only: "/units/d" }, { errors: 1 }],
  ];
  for (const [label, opts, expect] of cases) {
    fresh();
    const c = fakeCache(opts).seed(DK, { n: 12 });
    const r = await admitWith(ON, c, IP, T0);
    eq(r.ok, true, `DAY BUDGET FAILS OPEN: ${label} still ADMITS`);
    eq(unitsDay().refused, 0, `…and ${label} records no day-budget refusal`);
    for (const [k, v] of Object.entries(expect)) {
      eq(unitsDay()[k], v, `…and ${label} is recorded as unitsDay.${k}`);
    }
    if (r.ok) r.release();
  }
  {
    fresh();
    const c = fakeCache().seed(DK, { n: 12 }, 90000, 86400);
    const r = await admitWith(ON, c, IP, T0);
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

  // THE NASTIEST SHAPE: a `put` that lands and then never answers. Keeping the units to
  // retry them would publish them TWICE, which is an OVERCOUNT, which refuses somebody.
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
  }

  // A DAY boundary DROPS the ledger rather than moving it: spend recorded against a day
  // that did not spend it would refuse somebody tomorrow.
  {
    fresh();
    const c = fakeCache();
    (await admitWith(ON, c, IP, T0)).release();
    (await admitWith(ON, c, IP, T0)).release();
    deep(st().unitsDay, { pending: 3, bucket: DAY0 }, "3 units owed for day 0");
    const roll = await admitWith(ON, c, IP, T1);          // the next day
    roll.release();
    eq(c.count(ORIGIN + "/__moxie/rl/units/d1"), null,
       "day 0's unpublished units are never carried into day 1's entry");
    eq(unitsDay().dropped, 3, "…and the drop is recorded rather than left silent");
  }
}

/* =========================================================================== *
 * H. THE KEYS
 * =========================================================================== */
section("H");
{
  const ON = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_DAY: "100000",
  });
  const shapes = limits.__keyShapes();
  eq(shapes.prefix, "/__moxie/rl/", "every sub-tier lives under one prefix a reader can grep for");
  eq(shapes.windowArity, 3, "the window family's arity is unchanged by this slice");
  eq(shapes.unitsArity, 2, "…and so is the budget family's, so `unitsKeyUrl`'s collision argument still stands");
  ok(/^[a-z]$/.test(shapes.wideMark) && /^[a-z]$/.test(shapes.dayMark),
     "the two wide shapes are marked by a LETTER");
  ok(!/^[0-9]/.test(shapes.wideMark) && !/^[0-9]/.test(shapes.dayMark),
     "…and a decimal integer cannot begin with a letter, which is the whole separation " +
     "between a narrow key and a wide one INSIDE a family");

  fresh();
  const c = fakeCache();
  (await admitWith(ON, c, "203.0.113.99", T0)).release();
  const wideKey = c.log.keys.find((k) => k.indexOf("/" + shapes.wideMark + DAY0) >= 0) || "";
  const dayKey = c.log.keys.find((k) => k.indexOf("/units/" + shapes.dayMark) >= 0) || "";

  ok(wideKey.startsWith(ORIGIN + "/__moxie/rl/chat/"),
     `the wide entry lives on our OWN origin, under a non-route prefix — got ${wideKey}`);
  ok(!wideKey.includes("203.0.113.99"), "the visitor's ADDRESS is never in the wide key");
  const tag = wideKey.slice((ORIGIN + "/__moxie/rl/chat/").length).split("/")[0];
  ok(new RegExp("^[0-9a-f]{" + shapes.tagHex + "}$").test(tag),
     `…only a keyed tag of it, got ${JSON.stringify(tag)}`);
  eq(wideKey.slice((ORIGIN + "/__moxie/rl/").length).split("/").length, shapes.windowArity,
     "…and it has the window family's arity, so nothing about the budget family changed");

  eq(dayKey, ORIGIN + "/__moxie/rl/units/d0", "the day budget entry is origin + prefix + 'units' + d + the DAY bucket");
  ok(!dayKey.includes("203.0.113.99"), "…with no visitor's address in it");
  ok(!/(chat|speech|transcribe)/.test(dayKey.slice((ORIGIN + "/__moxie/rl/").length)),
     "…and no route either: the 3-vs-2 unit difference rides in the increment, not the key");

  // The same visitor in the same day keys the same wide entry; the next day does not.
  fresh();
  const c2 = fakeCache();
  (await admitWith(ON, c2, "203.0.113.99", T0 + 3600)).release();
  eq(c2.log.keys.find((k) => k.indexOf("/w") >= 0) || "", wideKey,
     "an hour later is the SAME wide entry — one entry per day is the point of it");
  fresh();
  const c3 = fakeCache();
  (await admitWith(ON, c3, "203.0.113.99", T1)).release();
  ok((c3.log.keys.find((k) => k.indexOf("/w") >= 0) || "") !== wideKey,
     "…and the next DAY keys a different entry, so the widest bucket still rotates the key");
  fresh();
  const c4 = fakeCache();
  (await admitWith(ON, c4, "203.0.113.98", T0)).release();
  ok((c4.log.keys.find((k) => k.indexOf("/w") >= 0) || "") !== wideKey,
     "a DIFFERENT visitor keys a different wide entry");
  fresh();
  const c5 = fakeCache();
  (await admitWith(ON, c5, "2001:db8:1:2:3:4:5:6", T0)).release();
  ok(!String(c5.log.keys.find((k) => k.indexOf("/w") >= 0) || "").includes("2001"),
     "an IPv6 /64 is not in the wide key either");

  // The wide entry's body carries a bucket beside every count and nothing else.
  fresh();
  const c6 = fakeCache();
  (await admitWith(ON, c6, "203.0.113.97", T0)).release();
  const stored = c6.body(c6.log.keys.find((k) => k.indexOf("/w") >= 0));
  deep(stored, { h: 1, hb: 2, d: 1, db: 0 },
       "the wide entry is two counts and the bucket each belongs to: no address, no route history, nothing");
}

/* =========================================================================== *
 * I. WHAT IT COSTS, AS A COUNT OF ROUND TRIPS
 * =========================================================================== */
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

  // A refusal spends no writes, at whichever scale refuses.
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

  // A refusal by a NARROWER scale never pays for the wider one's round trip.
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

/* =========================================================================== *
 * J. THE UNCAPPED DEPLOYMENT — a ceiling of 0 costs nothing at all
 * =========================================================================== */
section("J");
{
  fresh();
  const c = fakeCache();
  const NO_DAY = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_DAY: "0",
  });
  (await admitWith(NO_DAY, c, "198.51.100.40", T0)).release();
  eq(unitsDay().checked, 0, "with no DAY ceiling there is nothing to mirror, so that sub-tier never runs");
  ok(!c.log.keys.some((k) => k.indexOf("/units/d") >= 0), "…and its entry is never asked for");

  // `speech` has an hour cap and NO day cap (`windowLimits`), so the wide sub-tier runs
  // with one scale rather than two — the body must then carry one scale and not two.
  fresh();
  const c2 = fakeCache();
  const cfg2 = cfgOf({ DEMO_SPEECH_PER_MIN: "60", DEMO_SPEECH_PER_HOUR: "1000" });
  (await admitWith(cfg2, c2, "198.51.100.41", T0, "speech")).release();
  const k = c2.log.keys.find((x) => x.indexOf("/speech/") >= 0 && x.indexOf("/w") >= 0);
  deep(c2.body(k), { h: 1, hb: 2 },
       "a route with an hour cap and no day cap stores the hour and nothing else");
}

/* =========================================================================== *
 * K. WHICH DIRECTION EACH REFUSAL ERRS IN — the overcount, bounded and named
 * =========================================================================== *
 *
 * The tier's licence to exist is the sentence "every error is an undercount, so it can
 * only ever admit somebody it might have refused". A refusal that leaves a counter
 * INCREMENTED breaks that sentence, so both cases are measured here rather than reasoned
 * about: the one this slice removed, and the one it inherits and must not be read as
 * having removed.
 */
section("K");
{
  // (1) A WIDE-WINDOW refusal writes NOTHING, anywhere. This is what moving the wide check
  //     ahead of the minute's write bought: without it the minute entry would carry a
  //     count for a turn the hour refused.
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

  // (2) THE INHERITED RESIDUAL, asserted so nobody has to take the comment's word for it.
  //     A BUDGET refusal happens after both window entries have been written, so the
  //     visitor's windows are one higher than they earned. It is bounded at exactly one
  //     increment per refused request, it predates this slice (the hour budget has been
  //     ordered after the window write since 2026-09-05), and it only bites while the
  //     deployment is already answering everybody `budget_exhausted`. Named in
  //     live-sim-demo.md §4.6.3 with the fix.
  const SPENT = cfgOf({
    DEMO_CHAT_PER_MIN: "60", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000",
    DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "12",
  });
  fresh();
  const c2 = fakeCache().seed(ORIGIN + "/__moxie/rl/units/d0", { n: 12 });
  const r2 = await admitWith(SPENT, c2, "203.0.113.71", T0);
  eq(r2.ok, false, "a turn refused because the colo's DAY budget is spent");
  eq(r2.reason, "budget_exhausted", "…as budget_exhausted");
  eq(c2.count(c2.log.keys[0]), 1,
     "RESIDUAL, INHERITED: its per-IP minute entry was already written, so it counts a turn that never happened");
  deep(c2.body(c2.log.keys.find((k) => k.indexOf("/w" + DAY0) >= 0)), { h: 1, hb: 2, d: 1, db: 0 },
       "…and so was its wide entry. Bounded at ONE increment per refused request, and only while the " +
       "deployment is out of budget — the fix is a read-phase/write-phase split (§4.6.3)");
}

/* --------------------------------------------------------------------------- */
const summary = { checks, failures: fails, sections };
if (process.argv.includes("--json")) {
  console.log(JSON.stringify(summary));
} else if (fails.length) {
  console.log(`✗ shared_ceilings: ${fails.length} failure(s)`);
  for (const f of fails) console.log("  - " + f);
} else {
  console.log(`✓ shared_ceilings: ${checks} checks, the hour/day windows and the day budget are shared`);
}
process.exit(fails.length ? 1 : 0);
