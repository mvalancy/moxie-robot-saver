/* bridge.js — live MQTT bus → window.moxie.
 *
 * Connects the 3D Moxie (moxie.js) to the same MQTT topics a real robot sees,
 * over WebSocket (broker `listener 9001 / protocol websockets`). It watches the
 * server's replies on `/devices/+/commands/remote_chat`, speaks the text, and
 * animates the avatar from the behavior markup — the exact `<mark cmd:…>` verbs
 * documented in docs/reverse-engineering/behavior-markup.md.
 *
 * Classic script (uses the global `mqtt` from mqtt.js). No build step.
 */
(function () {
  "use strict";

  // ---- markup → avatar mappings (heuristic where the enum isn't fully RE'd) ----

  // EmotionState (robotbrain RemoteChat): 0 unknown,1 sadness,2 joy,3 love,
  // 4 anger,5 fear,6 surprise,7 neutral  → our 6 face expressions.
  const EMOTION_TO_FACE = {
    1: "sad", 2: "happy", 3: "happy", 4: "sad",
    5: "surprised", 6: "surprised", 7: "neutral",
  };
  // cmd:playback-mood `mood` int → SIL face. AUTHORITATIVE ePlaybackMood enum
  // (recovered from Assembly-CSharp — see behavior-markup.md). The SIL now renders
  // all 11 Bht_Eyeseme_* expressions, so this maps 1:1.
  const MOOD_TO_FACE = {
    0: "neutral", 1: "happy", 2: "sad", 3: "angry", 4: "shy", 5: "surprised",
    6: "afraid", 7: "concerned", 8: "confused", 9: "curious", 10: "embarrassed",
  };

  const C = 16384, MAX = 32767;      // motor rest / range (MOTOR_MAX_POS)

  // Motor indices: 0 L-shoulder, 1 L-elbow, 2 R-shoulder, 3 R-elbow, 4 head, 5 body-yaw, 6 body-lean.
  const set = (i, v) => window.moxie && window.moxie.setMotor(i, Math.max(0, Math.min(MAX, v)));
  const armsHome = () => { for (const i of [0, 1, 2, 3]) set(i, C); };
  const home = () => { for (let i = 0; i < 7; i++) set(i, C); };

  // A gesture = a short arm pose, then ease back to centre (the app's Gesture_* set).
  function gesture(name) {
    const m = window.moxie; if (!m) return;
    switch (name) {
      case "Gesture_Celebrate":
        set(0, 30000); set(2, 30000); set(1, 24000); set(3, 24000);
        m.setFace("happy"); setTimeout(armsHome, 1600); break;
      case "Gesture_Question":
      case "Gesture_Think":
      case "Gesture_Think_Subtle":
        set(2, 24000); set(3, 8000);            // right hand up near face
        m.setFace("thinking"); setTimeout(armsHome, 1800); break;
      case "Gesture_Point":
      case "Gesture_Point_Right":
        set(2, 26000); set(3, 30000); setTimeout(armsHome, 1400); break;
      case "Gesture_Self":                       // hand to own chest
        set(2, 18000); set(3, 4000); setTimeout(armsHome, 1400); break;
      case "Gesture_Large":                      // both arms wide open
        set(0, 26000); set(2, 26000); set(1, 30000); set(3, 30000); setTimeout(armsHome, 1500); break;
      case "Gesture_Higher": set(0, 28000); set(2, 28000); setTimeout(armsHome, 1200); break;
      case "Gesture_Lower":  set(0, 6000);  set(2, 6000);  setTimeout(armsHome, 1200); break;
      case "Gesture_Talk":   set(2, 20000); set(3, 20000); setTimeout(armsHome, 900); break;
      case "Gesture_None":   armsHome(); break;
      default: break;
    }
  }

  // Behaviour trees (Bht_*) — idle/expressive whole-body animations (the app's hardcoded set).
  function behaviourTree(name) {
    const m = window.moxie; if (!m) return;
    switch (name) {
      case "Bht_Gesture_Celebrate": return gesture("Gesture_Celebrate");
      case "Bht_Wing_Flap": {                    // flap both arms a couple times
        let up = true; const flap = (n) => { if (n <= 0) return armsHome();
          set(0, up ? 30000 : 8000); set(2, up ? 30000 : 8000); up = !up;
          setTimeout(() => flap(n - 1), 260); };
        flap(5); break;
      }
      case "Bht_Sleep_Anim":                     // droop arms + lower head
        set(0, 3000); set(2, 3000); set(4, 4000); setTimeout(home, 2500); break;
      case "Bht_Idle_Curious":                   // head tilt + slight body turn
        set(4, 24000); set(5, 22000); setTimeout(home, 1600); break;
      case "Bht_Idle_Active_Listening":          // lean in a little
        set(6, 22000); setTimeout(home, 1600); break;
      case "Bht_Active_Thinking":
      case "Bht_Vg_hmm_thinking":
        m.setFace("thinking"); set(2, 24000); set(3, 8000); setTimeout(armsHome, 1800); break;
      case "Bht_Bangle_on_off": set(1, 28000); setTimeout(armsHome, 900); break;

      // --- authoritative Bht_* trees (docs/reverse-engineering/behavior-tree-engine.md) ---
      case "Bht_Gesture_Greet": {                // friendly wave — arm up, hand wiggles
        m.setFace("happy"); set(0, 30000);
        let n = 5, out = true;
        const w = () => { if (n-- <= 0) return armsHome(); set(1, out ? 26000 : 12000); out = !out; setTimeout(w, 240); };
        w(); break;
      }
      case "Bht_Spin_360":                       // playful whole-body spin (yaw sweep both ways)
        set(5, 31000); setTimeout(() => set(5, 3000), 650); setTimeout(() => set(5, C), 1350); break;
      case "Bht_Robot_Pickup":                   // startled: arms up, surprised, head up
        m.setFace("surprised"); set(0, 27000); set(2, 27000); set(4, 26000); setTimeout(home, 2000); break;
      case "Bht_Robot_Putdown":                  // settle back to rest
        m.setFace("neutral"); set(4, 12000); setTimeout(home, 1400); break;
      case "Bht_Demo_Wake_Up":                   // wake: from droop → alert + happy
        m.setFace("sleep"); set(0, 4000); set(2, 4000); set(4, 5000);
        setTimeout(() => { m.setFace("happy"); home(); }, 1200); break;
      case "Bht_Search":                         // scan the room (yaw + head sweep)
        m.setFace("curious"); set(5, 27000); set(4, 22000);
        setTimeout(() => set(5, 7000), 900); setTimeout(home, 2000); break;
      case "Bht_Sign_off":                       // goodbye wave
        return behaviourTree("Bht_Gesture_Greet");
      case "Bht_Idle_Listening":                 // attentive lean-in + head tilt
        set(6, 20000); set(4, 20000); setTimeout(home, 1600); break;
      case "Bht_Talking_With_Gestures":
      case "Bht_Talking_Poses":
      case "Bht_Vocal_Gestures":                 // talking arm gestures (alternating)
        set(2, 22000); set(3, 17000);
        setTimeout(() => { armsHome(); set(0, 22000); set(1, 15000); }, 550);
        setTimeout(armsHome, 1500); break;
      case "Bht_Sleeping_Anim":
      case "Bht_System_Suspend":                 // go to sleep — droop + eyes shut
        m.setFace("sleep"); set(0, 3000); set(2, 3000); set(4, 4000); setTimeout(home, 2500); break;
      case "Bht_System_Resume":                  // wake back up
        m.setFace("neutral"); home(); break;

      default: break;
    }
  }

  // Parse the marks in a markup string and drive the avatar.
  function applyMarkup(markup) {
    if (!markup || !window.moxie) return;
    // mood
    const mood = /cmd:playback-mood,data:\{[^}]*?\+mood\+:(\d+)/.exec(markup);
    if (mood) { const f = MOOD_TO_FACE[+mood[1]]; if (f) window.moxie.setFace(f); }
    // gestures (eventName Gesture_*)
    const gx = /\+eventName\+:\+(Gesture_[A-Za-z_]+)\+/g; let g;
    while ((g = gx.exec(markup))) gesture(g[1]);
    // behaviour trees (behaviour Bht_*) — idle/expressive whole-body
    const bx = /\+behaviour\+:\+(Bht_[A-Za-z0-9_]+)\+/g; let b;
    while ((b = bx.exec(markup))) behaviourTree(b[1]);
    // icons-v2 → show/clear badges on the face. Each mark carries a command
    // (0 = show, 2 = clear) and up to 4 named icons (iconType:1). A turn shows
    // at the start and clears at the end; we show the union and clear ~4s later.
    const icx = /cmd:icons-v2,data:\{([\s\S]*?)\}"\/>/g; const shown = new Set();
    let hasClear = false, im;
    while ((im = icx.exec(markup))) {
      const block = im[1];
      const cmd = /\+command\+:(\d+)/.exec(block); const c = cmd ? +cmd[1] : 0;
      const vx = /\+iconType\+:1,\+value\+:\+([A-Za-z0-9_]+)\+/g; let vm;
      const vals = []; while ((vm = vx.exec(block))) vals.push(vm[1]);
      if (c === 0) vals.forEach((v) => shown.add(v));
      else if (c === 2) hasClear = true;
    }
    if (shown.size && window.moxie.showIcons) {
      window.moxie.showIcons([...shown]); status(`icons: ${[...shown].join(", ")}`);
      if (window.moxieAudio) window.moxieAudio.sfx("icon");
      if (hasClear) setTimeout(() => window.moxie.clearIcons && window.moxie.clearIcons(), 4000);
    }
  }

  function handleRemoteChat(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    const out = msg.output || {};
    const text = out.text || "";
    if (text) window.moxie && window.moxie.setSpeech(text);
    // emotion field (if the server tags one) wins for the face
    if (typeof msg.emotion === "number" && EMOTION_TO_FACE[msg.emotion])
      window.moxie && window.moxie.setFace(EMOTION_TO_FACE[msg.emotion]);
    applyMarkup(out.markup || "");
    if (text) {
      status(`💬 "${text.slice(0, 48)}"`); addTranscript("moxie", text);
      if (window.moxieAudio) window.moxieAudio.speak(text);   // Piper TTS out
    }
  }

  // ---- connection ----
  let client = null;
  function status(t) { const el = document.getElementById("bus-status"); if (el) el.textContent = t; }

  function connect(host, port) {
    if (typeof mqtt === "undefined") { status("mqtt.js not loaded"); return; }
    if (client) { try { client.end(true); } catch {} client = null; }
    const url = `ws://${host}:${port}`;
    status(`connecting ${url}…`);
    client = mqtt.connect(url, { reconnectPeriod: 3000, connectTimeout: 8000 });
    client.on("connect", () => {
      status(`● live on ${url}`);
      if (window.moxieAudio) window.moxieAudio.sfx("connect");
      client.subscribe("/devices/+/commands/remote_chat");   // Moxie's replies
      client.subscribe("/devices/+/events/remote-chat");     // the child's utterances
      client.subscribe("/devices/+/config");
      client.subscribe("/devices/+/commands/motor");         // SIL-only: drive motors directly
    });
    client.on("reconnect", () => status(`reconnecting ${url}…`));
    client.on("error", (e) => status(`error: ${e && e.message ? e.message : e}`));
    client.on("close", () => { status(`○ disconnected`);
      if (window.moxieAudio) window.moxieAudio.sfx("disconnect"); });
    client.on("message", (topic, payload) => route(topic, payload.toString()));
  }

  // Route one message to the avatar. Shared by the live client and by replay,
  // so a recorded session drives the exact same handlers.
  function route(topic, s) {
    if (recording && !replaying) recorded.push({ t: nowMs(), topic, payload: s });
    if (topic.endsWith("/commands/remote_chat")) handleRemoteChat(s);
    else if (topic.endsWith("/events/remote-chat")) handleUserTurn(s);
    else if (topic.endsWith("/commands/motor")) handleMotor(s);
    else if (topic.endsWith("/config")) {
      try { const c = JSON.parse(s); status(`config: pairing_status=${c.pairing_status}`); } catch {}
    }
  }

  // SIL-only motor channel. The real robot's motion is markup-driven on-device
  // (there is no cloud motor-position stream); this lets a scenario/test/recording
  // command the rig directly to demonstrate the 7 libmotionlib DOFs over the bus.
  // Payload: {"motors":{"0":30000,"2":30000}} or {"index":4,"value":24000}.
  function handleMotor(s) {
    let msg; try { msg = JSON.parse(s); } catch { return; }
    const m = window.moxie; if (!m || !m.setMotor) return;
    if (msg.motors && typeof msg.motors === "object")
      for (const [i, v] of Object.entries(msg.motors)) m.setMotor(+i, +v);
    else if (typeof msg.index === "number" && typeof msg.value === "number")
      m.setMotor(msg.index, msg.value);
    status(`motors ${JSON.stringify(msg.motors || { [msg.index]: msg.value })}`);
  }

  // ---- record / replay ----
  const nowMs = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
  let recorded = [], recording = false, replaying = false;
  function setRecording(on) {
    recording = on;
    if (on) recorded = [];
    status(on ? "● recording…" : `recorded ${recorded.length} events`);
  }
  function replay(session, speed) {
    if (!Array.isArray(session) || !session.length) { status("empty session"); return; }
    speed = speed || 1; replaying = true;
    const t0 = session[0].t || 0;
    status(`▶ replaying ${session.length} events`);
    session.forEach((ev) => setTimeout(() => route(ev.topic, ev.payload), Math.max(0, (ev.t - t0) / speed)));
    const dur = ((session[session.length - 1].t - t0) / speed) + 200;
    setTimeout(() => { replaying = false; status(`replay done (${session.length} events)`); }, dur);
  }
  function exportSession() {
    const blob = new Blob([JSON.stringify(recorded, null, 0)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "moxie-session.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---- transcript ----
  function addTranscript(role, text) {
    const el = document.getElementById("transcript"); if (!el || !text) return;
    const row = document.createElement("div");
    row.className = "turn " + (role === "moxie" ? "moxie" : "user");
    row.innerHTML = `<span class="who">${role === "moxie" ? "Moxie" : "Child"}</span>` +
                    `<span class="msg"></span>`;
    row.querySelector(".msg").textContent = text;   // textContent = XSS-safe
    el.appendChild(row);
    el.scrollTop = el.scrollHeight;
  }

  function handleUserTurn(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    // 'notify' turns are the robot echoing what it said — skip (avoid dupes).
    if (msg.command === "notify") return;
    let speech = msg.speech || "";
    for (const ln of msg.extra_lines || [])
      if (ln.context_type === "input" && ln.text) speech = ln.text;
    if (speech) { addTranscript("user", speech);
      if (window.moxieAudio) window.moxieAudio.sfx("listen"); }
  }

  // Public surface for other modules (mic.js): inject a child utterance either
  // onto the live bus (so the real backend answers) or locally into the avatar.
  window.moxieBridge = {
    route: route,
    sendUserTurn: function (text) {
      const payload = JSON.stringify({ command: "prompt", backend: "router", speech: text });
      const live = !!(client && client.connected);
      if (live) client.publish("/devices/d_sim/events/remote-chat", payload);
      route("/devices/d_sim/events/remote-chat", payload);   // always show it locally
      // No backend? Answer with the offline stub brain, using the SAME reply shape
      // so the avatar animates from real behavior markup either way.
      if (!live && window.moxieStub && window.moxieStub.enabled) {
        const r = window.moxieStub.reply(text);
        setTimeout(() => route("/devices/d_sim/commands/remote_chat", JSON.stringify(
          { command: "remote_chat", result: "OK", backend: "router",
            output: { text: r.text, markup: r.markup } })), 450);
      }
    },
    isLive: function () { return !!(client && client.connected); },
  };

  // ---- wire the panel once moxie + DOM are ready ----
  function wire(id, fn) { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); }
  function initUI() {
    const host = document.getElementById("bus-host");
    const btn = document.getElementById("bus-connect");
    if (host && !host.value) host.value = location.hostname || "127.0.0.1";
    if (btn) btn.addEventListener("click", () => connect(host.value.trim() || "127.0.0.1", 9001));
    // record / replay controls
    wire("rec-toggle", () => setRecording(!recording));
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
