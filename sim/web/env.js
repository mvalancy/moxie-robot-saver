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
  function needsBackend(btn, tip, on) {
    if (!btn) return;
    btn.classList.toggle("needs-backend", on !== false);
    btn.setAttribute("title", tip);
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
  if (isLocal) {
    Promise.all([probe(origin + ":8081/health"), probe(origin + ":8082/health")])
      .then(function (r) { localTts = r[0]; localStt = r[1]; render(); });
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
    if (tts) { ttsHint("piper tts &middot; connected", false); }
    else {
      // The buttons really do need the local Piper server, cloud voice or not — and they
      // still do in `live`: the hosted voice route only ever speaks text the server
      // itself just wrote (§3.2's ticket, no text field), so "speak arbitrary text" is
      // structurally not something the hosted demo can offer.
      needsBackend($("tts-test"), "Needs the Piper TTS server (python3 sim/tts/server.py). Not available on the hosted demo.");
      needsBackend($("speech-btn"), "Speaks arbitrary text via the local Piper TTS server. On the hosted demo only pre-rendered demo lines play.");
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
      needsBackend($("mic-btn"), "Live speech-to-text needs the STT server (python3 sim/stt/server.py). On the hosted demo, Listen plays a scripted demo line instead.");
    }
    // Live bus / Link (no probe — it's a WebSocket broker connection). This one keeps its
    // mark in EVERY mode, live included: a real robot's MQTT broker genuinely is not
    // available here, and no same-origin route can change that.
    needsBackend($("bus-connect"),
      "Links a REAL robot's MQTT broker over WebSocket (:9001). Needs your self-hosted backend — not available on the hosted demo.");
    var busSt = $("bus-status");
    if (!isLocal && busSt && /not connected/i.test(busSt.textContent)) {
      busSt.textContent = "not connected · needs a self-hosted broker"; busSt.classList.add("warn");
    }
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
    w.querySelector(".eb-x").addEventListener("click", function () {
      w.remove(); bannerEl = null; try { localStorage.setItem("moxie.envBannerDismissed", "1"); } catch (e) {}
    });
  }
})();
