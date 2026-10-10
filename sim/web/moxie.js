// Moxie robot simulator — visual front-end (three.js r160, via sim.html's importmap).
// Exposes window.moxie = { setMotor, getMotor, getAnimationStepCount, setFace, setSpeech,
//   setHeartLED, setMouthOpen, getMouthOpen, showIcons, clearIcons, centerAll, setIdle,
//   setShowAxes, setSceneLight, isAlive, isUserHeld, tapStats } and fires `moxie-ready`.
// A tap on her says hello once, then reacts with her face (A TAP ON HER, below).
//
// Anatomy (docs/architecture/sil-and-cicd.md "Visual reference"): a large egg-shaped HEAD
// on a pear-shaped BODY; curved arm-shell pads hug the flanks, each a shoulder + spring
// elbow ending in a light rounded hand. Face screen + camera lens on the head; speaker
// grille, heart-LED marking and `moxie` wordmark on the body.
//
// This file is the public API and the per-frame loop. Modules (moxie/README.md): scene.js
// (renderer, camera, lights), rig.js (the jointed robot), config.js (motor table),
// geometry.js, textures.js, face.js (canvas face + icons), liveness.js (idle micro-motion),
// bubble.js (speech bubble), stage.js (framing), panel.js (by-hand controls).

import * as THREE from 'three';
import { MOTOR_MAX, MOTOR_CENTER, MOTOR_DEFS, MOTOR_REST, motorAngle, springElbowFromMotor } from './moxie/config.js';
import { EXPRESSIONS, face, blink, mouthDrive, icons, stepFace, drawFace } from './moxie/face.js';
import { liveness, noteCommand, updateLiveness } from './moxie/liveness.js';
import { showSpeech, updateBubbleAnchor } from './moxie/bubble.js';
import { installStageFraming } from './moxie/stage.js';
import { buildPanel, syncSlider, markFaceButton } from './moxie/panel.js';
import { renderer, scene, camera, controls, sceneLight, applySceneLight, onStageTap, hitsAt } from './moxie/scene.js';
import {
  head, headTiltG, yawG, breatheG, leanG, armL, armR, screenMat, faceLight, faceHalo,
  heartState, heartMat, heartLight, setShowAxes,
} from './moxie/rig.js';

const faceLighting = { screenMat, faceLight, faceHalo };

// A TAP ON HER's record (the section is at the bottom of this file).
const tapRecord = { taps: 0, misses: 0, hellos: 0, faces: 0, said: '', last: '' };

// ---------------------------------------------------------------------------
// Motor state
// ---------------------------------------------------------------------------

const motorTargets = new Float32Array(MOTOR_REST);
const motorValues  = new Float32Array(MOTOR_REST);
// Monotonic count of animation steps, so consumers can tell their own rAF callbacks from
// render steps completed here. Observable, not writable.
let animationStepCount = 0;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

const api = {
  MOTOR_MAX,
  MOTOR_CENTER,
  motorNames: MOTOR_DEFS.map(d => d.name),
  expressions: Object.keys(EXPRESSIONS).concat('blink'),

  setMotor(index, value) {
    const i = index | 0;
    if (i < 0 || i > 6 || !Number.isFinite(value)) return;
    motorTargets[i] = Math.min(MOTOR_MAX, Math.max(0, value));
    noteCommand(i);
    syncSlider(i, motorTargets[i]);
  },

  getMotor(index) {
    const i = index | 0;
    if (i < 0 || i > 6) return undefined;
    return Math.round(motorValues[i]);
  },

  getAnimationStepCount() { return animationStepCount; },

  setFace(expression) {
    if (expression === 'blink') {
      blink.active = true;
      blink.phase = 0;
      return;
    }
    const e = EXPRESSIONS[expression];
    if (!e) { console.warn('moxie.setFace: unknown expression', expression); return; }
    face.target = { ...e };
    markFaceButton(expression);
  },

  setSpeech(text) {
    if (typeof text !== 'string' || !text.trim()) return;
    showSpeech(text.trim());
  },

  // Lip-sync drive from the audio layer, 0..1; only opens the mouth further.
  setMouthOpen(v) {
    if (!Number.isFinite(v)) return;
    mouthDrive.v = Math.min(1, Math.max(0, v));
  },

  // Current lip-sync drive — lets the audio layer and tests observe the mouth moving.
  getMouthOpen() { return mouthDrive.v; },

  setHeartLED(on, colorHex) {
    heartState.on = !!on;
    if (colorHex !== undefined) {
      heartState.color.set(colorHex);
      const el = document.getElementById('led-color');
      if (el) el.value = '#' + heartState.color.getHexString();
    }
    const chk = document.getElementById('led-on');
    if (chk) chk.checked = heartState.on;
  },

  showIcons(names) {
    if (!Array.isArray(names)) {
      console.warn('moxie.showIcons: expected an array of icon names');
      return;
    }
    const list = names.filter(n => typeof n === 'string' && n.trim().length).slice(0, 4).map(n => n.trim());
    if (!list.length) { api.clearIcons(); return; }
    icons.names = list;
    icons.shownAt = performance.now();
    icons.fading = false;
  },

  clearIcons() {
    if (!icons.names.length || icons.fading) return;
    icons.fading = true;
    icons.fadeAt = performance.now();
  },

  centerAll() {
    for (let i = 0; i < 7; i++) api.setMotor(i, MOTOR_REST[i]);
  },

  setShowAxes(on) { setShowAxes(on); },
  // Toggle the idle liveness layer (breathing/blink stay subtle regardless).
  setIdle(on) { liveness.enabled = on !== false; },

  // Hooks for life.js, which drives Moxie through setMotor/setFace to "live".
  isAlive() { return liveness.enabled && liveness.master > 0.6; },
  isUserHeld(i) {
    return (i | 0) >= 0 && (i | 0) < 7 && performance.now() - liveness.userAt[i | 0] < 5000;
  },

  // 0 (near-dark — the projected face lights the room) to 1 (fully lit), eased.
  setSceneLight(level) {
    if (!Number.isFinite(level)) return;
    sceneLight.level = Math.min(1, Math.max(0, level));
  },

  // What taps on her did, recorded as they happened (A TAP ON HER). Tests read this.
  tapStats() { return { ...tapRecord }; },
};

