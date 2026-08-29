/* audio.js — Moxie's voice (Piper TTS) + UI sound effects.
 *
 * TTS: the SERVER renders speech (mirroring the real robot's CloudTTSResponse ->
 * PCM path, docs/reverse-engineering/perception-pipeline.md). We fetch WAV from
 * the local Piper service (sim/tts/server.py, default :8081) and play it, driving
 * a simple mouth/viseme animation from the audio envelope while it speaks.
 *
 * SFX: short synthesized cues (WebAudio oscillators) — no asset files needed.
 * Exposes window.moxieAudio = { speak, sfx, setEnabled, setTtsBase }.
 */
(function () {
  "use strict";

  var TTS_BASE = (localStorage.getItem("moxie.ttsBase") || "http://127.0.0.1:8081");
  var enabled = true;
  var ctx = null;            // created on first user gesture (autoplay policy)
  var current = null;        // current HTMLAudio/AudioBufferSourceNode

  function actx() {
    if (!ctx) { var C = window.AudioContext || window.webkitAudioContext; ctx = C ? new C() : null; }
    if (ctx && ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  // ---- sound effects (synthesized; no files) ----
  var SFX = {
    connect:  [[660, 0.06], [990, 0.10]],
    disconnect: [[440, 0.08], [300, 0.12]],
    listen:   [[880, 0.05]],
    icon:     [[1320, 0.05], [1760, 0.06]],
    click:    [[520, 0.03]],
    error:    [[220, 0.14]],
  };
  function sfx(name, vol) {
    if (!enabled) return;
    var a = actx(); if (!a) return;
    var seq = SFX[name] || SFX.click, t = a.currentTime;
    seq.forEach(function (step) {
      var o = a.createOscillator(), g = a.createGain();
      o.type = "sine"; o.frequency.setValueAtTime(step[0], t);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime((vol || 0.12), t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + step[1]);
      o.connect(g); g.connect(a.destination); o.start(t); o.stop(t + step[1] + 0.02);
      t += step[1] * 0.8;
    });
  }

  // ---- speech (Piper) with mouth sync ----
  function stop() {
    if (current) { try { current.pause ? current.pause() : current.stop(); } catch (e) {} current = null; }
  }

  function speak(text) {
    if (!enabled || !text) return Promise.resolve(false);
    stop();
    var url = TTS_BASE.replace(/\/$/, "") + "/tts?text=" + encodeURIComponent(text.slice(0, 1000));
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("tts " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var a = actx(); if (!a) return false;
      return a.decodeAudioData(buf.slice(0)).then(function (audio) {
        var src = a.createBufferSource(); src.buffer = audio;
        var analyser = a.createAnalyser(); analyser.fftSize = 256;
        src.connect(analyser); analyser.connect(a.destination);
        current = src;
        // drive the mouth from the audio envelope while speaking
        var data = new Uint8Array(analyser.frequencyBinCount), raf = 0;
        function pump() {
          analyser.getByteTimeDomainData(data);
          var peak = 0;
          for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
          var open = Math.min(1, peak / 40);
          if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(open);
          raf = requestAnimationFrame(pump);
        }
        src.onended = function () {
          cancelAnimationFrame(raf); current = null;
          if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(0);
        };
        src.start(0); pump();
        return true;
      });
    }).catch(function (e) {
      var el = document.getElementById("bus-status");
      if (el) el.textContent = "tts unavailable (start sim/tts/server.py)";
      return false;
    });
  }

  window.moxieAudio = {
    speak: speak, sfx: sfx, stop: stop,
    setEnabled: function (v) { enabled = !!v; if (!enabled) stop(); },
    isEnabled: function () { return enabled; },
    setTtsBase: function (u) { TTS_BASE = u; try { localStorage.setItem("moxie.ttsBase", u); } catch (e) {} },
    getTtsBase: function () { return TTS_BASE; },
  };

  // Unlock audio on the first user gesture (browser autoplay policy).
  ["click", "keydown", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, function once() { actx(); }, { once: true, passive: true });
  });
})();
