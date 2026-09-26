/* audio.js — Moxie's voice + UI sound effects.
 *
 * Voice, in priority order (so sound ALWAYS works, incl. a fully static deploy):
 *   1) a PRE-CACHED clip (audio/index.json, rendered by sim/tools/prerender_audio.py)
 *      with envelope-driven mouth-sync;
 *   2) a live local Piper service (sim/tts/server.py) if reachable — skipped where no
 *      sidecar can exist (see skipProbe());
 *   3) the browser's speechSynthesis, so free text still makes sound.
 * THE SERVER VOICE: `playCloudTTS(payload)` plays a real `CloudTTSResponse` from
 * `/devices/{id}/commands/tts`, decoding base64 PCM in the client like firmware does.
 * THE CHILD'S VOICE: `speakClipOnly(text, "child")` plays a clip for that exact string or
 * nothing — it has no route into steps 2/3 (see its block comment).
 * SFX: synthesized WebAudio cues, no assets.
 *
 * Exposes window.moxieAudio = { speak, speakClipOnly, sfx, setEnabled, setTtsBase,
 *                               getClipPhrases, playCloudTTS, decodeCloudTTS, isSpeaking,
 *                               isMoxieSpeaking, isMoxieBusy }.
 * The three speaking predicates differ: `isSpeaking()` = server TTS only;
 * `isMoxieSpeaking()` = any voice of hers (not the child); `isMoxieBusy(ms)` = that plus a
 * grace beat. "May I make a sound now?" wants isMoxieBusy.
 *
 * THREE SPEAKING PREDICATES, AND THEY ARE NOT INTERCHANGEABLE. `isSpeaking()` is narrow
 * — the server-TTS flag only. `isMoxieSpeaking()` is broad — any voice of hers, by any
 * of the three routes above, excluding the child prop. `isMoxieBusy(ms)` is broad plus a
 * grace beat past her last syllable. Anything asking "may I make a sound right now?"
 * wants the last of the three; see the block comment on `isMoxieBusy`.
 */
