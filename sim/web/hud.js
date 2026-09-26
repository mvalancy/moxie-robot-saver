/* hud.js — the SIM page's HUD glue: panel wiring, openers, audio + QR controls. Also
 * mirrors #bus-status (bridge/) onto body[data-bus] so CSS can color the status line, link
 * lamp and REC button. */
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
    // ALIVE: the topbar button and the panel checkbox drive one state — moxie.setIdle
    // (additive liveness) + moxieLife (the motor loop). ON by default; OFF = full manual.
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

  /* Name the motor sliders (panel.js's <label> does not wrap the input, so they were
   * `slider ""`), copied from the rendered text; observed because `moxie-ready` fires
   * BEFORE the panel is built. */
  (function labelMotors() {
    var host = document.getElementById("motors");
    if (!host) return;
    function pass() {
      var rows = host.querySelectorAll(".motor");
      for (var i = 0; i < rows.length; i++) {
        var input = rows[i].querySelector('input[type="range"]');
        var name = rows[i].querySelector("label span");
        if (!input || !name || input.getAttribute("aria-label")) continue;
        // "4 · Head tilt (nod)" -> "Head tilt (nod), motor 4": what it does, then its index.
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
    /* With no Piper, cloud-transport.js adopts this box as "ask Moxie"; these listeners
     * STAND DOWN rather than being removed (the phrase chips hold `inp`). */
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
          b.setAttribute("aria-label", p);       // the whole line, not the truncated label
          // Pre-fill only while the box is the TTS control: Moxie's line in "ask Moxie"
          // would read as the visitor's question.
          b.addEventListener("click", function () { if (inp && !typedTurnOwnsBox()) inp.value = p; speak(p); });
          chips.appendChild(b);
        });
      });
    }
    if (on) on.addEventListener("change", function () { window.moxieAudio && window.moxieAudio.setEnabled(on.checked); });
    if (base && window.moxieAudio) base.value = window.moxieAudio.getTtsBase();
    if (base) base.addEventListener("change", function () { window.moxieAudio && window.moxieAudio.setTtsBase(base.value.trim()); });
    // #tts-status is owned by voice/cloud.js: hand it a resting hint, so an async probe
    // can never wipe a live "speaking" line (or be wiped by one).
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

  /* THE OPENERS (#chat-openers) send a first turn through `moxieTypedTurn.send`, the one
   * typed path, so every spend guard applies (a click on #speech-btn would not: with Piper
   * it means "say this aloud"). The visible label IS the message. */
  (function wireOpeners() {
    var box = document.getElementById("chat-openers");
    if (!box) return;
    function send(text) {
      var t = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
      if (!t) return false;
      var typed = window.moxieTypedTurn;
      if (typed && typeof typed.send === "function") return !!typed.send(t);
      // No transport (a fork without cloud-transport.js): the bridge + stub.js still
      // answer, like mic.js's fallback — a dead chip would be worse.
      if (window.moxieBridge && typeof window.moxieBridge.sendUserTurn === "function") {
        window.moxieBridge.sendUserTurn(t);
        return true;
      }
      return false;
    }
    box.addEventListener("click", function (e) {
      var t = e.target;
      var b = t && t.closest ? t.closest("button.opener") : null;
      if (!b || !box.contains(b)) return;
      send(b.textContent);
    });
  })();

  /* Revive QR, built client-side (byte-identical to moxie_toolkit's encoders), so a phone
   * on the static site can re-home a Moxie. Grammar: docs/reverse-engineering/qr-commands.md */
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
