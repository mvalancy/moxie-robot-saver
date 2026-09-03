/* cloud-transport.js — the live HTTP turn: one typed sentence in, Moxie's own voice out.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.4 (the voice-first ordering rule and
 * why no edit to `bridge.js` is needed), §3.5 (this file: a wrapper, not a replacement),
 * §3.2 (both route contracts), §4.5 (what a 429/503 means), §6 (the fallback).
 *
 * WHAT IT DOES, in one sentence: when `window.moxieMode` says this deployment has a live
 * brain, a child's turn goes to the same-origin `POST /api/chat` and `POST /api/speech`
 * instead of to `stub.js`, and the two payloads that come back are handed to the SAME
 * `route()` function a live MQTT bus and a recorded session already drive.
 *
 * ============================================================================
 * IT IS A WRAPPER, AND THAT IS THE WHOLE DESIGN (§3.5).
 *
 * `bridge.js` and `audio.js` ARE NOT MODIFIED. Not one line. Everything below composes
 * over the seven-and-more member surface `bridge.js` publishes on `window.moxieBridge`:
 * `route`, `faceEvent`, `presenceStats`, `telehealthStats`, `hasCloudVoice`, `actionStats`,
 * `activityStats`, `sendQuery`, `reportMentorBehavior`, `reportTelehealthState` all pass
 * through UNTOUCHED, and only `sendUserTurn` and `isLive` are wrapped. That is what keeps
 * `sim/test_bridge.mjs`, `sim/test_automarkup_render.mjs`, `sim/test_audio.mjs`,
 * `sim/test_presence_bridge.mjs`, `sim/test_voice.mjs` and `sim/tests/test_sil.py` green by
 * construction rather than by luck.
 *
 * A CONNECTED MQTT BROKER ALWAYS WINS. If `inner.isLive()` is true a real supervisor is on
 * the other end of the bus, and it gets the turn — the hosted HTTP brain never competes
 * with it. A self-hoster with a broker is completely unaffected by this file.
 * ============================================================================
 *
 * ============================================================================
 * THE ONE BEHAVIOUR THAT WOULD OTHERWISE BITE: THE DOUBLE VOICE (§3.4).
 *
 * `bridge.js`'s `speakLocally` speaks **IMMEDIATELY** when no MQTT client is connected
 * (`bridge.js`:298-300 — the 900 ms grace window only applies on a live bus). So a naive
 * HTTP transport that routed the chat message first would play the pre-cached/browser voice
 * AND THEN the gateway voice, one on top of the other.
 *
 * `speakLocally` returns instantly when `cloudVoice` is already latched
 * (`bridge.js`:299), and `handleTts` latches it (:315). So the fix is ORDERING, not an
 * edit:
 *
 *   1. POST /api/chat  -> the chat message plus a speech ticket.
 *   2. POST /api/speech immediately, in parallel with nothing else.
 *   3. Route the chat message as soon as EITHER the speech reply lands OR
 *      `SPEECH_WAIT_MS` elapses — routing the TTS message FIRST if it arrived.
 *
 * One voice, always. The bubble and the audio land together. If TTS fails or is slow the
 * text still renders within 2.5 s and speaks from the clip/browser voice exactly as the
 * site does today. And a TTS reply that arrives AFTER the local voice already started is
 * dropped rather than layered on top of it — `stats.lateSpeechDropped` records it, and
 * `sim/test_cloud_transport.mjs` asserts the ordering in both directions.
 * ============================================================================
 *
 * NO SECRET IS INVOLVED, AND THERE IS NO HOSTNAME IN THIS FILE. Both routes are
 * same-origin under `/api/*` (the base comes from `window.moxieMode.apiBase()`, which is
 * `location.origin`), so a fork on any domain works with zero configuration and the
 * browser never holds a gateway key. The `ticket` and `context` strings are OPAQUE here:
 * they are signed server-side, this file only carries them, and the context dies with the
 * tab — nothing about a conversation reaches disk anywhere (§2.6).
 */
