/* functions/api/probe.js — TEMPORARY. DELETE BEFORE MERGE.
 *
 * A throwaway diagnostic that answers §10 assumption 13 as far as the RUNTIME can answer
 * it, using the one route this project already knows settles Cloudflare questions without
 * an owner: every branch push publishes a public preview, so a `curl` is a measurement.
 * Assumptions 8, 9 and 27 were all closed exactly this way on 2026-09-03.
 *
 * THE QUESTION. `functions/api/_lib/limits.js` keeps one `Map` per counter in module
 * scope and consults nothing else, so every cap in §4.1 is enforced once per ISOLATE and
 * the true ceiling is N x the configured number (§4.6). Two things follow, and this file
 * measures both:
 *
 *   1. Is the **Cache API** usable here at all — and does a `put` survive into a LATER
 *      request, and into a DIFFERENT isolate? That is the whole question for a second,
 *      binding-free counter tier. It needs no account change and no owner action, so if
 *      the answer is yes it is buildable today.
 *   2. Is N observable from inside a Function? A per-isolate id minted lazily in module
 *      scope, plus `request.cf.colo`, lets a handful of curls COUNT the isolates and
 *      colos they actually reached, which turns "N is chosen by the platform" from an
 *      unquantified worry into a number with a method behind it.
 *
 * WHAT IT DELIBERATELY CANNOT ANSWER, said here so no reader over-reads the output:
 * whether KV, Durable Objects or the WAF Rate Limiting product are available ON THIS PLAN
 * is a dashboard fact. A Function can only see bindings that are ALREADY configured. An
 * empty binding list means "none configured", never "none available". `bindings` below is
 * therefore evidence for exactly one half of assumption 13.
 *
 * SAFETY RULES THIS FILE KEEPS, because it is an unauthenticated endpoint on a public host:
 *   - It NEVER reports a binding VALUE. Only the key name, `typeof`, the constructor name
 *     and the prototype's method names. A binding can be a secret; its name cannot leak it
 *     (and §5 of the spec publishes every name this project uses anyway).
 *   - It makes NO upstream call, spends nothing, and imports nothing from `_lib/`, so
 *     deleting this one file removes it completely.
 *   - Everything it writes to the cache is diagnostic and short-lived: isolate ids, colo
 *     codes, counts, `max-age=120`.
 *
 * USAGE
 *   GET /api/probe                 -> measure, and bump the shared cache counter
 *   GET /api/probe?run=<slug>      -> use a separate cache key for this experiment run
 *   GET /api/probe?reset=1         -> delete this run's cache key first (fresh start)
 *   GET /api/probe?nowrite=1       -> read the cache entry without bumping it
 *
 * Reply headers `X-Probe-Isolate` and `X-Probe-Colo` exist so a shell loop can tally
 * isolates and colos without a JSON parser.
 */

/* ------------------------------------------------------------------ isolate identity --
 * Minted LAZILY rather than at module top level. The value is per-isolate either way, and
 * the runtime restricts what may run in the global scope, so the first request does it. */
let ISOLATE_ID = null;
let ISOLATE_BORN_MS = 0;
let ISOLATE_REQUESTS = 0;

function isolate() {
  if (ISOLATE_ID === null) {
    let raw;
    try {
      const b = new Uint8Array(6);
      crypto.getRandomValues(b);
      raw = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    } catch {
      raw = Math.random().toString(36).slice(2, 14);
    }
    ISOLATE_ID = raw;
    ISOLATE_BORN_MS = Date.now();
  }
  return ISOLATE_ID;
}

/** Cheap, bounded de-dupe that keeps insertion order — the writer lists below are read by
 *  a human, so oldest-first with a cap is the useful shape. */
function pushUnique(list, value, cap) {
  const out = Array.isArray(list) ? list.slice(0, cap) : [];
  if (value && !out.includes(value)) out.push(value);
  return out.slice(-cap);
}

const err = (e) => String((e && e.message) || e).slice(0, 200);

