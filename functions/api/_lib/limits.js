/* functions/api/_lib/limits.js — request admission: the origin pin, the per-IP windows,
 * the unit budget and the concurrency ceiling, in that order, in one function (`admit`).
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (caps), §4.3 (origin pin), §4.5
 * (status + `Retry-After`), §4.6 ('Counters, honestly'), §7 (the capacity signal).
 *
 * Modules: `./counters.js` (state, scales, ledgers), `./clientip.js` (IP key, origin pin),
 * `./body.js` (bounded body readers), `./sharedtier.js` (the Cache API tier). This file
 * re-exports their public surface so routes and tests import from one place.
 *
 * ============================================================================
 * **THESE COUNTERS ARE BEST-EFFORT. THEY ARE NOT A GLOBAL CEILING.**
 *
 * An isolate is not a shared counter: Cloudflare runs many, in many colos, and recycles
 * them freely. The per-IP windows and unit budget are ALSO kept per-colo in the Cache API
 * (`sharedtier.js`), which removes the isolate multiplier but not the colo one, loses
 * writes under a burst, and only ever errs by undercounting. The concurrency ceiling, its
 * FIFO and `/api/health`'s probe stay per-isolate on purpose (a lost slot give-back fails
 * CLOSED; a probe must not be able to hang). No doc sentence may call any of this a hard
 * limit.
 *
 * What these counters are good for: stopping scripts and accidents. What actually bounds
 * the worst case: (1) a budget-scoped key on the gateway itself (§4.2); (2) the per-request
 * caps (`max_tokens`, input/TTS chars, timeouts) enforced in every route regardless of any
 * counter; (3) the speech ticket (`./hmac.js`), which makes `/api/speech` unable to speak
 * text we did not write. A true single-writer count needs a Durable Object (P1).
 * ============================================================================
 *
 * ORDERING IS THE POINT: every free refusal happens before any expensive one, and the
 * concurrency slot — the only thing that must be given back — is taken LAST. At the ceiling
 * a request may wait in a bounded per-isolate FIFO instead of being refused on the spot;
 * the ceiling itself matches the upstream key's parallel limit and is not raised.
 */

import {
  UNITS, SCALES, bucket, nowOr, prune, freshStats, state, windowLimits,
  accruePending, accrueDayPending, unaccruePending, unaccrueDayPending,
} from "./counters.js";
import { clientIp, checkOrigin } from "./clientip.js";
import { sharedStore, sharedWindowVerdict, sharedBudgetVerdict } from "./sharedtier.js";

export { UNITS } from "./counters.js";
export { ipKey, clientIp, checkOrigin } from "./clientip.js";
export { maxJsonBodyBytes, readJsonBody, readAudioBody } from "./body.js";
export { CACHE_TIMEOUT, CACHE_ERROR, withDeadline, __keyShapes } from "./sharedtier.js";

/** Reset every counter. Tests only. Waiters are EXPIRED, not dropped: a dropped waiter is
 *  a pending timer and a test that hangs instead of failing. Clearing the ledgers makes
 *  `__reset()` a real isolate boundary against a fake cache that survives it. */
export function __reset() {
  state.windows.clear();
  state.budget.clear();
  state.units = { pending: 0, bucket: -1 };
  state.unitsDay = { pending: 0, bucket: -1 };
  for (const route of Object.keys(state.waiters)) expireAll(state.waiters[route]);
  state.inflight = { chat: 0, speech: 0, transcribe: 0 };
  state.waiters = { chat: [], speech: [], transcribe: [] };
  state.stats = freshStats();
}

/** A snapshot of what the counters RECORDED — tests assert on this, not on timing. No IP. */
export function __state() {
  return {
    inflight: { ...state.inflight },
    waiting: {
      chat: state.waiters.chat.length,
      speech: state.waiters.speech.length,
      transcribe: state.waiters.transcribe.length,
    },
    windows: state.windows.size,
    budget: Object.fromEntries(state.budget),
    /** What this isolate OWES the shared hour/day — not what the colo has been told. */
    units: { ...state.units },
    unitsDay: { ...state.unitsDay },
    stats: JSON.parse(JSON.stringify(state.stats)),
  };
}

