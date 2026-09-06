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
 * **THE P0 COUNTERS ARE BEST-EFFORT. THEY ARE NOT A GLOBAL CEILING.**
 *
 * A Cloudflare Worker isolate is not a shared counter. The state below lives in one
 * isolate's memory: Cloudflare runs many isolates, in many colos, and recycles them
 * freely, so two visitors — or the same visitor on two requests — may be counted by two
 * different maps that never see each other. An attacker who can reach several colos gets
 * several budgets. §4.6 says this out loud and this comment is the code half of that
 * promise.
 *
 * **SINCE 2026-09-05 TWO OF THOSE COUNTS ARE ALSO KEPT IN THE CACHE API: the per-IP
 * MINUTE window, and the UNIT BUDGET's hour.** Both tiers are described in full further
 * down, at `sharedWindowVerdict` and `sharedBudgetVerdict`, and the three facts that
 * matter are these. They are **per-colo**, so they remove the *isolate* multiplier
 * (measured >= 7) and leave the *colo* one. **A burst defeats them** — 31 concurrent
 * increments stored 9 — while a paced sustained drain, which is the traffic a counter
 * actually exists to stop, lost 0 of 41. And **every one of their errors is an
 * undercount**, so they can only ever let someone through, never wrongly refuse them.
 *
 * The unit budget reaches that third property by a different route from the window, and
 * the difference is the interesting part of this file: `slot.refundBudget()` means a
 * shared charge would sometimes have to be UN-charged, and a lost un-charge is an
 * overcount, which refuses innocent visitors. So the shared budget is never told about a
 * charge until the request has been released without a refund — the isolate holds its
 * unpublished spend in `state.units` and publishes it on the next admission. There is no
 * refund write anywhere on the shared tier, which is why there is no lost refund to fear.
 *
 * The in-isolate `Map` below is unchanged and still decides FIRST: the cache
 * tier only ever ADDS refusals, and with `DEMO_CACHE_COUNTER=0` — or on any runtime with
 * no `caches.default` — `admit()` is exactly the function it was before it existed.
 *
 * **So: still not a global ceiling, and no sentence anywhere may call it one.** The wrong
 * version of that sentence has already been written once about this file (ledger row 25:
 * §4.6 described a Cache API tier for months while the code had none, and the spend risk
 * was sized off it in the unsafe direction). Do not write the second one.
 *
 * What these counters ARE good for, which is most of the real risk: they stop scripts and
 * accidents. A loop hammering one endpoint from one machine hits one isolate and is
 * refused — and now, in the colo that answered it, is refused even when the platform
 * spreads it across several isolates, and its SPEND is counted against one hourly budget
 * rather than against one budget per isolate.
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
 * 13, whose *runtime* half is now measured and whose *dashboard* half is not). A Durable
 * Object is still the only candidate that gives a TRUE single-writer count; the Cache API
 * tier does not retire that plan and does not weaken the sentence above it. Until then,
 * do not write a doc sentence that calls any of this a hard limit.
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

/** The keyed one-way tag for the cross-isolate tier's cache key — see that section, and
 *  `./hmac.js::keyedTag` for why the visitor's address may not appear in a URL unkeyed. */
