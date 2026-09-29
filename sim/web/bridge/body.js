/* bridge/body.js — behavior markup → the avatar's face, arms and icon badges. */
(function () {
  "use strict";
  const B = window.__moxieBridge;

  // EmotionState (robotbrain RemoteChat): 0 unknown,1 sadness,2 joy,3 love,
  // 4 anger,5 fear,6 surprise,7 neutral → a face. Heuristic: the enum isn't fully RE'd.
  B.EMOTION_TO_FACE = {
    1: "sad", 2: "happy", 3: "happy", 4: "sad",
    5: "surprised", 6: "surprised", 7: "neutral",
  };
  // cmd:playback-mood `mood` int → face. AUTHORITATIVE ePlaybackMood enum (recovered from
  // Assembly-CSharp; behavior-markup.md), 1:1 onto the 11 Bht_Eyeseme_* expressions.
  const MOOD_TO_FACE = {
    0: "neutral", 1: "happy", 2: "sad", 3: "angry", 4: "shy", 5: "surprised",
    6: "afraid", 7: "concerned", 8: "confused", 9: "curious", 10: "embarrassed",
  };

  const C = 16384, MAX = 32767;      // motor rest / range (MOTOR_MAX_POS)

  // Motor indices: 0/2 L/R shoulder up-down, 1/3 L/R shoulder in-out, 4 head, 5 body-yaw, 6 body-lean.
  const set = B.set = (i, v) => window.moxie && window.moxie.setMotor(i, Math.max(0, Math.min(MAX, v)));
  const armsHome = B.armsHome = () => { for (const i of [0, 1, 2, 3]) set(i, C); };
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
  B.gesture = gesture;

  // Behaviour trees (Bht_*) — whole-body animations; the authoritative set is
  // docs/reverse-engineering/runtime/behavior-tree-engine.md.
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
        // INFERRED (no hardware has played our markup): the planner's "hold the gaze" handle,
        // so it must read steadier and longer than Idle_Listening's tilt.
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
  B.behaviourTree = behaviourTree;

  // Parse the marks in a markup string and drive the avatar.
  B.applyMarkup = function applyMarkup(markup) {
    if (!markup || !window.moxie) return;
    const mood = /cmd:playback-mood,data:\{[^}]*?\+mood\+:(\d+)/.exec(markup);
    if (mood) { const f = MOOD_TO_FACE[+mood[1]]; if (f) window.moxie.setFace(f); }
    const gx = /\+eventName\+:\+(Gesture_[A-Za-z_]+)\+/g; let g;
    while ((g = gx.exec(markup))) gesture(g[1]);
    const bx = /\+behaviour\+:\+(Bht_[A-Za-z0-9_]+)\+/g; let b;
    while ((b = bx.exec(markup))) behaviourTree(b[1]);
    // icons-v2: each mark is a command (0 = show, 2 = clear) with up to 4 named icons
    // (iconType:1). A turn shows at its start and clears at its end; we show the union
    // and clear ~4 s later.
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
      window.moxie.showIcons([...shown]); B.status(`icons: ${[...shown].join(", ")}`);
      if (window.moxieAudio) window.moxieAudio.sfx("icon");
      if (hasClear) setTimeout(() => window.moxie.clearIcons && window.moxie.clearIcons(), 4000);
    }
  };
})();
