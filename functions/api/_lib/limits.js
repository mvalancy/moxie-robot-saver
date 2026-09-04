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
 *
 * AND THE CONCURRENCY STEP CAN NOW WAIT (2026-09-03). At the ceiling a request joins a
 * bounded FIFO for up to `DEMO_QUEUE_MAX_WAIT_MS` instead of being refused on the spot, so
 * the ten-or-so visitors the hosted demo is sized for get a slightly slower turn rather
 * than a scripted line. `admit()` is therefore `async` and all three routes await it. The
 * ceiling itself is deliberately NOT raised: it is matched to the upstream key's parallel
 * limit, which protects a neighbour on the same gateway. **The FIFO is per-isolate like
 * every counter above it** — see the queue section further down, which says exactly what
 * that order does and does not promise.
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
  /** route -> the FIFO of requests waiting for a concurrency slot. **PER-ISOLATE, like
   *  every other counter in this file** (see the header, and live-sim-demo.md §4.6): a
   *  visitor queued in one isolate is invisible to every other one, so this is a fair
   *  order *within the isolate that answered* and NOT a global queue position. Nothing in
   *  this module may be described as promising more than that. */
  waiters: { chat: [], speech: [], transcribe: [] },
  /** Recorded, for tests and for the report — never for a decision. */
  stats: {
    admitted: 0,
    refused: 0,
    refusals: {},
    upstreamCalls: 0,
    /** joined = queued at all; granted = got a slot from a release; expired = waited out
     *  the clock; refusedFull = never queued because the depth cap was already reached. */
    queue: { joined: 0, granted: 0, expired: 0, refusedFull: 0 },
  },
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

/** Reset every counter. Tests only — a route must never call this.
 *
 *  Every waiter is EXPIRED rather than dropped. A dropped waiter is a promise nobody ever
 *  settles, which under `node` is a pending timer and a test that hangs instead of
 *  failing — the worst way for this file to be wrong. */
export function __reset() {
  state.windows.clear();
  state.budget.clear();
  for (const route of Object.keys(state.waiters)) expireAll(state.waiters[route]);
  state.inflight = { chat: 0, speech: 0, transcribe: 0 };
  state.waiters = { chat: [], speech: [], transcribe: [] };
  state.stats = {
    admitted: 0,
    refused: 0,
    refusals: {},
    upstreamCalls: 0,
    queue: { joined: 0, granted: 0, expired: 0, refusedFull: 0 },
  };
}

/** A snapshot of what the counters RECORDED. Tests assert on this rather than on timing
 *  (playbook rule 11). Carries no IP: the keys are hashed into the map, not exported. */
export function __state() {
  return {
    inflight: { ...state.inflight },
    /** route -> how many are waiting for a slot right now, in THIS isolate. */
    waiting: {
      chat: state.waiters.chat.length,
      speech: state.waiters.speech.length,
      transcribe: state.waiters.transcribe.length,
    },
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

/** The `X-RateLimit-*` triple for this IP RIGHT NOW, charging nothing. Factored out of
 *  `chargeWindows` because a refunded request must be able to re-read it: telling a
 *  visitor `remaining: 3` when the unit that made it 3 has just been given back would be
 *  a header that contradicts the counter it claims to describe. */
function rateLimitSnapshot(ip, route, cfg, nowS) {
  const limits = windowLimits(cfg, route);
  const minKey = ip + "|" + route + "|min|" + bucket(nowS, SCALES.min);
  return {
    limit: limits.min,
    remaining: Math.max(0, limits.min - (state.windows.get(minKey) || 0)),
    reset: (bucket(nowS, SCALES.min) + 1) * SCALES.min,
  };
}

/** Peek at (and then charge) one request against every configured window for this IP.
 *
 *  `charged` is the exact list of keys that were incremented, so `refundCharges()` can
 *  put back precisely what this call took and nothing else. The keys embed their bucket,
 *  which is what makes a refund safe across a window boundary: if the minute rolled over
 *  while a request sat in the queue, the refund lands on the *past* window it actually
 *  charged and cannot credit the current one. */
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
      return { ok: false, reason: "budget_exhausted", retryAfterS: Math.max(1, resetAt - nowS), charged: [], cost };
    }
    touched.push([key, used + cost]);
  }
  for (const [key, next] of touched) state.budget.set(key, next);
  prune(state.budget);
  return { ok: true, reason: null, retryAfterS: 0, charged: touched.map(([key]) => key), cost };
}

