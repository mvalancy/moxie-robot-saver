/* voice/index.js — publishes window.moxieAudio (loads LAST of the voice parts). */
(function () {
  "use strict";
  var V = window.__moxieVoice;

  window.moxieAudio = {
    speak: V.speak, sfx: V.sfx, stop: V.stop,
    // The child's voice: a clip, or nothing. NEVER a synthesizer — see voice/local.js.
    speakClipOnly: V.speakClipOnly,
    // --- server voice (CloudTTSResponse on /devices/{id}/commands/tts) ---
    playCloudTTS: V.playCloudTTS,     // decode + play; resolves when it finished
    decodeCloudTTS: V.decodeCloudTTS, // pure wire decode (unit-tested in node)
    // NARROW: server TTS only ("is CLOUD audio in the air", cloud-transport.js).
    isSpeaking: function () { return V.speaking; },
    // BROAD: any voice of MOXIE's — clip, Piper, browser voice or server TTS. Not the child.
    isMoxieSpeaking: V.moxieIsSpeaking,
    // Broad + grace: isMoxieBusy(1600) = speaking, or stopped < 1.6 s ago.
    isMoxieBusy: V.isMoxieBusy,
    speakingInfo: V.speakingInfo,                          // summary, no PCM
    hasCloudVoice: function () { return V.cloudVoice; },   // a CloudTTSResponse has arrived
    setTtsHint: V.setTtsHint,         // resting text of #tts-status (never clobbers speaking)
    ttsPending: V.ttsPending,
    // Peak mouth-open of the current/last cloud-TTS utterance (0..1); survives playback.
    lastMouthPeak: function () { return V.mouthPeak; },
    // {event_id, chunks_played, order:[chunk_num…] (ascending by construction),
    //  max_pending (proves later chunks were pipelined)} of the current/last playback.
    lastPlaybackStats: function () {
      var s = V.stats;
      return { event_id: s.event_id, chunks_played: s.chunks_played,
               order: s.order.slice(), max_pending: s.max_pending };
    },
    setEnabled: function (v) { V.enabled = !!v; if (!V.enabled) V.stop(); },
    isEnabled: function () { return V.enabled; },
    setTtsBase: function (u) { V.ttsBase = u; V.ttsBaseExplicit = true;
                               try { localStorage.setItem("moxie.ttsBase", u); } catch (e) {} },
    getTtsBase: function () { return V.ttsBase; },
    getClipPhrases: function () {   // pre-cached Moxie lines guaranteed to make sound
      return V.loadClips().then(function (j) { return j && j.moxie ? Object.keys(j.moxie) : []; });
    },
    isUnlocked: function () { return !!(V.ctx && V.ctx.state === "running"); },
  };

  // Unlock on the first gesture and announce it, so ambient waits for real audio.
  var unlocked = false;
  function unlock() {
    if (unlocked) return;
    V.actx();
    unlocked = true;
    window.dispatchEvent(new CustomEvent("moxie-audio-unlocked"));
  }
  ["pointerdown", "click", "keydown", "touchstart"].forEach(function (ev) {
    window.addEventListener(ev, unlock, { once: true, passive: true });
  });
})();
