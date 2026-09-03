/* functions/api/_lib/limits.js — request admission: the origin pin, the per-IP windows,
 * the concurrency ceiling and the unit budget.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (every cap and its starting
 * number), §4.3 (how the origin is pinned, and what that is worth), §4.5 (the status and
 * `Retry-After` table), §4.6 ('Counters, honestly'), §7 (the capacity signal).
 *
 * ============================================================================
 * READ THIS BEFORE TRUSTING A NUMBER IN THIS FILE.
 *
 * **THE P0 COUNTERS ARE BEST-EFFORT AND IN-PROCESS. THEY ARE NOT A GLOBAL CEILING.**
 *
 * A Cloudflare Worker isolate is not a shared counter. The state below lives in one
 * isolate's memory: Cloudflare runs many isolates, in many colos, and recycles them
 * freely, so two visitors — or the same visitor on two requests — may be counted by two
 * different maps that never see each other. An attacker who can reach several colos gets
 * several budgets. §4.6 says this out loud and this comment is the code half of that
 * promise.
 *
 * What these counters ARE good for, which is most of the real risk: they stop scripts and
 * accidents. A loop hammering one endpoint from one machine hits one isolate and is
 * refused.
 *
 * What actually bounds the worst case, and where the real ceilings live:
 *   1. A **budget-scoped virtual key** on the gateway itself (§4.2's deployment
 *      requirement, §10 assumption 14). If the gateway can mint a key with a hard budget
 *      and RPM/TPM limits, that is the cheapest and strongest control in the whole
 *      document, and everything in this file becomes defence in depth.
 *   2. The **per-request caps** of §4.1 — `max_tokens`, `DEMO_MAX_INPUT_CHARS`,
 *      `DEMO_MAX_TTS_CHARS`, the timeouts — which bound the cost of every INDIVIDUAL
 *      request no matter how many arrive, and are enforced in `chat.js`/`speech.js`
 *      regardless of what any counter here says.
 *   3. The **ticket** (`./hmac.js`), which makes `/api/speech` structurally incapable of
 *      synthesizing text we did not write, so the expensive route cannot be driven at all
 *      without first paying for a chat turn.
 *
 * P1 replaces the counters with a KV or Durable Object single-writer counter — AFTER the
 * dashboard confirms which of those this account and plan actually has (§10 assumption
 * 13). Until then, do not write a doc sentence that calls these a hard limit.
 * ============================================================================
 *
 * WHY THE ORIGIN PIN IS IN THIS FILE. It is request admission, in the same family as the
 * windows and the ceiling: a cheap decision taken before any money is spent, and one that
 * chat.js and speech.js must not be able to get in a different order. `admit()` is one
 * function that does origin -> per-IP -> budget -> concurrency, in that order, so neither
 * route can accidentally spend a unit before checking the pin.
 */

/**
 * §4.1's request-unit denomination. **Units, not dollars, and the reason is in the spec:**
 * no price sheet exists anywhere in the repo (§10 assumption 19) — only latency and byte
 * sizes are recorded — so a dollar-denominated ceiling would be an invented number
 * masquerading as an accounting fact. A chat turn is 3 units and a speech call 2, so a
 * full turn is 5 and `DEMO_UNIT_BUDGET_HOUR = 600` reads as "about 120 full turns an
 * hour". When someone supplies real prices, this table is the one place to re-denominate.
 */
export const UNITS = Object.freeze({ chat: 3, speech: 2, transcribe: 2 });

/** Bounded state: an isolate that lived for days must not grow a map per visitor. When a
 *  bucket map passes this many keys the oldest window's keys are dropped wholesale. */
const MAX_KEYS = 5000;

const state = {
  /** `ip|route|scale|bucket` -> count */
  windows: new Map(),
  /** `route|scale|bucket` -> units spent */
  budget: new Map(),
  /** route -> requests currently in flight in THIS isolate */
  inflight: { chat: 0, speech: 0, transcribe: 0 },
  /** Recorded, for tests and for the report — never for a decision. */
  stats: { admitted: 0, refused: 0, refusals: {}, upstreamCalls: 0 },
};

