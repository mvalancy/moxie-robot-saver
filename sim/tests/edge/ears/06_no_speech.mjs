/* Part B §B8–B10: NOTHING SAID IS NOTHING SENT, ONE TURN AT A TIME, and a missing microphone
 * called a missing microphone. The real `mic.js`; where the levels decide, its OWN capture
 * (`wavCapture` computes the RMS from the samples — nothing is injected).
 *
 * The defect (review lane l4, verified): a tap with no speech uploaded the clip anyway, the
 * gateway's STT answered room tone with "(machine whirring)" and silence with "you", and that
 * went out as the child's turn — 1 STT + 1 chat + 1 speech unit for words nobody said.
 */
import { advance, bootMic, deep, eq, flush, ok, pendingTimers, wavlib } from "./harness.mjs";

const NOTHING_HEARD = "I did not hear anything — tap Listen and try again";
const ONE_AT_A_TIME = "one at a time — tap Listen again once Moxie has answered";
/** What the gateway said for no-speech clips (l4: 6/6 came back non-empty). */
const PHANTOM = () => ({ status: 200, json: { transcript: "(machine whirring)" } });

/** Seeded uniform noise at `rms` — one 4096-sample ScriptProcessor block. */
function noise(rms, seed) {
  const b = new Float32Array(4096);
  let x = seed >>> 0;
  for (let i = 0; i < b.length; i++) {
    x = (Math.imul(x, 1664525) + 1013904223) >>> 0;
    b[i] = (x / 4294967296 - 0.5) * 2 * Math.sqrt(3) * rms;
  }
  return b;
}
/** A 440 Hz tone block at 0.5 amplitude (RMS 0.35): speech, as far as the level gate knows. */
function voice() {
  const b = new Float32Array(4096);
  for (let i = 0; i < b.length; i++) b[i] = Math.sin((2 * Math.PI * 440 * i) / 48000) * 0.5;
  return b;
}
/** Hand the real capture `blocks` blocks, 85 ms of virtual time apart (4096 at 48 kHz). */
async function feed(w, blocks, make) {
  const node = w.audioCtx.processors[w.audioCtx.processors.length - 1];
  for (let i = 0; i < blocks; i++) {
    node.onaudioprocess({ inputBuffer: { getChannelData: () => make(i) } });
    await advance(85);
  }
}
/** A promise and the hand that settles it. */
function deferred() {
  let resolve;
  const p = new Promise((r) => { resolve = r; });
  return { p, resolve };
}

/* B8. A TAP WITH NO SPEECH UPLOADS NOTHING — the lane's room tone, a quieter room, and a
 * muted mic, through the real capture, for 6.5 s each. */
for (const [label, rms] of [["room tone at RMS 0.0028", 0.0028], ["a quieter room at RMS 0.001", 0.001],
                            ["digital silence (a muted mic)", 0]]) {
  const w = bootMic({ realCapture: true, answer: PHANTOM });
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, `${label}: the tap opens the real capture`);
  await feed(w, 77, (i) => noise(rms, i + 1));
  await flush();
  const st = w.mic.stats();
  eq(w.mic.isRecording(), false, `${label}: 5 s with no speech gives the mic back`);
  eq(st.emptyStops, 1, `${label}: …as an EMPTY stop`);
  eq(st.speechDetected, 0, `${label}: …having heard no speech (mic.js measured it)`);
  eq(w.posts.length, 0, `${label}: NOTHING IS UPLOADED — no /api/transcribe, so no "(machine whirring)"`);
  deep(w.published, [], `${label}: …no phantom turn reaches sendUserTurn`);
  eq(st.fallbacks, 0, `${label}: …and no scripted line is spent on it either`);
  eq(st.noSpeech, 1, `${label}: …recorded as a clip dropped unsent`);
  eq(w.statusText(), NOTHING_HEARD, `${label}: …and the visitor is told, with the way back`);
  eq(w.audioCtx.closed, true, `${label}: …and the microphone is released`);
}

/* B8b. …and so does a MANUAL stop with no speech (the second tap the lane missed: 2,774 B
 * and 49,196 B clips, both over the 2,000 B floor, both uploaded and answered "you"). */
