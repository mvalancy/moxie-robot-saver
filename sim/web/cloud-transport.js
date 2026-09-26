/* cloud-transport.js — the live HTTP turn: one typed sentence in, Moxie's own voice out.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2-3.5, §4.5, §6.
 *
 * When `window.moxieMode` says this deployment has a live brain, a child's turn goes to
 * same-origin `POST /api/chat` + `POST /api/speech` instead of `stub.js`, and the payloads
 * are handed to the SAME `route()` a live MQTT bus and a recorded session drive.
 *
 * A WRAPPER (§3.5): `bridge.js` and `audio.js` are not modified. Every `window.moxieBridge`
 * member passes through untouched except `sendUserTurn` and `isLive` (and additive
 * members), so the bridge/audio suites stay green by construction. A CONNECTED MQTT BROKER
 * ALWAYS WINS: a self-hoster's supervisor gets the turn.
 *
 * ONE VOICE (§3.4): with no MQTT client, `bridge.js::speakLocally` speaks immediately unless
 * `cloudVoice` is already latched (by `handleTts`). So the fix is ORDERING:
 *   1. POST /api/chat  -> the chat message plus a speech ticket;
 *   2. POST /api/speech immediately;
 *   3. route the chat message when EITHER the speech reply lands (TTS routed FIRST) or
 *      `SPEECH_WAIT_MS` elapses. Late TTS is dropped if a local voice is already speaking.
 *
 * No secret and no hostname here: the base is `moxieMode.apiBase()` (= location.origin).
 * `ticket` and `context` are opaque, signed server-side, and die with the tab (§2.6).
 */
