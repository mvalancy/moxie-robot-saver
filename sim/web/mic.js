/* mic.js — talk to Moxie: browser mic → STT → the chat loop.
 *
 * Captures mic audio (MediaRecorder), POSTs the clip to the local STT service
 * (sim/stt/server.py), and gets back the robot's real DeepgramResponse shape
 * (docs/reverse-engineering/perception-pipeline.md). The transcript is then
 * published to the bus as a child utterance on
 * `/devices/<id>/events/remote-chat`, exactly as a real robot would — so the
 * backend brain answers it and Moxie speaks the reply.
 *
 * Exposes window.moxieMic = { start, stop, toggle, isRecording, setSttBase }.
 */
(function () {
  "use strict";

  var STT_BASE = (localStorage.getItem("moxie.sttBase") ||
                  (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8082"));
  var rec = null, chunks = [], stream = null, recording = false;

  function status(t) {
    var el = document.getElementById("mic-status"); if (el) el.textContent = t;
    var b = document.getElementById("bus-status"); if (b && t) b.textContent = t;
  }

  function publishUtterance(text) {
    // Prefer the bridge's live MQTT client if it exposed one; otherwise just
    // render locally so the loop is still visible without a broker.
    if (window.moxieBridge && window.moxieBridge.sendUserTurn) {
      window.moxieBridge.sendUserTurn(text);
    } else if (window.moxieBridge && window.moxieBridge.route) {
      window.moxieBridge.route("/devices/d_sim/events/remote-chat",
        JSON.stringify({ command: "prompt", speech: text }));
    }
  }

  function transcribe(blob) {
    status("transcribing…");
    return fetch(STT_BASE.replace(/\/$/, "") + "/stt", {
      method: "POST", body: blob,
      headers: { "Content-Type": blob.type || "application/octet-stream" },
    }).then(function (r) {
      if (!r.ok) throw new Error("stt " + r.status);
      return r.json();
    }).then(function (dg) {
      var alt = (((dg.channel || {}).alternatives || [])[0]) || {};
      var text = (alt.transcript || "").trim();
      if (!text) { status("(nothing heard)"); return null; }
      status('heard: "' + text.slice(0, 40) + '"');
      publishUtterance(text);
      return text;
    }).catch(function () {
      // No STT service (static deploy): fall back to a scripted child line so the
      // conversation still runs. Cycles through the lines we have audio for.
      if (window.moxieStub && window.moxieStub.enabled) {
        return window.moxieStub.scriptedLines().then(function (lines) {
          if (!lines.length) { status("stt unavailable — run sim/stt/server.py"); return null; }
          var text = lines[(window.moxieMic._n = (window.moxieMic._n || 0) + 1) % lines.length];
          status('heard (scripted): "' + text.slice(0, 36) + '"');
          publishUtterance(text);
          return text;
        });
      }
      status("stt unavailable — run sim/stt/server.py");
      return null;
    });
  }

  function start() {
    if (recording) return Promise.resolve();
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      status("mic unsupported in this browser"); return Promise.resolve();
    }
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s; chunks = [];
      rec = new MediaRecorder(s);
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        var blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        chunks = [];
        if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
        if (blob.size > 800) transcribe(blob); else status("(too short)");
      };
      rec.start();
      recording = true;
      document.body.setAttribute("data-mic", "on");
      status("● listening…");
      if (window.moxieAudio) window.moxieAudio.sfx("listen");
    }).catch(function () { status("mic permission denied"); });
  }

  function stop() {
    if (!recording) return;
    recording = false;
    document.body.removeAttribute("data-mic");
    try { rec && rec.state !== "inactive" && rec.stop(); } catch (e) {}
  }

  window.moxieMic = {
    start: start, stop: stop,
    toggle: function () { return recording ? stop() : start(); },
    isRecording: function () { return recording; },
    setSttBase: function (u) { STT_BASE = u; try { localStorage.setItem("moxie.sttBase", u); } catch (e) {} },
    getSttBase: function () { return STT_BASE; },
  };

  // wire the HUD button if present
  function wire() {
    var b = document.getElementById("mic-btn");
    if (b) b.addEventListener("click", function () { window.moxieMic.toggle(); });
    var base = document.getElementById("stt-base");
    if (base) {
      base.value = STT_BASE;
      base.addEventListener("change", function () { window.moxieMic.setSttBase(base.value.trim()); });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire, { once: true });
  else wire();
})();
