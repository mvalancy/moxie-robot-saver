/* turnstile.js — the browser half of the bot control: one fresh token per send.
 *
 * Server half: `functions/api/_lib/turnstile.js` (the three mandatory checks, the
 * fail-open/fail-closed split, the two refusal reasons) and `functions/api/chat.js` step 7
 * (where in the guard sequence it runs, and why there).
 *
 * ============================================================================
 * WHAT THIS FILE IS, IN ONE SENTENCE. It publishes `window.moxieTurnstile.getToken()`,
 * which resolves to a FRESH Cloudflare Turnstile token, and `cloud-transport.js` calls it
 * on ONE line of the send path. Nothing else in the page knows this file exists.
 *
 * THE ONE-LINE SEAM IS DELIBERATE. A parallel branch is rewriting the composer, so the
 * whole of the widget lifecycle — loading Cloudflare's script, rendering, resetting,
 * timing out, giving up — lives here, behind a call that returns a promise of a string.
 * A conflict in that rewrite is then a conflict over one line rather than over a widget.
 * ============================================================================
 *
 * ============================================================================
 * NO CHECKBOX. THE OWNER'S STEER WAS "more like a ChatGPT/Claude interface … gamify this
 * for regular people", and a visible "I am not a robot" tick in front of every sentence a
 * child types is the exact opposite of that. So the widget is rendered
 * `appearance: "interaction-only"` and `execution: "execute"`:
 *
 *   · interaction-only — it draws NOTHING unless Cloudflare decides this visitor needs to
 *     interact. The overwhelmingly common case is an invisible pass and an empty box.
 *   · execute — the challenge runs when we ASK for it, not when the widget renders, which
 *     is what makes "a fresh token per send" possible at all.
 *
 * AND THE TOKEN IS MINTED PER SEND, NOT PER PAGE LOAD. A token expires after 300 seconds
 * and is SINGLE-USE: Cloudflare answers a replay with `timeout-or-duplicate`. One token
 * per page load would therefore work for exactly the first turn of a conversation and
 * then refuse every subsequent one — a demo that breaks after one sentence, in a way that
 * looks like the brain failing. `mint()` below does `reset()` then `execute()`, which is
 * Cloudflare's documented way to obtain a new token from an existing widget.
 * ============================================================================
 *
 * ============================================================================
 * IT IS INERT UNLESS THE DEPLOYMENT SAYS OTHERWISE, AND THAT IS THE WHOLE PREVIEW STORY.
 *
 * The sitekey arrives from the server, on the envelope `mode.js` already polls
 * (`window.moxieMode.turnstile()`). No sitekey — a fork, a `file://` page, a static CDN,
 * or a branch preview whose Turnstile variables are deliberately empty — and this file
 * loads NO third-party script, makes NO request, adds NO element and resolves `getToken()`
 * to `""`, which the transport sends as-is. That matters beyond tidiness: Turnstile
 * authorizes a hostname and all of its subdomains, and the platform-assigned preview
 * hostname is not on this widget's domain list, so a challenge on a preview URL could
 * never pass. A preview MUST be inert, and it is inert by having nothing to render rather
 * than by a hostname check (C3).
 * ============================================================================
 *
 * NO SECRET IS INVOLVED. A sitekey is public by construction — it is in the widget markup
 * of every site that uses one. The secret lives only in the Function's environment and is
 * never sent to a browser (`functions/api/_lib/env.js` keeps it non-enumerable so even
 * `JSON.stringify(cfg)` cannot carry it).
 */
