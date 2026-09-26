/* functions/api/_lib/sharedtier.js — the Cache API tier (live-sim-demo.md §4.6.1): the
 * per-IP windows and the unit budget, shared across the isolates of ONE COLO.
 *
 * ============================================================================
 * WHAT THIS TIER IS. **NOT A GLOBAL CEILING, AND NOTHING MAY CALL IT ONE.**
 *
 *   1. **PER-COLO.** Cloudflare's cache does not replicate outside its data center, so a
 *      visitor who reaches two colos gets two counts. It removes the *isolate* multiplier
 *      (measured >= 7), not the colo one.
 *   2. **A BURST DEFEATS IT.** Unlocked read-modify-write: 31 concurrent increments stored
 *      9, while a paced sequential drain — what a counter exists to stop — lost 0 of 41.
 *      Bursts are already bounded by the concurrency ceiling and queue depth.
 *   3. **EVERY ERROR IS AN UNDERCOUNT, SO IT FAILS OPEN.** Every write is some observed
 *      `prev + n`, so the stored value never exceeds the truth. A miss, stale entry,
 *      timeout, throw or lost write can let someone through; none can refuse someone who
 *      should be served. That property is this tier's licence to exist.
 *
 * The in-isolate counters in `limits.js` still decide FIRST; this tier only ADDS refusals.
 * With `DEMO_CACHE_COUNTER=0`, or no `caches.default`, `admit()` never reaches it.
 *
 * WHAT IS NOT HERE: the concurrency ceiling. A slot must be given back, a lost give-back
 * leaks it for ever, and that fails CLOSED. The unit budget has the same shape (a refund
 * is a give-back), which is why its sub-tier never writes a charge — see
 * `sharedBudgetVerdict`.
 *
 * COST: a served turn issues up to ~6 ops (minute read+write, wide read+write, hour and
 * day budget reads, plus a publish when this isolate owes something). §4.6.1 measured
 * ~15 ms per op; anything above three ops is an extrapolation, not a measurement.
 * ============================================================================
 */

import { COUNTER_INFO, keyedTag } from "./hmac.js";
import {
  SCALES, UNITS, bucket, state, windowLimits,
  pendingUnits, pendingDayUnits, clearPending, clearDayPending,
} from "./counters.js";

/** Entries live on this deployment's OWN origin under a non-Function path, so an outsider
 *  asking for the URL reaches the static handler. The body is `{"n":<int>}` (or the wide
 *  shape) and a visitor appears only as a keyed one-way tag. */
const CACHE_PATH = "/__moxie/rl/";

/** 96 bits of tag. A collision would merge two visitors into one bucket — conservative. */
const CACHE_TAG_HEX = 24;

/** The two ways a cache op can fail to answer; both fail open. Exported so
 *  `./ttscache.js` shares this one deadline wrapper and one pair of symbols. */
export const CACHE_TIMEOUT = Symbol("cache-timeout");
export const CACHE_ERROR = Symbol("cache-error");
/** A hit whose `Age` has passed its `max-age`: treated as absent. */
const CACHE_STALE = Symbol("cache-stale");

/**
 * Run one cache operation under a hard deadline, and NEVER reject.
 *
 * A hung `match()` never settles; without this it would hold a concurrency slot until the
 * route's own timeout. Every outcome — a value, a throw, a synchronous throw from `run()`,
 * or the clock — resolves exactly once, and the timer is always cleared.
 */
export function withDeadline(ms, run) {
  return new Promise((resolve) => {
    let done = false;
    let timer = null;
    const finish = (v) => {
      if (done) return;
      done = true;
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      resolve(v);
    };
    timer = setTimeout(() => {
      timer = null;
      finish(CACHE_TIMEOUT);
    }, Math.max(1, ms));
    let started;
    try {
      started = run();
    } catch {
      finish(CACHE_ERROR);
      return;
    }
    Promise.resolve(started).then(finish, () => finish(CACHE_ERROR));
  });
}

/** The store for this admission: the injected fake in a test, `caches.default` on the real
 *  runtime, or `null` — the "behave exactly as without the tier" path. */
