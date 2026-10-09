/* Part C §B20–B23: THE RECORDER'S OWN ENDINGS, on the local path. The hold on everything of
 * hers is taken only once the recorder is actually running, and it is released on every way a
 * recording can end — including the ways `mic.js` never asked for — and it is bounded even if
 * none of them comes. On the real `mic.js`, `cloud-transport.js`, `mode.js`, `bridge/` and
 * `voice/` (harness Part C), on a virtual clock, with a MediaRecorder-shaped recorder: the
 * local sidecar path (a chat-only deployment, a self-hosted page with local engines, a tap
 * before `/api/health` has said there are ears), where a real recorder can throw from
 * `start()` (NotSupportedError on an inactive stream) and fires its own `stop` when its
 * tracks end (a headset unplugged, the OS took the microphone).
 *
 * Before (the W3-S16 review's probes A and B, on a5830a3): `earsOpen()` ran before
 * `rec.start()`, so a `start()` that threw left the ears "working" with nothing to end them —
 * a typed line read "Moxie will answer that next." and was never POSTed, a hurt child's line
 * typed a minute later never got the grown-up redirect (eleven minutes), and a retry tap plus
 * another line sent nothing: dead until reload. A recorder that stopped by itself left
 * `recording` true, which `earsDone()` took for a new recording: the spoken line and a hurt
 * line typed after it waited half an hour, until the next Listen tap. And nothing bounded the
 * hold itself: `EARS_HOLD_MAX_MS` was only a term in `TURN_MAX_MS`.
 */
import { bootPage, deep, eq, fails, ok, page } from "./harness.mjs";

const { advance, envelope, now, said, voicedChunk } = page;
const T = () => globalThis.window.moxieBridge.transportStats();
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
const chatStatus = () => globalThis.document.getElementById("chat-status").textContent;
const sounds = (w, t0) => w.spy.sounds.map((s) => [s.kind, s.t - t0]);
const typed = (t) => globalThis.window.moxieTypedTurn.send(t);
/** The transport harness's own ledger (a timer that threw) folds into this suite's. */
const foldTransportFails = () => { for (const f of page.fails.splice(0)) fails.push("transport harness: " + f); };
/** The rule table's redirect for a hurt child, as `/api/chat` answers it: reason `blocked`,
 *  the line in `messages`, no voice ticket (it speaks from the local voice). */
const HURT = "i fell off my bike and my arm is bleeding";
const REDIRECT = "Thank you for telling me. Feelings this big need a grown-up who loves you, not a robot. Will you talk to one right now?";
const redirectSpokenAt = (w, t0) => w.spy.said.filter((s) => s.text === REDIRECT).map((s) => s.t - t0);
/** The local sidecar's path, once the harness has stripped the page's origin off the URL
 *  (`mic.js` builds it from `location`: `<origin>:8082/stt`). */
const SIDECAR = ":8082/stt";
/** A CHAT-ONLY deployment: health says `ears: false`, so a clip goes to the local sidecar,
 *  which answers the robot's `DeepgramResponse` shape after 800 ms. A hurt line gets the
 *  redirect 1.2 s later; anything else a voiced reply (the chat 1.5 s, its sentence 1 s on). */
const chatOnly = (over) => (path, body) => {
  if (path === "/api/health") return { status: 200, json: envelope(Object.assign({ ears: false }, over || {})) };
  if (path === SIDECAR) return { status: 200, json: { channel: { alternatives: [{ transcript: "spoken words" }] } }, delayMs: 800 };
  if (path === "/api/chat") {
    if (body.text === HURT) return Object.assign(said(REDIRECT, "sim-blk", { degraded: true, reason: "blocked" }), { delayMs: 1200 });
    const eid = "sim-" + String(body.text).replace(/[^a-z]/g, "").slice(0, 8);
    return Object.assign(said("Reply to " + body.text, eid, { speech: [{ ticket: `v1.${eid}.T0.M`, event_id: eid, chunk_num: 0 }] }), { delayMs: 1500 });
  }
  if (path === "/api/speech") return voicedChunk(/^v1\.(sim-[a-z]+)\./.exec(body.ticket)[1], 0, { delayMs: 1000, seconds: 2 });
  return { status: 404, text: "" };
};
/** A MediaRecorder-shaped recorder (the local sidecar path). `stop()` fires `dataavailable`
 *  then `stop` ASYNCHRONOUSLY, as a real one does; `startThrows`: `start()` throws the
 *  NotSupportedError a real one throws on an inactive stream; `stopThrows`: `stop()` throws;
 *  `stopHangs`: `stop()` never completes (no event ever fires). `stop()` called from OUTSIDE
 *  mic.js is the recorder stopping by itself (its tracks ended). */
