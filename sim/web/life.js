/* life.js — Moxie's autonomous "imaginary life" for the SIL.
 *
 * A standalone consumer of the window.moxie API (like bridge.js / ambient.js). While
 * ALIVE it plays coordinated idle "beats" — look around, weight-shift, attentive
 * listen, curious tilt, arm fidget, mood shifts, the occasional stretch or wonder —
 * mirroring the robot's NodeCanvas idle states (behavior-tree-engine.md). It drives
 * the REAL motor targets through window.moxie.setMotor, so the sliders animate
 * smoothly exactly like the 60 Hz Lizard servo loop.
 *
 * It NEVER touches a joint you grabbed in the last few seconds (window.moxie
 * .isUserHeld), so you can override any control live while Moxie keeps living around
 * it. Toggle with the ALIVE button; off = the loop stops and you have full manual
 * control (joints stay wherever they are).
 */
(function () {
  "use strict";
  var rnd = function (a, b) { return a + Math.random() * (b - a); };
  // weighted-cute pool for autonomous mood shifts (mostly calm, occasional spice)
  var FACES = ["neutral", "neutral", "neutral", "happy", "happy", "curious",
               "curious", "thinking", "shy", "surprised", "confused"];
  var nextAt = 0, running = true, pending = [];

  function m() { return window.moxie; }
  function drive(i, v) { var M = m(); if (M && !M.isUserHeld(i)) M.setMotor(i, Math.round(v)); }
  function face(n) { var M = m(); if (M) M.setFace(n); }
  // schedule a "return toward rest" that only fires if still alive (so toggling ALIVE
  // off mid-gesture freezes the pose for manual control instead of yanking it back)
  function later(ms, fn) { pending.push(setTimeout(function () { if (m() && m().isAlive()) fn(); }, ms)); }

  // One coordinated idle beat. Returns how long (seconds) to hold before the next.
  function beat() {
    var M = m(); if (!M) return 2.5;
    var C = M.MOTOR_CENTER, dir = Math.random() < 0.5 ? -1 : 1, r = Math.random();

    if (r < 0.35) {                    // look around — head + yaw move together
      drive(5, C + dir * rnd(3500, 8500));
      drive(4, C + rnd(-3500, 2500));
      if (Math.random() < 0.5) face("curious");
      return rnd(2.0, 3.6);
    } else if (r < 0.50) {             // weight shift / settle
      drive(6, C + rnd(-2800, 2800));
      drive(5, C + dir * rnd(1000, 3000));
      return rnd(2.4, 4.0);
    } else if (r < 0.62) {             // attentive listen — lean in, head dips a touch
      drive(6, C + rnd(2200, 4200));
      drive(4, C + rnd(1500, 3500));
      if (Math.random() < 0.4) face("neutral");
      return rnd(2.2, 3.4);
    } else if (r < 0.74) {             // curious tilt
      drive(4, C - rnd(1500, 3500));
      drive(5, C + dir * rnd(1500, 3500));
      face("curious");
      return rnd(1.8, 3.0);
    } else if (r < 0.84) {             // arm fidget — one shoulder eases up + out, then back
      var s = dir < 0 ? 0 : 2, o = dir < 0 ? 1 : 3;
      drive(s, C + rnd(4000, 9000)); drive(o, rnd(3000, 8000));
      later(1500, function () { drive(s, C); drive(o, 0); });
      return rnd(2.0, 3.0);
    } else if (r < 0.92) {             // expression beat — small head move + mood
      face(FACES[(Math.random() * FACES.length) | 0]);
      drive(4, C + rnd(-2000, 2000));
      return rnd(2.2, 3.6);
    } else if (r < 0.97) {             // stretch / reach up (rare), then relax
      drive(0, C + rnd(8000, 12000)); drive(2, C + rnd(8000, 12000)); drive(4, C - rnd(2000, 4000));
      face("happy");
      later(1700, function () { drive(0, C); drive(2, C); drive(4, C); });
      return rnd(1.8, 2.6);
    } else {                           // glance up / wonder (rare)
      drive(4, C - rnd(3000, 5000));
      face(Math.random() < 0.5 ? "surprised" : "curious");
      return rnd(1.4, 2.2);
    }
  }

  function tick() {
    requestAnimationFrame(tick);
    var M = m(); if (!M || !M.isAlive) return;
    var now = performance.now() / 1000;
    if (running && M.isAlive() && now >= nextAt) nextAt = now + beat();
  }
  requestAnimationFrame(tick);

  // Expose start/stop so the ALIVE toggle (sim.html) can drive it alongside setIdle().
  window.moxieLife = {
    start: function () { running = true; },
    stop: function () { running = false; pending.forEach(clearTimeout); pending = []; },
    isRunning: function () { return running; },
    // trigger a beat immediately (used by tests / manual poke)
    beatNow: function () { nextAt = 0; },
  };
})();
