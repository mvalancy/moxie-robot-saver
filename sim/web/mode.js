/* mode.js — what this deployment can actually DO, asked rather than guessed.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §6.3 (state machine, poll schedule),
 * §7 (capacity signalling + copy), §3.2 (envelope), §4.5 (what a 429/503 means).
 *
 * Asks one same-origin route, `GET /api/health`, and publishes `window.moxieMode`; env.js
 * paints from it. Nothing here decides what Moxie SAYS (bridge/, stub.js).
 *
 *   offline  — no /api/health (fork without Functions, file://, plain CDN, 404): the page
 *              behaves exactly as without this module, and is never polled again.
 *   degraded — the route answered honestly: scripted Moxie plus the reason.
 *              `gateway_not_configured` is sticky, so an unconfigured deployment fires
 *              exactly ONE request (no poll storm).
 *   live     — a live brain is reachable. A 429 does not leave `live` (soft degrade).
 *
 * HONESTY GUARD: `live` only DISPLAYS as live when cloud-transport.js has loaded
 * (`window.moxieCloudTransport`); otherwise it reads SCRIPTED with a line saying why.
 *
 * No secret and no hostname (base = `location.origin`). The one public value returned is
 * the Turnstile SITEKEY ("" = not enforced), delivered at runtime so forks and previews
 * never render this deployment's widget.
 */
