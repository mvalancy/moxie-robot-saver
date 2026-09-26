/* turnstile.js — the browser half of the bot control: one fresh token per send, per route.
 *
 * Server half: `functions/api/_lib/turnstile.js`, `functions/api/chat.js` step 7 and
 * `functions/api/transcribe.js` step 4d.
 *
 * Publishes `window.moxieTurnstile.getToken(action)`; `cloud-transport.js` and `mic.js` each
 * call it on ONE line of their send path, so the whole widget lifecycle (load, render,
 * reset, time out, give up) lives here behind a promise of a string.
 *
 * ONE WIDGET PER ACTION. The server requires a different `action` per spending route so a
 * token minted for a cheap typed turn cannot pay for a costly microphone turn. An action is
 * fixed at `render()` time (`execute()` takes no parameters), so there are two widgets in
 * `SLOTS`. The chat widget renders eagerly (typing comes first); the mic one on first use.
 *
 * NO CHECKBOX: `appearance: "interaction-only"` draws nothing unless Cloudflare wants an
 * interaction, and `execution: "execute"` runs the challenge when we ask. Tokens are
 * SINGLE-USE and expire in 300 s, so one is minted PER SEND (`reset()` then `execute()`);
 * a per-page-load token would break on the second sentence.
 *
 * INERT WITHOUT A SITEKEY. The sitekey arrives from the server via `window.moxieMode`. With
 * none (a fork, `file://`, a branch preview whose host is not on the widget's domain list)
 * this loads no script, adds no element and resolves `getToken()` to `""` (C3). A sitekey is
 * public; the secret stays in the Function's environment.
 */