/**
 * Give back exactly what `chargeWindows` and `chargeBudget` took.
 *
 * ============================================================================
 * THE DECISION THIS FUNCTION IMPLEMENTS, AND WHY IT WAS TAKEN THIS WAY.
 *
 * `admit()`'s ordering charges the per-IP window and the unit budget BEFORE the
 * concurrency slot is taken. That was safe for as long as the capacity check could only
 * succeed or refuse instantly. **The queue breaks it**: a visitor who waits and then times
 * out has spent a rate-limit unit and a budget unit on a turn they never received. At
 * `chat_per_min: 5`, timing out twice burns 40 % of their minute on nothing. That is a
 * user-visible unfairness, not a theoretical one.
 *
 * There were exactly two fixes, and they are not equally good.
 *
 *   (a) REFUND on the two paths that queue-and-fail. Chosen.
 *   (b) REORDER, so the wait happens before the charge. Rejected.
 *
 * (b) was rejected because it inverts the one invariant this file's header calls the
 * point: *every cheap, free refusal happens before any expensive one*. Waiting is not
 * free — a queue slot is a scarce, bounded resource — so under (b) a request that today is
 * refused instantly and for nothing (the 6th chat turn in a minute from one IP) would
 * first occupy a queue slot for up to `DEMO_QUEUE_MAX_WAIT_MS`, displacing a legitimate
 * visitor, before being charged and refused anyway. A script would then be able to fill
 * the whole queue with requests it never had the rate-limit budget to make. That is a new
 * abuse channel introduced to fix a fairness bug, which is a bad trade.
 *
 * (a) keeps the documented order exactly as it is and makes the accounting true after the
 * fact instead. Its own cost, stated rather than hidden: while a request waits, its charge
 * is held, so a *concurrent* request can be refused `rate_limited` or `budget_exhausted`
 * on a unit that is about to be given back. That transient over-count is bounded by
 * `DEMO_QUEUE_MAX_DEPTH` x the route's unit cost — at the defaults, 8 x 3 = 24 units
 * against a 600-unit hourly budget, and at most 8 window units spread across whichever IPs
 * are actually queued. Bounded, small, and in the conservative direction.
 * ============================================================================
 *
 * Refunds are precise, never generous: a key that is absent (the window rolled over and
 * `prune()` cleared the map, say) is skipped rather than driven negative, and a key whose
 * count would reach zero is deleted rather than left as a zero entry that keeps the map
 * growing. It is called at most ONCE per admission, on a path that has already decided to
 * refuse, so there is no double-refund to guard against.
 */