import { COUNTER_INFO, keyedTag } from "./hmac.js";

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
  /**
   * THE SHARED BUDGET TIER'S LEDGER — units this isolate has really SPENT and has not yet
   * published to the per-colo entry, plus the hour bucket they were charged in.
   *
   * It exists because a shared budget counter and a REFUND cannot both be safe, and the
   * long argument for that is at `sharedBudgetVerdict`. The short version, because a
   * reader who meets this field first deserves it: nothing is added here at admission.
   * `admit()` cannot know whether the route will reach the gateway, so a charge written
   * then would have to be given back, and a lost refund is an OVERCOUNT — the one
   * direction this tier may never fail in. Units land here only when a request has been
   * released without having been refunded, i.e. only when the money was really committed,
   * and the next admission's cache round trip publishes them.
   *
   * `bucket` is the hour those units belong to, and anything that does not match it is
   * DROPPED rather than moved: carrying last hour's crumbs into this hour's entry would
   * be spend recorded against an hour that did not spend it, which refuses somebody.
   * Dropping is an undercount, which is legal here. Moving is not.
   */
  units: { pending: 0, bucket: -1 },
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
    /** Budget units handed back by `slot.refundBudget()` — a request that was CHARGED at
     *  admission and then refused by the route body without ever calling the gateway. It
     *  is a recorded fact rather than an inference so a test can assert the refund
     *  HAPPENED and not merely that the arithmetic came out even (playbook rule 11). */
    refundedUnits: 0,
    /** joined = queued at all; granted = got a slot from a release; expired = waited out
     *  the clock; refusedFull = never queued because the depth cap was already reached. */
    queue: { joined: 0, granted: 0, expired: 0, refusedFull: 0 },
    /** The cross-isolate tier, as RECORDED facts (playbook rule 11). `ops` counts the
     *  actual Cache API round trips, which is the number the latency budget is spent in;
     *  `errors` + `timeouts` + `stale` are every way the tier failed open.
     *
     *  **THE FLAT FIELDS ARE THE PER-IP MINUTE WINDOW SUB-TIER, AND ONLY THAT.** The unit
     *  budget sub-tier (2026-09-05) records the identical field set under `.units`, kept
     *  separate rather than summed for one reason worth the extra nesting: the two
     *  sub-tiers refuse for different reasons, cost different numbers of ops, and fail
     *  open independently, so a single blended `ops` could not tell a test which of them
     *  did what. **The tier's TOTAL round trips are `cache.ops + cache.units.ops`** and no
     *  sentence may quote either half as the whole.
     *
     *  `units.published` is the one field the window sub-tier has no equivalent of: the
     *  number of units this isolate has actually handed to the shared entry. It is what
     *  makes "a refused request publishes NOTHING" an assertable fact rather than an
     *  inference from an unchanged stored value that might simply have been overwritten. */
    cache: {
      checked: 0, ops: 0, hit: 0, miss: 0, stale: 0,
      allowed: 0, refused: 0, wrote: 0, errors: 0, timeouts: 0,
      units: {
        checked: 0, ops: 0, hit: 0, miss: 0, stale: 0,
        allowed: 0, refused: 0, wrote: 0, errors: 0, timeouts: 0,
        published: 0, dropped: 0,
      },
    },
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
  // The ledger is ISOLATE state, so clearing it here is what makes `__reset()` a real
  // isolate boundary for the shared budget the way it already is for the maps: a test
  // that resets and keeps the same fake cache is then two isolates in one colo, which is
  // exactly the thing `sharedBudgetVerdict` exists to see.
  state.units = { pending: 0, bucket: -1 };
  for (const route of Object.keys(state.waiters)) expireAll(state.waiters[route]);
  state.inflight = { chat: 0, speech: 0, transcribe: 0 };
  state.waiters = { chat: [], speech: [], transcribe: [] };
  state.stats = {
    admitted: 0,
    refused: 0,
    refusals: {},
    upstreamCalls: 0,
    refundedUnits: 0,
    queue: { joined: 0, granted: 0, expired: 0, refusedFull: 0 },
    cache: {
      checked: 0, ops: 0, hit: 0, miss: 0, stale: 0,
      allowed: 0, refused: 0, wrote: 0, errors: 0, timeouts: 0,
      units: {
        checked: 0, ops: 0, hit: 0, miss: 0, stale: 0,
        allowed: 0, refused: 0, wrote: 0, errors: 0, timeouts: 0,
        published: 0, dropped: 0,
      },
    },
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
    /** The shared budget tier's unpublished ledger — `{pending, bucket}`. A test asserts
     *  on this rather than on the cache's stored value when what it wants to know is what
     *  this isolate OWES, which is a different fact from what the colo has been told. */
    units: { ...state.units },
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
 * Normalize an address into a RATE-LIMIT KEY — not into a canonical IP, which is a
 * different job with a different answer.
 *
 * ============================================================================
 * WHY A RAW ADDRESS IS THE WRONG KEY, AND THIS IS THE WHOLE BUG.
 *
 * IPv4 hands a residential visitor ONE address, so `ip -> bucket` is `person -> bucket`
 * and every per-IP window in §4.1 means what it says. **IPv6 does not.** A residential
 * allocation is a /64 at the very least (often a /56 or /48), so one visitor owns
 * 18 446 744 073 709 551 616 source addresses and can pick a fresh one per request at
 * zero cost. Keyed on the raw string, `chat_per_min: 5` becomes `chat_per_min: infinity`
 * for anyone on IPv6 — the entire per-IP tier defeated by a `for` loop, no botnet, no
 * proxy, no skill. That is not a theoretical hole; it is the default behaviour of a
 * consumer ISP.
 *
 * So the key is the FIRST FOUR HEXTETS (the /64), which is the smallest unit that
 * corresponds to *a subscriber* rather than *an interface*. It is deliberately COARSER
 * than the address: two people behind one /64 (a household, a phone hotspot) share a
 * bucket, exactly as two people behind one IPv4 NAT already do. Sharing is the
 * conservative direction and it is the direction IPv4 has always erred in.
 *
 * WHAT THIS IS NOT: it is not a /48 or /56, which would be the *truly* subscriber-sized
 * prefix on many ISPs. A /64 was chosen because it is the one boundary every RFC 4291
 * deployment agrees on (it is the interface-identifier split), while /48-vs-/56 varies by
 * ISP and guessing wrong there groups unrelated customers. If the demo is ever actually
 * attacked from many /64s of one allocation, widening this to three hextets is a
 * one-character change — and the tests below say what each form must key as.
 * ============================================================================
 *
 * The awkward real-world forms, all of which reach here in some deployment:
 *
 *   `203.0.113.9`            IPv4                  -> unchanged
 *   `1.2.3.4:5678`           IPv4 with a port      -> `1.2.3.4`
 *   `2001:db8:1:2:3:4:5:6`   full IPv6             -> `2001:db8:1:2`
 *   `2001:db8::1`            elided IPv6           -> `2001:db8:0:0`
 *   `::1`                    loopback              -> `0:0:0:0`
 *   `fe80::1%eth0`           a zone index          -> `fe80:0:0:0`
 *   `[2001:db8::1]:443`      bracketed, with port  -> `2001:db8:0:0`
 *   `::ffff:1.2.3.4`         IPv4-MAPPED           -> `1.2.3.4`   (NOT a /64)
 *   `::ffff:102:304`         the same address      -> `1.2.3.4`
 *
 * **The IPv4-mapped row is the one that would silently be wrong.** `::ffff:a.b.c.d` is
 * how a dual-stack listener reports an IPv4 client, and every such address shares the
 * `0:0:0:ffff` prefix — so truncating it to a /64 would collapse *the entire IPv4
 * internet* into one bucket and rate-limit every v4 visitor against every other one.
 * It is unmapped back to the v4 address instead, which keys identically to the same
 * client arriving as plain v4.
 *
 * Anything containing `:` that does not parse keys as `unknown` rather than as itself:
 * a malformed address is either a bug or a forgery attempt, and neither one has earned a
 * bucket of its own. (Through Cloudflare this cannot happen — `CF-Connecting-IP` is
 * always a canonical address — so this branch is the fallback path's seatbelt.)
 */
export function ipKey(raw) {
  let s = String(raw == null ? "" : raw).trim();
  if (!s) return "unknown";
  if (!s.includes(":")) return s; // plain IPv4 or a hostname: keyed as-is, unchanged.

  // `1.2.3.4:5678` — an IPv4 with a port, which some proxies write. Strip the port; do
  // NOT try this for a bare IPv6, where a trailing `:n` is a hextet, not a port.
  const v4port = /^(\d{1,3}(?:\.\d{1,3}){3}):\d{1,5}$/.exec(s);
  if (v4port) return v4port[1];

  // `[addr]` or `[addr]:port` — the RFC 3986 authority form.
  if (s.startsWith("[")) {
    const close = s.indexOf("]");
    if (close < 0) return "unknown";
    s = s.slice(1, close);
  }
  // `%eth0` / `%25eth0` — a zone index identifies an INTERFACE on the receiving host and
  // says nothing about the sender, so it is not part of the identity.
  const pct = s.indexOf("%");
  if (pct >= 0) s = s.slice(0, pct);

  const groups = expandV6(s);
  if (!groups) return "unknown";

  // IPv4-mapped (`::ffff:0:0/96`) — unmap rather than truncate. See above.
  if (groups[0] === 0 && groups[1] === 0 && groups[2] === 0 && groups[3] === 0 &&
      groups[4] === 0 && groups[5] === 0xffff) {
    return [groups[6] >> 8, groups[6] & 0xff, groups[7] >> 8, groups[7] & 0xff].join(".");
  }
  // The /64. Lower-case hex, no leading zeros — one address has exactly one key.
  return groups.slice(0, 4).map((g) => g.toString(16)).join(":");
}

/** Expand an IPv6 literal to eight 16-bit groups, or `null` if it is not one. Written out
 *  rather than delegated to `new URL("http://[" + s + "]")`, which accepts some forms and
 *  normalises others differently across runtimes — and a rate-limit key that depends on
 *  the runtime is a key that changes when Cloudflare updates workerd. */
function expandV6(input) {
  const s = String(input).toLowerCase();
  if (!s || s.length > 45 || !/^[0-9a-f:.]+$/.test(s)) return null;
  if (s.indexOf(":::") >= 0) return null;

  const halves = s.split("::");
  if (halves.length > 2) return null; // at most ONE elision, RFC 4291 §2.2
  const elided = halves.length === 2;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = elided ? (halves[1] ? halves[1].split(":") : []) : [];
  if (!elided && head.length !== 8) return null;

  const parts = head.concat(tail);
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.indexOf(".") >= 0) {
      // A dotted quad is legal only as the LAST element, where it stands for two hextets.
      if (i !== parts.length - 1) return null;
      const q = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(p);
      if (!q) return null;
      const b = [Number(q[1]), Number(q[2]), Number(q[3]), Number(q[4])];
      if (b.some((n) => n > 255)) return null;
      out.push((b[0] << 8) | b[1], (b[2] << 8) | b[3]);
      continue;
    }
    if (!/^[0-9a-f]{1,4}$/.test(p)) return null;
    out.push(parseInt(p, 16));
  }
  if (out.length > 8) return null;
  if (!elided) return out.length === 8 ? out : null;

  // Splice the zeros back in at the elision. `::` must stand for AT LEAST one group.
  const headLen = head.reduce((n, p) => n + (p.indexOf(".") >= 0 ? 2 : 1), 0);
  const fill = 8 - out.length;
  if (fill < 1) return null;
  return out.slice(0, headLen).concat(new Array(fill).fill(0), out.slice(headLen));
}