export function sharedStore(o, cfg) {
  if (!cfg || !cfg.cacheCounter) return null;
  if (o && "cache" in o) return o.cache || null;
  try {
    return (typeof caches !== "undefined" && caches && caches.default) || null;
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------------------- *
 * Key shapes. A wrong key merges two counters, so every component is argued.
 *
 *   window  <origin>/__moxie/rl/<route>/<tag>/<minute bucket>     (arity 3)
 *   wide    <origin>/__moxie/rl/<route>/<tag>/w<day bucket>       (arity 3)
 *   units   <origin>/__moxie/rl/units/<hour bucket>               (arity 2)
 *   day     <origin>/__moxie/rl/units/d<day bucket>               (arity 2)
 *
 * * `<origin>` keeps each deployment's (and each preview's) counts separate. No hostname
 *   is ever written under `functions/` — that is config (C3).
 * * `units` is not a route name AND the budget shapes have different arity, so no window
 *   key can spell a budget key. `__keyShapes()` exposes both facts for a test to assert.
 * * Within each arity pair, the one-letter MARK is the whole separation: a decimal bucket
 *   cannot begin with a letter, so no clock value makes a narrow key spell a wide one.
 * * The budget keys carry no visitor, no route (route cost rides in the increment) and no
 *   ceiling (compared, never keyed, so lowering it mid-hour bites immediately). Every byte
 *   is a literal or a clock-derived integer — nothing an outsider controls.
 * * The bucket in the key makes last period's count a DIFFERENT URL, so it cannot be read
 *   as this one's. The wide entry rotates daily, so it stamps each scale's bucket in its
 *   body instead (see `sharedWideWindow`).
 * ---------------------------------------------------------------------------- */

const UNITS_PATH = "units";
const WIDE_MARK = "w";
const DAY_MARK = "d";

function keyUrl(request, ...parts) {
  return new URL(request.url).origin + CACHE_PATH + parts.join("/");
}

/** The key shapes as DATA, so a test can assert the collision argument. Tests only. */
export function __keyShapes() {
  return {
    prefix: CACHE_PATH,
    units: UNITS_PATH,
    routes: Object.keys(UNITS),
    windowArity: 3,
    unitsArity: 2,
    wideMark: WIDE_MARK,
    dayMark: DAY_MARK,
    tagHex: CACHE_TAG_HEX,
  };
}

/* ---------------------------------------------------------------------------- *
 * Reading and writing an entry
 * ---------------------------------------------------------------------------- */

/** The stored body, `CACHE_STALE`, or `null` — the latter two mean "start from zero". */
async function readBody(store, key) {
  const hit = await store.match(key);
  if (!hit) return null;
  const age = Number(hit.headers.get("Age"));
  const cc = /max-age\s*=\s*(\d+)/i.exec(hit.headers.get("Cache-Control") || "");
  const maxAge = cc ? Number(cc[1]) : 0;
  if (Number.isFinite(age) && maxAge > 0 && age >= maxAge) return CACHE_STALE;
  let body = null;
  try {
    body = await hit.json();
  } catch {
    return null; // an entry we cannot parse is an entry we do not have
  }
  return body && typeof body === "object" ? body : null;
}

/** A single-counter entry: an integer, `CACHE_STALE`, or `null`. */
async function readCount(store, key) {
  const body = await readBody(store, key);
  if (body === CACHE_STALE || body === null) return body;
  const n = Number(body.n);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

/** Record a completed read on sub-tier stats `c`; return `seen` when `usable`, else `null`
 *  (a stale entry is never usable). */
function tally(c, seen, usable) {
  c.ops += 1;
  if (seen === CACHE_STALE) {
    c.stale += 1;
    return null;
  }
  if (usable) {
    c.hit += 1;
    return seen;
  }
  c.miss += 1;
  return null;
}

/** Write `body` under `key` with the given lifetime, never throwing, recording the outcome.
 *  Unlocked on purpose: a lost update undercounts. */
async function putEntry(c, store, key, body, maxAgeS, cfg) {
  const wrote = await withDeadline(cfg.cacheTimeoutMs, () =>
    store.put(key, new Response(JSON.stringify(body), {
      headers: { "Content-Type": "application/json", "Cache-Control": "max-age=" + maxAgeS },
    })),
  );
  if (wrote === CACHE_TIMEOUT) c.timeouts += 1;
  else if (wrote === CACHE_ERROR) c.errors += 1;
  else {
    c.ops += 1;
    c.wrote += 1;
  }
}

/* ---------------------------------------------------------------------------- *
 * The per-IP window sub-tier
 * ---------------------------------------------------------------------------- */

/**
 * The window verdict for one admission: `null` to allow (also every failure path), or a
 * refusal `{retryAfterS, rateLimit}` shaped exactly like `chargeWindows()`'s, so a client
 * cannot tell the tiers apart.
 *
 * A refusal writes nothing (it spent nothing), and a failed read never writes — writing
 * `1` after a failed read would RESET a live count.
 */
export async function sharedWindowVerdict(store, request, { ip, route, cfg, nowS }) {
  const limit = windowLimits(cfg, route).min;
  const c = state.stats.cache;
  const b = bucket(nowS, SCALES.min);
  const resetAt = (b + 1) * SCALES.min;

  const tag = await keyedTag(cfg, COUNTER_INFO, ip + "|" + route, CACHE_TAG_HEX);
  // No minute cap for this route — the wider scales may still have one.
  if (!limit) return sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });
  const key = keyUrl(request, route, tag, b);
  c.checked += 1;

  const seen = await withDeadline(cfg.cacheTimeoutMs, () => readCount(store, key));
  if (seen === CACHE_TIMEOUT) {
    c.timeouts += 1;
    c.allowed += 1;
    return null; // FAIL OPEN
  }
  if (seen === CACHE_ERROR) {
    c.errors += 1;
    c.allowed += 1;
    return null; // FAIL OPEN
  }
  const used = tally(c, seen, typeof seen === "number") || 0;

  if (used >= limit) {
    c.refused += 1;
    return { retryAfterS: Math.max(1, resetAt - nowS), rateLimit: { limit, remaining: 0, reset: resetAt } };
  }

  // The wider scales decide BEFORE this one writes, so a refusal by any of the three costs
  // zero writes: incrementing the minute for a request the hour then refuses would be an
  // OVERCOUNT. (Residual: a later BUDGET refusal still leaves these windows one higher —
  // bounded, and only while the deployment is already out of budget; §4.6.3.)
  const wider = await sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });
  if (wider) return wider;

  // The key carries the bucket, so one window of `max-age` is enough.
  await putEntry(c, store, key, { n: used + 1 }, SCALES.min, cfg);
  c.allowed += 1;
  return null;
}