/** Called by each route immediately before its gateway `fetch()`, so a test can assert
 *  ZERO upstream calls on every refusal path without stubbing anything. */
export function noteUpstreamCall() {
  state.stats.upstreamCalls += 1;
}

/* ---------------------------------------------------------------------------- *
 * §4.1 — the per-IP windows
 * ---------------------------------------------------------------------------- */

/** The `X-RateLimit-*` triple for this IP right now, charging nothing — re-read after a
 *  refund so the header never contradicts the counter. */
function rateLimitSnapshot(ip, route, cfg, nowS) {
  const limits = windowLimits(cfg, route);
  const minKey = ip + "|" + route + "|min|" + bucket(nowS, SCALES.min);
  return {
    limit: limits.min,
    remaining: Math.max(0, limits.min - (state.windows.get(minKey) || 0)),
    reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min,
  };
}

/** Check, then charge, every configured window for this IP. `charged` lists the exact keys
 *  incremented; the keys embed their bucket, so a refund after a boundary lands on the
 *  past window it charged and cannot credit the current one. */
function chargeWindows(ip, route, cfg, nowS) {
  const limits = windowLimits(cfg, route);
  const touched = [];
  for (const [name, scale] of Object.entries(SCALES)) {
    const limit = limits[name];
    if (!limit) continue; // 0 / undefined => not capped at this scale
    const key = ip + "|" + route + "|" + name + "|" + bucket(nowS, scale);
    const used = state.windows.get(key) || 0;
    if (used >= limit) {
      const resetAt = (bucket(nowS, scale) + 1) * scale;
      return {
        ok: false,
        reason: "rate_limited",
        retryAfterS: Math.max(1, resetAt - nowS),
        rateLimit: { limit: limits.min, remaining: 0, reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min },
        charged: [],
      };
    }
    touched.push([key, used + 1]);
  }
  for (const [key, next] of touched) state.windows.set(key, next);
  prune(state.windows);
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit: rateLimitSnapshot(ip, route, cfg, nowS),
    charged: touched.map(([key]) => key),
  };
}

/* ---------------------------------------------------------------------------- *
 * §4.1 — the unit budget
 * ---------------------------------------------------------------------------- */

/**
 * Charge one request against the in-isolate unit budget.
 *
 * `hourBucket`/`dayBucket` (and whether each ceiling exists) ride out with the answer for
 * the shared ledger: `release()` may settle after a bucket boundary, and the period that
 * pays for a turn is the one the charge was MADE in, not the one it was released in.
 */
function chargeBudget(route, cfg, nowS) {
  const cost = UNITS[route] || 1;
  const ceilings = { hour: cfg.unitBudgetHour, day: cfg.unitBudgetDay };
  const hourBucket = bucket(nowS, SCALES.hour);
  const hourly = !!cfg.unitBudgetHour;
  const dayBucket = bucket(nowS, SCALES.day);
  const daily = !!cfg.unitBudgetDay;
  const touched = [];
  for (const [name, ceiling] of Object.entries(ceilings)) {
    if (!ceiling) continue; // 0 => uncapped at this scale
    const scale = SCALES[name];
    const key = "units|" + name + "|" + bucket(nowS, scale);
    const used = state.budget.get(key) || 0;
    if (used + cost > ceiling) {
      const resetAt = (bucket(nowS, scale) + 1) * scale;
      return {
        ok: false, reason: "budget_exhausted", retryAfterS: Math.max(1, resetAt - nowS),
        charged: [], cost, hourBucket, hourly, dayBucket, daily,
      };
    }
    touched.push([key, used + cost]);
  }
  for (const [key, next] of touched) state.budget.set(key, next);
  prune(state.budget);
  return {
    ok: true, reason: null, retryAfterS: 0,
    charged: touched.map(([key]) => key), cost, hourBucket, hourly, dayBucket, daily,
  };
}

/**
 * Give back exactly what `chargeWindows` and `chargeBudget` took.
 *
 * WHY REFUND RATHER THAN REORDER. `admit()` charges the window and budget BEFORE taking a
 * slot, so a request that queues and then times out would have paid for a turn it never
 * got. Waiting BEFORE charging would let a request that is free to refuse today (the 6th
 * turn in a minute) first occupy a scarce queue slot — a script could fill the queue with
 * requests it had no budget to make. So the order stays and the accounting is corrected
 * after the fact. Cost: while a request waits its charge is held, so a concurrent request
 * may be refused on a unit about to come back — bounded by queue depth x unit cost, and in
 * the conservative direction.
 *
 * Precise, never generous: an absent key is skipped rather than driven negative, and a key
 * reaching zero is deleted.
 */