/**
 * The visitor's IP, as a RATE-LIMIT KEY (§4.1).
 *
 * `CF-Connecting-IP` is the source of truth: Cloudflare sets it on every request and
 * OVERWRITES whatever the client sent, so it is the one address header a visitor cannot
 * forge from outside. It is passed through `ipKey()`, which collapses an IPv6 address to
 * its /64 — see that function for why a raw IPv6 string is not a key at all.
 *
 * ============================================================================
 * `X-Forwarded-For` IS A CLIENT-WRITABLE STRING AND IS IGNORED BY DEFAULT.
 *
 * Until 2026-09-03 the absent-`CF-Connecting-IP` case fell back to `X-Forwarded-For`'s
 * first hop, for the benefit of a local `wrangler pages dev`. That is a header ANY caller
 * can set to anything, so wherever the fallback is reachable the per-IP windows are not a
 * limit at all: one process rotates the header and holds an unbounded supply of buckets.
 *
 * On the live deployment it is NOT reachable — Cloudflare always sets `CF-Connecting-IP`,
 * so the fallback is dead code in production — but "unreachable in today's topology" is a
 * property of the topology, not of this file. Put any proxy, tunnel or preview host in
 * front and the hole opens with no code change and no warning. So the fallback now needs
 * an explicit `DEMO_TRUST_XFF`, WHICH MUST STAY UNSET IN PRODUCTION, and the default is
 * to fall through to `unknown`.
 *
 * **`unknown` IS ONE SHARED BUCKET, AND THAT IS THE INTENT.** Every caller we cannot
 * identify is counted TOGETHER against one set of windows — throttled as a group rather
 * than each handed a free lane. The failure mode is that unidentifiable callers contend
 * with each other, which is the correct direction: the alternative (a bucket each) is
 * precisely the unbounded bypass this change closes.
 * ============================================================================
 *
 * @param {Request} request
 * @param {{trustXff?: boolean}} [cfg] the deployment config. Absent ⇒ XFF is not trusted.
 */
export function clientIp(request, cfg) {
  const h = request && request.headers;
  if (!h) return "unknown";
  const cf = h.get("CF-Connecting-IP");
  if (cf) return ipKey(cf);
  if (cfg && cfg.trustXff) {
    const xff = h.get("X-Forwarded-For");
    if (xff) return ipKey(String(xff).split(",")[0]);
  }
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

/**
 * Charge one request against the in-isolate unit budget.
 *
 * `hourBucket` and `hourly` are carried out with the answer for the SHARED sub-tier's
 * benefit, not this one's. A slot settles at `release()`, which can be a second or two
 * after this call and — once an hour — on the far side of a bucket boundary, so the
 * ledger has to be told which hour the units it is being handed were charged in. Reading
 * the clock again at settle time would be a different question with a different answer,
 * and the answer it would get wrong is "which hour pays for this turn".
 */
function chargeBudget(route, cfg, nowS) {
  const cost = UNITS[route] || 1;
  const ceilings = { hour: cfg.unitBudgetHour, day: cfg.unitBudgetDay };
  /** The hour this charge belongs to, and whether an hourly ceiling exists to mirror. */
  const hourBucket = bucket(nowS, SCALES.hour);
  const hourly = !!cfg.unitBudgetHour;
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
        charged: [], cost, hourBucket, hourly,
      };
    }
    touched.push([key, used + cost]);
  }
  for (const [key, next] of touched) state.budget.set(key, next);
  prune(state.budget);
  return {
    ok: true, reason: null, retryAfterS: 0,
    charged: touched.map(([key]) => key), cost, hourBucket, hourly,
  };
}

/* ---------------------------------------------------------------------------- *
 * The shared budget's LEDGER — three tiny functions, and the whole safety argument
 * ---------------------------------------------------------------------------- *
 *
 * These three exist so that the shared unit budget never has to un-say something it has
 * already said. `sharedBudgetVerdict` carries the full argument; what matters here is the
 * ONE invariant they enforce between them:
 *
 *   **A unit reaches the ledger only after the request that spent it has been released
 *   without a refund, and once in the ledger it can only be published or DROPPED.**
 *
 * Published means written to the colo's entry. Dropped means forgotten, which undercounts,
 * which is the direction this tier is allowed to be wrong in. There is no third outcome
 * and in particular there is no "taken back", because taking something back across an
 * eventually-consistent store is the failure mode that refuses innocent visitors.
 */

/** The units this isolate owes hour `b`, resetting the ledger when it belongs to a
 *  different hour.
 *
 *  IT MUTATES ON READ, on purpose. A ledger left holding a past hour's units would refuse
 *  every future accrual (`accruePending` will not mix hours), so the isolate would stop
 *  publishing for ever — a permanent undercount, legal but stuck, and a stuck counter is
 *  indistinguishable from a broken one to whoever reads the stats next. Resetting here is
 *  the self-heal, and `dropped` records that it happened rather than leaving it silent. */
function pendingUnits(b) {
  const u = state.units;
  if (u.bucket !== b) {
    if (u.pending > 0) state.stats.cache.units.dropped += u.pending;
    u.bucket = b;
    u.pending = 0;
  }
  return u.pending;
}

/** Add committed spend to the ledger. Called from `release()` and from nowhere else, so
 *  the only units that can ever arrive here are units a request actually spent. */
function accruePending(hourBucket, cost) {
  if (!cost) return;
  const u = state.units;
  if (u.pending === 0) u.bucket = hourBucket; // an empty ledger adopts the first charge's hour
  if (u.bucket !== hourBucket) {
    // A straggler from another hour. DROPPED rather than added: see `state.units`.
    state.stats.cache.units.dropped += cost;
    return;
  }
  u.pending += cost;
}

/** Take committed spend back OUT of the ledger — the one and only un-say in this design,
 *  and it is safe precisely because the ledger is in this isolate's memory and has not
 *  been published to anybody. It exists for the ordering `refundBudget()` after
 *  `release()`, which no route in this repo performs today (every one of them refunds
 *  inside the `try` and releases in the `finally`) but which nothing structurally
 *  prevents. Cheaper to be correct for it than to write a comment asking future callers
 *  not to do it. */
