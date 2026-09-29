/* bridge/index.js — the connection, the message router, and window.moxieBridge.
 * Loads LAST of the bridge parts (see bridge/core.js for why they are classic scripts). */
(function () {
  "use strict";
  const B = window.__moxieBridge;
  const dev = B.dev, status = (t) => B.status(t), nowMs = B.nowMs, rec = B.rec;

  // ---- voice arbitration ----
  // On a live link the reply text arrives a beat before its CloudTTSResponse: hold the local
  // voice for a short grace window and drop it the moment real audio lands. Off the bus
  // there is no server voice, so speak immediately. A streamed answer is several chunks of
  // one event_id: the grace timer ACCUMULATES them, so they are spoken as one line.
  const TTS_GRACE_MS = 900;
  let cloudVoice = false, pendingSpeak = 0, pendingText = "";

  function speakLocally(text) {
    if (!window.moxieAudio || cloudVoice) return;
    if (!B.isLive()) { window.moxieAudio.speak(text); return; }
    pendingText = pendingText ? pendingText + " " + text : text;
    clearTimeout(pendingSpeak);
    pendingSpeak = setTimeout(() => {
      pendingSpeak = 0;
      const say = pendingText; pendingText = "";
      if (!cloudVoice) window.moxieAudio.speak(say);
    }, TTS_GRACE_MS);
  }

  function cancelPendingSpeak() {
    if (pendingSpeak) { clearTimeout(pendingSpeak); pendingSpeak = 0; }
    pendingText = "";
  }

  // A CloudTTSResponse: `{audio:{buffer(base64 PCM),channels,sample_rate}, marks[],
  // event_id, chunk_num}`. voice/ decodes the wire itself (like firmware) and plays it;
  // here we only route + arbitrate.
  function handleTts(payload) {
    const msg = B.parse(payload); if (!msg) return;
    cloudVoice = true;                       // server voice wins from now on
    cancelPendingSpeak();
    if (!window.moxieAudio || !window.moxieAudio.playCloudTTS) return;
    window.moxieAudio.playCloudTTS(msg);
  }

  // One turn can answer with several responses sharing an event_id (filler, then the answer
  // streamed per sentence; mqtt-and-conversation.md §4.5). Audio ordering is voice/'s job;
  // here the transcript and bubble read as ONE turn.
  let chatEvent = null, chatSaid = "";

  function handleRemoteChat(payload) {
    const msg = B.parse(payload); if (!msg) return;
    const out = msg.output || {};
    const text = out.text || "";
    const eid = msg.event_id || "";
    B.noteFaceReply(eid, text);
    const more = typeof msg.chunk_num === "number" && msg.chunk_num > 0 &&
                 eid !== "" && eid === chatEvent;
    chatEvent = eid;
    chatSaid = more && chatSaid ? chatSaid + " " + text : text;
    if (text) window.moxie && window.moxie.setSpeech(chatSaid);
    // an emotion field (if the server tags one) wins for the face
    if (typeof msg.emotion === "number" && B.EMOTION_TO_FACE[msg.emotion])
      window.moxie && window.moxie.setFace(B.EMOTION_TO_FACE[msg.emotion]);
    B.applyMarkup(out.markup || "");
    // Actions AFTER the markup on purpose: the markup performs the line, the action is
    // what happens next (launch/exit/sleep).
    B.handleActions(msg);
    if (text) {
      status(`said: "${chatSaid.slice(0, 48)}"`);
      addTranscript("moxie", text, more);
      speakLocally(text);            // stands down if the server sends real audio
    }
  }

  // ---- telehealth ("Be Moxie"): the operator drives the body ----
  // A `TelehealthRobotCommand` carries the same Output markup a brain reply does
  // (protocol/telehealth.md), so PLAY_OUTPUT reuses handleRemoteChat and the avatar cannot
  // tell them apart. INTERRUPT (operator barge-in) stops the voice and clears the bubble —
  // our protocol page's reading; real-robot behavior is unobserved.
  const telehealth = { lines: [], interrupts: 0, session_id: "", last_action: "" };

  function handleTelehealth(payload) {
    const msg = B.parse(payload); if (!msg) return;
    const m = msg.message || msg;
    const action = String(m.action || "");
    telehealth.last_action = action;
    if (m.session_id) telehealth.session_id = m.session_id;
    if (action === "INTERRUPT") {
      telehealth.interrupts += 1;
      if (window.moxieAudio && window.moxieAudio.stop) window.moxieAudio.stop();
      cancelPendingSpeak();
      window.moxie && window.moxie.setSpeech("");
      status("telehealth: interrupted");
      return;
    }
    // Report our own RobotState upstream as the SIL robot does (`virtual_moxie.py::
    // _on_telehealth`): START_SESSION → IN_SESSION, END_SESSION → EXITING then READY.
    if (action === "START_SESSION") B.reportTelehealthState("IN_SESSION", m.session_id || "");
    else if (action === "END_SESSION") {
      B.reportTelehealthState("EXITING", m.session_id || "");
      B.reportTelehealthState("READY", "");
    }
    if (action !== "PLAY_OUTPUT") {
      status(`telehealth: ${action.toLowerCase().replace(/_/g, " ")}`);
      return;
    }
    const out = m.output || {};
    telehealth.lines.push({ text: out.text || "", markup: out.markup || "",
                            session_id: m.session_id || "", t: nowMs() });
    // `session_id` stands in for `event_id`, so consecutive operator lines are separate
    // utterances (telehealth never streams).
    handleRemoteChat(JSON.stringify({
      command: "remote_chat", event_id: m.session_id || "", output: out }));
  }

  // ---- the child's side ----
  function addTranscript(role, text, append) {
    const el = document.getElementById("transcript"); if (!el || !text) return;
    if (append) {                         // a later chunk of the same turn
      const rows = el.querySelectorAll(".turn.moxie");
      const last = rows[rows.length - 1];
      const msg = last && last.querySelector(".msg");
      if (msg) { msg.textContent += " " + text; el.scrollTop = el.scrollHeight; return; }
    }
    const row = document.createElement("div");
    row.className = "turn " + (role === "moxie" ? "moxie" : "user");
    row.innerHTML = `<span class="who">${role === "moxie" ? "Moxie" : "Child"}</span>` +
                    `<span class="msg"></span>`;
    row.querySelector(".msg").textContent = text;   // textContent = XSS-safe
    el.appendChild(row);
    el.scrollTop = el.scrollHeight;
  }

  function handleUserTurn(payload) {
    const msg = B.parse(payload); if (!msg) return;
    // 'notify' turns are the robot echoing what it said — skip (avoid dupes).
    if (msg.command === "notify") return;
    let speech = msg.speech || "";
    for (const ln of msg.extra_lines || [])
      if (ln.context_type === "input" && ln.text) speech = ln.text;
    // A perception event rides the `speech` slot — it is Moxie's eye, not the child's
    // voice, so it updates presence and never enters the comms log.
    if (B.notePresence(speech)) { if (msg.event_id) B.pendingFaceEvents.add(msg.event_id); return; }
    if (!speech) return;
    addTranscript("user", speech);
    if (!window.moxieAudio) return;
    window.moxieAudio.sfx("listen");
    /* ...and let the child be HEARD, from a clip shipped for that exact sentence or not at
     * all: the scripted demo lines and mic.js's fallback speak, a visitor's own words are
     * never read back at them. NOT gated on `replaying` (that would mute mic.js's
     * fallback); voice/local.js::speakClipOnly has the ordering rule. */
    if (window.moxieAudio.speakClipOnly) window.moxieAudio.speakClipOnly(speech, "child");
  }

  // SIL-only motor channel. The real robot's motion is markup-driven on-device (there is
  // no cloud motor stream); this lets a scenario/test/recording drive the 7 DOFs directly.
  // Payload: {"motors":{"0":30000,"2":30000}} or {"index":4,"value":24000}.
  function handleMotor(s) {
    const msg = B.parse(s); if (!msg) return;
    const m = window.moxie; if (!m || !m.setMotor) return;
    if (msg.motors && typeof msg.motors === "object")
      for (const [i, v] of Object.entries(msg.motors)) m.setMotor(+i, +v);
    else if (typeof msg.index === "number" && typeof msg.value === "number")
      m.setMotor(msg.index, msg.value);
    status(`motors ${JSON.stringify(msg.motors || { [msg.index]: msg.value })}`);
  }

  // ---- routing + connection ----
  // Shared by the live client and by replay, so a recorded session drives the exact same
  // handlers.
  function route(topic, s) {
    B.record(topic, s);
    if (topic.endsWith("/commands/remote_chat")) handleRemoteChat(s);
    else if (topic.endsWith("/commands/tts")) handleTts(s);
    else if (topic.endsWith("/commands/telehealth")) handleTelehealth(s);
    else if (topic.endsWith("/events/remote-chat")) handleUserTurn(s);
    else if (topic.endsWith("/commands/query_result")) B.handleQueryResult(s);
    else if (topic.endsWith("/commands/motor")) handleMotor(s);
    else if (topic.endsWith("/config")) {
      const c = B.parse(s); if (c) status(`config: pairing_status=${c.pairing_status}`);
    }
  }
  B.route = route;

  /* mqtt.js (322 KB) is fetched on the first Link, not on every visit: only the live bus
   * uses it, and almost no visitor presses Link. Same origin, so script-src 'self' covers it. */
  let mqttLoading = null;
  function loadMqtt() {
    if (typeof mqtt !== "undefined") return Promise.resolve(true);
    return mqttLoading = mqttLoading || new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "vendor/mqtt.min.js";
      s.onload = () => resolve(typeof mqtt !== "undefined");
      s.onerror = () => { mqttLoading = null; resolve(false); };
      document.head.appendChild(s);
    });
  }

  function connect(host, port) {
    if (typeof mqtt === "undefined") {
      status("loading mqtt.js…");
      loadMqtt().then((ok) => ok ? connect(host, port) : status("mqtt.js not loaded"));
      return;
    }
    if (B.client) { try { B.client.end(true); } catch {} B.client = null; }
    const url = `ws://${host}:${port}`;
    status(`connecting ${url}…`);
    const client = B.client = mqtt.connect(url, { reconnectPeriod: 3000, connectTimeout: 8000 });
    client.on("connect", () => {
      status(`● live on ${url}`);
      if (window.moxieAudio) window.moxieAudio.sfx("connect");
      client.subscribe("/devices/+/commands/remote_chat");   // Moxie's replies
      client.subscribe("/devices/+/commands/tts");           // the server voice (CloudTTSResponse)
      client.subscribe("/devices/+/events/remote-chat");     // the child's utterances
      client.subscribe("/devices/+/config");
      client.subscribe("/devices/+/commands/telehealth");     // the operator's lines
      client.subscribe("/devices/+/commands/query_result");   // answers to our activity-log queries
      client.subscribe("/devices/+/commands/motor");         // SIL-only: drive motors directly
      // A real robot pulls its day at the start of EVERY session (mqtt-and-conversation.md §3.8).
      B.sendQuery("schedule");
    });
    client.on("reconnect", () => status(`reconnecting ${url}…`));
    client.on("error", (e) => status(`error: ${e && e.message ? e.message : e}`));
    client.on("close", () => { status(`○ disconnected`);
      if (window.moxieAudio) window.moxieAudio.sfx("disconnect"); });
    client.on("message", (topic, payload) => route(topic, payload.toString()));
  }

  // ---- record / replay ----
  function setRecording(on) {
    rec.recording = on;
    if (on) rec.recorded = [];
    status(on ? "● recording…" : `recorded ${rec.recorded.length} events`);
  }
  function replay(session, speed) {
    if (!Array.isArray(session) || !session.length) { status("empty session"); return; }
    speed = speed || 1; rec.replaying = true;
    const t0 = session[0].t || 0;
    status(`▶ replaying ${session.length} events`);
    session.forEach((ev) => setTimeout(() => route(ev.topic, ev.payload), Math.max(0, (ev.t - t0) / speed)));
    const dur = ((session[session.length - 1].t - t0) / speed) + 200;
    // test_sil_child_voice.py waits on this exact "replay done" line.
    setTimeout(() => { rec.replaying = false; status(`replay done (${session.length} events)`); }, dur);
  }
  function exportSession() {
    const blob = new Blob([JSON.stringify(rec.recorded, null, 0)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "moxie-session.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---- the public surface (mic.js, mode.js, cloud-transport.js, the tests) ----
  window.moxieBridge = Object.assign(B.api, {
    loadMqtt,          // the lazy vendor/mqtt.min.js fetch connect() waits on
    route: route,
    // Inject a child utterance onto the live bus (the real backend answers) or, offline,
    // answer with the stub brain in the SAME reply shape so the avatar animates either way.
    sendUserTurn: function (text) {
      const payload = JSON.stringify({ command: "prompt", backend: "router", speech: text });
      const live = B.isLive();
      if (live) B.client.publish(dev("events/remote-chat"), payload);
      route(dev("events/remote-chat"), payload);   // always show it locally
      if (!live && window.moxieStub && window.moxieStub.enabled) {
        const r = window.moxieStub.reply(text);
        setTimeout(() => route(dev("commands/remote_chat"), JSON.stringify(
          { command: "remote_chat", result: "OK", backend: "router",
            output: { text: r.text, markup: r.markup } })), 450);
      }
    },
    isLive: B.isLive,
    // What the telehealth channel delivered, recorded as it happened. Tests assert this,
    // never a live sample.
    telehealthStats: function () {
      return { lines: telehealth.lines.slice(), interrupts: telehealth.interrupts,
               session_id: telehealth.session_id, last_action: telehealth.last_action };
    },
    // true once a CloudTTSResponse has arrived — the server voice has taken over
    hasCloudVoice: function () { return cloudVoice; },
  });

  // ---- wire the panel once moxie + DOM are ready ----
  function wire(id, fn) { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); }
  function initUI() {
    const host = document.getElementById("bus-host");
    const btn = document.getElementById("bus-connect");
    if (host && !host.value) host.value = location.hostname || "127.0.0.1";
    if (btn) btn.addEventListener("click", () => connect(host.value.trim() || "127.0.0.1", 9001));
    wire("presence-toggle", () => B.faceEvent(B.presence.present ? "lost" : "found"));
    B.presenceBadge();
    wire("rec-toggle", () => setRecording(!rec.recording));
    wire("rec-save", () => exportSession());
    wire("rec-demo", async () => {
      try { const r = await fetch("sessions/demo.json"); replay(await r.json(), 1); }
      catch (e) { status("demo load failed: " + e); }
    });
    const loader = document.getElementById("rec-load");
    if (loader) loader.addEventListener("change", (e) => {
      const f = e.target.files && e.target.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = () => { try { replay(JSON.parse(rd.result), 1); } catch (err) { status("bad session file"); } };
      rd.readAsText(f);
    });
  }
  if (window.moxie) initUI();
  else window.addEventListener("moxie-ready", initUI, { once: true });
})();