(function () {
  "use strict";

  // ---- schedule (§6.3) -----------------------------------------------------
  var POLL_MIN_MS = 30000;      // the floor, and the value any success resets to
  var POLL_MAX_MS = 300000;     // the 5-minute ceiling the backoff doubles up to
  var PROBE_TIMEOUT_MS = 6000;  // a probe that costs nothing may still hang
  var STRIKES_TO_DEGRADE = 3;   // consecutive transport errors before live -> degraded

  // ---- the closed reason set (§3.2). Anything else is treated as unknown. ----
  // EVERY reason the server can send MUST be listed: an unknown one is coerced to null,
  // and note() would read a refused turn as HEALTHY (painting LIVE over a deployment whose
  // turns are all refused). functions/api/_lib/envelope.js is the other half of this list.
  // `gateway_unreachable_or_gated` = Cloudflare Access answering with a login page + 200.
  var REASONS = ["rate_limited", "at_capacity", "budget_exhausted", "upstream_down",
                 "gateway_unreachable_or_gated",
                 "gateway_not_configured", "timeout", "bad_request", "too_long",
                 "too_short", "bad_ticket", "blocked", "forbidden_origin",
                 "turnstile_failed", "turnstile_misconfigured"];

  // ---- §7's visitor-facing copy lives HERE, not on the server: it must be honest in
  // `offline` too, and no raw status or upstream error string may reach a visitor.
  var BADGE_PLAIN = "HOSTED DEMO";
  var BADGE_LIVE = "MOXIE ONLINE";
  var BADGE_BUSY = "HOSTED DEMO · BUSY";
  var BADGE_SCRIPTED = "HOSTED DEMO · SCRIPTED";
  var COPY = {
    busy: "Moxie is talking with a few other people right now — answers may take a moment.",
    full: "Moxie has her hands full right now. She’s answering from her scripted repertoire until a slot opens.",
    budget_exhausted: "Moxie’s live brain has used up today’s demo budget. Everything you see still works — she’s speaking from her recorded lines.",
    unreachable: "Moxie’s brain is unreachable right now — she’s running on what she remembers.",
    rate_limited: "One at a time! Give Moxie a few seconds.",
    // Not in §7 (which assumes the transport exists); saying nothing would be dishonest.
    no_transport: "Moxie’s live brain is configured, but this build has no live transport yet — she’s answering from her recorded lines.",
    // turnstile_failed is per-turn (tokens expire/are single-use) and the page stays live;
    // turnstile_misconfigured refuses every visitor until fixed, so it reads scripted.
    turnstile_failed: "Moxie needs to check you’re a real person — give that another try.",
    turnstile_misconfigured: "Moxie’s visitor check isn’t set up right on this deployment, so she’s answering from her recorded lines.",
  };

  // ---- state ---------------------------------------------------------------
  var state = "boot";
  var reason = null;
  var limits = {};
  var load = { level: "ok", inflight: 0, capacity: 0 };
  var voice = false, ears = false;
  // The PUBLIC Turnstile sitekey, or "" = not enforced: the whole switch for the widget.
  var turnstile = "";
  var sticky = false;            // offline, and gateway_not_configured: never poll again
  var suppressUntil = 0;         // a 429/503 Retry-After window: no live turns until then
  var strikes = 0;               // consecutive transport errors (§6.3)
  var delay = POLL_MIN_MS;
  var timer = null;
  var hiddenSkip = false;        // a poll fell due while the tab was hidden
  var listeners = [];
  var lastKey = "";
  // Recorded, not sampled: tests assert on these, never on live timing (test_mode.mjs).
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

  /** Are live turns spendable right now? The transport asks before every turn. */
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
      // Still LIVE: a failed bot check is this turn's problem, not the deployment's.
      if (reason === "turnstile_failed")
        return { badge: BADGE_LIVE, message: COPY.turnstile_failed };
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
      // Own copy: the brain is fine, the DOOR is misconfigured.
      if (reason === "turnstile_misconfigured")
        return { badge: BADGE_SCRIPTED, message: COPY.turnstile_misconfigured };
      if (reason === "at_capacity") return { badge: BADGE_BUSY, message: COPY.full };
      // gateway_not_configured (and anything unknown): plain copy; env.js owns the wording.
      return { badge: BADGE_PLAIN, message: "" };
    }
    // boot and offline: the plain page.
    return { badge: BADGE_PLAIN, message: "" };
  }

  function snapshot() {
    var s = surface();
    return {
      state: state, reason: reason, badge: s.badge, message: s.message, level: load.level,
      load: { level: load.level, inflight: load.inflight, capacity: load.capacity },
      limits: limits, voice: voice, ears: ears, turnstile: turnstile,
      liveTurns: canSpendLiveTurn(), retryAfterS: retryAfterS(),
    };
  }

  function emit() {
    var snap = snapshot();
    // Only fire on a real change: a heartbeat that changed nothing must not repaint.
    var key = [snap.state, snap.reason, snap.badge, snap.message, snap.level,
               snap.voice, snap.ears, snap.turnstile, snap.liveTurns].join("|");
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
    // Never poll a hidden tab; `visibilitychange` runs the due poll when it returns.
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
    // A 200 without a mode (e.g. a static index page) is not this route.
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
    turnstile = typeof body.turnstile === "string" ? body.turnstile : "";
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
      // The route is always 200 (health.js), so 404/405/501 means it is ABSENT.
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
      // At boot a network error is the same outcome as an absent route: the plain page.
      stats.unusable++;
      unusable();
      return snapshot();
    });
  }

  /** The route is not there: the plain page, forever, no more requests. */
  function absent() { clear(); setState("offline", null); }

  /** A reply we cannot read, or a network failure. */
  function unusable() {
    if (state === "boot") { absent(); return; }        // never claim a route we can't read
    if (state === "live") {
      strikes++;
      stats.transportErrors++;
      if (strikes >= STRIKES_TO_DEGRADE) setState("degraded", "upstream_down");
    }
    schedule(backoff());
  }

  // ---- what the transport reports back (§4.5) ------------------------------
  /** cloud-transport.js calls this after every `/api/*` reply so the mode follows reality
   *  without waiting for the next poll. @param {{status?, reason?, retry_after_s?}} res */
  function note(res) {
    stats.notes++;
    var r = res && res.reason ? String(res.reason) : null;
    if (r !== null && REASONS.indexOf(r) === -1) r = null;
    var retry = Number(res && res.retry_after_s);
    var retryMs = isFinite(retry) && retry > 0 ? retry * 1000 : 0;

    if (r === "forbidden_origin") { absent(); return snapshot(); }   // §4.5: treated as offline
    if (r === "gateway_not_configured") { setState("degraded", r); clear(); return snapshot(); }
    // A misconfigured bot control degrades like a dead gateway, polled off Retry-After.
    if (r === "budget_exhausted" || r === "upstream_down" ||
        r === "gateway_unreachable_or_gated" || r === "turnstile_misconfigured") {
      setState("degraded", r);
      schedule(retryMs || POLL_MIN_MS);
      return snapshot();
    }
    if (r === "rate_limited") {
      // SOFT degrade (§6.3): stay live, answer this turn from the stub, resume after
      // Retry-After. A 429 is a healthy server saying "not so fast", so strikes reset.
      strikes = 0;
      suppressUntil = now() + (retryMs || 10000);
      reason = "rate_limited";
      emit();
      return snapshot();
    }
    if (r === "at_capacity") {
      // §7 gives at_capacity the BUSY badge in the LIVE row: a load signal, not a broken
      // deployment. Stay live, show BUSY, stop spending until Retry-After.
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
    if (r === "turnstile_failed") {
      // SOFT like rate_limited, but NO suppression window: the next send mints a fresh
      // token immediately. `reason` lets the LIVE badge carry the "try again" line.
      strikes = 0;
      reason = "turnstile_failed";
      emit();
      return snapshot();
    }
    // A clean turn: healthy. It also clears a lingering turnstile_failed note now (that
    // note has no suppression window to expire).
    strikes = 0;
    delay = POLL_MIN_MS;
    if (state === "live" && reason === "turnstile_failed") { reason = null; emit(); }
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
    /** PUBLIC Turnstile sitekey, or "" (not enforced). turnstile.js is the only caller. */
    turnstile: function () { return turnstile; },
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

  // First probe now, via tick(), so a background tab also waits until it is looked at.
  tick();
})();