for (const [label, blocks] of [["a second tap after one block (85 ms)", 1],
                               ["a second tap after 1.5 s", 18]]) {
  const w = bootMic({ realCapture: true, answer: () => ({ status: 200, json: { transcript: "you" } }) });
  await w.mic.toggle();
  await flush();
  await feed(w, blocks, (i) => noise(0.0028, i + 1));
  w.mic.toggle();                               // the visitor taps Listen again
  await advance(50);
  await flush();
  const st = w.mic.stats();
  eq(w.mic.isRecording(), false, `${label}: stops the recording`);
  eq(st.emptyStops, 0, `${label}: …as a user stop, not the timer`);
  eq(w.posts.length, 0, `${label}: …and UPLOADS NOTHING — a quiet clip is dropped whatever its size`);
  deep(w.published, [], `${label}: …so "you" is never anybody's turn`);
  eq(st.noSpeech, 1, `${label}: …recorded as dropped unsent`);
  eq(w.statusText(), NOTHING_HEARD, `${label}: …with the same honest line`);

  // A dropped clip arms no re-tap guard: "tap Listen and try again" means NOW.
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, `${label}: …and an immediate tap DOES start again`);
  eq(w.mic.stats().ignoredTaps, 0, `${label}: …with no tap ignored`);
}

/* B8c. THE SPEECH PATH IS UNCHANGED through the same real capture: speech then quiet is one
 * upload, a manual stop after speech is one upload, and the bytes are a WAV the route takes. */
{
  const w = bootMic({ realCapture: true });
  await w.mic.toggle();
  await flush();
  await feed(w, 12, () => voice());             // ~1 s of speech
  await feed(w, 16, (i) => noise(0.001, i + 1)); // then the room again
  await flush();
  const st = w.mic.stats();
  eq(st.speechDetected, 1, "speech through the real capture is detected");
  eq(st.silenceStops, 1, "…and 1.1 s of quiet after it ends the turn");
  eq(w.posts.length, 1, "…with EXACTLY ONE upload");
  deep(w.published, ["hi moxie"], "…whose transcript is the child's turn, as before");
  eq(st.noSpeech, 0, "…and nothing dropped");
  const bytes = w.posts[0].init.body.bytes;
  eq(wavlib.sttWavProblem?.(bytes), null, "…as a WAV the route forwards (16-bit PCM, mono, 16 kHz)");
}
{
  const w = bootMic({ realCapture: true });
  await w.mic.toggle();
  await flush();
  await feed(w, 6, () => voice());
  w.mic.toggle();                               // tap to send, mid-breath
  await advance(50);
  await flush();
  eq(w.posts.length, 1, "a manual stop AFTER speech still uploads exactly once");
  deep(w.published, ["hi moxie"], "…and the words are the turn");
}

/* B9. ONE TURN AT A TIME. The idle hint says "tap it again to send"; after an auto-stop that
 * already sent, that tap re-opened the mic and uploaded a second clip 1.9 s later (lane l1). */
{
  const w = bootMic();
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1100);                          // exactly when the silence stop sends the clip
  eq(w.mic.isRecording(), false, "1.1 s of quiet after speech ended the recording");
  eq(w.posts.length, 1, "…and the auto-stop sent the clip");

  await advance(1500);
  await w.mic.toggle();                         // "…then tap it again to send"
  await flush();
  eq(w.mic.isRecording(), false, "A TAP 1.5 s AFTER AN AUTO-STOP DOES NOT START A RECORDING");
  eq(w.mic.stats().starts, 1, "…the capture is not even opened");
  eq(w.mic.stats().ignoredTaps, 1, "…the tap is recorded as ignored");
  eq(w.statusText(), ONE_AT_A_TIME, "…and answered, so the button is never dead");
  eq(w.posts.length, 1, "…and no second clip is uploaded");

  await advance(1500);                          // 3 s after the stop
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, "a tap 3 s after the auto-stop DOES start the next turn");
  eq(w.mic.stats().starts, 2, "…as a second recording");
}
{
  // The 15 s hard stop is an auto-stop too.
  const w = bootMic();
  await w.mic.toggle();
  await advance(15001);
  await flush();
  eq(w.posts.length, 1, "the hard stop sent the clip");
  await advance(1500);
  await w.mic.toggle();
  eq(w.mic.isRecording(), false, "a tap 1.5 s after the HARD stop is ignored as well");
  await advance(1000);
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, "…and one after the guard starts");
}
{
  // While the clip is still being TRANSCRIBED, a tap waits, however long that takes.
  const w = bootMic();
  const late = deferred();
  const answer = globalThis.fetch;
  globalThis.fetch = (u, init) => late.p.then(() => answer(u, init));
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1200);
  await advance(2500);                          // the re-tap guard is long gone
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), false, "A TAP WHILE THE CLIP IS BEING TRANSCRIBED DOES NOT RE-RECORD");
  eq(w.mic.stats().ignoredTaps, 1, "…recorded as ignored");
  late.resolve();
  await flush(); await flush(); await flush();
  deep(w.published, ["hi moxie"], "…the transcript then arrives and is the turn");
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, "…after which a tap starts the next turn");
}
{
  // …and while MOXIE IS THINKING: cloud-transport.js's live turn is a promise until she answers.
  const turn = deferred();
  const w = bootMic({ bridge: (rec) => ({ sendUserTurn: (t) => { rec.published.push(t); return turn.p; } }) });
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1200);
  await advance(2500);
  deep(w.published, ["hi moxie"], "the turn went out");
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), false, "A TAP WHILE SHE IS THINKING DOES NOT RE-RECORD");
  eq(w.statusText(), ONE_AT_A_TIME, "…and says so");
  turn.resolve();
  await flush();
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, "…once she has answered, a tap starts the next turn");
  eq(w.mic.stats().ignoredTaps, 1, "…exactly one tap was ignored");
}
{
  // A turn that NEVER settles (no AbortSignal.timeout on an old browser) cannot kill the button.
  const w = bootMic({ bridge: () => ({ sendUserTurn: () => new Promise(() => {}) }) });
  await w.mic.toggle();
  await flush();
  w.level(0.09);
  await advance(300);
  w.level(0.001);
  await advance(1200);
  await advance(10000);
  await w.mic.toggle();
  eq(w.mic.isRecording(), false, "a hung turn holds the button for a while…");
  await advance(20000);                         // 30 s after the send
  await w.mic.toggle();
  await flush();
  eq(w.mic.isRecording(), true, "…but never past 30 s: the button cannot die for good");
  w.mic.toggle();                               // and a stop is never refused
  eq(w.mic.isRecording(), false, "a tap that STOPS a recording is never refused");
  eq(w.mic.stats().ignoredTaps, 1, "…only the tap that would have started one was ignored");
  await advance(0);                             // let that upload land on THIS page
  await flush();
  eq(w.posts.length, 2, "…and the stopped clip is sent like any other");
}

