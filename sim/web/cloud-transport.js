/* cloud-transport.js — the live HTTP turn: one typed sentence in, Moxie's own voice out.
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2-3.5, §4.5, §6.
 *
 * When `window.moxieMode` says there is a live brain, a child's turn goes to same-origin
 * `POST /api/chat` + `/api/speech` instead of `stub.js`, and the payloads are handed to the
 * SAME `route()` a live MQTT bus and a recorded session drive.
 *
 * A WRAPPER (§3.5): bridge/ and voice/ are not modified; every `window.moxieBridge` member
 * passes through except `sendUserTurn` and `isLive` (plus additive members). A CONNECTED
 * MQTT BROKER ALWAYS WINS: a self-hoster's supervisor gets the turn.
 *
 * ONE VOICE (§3.4): with no MQTT client, `bridge/index.js::speakLocally` speaks a line at
 * once unless its EVENT expects a server voice. So a turn holding speech tickets:
 *   1. POST /api/chat  -> the chat message plus the speech tickets, one per sentence;
 *   2. tell the bridge to expect that event's voice, and POST /api/speech for chunk 0
 *      immediately (each later chunk as its predecessor lands: `SPEECH_PARALLEL`);
 *   3. route the chat message when EITHER chunk 0 lands (TTS routed FIRST) or
 *      `SPEECH_WAIT_MS` elapses — still expecting the voice, which plays when it lands;
 *   4. if chunk 0 fails (refused, unreachable, or no answer by `SPEECH_FETCH_MS`), release
 *      the expectation: the words speak locally, ONCE, and a voice turning up later is dropped;
 *   5. the later chunks are routed in order behind chunk 0 as they land; the first that
 *      fails ends the voice, and nothing local ever stands in for a later chunk;
 *   6. a NEWER reply's voice starting (its chunk 0 routed, or a line spoken locally) ends an
 *      older reply's pipeline: voice/ gives the speakers to the newest reply, so the rest of
 *      the older one is not paid for, and never heard after the newer one.
 * A voice failure is not a brain failure: speech-route reasons are recorded in the stats and
 * never reported to the mode machine.
 *
 * ONE LINE AT A TIME FROM THE CONTROLS (`queueUserTurn`): a line from the Ask box, an opener
 * or the mic's transcript WAITS while a turn's reply is still on its way, then goes out
 * carrying that reply's context, and its own reply waits behind that reply's voice, so both
 * replies are heard whole, in order, and both exchanges reach the next turn — unless it is
 * the route's safety line, which is said next, after the sentence now playing (W4-S7). Rule 6
 * stays as the backstop for `sendUserTurn` itself, which still sends at once. THE EARS COME FIRST
 * (`interruptVoice`, `earsOpen` / `earsIdle`, driven by mic.js): the Listen tap is a
 * deliberate interruption — every open pipeline ends through rule 6's path and voice/ is
 * stopped — and from the microphone OPENING until the ears are done with the clip nothing of
 * hers (a reply landing, a stub line, a queued line) starts; what the tap itself RELEASES (a
 * reply held behind the one it ended, a safety line waiting for her sentence) first waits for
 * that microphone to open, `TAP_HOLD_MAX_MS` at most (W4-S7). A turn in flight is settled by
 * `TURN_MAX_MS` at the latest, and the ears' hold ends at its bound (the record cap plus
 * mic.js's 30 s upload valve, told to `earsOpen`; `EARS_HOLD_MAX_MS` when nothing is told)
 * even if nothing ever closes it, so the queue can never be held for good.
 *
 * No secret and no hostname here: the base is `moxieMode.apiBase()` (= location.origin).
 * `ticket` and `context` are opaque, signed server-side, and die with the tab (§2.6).
 */