(function () {
  "use strict";

  /* Cloudflare's widget script in EXPLICIT mode, so we get `turnstile.render()` with an
   * `action` and `execution`. `_headers` must allow this host in script-src, frame-src AND
   * connect-src (test_csp.mjs block 9) — a missing one fails SILENTLY. */
  var API_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

  /* MUST equal the server's `TURNSTILE_ACTIONS` — a drift refuses every visitor with
   * `turnstile_failed`. `sim/test_turnstile.mjs` §10 compares the two sources. */
  var ACTIONS = { chat: "chat", transcribe: "transcribe" };

  /* How long one `execute()` may take. Shorter than the server's own patience (the transport
   * gives /api/chat 25 s) so a visitor who must retry is told quickly. An interactive
   * challenge can take longer: `mint()` spends a late solve via `getResponse()` and never
   * resets a challenge still on screen (`outstanding`). */
  var EXECUTE_TIMEOUT_MS = 8000;

  /** Current mint deadline; only the `__deadlineMs()` TEST HOOK changes it (test_turnstile
   *  §9 pins the shipped 8000 from this source separately). */
  var deadlineMs = EXECUTE_TIMEOUT_MS;

  /* Script requests allowed per page. A failed load (ad-blocker, captive portal, one 5xx,
   * or merely slow) must not disable live turns for the whole session, so failures are
   * never memoised — but bounded, because a permanently blocked host must not get a
   * `<script>` appended per Send. */
  var MAX_SCRIPT_TRIES = 3;

  /** Recorded facts, for the tests. Never a live sample (playbook rule 11). */
  var stats = {
    scriptTries: 0,     // times Cloudflare's api.js was requested (<= MAX_SCRIPT_TRIES)
    scriptLoads: 0,     // ...and times that request succeeded
    scriptErrors: 0,    // ...and times it failed (CSP, offline, blocked, too slow)
    renders: 0,         // turnstile.render() calls — one per ACTION, not per send
    renderErrors: 0,
    mints: 0,           // getToken() calls that asked the widget for a new challenge
    rejoined: 0,        // ...and the ones that WAITED on a challenge already on screen
                        //    instead of resetting it out from under the visitor
    reused: 0,          // ...and the ones answered by a token the widget already held,
                        //    unspent, from a challenge that finished past the deadline
    tokens: 0,          // sends that ended up with a token, by any of those routes
    timeouts: 0,        // ...and the ones the 8 s deadline gave up on
    widgetErrors: 0,    // error-callback fired
    expiries: 0,        // expired-callback fired
    skipped: 0,         // getToken() calls with no sitekey: enforcement is off
    unknownAction: 0,   // getToken() calls for an action this file does not know
  };

  var loading = null;        // a promise for "Cloudflare's script is present", or null
  var holder = null;         // the element the widgets live in

  /** Per-ACTION state. One widget, one in-flight resolver, one serialisation chain and one
   *  single-use record each, because the two routes' tokens are not interchangeable. */
  var SLOTS = {};
  function slot(action) {
    var name = String(action || "");
    if (!Object.prototype.hasOwnProperty.call(ACTIONS, name)) return null;
    if (!SLOTS[name]) {
      SLOTS[name] = {
        action: ACTIONS[name],
        id: null,             // whatever turnstile.render() handed back
        box: null,            // this widget's own child of the holder
        pending: null,        // the resolver of the mint currently in flight
        chain: Promise.resolve(),
        /* An `execute()` is in flight — a challenge may be ON SCREEN. A later mint must wait
         * for it, not `reset()` it away. */
        outstanding: false,
        /* The last token handed out. Tokens are single-use, so `mint()` may trust a held
         * `getResponse()` only when it differs from this one. */
        spent: "",
      };
    }
    return SLOTS[name];
  }

  function sitekey() {
    try {
      var m = window.moxieMode;
      return (m && typeof m.turnstile === "function" && m.turnstile()) || "";
    } catch (e) { return ""; }
  }

  function api() {
    try { return window.turnstile || null; } catch (e) { return null; }
  }

  /* ---- the element the widgets draw into --------------------------------- *
   * A full-viewport, `pointer-events: none` centring layer on `document.body`:
   *   · not inside a panel, whose overflow/collapse could clip a challenge to nothing;
   *   · not anchored to the bottom, where it covered #rail-toggle and the composer;
   *   · the layer takes no pointer events (children do), so an empty holder can never
   *     swallow a tap and elementFromPoint skips it.
   * `place()` keeps it above the bottom controls. Styled by an injected `<style>` so the
   * rule ships with (and cannot drift from) this script; an attribute cannot express
   * `#turnstile-holder > *`. */
  var HOLDER_CSS =
    "#turnstile-holder{position:fixed;inset:0;z-index:210;display:flex;align-items:center;" +
    "justify-content:center;gap:8px;pointer-events:none}" +
    "#turnstile-holder>*{pointer-events:auto}";

  function ensureHolder() {
    if (holder) return holder;
    try {
      if (!document || !document.body) return null;
      holder = document.getElementById("turnstile-holder");
      if (!holder) {
        if (!document.getElementById("turnstile-css")) {
          var st = document.createElement("style");
          st.id = "turnstile-css";
          st.appendChild(document.createTextNode(HOLDER_CSS));
          (document.head || document.documentElement).appendChild(st);
        }
        holder = document.createElement("div");
        holder.id = "turnstile-holder";
        document.body.appendChild(holder);
      }
      watchPlacement();
    } catch (e) { holder = null; }
    return holder;
  }

  /* ---- where "the middle of the viewport" is ----------------------------- *
   * `#chat-dock` grows as the log fills (to #transcript's max-height), carrying
   * #rail-toggle up into a viewport-centred challenge — overlapping for 683 < vh < 909, i.e.
   * ordinary modern phones. Cloudflare's `render()` has no position option; placement is
   * ours. So the layer's `bottom` is set from the measured top of the bottom stack, with the
   * dock's REMAINING growth already reserved (`growth()`), so the answer does not change as
   * she rambles. (Deliberately not env.js's `--eb-lift`, which skips a panel whose bottom is
   * in the upper half — see docs/architecture/backlog/turnstile-layout-collision.md.)
   * The observers are for changes a person makes: rotation, opening the drawer.
   * ------------------------------------------------------------------------ */
  var DRAWER_MQ = "(max-width: 899px)";   // must match the CSS drawer breakpoint
  var PLACE_GAP = 8;                      // clear air between challenge and controls
  /* Below this much room, fall back to the whole viewport: a challenge in the way beats a
   * clipped, unsolvable one (reached only by e.g. a 667x375 landscape phone). */
  var PLACE_MIN = 88;
  /* Below this, centring overflows both ends of the region; `flex-end` pins the bottom edge
   * instead. 300 clears Turnstile's tallest widget (compact, 130x120). */
  var PLACE_CENTRE_MIN = 300;

  /** Height `#transcript` can still gain before it hits its own `max-height` cap. */
  function growth() {
    try {
      var t = document.getElementById("transcript");
      if (!t) return 0;
      var h = t.getBoundingClientRect().height;
      if (!(h > 0)) return 0;
      var max = parseFloat(window.getComputedStyle(t).maxHeight);
      if (!isFinite(max) || max <= 0) return 0;
      return Math.max(0, max - h);
    } catch (e) { return 0; }
  }

  /** The top of the page's bottom stack, with the dock's remaining growth already spent. */
  function controlsTop() {
    var H = window.innerHeight || 0;
    var top = H;
    var drawer = false;
    try { drawer = !!(window.matchMedia && window.matchMedia(DRAWER_MQ).matches); } catch (e) {}
    /* `#panel` counts only in drawer mode (at >=900 px it is a full-height column).
     * `#rail-toggle` is inside `#panel`, so needs no entry of its own. */
    var ids = drawer ? ["panel", "chat-dock"] : ["chat-dock"];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (!el || !el.getBoundingClientRect) continue;
      var r = el.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0)) continue;    // a collapsed rail is not furniture
      if (r.top < top) top = r.top;
    }
    return top - growth();
  }

  function place() {
    if (!holder || !holder.style) return;
    var H = window.innerHeight || 0;
    if (!H) return;
    var room = controlsTop() - PLACE_GAP;
    if (!(room >= PLACE_MIN) || room >= H) {   // no room above the controls, or no controls
      holder.style.bottom = "0px";
      holder.style.alignItems = "center";
      return;
    }
    holder.style.bottom = Math.round(H - room) + "px";
    holder.style.alignItems = room >= PLACE_CENTRE_MIN ? "center" : "flex-end";
  }

  var placing = false;
  function watchPlacement() {
    if (placing) return;
    placing = true;
    place();
    try { window.addEventListener("resize", place, { passive: true }); } catch (e) {}
    try { window.addEventListener("orientationchange", place); } catch (e) {}
    /* These boxes resize without a resize event (drawer, openers). The holder itself is
     * never observed: `place()` writes to it, and that would loop. */
    try {
      if (window.ResizeObserver) {
        var ro = new window.ResizeObserver(place);
        var ids = ["panel", "chat-dock"];
        for (var i = 0; i < ids.length; i++) {
          var el = document.getElementById(ids[i]);
          if (el) ro.observe(el);
        }
      }
    } catch (e) {}
  }

  /** This action's own child of the holder, so two widgets cannot render into one element. */
  function ensureBox(w) {
    if (w.box) return w.box;
    var el = ensureHolder();
    if (!el) return null;
    try {
      w.box = document.createElement("div");
      w.box.className = "cf-turnstile-box";
      w.box.setAttribute("data-action", w.action);
      el.appendChild(w.box);
    } catch (e) { w.box = null; }
    return w.box;
  }

  /* ---- Cloudflare's script ---------------------------------------------- *
   * Never memoises a failure. In order:
   *   · script already here -> `true`, no request (also rescues a load that beat onload);
   *   · a request in flight -> that same promise;
   *   · otherwise -> one new request, memo cleared if it fails. */
  function loadApi() {
    if (api()) return Promise.resolve(true);
    if (loading) return loading;
    if (stats.scriptTries >= MAX_SCRIPT_TRIES) return Promise.resolve(false);
    loading = new Promise(function (resolve) {
      var s;
      try {
        s = document.createElement("script");
      } catch (e) { resolve(false); return; }
      s.src = API_SRC;
      s.async = true;
      s.defer = true;
      /* Resolves a boolean exactly once on every path — a hung promise would hang Send. */
      var settled = false;
      var done = function (v) { if (!settled) { settled = true; resolve(v); } };
      s.onload = function () { stats.scriptLoads++; done(true); };
      s.onerror = function () { stats.scriptErrors++; done(false); };
      setTimeout(function () {
        // `!!api()`, not `false`: a script can load without firing `onload`.
        if (!settled) { if (!api()) stats.scriptErrors++; done(!!api()); }
      }, deadlineMs);
      stats.scriptTries++;
      try {
        (document.head || document.documentElement).appendChild(s);
      } catch (e) { done(false); }
    }).then(function (loaded) {
      // Memo cleared on failure so the next Send may retry (bounded by the tag cap).
      if (!loaded) loading = null;
      return loaded;
    });
    return loading;
  }

  /* ---- the widget for one action, rendered at most once ------------------ */
  function ensureWidget(action) {
    var key = sitekey();
    if (!key) return Promise.resolve(false);
    var w = slot(action);
    if (!w) return Promise.resolve(false);
    if (w.id !== null) return Promise.resolve(true);
    return loadApi().then(function (loaded) {
      /* `loaded` is the one guard. Re-reading `api()` here would be unreachable redundancy
       * that also hid a broken load deadline from mutation row D6i. */
      if (!loaded) return false;
      var t = api();
      if (!t || typeof t.render !== "function") return false;
      var el = ensureBox(w);
      if (!el) return false;
      if (w.id !== null) return true;              // a concurrent call won the race
      try {
        stats.renders++;
        w.id = t.render(el, {
          sitekey: key,
          action: w.action,
          // invisible unless an interaction is needed; challenge runs when we ask
          appearance: "interaction-only",
          execution: "execute",
          size: "flexible",
          theme: "auto",
          /* All three documented callbacks funnel into one `pending` resolver, so a mint
           * settles exactly once. The deadline is `mint()`'s own setTimeout, which also
           * covers Cloudflare's script never loading. */
          callback: function (token) { settle(w, String(token || "") || null); },
          "error-callback": function () { stats.widgetErrors++; settle(w, null); return true; },
          "expired-callback": function () { stats.expiries++; settle(w, null); },
        });
      } catch (e) {
        stats.renderErrors++;
        w.id = null;
        return false;
      }
      return w.id !== null && w.id !== undefined;
    });
  }

  /** A widget callback fired: the challenge has CONCLUDED, so nothing is on screen any
   *  more and a later mint is free to ask for a new one. */
  function settle(w, v) {
    w.outstanding = false;
    var p = w.pending;
    w.pending = null;
    if (p) p(v);
  }

  /* ---- one fresh token -------------------------------------------------- */
  /**
   * Ask one action's widget for a token. Serialised per action through `w.chain`, because
   * `w.pending` is a single resolver and overlapping mints would hang.
   */
  function mint(w) {
    var run = function () {
      return new Promise(function (resolve) {
        var settled = false;
        var done = function (v) {
          if (settled) return;
          settled = true;
          if (w.pending === done) w.pending = null;
          if (v) { w.spent = v; stats.tokens++; }
          resolve(v);
        };
        w.pending = done;
        var t = api();
        if (!t || typeof t.execute !== "function") { done(null); return; }
        /* An unspent token the widget already holds (an interactive solve that landed past
         * the deadline) is the one to use — resetting would discard it. `held !== w.spent`
         * keeps this from replaying the last turn's token. */
        var held = "";
        try {
          held = typeof t.getResponse === "function" ? String(t.getResponse(w.id) || "") : "";
        } catch (e) { held = ""; }
        if (held && held !== w.spent) { stats.reused++; done(held); return; }
        try {
          if (w.outstanding) {
            /* A challenge is still on the visitor's screen: do NOT reset it (the page's
             * "try me once more" would otherwise discard their half-solved puzzle every
             * time). Become the waiter for it under a fresh deadline. If they abandon it,
             * Cloudflare's expired/error callback settles and clears `outstanding`. */
            stats.rejoined++;
          } else {
            // Reset first: `execute()` on a widget holding a token can return the SAME
            // single-use token, refused by the server on the second turn.
            if (typeof t.reset === "function") t.reset(w.id);
            stats.mints++;
            w.outstanding = true;
            t.execute(w.id);
          }
        } catch (e) { done(null); return; }
        setTimeout(function () {
          if (!settled) { stats.timeouts++; done(null); }
        }, deadlineMs);
      });
    };
    w.chain = w.chain.then(run, run);
    return w.chain;
  }

  /**
   * A token for ONE send of ONE route.
   *
   * @param {string} action `"chat"` or `"transcribe"`. REQUIRED: an unknown name resolves
   *   `null` rather than defaulting to chat (that default is the cross-route replay check 2
   *   refuses).
   * @returns {Promise<string|null>} never rejects:
   *   · `""`      — enforcement off on this deployment; send as-is.
   *   · `"<tok>"` — a fresh single-use token.
   *   · `null`    — enforcement on and no token; do NOT send, say something human
   *                 (`cloud-transport.js::botUnavailable`).
   */
  function getToken(action) {
    if (!sitekey()) { stats.skipped++; return Promise.resolve(""); }
    var w = slot(action);
    if (!w) { stats.unknownAction++; return Promise.resolve(null); }
    return ensureWidget(action).then(function (ready) {
      return ready ? mint(w) : null;
    }, function () { return null; });
  }

  /* Render the CHAT widget as soon as a sitekey is known, so the challenge is solved before
   * the first Send. `onChange` fires now and on every change (its key includes the sitekey).
   * Without mode.js or a sitekey this does nothing. */
  try {
    if (window.moxieMode && typeof window.moxieMode.onChange === "function") {
      window.moxieMode.onChange(function () { if (sitekey()) ensureWidget("chat"); });
    }
  } catch (e) {}

  window.moxieTurnstile = {
    getToken: getToken,
    /** The sitekey in force, or "" — the same value the module decides everything from. */
    sitekey: sitekey,
    /** Whether the bot control is enforced on this deployment. */
    enforced: function () { return !!sitekey(); },
    /** Route-name -> action table (test_turnstile §10 compares it to the server's). */
    actions: function () { return JSON.parse(JSON.stringify(ACTIONS)); },
    /** What actually happened, for the tests. */
    stats: function () { return JSON.parse(JSON.stringify(stats)); },
    /** TEST ONLY — shorten the mint deadline (see `deadlineMs`). Clamped positive so it
     *  can never switch the deadline off. */
    __deadlineMs: function (ms) {
      var n = Number(ms);
      deadlineMs = isFinite(n) && n > 0 ? n : EXECUTE_TIMEOUT_MS;
      return deadlineMs;
    },
  };
})();
