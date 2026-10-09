/* Part C §B17–B19: WHAT THE HOLD COVERS, AND WHAT BOUNDS IT. The Listen tap stops her and
 * holds nothing; the hold on everything of hers starts once the microphone is OPEN and ends
 * when the ears are done with the clip — and every leg of it is bounded. On the real
 * `mic.js`, `cloud-transport.js`, `mode.js`, `bridge/` and `voice/` (harness Part C), on a
 * virtual clock.
 *
 * Before (the W3-S16 review's probes P1–P6, on c56e3f1): the hold was taken AT THE TAP, before
 * the capture opened, and only the capture's own settle released it. A permission prompt left
 * unanswered (`getUserMedia` need never settle) held a typed line for good — nothing sent in
 * five minutes, the status reading "Moxie will answer that next." — swallowed a reply already
 * in flight, and never showed or spoke the grown-up redirect to "i fell off my bike and my arm
 * is bleeding"; `body[data-mic]` stayed on (ambient silenced) and a second tap did nothing.
 * origin/dev answered in every case. Also measured there (P3): a typed line nothing live could
 * take went to bridge/'s own echo + stub, whose 450 ms beat started the stub's clip into a
 * microphone opened 100 ms after the line.
 */
import { bootPage, deep, eq, fails, ok, page } from "./harness.mjs";

const { advance, envelope, now, said, voicedChunk } = page;
const T = () => globalThis.window.moxieBridge.transportStats();
/** A hosted page: health says `ears: true`, so the mic uploads to the same-origin route. */
const hosted = (other) => (path, body, spy) =>
  path === "/api/health" ? { status: 200, json: envelope({ ears: true }) } : other(path, body, spy);
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
const chatStatus = () => globalThis.document.getElementById("chat-status").textContent;
const sounds = (w, t0) => w.spy.sounds.map((s) => [s.kind, s.t - t0]);
/** The transport harness's own ledger (a timer that threw) folds into this suite's. */
const foldTransportFails = () => { for (const f of page.fails.splice(0)) fails.push("transport harness: " + f); };
/** A capture whose promise never settles: the browser's permission prompt, left unanswered. */
const NEVER = () => new Promise(() => {});
/** The rule table's redirect for a hurt child, as `/api/chat` answers it: reason `blocked`,
 *  the line in `messages`, no voice ticket (it speaks from the local voice). */
const HURT = "i fell off my bike and my arm is bleeding";
const REDIRECT = "Thank you for telling me. Feelings this big need a grown-up who loves you, not a robot. Will you talk to one right now?";
const redirect = (over) => Object.assign(said(REDIRECT, "sim-blk", { degraded: true, reason: "blocked" }), over || {});
const redirectSpokenAt = (w, t0) => w.spy.said.filter((s) => s.text === REDIRECT).map((s) => s.t - t0);
/** A voiced reply to anything: the chat lands after 1.5 s, its one sentence 1 s later. */
const voicedReply = (path, body) => {
  if (path === "/api/chat") {
    const eid = "sim-" + String(body.text).replace(/[^a-z]/g, "").slice(0, 8);
    return Object.assign(said("Reply to " + body.text, eid, { speech: [{ ticket: `v1.${eid}.T0.M`, event_id: eid, chunk_num: 0 }] }), { delayMs: 1500 });
  }
  if (path === "/api/speech") return voicedChunk(/^v1\.(sim-[a-z]+)\./.exec(body.ticket)[1], 0, { delayMs: 1000, seconds: 2 });
  if (path === "/api/transcribe") return { status: 200, json: { transcript: "hello" }, delayMs: 2500 };
  return { status: 404, text: "" };
};
const paused = (retry) => ({ status: 429, json: envelope({ ok: false, degraded: true, reason: "rate_limited", retry_after_s: retry, mode: "live" }) });