function unaccruePending(hourBucket, cost) {
  const u = state.units;
  if (!cost || u.bucket !== hourBucket) return;
  u.pending = Math.max(0, u.pending - cost);
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

/**
 * The budget answer with nothing charged — for a probe that must spend nothing.
 *
 * **IN-ISOLATE ONLY, AND DELIBERATELY SO EVEN THOUGH THE BUDGET IS NOW ALSO SHARED.**
 * `/api/health` is the one route that does not call `admit()` and is not `async`
 * (`sim/test_mode.mjs` asserts that structurally: a probe that cannot await cannot call
 * upstream, and a probe that awaited a cache would be a probe that can hang). So this
 * reports what THIS isolate knows, which can be less than the colo knows, and the honest
 * consequence is that the page can be told `live` a moment before a spending route tells
 * it `budget_exhausted`. That is the same shape of incompleteness §7's copy is already
 * written for ("a few other people") and no sentence may describe this function's answer
 * as the deployment's budget.
 */
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
 *
 * CHECKED WHEN `/api/chat` GREW A THIRD FIELD (2026-09-05, the Turnstile token): a
 * Cloudflare Turnstile response token is documented at up to 2 048 bytes, and the default
 * ceiling here is `4096 + 3 * (500 + 1500)` = 10 096 — so a maximal utterance, a maximal
 * context blob AND a maximal token together come to roughly half of it. The flat 4 096
 * term, which exists for JSON syntax, absorbs the new field with room to spare and no
 * change was needed. Worth re-doing that arithmetic before adding a FOURTH field: the
 * failure mode is a `too_long` refusal that blames the visitor's sentence for a byte
 * budget their sentence had nothing to do with.
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
  // `release()` and `refundBudget()` are both no-ops here and both are PRESENT on purpose:
  // a refused admission holds no slot and (after `refundCharges`) has spent nothing, but a
  // caller must be able to call either without asking which kind of answer it got. A slot
  // shape with a missing method is a `TypeError` on a refusal path, which is the one place
  // a route cannot afford to throw.
  return {
    ok: false, reason, retryAfterS: 0, rateLimit: null,
    release: () => {}, refundBudget: () => {},
    ...extra,
  };
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

/**
 * Build the granted-slot result. `inflight` has ALREADY been accounted for by the
 * caller — either incremented on the fast path, or handed over by `handOffOrRelease` —
 * so this function only wraps it in the idempotent `release()`.
 *
 * ============================================================================
 * `refundBudget()`, AND WHY IT GIVES BACK THE UNIT BUDGET AND *NOT* THE PER-IP WINDOW.
 *
 * THE HOLE IT CLOSES, measured (2026-09-05). `admit()` charges `UNITS[route]` — 3 for a
 * chat turn — BEFORE the route body runs, and the route body has refusals of its own:
 * `too_long`, `too_short`, `bad_request`, the safety floor's `blocked`, and (since the
 * Turnstile slice) `turnstile_failed`. Every one of those spends 3 units on a request
 * that makes NO gateway call. 200 tokenless POSTs — an empty JSON body, no browser, no
 * token, and each one correctly refused — therefore emptied `DEMO_UNIT_BUDGET_HOUR` (600)
 * and the NEXT visitor, holding a perfectly good token, was answered `budget_exhausted`
 * with the page painted SCRIPTED for the rest of the hour. **The bot control had turned a
 * paid drain into a free drain and left the availability outcome unchanged**, which is the
 * opposite of what `./turnstile.js`'s header claims for it.
 *
 * WHY A REFUND AND NOT A REORDER. Exactly the argument `refundCharges()` sets out for the
 * queue: charging after the decision would put a request that is refused instantly and for
 * nothing in front of a scarce resource first. The order stays; the accounting is made
 * true after the fact.
 *
 * WHY THE PER-IP WINDOW IS DELIBERATELY *NOT* GIVEN BACK — the one asymmetry with
 * `refundCharges()`'s own two call sites, which refund both:
 *
 *   * the unit budget is SHARED BY EVERY VISITOR. Its exhaustion is the availability harm
 *     above: one address spends it and everybody else gets scripted lines. Nothing a
 *     refused request did justifies that, so it comes back.
 *   * the per-IP window is SELF-INFLICTED and is the only thing that makes a flood of
 *     free refusals from one address eventually go quiet. Refunding it would make an
 *     unauthenticated, tokenless refusal *unlimited* per IP — a new abuse channel opened
 *     to fix an abuse channel, which is the trade `refundCharges()` rejected for (b).
 *     The visitor who eats a window unit on a refused turn is the visitor whose request
 *     was wrong; the fairness cost is theirs alone and it is bounded by one minute.
 *
 * The two existing refund sites (the queue expiring, the shared tier refusing) keep
 * refunding BOTH, and that is right for them: neither is the requester's fault at all.
 *
 * IDEMPOTENT, for the same reason `release()` is: a second call would credit units nobody
 * ever spent, and an over-credited shared budget is money.
 *
 * AND IT MUST NOT BE CALLED ON A PATH THAT WAS SERVED. There is no way for this function
 * to know, so the rule lives at the call sites: refund only where the route returns
 * WITHOUT having called `noteUpstreamCall()`. `sim/test_turnstile.mjs` §11 pins the
 * balance both ways — a refused turn leaves the counter where it found it, a served turn
 * leaves 3 units spent.
 *
 * ---------------------------------------------------------------------------
 * AND SINCE 2026-09-05 `release()` IS ALSO WHERE THE SHARED BUDGET LEARNS ANYTHING.
 *
 * The pair below is the seam the shared unit-budget sub-tier is built on, so it is worth
 * being explicit about why the settle hangs off `release()` rather than off `admit()` or
 * off a new method of its own.
 *
 *   * NOT `admit()`, because at admission nobody knows yet whether this turn will reach
 *     the gateway. A shared counter told at admission has to be told again on a refusal,
 *     and a correction that gets lost leaves the visitor charged for a turn that never
 *     happened. That is `sharedBudgetVerdict`'s whole argument and it is why the ledger
 *     exists at all.
 *   * NOT A NEW `settleBudget()` THAT ROUTES MUST REMEMBER, because "every path must
 *     remember to call it" is EXACTLY the discipline that produced the bug this function's
 *     own header describes: `refundBudget()` had to be added to five separate return paths
 *     in `chat.js` and one missed path is a free drain. A rule enforced in one place beats
 *     a rule written down in six.
 *   * `release()`, because every route already calls it in a `finally` — it is the one
 *     call this file can be sure happens exactly once on every path, and
 *     `sim/tools/turnstile_mutation_check.py` rows D2 and D2b already prove it is
 *     load-bearing. The settle rides on a discipline that is already tested.
 *
 * The two orderings, both handled: `refundBudget()` then `release()` is what all three
 * routes do (refund inside the `try`, release in the `finally`) and the settle simply does
 * not happen. `release()` then `refundBudget()` happens nowhere today, and
 * `unaccruePending` takes the units back out of a ledger nobody has been shown yet.
 * ---------------------------------------------------------------------------
 * ============================================================================
 */
function grantedSlot(route, capacity, rateLimit, budget) {
  state.stats.admitted += 1;
  let released = false;
  let refunded = false;
  let settled = false;
  /** What this request will owe the SHARED hour if it is released un-refunded. Zero
   *  whenever there is no hourly ceiling to mirror, or whenever the in-isolate charge
   *  took nothing (an uncapped deployment), so an unbudgeted deployment accrues nothing
   *  and publishes nothing. */
  const owed = budget && budget.hourly && budget.charged && budget.charged.length
    ? (budget.cost || 0) : 0;
  const hourBucket = (budget && budget.hourBucket) || 0;
  return {
    ok: true,
    reason: null,
    retryAfterS: 0,
    rateLimit,
    load: { inflight: state.inflight[route], capacity },
    release() {
      if (released) return; // idempotent: a double release would under-count for ever
      released = true;
      // THE SETTLE. A request that got this far without being refunded reached the
      // gateway, so its units are real spend and the colo may be told about them.
      if (!refunded && !settled) {
        settled = true;
        accruePending(hourBucket, owed);
      }
      handOffOrRelease(route);
    },
    refundBudget() {
      if (refunded) return; // idempotent: a double refund would credit units never spent
      refunded = true;
      if (settled) {
        settled = false;
        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above
      }
      state.stats.refundedUnits += (budget && budget.charged && budget.charged.length)
        ? (budget.cost || 0) : 0;
      refundCharges([], (budget && budget.charged) || [], (budget && budget.cost) || 0);
    },
  };
}

/* ---------------------------------------------------------------------------- *
 * §4.6.1 — the Cache API tier: a SECOND per-IP minute window, shared across the
 *          isolates of ONE COLO
 * ---------------------------------------------------------------------------- *
 *
 * ============================================================================
 * WHAT THIS TIER IS, STATED AS PLAINLY AS THE HEADER STATES THE REST.
 *
 * **IT IS NOT A GLOBAL CEILING AND NOTHING MAY CALL IT ONE.** It is a second,
 * best-effort per-IP minute window kept in `caches.default`. Three things are true of it
 * at once and all three have to be said together, because the previous version of this
 * document said only the flattering one and that single wrong sentence is what made
 * someone size the spend risk in the unsafe direction:
 *
 *   1. **It is PER-COLO.** Cloudflare's cache "does not replicate outside of the
 *      originating data center", so each colo keeps its own count. A visitor who reaches
 *      two colos gets two windows. What it removes is the *isolate* multiplier — measured
 *      at >= 7 for one client on one path (§4.6.1) — not the colo one.
 *   2. **A BURST DEFEATS IT.** It is an unlocked read-modify-write, so concurrent writes
 *      overwrite each other: the probe stored 9 of 31 concurrent increments, losing about
 *      two thirds. Sequentially it lost 0 of 41 — and *sequential* is the traffic a
 *      counter exists to stop (a paced drain over hours), while a burst is already bounded
 *      by `DEMO_MAX_CONCURRENT_CHAT` and `DEMO_QUEUE_MAX_DEPTH`.
 *   3. **EVERY ERROR IS AN UNDERCOUNT, SO IT FAILS OPEN.** Every write is some observed
 *      `prev + 1`, so the stored value can never exceed the truth. A lost write, a cache
 *      miss, a timeout, a throw, a stale entry — every one of them lets a visitor through
 *      who should perhaps have been refused, and NONE of them can refuse a visitor who
 *      should have been allowed. That is the direction this file must fail in, and
 *      `sim/test_demo_proxy.mjs` §14 asserts it for each failure mode by name.
 *
 * **THE IN-ISOLATE `Map` STAYS UNDERNEATH, UNCHANGED, AND DECIDES FIRST.** That is the
 * property that makes this safe rather than merely likely to be safe: this tier only ever
 * ADDS refusals. Switch it off with `DEMO_CACHE_COUNTER=0`, or run it where
 * `caches.default` does not exist (bare `node`), and `admit()` is byte-for-byte the
 * function it was before — the `if (!store)` line below returns the same `grantedSlot()`
 * synchronously, with no cache call and no extra `await`.
 * ============================================================================
 *
 * WHAT IT COUNTS, AND WHY THAT AND NOT SOMETHING ELSE.
 *
 * The **per-IP MINUTE window** (`chat_per_min` and its two siblings), and only that.
 * §4.6.1's implementation note asks for exactly this: *"apply the tier to the per-IP
 * window before the unit budget, since an undercounted window costs a few extra turns
 * while an undercounted budget costs money"* — put the lossy counter where being wrong is
 * cheap. Three candidates were considered, one rejected and one built next door:
 *
 *   * The **hour and day windows** — rejected on the latency budget. Each extra key is
 *     another `match` + `put`; §4.6.1 row h measured three cache ops at <=44 ms and this
 *     sub-tier gets two. The minute is also the bucket that rotates fastest, so a hot key
 *     is never long-lived and a stale one expires itself.
 *   * The **unit budget** — BUILT 2026-09-05, at `sharedBudgetVerdict`, and it is one more
 *     `match` (+ one conditional `put`) on the key `.../units/<hour bucket>` as this note
 *     predicted. What this note got WRONG, and it is worth leaving the correction next to
 *     the prediction: *"the same fail-open rules apply verbatim"* is not true of the
 *     refund path, because `slot.refundBudget()` did not exist when it was written. A
 *     refund is a `prev - cost`, a lost `prev - cost` is an OVERCOUNT, and an overcount
 *     refuses visitors who should be served. The sub-tier resolves that by never writing a
 *     charge it might have to take back; the whole argument is over there.
 *   * The **concurrency ceiling** — rejected outright, and this one is not a budget
 *     question. A slot must be RELEASED, so a lost write would leak a slot for ever: the
 *     counter would drift upward and start refusing visitors who should be allowed. That
 *     is failing CLOSED, which is the one direction this tier may never fail in. An
 *     eventually-consistent counter cannot hold a resource that must be given back.
 *     (The unit budget's refund is the same shape of problem, which is why it is solved
 *     by removing the give-back rather than by making it reliable.)
 *
 * WHERE IT SITS IN `admit()`, AND WHY NOT EARLIER. It runs LAST, after the concurrency
 * slot has been taken, and `admit()` has exactly ONE `await` — `grantOrShared()` — on the
 * fast path. Earlier would have been worse in two specific ways. It would have put a
 * ~30 ms network round trip in front of refusals that are free today (the origin pin, the
 * in-isolate window), so a script being refused would cost more than a visitor being
 * served. And it would have inserted an `await` before the FIFO join, breaking the
 * property the queue section calls the point: everything that decides runs synchronously,
 * so a late arrival cannot overtake. Running last costs the refusal path one slot held for
 * the length of one cache round trip, which `handOffOrRelease()` then passes straight to
 * whoever is waiting.
 *
 * **THAT COUNT IS AWAITS IN `admit()`, NOT CACHE OPS, AND THE SECOND SUB-TIER DID NOT
 * CHANGE IT.** Adding the unit budget (2026-09-05) put more awaited work INSIDE that one
 * `await` — the ops below — and none in front of it: every decision in `admit()` still
 * runs synchronously before the first `await`, the FIFO join is still ahead of every cache
 * call, and a free refusal still costs nothing. The honest number that DID change is the
 * op count, and it is stated where it is spent: §4.6.1 row h measured three ops at <=44 ms
 * (~15 ms each), and a served turn now issues up to four — two for the window, one budget
 * read and one conditional budget write — which by that measurement's own per-op cost is
 * ~59 ms. That is an extrapolation from row h, not a measurement, and it is labelled as
 * one. A route-refused turn issues three and a tier-refused one, at most two.
 */

/** The path prefix of the tier's cache entries, on this deployment's OWN origin — the
 *  form the probe measured (§4.6.1). It is not a Function route and holds no secret: the
 *  body is `{"n":<integer>}` and the visitor appears only as a keyed one-way tag, because
 *  a Cache API entry lives under a URL an outsider could in principle ask for. */
const CACHE_PATH = "/__moxie/rl/";

/** 96 bits of tag. Long enough that a collision is not a thing that happens; short enough
 *  that the key stays readable in a log. A collision would merge two visitors into one
 *  bucket, which throttles them together — the conservative direction. */
const CACHE_TAG_HEX = 24;

/** The two ways a cache op can fail to answer. Distinct symbols so the caller can record
 *  WHICH happened; both fail open.
 *
 *  EXPORTED SINCE 2026-09-05 so `./ttscache.js` — the synthesised-audio cache behind
 *  `/api/speech` — reuses these and `withDeadline()` rather than growing a second, subtly
 *  different fail-open style next door. There is one deadline wrapper in this tree and one
 *  pair of failure symbols, so "every cache error falls open" is a property of one
 *  function that two callers share, not a discipline two files each promise separately. */
export const CACHE_TIMEOUT = Symbol("cache-timeout");
export const CACHE_ERROR = Symbol("cache-error");
/** A hit whose `Age` has passed its own `max-age`. A real cache would not serve one; the
 *  fake in the tests does, and the answer must be "treat it as absent". */
const CACHE_STALE = Symbol("cache-stale");

/**
 * Run one cache operation under a hard deadline, and NEVER reject.
 *
 * A hung `match()` is the failure mode that would actually hurt: it is not an error the
 * runtime reports, it is a promise that simply never settles, and without this wrapper it
 * would hold a concurrency slot and the visitor's turn open until the route's own timeout.
 * So every outcome — a value, a throw, a synchronous throw from `run()` itself, or the
 * clock — resolves this promise exactly once. The timer is cleared on every settled path,
 * so a hung op leaves no handle behind and cannot keep a `node` test alive.
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

/** The store this admission should use: the injected one in a test, `caches.default` on
 *  the real runtime, or `null` — which is the "behave exactly as before" path. */
function sharedStore(o, cfg) {
  if (!cfg || !cfg.cacheCounter) return null;
  if (o && "cache" in o) return o.cache || null; // tests inject a fake with the same surface
  try {
    return (typeof caches !== "undefined" && caches && caches.default) || null;
  } catch {
    return null; // a runtime that throws on the global is a runtime without a cache
  }
}

/** The unit budget's own path component — the slot the window key fills with a ROUTE
 *  name. It is a literal, and the reason it is safe as a literal rather than a hashed
 *  namespace is arithmetic, not hope: see `unitsKeyUrl` for the collision argument. */
const UNITS_PATH = "units";

/** `https://<own origin>/__moxie/rl/<route>/<tag>/<minute bucket>` */
function cacheKeyUrl(request, route, tag, minuteBucket) {
  const origin = new URL(request.url).origin;
  return origin + CACHE_PATH + route + "/" + tag + "/" + minuteBucket;
}

/**
 * `https://<own origin>/__moxie/rl/units/<hour bucket>` — the unit budget's shared entry.
 *
 * ============================================================================
 * EVERY COMPONENT, AND WHY IT IS THERE. A wrong key here does not play the wrong words —
 * it merges two counters, and merging the deployment's SPEND with anything else is its own
 * kind of wrong.
 *
 *   1. **`<own origin>`**, from `new URL(request.url).origin`. A Cache API entry is keyed
 *      by URL, so the origin is what keeps this deployment's spend inside this deployment:
 *      the project's own host, a branch preview and a custom domain are three different
 *      hostnames and therefore three different entries, and a preview can never spend
 *      production's hour. (No hostname is named here or anywhere under `functions/` —
 *      that is deployment CONFIG, C3, and `sim/test_demo_proxy.mjs` §12 enforces it over
 *      this whole tree, comments included. It caught the first draft of this very
 *      paragraph.) It is also the form §4.6.1's probe actually measured.
 *   2. **`/__moxie/rl/`** (`CACHE_PATH`). Not a Function route, on purpose: an outsider who
 *      asks for the URL reaches the static asset handler, never this file. Shared with the
 *      window sub-tier so the whole tier occupies one namespace a reader can grep for.
 *   3. **`units`** (`UNITS_PATH`). The namespace discriminator. It occupies the slot the
 *      window key fills with a route name, and it can never be confused with one for TWO
 *      independent reasons, either of which alone is sufficient: (a) `"units"` is not a
 *      member of `{chat, speech, transcribe}`, the closed set `capacityOf`/`windowLimits`
 *      accept — and `__keyShapes()` asserts that rather than assuming it; and (b) the two
 *      shapes have DIFFERENT ARITY — a window key is three `/`-separated components after
 *      the prefix and this is two — so no assignment of values to a window key's fields
 *      can produce this string. That is the byte-level answer, and it does not depend on
 *      any property of the tag.
 *   4. **`<hour bucket>`**, `Math.floor(nowS / 3600)`. Three jobs at once. It matches the
 *      in-isolate budget's own bucket (`units|hour|<bucket>`), so the shared count and the
 *      local count are counts of the SAME hour rather than two overlapping windows. It
 *      rotates the key hourly, so the hot entry is never long-lived. And it makes a stale
 *      entry unbelievable rather than merely unlikely: last hour's count lives under a
 *      DIFFERENT URL and cannot be read as this hour's, whatever the cache does with
 *      `Age`.
 *
 * AND WHAT IS DELIBERATELY ABSENT, because an absent component is a decision too:
 *
 *   * **The visitor.** There is no IP, no tag, no per-visitor anything — the unit budget is
 *     one ceiling for the whole deployment (`state.budget`'s key carries no IP either), so
 *     one entry per hour is not a collision, it is the definition. This is the one place
 *     where "two visitors share a bucket" is correct rather than a bug. It is also why
 *     this key needs no `keyedTag`: there is nothing about a person in it to protect.
 *   * **The route.** `chat` costs 3 units and `speech` 2 (`UNITS`); the difference is
 *     carried in the INCREMENT, not in the key, exactly as `chargeBudget` does it. A
 *     per-route key would be three budgets that each think they are the whole one.
 *   * **The ceiling.** `DEMO_UNIT_BUDGET_HOUR` is compared against the entry, never keyed
 *     into it, so lowering the ceiling mid-hour takes effect immediately against the spend
 *     already recorded — the same behaviour the in-isolate map has.
 *
 * **NOTHING AN OUTSIDER CONTROLS APPEARS IN THIS KEY.** Every byte is a literal or a
 * decimal integer derived from the clock, so there is no component to length-prefix and no
 * digest to truncate: the discipline `_lib/ttscache.js::lp()` exists to enforce is met here
 * by there being no variable-length input at all. `__keyShapes()` asserts the exact shape.
 * ============================================================================
 */
function unitsKeyUrl(request, hourBucket) {
  const origin = new URL(request.url).origin;
  return origin + CACHE_PATH + UNITS_PATH + "/" + hourBucket;
}

/** The two key shapes as DATA, so a test can assert the collision argument above rather
 *  than restate it. Tests only. */
export function __keyShapes() {
  return {
    prefix: CACHE_PATH,
    units: UNITS_PATH,
    routes: Object.keys(UNITS),
    /** Components after `CACHE_PATH`, for each shape. Different => cannot collide. */
    windowArity: 3,
    unitsArity: 2,
    tagHex: CACHE_TAG_HEX,
  };
}

/** Read the stored count. Returns an integer, `CACHE_STALE`, or `null` for "no usable
 *  entry" — and every one of those three means "start from zero", which is fail-open. */
async function readCount(store, key) {
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
  const n = body && Number(body.n);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null;
}

/**
 * The tier's verdict for one admission: `null` to allow (which is also every failure
 * path), or a refusal descriptor.
 *
 * TWO CACHE OPS, ON THE ADMITTED PATH ONLY. One `match` and one `put` when the visitor is
 * allowed; one `match` and NO `put` when the visitor is refused (a refusal spends nothing,
 * so it counts nothing); and no `put` when the read itself failed, because writing `1`
 * after a failed read would RESET a live count to one — a much larger undercount than
 * simply not writing. §4.6.1 row h measured three ops at <=44 ms; this half stays under
 * that on its own, and `sharedBudgetVerdict` adds one or two more — see the placement note
 * above for the whole tier's arithmetic.
 */
async function sharedWindowVerdict(store, request, { ip, route, cfg, nowS }) {
  const limit = windowLimits(cfg, route).min;
  if (!limit) return null; // this route has no per-minute cap to mirror
  const c = state.stats.cache;
  const b = bucket(nowS, SCALES.min);
  const resetAt = (b + 1) * SCALES.min;

  const tag = await keyedTag(cfg, COUNTER_INFO, ip + "|" + route, CACHE_TAG_HEX);
  const key = cacheKeyUrl(request, route, tag, b);
  c.checked += 1;

  // ---- op 1: read.
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
  c.ops += 1;
  let used = 0;
  if (seen === CACHE_STALE) c.stale += 1;
  else if (typeof seen === "number") {
    c.hit += 1;
    used = seen;
  } else c.miss += 1;

  if (used >= limit) {
    c.refused += 1;
    return {
      retryAfterS: Math.max(1, resetAt - nowS),
      // The same numbers `chargeWindows()` sends on its own refusal, so a visitor cannot
      // tell the two tiers apart and `chat.py`'s backoff loop needs no new case.
      rateLimit: { limit, remaining: 0, reset: resetAt },
    };
  }

  // ---- op 2: write back. Unlocked and on purpose — a lost update undercounts (2).
  // `max-age` is one window, so an entry outlives its own bucket by at most that and then
  // evicts itself; the key already carries the bucket, so nothing stale can be believed.
  const wrote = await withDeadline(cfg.cacheTimeoutMs, () =>
    store.put(
      key,
      new Response(JSON.stringify({ n: used + 1 }), {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "max-age=" + SCALES.min,
        },
      }),
    ),
  );
  if (wrote === CACHE_TIMEOUT) c.timeouts += 1;
  else if (wrote === CACHE_ERROR) c.errors += 1;
  else {
    c.ops += 1;
    c.wrote += 1;
  }
  c.allowed += 1;
  return null;
}

