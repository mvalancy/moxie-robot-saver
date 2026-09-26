/* mic.js — talk to Moxie: browser mic → STT → the chat loop.
 *
 * Records a clip and POSTs it to whichever ears this deployment has:
 *   • the same-origin route `POST /api/transcribe` (`functions/api/transcribe.js`) when
 *     `window.moxieMode` reports `ears`; answers the house envelope with `transcript`;
 *   • the local sidecar `POST <base>/stt` (`sim/stt/server.py`), answering the robot's
 *     `DeepgramResponse` shape. An explicit `moxie.sttBase` always wins.
 *
 * The hosted path ENCODES ITS OWN 16 kHz mono WAV: the gateway's STT answered 500 for
 * webm/Opus, ogg/Opus and mp4/AAC (evidence: `functions/api/_lib/env.js::sttFormats`), and
 * MediaRecorder never produces WAV. The local sidecar keeps MediaRecorder (ffmpeg decodes
 * anything).
 *
 * A transcript is published as a child utterance on `/devices/<id>/events/remote-chat`, as
 * a real robot would. When the ears fail the page falls back to a scripted child line
 * (spec §6) — sent via `sendScriptedTurn`, so a line nobody spoke never spends a live
 * chat/speech turn. Only a REAL transcript may. A denied mic, a too-short clip and an empty
 * transcript get no consolation line at all.
 *
 * The hosted upload carries a Turnstile token for the `transcribe` action (the ears are
 * the costlier route, billed by duration) on a request header; the local sidecar asks for
 * nothing.
 *
 * THE 15-SECOND HARD STOP lives here because the server's byte cap is not a duration cap
 * for compressed audio and a Function only sees a finished upload (live-sim-demo.md §4.1).
 * `DEMO_MAX_RECORD_MS` arrives in `/api/health`'s `limits`; 15 s without a server.
 *
 * Exposes window.moxieMic = { start, stop, toggle, isRecording, setSttBase, getSttBase,
 *                             sttTarget, maxRecordMs, stats, setCapture, encodeWav }.
 */
