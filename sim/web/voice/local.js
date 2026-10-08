/* voice/local.js — the local voices: pre-cached clips, a Piper sidecar, speechSynthesis. */
(function () {
  "use strict";
  var V = window.__moxieVoice;

  V.ttsBase = (localStorage.getItem("moxie.ttsBase") ||
               (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8081"));
  // Did a HUMAN name that address, or is it the localhost default? See skipProbe().
  V.ttsBaseExplicit = false;
  try { V.ttsBaseExplicit = !!localStorage.getItem("moxie.ttsBase"); } catch (e) {}

  /* Decode fetched bytes and play them, driving the mouth from the envelope. `o.who` tags
   * the voice; `o.mouth === false` leaves the face alone (the child's clips play here too,
   * and only Moxie's own voice may move her mouth); `o.since` is the caller's floor
   * snapshot (THE THIRD SEAM, voice/core.js). */
  function playBytes(buf, o) {
    var a = V.actx(); if (!a) return false;
    var driveMouth = o.mouth !== false;
    return a.decodeAudioData(buf.slice(0)).then(function (audio) {
      if (V.heldBy(o.who) && V.floor !== o.since) return false;   // Moxie began answering mid-load
      var src = a.createBufferSource(); src.buffer = audio;
      var analyser = a.createAnalyser(); analyser.fftSize = 256;
      src.connect(analyser); analyser.connect(a.destination);
      V.current = src; V.currentWho = o.who || null;
      var data = new Uint8Array(analyser.frequencyBinCount), raf = 0;
      function pump() {
        analyser.getByteTimeDomainData(data);
        var peak = 0;
        for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
        V.mouth(Math.min(1, peak / 40));
        raf = requestAnimationFrame(pump);
      }
      src.onended = function () {
        if (driveMouth) cancelAnimationFrame(raf);
        if (V.current === src) { V.noteSpoke(); V.current = null; V.currentWho = null; }
        if (driveMouth) V.mouth(0);
      };
      src.start(0);
      if (driveMouth) pump();
      return true;
    });
  }

  function playUrl(url, o) {
    if (o.since == null) o.since = V.floor;
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("audio " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) { return playBytes(buf, o); })
      .catch(function () { return false; });
  }

  // Pre-rendered clip manifest (static deploys): { moxie: {text:file}, child:{...} }
  var clips = null, clipsTried = false;
  V.loadClips = function loadClips() {
    if (clipsTried) return Promise.resolve(clips);
    clipsTried = true;
    return fetch("audio/index.json").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { clips = j; return j; }).catch(function () { return null; });
  };

  // Play a pre-rendered clip for `text` if one exists (either speaker).
  function playClip(text, who, since) {
    return V.loadClips().then(function (j) {
      if (!j) return false;
      var rel = (j[who || "moxie"] || {})[text] || (j.moxie || {})[text] || (j.child || {})[text];
      if (!rel) return false;
      return playUrl("audio/" + rel, { who: who || "moxie", since: since });
    });
  }

  // STRICT lookup in the named group only. playClip's moxie->child fallthrough would
  // answer a child line with Moxie's voice saying the child's words.
  function clipInGroup(manifest, text, who) {
    return (manifest && manifest[who] && manifest[who][text]) || null;
  }

  function setVoiceStatus(mode) {
    var el = document.getElementById("bus-status");
    if (!el) return;
    var msg = { clip: "voice: playing (pre-cached)", piper: "voice: playing (Piper)",
                browser: "voice: playing (browser voice)", none: "muted / no audio available" }[mode];
    if (msg) el.textContent = msg;
  }

  /* Skip step 2 (the 1.4 s probe of a local Piper on :8081)?
   *  · :8081 is a LOCALHOST port. From a public origin the probe is a cross-origin request
   *    our CSP (`connect-src 'self'`) refuses and logs, so only localhost / LAN / *.local
   *    pages ever probe.
   *  · otherwise skip when `mode.js` says `degraded` — /api/health answered, so this is a
   *    hosted deployment with no sidecar. `offline` (a self-hoster on sim/serve.py) and
   *    `live` keep probing.
   *  · an address a human set (setTtsBase / moxie.ttsBase) always wins over the mode. */
  function pageCouldReachSidecar() {
    try {
      var h = (location && location.hostname) || "";
      return h === "" ||
        /^(localhost|127\.|0\.0\.0\.0|::1|\[::1\]|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) ||
        /\.local$|\.lan$/.test(h);
    } catch (e) { return false; }
  }

  function skipProbe() {
    if (!pageCouldReachSidecar()) return true;
    if (V.ttsBaseExplicit) return false;
    try {
      return !!(window.moxieMode && typeof window.moxieMode.state === "function" &&
                window.moxieMode.state() === "degraded");
    } catch (e) { return false; }
  }

  function speak(text, who) {
    if (!V.enabled || !text) return Promise.resolve(false);
    /* An ambient line (a mutter, the thinking filler) never FLUSHES her queued answer: a
     * cloud chunk waiting its turn or an autoplay gesture is invisible to every speaking
     * predicate, so no caller can guard it, and the stop() below would drop it for good. A
     * PLAYING answer is visible to isMoxieBusy(), which every ambient caller asks first
     * (ambient.js, bridge/alive.js); unguarded, a line here still cuts it — the negative
     * control of sim/test_ambient_guard.mjs §2. */
    if (V.heldBy(who) && V.ttsPending && V.ttsPending() > 0) return Promise.resolve(false);
    V.stop();
    if (!V.heldBy(who)) V.takeFloor();     // a REPLY claims the speakers — THE THIRD SEAM
    var mine = V.floor;
    var lostFloor = function () { return V.heldBy(who) && V.floor !== mine; };
    var browser = function () {
      var ok = speakBrowser(text);
      setVoiceStatus(ok ? "browser" : "none");
      return ok;
    };
    // 1) pre-cached clip (real recorded speech — works on a fully static deploy)
    return playClip(text, who, mine).then(function (done) {
      if (done) { setVoiceStatus("clip"); return true; }
      if (lostFloor()) return false;       // abandon; do NOT fall through
      if (skipProbe()) return browser();   // 3) where a Piper sidecar cannot exist
      // 2) live Piper service, ONLY if one is actually reachable
      return speakLive(text, who, mine).then(function (ok) {
        if (ok) { setVoiceStatus("piper"); return true; }
        if (lostFloor()) return false;     // …and again after the round-trip
        return browser();                  // 3) honest fallback: sound really plays
      });
    });
  }
  V.speak = speak;

  /* speakClipOnly — a voice with NO fallback, guaranteed by construction.
   *
   * bridge/index.js::handleUserTurn carries the scripted child lines, mic.js's degraded
   * Listen line, AND whatever a visitor typed or said. Synthesizing the last would read
   * their own words back in a stranger's voice. So a child line plays ONLY from a clip
   * shipped for that exact string. A separate function (not a flag on speak()) makes that
   * a property of which function you called; no path out of here reaches a synthesizer.
   * No `replaying` gate: it would mute the degraded mic's scripted line; the residual (a
   * visitor typing one of the two authored lines verbatim hears it) is bounded.
   *
   * ORDERING — the child yields, Moxie interrupts: speak() stop()s a playing child clip;
   * this refuses to start while moxieIsSpeaking(); a newer child line replaces an older
   * one. sessions/demo.json is timed so Moxie never has to cut the child
   * (test_fallback_coverage.mjs §2). */
  function speakClipOnly(text, who) {
    if (!V.enabled || !text) return Promise.resolve(false);
    who = who || "child";
    return V.loadClips().then(function (j) {
      var rel = clipInGroup(j, text, who);
      if (!rel) return false;                 // no clip -> silence
      if (V.moxieIsSpeaking()) return false;  // never talk over the robot
      if (V.current) V.stop();                // a newer child line replaces an older one
      return playUrl("audio/" + rel, { who: who, mouth: false }).then(function (done) {
        if (done) setVoiceStatus("clip");
        return done;
      }, function () { return false; });
    }, function () { return false; });
  }
  V.speakClipOnly = speakClipOnly;

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
          V.mouth(0.25 + 0.35 * Math.abs(Math.sin((Date.now() - t0) / 90)));
        }, 55);
      };
      u.onend = u.onerror = function () {
        clearInterval(mo);
        V.mouth(0);
        V.noteSpoke();
        V.current = null;
      };
      V.current = { stop: function () { clearInterval(mo); window.speechSynthesis.cancel(); } };
      window.speechSynthesis.speak(u);
      return true;
    } catch (e) { return false; }
  }

  // A 1.4 s timeout so an unreachable sidecar falls back to the browser voice quickly
  // instead of hanging (important on the static deploy).
  function speakLive(text, who, since) {
    var url = V.ttsBase.replace(/\/$/, "") + "/tts?text=" + encodeURIComponent(text.slice(0, 1000));
    var ctl = ("AbortController" in window) ? new AbortController() : null;
    var to = ctl ? setTimeout(function () { ctl.abort(); }, 1400) : 0;
    return fetch(url, ctl ? { signal: ctl.signal } : undefined).then(function (r) {
      clearTimeout(to);
      if (!r.ok) throw new Error("tts " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) { return playBytes(buf, { who: who, since: since }); })
      .catch(function () {
        clearTimeout(to);
        return false;   // caller falls back to the browser voice; no scary message
      });
  }
})();
