/* Part B §B5–B8: the page never goes dead, a consolation line never spends a live turn, the
 * free client-side gates, capture failures, the browser WAV encoder, source guards.
 */
import {
  FULL, MIC_SRC, ORIGIN, advance, bootMic, deep, envmod, eq, flush, ok, pendingTimers,
  recordToCap, route, wavlib,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * B5. §6 — the page NEVER goes dead, for any reason the server can send
 * --------------------------------------------------------------------------- */
{
  const cases = [
    ["rate_limited", 429, /one at a time/i],
    ["at_capacity", 503, /hands full/i],
    ["budget_exhausted", 503, /budget/i],
    ["upstream_down", 503, /can't hear/i],
    ["gateway_unreachable_or_gated", 503, /can't hear/i],
    ["gateway_not_configured", 503, /scripted/i],
    ["timeout", 504, /too long/i],
    ["bad_request", 400, /wasn't usable/i],
    ["too_long", 400, /too long/i],
    ["forbidden_origin", 403, /not available/i],
  ];
  for (const [reason, status, copy] of cases) {
    const w = bootMic({
      answer: () => ({ status, json: { ok: false, degraded: true, reason, retry_after_s: 7, transcript: "" } }),
    });
    await recordToCap(w);
    eq(w.mic.stats().fallbacks, 1, `${reason}: falls back to a scripted line`);
    eq(w.published.length, 1, `${reason}: …and a child line still reaches the bus — never a dead button`);
    ok(copy.test(w.statusText()), `${reason}: the status says why (got "${w.statusText()}")`);
    ok(!/\b\d{3}\b/.test(w.statusText()), `${reason}: …with no raw status code shown to a visitor`);
    deep(w.notes.map((n) => n.reason), [reason], `${reason}: reported to the mode machine, so the badge follows`);
    eq(w.notes[0].retry_after_s, 7, `${reason}: …with the Retry-After the server sent`);
  }

  // A network failure with no envelope at all: three of those degrade the page (§6.3),
  // and this one turn still answers.
  const dead = bootMic({ answer: () => ({ reject: true }) });
  await recordToCap(dead);
  eq(dead.mic.stats().fallbacks, 1, "a network failure still answers from the scripted repertoire");
  deep(dead.notes.map((n) => n.reason), ["transport_error"], "…and is reported as a transport error");

  // A non-2xx with no readable envelope (an old sidecar, a proxy error page).
  const bare = bootMic({ mode: { ears: () => false }, answer: () => ({ status: 500, text: "boom" }) });
  await recordToCap(bare);
  eq(bare.mic.stats().fallbacks, 1, "a bare non-2xx still answers");

  // And with no stub loaded at all, it says something rather than nothing.
  const nostub = bootMic({ answer: () => ({ reject: true }) });
  globalThis.window.moxieStub = null;
  await recordToCap(nostub);
  ok(nostub.statusText().length > 0, "with no stub either, the status line is still honest, never blank");
}

/* --------------------------------------------------------------------------- *
 * B5b. THE CONSOLATION LINE MAY NOT SPEND A LIVE TURN
 * --------------------------------------------------------------------------- *
 * The scripted child line published via `moxieBridge.sendUserTurn` would be a full paid
 * `/api/chat` + `/api/speech` turn on words nobody said. `mic.js` must use the free seam
 * (`sim/test_cloud_transport.mjs` 6b prices the seam; `sim/test_mic_spend.mjs` counts
 * real requests in Chrome).
 * --------------------------------------------------------------------------- */
{
  const liveBridge = (rec) => ({ sendScriptedTurn: (t) => rec.scripted.push(t) });
  const LIVE = { canSpendLiveTurn: () => true };

  // Every degraded path a live page can reach, including the two that never even upload.
  const paths = [
    ["a refusal the mode ignores (bad_request)",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "bad_request" } }) }],
    ["a server too_short",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "too_short" } }) }],
    ["a server too_long",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "too_long" } }) }],
    ["a timeout (only the THIRD of which degrades the page)",
     { mode: LIVE, answer: () => ({ status: 504, json: { ok: false, reason: "timeout" } }) }],
    ["a network failure with no envelope",
     { mode: LIVE, answer: () => ({ reject: true }) }],
    ["an unparseable non-2xx",
     { mode: LIVE, answer: () => ({ status: 500, text: "boom" }) }],
    ["a clip over max_audio_bytes, refused CLIENT-side with no upload at all",
     { mode: LIVE, recorder: { size: 900000 } }],
  ];
  for (const [label, opts] of paths) {
    const w = bootMic(Object.assign({ bridge: liveBridge }, opts));
    await recordToCap(w);
    eq(w.mic.stats().fallbacks, 1, `${label}: the visitor is still consoled with a scripted line`);
    eq(w.scripted.length, 1, `${label}: …through the FREE scripted seam`);
    deep(w.published, [],
         `${label}: …AND NOT ONE WORD REACHED sendUserTurn — no /api/chat, no /api/speech`);
  }

  // A REAL transcript on the very same live page still spends its turn, exactly as before.
  {
    const w = bootMic({ mode: LIVE, bridge: liveBridge,
                        answer: () => ({ status: 200, json: { transcript: "hi moxie" } }) });
    await recordToCap(w);
    deep(w.published, ["hi moxie"], "a real transcript STILL goes through sendUserTurn — the paid path is for words");
    deep(w.scripted, [], "…and never through the scripted seam");
    eq(w.mic.stats().transcripts, 1, "…recorded as a transcript");
  }

  // A page that CANNOT spend takes exactly the path it takes today: sendUserTurn, which is
  // bridge/'s own and answers from stub.js for free. Nothing here needed changing.
  {
    const w = bootMic({ answer: () => ({ reject: true }) });      // no canSpendLiveTurn at all
    await recordToCap(w);
    eq(w.published.length, 1, "with nothing spendable the scripted line still goes through sendUserTurn");
    deep(w.routed, [], "…and nothing was routed around it");
  }

  // Belt and braces: a live page whose transport wrapped the bridge WITHOUT offering the
  // seam must still not pay. The line is echoed locally instead.
  {
    const w = bootMic({ mode: LIVE, answer: () => ({ reject: true }) });
    await recordToCap(w);
    deep(w.published, [], "a live page with no scripted seam STILL does not reach sendUserTurn");
    eq(w.routed.length, 1, "…the line is echoed locally instead");
    const echo = w.routed[0] || ["", "{}"];
    eq(echo[0], "/devices/d_sim/events/remote-chat", "…on the child-utterance topic");
    ok(String(JSON.parse(echo[1] || "{}").speech || "").length > 0, "…carrying the scripted words");
  }
}