/* ------------------------------------------------------------------------- the route -- */
export async function onRequestGet(context) {
  const { request, env } = context;
  const me = isolate();
  ISOLATE_REQUESTS += 1;

  const url = new URL(request.url);
  const run = (url.searchParams.get("run") || "default").replace(/[^a-z0-9-]/gi, "").slice(0, 24) || "default";
  const reset = url.searchParams.get("reset") === "1";
  const nowrite = url.searchParams.get("nowrite") === "1";

  const cf = request.cf || null;
  const colo = (cf && cf.colo) || null;

  /* ---- 1. runtime surface -------------------------------------------------------- */
  const runtime = {
    userAgent: (typeof navigator !== "undefined" && navigator && navigator.userAgent) || null,
    hasCaches: typeof caches !== "undefined",
    hasCachesDefault: typeof caches !== "undefined" && !!caches.default,
    hasCachesOpen: typeof caches !== "undefined" && typeof caches.open === "function",
    hasCryptoSubtle: typeof crypto !== "undefined" && !!crypto.subtle,
    hasCf: !!cf,
    cfKeys: cf ? Object.keys(cf).length : 0,
    colo,
    country: (cf && cf.country) || null,
    httpProtocol: (cf && cf.httpProtocol) || null,
    contextKeys: Object.keys(context).sort(),
  };

  /* ---- 2. bindings — NAMES AND SHAPES ONLY, NEVER VALUES -------------------------- */
  const bindings = [];
  let bindingError = null;
  try {
    for (const name of Object.keys(env || {})) {
      const entry = { name, type: "unknown" };
      try {
        const v = env[name];
        entry.type = typeof v;
        if (v && (entry.type === "object" || entry.type === "function")) {
          entry.ctor = (v.constructor && v.constructor.name) || null;
          const proto = Object.getPrototypeOf(v);
          /* `Object.prototype` is noise, not a binding shape — a plain object tells us
           * nothing, while a KV namespace or a Durable Object namespace has a named
           * prototype whose METHOD NAMES are the giveaway (`get`/`put`/`list` vs
           * `idFromName`/`get`). Method names are not values, so this leaks nothing. */
          entry.methods =
            proto && proto !== Object.prototype
              ? Object.getOwnPropertyNames(proto).filter((n) => n !== "constructor").sort().slice(0, 24)
              : [];
        }
      } catch (e) {
        entry.readError = err(e);
      }
      bindings.push(entry);
    }
  } catch (e) {
    bindingError = err(e);
  }
  /* The shapes a P1 single-writer counter could actually be built on, if one were bound. */
  const STATEFUL = /KvNamespace|DurableObject|D1Database|R2Bucket|Queue|AnalyticsEngine|RateLimit|Hyperdrive/i;
  const stateful = bindings
    .filter((b) => (b.ctor && STATEFUL.test(b.ctor)) || STATEFUL.test(b.name))
    .map((b) => ({ name: b.name, ctor: b.ctor || null }));

  /* ---- 3. the Cache API, in three separate questions ------------------------------ */
  const cache = {
    reachable: false,
    sameRequestPutVisible: null, // does a put land in time for a match in THIS request?
    crossRequestHit: null, // did a PREVIOUS request's write survive?
    crossIsolateHit: null, // ...and was that previous write made by ANOTHER isolate?
    ageSeconds: null,
    previous: null,
    wrote: false,
    errors: {},
  };

  const keyFor = (suffix) => {
    const k = new URL(url.origin);
    k.pathname = `/__probe/${suffix}`;
    return k.toString();
  };
  const jsonResponse = (obj, maxAge) =>
    new Response(JSON.stringify(obj), {
      headers: { "content-type": "application/json", "cache-control": `max-age=${maxAge}` },
    });

  if (runtime.hasCachesDefault) {
    const store = caches.default;
    cache.reachable = true;

    /* 3a. Same-request visibility. A unique key, so nothing else can answer it. */
    try {
      const oneShot = keyFor(`same-${me}-${ISOLATE_REQUESTS}`);
      await store.put(oneShot, jsonResponse({ probe: "same-request" }, 60));
      cache.sameRequestPutVisible = !!(await store.match(oneShot));
    } catch (e) {
      cache.errors.sameRequest = err(e);
    }

    /* 3b/3c. The shared counter — the load-bearing measurement. Read, then write back
     *        with this isolate recorded as the writer. Deliberately a plain
     *        read-modify-write with no locking, because that is exactly what a Cache-API
     *        counter tier WOULD be: two concurrent requests can both read n and both
     *        write n+1, and the reported `writes` undercounting is itself a finding. */
    const sharedKey = keyFor(`counter-${run}`);
    try {
      if (reset) await store.delete(sharedKey);
    } catch (e) {
      cache.errors.reset = err(e);
    }
    let prev = null;
    try {
      const hit = await store.match(sharedKey);
      cache.crossRequestHit = !!hit;
      if (hit) {
        const age = hit.headers.get("age");
        cache.ageSeconds = age === null ? null : Number(age);
        prev = await hit.json();
      }
    } catch (e) {
      cache.errors.match = err(e);
      cache.crossRequestHit = null;
    }
    if (prev && typeof prev === "object") {
      cache.previous = {
        writes: prev.writes ?? null,
        lastWriter: prev.lastWriter ?? null,
        writers: Array.isArray(prev.writers) ? prev.writers : [],
        writerCount: Array.isArray(prev.writers) ? prev.writers.length : null,
        colos: Array.isArray(prev.colos) ? prev.colos : [],
        firstAtMs: prev.firstAtMs ?? null,
        lastAtMs: prev.lastAtMs ?? null,
      };
      cache.crossIsolateHit = prev.lastWriter ? prev.lastWriter !== me : null;
    }
    if (!nowrite) {
      const now = Date.now();
      const next = {
        writes: ((prev && prev.writes) || 0) + 1,
        lastWriter: me,
        writers: pushUnique(prev && prev.writers, me, 40),
        colos: pushUnique(prev && prev.colos, colo, 20),
        firstAtMs: (prev && prev.firstAtMs) || now,
        lastAtMs: now,
      };
      try {
        await store.put(sharedKey, jsonResponse(next, 120));
        cache.wrote = true;
      } catch (e) {
        cache.errors.put = err(e);
      }
    }
  }

  /* ---- 4. the reply ---------------------------------------------------------------- */
  const body = {
    probe: "counter-probe",
    note: "TEMPORARY diagnostic for live-sim-demo.md sec 10 assumption 13. Delete before merge.",
    run,
    isolate: {
      id: me,
      requestsServedByThisIsolate: ISOLATE_REQUESTS,
      /* Date.now() only advances on I/O in this runtime, so this is a LOWER BOUND on how
       * long the isolate has been alive — useful for ordering, not for billing. */
      apparentAgeMs: Date.now() - ISOLATE_BORN_MS,
    },
    runtime,
    bindings: { count: bindings.length, entries: bindings, stateful, error: bindingError },
    cache,
  };

  return new Response(JSON.stringify(body, null, 2), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      /* The probe's OWN reply must never be served from a cache, or repeated curls would
       * measure the CDN instead of the runtime. */
      "cache-control": "no-store, no-cache, must-revalidate",
      "cdn-cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "same-origin",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
      "x-probe-isolate": me,
      "x-probe-colo": colo || "unknown",
    },
  });
}
