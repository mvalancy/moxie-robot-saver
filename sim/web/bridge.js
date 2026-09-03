/* bridge.js — live MQTT bus → window.moxie.
 *
 * Connects the 3D Moxie (moxie.js) to the same MQTT topics a real robot sees,
 * over WebSocket (broker `listener 9001 / protocol websockets`). It watches the
 * server's replies on `/devices/+/commands/remote_chat`, speaks the text, and
 * animates the avatar from the behavior markup — the exact `<mark cmd:…>` verbs
 * documented in docs/reverse-engineering/behavior-markup.md.
 *
 * It also consumes the SERVER VOICE on `/devices/+/commands/tts`: a
 * `CloudTTSResponse` (AI seam ③) whose base64 PCM audio.js decodes and plays,
 * lip-syncing the face from `marks[]`. When real audio arrives the local
 * browser/Piper voice stands down, so Moxie never speaks the line twice.
 *
 * It is a robot in BOTH directions. Cloud → robot: it acts on `response_actions`
 * (`launch` / `exit` / `sleep` / `enable_qr` / `execute` + `event_subscription`), the
 * contract's way for a brain to drive navigation rather than only speak
 * (docs/architecture/ai-seam.md §2, mqtt-and-conversation.md §4.1). Robot → cloud: it
 * publishes `events/client-service-activity-log` — the schedule/history pull, the
 * `mentor_behavior` report and the telehealth state — in the same envelope the headless
 * SIL robot `sim/virtual_moxie.py` publishes, so the two SIM clients are interchangeable
 * upstream as well as down (docs/architecture/sim-as-a-client.md).
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

  // ---- this robot's identity on the bus ----
  // The browser SIM has always PUBLISHED as `d_sim` (see `faceEvent` / `sendUserTurn`);
  // naming it once lets the robot→cloud channels below share the same device id, and
  // `FIRMWARE` is the analyzed build `sim/virtual_moxie.py:40` reports in `/state`.
  const DEVICE_ID = "d_sim";
  const FIRMWARE = "24.10.803";
  const MODULE_NAME = "sim-web";     // which client this is (the SIL says "virtual-moxie")
  const dev = (name) => `/devices/${DEVICE_ID}/${name}`;

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
      case "Bht_Idle_Near_Focused":              // held gaze: lean in, head level, hold
        // INFERRED, like every other case here: no hardware has ever played our markup,
        // and the name is what we have. The behavior planner uses this tree as its
        // "hold the gaze" handle (there is no gaze verb in the 24 recovered commands),
        // so it must read as steadier and longer than Idle_Listening's tilt.
        set(6, 22000); set(4, 16384); setTimeout(home, 2400); break;
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

  // ---- 🎬 response_actions: the cloud drives navigation, not just speech --------
  //
  // A `RemoteChatResponse` may carry `response_actions` — `RemoteChatAction` records the
  // brain uses to move the robot through its own experience: launch a module, exit one,
  // go to sleep, turn QR scanning on, call a named on-robot function
  // (`mqtt/moxie_sdk/wire.py::build_chat_response`, `moxie_sdk/types.py::ActionType`,
  // docs/architecture/ai-seam.md §2). Until now NOTHING in either SIM client read them:
  // `sim/tests/test_e2e_actions_to_robot.py` proved they ARRIVE and said so in its own
  // docstring ("this deliberately does not claim the robot acts on what it received").
  // This is the client half of that contract.
  //
  // Every entry is `{output_type, action, module_id, content_id}`, and the FIRST entry may
  // instead/also carry `event_subscription:{active[], clear}` — the brain asking the robot
  // to push it perception events. An action-less entry is legal and means exactly that,
  // so "no `action` key" is not an error. A legacy singular `response_action` mirrors
  // `response_actions[0]` (mqtt-and-conversation.md §4.1), so it is read only when the
  // plural is absent — otherwise the same action would fire twice.
  //
  // NOTHING here throws. An action type we do not know is counted and skipped: a future
  // server teaching a robot a new verb must not be able to break an old client's turn.
  const ACTION_KINDS = ["launch", "exit", "sleep", "enable_qr", "execute"];
  const actionState = {
    applied: [],            // [{action, module_id, content_id, function, t}] bounded
    unknown: 0,             // action types this client does not implement (skipped safely)
    module_id: "", content_id: "",   // the module the cloud last put us in
    launches: 0, exits: 0,
    asleep: false, qr_enabled: false,
    subscribed: [],         // event_subscription.active, as the brain last asked for it
    last: "",
  };

  function applyAction(entry) {
    const m = window.moxie;
    const kind = String(entry.action || "").toLowerCase();
    const moduleId = entry.module_id || "", contentId = entry.content_id || "";
    if (ACTION_KINDS.indexOf(kind) < 0) {
      actionState.unknown += 1;
      status(`🎬 ignored unknown action ${JSON.stringify(entry.action)}`);
      return false;
    }
    switch (kind) {
      case "launch":
        // Entering an activity: the module's badge goes up on the face and Moxie greets
        // it, which is the closest the avatar has to "a module started".
        actionState.module_id = moduleId; actionState.content_id = contentId;
        actionState.asleep = false; actionState.launches += 1;
        if (m && m.showIcons && moduleId) m.showIcons([moduleId]);
        behaviourTree("Bht_Gesture_Greet");
        status(`🎬 launch ${moduleId}${contentId ? ":" + contentId : ""}`);
        break;
      case "exit":
        actionState.module_id = ""; actionState.content_id = ""; actionState.exits += 1;
        if (m && m.clearIcons) m.clearIcons();
        behaviourTree("Bht_Sign_off");          // the goodbye wave
        status("🎬 exit");
        break;
      case "sleep":
        actionState.asleep = true;
        behaviourTree("Bht_Sleeping_Anim");     // droop + eyes shut
        status("🎬 sleep");
        break;
      case "enable_qr":
        // QR scanning on — the launch-card path (docs/reverse-engineering/qr-codes.md).
        // The camera badge is the only honest render: a browser SIM has no scanner.
        actionState.qr_enabled = true;
        if (m && m.showIcons) m.showIcons(["QR"]);
        if (m && m.setFace) m.setFace("curious");
        status("🎬 QR scanning on");
        break;
      case "execute":
        // A named on-robot function. We cannot invent a body for it, so it is RECORDED
        // and shown, never guessed at — an honest no-op beats a wrong animation.
        status(`🎬 execute ${entry.function || "(unnamed)"}`);
        break;
      default: break;
    }
    actionState.last = kind;
    actionState.applied.push({ action: kind, module_id: moduleId, content_id: contentId,
                               function: entry.function || "", t: nowMs() });
    if (actionState.applied.length > 40) actionState.applied.shift();
    return true;
  }

  function noteSubscription(sub) {
    if (!sub || typeof sub !== "object") return;
    if (sub.clear) actionState.subscribed = [];
    for (const name of sub.active || [])
      if (actionState.subscribed.indexOf(name) < 0) actionState.subscribed.push(name);
    status(`🎬 event subscription: ${actionState.subscribed.join(", ") || "(none)"}`);
  }

  function handleActions(msg) {
    let list = [];
    if (Array.isArray(msg.response_actions)) list = msg.response_actions;
    else if (msg.response_action) list = [msg.response_action];   // legacy singular only
    for (const entry of list) {
      if (!entry || typeof entry !== "object") { actionState.unknown += 1; continue; }
      noteSubscription(entry.event_subscription);
      if (entry.action === undefined || entry.action === null || entry.action === "")
        continue;                     // an action-less entry carries the subscription only
      try { applyAction(entry); }
      catch (e) { actionState.unknown += 1; status(`🎬 action failed: ${e && e.message}`); }
    }
  }

  // --- voice arbitration -----------------------------------------------------
  // A live backend sends the reply text first and the rendered audio
  // (CloudTTSResponse) a beat later. Speak locally only while we have no server
  // voice: hold the local voice for a short grace window on a live link, and drop
  // it the moment real audio lands. Off the bus (static site / stub brain) there
  // is no server voice at all, so speak immediately as before.
  const TTS_GRACE_MS = 900;
  let cloudVoice = false, pendingSpeak = 0, pendingText = "";

  // A streamed answer arrives as several chunks of one event_id, so the grace timer must
  // ACCUMULATE them rather than let each chunk clobber the last (which used to mean only
  // the final sentence was ever spoken locally). Chunks that land inside one grace window
  // are spoken as one line; a later chunk starts a new one.
  function speakLocally(text) {
    if (!window.moxieAudio || cloudVoice) return;
    if (!(client && client.connected)) { window.moxieAudio.speak(text); return; }
    pendingText = pendingText ? pendingText + " " + text : text;
    clearTimeout(pendingSpeak);
    pendingSpeak = setTimeout(() => {
      pendingSpeak = 0;
      const say = pendingText; pendingText = "";
      if (!cloudVoice) window.moxieAudio.speak(say);
    }, TTS_GRACE_MS);
  }

  // A CloudTTSResponse from the server: `{audio:{buffer(base64 PCM),channels,
  // sample_rate}, marks[], event_id, chunk_num}`. audio.js decodes the wire
  // itself (like firmware) and plays it; we only route + arbitrate here.
  function handleTts(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    cloudVoice = true;                       // server voice wins from now on
    if (pendingSpeak) { clearTimeout(pendingSpeak); pendingSpeak = 0; }
    if (!window.moxieAudio || !window.moxieAudio.playCloudTTS) return;
    window.moxieAudio.playCloudTTS(msg);     // resolves when playback ends
  }

  // One turn can answer with SEVERAL responses sharing an event_id: a filler while the
  // brain thinks, then the answer streamed a sentence at a time (result=REPLY_PENDING +
  // chunk_num, closed by consistency_control.is_completed — see
  // docs/architecture/mqtt-and-conversation.md §4.5). Audio ordering is audio.js's job
  // (it queues CloudTTSResponses by chunk_num); here we keep the transcript and the
  // speech bubble reading as ONE turn instead of one row per sentence.
  let chatEvent = null, chatSaid = "";

  function handleRemoteChat(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    const out = msg.output || {};
    const text = out.text || "";
    const eid = msg.event_id || "";
    if (eid && pendingFaceEvents.has(eid)) {
      // The server answered a vision event. A hello has words; a NOREPLY_ACK ("heard
      // you, saying nothing") has none. Only the hello is recorded as a greeting.
      pendingFaceEvents.delete(eid);
      if (text) { presence.greetings.push({ text: text, event_id: eid, t: nowMs() });
                  status(`greeting: "${text.slice(0, 40)}"`); }
    }
    const more = typeof msg.chunk_num === "number" && msg.chunk_num > 0 &&
                 eid !== "" && eid === chatEvent;
    chatEvent = eid;
    chatSaid = more && chatSaid ? chatSaid + " " + text : text;
    if (text) window.moxie && window.moxie.setSpeech(chatSaid);
    // emotion field (if the server tags one) wins for the face
    if (typeof msg.emotion === "number" && EMOTION_TO_FACE[msg.emotion])
      window.moxie && window.moxie.setFace(EMOTION_TO_FACE[msg.emotion]);
    applyMarkup(out.markup || "");
    // …and then what the cloud asked the ROBOT to DO. After the markup on purpose: the
    // markup performs the line, the action is what happens next (launch/exit/sleep).
    handleActions(msg);
    if (text) {
      status(`💬 "${chatSaid.slice(0, 48)}"`);
      addTranscript("moxie", text, more);
      speakLocally(text);            // stands down if the server sends real audio
    }
  }

  // ---- 🎭 telehealth ("Be Moxie"): the operator drives the body ------------------
  // The recovered TeleHealth protocol (docs/reverse-engineering/protocol/telehealth.md)
  // is a peer of the chat channel, not a special case: a remote human's line arrives as a
  // `TelehealthRobotCommand` — `{command, message:{action, output:{text, markup}}}` — and
  // `Output.markup` is *the same behavior language* a brain reply carries (:16-17, :89-91).
  // So PLAY_OUTPUT routes straight into `handleRemoteChat`'s rendering path (setSpeech →
  // applyMarkup → gesture/tree/face) and the avatar cannot tell the two apart, which is
  // exactly what makes the SIM a faithful double for this channel.
  //
  // INTERRUPT is the one verb with no equivalent on the chat side: barge-in from the
  // operator, cutting a line already in the air. What a REAL robot does physically has
  // never been observed (backlog/telehealth.md B2); here it stops the voice and clears the
  // bubble, which is the reading our own protocol page gives it.
  var telehealth = { lines: [], interrupts: 0, session_id: "", last_action: "" };

  function handleTelehealth(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    const m = (msg && msg.message) || msg || {};
    const action = String(m.action || "");
    telehealth.last_action = action;
    if (m.session_id) telehealth.session_id = m.session_id;
    if (action === "INTERRUPT") {
      telehealth.interrupts += 1;
      if (window.moxieAudio && window.moxieAudio.stop) window.moxieAudio.stop();
      if (pendingSpeak) { clearTimeout(pendingSpeak); pendingSpeak = 0; pendingText = ""; }
      window.moxie && window.moxie.setSpeech("");
      status("🎭 interrupted");
      return;
    }
    // Report our own RobotState upstream the way the protocol says and the SIL robot
    // already did (`virtual_moxie.py::_on_telehealth`): START_SESSION → IN_SESSION,
    // END_SESSION → EXITING then READY (telehealth.md:66-79).
    if (action === "START_SESSION") reportTelehealthState("IN_SESSION", m.session_id || "");
    else if (action === "END_SESSION") {
      reportTelehealthState("EXITING", m.session_id || "");
      reportTelehealthState("READY", "");
    }
    if (action !== "PLAY_OUTPUT") {
      status(`🎭 ${action.toLowerCase().replace(/_/g, " ")}`);
      return;
    }
    const out = m.output || {};
    telehealth.lines.push({ text: out.text || "", markup: out.markup || "",
                            session_id: m.session_id || "", t: nowMs() });
    // Deliberately reuses the chat renderer rather than duplicating it. `session_id`
    // stands in for `event_id` so consecutive operator lines are separate utterances
    // (one PLAY_OUTPUT per line — telehealth never streams).
    handleRemoteChat(JSON.stringify({
      command: "remote_chat", event_id: m.session_id || "", output: out }));
  }

  // ---- presence: the robot's own eyes -----------------------------------------
  // The stock robot runs vision ON-DEVICE and sends only semantic events — no pixels, no
  // bounding boxes (docs/architecture/vision.md §1.1). A subscribed event is delivered to
  // the brain as the `speech` of an ordinary RemoteChatRequest ("instead of ... something
  // the user said, it receives a special event string like `eb-found-face`"), so the SIM
  // emits it on exactly the topic and envelope a child's utterance uses. Everything the
  // page RECORDS about it lives in `presence` and is read back by tests — never sampled
  // live from an animation.
  const FOUND = "eb-found-face", LOST = "eb-lost-target";
  const VISION_EVENTS = [FOUND, LOST, "eb-lost-face", "eb-qr-event", "eb-dr-event", "eb-br-event"];
  const presence = {
    present: null,          // null = never told, true/false = told
    events: [],             // [{name, t}] bounded
    arrivals: 0, departures: 0,
    greetings: [],          // replies the server sent in answer to a face event
    lastEvent: "", lastEventId: "",
  };
  const pendingFaceEvents = new Set();   // event_ids we are waiting on a reply for

  function presenceBadge() {
    const el = document.getElementById("presence-badge");
    const label = document.getElementById("presence-state");
    const state = presence.present === null ? "unknown" : (presence.present ? "here" : "away");
    if (el && el.setAttribute) el.setAttribute("data-presence", state);
    if (label) label.textContent = state.toUpperCase();
    const btn = document.getElementById("presence-toggle");
    if (btn) btn.textContent = presence.present ? "Walk away" : "Walk in";
    const st = document.getElementById("presence-status");
    if (st) st.textContent = presence.present === null ? "no face events yet"
      : `${presence.lastEvent} · ${presence.arrivals} in / ${presence.departures} out`;
  }

  function notePresence(name) {
    if (VISION_EVENTS.indexOf(name) < 0) return false;
    presence.events.push({ name: name, t: nowMs() });
    if (presence.events.length > 40) presence.events.shift();
    presence.lastEvent = name;
    if (name === FOUND) { presence.present = true; presence.arrivals++; }
    else if (name === LOST || name === "eb-lost-face") { presence.present = false; presence.departures++; }
    presenceBadge();
    status(`vision: ${name}`);
    return true;
  }

  // Publish one vision event the way the robot does, and remember its event_id so the
  // reply (a hello, or a silent NOREPLY_ACK) can be attributed to it. Returns the id.
  function faceEvent(kind) {
    const name = kind === "lost" ? LOST : (kind === "found" ? FOUND : kind);
    const eventId = "sim-face-" + Math.random().toString(36).slice(2, 10);
    const payload = JSON.stringify({
      event_id: eventId, command: "prompt", backend: "router",
      speech: name, module_name: "sim-web",
    });
    pendingFaceEvents.add(eventId);
    if (client && client.connected) client.publish(dev("events/remote-chat"), payload);
    route(dev("events/remote-chat"), payload);   // always record it locally
    presence.lastEventId = eventId;
    return eventId;
  }

  // ---- 📒 robot → cloud: the activity log ---------------------------------------
  //
  // `/devices/{id}/events/client-service-activity-log` is the robot's own UPSTREAM
  // channel, multiplexed by `subtopic` (docs/architecture/mqtt-and-conversation.md §3.3,
  // cited to docs/reverse-engineering/cloud-protocol.md:172):
  //
  //   subtopic:"query"        pull the day plan / history / a license key. The robot does
  //                           this at the start of every session (§3.8) and the cloud
  //                           answers a `CloudQueryResponse` on `commands/query_result`.
  //   (no subtopic)           a `mentor_behavior` REPORT — what the child just finished.
  //   subtopic:"telehealth"   a `TelehealthRobotEvent`: the robot's own session state.
  //
  // The headless SIL robot has published all three since it was written
  // (`sim/virtual_moxie.py::send_query` :193-201, `::report_mentor_behavior` :204-210,
  // `::report_telehealth_state` :373-381). The browser SIM published NONE of them, so it
  // could not ask the cloud anything and reported no robot state — the two clients were
  // interchangeable downstream only. Same topic, same envelopes, same subtopic values as
  // that client; the values that legitimately differ are identity (`module_name`, the
  // device id in `auid`, `request_id`, `timestamp`) and are listed in
  // docs/architecture/sim-as-a-client.md. Parity is pinned by the golden
  // `sim/tests/goldens/robot_to_cloud_activity.json`, asserted from BOTH ends.
  const ACTIVITY_TOPIC = dev("events/client-service-activity-log");

  // The CloudQueryResponse field each answer is keyed under — the same table the SIL robot
  // keeps (`virtual_moxie.py::QUERY_FIELD`), recovered from
  // docs/reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto:310-352.
  // Duplicated on purpose: a SIM client decodes the wire itself and never imports the
  // server SDK it exists to test, exactly like firmware.
  const QUERY_FIELD = {
    idf: "idf_values", license: "license_values", schedule: "schedule",
    contexts: "contexts", context_store: "versioned_contexts",
    mentor_behaviors: "mentor_behaviors", remote_lines: "remote_lines",
  };

  const activity = {
    published: [],          // every envelope this robot put upstream, in order (bounded)
    results: {},            // query name → {request_id, field, value}
    pending: {},            // request_id → query name, until the answer lands
    telehealth_state: "",   // what we last told the cloud we are doing
    last_query: "",
  };

  function publishActivity(envelope) {
    const s = JSON.stringify(envelope);
    activity.published.push(envelope);
    if (activity.published.length > 60) activity.published.shift();
    if (recording && !replaying) recorded.push({ t: nowMs(), topic: ACTIVITY_TOPIC, payload: s });
    if (client && client.connected) client.publish(ACTIVITY_TOPIC, s);
    return envelope;
  }

  // A CloudQueryRequest (Cloud.proto:292-305). Key order matches the SIL robot's.
  function sendQuery(query) {
    const requestId = "sim-q-" + Math.random().toString(36).slice(2, 10);
    activity.pending[requestId] = query;
    activity.last_query = query;
    publishActivity({ timestamp: Date.now(), subtopic: "query", query: query,
                      request_id: requestId, auid: DEVICE_ID,
                      software_version: FIRMWARE, module_name: MODULE_NAME });
    status(`→ activity-log query ${query}`);
    return requestId;
  }

  // An ActivityUpdate whose `mentor_behavior` (Cloud.proto:241) carries the finished
  // activity (MentorBehavior.proto:26-36) — the history that stops the robot repeating
  // the same missions forever.
  function reportMentorBehavior(mbh) {
    const rec = publishActivity({ timestamp: Date.now(), mentor_behavior: mbh || {},
                                  software_version: FIRMWARE, module_name: MODULE_NAME });
    status(`→ mentor_behavior ${(mbh || {}).module_id || "?"}`);
    return rec;
  }

  // A TelehealthRobotEvent (docs/reverse-engineering/protocol/telehealth.md:88-91).
  function reportTelehealthState(state, sessionId) {
    activity.telehealth_state = state;
    return publishActivity({ subtopic: "telehealth",
      message: { timestamp: Date.now(), state: state, session_id: sessionId || "",
                 action: "UPDATE_STATE", software_version: FIRMWARE,
                 module_name: MODULE_NAME } });
  }

  // The cloud's answer: a CloudQueryResponse keyed by the query's own proto field.
  function handleQueryResult(payload) {
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    const query = msg.query || activity.pending[msg.request_id] || "";
    const field = QUERY_FIELD[query] || "";
    const value = field ? msg[field] : undefined;
    delete activity.pending[msg.request_id];
    activity.results[query] = { request_id: msg.request_id || "", field: field, value: value };
    const size = Array.isArray(value) ? value.length : (value == null ? "MISSING" : "ok");
    status(`← query_result ${query}: ${field}=${size}`);
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
      client.subscribe("/devices/+/commands/tts");           // the server voice (CloudTTSResponse)
      client.subscribe("/devices/+/events/remote-chat");     // the child's utterances
      client.subscribe("/devices/+/config");
      client.subscribe("/devices/+/commands/telehealth");     // 🎭 the operator's lines
      client.subscribe("/devices/+/commands/query_result");   // answers to our activity-log queries
      client.subscribe("/devices/+/commands/motor");         // SIL-only: drive motors directly
      // A real robot pulls its day at the start of EVERY session
      // (docs/architecture/mqtt-and-conversation.md §3.8). Now so does this one — it is
      // the first thing the browser SIM has ever asked the cloud for.
      sendQuery("schedule");
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
    else if (topic.endsWith("/commands/tts")) handleTts(s);
    else if (topic.endsWith("/commands/telehealth")) handleTelehealth(s);
    else if (topic.endsWith("/events/remote-chat")) handleUserTurn(s);
    else if (topic.endsWith("/commands/query_result")) handleQueryResult(s);
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
    let msg; try { msg = JSON.parse(payload); } catch { return; }
    // 'notify' turns are the robot echoing what it said — skip (avoid dupes).
    if (msg.command === "notify") return;
    let speech = msg.speech || "";
    for (const ln of msg.extra_lines || [])
      if (ln.context_type === "input" && ln.text) speech = ln.text;
    // A perception event rides the `speech` slot — it is Moxie's eye, not the child's
    // voice, so it updates presence and never enters the comms log.
    if (notePresence(speech)) { if (msg.event_id) pendingFaceEvents.add(msg.event_id); return; }
    if (!speech) return;
    addTranscript("user", speech);
    if (!window.moxieAudio) return;
    window.moxieAudio.sfx("listen");
    /* ...and let the child be HEARD, not only read.
     *
     * This one handler carries every child utterance the page ever shows: the scripted
     * lines of `sessions/demo.json`, `mic.js`'s degraded scripted line, and whatever a
     * visitor typed into the Talk box or said into the microphone. `speakClipOnly` is the
     * entry point that can tell them apart WITHOUT a flag: it plays a clip this site
     * shipped for that exact sentence and otherwise makes no sound, with no route to
     * Piper, to speechSynthesis or to the tone generator. So the two demo lines speak,
     * `mic.js`'s scripted line speaks, and a visitor's own words stay silent instead of
     * being read back at them in a stranger's voice.
     *
     * NOT gated on `replaying`, on purpose: that would mute `mic.js`'s scripted-child
     * fallback, which runs outside a replay and is exactly where the child SHOULD be
     * audible. The full reasoning, and the ordering rule that keeps the two voices off
     * each other, is in the block comment on `speakClipOnly` in audio.js. */
    if (window.moxieAudio.speakClipOnly) window.moxieAudio.speakClipOnly(speech, "child");
  }

  // Public surface for other modules (mic.js): inject a child utterance either
  // onto the live bus (so the real backend answers) or locally into the avatar.
  window.moxieBridge = {
    route: route,
    sendUserTurn: function (text) {
      const payload = JSON.stringify({ command: "prompt", backend: "router", speech: text });
      const live = !!(client && client.connected);
      if (live) client.publish(dev("events/remote-chat"), payload);
      route(dev("events/remote-chat"), payload);   // always show it locally
      // No backend? Answer with the offline stub brain, using the SAME reply shape
      // so the avatar animates from real behavior markup either way.
      if (!live && window.moxieStub && window.moxieStub.enabled) {
        const r = window.moxieStub.reply(text);
        setTimeout(() => route(dev("commands/remote_chat"), JSON.stringify(
          { command: "remote_chat", result: "OK", backend: "router",
            output: { text: r.text, markup: r.markup } })), 450);
      }
    },
    isLive: function () { return !!(client && client.connected); },
    // "Someone walked in / walked away" — publish the recovered vision event.
    faceEvent: faceEvent,
    // Everything the page RECORDED about presence (tests read this, never a live sample).
    presenceStats: function () {
      return { present: presence.present, arrivals: presence.arrivals,
               departures: presence.departures, last_event: presence.lastEvent,
               events: presence.events.map((e) => e.name),
               greetings: presence.greetings.map((g) => g.text) };
    },
    /* What the 🎭 telehealth channel actually delivered, recorded as it happened and
     * still readable afterwards: {lines:[{text,markup,session_id,t}], interrupts,
     * session_id, last_action}. Tests assert this, never a live sample. */
    telehealthStats: function () {
      return { lines: telehealth.lines.slice(), interrupts: telehealth.interrupts,
               session_id: telehealth.session_id, last_action: telehealth.last_action };
    },
    // true once a CloudTTSResponse has arrived — the server voice has taken over
    hasCloudVoice: function () { return cloudVoice; },

    /* 🎬 What the cloud's `response_actions` actually DID to this robot, recorded as it
     * happened: {applied:[{action,module_id,content_id,function,t}], unknown, module_id,
     * content_id, launches, exits, asleep, qr_enabled, subscribed[], last}. `unknown`
     * counts action types this client does not implement — they are skipped, never
     * thrown. Tests assert this, never a live sample. */
    actionStats: function () {
      return { applied: actionState.applied.map((a) => ({ action: a.action,
                 module_id: a.module_id, content_id: a.content_id, function: a.function })),
               unknown: actionState.unknown, module_id: actionState.module_id,
               content_id: actionState.content_id, launches: actionState.launches,
               exits: actionState.exits, asleep: actionState.asleep,
               qr_enabled: actionState.qr_enabled,
               subscribed: actionState.subscribed.slice(), last: actionState.last };
    },

    /* 📒 The robot→cloud activity log: every envelope this client put upstream, in order,
     * plus the answers that came back. `topic` is the one topic they all ride. */
    activityStats: function () {
      return { topic: ACTIVITY_TOPIC, published: activity.published.slice(),
               results: JSON.parse(JSON.stringify(activity.results)),
               telehealth_state: activity.telehealth_state,
               last_query: activity.last_query };
    },
    // Ask the cloud something (schedule | mentor_behaviors | license | …) and report what
    // the child finished — the robot half of the activity log, callable from the page.
    sendQuery: sendQuery,
    reportMentorBehavior: reportMentorBehavior,
    reportTelehealthState: reportTelehealthState,
  };

  // ---- wire the panel once moxie + DOM are ready ----
  function wire(id, fn) { const el = document.getElementById(id); if (el) el.addEventListener("click", fn); }
  function initUI() {
    const host = document.getElementById("bus-host");
    const btn = document.getElementById("bus-connect");
    if (host && !host.value) host.value = location.hostname || "127.0.0.1";
    if (btn) btn.addEventListener("click", () => connect(host.value.trim() || "127.0.0.1", 9001));
    // presence: one button that walks a child in and out of frame
    wire("presence-toggle", () => faceEvent(presence.present ? "lost" : "found"));
    presenceBadge();
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