/** The three window scales, in seconds. Fixed epoch buckets, not sliding windows: a
 *  sliding window needs a timestamp list per key, and a fixed bucket needs one integer.
 *  The trade is that a burst straddling a boundary can see up to 2x the nominal rate for
 *  one instant, which for a demo's 5/min is not the risk worth the memory. */
const SCALES = Object.freeze({ min: 60, hour: 3600, day: 86400 });

function bucket(nowS, scale) {
  return Math.floor(nowS / scale);
}

function prune(map) {
  if (map.size <= MAX_KEYS) return;
  // Cheapest correct eviction: clear it. Every counter is a fixed-window count, so the
  // worst consequence is that the current windows restart — a strictly more permissive
  // outcome that only happens after 5000 distinct keys, and never silently wrong.
  map.clear();
}

/** Reset every counter. Tests only — a route must never call this. */
export function __reset() {
  state.windows.clear();
  state.budget.clear();
  state.inflight = { chat: 0, speech: 0, transcribe: 0 };
  state.stats = { admitted: 0, refused: 0, refusals: {}, upstreamCalls: 0 };
}

/** A snapshot of what the counters RECORDED. Tests assert on this rather than on timing
 *  (playbook rule 11). Carries no IP: the keys are hashed into the map, not exported. */
export function __state() {
  return {
    inflight: { ...state.inflight },
    windows: state.windows.size,
    budget: Object.fromEntries(state.budget),
    stats: JSON.parse(JSON.stringify(state.stats)),
  };
}

/** Called by the routes immediately before a `fetch()` to the gateway, so a test can
 *  assert **zero upstream calls** on every refusal path without stubbing anything. */
export function noteUpstreamCall() {
  state.stats.upstreamCalls += 1;
}

/* ---------------------------------------------------------------------------- *
 * Who is asking
 * ---------------------------------------------------------------------------- */

/**
 * The visitor's IP, keyed on `CF-Connecting-IP` (§4.1) — the header Cloudflare itself
 * sets and a client cannot forge through Cloudflare. `X-Forwarded-For`'s first hop is
 * read only as a fallback for a local `wrangler pages dev`, where there is no Cloudflare
 * in front. An absent IP is keyed as `unknown` and therefore SHARES one bucket with every
 * other anonymous caller, which is the conservative direction.
 */
export function clientIp(request) {
  const h = request && request.headers;
  if (!h) return "unknown";
  const cf = h.get("CF-Connecting-IP");
  if (cf) return String(cf).trim();
  const xff = h.get("X-Forwarded-For");
  if (xff) return String(xff).split(",")[0].trim() || "unknown";
  return "unknown";
}

/* ---------------------------------------------------------------------------- *
 * §4.3 — the origin pin
 * ---------------------------------------------------------------------------- */

/**
 * Pin the request to this deployment's own origin.
 *
 * **STATED PLAINLY, AS §4.3 REQUIRES: THIS STOPS BROWSER HOTLINKING ONLY. `curl` FORGES
 * THESE HEADERS TRIVIALLY.** It is a cheap first filter that sits under the caps, the
 * budget and the gateway-side key budget — never a control to rely on. Bot detection
 * (Turnstile) is P1. Nothing in this function is load-bearing for cost: everything that
 * bounds cost is enforced whether the pin passes or not.
 *
 * The default allowlist is the request's OWN origin, which is what lets a fork on any
 * domain work with zero origin configuration (C3), and why nothing in this repo needs to
 * know a deployment hostname.
 *
 * `Sec-Fetch-Site` is required to be `same-origin` WHEN PRESENT. Absent (curl, an old
 * browser) is not itself a rejection — but absent *plus* a missing or mismatched `Origin`
 * is, which is the rule §4.3 states.
 */
