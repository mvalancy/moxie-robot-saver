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
    if (document.hidden || !livenessOn()) { schedule(false); return; }
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
  function stop() { running = false; clearTimeout(timer); clearTimeout(relax); clearGesture(); }

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

  watchMode();          // sim.html loads mode.js BEFORE ambient.js, so it is already there

  if (window.moxie) boot();
  else window.addEventListener("moxie-ready", boot, { once: true });
})();