function refundCharges(windowKeys, budgetKeys, cost) {
  for (const key of windowKeys || []) {
    const used = state.windows.get(key);
    if (used === undefined) continue; // pruned or never charged: there is nothing to give back
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
  // `transcribe` deliberately SHARES the chat ceiling rather than getting one of its own.
  // §4.1 gives concurrency numbers for chat and speech only, and inventing a third would
  // be a number with no reasoning behind it — while an STT call at ~2.5-2.8 s
  // (`docs/guides/litellm-stt-setup.md`:83) is squarely in chat's cost bracket and is the
  // FIRST leg of a chat turn anyway, so bounding both at 4 bounds the pipeline once.
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

/**
 * Read a RAW AUDIO request body, bounded at both ends, without throwing.
 *
 * This is `/api/transcribe`'s body reader (P1). It is the JSON reader's sibling and lives
 * beside it for the same reason: a route must not be able to invent its own idea of "how
 * big is too big".
 *
 * BOTH CAPS ARE COST CONTROLS, AND THEY ARE NOT THE SAME KIND OF CONTROL.
 *
 *   * `DEMO_MAX_AUDIO_BYTES` (500 000) bounds ONE upload. The declared `Content-Length` is
 *     checked FIRST, so an oversized upload is refused WITHOUT the body ever being read.
 *     The real byte count is then checked too, because a chunked body can exceed what it
 *     declared.
 *   * `DEMO_MIN_AUDIO_BYTES` (2 000) is the floor that mirrors `mqtt/moxie_sdk/stt.py`'s
 *     `MIN_MS = 120` — **"no audio → no request, no cost, no latency"** (:194-197, :237-244).
 *     A blip too short to be speech must not become a paid ~2.5 s round trip that comes
 *     back empty.
 *
 * And the honest limit of the byte cap, restated because §4.1 makes a point of it: **500 KB
 * is not 15 seconds of a compressed container.** It is ~15 s of 16 kHz PCM but MINUTES of
 * webm/Opus, which is what a browser's `MediaRecorder` actually produces. The duration
 * ceiling is `DEMO_MAX_RECORD_MS`, enforced in `sim/web/mic.js`, because a Function only
 * ever sees the finished upload.
 *
 * @returns {{ok: boolean, reason: string|null, bytes: Uint8Array|null, declared: number}}
 */
export async function readAudioBody(request, cfg) {
  const max = cfg.maxAudioBytes;
  const declared = Number(request.headers.get("Content-Length"));
  // Refused UNREAD. The point of trusting the declared length here is that it lets us say
  // no before spending memory; the real count below is what makes the answer sound.
  if (Number.isFinite(declared) && declared > max) {
    return { ok: false, reason: "too_long", bytes: null, declared };
  }
  let buf;
  try {
    buf = await request.arrayBuffer();
  } catch {
    return { ok: false, reason: "bad_request", bytes: null, declared: 0 };
  }
  const bytes = new Uint8Array(buf);
  if (bytes.length > max) return { ok: false, reason: "too_long", bytes: null, declared: bytes.length };
  // THE NO-CALL FLOOR. Below it the route returns here and the gateway is never touched.
  if (bytes.length < cfg.minAudioBytes) {
    return { ok: false, reason: "too_short", bytes: null, declared: bytes.length };
  }
  return { ok: true, reason: null, bytes, declared: bytes.length };
}

/* ---------------------------------------------------------------------------- *
 * The one admission function
 * ---------------------------------------------------------------------------- */

function refuse(reason, extra) {
  state.stats.refused += 1;
  state.stats.refusals[reason] = (state.stats.refusals[reason] || 0) + 1;
  return { ok: false, reason, retryAfterS: 0, rateLimit: null, release: () => {}, ...extra };
}

/* ---------------------------------------------------------------------------- *
 * The admission queue — a bounded FIFO behind the concurrency ceiling
 * ---------------------------------------------------------------------------- *
 *
 * WHY A QUEUE AND NOT A BIGGER CEILING. See `_lib/env.js`'s note on
 * `DEMO_QUEUE_MAX_WAIT_MS`: `DEMO_MAX_CONCURRENT_CHAT` is matched to the upstream key's
 * parallel-request limit, which protects a neighbour sharing the same self-hosted
 * gateway. Raising it would move the refusal upstream, not remove it. Four slots at ~1.2 s
 * a turn are already ~3 turns/second, which is far more than ten conversational visitors
 * need; what breaks is the momentary collision, and a short bounded wait absorbs exactly
 * that.
 *
 * **PER-ISOLATE, LIKE EVERYTHING ELSE IN THIS FILE.** The FIFO lives in one isolate's
 * memory (see the header and live-sim-demo.md §4.6). It is a fair order *among the
 * requests this isolate is holding* and it is NOT a global queue position: two visitors
 * served by two isolates are ordered by neither of them, and no code or copy may imply
 * otherwise. The Cache API tier that months of the spec claimed for the counters never
 * existed; this queue does not invent one either.
 *
 * WHAT MAKES THE ORDER FAIR, MECHANICALLY. `release()` does not decrement `inflight` when
 * anyone is waiting — it HANDS THE SLOT OVER, leaving the count unchanged, and resolves
 * the longest-waiting request. Two consequences fall out of that one rule:
 *
 *   * No slot is ever momentarily free, so a request arriving during the microtask gap
 *     between the release and the waiter resuming cannot jump the queue. FIFO is a
 *     property of the counter, not of scheduling luck.
 *   * No slot can be double-issued or leaked: the count is conserved on hand-over and
 *     decremented exactly once, by whichever release finds the queue empty.
 *
 * `admit()` is `async` only for the wait. Everything that DECIDES — the origin pin, the
 * windows, the budget, the capacity comparison and the increment — runs synchronously
 * before the first `await`, so two concurrent admissions can never interleave inside the
 * decision and see the same free slot.
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

/** Join the FIFO and resolve with `"granted"` or `"expired"`. Never rejects: a rejection
 *  here would have to be caught by three routes and turned back into a refusal anyway. */
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

/** Give the slot to the longest-waiting request, or put it back if nobody wants it.
 *  THE COUNT IS CONSERVED on a hand-over — see the section header for why that is what
 *  makes the order fair rather than merely likely. */
function handOffOrRelease(route) {
  const waiting = state.waiters[route];
  while (waiting && waiting.length) {
    const w = waiting.shift();
    if (w.settled) continue; // already timed out; it is not holding anything
    w.settled = true;
    if (w.timer !== null) clearTimeout(w.timer);
    state.stats.queue.granted += 1;
    w.resolve("granted");
    return; // inflight deliberately UNCHANGED: the slot moved, it did not free
  }
  state.inflight[route] = Math.max(0, (state.inflight[route] || 0) - 1);
}

/** Build the granted-slot result. `inflight` has ALREADY been accounted for by the
 *  caller — either incremented on the fast path, or handed over by `handOffOrRelease` —
 *  so this function only wraps it in the idempotent `release()`. */
function grantedSlot(route, capacity, rateLimit) {
  state.stats.admitted += 1;
  let released = false;
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit,
    load: { inflight: state.inflight[route], capacity },
    release() {
      if (released) return; // idempotent: a double release would under-count for ever
      released = true;
      handOffOrRelease(route);
    },
  };
}

/**
 * Origin -> per-IP windows -> unit budget -> concurrency, in that order, once.
 *
 * ORDERING IS THE POINT. Every cheap, free refusal happens before any expensive one, and
 * the concurrency slot — the only thing that must be given back — is taken LAST, so no
 * refusal path can leak one. The caller must call `release()` in a `finally`.
 *
 * **`async` SINCE 2026-09-03, FOR THE QUEUE AND FOR NOTHING ELSE.** At capacity the
 * request joins a bounded FIFO instead of being refused outright (see the queue section
 * above for the mechanics and for what "fair" does and does not mean here). Everything
 * that decides still runs synchronously before the first `await`; the fast path — a free
 * slot — awaits nothing at all and is exactly the code that was here before. All three
 * routes (`chat.js`, `speech.js`, `transcribe.js`) `await` it and keep `release()` in a
 * `finally`. **`/api/health` does not call this function and stays non-`async`:** a probe
 * that cannot await cannot call upstream, and `sim/test_mode.mjs` asserts that
 * structurally.
 *
 * The two refusals that can now happen AFTER the windows and the budget were charged —
 * the depth cap, and the wait expiring — refund what they charged. `refundCharges()`
 * carries the whole argument for refunding rather than reordering.
 *
 * @param {{request: Request, cfg: object, route: "chat"|"speech"|"transcribe", nowS?: number}} o
 * @returns {Promise<{ok: boolean, reason: string|null, retryAfterS: number,
 *            rateLimit: {limit:number,remaining:number,reset:number}|null,
 *            load: {inflight:number,capacity:number}, release: () => void}>}
 */
export async function admit(o) {
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
  const waiting = state.waiters[route];

  // ---- The fast path, unchanged: a free slot and nobody ahead of us. The
  // `waiting.length === 0` half is belt to the hand-over's braces — because `release()`
  // conserves the count while anyone waits, `inflight` cannot drop below `capacity` with a
  // non-empty FIFO — but stating it here means a LATE ARRIVAL CANNOT OVERTAKE even if some
  // future edit breaks that invariant.
  if (waiting.length === 0 && (state.inflight[route] || 0) < capacity) {
    state.inflight[route] = (state.inflight[route] || 0) + 1;
    return grantedSlot(route, capacity, win.rateLimit);
  }

  // ---- At capacity. Refuse immediately when the queue is switched off (either value 0 —
  // the documented escape hatch back to the pre-queue behaviour) or already at its depth.
  // **A QUEUE WITHOUT A DEPTH CAP IS JUST A SLOWER WAY TO FALL OVER**: past the cap the
  // honest answer is the same `at_capacity` this route has always given.
  const maxWaitMs = Number.isFinite(cfg.queueMaxWaitMs) ? cfg.queueMaxWaitMs : 0;
  const maxDepth = Number.isFinite(cfg.queueMaxDepth) ? cfg.queueMaxDepth : 0;
  const refusal = () => {
    // Refund FIRST, then read the rate-limit headers back, so the numbers the visitor is
    // sent describe the counter as it stands after the refund rather than before it.
    refundCharges(win.charged, budget.charged, budget.cost);
    return {
      ...refuse("at_capacity", { rateLimit: rateLimitSnapshot(ip, route, cfg, nowS) }),
      // `retryAfterS` stays 0 so `envelope.js`'s §4.5 table supplies `Retry-After: 15`.
      // That IS the honest number: a request refused here has either found a full queue or
      // already waited `DEMO_QUEUE_MAX_WAIT_MS` without a slot opening, so the ceiling is
      // genuinely saturated beyond what the queue can absorb. Telling it to come back in
      // 2.5 s would just put it straight back into the FIFO, ahead of nobody and at the
      // cost of everybody already in it.
      load: { inflight: state.inflight[route] || 0, capacity },
    };
  };
  if (maxWaitMs <= 0 || maxDepth <= 0 || waiting.length >= maxDepth) {
    state.stats.queue.refusedFull += 1;
    return refusal();
  }

  // ---- Wait. The slot, if one comes, is handed over by `release()` with `inflight`
  // already accounted for, so nothing is incremented here.
  state.stats.queue.joined += 1;
  const outcome = await waitForSlot(route, maxWaitMs);
  if (outcome !== "granted") {
    state.stats.queue.expired += 1;
    return refusal();
  }
  return grantedSlot(route, capacity, win.rateLimit);
}