function refundCharges(windowKeys, budgetKeys, cost) {
  for (const key of windowKeys || []) {
    const used = state.windows.get(key);
    if (used === undefined) continue; // pruned or never charged: nothing to give back
    if (used <= 1) state.windows.delete(key);
    else state.windows.set(key, used - 1);
  }
  for (const key of budgetKeys || []) {
    const used = state.budget.get(key);
    if (used === undefined) continue;
    if (used <= cost) state.budget.delete(key);
    else state.budget.set(key, used - cost);
  }
}

/** Force the budget to its ceiling. Tests only (reach `budget_exhausted` without 200 calls). */
export function __exhaustBudget(cfg, nowS) {
  const now = nowOr(nowS);
  if (cfg.unitBudgetHour) state.budget.set("units|hour|" + bucket(now, SCALES.hour), cfg.unitBudgetHour);
  if (cfg.unitBudgetDay) state.budget.set("units|day|" + bucket(now, SCALES.day), cfg.unitBudgetDay);
}

/**
 * The budget answer with nothing charged, for `/api/health`.
 *
 * IN-ISOLATE ONLY, deliberately: the probe is synchronous (a probe that cannot await cannot
 * call upstream or hang on a cache — `sim/test_mode.mjs` asserts it), so it can report
 * `live` a moment before a spending route reports `budget_exhausted`. It is not the
 * deployment's budget and must not be described as one.
 */
export function budgetState(cfg, nowS) {
  const now = nowOr(nowS);
  for (const [name, ceiling] of Object.entries({ hour: cfg.unitBudgetHour, day: cfg.unitBudgetDay })) {
    if (!ceiling) continue;
    const scale = SCALES[name];
    const used = state.budget.get("units|" + name + "|" + bucket(now, scale)) || 0;
    if (used >= ceiling) {
      return { exhausted: true, retryAfterS: Math.max(1, (bucket(now, scale) + 1) * scale - now) };
    }
  }
  return { exhausted: false, retryAfterS: 0 };
}

/* ---------------------------------------------------------------------------- *
 * §7 — the capacity signal
 * ---------------------------------------------------------------------------- */

function capacityOf(cfg, route) {
  if (route === "speech") return cfg.maxConcurrentSpeech;
  // `transcribe` SHARES chat's ceiling: §4.1 names no third number, an STT call is in
  // chat's cost bracket, and it is the first leg of a chat turn anyway.
  return cfg.maxConcurrentChat;
}

/** `{inflight, capacity}` for the envelope. `inflight` is THIS isolate's count — an honest
 *  number about an incomplete view, which is why §7's copy is human, not a gauge. */
export function loadOf(cfg, route) {
  return { inflight: state.inflight[route] || 0, capacity: capacityOf(cfg, route) };
}

/* ---------------------------------------------------------------------------- *
 * Refusals and the admission queue
 * ---------------------------------------------------------------------------- */

/** A refusal. `release`, `refundBudget` and `chargeExtra` are present no-ops (the last
 *  answers false) so a caller never throws a `TypeError` on a refusal path. */
function refuse(reason, extra) {
  state.stats.refused += 1;
  state.stats.refusals[reason] = (state.stats.refusals[reason] || 0) + 1;
  return {
    ok: false, reason, retryAfterS: 0, rateLimit: null,
    release: () => {}, refundBudget: () => {}, chargeExtra: () => false,
    ...extra,
  };
}

/*
 * THE QUEUE. Four slots at ~1.2 s a turn already serve ~3 turns/s; what breaks is the
 * momentary collision, and a short bounded wait absorbs it. PER-ISOLATE: a fair order among
 * the requests this isolate holds, NOT a global position.
 *
 * FAIRNESS, MECHANICALLY: `release()` does not decrement `inflight` while anyone waits — it
 * HANDS THE SLOT OVER to the longest waiter, count unchanged. So no slot is ever briefly
 * free for a late arrival to grab, and none can be double-issued or leaked. Everything that
 * DECIDES in `admit()` runs synchronously before its first `await`, so two admissions can
 * never interleave inside the decision.
 */