(function () {
  "use strict";

  /* Cloudflare's widget script, in EXPLICIT mode.
   *
   * `?render=explicit` is what stops the script auto-rendering every `.cf-turnstile`
   * element it finds and hands us `turnstile.render()` instead — which is how the widget
   * gets `execution: "execute"` and an `action`, neither of which we can set from markup
   * we do not write. It is the only off-origin host THIS REPO'S OWN CODE loads — the other
   * one in the policy, the analytics beacon, is injected by the platform into every HTML
   * response and is not a tag we write. `sim/web/_headers` names this host in `script-src`,
   * `frame-src` AND `connect-src`; block 9 of `sim/test_csp.mjs` pins all three, because a
   * CSP that refuses any one of them fails SILENTLY — no widget renders, `getToken()`
   * resolves to `null`, and every send says "try again". */
  var API_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

  /* The action, and it MUST equal `functions/api/_lib/turnstile.js::TURNSTILE_ACTION`.
   * The server refuses a verdict whose `action` is anything else — that check is what
   * stops a token minted by some other widget flow on this domain being spendable here —
   * so these two constants are one contract in two files. `sim/test_turnstile.mjs` §10
   * reads both out of the sources and requires them equal, because a silent drift here
   * would refuse every visitor with `turnstile_failed` and look like a Cloudflare fault. */
  var ACTION = "chat";

  /* How long one `execute()` may take before we give up on it.
   *
   * Sized against the SERVER's own patience, from the other end: `cloud-transport.js`
   * gives `/api/chat` 25 s and the route itself times out upstream at 20 s. Eight seconds
   * for an invisible challenge is generous — the pass case is milliseconds — and it is
   * deliberately short enough that a visitor who is going to be told "try again" is told
   * quickly rather than left watching a spinner.
   *
   * AN INTERACTIVE CHALLENGE CAN LEGITIMATELY TAKE LONGER THAN THIS, and that case is
   * handled — but only because `mint()` below checks `getResponse()` first. The timeout
   * resolves `null`, the page says one honest sentence, the visitor finishes clicking, and
   * the widget quietly holds the token it produced; the NEXT Send finds it and spends it.
   * An earlier draft of this file claimed exactly that behaviour while calling `reset()`
   * unconditionally at the top of `mint()`, which would have thrown that token away and
   * started a fresh challenge every time — an interactive visitor could never have got a
   * turn out of the page at all. */
  var EXECUTE_TIMEOUT_MS = 8000;

  /** Recorded facts, for the tests. Never a live sample (playbook rule 11). */
  var stats = {
    scriptLoads: 0,     // times Cloudflare's api.js was requested (must be 0 or 1)
    scriptErrors: 0,    // ...and times that request failed (CSP, offline, blocked)
    renders: 0,         // turnstile.render() calls
    renderErrors: 0,
    mints: 0,           // getToken() calls that actually asked for a token
    reused: 0,          // ...and the ones answered by a token the widget already held,
                        //    unspent, from a challenge that finished past the deadline
    tokens: 0,          // sends that ended up with a token, by either route
    timeouts: 0,        // ...and the ones the 8 s deadline gave up on
    widgetErrors: 0,    // error-callback fired
    expiries: 0,        // expired-callback fired
    skipped: 0,         // getToken() calls with no sitekey: enforcement is off
  };

  var loading = null;        // a promise for "Cloudflare's script is present"
  var widgetId = null;       // whatever turnstile.render() handed back
  var holder = null;         // the element the widget lives in
  var pending = null;        // the resolver of the mint currently in flight
  var chain = Promise.resolve();  // mints are serialised — see mint()
  /* The last token this module handed out. A Turnstile token is SINGLE-USE, so the one
   * thing `mint()` must never do is hand the same string over twice — the server would
   * refuse the second turn with `timeout-or-duplicate` and it would look like the brain
   * failing. This is what lets `mint()` trust `getResponse()`: a held token that is not
   * this one has never been spent. */
  var spent = "";

  function sitekey() {
    try {
      var m = window.moxieMode;
      return (m && typeof m.turnstile === "function" && m.turnstile()) || "";
    } catch (e) { return ""; }
  }

  function api() {
    try { return window.turnstile || null; } catch (e) { return null; }
  }

  /* ---- the element the widget draws into -------------------------------- *
   * FIXED-POSITIONED ON `document.body`, not injected into the Voice panel, and that is a
   * considered choice rather than laziness. `interaction-only` means this box is EMPTY
   * almost always and then suddenly holds a real interactive challenge the visitor has to
   * be able to see and click. Inside the SIM's rail or a `section.sub` it would be subject
   * to that panel's `overflow`, its scroll position and its collapsed/expanded state — a
   * challenge clipped to nothing is a page that silently cannot be used, which is the
   * worst failure this file can have. Fixed to the bottom centre cannot be clipped by any
   * panel, and when there is no challenge it occupies no visual space at all.
   *
   * Styled with a `style=` ATTRIBUTE rather than a class, on purpose: `sim/web/style.css`
   * is not in `_headers`' no-cache list, so a rule added there could be served stale to a
   * visitor running today's script — while an attribute ships inside the script itself and
   * cannot drift from it. (`style-src` carries `'unsafe-inline'`, so the attribute is
   * permitted; `script-src` does not, which is why no behaviour lives in markup here.) */
  function ensureHolder() {
    if (holder) return holder;
    try {
      if (!document || !document.body) return null;
      holder = document.getElementById("turnstile-holder");
      if (!holder) {
        holder = document.createElement("div");
        holder.id = "turnstile-holder";
        holder.setAttribute("style",
          "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;" +
          "display:flex;justify-content:center;pointer-events:auto");
        document.body.appendChild(holder);
      }
    } catch (e) { holder = null; }
    return holder;
  }

  /* ---- Cloudflare's script, loaded at most once ------------------------- */
  function loadApi() {
    if (loading) return loading;
    loading = new Promise(function (resolve) {
      if (api()) { resolve(true); return; }
      var s;
      try {
        s = document.createElement("script");
      } catch (e) { resolve(false); return; }
      s.src = API_SRC;
      s.async = true;
      s.defer = true;
      /* NEVER REJECTS, and never leaves the promise hanging. A CSP refusal fires `onerror`
       * on the element; a blocked-by-extension load may fire neither, which is what the
       * timeout is for. Every path resolves a boolean exactly once, because a hung promise
       * here would hang the visitor's Send for ever — the failure mode this whole module
       * exists to avoid. */
      var settled = false;
      var done = function (v) { if (!settled) { settled = true; resolve(v); } };
      s.onload = function () { stats.scriptLoads++; done(true); };
      s.onerror = function () { stats.scriptErrors++; done(false); };
      setTimeout(function () { if (!settled) { stats.scriptErrors++; done(!!api()); } }, EXECUTE_TIMEOUT_MS);
      try {
        (document.head || document.documentElement).appendChild(s);
      } catch (e) { done(false); }
    });
    return loading;
  }

  /* ---- the widget, rendered at most once -------------------------------- */
  function ensureWidget() {
    var key = sitekey();
    if (!key) return Promise.resolve(false);
    if (widgetId !== null) return Promise.resolve(true);
    return loadApi().then(function (loaded) {
      if (!loaded) return false;
      var t = api();
      var el = ensureHolder();
      if (!t || !el || typeof t.render !== "function") return false;
      if (widgetId !== null) return true;          // a concurrent call won the race
      try {
        stats.renders++;
        widgetId = t.render(el, {
          sitekey: key,
          action: ACTION,
          // Invisible unless Cloudflare wants an interaction, and the challenge runs when
          // we ask — see the header for why both are required for a per-send token.
          appearance: "interaction-only",
          execution: "execute",
          size: "flexible",
          theme: "auto",
          /* ALL THREE CALLBACKS FUNNEL INTO THE SAME `pending` RESOLVER, so a mint can
           * only ever settle once and can never be left waiting on a widget that gave up
           * quietly. That is the difference between "the page says try again" and "the
           * Send button does nothing", and the second is the exact dead-send this
           * codebase's chat contract exists to prevent.
           *
           * Only the three names Cloudflare's rendering reference documents are passed.
           * There is no fourth "the challenge took too long" hook here on purpose: the
           * deadline is `mint()`'s own `setTimeout`, which cannot be missing, cannot
           * depend on a widget option name staying stable, and covers the case a callback
           * would not — Cloudflare's script never loading in the first place. */
          callback: function (token) { settle(String(token || "") || null); },
          "error-callback": function () { stats.widgetErrors++; settle(null); return true; },
          "expired-callback": function () { stats.expiries++; settle(null); },
        });
      } catch (e) {
        stats.renderErrors++;
        widgetId = null;
        return false;
      }
      return widgetId !== null && widgetId !== undefined;
    });
  }

  function settle(v) {
    var p = pending;
    pending = null;
    if (p) p(v);
  }

  /* ---- one fresh token -------------------------------------------------- */
  /**
   * Ask the widget for a token.
   *
   * SERIALISED THROUGH `chain`, because `pending` is a single resolver: two overlapping
   * mints would have the second one's `render` callback resolve the first one's promise
   * and then hang. A conversation is one turn at a time so this queue is almost always
   * empty — but "almost always" is not a property to leave a dangling promise behind.
   */
  function mint() {
    var run = function () {
      return new Promise(function (resolve) {
        var settled = false;
        var done = function (v) {
          if (settled) return;
          settled = true;
          if (pending === done) pending = null;
          if (v) { spent = v; stats.tokens++; }
          resolve(v);
        };
        pending = done;
        var t = api();
        if (!t || typeof t.execute !== "function") { done(null); return; }
        /* AN UNSPENT TOKEN THE WIDGET IS ALREADY HOLDING IS THE ONE TO USE.
         *
         * This is the interactive case, and it is the only path by which a visitor who had
         * to click something ever gets a turn: their solve landed after our 8 s deadline,
         * so the previous `getToken()` already resolved `null` and the page already told
         * them to try again — and the widget is sitting there holding a perfectly good
         * token. Resetting it here would throw that away and start the challenge over,
         * for ever.
         *
         * `held !== spent` is what keeps this safe rather than a replay: the only token
         * this module has ever handed out is `spent`, so anything else the widget holds is
         * unused. Without that comparison this would re-send the last turn's token and the
         * server would refuse it as `timeout-or-duplicate`. */
        var held = "";
        try {
          held = typeof t.getResponse === "function" ? String(t.getResponse(widgetId) || "") : "";
        } catch (e) { held = ""; }
        if (held && held !== spent) { stats.reused++; done(held); return; }
        try {
          // SINGLE-USE TOKENS: otherwise reset first, unconditionally. Without it
          // `execute()` on a widget that already holds a token can hand back the SAME one,
          // which the server refuses as `timeout-or-duplicate` — a bug that would only
          // appear on the second turn of a conversation.
          if (typeof t.reset === "function") t.reset(widgetId);
          stats.mints++;
          t.execute(widgetId);
        } catch (e) { done(null); return; }
        setTimeout(function () {
          if (!settled) { stats.timeouts++; done(null); }
        }, EXECUTE_TIMEOUT_MS);
      });
    };
    chain = chain.then(run, run);
    return chain;
  }

  /**
   * A token for ONE send.
   *
   * @returns {Promise<string|null>} three outcomes, and the caller must tell them apart:
   *   · `""`      — this deployment does not enforce the bot control. Send as-is. Every
   *                 fork, every branch preview and every local page is this case.
   *   · `"<tok>"` — a fresh single-use token. Put it in the body and send.
   *   · `null`    — enforcement IS on and no token could be obtained (Cloudflare's script
   *                 refused to load, the widget errored, the challenge timed out). The
   *                 caller must NOT send, and must say something human — see
   *                 `cloud-transport.js::botUnavailable`. A silent dead Send is the one
   *                 outcome that is not allowed.
   *
   * It never rejects. Every branch resolves.
   */
  function getToken() {
    if (!sitekey()) { stats.skipped++; return Promise.resolve(""); }
    return ensureWidget().then(function (ready) {
      return ready ? mint() : null;
    }, function () { return null; });
  }

  /* Render the widget as soon as the deployment tells us to, so the challenge is solved
   * and waiting BEFORE the first Send rather than during it. `onChange` fires immediately
   * with the current snapshot and again on every change, and `mode.js`'s emit key now
   * includes the sitekey — so a sitekey that arrives with the first `/api/health` reply
   * gets here even though nothing else about the page changed.
   *
   * Failures are swallowed: with no `mode.js` (a page that does not load it) or no
   * sitekey, this does nothing at all, which is the inert case the header describes. */
  try {
    if (window.moxieMode && typeof window.moxieMode.onChange === "function") {
      window.moxieMode.onChange(function () { if (sitekey()) ensureWidget(); });
    }
  } catch (e) {}

  window.moxieTurnstile = {
    getToken: getToken,
    /** The sitekey in force, or "" — the same value the module decides everything from. */
    sitekey: sitekey,
    /** Whether the bot control is enforced on this deployment. */
    enforced: function () { return !!sitekey(); },
    /** The action this page mints tokens for. Read by `sim/test_turnstile.mjs` §7. */
    action: ACTION,
    /** What actually happened, for the tests. */
    stats: function () { return JSON.parse(JSON.stringify(stats)); },
  };
})();
