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
 *      fails ends the voice, and nothing local ever stands in for a later chunk.
 * A voice failure is not a brain failure: speech-route reasons are recorded in the stats and
 * never reported to the mode machine.
 *
 * No secret and no hostname here: the base is `moxieMode.apiBase()` (= location.origin).
 * `ticket` and `context` are opaque, signed server-side, and die with the tab (§2.6).
 */
(function () {
  "use strict";

  // §3.4's client-side ceiling on how long the words wait for the voice.
  var SPEECH_WAIT_MS = 2500;
  // Client ceilings ABOVE the server's own (20 s / 12 s), so its honest 504 `timeout`
  // envelope wins the race and the page learns WHY. SPEECH_FETCH_MS is also the deadline
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
    lateSpeechDropped: 0,    // a voice that landed after its words were already spoken locally
    tickets: 0,              // speech tickets received for live replies (one per sentence)
    chunksRouted: 0,         // later chunks (1+) routed behind their predecessor
    chunkFailures: 0,        // a later chunk refused, unreachable or past the deadline: the voice ended there
    chunksDropped: 0,        // later-chunk audio that landed but was not played (after a failure, or too late)
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

  /** A reply composed on the page, through the same `route()` a real one takes. */
  function localReply(text, markup) {
    stats.order.push("stub");
    inner.route("/devices/d_sim/commands/remote_chat", JSON.stringify({
      command: "remote_chat", result: "OK", backend: "router",
      output: { text: text, markup: markup },
    }));
  }

  /** The degraded answer for ONE turn: `stub.js` after the bridge's 450 ms beat. The turn
   *  is already echoed, so not via `inner.sendUserTurn`. */
  function fallbackReply(text) {
    stats.fallbacks++;
    if (!window.moxieStub || !window.moxieStub.enabled) return Promise.resolve();
    var r = window.moxieStub.reply(text);
    return new Promise(function (resolve) {
      setTimeout(function () { localReply(r.text, r.markup); resolve(); }, FALLBACK_MS);
    });
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

  function releaseVoice(eid) {
    try { if (eid && inner.releaseCloudVoice) inner.releaseCloudVoice(eid); } catch (e) {}
  }

  /** How many /api/speech redemptions of one reply are in flight at once: ONE. Chunk 1 is
   *  requested the moment chunk 0 lands and synthesises while chunk 0 plays (measured: a
   *  later chunk's round trip 1.7-2.9 s against 2.2-5.4 s of playback before it), and so on.
   *  Two at once was measured to slow chunk 0 (2026-10-08, two 10-turn arms): 2.4-3.7 s,
   *  median 3.3 s, on the 5 chunked turns of the two-in-flight arm, against 1.6-2.6 s,
   *  median 2.0 s, on that arm's single-chunk turns and 2.1-3.3 s, median 2.5 s, on the 6
   *  chunked turns of this arm — which delays the first words, the one thing chunking is for. */
  var SPEECH_PARALLEL = 1;
  /** More tickets than any reply is worth. A server that minted them is misconfigured, and
   *  nothing past this many is redeemed: each costs the visitor's speech window. */
  var MAX_TICKETS = 8;

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
  function voiceFirst(chatMessages, tickets, eid) {
    var n = tickets.length;
    var landed = [];         // chunk -> its TTS messages, once /api/speech delivered them
    var settled = [];        // chunk -> true once its request answered, failed or timed out
    var started = 0, inflight = 0;
    var next = 1;            // the later chunk whose turn it is to be routed
    var voiced = false;      // chunk 0 has been routed: the later chunks may follow it
    var over = false;        // the voice is finished with: a chunk failed, or chunk 0 was given up
    var failedAt = -1;       // the first later chunk known to have failed, noticed in order by pump()
    expectVoice(eid);

    /** The voice ends here: nothing later is redeemed, and audio already in hand is not played. */
    function giveUp() {
      if (over) return;
      over = true;
      for (var j = next; j < n; j++) if (landed[j]) { stats.chunksDropped++; landed[j] = null; }
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
      fill();
    }

    /** The voice is not coming: its words speak locally, once. */
    function fallBack() {
      stats.voiceFallbacks++;
      giveUp();
      releaseVoice(eid);     // speaks the held words, if they are already out
    }

    var speech0 = redeem(0);
    fill();
    var wait = new Promise(function (resolve) { setTimeout(resolve, SPEECH_WAIT_MS); });

    return Promise.race([speech0, wait]).then(function () {
      if (landed[0]) {
        // Voice first: bubble and audio land together, and the rest follows in order.
        routeAll(landed[0], "tts");
        routeAll(chatMessages, "chat");
        stats.voiceFirst++;
        voiced = true;
        pump();
        return;
      }
      if (settled[0]) {
        // Refused or unreachable before the wait was up: words and local voice together.
        fallBack();
        routeAll(chatMessages, "chat");
        return;
      }
      // No voice yet: the words go out now, silently, still expecting their own voice.
      routeAll(chatMessages, "chat");
      stats.chatFirst++;
      return speech0.then(function () {
        if (landed[0] && eid) {
          // However late it is, nothing local has said this line: play it.
          routeAll(landed[0], "tts");
          stats.lateSpeechPlayed++;
          voiced = true;
          pump();
          return;
        }
        if (landed[0]) { stats.lateSpeechDropped++; giveUp(); return; }   // no event to hold: the words spoke locally
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
    localReply(BOT_LINE, MK_MOOD + MK_SHRUG + BOT_LINE);
    return Promise.resolve();
  }

  /* ---- the live turn ----------------------------------------------------- */
  function liveTurn(text) {
    stats.live++;
    status("thinking…");
    // …and with her face and arms (a child won't read the status line); a fast turn
    // never flashes a pose.
    if (window.moxieAlive) window.moxieAlive.thinking();
    echoUser(text);
    // The bot control, in one line. `""` means this deployment does not enforce it.
    return botToken().then(function (tok) {
      if (tok === null) return botUnavailable(text);
      // The widget works now, so the consecutive-failure count restarts.
      botStrikes = 0;
      if (tok) stats.botTokens++;
      return chatPost(text, tok);
    });
  }

  /** The POST itself, split out of `liveTurn` so the token step is a wrapper. */
  function chatPost(text, token) {
    var payload = { text: text, context: contextBlob };
    // Cloudflare's own form-field name (`_lib/turnstile.js::TOKEN_FIELD`); absent when
    // unenforced, so such a deployment sends byte-identically.
    if (token) payload["cf-turnstile-response"] = token;
    return post("/api/chat", payload, CHAT_FETCH_MS).then(function (res) {
      // The wait is over, whatever the outcome: every answer and refusal passes here once.
      if (window.moxieAlive) window.moxieAlive.settled();
      if (!res.body) {
        stats.chatErrors++;
        noteTransportError();
        status("Moxie’s brain is unreachable — answering from her recorded lines.");
        return fallbackReply(text);
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
        if (body.messages && body.messages.length) { routeAll(body.messages, "chat"); return; }
        return fallbackReply(text);
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
      if (!tickets.length) {
        // No voice configured (`voice: false`): the words speak from the clips.
        routeAll(body.messages, "chat");
        return;
      }
      stats.tickets += tickets.length;
      return voiceFirst(body.messages, tickets, eventOf(body.messages, body.speech));
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
     *  must cost nothing: same routing as `sendUserTurn` except a LIVE page answers with a
     *  local echo + stub reply and NOT ONE REQUEST. */
    sendScriptedTurn: function (text) {
      var t = String(text == null ? "" : text).trim();
      if (!t) return Promise.resolve();
      stats.scripted++;
      if (inner.isLive() || !canSpendLiveTurn()) {
        inner.sendUserTurn(t);
        return Promise.resolve();
      }
      stats.scriptedFree++;
      echoUser(t);
      return fallbackReply(t);
    },

    /** Live means "a brain will answer this turn", from either transport (§3.5). */
    isLive: function () {
      var m = mode();
      return !!(inner.isLive() || (m && m.state && m.state() === "live"));
    },

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
   *  exactly like the mic: live only when `canSpendLiveTurn()`. @returns {boolean} sent. */
  function sendTyped(text) {
    var t = String(text == null ? "" : text).trim();
    if (!t) return false;
    var max = maxChars();
    if (t.length > max) {
      status("that is a bit long — " + max + " characters at most.");
      return false;
    }
    status("");
    window.moxieBridge.sendUserTurn(t);
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