/** Settle every waiter in a list as expired, clearing its timer. Used by `__reset()`. */
function expireAll(waiting) {
  while (waiting && waiting.length) {
    const w = waiting.shift();
    if (w.settled) continue;
    w.settled = true;
    if (w.timer !== null) clearTimeout(w.timer);
    w.resolve("expired");
  }
}

/** Join the FIFO; resolve `"granted"` or `"expired"`. Never rejects. */
function waitForSlot(route, maxWaitMs) {
  const waiting = state.waiters[route];
  return new Promise((resolve) => {
    const w = { settled: false, resolve, timer: null };
    w.timer = setTimeout(() => {
      if (w.settled) return;
      w.settled = true;
      const i = waiting.indexOf(w);
      if (i >= 0) waiting.splice(i, 1); // leave no tombstone in the FIFO
      resolve("expired");
    }, maxWaitMs);
    waiting.push(w);
  });
}

/** Hand the slot to the longest waiter (count CONSERVED), or free it if nobody waits. */
function handOffOrRelease(route) {
  const waiting = state.waiters[route];
  while (waiting && waiting.length) {
    const w = waiting.shift();
    if (w.settled) continue; // already timed out; it holds nothing
    w.settled = true;
    if (w.timer !== null) clearTimeout(w.timer);
    state.stats.queue.granted += 1;
    w.resolve("granted");
    return; // inflight deliberately UNCHANGED: the slot moved, it did not free
  }
  state.inflight[route] = Math.max(0, (state.inflight[route] || 0) - 1);
}

/**
 * The granted-slot result. `inflight` is already accounted for by the caller.
 *
 * ============================================================================
 * `refundBudget()` GIVES BACK THE UNIT BUDGET, *NOT* THE PER-IP WINDOW.
 *
 * Admission charges units before the route body runs, and the body has refusals of its
 * own (`too_long`, `too_short`, `bad_request`, `blocked`, `turnstile_failed`) that make no
 * gateway call. Without a refund, 200 tokenless POSTs empty the hourly budget for free and
 * every real visitor gets a SCRIPTED page — a bot control turning a paid drain into a free
 * one. So those paths refund the budget, which is SHARED by every visitor.
 *
 * The per-IP window is NOT refunded: it is self-inflicted, and it is the only thing that
 * makes a flood of free refusals from one address go quiet. Refunding it would make
 * tokenless refusals unlimited per IP. (The queue-expiry and shared-tier refusals refund
 * BOTH, via `refundCharges` — neither is the requester's fault.)
 *
 * Refund only where the route returns WITHOUT having called `noteUpstreamCall()`.
 * Idempotent, as is `release()`: a second call would credit units nobody spent.
 *
 * THE SETTLE rides on `release()`: an un-refunded release means the turn reached the
 * gateway, so its units go to the shared ledger (never at admission — nobody knows yet
 * whether the turn will be served; never a separate method routes must remember — one
 * missed path is a free drain). Every route releases in a `finally`, which the mutation
 * tables prove load-bearing. Refund-then-release never settles; release-then-refund
 * un-accrues from the still-unpublished ledger.
 * ============================================================================
 */