/* ---------------------------------------------------------------------------- *
 * §4.6.1 — the Cache API tier's SECOND sub-tier: the unit budget, shared across
 *          the isolates of one colo
 * ---------------------------------------------------------------------------- *
 *
 * ============================================================================
 * THE PROBLEM THIS SUB-TIER HAD TO SOLVE FIRST, AND IT IS NOT THE ONE THE PLAN NAMED.
 *
 * The window sub-tier above says of the unit budget: *"deliberately next, not now. Same op
 * cost… when it is built it is one more `match` + `put` on the key `.../units/<hour
 * bucket>` and the same fail-open rules apply verbatim."* That sentence was written before
 * `slot.refundBudget()` existed, and **for the refund path it is false.**
 *
 * Here is why, in the terms this file already uses. Every failure in the window sub-tier is
 * an UNDERCOUNT because every write is some observed `prev + 1`: lose it and the stored
 * value is smaller than the truth, so somebody gets served who might have been refused. A
 * REFUND is the opposite shape. `admit()` charges `UNITS[route]` before the route body
 * runs, and five refusals in that body (`too_long`, `too_short`, `bad_request`, the safety
 * floor's `blocked`, `turnstile_failed`) hand the units back. Written to a shared entry,
 * that hand-back is a `prev - cost`, and **a lost `prev - cost` is an OVERCOUNT**: the
 * visitor stays charged for a turn that never happened, the colo's hour empties early, and
 * real visitors are answered `budget_exhausted` with the page painted SCRIPTED. That is
 * failing CLOSED — the exact property for which the window sub-tier's own notes REJECT
 * putting the concurrency ceiling on this tier ("an eventually-consistent counter cannot
 * hold a resource that must be given back"). A refund is a resource being given back.
 *
 * So the shipped design does not give anything back. **THE SHARED ENTRY IS NEVER TOLD
 * ABOUT A CHARGE IT MIGHT HAVE TO UN-HEAR.**
 *
 *   * `admit()` READS the entry and decides. It writes nothing on behalf of the request it
 *     is admitting.
 *   * The units are held in `state.units`, this isolate's own ledger, and are added to it
 *     only by `release()` and only when `refundBudget()` was not called — i.e. only when
 *     the request really reached the gateway (`grantedSlot` carries that argument).
 *   * The NEXT admission's read/write pair publishes the ledger: `put(seen + owed)`.
 *
 * WHAT THAT BUYS, stated as the property rather than as the mechanism: **there is no write
 * on any path whose loss could refuse a visitor who should be served.** Not "the refund is
 * unlikely to be lost", not "the loss is bounded" — there is no refund write at all. A
 * refused request contributes zero to the ledger, so the 200-tokenless-POST drain that
 * `sim/test_turnstile.mjs` §12 exists for publishes literally nothing to the colo, and it
 * does so structurally rather than by remembering to undo something.
 *
 * THE THREE OPTIONS THAT WERE REJECTED, because each is a reasonable-sounding sentence:
 *
 *   1. **Charge shared, refund isolate-local.** The simplest reading of "the same rules
 *      apply verbatim", and it re-opens the vulnerability #160 closed — in the shared
 *      dimension, where it is worse. 200 tokenless POSTs × 3 units = 600 = exactly
 *      `DEMO_UNIT_BUDGET_HOUR`, and every isolate in the colo then reads an exhausted hour
 *      for an attack that made no gateway call. A regression dressed as a limitation.
 *   2. **Store a signed net so a lost refund is bounded.** It is not bounded. Each lost
 *      refund is permanent for the hour and they accumulate; the attacker chooses how many.
 *   3. **Refund the shared tier anyway and accept the risk.** This is the one worth naming
 *      explicitly because it is what "verbatim" would have produced. The risk is not
 *      symmetrical with the tier's other risks: everything else here can only cost a
 *      refusal that should have happened, and this can only cost a turn that should have
 *      been served. Accepting it would make the sentence "every error is an undercount"
 *      false, and that sentence is the reason this tier was allowed to exist.
 *
 * AND THE COST, STATED RATHER THAN HIDDEN. The colo's entry lags the colo's real spend by
 * whatever its isolates have not published yet — at most one settled request per isolate,
 * plus whatever is in flight. That is bounded by the same numbers the queue is
 * (`DEMO_MAX_CONCURRENT_*` + `DEMO_QUEUE_MAX_DEPTH` per isolate) and it is in the
 * PERMISSIVE direction, which is the direction this tier is allowed to be wrong in. An
 * isolate recycled with units still in its ledger takes them to the grave: an undercount,
 * legal, and recorded as `cache.units.dropped` rather than left invisible.
 *
 * WHAT IS NOT MIRRORED, and why:
 *
 *   * **The DAY ceiling** (`DEMO_UNIT_BUDGET_DAY`). Another key is another `match` + `put`
 *     on the request path, and the hour is the bucket that both rotates fast enough to keep
 *     the entry short-lived and matches the ceiling a runaway actually hits first. The day
 *     stays a purely in-isolate ceiling and no sentence may call it a shared one.
 *   * **`/api/health`'s probe** (`budgetState`). It stays synchronous and in-isolate — see
 *     its own note. A probe that awaited a cache would be a probe that can hang.
 * ============================================================================
 *
 * ONE OR TWO CACHE OPS, AND THE SECOND ONE IS NOT SPENT ON A REFUSAL. One `match` always;
 * one `put` only when this isolate has unpublished units AND the request is being admitted.
 * A refused request, a request from an isolate that owes nothing, and a deployment with no
 * hourly ceiling all cost zero writes here.
 */
