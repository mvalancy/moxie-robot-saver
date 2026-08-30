/* audio.js — Moxie's voice + UI sound effects.
 *
 * Voice, in priority order (so sound ALWAYS works, incl. a fully static deploy):
 *   1) a PRE-CACHED clip shipped with the site (audio/index.json, rendered by
 *      sim/tools/prerender_audio.py with Piper) — the fixed UI phrases + scenario
 *      lines play as real recorded speech with envelope-driven mouth-sync.
 *   2) a live local Piper service (sim/tts/server.py) IF one is actually reachable
 *      — lets arbitrary typed text be synthesized when self-hosting.
 *   3) the browser's own speechSynthesis (Web Speech API) — an honest fallback so
 *      free text still makes sound on the static site instead of silently failing.
 * This mirrors the real robot's CloudTTSResponse -> PCM path
 * (docs/reverse-engineering/perception-pipeline.md) as closely as a web page can.
 *
 * SFX: short synthesized cues (WebAudio oscillators) — no asset files needed.
 * Exposes window.moxieAudio = { speak, sfx, setEnabled, setTtsBase, getClipPhrases }.
 */
(function () {
  "use strict";

  var TTS_BASE = (localStorage.getItem("moxie.ttsBase") ||
                  (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8081"));
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

  // Fetch an audio URL, decode it, play it, and drive the mouth from its envelope.
  function playUrl(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("audio " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var a = actx(); if (!a) return false;
      return a.decodeAudioData(buf.slice(0)).then(function (audio) {
        var src = a.createBufferSource(); src.buffer = audio;
        var analyser = a.createAnalyser(); analyser.fftSize = 256;
        src.connect(analyser); analyser.connect(a.destination);
        current = src;
        var data = new Uint8Array(analyser.frequencyBinCount), raf = 0;
        function pump() {
          analyser.getByteTimeDomainData(data);
          var peak = 0;
          for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
          if (window.moxie && window.moxie.setMouthOpen)
            window.moxie.setMouthOpen(Math.min(1, peak / 40));
          raf = requestAnimationFrame(pump);
        }
        src.onended = function () {
          cancelAnimationFrame(raf); current = null;
          if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(0);
        };
        src.start(0); pump();
        return true;
      });
    }).catch(function () { return false; });
  }

  // Pre-rendered clip manifest (static deploys): { moxie: {text:file}, child:{...} }
  var clips = null, clipsTried = false;
  function loadClips() {
    if (clipsTried) return Promise.resolve(clips);
    clipsTried = true;
    return fetch("audio/index.json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { clips = j; return j; }).catch(function () { return null; });
  }

  // Play a pre-rendered clip for `text` if one exists (either speaker).
  function playClip(text, who) {
    return loadClips().then(function (j) {
      if (!j) return false;
      var rel = (j[who || "moxie"] || {})[text] || (j.moxie || {})[text] || (j.child || {})[text];
      if (!rel) return false;
      return playUrl("audio/" + rel);
    });
  }

  function setVoiceStatus(mode) {
    var el = document.getElementById("bus-status");
    if (!el) return;
    var msg = { clip: "🔊 playing (pre-cached voice)", piper: "🔊 playing (Piper)",
                browser: "🔊 playing (browser voice)", none: "muted / no audio available" }[mode];
    if (msg) el.textContent = msg;
  }

  function speak(text, who) {
    if (!enabled || !text) return Promise.resolve(false);
    stop();
    // 1) pre-cached clip (real recorded speech — works on a fully static deploy)
    return playClip(text, who).then(function (done) {
      if (done) { setVoiceStatus("clip"); return true; }
      // 2) live Piper service, ONLY if one is actually reachable
      return speakLive(text).then(function (ok) {
        if (ok) { setVoiceStatus("piper"); return true; }
        // 3) honest fallback: the browser's own voice, so sound really plays
        var spoke = speakBrowser(text);
        setVoiceStatus(spoke ? "browser" : "none");
        return spoke;
      });
    });
  }

  // Browser Web Speech API fallback. No audio stream to analyse, so drive a gentle
  // mouth oscillation for the utterance's duration instead of the envelope.
  function speakBrowser(text) {
    if (!("speechSynthesis" in window)) return false;
    try {
      window.speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance(text);
      u.rate = 1.0; u.pitch = 1.25; u.volume = 1.0;   // slightly higher = warmer/companion
      var vs = window.speechSynthesis.getVoices() || [];
      var pick = vs.filter(function (v) { return /^en/i.test(v.lang); })
        .sort(function (a, b) {
          var pref = /female|samantha|zira|karen|moira|tessa|aria|jenny|google us/i;
          return (pref.test(b.name) ? 1 : 0) - (pref.test(a.name) ? 1 : 0);
        })[0];
      if (pick) u.voice = pick;
      var mo = 0;
      u.onstart = function () {
        var t0 = Date.now();
        mo = setInterval(function () {
          if (window.moxie && window.moxie.setMouthOpen)
            window.moxie.setMouthOpen(0.25 + 0.35 * Math.abs(Math.sin((Date.now() - t0) / 90)));
        }, 55);
      };
      u.onend = u.onerror = function () {
        clearInterval(mo);
        if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(0);
        current = null;
      };
      current = { stop: function () { clearInterval(mo); window.speechSynthesis.cancel(); } };
      window.speechSynthesis.speak(u);
      return true;
    } catch (e) { return false; }
  }

  function speakLive(text) {
    var url = TTS_BASE.replace(/\/$/, "") + "/tts?text=" + encodeURIComponent(text.slice(0, 1000));
    // Fast timeout so an unreachable service falls back to the browser voice
    // quickly instead of hanging (important on the static deploy).
    var ctl = ("AbortController" in window) ? new AbortController() : null;
    var to = ctl ? setTimeout(function () { ctl.abort(); }, 1400) : 0;
    return fetch(url, ctl ? { signal: ctl.signal } : undefined).then(function (r) {
      clearTimeout(to);
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
    }).catch(function () {
      clearTimeout(to);
      return false;   // caller falls back to the browser voice; no scary message
    });
  }

  window.moxieAudio = {
    speak: speak, sfx: sfx, stop: stop,
    setEnabled: function (v) { enabled = !!v; if (!enabled) stop(); },
    isEnabled: function () { return enabled; },
    setTtsBase: function (u) { TTS_BASE = u; try { localStorage.setItem("moxie.ttsBase", u); } catch (e) {} },
    getTtsBase: function () { return TTS_BASE; },
    getClipPhrases: function () {   // pre-cached Moxie lines guaranteed to make sound
      return loadClips().then(function (j) { return j && j.moxie ? Object.keys(j.moxie) : []; });
    },
  };

  // Unlock audio on the first user gesture (browser autoplay policy).
  ["click", "keydown", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, function once() { actx(); }, { once: true, passive: true });
  });
})();