/**
 * The window sub-tier's WIDER half: the hour and the day, in ONE entry, so both scales cost
 * one round trip rather than two each.
 *
 * The entry rotates DAILY (its widest scale), so each scale's BUCKET is stamped in the body
 * beside its count and a count stamped with any other bucket reads as ZERO. Every failure —
 * miss, stale, timeout, throw, bad body, mismatched bucket, lost update — reads SMALLER and
 * admits. A refusal writes nothing, and when the hour refuses the day is not incremented.
 */
async function sharedWideWindow(store, request, { ip, route, cfg, nowS, tag }) {
  const limits = windowLimits(cfg, route);
  /** `[scale, ceiling, count field, bucket field]`, NARROWEST first, so the refusal a
   *  visitor meets carries the shortest `Retry-After` that applies. */
  const scales = [];
  if (limits.hour) scales.push(["hour", limits.hour, "h", "hb"]);
  if (limits.day) scales.push(["day", limits.day, "d", "db"]);
  if (!scales.length) return null;
  const w = state.stats.cache.wide;
  const key = keyUrl(request, route, tag, WIDE_MARK + bucket(nowS, SCALES.day));
  w.checked += 1;

  const seen = await withDeadline(cfg.cacheTimeoutMs, () => readBody(store, key));
  if (seen === CACHE_TIMEOUT) {
    w.timeouts += 1;
    w.allowed += 1;
    return null; // FAIL OPEN: a deadline is not evidence that anybody is over their hour
  }
  if (seen === CACHE_ERROR) {
    w.errors += 1;
    w.allowed += 1;
    return null; // FAIL OPEN: neither is a throw from a store having a bad day
  }
  const body = tally(w, seen, !!seen);

  const next = {};
  for (const [name, ceiling, nField, bField] of scales) {
    const scale = SCALES[name];
    const b = bucket(nowS, scale);
    // THE BUCKET CHECK IS THE STALENESS ARGUMENT: a closed window's count reads as zero.
    const stored = body && Number(body[bField]) === b ? Number(body[nField]) : 0;
    const used = Number.isFinite(stored) && stored > 0 ? Math.floor(stored) : 0;
    if (used >= ceiling) {
      w.refused += 1;
      return {
        retryAfterS: Math.max(1, (b + 1) * scale - nowS),
        // The minute's triple, as `chargeWindows()` sends when ITS hour or day refuses.
        rateLimit: { limit: limits.min, remaining: 0, reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min },
      };
    }
    next[nField] = used + 1;
    next[bField] = b;
  }

  // `max-age` is one DAY, the widest scale held; narrower ones are policed by their stamps.
  await putEntry(w, store, key, next, SCALES.day, cfg);
  w.allowed += 1;
  return null;
}

/* ---------------------------------------------------------------------------- *
 * The unit-budget sub-tier
 * ---------------------------------------------------------------------------- */