async function sharedBudgetVerdict(store, request, { cfg, nowS }) {
  const ceiling = cfg.unitBudgetHour;
  if (!ceiling) return null; // 0 => uncapped at this scale, so there is nothing to mirror
  const c = state.stats.cache.units;
  const b = bucket(nowS, SCALES.hour);
  const resetAt = (b + 1) * SCALES.hour;
  /** What this isolate has spent and not yet told the colo about. Reading it also rolls
   *  the ledger if it belongs to a past hour — see `pendingUnits`. */
  const owed = pendingUnits(b);
  const key = unitsKeyUrl(request, b);
  c.checked += 1;

  // ---- op 1: read. Every failure below returns `null`, which ADMITS.
  const seen = await withDeadline(cfg.cacheTimeoutMs, () => readCount(store, key));
  if (seen === CACHE_TIMEOUT) {
    c.timeouts += 1;
    c.allowed += 1;
    return null; // FAIL OPEN — and the ledger is KEPT, because nothing was written
  }
  if (seen === CACHE_ERROR) {
    c.errors += 1;
    c.allowed += 1;
    // FAIL OPEN — and, like the timeout above, the ledger is KEPT: no write was attempted,
    // so nothing can have landed, so nothing can be published twice by keeping it.
    return null;
  }
  c.ops += 1;
  let published = 0;
  if (seen === CACHE_STALE) c.stale += 1;
  else if (typeof seen === "number") {
    c.hit += 1;
    published = seen;
  } else c.miss += 1;

  // The colo's hour, as best anybody knows it: what the entry says plus what this isolate
  // has spent and not yet said. `>=` rather than `chargeBudget`'s `used + cost > ceiling`
  // deliberately: this sub-tier's job is to notice what OTHER isolates spent, the local
  // cost is already accounted for by the in-isolate map that ran first, and every
  // difference between the two comparisons is in the permissive direction.
  if (published + owed >= ceiling) {
    c.refused += 1;
    return { retryAfterS: Math.max(1, resetAt - nowS) };
  }

  // ---- op 2: publish the ledger. SKIPPED ENTIRELY when there is nothing to publish,
  // which is the case for every request that was refused by its route body — that is the
  // free-drain property, and it costs an op rather than saving one to state it.
  if (owed > 0) {
    const wrote = await withDeadline(cfg.cacheTimeoutMs, () =>
      store.put(
        key,
        new Response(JSON.stringify({ n: published + owed }), {
          headers: {
            "Content-Type": "application/json",
            // One window, so an entry outlives its own bucket by at most an hour and then
            // evicts itself; the key already carries the bucket, so nothing stale can be
            // believed as this hour's.
            "Cache-Control": "max-age=" + SCALES.hour,
          },
        }),
      ),
    );
    if (wrote === CACHE_TIMEOUT) c.timeouts += 1;
    else if (wrote === CACHE_ERROR) c.errors += 1;
    else {
      c.ops += 1;
      c.wrote += 1;
    }
    // THE LEDGER IS CLEARED WHETHER OR NOT THE WRITE WAS CONFIRMED, and that asymmetry is
    // the point. A `put` that timed out may still have landed; keeping the units to retry
    // would then publish them TWICE, which is an overcount, which refuses somebody.
    // Forgetting them loses real spend, which is an undercount, which serves somebody.
    // Every unconfirmed outcome in this file resolves in the second direction.
    c.published += owed;
    clearPending(b);
  }
  c.allowed += 1;
  return null;
}

