/* mic.js — talk to Moxie: browser mic → STT → the chat loop.
 *
 * Captures mic audio (MediaRecorder) and POSTs the clip to whichever ears this
 * deployment actually has. There are two, and the mode machine picks between them:
 *
 *   • **the same-origin route** `POST /api/transcribe` (`functions/api/transcribe.js`),
 *     when `window.moxieMode` reports `ears` — i.e. the hosted site has a gateway and a
 *     `DEMO_STT_MODEL` configured. It answers the house envelope, with `transcript`.
 *   • **the local sidecar** `POST <base>/stt` (`sim/stt/server.py`), for anyone running
 *     the full stack at home. It answers the robot's real `DeepgramResponse` shape
 *     (docs/reverse-engineering/perception-pipeline.md). **That path is unchanged**, down
 *     to the `moxie.sttBase` override, which still WINS over the same-origin route: a
 *     developer who typed a base meant it.
 *
 * ===========================================================================
 * WHY THE HOSTED PATH ENCODES ITS OWN WAV.
 *
 * A `MediaRecorder` with no `mimeType` produces a COMPRESSED container — webm/Opus on
 * Chrome and Firefox, mp4/AAC on Safari — and never a WAV. Probed live on 2026-09-03
 * against the gateway's `stt-whisper`, one utterance in four containers: **16 kHz mono
 * RIFF/WAVE transcribed word-perfect in 2.58 s; webm/Opus, ogg/Opus and mp4/AAC each
 * answered HTTP 500.** (§10 assumption 15, and the evidence table lives in
 * `functions/api/_lib/env.js::sttFormats`.)
 *
 * So on the hosted path this file does what `mqtt/moxie_sdk/stt.py::wav_bytes` has always
 * done server-side, and does it in the browser: `getUserMedia` → `AudioContext` →
 * Float32 → downsample to 16 kHz → signed 16-bit → a RIFF header. §2.1 of the spec noted
 * that no downsampler or WAV writer existed anywhere in `sim/web`; this is it, and it is
 * the difference between ears that work and a microphone that 500s.
 *
 * **The local sidecar keeps `MediaRecorder`**, unchanged: `sim/stt/server.py` runs
 * faster-whisper, which decodes whatever ffmpeg can, so there is nothing to fix there and
 * no reason to make a home stack pay for a resample.
 * ===========================================================================
 *
 * Either way the transcript is published to the bus as a child utterance on
 * `/devices/<id>/events/remote-chat`, exactly as a real robot would, so the backend brain
 * answers it and Moxie speaks the reply — and on ANY failure the page falls back to a
 * scripted child line rather than showing a dead button (spec §6).
 *
 * ===========================================================================
 * THE 15-SECOND HARD STOP, AND WHY IT IS IN THE BROWSER.
 *
 * `DEMO_MAX_AUDIO_BYTES` (500 000) is the server's cap, and §4.1 of
 * docs/architecture/backlog/live-sim-demo.md is explicit that **it is not a duration cap
 * for a compressed container**: 500 KB is ~15 s of 16 kHz PCM but MINUTES of the
 * webm/Opus a `MediaRecorder` actually produces. A Function only ever sees a finished
 * upload, so no route can bound how long someone talks.
 *
 * **The honest ceiling on the cost of the ears is therefore this timer, and nothing
 * else.** `DEMO_MAX_RECORD_MS` (15 000) arrives in `/api/health`'s `limits`, so the
 * deployment sets it in one place and the recorder obeys; with no server at all it falls
 * back to the same 15 s, because an unbounded recorder is a bug on a laptop too.
 * ===========================================================================
 *
 * Exposes window.moxieMic = { start, stop, toggle, isRecording, setSttBase, getSttBase,
 *                             sttTarget, maxRecordMs, stats, setCapture, encodeWav }.
 */
