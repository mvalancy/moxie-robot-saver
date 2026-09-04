/* env.js — environment awareness for the SIL.
 *
 * Tells the user what this deployment can actually DO — and marks the controls that
 * genuinely need a server (live voice, mic/STT, live-robot link) with tooltips, status
 * text, a capacity pill and a one-time banner — so it is obvious why you can't, e.g.,
 * record and talk to Moxie here. Purely presentational.
 *
 * WHAT CHANGED, and why it matters: this file used to decide everything from the
 * HOSTNAME. Any non-local host was assumed backend-less, so the page told every visitor
 * "hosted demo — only pre-scripted lines have audio (no live TTS)" whether that was true
 * or not, and it never re-checked. It now asks `mode.js` (which asks `GET /api/health`,
 * spec docs/architecture/backlog/live-sim-demo.md §3.2/§6.3/§7) and paints the badge, the
 * pill, the `needs-backend` marks and the banner FROM THE ANSWER. The hostname still
 * decides one thing only, and honestly: whether the OPTIONAL LOCAL sidecars (:8081 Piper,
 * :8082 STT) could possibly be reachable, because those are localhost ports.
 *
 * Every mode is honest, including the two where nothing is configured:
 *   offline / boot / not-configured -> byte-identical to the page as it shipped before.
 *   degraded                        -> the same page, plus the reason, on screen.
 *   live                            -> says the live brain is on, and stops claiming the
 *                                      mic needs a local server when it does not.
 * It also renders correctly BEFORE mode.js has answered (and if mode.js is absent
 * entirely), because `boot` is deliberately today's page.
 */
