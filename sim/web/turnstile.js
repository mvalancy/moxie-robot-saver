/* turnstile.js — the browser half of the bot control: one fresh token per send, per route.
 *
 * Server half: `functions/api/_lib/turnstile.js` (the three mandatory checks, the
 * fail-open/fail-closed split, the two refusal reasons), `functions/api/chat.js` step 7 and
 * `functions/api/transcribe.js` step 4d (where in each guard sequence it runs, and why).
 *
 * ============================================================================
 * WHAT THIS FILE IS, IN ONE SENTENCE. It publishes `window.moxieTurnstile.getToken(action)`,
 * which resolves to a FRESH Cloudflare Turnstile token for one route's action, and
 * `cloud-transport.js` and `mic.js` each call it on ONE line of their send path. Nothing
 * else in the page knows this file exists.
 *
 * THE ONE-LINE SEAM IS DELIBERATE. A parallel branch is rewriting the composer, so the
 * whole of the widget lifecycle — loading Cloudflare's script, rendering, resetting,
 * timing out, giving up — lives here, behind a call that returns a promise of a string.
 * A conflict in that rewrite is then a conflict over one line rather than over a widget.
 * ============================================================================
 *
 * ============================================================================
 * ONE WIDGET PER **ACTION**, AND THERE ARE TWO OF THEM.
 *
 * The two routes that spend money do not cost the same: a chat turn is 160 tokens of
 * completion, and a microphone turn is up to 15 seconds of billable speech-to-text. The
 * server therefore requires a DIFFERENT `action` back from each (`TURNSTILE_ACTIONS`), so
 * that a token minted for a typed sentence is refused by the ears exactly as a stranger's
 * would be — that cross-route replay is the entire value of check 2 when one route is
 * cheap and the other is not.
 *
 * An `action` is fixed at `render()` time (Cloudflare's rendering reference lists it as a
 * render parameter, and `execute()` is documented with no parameters at all, so there is
 * no supported way to change it per call). **So there are two widgets**, kept in `SLOTS`,
 * each with its own widget id, its own single-use bookkeeping and its own serialisation
 * chain. `getToken()` takes the action it is for and a name this file does not know
 * resolves `null` — never a token for some other route.
 *
 * THE CHAT WIDGET IS RENDERED EAGERLY AND THE MICROPHONE ONE IS NOT. Typing is the first
 * thing a visitor does and the challenge should already be solved and waiting when they
 * press Send; pressing the microphone is a deliberate act that takes a second of its own,
 * and an iframe nobody may ever need is not worth loading into every page view.
 * ============================================================================
 *
 * ============================================================================
 * NO CHECKBOX. THE OWNER'S STEER WAS "more like a ChatGPT/Claude interface … gamify this
 * for regular people", and a visible "I am not a robot" tick in front of every sentence a
 * child types is the exact opposite of that. So each widget is rendered
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
   * element it finds and hands us `turnstile.render()` instead — which is how a widget
   * gets `execution: "execute"` and an `action`, neither of which we can set from markup
   * we do not write. It is the only off-origin host THIS REPO'S OWN CODE loads — the other
   * one in the policy, the analytics beacon, is injected by the platform into every HTML
   * response and is not a tag we write. `sim/web/_headers` names this host in `script-src`,
   * `frame-src` AND `connect-src`; block 9 of `sim/test_csp.mjs` pins all three, because a
   * CSP that refuses any one of them fails SILENTLY — no widget renders, `getToken()`
   * resolves to `null`, and every send says "try again". */
  var API_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

  /* THE ACTIONS, and this table MUST equal `functions/api/_lib/turnstile.js`'s
   * `TURNSTILE_ACTIONS`. The server refuses a verdict whose `action` is anything else —
   * that check is what stops a token minted for one route being spendable on the other —
   * so these two tables are one contract in two files. `sim/test_turnstile.mjs` §10 reads
   * both out of the sources and requires them equal, because a silent drift here would
   * refuse every visitor with `turnstile_failed` and look like a Cloudflare fault. */
  var ACTIONS = { chat: "chat", transcribe: "transcribe" };

  /* How long one `execute()` may take before we give up on it.
   *
   * Sized against the SERVER's own patience, from the other end: `cloud-transport.js`
   * gives `/api/chat` 25 s and the route itself times out upstream at 20 s. Eight seconds
   * for an invisible challenge is generous — the pass case is milliseconds — and it is
   * deliberately short enough that a visitor who is going to be told "try again" is told
   * quickly rather than left watching a spinner.
   *
   * AN INTERACTIVE CHALLENGE CAN LEGITIMATELY TAKE LONGER THAN THIS, and that case is
   * handled twice over. `mint()` checks `getResponse()` first, so a solve that lands after
   * the deadline is SPENT by the next Send rather than thrown away; and a Send issued
   * while a challenge is still on screen does not reset it (see `outstanding`). An earlier
   * draft of this file called `reset()` unconditionally at the top of `mint()`, which threw
   * away the token an interactive visitor had just produced and started the challenge over
   * — and the page's own copy ("try me once more") is what invited them to do it, so a
   * challenged visitor could never complete a turn at all. */
  var EXECUTE_TIMEOUT_MS = 8000;

  /** How long a mint currently waits. `EXECUTE_TIMEOUT_MS` is the shipped value and the
   *  only thing that ever changes it is `__deadlineMs()` at the bottom of this file —
   *  a TEST HOOK, because the one behaviour that cannot be observed any other way is what
   *  happens to a send issued AFTER a mint has given up but WHILE the challenge is still
   *  on the visitor's screen, and reaching that state honestly costs eight seconds of
   *  wall clock per assertion. `sim/test_turnstile.mjs` §9 pins the shipped 8 000 out of
   *  this source separately, so shortening it in a test cannot hide a change to it. */
  var deadlineMs = EXECUTE_TIMEOUT_MS;

  /* How many times Cloudflare's script may be requested before this page gives up on it.
   *
   * NOT ONE, AND THAT IS THE FIX FOR A MEASURED BUG. The first version memoised the
   * PROMISE, so a single failed load — one `onerror` from an ad-blocker rule, a captive
   * portal, a cell handoff, one edge 5xx — or merely a load that took longer than the 8 s
   * deadline, disabled every live turn for the REST OF THE PAGE SESSION: the cached
   * `false` was handed to every later caller, no second request was ever made, no widget
   * was ever rendered, and the page told the visitor to retry something that could never
   * succeed. `loadApi()` now clears its memo on failure and answers instantly from
   * `window.turnstile` whenever the script is present, however late it arrived.
   *
   * BOUNDED, because the other failure is a host that is blocked permanently (an extension,
   * a DNS filter, a policy) and a page that appends a `<script>` per Send is its own bug.
   * Three tries is enough to ride out a transient failure and few enough to be invisible. */
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
        /* An `execute()` has been asked for and has not produced a token or an error yet —
         * i.e. THERE MAY BE A CHALLENGE ON THE VISITOR'S SCREEN RIGHT NOW. A later mint
         * must then WAIT for it rather than `reset()` it away: the deadline resolving
         * `null` is this page giving up on the wait, not Cloudflare giving up on the
         * visitor, and the two were being confused. */
        outstanding: false,
        /* The last token this slot handed out. A Turnstile token is SINGLE-USE, so the one
         * thing `mint()` must never do is hand the same string over twice — the server
         * would refuse the second turn with `timeout-or-duplicate` and it would look like
         * the brain failing. This is what lets `mint()` trust `getResponse()`: a held token
         * that is not this one has never been spent. */
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
   * A FULL-VIEWPORT, `pointer-events: none`, CENTRING LAYER ON `document.body` — not a box
   * anchored to the bottom of the screen, and not an element inside the Voice panel.
   * Every part of that is a considered choice, and the second one is a fix.
   *
   * NOT INSIDE A PANEL: `interaction-only` means these boxes are EMPTY almost always and
   * then suddenly hold a real interactive challenge the visitor has to be able to see and
   * click. Inside the SIM's rail or a `section.sub` they would be subject to that panel's
   * `overflow`, its scroll position and its collapsed/expanded state — a challenge clipped
   * to nothing is a page that silently cannot be used, which is the worst failure this
   * file can have.
   *
   * AND NOT ANCHORED TO THE BOTTOM, WHICH IS WHERE THE CONTROLS ARE. Measured in Chromium
   * at 393x851 and 375x667 with touch: a 300x65 challenge at `bottom: 16px` sat exactly on
   * top of `#rail-toggle` — the only way to open the drawer that contains the text box on a
   * phone — and `document.elementFromPoint()` at the toggle's centre returned the widget.
   * That is the SAME defect `#env-banner` had, which `style.css`:1093 records and which
   * `env.js`'s measured `--eb-lift` exists to fix; the parallel composer branch puts a
   * `#composer`, a `#mic-btn` and a `#speech-input` in that same strip, and all three were
   * covered too. So the challenge is centred in the VIEWPORT instead: the middle of the
   * screen is Moxie's face on every layout, it holds no controls at any width, it cannot be
   * clipped by a panel, and it is the most visible place on the page for the one thing a
   * visitor MUST be able to complete.
   *
   * THE LAYER ITSELF IS `pointer-events: none` AND ITS CHILDREN ARE NOT. So an empty
   * holder — the overwhelmingly common case — is not merely small but completely
   * untouchable: it cannot swallow a tap even if some future layout puts a control under
   * it, and `document.elementFromPoint()` skips it entirely, which is what keeps
   * `sim/test_mobile_layout.mjs`'s existing hit tests measuring what they say they measure.
   *
   * STYLED FROM A `<style>` ELEMENT THIS SCRIPT INJECTS, not from `sim/web/style.css` and
   * not from a `style=` attribute. `style.css` is not in `_headers`' no-cache list, so a
   * rule added there could be served stale to a visitor running today's script; an
   * attribute cannot express `#turnstile-holder > *`, which is what re-enables pointer
   * events on the challenge without re-enabling them on the layer. A `<style>` block ships
   * inside the script itself and cannot drift from it. (`style-src` carries
   * `'unsafe-inline'`, so it is permitted; `script-src` does not, which is why no behaviour
   * lives in markup here.) */
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
    } catch (e) { holder = null; }
    return holder;
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
   * NEVER MEMOISES A FAILURE. See `MAX_SCRIPT_TRIES` for the bug that rule fixes. Three
   * answers, in this order, and the order is the fix:
   *   · the script is ALREADY here -> `true`, immediately, with no request. This is also
   *     what rescues a load that merely arrived after our deadline.
   *   · a request is in flight -> that same promise, so N sends make one request.
   *   · otherwise -> one new request, and the memo is cleared if it fails. */
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
      /* NEVER REJECTS, and never leaves the promise hanging. A CSP refusal fires `onerror`
       * on the element; a blocked-by-extension load may fire neither, which is what the
       * timeout is for. Every path resolves a boolean exactly once, because a hung promise
       * here would hang the visitor's Send for ever — the failure mode this whole module
       * exists to avoid. */
      var settled = false;
      var done = function (v) { if (!settled) { settled = true; resolve(v); } };
      s.onload = function () { stats.scriptLoads++; done(true); };
      s.onerror = function () { stats.scriptErrors++; done(false); };
      setTimeout(function () {
        // The deadline. `!!api()` rather than `false`, because a script that loaded without
        // firing `onload` (some extensions, some proxies) has still given us the API.
        if (!settled) { if (!api()) stats.scriptErrors++; done(!!api()); }
      }, deadlineMs);
      stats.scriptTries++;
      try {
        (document.head || document.documentElement).appendChild(s);
      } catch (e) { done(false); }
    }).then(function (loaded) {
      // THE MEMO IS CLEARED ON FAILURE so the next Send may try again — with the tag cap
      // above as the bound. On success it is kept, so nothing re-requests a script that
      // is already on the page.
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
      /* ONE GUARD, IN ONE PLACE, AND IT IS `loadApi()`'s.
       *
       * An earlier draft of this pass ALSO re-read `api()` here and ignored `loaded`
       * entirely, as belt to `loadApi()`'s braces. It was removed on purpose: the
       * redundancy is unreachable by construction (`loadApi()` answers from `api()` before
       * it consults its memo or its counter, so a `false` here means the API was genuinely
       * absent), and — worse — it SWALLOWED A MUTATION. With the load deadline broken to
       * answer a flat `false` instead of `!!api()`, the re-read quietly rescued the page
       * and mutation row D6i reported NOT CAUGHT: a guard nothing can disprove is a guard
       * nobody can trust. Redundancy that hides the failure of the thing it duplicates is
       * worse than no redundancy. */
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
   * Ask one action's widget for a token.
   *
   * SERIALISED THROUGH `w.chain`, because `w.pending` is a single resolver: two overlapping
   * mints would have the second one's callback resolve the first one's promise and then
   * hang. A conversation is one turn at a time so this queue is almost always empty — but
   * "almost always" is not a property to leave a dangling promise behind. The chains are
   * PER ACTION, so a microphone turn is never queued behind a typed one.
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
        /* AN UNSPENT TOKEN THE WIDGET IS ALREADY HOLDING IS THE ONE TO USE.
         *
         * This is the interactive case, and it is one of the two paths by which a visitor
         * who had to click something ever gets a turn: their solve landed after our 8 s
         * deadline, so the previous `getToken()` already resolved `null` and the page
         * already told them to try again — and the widget is sitting there holding a
         * perfectly good token. Resetting it here would throw that away and start the
         * challenge over, for ever.
         *
         * `held !== w.spent` is what keeps this safe rather than a replay: the only token
         * this slot has ever handed out is `w.spent`, so anything else the widget holds is
         * unused. Without that comparison this would re-send the last turn's token and the
         * server would refuse it as `timeout-or-duplicate`. */
        var held = "";
        try {
          held = typeof t.getResponse === "function" ? String(t.getResponse(w.id) || "") : "";
        } catch (e) { held = ""; }
        if (held && held !== w.spent) { stats.reused++; done(held); return; }
        try {
          if (w.outstanding) {
            /* A CHALLENGE IS STILL ON THE VISITOR'S SCREEN. Do NOT reset it.
             *
             * This is the other half of the interactive story, and the page's own copy is
             * what makes it matter: the first Send times out, Moxie says "try me once
             * more", the visitor does exactly that WHILE still working through the
             * challenge — and an unconditional `reset()` here discarded the half-finished
             * puzzle and drew a new one, every single time they followed the instruction.
             * So this mint simply becomes the waiter for the challenge already running:
             * `pending` is installed above, the widget's callback will resolve it, and a
             * fresh 8 s deadline is armed below. Nothing is reset and nothing is executed.
             *
             * THE RESIDUAL, stated rather than hidden: if the visitor ABANDONS a challenge
             * they were shown, `outstanding` stays true until Cloudflare's own widget
             * concludes it — which it does, through the `expired-callback` or the
             * `error-callback`, both of which run `settle()` and clear the flag. Until one
             * of them fires, every send resolves `null` after its deadline and the page
             * says its line: slow, honest, never a dead Send. A local "give up after N
             * rejoins and reset anyway" was considered and rejected — it is the
             * unconditional reset in disguise, and it would take the challenge away from
             * exactly the visitor who is still working through it.
             */
            stats.rejoined++;
          } else {
            // SINGLE-USE TOKENS: otherwise reset first. Without it `execute()` on a widget
            // that already holds a token can hand back the SAME one, which the server
            // refuses as `timeout-or-duplicate` — a bug that would only appear on the
            // second turn of a conversation.
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
   * @param {string} action `"chat"` or `"transcribe"` — which route this token is for. It
   *   is REQUIRED: an unknown or missing name resolves `null` rather than defaulting to
   *   `chat`, because a default is how a microphone turn would come to be paid for with a
   *   typed turn's token, which is the exact replay the server's check 2 exists to refuse.
   * @returns {Promise<string|null>} three outcomes, and the caller must tell them apart:
   *   · `""`      — this deployment does not enforce the bot control. Send as-is. Every
   *                 fork, every branch preview and every local page is this case.
   *   · `"<tok>"` — a fresh single-use token. Put it on the request and send.
   *   · `null`    — enforcement IS on and no token could be obtained (Cloudflare's script
   *                 refused to load, the widget errored, the challenge timed out, or the
   *                 action is not one of ours). The caller must NOT send, and must say
   *                 something human — see `cloud-transport.js::botUnavailable`. A silent
   *                 dead Send is the one outcome that is not allowed.
   *
   * It never rejects. Every branch resolves.
   */
  function getToken(action) {
    if (!sitekey()) { stats.skipped++; return Promise.resolve(""); }
    var w = slot(action);
    if (!w) { stats.unknownAction++; return Promise.resolve(null); }
    return ensureWidget(action).then(function (ready) {
      return ready ? mint(w) : null;
    }, function () { return null; });
  }

  /* Render the CHAT widget as soon as the deployment tells us to, so the challenge is
   * solved and waiting BEFORE the first Send rather than during it. `onChange` fires
   * immediately with the current snapshot and again on every change, and `mode.js`'s emit
   * key now includes the sitekey — so a sitekey that arrives with the first `/api/health`
   * reply gets here even though nothing else about the page changed.
   *
   * ONLY THE CHAT ONE: the microphone's widget is built on first use (see the header), so
   * a visitor who never presses it never loads a second iframe.
   *
   * Failures are swallowed: with no `mode.js` (a page that does not load it) or no
   * sitekey, this does nothing at all, which is the inert case the header describes. */
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
    /** The actions this page can mint for, by route name. Read by `sim/test_turnstile.mjs`
     *  §10, which requires it to equal the server's `TURNSTILE_ACTIONS`. */
    actions: function () { return JSON.parse(JSON.stringify(ACTIONS)); },
    /** What actually happened, for the tests. */
    stats: function () { return JSON.parse(JSON.stringify(stats)); },
    /** TEST ONLY — shorten the mint deadline. Nothing in this page calls it; it exists so
     *  `sim/test_turnstile.mjs` can reach the state where a mint has timed out and the
     *  challenge is still on screen without spending eight seconds of wall clock doing
     *  it (see `deadlineMs`). Clamped to something positive so it can never switch the
     *  deadline off, which is the one direction that would hang a Send for ever. */
    __deadlineMs: function (ms) {
      var n = Number(ms);
      deadlineMs = isFinite(n) && n > 0 ? n : EXECUTE_TIMEOUT_MS;
      return deadlineMs;
    },
  };
})();