function mediaRecorder(o) {
  const opt = o || {};
  const r = { state: "inactive", mimeType: "audio/webm", ondataavailable: null, onstop: null, log: [] };
  r.start = () => {
    if (opt.startThrows) { const e = new Error("There was an error starting the MediaRecorder."); e.name = "NotSupportedError"; throw e; }
    r.state = "recording";
    r.log.push(["start", now()]);
  };
  r.stop = () => {
    r.log.push(["stop", now()]);
    if (opt.stopThrows) { const e = new Error("The MediaRecorder is in an invalid state."); e.name = "InvalidStateError"; throw e; }
    if (r.state === "inactive" || opt.stopHangs) return;
    r.state = "inactive";
    globalThis.setTimeout(() => {
      if (r.ondataavailable) r.ondataavailable({ data: new Blob([new Uint8Array(40000)], { type: "audio/webm" }) });
      if (r.onstop) r.onstop();
    }, 5);
  };
  return r;
}
const captureOf = (r) => () => Promise.resolve({ recorder: r, stream: { getTracks: () => [] } });
const micStats = (p) => (({ starts, stops, selfStops, posts, transcripts, holdValved }) => ({ starts, stops, selfStops, posts, transcripts, holdValved }))(p.mic.stats());

/* =========================================================================== *
 * B20. `MediaRecorder.start()` THROWS (probe A). The page says to type instead — and typing
 *      works: nothing was held, because the hold is taken only once the recorder runs. A line
 *      typed after the failure goes out at once and is heard; a hurt child's line gets the
 *      grown-up redirect within 1.3 s; a retry tap fails the same way and the line typed after
 *      it still goes out; `body[data-mic]` is never set.
 * =========================================================================== */
{
  const p = await bootPage({ answer: chatOnly() });
  const w = p.world, t0 = now();
  p.mic.setCapture(captureOf(mediaRecorder({ startThrows: true })));
  eq(p.mic.sttTarget().kind, "local", "B20: a chat-only deployment: the clip would go to the local sidecar (the MediaRecorder path)");
  p.mic.toggle();
  await advance(100);
  deep([p.mic.isRecording(), p.micStatus(), p.attrs["data-mic"], T().heldForEars],
       [false, "the microphone did not start — type a message and tap Ask instead", undefined, 0],
       "B20: start() threw: not recording, the status says to type instead, and NOTHING IS HELD — body[data-mic] not set (it read 'on', for good)");
  typed("can you hear me");
  await advance(10);
  deep([chats(w), chatStatus(), T().queued], [["can you hear me"], "thinking…", 0],
       "B20: A LINE TYPED AFTER THE FAILED START GOES OUT AT ONCE (it read 'Moxie will answer that next.' and was never POSTed: dead until reload)");
  await advance(2600);
  deep([sounds(w, t0), w.spy.transcript], [[["cloud", 2600]], ["can you hear me", "Reply to can you hear me"]],
       "B20: …and its reply is shown and heard in her voice at 2.6 s (2.5 s after the line)");
  await advance(3000);                                 // her 2 s sentence is over
  const tH = now();
  typed(HURT);
  await advance(1300);
  deep([chats(w).length, w.spy.transcript.includes(REDIRECT), redirectSpokenAt(w, tH), T().blocked], [2, true, [1200], 1],
       "B20: THE GROWN-UP REDIRECT TO A HURT CHILD IS SHOWN AND SPOKEN within 1.3 s of the line (never, before: still waiting eleven minutes on)");
  await advance(9000);                                 // the redirect is spoken out (8.3 s)
  p.mic.toggle();                                      // the visitor tries Listen again: same hardware
  await advance(100);
  typed("hello?");
  await advance(10);
  deep([chats(w).length, p.micStatus(), p.attrs["data-mic"], micStats(p).starts, T().queued],
       [3, "the microphone did not start — type a message and tap Ask instead", undefined, 0, 0],
       "B20: a retry tap fails the same way and the line typed after it still goes out; no recording was ever counted as started");
  foldTransportFails();
}

