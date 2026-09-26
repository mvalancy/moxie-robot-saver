/* Part B §B1–B4: the silence auto-stop, the 15 s HARD STOP on a real (fake) recorder, the
 * server-published cap, where the clip goes, both reply shapes.
 */
import {
  ORIGIN, advance, bootMic, deep, eq, flush, ok, pendingTimers, recordToCap,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * B1b. THE SILENCE AUTO-STOP — "the user pressed the button and it never resets"
 * --------------------------------------------------------------------------- *
 * End the turn promptly, but NEVER cut a child off mid-sentence: both directions are
 * asserted, and the mid-sentence pause is the case that matters most.
 * --------------------------------------------------------------------------- */
{
  const w = bootMic();
  await w.mic.start();
  await flush();
  eq(w.mic.isRecording(), true, "recording");

  // Speech, then a PAUSE TO THINK that must NOT end the turn.
  w.level(0.09);
  await advance(300);
  w.level(0.002);                       // silence begins
  await advance(900);                   // …but only 900 ms of it
  eq(w.mic.isRecording(), true,
     "a 900 ms pause mid-sentence does NOT end the recording — that is a child thinking");
  w.level(0.07);                        // they carry on
  await advance(200);
  eq(w.mic.isRecording(), true, "…and speaking again keeps it open");

  // Now they really are finished.
  w.level(0.001);
  await advance(1200);
  eq(w.mic.isRecording(), false, "1.1 s of silence AFTER speech ends the recording");
  eq(w.mic.stats().silenceStops, 1, "…recorded as a silence stop, not a cap stop");
  eq(w.mic.stats().speechDetected, 1, "…having actually heard speech first");
  /* No assertion on the status text: the transcribe overwrites `#mic-status` a tick later.
   * `silenceStops` is the RECORDED fact that the room going quiet ended the recording. */
  eq(pendingTimers(), 0, "…leaving no timer behind");
}
{
  // A tap with NOTHING said: the button was pressed by accident, or the mic is muted.
  // Fifteen seconds of a listening indicator that will transcribe nothing is unkind, and
  // the size floor would refuse the clip anyway.
  const w = bootMic();
  await w.mic.start();
  await flush();
  w.level(0.001);
  await advance(4000);
  eq(w.mic.isRecording(), true, "4 s of an empty room is not yet a decision");
  await advance(1200);
  eq(w.mic.isRecording(), false, "…but 5 s with no speech at all gives the mic back");
  eq(w.mic.stats().emptyStops, 1, "…recorded as an EMPTY stop, distinct from a silence stop");
  eq(w.mic.stats().silenceStops, 0, "…and never as a silence stop: nothing was ever heard");
  eq(w.mic.stats().speechDetected, 0, "…with no speech detected");
}
{
  // THE AUTO-STOP CAN ONLY EVER SHORTEN A RECORDING. A visitor who keeps talking still
  // meets the 15 s hard cap and nothing else, so §4.1's ceiling on what one visitor can
  // spend is untouched by any of this.
  const w = bootMic();
  await w.mic.start();
  await flush();
  for (let i = 0; i < 30; i++) { w.level(0.08); await advance(490); }
  eq(w.mic.isRecording(), true, "continuous speech is never interrupted by the auto-stop");
  await advance(400);
  eq(w.mic.isRecording(), false, "…and the 15 s HARD CAP still ends it");
  eq(w.mic.stats().autoStops, 1, "…as a cap stop");
  eq(w.mic.stats().silenceStops, 0, "…not a silence stop");
}

/* --------------------------------------------------------------------------- *
 * B1. THE HARD STOP — 15 s actually stops a recorder
 * --------------------------------------------------------------------------- */
{
  const w = bootMic();
  eq(w.mic.maxRecordMs(), 15000, "the cap is the server-published DEMO_MAX_RECORD_MS");
  await w.mic.start();
  await flush();
  eq(w.mic.isRecording(), true, "recording");
  deep(w.rec.log, ["start"], "the recorder was started");

  await advance(14999);
  deep(w.rec.log, ["start"], "…and is STILL running at 14 999 ms");
  eq(w.mic.isRecording(), true, "…still recording just under the cap");

  await advance(2);
  deep(w.rec.log, ["start", "stop"], "AT 15 000 ms THE RECORDER IS STOPPED — the cap is real");
  eq(w.mic.isRecording(), false, "…and the page knows it stopped");
  eq(w.mic.stats().autoStops, 1, "…recorded as an autoStop, not a user stop");
  eq(w.bodyAttrs["data-mic"], undefined, "…and the recording indicator is cleared");
  eq(pendingTimers(), 0, "the cap timer is not left behind");
}

/* --------------------------------------------------------------------------- *
 * B2. The cap is the SERVER's number, is overridable, and survives a silly one
 * --------------------------------------------------------------------------- */
{
  // A deployment that shortens it.
  const short = bootMic({ mode: { limits: () => ({ max_record_ms: 4000 }) } });
  eq(short.mic.maxRecordMs(), 4000, "a server-published cap is obeyed");
  await short.mic.start();
  await advance(4001);
  deep(short.rec.log, ["start", "stop"], "…and stops the recorder at ITS number");

  // No server at all: the built-in 15 s still applies. An unbounded recorder is a bug on
  // a laptop too.
  const local = bootMic({ mode: null });
  eq(local.mic.maxRecordMs(), 15000, "with NO mode machine the built-in 15 s cap still applies");
  await local.mic.start();
  await advance(15001);
  deep(local.rec.log, ["start", "stop"], "…and still stops the recorder");

  // A malformed or absurd published cap falls back rather than disabling the stop.
  for (const [label, value] of [
    ["a string", "soon"], ["zero", 0], ["negative", -1], ["an hour and a half", 5400000], ["null", null],
  ]) {
    const w = bootMic({ mode: { limits: () => ({ max_record_ms: value }) } });
    eq(w.mic.maxRecordMs(), 15000, `${label} as a cap falls back to 15 s — the stop is never disabled`);
  }

  // A self-hoster can choose their own via localStorage, when no server published one.
  const custom = bootMic({ mode: null, storage: { "moxie.maxRecordMs": "3000" } });
  eq(custom.mic.maxRecordMs(), 3000, "moxie.maxRecordMs lets a self-hoster pick a different cap");

  // A user stop before the cap clears the timer — no ghost stop later.
  const early = bootMic();
  await early.mic.start();
  await advance(1000);
  early.mic.stop();
  await advance(60000);
  deep(early.rec.log, ["start", "stop"], "a user stop fires once; the cap timer does not fire again");
  eq(early.mic.stats().autoStops, 0, "…and is not recorded as an autoStop");
  eq(pendingTimers(), 0, "…with no timer left behind");
}

/* --------------------------------------------------------------------------- *
 * B3. Where the clip goes: the mode machine picks, and an explicit base wins
 * --------------------------------------------------------------------------- */
{
  const cloud = bootMic();
  deep(cloud.mic.sttTarget(), { url: ORIGIN + "/api/transcribe", kind: "cloud" },
       "live ears => the SAME ORIGIN route, with no hostname anywhere in the code (C3)");

  const noEars = bootMic({ mode: { ears: () => false } });
  eq(noEars.mic.sttTarget().kind, "local", "no ears => the local sidecar, exactly as today");
  ok(/:8082\/stt$/.test(noEars.mic.sttTarget().url), "…on port 8082");

  const offline = bootMic({ mode: null });
  eq(offline.mic.sttTarget().kind, "local", "no mode machine at all => the local sidecar");

  const noBase = bootMic({ mode: { apiBase: () => null } });
  eq(noBase.mic.sttTarget().kind, "local", "a file:// page (no apiBase) => the local sidecar");

  // An explicit base is a developer saying "use THIS", and beats the same-origin route.
  const pinned = bootMic({ storage: { "moxie.sttBase": "http://127.0.0.1:8082" } });
  eq(pinned.mic.sttTarget().kind, "local", "an explicit moxie.sttBase WINS over live ears");
  eq(pinned.mic.sttTarget().url, "http://127.0.0.1:8082/stt", "…and is used verbatim");
  const set = bootMic();
  set.mic.setSttBase("http://192.0.2.7:8082");
  eq(set.mic.sttTarget().kind, "local", "…and setSttBase pins it at runtime too");
}

/* --------------------------------------------------------------------------- *
 * B4. Both shapes parse, and the local sidecar path is UNCHANGED
 * --------------------------------------------------------------------------- */
{
  // The house envelope from /api/transcribe.
  const w = bootMic({ answer: () => ({ status: 200, json: { transcript: "hi moxie", reason: null } }) });
  await recordToCap(w);
  eq(w.posts.length, 1, "one POST");
  eq(w.posts[0].url, ORIGIN + "/api/transcribe", "…to the same-origin route");
  eq(w.posts[0].init.method, "POST", "…as a POST");
  eq(w.posts[0].init.credentials, "omit", "…with no credentials");
  eq(w.posts[0].init.headers["Content-Type"], "audio/webm;codecs=opus",
     "…and the blob's own type as the Content-Type, exactly what §3.2 says the route takes");
  deep(w.published, ["hi moxie"], "the transcript is published as a child utterance");
  eq(w.mic.stats().transcripts, 1, "…and recorded");
  ok(/heard: "hi moxie"/.test(w.statusText()), "…and shown");

  // The sidecar's DeepgramResponse, unchanged.
  const d = bootMic({
    mode: { ears: () => false },
    answer: () => ({ status: 200, json: { channel: { alternatives: [{ transcript: "look what I made", confidence: 0.9 }] } } }),
  });
  await recordToCap(d);
  ok(/\/stt$/.test(d.posts[0].url), "the sidecar is still POSTed at /stt");
  deep(d.published, ["look what I made"], "…and its DeepgramResponse still parses, unchanged");

  // An empty transcript is silence, not a failure — and publishes nothing.
  const q = bootMic({ answer: () => ({ status: 200, json: { transcript: "" } }) });
  await recordToCap(q);
  deep(q.published, [], "silence publishes nothing");
  eq(q.statusText(), "(nothing heard)", "…and says so");
  eq(q.mic.stats().fallbacks, 0, "…without burning a scripted line");
}