(function () {
  "use strict";

  /* ---- configuration ---------------------------------------------------- */

  /** The local sidecar. An explicit `moxie.sttBase` beats the same-origin route. */
  var explicitBase = null;
  try { explicitBase = localStorage.getItem("moxie.sttBase"); } catch (e) { explicitBase = null; }
  var STT_BASE = explicitBase ||
                 (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8082");

  /** Recording cap when no server publishes one (same as the server default). */
  var DEFAULT_MAX_RECORD_MS = 15000;
  /** Bounds on a server-published cap, so a malformed value cannot disable the stop. */
  var MIN_CAP_MS = 1000, MAX_CAP_MS = 600000;

  /** Client POST timeout, ABOVE the server's `DEMO_STT_TIMEOUT_MS` (12 s) so the server's
   *  honest `timeout` envelope wins; this only catches a connection that never answers. */
  var POST_TIMEOUT_MS = 20000;

  /** Smallest clip worth a request: the server's `DEMO_MIN_AUDIO_BYTES`, else 800. */
  var DEFAULT_MIN_BYTES = 800;

  var rec = null, chunks = [], stream = null, recording = false, capTimer = null;

  /* ======================================================================== *
   * SILENCE AUTO-STOP. Two rules, because it must never cut somebody off:
   *   · SILENCE_END_MS — hang up only after speech was heard and then stopped; well past a
   *     breath or a child's thinking pause.
   *   · NO_SPEECH_MS — nothing loud at all: an accidental press or a muted mic.
   * SPEECH_RMS is a generous level (room tone ~0.002-0.008, speech 0.05+): erring towards
   * "speech" only lengthens a recording. It can only end a recording EARLIER; the hard cap
   * remains the outer bound.
   * ======================================================================== */
  var SPEECH_RMS = 0.02;
  var SILENCE_END_MS = 1100;
  var NO_SPEECH_MS = 5000;
  var silenceTimer = null, speechSeen = false, startedAt = 0;

  function clearSilence() {
    if (silenceTimer !== null) { clearTimeout(silenceTimer); silenceTimer = null; }
  }

  /** One RMS block from the capture. Arms, disarms and re-arms the auto-stop. */
  function onLevel(rms) {
    if (!recording) return;
    var loud = rms >= SPEECH_RMS;
    if (loud) {
      if (!speechSeen) { speechSeen = true; stats.speechDetected++; }
      clearSilence();                       // still talking: the clock restarts
      return;
    }
    if (silenceTimer !== null) return;      // already counting down
    var wait = speechSeen ? SILENCE_END_MS : Math.max(0, NO_SPEECH_MS - (Date.now() - startedAt));
    silenceTimer = setTimeout(function () {
      silenceTimer = null;
      if (!recording) return;
      if (speechSeen) {
        stats.silenceStops++;
        status("● got it — transcribing…");
      } else {
        stats.emptyStops++;
        status("I did not hear anything");
      }
      stop();
    }, wait);
  }

  /** Recorded, never sampled (playbook rule 11). `speechDetected`/`silenceStops`/
   *  `emptyStops` let a test assert WHY a recording ended. */
  var stats = { starts: 0, stops: 0, autoStops: 0, speechDetected: 0, silenceStops: 0, emptyStops: 0, posts: 0, transcripts: 0, fallbacks: 0,
                tooShort: 0, tooLong: 0, botUnavailable: 0, botTokens: 0,
                reasons: [], lastUrl: "", lastBytes: 0,
                lastMime: "", lastCapMs: 0, lastKind: "" };

  function mode() {
    try { return window.moxieMode || null; } catch (e) { return null; }
  }

  function limits() {
    var m = mode();
    var l = m && m.limits ? m.limits() : null;
    return l && typeof l === "object" ? l : {};
  }

  function num(v, dflt, lo, hi) {
    var n = Number(v);
    if (!isFinite(n)) return dflt;
    if (n < lo || n > hi) return dflt;
    return n;
  }

  /** The cap in force: the server's if usable, else `moxie.maxRecordMs`, else 15 s. */
  function maxRecordMs() {
    var served = limits().max_record_ms;
    if (served !== undefined && served !== null) {
      var n = num(served, null, MIN_CAP_MS, MAX_CAP_MS);
      if (n !== null) return n;
    }
    var local = null;
    try { local = localStorage.getItem("moxie.maxRecordMs"); } catch (e) {}
    return num(local, DEFAULT_MAX_RECORD_MS, MIN_CAP_MS, MAX_CAP_MS);
  }

  function minBytes() { return num(limits().min_audio_bytes, DEFAULT_MIN_BYTES, 1, 5e7); }
  function maxBytes() { return num(limits().max_audio_bytes, Infinity, 1, 5e7); }

  /**
   * Where this clip is going. The mode machine owns "is there a same-origin route"
   * (`apiBase()` + `ears`); no hostname here (C3); an explicit `moxie.sttBase` always wins.
   *
   * @returns {{url: string, kind: "cloud"|"local"}}
   */
  function sttTarget() {
    var m = mode();
    var base = m && m.apiBase ? m.apiBase() : null;
    if (!explicitBase && base && m && m.ears && m.ears()) {
      return { url: base + "/api/transcribe", kind: "cloud" };
    }
    return { url: STT_BASE.replace(/\/$/, "") + "/stt", kind: "local" };
  }

  function status(t) {
    var el = document.getElementById("mic-status"); if (el) el.textContent = t;
    var b = document.getElementById("bus-status"); if (b && t) b.textContent = t;
  }

  /** The topic a child's utterance rides, exactly as `bridge.js` publishes it. */
  var USER_TOPIC = "/devices/d_sim/events/remote-chat";

  /** Words the visitor actually said — the paid path on a live deployment, by design. */
  function publishUtterance(text) {
    // the bridge's live turn if it has one, else route locally so the loop stays visible
    if (window.moxieBridge && window.moxieBridge.sendUserTurn) {
      window.moxieBridge.sendUserTurn(text);
    } else if (window.moxieBridge && window.moxieBridge.route) {
      window.moxieBridge.route(USER_TOPIC, JSON.stringify({ command: "prompt", speech: text }));
    }
  }

  /**
   * A line THIS PAGE CHOSE (the consolation for a failed turn): reaches the page like a
   * child's turn and costs nothing. `cloud-transport.js` owns `sendScriptedTurn` (it alone
   * knows whether a turn would be paid for). Without it, `sendUserTurn` is bridge.js's own
   * and already free; `canSpendLiveTurn` guards a future transport lacking the seam.
   */
  function publishScripted(text) {
    var b = window.moxieBridge;
    if (b && typeof b.sendScriptedTurn === "function") { b.sendScriptedTurn(text); return; }
    var m = mode();
    if (m && m.canSpendLiveTurn && m.canSpendLiveTurn() && b && typeof b.route === "function") {
      b.route(USER_TOPIC, JSON.stringify({ command: "prompt", speech: text }));
      return;
    }
    publishUtterance(text);
  }

  /**
   * The transcript from either shape: house envelope `transcript`, or the sidecar's
   * `channel.alternatives[0].transcript`. One parser so the two cannot drift.
   */
  function pickTranscript(body) {
    if (!body || typeof body !== "object") return "";
    if (typeof body.transcript === "string") return body.transcript.trim();
    var alt = (((body.channel || {}).alternatives || [])[0]) || {};
    return String(alt.transcript || "").trim();
  }

  /** Tell the mode machine what the server said so the badge follows reality (§4.5). */
  function note(reason, retryAfterS) {
    if (reason) stats.reasons.push(reason);
    var m = mode();
    if (m && m.note) m.note({ reason: reason || null, retry_after_s: retryAfterS || 0 });
  }

  function noteTransportError() {
    stats.reasons.push("transport_error");
    var m = mode();
    if (m && m.noteTransportError) m.noteTransportError();
  }

  /** One line of honest copy per refusal, never a status code (§7). Anything unnamed
   *  degrades silently to the scripted line. */
  var REASON_COPY = {
    rate_limited: "one at a time — give Moxie a few seconds",
    at_capacity: "Moxie has her hands full — using a scripted line",
    budget_exhausted: "the live ears are out of demo budget for now",
    upstream_down: "Moxie can't hear right now — using a scripted line",
    gateway_unreachable_or_gated: "Moxie can't hear right now — using a scripted line",
    gateway_not_configured: "no live speech-to-text here — using a scripted line",
    timeout: "that took too long to transcribe — using a scripted line",
    too_long: "that clip was too long — try a shorter one",
    too_short: "(too short)",
    bad_request: "that recording wasn't usable — using a scripted line",
    forbidden_origin: "speech-to-text is not available on this page",
    // The ears carry the bot control too (transcribe.js step 4d). Same split as mode.js:
    // the visitor's token vs the deployment's configuration.
    turnstile_failed: "Moxie needs to check you’re a real person — using a scripted line",
    turnstile_misconfigured: "Moxie’s visitor check isn’t set up right here — using a scripted line"
  };

  /* ---- the bot control, in one line on the send path --------------------- *
   * `turnstile.js` owns the widget; this file owns the upload. Outcomes:
   *   ""      — nothing to prove (control not enforced, local sidecar, or no turnstile.js
   *             loaded — the server is then not asking). Upload as-is.
   *   "<tok>" — a fresh token for the `transcribe` action (not `chat`: a typed turn's token
   *             must not pay for costly STT), sent as a HEADER since the body is raw audio.
   *   null    — enforcement on, no token: do NOT upload; degrade to the scripted line.
   * Returns a bare string (not a promise) when there is nothing to ask, so an unenforced
   * page uploads on exactly the same tick as before — `.then()` defers by a microtask, and
   * test_demo_ears.mjs observed the difference. */
  function botToken(kind) {
    if (kind !== "cloud") return "";
    var t;
    try { t = window.moxieTurnstile; } catch (e) { t = null; }
    if (!t || typeof t.getToken !== "function") return "";
    try {
      return Promise.resolve(t.getToken("transcribe")).then(function (tok) {
        return typeof tok === "string" ? tok : null;
      }, function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }

  /**
   * Upload one clip: `botToken()` first, then `upload()` plus one header (a wrapper, not a
   * re-indentation). `stats.posts` counts here so a clip refused for want of a token still
   * counts as an attempt.
   */
  function transcribe(blob) {
    var target = sttTarget();
    status("transcribing…");
    stats.posts++;
    stats.lastUrl = target.url;
    stats.lastKind = target.kind;
    stats.lastBytes = blob.size;
    stats.lastMime = blob.type || "";

    var asked = botToken(target.kind);
    // No control to satisfy: upload on the same tick (see `botToken`).
    if (typeof asked === "string") return upload(blob, target, asked);
    return asked.then(function (tok) {
      if (tok === null) {
        /* No token, no upload: degrade to a free scripted line like every other ears
         * failure, and count a transport strike so the badge stops claiming LIVE (as
         * `cloud-transport.js::botUnavailable` does for typed turns). */
        stats.botUnavailable++;
        noteTransportError();
        return fallback("Moxie couldn’t finish her visitor check — using a scripted line");
      }
      if (tok) stats.botTokens++;
      return upload(blob, target, tok);
    });
  }

  function upload(blob, target, token) {
    var opt = {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      body: blob,
      headers: { "Content-Type": blob.type || "application/octet-stream" },
    };
    // The token header the route reads. ABSENT rather than empty when not needed, so an
    // unenforced deployment and a local sidecar see the request they always saw.
    if (token) opt.headers["X-Turnstile-Response"] = token;
    try {
      if (typeof AbortSignal !== "undefined" && AbortSignal.timeout)
        opt.signal = AbortSignal.timeout(POST_TIMEOUT_MS);
    } catch (e) {}

    return fetch(target.url, opt).then(function (r) {
      return r.text().then(function (text) {
        var body = null;
        try { body = JSON.parse(text); } catch (e) { body = null; }
        return { status: r.status, body: body };
      });
    }).then(function (res) {
      var body = res.body;
      // The house envelope carries WHY, so a refused turn degrades honestly.
      var reason = body && typeof body.reason === "string" ? body.reason : null;
      if (reason) {
        note(reason, Number(body.retry_after_s) || 0);
        if (reason === "too_short") stats.tooShort++;
        if (reason === "too_long") stats.tooLong++;
        return fallback(REASON_COPY[reason] || null);
      }
      if (res.status < 200 || res.status >= 300 || !body) {
        // A sidecar that answered a bare non-2xx, or anything unparseable.
        noteTransportError();
        return fallback(null);
      }
      note(null, 0);
      var text = pickTranscript(body);
      if (!text) { status("(nothing heard)"); return null; }
      stats.transcripts++;
      status('heard: "' + text.slice(0, 40) + '"');
      publishUtterance(text);
      return text;
    }).catch(function () {
      // No route and no sidecar (static deploy, or the network went away).
      noteTransportError();
      return fallback(null);
    });
  }

  /**
   * The degraded answer: a scripted child line (from the clips we have audio for) so the
   * conversation still runs; the status line says why. Sent via `publishScripted` — a
   * consolation prize must not cost a live turn.
   */
  function fallback(why) {
    stats.fallbacks++;
    if (window.moxieStub && window.moxieStub.enabled) {
      return window.moxieStub.scriptedLines().then(function (lines) {
        if (!lines.length) { status(why || "stt unavailable — run sim/stt/server.py"); return null; }
        var text = lines[(window.moxieMic._n = (window.moxieMic._n || 0) + 1) % lines.length];
        status(why ? why + ' — heard (scripted): "' + text.slice(0, 24) + '"'
                   : 'heard (scripted): "' + text.slice(0, 36) + '"');
        publishScripted(text);
        return text;
      });
    }
    status(why || "stt unavailable — run sim/stt/server.py");
    return Promise.resolve(null);
  }

  /* ---- capture ----------------------------------------------------------- */

  /**
   * Open the microphone. Replaceable via `setCapture` so tests drive a FAKE recorder (no
   * live mic, playbook rule 11). Contract: `{recorder, stream}`; `recorder` has `start()`,
   * `stop()`, `state`, `mimeType`, `ondataavailable`, `onstop`; `stream` has `getTracks()`.
   */
  function defaultCapture() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      return Promise.reject(new Error("unsupported"));
    }
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      return { recorder: new MediaRecorder(s), stream: s };
    });
  }

  /** The rate the gateway's ears want (docs/guides/litellm-stt-setup.md). */
  var TARGET_RATE = 16000;

  /**
   * Float32 mono at `fromRate` → a complete 16-bit RIFF/WAVE at `TARGET_RATE`, by
   * nearest-neighbour decimation (fine for ASR). The header carries the TRUE rate: a wrong
   * one pitch-shifts the audio and wrecks the transcript. Exported so test_demo_ears.mjs can
   * parse it with the server's own RIFF walker (`functions/api/_lib/wav.js`).
   */
  function encodeWav(chunks, total, fromRate) {
    var src = new Float32Array(total), at = 0, i, j;
    for (i = 0; i < chunks.length; i++) { src.set(chunks[i], at); at += chunks[i].length; }

    // NEVER upsample (the header would lie); below the target keep the source rate.
    var rate = fromRate > 0 ? fromRate : TARGET_RATE;
    var ratio = rate > TARGET_RATE ? rate / TARGET_RATE : 1;
    var outRate = Math.round(rate / ratio);
    var outLen = Math.floor(src.length / ratio);

    var bytes = new Uint8Array(44 + outLen * 2);
    var view = new DataView(bytes.buffer);
    var wr = function (off, str) {
      for (var k = 0; k < str.length; k++) view.setUint8(off + k, str.charCodeAt(k));
    };
    wr(0, "RIFF");
    view.setUint32(4, 36 + outLen * 2, true);   // file size - 8
    wr(8, "WAVE");
    wr(12, "fmt ");
    view.setUint32(16, 16, true);               // PCM fmt chunk size
    view.setUint16(20, 1, true);                // format 1 = PCM
    view.setUint16(22, 1, true);                // mono
    view.setUint32(24, outRate, true);          // THE TRUE RATE
    view.setUint32(28, outRate * 2, true);      // byte rate
    view.setUint16(32, 2, true);                // block align
    view.setUint16(34, 16, true);               // bits per sample
    wr(36, "data");
    view.setUint32(40, outLen * 2, true);
    for (j = 0; j < outLen; j++) {
      var v = src[Math.floor(j * ratio)];
      if (!isFinite(v)) v = 0;
      if (v > 1) v = 1;
      if (v < -1) v = -1;
      view.setInt16(44 + j * 2, Math.round(v * 32767), true);
    }
    return bytes;
  }

  /**
   * The hosted capture: real frames, encoded as WAV on stop. Same `{recorder, stream}`
   * contract as `defaultCapture`, so caps, gates, fallback and tests are identical.
   */
  function wavCapture() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices || !Ctx) return Promise.reject(new Error("unsupported"));
    return navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    }).then(function (s) {
      var ctx = new Ctx();
      var source = ctx.createMediaStreamSource(s);
      var node = ctx.createScriptProcessor(4096, 1, 1);
      // A ScriptProcessor runs only while connected to a destination; route it through a
      // SILENT gain node to avoid a feedback loop.
      var mute = ctx.createGain();
      mute.gain.value = 0;
      var buffers = [], total = 0, running = false;
      /* Level meter: RMS of each 4096-sample block this handler already has (~12/s), which
       * drives the silence auto-stop for free. MediaRecorder never sees samples, so the
       * local path keeps the hard cap alone. */
      var onLevel = null;

      node.onaudioprocess = function (e) {
        if (!running) return;
        var ch = e.inputBuffer.getChannelData(0);
        var copy = new Float32Array(ch.length);
        copy.set(ch);
        buffers.push(copy);
        total += copy.length;
        if (onLevel) {
          var sum = 0;
          for (var i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
          onLevel(Math.sqrt(sum / ch.length));
        }
      };
      source.connect(node);
      node.connect(mute);
      mute.connect(ctx.destination);

      var recorder = {
        state: "inactive",
        mimeType: "audio/wav",
        ondataavailable: null,
        onstop: null,
        start: function () { running = true; recorder.state = "recording"; },
        stop: function () {
          if (recorder.state === "inactive") return;
          running = false;
          recorder.state = "inactive";
          try { node.disconnect(); source.disconnect(); mute.disconnect(); } catch (e) {}
          var wav = encodeWav(buffers, total, ctx.sampleRate);
          buffers = []; total = 0;
          try { ctx.close(); } catch (e) {}
          if (recorder.ondataavailable) {
            recorder.ondataavailable({ data: new Blob([wav], { type: "audio/wav" }) });
          }
          if (recorder.onstop) recorder.onstop();
        },
      };
      return {
        recorder: recorder,
        stream: s,
        /** Subscribe to the RMS of each captured block. Present only on this capture. */
        setLevelListener: function (fn) { onLevel = typeof fn === "function" ? fn : null; },
      };
    });
  }

  /** An explicit override (tests) wins; otherwise the capture follows the target, since
   *  the two ears want different things on the wire. */
  var capture = null;
  function captureFor(kind) {
    if (capture) return capture();
    return kind === "cloud" ? wavCapture() : defaultCapture();
  }

  function clearCap() {
    if (capTimer !== null) { clearTimeout(capTimer); capTimer = null; }
  }

  function releaseStream() {
    if (stream && stream.getTracks) {
      try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
    }
    stream = null;
  }

  function start() {
    if (recording) return Promise.resolve();
    return captureFor(sttTarget().kind).then(function (got) {
      rec = got && got.recorder;
      stream = (got && got.stream) || null;
      if (!rec) { status("mic unsupported in this browser"); return; }
      chunks = [];
      rec.ondataavailable = function (e) { if (e && e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () {
        clearCap();
        var blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        chunks = [];
        releaseStream();
        // Both gates are FREE refusals of clips the route would refuse anyway.
        if (blob.size < minBytes()) { stats.tooShort++; status("(too short)"); return; }
        if (blob.size > maxBytes()) { stats.tooLong++; fallback(REASON_COPY.too_long); return; }
        transcribe(blob);
      };
      rec.start();
      recording = true;
      speechSeen = false;
      startedAt = Date.now();
      clearSilence();
      // Only the hosted (WAV) capture hands us levels; MediaRecorder never sees samples.
      if (got && typeof got.setLevelListener === "function") got.setLevelListener(onLevel);
      stats.starts++;
      document.body.setAttribute("data-mic", "on");
      status("● listening…");
      if (window.moxieAudio) window.moxieAudio.sfx("listen");
      // She notices the tap (`bridge.js::moxieAlive` owns the vocabulary).
      if (window.moxieAlive) window.moxieAlive.listening();

      // THE HARD STOP (§4.1): this, not the byte cap, bounds what one visitor can spend.
      var cap = maxRecordMs();
      stats.lastCapMs = cap;
      clearCap();
      capTimer = setTimeout(function () {
        capTimer = null;
        if (!recording) return;
        stats.autoStops++;
        status("● that's plenty — transcribing…");
        stop();
      }, cap);
    }).catch(function () {
      clearCap();
      releaseStream();
      status(navigator.mediaDevices && window.MediaRecorder
        ? "mic permission denied — type a message and tap Ask instead"
        : "mic unsupported in this browser — type a message and tap Ask instead");
    });
  }

  function stop() {
    if (!recording) return;
    clearSilence();
    // Whatever comes next owns her face; `thinking()` is armed by the SEND, not the stop.
    if (window.moxieAlive) window.moxieAlive.settled();
    recording = false;
    stats.stops++;
    clearCap();
    document.body.removeAttribute("data-mic");
    try { rec && rec.state !== "inactive" && rec.stop(); } catch (e) {}
  }

  window.moxieMic = {
    start: start, stop: stop,
    toggle: function () { return recording ? stop() : start(); },
    isRecording: function () { return recording; },
    setSttBase: function (u) {
      STT_BASE = u; explicitBase = u;
      try { localStorage.setItem("moxie.sttBase", u); } catch (e) {}
    },
    getSttBase: function () { return STT_BASE; },
    /** Where a clip would go right now, and in which shape. */
    sttTarget: sttTarget,
    /** The hard stop in force, in ms — server-published when there is a server. */
    maxRecordMs: maxRecordMs,
    /** Recorded state for tests and for the console; never a live sample. */
    stats: function () { return JSON.parse(JSON.stringify(stats)); },
    /** Swap the capture source. For tests and headless harnesses; pass nothing to
     *  restore the real microphone (and the per-target choice of encoder). */
    setCapture: function (fn) { capture = typeof fn === "function" ? fn : null; },
    /** Float32 frames -> a 16 kHz 16-bit mono RIFF/WAVE file. Exposed so a test can parse
     *  the result with the server's own RIFF walker. */
    encodeWav: encodeWav,
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