/* --------------------------------------------------------------------------- *
 * B6. The free client-side gates — a doomed upload never happens
 * --------------------------------------------------------------------------- */
{
  const tiny = bootMic({ recorder: { size: 500 } });
  await recordToCap(tiny);
  eq(tiny.posts.length, 0, "a clip under min_audio_bytes IS NEVER UPLOADED — no request at all");
  eq(tiny.statusText(), "(too short)", "…and says so");
  eq(tiny.mic.stats().tooShort, 1, "…recorded");

  const huge = bootMic({ recorder: { size: 900000 } });
  await recordToCap(huge);
  eq(huge.posts.length, 0, "a clip over max_audio_bytes is never uploaded either");
  eq(huge.mic.stats().tooLong, 1, "…recorded");
  eq(huge.published.length, 1, "…and still answers with a scripted line");

  // With no server-published floor, the historical 800-byte gate is what applies.
  const old = bootMic({ mode: null, recorder: { size: 900 } });
  await recordToCap(old);
  eq(old.posts.length, 1, "with no published floor the historical 800-byte gate applies, unchanged");
}

/* --------------------------------------------------------------------------- *
 * B7. Capture failures and the honest button
 * --------------------------------------------------------------------------- */
{
  const denied = bootMic();
  denied.mic.setCapture(() => Promise.reject(new Error("NotAllowedError")));
  await denied.mic.start();
  await flush();
  eq(denied.mic.isRecording(), false, "a denied microphone leaves the page not recording");
  ok(denied.statusText().length > 0, "…and says something");
  eq(pendingTimers(), 0, "…with no cap timer left running");

  // start() twice is one recording; stop() twice is one stop.
  const w = bootMic();
  await w.mic.start();
  await w.mic.start();
  await flush();
  deep(w.rec.log, ["start"], "start() while recording is a no-op");
  w.mic.stop();
  w.mic.stop();
  deep(w.rec.log, ["start", "stop"], "stop() when not recording is a no-op");
}

