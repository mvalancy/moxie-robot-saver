/* voice/core.js — Moxie's voice + UI sound effects, part 1 of 4 (load order: sim.html).
 *
 * Voice, in priority order (so sound ALWAYS works, incl. a fully static deploy):
 *   1) a PRE-CACHED clip (audio/index.json, rendered by sim/tools/prerender_audio.py)
 *      with envelope-driven mouth-sync;
 *   2) a live local Piper service (sim/tts/server.py) if reachable (see skipProbe());
 *   3) the browser's speechSynthesis, so free text still makes sound.
 * THE SERVER VOICE (voice/cloud.js) plays a real `CloudTTSResponse` from
 * `/devices/{id}/commands/tts`, decoding base64 PCM in the client like firmware does.
 * THE CHILD'S VOICE (`speakClipOnly`) is a clip for that exact string or nothing.
 * SFX: synthesized WebAudio cues, no assets.
 *
 * THREE SPEAKING PREDICATES, NOT INTERCHANGEABLE: `isSpeaking()` = server TTS only;
 * `isMoxieSpeaking()` = any voice of hers by any route, not the child prop;
 * `isMoxieBusy(ms)` = that plus a grace beat. "May I make a sound now?" wants isMoxieBusy.
 *
 * Classic scripts sharing `window.__moxieVoice` (`V`), created fresh here, for the same
 * reasons as bridge/core.js. voice/index.js publishes window.moxieAudio.
 */
(function () {
  "use strict";
  var V = window.__moxieVoice = {
    enabled: true,
    ctx: null,             // created on first user gesture (autoplay policy)
    current: null,         // the playing HTMLAudio / AudioBufferSourceNode / utterance handle
    // Whose voice `current` is: "child" = the scripted prop voice; anything else (null
    // included) is Moxie, who is never interrupted by the prop.
    currentWho: null,
    speaking: false,       // server TTS on the speakers (voice/cloud.js)
    spokeUntil: 0,         // ms of Moxie's last live syllable (see isMoxieBusy)
    floor: 0,              // THE THIRD SEAM, below
  };

  V.actx = function () {
    if (!V.ctx) { var C = window.AudioContext || window.webkitAudioContext; V.ctx = C ? new C() : null; }
    if (V.ctx && V.ctx.state === "suspended") V.ctx.resume();
    return V.ctx;
  };

  V.mouth = function (v) {
    try { if (window.moxie && window.moxie.setMouthOpen) window.moxie.setMouthOpen(v); } catch (e) {}
  };

  // ---- sound effects (synthesized; no files) ----
  var SFX = {
    connect:  [[660, 0.06], [990, 0.10]],
    disconnect: [[440, 0.08], [300, 0.12]],
    listen:   [[880, 0.05]],
    icon:     [[1320, 0.05], [1760, 0.06]],
    click:    [[520, 0.03]],
    error:    [[220, 0.14]],
  };
  V.sfx = function sfx(name, vol) {
    if (!V.enabled) return;
    var a = V.actx(); if (!a) return;
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
  };

  /* THE THIRD SEAM — a clip still LOADING when the answer arrives. Local playback fetches
   * and decodes before any node exists, so an ambient quip that began loading while Moxie
   * was silent would start on top of her answer. `floor` counts who last took the
   * speakers; an ambient load refuses to start if it moved (stale-async guard for audio).
   *
   * A REPLY takes the floor (ttsPump, and speak() for non-ambient lines). Ambient never
   * does, and the child prop neither takes it nor is subject to it (bumping in stop()
   * dropped the child's scripted line; ttsPump alone left scripted replies unguarded).
   * Rule: NOTHING STARTS ON TOP OF MOXIE ANSWERING. Losing the floor abandons the whole
   * clip->Piper->browser chain; falling through would talk over the answer just as loudly. */
  V.takeFloor = function () { V.floor++; };
  /** Only ambient yields the floor (the child prop must not — see above). */
  V.heldBy = function (who) { return who === "ambient"; };

  // Is MOXIE's voice on the speakers? A "child" clip does not count (speakClipOnly ORDERING).
  V.moxieIsSpeaking = function () { return V.speaking || (!!V.current && V.currentWho !== "child"); };

  // Stamp the end of her voice. No-op when she was not speaking, so stop() on silence or
  // on a child clip does not push the grace beat out.
  V.noteSpoke = function () { if (V.moxieIsSpeaking()) V.spokeUntil = Date.now(); };

  /* THE PREDICATE AMBIENT SELF-TALK ASKS: is her voice live, or inside the grace beat?
   * BROAD on purpose — isSpeaking() is blind to clips, Piper and the browser voice, which
   * would leave the scripted/degraded deployments unguarded. A quip right after her last
   * syllable still reads as interrupting, so the end is timestamped. `graceMs` omitted or
   * 0 gives the bare "is she speaking" answer. */
  V.isMoxieBusy = function (graceMs) {
    if (V.moxieIsSpeaking()) return true;
    var g = +graceMs || 0;
    return g > 0 && V.spokeUntil > 0 && (Date.now() - V.spokeUntil) < g;
  };

  V.stop = function stop() {
    V.noteSpoke();           // whatever we are about to cut, her voice was live until now
    V.stopCloudTTS();        // cancel any queued/playing server audio too
    if (V.current) {
      try { V.current.pause ? V.current.pause() : V.current.stop(); } catch (e) {}
      V.current = null;
    }
    V.currentWho = null;
  };
})();