export function checkOrigin(request, cfg) {
  let self;
  try {
    self = new URL(request.url).origin;
  } catch {
    return { ok: false, reason: "forbidden_origin" };
  }
  const allowed = [self, ...(cfg.allowedOrigins || [])];
  const h = request.headers;
  const site = h.get("Sec-Fetch-Site");
  if (site && site !== "same-origin") return { ok: false, reason: "forbidden_origin" };

  let origin = h.get("Origin") || "";
  if (!origin) {
    const ref = h.get("Referer");
    if (ref) {
      try {
        origin = new URL(ref).origin;
      } catch {
        origin = "";
      }
    }
  }
  if (!origin) {
    // No Origin and no Referer. A same-origin `fetch()` from our own page always sends
    // `Origin` on a POST, so this is a non-browser caller. Allowed only when the browser
    // fetch metadata explicitly vouched for it.
    return site === "same-origin" ? { ok: true, reason: null } : { ok: false, reason: "forbidden_origin" };
  }
  return allowed.includes(origin) ? { ok: true, reason: null } : { ok: false, reason: "forbidden_origin" };
}

/* ---------------------------------------------------------------------------- *
 * §4.1 — the per-IP windows
 * ---------------------------------------------------------------------------- */

function windowLimits(cfg, route) {
  if (route === "chat") return { min: cfg.chatPerMin, hour: cfg.chatPerHour, day: cfg.chatPerDay };
  if (route === "speech") return { min: cfg.speechPerMin, hour: cfg.speechPerHour, day: 0 };
  return { min: cfg.sttPerMin, hour: cfg.sttPerHour, day: 0 };
}

/** Peek at (and then charge) one request against every configured window for this IP. */
function chargeWindows(ip, route, cfg, nowS) {
  const limits = windowLimits(cfg, route);
  const touched = [];
  for (const [name, scale] of Object.entries(SCALES)) {
    const limit = limits[name];
    if (!limit) continue; // 0 / undefined => this scale is not capped for this route
    const key = ip + "|" + route + "|" + name + "|" + bucket(nowS, scale);
    const used = state.windows.get(key) || 0;
    if (used >= limit) {
      const resetAt = (bucket(nowS, scale) + 1) * scale;
      return {
        ok: false,
        reason: "rate_limited",
        retryAfterS: Math.max(1, resetAt - nowS),
        rateLimit: { limit: limits.min, remaining: 0, reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min },
      };
    }
    touched.push([key, used + 1]);
  }
  for (const [key, next] of touched) state.windows.set(key, next);
  prune(state.windows);
  const minKey = ip + "|" + route + "|min|" + bucket(nowS, SCALES.min);
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit: {
      limit: limits.min,
      remaining: Math.max(0, limits.min - (state.windows.get(minKey) || 0)),
      reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min,
    },
  };
}

/* ---------------------------------------------------------------------------- *
 * §4.1 — the unit budget
 * ---------------------------------------------------------------------------- */

function chargeBudget(route, cfg, nowS) {
  const cost = UNITS[route] || 1;
  const ceilings = { hour: cfg.unitBudgetHour, day: cfg.unitBudgetDay };
  const touched = [];
  for (const [name, ceiling] of Object.entries(ceilings)) {
    if (!ceiling) continue; // 0 => uncapped at this scale
    const scale = SCALES[name];
    const key = "units|" + name + "|" + bucket(nowS, scale);
    const used = state.budget.get(key) || 0;
    if (used + cost > ceiling) {
      const resetAt = (bucket(nowS, scale) + 1) * scale;
      return { ok: false, reason: "budget_exhausted", retryAfterS: Math.max(1, resetAt - nowS) };
    }
    touched.push([key, used + cost]);
  }
  for (const [key, next] of touched) state.budget.set(key, next);
  prune(state.budget);
  return { ok: true, reason: null, retryAfterS: 0 };
}

/** Force the budget to its ceiling. Tests only — acceptance criterion A6 needs a way to
 *  reach `budget_exhausted` without making 200 real calls. */
export function __exhaustBudget(cfg, nowS) {
  const now = Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
  if (cfg.unitBudgetHour) state.budget.set("units|hour|" + bucket(now, SCALES.hour), cfg.unitBudgetHour);
  if (cfg.unitBudgetDay) state.budget.set("units|day|" + bucket(now, SCALES.day), cfg.unitBudgetDay);
}

/** The budget answer with nothing charged — for a probe that must spend nothing. */
export function budgetState(cfg, nowS) {
  const now = Number.isFinite(Number(nowS)) ? Number(nowS) : Math.floor(Date.now() / 1000);
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
  return cfg.maxConcurrentChat;
}

