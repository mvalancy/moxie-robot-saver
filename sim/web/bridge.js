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
  // cmd:playback-mood `mood` int → face. Inferred from shipped content (see
  // behavior-markup.md#data-schemas): 0 neutral/default, 1 positive/engaged,
  // 2 concerned ("I'm sorry"), 4 oops ("Oops."), 5 surprised ("Oh!").
  const MOOD_TO_FACE = { 0: "neutral", 1: "happy", 2: "sad", 4: "sad", 5: "surprised" };

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
    // icons-v2 → log the icon names (badge rendering is a D5 face task)
    const ix = /\+iconType\+:1,\+value\+:\+([A-Za-z0-9_]+)\+/g; const icons = [];
    let ic; while ((ic = ix.exec(markup))) icons.push(ic[1]);
    if (icons.length) status(`icons: ${icons.join(", ")}`);
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
    if (text) status(`💬 "${text.slice(0, 48)}"`);
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
      client.subscribe("/devices/+/commands/remote_chat");
      client.subscribe("/devices/+/config");
    });
    client.on("reconnect", () => status(`reconnecting ${url}…`));
    client.on("error", (e) => status(`error: ${e && e.message ? e.message : e}`));
    client.on("close", () => status(`○ disconnected`));
    client.on("message", (topic, payload) => {
      const s = payload.toString();
      if (topic.endsWith("/commands/remote_chat")) handleRemoteChat(s);
      else if (topic.endsWith("/config")) {
        try { const c = JSON.parse(s); status(`config: pairing_status=${c.pairing_status}`); } catch {}
      }
    });
  }

  // ---- wire the panel once moxie + DOM are ready ----
  function initUI() {
    const host = document.getElementById("bus-host");
    const btn = document.getElementById("bus-connect");
    if (!btn) return;
    if (host && !host.value) host.value = location.hostname || "127.0.0.1";
    btn.addEventListener("click", () => connect(host.value.trim() || "127.0.0.1", 9001));
  }
  if (window.moxie) initUI();
  else window.addEventListener("moxie-ready", initUI, { once: true });
})();
