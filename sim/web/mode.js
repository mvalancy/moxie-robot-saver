/* mode.js — what this deployment can actually DO, asked rather than guessed.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §6.3 (the state machine and the poll
 * schedule), §7 (capacity signalling and the visitor-facing copy), §3.2 (the envelope),
 * §4.5 (what a 429/503 means).
 *
 * WHY THIS FILE EXISTS. Until now the page decided everything from the HOSTNAME
 * (`env.js`:12-14): any non-local host was assumed to be a backend-less static demo, and
 * the page said so — "hosted demo — only pre-scripted lines have audio" — whether that
 * was true or not. It also had no notion of gateway health at all: with a broker
 * connected and the brain dead the page rendered nothing, which is dead air. So this
 * module asks one same-origin route, `GET /api/health`, and publishes the answer as
 * `window.moxieMode`. `env.js` paints the badge, the pill and the `needs-backend` marks
 * from it. Nothing here decides what Moxie SAYS — that stays `bridge.js`/`stub.js`.
 *
 * THE THREE STATES (§6.3), and the guarantee each one carries:
 *
 *   offline  — `/api/health` is not there at all: a fork with no Functions, `file://`, a
 *              plain CDN, a 404. Behaviour and copy are BYTE-IDENTICAL TO TODAY, and
 *              nothing is polled again for the rest of the session. This is the promise
 *              that adding all of this cannot regress the existing site.
 *   degraded — the route exists and answered honestly. Scripted Moxie plus the reason,
 *              on screen. `gateway_not_configured` is sticky for the session (§4.5:
 *              "no poll storm"), so an unconfigured deployment fires exactly ONE request.
 *   live     — a live brain is configured and reachable, so the HTTP transport may be
 *              used. A 429 does NOT leave this state: a rate-limited visitor is not a
 *              broken deployment (§6.3, "soft degrade").
 *
 * HONESTY GUARD, and it is the point of the whole slice: `live` only ever *displays* as
 * live when something is actually loaded that can use it. P0-a ships this mode machine
 * alone; the live HTTP transport is `cloud-transport.js` in P0-b (§3.5), which sets
 * `window.moxieCloudTransport = true`. Until that file is present, a configured
 * deployment reads as SCRIPTED with a copy line that says exactly why — because painting
 * "LIVE" over a page that still answers from `stub.js` would be the precise dishonesty
 * this slice exists to remove.
 *
 * NO SECRET IS INVOLVED. The route never returns a gateway URL, a key, or a model id
 * (§4.2), and this file contains no hostname of any kind: the base is `location.origin`,
 * so a fork on any domain works with zero configuration (C3).
 */