function grantedSlot(route, capacity, rateLimit, budget, ctx) {
  state.stats.admitted += 1;
  let released = false;
  let refunded = false;
  let settled = false;
  /** What this request owes the shared HOUR if released un-refunded. Zero with no hourly
   *  ceiling, or when the in-isolate charge took nothing (uncapped). */
  let owed = budget && budget.hourly && budget.charged && budget.charged.length
    ? (budget.cost || 0) : 0;
  const hourBucket = (budget && budget.hourBucket) || 0;
  /** The same for the DAY, from the day ceiling's own flag. */
  let owedDay = budget && budget.daily && budget.charged && budget.charged.length
    ? (budget.cost || 0) : 0;
  const dayBucket = (budget && budget.dayBucket) || 0;
  /** Every budget key charged, admission first then `chargeExtra()`'s. A key may repeat,
   *  and `refundCharges` subtracts once per occurrence — so a refund returns both charges.
   *  A copy, because `admit()` still reads `budget.charged` on its own refusal paths. */
  const budgetKeys = [...((budget && budget.charged) || [])];
  let extraUnits = 0;
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit,
    load: { inflight: state.inflight[route], capacity },
    release() {
      if (released) return; // idempotent: a double release would under-count for ever
      released = true;
      // THE SETTLE: un-refunded means the gateway was reached, so the units are real spend.
      if (!refunded && !settled) {
        settled = true;
        accruePending(hourBucket, owed);
        accrueDayPending(dayBucket, owedDay);
      }
      handOffOrRelease(route);
    },
    refundBudget() {
      if (refunded) return; // idempotent: a double refund would credit units never spent
      refunded = true;
      if (settled) {
        settled = false;
        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above
        unaccrueDayPending(dayBucket, owedDay);
      }
      state.stats.refundedUnits += ((budget && budget.charged && budget.charged.length)
        ? (budget.cost || 0) : 0) + extraUnits;
      refundCharges([], budgetKeys, (budget && budget.cost) || 0);
    },
    /**
     * Charge this admitted request for ONE MORE gateway call (`chat.js`'s re-roll).
     *
     * The per-IP window is NOT touched — it counts what the visitor did, and they typed one
     * sentence. The unit budget IS charged in full — it counts what the deployment spent,
     * and an uncharged re-roll would let the budget describe up to twice the money it
     * names. With no headroom this returns FALSE and the caller MUST NOT make the call (the
     * visitor keeps the reply they have). Charged against the ADMISSION's clock so a turn
     * straddling a boundary bills one period. Always false after release or refund.
     *
     * @returns {boolean} true when the units were taken and the caller may spend them.
     */
    chargeExtra() {
      if (released || refunded) return false;
      if (!ctx || !ctx.cfg) return false;
      const extra = chargeBudget(route, ctx.cfg, ctx.nowS);
      if (!extra.ok) return false;
      for (const key of extra.charged) budgetKeys.push(key);
      const cost = extra.cost || 0;
      // An uncapped deployment charges no key, so it owes and refunds nothing.
      if (extra.charged.length) extraUnits += cost;
      if (extra.hourly && extra.charged.length) owed += cost;
      if (extra.daily && extra.charged.length) owedDay += cost;
      return true;
    },
  };
}

/** Grant the slot, consulting the shared tier when there is one. NOT `async`: with no
 *  cache this returns the granted slot synchronously, with no extra microtask. */
function grantOrShared(o, ctx) {
  const store = sharedStore(o, ctx.cfg);
  if (!store) return grantedSlot(ctx.route, ctx.capacity, ctx.win.rateLimit, ctx.budget, ctx);
  return sharedThenGrant(store, o, ctx);
}

/**
 * The async half of `grantOrShared`: holds the slot across the cache round trips and hands
 * it on if a sub-tier refuses.
 *
 * WINDOW BEFORE BUDGET, deliberately, though budget-first would be cheaper when the hour is
 * spent: it would tell a visitor who is merely over their own minute that the whole
 * deployment is out (`budget_exhausted`, a 503 that paints the page SCRIPTED) instead of
 * `rate_limited` (a 429 the browser paces against).
 */
async function sharedThenGrant(store, o, ctx) {
  const { route, capacity, cfg, win, budget, ip, nowS } = ctx;
  let verdict = null;
  let reason = "rate_limited";
  try {
    verdict = await sharedWindowVerdict(store, o.request, { ip, route, cfg, nowS });
    if (!verdict) {
      const over = await sharedBudgetVerdict(store, o.request, { cfg, nowS });
      if (over) {
        // The same `rateLimit` triple `admit()`'s own `budget_exhausted` sends.
        verdict = { retryAfterS: over.retryAfterS, rateLimit: win.rateLimit };
        reason = "budget_exhausted";
      }
    }
  } catch {
    // Last seatbelt: the sub-tiers swallow cache errors, so something else threw. Still
    // may not cost a visitor their turn. Recorded on the outer counter (no honest owner).
    state.stats.cache.errors += 1;
    verdict = null;
  }
  if (!verdict) return grantedSlot(route, capacity, win.rateLimit, budget, ctx);

  // Refused: give the slot back FIRST (someone may be queued), then refund the charge. The
  // ledger is untouched — this request never reaches a granted `release()`.
  handOffOrRelease(route);
  refundCharges(win.charged, budget.charged, budget.cost);
  return {
    ...refuse(reason, { retryAfterS: verdict.retryAfterS, rateLimit: verdict.rateLimit }),
    load: { inflight: state.inflight[route] || 0, capacity },
  };
}