/** `{inflight, capacity}` for the envelope. `inflight` is THIS isolate's count — see the
 *  header: it is an honest number about an incomplete view, which is why §7's copy is
 *  human ("a few other people") rather than a gauge. */
export function loadOf(cfg, route) {
  return { inflight: state.inflight[route] || 0, capacity: capacityOf(cfg, route) };
}

/* ---------------------------------------------------------------------------- *
 * Reading a request body
 * ---------------------------------------------------------------------------- */

/**
 * The body-size ceiling, DERIVED from the caps so an override scales it: the largest
 * legitimate body is one utterance plus one context blob, and base64 plus JSON escaping
 * costs under 3x. Everything above it is refused unread.
 */
export function maxJsonBodyBytes(cfg) {
  return 4096 + 3 * (cfg.maxInputChars + cfg.maxContextChars);
}

/**
 * Read a JSON request body, bounded, without throwing.
 * @returns {{ok: boolean, reason: string|null, body: object}}
 */
export async function readJsonBody(request, cfg) {
  const max = maxJsonBodyBytes(cfg);
  const declared = Number(request.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > max) return { ok: false, reason: "too_long", body: {} };
  let text;
  try {
    text = await request.text();
  } catch {
    return { ok: false, reason: "bad_request", body: {} };
  }
  // A chunked body can exceed the declared length, so the real bytes are checked too.
  if (text.length > max) return { ok: false, reason: "too_long", body: {} };
  if (!text.trim()) return { ok: true, reason: null, body: {} };
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return { ok: false, reason: "bad_request", body: {} };
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, reason: "bad_request", body: {} };
  return { ok: true, reason: null, body };
}

/* ---------------------------------------------------------------------------- *
 * The one admission function
 * ---------------------------------------------------------------------------- */

function refuse(reason, extra) {
  state.stats.refused += 1;
  state.stats.refusals[reason] = (state.stats.refusals[reason] || 0) + 1;
  return { ok: false, reason, retryAfterS: 0, rateLimit: null, release: () => {}, ...extra };
}

/**
 * Origin -> per-IP windows -> unit budget -> concurrency, in that order, once.
 *
 * ORDERING IS THE POINT. Every cheap, free refusal happens before any expensive one, and
 * the concurrency slot — the only thing that must be given back — is taken LAST, so no
 * refusal path can leak one. The caller must call `release()` in a `finally`.
 *
 * @param {{request: Request, cfg: object, route: "chat"|"speech"|"transcribe", nowS?: number}} o
 * @returns {{ok: boolean, reason: string|null, retryAfterS: number,
 *            rateLimit: {limit:number,remaining:number,reset:number}|null,
 *            load: {inflight:number,capacity:number}, release: () => void}}
 */
export function admit(o) {
  const { request, cfg, route } = o;
  const nowS = Number.isFinite(Number(o.nowS)) ? Number(o.nowS) : Math.floor(Date.now() / 1000);
  const load = loadOf(cfg, route);

  const origin = checkOrigin(request, cfg);
  if (!origin.ok) return { ...refuse("forbidden_origin"), load };

  const ip = clientIp(request);
  const win = chargeWindows(ip, route, cfg, nowS);
  if (!win.ok) {
    return { ...refuse("rate_limited", { retryAfterS: win.retryAfterS, rateLimit: win.rateLimit }), load };
  }

  const budget = chargeBudget(route, cfg, nowS);
  if (!budget.ok) {
    return { ...refuse("budget_exhausted", { retryAfterS: budget.retryAfterS, rateLimit: win.rateLimit }), load };
  }

  const capacity = capacityOf(cfg, route);
  if ((state.inflight[route] || 0) >= capacity) {
    return {
      ...refuse("at_capacity", { rateLimit: win.rateLimit }),
      load: { inflight: state.inflight[route] || 0, capacity },
    };
  }

  state.inflight[route] = (state.inflight[route] || 0) + 1;
  state.stats.admitted += 1;
  let released = false;
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit: win.rateLimit,
    load: { inflight: state.inflight[route], capacity },
    release() {
      if (released) return; // idempotent: a double release would under-count for ever
      released = true;
      state.inflight[route] = Math.max(0, (state.inflight[route] || 0) - 1);
    },
  };
}