(function () {
  "use strict";
  var host = location.hostname || "";
  var isLocal = host === "" ||
    /^(localhost|127\.|0\.0\.0\.0|::1|\[::1\]|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host) ||
    /\.local$|\.lan$/.test(host);
  var origin = location.protocol + "//" + (host || "127.0.0.1");
  if (document.body) document.body.setAttribute("data-env", isLocal ? "local" : "hosted");
  var $ = function (id) { return document.getElementById(id); };

  /** The deployment's own answer about itself, or null when mode.js is absent. */
  function modeSnap() {
    try {
      return (window.moxieMode && window.moxieMode.snapshot) ? window.moxieMode.snapshot() : null;
    } catch (e) { return null; }
  }

  // ---- environment badge + capacity pill in the topbar ----
  var badgeEl = null, pillEl = null;
  var ls = document.querySelector("#topbar .linkstate");
  if (ls && ls.parentNode) {
    badgeEl = document.createElement("span");
    badgeEl.className = "env-badge " + (isLocal ? "local" : "hosted");
    ls.parentNode.insertBefore(badgeEl, ls);
    // The capacity / degrade pill (§7). aria-live so a screen reader hears the state
    // change, and it sits beside the badge so it can never cover the avatar.
    pillEl = document.createElement("span");
    pillEl.className = "mode-pill";
    pillEl.setAttribute("aria-live", "polite");
    pillEl.setAttribute("role", "status");
    pillEl.hidden = true;
    ls.parentNode.insertBefore(pillEl, ls);
  }

  var LOCAL_TITLE = "Served from localhost — voice, mic and the live-robot link work when their servers are running.";
  var HOSTED_TITLE = "Served as a static site — no backend. Voice, mic and the live-robot link need a locally-run server (see the banner).";
  var LIVE_TITLE = "Served as a static site with a live brain on the same origin. Connecting a REAL robot still needs your own broker.";

  function paintBadge(snap) {
    if (!badgeEl) return;
    if (isLocal) {
      badgeEl.textContent = "LOCAL";
      badgeEl.title = LOCAL_TITLE;
    } else {
      badgeEl.textContent = (snap && snap.badge) || "HOSTED DEMO";
      badgeEl.title = (snap && snap.state === "live" && snap.liveTurns) ? LIVE_TITLE : HOSTED_TITLE;
    }
    if (document.body)
      document.body.setAttribute("data-mode", (snap && snap.state) || "boot");
    if (!pillEl) return;
    // Never a raw status code and never an upstream error string — mode.js only ever
    // hands over one of its own fixed lines (§7).
    var msg = (snap && snap.message) || "";
    pillEl.textContent = msg;
    // Also on the title: the pill's inline wording is dropped at phone widths (style.css),
    // and the badge alone carries the state there.
    pillEl.title = msg;
    pillEl.hidden = !msg;
    pillEl.className = "mode-pill" + (msg ? " on level-" + ((snap && snap.level) || "ok") : "");
  }

  // `on === false` UNMARKS: the mode can change mid-session (a health poll that reports
  // ears turns the mic from unavailable to available), so the mark has to be removable —
  // and its tooltip replaced, or a stale "needs a local server" title would outlive the
  // claim it was making.
  /**
   * @param {Element|null} btn
   * @param {string} tip     what to tell a human, on hover and to a screen reader.
   * @param {boolean} [on]   `false` UNMARKS.
   * @param {boolean} [dead] the control CANNOT work here — disable it, do not merely hint.
   *
   * WHY `dead` EXISTS (measured on the live site, 2026-09-03). A mark was a tooltip and
   * half opacity, and nothing else: `#speech-btn`, `#tts-test` and `#bus-connect` stayed
   * fully clickable on the hosted deploy, and clicking them fired a cross-origin request
   * (:8081 Piper, :9001 MQTT/WS) that this site's own CSP correctly refused — silence for
   * the visitor and a console error for anyone looking. A control that looks live and
   * silently fails is worse than one that is visibly unavailable, so a control whose ONLY
   * job is to reach another origin is now disabled on an origin that may not reach one.
   *
   * `dead` is deliberately NOT implied by the mark. `#mic-btn` is marked on a scripted
   * deploy and must stay clickable — "Listen" really does play a scripted child line
   * there, which is behaviour, not a dead end. The distinction is the whole point.
   */
  function needsBackend(btn, tip, on, dead) {
    if (!btn) return;
    var marked = on !== false;
    btn.classList.toggle("needs-backend", marked);
    btn.setAttribute("title", tip);
    var off = !!(marked && dead);
    try { btn.disabled = off; } catch (e) {}
    if (off) btn.setAttribute("aria-disabled", "true");
    else if (btn.removeAttribute) btn.removeAttribute("aria-disabled");
  }
  function warn(el, html) { if (el) { el.innerHTML = html; el.classList.add("warn"); } }

  // #tts-status is shared with the cloud/server voice indicator in audio.js, and
  // this probe is async: writing the element directly meant a slow probe could
  // land mid-utterance and wipe the live "speaking" line (and be wiped in turn
  // when playback restored the pre-probe text). audio.js owns that element, so
  // hand it a resting hint instead — it paints it only when nothing is speaking.
  function ttsHint(html, isWarn) {
    if (window.moxieAudio && window.moxieAudio.setTtsHint)
      return window.moxieAudio.setTtsHint({ html: html, warn: !!isWarn });
    var el = $("tts-status");                       // audio.js absent: old behaviour
    if (!el) return;
    el.innerHTML = html;
    el.classList.toggle("warn", !!isWarn);
  }

  /* ---- the Voice panel's standing note (#voice-note) ----
   *
   * WHY IT MOVED HERE. That <p> was written when typing into the Speech box could only
   * ever reach a local Piper sidecar or `speechSynthesis`, and it said so: "Free text
   * uses your browser's voice, or a local Piper service if you run one." PR #112 changed
   * what the box does — with no sidecar, `cloud-transport.js::adoptSpeechControl` renames
   * "Say" to "Ask" and routes the line to Moxie — but nothing updated the paragraph, so
   * on the live site (measured 2026-09-04: state=live, liveTurns=true, button reading
   * "Ask") the panel described the opposite of what the button beside it does.
   *
   * It is painted from the SAME two facts `apply()` already uses to decide the button —
   * whether a local Piper answered, and `mode.js`'s snapshot — so the note and the button
   * cannot disagree; there is no third source of truth to drift.
   *
   * It is written directly, unlike `#tts-status`. That element is shared with audio.js's
   * live "speaking" indicator, so env.js hands audio.js a resting hint instead of racing
   * it (see `ttsHint` below). Nothing else writes #voice-note, so there is nothing to
   * hand off to — the ownership is the same, the hand-off is simply unnecessary. */
  var VOICE_NOTE_PIPER =
    "Tap phrases above play shipped audio (no server). Free text uses your browser&#39;s " +
    "voice, or a local Piper service if you run one.";
  var VOICE_NOTE_LIVE =
    "Tap a phrase above to play shipped audio. Type a line and press <b>Ask</b> &mdash; " +
    "Moxie answers here, in her own voice.";
  var VOICE_NOTE_SCRIPTED =
    "Tap a phrase above to play shipped audio. Type a line and press <b>Ask</b> &mdash; " +
    "Moxie answers here with a pre&#8209;scripted line; this deploy has no live brain.";

  /**
   * @param {boolean} piper  a local Piper sidecar answered — the box is still "Say".
   * @param {boolean} asks   the box was adopted as the typed turn — it now says "Ask".
   * @param {object|null} snap
   */
  function paintVoiceNote(piper, asks, snap) {
    var el = $("voice-note");
    if (!el) return;                                   // a fork that removed the note
    var live = !!(snap && snap.state === "live" && snap.liveTurns);
    // Not adopted and no Piper = the button is the old, disabled "Say": the shipped
    // wording is still the honest description of what free text can do (nothing here).
    var want = piper ? VOICE_NOTE_PIPER
             : !asks ? VOICE_NOTE_PIPER
             : live  ? VOICE_NOTE_LIVE
                     : VOICE_NOTE_SCRIPTED;
    if (el.innerHTML !== want) el.innerHTML = want;
  }

  // Is a server voice available? Then "no TTS server" is simply untrue — the
  // Piper sidecar is only one of the two ways this sim gets a voice.
  function hasCloudVoice() {
    try {
      var a = window.moxieAudio;
      if (a && ((a.hasCloudVoice && a.hasCloudVoice()) || (a.isSpeaking && a.isSpeaking()))) return true;
      var b = window.moxieBridge;
      if (b && b.hasCloudVoice && b.hasCloudVoice()) return true;
    } catch (e) {}
    return false;
  }

  // ---- probe the optional local services, then annotate ----
  function probe(url) {
    var opt = ("AbortSignal" in window && AbortSignal.timeout) ? { signal: AbortSignal.timeout(2500) } : {};
    return fetch(url, opt).then(function (r) { return r.ok; }).catch(function () { return false; });
  }
  // Locally, probe for the optional TTS/STT sidecars and annotate accordingly.
  // On the hosted static deploy those ports can't exist, so skip the two
  // guaranteed-to-fail cross-origin requests and go straight to the hosted-demo
  // annotations — same UI, no wasted requests or pending connections.
  var localTts = false, localStt = false;
  /* Has the sidecar question been ANSWERED yet? On a hosted origin it is answered the
   * moment the page loads (those ports cannot be reached from here, so no probe fires).
   * On a local origin it is only answered when the probe settles — and the answer decides
   * whether `#speech-btn` stays the Piper "Say" control or becomes the typed turn, so
   * acting before it lands would flip the button's label and job under a self-hoster who
   * does have Piper running. `ttsProbed` is what makes that impossible. */
  var ttsProbed = !isLocal;
  if (isLocal) {
    Promise.all([probe(origin + ":8081/health"), probe(origin + ":8082/health")])
      .then(function (r) { localTts = r[0]; localStt = r[1]; ttsProbed = true; render(); });
  }

  /** `cloud-transport.js`'s typed-turn seam, or null on a page/fork without it. */
  function typedTurn() {
    try { return window.moxieTypedTurn || null; } catch (e) { return null; }
  }

  function render() {
    var snap = modeSnap();
    paintBadge(snap);
    // A same-origin transcribe route is a real pair of ears, so the mic no longer needs
    // a locally-run server. `ears` is the server's own answer to that question.
    apply(localTts, localStt || !!(snap && snap.ears), snap);
    paintBanner(snap);
  }

  function apply(tts, stt, snap) {
    // Voice / TTS
    if (tts) {
      ttsHint("piper tts &middot; connected", false);
      // A reachable sidecar: everything in this panel is exactly as it has always been.
      // Written explicitly rather than left alone so the branch is SYMMETRIC — no mark, no
      // `disabled` and no tooltip from the other branch can survive into this one.
      needsBackend($("tts-test"), "Speaks a test line through the local Piper TTS server.", false);
      needsBackend($("tts-base"), "The local Piper TTS server this page is using.", false);
      needsBackend($("speech-btn"), "Speaks this line through the local Piper TTS server.", false);
      paintVoiceNote(true, false, snap);
    }
    else {
      // The buttons really do need the local Piper server, cloud voice or not — and they
      // still do in `live`: the hosted voice route only ever speaks text the server
      // itself just wrote (§3.2's ticket, no text field), so "speak arbitrary text" is
      // structurally not something the hosted demo can offer.
      needsBackend($("tts-test"), "Needs the Piper TTS server (python3 sim/tts/server.py). Not available on the hosted demo.", true, !isLocal);
      needsBackend($("tts-base"), "Addresses the local Piper TTS server. A page served from another origin cannot reach it (CSP: connect-src 'self').", true, !isLocal);
      /* `#speech-btn` is the one control here with somewhere better to be. Its Piper job
       * is impossible without a sidecar, but the box beside it is the most obvious "talk
       * to Moxie" affordance on the page — so hand it the typed turn rather than leaving
       * a dead button that silently 'fails' into a CSP error. See
       * `cloud-transport.js::adoptSpeechControl`. WHAT DECIDES: being in this branch at
       * all means the sidecar probe came back with no Piper, and `ttsProbed` means it has
       * actually come back — never a hostname test. Where the turn then GOES is `mode.js`'s
       * answer, inside the transport. If the transport is absent (a fork that removed it),
       * the button genuinely cannot do anything and is disabled instead. */
      var took = ttsProbed && typedTurn() && typedTurn().adopt(true);
      if (took)
        needsBackend($("speech-btn"),
          "Sends your line to Moxie — she answers here. (Speaking arbitrary text needs the local Piper server.)", false);
      else
        needsBackend($("speech-btn"),
          "Speaks arbitrary text via the local Piper TTS server. On the hosted demo only pre-rendered demo lines play.",
          true, !isLocal && ttsProbed);
      // `took` is what the button itself was decided from one line up, so the note can
      // never claim a typed line reaches Moxie on a page where it does not.
      paintVoiceNote(false, !!took, snap);
      // ...but only say the sim has no voice when it really has neither.
      if (!hasCloudVoice())
        ttsHint(isLocal
          ? "no TTS server &mdash; run <code>python3 sim/tts/server.py</code>"
          : (snap && snap.voice)
            ? "hosted demo &mdash; Moxie&#39;s own voice is live on this page"
            : "hosted demo &mdash; only pre&#8209;scripted lines have audio (no live TTS)", !(snap && snap.voice));
    }
    // Mic / STT
    var micSt = $("mic-status");
    if (stt) {
      if (micSt) { micSt.textContent = "click to start / stop recording"; micSt.classList.remove("warn"); }
      needsBackend($("mic-btn"), isLocal
        ? "Records and transcribes through the local STT server."
        : "Records and transcribes on this page — speech-to-text runs on the site's own origin.", false);
    } else {
      warn(micSt, isLocal
        ? "no STT server &mdash; run <code>python3 sim/stt/server.py</code> (Listen falls back to a scripted line)"
        : "hosted demo &mdash; Listen plays a scripted child line (no live speech&#8209;to&#8209;text)");
      // NOT `dead`: "Listen" still publishes a scripted child line here, which is real
      // behaviour and the fallback this page has always had.
      needsBackend($("mic-btn"), "Live speech-to-text needs the STT server (python3 sim/stt/server.py). On the hosted demo, Listen plays a scripted demo line instead.");
    }
    // The STT address field only ever points at the local sidecar, so it is dead for the
    // same reason `#tts-base` is — and arming it on a hosted origin would only produce a
    // refused request.
    needsBackend($("stt-base"),
      stt && isLocal
        ? "The local speech-to-text server this page is using."
        : "Addresses the local STT server (python3 sim/stt/server.py). A page served from another origin cannot reach it (CSP: connect-src 'self').",
      !(stt && isLocal), !isLocal);
    // Live bus / Link (no probe — it's a WebSocket broker connection). This one keeps its
    // mark in EVERY mode, live included: a real robot's MQTT broker genuinely is not
    // available here, and no same-origin route can change that.
    // `dead` off-localhost: `bridge.js` opens `ws://host:9001`, which a secure page may
    // not open at all and which `connect-src 'self'` refuses regardless — so the click
    // could only ever produce a console error. Locally it is a working control.
    needsBackend($("bus-connect"),
      "Links a REAL robot's MQTT broker over WebSocket (:9001). Needs your self-hosted backend — not available on the hosted demo.",
      true, !isLocal);
    needsBackend($("bus-host"),
      "The broker host to link. Needs your self-hosted backend — not available on the hosted demo.",
      true, !isLocal);
    var busSt = $("bus-status");
    if (!isLocal && busSt && /not connected/i.test(busSt.textContent)) {
      busSt.textContent = "not connected · needs a self-hosted broker"; busSt.classList.add("warn");
    }
  }

  /* ---- the banner must never sit ON a control (measured in Chrome, 2026-09-03) ----
   *
   * At phone widths the HUD rail collapses to `#rail-toggle` ("Controls"), anchored to the
   * BOTTOM of the viewport — and `#env-banner` is `position: fixed; bottom: …; z-index: 30`
   * stretched `left:10px; right:10px` under `@media (max-width: 640px)`. It landed exactly
   * on top of the toggle. At 375x667 on the live site:
   *
   *     #rail-toggle           357x48 at y=610, visible, pointer-events:auto
   *     elementFromPoint(centre) -> div#env-banner            (NOT the toggle)
   *     tap()                    -> refused, element obscured
   *     force-click              -> aria-expanded STAYS false
   *
   * So on the demo's most likely device the primary control was dead, with no feedback,
   * until the visitor dismissed a notice that never said it was in the way.
   *
   * THE FIX IS LAYOUT, NOT z-index. Raising the toggle over the banner would only move the
   * collision (the banner's own dismiss X would go under it). The banner is instead LIFTED
   * clear of whatever the bottom-anchored panel currently occupies, measured rather than
   * guessed — the panel is ~48 px collapsed and up to 42 vh open, so no constant is right.
   * At >=900px the rail is a side column, nothing is bottom-anchored, and the lift is 0.
   *
   * `sim/test_mobile_layout.mjs` asserts `document.elementFromPoint()` at the toggle's
   * centre resolves to the toggle at 360/375/414 — the assertion that would have caught
   * this. A visibility check would not have: the toggle was visible the whole time. */
  var DRAWER_MQ = "(max-width: 899px)";       // must match the CSS drawer breakpoint
  function liftBanner() {
    var root = document.documentElement;
    if (!root || !root.style || !root.style.setProperty) return;
    var lift = 0;
    if (bannerEl) {
      var drawer = false;
      try { drawer = !!(window.matchMedia && window.matchMedia(DRAWER_MQ).matches); } catch (e) {}
      var panel = $("panel");
      if (drawer && panel && panel.getBoundingClientRect) {
        var h = panel.getBoundingClientRect().height;
        if (h > 0) lift = Math.ceil(h) + 8;
      }
    }
    root.style.setProperty("--eb-lift", lift + "px");
  }
  function watchLift() {
    liftBanner();
    try { window.addEventListener("resize", liftBanner, { passive: true }); } catch (e) {}
    try { window.addEventListener("orientationchange", liftBanner); } catch (e) {}
    // The panel's height also changes with no resize event at all — opening the drawer,
    // a <details> group toggling — so watch the box itself where the browser can.
    try {
      var panel = $("panel");
      if (panel && window.ResizeObserver) new window.ResizeObserver(liftBanner).observe(panel);
      else if (panel) {
        var t = $("rail-toggle");
        if (t) t.addEventListener("click", function () { setTimeout(liftBanner, 0); });
      }
    } catch (e) {}
  }

  // ---- one-time banner on the hosted demo ----
  var BANNER_SCRIPTED =
    '<b>3D Moxie, gestures, expressions, Play&nbsp;demo and the QR tools work here.</b> ' +
    'Live voice, the mic and connecting a real robot need a locally&#8209;run backend.';
  var BANNER_LIVE =
    '<b>3D Moxie, gestures, expressions, Play&nbsp;demo and the QR tools work here.</b> ' +
    'Moxie&#39;s live brain answers on this page; connecting a real robot still needs a ' +
    'locally&#8209;run backend. She forgets this conversation when you close the tab.';
  var bannerEl = null;

  function paintBanner(snap) {
    if (!bannerEl) return;
    var t = bannerEl.querySelector(".eb-text");
    if (!t) return;
    var live = !!(snap && snap.state === "live" && snap.liveTurns);
    var want = live ? BANNER_LIVE : BANNER_SCRIPTED;
    if (t.innerHTML !== want) t.innerHTML = want;
  }

  // mode.js answers asynchronously, so subscribe BEFORE the first render — and before
  // the dismissed-banner early-out below, or a dismissed banner would also freeze the
  // badge at its boot value.
  if (window.moxieMode && window.moxieMode.onChange) window.moxieMode.onChange(render);
  else render();

  if (!isLocal) {
    try { if (localStorage.getItem("moxie.envBannerDismissed") === "1") return; } catch (e) {}
    var w = document.createElement("div");
    w.id = "env-banner";
    w.innerHTML =
      '<span class="eb-badge">HOSTED&nbsp;DEMO</span>' +
      '<span class="eb-text">' + BANNER_SCRIPTED + '</span>' +
      '<a class="eb-link" href="docs.html#guides/revive-your-moxie.md">Run it locally &rarr;</a>' +
      '<button class="eb-x" aria-label="Dismiss">&#10005;</button>';
    document.body.appendChild(w);
    bannerEl = w;
    paintBanner(modeSnap());
    watchLift();
    w.querySelector(".eb-x").addEventListener("click", function () {
      w.remove(); bannerEl = null; liftBanner();
      try { localStorage.setItem("moxie.envBannerDismissed", "1"); } catch (e) {}
    });
  }
})();