/**
 * Origin -> per-IP windows -> unit budget -> concurrency (-> shared tier), once.
 *
 * `async` only for the queue wait and the shared tier; everything that decides runs
 * synchronously first. The caller must `release()` in a `finally`. `/api/health` never
 * calls this and stays synchronous. Refusals after the charges (full queue, expired wait,
 * shared-tier refusal) refund what was charged — see `refundCharges`.
 *
 * @param {{request: Request, cfg: object, route: "chat"|"speech"|"transcribe", nowS?: number,
 *          cache?: {match: Function, put: Function}|null}} o `cache` is a TEST seam only.
 * @returns {Promise<{ok: boolean, reason: string|null, retryAfterS: number,
 *            rateLimit: {limit:number,remaining:number,reset:number}|null,
 *            load: {inflight:number,capacity:number}, release: () => void}>}
 */
export async function admit(o) {
  const { request, cfg, route } = o;
  const nowS = nowOr(o.nowS);
  const load = loadOf(cfg, route);

  const origin = checkOrigin(request, cfg);
  if (!origin.ok) return { ...refuse("forbidden_origin"), load };

  const ip = clientIp(request, cfg);
  const win = chargeWindows(ip, route, cfg, nowS);
  if (!win.ok) {
    return { ...refuse("rate_limited", { retryAfterS: win.retryAfterS, rateLimit: win.rateLimit }), load };
  }

  const budget = chargeBudget(route, cfg, nowS);
  if (!budget.ok) {
    return { ...refuse("budget_exhausted", { retryAfterS: budget.retryAfterS, rateLimit: win.rateLimit }), load };
  }

  const capacity = capacityOf(cfg, route);
  const waiting = state.waiters[route];

  // Fast path: a free slot and nobody ahead. `waiting.length === 0` is belt-and-braces —
  // the hand-over already keeps `inflight` at capacity while anyone waits — so a late
  // arrival cannot overtake even if that invariant ever breaks.
  if (waiting.length === 0 && (state.inflight[route] || 0) < capacity) {
    state.inflight[route] = (state.inflight[route] || 0) + 1;
    return grantOrShared(o, { route, capacity, cfg, win, budget, ip, nowS });
  }

  // At capacity. Refuse at once when the queue is off (either value 0) or full: a queue
  // without a depth cap is just a slower way to fall over.
  const maxWaitMs = Number.isFinite(cfg.queueMaxWaitMs) ? cfg.queueMaxWaitMs : 0;
  const maxDepth = Number.isFinite(cfg.queueMaxDepth) ? cfg.queueMaxDepth : 0;
  const refusal = () => {
    // Refund FIRST, then read the headers back, so they describe the post-refund counter.
    refundCharges(win.charged, budget.charged, budget.cost);
    return {
      ...refuse("at_capacity", { rateLimit: rateLimitSnapshot(ip, route, cfg, nowS) }),
      // `retryAfterS` 0 => the envelope's `Retry-After: 15`. Honest: the ceiling is
      // saturated beyond what the queue absorbs, and a shorter hint just re-joins the FIFO.
      load: { inflight: state.inflight[route] || 0, capacity },
    };
  };
  if (maxWaitMs <= 0 || maxDepth <= 0 || waiting.length >= maxDepth) {
    state.stats.queue.refusedFull += 1;
    return refusal();
  }

  // Wait. A granted slot arrives with `inflight` already accounted for.
  state.stats.queue.joined += 1;
  const outcome = await waitForSlot(route, maxWaitMs);
  if (outcome !== "granted") {
    state.stats.queue.expired += 1;
    return refusal();
  }
  return grantOrShared(o, { route, capacity, cfg, win, budget, ip, nowS });
}