/** Forget the ledger, having attempted to publish it. Separate from `pendingUnits` so the
 *  two mutations read differently at their call sites: one rolls an hour, this one banks
 *  a write. */
function clearPending(b) {
  state.units.pending = 0;
  state.units.bucket = b;
}

/**
 * Grant the slot — consulting the shared tier first when there is one.
 *
 * NOT `async`, deliberately. With no cache (switched off, or a runtime without one) this
 * returns the very same object the pre-2026-09-05 code returned, from the same call, with
 * no promise and no extra microtask: "exactly as it does today" is a property of the code
 * path, not a claim about it.
 */
function grantOrShared(o, ctx) {
  const store = sharedStore(o, ctx.cfg);
  if (!store) return grantedSlot(ctx.route, ctx.capacity, ctx.win.rateLimit, ctx.budget);
  return sharedThenGrant(store, o, ctx);
}

/**
 * The async half of `grantOrShared`. Holds the slot across the cache round trips and hands
 * it back — to the next waiter, via `handOffOrRelease` — if either sub-tier refuses.
 *
 * THE TWO SUB-TIERS RUN IN `admit()`'s OWN ORDER: the per-IP window, then the unit budget.
 * That is deliberate and it costs something, so here is the trade in full. Checking the
 * budget FIRST would be cheaper when the hour is spent — one `match` instead of three ops,
 * and no pointless window `put` for a request about to be refused anyway. It would also
 * tell a visitor who is merely over their own minute that the whole DEPLOYMENT is out of
 * budget: `budget_exhausted` is a 503 that paints the page SCRIPTED for everyone
 * (`envelope.js`, §4.5), while `rate_limited` is a 429 the browser paces itself against.
 * Swapping a per-visitor condition for a deployment-wide one is a worse answer than a
 * wasted cache write, and it would make the two tiers disagree about which refusal a
 * visitor sees for the same request. Order preserved; the extra ops are the price.
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
        // The same `rateLimit` triple `admit()`'s own `budget_exhausted` sends, so the two
        // tiers are indistinguishable to a client and `chat.py`'s backoff needs no case.
        verdict = { retryAfterS: over.retryAfterS, rateLimit: win.rateLimit };
        reason = "budget_exhausted";
      }
    }
  } catch {
    // The last seatbelt. Both sub-tiers already swallow every cache error, so reaching here
    // means something else threw — a fake with a hostile surface, a URL that will not
    // parse, a `crypto.subtle` that is not there. It still may not cost a visitor their
    // turn: allow, and record it. Recorded on the tier's OUTER counter rather than on
    // either sub-tier's, because at this point there is no honest way to say which of them
    // threw and a guessed attribution is worse than none.
    state.stats.cache.errors += 1;
    verdict = null;
  }
  if (!verdict) return grantedSlot(route, capacity, win.rateLimit, budget);

  // Refused by the shared tier. Give the slot back FIRST — it is the scarce thing and
  // somebody may be queued for it — then refund the charge, exactly as the `at_capacity`
  // path does, so the request that was not served has not spent anything. Note what is NOT
  // here: nothing touches `state.units`. A request refused at this point never reaches
  // `release()` on a granted slot, so it never settles, so the colo is never told about it.
  handOffOrRelease(route);
  refundCharges(win.charged, budget.charged, budget.cost);
  return {
    ...refuse(reason, { retryAfterS: verdict.retryAfterS, rateLimit: verdict.rateLimit }),
    load: { inflight: state.inflight[route] || 0, capacity },
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
 * The refusals that can happen AFTER the windows and the budget were charged — the depth
 * cap, the wait expiring, and (since 2026-09-05) the shared cache tier — refund what they
 * charged. `refundCharges()` carries the whole argument for refunding rather than
 * reordering.
 *
 * **AND THE CACHE TIER RUNS LAST, AFTER THE SLOT IS TAKEN (2026-09-05).** It is the only
 * `await` on the fast path and the only one that touches anything outside this isolate.
 * `grantOrShared()` is the seam: with no cache it returns the same `grantedSlot()` this
 * function has always returned, synchronously. It now holds TWO sub-tiers behind that one
 * `await` — the per-IP minute window and the unit budget's hour, in that order. See
 * `sharedWindowVerdict` for what the tier is and what it is not,
 * `sharedBudgetVerdict` for why the budget half may not write a charge at admission, and
 * `sharedThenGrant` for why the two run in this order and not the cheaper one.
 *
 * @param {{request: Request, cfg: object, route: "chat"|"speech"|"transcribe", nowS?: number,
 *          cache?: {match: Function, put: Function}|null}} o `cache` is a TEST seam only:
 *   the routes never pass it, and on the real runtime the store is `caches.default`.
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

  // ---- The fast path, unchanged: a free slot and nobody ahead of us. The
  // `waiting.length === 0` half is belt to the hand-over's braces — because `release()`
  // conserves the count while anyone waits, `inflight` cannot drop below `capacity` with a
  // non-empty FIFO — but stating it here means a LATE ARRIVAL CANNOT OVERTAKE even if some
  // future edit breaks that invariant.
  if (waiting.length === 0 && (state.inflight[route] || 0) < capacity) {
    state.inflight[route] = (state.inflight[route] || 0) + 1;
    return grantOrShared(o, { route, capacity, cfg, win, budget, ip, nowS });
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
  return grantOrShared(o, { route, capacity, cfg, win, budget, ip, nowS });
}