/* --------------------------------------------------------------------------- *
 * B7b. §10 assumption 15's CONSEQUENCE — the browser encodes WAV for the hosted ear
 * --------------------------------------------------------------------------- *
 * `MediaRecorder` cannot produce WAV, so the hosted path builds one. The assertion that
 * matters parses `mic.js`'s output with the SERVER's own RIFF walker: both halves of the
 * contract, no server and no browser.
 * --------------------------------------------------------------------------- */
{
  // ---- the encoder, against functions/api/_lib/wav.js -----------------------
  const w = bootMic();
  const tone = (n, rate) => {
    const f = new Float32Array(n);
    for (let i = 0; i < n; i++) f[i] = Math.sin((2 * Math.PI * 440 * i) / rate) * 0.5;
    return f;
  };
  const frames = tone(48000, 48000);          // one second at a browser's usual rate
  const wav = w.mic.encodeWav([frames], frames.length, 48000);

  eq(wav.length, 44 + 16000 * 2, "one second at 48 kHz becomes 16 000 samples plus a 44-byte header");
  const parsed = wavlib.pcmFromAudio(wav, { sampleRate: 22050, channels: 1 });
  eq(parsed.container, "wav", "THE SERVER'S OWN RIFF WALKER READS IT as a wav");
  eq(parsed.sampleRate, 16000, "…at 16 000 Hz — the rate gateway-voice-and-ears.md says matters");
  eq(parsed.channels, 1, "…mono");
  eq(parsed.pcm.length, 16000 * 2, "…with the expected PCM length");
  // The header fields of the control clip the gateway was measured to transcribe.
  deep({ rate: parsed.sampleRate, ch: parsed.channels, bits: 16, container: parsed.container },
       { rate: 16000, ch: 1, bits: 16, container: "wav" },
       "…identical in shape to the control WAV the gateway accepted live");

  // The route agrees: this is a container it will forward, and it sniffs as one.
  const kind = route.audioKind(wav, null);
  eq(kind.ext, "wav", "the route sniffs the browser's own file as a wav");
  ok(envmod.readConfig(FULL).sttFormats.includes(kind.ext),
     "…and it is inside DEMO_STT_FORMATS, so it is forwarded rather than refused");

  // Never upsample: a header claiming a rate the audio does not have wrecks a transcript.
  const low = w.mic.encodeWav([tone(8000, 8000)], 8000, 8000);
  eq(wavlib.pcmFromAudio(low, { sampleRate: 22050, channels: 1 }).sampleRate, 8000,
     "audio already below 16 kHz keeps its TRUE rate — the header never lies");
  // No frames at all is a bare 44-byte header. The server's parser REFUSES it (a WAV with
  // no data chunk is unreadable) — and it can never get there, because 44 bytes is far
  // under both the client's floor and DEMO_MIN_AUDIO_BYTES. Two independent guards, and
  // the cheap one runs first.
  const empty = w.mic.encodeWav([], 0, 48000);
  eq(empty.length, 44, "no frames is a bare 44-byte RIFF header");
  ok(empty.length < envmod.readConfig(FULL).minAudioBytes,
     "…which is under DEMO_MIN_AUDIO_BYTES, so it is refused for free before any parser sees it");
  let refused = null;
  try { wavlib.pcmFromAudio(empty, { sampleRate: 22050, channels: 1 }); } catch (e) { refused = e.kind; }
  eq(refused, "unreadable", "…and the server's parser would refuse it anyway");
  // Out-of-range samples clamp rather than wrapping into noise.
  const hot = w.mic.encodeWav([new Float32Array([2, -2, NaN, 0])], 4, 16000);
  const dv = new DataView(hot.buffer, hot.byteOffset);
  deep([dv.getInt16(44, true), dv.getInt16(46, true), dv.getInt16(48, true)], [32767, -32767, 0],
       "samples outside [-1,1] and NaN clamp instead of wrapping");

  // ---- and it is what actually goes on the wire ----------------------------
  const live = bootMic({ realCapture: true });
  await live.mic.start();
  await flush();
  eq(live.mic.isRecording(), true, "the hosted path opens its own capture");
  deep(live.gum[0], { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } },
       "…asking getUserMedia for mono, which is what the encoder writes");
  ok(live.audioCtx.processors.length === 1, "…through exactly one ScriptProcessor");

  // Feed it a second of audio, the way a browser would.
  const node = live.audioCtx.processors[0];
  for (let i = 0; i < 12; i++) {
    node.onaudioprocess({ inputBuffer: { getChannelData: () => tone(4096, 48000) } });
  }
  await advance(15001);                        // the hard stop fires and encodes
  await flush();
  eq(live.audioCtx.closed, true, "the AudioContext is CLOSED on stop — no mic left running");
  eq(live.posts.length, 1, "one POST");
  eq(live.posts[0].url, ORIGIN + "/api/transcribe", "…to the same-origin route");
  eq(live.posts[0].init.headers["Content-Type"], "audio/wav",
     "…AS audio/wav, not the webm the gateway answers 500 to");
  const blob = live.posts[0].init.body;
  ok(blob.bytes && blob.bytes.length > 44, "…carrying real encoded bytes");
  const onWire = wavlib.pcmFromAudio(blob.bytes, { sampleRate: 22050, channels: 1 });
  eq(onWire.container, "wav", "THE BYTES ON THE WIRE PARSE AS A WAV on the server side");
  eq(onWire.sampleRate, 16000, "…at 16 000 Hz");

  // ---- while the local sidecar still gets a MediaRecorder ------------------
  const home = bootMic({ realCapture: true, mode: { ears: () => false } });
  let threw = null;
  await home.mic.start().catch((e) => { threw = e; });
  await flush();
  // `MediaRecorder` in this harness throws on construction, which is exactly how we prove
  // the local path still reaches for it rather than the WAV encoder.
  eq(home.audioCtx.processors.length, 0,
     "the LOCAL path does not build an AudioContext — it still uses MediaRecorder, unchanged");
  eq(home.mic.isRecording(), false, "…and a MediaRecorder that will not construct fails safely");
  ok(home.statusText().length > 0, "…with an honest status line, never a silent dead button");
}

/* --------------------------------------------------------------------------- *
 * B8. The source-level guards the other suites expect to keep holding
 * --------------------------------------------------------------------------- */
{
  for (const m of ["start", "stop", "toggle", "setSttBase"]) {
    ok(MIC_SRC.includes(m + ":") || MIC_SRC.includes("function " + m), `mic.js still exposes ${m}`);
  }
  ok(MIC_SRC.includes("events/remote-chat"),
     "mic.js still publishes the transcript as a child utterance on events/remote-chat");
  ok(!/graphlings|mattvalancy|pages\.dev/i.test(MIC_SRC),
     "mic.js names no deployment hostname — the base comes from the mode machine (C3)");
  ok(/\bsk-[A-Za-z0-9_-]{16,}/.test(MIC_SRC) === false, "mic.js carries no key-shaped literal");
}