{
  // Two taps while the browser is still ASKING for the microphone (the permission prompt):
  // the second opened another capture, and the stop released only one stream — a mic left on.
  const w = bootMic({ realCapture: true });
  const asks = [];
  let stops = 0;
  globalThis.navigator.mediaDevices.getUserMedia = () => new Promise((r) => asks.push(r));
  w.mic.toggle();
  w.mic.toggle();
  await flush();
  eq(asks.length, 1, "TWO TAPS DURING THE PERMISSION PROMPT ASK FOR ONE MICROPHONE");
  asks.forEach((r) => r({ getTracks: () => [{ stop() { stops++; } }] }));
  await flush();
  eq(w.mic.isRecording(), true, "…which then records");
  eq(w.audioCtx.processors.length, 1, "…through exactly one capture");
  eq(w.mic.stats().starts, 1, "…as one recording");
  w.mic.toggle();
  await flush();
  eq(stops, asks.length, "…and one stop releases every microphone stream that was opened");
}

/* B10. WHY THE MIC DID NOT OPEN, honestly: a machine with no microphone was told "mic
 * permission denied" (lane l1, mic_nodevice). Through the real capture's getUserMedia. */
for (const [label, err, want] of [
  ["no microphone at all (NotFoundError)", new DOMException("Requested device not found", "NotFoundError"),
   "no microphone found — type a message and tap Ask instead"],
  ["a refused permission (NotAllowedError)", new DOMException("Permission denied", "NotAllowedError"),
   "mic permission denied — type a message and tap Ask instead"],
  ["a bare Error carrying the name (how test_mic_spend.mjs fakes it)", new Error("NotAllowedError"),
   "mic permission denied — type a message and tap Ask instead"],
  ["a device another app holds (NotReadableError)", new DOMException("Could not start audio source", "NotReadableError"),
   "the microphone did not start — type a message and tap Ask instead"],
]) {
  const w = bootMic({ realCapture: true, gumError: err });
  await w.mic.toggle();
  await flush();
  eq(w.statusText(), want, `${label}: the status names it`);
  eq(w.mic.isRecording(), false, `${label}: …not recording`);
  eq(w.posts.length, 0, `${label}: …nothing uploaded`);
  eq(pendingTimers(), 0, `${label}: …no timer left running`);
}
{
  const w = bootMic({ realCapture: true });
  globalThis.navigator.mediaDevices = undefined;    // an insecure origin, or a very old browser
  await w.mic.toggle();
  await flush();
  eq(w.statusText(), "mic unsupported in this browser — type a message and tap Ask instead",
     "no mediaDevices at all is 'unsupported', as before");
}
