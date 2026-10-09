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
 *
 * Two optional fields on a line (sim/test_ambient.mjs checks them):
 *   "months": [10]       in the bag only in those months (1-12) of the VISITOR'S local
 *                        calendar: the October set;
 *   "beat": "glitch"     never in the bag; the rare glitch beat (THE GLITCH, below);
 *   "beat": "signoff"    never in the bag; one aside after a goodbye (AFTER A GOODBYE).
 */
(function () {
  "use strict";
  var lines = null, bag = [], timer = 0, relax = 0, gt = [], running = false, started = false;
  var bagMonth = 0;                         // the month the bag was filled in (see nextLine)
  var lastTurnAt = 0;                       // when the visitor last exchanged a real turn
  var visitorTurns = 0;                     // the visitor's own rows seen (a goodbye that did not stick)
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
    slump:     [ {0: 13200, 2: 13200, 4: 13600, 6: 15600} ],
    // ghost hands up, then a lean in for the "boo" (the October set)
    boo:       [ {0: 28500, 1: 17800, 2: 28500, 3: 17800}, {6: 20400, 4: 18200, 0: 30500, 2: 30500} ],
    // the glitch: a small stutter of the head, side to side, then level again
    twitch:    [ {5: 18200, 4: 15200}, {5: 14600, 4: 17400}, {5: 17600, 4: 15800}, {5: 16384, 4: 16384} ]
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
  /** In season: a line without `months` is for every month; one with it, for those months of
   *  the visitor's local calendar (1 = January). */
  function inSeason(ln, month) {
    return !Array.isArray(ln.months) || ln.months.indexOf(month) !== -1;
  }
  /** The random bag: every line in season that is not a beat (a glitch or a post-goodbye
   *  aside waits for its moment). The month is read at EVERY pick and a new month refills
   *  the bag, so a page left open over the last night of October stops saying October
   *  things at midnight, and starts on the first. */
  function nextLine() {
    if (!lines || !lines.length) return null;
    var month = new Date().getMonth() + 1;
    if (!bag.length || month !== bagMonth) {
      bag = shuffle(lines.filter(function (l) { return !l.beat && inSeason(l, month); }));
      bagMonth = month;
    }
    return bag.pop() || null;
  }
  var lastBeat = {};
  /** One of the `"beat": kind` lines in season, never the one this beat said last. */
  function pickBeat(kind) {
    if (!lines) return null;
    var month = new Date().getMonth() + 1;
    var pool = lines.filter(function (l) { return l.beat === kind && inSeason(l, month); });
    var fresh = pool.filter(function (l) { return l !== lastBeat[kind]; });
    if (fresh.length) pool = fresh;
    var ln = pool.length ? pool[Math.floor(Math.random() * pool.length)] : null;
    if (ln) lastBeat[kind] = ln;
    return ln;
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
  var composeAt = 0;                        // when the visitor last focused or typed in the box

  /** The visitor is making a line for her: the message box holds text or has focus, or the
   *  mic is open (mic.js's body[data-mic]). Before their FIRST line there is no `.turn` for
   *  the hold to count, so her first quip landed 5-9 s after the unlock, while they typed
   *  (measured on the live site: 5.4-5.7 s before the first send) — and a quip into an open
   *  mic is a quip in the visitor's own clip.
   *  FOCUS ALONE holds her before the first line, and after it only for CHAT_QUIET_MS from
   *  the visitor's last focus or keystroke: Enter sends a typed turn WITHOUT blurring the
   *  box, so "focused" stayed true after the conversation and she never spoke again on a
   *  desktop until they clicked somewhere else. */
  function composing() {
    if (making()) return true;
    var box = document.getElementById("speech-input");
    if (!box) return false;
    if (document.activeElement !== box) return false;
    return lastTurnAt === 0 || (Date.now() - composeAt) < CHAT_QUIET_MS;
  }

  /** The narrow half of composing(): words in the box, or the mic open. Focus alone is not
   *  a line being made, and after a goodbye the box usually still has it. */
  function making() {
    if (document.body && document.body.getAttribute("data-mic") === "on") return true;
    var box = document.getElementById("speech-input");
    return !!(box && box.value && String(box.value).trim());
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

  /** A real turn landed in the log; `fromVisitor` when it is the visitor's own row. */
  function noteTurn(fromVisitor) {
    lastTurnAt = Date.now();
    if (fromVisitor === true) visitorTurns++;
    reflectHold();
    // Repaint when the hold LAPSES, not at the next tick (up to 24 s later).
    clearTimeout(holdTimer);
    holdTimer = setTimeout(reflectHold, CHAT_QUIET_MS + 50);
  }

  /** Inside one of her own `.mutter` rows? A text node is judged by its parent. */
  function inMutter(node) {
    var el = node && node.nodeType === 1 ? node : node && node.parentNode;
    return !!(el && el.closest && el.closest(".mutter"));
  }

  /** Watch the comms log for REAL turns — never our own `.mutter` rows. */
  function watchTranscript() {
    if (watching) return;                   // `start()` may be called more than once
    var el = document.getElementById("transcript");
    if (!el || typeof MutationObserver !== "function") return;
    watching = true;
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        // Her own row, re-worded in place before the first turn (logMutter), changes the
        // children of a `.msg` exactly as a streamed reply does — and is NOT a turn: counted,
        // her second quip put the hold on, so she went quiet with nobody talking and the page
        // said "paused while you're chatting".
        if (inMutter(records[i].target)) continue;
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (n && n.nodeType === 1 && n.classList && n.classList.contains("turn")) {
            noteTurn(n.classList.contains("user"));     // bridge/'s "turn user" row
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
    timer = setTimeout(function () { tick(true); }, d);  // only the timer's tick may glitch
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
  /** Her voice is on the speakers, or ended less than `graceMs` (default SPEAK_GRACE_MS)
   *  ago; `moxieBusy(0)` is the bare "is she speaking". */
  function moxieBusy(graceMs) {
    var a = window.moxieAudio, g = graceMs === undefined ? SPEAK_GRACE_MS : graceMs;
    try { return !!(a && a.isMoxieBusy && a.isMoxieBusy(g)); } catch (e) { return false; }
  }
  /** Server-voice chunks waiting their turn: no speaking predicate sees them yet. */
  function voiceQueued() {
    var a = window.moxieAudio;
    try { return !!(a && a.ttsPending && a.ttsPending() > 0); } catch (e) { return false; }
  }
  function degradedPage() {
    try { return !!(window.moxieMode && window.moxieMode.state() === "degraded"); }
    catch (e) { return false; }
  }

  /* THE GLITCH: rarely, in place of a quip, a "reboot" that ends fine. Her face flickers,
   * the heart LED stutters green, then a twitch and one of ambient.json's `"beat": "glitch"`
   * lines. Only the TIMER's tick may glitch (moxieAmbient.say() and the tests' pokes never
   * do), so it has every guard a quip has (hidden tab, liveness off, the conversation hold,
   * an open mic, her own voice), checked again at every flicker frame: a reply or a keystroke
   * mid-flicker ends it, and the face is put back only if no turn has taken it. At most one
   * per GLITCH_EVERY_MS, none before her GLITCH_AFTER_QUIPS-th quip (she sounds like herself
   * first), none on a degraded page ("rebooting... I am back" would read as the brain
   * coming back). */
  var GLITCH_EVERY_MS = 10 * 60 * 1000;
  var GLITCH_AFTER_QUIPS = 3;
  var GLITCH_CHANCE = 0.1;                  // per eligible quip: one in ten, so not clockwork
  var GLITCH_FLICKER = ["sleep", "surprised", "sleep", "confused", "sleep", "surprised"];
  var GLITCH_FRAME_MS = 140;                // the face eases in ~110 ms (face.js): a real flicker
  var GLITCH_LED = "#39ff14";
  var quips = 0, glitchAt = -Infinity, ft = [];

  function glitchDue() {
    return quips >= GLITCH_AFTER_QUIPS && Date.now() - glitchAt >= GLITCH_EVERY_MS &&
           !degradedPage() && Math.random() < GLITCH_CHANCE;
  }
  function clearFlicker() { ft.forEach(clearTimeout); ft = []; }

  /** Play the glitch beat. False when there is no glitch line (or no avatar). */
  function glitch() {
    var m = window.moxie, ln = pickBeat("glitch");
    if (!m || !ln) return false;
    glitchAt = Date.now();                  // an interrupted glitch still spends the ten minutes
    var led = document.getElementById("led-on"), hadHeart = led ? led.checked : false;
    var turnAt = lastTurnAt;
    var stopped = function () {
      return !running || document.hidden || !livenessOn() || conversing() || moxieBusy() ||
             lastTurnAt !== turnAt;
    };
    var abort = function () {
      clearFlicker();
      try {
        m.setHeartLED(hadHeart);
        if (lastTurnAt === turnAt && !moxieBusy()) m.setFace("neutral");   // else a reply owns it
      } catch (e) {}
    };
    clearFlicker();
    GLITCH_FLICKER.forEach(function (face, i) {
      ft.push(setTimeout(function () {
        if (stopped()) { abort(); return; }
        try { m.setFace(face); m.setHeartLED(i % 2 === 0, GLITCH_LED); } catch (e) {}
      }, i * GLITCH_FRAME_MS));
    });
    ft.push(setTimeout(function () {
      if (stopped()) { abort(); return; }
      ft = [];
      try { m.setHeartLED(hadHeart); } catch (e) {}   // perform() restores what it finds
      perform(ln, "ambient");
    }, GLITCH_FLICKER.length * GLITCH_FRAME_MS));
    return true;
  }

  /* AFTER A GOODBYE: one dry aside to herself once the goodbye is over (ambient.json's
   * `"beat": "signoff"` lines). bridge/actions.js fires `moxie-signoff` when a reply closes
   * the conversation: an `exit_module` action on the robot path, `end_turn: true` from the
   * hosted brain (functions/api/chat.js sends no actions). Never over her own words: it
   * waits until her voice has been heard since the sign-off and has then been quiet for
   * SIGNOFF_GAP_MS (no voice at all, e.g. muted: SIGNOFF_VOICE_WAIT_MS). One aside per
   * sign-off however many signals a reply carries; a new line from the visitor means they
   * did not leave, and it is dropped. Not held by the conversation hold (the conversation is
   * over) or by focus alone (Enter leaves the box focused), only by a line being made. */
  var SIGNOFF_GAP_MS = 3500;                // "a few seconds" after her last syllable
  var SIGNOFF_VOICE_WAIT_MS = 16000;        // past cloud-transport.js's 15 s voice deadline,
                                            // after which the goodbye is spoken locally
  var SIGNOFF_GIVE_UP_MS = 60000;           // no good moment within a minute: let it go
  var SIGNOFF_POLL_MS = 400;
  var signoff = null;                       // {at, voiced, visitorTurns} while one is owed
  var signoffTimer = 0;

  function onSignoff() {
    if (signoff || !running) return;        // one per sign-off; liveness off means quiet
    signoff = { at: Date.now(), voiced: false, visitorTurns: visitorTurns };
    loadOnce();
    pollSignoff();
  }

  function pollSignoff() {
    clearTimeout(signoffTimer);
    var s = signoff;
    if (!s) return;
    var since = Date.now() - s.at;
    if (!running || visitorTurns !== s.visitorTurns || since > SIGNOFF_GIVE_UP_MS) {
      signoff = null;
      return;
    }
    if (moxieBusy(0)) s.voiced = true;
    if ((s.voiced || since >= SIGNOFF_VOICE_WAIT_MS) && since >= SIGNOFF_GAP_MS &&
        !moxieBusy(SIGNOFF_GAP_MS) && !voiceQueued() && !document.hidden && livenessOn() &&
        !making()) {
      signoff = null;
      var ln = pickBeat("signoff");
      if (ln) perform(ln, "ambient");
      return;
    }
    signoffTimer = setTimeout(pollSignoff, SIGNOFF_POLL_MS);
  }

  function tick(scheduled) {
    if (!running) return;
    reflectHold();
    if (document.hidden || !livenessOn()) { schedule(false); return; }
    // Conversation in progress: re-arm, never stop.
    if (conversing()) { schedule(false); return; }
    if (moxieBusy()) { schedule(false); return; }
    if (signoff) { schedule(false); return; }       // a post-goodbye aside is owed first
    var m = window.moxie;
    if (m && scheduled === true && glitchDue() && glitch()) { schedule(false); return; }
    var ln = nextLine();
    if (!m || !ln) { schedule(false); return; }
    perform(ln, "ambient");
    quips++;
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
    clearFlicker(); clearTimeout(signoffTimer); signoff = null;
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
  try { window.addEventListener("moxie-signoff", onSignoff); } catch (e) {}   // AFTER A GOODBYE

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
                          say: function () { running = true; started = true;   // never a glitch
                            (lines ? Promise.resolve() : loadOnce()).then(function () { tick(false); }); },
                          // for tests and manual poking: the degraded line's state
                          degradedState: function () {
                            return { text: degraded && degraded.text ? degraded.text : null,
                                     said: degradedSaid, pending: degradedPending };
                          } };

  /* TEST SEAM, not an API: test_liveliness.mjs shortens the quiet period and reads the
   * recorded state; test_ambient.mjs reads the beats' state and stands in for the
   * transcript observer (`noteTurn(true)` = a visitor's row). Nothing in the page calls it. */
  window.__ambient = {
    quietMs: function (ms) { if (typeof ms === "number" && ms >= 0) CHAT_QUIET_MS = ms; return CHAT_QUIET_MS; },
    state: function () {
      return { running: running, conversing: conversing(), composing: composing(),
               livenessOn: livenessOn(), lastTurnAt: lastTurnAt, watching: watching,
               quips: quips, glitchAt: glitchAt, signoff: !!signoff, visitorTurns: visitorTurns };
    },
    noteTurn: noteTurn,
    say: function (text) { logMutter(text); },
    glitch: function () { return glitch(); }        // the glitch beat now, for a manual look
  };

  /** Repaint the hold as the visitor starts or stops writing — the next tick can be 24 s
   *  away. Deferred a task, so a `blur` is read after focus has really moved. A focus or a
   *  keystroke also restarts the focus hold's quiet period, repainted when it lapses (one
   *  timer with noteTurn's: whichever came last lapses last). */
  function watchComposer() {
    var box = document.getElementById("speech-input");
    if (!box || !box.addEventListener) return;
    var soon = function (e) {
      if (e && e.type !== "blur") {
        composeAt = Date.now();
        clearTimeout(holdTimer);
        holdTimer = setTimeout(reflectHold, CHAT_QUIET_MS + 50);
      }
      setTimeout(reflectHold, 0);
    };
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
