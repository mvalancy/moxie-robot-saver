/* voice/cloud.js — the SERVER voice: CloudTTSResponse playback (AI seam ③).
 *
 * Decoded from the wire like firmware, never via the server SDK
 * (docs/architecture/sim-as-a-client.md). Recovered proto (CloudTTS.proto · ai-seam.md §3):
 *   AudioBuffer      { bytes buffer; int32 channels; int32 sample_rate }
 *   TTSMark          { uint32 time; uint32 start; uint32 end; string type; string value }
 *   CloudTTSResponse { audio; repeated marks; event_id; chunk_num; ... }
 * `buffer` is base64 RAW little-endian int16 PCM (no container), so the AudioBuffer is
 * built by hand. Chunks of one `event_id` play in order through a serial queue.
 * Also owns the #tts-status line.
 */
(function () {
  "use strict";
  var V = window.__moxieVoice;

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
  V.decodeCloudTTS = function decodeCloudTTS(resp) {
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
  };

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

  var ttsQueue = [], ttsPlaying = null, speakingInfo = null, gestureArmed = false;
  V.cloudVoice = false;

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

  // Wait a bounded moment for the missing chunk, then play what we hold (GAP RULE).
  // Armed only while chunks are queued.
  function armGap() {
    if (gapTimer || !ttsQueue.length) return;
    gapTimer = setTimeout(function () {
      gapTimer = 0;
      if (!ttsPlaying) gapFilled = true;
      ttsPump();
    }, TTS_GAP_MS);
  }

  /* RECORDS, since sampling races a ~1 s animation: the loudest mouth-open of the
   * current/last utterance (sim/tests/test_sil.py) and each playback's event, chunks
   * played, start order and deepest queue. Reset only when a NEW event starts (a chunked
   * utterance goes silent between chunks), seeded with what already waits. */
  V.mouthPeak = 0;
  V.stats = { event_id: null, chunks_played: 0, order: [], max_pending: 0 };

  function notePending() {                       // deepest queue seen during THIS utterance
    if (utter && ttsQueue.length > V.stats.max_pending) V.stats.max_pending = ttsQueue.length;
  }

  // JSON-friendly summary WITHOUT the decoded PCM.
  function ttsSummary(d) {
    return { sampleRate: d.sampleRate, channels: d.channels, frames: d.frames,
             duration: d.duration, bytes: d.bytes, marks: d.marks.length,
             eventId: d.eventId, chunkNum: d.chunkNum };
  }
  V.speakingInfo = function () { return speakingInfo; };
  V.ttsPending = function () { return ttsQueue.length; };

  /* ---- #tts-status: one line, two writers ----
   * The live speaking indicator and env.js's probe result both want this element; direct
   * writes clobbered each other. This file OWNS it: others call setTtsHint(), painted only
   * while nothing is speaking. */
  var ttsHint = null;        // {html|text, warn} — the resting line (env.js / the Test button)
  var ttsStatusRest = null;  // the line the markup shipped, captured before any override

  function paintTtsStatus() {
    var el = document.getElementById("tts-status");
    if (!el) return;
    if (ttsStatusRest === null) ttsStatusRest = el.textContent;
    if (V.speaking && speakingInfo) {                     // the override wins, always
      el.textContent = "speaking — cloud TTS " + speakingInfo.sampleRate + " Hz · " +
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
  V.setTtsHint = function setTtsHint(hint, warn) {
    if (hint === null || hint === undefined) ttsHint = null;
    else if (typeof hint === "string") ttsHint = { text: hint, warn: !!warn };
    else ttsHint = { html: hint.html, text: hint.text,
                     warn: hint.warn === undefined ? !!warn : !!hint.warn };
    try { paintTtsStatus(); } catch (e) {}
  };

  // Does the chunk about to start begin a NEW utterance (different or unlabelled event)?
  // That alone resets the record. Also advances the chunk_num the event now expects.
  function beginUtterance(d) {
    var u = openUtterance();
    if (!u || !d.eventId || u.eventId !== d.eventId) {
      if (u && u.eventId && u.eventId !== d.eventId) flushEvent(u.eventId);
      V.mouthPeak = 0;
      // ttsQueue = what waits BEHIND this chunk, so a pre-unlock burst still counts.
      V.stats = { event_id: d.eventId, chunks_played: 0, order: [],
                  max_pending: ttsQueue.length };
      utter = u = { eventId: d.eventId, next: 0, idle: 0 };
    }
    u.idle = 0;
    u.next = d.chunkNum + 1;             // the only chunk that may follow this one
    if (!d.eventId) utter = null;        // an unlabelled payload is a one-off, not a stream
  }

  function setSpeaking(on, info) {
    V.speaking = !!on;
    speakingInfo = V.speaking ? ttsSummary(info) : null;
    try {
      if (document.body && document.body.classList)
        document.body.classList.toggle("tts-speaking", V.speaking);
      paintTtsStatus();
    } catch (e) {}
    try {
      window.dispatchEvent(new CustomEvent(V.speaking ? "moxie-tts-start" : "moxie-tts-end",
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
    var go = function () { gestureArmed = false; V.actx(); ttsPump(); };
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
    var a = V.actx();
    if (!a) {                                   // no Web Audio at all → drain honestly
      while (ttsQueue.length) ttsQueue.shift().resolve({ played: false, reason: "no-audio-context" });
      return;
    }
    if (a.state !== "running") {               // suspended → resume, else wait for a gesture
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
    if (V.current && V.current !== src) {
      try { V.current.stop ? V.current.stop() : V.current.pause(); } catch (e) {}
      V.currentWho = null;
    }
    V.takeFloor();                         // the answer owns the speakers now
    ttsPlaying = item; V.current = src;
    beginUtterance(d);                     // a NEW event resets the stats below
    setSpeaking(true, d);
    V.stats.chunks_played++;
    V.stats.order.push(d.chunkNum);
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
      V.mouth(open);
      // Read back off the avatar, so the peak witnesses the face really was driven.
      var shown = open;
      try {
        if (window.moxie && window.moxie.getMouthOpen) shown = window.moxie.getMouthOpen();
      } catch (e) {}
      if (shown > V.mouthPeak) V.mouthPeak = shown;
      raf = requestAnimationFrame(frame);
    }
    function finish() {
      if (done) return;
      done = true;
      V.noteSpoke();        // stamped per chunk; the last one is the end of the utterance
      clearTimeout(guard);
      cancelAnimationFrame(raf);
      if (V.current === src) V.current = null;
      ttsPlaying = null;
      V.mouth(0);
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
  V.playCloudTTS = function playCloudTTS(payload) {
    var dec = V.decodeCloudTTS(payload);
    // A decodable payload proves a server voice exists (env.js stops saying "no TTS server").
    if (dec.frames) V.cloudVoice = true;
    if (!V.enabled) return Promise.resolve({ played: false, reason: "muted", decoded: dec });
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
  };

  V.stopCloudTTS = function stopCloudTTS() {
    clearGap(); gapFilled = false; utter = null;      // the utterance is over; ordering resets
    while (ttsQueue.length) ttsQueue.shift().resolve({ played: false, reason: "stopped" });
    if (ttsPlaying) { try { V.current && V.current.stop && V.current.stop(); } catch (e) {} }
  };
})();