window.moxie = api;
window.dispatchEvent(new CustomEvent('moxie-ready', { detail: api }));

buildPanel(api, motorTargets);

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

const clock = new THREE.Clock();
const a = new Float32Array(7);          // this frame's joint angles (reused, no per-frame alloc)

function animate() {
  requestAnimationFrame(animate);
  animationStepCount++;
  updateBubbleAnchor(head, camera);   // keep her words on her head, whatever the camera does
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = clock.elapsedTime;
  const now = performance.now();

  const k = 1 - Math.exp(-dt * 7);
  for (let i = 0; i < 7; i++) {
    motorValues[i] += (motorTargets[i] - motorValues[i]) * k;
    if (Math.abs(motorTargets[i] - motorValues[i]) < 2) motorValues[i] = motorTargets[i];
  }

  const live = updateLiveness(t, dt, now, motorTargets, motorValues);
  for (let i = 0; i < 7; i++) a[i] = motorAngle(i, motorValues[i]);

  armL.shoulder.rotation.x = a[0] + live[0];    // up/down  (motor 0)
  armL.shoulder.rotation.z = a[1] + live[1];    // in/out   (motor 1)
  armR.shoulder.rotation.x = a[2] + live[2];    // up/down  (motor 2)
  armR.shoulder.rotation.z = a[3] + live[3];    // in/out   (motor 3)
  // The spring elbow is released by the arm swinging AWAY from the body (motors 1/3).
  armL.elbow.rotation.z = armL.elbowSign * springElbowFromMotor(motorValues[1]);
  armR.elbow.rotation.z = armR.elbowSign * springElbowFromMotor(motorValues[3]);
  headTiltG.rotation.x = a[4] + live[4];
  yawG.rotation.y      = a[5] + live[5];
  leanG.rotation.x     = a[6] + live[6];

  // breathing — slow body scale + vertical bob (~4.3 s cycle)
  const breath = Math.sin(t * (Math.PI * 2 / 4.3)) * liveness.master;
  breatheG.scale.set(1 - 0.004 * breath, 1 + 0.011 * breath, 1 - 0.004 * breath);
  breatheG.position.y = 0.005 * breath;

  sceneLight.current += (sceneLight.level - sceneLight.current) * (1 - Math.exp(-dt * 5));
  applySceneLight(faceLighting);

  stepFace(t, dt);
  drawFace(t);

  if (heartState.on) {
    const pulse = 0.75 + 0.35 * Math.sin(t * 2.6);
    heartMat.emissive.copy(heartState.color);
    heartMat.emissiveIntensity = 1.3 * pulse;
    heartLight.color.copy(heartState.color);
    heartLight.intensity = 0.6 * pulse;
  } else {
    heartMat.emissive.set(0xffffff);
    heartMat.emissiveIntensity = 0.10;
    heartLight.intensity = 0;
  }

  controls.update();
  renderer.render(scene, camera);
}

animate();

// Project a world point to screen px (respects the view offset) — for the responsive tests.
window.__moxieProject = function (x, y, z) {
  const v = new THREE.Vector3(x, y, z).project(camera);
  return { x: (v.x * 0.5 + 0.5) * window.innerWidth,
           y: (1 - (v.y * 0.5 + 0.5)) * window.innerHeight };
};

installStageFraming(camera, renderer);

