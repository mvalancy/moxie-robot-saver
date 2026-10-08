/* Part C §B11–B13: DELIBERATE BARGE-IN. The Listen tap stops her speech on purpose — the
 * playing clip AND every queued sentence of #317's pipelined tickets — within 50 ms, no
 * further ticket of that reply is redeemed, the recording never holds her own voice, and
 * from the microphone opening nothing of hers starts (a reply landing meanwhile, a stub line)
 * until the ears are done with the clip. The interrupted reply's text stays in the log. On
 * the real `mic.js`, `cloud-transport.js`, `bridge/` and `voice/` (harness Part C), on a
 * virtual clock. (What the tap does NOT hold, and what bounds the hold: 09_hold_bounds.mjs.)
 *
 * Before: `mic.start()` opened the capture under whatever she was saying; a three-sentence
 * reply kept redeeming and playing its sentences into the open microphone.
 */
import { bootPage, deep, eq, fails, ok, page } from "./harness.mjs";

const { advance, envelope, now, said, voicedChunk } = page;
const T = () => globalThis.window.moxieBridge.transportStats();
const A = () => globalThis.window.moxieAudio;
const THREE = "One sentence here. Two sentences here. Three sentences here.";
/** Tickets that name their event, so a `/api/speech` request says which reply and chunk. */
const tix = (eid, n) => [...Array(n).keys()].map((i) => ({ ticket: `v1.${eid}.T${i}.M`, event_id: eid, chunk_num: i }));
const ticketOf = (body) => /^v1\.(sim-[a-z]+)\.T(\d)\.M$/.exec(body.ticket);
/** A hosted page: health says `ears: true`, so the mic uploads to the same-origin route. */
const hosted = (other) => (path, body, spy) =>
  path === "/api/health" ? { status: 200, json: envelope({ ears: true }) } : other(path, body, spy);
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
const speeches = (w) => w.spy.fetches.filter(([p]) => p === "/api/speech").map(([, b]) => { const m = ticketOf(b); return m[1].slice(4) + m[2]; });
const uploads = (w) => w.spy.fetches.filter(([p]) => p === "/api/transcribe").length;
/** The transport harness's own ledger (a timer that threw) folds into this suite's. */
const foldTransportFails = () => { for (const f of page.fails.splice(0)) fails.push("transport harness: " + f); };

/* =========================================================================== *
 * B11. THE MIC OPENS ON A THREE-SENTENCE REPLY, sentence 1 playing and sentence 2 already
 *      being synthesised: sentence 1 is cut the instant Listen is tapped, sentence 3 is never
 *      paid for, sentence 2's audio is dropped when it lands, and nothing of hers sounds
 *      until the clip is transcribed — then the transcript is answered in her voice.
 * =========================================================================== */
{
  const p = await bootPage({ answer: hosted((path, body) => {
    if (path === "/api/chat") {
      const first = body.text === "say three things";
      return Object.assign(said(first ? THREE : "Dogs are great.", first ? "sim-barge" : "sim-dogs",
                                { speech: tix(first ? "sim-barge" : "sim-dogs", first ? 3 : 1) }), { delayMs: 1800 });
    }
    if (path === "/api/speech") { const [, eid, k] = ticketOf(body); return voicedChunk(eid, Number(k), { delayMs: 2300, seconds: 3 }); }
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "and what about dogs" }, delayMs: 2500 };
    return { status: 404, text: "" };
  }) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send("say three things");
  await advance(4500);                                 // chunk 0 landed at 4.1 s and plays (3 s); chunk 1 in flight since 4.1 s
  deep([w.spy.sounds.map((s) => [s.kind, s.t - t0]), speeches(w), A().isMoxieSpeaking()], [[["cloud", 4100]], ["barge0", "barge1"], true],
       "B11: at 4.5 s her first sentence is playing and the second is being synthesised");

  p.mic.toggle();                                      // the child taps Listen
  await advance(50);
  deep(w.spy.cuts.map((c) => c.t - t0), [4500], "B11: THE TAP CUTS HER SENTENCE AT ONCE — at 4500 ms, within 50 ms of the tap (0 ms)");
  deep([p.mic.isRecording(), A().isMoxieSpeaking(), p.attrs["data-mic"]], [true, false, "on"],
       "B11: …the capture is open, her voice is silent, and body[data-mic] says the ears are working");
  deep([T().interrupted, T().chunksSuperseded], [1, 2],
       "B11: recorded as one interruption; the reply's two remaining sentences are given up through #317's path");

  p.level(0.09);                                       // the child speaks for 300 ms…
  await advance(300);
  p.level(0.001);                                      // …then falls silent: the auto-stop sends the clip at 5.95 s
  await advance(1200);
  deep([p.mic.isRecording(), uploads(w), p.attrs["data-mic"]], [false, 1, "on"],
       "B11: the clip is uploading (6.05 s), and body[data-mic] STAYS SET through the upload (it was cleared at stop, and ambient could start a quip)");
  await advance(1000);                                 // chunk 1 landed at 6.4 s
  deep([speeches(w), T().chunksDropped, w.spy.sounds.length], [["barge0", "barge1"], 1, 1],
       "B11: NO FURTHER TICKET OF THAT REPLY IS REDEEMED (sentence 3 never paid for); sentence 2's audio, landing at 6.4 s, is dropped; nothing else has sounded");
  ok(w.spy.transcript.includes(THREE), "B11: the interrupted reply's text stays in the log");

  await advance(1500);                                 // the transcript landed at 8.45 s
  deep([p.attrs["data-mic"], chats(w), p.mic.stats().transcripts], [undefined, ["say three things", "and what about dogs"], 1],
       "B11: the ears are done once the transcript lands, and the child's words go to the brain");
  await advance(5000);
  deep(w.spy.sounds.map((s) => [s.kind, s.t - t0]), [["cloud", 4100], ["cloud", 12550]],
       "B11: her answer to the spoken line is heard at 12.55 s, and nothing of the interrupted reply resurfaced in between");
  deep(speeches(w), ["barge0", "barge1", "dogs0"], "B11: …paid for: the two sentences before the tap, then the new reply");
  eq(w.spy.cuts.length, 1, "B11: the tap's cut was the only one");
  eq(globalThis.window.moxieMode.state(), "live", "B11: the page is live throughout");
  foldTransportFails();
}