(function () {
  "use strict";

  // ---- schedule (§6.3) -----------------------------------------------------
  var POLL_MIN_MS = 30000;      // the floor, and the value any success resets to
  var POLL_MAX_MS = 300000;     // the 5-minute ceiling the backoff doubles up to
  var PROBE_TIMEOUT_MS = 6000;  // a probe that costs nothing may still hang
  var STRIKES_TO_DEGRADE = 3;   // consecutive transport errors before live -> degraded

  // ---- the closed reason set (§3.2). Anything else is treated as unknown. ----
  // `gateway_unreachable_or_gated` is P0-b's one addition to §3.2's set: the gateway is
  // expected to live behind a Cloudflare Tunnel, and a tunnel behind Cloudflare Access
  // answers a server-side fetch with an HTML LOGIN PAGE AND A 200 — so the Function
  // distinguishes "the brain is down" from "the door in front of it is locked"
  // (functions/api/_lib/envelope.js says why). It MUST be listed here: an unknown reason
  // is coerced to `null` below, which `note()` would then read as a HEALTHY turn.
  // The visitor sees the same badge and the same copy as `upstream_down`; only an
  // operator reading the reason learns anything.
  var REASONS = ["rate_limited", "at_capacity", "budget_exhausted", "upstream_down",
                 "gateway_unreachable_or_gated",
                 "gateway_not_configured", "timeout", "bad_request", "too_long",
                 "too_short", "bad_ticket", "blocked", "forbidden_origin"];

  // ---- §7's visitor-facing copy, and it lives HERE rather than on the server ----
  // Two reasons. It has to be honest in `offline` too, where there is no server to send
  // a string; and a raw status code or an upstream error string must never reach a
  // visitor, which is easiest to guarantee when the visitor's words are all local.
  var BADGE_PLAIN = "HOSTED DEMO";
  var BADGE_LIVE = "HOSTED DEMO · LIVE";
  var BADGE_BUSY = "HOSTED DEMO · BUSY";
  var BADGE_SCRIPTED = "HOSTED DEMO · SCRIPTED";
  var COPY = {
    busy: "Moxie is talking with a few other people right now — answers may take a moment.",
    full: "Moxie has her hands full right now. She’s answering from her scripted repertoire until a slot opens.",
    budget_exhausted: "Moxie’s live brain has used up today’s demo budget. Everything you see still works — she’s speaking from her recorded lines.",
    unreachable: "Moxie’s brain is unreachable right now — she’s running on what she remembers.",
    rate_limited: "One at a time! Give Moxie a few seconds.",
    // Not in §7's table because §7 assumes the transport exists. P0-a ships without it,
    // and saying nothing would be the dishonest option.
    no_transport: "Moxie’s live brain is configured, but this build has no live transport yet — she’s answering from her recorded lines.",
  };

  // ---- state ---------------------------------------------------------------
  var state = "boot";
  var reason = null;
  var limits = {};
  var load = { level: "ok", inflight: 0, capacity: 0 };
  var voice = false, ears = false;
  var sticky = false;            // offline, and gateway_not_configured: never poll again
  var suppressUntil = 0;         // a 429/503 Retry-After window: no live turns until then
  var strikes = 0;               // consecutive transport errors (§6.3)
  var delay = POLL_MIN_MS;
  var timer = null;
  var hiddenSkip = false;        // a poll fell due while the tab was hidden
  var listeners = [];
  var lastKey = "";
  // Recorded, not sampled: every test asserts on these rather than on a live timing
  // (sim/test_mode.mjs). A poll that already happened is a fact; one that is about to is
  // a bet.
  var stats = { polls: 0, usable: 0, unusable: 0, absent: 0, transportErrors: 0,
                hiddenSkips: 0, notes: 0, lastDelayMs: 0, scheduled: [], transitions: [] };

  function now() { return Date.now(); }

  function hasTransport() {
    try { return !!window.moxieCloudTransport; } catch (e) { return false; }
  }

  /** The same-origin base for `/api/*`, or null where there cannot be one (`file://`). */
  function apiBase() {
    try {
      if (!/^https?:$/.test(location.protocol)) return null;
      return location.origin;
    } catch (e) { return null; }
  }

  /** Are live turns spendable right now? The transport (P0-b) asks before every turn. */
  function canSpendLiveTurn() {
    return state === "live" && hasTransport() && now() >= suppressUntil;
  }

  function retryAfterS() {
    var left = Math.ceil((suppressUntil - now()) / 1000);
    return left > 0 ? left : 0;
  }

  /** §7's table, as one pure function of the state. */
  function surface() {
    if (state === "live") {
      if (!hasTransport()) return { badge: BADGE_SCRIPTED, message: COPY.no_transport };
      if (now() < suppressUntil && reason === "rate_limited")
        return { badge: BADGE_LIVE, message: COPY.rate_limited };
      if (load.level === "full") return { badge: BADGE_BUSY, message: COPY.full };
      if (load.level === "busy") return { badge: BADGE_BUSY, message: COPY.busy };
      return { badge: BADGE_LIVE, message: "" };
    }
    if (state === "degraded") {
      if (reason === "budget_exhausted")
        return { badge: BADGE_SCRIPTED, message: COPY.budget_exhausted };
      if (reason === "upstream_down" || reason === "timeout" ||
          reason === "gateway_unreachable_or_gated")
        return { badge: BADGE_SCRIPTED, message: COPY.unreachable };
      if (reason === "at_capacity") return { badge: BADGE_BUSY, message: COPY.full };
      // gateway_not_configured (and anything unknown): today's copy, unchanged. §7 is
      // explicit that this row keeps the existing wording — env.js owns it.
      return { badge: BADGE_PLAIN, message: "" };
    }
    // boot and offline: today's page, exactly.
    return { badge: BADGE_PLAIN, message: "" };
  }

  function snapshot() {
    var s = surface();
    return {
      state: state,
      reason: reason,
      badge: s.badge,
      message: s.message,
      level: load.level,
      load: { level: load.level, inflight: load.inflight, capacity: load.capacity },
      limits: limits,
      voice: voice,
      ears: ears,
      liveTurns: canSpendLiveTurn(),
      retryAfterS: retryAfterS(),
    };
  }

  function emit() {
    var snap = snapshot();
    // Only fire on a real change: env.js re-renders on every event, and a 30-second
    // heartbeat that changed nothing must not repaint the topbar.
    var key = [snap.state, snap.reason, snap.badge, snap.message, snap.level,
               snap.voice, snap.ears, snap.liveTurns].join("|");
    if (key === lastKey) return;
    lastKey = key;
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](snap); } catch (e) {}
    }
  }

  function setState(next, why) {
    if (next === state && why === reason) return;
    stats.transitions.push(state + "->" + next + (why ? ":" + why : ""));
    state = next;
    reason = why || null;
    if (next === "offline" || (next === "degraded" && why === "gateway_not_configured"))
      sticky = true;
    emit();
  }

  // ---- polling -------------------------------------------------------------
  function clear() { if (timer !== null) { clearTimeout(timer); timer = null; } }

  function schedule(ms) {
    clear();
    if (sticky) return;                       // offline / not-configured: no poll storm
    var wait = Math.max(1000, Math.min(POLL_MAX_MS, Math.round(ms)));
    stats.lastDelayMs = wait;
    stats.scheduled.push(wait);
    timer = setTimeout(tick, wait);
  }

  function hidden() {
    try { return !!(document && document.hidden); } catch (e) { return false; }
  }

  function tick() {
    timer = null;
    // Never poll while the tab is hidden — the rule ambient.js:77 already follows. The
    // due poll is not lost: `visibilitychange` runs it the moment the tab comes back.
    if (hidden()) { hiddenSkip = true; stats.hiddenSkips++; return; }
    poll();
  }

  function backoff() {
    delay = Math.min(POLL_MAX_MS, delay * 2);
    return delay;
  }

  /** Read one envelope. Returns null when the reply is not a usable envelope at all. */
  function parseEnvelope(text) {
    var body;
    try { body = JSON.parse(text); } catch (e) { return null; }
    if (!body || typeof body !== "object" || Array.isArray(body)) return null;
    // A static host that answers 200 with its index page, or any body without a mode, is
    // not this route. Treated as absent rather than believed.
    if (body.mode !== "live" && body.mode !== "degraded") return null;
    return body;
  }

  function applyEnvelope(body) {
    var r = body.reason === null || body.reason === undefined ? null : String(body.reason);
    if (r !== null && REASONS.indexOf(r) === -1) r = null;
    limits = (body.limits && typeof body.limits === "object" && !Array.isArray(body.limits))
      ? body.limits : {};
    var l = body.load && typeof body.load === "object" ? body.load : {};
    load = {
      level: ["ok", "busy", "full"].indexOf(l.level) !== -1 ? l.level : "ok",
      inflight: isFinite(Number(l.inflight)) ? Number(l.inflight) : 0,
      capacity: isFinite(Number(l.capacity)) ? Number(l.capacity) : 0,
    };
    voice = !!body.voice;
    ears = !!body.ears;
    var retry = Number(body.retry_after_s);
    setState(body.mode === "live" ? "live" : "degraded", r);
    emit();                                   // load/limits can change with no state change
    return isFinite(retry) && retry > 0 ? retry * 1000 : 0;
  }

  function poll() {
    var base = apiBase();
    if (base === null) { setState("offline", null); return Promise.resolve(snapshot()); }
    stats.polls++;
    var opt = { cache: "no-store", credentials: "omit", headers: { Accept: "application/json" } };
    try {
      if (typeof AbortSignal !== "undefined" && AbortSignal.timeout)
        opt.signal = AbortSignal.timeout(PROBE_TIMEOUT_MS);
    } catch (e) {}
    return fetch(base + "/api/health", opt).then(function (r) {
      // The route is contractually always 200 (health.js), so a 404/405/501 means the
      // route is ABSENT — a fork with no Functions, or a plain CDN.
      if (r.status === 404 || r.status === 405 || r.status === 501) {
        stats.absent++;
        absent();
        return snapshot();
      }
      return r.text().then(function (text) {
        var body = parseEnvelope(text);
        if (!body) { stats.unusable++; unusable(); return snapshot(); }
        stats.usable++;
        strikes = 0;
        delay = POLL_MIN_MS;                  // any success resets the backoff (§6.3)
        var retryMs = applyEnvelope(body);
        schedule(retryMs || delay);           // Retry-After when the server sent one
        return snapshot();
      });
    }).catch(function () {
      // A network error is indistinguishable from an absent route at boot, and both are
      // the same outcome for a visitor: today's page.
      stats.unusable++;
      unusable();
      return snapshot();
    });
  }

  /** The route is not there. Byte-identical-to-today, forever, no more requests. */
  function absent() { clear(); setState("offline", null); }

  /** A reply we cannot read, or a network failure. */
  function unusable() {
    if (state === "boot") { absent(); return; }        // never claim a route we can't read
    if (state === "live") {
      strikes++;
      stats.transportErrors++;
      if (strikes >= STRIKES_TO_DEGRADE) { setState("degraded", "upstream_down"); }
    }
    schedule(backoff());
  }

  // ---- what the transport reports back (§4.5) ------------------------------
  /**
   * The live transport calls this after every `/api/*` reply so the mode follows reality
   * without waiting for the next poll. P0-b's cloud-transport.js is the caller.
   * @param {{status?:number, reason?:string, retry_after_s?:number}} res
   */
  function note(res) {
    stats.notes++;
    var r = res && res.reason ? String(res.reason) : null;
    if (r !== null && REASONS.indexOf(r) === -1) r = null;
    var retry = Number(res && res.retry_after_s);
    var retryMs = isFinite(retry) && retry > 0 ? retry * 1000 : 0;

    if (r === "forbidden_origin") { absent(); return snapshot(); }   // §4.5: treated as offline
    if (r === "gateway_not_configured") { setState("degraded", r); clear(); return snapshot(); }
    if (r === "budget_exhausted" || r === "upstream_down" || r === "gateway_unreachable_or_gated") {
      setState("degraded", r);
      schedule(retryMs || POLL_MIN_MS);
      return snapshot();
    }
    if (r === "rate_limited") {
      // SOFT degrade (§6.3): the mode STAYS live. A rate-limited visitor is not a broken
      // deployment. This turn is answered from the stub and live turns resume after
      // Retry-After. Strikes reset: a 429 is a healthy server saying "not so fast".
      strikes = 0;
      suppressUntil = now() + (retryMs || 10000);
      reason = "rate_limited";
      emit();
      return snapshot();
    }
    if (r === "at_capacity") {
      // §6.3's "live -> degraded on 503" and §4.5's at_capacity row disagree; §7 settles
      // it by giving at_capacity the BUSY badge in the *live* row. So capacity is a load
      // signal, not a broken deployment: stay live, show BUSY, and stop spending until
      // Retry-After.
      strikes = 0;
      suppressUntil = now() + (retryMs || 15000);
      load = { level: "full", inflight: load.capacity, capacity: load.capacity };
      if (state === "live") { reason = "at_capacity"; emit(); }
      else setState("degraded", "at_capacity");
      schedule(retryMs || 15000);
      return snapshot();
    }
    if (r === "timeout") {                    // §4.5: counts toward the 3-strike degrade
      unusable();
      return snapshot();
    }
    if (r === "bad_request" || r === "too_long" || r === "too_short" ||
        r === "bad_ticket" || r === "blocked") {
      return snapshot();                      // input/safety outcome: never a mode change
    }
    // A clean turn: the deployment is healthy.
    strikes = 0;
    delay = POLL_MIN_MS;
    if (state === "degraded" && !sticky) { setState("live", null); schedule(delay); }
    return snapshot();
  }

  /** A transport error with no envelope at all (§6.3: 3 consecutive -> degraded). */
  function noteTransportError() { stats.notes++; unusable(); return snapshot(); }

  // ---- wiring --------------------------------------------------------------
  try {
    if (document && document.addEventListener) {
      document.addEventListener("visibilitychange", function () {
        if (hidden() || sticky) return;
        if (hiddenSkip || timer === null) { hiddenSkip = false; poll(); }
      });
    }
  } catch (e) {}

  window.moxieMode = {
    state: function () { return state; },
    reason: function () { return reason; },
    badge: function () { return surface().badge; },
    message: function () { return surface().message; },
    load: function () { return { level: load.level, inflight: load.inflight, capacity: load.capacity }; },
    limits: function () { return limits; },
    voice: function () { return voice; },
    ears: function () { return ears; },
    apiBase: apiBase,
    hasTransport: hasTransport,
    canSpendLiveTurn: canSpendLiveTurn,
    retryAfterS: retryAfterS,
    note: note,
    noteTransportError: noteTransportError,
    snapshot: snapshot,
    refresh: poll,
    stats: function () { return JSON.parse(JSON.stringify(stats)); },
    onChange: function (fn) {
      if (typeof fn !== "function") return function () {};
      listeners.push(fn);
      try { fn(snapshot()); } catch (e) {}
      return function () {
        var i = listeners.indexOf(fn);
        if (i !== -1) listeners.splice(i, 1);
      };
    },
  };

  // First probe immediately — via tick(), so the "never poll while hidden" rule holds for
  // the very first request too: a page opened in a background tab shows today's copy
  // (which is what `boot` deliberately is) and asks the moment it is looked at.
  tick();
})();
