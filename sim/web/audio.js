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
 * THE SERVER VOICE (the real thing, not a mirror): `playCloudTTS(payload)` plays an
 * actual `CloudTTSResponse` the supervisor published on `/devices/{id}/commands/tts`
 * — base64 raw 16-bit PCM decoded HERE, in the client, like robot firmware does
 * (no server SDK). bridge.js routes it in. See the section near the bottom.
 *
 * SFX: short synthesized cues (WebAudio oscillators) — no asset files needed.
 * Exposes window.moxieAudio = { speak, sfx, setEnabled, setTtsBase, getClipPhrases,
 *                               playCloudTTS, decodeCloudTTS, isSpeaking }.
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
    stopCloudTTS();          // cancel any queued/playing server audio too
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

  // ------------------------------------------------------------------------
  // CloudTTSResponse playback — the SERVER voice (AI seam ③).
  //
  // The supervisor synthesizes a turn and publishes a CloudTTSResponse on
  // `/devices/{id}/commands/tts`; bridge.js hands it here. The SIM decodes the
  // WIRE ITSELF — exactly like robot firmware, never importing the server SDK
  // (docs/architecture/sim-as-a-client.md). Shape, from the recovered proto
  // (embodied/unity/CloudTTS.proto · docs/architecture/ai-seam.md §3):
  //
  //   AudioBuffer      { bytes buffer; int32 channels; int32 sample_rate }
  //   TTSMark          { uint32 time; uint32 start; uint32 end; string type; string value }
  //   CloudTTSResponse { audio; repeated marks; event_id; chunk_num; ... }
  //
  // `buffer` is base64 of RAW little-endian signed 16-bit PCM — it is NOT a
  // container (no RIFF/OGG header), so `decodeAudioData()` cannot read it; we
  // build the AudioBuffer by hand. Chunked responses (`chunk_num`) for one
  // `event_id` are played in order through a small serial queue.
  // ------------------------------------------------------------------------

  var TTS_DEFAULT_RATE = 24000;      // CloudTTSResponse default when unset
  var TTS_MIN_RATE = 3000, TTS_MAX_RATE = 384000;   // Web Audio createBuffer limits

  // Viseme → how far the mouth opens. The mark `value`s our synthesizers emit
  // follow the common Polly/Piper viseme alphabet; anything unknown gets a
  // mid-open default, so an unfamiliar mark set still animates.
  var VISEME_OPEN = {
    sil: 0.02, p: 0.06, t: 0.20, S: 0.30, T: 0.24, f: 0.16, k: 0.26, i: 0.30,
    r: 0.30, s: 0.20, u: 0.45, "@": 0.50, a: 0.80, e: 0.55, E: 0.62, o: 0.70, O: 0.76,
  };

  function b64ToBytes(b64) {
    if (!b64) return new Uint8Array(0);
    var bin;
    try { bin = atob(String(b64)); } catch (e) { return new Uint8Array(0); }
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) & 0xff;
    return out;
  }

  /* PURE decode: CloudTTSResponse (object or JSON string) → planar Float32 audio
   * + metadata. No AudioContext, no DOM — so `node sim/test_audio.mjs` can test
   * the maths directly. Tolerant of every missing/partial field (a real client
   * never crashes on a short frame). */
  function decodeCloudTTS(resp) {
    if (typeof resp === "string") { try { resp = JSON.parse(resp); } catch (e) { resp = null; } }
    resp = resp || {};
    var a = resp.audio || {};
    var rate = Math.round(+a.sample_rate) || TTS_DEFAULT_RATE;
    rate = Math.min(TTS_MAX_RATE, Math.max(TTS_MIN_RATE, rate));
    var channels = Math.max(1, Math.min(8, Math.round(+a.channels) || 1));
    var bytes = b64ToBytes(a.buffer);
    var samples = bytes.length >> 1;                 // 16-bit → 2 bytes/sample
    var frames = Math.floor(samples / channels);     // odd tail byte/frame ignored
    var view = new DataView(bytes.buffer, bytes.byteOffset, samples * 2);
    var data = [], c;
    for (c = 0; c < channels; c++) data.push(new Float32Array(frames));
    for (var f = 0; f < frames; f++) {
      for (c = 0; c < channels; c++)
        data[c][f] = view.getInt16(((f * channels + c) << 1), true) / 32768;
    }
    return {
      data: data, channels: channels, sampleRate: rate, frames: frames,
      duration: frames / rate, bytes: bytes.length,
      marks: Array.isArray(resp.marks) ? resp.marks : [],
      eventId: resp.event_id || "", chunkNum: Math.round(+resp.chunk_num) || 0,
    };
  }

  /* TTSMark[] → a time-sorted mouth track: {t: ms from utterance start, open}. */
  function markTrack(marks) {
    var out = [];
    for (var i = 0; i < (marks || []).length; i++) {
      var m = marks[i] || {};
      var type = String(m.type || "").toLowerCase();
      var open;
      if (type.indexOf("viseme") !== -1) {
        var v = VISEME_OPEN[m.value];
        open = (v === undefined) ? 0.35 : v;
      } else if (type.indexOf("word") !== -1 || type.indexOf("sentence") !== -1) {
        open = 0.45;                       // no viseme detail → a per-word pulse
      } else continue;                     // ssml/gesture marks don't move the mouth
      out.push({ t: Math.max(0, +m.time || 0), open: open });
    }
    return out.sort(function (x, y) { return x.t - y.t; });
  }

  var ttsQueue = [], ttsPlaying = null, speaking = false, speakingInfo = null;
  var gestureArmed = false, cloudVoice = false;

  /* Loudest mouth-open reached during the CURRENT cloud-TTS utterance, reset when one
   * starts and left standing after it ends (so it can be read afterwards).
   *
   * The mouth is a live ~1 s animation driven by the audio envelope, so *sampling* it
   * is a race an observer loses on a loaded machine: the utterance can begin and end
   * between two polls and the peak is gone with it. Remembering the peak turns "did the
   * face move while it spoke?" from a question about timing into a question about the
   * whole utterance, which is what anyone actually wants to know. Used by
   * sim/tests/test_sil.py's cloud-TTS tests. */
  var mouthPeak = 0;

  function mouth(v) {
    try { if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(v); } catch (e) {}
  }

  // A light, JSON-friendly summary of what is being spoken — deliberately WITHOUT
  // the decoded PCM, so a UI (or a test) can read it without copying megabytes.
  function ttsSummary(d) {
    return { sampleRate: d.sampleRate, channels: d.channels, frames: d.frames,
             duration: d.duration, bytes: d.bytes, marks: d.marks.length,
             eventId: d.eventId, chunkNum: d.chunkNum };
  }

  /* ---- #tts-status: one line, two writers -------------------------------
   * The live "speaking" indicator below and the async service probe in env.js
   * ("no TTS server — run …") both want this element. They used to write it
   * directly, so whichever landed last won: a probe resolving mid-playback
   * wiped the speaking indicator, and the end of playback then restored the
   * pre-probe text and swallowed the probe's result. audio.js now OWNS the
   * element — other code hands it a resting hint via `setTtsHint()`, which is
   * stored and painted only while nothing is speaking.
   */
  var ttsHint = null;        // {html|text, warn} — the resting line (env.js / the Test button)
  var ttsStatusRest = null;  // the line the markup shipped, captured before any override

  function paintTtsStatus() {
    var el = document.getElementById("tts-status");
    if (!el) return;
    if (ttsStatusRest === null) ttsStatusRest = el.textContent;
    if (speaking && speakingInfo) {                       // the override wins, always
      el.textContent = "🔊 speaking — cloud TTS " + speakingInfo.sampleRate + " Hz · " +
                       speakingInfo.duration.toFixed(1) + "s";
      if (el.classList) el.classList.remove("warn");
      return;
    }
    if (ttsHint && ttsHint.html !== undefined && ("innerHTML" in el)) el.innerHTML = ttsHint.html;
    else if (ttsHint) el.textContent = ttsHint.text !== undefined ? ttsHint.text : ttsHint.html;
    else el.textContent = ttsStatusRest;
    if (el.classList) el.classList.toggle("warn", !!(ttsHint && ttsHint.warn));
  }

  /* Set the resting text of #tts-status. `hint` is a plain string, or
   * {text} / {html} plus an optional `warn` flag; null clears it. Safe to call
   * at any time — it never paints over a live speaking indicator. */
  function setTtsHint(hint, warn) {
    if (hint === null || hint === undefined) ttsHint = null;
    else if (typeof hint === "string") ttsHint = { text: hint, warn: !!warn };
    else ttsHint = { html: hint.html, text: hint.text,
                     warn: hint.warn === undefined ? !!warn : !!hint.warn };
    try { paintTtsStatus(); } catch (e) {}
  }

  function setSpeaking(on, info) {
    if (on && !speaking) mouthPeak = 0;    // a NEW utterance; chunks of one accumulate
    speaking = !!on;
    speakingInfo = speaking ? ttsSummary(info) : null;
    try {
      if (document.body && document.body.classList)
        document.body.classList.toggle("tts-speaking", speaking);
      paintTtsStatus();
    } catch (e) {}
    try {
      window.dispatchEvent(new CustomEvent(speaking ? "moxie-tts-start" : "moxie-tts-end",
                                           { detail: speakingInfo }));
    } catch (e) {}
  }

  // Same event_id → play chunk_num in order; different events stay FIFO.
  function ttsEnqueue(item) {
    var i = ttsQueue.length;
    while (i > 0 && ttsQueue[i - 1].dec.eventId === item.dec.eventId &&
           ttsQueue[i - 1].dec.chunkNum > item.dec.chunkNum) i--;
    ttsQueue.splice(i, 0, item);
  }

  // Autoplay policy: a page that never got a gesture has a suspended context.
  // Keep the audio queued and play it on the next real gesture (nothing is lost).
  function armGesture() {
    if (gestureArmed) return;
    gestureArmed = true;
    var go = function () { gestureArmed = false; actx(); ttsPump(); };
    ["pointerdown", "click", "keydown", "touchstart"].forEach(function (ev) {
      window.addEventListener(ev, go, { once: true, passive: true });
    });
  }

  function ttsPump() {
    if (ttsPlaying || !ttsQueue.length) return;
    var a = actx();
    if (!a) {                                   // no Web Audio at all → drain honestly
      while (ttsQueue.length) ttsQueue.shift().resolve({ played: false, reason: "no-audio-context" });
      return;
    }
    if (a.state !== "running") {                // suspended → resume, else wait for a gesture
      armGesture();
      try { var p = a.resume(); if (p && p.then) p.then(ttsPump, function () {}); } catch (e) {}
      return;
    }
    var item = ttsQueue.shift(), d = item.dec;
    var buf, src, analyser;
    try {
      buf = a.createBuffer(d.channels, d.frames, d.sampleRate);
      for (var c = 0; c < d.channels; c++) {
        if (buf.copyToChannel) buf.copyToChannel(d.data[c], c);
        else buf.getChannelData(c).set(d.data[c]);
      }
      src = a.createBufferSource(); src.buffer = buf;
      analyser = a.createAnalyser(); analyser.fftSize = 256;
      src.connect(analyser); analyser.connect(a.destination);
    } catch (e) {
      item.resolve({ played: false, reason: "decode-error: " + (e && e.message), decoded: d });
      return ttsPump();
    }
    ttsPlaying = item; current = src;
    setSpeaking(true, d);

    // Mouth: driven by the audio envelope (the physical truth of the buffer),
    // raised to the viseme/word track from marks[] when the server sent one.
    var track = markTrack(d.marks), mi = 0;
    var probe = new Uint8Array(analyser.frequencyBinCount);
    var t0 = a.currentTime, raf = 0, guard = 0, done = false;
    function frame() {
      analyser.getByteTimeDomainData(probe);
      var peak = 0;
      for (var i = 0; i < probe.length; i++) peak = Math.max(peak, Math.abs(probe[i] - 128));
      var open = Math.min(1, peak / 40);
      if (track.length) {
        var ms = (a.currentTime - t0) * 1000;
        while (mi + 1 < track.length && track[mi + 1].t <= ms) mi++;
        if (track[mi] && track[mi].t <= ms) open = Math.max(open, track[mi].open);
      }
      mouth(open);
      // Read the value back off the avatar, not the one we computed: the peak then
      // witnesses that the face really was driven, which is what the test asserts.
      var shown = open;
      try {
        if (window.moxie && window.moxie.getMouthOpen) shown = window.moxie.getMouthOpen();
      } catch (e) {}
      if (shown > mouthPeak) mouthPeak = shown;
      raf = requestAnimationFrame(frame);
    }
    function finish() {
      if (done) return;
      done = true;
      clearTimeout(guard);
      cancelAnimationFrame(raf);
      if (current === src) current = null;
      ttsPlaying = null;
      mouth(0);
      item.resolve({ played: true, decoded: d });
      ttsPump();                                  // next chunk (keeps `speaking` true)
      if (!ttsPlaying) setSpeaking(false);
    }
    src.onended = finish;
    // Belt-and-braces: if `onended` never fires (a sink that doesn't advance),
    // never strand the SIM in the speaking state.
    guard = setTimeout(finish, d.duration * 1000 + 1500);
    try { src.start(0); } catch (e) { return finish(); }
    frame();
  }

  /* Play one CloudTTSResponse. Resolves when THIS payload finished playing:
   * {played, decoded, reason?}. Never rejects — a client that throws on bad
   * audio is a client that goes mute. */
  function playCloudTTS(payload) {
    var dec = decodeCloudTTS(payload);
    // A decodable payload proves a server voice exists, whatever happens to it
    // next (muted, queued, no audio context) — env.js reads this so it stops
    // claiming "no TTS server" while the cloud voice is doing the talking.
    if (dec.frames) cloudVoice = true;
    if (!enabled) return Promise.resolve({ played: false, reason: "muted", decoded: dec });
    if (!dec.frames) return Promise.resolve({ played: false, reason: "empty", decoded: dec });
    var item = { dec: dec, resolve: null };
    var p = new Promise(function (res) { item.resolve = res; });
    ttsEnqueue(item);
    ttsPump();
    return p;
  }

  function stopCloudTTS() {
    while (ttsQueue.length) ttsQueue.shift().resolve({ played: false, reason: "stopped" });
    if (ttsPlaying) { try { current && current.stop && current.stop(); } catch (e) {} }
  }

  window.moxieAudio = {
    speak: speak, sfx: sfx, stop: stop,
    // --- server voice (CloudTTSResponse on /devices/{id}/commands/tts) ---
    playCloudTTS: playCloudTTS,       // decode + play; resolves when it finished
    decodeCloudTTS: decodeCloudTTS,   // pure wire decode (unit-tested in node)
    isSpeaking: function () { return speaking; },
    speakingInfo: function () { return speakingInfo; },   // summary, no PCM
    hasCloudVoice: function () { return cloudVoice; },    // a CloudTTSResponse has arrived
    setTtsHint: setTtsHint,           // resting text of #tts-status (never clobbers speaking)
    ttsPending: function () { return ttsQueue.length; },
    // Peak mouth-open of the current/most recent cloud-TTS utterance (0..1). Survives
    // the end of playback, so "the mouth moved" is assertable without racing it.
    lastMouthPeak: function () { return mouthPeak; },
    setEnabled: function (v) { enabled = !!v; if (!enabled) stop(); },
    isEnabled: function () { return enabled; },
    setTtsBase: function (u) { TTS_BASE = u; try { localStorage.setItem("moxie.ttsBase", u); } catch (e) {} },
    getTtsBase: function () { return TTS_BASE; },
    getClipPhrases: function () {   // pre-cached Moxie lines guaranteed to make sound
      return loadClips().then(function (j) { return j && j.moxie ? Object.keys(j.moxie) : []; });
    },
    isUnlocked: function () { return !!(ctx && ctx.state === "running"); },
  };

  // Unlock audio on the first user gesture (browser autoplay policy) and announce
  // it, so ambient self-talk waits for real audio instead of miming silently.
  var unlocked = false;
  function unlock() {
    if (unlocked) return;
    actx();
    unlocked = true;
    window.dispatchEvent(new CustomEvent("moxie-audio-unlocked"));
  }
  ["pointerdown", "click", "keydown", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, unlock, { once: true, passive: true });
  });
})();