/* =========================================================================== *
 * B21. THE RECORDER STOPS BY ITSELF (probe B: its tracks ended — a headset unplugged, the OS
 *      took the microphone) and fires its own `stop`; mic.js never ran `stop()`. It is stopped
 *      all the same: the clip is uploaded, the ears are done when its transcript lands, the
 *      spoken line goes to the brain at once, a hurt line typed after it gets the redirect,
 *      `body[data-mic]` is cleared, and the next Listen tap opens a NEW recording.
 * =========================================================================== */
{
  const p = await bootPage({ answer: chatOnly() });
  const w = p.world, t0 = now();
  const r = mediaRecorder();
  p.mic.setCapture(captureOf(r));
  p.mic.toggle();
  await advance(2000);
  deep([p.mic.isRecording(), p.attrs["data-mic"], T().heldForEars], [true, "on", 0], "B21: recording for 2 s, the ears working");
  r.stop();                                            // the tracks ended: the recorder stops itself
  await advance(50);                                   // …and fired `dataavailable` and `stop` 5 ms later
  deep([p.mic.isRecording(), micStats(p), p.attrs["data-mic"]],
       [false, { starts: 1, stops: 1, selfStops: 1, posts: 1, transcripts: 0, holdValved: 0 }, "on"],
       "B21: A RECORDER THAT STOPPED BY ITSELF IS STOPPED: mic.js is no longer recording (it still was), the stop is counted as its own kind, the clip is uploading, the ears still working");
  await advance(800);                                  // the sidecar answered at +0.8 s
  deep([chats(w), p.attrs["data-mic"], micStats(p).transcripts, p.micStatus()], [["spoken words"], undefined, 1, 'heard: "spoken words"'],
       "B21: THE SPOKEN LINE GOES TO THE BRAIN THE MOMENT ITS TRANSCRIPT LANDS, and the ears are idle (it waited half an hour, until the next tap)");
  await advance(5000);                                 // her reply (4.3 s) and its 2 s sentence (5.3–7.3 s) are over
  const tH = now();
  typed(HURT);
  await advance(1300);
  deep([chats(w), w.spy.transcript.includes(REDIRECT), redirectSpokenAt(w, tH)], [["spoken words", HURT], true, [1200]],
       "B21: a hurt line typed after it gets the grown-up redirect, shown and spoken within 1.3 s (never shown in half an hour, before)");
  await advance(9000);                                 // the redirect is spoken out
  const r2 = mediaRecorder();
  p.mic.setCapture(captureOf(r2));
  p.mic.toggle();
  await advance(10);
  deep([p.mic.isRecording(), micStats(p).starts, micStats(p).stops, r2.log.length], [true, 2, 1, 1],
       "B21: the next Listen tap opens a NEW recording (mic.js took it for a stop of the old one, and sent the waiting lines only then)");
  foldTransportFails();
}

/* =========================================================================== *
 * B22. A RECORDER THAT CANNOT STOP (`stop()` throws): the ears end at once — there is no clip
 *      to wait for — and the line that waited goes out. (Pinned: the review's mutant N9
 *      removed this and survived.)
 * =========================================================================== */
{
  const p = await bootPage({ answer: chatOnly() });
  const w = p.world;
  p.mic.setCapture(captureOf(mediaRecorder({ stopThrows: true })));
  p.mic.toggle();
  await advance(500);
  typed("typed while recording");
  await advance(10);
  deep([chats(w), T().queued, chatStatus()], [[], 1, "Moxie will answer that next."], "B22: a line typed into an open microphone waits for the ears");
  p.mic.toggle();                                      // stop(): the recorder throws
  await advance(10);
  deep([p.mic.isRecording(), p.attrs["data-mic"], chats(w), micStats(p).posts, micStats(p).stops],
       [false, undefined, ["typed while recording"], 0, 1],
       "B22: A RECORDER THAT CANNOT STOP ENDS THE EARS AT ONCE: nothing to upload, body[data-mic] cleared, the waiting line goes out");
  foldTransportFails();
}

