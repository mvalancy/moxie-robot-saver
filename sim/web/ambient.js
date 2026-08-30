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
 */
(function () {
  "use strict";
  var lines = null, bag = [], timer = 0, relax = 0, gt = [], running = false, started = false;

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
      .then(function (j) { lines = (j && j.lines) || []; return lines; })
      .catch(function () { lines = []; return lines; });
  }
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

  function tick() {
    if (!running) return;
    if (document.hidden || !livenessOn()) { schedule(false); return; }
    var m = window.moxie, ln = nextLine();
    if (!m || !ln) { schedule(false); return; }

    var ledChk = document.getElementById("led-on");
    var hadHeart = ledChk ? ledChk.checked : false;
    try {
      if (ln.face) m.setFace(ln.face);
      if (ln.heart) m.setHeartLED(true, ln.heart);
      if (Array.isArray(ln.icons)) m.showIcons(ln.icons);
      if (ln.gesture) playGesture(ln.gesture);
      m.setSpeech(ln.text);
      if (window.moxieAudio) window.moxieAudio.speak(ln.text, "ambient");
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

    schedule(false);
  }

  function start(initial) {
    if (running) return;
    running = true;
    var kick = function () { schedule(initial); };
    if (!started) { started = true; load().then(kick); } else kick();
  }
  function stop() { running = false; clearTimeout(timer); clearTimeout(relax); clearGesture(); }

  function boot() {
    var idle = document.getElementById("idle-on");
    if (idle) idle.addEventListener("change", function () { idle.checked ? start(true) : stop(); });
    // Browsers block sound until the user interacts, so wait for the audio unlock
    // before the first quip — otherwise Moxie mimes silently and looks broken.
    var kick = function () { if (livenessOn()) start(true); };
    if (window.moxieAudio && window.moxieAudio.isUnlocked && window.moxieAudio.isUnlocked()) kick();
    else window.addEventListener("moxie-audio-unlocked", kick, { once: true });
  }

  // expose for tests / manual poking
  window.moxieAmbient = { start: function () { start(false); }, stop: stop,
                          gesture: playGesture,
                          say: function () { running = true; started = true;
                            (lines ? Promise.resolve() : load()).then(tick); } };

  if (window.moxie) boot();
  else window.addEventListener("moxie-ready", boot, { once: true });
})();
