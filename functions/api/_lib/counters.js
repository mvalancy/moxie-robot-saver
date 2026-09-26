/* functions/api/_lib/counters.js — the in-isolate counter state `limits.js` and
 * `sharedtier.js` share, plus the unit ledgers that feed the shared budget.
 *
 * Everything here lives in ONE isolate's memory: best-effort, not a global ceiling
 * (live-sim-demo.md §4.6; see `limits.js`'s header).
 */

/**
 * §4.1's request-unit denomination. Units, not dollars: no price sheet exists (§10
 * assumption 19), so a dollar ceiling would be an invented number. A chat turn is 3 and a
 * speech call 2, so `DEMO_UNIT_BUDGET_HOUR = 600` reads as "about 120 full turns an hour".
 */
export const UNITS = Object.freeze({ chat: 3, speech: 2, transcribe: 2 });

/** Bounded state: past this many keys a map is cleared wholesale (see `prune`). */
const MAX_KEYS = 5000;

/** Fixed epoch buckets, not sliding windows: one integer per key instead of a timestamp
 *  list. A burst straddling a boundary can briefly see 2x the nominal rate. */
export const SCALES = Object.freeze({ min: 60, hour: 3600, day: 86400 });

export function bucket(nowS, scale) {
  return Math.floor(nowS / scale);
}

/** `nowS` if the caller passed a usable one, else the wall clock in seconds. */
export function nowOr(nowS) {
  return Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
}

/** Cheapest correct eviction: clear. Every counter is a fixed window, so the worst case is
 *  that current windows restart — strictly more permissive, never silently wrong. */
export function prune(map) {
  if (map.size > MAX_KEYS) map.clear();
}

const tierStats = (ledger) => ({
  checked: 0, ops: 0, hit: 0, miss: 0, stale: 0,
  allowed: 0, refused: 0, wrote: 0, errors: 0, timeouts: 0,
  ...(ledger ? { published: 0, dropped: 0 } : {}),
});

/** Recorded facts for tests and reports — never read for a decision. */
export function freshStats() {
  return {
    admitted: 0,
    refused: 0,
    refusals: {},
    upstreamCalls: 0,
    /** Units handed back by `slot.refundBudget()` — recorded so a test can assert the
     *  refund HAPPENED, not merely that the arithmetic came out even. */
    refundedUnits: 0,
    /** joined = queued at all; granted = got a slot from a release; expired = waited out
     *  the clock; refusedFull = never queued because the depth cap was reached. */
    queue: { joined: 0, granted: 0, expired: 0, refusedFull: 0 },
    /** The Cache API tier. The FLAT fields are the per-IP MINUTE window only; `wide`,
     *  `units` and `unitsDay` are the other sub-tiers, kept apart because they fail open
     *  independently. Total round trips are the SUM of every sub-tier's `ops`. */
    cache: {
      ...tierStats(false),
      units: tierStats(true),
      wide: tierStats(false),
      unitsDay: tierStats(true),
    },
  };
}

export const state = {
  /** `ip|route|scale|bucket` -> count */
  windows: new Map(),
  /** `units|scale|bucket` -> units spent */
  budget: new Map(),
  /**
   * The shared budget's LEDGER: units this isolate has really SPENT and not yet published
   * to the colo's entry, plus the hour they were charged in. Nothing is added at admission
   * (a charge written then might need a refund, and a lost shared refund is an OVERCOUNT);
   * units land here only when a request is released un-refunded. Units from another hour
   * are DROPPED, never moved: dropping undercounts (legal), moving would bill an hour that
   * did not spend them (refuses somebody).
   */
  units: { pending: 0, bucket: -1 },
  /** The DAY twin — a separate ledger because hour and day roll on different clocks, and
   *  one ledger would drop the day's units every time the hour rolled. */
  unitsDay: { pending: 0, bucket: -1 },
  /** route -> requests in flight in THIS isolate */
  inflight: { chat: 0, speech: 0, transcribe: 0 },
  /** route -> FIFO of requests waiting for a slot. Per-isolate: a fair order within the
   *  isolate that answered, NOT a global queue position. */
  waiters: { chat: [], speech: [], transcribe: [] },
  stats: freshStats(),
};

export function windowLimits(cfg, route) {
  if (route === "chat") return { min: cfg.chatPerMin, hour: cfg.chatPerHour, day: cfg.chatPerDay };
  if (route === "speech") return { min: cfg.speechPerMin, hour: cfg.speechPerHour, day: 0 };
  return { min: cfg.sttPerMin, hour: cfg.sttPerHour, day: 0 };
}

/* ---------------------------------------------------------------------------- *
 * The ledgers. ONE invariant: a unit reaches a ledger only after its request was released
 * without a refund, and once there it can only be PUBLISHED or DROPPED. There is no
 * "taken back" across the shared store — that is the failure mode that refuses innocent
 * visitors. (`unaccrue*` only edits what has not yet left this isolate.)
 * ---------------------------------------------------------------------------- */

/** Units this isolate owes hour `b`. MUTATES ON READ: a ledger stuck on a past hour would
 *  refuse every future accrual and never publish again, so it self-heals here and the
 *  drop is recorded. */
export function pendingUnits(b) {
  const u = state.units;
  if (u.bucket !== b) {
    if (u.pending > 0) state.stats.cache.units.dropped += u.pending;
    u.bucket = b;
    u.pending = 0;
  }
  return u.pending;
}

/** `pendingUnits` for the DAY ledger (drops recorded under `cache.unitsDay`). */
export function pendingDayUnits(b) {
  const u = state.unitsDay;
  if (u.bucket !== b) {
    if (u.pending > 0) state.stats.cache.unitsDay.dropped += u.pending;
    u.bucket = b;
    u.pending = 0;
  }
  return u.pending;
}

/** Add committed spend. Called only from `release()`, so only real spend arrives here. */
export function accruePending(hourBucket, cost) {
  if (!cost) return;
  const u = state.units;
  if (u.pending === 0) u.bucket = hourBucket; // an empty ledger adopts the first charge's hour
  if (u.bucket !== hourBucket) {
    state.stats.cache.units.dropped += cost; // a straggler from another hour: dropped
    return;
  }
  u.pending += cost;
}

/** `accruePending` for the DAY ledger. */
export function accrueDayPending(dayBucket, cost) {
  if (!cost) return;
  const u = state.unitsDay;
  if (u.pending === 0) u.bucket = dayBucket;
  if (u.bucket !== dayBucket) {
    state.stats.cache.unitsDay.dropped += cost;
    return;
  }
  u.pending += cost;
}

/** Take spend back OUT of the unpublished ledger — safe only because nobody outside this
 *  isolate has seen it. Serves the `release()`-then-`refundBudget()` ordering, which no
 *  route performs today but nothing prevents. */
export function unaccruePending(hourBucket, cost) {
  const u = state.units;
  if (!cost || u.bucket !== hourBucket) return;
  u.pending = Math.max(0, u.pending - cost);
}

/** `unaccruePending` for the DAY ledger. */
export function unaccrueDayPending(dayBucket, cost) {
  const u = state.unitsDay;
  if (!cost || u.bucket !== dayBucket) return;
  u.pending = Math.max(0, u.pending - cost);
}

/** Forget the ledger after ATTEMPTING to publish it (see `sharedBudgetVerdict`). */
export function clearPending(b) {
  state.units.pending = 0;
  state.units.bucket = b;
}

/** `clearPending` for the DAY ledger. */
export function clearDayPending(b) {
  state.unitsDay.pending = 0;
  state.unitsDay.bucket = b;
}