/* =========================================================================== *
 * B17. THE PERMISSION PROMPT IS LEFT UNANSWERED (the capture never settles). The tap holds
 *      nothing: a line typed after it goes out and is answered in her voice; a reply already
 *      on its way is shown and heard; the grown-up redirect reaches a hurt child either way;
 *      body[data-mic] is never set; a second tap says what to do.
 * =========================================================================== */
{
  // B17a. A line typed 500 ms after the tap (P1).
  const p = await bootPage({ answer: hosted(voicedReply) });
  const w = p.world, t0 = now();
  p.mic.setCapture(NEVER);
  p.mic.toggle();
  await advance(500);
  globalThis.window.moxieTypedTurn.send("typed instead");
  await advance(10);
  deep([chats(w), chatStatus(), T().queued, p.attrs["data-mic"]], [["typed instead"], "thinking…", 0, undefined],
       "B17a: A LINE TYPED WHILE THE BROWSER ASKS FOR THE MICROPHONE GOES OUT AT ONCE (it waited for good: nothing sent in five minutes), nothing queued, body[data-mic] not set");
  await advance(3000);
  deep([sounds(w, t0), w.spy.transcript], [[["cloud", 3000]], ["typed instead", "Reply to typed instead"]],
       "B17a: …and its reply is shown and heard in her voice at 3.0 s (never, before)");
  deep([T().heldForEars, T().interrupted], [0, 0], "B17a: nothing was held for the ears, and nothing interrupted (she was silent at the tap)");
  p.mic.toggle();                                      // a second tap while the prompt sits there
  await advance(10);
  deep([p.mic.isRecording(), p.micStatus()], [false, "waiting for the microphone — allow it in the browser, or type a message and tap Ask"],
       "B17a: a second tap opens no second capture and says what to do (it did nothing, silently)");
  await advance(300_000);
  deep([chats(w).length, p.attrs["data-mic"], T().queued], [1, undefined, 0],
       "B17a: five minutes on, nothing is held and the ears were never marked as working");
  foldTransportFails();
}
{
  // B17b. A reply already on its way when Listen is tapped (P2).
  const p = await bootPage({ answer: hosted(voicedReply) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send("first question");
  await advance(300);
  p.mic.setCapture(NEVER);
  p.mic.toggle();
  await advance(2300);
  deep([sounds(w, t0), w.spy.transcript.includes("Reply to first question"), w.spy.cuts.length, T().heldForEars, p.attrs["data-mic"]],
       [[["cloud", 2500]], true, 0, 0, undefined],
       "B17b: A REPLY ALREADY IN FLIGHT AT THE TAP IS STILL SHOWN AND HEARD (2.5 s; never, before), nothing cut, nothing held");
  foldTransportFails();
}
for (const [label, typedAt] of [["typed after the tap", 1000], ["typed before the tap", -300]]) {
  // B17c. The hurt line typed after the tap (P5) and before it (P6): the redirect is shown
  //       and spoken both times.
  const p = await bootPage({ answer: hosted((path) => (path === "/api/chat" ? redirect({ delayMs: 1200 }) : { status: 404, text: "" })) });
  const w = p.world, t0 = now();
  if (typedAt < 0) { globalThis.window.moxieTypedTurn.send(HURT); await advance(-typedAt); }
  p.mic.setCapture(NEVER);
  p.mic.toggle();
  if (typedAt > 0) { await advance(typedAt); globalThis.window.moxieTypedTurn.send(HURT); }
  await advance(1300);
  deep([chats(w), w.spy.transcript.includes(REDIRECT), redirectSpokenAt(w, t0).length, T().blocked, p.attrs["data-mic"]], [[HURT], true, 1, 1, undefined],
       `B17c (${label}): THE GROWN-UP REDIRECT IS SHOWN AND SPOKEN within 1.3 s of the line (never, before: the line was held, or its reply was)`);
  foldTransportFails();
}
{
  // B17d. The prompt is answered 3 s after the tap (the review's probe C): a reply that began
  //       while the browser asked plays — the tap holds nothing — and is cut the instant the
  //       capture opens, so the recording never holds her voice. Its words stay in the log.
  //       (The cut part is not re-spoken: an honest gap, stated in the PR.) Pinned: the
  //       review's mutant N4 removed this second stop and survived.
  const p = await bootPage({ answer: hosted((path) => (path === "/api/chat" ? redirect({ delayMs: 1200 }) : { status: 404, text: "" })) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(300);
  p.mic.setCapture(() => new Promise((res) => globalThis.setTimeout(() => res({ recorder: p.rec, stream: { getTracks: () => [] } }), 3000)));
  p.mic.toggle();                                      // the browser asks; the visitor answers at 3.3 s
  await advance(1000);                                 // t+1.3 s: the redirect landed at 1.2 s
  deep([redirectSpokenAt(w, t0), w.spy.cuts.length, T().interrupted, T().heldForEars, p.attrs["data-mic"]], [[1200], 0, 0, 0, undefined],
       "B17d: a reply landing while the browser asks is spoken (the tap holds nothing; she was silent at the tap, so nothing was interrupted)");
  await advance(2050);                                 // t+3.35 s: the capture opened at 3.3 s
  deep([p.mic.isRecording(), w.spy.cuts.map((c) => [c.text === REDIRECT, c.t - t0]), globalThis.window.moxieAudio.isMoxieSpeaking(), p.attrs["data-mic"]],
       [true, [[true, 3300]], false, "on"],
       "B17d: THE CAPTURE OPENING CUTS HER AT ONCE (3.3 s, 2.1 s into the redirect): the recording never holds her voice, and the ears are working");
  ok(w.spy.transcript.includes(REDIRECT), "B17d: the redirect's words stay in the log");
  foldTransportFails();
}

/* =========================================================================== *
 * B18. WHAT THE HOLD COVERS while the microphone IS open: a `blocked` redirect landing
 *      mid-recording, a refused turn's stub answer, and the stub answer to a line nothing
 *      live could take — each waits for the ears, then is shown and spoken.
 * =========================================================================== */
{
  // B18a. The redirect lands while the child is speaking: held, then spoken.
  const p = await bootPage({ answer: hosted((path, body) => {
    if (path === "/api/chat") return body.text === HURT ? redirect({ delayMs: 1200 }) : Object.assign(said("I am listening.", "sim-lst"), { delayMs: 100 });
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "it really hurts" }, delayMs: 2500 };
    return { status: 404, text: "" };
  }) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send(HURT);
  await advance(300);
  await p.speak();                                     // tap at 0.3 s; the auto-stop sends the clip at 1.75 s
  await advance(500);                                  // t+2.3 s: the redirect landed at 1.2 s into an open microphone
  deep([T().heldForEars, w.spy.said.length, w.spy.transcript.includes(REDIRECT), p.attrs["data-mic"]], [1, 0, false, "on"],
       "B18a: a redirect landing while the child speaks is HELD — not shown, not spoken — while the ears work");
  await advance(2100);                                 // the clip's transcript landed at 4.25 s: the ears are done
  deep([redirectSpokenAt(w, t0), w.spy.transcript.includes(REDIRECT), chats(w)], [[4250], true, [HURT, "it really hurts"]],
       "B18a: …and is shown and spoken the moment they are (4.25 s), before the spoken line goes to the brain");
  foldTransportFails();
}
{
  // B18b. A refused turn's stub answer (a 429 landing mid-recording): held, then spoken.
  const p = await bootPage({ answer: hosted((path) => {
    if (path === "/api/chat") return Object.assign(paused(20), { delayMs: 1500 });
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "hello" }, delayMs: 2500 };
    return { status: 404, text: "" };
  }) });
  const w = p.world, t0 = now();
  globalThis.window.moxieTypedTurn.send("tell me a joke");
  await advance(300);
  await p.speak();                                     // tap at 0.3 s; the 429 lands at 1.5 s; its stub line is due at 1.95 s
  await advance(500);                                  // t+2.3 s
  deep([T().chatRefused, T().heldForEars, w.spy.sounds.length, w.spy.transcript], [1, 1, 0, ["tell me a joke"]],
       "B18b: THE STUB ANSWER TO A REFUSED TURN IS HELD while the ears work: nothing sounded, only the child's line in the log");
  await advance(2100);                                 // the ears are done at 4.25 s
  deep([sounds(w, t0)[0], w.spy.transcript.length >= 3], [["clip", 4250], true],
       "B18b: …then the stub line is shown and its clip plays (4.25 s), once the ears are done");
  foldTransportFails();
}
{
  // B18c. A typed line nothing live can take (chat paused by a 429 moments ago) and Listen
  //       tapped 100 ms later (P3): its stub answer waits for the ears instead of starting
  //       into the recording from bridge/'s own 450 ms beat.
  let first = true;
  const p = await bootPage({ answer: hosted((path) => {
    if (path === "/api/chat") {
      if (first) { first = false; return paused(30); }
      return { status: 200, json: envelope({ messages: [], speech: [] }) };
    }
    if (path === "/api/transcribe") return { status: 200, json: { transcript: "hello" }, delayMs: 1000 };
    return { status: 404, text: "" };
  }) });
  const w = p.world;
  globalThis.window.moxieTypedTurn.send("tell me a joke");      // refused at once: its stub answer plays at 450 ms
  await advance(5000);
  const before = w.spy.sounds.length, t1 = now();
  globalThis.window.moxieTypedTurn.send("another joke");         // chat is paused: nothing live can take it
  await advance(100);
  await p.speak();                                     // tap at +100 ms; the stub line is due at +450 ms; the clip is sent at +1.55 s
  await advance(100);                                  // t1+1.7 s
  deep([T().delegated, T().heldForEars, w.spy.sounds.slice(before).map((s) => [s.kind, s.t - t1])], [0, 1, []],
       "B18c: A STUB ANSWER TO A LINE NOTHING LIVE COULD TAKE WAITS FOR THE EARS (it started into the recording at 450 ms from bridge/'s own beat, delegated 1)");
  await advance(1000);                                 // the ears are done at +2.55 s
  deep(w.spy.sounds.slice(before).map((s) => [s.kind, s.t - t1])[0], ["clip", 2550],
       "B18c: …and plays once they are (2.55 s)");
  foldTransportFails();
}

/* =========================================================================== *
 * B19. WHAT BOUNDS THE HOLD: an upload that never answers releases the ears after 30 s
 *      (mic.js's valve); a recording opened while an earlier clip is still uploading keeps
 *      the ears working until it ends, and only then.
 * =========================================================================== */
{
  // B19a. The upload never answers.
  const p = await bootPage({ answer: hosted((path) => (path === "/api/transcribe"
    ? { status: 200, json: { transcript: "hello" }, delayMs: 600_000 }
    : path === "/api/chat" ? Object.assign(said("Hi.", "sim-hi"), { delayMs: 100 }) : { status: 404, text: "" })) });
  const w = p.world;
  await p.speak();                                     // the clip is sent at 1.45 s; nothing ever answers it
  globalThis.window.moxieTypedTurn.send("typed during a hung upload");
  await advance(10);
  deep([chats(w), chatStatus(), T().queued, p.attrs["data-mic"]], [[], "Moxie will answer that next.", 1, "on"],
       "B19a: a line typed during the upload waits: the ears are working");
  await advance(29_000);                               // 29 s into the upload
  deep([chats(w), p.attrs["data-mic"]], [[], "on"], "B19a: …still, 29 s in");
  await advance(1100);                                 // 30.1 s into the upload
  deep([chats(w), p.attrs["data-mic"], p.mic.stats().posts], [["typed during a hung upload"], undefined, 1],
       "B19a: AN UPLOAD THAT NEVER ANSWERS RELEASES THE EARS AT 30 s: the line goes out and body[data-mic] is cleared (the one upload still pending)");
  foldTransportFails();
}
{
  // B19b. `mic.start()` while an earlier clip is still uploading (the button refuses such a
  //       tap; a page script need not): the ears stay working until THIS recording's clip is
  //       done, not until the earlier one's.
  const p = await bootPage({ answer: hosted((path) => (path === "/api/transcribe"
    ? { status: 200, json: { transcript: "hello" }, delayMs: 2500 }
    : path === "/api/chat" ? Object.assign(said("Hi.", "sim-hi"), { delayMs: 100 }) : { status: 404, text: "" })) });
  const w = p.world;
  await p.speak();                                     // clip 1 sent at 1.45 s, its transcript due at 3.95 s
  await advance(500);                                  // t+2.0 s
  p.mic.start();                                       // a second recording opens during the upload
  await advance(50);
  eq(p.mic.isRecording(), true, "B19b: a second recording is open while the first clip uploads");
  globalThis.window.moxieTypedTurn.send("typed during two clips");
  for (let i = 0; i < 6; i++) { p.level(0.09); await advance(400); }   // the child talks until 4.45 s
  deep([chats(w), p.attrs["data-mic"], T().queued], [[], "on", 2],
       "B19b: THE FIRST CLIP'S TRANSCRIPT (3.95 s) DID NOT DECLARE THE EARS IDLE: the typed line and that transcript both wait, body[data-mic] stays on");
  p.level(0.001);                                      // the auto-stop sends clip 2 at 5.55 s; its transcript lands at 8.05 s
  await advance(3700);                                 // t+8.15 s
  deep([chats(w)[0], p.attrs["data-mic"], p.mic.stats().posts], ["typed during two clips", undefined, 2],
       "B19b: …until this recording's clip is done (8.05 s): then the ears are idle and the waiting lines go, in order");
  foldTransportFails();
}
{
  // B19c. …and not until then even once the second recording has ENDED and its clip is still
  //       uploading when the first clip's transcript lands: each recording has its own number,
  //       so the earlier clip cannot declare the ears idle (a guard on "is a recording open"
  //       would have, with the second clip still in the air).
  let uploads = 0;
  const p = await bootPage({ answer: hosted((path) => (path === "/api/transcribe"
    ? { status: 200, json: { transcript: "hello" }, delayMs: ++uploads === 1 ? 6000 : 6000 }
    : path === "/api/chat" ? Object.assign(said("Hi.", "sim-hi"), { delayMs: 100 }) : { status: 404, text: "" })) });
  const w = p.world;
  await p.speak();                                     // clip 1 sent at 1.45 s, its transcript due at 7.45 s
  await advance(500);                                  // t+2.0 s
  p.mic.start();                                       // a second recording opens during the upload…
  await advance(50);
  globalThis.window.moxieTypedTurn.send("typed during two uploads");
  for (let i = 0; i < 2; i++) { p.level(0.09); await advance(400); }
  p.level(0.001);                                      // …and the auto-stop sends clip 2 at 3.95 s; its transcript is due at 9.95 s
  await advance(5150);                                 // t+8.0 s: clip 1's transcript landed at 7.45 s, clip 2 is still uploading
  deep([p.mic.isRecording(), p.mic.stats().posts, chats(w), p.attrs["data-mic"], T().queued], [false, 2, [], "on", 2],
       "B19c: THE FIRST CLIP'S TRANSCRIPT (7.45 s) DID NOT DECLARE THE EARS IDLE while the second clip is still uploading: the typed line and that transcript both wait, body[data-mic] stays on");
  await advance(2050);                                 // t+10.05 s
  deep([chats(w)[0], p.attrs["data-mic"], T().queued], ["typed during two uploads", undefined, 3],
       "B19c: …until this recording's clip is done (9.95 s): then the ears are idle and the waiting lines go, in order");
  foldTransportFails();
}
