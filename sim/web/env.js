/* env.js — environment awareness for the SIL.
 *
 * Tells the user whether the sim is running LOCALLY (backend servers reachable)
 * or as a HOSTED static demo (Cloudflare Pages — no backend), and clearly marks
 * the controls that need a server (live voice, mic/STT, live-robot link) with
 * tooltips, status text, and a one-time banner — so it's obvious why you can't,
 * e.g., record and talk to Moxie on the hosted page. Purely presentational.
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

  // ---- environment badge in the topbar ----
  var ls = document.querySelector("#topbar .linkstate");
  if (ls && ls.parentNode) {
    var badge = document.createElement("span");
    badge.className = "env-badge " + (isLocal ? "local" : "hosted");
    badge.textContent = isLocal ? "LOCAL" : "HOSTED DEMO";
    badge.title = isLocal
      ? "Served from localhost — voice, mic and the live-robot link work when their servers are running."
      : "Served as a static site — no backend. Voice, mic and the live-robot link need a locally-run server (see the banner).";
    ls.parentNode.insertBefore(badge, ls);
  }

  function needsBackend(btn, tip) {
    if (!btn) return;
    btn.classList.add("needs-backend");
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
  if (isLocal) {
    Promise.all([probe(origin + ":8081/health"), probe(origin + ":8082/health")])
      .then(function (r) { apply(r[0], r[1]); });
  } else {
    apply(false, false);
  }

  function apply(tts, stt) {
    // Voice / TTS
    if (tts) { ttsHint("piper tts &middot; connected", false); }
    else {
      // The buttons really do need the local Piper server, cloud voice or not.
      needsBackend($("tts-test"), "Needs the Piper TTS server (python3 sim/tts/server.py). Not available on the hosted demo.");
      needsBackend($("speech-btn"), "Speaks arbitrary text via the local Piper TTS server. On the hosted demo only pre-rendered demo lines play.");
      // ...but only say the sim has no voice when it really has neither.
      if (!hasCloudVoice())
        ttsHint(isLocal
          ? "no TTS server &mdash; run <code>python3 sim/tts/server.py</code>"
          : "hosted demo &mdash; only pre&#8209;scripted lines have audio (no live TTS)", true);
    }
    // Mic / STT
    var micSt = $("mic-status");
    if (stt) { if (micSt) { micSt.textContent = "click to start / stop recording"; micSt.classList.remove("warn"); } }
    else {
      warn(micSt, isLocal
        ? "no STT server &mdash; run <code>python3 sim/stt/server.py</code> (Listen falls back to a scripted line)"
        : "hosted demo &mdash; Listen plays a scripted child line (no live speech&#8209;to&#8209;text)");
      needsBackend($("mic-btn"), "Live speech-to-text needs the STT server (python3 sim/stt/server.py). On the hosted demo, Listen plays a scripted demo line instead.");
    }
    // Live bus / Link (no probe — it's a WebSocket broker connection)
    needsBackend($("bus-connect"),
      "Links a REAL robot's MQTT broker over WebSocket (:9001). Needs your self-hosted backend — not available on the hosted demo.");
    var busSt = $("bus-status");
    if (!isLocal && busSt && /not connected/i.test(busSt.textContent)) {
      busSt.textContent = "not connected · needs a self-hosted broker"; busSt.classList.add("warn");
    }
  }

  // ---- one-time banner on the hosted demo ----
  if (!isLocal) {
    try { if (localStorage.getItem("moxie.envBannerDismissed") === "1") return; } catch (e) {}
    var w = document.createElement("div");
    w.id = "env-banner";
    w.innerHTML =
      '<span class="eb-badge">HOSTED&nbsp;DEMO</span>' +
      '<span class="eb-text"><b>3D Moxie, gestures, expressions, Play&nbsp;demo and the QR tools work here.</b> ' +
      'Live voice, the mic and connecting a real robot need a locally&#8209;run backend.</span>' +
      '<a class="eb-link" href="docs.html#guides/revive-your-moxie.md">Run it locally &rarr;</a>' +
      '<button class="eb-x" aria-label="Dismiss">&#10005;</button>';
    document.body.appendChild(w);
    w.querySelector(".eb-x").addEventListener("click", function () {
      w.remove(); try { localStorage.setItem("moxie.envBannerDismissed", "1"); } catch (e) {}
    });
  }
})();