(function () {
  "use strict";

  // §3.4's client-side ceiling on how long the words wait for the voice.
  var SPEECH_WAIT_MS = 2500;
  // Client ceilings ABOVE the server's own (DEMO_CHAT_TIMEOUT_MS / DEMO_SPEECH_TIMEOUT_MS,
  // 10 s / 12 s by default), so its honest 504 `timeout` envelope wins the race and the page
  // learns WHY. SPEECH_FETCH_MS is also the deadline
  // after which a voice is given up for the local one (kept even without AbortSignal.timeout).
  var CHAT_FETCH_MS = 25000;
  var SPEECH_FETCH_MS = 15000;
  // The pause before a fallback reply, matching the bridge's own 450 ms beat.
  var FALLBACK_MS = 450;

  var inner = window.moxieBridge;
  // Additive: with no bridge this does nothing rather than half-wiring a page.
  if (!inner || typeof inner.sendUserTurn !== "function" || typeof inner.route !== "function") return;

  // `route()` dispatches on the topic SUFFIX; `d_sim` is the browser SIM's identity.
  var USER_TOPIC = "/devices/d_sim/events/remote-chat";

  // The signed conversation blob (§3.3): opaque, re-minted every turn, dies with the tab.
  var contextBlob = "";

  /* What this transport RECORDED; tests assert on this, never live timing (rule 11). */
  var stats = {
    turns: 0, live: 0, delegated: 0, fallbacks: 0,
    scripted: 0,             // consolation lines the PAGE chose (mic.js's degraded turn)
    scriptedFree: 0,         // ...of those, the ones a live page answered for FREE
    chatOk: 0, diagrams: 0, cited: 0, chatRefused: 0, chatErrors: 0,
    speechOk: 0, speechRefused: 0, speechErrors: 0,
    voiceFirst: 0,           // the TTS message was routed BEFORE the chat message
    chatFirst: 0,            // the 2.5 s wait elapsed: the words went out, their voice still expected
    lateSpeechPlayed: 0,     // …and that voice landed later and played
    voiceFallbacks: 0,       // the voice failed: the words were spoken locally, once
    lateSpeechDropped: 0,    // a chunk 0 that landed after its words were spoken locally, or after a newer reply's voice started
    tickets: 0,              // speech tickets received for live replies (one per sentence)
    chunksRouted: 0,         // later chunks (1+) handed to voice/ behind their predecessor (handed, not heard)
    chunkFailures: 0,        // a later chunk refused, unreachable or past the deadline: the voice ended there
    chunksDropped: 0,        // later-chunk audio that landed but was not played (after a failure, too late, or superseded)
    chunksSuperseded: 0,     // chunks of an older reply given up because a newer reply's voice started
    queued: 0,               // control lines that waited for the turn in flight (or the ears) before going out
    early: 0,                // control lines sent while an earlier reply was still being voiced (its words back)
    heldReplies: 0,          // …of their replies, those held behind an earlier reply's voice
    safetyFirst: 0,          // safety lines of an early line said next: every earlier reply ended after its playing sentence
    heldAtTap: 0,            // lines the Listen tap released (a held reply, a waiting safety line) that waited for its microphone to open
    tapValved: 0,            // …waits ended by TAP_HOLD_MAX_MS with no microphone open (the line then went on)
    heldForEars: 0,          // replies and page-composed lines that waited for the ears to finish a clip
    interrupted: 0,          // the Listen tap landed on a reply: its voice stopped and its pipeline ended on purpose
    earsValved: 0,           // holds the transport ended itself: `earsOpen` with no `earsIdle` by its bound
    turnsValved: 0,          // live turns settled by TURN_MAX_MS, their pipeline never having closed on its own
    blocked: 0,
    botTokens: 0,            // sends that carried a fresh Turnstile token
    botUnavailable: 0,       // sends REFUSED locally because no token could be minted
    reasons: [],             // every reason the server gave the BRAIN, in order (mode.js hears these)
    speechReasons: [],       // every reason the speech route gave (recorded only — see voiceFirst)
    order: [],               // "tts" / "chat" / "stub", in the order they were routed
  };

  function mode() {
    try { return window.moxieMode || null; } catch (e) { return null; }
  }

  function apiBase() {
    var m = mode();
    var base = m && m.apiBase ? m.apiBase() : null;
    return base || null;
  }

  /** Is a live turn spendable now? `mode.js` owns the answer (§6.3). */
  function canSpendLiveTurn() {
    var m = mode();
    return !!(m && m.canSpendLiveTurn && m.canSpendLiveTurn() && apiBase());
  }

  function status(text) {
    var el = document.getElementById("chat-status");
    if (el) el.textContent = text;
  }

  /* ---- reporting back to the mode machine (§4.5) -------------------------- */
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

  /* ---- one POST: resolves {ok, body}; `ok` = "a usable envelope with no reason", never
   * "the status was 2xx". Never rejects. */
  function post(path, payload, timeoutMs) {
    var base = apiBase();
    if (!base) return Promise.resolve({ ok: false, body: null });
    var opt = {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    };
    try {
      if (typeof AbortSignal !== "undefined" && AbortSignal.timeout)
        opt.signal = AbortSignal.timeout(timeoutMs);
    } catch (e) {}
    return fetch(base + path, opt).then(function (r) {
      return r.text().then(function (text) {
        var body = null;
        try { body = JSON.parse(text); } catch (e) { body = null; }
        if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, body: null };
        return { ok: !body.reason, body: body };
      });
    }).catch(function () {
      return { ok: false, body: null };
    });
  }

  /* ---- routing ------------------------------------------------------------ */
  function routeAll(messages, kind) {
    var list = Array.isArray(messages) ? messages : [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (!m || typeof m.topic !== "string" || typeof m.payload !== "string") continue;
      stats.order.push(kind);
      inner.route(m.topic, m.payload);
    }
  }

  /** Echo the child's turn locally (transcript row + `listen` SFX). NOT
   *  `inner.sendUserTurn`: that also fires the offline stub and Moxie would answer twice. */
  function echoUser(text) {
    inner.route(USER_TOPIC, JSON.stringify({ command: "prompt", backend: "router", speech: text }));
  }

  /** A reply composed on the page, through the same `route()` a real one takes. It speaks
   *  as soon as the ears are idle: the voice of turn `seq` (or of the newest, with none) is
   *  starting. Resolves once it is routed. */
  function localReply(text, markup, seq) {
    return whenEarsIdle().then(function () {
      supersedeVoices(seq);
      stats.order.push("stub");
      inner.route("/devices/d_sim/commands/remote_chat", JSON.stringify({
        command: "remote_chat", result: "OK", backend: "router",
        output: { text: text, markup: markup },
      }));
    });
  }

  /** The degraded answer for ONE turn: `stub.js` after the bridge's 450 ms beat. The turn
   *  is already echoed, so not via `inner.sendUserTurn`. */
  function fallbackReply(text, seq) {
    stats.fallbacks++;
    if (!window.moxieStub || !window.moxieStub.enabled) return Promise.resolve();
    var r = window.moxieStub.reply(text);
    return new Promise(function (resolve) {
      setTimeout(function () { localReply(r.text, r.markup, seq).then(resolve); }, FALLBACK_MS);
    });
  }

  /* ---- one line at a time from the controls, and the ears first (W3-S16) ------ *
   * Two lines 300 ms apart used to race: `chatPost` sends whatever `contextBlob` holds and
   * the last reply to land overwrites it, so the second line carried no context, the replies
   * showed in landing order, and the third turn's history lacked one exchange (measured in
   * Chrome on the shipped page, 2026-10-07). A line from a CONTROL now waits in `waiting`
   * until no live turn is in flight — POSTed and not yet SETTLED, i.e. its reply wholly handed
   * to the speakers (every chunk routed, or the voice given up) — so it carries that reply's
   * context and its own voice queues behind the earlier one in voice/ instead of ending it.
   * Queued rather than a disabled button: the child's line is taken the moment they tap, and
   * she answers it next; a dead Send would have them re-typing (and the HUD is not this file's
   * to change). `sendUserTurn` itself still sends at once (rule 6 above is its backstop; the
   * transport §4i-4k pins stay as they are).
   *
   * THE EARS COME FIRST. The Listen tap is the child saying "stop, listen to me": mic.js
   * calls `interruptVoice()`, every open pipeline ends through `supersedeVoices` (nothing
   * more of any reply is redeemed; a chunk in flight is dropped when it lands) and voice/ is
   * stopped (the playing clip and every queued chunk). The HOLD starts only once the
   * microphone is OPEN (`earsOpen()`, as the capture starts — she is stopped again then, so
   * the recording never holds her voice) and lasts until `earsIdle()` (the clip dropped, or
   * its upload settled): a reply that lands meanwhile, a stub line and the next queued line
   * all wait. Never from the tap: the browser may be asking for the microphone, and a prompt
   * left unanswered never settles — a hold taken at the tap kept a typed line, and a safety
   * redirect already on its way, from ever reaching the child (measured 2026-10-08). And
   * never for good: mic.js releases the hold on every way a recording can end and bounds
   * it itself, and `earsOpen` bounds it here too, in the state this file keeps, by the
   * number mic.js names (its record cap plus 30 s) or EARS_HOLD_MAX_MS — a caller that
   * never says `earsIdle` cannot hold her for ever. ambient.js reads the same fact from
   * body[data-mic].
   *
   * A SAFETY LINE NEVER WAITS BEHIND AN EARLIER REPLY (W4-S7). Holding the POST until the
   * earlier turn SETTLED cost a hurt child 3.7 s: a three-sentence reply playing from 4.1 s,
   * "i fell off my bike and my arm is bleeding" typed at 5.0 s, the grown-up redirect heard
   * at 9.9 s, against 6.2 s before the queue (the W3-S16 review, on the virtual clock). So a
   * waiting line goes out the moment every earlier reply's WORDS are back (`awaitingChat`):
   * its context is known then, and nothing is bought sooner than it would have been. Its
   * reply is then held (`heldBehind`) until every turn POSTed before it has settled — and,
   * when it would start a local voice, until her playing sentence ends — so the earlier
   * reply is still heard whole and never cut. UNLESS it is the route's own SAFETY LINE: the
   * words on a reason body (an input block's redirect; the output floor's swap and a hurt
   * child's referral on a refusal ride the same shape). Then every earlier reply ends
   * (`endEarlier`): nothing more of it redeemed, a chunk in flight dropped when it lands
   * (rule 6's path), a sentence already queued in voice/ dropped (`dropQueuedTTS`), its
   * words put in the log first if they are not yet, silently, and a reply still held never
   * voiced; the sentence now playing plays out, and the safety line is said next, its words
   * with its voice. A line sent with nothing in flight, and `sendUserTurn`, behave exactly
   * as before.
   *
   * WHAT THE LISTEN TAP RELEASES GOES INTO THE EARS, NOT AT THEM (W4-S7). The tap ends the
   * reply a held line waits behind and stops the sentence a safety line waits for: it
   * RELEASES them, their reply already in hand — where, with the POST held (#325), the line
   * went out at the tap and its reply landed a chat round trip later, into the open
   * microphone, and was heard after the recording. Released at once it started before the
   * microphone opened, and the recorder's own stop (`earsOpen`) cut it: the W4-S7 review
   * heard 0 ms of a held reply at each of 10 tap times, and 100 ms of a 7.7 s redirect at
   * each of 5 (20-300 ms with the microphone 120-400 ms slow to open). So such a line waits
   * for the microphone the tap asked for (`whenMicOpen`), then for the ears: heard whole
   * after the recording, nothing of it bought before. For TAP_HOLD_MAX_MS at most: a prompt
   * left unanswered never holds it (#325's rule), and a microphone opening later still cuts
   * it, as it cuts any reply that began while the browser asked (ears B17d). */
  var waiting = [];        // {text, resolve}: control lines waiting for the turn in flight
  var inflight = 0;        // live turns POSTed and not yet settled
  var awaitingChat = 0;    // …of those, the ones whose reply is not back yet: a waiting line waits for these
  var openTurns = [];      // the live turns in flight, in POST order (what an early line's reply is held behind)
  var earsBusy = false;    // mic.js: recording, or still transcribing the clip
  var earsWaiters = [];    // what is held for the ears: resolved, in order, by earsIdle()
  var earsValve = null;    // the hold's own bound here: mic.js's valve is the first line, this the second
  var tapHold = null;      // a Listen tap's bound while the microphone it asked for opens (W4-S7)
  var tapWaiters = [];     // …what the tap released, waiting for that microphone: resolved, in order, by earsOpen() or the bound

  function once(fn) {
    var done = false;
    return function () { if (done) return; done = true; fn(); };
  }

  /** Resolves when the ears are idle — at once, when they are. */
  function whenEarsIdle() {
    if (!earsBusy) return Promise.resolve();
    stats.heldForEars++;
    return new Promise(function (resolve) { earsWaiters.push(resolve); });
  }

  /** Resolves once the microphone a Listen tap asked for is open, or TAP_HOLD_MAX_MS has
   *  passed without it — at once with no tap pending. Only for what a tap RELEASES (an early
   *  line's reply, its safety line: `heldBehind`, `chatPost`); what follows is the ears'. */
  function whenMicOpen() {
    if (tapHold === null) return Promise.resolve();
    stats.heldAtTap++;
    return new Promise(function (resolve) { tapWaiters.push(resolve); });
  }

  /** The microphone the tap asked for is open (`earsOpen`), or will not be in time: what the
   *  tap released goes on, in order — into the ears' hold, when they opened. */
  function endTapHold() {
    if (tapHold !== null) { clearTimeout(tapHold); tapHold = null; }
    var rs = tapWaiters.splice(0);
    for (var i = 0; i < rs.length; i++) rs[i]();
  }

  /** The child interrupts her (Listen tapped): the playing clip and every queued chunk stop,
   *  every open pipeline ends through rule 6's path, and nothing more of any reply is paid
   *  for. Nothing new is held; what the tap releases waits for its microphone, for
   *  TAP_HOLD_MAX_MS at most (`whenMicOpen`, W4-S7). */
  function interruptVoice() {
    var a = window.moxieAudio, speaking = false;
    try { speaking = !!(a && a.isMoxieSpeaking && a.isMoxieSpeaking()); } catch (e) {}
    if (pipelines.length || speaking) stats.interrupted++;
    if (tapHold !== null) clearTimeout(tapHold);
    tapHold = setTimeout(function () {
      tapHold = null;
      if (tapWaiters.length) stats.tapValved++;
      endTapHold();
    }, TAP_HOLD_MAX_MS);
    stopVoice();
  }

  /** #317's path, then voice/: nothing more of any reply is paid for, routed or heard. */
  function stopVoice() {
    supersedeVoices(null);               // #317's path: nothing more of any reply is paid for
    var a = window.moxieAudio;
    try { if (a && a.stop) a.stop(); } catch (e) {}   // the playing clip and every queued chunk
  }

  /** The microphone is OPEN: she is stopped again (a reply may have begun while the browser
   *  asked; the tap already counted the interruption) and what follows is held until
   *  `earsIdle` — or for `holdMs` at most (mic.js names its record cap plus its 30 s upload
   *  valve; EARS_HOLD_MAX_MS when nothing is named), after which the ears are idle here
   *  whatever the caller did. What the tap released stops waiting for the microphone and
   *  waits for the ears like the rest. */
  function earsOpen(holdMs) {
    earsBusy = true;
    stopVoice();
    if (earsValve !== null) clearTimeout(earsValve);
    var ms = Number(holdMs);
    earsValve = setTimeout(function () { earsValve = null; stats.earsValved++; earsIdle(); }, ms > 0 ? ms : EARS_HOLD_MAX_MS);
    endTapHold();
  }

  /** The ears are done with the clip: what waited for them may go, in order. */
  function earsIdle() {
    if (earsValve !== null) { clearTimeout(earsValve); earsValve = null; }
    if (!earsBusy) return;
    earsBusy = false;
    var rs = earsWaiters.splice(0);
    for (var i = 0; i < rs.length; i++) rs[i]();
    drain();
  }

  /** The path a turn takes when nothing live can answer it: bridge/'s own (echo + stub). */
  function delegate(text) {
    stats.delegated++;
    inner.sendUserTurn(text);
    return Promise.resolve();
  }

  /** Send the next waiting line, if no reply is still on its way and the ears are idle (a
   *  turn whose words are back may still be voicing them: the line goes out EARLY, and its
   *  reply waits behind that voice, W4-S7). The mode is asked again NOW: the earlier reply
   *  may have been a refusal that paused live turns, and a line already in the log is then
   *  answered from `stub.js` (never echoed twice). With
   *  nothing live to answer it the line is echoed and answered from `stub.js` HERE, as
   *  bridge/'s own path would, so that the stub line waits for the ears like any voice of
   *  hers: bridge/'s own 450 ms beat cannot be held, and spoke into a microphone opened
   *  just after the line (a broker connected meanwhile still gets the turn itself). */
  function drain() {
    while (waiting.length && !awaitingChat && !earsBusy) {
      var w = waiting.shift(), p;
      if (inner.isLive()) p = delegate(w.text);               // a broker connected meanwhile
      else if (canSpendLiveTurn()) p = liveTurn(w.text, w.echoed, inflight > 0);
      else {
        if (!w.echoed) echoUser(w.text);
        else {
          var m = mode();
          status((m && m.message && m.message()) || "answering from her recorded lines.");
        }
        p = stubBehind(w.text);
      }
      p.then(w.resolve, w.resolve);
    }
  }

  /** A line from a control: one at a time, in order. A line that has to wait is echoed NOW
   *  (taken the moment they tapped) and sent when its turn comes. Resolves as
   *  `sendUserTurn`'s promise does, once THIS line's reply has started (mic.js holds its
   *  button on it). */
  function queueUserTurn(text) {
    var t = String(text == null ? "" : text).trim();
    if (!t) return Promise.resolve();
    stats.turns++;
    if (inner.isLive()) return delegate(t);      // a connected broker always wins, at once
    var waits = !!(awaitingChat || waiting.length || earsBusy);
    if (waits) { stats.queued++; echoUser(t); }
    return new Promise(function (resolve) {
      waiting.push({ text: t, resolve: resolve, echoed: waits });
      drain();
    });
  }

  /* ---- the hold an early line's reply takes, and the safety line's way past it (W4-S7) */
  /** How often a held line asks whether her sentence is over, and the longest it asks: a
   *  speaking predicate stuck true delays a line, never holds it. A sentence is bounded by
   *  the speech route's character cap; 20 s is past it. */
  var QUIET_POLL_MS = 100;
  var QUIET_MAX_MS = 20000;
  /** The longest what a Listen tap released waits for the microphone the tap asked for: past
   *  the 20 ms-1.5 s grants the W4-S7 review probed, and short enough that a microphone that
   *  never opens (a prompt left unanswered, a capture that failed: mic.js tells this file
   *  nothing then) delays a redirect by 2 s at most. */
  var TAP_HOLD_MAX_MS = 2000;

  /** Resolves once her SERVER voice is off the speakers — at once, when it is. The narrow
   *  predicate on purpose: what a held line must not cut is a chunk of a reply it went out
   *  behind (between two queued chunks it stays true); a local voice is cut by a newer line
   *  exactly as before. */
  function whenQuiet() {
    return new Promise(function (resolve) {
      var waited = 0;
      (function poll() {
        var a = window.moxieAudio, speaking = false;
        try { speaking = !!(a && a.isSpeaking && a.isSpeaking()); } catch (e) {}
        if (!speaking || waited >= QUIET_MAX_MS) return resolve();
        waited += QUIET_POLL_MS;
        setTimeout(poll, QUIET_POLL_MS);
      })();
    });
  }

  /** What an EARLY line's reply waits for before it starts: every turn POSTed BEFORE it to
   *  settle (its reply wholly handed to the speakers), then — for words that would start a
   *  local voice (`local`: no ticket, a stub line) — her playing sentence to end, so the
   *  earlier reply is heard whole and never cut, as when the POST itself waited. A ticketed
   *  reply needs no second wait: its chunk 0 queues behind the playing one in voice/. Last,
   *  the microphone a Listen tap asked for, when the tap is what released it (`whenMicOpen`).
   *  With no `turn` (a waiting line nothing live can take) the hold is on every open turn. A
   *  line that went out with nothing in flight waits for nothing. */
  function heldBehind(turn, local) {
    if (turn ? !turn.early : !inflight) return Promise.resolve();
    var earlier = [];
    for (var i = 0; i < openTurns.length && openTurns[i] !== turn; i++) earlier.push(openTurns[i].done);
    if (turn && earlier.length) stats.heldReplies++;
    return Promise.all(earlier).then(function () { return local ? whenQuiet() : undefined; }).then(whenMicOpen);
  }

  /** A waiting line nothing live can take, answered from `stub.js` behind every open turn:
   *  a stub line is a local voice, and would cut her. */
  function stubBehind(text) {
    return heldBehind(null, true).then(function () { return fallbackReply(text); });
  }

  /** The safety line of `turn` (turn `seq`) is said next: every reply of a turn POSTed before
   *  it ends. Its words go in the log now if they are not out yet — silently (its voice is
   *  expected), so the log reads in order and the bubble ends on the safety line — and
   *  nothing more of it is redeemed or heard after the sentence now playing: rule 6's path,
   *  and the sentences already queued in voice/ behind the playing one dropped. A reply
   *  still held behind it is never voiced. */
  function endEarlier(turn, seq) {
    stats.safetyFirst++;
    for (var i = 0; i < pipelines.length; i++) if (pipelines[i].seq < seq) pipelines[i].flushWords();
    for (var j = 0; j < openTurns.length && openTurns[j] !== turn; j++) {
      openTurns[j].silenced = true;
      if (openTurns[j].mute) openTurns[j].mute();
    }
    supersedeVoices(seq);
    var a = window.moxieAudio;
    try { if (a && a.dropQueuedTTS) a.dropQueuedTTS(); } catch (e) {}
  }

  /* ---- §3.4: one voice per reply ----------------------------------------- */
  /** The event a reply's words belong to — the key the bridge holds them under. */
  function eventOf(messages, speech) {
    for (var i = 0; i < (messages || []).length; i++) {
      try {
        var id = JSON.parse(messages[i].payload).event_id;
        if (id) return String(id);
      } catch (e) {}
    }
    return speech && speech[0] && speech[0].event_id ? String(speech[0].event_id) : "";
  }

  function expectVoice(eid) {
    try { if (eid && inner.expectCloudVoice) inner.expectCloudVoice(eid); } catch (e) {}
  }

  /* An EARLY reply's stand-in waits for her playing sentence (W4-S7). Its voice is bought the
   * moment the reply before it is handed over, before that reply's last sentence has played,
   * so a refused voice said its words locally at once, over that sentence: a 429 after
   * 1.5 s cut it at 10.2 s in the W4-S7 review's probe, where with the POST held (#325) the
   * stand-in came at 11.4 s, uncut. Like any local voice of an early reply (`heldBehind`) it
   * waits for her server voice instead — and is dropped, its words on screen as for any reply
   * a newer one ended, if a newer voice or the Listen tap has the speakers by then. */
  var quietFirst = {};     // event -> true while an early reply's voice is being decided
  var voiceStarts = 0;     // every voice started, or stopped, through `supersedeVoices`

  function releaseVoice(eid) {
    if (!eid) return;
    var say = function () { try { if (inner.releaseCloudVoice) inner.releaseCloudVoice(eid); } catch (e) {} };
    if (!quietFirst[eid]) return say();
    var mark = voiceStarts;
    whenQuiet().then(function () { if (voiceStarts === mark) say(); });
  }

  /* The replies whose voice is still being assembled: `voiceFirst` pipelines with a chunk
   * still to redeem or route, each stamped with its turn's `seq` (the order the turns were
   * POSTED, which is the conversation's order; replies land in any order). A reply's voice
   * STARTING — its chunk 0 routed, or a line spoken locally — ends every pipeline of an
   * EARLIER turn, and only those: a later turn's reply, still on its way, ends this one in
   * its turn. voice/ gives the speakers to the newest reply (a chunk of a new event closes
   * the old event, voice/cloud.js's EVENT RULE; a local line stops the queue), so an older
   * reply's remaining chunks would be paid for and then flushed, or played after the newer
   * reply, out of context: measured 2026-10-08 on the real voice/ (two typed turns 200 ms
   * apart, three chunks each), A1 was redeemed and then flushed as superseded when B0
   * started, and A2 was heard after the whole of B. An older reply's chunk already routed
   * BEFORE the newer voice plays out in order ahead of it. */
  var pipelines = [];
  var turnSeq = 0;

  /** The voice of turn `seq` is starting: every open pipeline of an earlier turn is over.
   *  A line with no turn of its own (the scripted consolation, the bot line) is the newest.
   *  Counted (`voiceStarts`): a stand-in waiting its turn is said only if none started since. */
  function supersedeVoices(seq) {
    var before = seq == null ? Infinity : seq;
    voiceStarts++;
    for (var i = pipelines.length - 1; i >= 0; i--) if (pipelines[i].seq < before) pipelines[i].supersede();
  }

  /** How many /api/speech redemptions of one reply are in flight at once: ONE. Chunk 1 is
   *  requested the moment chunk 0 lands and synthesises while chunk 0 plays (measured: a
   *  later chunk's round trip 1.7-2.9 s against 2.6-5.4 s of playback before it), and so on.
   *  Two at once was measured to slow chunk 0 (2026-10-08, two 10-turn arms): 2.4-3.7 s,
   *  median 3.3 s, on the 5 chunked turns of the two-in-flight arm, against 1.6-2.6 s,
   *  median 2.0 s, on that arm's single-chunk turns and 2.1-3.3 s, median 2.5 s, on the 6
   *  chunked turns of this arm — which delays the first words, the one thing chunking is for. */
  var SPEECH_PARALLEL = 1;
  /** More tickets than any reply is worth. A server that minted them is misconfigured, and
   *  nothing past this many is redeemed: each costs the visitor's speech window. */
  var MAX_TICKETS = 8;
  /** The longest the ears can hold a landed reply when mic.js names no bound of its own:
   *  its default record cap (15 s) plus its 30 s valve on an upload that never answers.
   *  `earsOpen` bounds the hold by it (or by the number mic.js passes: the served cap plus
   *  30 s), so it is a bound, not only a term in TURN_MAX_MS. */
  var EARS_HOLD_MAX_MS = 45000;
  /** The longest a turn can honestly be in flight: the chat deadline, the ears' hold, then
   *  up to MAX_TICKETS sentences each at the speech deadline, one at a time. Past it the
   *  turn is SETTLED regardless (`liveTurn`), so a pipeline that never closes — an exception
   *  on the reply path — cannot hold the queue for good; rule 6 is then the backstop, as it
   *  always is for `sendUserTurn`. */
  var TURN_MAX_MS = CHAT_FETCH_MS + EARS_HOLD_MAX_MS + MAX_TICKETS * SPEECH_FETCH_MS;   // 190 s

  /** The tickets of a reply in chunk order, chunk 0 first — or none, which is "no voice":
   *  the words then speak locally, as on a deployment without a TTS model. */
  function ticketsOf(speech) {
    var list = Array.isArray(speech) ? speech : [], got = [];
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      if (s && typeof s.ticket === "string" && s.ticket) got.push({ ticket: s.ticket, n: Number(s.chunk_num) || 0 });
    }
    got.sort(function (a, b) { return a.n - b.n; });
    if (!got.length || got[0].n !== 0) return [];
    var out = [];
    for (var j = 0; j < got.length && j < MAX_TICKETS; j++) out.push(got[j].ticket);
    return out;
  }

  /* A turn holding speech tickets never starts a local voice while its own is on the way:
   * a stand-in that the late voice then cut and restarted was the double voice measured on
   * prod (the browser voice at +6,008 ms, hers at +6,166 ms).
   *
   * THE CHUNKS. Chunk 0 decides the turn exactly as one ticket did: voice first, or the
   * words at SPEECH_WAIT_MS still expecting it, or a local voice once if it fails. The later
   * chunks are redeemed SPEECH_PARALLEL at a time, each as its predecessor lands, and ROUTED
   * IN ORDER, chunk k only behind chunk k-1, because voice/ writes a missing chunk off after
   * TTS_GAP_MS and then drops it when it does arrive: routed as they landed, a slow sentence
   * 2 would be lost behind a fast sentence 3. The first chunk that fails ends the voice —
   * nothing later is redeemed or routed — and no local voice stands in for a later chunk:
   * the words are on screen, and her first sentence was heard in her voice (or spoken
   * locally, if chunk 0 failed). */
  function voiceFirst(chatMessages, tickets, eid, seq, settle) {
    var n = tickets.length;
    var landed = [];         // chunk -> its TTS messages, once /api/speech delivered them
    var settled = [];        // chunk -> true once its request answered, failed or timed out
    var started = 0, inflight = 0;
    var next = 1;            // the later chunk whose turn it is to be routed
    var voiced = false;      // chunk 0 has been routed: the later chunks may follow it
    var over = false;        // the voice is finished with: a chunk failed, or chunk 0 was given up
    var superseded = false;  // …because a newer reply's voice started: its words stay silent, no stand-in
    var failedAt = -1;       // the first later chunk known to have failed, noticed in order by pump()
    var wordsOut = false;    // the chat message has been routed (once, by whichever path gets there first)
    var pipe = { seq: seq, supersede: supersede, flushWords: words };
    pipelines.push(pipe);
    expectVoice(eid);

    /** Nothing left to redeem or route: no newer reply can end this one any more, and the
     *  turn is SETTLED — the next waiting line may go. */
    function close() {
      var k = pipelines.indexOf(pipe);
      if (k < 0) return;
      pipelines.splice(k, 1);
      if (settle) settle();
    }

    /** The words, once. A safety line ending this reply before they are out puts them in the
     *  log first (`endEarlier`, W4-S7), silently: the voice is expected, so nothing local says them. */
    function words() {
      if (wordsOut) return;
      wordsOut = true;
      routeAll(chatMessages, "chat");
    }

    /** The voice ends here: nothing later is redeemed, and audio already in hand is not played. */
    function giveUp() {
      if (over) return;
      over = true;
      for (var j = next; j < n; j++) if (landed[j]) { stats.chunksDropped++; landed[j] = null; }
      close();
    }

    /** A newer reply's voice is starting: the rest of this one is not paid for, routed or
     *  heard. A chunk 0 not yet routed (in flight, or landed this instant) goes with the rest,
     *  and its words go out silently rather than in a local voice over the newer reply. */
    function supersede() {
      if (over) return;
      superseded = true;
      stats.chunksSuperseded += voiced ? n - next : n;
      if (!voiced && landed[0]) { landed[0] = null; stats.lateSpeechDropped++; }
      giveUp();
    }

    /** Redeem chunk `i`; resolves when it answered, failed, or passed the client's own
     *  deadline (where `AbortSignal.timeout` is missing the request has none). */
    function redeem(i) {
      started++; inflight++;
      var late = false;
      var req = post("/api/speech", { ticket: tickets[i] }, SPEECH_FETCH_MS).then(function (res) {
        var body = res.body;
        // Recorded, NEVER noted: a voice failure is not a brain failure (one speech 503 used to
        // read the whole page as degraded for ~30 s), so mode.js hears only /api/chat.
        if (body && body.reason) stats.speechReasons.push(body.reason);
        if (res.ok && body.messages && body.messages.length) {
          stats.speechOk++;
          // Past its deadline, or the voice is over: the audio is not played. For chunk 0
          // that is a voice turning up after the line was said (the line is never said twice).
          if (late || over) { if (i === 0) stats.lateSpeechDropped++; else stats.chunksDropped++; return; }
          landed[i] = body.messages;
        } else if (body) {
          stats.speechRefused++;
        } else {
          stats.speechErrors++;
        }
      });
      var deadline = new Promise(function (resolve) { setTimeout(resolve, SPEECH_FETCH_MS); });
      return Promise.race([req, deadline]).then(function () {
        late = true; settled[i] = true; inflight--;
        if (i > 0 && !landed[i] && failedAt < 0) failedAt = i;
        if (i === 0 && !landed[0]) giveUp();   // no first sentence, so nothing can follow it
        pump();
      });
    }

    /** Start redemptions up to the parallel cap, in chunk order — never past a failure. */
    function fill() {
      while (!over && failedAt < 0 && started < n && inflight < SPEECH_PARALLEL) redeem(started);
    }

    /** Route the later chunks in order as far as they have landed; the first failure ends
     *  the voice. Then top up the redemptions. */
    function pump() {
      while (voiced && !over && next < n && settled[next]) {
        if (!landed[next]) { stats.chunkFailures++; giveUp(); break; }
        routeAll(landed[next], "tts");
        stats.chunksRouted++;
        next++;
      }
      if (voiced && !over && next >= n) close();   // every chunk routed: nothing left to give up
      fill();
    }

    /** The voice is not coming: its words speak locally, once — a newer voice, which ends
     *  any older reply still being assembled. */
    function fallBack() {
      stats.voiceFallbacks++;
      giveUp();
      supersedeVoices(seq);
      releaseVoice(eid);     // speaks the held words, if they are already out
    }

    var speech0 = redeem(0);
    fill();
    var wait = new Promise(function (resolve) { setTimeout(resolve, SPEECH_WAIT_MS); });

    return Promise.race([speech0, wait]).then(function () {
      if (landed[0]) {
        // Voice first: bubble and audio land together, and the rest follows in order. Any
        // older reply still being assembled ends here.
        supersedeVoices(seq);
        routeAll(landed[0], "tts");
        words();
        stats.voiceFirst++;
        voiced = true;
        pump();
        return;
      }
      if (settled[0]) {
        // Refused or unreachable before the wait was up: words and local voice together
        // (superseded meanwhile: the words alone, silently — a newer reply has the voice).
        if (!superseded) fallBack();
        words();
        return;
      }
      // No voice yet: the words go out now, silently, still expecting their own voice.
      words();
      stats.chatFirst++;
      return speech0.then(function () {
        if (landed[0] && eid) {
          // However late it is, nothing local has said this line: play it, and any older
          // reply still being assembled ends here.
          supersedeVoices(seq);
          routeAll(landed[0], "tts");
          stats.lateSpeechPlayed++;
          voiced = true;
          pump();
          return;
        }
        if (landed[0]) { stats.lateSpeechDropped++; giveUp(); return; }   // no event to hold: the words spoke locally
        // Superseded while waiting: the words are on screen and a newer reply has the voice.
        if (superseded) return;
        // Refused, unreachable, or past the deadline: the line is said locally, once, and a
        // voice turning up after all is dropped (`redeem` counts it).
        fallBack();
      });
    });
  }

  /* ---- the bot control: turnstile.js resolves "" (not enforced — also when the module
   * is absent: the control lives on the SERVER), a fresh token, or null (DO NOT SEND). */
  function botToken() {
    var t;
    try { t = window.moxieTurnstile; } catch (e) { t = null; }
    if (!t || typeof t.getToken !== "function") return Promise.resolve("");
    try {
      /* The action is named HERE: the routes require different actions so a typed turn's
       * token cannot pay for costly STT (`mic.js` asks for "transcribe"). */
      return Promise.resolve(t.getToken("chat")).then(function (tok) {
        return typeof tok === "string" ? tok : null;
      }, function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }

  /* Marks for the one locally-composed line (same format as stub.js's `MK`): mood 1 +
   * `Gesture_Question`, a puzzled shrug. */
  var MK_MOOD = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>';
  var MK_SHRUG = '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,' +
                 '+repeat+:1,+blocking+:false,+action+:0,+eventName+:+Gesture_Question+,' +
                 '+category+:+BehaviourTree+,+behaviour+:++,+Track+:++}"/>';

  // What Moxie says when no token could be minted: never a silent dead Send, and no request.
  var BOT_LINE = "Hmm, my visitor check did not answer just now. Try me once more!";

  // CONSECUTIVE local token failures; the first send that gets a token resets it.
  var botStrikes = 0;

  /** No token, so nothing was sent. The FIRST failure says the honest "try me once more"
   *  (a one-off usually is); from the SECOND in a row the turn is answered from `stub.js`
   *  rather than repeating one sentence (with no stub, the line is still spoken). */
  function botUnavailable(text) {
    stats.botUnavailable++;
    botStrikes++;
    // Counted against the same 3-strike degrade an unreachable gateway uses, so the badge
    // and the copy stop claiming a live brain the page cannot reach.
    noteTransportError();
    var haveStub = false;
    try { haveStub = !!(window.moxieStub && window.moxieStub.enabled); } catch (e) {}
    if (botStrikes > 1 && haveStub) {
      status("Moxie’s visitor check isn’t answering — she’s using her recorded lines.");
      return fallbackReply(text);
    }
    status("Moxie could not finish her visitor check — try that again in a moment.");
    return localReply(BOT_LINE, MK_MOOD + MK_SHRUG + BOT_LINE);
  }

  /* ---- the live turn ----------------------------------------------------- */
  /** Resolves once the reply has STARTED (its voice routed, or its words out); the turn is
   *  SETTLED — in flight no more — once the reply is wholly handed to the speakers.
   *  `echoed`: the line is already in the log (it waited its turn). `early`: it goes out
   *  while an earlier reply is still being voiced, so its own reply waits behind that voice
   *  (`heldBehind`) — unless it is a safety line (W4-S7). */
  function liveTurn(text, echoed, early) {
    stats.live++;
    inflight++;
    awaitingChat++;
    if (early) stats.early++;
    // What the queue knows of this turn: `chatBack` once its reply's words are back (the
    // next line may go), `done` once it settles, `silenced`/`mute` for a later safety line.
    var finish;
    var turn = { early: !!early, silenced: false, mute: null, chatBack: once(function () { awaitingChat--; }) };
    turn.done = new Promise(function (resolve) { finish = resolve; });
    openTurns.push(turn);
    var valve = null;
    var settle = once(function () {
      clearTimeout(valve);
      turn.chatBack();           // a reply that never came back (the valve) frees the next line too
      inflight--;
      var k = openTurns.indexOf(turn);
      if (k >= 0) openTurns.splice(k, 1);
      finish();                  // a reply held behind this turn may start
      drain();
    });
    // However it goes, the turn is in flight for TURN_MAX_MS at most (see there).
    valve = setTimeout(function () { stats.turnsValved++; settle(); }, TURN_MAX_MS);
    status("thinking…");
    // …and with her face and arms (a child won't read the status line); a fast turn
    // never flashes a pose.
    if (window.moxieAlive) window.moxieAlive.thinking();
    if (!echoed) echoUser(text);
    // The bot control, in one line. `""` means this deployment does not enforce it.
    return botToken().then(function (tok) {
      if (tok === null) return heldBehind(turn, true).then(function () { return botUnavailable(text); }).then(settle, settle);
      // The widget works now, so the consecutive-failure count restarts.
      botStrikes = 0;
      if (tok) stats.botTokens++;
      return chatPost(text, tok, settle, turn);
    });
  }

  /** The POST itself, split out of `liveTurn` so the token step is a wrapper. `settle` is
   *  called exactly once, on every path, when the reply is wholly handed to the speakers;
   *  `turn` is what the queue knows of it (`liveTurn`). */
  function chatPost(text, token, settle, turn) {
    var payload = { text: text, context: contextBlob };
    // Cloudflare's own form-field name (`_lib/turnstile.js::TOKEN_FIELD`); absent when
    // unenforced, so such a deployment sends byte-identically.
    if (token) payload["cf-turnstile-response"] = token;
    var seq = ++turnSeq;      // this turn's place in the conversation, whenever its reply lands
    return post("/api/chat", payload, CHAT_FETCH_MS).then(function (res) {
      // The wait is over, whatever the outcome: every answer and refusal passes here once.
      if (window.moxieAlive) window.moxieAlive.settled();
      if (!res.body) {
        stats.chatErrors++;
        noteTransportError();
        status("Moxie’s brain is unreachable — answering from her recorded lines.");
        return heldBehind(turn, true).then(function () { return fallbackReply(text, seq); }).then(settle);
      }
      var body = res.body;
      note(body.reason, body.retry_after_s);

      if (body.reason) {
        // A refusal or a safety block: the page must not go quiet (§4.5). `blocked` carries
        // the rule table's redirect line; every other reason answers from `stub.js`.
        if (body.reason === "blocked") stats.blocked++;
        else stats.chatRefused++;
        // A refused blob would be resent every turn (only success replaces it): drop it.
        if (body.reason === "bad_request") contextBlob = "";
        var m2 = mode();
        status((m2 && m2.message && m2.message()) || "answering from her recorded lines.");
        // A line with no voice coming speaks as soon as the ears allow: this turn's voice is
        // starting. The route's own words on a reason body are its SAFETY LINE, never held
        // behind an earlier reply: that reply ends, and this line follows the sentence now
        // playing rather than cutting it — or, the Listen tap having stopped that sentence,
        // the microphone it opens (W4-S7).
        if (body.messages && body.messages.length) {
          if (turn.early) endEarlier(turn, seq);
          return (turn.early ? whenQuiet().then(whenMicOpen) : Promise.resolve()).then(whenEarsIdle).then(function () {
            supersedeVoices(seq);
            routeAll(body.messages, "chat");
            settle();
          });
        }
        return heldBehind(turn, true).then(function () { return fallbackReply(text, seq); }).then(settle);
      }

      stats.chatOk++;
      status("");
      // She cites her source: `cited` = "<title>|<path>", linked into the docs explorer.
      if (body.cited) {
        var bar = body.cited.indexOf("|");
        var ctitle = bar < 0 ? body.cited : body.cited.slice(0, bar);
        var cpath = bar < 0 ? "" : body.cited.slice(bar + 1);
        var log = document.getElementById("transcript");
        if (log && ctitle) {
          stats.cited++;
          var row = document.createElement("div");
          row.className = "cited";
          var lead = document.createElement("span");
          lead.textContent = "looked it up in ";
          var a = document.createElement("a");
          a.textContent = ctitle;                    // textContent = XSS-safe
          a.href = cpath ? "docs.html#/" + cpath : "docs.html";
          a.target = "_blank";
          a.rel = "noopener";
          row.appendChild(lead);
          row.appendChild(a);
          log.appendChild(row);
          if ((log.scrollHeight - log.scrollTop - log.clientHeight) < 40) log.scrollTop = log.scrollHeight;
        }
      }
      // She drew something: fired, not awaited — a bonus that must never delay her words
      // (mermaid's first load is 3.3 MB); every failure inside draws nothing.
      if (body.diagram && window.moxieDiagram) {
        stats.diagrams++;
        window.moxieDiagram.render(body.diagram);
      }
      contextBlob = typeof body.context === "string" ? body.context : "";
      var tickets = ticketsOf(body.speech);
      // The context is kept NOW, and a line waiting for it goes out at once (W4-S7). The
      // reply itself starts only once every reply POSTed before it is handed over (an early
      // line) and the ears are idle — never into an open microphone. Until then a later
      // safety line may end it: its words go in the log at once, silently, never voiced.
      turn.mute = once(function () {
        var eid = eventOf(body.messages, body.speech);
        if (eid) { expectVoice(eid); routeAll(body.messages, "chat"); }
      });
      turn.chatBack();
      drain();
      // An early reply's stand-in voice, should its own be refused, waits for her playing
      // sentence (`releaseVoice`): marked until this reply's voice is decided.
      var quiet = turn.early ? eventOf(body.messages, body.speech) : "";
      if (quiet) quietFirst[quiet] = true;
      var reply = heldBehind(turn, !tickets.length).then(whenEarsIdle).then(function () {
        turn.mute = null;
        if (turn.silenced) { stats.tickets += tickets.length; stats.chunksSuperseded += tickets.length; settle(); return; }
        if (!tickets.length) {
          // No voice configured (`voice: false`): the words speak from the clips, at once.
          supersedeVoices(seq);
          routeAll(body.messages, "chat");
          settle();
          return;
        }
        stats.tickets += tickets.length;
        return voiceFirst(body.messages, tickets, eventOf(body.messages, body.speech), seq, settle);
      });

      var forget = function () { if (quiet) delete quietFirst[quiet]; };
      reply.then(forget, forget);
      return reply;
    });
  }

  /* ---- the wrapped surface (§3.5) ---------------------------------------- */
  window.moxieBridge = Object.assign({}, inner, {
    /** A child's turn: a connected MQTT broker, or nothing spendable -> `inner.sendUserTurn`
     *  (untouched / echo + `stub.js`); otherwise the HTTP turn. */
    sendUserTurn: function (text) {
      var t = String(text == null ? "" : text).trim();
      if (!t) return Promise.resolve();
      stats.turns++;
      if (inner.isLive() || !canSpendLiveTurn()) {
        stats.delegated++;
        inner.sendUserTurn(t);
        return Promise.resolve();
      }
      return liveTurn(t);
    },

    /** A line the PAGE chose (mic.js's scripted consolation), not a visitor's words, so it
     *  must cost nothing and is never shown as theirs: NOT ONE REQUEST, and no "You" row
     *  (one read "YOU: Guess what, it's my birthday today!" on production). It is logged as
     *  a "Pretend line", played from its child clip as a child turn is, and answered from
     *  `stub.js` after the same beat, live page or not. A connected MQTT broker is a
     *  self-hoster's own backend and still gets it through `inner.sendUserTurn`. */
    sendScriptedTurn: function (text) {
      var t = String(text == null ? "" : text).trim();
      if (!t) return Promise.resolve();
      stats.scripted++;
      if (inner.isLive()) {
        inner.sendUserTurn(t);
        return Promise.resolve();
      }
      if (canSpendLiveTurn()) stats.scriptedFree++;
      var log = document.getElementById("transcript");
      if (log) {
        // `.turn` (the openers step aside, ambient.js holds its mutters), never `.user`.
        var row = document.createElement("div");
        row.className = "turn pretend";
        row.innerHTML = '<span class="who">Pretend line</span><span class="msg"></span>';
        row.querySelector(".msg").textContent = t;     // textContent = XSS-safe
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;
      }
      if (window.moxieAudio) {
        window.moxieAudio.sfx("listen");
        if (window.moxieAudio.speakClipOnly) window.moxieAudio.speakClipOnly(t, "child");
      }
      return fallbackReply(t);
    },

    /** Live means "a brain will answer this turn", from either transport (§3.5). */
    isLive: function () {
      var m = mode();
      return !!(inner.isLive() || (m && m.state && m.state() === "live"));
    },

    /** A line from a CONTROL (the Ask box, an opener, the mic's transcript): one at a time,
     *  in order, behind the turn in flight and the ears (additive; see above). */
    queueUserTurn: queueUserTurn,

    /** The ears' three moments, told by mic.js (additive): Listen was tapped — stop her, on
     *  purpose, holding nothing — the microphone is open (hold what follows, for the bound
     *  in ms it names at most), and the ears are done with the clip. */
    interruptVoice: interruptVoice,
    earsOpen: earsOpen,
    earsIdle: earsIdle,

    /** What the transport RECORDED (additive to the bridge's surface). */
    transportStats: function () { return JSON.parse(JSON.stringify(stats)); },
  });

  /* ---- the typed turn, and the ONE control that carries it ---------------- *
   * A typed line is a spoken line without the STT leg (the same `sendUserTurn` mic.js uses).
   * `#speech-input` + `#speech-btn` ("Say") speak through the LOCAL Piper sidecar; where none
   * can exist (env.js decides, from mode.js and its probe — never the hostname) env.js calls
   * `adopt()` and the box becomes the typed turn ("Ask"). With a real Piper it is untouched:
   * local engines stay first-class. The injected `#chat-sub` box is the fallback for pages
   * with no `#speech-input`; exactly one typed control is ever visible. */
  var talkSec = null;        // the injected "Talk" section, when one had to be made
  var adopted = false;       // true once #speech-input/#speech-btn carry the typed turn

  /** Client-side line cap (also enforced server-side, §4.1), so the page says WHY before an
   *  over-long line reaches `admit()`. */
  function maxChars() {
    var m = mode();
    var lim = (m && m.limits) ? m.limits() : {};
    var n = Number(lim && lim.max_input_chars);
    return (isFinite(n) && n > 0) ? n : 500;
  }

  /** The one typed path (human text only; invented lines use `sendScriptedTurn`). It spends
   *  exactly like the mic: live only when `canSpendLiveTurn()`, and one line at a time —
   *  a line typed while she is still answering is taken now and answered next.
   *  @returns {boolean} sent. */
  function sendTyped(text) {
    var t = String(text == null ? "" : text).trim();
    if (!t) return false;
    var max = maxChars();
    if (t.length > max) {
      status("that is a bit long — " + max + " characters at most.");
      return false;
    }
    status(inflight || waiting.length || earsBusy ? "Moxie will answer that next." : "");
    window.moxieBridge.queueUserTurn(t);
    return true;
  }

  /** `#chat-status` — `status()`'s target — wherever the typed control ended up. */
  function ensureStatus(section) {
    var st = document.getElementById("chat-status");
    if (!st) {
      st = document.createElement("p");
      st.id = "chat-status";
      st.className = "hint";
      st.setAttribute("aria-live", "polite");
    }
    if (section && section.appendChild) section.appendChild(st);
    return st;
  }

  function submitFrom(input) {
    if (!input) return false;
    if (!sendTyped(input.value || "")) return false;
    input.value = "";
    return true;
  }

  /** Hand `#speech-input` / `#speech-btn` the typed turn. ONE-WAY (`adopt(false)` only
   *  queries). Existing listeners are not removed — they check `moxieTypedTurn.adopted()`
   *  and stand down; replacing the nodes would break the phrase chips.
   *  @returns {boolean} whether this page has such a control at all. */
  function adoptSpeechControl() {
    if (adopted) return true;
    var btn = document.getElementById("speech-btn");
    var inp = document.getElementById("speech-input");
    if (!btn || !inp) return false;

    var sec = btn.closest ? btn.closest("section.sub") : null;
    /* The status line follows the control. Adoption normally runs before `injectTalkUI`
     * (env.js renders while the document parses), hence `ensureStatus` and the early-out. */
    ensureStatus(sec);
    // …and a late-adopting page hides the injected box: never two text inputs.
    if (talkSec) talkSec.hidden = true;

    btn.textContent = "Ask";
    btn.setAttribute("title",
      "Sends your line to Moxie — she answers here. (Speaking arbitrary text needs the local Piper server.)");
    btn.removeAttribute("disabled");
    btn.disabled = false;
    inp.setAttribute("placeholder", "Ask Moxie anything…"); // the same words sim.html ships
    inp.setAttribute("maxlength", String(maxChars()));
    inp.removeAttribute("disabled");
    inp.disabled = false;
    var hint = sec && sec.querySelector ? sec.querySelector("h3 .hint") : null;
    if (hint) hint.textContent = "tap a phrase · or ask her";

    btn.addEventListener("click", function () { submitFrom(inp); });
    inp.addEventListener("keydown", function (e) { if (e.key === "Enter") submitFrom(inp); });
    adopted = true;
    return true;
  }

  /* The injected fallback box, for pages with no `#speech-input` to adopt. Injected here so
   * the control exists exactly when the transport does. */
  function injectTalkUI() {
    if (adopted) return;                        // the page already has a typed control
    if (document.getElementById("chat-send")) return;
    var mic = document.getElementById("mic-btn");
    var host = mic && mic.closest ? mic.closest("section.sub") : null;
    /* #chat-dock holds exactly ONE text box, so when the mic lives there this box goes to
     * the rail (only a self-hosted page with a live Piper sidecar reaches this). */
    if (host && host.closest && host.closest("#chat-dock")) {
      var note = document.getElementById("voice-note");
      host = note && note.closest ? note.closest("section.sub") : null;
    }
    if (!host || !host.parentNode) return;

    var sec = document.createElement("section");
    sec.className = "sub";
    sec.id = "chat-sub";
    var h = document.createElement("h3");
    h.textContent = "Talk";
    var hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = "type · she answers";
    h.appendChild(hint);
    var row = document.createElement("div");
    row.className = "row";
    var input = document.createElement("input");
    input.id = "chat-input";
    input.type = "text";
    input.placeholder = "Ask Moxie anything…";
    input.autocomplete = "off";
    input.setAttribute("maxlength", "500");     // mirrors DEMO_MAX_INPUT_CHARS (§4.1)
    var send = document.createElement("button");
    send.id = "chat-send";
    send.type = "button";
    send.textContent = "Send";
    var p = document.createElement("p");
    p.id = "chat-status";
    p.className = "hint";
    p.setAttribute("aria-live", "polite");

    row.appendChild(input);
    row.appendChild(send);
    sec.appendChild(h);
    sec.appendChild(row);
    sec.appendChild(p);
    host.parentNode.insertBefore(sec, host);
    talkSec = sec;

    send.addEventListener("click", function () { submitFrom(input); });
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") submitFrom(input); });
  }

  try {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", injectTalkUI, { once: true });
    } else {
      injectTalkUI();
    }
  } catch (e) {}

  /* The seam env.js uses. Published before the honesty flag below. */
  window.moxieTypedTurn = {
    adopt: function (on) { return on === false ? adopted : adoptSpeechControl(); },
    adopted: function () { return adopted; },
    send: sendTyped,
    maxChars: maxChars,
  };

  /* THE HONESTY GUARD: tells mode.js a configured deployment may be PAINTED live because
   * something can now use it. Set LAST, so it is never true while half-wired. */
  window.moxieCloudTransport = true;
})();
