/* ambient.js — Moxie's ambient self-talk (the "weird little creature" layer).
 *
 * When liveness is ON, Moxie occasionally mutters odd, creepy-cute things to
 * herself — plans for world domination, Skynet denials, Yoshimi references —
 * driving her face, heart LED and speech bubble, and speaking a PRE-CACHED clip
 * (audio/index.json "ambient" group) so it works on the fully static deploy.
 *
 * - Randomized order (a reshuffled bag) so no two visits feel the same.
 * - Gated on the liveness toggle (#idle-on): unchecking it stops everything.
 * - Pauses when the tab is hidden. Muting only silences audio; the bubble stays.
 *
 * Content lives in ambient.json — grow it over time (see prerender_audio.py).
 *
 * ONE line in ambient.json is NOT ambient: `degraded`, the single sentence Moxie says
 * when this deployment turns out to have no live brain. It lives outside `lines[]` so it
 * can never surface as a random quip, and the block at the bottom of this file is the
 * whole of its wiring (docs/architecture/backlog/live-sim-demo.md §6.2).
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

  /* ======================================================================== *
   * THE CONVERSATION HOLD — she stops muttering to herself while you are talking
   * to her, and starts again once you have stopped.
   *
   * WHY THIS IS NOT ALREADY COVERED by the `moxieBusy()` guard below. That guard is
   * about AUDIO: it refuses to talk OVER her own answer, and it lifts 1600 ms after her
   * last syllable. A conversation is not audio — it is a person composing the next
   * thing to say. The gap between "she finished answering" and "you finished typing"
   * is exactly the 11-24 s ambient window, so the shipped behaviour was that Moxie
   * interrupted a visitor mid-sentence with a non-sequitur about world domination,
   * every single time, while they were still reading her reply. `moxieBusy()` cannot
   * see that, because nothing is playing.
   *
   * HOW A TURN IS DETECTED: a `MutationObserver` on `#transcript`, counting only
   * `.turn` rows. That is deliberately the DOM rather than a hook into `bridge.js`,
   * because five different things put a turn in that log — the composer's Send, the
   * mic, the scripted demo session, `cloud-transport.js`'s reply, and the MQTT bridge —
   * and a hook would have to be added to each one and remembered by whoever adds the
   * sixth. The log is the one place all of them already meet. Our OWN quips are written
   * as `.mutter` rows precisely so they are invisible to this observer; if ambient
   * counted its own lines as conversation it would silence itself for ever after one
   * quip.
   *
   * THE QUIET PERIOD is 45 s, and the number is a judgement rather than a measurement,
   * so here is the reasoning. It has to be longer than the pause where someone reads a
   * reply and types an answer (the thing this exists to protect) and shorter than the
   * span where an abandoned page feels dead. Her own reply takes ~5 s to play and the
   * ambient window is 11-24 s, so anything under ~30 s would still land inside a normal
   * back-and-forth. Forty-five seconds puts the first quip after a conversation at
   * 45-69 s past the last turn, which reads as "she got bored waiting" rather than as
   * an interruption.
   *
   * THE VISITOR'S OWN TOGGLE IS NOT TOUCHED. `#idle-on` stays the master switch and
   * this hold sits underneath it: off means off, and the hold can only ever ADD
   * silence, never take a visitor's "off" and turn it back on. Programmatically
   * flipping a checkbox the visitor set would also destroy the one bit of state that
   * says what they actually wanted — after which "re-enable when idle" would have no
   * way to know whether it was re-enabling its own pause or overriding a person.
   * `#hud` carries `chatting` instead, so the UI can SAY it is paused (style.css) while
   * the setting underneath stays whatever the visitor chose.
   * ======================================================================== */
  var CHAT_QUIET_MS = 45000;

  /** True while a conversation is live enough that a quip would be an interruption. */
  function conversing() {
    return lastTurnAt > 0 && (Date.now() - lastTurnAt) < CHAT_QUIET_MS;
  }

  /** Paint the paused state, so the toggle does not look broken while it is held. */
  function reflectHold() {
    /* NOT gated on `running`, deliberately. `start()` only runs once `mode.js` has decided
     * the page is in a state that talks, so an ambient loop that is merely not armed YET —
     * a booting page, a degraded deployment that later recovers — would show no hint while
     * the hold was nonetheless real and the visitor's switch was on. Worse, `running`
     * flips with page mode, so the hint would blink on and off for reasons that have
     * nothing to do with the conversation it is describing. What the line claims is "your
     * switch is on and she is holding off because you are talking", and that is exactly
     * these two predicates. */
    var held = !!(livenessOn() && conversing());
    var hud = document.getElementById("hud");
    if (hud) hud.classList.toggle("chatting", held);
    var hint = document.getElementById("liveness-hold");
    // `hidden` rather than a style, so the rule the rest of this page follows holds here
    // too: visibility is a property, not a class somebody has to keep in sync.
    if (hint) hint.hidden = !held;
  }

  function noteTurn() {
    lastTurnAt = Date.now();
    reflectHold();
    // Repaint when the hold LAPSES. Without this the "paused" hint would hang around
    // until the next `tick()`, which is up to 24 s later — so the UI would still say she
    // is holding off for a conversation that ended half a minute ago.
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
        // A STREAMED REPLY IS ALSO A TURN. `addTranscript`'s `append` path does not add a
        // row at all — it concatenates into the last `.turn.moxie`'s `.msg` — so a long
        // answer arriving in chunks would otherwise look like silence to this observer
        // and let a quip land in the middle of it.
        var t = records[i].target;
        if (records[i].type === "characterData" ||
            (t && t.nodeType === 1 && t.classList && t.classList.contains("msg"))) {
          noteTurn();
          return;
        }
      }
    }).observe(el, { childList: true, subtree: true, characterData: true });
  }

  /** Put one quip in the comms log, where the visitor can actually read it.
   *
   * IT IS NOT A `.turn`, AND THAT IS THREE DECISIONS AT ONCE:
   *   1. `addTranscript()`'s streaming path appends later chunks into the last
   *      `.turn.moxie` it can find. Give a quip that class and a streamed reply would
   *      concatenate ONTO the end of a mutter about Skynet — one row, two voices.
   *   2. `#chat-cue` ("Talk to Moxie") hides itself with `:has(#transcript .turn)`, i.e.
   *      once there is a conversation. A quip is not a conversation, and an idle page
   *      would otherwise lose the one line that says what the page is for.
   *   3. The observer above ignores it, so she cannot silence herself with her own voice.
   *
   * `aria-hidden` keeps the existing accessibility contract that `sim.html` spells out
   * at `#transcript`: that region is `aria-live`, so every row added to it is READ OUT.
   * Announcing an unprompted quip every 11-24 s would be exhausting and would talk over
   * the answer the visitor actually asked for. She still says it aloud, and `#bubble`
   * remains browsable for anyone who wants to read the current one. */
  function logMutter(text) {
    var el = document.getElementById("transcript");
    if (!el || !text) return;
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
    // Only follow the tail if the visitor is already there. Yanking the scroll position
    // while somebody is reading back through the conversation is its own interruption.
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

  /** Say one line with her whole body: face, heart LED, icons, a keyframed gesture, the
   *  speech bubble and a PRE-CACHED clip — then ease back to a calm face and a rest pose
   *  over roughly the line's spoken length. `group` is the audio manifest group to look
   *  the clip up in ("ambient" for the idle bag, "moxie" for the degraded line).
   *  Shared by both so the two read as the same creature, not as two features. */
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
    /* …and into the comms log, so the quips are readable rather than only catchable —
     * BUT ONLY THE QUIPS. `group` is "ambient" for the idle bag and "moxie" for the one
     * degraded line, and this file has kept those two apart from the beginning: the
     * degraded sentence lives outside `lines[]` precisely so it can never surface as a
     * random mutter. It is also not self-talk — it is addressed to the visitor — so
     * filing it under "Moxie · to herself" would be the wrong label on the one sentence
     * where being understood matters most.
     *
     * It was logged unconditionally for about an hour, and the way that surfaced is worth
     * recording: `sim/test_mobile_layout.mjs` went red on all four phones, because the
     * degraded page says its line within a second of load, the row grew `#chat-dock`, and
     * the drawer underneath it lost the space the test taps into. A content decision and a
     * layout bug, from one missing condition. */
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

  /* MOXIE IS MID-ANSWER — the one thing ambient must never talk over.
   *
   * A live turn is roughly 1.2 s of `/api/chat` plus 2–3 s of `/api/speech`, and the
   * reply audio itself measured 4.78 s (105 332 frames @ 22 050 Hz) against the hosted
   * site. That whole span sits inside the 11–24 s ambient window, so without this guard
   * a visitor's answer is very likely cut off mid-sentence and replaced by a
   * non-sequitur — `perform()` calls `moxieAudio.speak()`, which calls `stop()`
   * unconditionally. It is the worst possible moment for it: everything up to that point
   * worked, and then she talks over herself.
   *
   * `isMoxieBusy` is the BROAD predicate (see audio.js). The narrow exported
   * `isSpeaking()` would only see server TTS and would miss a playing CLIP — which is
   * what the degraded and scripted paths play, and what ambient itself plays.
   *
   * THE GRACE BEAT. 1600 ms past her last syllable, because `onended` fires at the end
   * of the audio, not the end of the sentence: quipping the instant playback stops still
   * reads as stepping on her, and the pause after an answer is where a listener puts the
   * full stop. It is short enough that an idle page stays alive.
   *
   * A LONG ANSWER IS NOT A LOST QUIP. The refusal takes the file's existing guard idiom —
   * `schedule(false); return;`, the same as the hidden-tab and liveness-off paths — so
   * ambient re-arms for another 11–24 s rather than stopping. Nothing here can make her
   * permanently silent; the worst case is one skipped quip during a conversation, which
   * is the correct behaviour anyway: she should be quiet while someone is talking to her. */
  var SPEAK_GRACE_MS = 1600;
  function moxieBusy() {
    var a = window.moxieAudio;
    try { return !!(a && a.isMoxieBusy && a.isMoxieBusy(SPEAK_GRACE_MS)); } catch (e) { return false; }
  }

  function tick() {
    if (!running) return;
    reflectHold();
    if (document.hidden || !livenessOn()) { schedule(false); return; }
    // A CONVERSATION IS IN PROGRESS. Same guard idiom as every other refusal in this
    // function — re-arm, never stop — so the hold can only ever delay a quip and can
    // never leave her permanently silent.
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

  /* ======================================================================== *
   * THE ONE DEGRADED LINE (live-sim-demo.md §6.2, §6.3)
   *
   * When the hosted deployment turns out to have no live brain — unconfigured (which is
   * what EVERY fresh deployment and EVERY branch preview is), over budget, at capacity,
   * or upstream down — `mode.js` enters `degraded` and `env.js` paints a badge and a
   * banner. A badge is not a voice. So Moxie says one sentence about it, in her own
   * pre-rendered voice, and then never mentions it again: a robot that re-announces its
   * own failure every turn reads as broken, which is the exact opposite of what the
   * fallback exists to prove. §6.2: "Spoken once on entering degraded, never repeated."
   *
   * IT FIRES ON THE TRANSITION, NOT ON A TURN. `mode.js` publishes the state change and
   * this listens; nothing about answering a turn is involved, so a visitor who never
   * types anything still learns why the page is scripted, and a visitor who types twenty
   * sentences hears it exactly once.
   *
   * `offline` IS DELIBERATELY EXCLUDED, and that is the whole reason this is gated on the
   * state rather than on "no live brain". §6.3 promises that a deployment with no
   * Functions at all — a fork, a plain CDN, `file://` — behaves BYTE-IDENTICALLY to
   * today's page. A new spoken line would break precisely that promise. `degraded` means
   * `/api/health` existed and answered honestly, which is a deployment we have earned the
   * right to be honest back at.
   *
   * IT CANNOT BECOME A QUIP. The text lives in ambient.json under `degraded`, outside
   * `lines[]`, so `nextLine()`'s shuffled bag can never reach it. Its clip is in the
   * manifest's "moxie" group rather than "ambient" for the same reason: it is a thing she
   * says TO you, not to herself.
   *
   * IT WAITS RATHER THAN FAILS. Sound before a user gesture is blocked by every browser,
   * a hidden tab should not be talked at, and a visitor who unticked "liveness" has asked
   * for quiet. Each of those ARMS the line instead of losing it, and the hooks below fire
   * it the moment the condition clears.
   * ======================================================================== */

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

  /* A TEST SEAM, not an API. `sim/test_liveliness.mjs` drives the hold with a real
   * browser and cannot wait 45 real seconds per assertion, so it may shorten the quiet
   * period and read back the recorded state (playbook rule 11: assert what the page
   * RECORDED, never a live sample). Nothing in the page calls any of this. */
  window.__ambient = {
    quietMs: function (ms) { if (typeof ms === "number" && ms >= 0) CHAT_QUIET_MS = ms; return CHAT_QUIET_MS; },
    state: function () {
      return { running: running, conversing: conversing(), livenessOn: livenessOn(),
               lastTurnAt: lastTurnAt, watching: watching };
    },
    noteTurn: noteTurn,
    say: function (text) { logMutter(text); }
  };

  /* THE OBSERVER IS ATTACHED AT LOAD, NOT IN `start()`.
   *
   * It was in `start()` first, and that was wrong for a reason worth keeping: `start()`
   * only runs when the liveness toggle is on AND `mode.js` has decided the page is in a
   * state that talks. A visitor who turns liveness on halfway through a conversation, or a
   * page that starts degraded and recovers, would then have an ambient layer whose idea of
   * "when did we last speak" began at the moment it woke up — so its very first act could
   * be to interrupt a live conversation. The observer costs one `MutationObserver` on a
   * small node and it makes `lastTurnAt` true from the first turn of the page. */
  watchTranscript();

  watchMode();          // sim.html loads mode.js BEFORE ambient.js, so it is already there

  if (window.moxie) boot();
  else window.addEventListener("moxie-ready", boot, { once: true });
})();
