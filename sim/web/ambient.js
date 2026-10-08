/* ambient.js — Moxie's ambient self-talk (the "weird little creature" layer).
 *
 * While liveness is ON (#idle-on), Moxie occasionally mutters creepy-cute things to
 * herself — face, heart LED, gesture, bubble, and a PRE-CACHED clip (audio/index.json
 * "ambient" group), so it works on a fully static deploy. Lines come from ambient.json in
 * a reshuffled bag; paused while the tab is hidden; muting only silences the audio.
 *
 * ambient.json's `degraded` entry is NOT a quip: it is the one sentence she says when the
 * deployment has no live brain, kept outside `lines[]` (see the bottom of this file;
 * docs/architecture/backlog/live-sim-demo.md §6.2).
 */
(function () {
  "use strict";
  var lines = null, bag = [], timer = 0, relax = 0, gt = [], running = false, started = false;
  var lastTurnAt = 0;                       // when the visitor last exchanged a real turn
  var watching = false;                     // the transcript observer is attached once
  var holdTimer = 0;                        // repaints the paused hint when the hold lapses
  var loading = null;                       // the single in-flight ambient.json fetch
  var degraded = null;                      // ambient.json's `degraded` entry, if any
  var degradedSaid = false;                 // said ONCE per session, then never again
  var degradedPending = false;              // armed, but the page cannot speak it yet

  // Keyframed body gestures — each frame is {motorIndex: value}; frames play ~520ms
  // apart and the sim eases between them. Motors: 0/1 L shoulder up-down/in-out,
  // 2/3 R shoulder, 4 head nod, 5 yaw, 6 lean. Rest = 16384 (0 for in/out).
  var GESTURES = {
    wave:      [ {0: 30000, 1: 17000}, {0: 24000}, {0: 30000}, {0: 22000} ],
    raiseBoth: [ {0: 30000, 1: 16000, 2: 30000, 3: 16000, 6: 14800}, {0: 31500, 2: 31500} ],
    shrug:     [ {1: 13000, 3: 13000, 0: 20000, 2: 20000, 4: 15200} ],
    leanIn:    [ {6: 20800, 4: 18200, 5: 17600} ],
    tilt:      [ {4: 19600, 5: 17400} ],
    point:     [ {2: 27000, 3: 15000} ],
    peek:      [ {5: 20800, 4: 18000}, {5: 12200} ],
    slump:     [ {0: 13200, 2: 13200, 4: 13600, 6: 15600} ]
  };
  function clearGesture() { gt.forEach(clearTimeout); gt = []; }
  function playGesture(name) {
    var frames = GESTURES[name];
    if (!frames || !window.moxie) return;
    clearGesture();
    frames.forEach(function (frame, i) {
      gt.push(setTimeout(function () {
        for (var k in frame) if (frame.hasOwnProperty(k)) {
          try { window.moxie.setMotor(+k, frame[k]); } catch (e) {}
        }
      }, i * 520));
    });
  }

  function load() {
    return fetch("ambient.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        lines = (j && j.lines) || [];
        degraded = (j && j.degraded) || null;   // NOT pushed into `lines` — see the bottom
        return lines;
      })
      .catch(function () { lines = []; return lines; });
  }
  /** One fetch per page, whoever asks first: the idle loop, or the degraded announcer. */
  function loadOnce() { if (!loading) loading = load(); return loading; }
  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }
  function nextLine() {
    if (!lines || !lines.length) return null;
    if (!bag.length) bag = shuffle(lines.slice());
    return bag.pop();
  }
  function livenessOn() {
    var c = document.getElementById("idle-on");
    return !c || c.checked;   // default on if the control is absent
  }

  /* THE CONVERSATION HOLD — no muttering while you are talking to her.
   * moxieBusy() only covers AUDIO; a visitor reading a reply and typing the next line sits
   * in the ambient window with nothing playing. Turns are counted by a MutationObserver on
   * #transcript `.turn` rows — where every turn source meets; our quips are `.mutter` rows.
   * CHAT_QUIET_MS (a judgement): longer than read-and-reply, shorter than "the page feels
   * dead". #idle-on is never touched (the hold only ADDS silence); `#hud.chatting` lets the
   * UI say it is paused. */
  var CHAT_QUIET_MS = 45000;

  /** The visitor is making a line for her: the message box has focus or holds text, or the
   *  mic is open (mic.js's body[data-mic]). Before their FIRST line there is no `.turn` for
   *  the hold to count, so her first quip landed 5-9 s after the unlock, while they typed
   *  (measured on the live site: 5.4-5.7 s before the first send) — and a quip into an open
   *  mic is a quip in the visitor's own clip. */
  function composing() {
    if (document.body && document.body.getAttribute("data-mic") === "on") return true;
    var box = document.getElementById("speech-input");
    if (!box) return false;
    return document.activeElement === box || !!(box.value && String(box.value).trim());
  }

  /** True while a conversation is live enough that a quip would be an interruption. */
  function conversing() {
    return composing() || (lastTurnAt > 0 && (Date.now() - lastTurnAt) < CHAT_QUIET_MS);
  }

  /** Paint the paused state, so the toggle does not look broken while it is held. */
  function reflectHold() {
    // NOT gated on `running`: that flips with page mode, and the hint claims only "your
    // switch is on and she is holding off because you are talking".
    var held = !!(livenessOn() && conversing());
    var hud = document.getElementById("hud");
    if (hud) hud.classList.toggle("chatting", held);
    var hint = document.getElementById("liveness-hold");
    if (hint) hint.hidden = !held;
  }

  function noteTurn() {
    lastTurnAt = Date.now();
    reflectHold();
    // Repaint when the hold LAPSES, not at the next tick (up to 24 s later).
    clearTimeout(holdTimer);
    holdTimer = setTimeout(reflectHold, CHAT_QUIET_MS + 50);
  }

  /** Watch the comms log for REAL turns — never our own `.mutter` rows. */
  function watchTranscript() {
    if (watching) return;                   // `start()` may be called more than once
    var el = document.getElementById("transcript");
    if (!el || typeof MutationObserver !== "function") return;
    watching = true;
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (n && n.nodeType === 1 && n.classList && n.classList.contains("turn")) {
            noteTurn();
            return;
          }
        }
        // A streamed reply appends into the last `.turn.moxie`'s `.msg` without adding a
        // row — that is a turn too.
        var t = records[i].target;
        if (records[i].type === "characterData" ||
            (t && t.nodeType === 1 && t.classList && t.classList.contains("msg"))) {
          noteTurn();
          return;
        }
      }
    }).observe(el, { childList: true, subtree: true, characterData: true });
  }

  /** One quip in the comms log. NOT a `.turn`: a streamed reply appends into the last
   *  `.turn.moxie`, #chat-cue hides once a `.turn` exists, and the observer must not count
   *  her own voice. aria-hidden: #transcript is aria-live, and a quip every 11-24 s would
   *  talk over real answers (she still says it aloud).
   *  UNTIL THE FIRST REAL TURN there is one row, re-worded in place: a row per quip grew the
   *  dock over her body while nobody was talking (css/dock.css holds that row's height). */
  function logMutter(text) {
    var el = document.getElementById("transcript");
    if (!el || !text) return;
    var solo = el.querySelector(".turn") ? null : el.querySelector(".mutter .msg");
    if (solo) { solo.textContent = text; return; }
    var row = document.createElement("div");
    row.className = "mutter";
    row.setAttribute("aria-hidden", "true");
    var who = document.createElement("span");
    who.className = "who";
    who.textContent = "Moxie \u00b7 to herself";
    var msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = text;              // textContent = XSS-safe, same as addTranscript
    row.appendChild(who);
    row.appendChild(msg);
    el.appendChild(row);
    // Follow the tail only if the visitor is already there.
    var atBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 40;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  function schedule(initial) {
    clearTimeout(timer);
    if (!running) return;
    var d = initial ? (5000 + Math.random() * 4000)     // first quip: let the scene settle
                    : (11000 + Math.random() * 13000);  // then every ~11–24s
    timer = setTimeout(tick, d);
  }

  /** Say one line with her whole body (face, LED, icons, gesture, bubble, clip), then
   *  ease back to calm over roughly its spoken length. `group` is the clip group:
   *  "ambient" for the idle bag, "moxie" for the degraded line. */
  function perform(ln, group) {
    var m = window.moxie;
    if (!m || !ln || !ln.text) return false;
    var ledChk = document.getElementById("led-on");
    var hadHeart = ledChk ? ledChk.checked : false;
    try {
      if (ln.face) m.setFace(ln.face);
      if (ln.heart) m.setHeartLED(true, ln.heart);
      if (Array.isArray(ln.icons)) m.showIcons(ln.icons);
      if (ln.gesture) playGesture(ln.gesture);
      m.setSpeech(ln.text);
      if (window.moxieAudio) window.moxieAudio.speak(ln.text, group || "ambient");
    } catch (e) {}
    // Only QUIPS go in the log: the degraded line is addressed to the visitor, not
    // "to herself" (and logging it on load grew #chat-dock over the phone drawer).
    if ((group || "ambient") === "ambient") logMutter(ln.text);

    // relax back toward a calm face + rest pose (roughly the line's spoken length)
    clearTimeout(relax);
    var dur = 2800 + ln.text.length * 55;
    relax = setTimeout(function () {
      try {
        if (window.moxie) {
          window.moxie.setFace("neutral");
          window.moxie.centerAll();                          // ease limbs back to rest
          if (!hadHeart) window.moxie.setHeartLED(false);    // don't clobber a user-set LED
        }
      } catch (e) {}
    }, dur);
    return true;
  }

  /* Never talk over her answer (perform() -> speak() stop()s it). isMoxieBusy is voice/'s
   * BROAD predicate (isSpeaking() misses clips); SPEAK_GRACE_MS leaves a beat after the
   * audio ends. A refusal re-arms, so a long answer costs at most one skipped quip. */
  var SPEAK_GRACE_MS = 1600;
  function moxieBusy() {
    var a = window.moxieAudio;
    try { return !!(a && a.isMoxieBusy && a.isMoxieBusy(SPEAK_GRACE_MS)); } catch (e) { return false; }
  }

  function tick() {
    if (!running) return;
    reflectHold();
    if (document.hidden || !livenessOn()) { schedule(false); return; }
    // Conversation in progress: re-arm, never stop.
    if (conversing()) { schedule(false); return; }
    if (moxieBusy()) { schedule(false); return; }
    var m = window.moxie, ln = nextLine();
    if (!m || !ln) { schedule(false); return; }
    perform(ln, "ambient");
    schedule(false);
  }

  function start(initial) {
    if (running) return;
    running = true;
    var kick = function () { schedule(initial); };
    if (!started) { started = true; loadOnce().then(kick); } else kick();
  }
  function stop() {
    running = false;
    clearTimeout(timer); clearTimeout(relax); clearTimeout(holdTimer); clearGesture();
    reflectHold();   // liveness off: the paused hint must not outlive the feature
  }

  /* THE ONE DEGRADED LINE (live-sim-demo.md §6.2, §6.3): when mode.js enters `degraded`
   * she says one sentence about it, ONCE per session (re-announcing failure reads as
   * broken), on the state TRANSITION. `offline` is excluded (a Functions-less deploy stays
   * byte-identical). Outside `lines[]`, clip in the "moxie" group. Locked autoplay, a
   * hidden tab or liveness off ARM it; the hooks below fire it once that clears. */

  /** Say it, or arm it and wait. Never says it twice. */
  function sayDegraded() {
    if (degradedSaid || !degraded || !degraded.text) return false;
    degradedPending = true;                        // stays armed until it actually lands
    if (!window.moxie) return false;               // the avatar has not booted yet
    if (document.hidden) return false;             // do not talk at a background tab
    if (!livenessOn()) return false;               // the visitor asked for quiet
    var a = window.moxieAudio;
    if (a && a.isUnlocked && !a.isUnlocked()) return false;   // autoplay still locked
    degradedPending = false;
    degradedSaid = true;
    return perform(degraded, "moxie");
  }

  /** `mode.js` says we are degraded. Make sure the line is loaded, then try to say it. */
  function armDegraded() {
    if (degradedSaid) return;
    if (degraded) { sayDegraded(); return; }
    loadOnce().then(sayDegraded);
  }

  function watchMode() {
    var mm = null;
    try { mm = window.moxieMode; } catch (e) { mm = null; }
    if (!mm || typeof mm.onChange !== "function") return;
    var off = null;
    off = mm.onChange(function (snap) {
      if (degradedSaid) { if (off) { off(); off = null; } return; }
      if (snap && snap.state === "degraded") armDegraded();
    });
  }

  // Retry hooks — each one is a condition `sayDegraded` refused on, clearing.
  try {
    window.addEventListener("moxie-audio-unlocked", function () {
      if (degradedPending) sayDegraded();
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && degradedPending) sayDegraded();
    });
  } catch (e) {}

  function boot() {
    var idle = document.getElementById("idle-on");
    if (idle) idle.addEventListener("change", function () {
      idle.checked ? start(true) : stop();
      if (idle.checked && degradedPending) sayDegraded();   // quiet was lifted
    });
    // Browsers block sound until the user interacts, so wait for the audio unlock
    // before the first quip — otherwise Moxie mimes silently and looks broken.
    var kick = function () { if (livenessOn()) start(true); };
    if (window.moxieAudio && window.moxieAudio.isUnlocked && window.moxieAudio.isUnlocked()) kick();
    else window.addEventListener("moxie-audio-unlocked", kick, { once: true });
    if (degradedPending) sayDegraded();            // the avatar just booted
  }

  // expose for tests / manual poking
  window.moxieAmbient = { start: function () { start(false); }, stop: stop,
                          gesture: playGesture,
                          say: function () { running = true; started = true;
                            (lines ? Promise.resolve() : loadOnce()).then(tick); },
                          // for tests and manual poking: the degraded line's state
                          degradedState: function () {
                            return { text: degraded && degraded.text ? degraded.text : null,
                                     said: degradedSaid, pending: degradedPending };
                          } };

  /* TEST SEAM, not an API: test_liveliness.mjs shortens the quiet period and reads the
   * recorded state. Nothing in the page calls it. */
  window.__ambient = {
    quietMs: function (ms) { if (typeof ms === "number" && ms >= 0) CHAT_QUIET_MS = ms; return CHAT_QUIET_MS; },
    state: function () {
      return { running: running, conversing: conversing(), composing: composing(),
               livenessOn: livenessOn(), lastTurnAt: lastTurnAt, watching: watching };
    },
    noteTurn: noteTurn,
    say: function (text) { logMutter(text); }
  };

  /** Repaint the hold as the visitor starts or stops writing — the next tick can be 24 s
   *  away. Deferred a task, so a `blur` is read after focus has really moved. */
  function watchComposer() {
    var box = document.getElementById("speech-input");
    if (!box || !box.addEventListener) return;
    var soon = function () { setTimeout(reflectHold, 0); };
    ["focus", "blur", "input"].forEach(function (ev) { box.addEventListener(ev, soon); });
  }

  // Attached at LOAD, not in start(): start() waits for liveness + a talking mode, and a
  // later-woken layer would not know a conversation was already under way.
  watchTranscript();
  watchComposer();

  watchMode();          // sim.html loads mode.js BEFORE ambient.js, so it is already there

  if (window.moxie) boot();
  else window.addEventListener("moxie-ready", boot, { once: true });
})();