(function () {
  "use strict";

  /* ---- configuration ---------------------------------------------------- */

  /** The local sidecar, exactly as before. An explicit `moxie.sttBase` is a developer
   *  saying "use THIS", and beats the same-origin route in `sttTarget()` below. */
  var explicitBase = null;
  try { explicitBase = localStorage.getItem("moxie.sttBase"); } catch (e) { explicitBase = null; }
  var STT_BASE = explicitBase ||
                 (location.protocol + "//" + (location.hostname || "127.0.0.1") + ":8082");

  /** The fallback recording cap, used when there is no server to publish one — a local
   *  stack, a `file://` page, a fork with no Functions. Same number as the server default
   *  (`DEMO_MAX_RECORD_MS`) so the two can never surprise each other. */
  var DEFAULT_MAX_RECORD_MS = 15000;
  /** Sanity bounds on a server-published cap: a deployment may shorten or lengthen it, but
   *  a malformed value must not disable the stop or freeze the button for an hour. */
  var MIN_CAP_MS = 1000, MAX_CAP_MS = 600000;

  /** The client-side POST timeout. Deliberately ABOVE the server's own
   *  `DEMO_STT_TIMEOUT_MS` (12 000) so the server's honest `timeout` envelope wins the
   *  race and the page learns *why*; this only catches a connection that never answers. */
  var POST_TIMEOUT_MS = 20000;

  /** The floor below which a clip is not worth a request. The server's own
   *  `DEMO_MIN_AUDIO_BYTES` when it published one, else the historical 800. */
  var DEFAULT_MIN_BYTES = 800;

  var rec = null, chunks = [], stream = null, recording = false, capTimer = null;

  /** Recorded, never sampled: `sim/test_demo_ears.mjs` asserts on these rather than on a
   *  live microphone (playbook rule 11). */
  var stats = { starts: 0, stops: 0, autoStops: 0, posts: 0, transcripts: 0, fallbacks: 0,
                tooShort: 0, tooLong: 0, reasons: [], lastUrl: "", lastBytes: 0,
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

  /** The recording cap in force right now: the server's when it published a usable one,
   *  else `moxie.maxRecordMs` for a self-hoster who wants a different number, else 15 s. */
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
   * Where this clip is going, and what shape the answer will be.
   *
   * The mode machine owns the "is there a same-origin route" question exactly as it does
   * for chat and the voice in `cloud-transport.js` — `apiBase()` plus the server's own
   * `ears` flag. Nothing here knows a hostname (C3), and an explicit `moxie.sttBase`
   * always wins so a home stack is never quietly redirected.
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

  function publishUtterance(text) {
    // Prefer the bridge's live MQTT client if it exposed one; otherwise just
    // render locally so the loop is still visible without a broker.
    if (window.moxieBridge && window.moxieBridge.sendUserTurn) {
      window.moxieBridge.sendUserTurn(text);
    } else if (window.moxieBridge && window.moxieBridge.route) {
      window.moxieBridge.route("/devices/d_sim/events/remote-chat",
        JSON.stringify({ command: "prompt", speech: text }));
    }
  }

  /**
   * The transcript out of either shape.
   *   • the house envelope (`/api/transcribe`): `transcript`, a string;
   *   • the sidecar's `DeepgramResponse`: `channel.alternatives[0].transcript`.
   * One function, so neither ear gets a parser the other could drift away from.
   */
  function pickTranscript(body) {
    if (!body || typeof body !== "object") return "";
    if (typeof body.transcript === "string") return body.transcript.trim();
    var alt = (((body.channel || {}).alternatives || [])[0]) || {};
    return String(alt.transcript || "").trim();
  }

  /** Tell the mode machine what the server said, so the badge and the poll schedule
   *  follow reality (§4.5). `bad_request`/`too_short`/`too_long` change no mode there;
   *  `upstream_down`/`budget_exhausted` degrade the page; `rate_limited` soft-degrades. */
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

  /** One line of honest copy per refusal, with never a status code or an upstream string
   *  in it (§7's last rule). Anything not named here degrades silently to the scripted
   *  line, which is the behaviour the page has today. */
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
    forbidden_origin: "speech-to-text is not available on this page"
  };

  function transcribe(blob) {
    var target = sttTarget();
    status("transcribing…");
    stats.posts++;
    stats.lastUrl = target.url;
    stats.lastKind = target.kind;
    stats.lastBytes = blob.size;
    stats.lastMime = blob.type || "";

    var opt = {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      body: blob,
      headers: { "Content-Type": blob.type || "application/octet-stream" },
    };
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
      // The house envelope carries WHY, on success and on refusal alike, so a refused
      // turn can degrade honestly instead of looking like a dead route.
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
      // No route and no sidecar (a static deploy with no Functions, or the network went
      // away). Exactly today's behaviour.
      noteTransportError();
      return fallback(null);
    });
  }

  /**
   * The degraded answer: a scripted child line, so the conversation still runs. Cycles
   * through the lines we actually have audio for. This is the path that has always been
   * here (and the reason §6.1 lists "mic.js's scripted-child fallback" as reused as-is);
   * all that is new is that the STATUS LINE can now say why.
   */
  function fallback(why) {
    stats.fallbacks++;
    if (window.moxieStub && window.moxieStub.enabled) {
      return window.moxieStub.scriptedLines().then(function (lines) {
        if (!lines.length) { status(why || "stt unavailable — run sim/stt/server.py"); return null; }
        var text = lines[(window.moxieMic._n = (window.moxieMic._n || 0) + 1) % lines.length];
        status(why ? why + ' — heard (scripted): "' + text.slice(0, 24) + '"'
                   : 'heard (scripted): "' + text.slice(0, 36) + '"');
        publishUtterance(text);
        return text;
      });
    }
    status(why || "stt unavailable — run sim/stt/server.py");
    return Promise.resolve(null);
  }

  /* ---- capture ----------------------------------------------------------- */

  /**
   * Open the microphone. Replaceable via `setCapture` so the recording logic — the caps,
   * the hard stop, the size gates — can be driven by a FAKE recorder in a test without a
   * live microphone anywhere (playbook rule 11). The contract is small on purpose:
   * `{recorder, stream}`, where `recorder` has `start()`, `stop()`, `state`, `mimeType`,
   * `ondataavailable` and `onstop`, and `stream` has `getTracks()`.
   */
  function defaultCapture() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      return Promise.reject(new Error("unsupported"));
    }
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      return { recorder: new MediaRecorder(s), stream: s };
    });
  }

  /** The rate the gateway's ears actually want. `docs/guides/litellm-stt-setup.md` says it
   *  in as many words — *"The rate that matters is 16000"* — and it is the rate of the
   *  control clip that transcribed word-perfect in the 2026-09-03 probe. */
  var TARGET_RATE = 16000;

  /**
   * Float32 mono at `fromRate` → a complete 16-bit RIFF/WAVE file at `TARGET_RATE`.
   *
   * The resample is nearest-neighbour decimation, which is honest about what it is: for
   * speech going into an ASR at a 3:1 ratio (48 000 → 16 000) it is what
   * `AudioContext`-based recorders have always done, and the transcript is what is being
   * optimised, not the fidelity. **The header carries the TRUE rate** — a WAV that claimed
   * 16000 for 48 kHz audio would pitch-shift it and wreck the transcript, which is the
   * exact warning `stt.py::wav_bytes` carries.
   *
   * Exported on `window.moxieMic` so `sim/test_demo_ears.mjs` can parse the result with
   * the SERVER's own RIFF walker (`functions/api/_lib/wav.js`) — one test pinning both
   * halves of the contract, the trick `sim/test_wav_decode.mjs` established for the voice.
   */
  function encodeWav(chunks, total, fromRate) {
    var src = new Float32Array(total), at = 0, i, j;
    for (i = 0; i < chunks.length; i++) { src.set(chunks[i], at); at += chunks[i].length; }

    // NEVER upsample: a header claiming 16 000 for 8 kHz audio would be a lie, and a lie
    // in a WAV header pitch-shifts the audio and wrecks the transcript (the warning
    // `stt.py::wav_bytes` carries). Below the target we keep the source rate and say so.
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
   * The hosted capture: real audio frames, encoded as WAV on stop.
   *
   * It presents the SAME `{recorder, stream}` contract as `defaultCapture`, so the cap
   * timer, the size gates, the fallback and the tests are all identical either way — the
   * only difference is what comes out of the blob.
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
      // A ScriptProcessor only runs while it is connected to a destination — but routing
      // the microphone to the speakers would be a howling feedback loop, so it goes
      // through a SILENT gain node. The frames still arrive; nothing is audible.
      var mute = ctx.createGain();
      mute.gain.value = 0;
      var buffers = [], total = 0, running = false;

      node.onaudioprocess = function (e) {
        if (!running) return;
        var ch = e.inputBuffer.getChannelData(0);
        var copy = new Float32Array(ch.length);
        copy.set(ch);
        buffers.push(copy);
        total += copy.length;
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
      return { recorder: recorder, stream: s };
    });
  }

  /** An explicit override (tests, a headless harness) always wins; otherwise the capture
   *  follows the TARGET, because the two ears want different things on the wire. */
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
        // Both gates are FREE refusals — a clip this size would be refused by the route
        // anyway (`too_short`/`too_long`), so not sending it is a request that never
        // happens rather than one that costs a round trip to be told no.
        if (blob.size < minBytes()) { stats.tooShort++; status("(too short)"); return; }
        if (blob.size > maxBytes()) { stats.tooLong++; fallback(REASON_COPY.too_long); return; }
        transcribe(blob);
      };
      rec.start();
      recording = true;
      stats.starts++;
      document.body.setAttribute("data-mic", "on");
      status("● listening…");
      if (window.moxieAudio) window.moxieAudio.sfx("listen");

      // THE HARD STOP (§4.1). See the header: this, and not the byte cap, is what bounds
      // how much gateway time one visitor can spend on the ears.
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
        ? "mic permission denied" : "mic unsupported in this browser");
    });
  }

  function stop() {
    if (!recording) return;
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