(function () {
  "use strict";

  // §3.4's client-side ceiling on how long the words wait for the voice.
  var SPEECH_WAIT_MS = 2500;
  // Client ceilings ABOVE the server's own (20 s / 12 s), so its honest 504 `timeout`
  // envelope wins the race and the page learns WHY.
  var CHAT_FETCH_MS = 25000;
  var SPEECH_FETCH_MS = 15000;
  // The pause before a fallback reply, matching bridge.js's own 450 ms beat.
  var FALLBACK_MS = 450;

  var inner = window.moxieBridge;
  // Additive: with no bridge.js this does nothing rather than half-wiring a page.
  if (!inner || typeof inner.sendUserTurn !== "function" || typeof inner.route !== "function") return;

  /* The topic the local echo rides. `route()` dispatches on the topic SUFFIX only, so the
   * device segment is identity, not routing; `d_sim` is what the browser SIM publishes as. */
  var USER_TOPIC = "/devices/d_sim/events/remote-chat";

  /* The signed conversation blob (§3.3): opaque, capped server-side, re-minted every turn,
   * gone when the tab closes. */
  var contextBlob = "";

  /* What this transport RECORDED; tests assert on this, never live timing (rule 11). */
  var stats = {
    turns: 0, live: 0, delegated: 0, fallbacks: 0,
    scripted: 0,             // consolation lines the PAGE chose (mic.js's degraded turn)
    scriptedFree: 0,         // ...of those, the ones a live page answered for FREE
    chatOk: 0, diagrams: 0, cited: 0, chatRefused: 0, chatErrors: 0,
    speechOk: 0, speechRefused: 0, speechErrors: 0,
    voiceFirst: 0,           // the TTS message was routed BEFORE the chat message
    chatFirst: 0,            // the 2.5 s wait elapsed, so the words went out alone
    lateSpeechDropped: 0,    // TTS arrived after the local voice had already started
    lateSpeechPlayed: 0,     // TTS arrived late but nothing was speaking, so it played
    blocked: 0,
    botTokens: 0,            // sends that carried a fresh Turnstile token
    botUnavailable: 0,       // sends REFUSED locally because no token could be minted
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

  /** Echo the child's turn locally (transcript row + `listen` SFX). NOT
   *  `inner.sendUserTurn`: that also fires the offline stub and Moxie would answer twice. */
  function echoUser(text) {
    inner.route(USER_TOPIC, JSON.stringify({ command: "prompt", backend: "router", speech: text }));
  }

  /** The degraded answer for ONE turn: `stub.js` after the bridge's 450 ms beat. The turn
   *  is already echoed, so not via `inner.sendUserTurn`. */
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
        // Voice first: `handleTts` latches `cloudVoice`, so `speakLocally` stays silent and
        // bubble and audio land together.
        routeAll(tts, "tts");
        routeAll(chatMessages, "chat");
        stats.voiceFirst++;
        return;
      }
        // No voice in time: the words go out alone and speak from the clip/browser voice.
      routeAll(chatMessages, "chat");
      stats.chatFirst++;
      if (!waited) return;      // the speech promise settled without producing audio
      return speech.then(function () {
        if (!tts) return;
        // Late audio: drop it if a local voice is already speaking (the double voice).
        // From the second turn `cloudVoice` is latched and nothing is speaking, so play it.
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

  /* ---- the bot control ---------------------------------------------------- *
   * `turnstile.js` owns the widget and hands back one promise of a string:
   *   ""      — not enforced here (fork, preview, local page, or turnstile.js not loaded):
   *             send as-is;
   *   "<tok>" — a fresh single-use token for THIS send;
   *   null    — enforcement on, no token: DO NOT SEND.
   * A missing module is the `""` case: the control lives on the SERVER.
   */
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

  /* What Moxie says when a token could not be minted. Never a silent dead Send: the child's
   * line is already echoed, she answers through the same `route()` a real reply takes, and
   * NO REQUEST IS MADE. */
  var BOT_LINE = "Hmm, my visitor check did not answer just now. Try me once more!";

  /* CONSECUTIVE local token failures, reset by the first send that gets a token (or an
   * unenforced deployment) — what matters is whether the widget works NOW. */
  var botStrikes = 0;

  /**
   * No token could be minted, so nothing was sent. Degrades like every other failure:
   *   · EVERY failure is a transport strike — the same 3-strike degrade an unreachable
   *     gateway uses (§6.3), so the badge stops saying LIVE over a page that cannot send;
   *   · the FIRST failure says Moxie's honest "try me once more" (true for a one-off, and
   *     turnstile.js no longer memoises a failed load);
   *   · from the SECOND consecutive failure the turn is answered from `stub.js` rather than
   *     repeating the same sentence; with no stub the honest line is still spoken.
   * No request is made and nothing is spent.
   */
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
    stats.order.push("stub");
    inner.route("/devices/d_sim/commands/remote_chat", JSON.stringify({
      command: "remote_chat", result: "OK", backend: "router",
      output: { text: BOT_LINE, markup: MK_MOOD + MK_SHRUG + BOT_LINE },
    }));
    return Promise.resolve();
  }

  /* ---- the live turn ----------------------------------------------------- */
  function liveTurn(text) {
    stats.live++;
    status("thinking…");
    // …and show it with her face and arms (the status line is text a child won't read).
    // No-op for the first THINK_DELAY_MS, so a fast turn never flashes a pose.
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
    // Cloudflare's own form-field name (every Turnstile example verifies it; the other half
    // is `_lib/turnstile.js::TOKEN_FIELD`). Absent when there is no control, so an
    // unenforced deployment sends byte-identically.
    if (token) payload["cf-turnstile-response"] = token;
    return post("/api/chat", payload, CHAT_FETCH_MS).then(function (res) {
      /* The wait is over, whatever the outcome: cleared once here, on the single path every
       * answer and refusal returns through, so a thinking pose never outlives its turn. */
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
        /* A refused blob would otherwise be resent every turn (it is only replaced on
         * success), so drop it on `bad_request`: one bad turn instead of a permanent wedge. */
        if (body.reason === "bad_request") contextBlob = "";
        var m2 = mode();
        status((m2 && m2.message && m2.message()) || "answering from her recorded lines.");
        if (body.messages && body.messages.length) { routeAll(body.messages, "chat"); return; }
        return fallbackReply(text);
      }

      stats.chatOk++;
      status("");
      /* She cites her source: `cited` is "<title>|<path>" for a corpus doc, linked into the
       * docs explorer so a visitor can read what she paraphrased. */
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
      var ticket = body.speech && body.speech[0] && body.speech[0].ticket;
      if (!ticket) {
        // No voice configured (`voice: false`): the words speak from the clips.
        routeAll(body.messages, "chat");
        return;
      }
      return voiceFirst(body.messages, ticket);
    });
  }

  /* ---- the wrapped surface (§3.5) ---------------------------------------- */
  window.moxieBridge = Object.assign({}, inner, {
    /**
     * A child's turn, in priority order:
     *   1. connected MQTT broker -> `inner.sendUserTurn`, untouched;
     *   2. mode `live`, transport loaded, no open `Retry-After` -> the HTTP turn;
     *   3. anything else -> `inner.sendUserTurn` (echo + `stub.js`).
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
     * A line the PAGE chose (mic.js's scripted consolation), not words a visitor said — so it
     * must cost nothing. Via `sendUserTurn` it used to buy a full chat + speech turn on every
     * refusal that changes no mode. Same ordering, middle path replaced:
     *   1. connected MQTT broker -> still gets it (a self-hoster's own backend);
     *   2. nothing spendable -> `inner.sendUserTurn` (`stub.js`, free);
     *   3. LIVE page -> local echo + stub answer, same beat, and NOT ONE REQUEST.
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

    /** What the transport RECORDED (additive to the bridge's surface). */
    transportStats: function () { return JSON.parse(JSON.stringify(stats)); },
  });

  /* ---- the typed turn, and the ONE control that carries it ---------------- *
   * A typed line is a spoken line without the STT leg: the same `sendUserTurn` call mic.js
   * makes, with no second copy of the flow here.
   *
   * `#speech-input` + `#speech-btn` ("Say") speak text through the LOCAL Piper sidecar. On a
   * hosted deployment that sidecar cannot exist and the CSP refuses it, so the most obvious
   * box on the page was a dead control. So when local Piper is NOT available (env.js decides,
   * via mode.js and its sidecar probe — never the hostname), env.js calls `adopt()` and the
   * box becomes the typed turn ("Ask"). With a real Piper it is untouched: local engines
   * stay first-class.
   *
   * The injected `#chat-sub` box is the fallback for pages with no `#speech-input`. Exactly
   * one typed control is ever visible, and `#chat-status` moves under the one in use.
   */
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

  /**
   * The one typed path. It inherits the spend story: `sendUserTurn` goes live only when
   * `canSpendLiveTurn()`, through the same server `admit()` the microphone passes; otherwise
   * `stub.js`, free. Only text a human typed reaches it (invented lines use
   * `sendScriptedTurn`).
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
   * Hand `#speech-input` / `#speech-btn` the typed turn. ONE-WAY: env.js calls it once,
   * after its sidecar probe resolves, so `adopt(false)` is only a query. The existing
   * listeners (moxie.js `setSpeech`, sim.html `wireAudio`) are not removed — they check
   * `moxieTypedTurn.adopted()` and stand down; replacing nodes would break the phrase chips.
   *
   * @returns {boolean} whether this page has such a control at all.
   */
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

  /* The injected fallback box, for pages with no `#speech-input` to adopt (and the unit
   * test). Works in every mode. Injected here so the control exists exactly when the
   * transport does. */
  function injectTalkUI() {
    if (adopted) return;                        // the page already has a typed control
    if (document.getElementById("chat-send")) return;
    var mic = document.getElementById("mic-btn");
    var host = mic && mic.closest ? mic.closest("section.sub") : null;
    /* The composer dock (#chat-dock) must hold exactly ONE text box, so when the mic lives
     * there this box goes to the rail beside the voice controls. Only a self-hosted page with
     * a live Piper sidecar (whose #speech-input cannot be adopted) reaches this. */
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
