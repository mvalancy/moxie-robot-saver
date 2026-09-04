/* audio.js — Moxie's voice + UI sound effects.
 *
 * Voice, in priority order (so sound ALWAYS works, incl. a fully static deploy):
 *   1) a PRE-CACHED clip shipped with the site (audio/index.json, rendered by
 *      sim/tools/prerender_audio.py with Piper) — the fixed UI phrases + scenario
 *      lines play as real recorded speech with envelope-driven mouth-sync.
 *   2) a live local Piper service (sim/tts/server.py) IF one is actually reachable
 *      — lets arbitrary typed text be synthesized when self-hosting. SKIPPED when
 *      `mode.js` says `degraded`: that state means a hosted deployment answered, where
 *      no sidecar can exist and the 1.4 s probe is pure dead air (see skipProbe()).
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
 * THE CHILD'S VOICE: `speakClipOnly(text, "child")` plays a pre-cached clip for that
 * EXACT string and, if there is none, makes no sound at all. It is a separate entry
 * point precisely so it has no route into steps 2 and 3 above — the same code path
 * carries a visitor's own typed/spoken words, and synthesizing those would read them
 * back in a stranger's voice. See the block comment on speakClipOnly.
 *
 * Exposes window.moxieAudio = { speak, speakClipOnly, sfx, setEnabled, setTtsBase,
 *                               getClipPhrases, playCloudTTS, decodeCloudTTS, isSpeaking,
 *                               isMoxieSpeaking, isMoxieBusy }.
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
  // Did a HUMAN name that address, or is it just the localhost default? See skipProbe().
  var ttsBaseExplicit = false;
  try { ttsBaseExplicit = !!localStorage.getItem("moxie.ttsBase"); } catch (e) {}
  var enabled = true;
  var ctx = null;            // created on first user gesture (autoplay policy)
  var current = null;        // current HTMLAudio/AudioBufferSourceNode
  /* WHOSE voice `current` is. "child" means the pre-recorded prop voice of the scripted
   * session (see speakClipOnly); anything else — null included — is MOXIE herself, and
   * Moxie is never interrupted by the prop. Cleared by stop() and by playback ending. */
  var currentWho = null;
  /* Wall-clock ms of the last moment Moxie's OWN voice was live. Stamped by noteSpoke()
   * at every point her audio ends, and read only through `isMoxieBusy(graceMs)` — see
   * the block comment there for why the tail needs a timestamp and not just a flag. */
  var spokeUntil = 0;

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
    noteSpoke();             // whatever we are about to cut, her voice was live until now
    stopCloudTTS();          // cancel any queued/playing server audio too
    if (current) { try { current.pause ? current.pause() : current.stop(); } catch (e) {} current = null; }
    currentWho = null;
  }

  /* Is the ROBOT's voice occupying the speakers right now?
   *
   * `speaking` is the server voice (CloudTTSResponse); `current` is a clip, a Piper stream
   * or a browser utterance. A `current` tagged "child" is the scripted prop voice, which
   * does NOT count: Moxie may cut it off, and a newer child line may replace an older one.
   * This is the one asymmetry that keeps the two voices off each other — see
   * speakClipOnly's ORDERING note. */
  function moxieIsSpeaking() { return speaking || (!!current && currentWho !== "child"); }

  /* Remember that her voice was live as of NOW. Called at every point Moxie's audio
   * ends or is cut — so `spokeUntil` is the instant of her last syllable, give or take
   * one event loop turn. Deliberately a no-op when she was not speaking, so a `stop()`
   * on silence (or on a child clip) does not push the grace beat out. */
  function noteSpoke() { if (moxieIsSpeaking()) spokeUntil = Date.now(); }

  /* Is Moxie's voice unavailable right now — either live, or still inside the grace
   * beat after her last syllable? THE PREDICATE AMBIENT SELF-TALK ASKS.
   *
   * Two parts, and both are load-bearing:
   *
   *   SPEAKING — `moxieIsSpeaking()`, the BROAD predicate, not the exported
   *     `isSpeaking()`. `isSpeaking()` reports only `speaking`, the server-TTS flag, and
   *     is right for its one caller (cloud-transport.js's late-audio drop, which is
   *     asking specifically whether cloud audio is already in the air). It is wrong
   *     here: it is blind to a clip, a Piper stream and the browser voice, and a clip is
   *     exactly what ambient itself plays and what the degraded and scripted paths play.
   *     Guarding on it would leave the fallback deployments — the ones with no live
   *     brain, where the demo has least room to look broken — completely unguarded.
   *
   *   GRACE — the tail. `moxieIsSpeaking()` goes false on `onended`, which is the
   *     sample after her last syllable, not the beat after her last WORD. An ambient
   *     quip landing in that window does not overlap the answer but still reads as
   *     interrupting it: the visitor is a few hundred ms into hearing a sentence end,
   *     and a non-sequitur arrives on top of the silence they were reading it in. A
   *     boolean cannot express "recently", so the end is timestamped and the caller
   *     names the beat it wants.
   *
   * `graceMs` omitted or 0 gives the bare "is she speaking" answer. */
  function isMoxieBusy(graceMs) {
    if (moxieIsSpeaking()) return true;
    var g = +graceMs || 0;
    return g > 0 && spokeUntil > 0 && (Date.now() - spokeUntil) < g;
  }

  /* Fetch an audio URL, decode it, play it, and drive the mouth from its envelope.
   *
   * `opts.who` tags whose voice this is (see `currentWho`), and `opts.mouth === false`
   * leaves Moxie's face ALONE. That second option is not cosmetic: the child's clips play
   * through this same function, and a robot lip-syncing the child's words is a plainly
   * broken toy. Nothing but the envelope of Moxie's OWN voice may move her mouth. */
  function playUrl(url, opts) {
    var who = (opts && opts.who) || null;
    var driveMouth = !(opts && opts.mouth === false);
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("audio " + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var a = actx(); if (!a) return false;
      return a.decodeAudioData(buf.slice(0)).then(function (audio) {
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
  function playClip(text, who) {
    return loadClips().then(function (j) {
      if (!j) return false;
      var rel = (j[who || "moxie"] || {})[text] || (j.moxie || {})[text] || (j.child || {})[text];
      if (!rel) return false;
      return playUrl("audio/" + rel, { who: who || "moxie" });
    });
  }

  /* STRICT clip lookup: the named group and NOTHING else.
   *
   * `playClip` deliberately falls through moxie -> child so a line rendered into either
   * group still makes sound. That fallthrough is exactly wrong for the child: it would
   * answer a child line with a clip of MOXIE's voice saying the child's words. */
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

  /* Should step 2 — the 1.4 s probe for an optional local Piper sidecar — be skipped?
   * (docs/architecture/backlog/live-sim-demo.md §6.2, row 4.)
   *
   * `speakLive` asks `hostname:8081` for the line and waits up to 1.4 s before giving up
   * (see the AbortController below). On a HOSTED deployment nothing is listening on port
   * 8081 and nothing ever will be, so every uncached line costs a second and a half of
   * dead air — at exactly the moment a degraded page is trying to prove it is still
   * alive. Clip -> browser voice, directly.
   *
   * The gate is `mode.js`'s state and NOT the hostname, because the hostname cannot tell
   * these two apart:
   *   · `degraded` — `/api/health` answered, so this is a real deployment of this site
   *                  with Functions and no sidecar. SKIP.
   *   · `offline`  — no `/api/health` at all, which is precisely what a self-hoster
   *                  running `sim/serve.py` on localhost gets. Their Piper on :8081 is
   *                  the entire reason this probe exists. NEVER SKIP.
   * `live` also keeps the probe: that path is only reached when the gateway voice did not
   * arrive, and one wasted probe is the cheaper mistake.
   *
   * An address a human typed always wins over the mode — `setTtsBase` or a `moxie.ttsBase`
   * already in localStorage means somebody asked for this probe on purpose. */
  /* THE SECOND SKIP, AND THE ONE THAT WAS A REAL BUG (measured 2026-09-03).
   * :8081 is a LOCALHOST port. From any other origin the probe is not merely wasted — it
   * is a cross-origin request this site's own CSP (`connect-src 'self'`, sim/web/_headers)
   * refuses, and Chrome logs the refusal as a console error:
   *
   *     Refused to connect to 'https://moxie.mattvalancy.com:8081/tts?text=…'
   *
   * The old comment above argued "one wasted probe is the cheaper mistake" and therefore
   * kept probing in `live`. That reasoning holds for a wasted request; it does not hold
   * for a policy violation, and `live` is exactly the state the hosted deployment is in.
   * So the hostname decides ONE thing here, and honestly — the same one it decides in
   * `env.js`: whether a localhost sidecar could possibly be reachable from this browser at
   * all. A self-hoster on localhost / a LAN address / *.local is unaffected and still
   * probes in every state; nothing about which VOICE is chosen is decided here. */
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
    // 1) pre-cached clip (real recorded speech — works on a fully static deploy)
    return playClip(text, who).then(function (done) {
      if (done) { setVoiceStatus("clip"); return true; }
      // 3) …straight to the browser voice where a Piper sidecar cannot exist
      if (skipProbe()) {
        var quick = speakBrowser(text);
        setVoiceStatus(quick ? "browser" : "none");
        return quick;
      }
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

  /* ------------------------------------------------------------------------
   * speakClipOnly — a voice with NO fallback, guaranteed by construction.
   *
   * WHY IT IS A SEPARATE FUNCTION. `speak()` promises SOUND: clip -> Piper -> the
   * browser's own voice, so a line always lands. For the CHILD that promise is exactly
   * backwards. The same `handleUserTurn` path in `bridge.js` carries three different
   * things:
   *
   *   1. the two scripted child lines of `sessions/demo.json`, which we WANT audible;
   *   2. `mic.js`'s degraded "Listen", which publishes a scripted child line on purpose;
   *   3. whatever a VISITOR typed into the Talk box or said into the microphone.
   *
   * Synthesizing (3) would read a visitor's own words back at them in a stranger's voice,
   * and on the mic path talk over them. That is worse than the silence we started with.
   * So the rule is: a child line is spoken ONLY from a clip this site shipped for that
   * exact string, and there is no second choice — not Piper, not speechSynthesis, not the
   * tone generator. Making that a separate entry point rather than a `noFallback` flag
   * threaded through `speak()` is the point: the guarantee is then a property of which
   * function you called, and cannot be loosened by editing a condition. There is no code
   * path out of here that reaches a synthesizer.
   *
   * WHY NO `replaying` GATE (bridge.js's replay flag). It was considered and rejected.
   * The clip check is both the tighter guarantee and the more meaningful one — sound
   * happens only where this site authored the child's voice for that exact sentence, which
   * is a fact about the shipped assets rather than about a flag someone can set. Adding
   * `replaying` on top would buy nothing against case (3) that the clip check does not
   * already buy, and would actively BREAK case (2): the degraded microphone's scripted
   * child line runs outside a replay, and muting it is the opposite of what that fallback
   * is for. The residual it leaves is bounded and known: a visitor who types one of the
   * two authored lines verbatim hears it read back. Two strings, only after they pressed
   * send, in the voice the demo already uses. That is a curiosity, not the hazard.
   *
   * ORDERING — the child yields, Moxie interrupts. Deliberately asymmetric:
   *   · Moxie starting to speak CUTS a playing child clip, because `speak()` calls
   *     `stop()` first. Kept as-is: the robot is the subject of the page and must never
   *     be talked over by a prop.
   *   · The child NEVER cuts Moxie. This function refuses to start while
   *     `moxieIsSpeaking()`, so a visitor typing while Moxie answers cannot silence her.
   *   · A newer child line replaces an older child line still playing.
   * The shipped `sessions/demo.json` is timed so the first rule never has to fire —
   * `test_fallback_coverage.mjs` §2 asserts each scripted child line has room to finish
   * before the next event, because "Moxie cuts her off mid-word" is what silence turns
   * into the moment you give the child a voice.
   * ------------------------------------------------------------------------ */
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

  /* ---- chunk ordering: a property of the design, not of the timing ----------
   * One streamed turn arrives as several CloudTTSResponses sharing an `event_id`,
   * numbered by `chunk_num`, and a client MUST start them in that order — a child who
   * hears the end of a sentence before its middle is holding a broken toy
   * (docs/architecture/sim-as-a-client.md §"chunks").
   *
   * Sorting the QUEUE is not enough, and that was the bug: the queue only holds what is
   * *waiting*. With ~0.4 s chunks and one message per round trip, chunk 0 could finish
   * and empty the queue before chunk 1 landed; chunk 2 — alone in the queue and so
   * "first" — then started ahead of it (recorded order [0,2,1], CI run 33632125915).
   * Whether that happened was pure timing: the same code passed on a slower box.
   *
   * So the PLAYER owns the order, not the queue. It remembers the utterance being
   * assembled — which event it is and which chunk_num must come next — and starts a
   * chunk only when its turn has come, even while sitting completely idle:
   *
   *   ORDERING RULE  within one `event_id`, chunk n+1 starts only after chunk n has
   *                  started, and an event's first chunk is chunk_num 0. A chunk that
   *                  arrives ahead of its turn WAITS, however idle the player is.
   *   GAP RULE       the wait is bounded. If the chunk it is waiting for has not
   *                  arrived TTS_GAP_MS later, that chunk is written off as lost and
   *                  the lowest chunk in hand starts instead — a skipped sentence beats
   *                  a robot that stops talking. A chunk that turns up after its slot
   *                  has passed (a duplicate, or one already written off) is dropped as
   *                  `late` rather than played out of turn, so the order chunks are
   *                  STARTED in is always ascending, by construction.
   *   EVENT RULE     an event stays current for TTS_EVENT_MS after its last chunk
   *                  drained, then closes: the same event_id seen later is a NEW
   *                  utterance (a replayed session re-sends the very same ids and must
   *                  not be silenced by the rule above). A chunk of a DIFFERENT event
   *                  closes the current utterance at once — events stay FIFO, and
   *                  whatever is still queued for the old one is stale.
   *
   * A payload with no `event_id` is not part of a stream at all: it is its own
   * one-off utterance and plays FIFO, under no ordering constraint. */
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

  /* The same trick for the QUEUE. A chunked utterance is a live pipeline — chunks
   * arrive, wait behind the one playing, and drain — so `ttsPending()` sampled from
   * outside is a race an observer loses whenever the chunks are short or the box is
   * loaded: by the time "is it speaking yet?" comes back, the queue that proves the
   * chunks were pipelined has already emptied. So the page RECORDS the shape of each
   * playback instead: which event it was, how many chunks it played and in what
   * chunk_num order, and the deepest the queue ever got behind the chunk playing.
   *
   * Reset when a NEW utterance starts — a chunk of a different event — and NOT merely
   * on the false->true `speaking` edge: a chunked utterance legitimately falls silent
   * between chunks while it waits for the next one to arrive, and the record has to
   * survive that gap or the three chunks look like three utterances. Seeded with
   * whatever is already waiting, so chunks that piled up while the audio context was
   * still suspended still count; updated as chunks are enqueued and started; left
   * frozen when playback ends. Read via `lastPlaybackStats()`. */
  var stats = { event_id: null, chunks_played: 0, order: [], max_pending: 0 };

  function notePending() {                       // deepest queue seen during THIS utterance
    if (utter && ttsQueue.length > stats.max_pending) stats.max_pending = ttsQueue.length;
  }

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

  /* Which utterance does the chunk about to start belong to? A chunk of a different
   * event (or an unlabelled one-off) begins a NEW utterance, which is the only thing
   * that resets the record — see the note on `stats` above for why the speaking edge
   * cannot be the trigger. Also advances the chunk_num the event now expects. */
  function beginUtterance(d) {
    var u = openUtterance();
    if (!u || !d.eventId || u.eventId !== d.eventId) {
      if (u && u.eventId && u.eventId !== d.eventId) flushEvent(u.eventId);
      mouthPeak = 0;
      // `ttsQueue` here is what is waiting BEHIND the chunk about to start, so a burst
      // that queued up before the context could run is not lost by the reset.
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

  // Same event_id → keep the queue sorted by chunk_num; different events stay FIFO.
  // Sorting alone does NOT order playback (the queue only holds what is still waiting)
  // — the gate in ttsPump does. It just keeps the lowest chunk in hand at the front.
  function ttsEnqueue(item) {
    var i = ttsQueue.length;
    while (i > 0 && ttsQueue[i - 1].dec.eventId === item.dec.eventId &&
           ttsQueue[i - 1].dec.chunkNum > item.dec.chunkNum) i--;
    ttsQueue.splice(i, 0, item);
    notePending();
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
    // THE ORDERING GATE: start the first chunk whose turn has come — never simply the
    // head of the queue, which only means "first to arrive" (see the note above).
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
    /* THE OTHER HALF OF THE AMBIENT FIX — the answer cuts a local voice already in the air.
     *
     * `current` may still hold a LOCAL voice: an ambient quip, a Piper stream, the browser
     * voice, or a child clip. Assigning `current = src` on top of it merely FORGETS it —
     * nothing ever stops it — so it keeps playing underneath the answer. Two Moxies at
     * once is the same defect ambient.js's `moxieBusy` guard fixes from the other side.
     *
     * That guard cannot close this one. It stops ambient starting while she is SPEAKING,
     * but a turn is ~1.2 s of /api/chat plus 2–3 s of /api/speech during which she is
     * genuinely silent and ambient is right to start — and then the answer lands on top.
     * So the answer closes it on arrival: the server voice is the thing the visitor asked
     * for, and it wins, exactly as `speak()`'s unconditional `stop()` makes Moxie win over
     * the child prop (see speakClipOnly's ORDERING note).
     *
     * A cloud chunk is never what gets cut here — `finish()` clears `current` before it
     * pumps the next chunk — so a chunked utterance still plays through intact. */
    if (current && current !== src) {
      try { current.stop ? current.stop() : current.pause(); } catch (e) {}
      currentWho = null;
    }
    ttsPlaying = item; current = src;
    beginUtterance(d);                     // a NEW event resets the stats below
    setSpeaking(true, d);
    stats.chunks_played++;
    stats.order.push(d.chunkNum);
    notePending();

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
    // A chunk whose slot has already passed — a duplicate, or one the GAP RULE wrote off
    // — is dropped rather than played out of turn, so `order` stays ascending (GAP RULE).
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
    /* NARROW: the server-TTS flag alone. Blind to a clip, a Piper stream and the browser
     * voice. Keep using it only where the question really is "is CLOUD audio in the air"
     * (cloud-transport.js's late-audio drop). For "may I make a sound right now?" — what
     * ambient.js asks — use isMoxieBusy. */
    isSpeaking: function () { return speaking; },
    // BROAD: any voice of MOXIE's — clip, Piper, browser voice or server TTS. Not the child.
    isMoxieSpeaking: moxieIsSpeaking,
    /* Broad, plus a grace beat past her last syllable. `isMoxieBusy(1600)` is "she is
     * speaking, or stopped less than 1.6 s ago". See the block comment on the function. */
    isMoxieBusy: isMoxieBusy,
    speakingInfo: function () { return speakingInfo; },   // summary, no PCM
    hasCloudVoice: function () { return cloudVoice; },    // a CloudTTSResponse has arrived
    setTtsHint: setTtsHint,           // resting text of #tts-status (never clobbers speaking)
    ttsPending: function () { return ttsQueue.length; },
    // Peak mouth-open of the current/most recent cloud-TTS utterance (0..1). Survives
    // the end of playback, so "the mouth moved" is assertable without racing it.
    lastMouthPeak: function () { return mouthPeak; },
    /* What the current/most recent cloud-TTS playback actually did, recorded as it
     * happened and still readable once it is over:
     *   {event_id, chunks_played, order:[chunk_num…], max_pending}
     * `order` is the sequence chunks were STARTED in — ascending by construction, see
     * the ORDERING/GAP rules above — and `max_pending` is the deepest the queue got,
     * which proves the later chunks really were pipelined rather than dropped. */
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