(function () {
  "use strict";

  var TTS_BASE = (localStorage.getItem("moxie.ttsBase") ||
                  (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8081"));
  // Did a HUMAN name that address, or is it the localhost default? See skipProbe().
  var ttsBaseExplicit = false;
  try { ttsBaseExplicit = !!localStorage.getItem("moxie.ttsBase"); } catch (e) {}
  var enabled = true;
  var ctx = null;            // created on first user gesture (autoplay policy)
  var current = null;        // current HTMLAudio/AudioBufferSourceNode
  // Whose voice `current` is: "child" = the scripted prop voice; anything else (null
  // included) is Moxie, who is never interrupted by the prop.
  var currentWho = null;
  var spokeUntil = 0;       // ms of Moxie's last live syllable (see isMoxieBusy)

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
  /* THE THIRD SEAM — a clip still LOADING when the answer arrives. playUrl/speakLive
   * fetch and decode before any node exists, so an ambient quip that began loading while
   * Moxie was silent would start on top of her answer. `floor` counts who last took the
   * speakers; an ambient load refuses to start if it moved (stale-async guard for audio).
   *
   * Who takes the floor: a REPLY does (ttsPump, and speak() for non-ambient lines).
   * Ambient never does, and the child prop neither takes it nor is subject to it (bumping
   * in stop() dropped the child's scripted line; ttsPump alone left scripted replies
   * unguarded). Rule: NOTHING STARTS ON TOP OF MOXIE ANSWERING.
   * Losing the floor abandons the whole clip->Piper->browser chain; falling through to
   * the browser voice would talk over the answer just as loudly. */
  var floor = 0;
  function takeFloor() { floor++; }
  /** Only ambient yields the floor (the child prop must not — see above). */
  function heldBy(who) { return who === "ambient"; }

  function stop() {
    noteSpoke();             // whatever we are about to cut, her voice was live until now
    stopCloudTTS();          // cancel any queued/playing server audio too
    if (current) { try { current.pause ? current.pause() : current.stop(); } catch (e) {} current = null; }
    currentWho = null;
  }

  // Is MOXIE's voice on the speakers? `speaking` = server voice; `current` = clip, Piper
  // or browser utterance. A "child" clip does not count (see speakClipOnly ORDERING).
  function moxieIsSpeaking() { return speaking || (!!current && currentWho !== "child"); }

  // Stamp the end of her voice. No-op when she was not speaking, so stop() on silence or
  // on a child clip does not push the grace beat out.
  function noteSpoke() { if (moxieIsSpeaking()) spokeUntil = Date.now(); }

  /* THE PREDICATE AMBIENT SELF-TALK ASKS: is her voice live, or inside the grace beat?
   * Uses the BROAD moxieIsSpeaking() — the exported isSpeaking() is blind to clips, Piper
   * and the browser voice, which would leave the scripted/degraded deployments unguarded.
   * The grace tail exists because a quip right after her last syllable still reads as
   * interrupting; a boolean cannot say "recently", so the end is timestamped.
   * `graceMs` omitted or 0 gives the bare "is she speaking" answer. */
  function isMoxieBusy(graceMs) {
    if (moxieIsSpeaking()) return true;
    var g = +graceMs || 0;
    return g > 0 && spokeUntil > 0 && (Date.now() - spokeUntil) < g;
  }

  /* Fetch, decode and play a URL, driving the mouth from its envelope. `opts.who` tags
   * the voice; `opts.mouth === false` leaves the face alone — the child's clips play here
   * too, and only Moxie's own voice may move her mouth. */
  function playUrl(url, opts) {
    var who = (opts && opts.who) || null;
    var driveMouth = !(opts && opts.mouth === false);
    // The caller's floor snapshot where given (loadClips() may await after speak()'s stop()).
    var mine = (opts && opts.since != null) ? opts.since : floor;
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("audio " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var a = actx(); if (!a) return false;
      return a.decodeAudioData(buf.slice(0)).then(function (audio) {
        if (heldBy(who) && floor !== mine) return false;   // Moxie began answering mid-load
        var src = a.createBufferSource(); src.buffer = audio;
        var analyser = a.createAnalyser(); analyser.fftSize = 256;
        src.connect(analyser); analyser.connect(a.destination);
        current = src; currentWho = who;
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
          if (driveMouth) cancelAnimationFrame(raf);
          if (current === src) { noteSpoke(); current = null; currentWho = null; }
          if (driveMouth && window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(0);
        };
        src.start(0);
        if (driveMouth) pump();
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
  function playClip(text, who, since) {
    return loadClips().then(function (j) {
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
    var msg = { clip: "🔊 playing (pre-cached voice)", piper: "🔊 playing (Piper)",
                browser: "🔊 playing (browser voice)", none: "muted / no audio available" }[mode];
    if (msg) el.textContent = msg;
  }

  /* Skip step 2 (the 1.4 s probe of a local Piper on :8081)?
   *  · pageCouldReachSidecar(): :8081 is a LOCALHOST port. From a public origin the probe
   *    is a cross-origin request our CSP (`connect-src 'self'`) refuses and logs, so only
   *    localhost / LAN / *.local pages ever probe.
   *  · otherwise skip when `mode.js` says `degraded` — /api/health answered, so this is a
   *    hosted deployment with no sidecar and the probe is dead air. `offline` (no
   *    /api/health: a self-hoster on sim/serve.py) and `live` keep probing.
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
    if (ttsBaseExplicit) return false;
    try {
      return !!(window.moxieMode && typeof window.moxieMode.state === "function" &&
                window.moxieMode.state() === "degraded");
    } catch (e) { return false; }
  }

  function speak(text, who) {
    if (!enabled || !text) return Promise.resolve(false);
    stop();
    if (!heldBy(who)) takeFloor();         // a REPLY claims the speakers — see THE THIRD SEAM
    var mine = floor;
    // 1) pre-cached clip (real recorded speech — works on a fully static deploy)
    return playClip(text, who, mine).then(function (done) {
      if (done) { setVoiceStatus("clip"); return true; }
      if (heldBy(who) && floor !== mine) return false;   // abandon; do NOT fall through
      // 3) …straight to the browser voice where a Piper sidecar cannot exist
      if (skipProbe()) {
        var quick = speakBrowser(text);
        setVoiceStatus(quick ? "browser" : "none");
        return quick;
      }
      // 2) live Piper service, ONLY if one is actually reachable
      return speakLive(text, who, mine).then(function (ok) {
        if (ok) { setVoiceStatus("piper"); return true; }
        if (heldBy(who) && floor !== mine) return false;   // …and again after the round-trip
        // 3) honest fallback: the browser's own voice, so sound really plays
        var spoke = speakBrowser(text);
        setVoiceStatus(spoke ? "browser" : "none");
        return spoke;
      });
    });
  }

  /* speakClipOnly — a voice with NO fallback, guaranteed by construction.
   *
   * bridge.js::handleUserTurn carries the scripted child lines, mic.js's degraded Listen
   * line, AND whatever a visitor typed or said. Synthesizing the last would read their
   * own words back in a stranger's voice. So a child line plays ONLY from a clip shipped
   * for that exact string — no Piper, no speechSynthesis, no tone. A separate function
   * (not a flag on speak()) makes that a property of which function you called; no path
   * out of here reaches a synthesizer. No `replaying` gate: it would mute the degraded
   * mic's scripted line and add nothing; the residual (a visitor typing one of the two
   * authored lines verbatim hears it) is bounded.
   *
   * ORDERING — the child yields, Moxie interrupts: speak() stop()s a playing child clip;
   * this refuses to start while moxieIsSpeaking(); a newer child line replaces an older
   * one. sessions/demo.json is timed so Moxie never has to cut the child
   * (test_fallback_coverage.mjs §2). */
  function speakClipOnly(text, who) {
    if (!enabled || !text) return Promise.resolve(false);
    who = who || "child";
    return loadClips().then(function (j) {
      var rel = clipInGroup(j, text, who);
      if (!rel) return false;                 // no clip -> silence, exactly as before
      if (moxieIsSpeaking()) return false;    // never talk over the robot
      if (current) stop();                    // a newer child line replaces an older one
      return playUrl("audio/" + rel, { who: who, mouth: false }).then(function (done) {
        if (done) setVoiceStatus("clip");
        return done;
      }, function () { return false; });
    }, function () { return false; });
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
        noteSpoke();
        current = null;
      };
      current = { stop: function () { clearInterval(mo); window.speechSynthesis.cancel(); } };
      window.speechSynthesis.speak(u);
      return true;
    } catch (e) { return false; }
  }

  function speakLive(text, who, since) {
    var url = TTS_BASE.replace(/\/$/, "") + "/tts?text=" + encodeURIComponent(text.slice(0, 1000));
    // Fast timeout so an unreachable service falls back to the browser voice
    // quickly instead of hanging (important on the static deploy).
    var ctl = ("AbortController" in window) ? new AbortController() : null;
    var to = ctl ? setTimeout(function () { ctl.abort(); }, 1400) : 0;
    var mine = (since != null) ? since : floor;         // see THE THIRD SEAM
    return fetch(url, ctl ? { signal: ctl.signal } : undefined).then(function (r) {
      clearTimeout(to);
      if (!r.ok) throw new Error("tts " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var a = actx(); if (!a) return false;
      return a.decodeAudioData(buf.slice(0)).then(function (audio) {
        if (heldBy(who) && floor !== mine) return false;   // Moxie began answering mid-load
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
          cancelAnimationFrame(raf); noteSpoke(); current = null;
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
  // CloudTTSResponse playback — the SERVER voice (AI seam ③), decoded from the wire
  // like firmware, never via the server SDK (docs/architecture/sim-as-a-client.md).
  // Recovered proto (CloudTTS.proto · docs/architecture/ai-seam.md §3):
  //   AudioBuffer      { bytes buffer; int32 channels; int32 sample_rate }
  //   TTSMark          { uint32 time; uint32 start; uint32 end; string type; string value }
  //   CloudTTSResponse { audio; repeated marks; event_id; chunk_num; ... }
  // `buffer` is base64 RAW little-endian int16 PCM (no container), so the AudioBuffer is
  // built by hand. Chunks of one `event_id` play in order through a serial queue.
  // ------------------------------------------------------------------------

  var TTS_DEFAULT_RATE = 24000;      // CloudTTSResponse default when unset
  var TTS_MIN_RATE = 3000, TTS_MAX_RATE = 384000;   // Web Audio createBuffer limits

  // Viseme (Polly/Piper alphabet) → mouth opening; unknown marks get a mid-open default.
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

  /* PURE decode: CloudTTSResponse (object or JSON) → planar Float32 + metadata. No DOM,
   * so node tests can check the maths. Tolerant of every missing/partial field. */
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

  /* ---- chunk ordering: owned by the PLAYER, not the queue ---------------------
   * One streamed turn = several CloudTTSResponses sharing `event_id`, numbered by
   * `chunk_num`, and they MUST start in order. Sorting the queue is not enough — it only
   * holds what is waiting, so chunk 2 could start before a late chunk 1.
   *   ORDERING RULE  within an event, chunk n+1 starts only after chunk n; an event's
   *                  first chunk is 0. An early chunk WAITS, however idle the player.
   *   GAP RULE       the wait is bounded by TTS_GAP_MS; then the missing chunk is written
   *                  off and the lowest in hand starts. A chunk arriving after its slot
   *                  is dropped as `late`, so start order is ascending by construction.
   *   EVENT RULE     an event stays current TTS_EVENT_MS after it drains, then closes (a
   *                  replay re-sends the same ids). A different event closes it at once.
   * A payload with no `event_id` is a one-off and plays FIFO. */
  var TTS_GAP_MS = 1200;        // how long a missing chunk is waited for
  var TTS_EVENT_MS = 5000;      // how long an idle event stays the current utterance
  var utter = null;             // {eventId, next, idle} — the utterance being assembled
  var gapTimer = 0, gapFilled = false;

  function clearGap() { if (gapTimer) { clearTimeout(gapTimer); gapTimer = 0; } }

  // The utterance still being assembled, or null once it has gone stale (EVENT RULE).
  function openUtterance() {
    if (utter && utter.idle && (Date.now() - utter.idle) > TTS_EVENT_MS) utter = null;
    return utter;
  }

  // Has this queued chunk's turn come? (ORDERING RULE)
  function startable(item) {
    var d = item.dec, u = openUtterance();
    if (!d.eventId) return true;                       // unlabelled → not part of a stream
    if (u && u.eventId === d.eventId) return d.chunkNum <= u.next;
    return d.chunkNum === 0;                           // a new event starts at its first chunk
  }

  // The utterance is over; anything still queued for it is stale (EVENT RULE).
  function flushEvent(eventId) {
    for (var i = ttsQueue.length - 1; i >= 0; i--)
      if (ttsQueue[i].dec.eventId === eventId)
        ttsQueue.splice(i, 1)[0].resolve({ played: false, reason: "superseded" });
  }

  // Nothing may start yet: wait a bounded moment for the chunk we are missing, then
  // give up on it and play what we hold (GAP RULE). Armed only while chunks are queued.
  function armGap() {
    if (gapTimer || !ttsQueue.length) return;
    gapTimer = setTimeout(function () {
      gapTimer = 0;
      if (!ttsPlaying) gapFilled = true;
      ttsPump();
    }, TTS_GAP_MS);
  }

  // Loudest mouth-open of the current/last cloud-TTS utterance, kept after it ends so
  // "did the face move?" does not race a ~1 s animation (sim/tests/test_sil.py).
  var mouthPeak = 0;

  /* The same trick for the queue: a RECORD of each playback (event, chunks played, start
   * order, deepest queue), since sampling ttsPending() races. Reset only when a NEW event
   * starts (a chunked utterance goes silent between chunks), seeded with what already
   * waits, frozen when playback ends. Read via `lastPlaybackStats()`. */
  var stats = { event_id: null, chunks_played: 0, order: [], max_pending: 0 };

  function notePending() {                       // deepest queue seen during THIS utterance
    if (utter && ttsQueue.length > stats.max_pending) stats.max_pending = ttsQueue.length;
  }

  function mouth(v) {
    try { if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(v); } catch (e) {}
  }

  // JSON-friendly summary WITHOUT the decoded PCM.
  function ttsSummary(d) {
    return { sampleRate: d.sampleRate, channels: d.channels, frames: d.frames,
             duration: d.duration, bytes: d.bytes, marks: d.marks.length,
             eventId: d.eventId, chunkNum: d.chunkNum };
  }

  /* ---- #tts-status: one line, two writers -------------------------------
   * The live speaking indicator and env.js's probe result both want this element;
   * direct writes clobbered each other. audio.js OWNS it: others call setTtsHint() and
   * the hint is painted only while nothing is speaking. */
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

  // Resting text of #tts-status: a string, or {text}/{html} + optional `warn`; null
  // clears. Never paints over a live speaking indicator.
  function setTtsHint(hint, warn) {
    if (hint === null || hint === undefined) ttsHint = null;
    else if (typeof hint === "string") ttsHint = { text: hint, warn: !!warn };
    else ttsHint = { html: hint.html, text: hint.text,
                     warn: hint.warn === undefined ? !!warn : !!hint.warn };
    try { paintTtsStatus(); } catch (e) {}
  }

  // Does the chunk about to start begin a NEW utterance (different or unlabelled event)?
  // That alone resets the record. Also advances the chunk_num the event now expects.
  function beginUtterance(d) {
    var u = openUtterance();
    if (!u || !d.eventId || u.eventId !== d.eventId) {
      if (u && u.eventId && u.eventId !== d.eventId) flushEvent(u.eventId);
      mouthPeak = 0;
      // ttsQueue = what waits BEHIND this chunk, so a pre-unlock burst still counts.
      stats = { event_id: d.eventId, chunks_played: 0, order: [],
                max_pending: ttsQueue.length };
      utter = u = { eventId: d.eventId, next: 0, idle: 0 };
    }
    u.idle = 0;
    u.next = d.chunkNum + 1;             // the only chunk that may follow this one
    if (!d.eventId) utter = null;        // an unlabelled payload is a one-off, not a stream
  }

  function setSpeaking(on, info) {
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

  // Keep one event's chunks sorted by chunk_num (events stay FIFO). Only a convenience:
  // the gate in ttsPump orders playback.
  function ttsEnqueue(item) {
    var i = ttsQueue.length;
    while (i > 0 && ttsQueue[i - 1].dec.eventId === item.dec.eventId &&
           ttsQueue[i - 1].dec.chunkNum > item.dec.chunkNum) i--;
    ttsQueue.splice(i, 0, item);
    notePending();
  }

  // Autoplay: a suspended context keeps the audio queued until the next real gesture.
  function armGesture() {
    if (gestureArmed) return;
    gestureArmed = true;
    var go = function () { gestureArmed = false; actx(); ttsPump(); };
    ["pointerdown", "click", "keydown", "touchstart"].forEach(function (ev) {
      window.addEventListener(ev, go, { once: true, passive: true });
    });
  }

  function ttsPump() {
    if (ttsPlaying) return;
    if (!ttsQueue.length) {                     // nothing in hand: start the EVENT RULE clock
      clearGap(); gapFilled = false;
      if (utter && !utter.idle) utter.idle = Date.now();
      return;
    }
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
    // THE ORDERING GATE: the first chunk whose turn has come, not simply the head.
    var qi = 0;
    while (qi < ttsQueue.length && !startable(ttsQueue[qi])) qi++;
    if (qi >= ttsQueue.length) {
      if (!gapFilled) { armGap(); return; }     // wait a bounded moment for the missing chunk
      qi = 0;                                   // …it never came: play the lowest we hold
    }
    gapFilled = false; clearGap();
    var item = ttsQueue.splice(qi, 1)[0], d = item.dec;
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
    /* The answer cuts a LOCAL voice already in the air (ambient quip, Piper, browser,
     * child clip); reassigning `current` alone would leave it playing underneath.
     * Ambient's moxieBusy guard cannot cover this: a turn has seconds of genuine silence
     * before the answer lands. A cloud chunk is never cut here — finish() clears
     * `current` before pumping the next. */
    if (current && current !== src) {
      try { current.stop ? current.stop() : current.pause(); } catch (e) {}
      currentWho = null;
    }
    takeFloor();                           // the answer owns the speakers now
    ttsPlaying = item; current = src;
    beginUtterance(d);                     // a NEW event resets the stats below
    setSpeaking(true, d);
    stats.chunks_played++;
    stats.order.push(d.chunkNum);
    notePending();

    // Mouth: the audio envelope, raised to the marks[] viseme/word track when present.
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
      // Read back off the avatar, so the peak witnesses the face really was driven.
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
      noteSpoke();          // stamped per chunk; the last one is the end of the utterance
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
    // If `onended` never fires, never strand the SIM in the speaking state.
    guard = setTimeout(finish, d.duration * 1000 + 1500);
    try { src.start(0); } catch (e) { return finish(); }
    frame();
  }

  // Play one CloudTTSResponse; resolves {played, decoded, reason?} when THIS payload is
  // done. Never rejects — a client that throws on bad audio goes mute.
  function playCloudTTS(payload) {
    var dec = decodeCloudTTS(payload);
    // A decodable payload proves a server voice exists (env.js stops saying "no TTS server").
    if (dec.frames) cloudVoice = true;
    if (!enabled) return Promise.resolve({ played: false, reason: "muted", decoded: dec });
    if (!dec.frames) return Promise.resolve({ played: false, reason: "empty", decoded: dec });
    // A chunk whose slot has passed (duplicate / written off) is dropped (GAP RULE).
    var cur = openUtterance();
    if (dec.eventId && cur && cur.eventId === dec.eventId && dec.chunkNum < cur.next)
      return Promise.resolve({ played: false, reason: "late", decoded: dec });
    var item = { dec: dec, resolve: null };
    var p = new Promise(function (res) { item.resolve = res; });
    ttsEnqueue(item);
    ttsPump();
    return p;
  }

  function stopCloudTTS() {
    clearGap(); gapFilled = false; utter = null;      // the utterance is over; ordering resets
    while (ttsQueue.length) ttsQueue.shift().resolve({ played: false, reason: "stopped" });
    if (ttsPlaying) { try { current && current.stop && current.stop(); } catch (e) {} }
  }

  window.moxieAudio = {
    speak: speak, sfx: sfx, stop: stop,
    // The child's voice: a clip, or nothing. NEVER a synthesizer — see speakClipOnly.
    speakClipOnly: speakClipOnly,
    // --- server voice (CloudTTSResponse on /devices/{id}/commands/tts) ---
    playCloudTTS: playCloudTTS,       // decode + play; resolves when it finished
    decodeCloudTTS: decodeCloudTTS,   // pure wire decode (unit-tested in node)
    // NARROW: server TTS only ("is CLOUD audio in the air", cloud-transport.js). For
    // "may I make a sound now?" use isMoxieBusy.
    isSpeaking: function () { return speaking; },
    // BROAD: any voice of MOXIE's — clip, Piper, browser voice or server TTS. Not the child.
    isMoxieSpeaking: moxieIsSpeaking,
    // Broad + grace: isMoxieBusy(1600) = speaking, or stopped < 1.6 s ago.
    isMoxieBusy: isMoxieBusy,
    speakingInfo: function () { return speakingInfo; },   // summary, no PCM
    hasCloudVoice: function () { return cloudVoice; },    // a CloudTTSResponse has arrived
    setTtsHint: setTtsHint,           // resting text of #tts-status (never clobbers speaking)
    ttsPending: function () { return ttsQueue.length; },
    // Peak mouth-open of the current/last cloud-TTS utterance (0..1); survives playback.
    lastMouthPeak: function () { return mouthPeak; },
    // {event_id, chunks_played, order:[chunk_num…] (ascending by construction),
    //  max_pending (proves later chunks were pipelined)} of the current/last playback.
    lastPlaybackStats: function () {
      return { event_id: stats.event_id, chunks_played: stats.chunks_played,
               order: stats.order.slice(), max_pending: stats.max_pending };
    },
    setEnabled: function (v) { enabled = !!v; if (!enabled) stop(); },
    isEnabled: function () { return enabled; },
    setTtsBase: function (u) { TTS_BASE = u; ttsBaseExplicit = true;
                               try { localStorage.setItem("moxie.ttsBase", u); } catch (e) {} },
    getTtsBase: function () { return TTS_BASE; },
    getClipPhrases: function () {   // pre-cached Moxie lines guaranteed to make sound
      return loadClips().then(function (j) { return j && j.moxie ? Object.keys(j.moxie) : []; });
    },
    isUnlocked: function () { return !!(ctx && ctx.state === "running"); },
  };

  // Unlock on the first gesture and announce it, so ambient waits for real audio.
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
