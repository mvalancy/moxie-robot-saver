/* hud.js — the SIM page's HUD glue: panel wiring, typed turns, transcript, controls.
 *
 * Lived inline at the bottom of `sim.html` until 2026-09-04 (213 lines). Moved out so
 * `script-src` can drop `'unsafe-inline'`: a same-origin file is covered by `'self'` and,
 * unlike a hash, cannot drift out of sync with the policy and blank the page.
 *
 * Loaded WITHOUT defer/async, in the same document position the inline block held, so its
 * execution order relative to the other classic scripts on the page is unchanged.
 */
/* HUD glue — mirrors the #bus-status text (written by bridge.js) onto
 * body[data-bus] so CSS can color the status line, the topbar link lamp,
 * and the REC button. Pure presentation; touches no simulator state. */
(function () {
  "use strict";
  var el = document.getElementById("bus-status");
  var label = document.getElementById("link-label");
  if (!el) return;
  var LABELS = { live: "LINK LIVE", rec: "RECORDING", wait: "LINK …",
                 down: "LINK DOWN", idle: "LINK IDLE" };
  function classify(t) {
    t = (t || "").toLowerCase();
    if (t.indexOf("live") !== -1) return "live";
    if (t.indexOf("recording") !== -1) return "rec";
    if (t.indexOf("connecting") !== -1 || t.indexOf("reconnect") !== -1 ||
        t.indexOf("replaying") !== -1) return "wait";
    if (t.indexOf("error") !== -1 || t.indexOf("disconnected") !== -1 ||
        t.indexOf("failed") !== -1 || t.indexOf("not loaded") !== -1 ||
        t.indexOf("bad ") !== -1) return "down";
    return "idle";
  }
  function sync() {
    var s = classify(el.textContent);
    document.body.setAttribute("data-bus", s);
    if (label) label.textContent = LABELS[s];
  }
  new MutationObserver(sync).observe(el, { childList: true, characterData: true, subtree: true });
  sync();

  // Scene lighting + liveness controls -> window.moxie (once ready).
  function wireScene() {
    var sl = document.getElementById("scene-light");
    var slv = document.getElementById("scene-light-val");
    var idle = document.getElementById("idle-on");
    if (sl && window.moxie && window.moxie.setSceneLight) {
      sl.addEventListener("input", function () {
        var v = (+sl.value) / 100; if (slv) slv.textContent = v.toFixed(2);
        window.moxie.setSceneLight(v);
      });
    }
    // Unified ALIVE control — the prominent topbar button and the panel "liveness"
    // checkbox both drive the same state: window.moxie.setIdle (additive liveness) +
    // window.moxieLife (the autonomous motor loop). ON by default; OFF = full manual.
    var aliveBtn = document.getElementById("alive-toggle");
    function setAlive(on) {
      on = !!on;
      if (window.moxie && window.moxie.setIdle) window.moxie.setIdle(on);
      if (window.moxieLife) (on ? window.moxieLife.start() : window.moxieLife.stop());
      if (idle) idle.checked = on;
      if (aliveBtn) {
        aliveBtn.classList.toggle("alive-on", on);
        aliveBtn.classList.toggle("alive-off", !on);
        aliveBtn.setAttribute("aria-pressed", String(on));
        aliveBtn.querySelector(".alive-label").textContent = on ? "ALIVE" : "PAUSED";
      }
    }
    if (aliveBtn) aliveBtn.addEventListener("click", function () {
      setAlive(!aliveBtn.classList.contains("alive-on"));
    });
    if (idle) idle.addEventListener("change", function () { setAlive(idle.checked); });
    setAlive(idle ? idle.checked : true);   // initialise from the (checked-by-default) box
    var axes = document.getElementById("axes-on");
    if (axes && window.moxie && window.moxie.setShowAxes) {
      axes.addEventListener("change", function () {
        window.moxie.setShowAxes(axes.checked);
        var lg = document.getElementById("axis-legend");
        if (lg) lg.hidden = !axes.checked;
      });
    }
  }
  if (window.moxie) wireScene();
  else window.addEventListener("moxie-ready", wireScene, { once: true });

  /* Name the motor sliders.
   *
   * `moxie.js::buildPanel` writes each row as
   *     <label><span>4 · Head tilt (nod)</span><span class="val">16384</span></label>
   *     <input type="range" …>
   * — the <label> WRAPS neither the input nor carries a `for`, so it labels nothing, and
   * the seven sliders reach the accessibility tree as `slider ""`. Measured on the live
   * site 2026-09-04: those seven, plus #led-color and #qr-kind, were the only interactive
   * nodes on the page with an empty accessible name. A sighted user reads the text an
   * inch away; a screen-reader user is told "slider, 16384" seven times.
   *
   * Fixed here rather than in moxie.js because this file already owns the HUD glue, and
   * because the fix is one attribute copied from text the panel already renders — there
   * is no second source of truth to drift. It runs from a MutationObserver rather than
   * on `moxie-ready`: that event is dispatched BEFORE buildPanel() appends anything, so a
   * ready-handler would run against an empty #motors. */
  (function labelMotors() {
    var host = document.getElementById("motors");
    if (!host) return;
    function pass() {
      var rows = host.querySelectorAll(".motor");
      for (var i = 0; i < rows.length; i++) {
        var input = rows[i].querySelector('input[type="range"]');
        var name = rows[i].querySelector("label span");
        if (!input || !name || input.getAttribute("aria-label")) continue;
        // "4 · Head tilt (nod)" -> "Head tilt (nod), motor 4" — the joint first, because
        // that is what the control does; the index second, because it is how the docs and
        // the window.moxie API address it.
        var txt = name.textContent.replace(/\s+/g, " ").trim();
        var m = /^(\d+)\s*·\s*(.+)$/.exec(txt);
        input.setAttribute("aria-label", m ? (m[2] + ", motor " + m[1]) : txt);
      }
    }
    pass();                                   // in case the panel is already built
    try { new MutationObserver(pass).observe(host, { childList: true }); } catch (e) {}
  })();

  // Audio: SAY speaks via Piper; mute toggle; TTS endpoint + test.
  (function wireAudio() {
    var say = document.getElementById("speech-btn");
    var inp = document.getElementById("speech-input");
    var on = document.getElementById("audio-on");
    var base = document.getElementById("tts-base");
    var test = document.getElementById("tts-test");
    var st = document.getElementById("tts-status");
    function speak(t) { if (t && window.moxieAudio) window.moxieAudio.speak(t); }
    /* When there is no local Piper sidecar, `cloud-transport.js` takes this box over as
     * the "ask Moxie" control (see its `adoptSpeechControl`) — because "speak arbitrary
     * text" is structurally not something a hosted deploy can offer, and a button that
     * looks live and silently fails is worse than one that is honest. These two listeners
     * are left in place and STAND DOWN instead of being removed: the phrase chips below
     * hold a reference to `inp`, and removing/replacing the nodes would break them. */
    function typedTurnOwnsBox() {
      try { return !!(window.moxieTypedTurn && window.moxieTypedTurn.adopted()); } catch (e) { return false; }
    }
    if (say) say.addEventListener("click", function () { if (typedTurnOwnsBox()) return; speak((inp && inp.value || "").trim()); });
    if (inp) inp.addEventListener("keydown", function (e) { if (e.key === "Enter" && !typedTurnOwnsBox()) speak(inp.value.trim()); });
    // Tap-to-play chips from the pre-cached phrase set — guaranteed to make sound
    // on the static deploy (no TTS server needed).
    var chips = document.getElementById("speech-chips");
    if (chips && window.moxieAudio && window.moxieAudio.getClipPhrases) {
      window.moxieAudio.getClipPhrases().then(function (phrases) {
        (phrases || []).slice(0, 10).forEach(function (p) {
          var b = document.createElement("button");
          b.className = "chip"; b.type = "button";
          b.textContent = p.length > 30 ? p.slice(0, 28) + "…" : p;
          b.title = p;
          // The visible label is truncated to fit the chip, and a truncated label is a
          // truncated accessible name ("Happy birthday! I hope your …"). The name is the
          // whole line; the ellipsis is a layout constraint, not part of the phrase.
          b.setAttribute("aria-label", p);
          // Chips always play their pre-cached clip. They only pre-fill the box when the
          // box is still the TTS control — dropping Moxie's own line into an "ask Moxie"
          // input would read as the visitor's question.
          b.addEventListener("click", function () { if (inp && !typedTurnOwnsBox()) inp.value = p; speak(p); });
          chips.appendChild(b);
        });
      });
    }
    if (on) on.addEventListener("change", function () { window.moxieAudio && window.moxieAudio.setEnabled(on.checked); });
    if (base && window.moxieAudio) base.value = window.moxieAudio.getTtsBase();
    if (base) base.addEventListener("change", function () { window.moxieAudio && window.moxieAudio.setTtsBase(base.value.trim()); });
    // #tts-status is owned by audio.js (the cloud-voice indicator lives there too),
    // so hand it a resting hint rather than writing the element: an async probe
    // result can then never wipe a live "speaking" line, or be wiped by one.
    function ttsHint(t) {
      if (window.moxieAudio && window.moxieAudio.setTtsHint) window.moxieAudio.setTtsHint(t);
      else if (st) st.textContent = t;
    }
    if (test) test.addEventListener("click", function () {
      if (!window.moxieAudio) return;
      // Always plays a real pre-cached clip; separately probe for an optional Piper.
      window.moxieAudio.speak("Hi! I am Moxie. It is nice to meet you.");
      ttsHint("checking for local Piper…");
      fetch(window.moxieAudio.getTtsBase().replace(/\/$/, "") + "/health")
        .then(function (r) { return r.json(); })
        .then(function (j) { ttsHint(j.ok ? ("local Piper: " + j.voice) : "no Piper (using pre-cached + browser voice)"); })
        .catch(function () { ttsHint("no Piper (using pre-cached + browser voice)"); });
    });
  })();

  /* Revive QR — the codes that re-home a real robot are plain JSON, so we can
   * build them client-side: a phone loading the static site can revive a Moxie
   * with nothing installed. Byte-identical to moxie_toolkit's encoders.
   * Grammar: docs/reverse-engineering/qr-commands.md */
  (function () {
    var kind = document.getElementById("qr-kind");
    var btn = document.getElementById("qr-make");
    var cv = document.getElementById("qr-canvas");
    var st = document.getElementById("qr-status");
    var wifi = document.getElementById("qr-wifi");
    if (!btn || !window.moxieQR) return;

    kind.addEventListener("change", function () {
      wifi.style.display = kind.value === "wifi" ? "" : "none";
    });

    btn.addEventListener("click", function () {
      var Q = window.moxieQR, v = kind.value, payload;
      try {
        if (v === "wifi") {
          var ssid = document.getElementById("qr-ssid").value.trim();
          if (!ssid) { st.textContent = "enter an SSID first"; return; }
          payload = Q.encodeWifi(ssid, document.getElementById("qr-pass").value);
        } else if (v in Q.ENDPOINTS) {
          payload = Q.encodeEndpoint(v);
        } else {
          payload = Q.encodeDebug(v);
        }
        Q.render(cv, payload, 4);
        cv.style.display = "block";
        st.textContent = payload;
      } catch (e) {
        cv.style.display = "none";
        st.textContent = "QR error: " + e.message;
      }
    });
  })();
})();