(function () {
  "use strict";

  // §3.4's client-side ceiling on how long the words wait for the voice.
  var SPEECH_WAIT_MS = 2500;
  // Client-side request ceilings. Deliberately ABOVE the server's own
  // `DEMO_CHAT_TIMEOUT_MS`/`DEMO_SPEECH_TIMEOUT_MS` (20 s / 12 s) so the server's honest
  // 504 `timeout` envelope wins the race and the page learns WHY, instead of the browser
  // aborting first and the page learning only "something failed".
  var CHAT_FETCH_MS = 25000;
  var SPEECH_FETCH_MS = 15000;
  // The pause before a fallback reply, matching `bridge.js`:691's own 450 ms so a degraded
  // turn has the same rhythm as it does today.
  var FALLBACK_MS = 450;

  var inner = window.moxieBridge;
  // No bridge, no transport. This file is additive: with `bridge.js` absent it does
  // nothing at all rather than half-wiring a page.
  if (!inner || typeof inner.sendUserTurn !== "function" || typeof inner.route !== "function") return;

  /* The topic the local echo rides. `route()` dispatches on the topic SUFFIX only
   * (`bridge.js`:599-610), so the device segment is identity and not routing: if a
   * deployment sets `DEMO_DEVICE_ID` to something else, the server's own messages carry
   * that id and still route correctly, and this echo still routes correctly too. `d_sim`
   * mirrors `bridge.js`:50, which is what the browser SIM has always published as. */
  var USER_TOPIC = "/devices/d_sim/events/remote-chat";

  /* The signed conversation blob (§3.3). Opaque, browser-held, capped server-side at 4
   * turns / 1500 chars, re-minted every turn, and gone when the tab closes. Moxie really
   * does forget this conversation when you close the tab, and the page says so. */
  var contextBlob = "";

  /* Everything this transport RECORDED, readable after the fact. Tests assert on this and
   * never on a live timing (playbook rule 11). */
  var stats = {
    turns: 0, live: 0, delegated: 0, fallbacks: 0,
    chatOk: 0, chatRefused: 0, chatErrors: 0,
    speechOk: 0, speechRefused: 0, speechErrors: 0,
    voiceFirst: 0,           // the TTS message was routed BEFORE the chat message
    chatFirst: 0,            // the 2.5 s wait elapsed, so the words went out alone
    lateSpeechDropped: 0,    // TTS arrived after the local voice had already started
    lateSpeechPlayed: 0,     // TTS arrived late but nothing was speaking, so it played
    blocked: 0,
    reasons: [],             // every reason the server gave, in order
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

  /** Is a live turn spendable right now? `mode.js` owns this answer: it is `live`, a
   *  transport is loaded (this file), and no `Retry-After` window is open (§6.3). */
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

  /* ---- one POST ----------------------------------------------------------- */
  /**
   * @returns {Promise<{ok:boolean, body:object|null}>} — `ok` means "a usable envelope
   * came back with no reason", never "the HTTP status was 2xx". Never rejects.
   */
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

  /** Echo the child's turn locally so the transcript row and the `listen` SFX fire
   *  (`bridge.js`:668-681). Deliberately NOT `inner.sendUserTurn`: that would ALSO fire
   *  the offline stub 450 ms later (`bridge.js`:689-695) and Moxie would answer twice. */
  function echoUser(text) {
    inner.route(USER_TOPIC, JSON.stringify({ command: "prompt", backend: "router", speech: text }));
  }

  /** The degraded answer for ONE turn: `stub.js`, in the same reply shape, after the same
   *  450 ms beat `bridge.js` uses. The user turn has already been echoed, so this must not
   *  go back through `inner.sendUserTurn` (that would echo it a second time). */
  function fallbackReply(text) {
    stats.fallbacks++;
    if (!window.moxieStub || !window.moxieStub.enabled) return Promise.resolve();
    var r = window.moxieStub.reply(text);
    return new Promise(function (resolve) {
      setTimeout(function () {
        stats.order.push("stub");
        inner.route("/devices/d_sim/commands/remote_chat", JSON.stringify({
          command: "remote_chat", result: "OK", backend: "router",
          output: { text: r.text, markup: r.markup },
        }));
        resolve();
      }, FALLBACK_MS);
    });
  }

  /* ---- §3.4: the voice, then the words ----------------------------------- */
  function voiceFirst(chatMessages, ticket) {
    var tts = null;
    var speech = post("/api/speech", { ticket: ticket }, SPEECH_FETCH_MS).then(function (res) {
      if (res.body) {
        note(res.body.reason, res.body.retry_after_s);
        if (res.ok && res.body.messages && res.body.messages.length) {
          stats.speechOk++;
          tts = res.body.messages;
        } else {
          stats.speechRefused++;
        }
      } else {
        stats.speechErrors++;
        noteTransportError();
      }
    });
    var waited = false;
    var wait = new Promise(function (resolve) {
      setTimeout(function () { waited = true; resolve(); }, SPEECH_WAIT_MS);
    });

    return Promise.race([speech, wait]).then(function () {
      if (tts) {
        // The voice arrived first. Route it BEFORE the words: `handleTts` latches
        // `cloudVoice`, after which `speakLocally` is a permanent no-op and the bubble
        // and the audio land together.
        routeAll(tts, "tts");
        routeAll(chatMessages, "chat");
        stats.voiceFirst++;
        return;
      }
      // 2.5 s elapsed with no voice. The words go out alone and speak from the clip or
      // the browser voice, exactly as the site does today.
      routeAll(chatMessages, "chat");
      stats.chatFirst++;
      if (!waited) return;      // the speech promise settled without producing audio
      return speech.then(function () {
        if (!tts) return;
        // Late audio. Playing it on top of a local voice already in the air is the double
        // voice this whole ordering exists to prevent — so it is dropped. But from the
        // second turn onward `cloudVoice` is latched and `speakLocally` did nothing at
        // all, so nothing is speaking and the late audio is exactly what the page needs.
        var speaking = false;
        try {
          speaking = !!(window.moxieAudio && window.moxieAudio.isSpeaking && window.moxieAudio.isSpeaking());
        } catch (e) {}
        if (speaking) { stats.lateSpeechDropped++; return; }
        routeAll(tts, "tts");
        stats.lateSpeechPlayed++;
      });
    });
  }

  /* ---- the live turn ----------------------------------------------------- */
  function liveTurn(text) {
    stats.live++;
    status("thinking…");
    echoUser(text);
    return post("/api/chat", { text: text, context: contextBlob }, CHAT_FETCH_MS).then(function (res) {
      if (!res.body) {
        stats.chatErrors++;
        noteTransportError();
        status("Moxie’s brain is unreachable — answering from her recorded lines.");
        return fallbackReply(text);
      }
      var body = res.body;
      note(body.reason, body.retry_after_s);

      if (body.reason) {
        // A refusal, or a safety block. Either way the page must not go quiet (§4.5).
        // `blocked` carries the rule table's own redirect line, so it is routed; every
        // other reason answers from `stub.js` for this one turn.
        if (body.reason === "blocked") stats.blocked++;
        else stats.chatRefused++;
        var m2 = mode();
        status((m2 && m2.message && m2.message()) || "answering from her recorded lines.");
        if (body.messages && body.messages.length) { routeAll(body.messages, "chat"); return; }
        return fallbackReply(text);
      }

      stats.chatOk++;
      status("");
      contextBlob = typeof body.context === "string" ? body.context : "";
      var ticket = body.speech && body.speech[0] && body.speech[0].ticket;
      if (!ticket) {
        // No voice configured (`DEMO_TTS_MODEL` unset => `voice: false`): the words go out
        // and speak from the clips, which is today's behaviour and honest about it.
        routeAll(body.messages, "chat");
        return;
      }
      return voiceFirst(body.messages, ticket);
    });
  }

  /* ---- the wrapped surface (§3.5) ---------------------------------------- */
  window.moxieBridge = Object.assign({}, inner, {
    /**
     * A child's turn. Three paths, in priority order:
     *   1. A connected MQTT broker -> `inner.sendUserTurn`, untouched. A real supervisor
     *      answers and the hosted brain stays out of the way.
     *   2. Mode `live`, a transport loaded, no open `Retry-After` -> the HTTP turn.
     *   3. Anything else -> `inner.sendUserTurn`, which echoes the turn and answers from
     *      `stub.js` exactly as it does today.
     */
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

    /** Live means "a brain will answer this turn", from either transport (§3.5). */
    isLive: function () {
      var m = mode();
      return !!(inner.isLive() || (m && m.state && m.state() === "live"));
    },

    /** What the transport RECORDED. Additive: every member `bridge.js` published is still
     *  present and unchanged, so nothing that reads the old surface can notice. */
    transportStats: function () { return JSON.parse(JSON.stringify(stats)); },
  });

  /* ---- the one piece of UI this slice needs ------------------------------ *
   * The page has no "type a sentence to Moxie" control today: `#speech-input`/`#speech-btn`
   * make MOXIE say a line (a TTS test), and the only thing that has ever sent a CHILD turn
   * is `mic.js`. The definition of done is that a stranger "types or speaks a sentence", so
   * typing needs a box.
   *
   * It is injected from here rather than added to `sim.html` for two reasons: it keeps
   * `sim.html`'s edit to the single `<script>` tag §9's file table budgets, and it means the
   * control exists exactly when the transport does. `env.js` already established this
   * pattern — it injects the env badge and the banner the same way.
   *
   * It works in EVERY mode: with no live brain the turn goes to `stub.js` through
   * `inner.sendUserTurn`, which is a strict improvement on today's page (you could not type
   * to Moxie at all), and it is why the box carries no mode-dependent copy of its own.
   */
  function injectTalkUI() {
    if (document.getElementById("chat-send")) return;
    var mic = document.getElementById("mic-btn");
    var host = mic && mic.closest ? mic.closest("section.sub") : null;
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
    input.placeholder = "say something to moxie…";
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

    function submit() {
      var t = (input.value || "").trim();
      if (!t) return;
      // The cap is enforced server-side too (§4.1 enforces it twice on purpose); this is
      // only so the page can say why before spending a request.
      var lim = mode() && mode().limits ? mode().limits() : {};
      var max = Number(lim.max_input_chars) || 500;
      if (t.length > max) { status("that is a bit long — " + max + " characters at most."); return; }
      input.value = "";
      window.moxieBridge.sendUserTurn(t);
    }
    send.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });
  }

  try {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", injectTalkUI, { once: true });
    } else {
      injectTalkUI();
    }
  } catch (e) {}

  /* THE HONESTY GUARD (`mode.js`:29-35). This flag is what tells `mode.js` that a
   * configured deployment may finally be PAINTED as live, because something is now loaded
   * that can actually use it. It is set LAST, after the wrapper is installed, so the flag
   * can never be true while the transport is only half-wired. */
  window.moxieCloudTransport = true;
})();
