/* Harness for `sim/tests/helpers_shared_ceilings.mjs`: a per-SECTION ledger (the pytest
 * wrapper asserts each section's count, so a vanished section is a failure rather than a
 * smaller green number), the fake store, and one-line admissions with an explicit clock.
 */
import { api, GATEWAY, ORIGIN, post, fakeCache as baseFakeCache } from "../common.mjs";

export { ORIGIN };
export const limits = await api("_lib", "limits.js");
const env = await api("_lib", "env.js");

export const fails = [];
export const sections = {};
export const S = { current: "?", checks: 0 };

export const ok = (c, m) => {
  S.checks += 1;
  sections[S.current].checks += 1;
  if (!c) {
    fails.push(`[${S.current}] ${m}`);
    sections[S.current].failures += 1;
  }
};
export const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
export const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);
export const section = (name) => {
  S.current = name;
  if (!sections[name]) sections[name] = { checks: 0, failures: 0 };
};

export const cfgOf = (extra) => env.readConfig({ ...GATEWAY, DEMO_TTS_MODEL: "test-voice-model", ...(extra || {}) });
/** The same ceilings with the tier switched off: the CONTROL every binding claim needs. */
export const offOf = (extra) => cfgOf({ ...extra, DEMO_CACHE_COUNTER: "0" });

/** `__reset()` is the ISOLATE BOUNDARY: a new `Map` and new ledgers, same store. */
export const fresh = () => limits.__reset();

/** The fake store, with day-long seeded entries (the wide and day entries live a day). */
export const fakeCache = (opts) => baseFakeCache({ seedMaxAge: 86400, ...(opts || {}) });

/** One admission straight at `admit()`. `cache: null` is "no cache here", not "omitted". */
export const admitWith = (cfg, cache, ip, nowS, route) =>
  limits.admit({
    request: post("/api/" + (route || "chat"), { text: "x" }, { "CF-Connecting-IP": ip }),
    cfg,
    route: route || "chat",
    cache,
    nowS,
  });

export const st = () => limits.__state();
export const wide = () => st().stats.cache.wide;
export const unitsDay = () => st().stats.cache.unitsDay;

/* Buckets spelled out, not derived, so a wrong bucket is a wrong NUMBER.
 *   T0 = 7200  -> minute 120, hour 2, day 0;   T1 = 93600 -> hour 26, day 1 (the roll). */
export const T0 = 7200;
export const T1 = 93600;
export const DAY0 = 0;
export const DK = ORIGIN + "/__moxie/rl/units/d0";