// ---------------------------------------------------------------------------
// A TAP ON HER: a hello the first time, a face after that
// ---------------------------------------------------------------------------
/* Tapping Moxie herself did nothing (the stage's only input was OrbitControls): her first
 * sound came 6.3 s (desktop) or 9.5 s (phone) after a first tap, from ambient.js's first
 * quip. Now the FIRST tap on her says hello: Bht_Gesture_Greet (bridge/body.js: a happy face
 * and a wave) and one of the three greetings that ship as clips (audio/index.json, `moxie`
 * group), through the normal local voice path: no gateway, no turn, nothing in the log. Every
 * later tap is a FACE (a blink, and a smile when nothing else owns her face), never a sound.
 * The greeting holds the speakers from the tap, before its clip has loaded (voice/local.js,
 * THE CLAIM): an ambient tick in that window waits, and a newer reply takes them over.
 *
 * The hello is for somebody who has not started talking to her yet. It is never said:
 *   · over her voice, its last syllable, or a reply queued for the speakers (speak() would
 *     stop them);
 *   · once a conversation exists: a `.turn` in the log (a line sent, in flight or answered)
 *     or Listen pressed (a clip may be on its way up);
 *   · into an open microphone (body[data-mic]);
 *   · before /api/health has answered (mode.js `boot`): the page cannot know yet whether her
 *     brain is out, and when it was, the degraded line the answer brought cut the greeting a
 *     second in;
 *   · on a page whose brain is out: ambient.js's one degraded line is her hello there, said on
 *     this same unlock (it would stop a hello, or, said after it, she would greet twice), and
 *     from the answer on, before that line has even loaded;
 *   · with ALIVE off (the visitor asked to drive her by hand: no wave).
 * A tap refused for one of these does not spend it. */
const GREETINGS = [
  'Hi! I am Moxie. It is nice to meet you.',
  'Hello there! I am so happy to see you.',
  "Hi there! It's so good to see you.",
];
const greeting = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
const VOICE_TAIL_MS = 500;                  // a tap as a line ends waits out its last syllable
const LINE_FACE_MS = 4000;                  // a reply's face is its own this long (life.js)
const TAP_SLOP_PX = { touch: 14, mouse: 4 };  // around the tap point, still "on her"
const rig = yawG.parent || yawG;            // the whole robot: base, body, arms, head

function voiceBusy() {
  try {
    const a = window.moxieAudio;
    return !!(a && ((a.isMoxieBusy && a.isMoxieBusy(VOICE_TAIL_MS)) || (a.ttsPending && a.ttsPending() > 0)));
  } catch { return false; }
}
const micOpen = () => document.body.getAttribute('data-mic') === 'on';
let listened = false;                       // Listen was pressed on this page
function talking() {
  if (listened || document.querySelector('#transcript .turn')) return true;
  try {
    const m = window.moxieMic;
    return !!(m && ((m.isRecording && m.isRecording()) || (m.stats && m.stats().starts > 0)));
  } catch { return false; }
}
/** No answer from /api/health yet: mode.js is still in `boot`. A page without mode.js has
 *  nothing to wait for. */
function booting() {
  try { const m = window.moxieMode; return !!(m && m.state && m.state() === 'boot'); } catch { return false; }
}
/** Her brain is out: mode.js says `degraded`, from the answer on, while ambient.js may still
 *  be loading the line it will say about it (ambient.json, asked for only then); or that line
 *  is armed, or was said. */
function brainOut() {
  try {
    const m = window.moxieMode;
    if (m && m.state && m.state() === 'degraded') return true;
    const s = window.moxieAmbient && window.moxieAmbient.degradedState && window.moxieAmbient.degradedState();
    return !!(s && s.text && (s.pending || s.said));
  } catch { return false; }
}
const aliveOn = () => { const c = document.getElementById('idle-on'); return !c || c.checked; };

/** Why the hello may not be said now, or '' when it may. */
function helloRefused() {
  if (tapRecord.hellos) return 'said';
  if (!aliveOn()) return 'alive-off';
  if (micOpen()) return 'mic';
  if (talking()) return 'talking';
  if (voiceBusy()) return 'speaking';
  if (booting()) return 'booting';
  if (brainOut()) return 'brain-out';
  return '';
}

function hello() {
  tapRecord.hellos++;
  tapRecord.said = greeting;
  const B = window.__moxieBridge;           // bridge/body.js plays the Bht_* trees
  if (B && typeof B.behaviourTree === 'function') B.behaviourTree('Bht_Gesture_Greet');
  else api.setFace('happy');
  api.setSpeech(greeting);
  try { if (window.moxieAudio) window.moxieAudio.speak(greeting, 'moxie'); } catch {}
}

/** A blink, and a smile when her face is free: not while a voice of hers or a reply's face
 *  owns it (life.js's rule), with the mic open, or while the visitor's line awaits her answer. */
function faceOnly() {
  tapRecord.faces++;
  let free = !voiceBusy() && !micOpen();
  try {
    const b = window.moxieBridge;
    if (b && b.msSinceLine && b.msSinceLine() < LINE_FACE_MS) free = false;
    const rows = document.querySelectorAll('#transcript .turn');
    if (rows.length && rows[rows.length - 1].classList.contains('user')) free = false;
  } catch {}
  if (free) api.setFace('happy');
  api.setFace('blink');
}

onStageTap((t) => {
  if (!hitsAt(t.x, t.y, rig, t.touch ? TAP_SLOP_PX.touch : TAP_SLOP_PX.mouse)) { tapRecord.misses++; return; }
  tapRecord.taps++;
  const why = helloRefused();
  tapRecord.last = why || 'hello';
  if (why) faceOnly(); else hello();
});
const micBtn = document.getElementById('mic-btn');
if (micBtn) micBtn.addEventListener('click', () => { listened = true; });