/* =========================================================================== *
 * B12. A REPLY THAT LANDS WHILE THE MIC IS OPEN WAITS: nothing of hers starts into the open
 *      microphone or the upload; when the ears are done the reply plays whole, and the
 *      spoken line (queued behind it) is answered after it.
 * =========================================================================== */
{
  const p = await bootPage({ answer: hosted((path, body) => {
    if (path === "/api/chat") {
      const first = body.text === "slow one";
      return Object.assign(said(first ? "Held until you are done." : "Hi back.", first ? "sim-held" : "sim-hi",
                                { speech: tix(first ? "sim-held" : "sim-hi", 1) }), { delayMs: first ? 1500 : 1800 });
    }
    if (path === "/api/speech") { const [, eid] = ticketOf(body); return voicedChunk(eid, 0, { delayMs: 2300, seconds: 2 }); }
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "hi" }, delayMs: 2500 };
    return { status: 404, text: "" };
  }) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send("slow one");
  await advance(1000);
  p.mic.toggle();                                      // nothing of hers is in the air yet: nothing to cut
  await advance(50);
  for (let i = 0; i < 8; i++) { p.level(0.09); await advance(400); }   // the child talks until 4.25 s
  p.level(0.001);                                      // the auto-stop sends the clip at 5.35 s; it lands at 7.85 s
  await advance(2000);                                 // t+6.25 s: the reply landed at 1.5 s, the clip is uploading
  deep([speeches(w), w.spy.sounds, w.spy.setSpeech, T().heldForEars, T().interrupted], [[], [], [], 1, 0],
       "B12: THE REPLY THAT LANDED AT 1.5 s IS HELD: no synthesis bought, nothing sounded, no bubble, while the ears work (recorded as held; nothing was interrupted)");
  await advance(8500);                                 // ears done at 7.85 s; its voice lands at 10.15 s; then the spoken line's at 14.25 s
  deep(w.spy.sounds.map((s) => [s.kind, s.t - t0]), [["cloud", 10150], ["cloud", 14250]],
       "B12: once the ears are done the held reply plays whole (10.15 s), then the answer to the spoken line (14.25 s), nothing cut");
  deep(w.spy.cuts, [], "B12: nothing was cut");
  deep(w.spy.transcript, ["slow one", "hi", "Held until you are done.", "Hi back."],
       "B12: the log reads in order: both lines, the held reply, then the spoken line's answer");
  deep(w.spy.fetches.filter(([pth]) => pth === "/api/chat").map(([, b]) => b.text), ["slow one", "hi"], "B12: the spoken line went to the brain after the held reply was handed over");
  foldTransportFails();
}

/* =========================================================================== *
 * B13. A LOCAL VOICE IS STOPPED TOO — a stub answer's clip — and on a page WITHOUT the
 *      transport's seam (a fork, bridge/ + voice/ only) mic.js stops her voice itself.
 * =========================================================================== */
{
  const p = await bootPage({ answer: hosted((path) => (path === "/api/chat"
    ? { status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "live" }) }
    : { status: 404, text: "" })) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send("tell me a joke");     // refused at once; the stub's clip starts at 450 ms
  await advance(700);
  deep(w.spy.sounds.map((s) => [s.kind, s.t - t0]), [["clip", 450]], "B13: the stub's answer is playing from its clip");
  p.mic.toggle();
  await advance(50);
  deep([w.spy.cuts.map((c) => c.t - t0), T().interrupted], [[700], 1], "B13: Listen cuts the clip at once (700 ms) — a local voice of hers is a voice of hers");
  foldTransportFails();
}
{
  const p = await bootPage({ answer: hosted((path) => (path === "/api/transcribe"
    ? { status: 200, json: { transcript: "hello" }, delayMs: 1500 } : { status: 404, text: "" })) });
  const w = p.world, t0 = now();
  const b = globalThis.window.moxieBridge;
  delete b.interruptVoice; delete b.earsOpen; delete b.earsIdle; delete b.queueUserTurn;    // a page without the transport's seams
  globalThis.window.moxieAudio.speak("Hi there! It's so good to see you.");
  await advance(100);
  deep(w.spy.sounds.map((s) => [s.kind, s.t - t0]), [["clip", 0]], "B13b: a shipped clip of hers is playing");
  p.mic.toggle();
  await advance(50);
  deep(w.spy.cuts.map((c) => c.t - t0), [100], "B13b: with no transport seam, mic.js stops her voice itself the moment Listen is tapped");
  eq(p.mic.isRecording(), true, "B13b: …and records");
  p.mic.toggle();
  await advance(10);
  eq(p.attrs["data-mic"], "on", "B13b: …the ears stay marked as working while the clip is transcribed");
  await advance(3000);
  eq(p.attrs["data-mic"], undefined, "B13b: …and the mark is cleared once the ears are done");
  foldTransportFails();
}
