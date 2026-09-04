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
    scripted: 0,             // consolation lines the PAGE chose (mic.js's degraded turn)
    scriptedFree: 0,         // ...of those, the ones a live page answered for FREE
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

    /**
     * A line the PAGE chose, not words a visitor said — and therefore a line that must
     * cost nothing.
     *
     * `mic.js` consoles a visitor whose clip was too long, or whose transcription was
     * refused or failed, with a SCRIPTED CHILD LINE so the conversation still runs and the
     * button is never dead (spec §6). Nobody spoke those words. Routed through
     * `sendUserTurn` — which is what it did before — that consolation bought a full
     * `POST /api/chat` and `POST /api/speech` out of a budget the whole demo shares, on
     * every path where a refusal changes no mode: `bad_request`, `too_long`, `too_short`,
     * the client-side over-size gate that never even uploaded, and the first two of the
     * three transport errors it takes to degrade the page. (`rate_limited`, `at_capacity`,
     * `budget_exhausted` and `upstream_down` were free only by accident — they happen to
     * shut `canSpendLiveTurn()` on their way past `mode.js`.)
     *
     * Same three-way ordering as `sendUserTurn`, with the middle one replaced:
     *   1. A connected MQTT broker still gets it. That is a self-hoster's OWN backend, it
     *      costs this demo nothing, and it is the behaviour they have today.
     *   2. Nothing spendable -> `inner.sendUserTurn`, i.e. `stub.js`, free and unchanged.
     *   3. A LIVE page -> the local echo plus the stub answer, assembled here: the same
     *      transcript row, the same child clip, the same 450 ms beat as (2), and NOT ONE
     *      REQUEST.
     *
     * The visitor cannot tell (2) and (3) apart. The gateway can.
     */
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

    /** What the transport RECORDED. Additive: every member `bridge.js` published is still
     *  present and unchanged, so nothing that reads the old surface can notice. */
    transportStats: function () { return JSON.parse(JSON.stringify(stats)); },
  });

  /* ---- the typed turn, and the ONE control that carries it ---------------- *
   * A typed line is a spoken line without the STT leg. `mic.js`:157 hands a transcript to
   * `window.moxieBridge.sendUserTurn(text)` and EVERYTHING downstream — the transcript
   * row, the chat message, the speech ticket, playback, the mouth — follows from there.
   * So the typed path is not a second flow; it is the same call with the microphone
   * removed, and there is deliberately no second copy of it in this file.
   *
   * WHICH CONTROL CARRIES IT. `sim.html` has had a text box in the Voice panel since long
   * before this transport existed: `#speech-input` + `#speech-btn` ("Say"), whose job is
   * to make MOXIE say arbitrary text through the LOCAL Piper sidecar on :8081. On a hosted
   * deployment that sidecar cannot exist, and this site's own CSP (`connect-src 'self'`,
   * `sim/web/_headers`) correctly refuses the request — so a visitor typed a sentence into
   * the most obvious box on the page, pressed the button, and got silence plus a console
   * error. Measured in Chrome against the live site on 2026-09-03:
   *
   *     apiCalls: only /api/health      audioDecoded: 0   audioStarted: 0
   *     Refused to connect to 'https://…:8081/tts?text=…' — connect-src 'self'
   *
   * `env.js` already MARKED that button `needs-backend`, but a mark is a tooltip and a
   * half-opacity: the button stayed fully clickable and silently failed. A dead control
   * that looks alive is worse than one that is visibly unavailable.
   *
   * So when the local Piper voice is NOT available — and only then — `env.js` calls
   * `adopt()` below and that box becomes the typed turn: "Say" becomes "Ask", and the
   * line goes to Moxie instead of through a sidecar that is not there. When a real Piper
   * IS reachable the button is untouched and behaves exactly as it always has; the owner's
   * standing rule is that the local engines stay first-class options, and this is
   * additive, never a replacement. THE MODE DECIDES, NOT THE HOSTNAME: `env.js` asks
   * `mode.js` and its own sidecar probe, and hands the answer here.
   *
   * The injected `#chat-sub` box below remains the fallback for any page that has no
   * `#speech-input` to adopt (and for the transport's own unit test). Exactly one typed
   * control is ever visible: adopting hides the injected one and moves `#chat-status`
   * across, so the "thinking…" line and every refusal still land under the control the
   * visitor actually used.
   */
  var talkSec = null;        // the injected "Talk" section, when one had to be made
  var adopted = false;       // true once #speech-input/#speech-btn carry the typed turn

  /** The client-side ceiling on one line. `§4.1` enforces it server-side too, on purpose;
   *  this half exists so the page can say WHY before it spends a request — an over-long
   *  line must never reach `admit()` at all. */
  function maxChars() {
    var m = mode();
    var lim = (m && m.limits) ? m.limits() : {};
    var n = Number(lim && lim.max_input_chars);
    return (isFinite(n) && n > 0) ? n : 500;
  }

  /**
   * The one typed path, shared by whichever control is carrying it.
   *
   * It inherits the whole spend story for free: `sendUserTurn` above sends a turn to the
   * live brain ONLY when `canSpendLiveTurn()` says so, and the server's `admit()` — origin
   * pin, per-IP window, budget, the bounded FIFO queue — is the same gate the microphone
   * passes. Typing is not a cheaper way to spend the gateway than speaking.
   *
   * When the page is NOT live it falls through `inner.sendUserTurn` to `stub.js`, which
   * costs nothing and is the scripted behaviour the site has today. Note what it does NOT
   * do: it never invents a line — the only text that reaches `sendUserTurn` from here is
   * text a human typed. `mic.js`'s degraded path publishes a line the page invented, and
   * used to publish it through this same call, which on a live page spent a full chat +
   * speech turn on words the visitor never said; it goes through `sendScriptedTurn` above
   * now, which is the same rule this function has always followed, written down.
   *
   * @returns {boolean} whether the line was sent.
   */
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

  /**
   * Hand `#speech-input` / `#speech-btn` the typed turn.
   *
   * ONE-WAY ON PURPOSE. The only input that can change the answer is `env.js`'s sidecar
   * probe, which resolves exactly once per session, and `env.js` withholds the call until
   * it has resolved — so there is no "un-adopt" to get wrong, and no window in which the
   * button's label and its behaviour could disagree. `adopt(false)` is therefore a query,
   * not a command.
   *
   * The two existing listeners on those elements (`moxie.js`'s `setSpeech`, `sim.html`'s
   * `wireAudio`) are NOT removed — they ask `moxieTypedTurn.adopted()` and stand down.
   * That is deliberately explicit rather than clever: replacing the nodes would silently
   * break `sim.html`'s phrase chips, which hold a reference to the input, and a
   * capture-phase interceptor would make the page's behaviour depend on listener ordering.
   *
   * @returns {boolean} whether this page has such a control at all.
   */
  function adoptSpeechControl() {
    if (adopted) return true;
    var btn = document.getElementById("speech-btn");
    var inp = document.getElementById("speech-input");
    if (!btn || !inp) return false;

    var sec = btn.closest ? btn.closest("section.sub") : null;
    /* The status line follows the control: "thinking…", every refusal reason and the
     * over-long warning have to appear under the box the visitor actually used. Note the
     * ORDER this runs in — `env.js` renders synchronously while the document is still
     * parsing, so adoption normally happens BEFORE `injectTalkUI` would have made a
     * `#chat-status` at all. Hence `ensureStatus`, and hence `injectTalkUI`'s early-out:
     * when this control was adopted, the duplicate box is never built in the first place. */
    ensureStatus(sec);
    // ...and if one was already built (a page that adopts late), it goes away, so the
    // panel never shows two text inputs that look like they do the same thing. It did, on
    // the live site, and the visitor reliably tried the top one — the dead one.
    if (talkSec) talkSec.hidden = true;

    btn.textContent = "Ask";
    btn.setAttribute("title",
      "Sends your line to Moxie — she answers here. (Speaking arbitrary text needs the local Piper server.)");
    btn.removeAttribute("disabled");
    btn.disabled = false;
    inp.setAttribute("placeholder", "say something to moxie…");
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

  /* The injected fallback box. Kept for any page with no `#speech-input` to adopt — and
   * it is what the transport's own unit test drives. It works in EVERY mode: with no live
   * brain the turn goes to `stub.js` through `inner.sendUserTurn`.
   *
   * It is injected from here rather than added to `sim.html` for two reasons: it keeps
   * `sim.html`'s edit to the single `<script>` tag §9's file table budgets, and it means
   * the control exists exactly when the transport does. `env.js` already established this
   * pattern — it injects the env badge and the banner the same way. */
  function injectTalkUI() {
    if (adopted) return;                        // the page already has a typed control
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

  /* The seam `env.js` uses to hand the typed turn a control. Published BEFORE the honesty
   * guard below for the same reason that flag is set last: nothing may be able to adopt a
   * control while the transport is only half-wired. */
  window.moxieTypedTurn = {
    adopt: function (on) { return on === false ? adopted : adoptSpeechControl(); },
    adopted: function () { return adopted; },
    send: sendTyped,
    maxChars: maxChars,
  };

  /* THE HONESTY GUARD (`mode.js`:29-35). This flag is what tells `mode.js` that a
   * configured deployment may finally be PAINTED as live, because something is now loaded
   * that can actually use it. It is set LAST, after the wrapper is installed, so the flag
   * can never be true while the transport is only half-wired. */
  window.moxieCloudTransport = true;
})();