/* =========================================================================== *
 * B23. THE HOLD ITSELF IS BOUNDED. A recorder whose `stop()` never completes (no event ever
 *      fires) would hold the ears for good: 45 s after the microphone opened — the record cap
 *      (15 s) plus the 30 s upload valve — mic.js declares them idle itself, whatever the
 *      recorder did. The bound follows the SERVED cap (a 60 s cap: 90 s), so a legitimate long
 *      recording is never released early — the transport honours the bound mic.js names rather
 *      than its own 45 s default.
 * =========================================================================== */
{
  // B23a. The stop never completes.
  const p = await bootPage({ answer: chatOnly() });
  const w = p.world, t0 = now();
  p.mic.setCapture(captureOf(mediaRecorder({ stopHangs: true })));
  p.mic.toggle();                                      // the microphone opens at t0
  await advance(500);
  typed("typed while recording");
  await advance(10);
  p.mic.toggle();                                      // stop(): the recorder never finishes
  await advance(10);
  deep([p.mic.isRecording(), p.attrs["data-mic"], chats(w), micStats(p).stops], [false, "on", [], 1],
       "B23a: mic.js stopped, the recorder never finished: the ears are still working (nothing has released them)");
  await advance(44_000);                               // t0 + 44.52 s
  deep([chats(w), p.attrs["data-mic"]], [[], "on"], "B23a: …44.5 s after the microphone opened, still");
  await advance(600);                                  // t0 + 45.12 s
  deep([chats(w), p.attrs["data-mic"], micStats(p).holdValved, T().earsValved], [["typed while recording"], undefined, 1, 0],
       "B23a: THE HOLD IS BOUNDED: 45 s after the microphone opened (the 15 s cap plus the 30 s valve), whatever the recorder did, mic.js declares the ears idle — body[data-mic] cleared, the line goes out (it waited for good); the transport's own valve did not have to fire");
  eq(now() - t0, 45_120, "B23a: (clock check) 45.12 s since the microphone opened");
  foldTransportFails();
}
{
  // B23b. A served 60 s cap: the child records to the cap. At 46 s the ears are still (rightly)
  //       working — a 45 s bound would have let her reply start into the open microphone — and
  //       the clip's own end releases them at 60.8 s, before the 90 s bound.
  const p = await bootPage({ answer: chatOnly({ limits: { max_input_chars: 500, max_record_ms: 60000 } }) });
  const w = p.world, t0 = now();
  const r = mediaRecorder();
  p.mic.setCapture(captureOf(r));
  p.mic.toggle();
  await advance(1000);
  typed("typed during a long recording");
  await advance(45_000);                               // t0 + 46 s
  deep([p.mic.isRecording(), p.attrs["data-mic"], chats(w), T().earsValved, micStats(p).holdValved, p.mic.maxRecordMs()],
       [true, "on", [], 0, 0, 60000],
       "B23b: THE BOUND FOLLOWS THE SERVED CAP: 46 s into a legitimate 60 s recording the ears are still working — neither mic.js's valve nor the transport's own 45 s default released them (her reply would have started into the open microphone)");
  await advance(14_900);                               // t0 + 60.9 s: the cap stopped the recording at 60 s, the sidecar answered at 60.805 s
  deep([p.mic.isRecording(), micStats(p), p.attrs["data-mic"], chats(w), T().queued],
       [false, { starts: 1, stops: 1, selfStops: 0, posts: 1, transcripts: 1, holdValved: 0 }, undefined, ["typed during a long recording"], 2],
       "B23b: the cap stopped the recording at 60 s and the clip's transcript released the ears at 60.8 s: the typed line goes out first, the spoken line waits its turn, and the 90 s bound never had to fire");
  foldTransportFails();
}