/**
 * The shared HOUR budget: `null` to allow, or `{retryAfterS}`.
 *
 * ============================================================================
 * THE SHARED ENTRY IS NEVER TOLD ABOUT A CHARGE IT MIGHT HAVE TO UN-HEAR.
 *
 * A window write is `prev + 1`, so losing it undercounts. A budget REFUND would be
 * `prev - cost`, and losing THAT overcounts: the colo's hour empties early and real
 * visitors get `budget_exhausted`. That fails CLOSED. So:
 *
 *   * `admit()` only READS the entry for the request it is admitting;
 *   * units reach this isolate's ledger (`state.units`) only from `release()` on an
 *     un-refunded request — i.e. only real gateway spend;
 *   * the NEXT admission publishes the ledger: `put(seen + owed)`.
 *
 * There is no refund write anywhere, so no lost refund can refuse anybody, and a flood of
 * refused requests publishes literally nothing.
 *
 * Rejected alternatives: charging shared + refunding locally (a free drain: 200 tokenless
 * POSTs x 3 units empties a 600-unit hour for every isolate); a signed net (lost refunds
 * accumulate unboundedly); refunding shared anyway (breaks "every error is an undercount").
 *
 * Cost, stated: the colo's entry lags real spend by what isolates have not yet published —
 * bounded by concurrency + queue depth per isolate, and permissive. A recycled isolate
 * takes its ledger with it (recorded as `dropped`).
 * ============================================================================
 *
 * One `match` always; one `put` only when this isolate owes something AND is admitting.
 */
export async function sharedBudgetVerdict(store, request, { cfg, nowS }) {
  const ceiling = cfg.unitBudgetHour;
  // Uncapped hour: nothing to mirror here, but the DAY may still be capped.
  if (!ceiling) return sharedDayBudget(store, request, { cfg, nowS });
  const c = state.stats.cache.units;
  const b = bucket(nowS, SCALES.hour);
  const resetAt = (b + 1) * SCALES.hour;
  const owed = pendingUnits(b); // also rolls a past hour's ledger
  const key = keyUrl(request, UNITS_PATH, b);
  c.checked += 1;

  const seen = await withDeadline(cfg.cacheTimeoutMs, () => readCount(store, key));
  if (seen === CACHE_TIMEOUT) {
    c.timeouts += 1;
    c.allowed += 1;
    return null; // FAIL OPEN; ledger KEPT (nothing was written)
  }
  if (seen === CACHE_ERROR) {
    c.errors += 1;
    c.allowed += 1;
    return null; // FAIL OPEN; ledger KEPT (no write was attempted)
  }
  const published = tally(c, seen, typeof seen === "number") || 0;

  // `>=` rather than `chargeBudget`'s `used + cost >`: this sub-tier's job is noticing what
  // OTHER isolates spent; the local cost was already counted by the in-isolate map.
  if (published + owed >= ceiling) {
    c.refused += 1;
    return { retryAfterS: Math.max(1, resetAt - nowS) };
  }

  // Skipped when there is nothing to publish — every route-refused request, structurally.
  if (owed > 0) {
    await putEntry(c, store, key, { n: published + owed }, SCALES.hour, cfg);
    // Cleared on the ATTEMPT, confirmed or not: a timed-out `put` may have landed, and
    // retrying would publish twice (overcount). Forgetting undercounts, which is legal.
    c.published += owed;
    clearPending(b);
  }
  c.allowed += 1;
  // The hour admitted; the day is the same ceiling one scale wider.
  return sharedDayBudget(store, request, { cfg, nowS });
}

/**
 * The shared DAY budget (`DEMO_UNIT_BUDGET_DAY`) — the hour's design exactly, with its own
 * ledger (`state.unitsDay`) because the two roll on different clocks.
 */
async function sharedDayBudget(store, request, { cfg, nowS }) {
  const ceiling = cfg.unitBudgetDay;
  if (!ceiling) return null;
  const d = state.stats.cache.unitsDay;
  const db = bucket(nowS, SCALES.day);
  const resetAt = (db + 1) * SCALES.day;
  const owedDay = pendingDayUnits(db); // also rolls a past day's ledger
  const key = keyUrl(request, UNITS_PATH, DAY_MARK + db);
  d.checked += 1;

  const seen = await withDeadline(cfg.cacheTimeoutMs, () => readCount(store, key));
  if (seen === CACHE_TIMEOUT) {
    d.timeouts += 1;
    d.allowed += 1;
    return null; // FAIL OPEN, ledger KEPT: nothing was written, so nothing can have landed
  }
  if (seen === CACHE_ERROR) {
    d.errors += 1;
    d.allowed += 1;
    return null; // FAIL OPEN, ledger KEPT, for the same reason as the timeout above
  }
  const spent = tally(d, seen, typeof seen === "number") || 0;

  if (spent + owedDay >= ceiling) {
    d.refused += 1;
    return { retryAfterS: Math.max(1, resetAt - nowS) };
  }

  if (owedDay > 0) {
    await putEntry(d, store, key, { n: spent + owedDay }, SCALES.day, cfg);
    // Cleared on the ATTEMPT — see the hour.
    d.published += owedDay;
    clearDayPending(db);
  }
  d.allowed += 1;
  return null;
}
