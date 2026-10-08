/* env.js — environment awareness for the SIM. Purely presentational.
 *
 * Tells the visitor what this deployment can actually DO, and marks the controls that
 * need a server (live voice, mic/STT, live-robot link) with tooltips, status text, a
 * capacity pill and a one-time banner. Everything is painted from `mode.js`'s answer
 * (GET /api/health; live-sim-demo.md §3.2/§6.3/§7). The hostname decides one thing only:
 * whether the OPTIONAL LOCAL sidecars (:8081 Piper, :8082 STT) could be reachable.
 *   offline / boot / not-configured -> the plain page, unchanged.
 *   degraded                        -> the same page, plus the reason.
 *   live                            -> says the live brain is on; the mic no longer
 *                                      claims to need a local server.
 * Renders correctly before mode.js answers (or without it): `boot` is the plain page.
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
    // Capacity/degrade pill (§7): aria-live, beside the badge so it never covers the avatar.
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
    // Online reads mint; every fallback keeps the caution amber (style.css).
    badgeEl.classList.toggle("online", !isLocal && badgeEl.textContent === "MOXIE ONLINE");
    if (document.body)
      document.body.setAttribute("data-mode", (snap && snap.state) || "boot");
    if (!pillEl) return;
    // Only ever one of mode.js's fixed lines — never a status code or upstream error (§7).
    var msg = (snap && snap.message) || "";
    pillEl.textContent = msg;
    // Also on the title: the inline wording is hidden at phone widths (style.css).
    pillEl.title = msg;
    pillEl.hidden = !msg;
    pillEl.className = "mode-pill" + (msg ? " on level-" + ((snap && snap.level) || "ok") : "");
  }

  /* `on === false` UNMARKS (the mode can change mid-session). `dead`: the control cannot
   * work here (its only job is a cross-origin request CSP refuses) — disable it. Not implied
   * by the mark: #mic-btn is marked on a scripted deploy but still plays a scripted line. */
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

  // #tts-status is owned by voice/cloud.js (its live "speaking" line), and this probe is
  // async — so hand it a resting hint rather than writing it directly.
  function ttsHint(html, isWarn) {
    if (window.moxieAudio && window.moxieAudio.setTtsHint)
      return window.moxieAudio.setTtsHint({ html: html, warn: !!isWarn });
    var el = $("tts-status");                       // voice/ absent
    if (!el) return;
    el.innerHTML = html;
    el.classList.toggle("warn", !!isWarn);
  }

  /* #voice-note: painted from the same facts apply() uses for #speech-btn (Piper answered;
   * mode.js's snapshot), so the note and the button cannot disagree. */
  var VOICE_NOTE_PIPER =
    "Tap phrases above play shipped audio (no server). Free text uses your browser&#39;s " +
    "voice, or a local Piper service if you run one.";
  // "at the bottom": the message box is the page composer, not beside this note.
  var VOICE_NOTE_LIVE =
    "Tap a phrase above to play shipped audio. Type in the message box at the bottom and " +
    "press <b>Ask</b> &mdash; Moxie answers there, in her own voice.";
  var VOICE_NOTE_SCRIPTED =
    "Tap a phrase above to play shipped audio. Type in the message box at the bottom and " +
    "press <b>Ask</b> &mdash; Moxie answers there with a pre&#8209;scripted line; this " +
    "deploy has no live brain.";

  // piper: a local sidecar answered (box is "Say"); asks: adopted as the typed turn ("Ask").
  function paintVoiceNote(piper, asks, snap) {
    var el = $("voice-note");
    if (!el) return;                                   // a fork that removed the note
    var live = !!(snap && snap.state === "live" && snap.liveTurns);
    // Not adopted and no Piper = the old, disabled "Say": the Piper wording is still honest.
    var want = piper ? VOICE_NOTE_PIPER
             : !asks ? VOICE_NOTE_PIPER
             : live  ? VOICE_NOTE_LIVE
                     : VOICE_NOTE_SCRIPTED;
    if (el.innerHTML !== want) el.innerHTML = want;
  }

  // A server voice counts as a voice: then "no TTS server" would be untrue.
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
  // Only on a local origin (hosted, those ports cannot exist). `ttsProbed`: nothing may
  // decide #speech-btn ("Say" vs the typed turn) before the probe settles.
  var localTts = false, localStt = false;
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
    // A same-origin transcribe route (`ears`) means the mic needs no local server.
    apply(localTts, localStt || !!(snap && snap.ears), snap);
    paintBanner(snap);
  }

  function apply(tts, stt, snap) {
    // Voice / TTS
    if (tts) {
      ttsHint("piper tts &middot; connected", false);
      // Written explicitly so no mark/disabled/tooltip from the other branch survives.
      needsBackend($("tts-test"), "Speaks a test line through the local Piper TTS server.", false);
      needsBackend($("tts-base"), "The local Piper TTS server this page is using.", false);
      needsBackend($("speech-btn"), "Speaks this line through the local Piper TTS server.", false);
      paintVoiceNote(true, false, snap);
    }
    else {
      // These need a local Piper even in `live`: the hosted voice route only speaks text
      // the server itself just wrote, never arbitrary text.
      needsBackend($("tts-test"), "Needs the Piper TTS server (python3 sim/tts/server.py). Not available on the hosted demo.", true, !isLocal);
      needsBackend($("tts-base"), "Addresses the local Piper TTS server. A page served from another origin cannot reach it (CSP: connect-src 'self').", true, !isLocal);
      /* With no Piper (once the probe settled — never a hostname test) #speech-btn becomes
       * the typed turn (cloud-transport.js::adoptSpeechControl); without the transport it
       * can do nothing and is disabled. */
      var took = ttsProbed && typedTurn() && typedTurn().adopt(true);
      if (took)
        needsBackend($("speech-btn"),
          "Sends your line to Moxie — she answers here. (Speaking arbitrary text needs the local Piper server.)", false);
      else
        needsBackend($("speech-btn"),
          "Speaks arbitrary text via the local Piper TTS server. On the hosted demo only pre-rendered demo lines play.",
          true, !isLocal && ttsProbed);
      // The note follows `took`, the same fact the button was decided from.
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
      /* Name what THIS capture does. The site's own ears (`snap.ears`, mic.js's "cloud"
       * capture) stop by themselves after a breath of silence, so a second tap re-opened the
       * mic and uploaded a second clip; a local sidecar records with MediaRecorder, which has
       * no silence stop, so there the second tap is still how a line is sent. */
      if (micSt) {
        micSt.textContent = (snap && snap.ears)
          ? "Tap Listen and talk — I'll know when you're done."
          : "Tap Listen, say something, then tap it again to send.";
        micSt.classList.remove("warn");
      }
      needsBackend($("mic-btn"), isLocal
        ? "Records and transcribes through the local STT server."
        : "Records and transcribes on this page — speech-to-text runs on the site's own origin.", false);
    } else {
      warn(micSt, isLocal
        ? "no STT server &mdash; run <code>python3 sim/stt/server.py</code> (Listen falls back to a scripted line)"
        : "hosted demo &mdash; Listen plays a scripted child line (no live speech&#8209;to&#8209;text)");
      // NOT `dead`: Listen still publishes a scripted child line here.
      needsBackend($("mic-btn"), "Live speech-to-text needs the STT server (python3 sim/stt/server.py). On the hosted demo, Listen plays a scripted demo line instead.");
    }
    // The STT address only points at the local sidecar: dead off-localhost, like #tts-base.
    needsBackend($("stt-base"),
      stt && isLocal
        ? "The local speech-to-text server this page is using."
        : "Addresses the local STT server (python3 sim/stt/server.py). A page served from another origin cannot reach it (CSP: connect-src 'self').",
      !(stt && isLocal), !isLocal);
    // Live bus: marked in EVERY mode (a real robot's broker is never on this origin), and
    // dead off-localhost — bridge/index.js opens ws://host:9001, which CSP refuses.
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

  /* The banner must never sit ON a control (on phones it swallowed #rail-toggle's taps).
   * LAYOUT, not z-index (that would bury the dismiss X): lift it clear of the MEASURED
   * bottom stack — no constant fits a handle, a 42vh drawer and a growing composer.
   * test_mobile_layout.mjs asserts the hit test. */
  var DRAWER_MQ = "(max-width: 899px)";       // must match the CSS drawer breakpoint

  // The bottom stack is #chat-dock at EVERY width, plus #panel in drawer mode. The lift
  // is from the top of the highest box whose bottom is in the lower half of the viewport,
  // which keeps the desktop side column out of the sum.
  // `--eb-top` is the header's measured bottom (the top of #stage): on a phone the banner
  // hangs there instead (style.css), because lifted above the dock a phone-width card sat
  // on her torso, and climbed higher as the conversation grew the dock.
  function liftBanner() {
    var root = document.documentElement;
    if (!root || !root.style || !root.style.setProperty) return;
    var lift = 0;
    var st = $("stage");
    if (st && st.getBoundingClientRect)
      root.style.setProperty("--eb-top", Math.max(0, Math.round(st.getBoundingClientRect().top)) + "px");
    if (bannerEl) {
      var H = window.innerHeight || 0;
      var top = H;
      var drawer = false;
      try { drawer = !!(window.matchMedia && window.matchMedia(DRAWER_MQ).matches); } catch (e) {}
      var boxes = [$("chat-dock")];
      if (drawer) boxes.push($("panel"));       // stacked above the dock, drawer mode only
      for (var i = 0; i < boxes.length; i++) {
        var el = boxes[i];
        if (!el || !el.getBoundingClientRect) continue;
        var r = el.getBoundingClientRect();
        if (r.height > 0 && r.bottom > H * 0.5 && r.top < top) top = r.top;
      }
      if (H > 0 && top < H) lift = Math.ceil(H - top) + 8;
    }
    root.style.setProperty("--eb-lift", lift + "px");
  }
  function watchLift() {
    liftBanner();
    try { window.addEventListener("resize", liftBanner, { passive: true }); } catch (e) {}
    try { window.addEventListener("orientationchange", liftBanner); } catch (e) {}
    // These boxes also resize without a window resize (drawer, <details>, a new log row):
    // watch them, or fall back to the toggle click.
    try {
      var watched = 0, ids = ["panel", "chat-dock", "topbar"];
      for (var i = 0; i < ids.length; i++) {
        var el = $(ids[i]);
        if (el && window.ResizeObserver) { new window.ResizeObserver(liftBanner).observe(el); watched++; }
      }
      if (!watched) {
        var t = $("rail-toggle");
        if (t) t.addEventListener("click", function () { setTimeout(liftBanner, 0); });
      }
    } catch (e) {}
  }

  // ---- one-time banner on the hosted demo ----
  // SCRIPTED is true of a deployment with no brain at all: no Functions (`offline`), or
  // Functions with no gateway (`gateway_not_configured`, sticky). It was also painted for
  // every OTHER degraded state, where it is false: that deployment HAS a live brain, which
  // is out for a minute (or out of today's budget), and "need a locally-run backend" plus
  // "Run it locally" told a stranger mid-chat that the site cannot talk at all.
  // `eb-more` is the part a phone drops (style.css): there the card hangs in her headroom.
  var BANNER_SCRIPTED =
    '<b class="eb-more">3D Moxie, gestures, expressions, Play&nbsp;demo and the QR tools work here.</b> ' +
    'Live voice, the mic and connecting a real robot need a locally&#8209;run backend.';
  var BANNER_LIVE =
    '<b class="eb-more">3D Moxie, gestures, expressions, Play&nbsp;demo and the QR tools work here.</b> ' +
    'Moxie&#39;s live brain answers on this page; connecting a real robot still needs a ' +
    'locally&#8209;run backend. She forgets this conversation when you close the tab.';
  // A transient outage: mode.js polls again within ~30 s, so "a minute" is the truth.
  var BANNER_NAPPING =
    '<b>Moxie&#39;s brain is napping</b> &mdash; she&#39;s using her recorded lines; ' +
    'try again in a minute.';
  // Out for longer than that: the hour/day budget, or a bot check the owner must fix.
  var BANNER_RESTING =
    '<b>Moxie&#39;s brain is resting</b> &mdash; she&#39;s using her recorded lines for ' +
    'now; try again later.';
  var RESTING = { budget_exhausted: true, turnstile_misconfigured: true };
  var bannerEl = null;

  function paintBanner(snap) {
    if (!bannerEl) return;
    var t = bannerEl.querySelector(".eb-text");
    if (!t) return;
    var live = !!(snap && snap.state === "live" && snap.liveTurns);
    var napping = !live && !!(snap && snap.state === "degraded" &&
                              snap.reason !== "gateway_not_configured");
    var want = live ? BANNER_LIVE
             : !napping ? BANNER_SCRIPTED
             : RESTING[snap.reason] ? BANNER_RESTING : BANNER_NAPPING;
    if (t.innerHTML !== want) t.innerHTML = want;
    // "Run it locally" is advice for a deployment with no brain, never for a napping one.
    var link = bannerEl.querySelector(".eb-link");
    if (link) link.hidden = napping;
  }

  // Subscribe BEFORE the first render and before the dismissed-banner early-out, or a
  // dismissed banner would freeze the badge at its boot value.
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
