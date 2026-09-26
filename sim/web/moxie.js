// Moxie robot simulator — visual front-end (three.js r160, via sim.html's importmap).
// Exposes window.moxie = { setMotor, getMotor, getAnimationStepCount, setFace, setSpeech,
//   setHeartLED, setMouthOpen, getMouthOpen, showIcons, clearIcons, centerAll, setIdle,
//   setShowAxes, setSceneLight, isAlive, isUserHeld } and fires `moxie-ready`.
//
// Anatomy (docs/architecture/sil-and-cicd.md "Visual reference"): a large egg-shaped HEAD
// on a pear-shaped BODY; curved arm-shell pads hug the flanks, each a shoulder + spring
// elbow ending in a light rounded hand. Face screen + camera lens on the head; speaker
// grille, heart-LED marking and `moxie` wordmark on the body.
//
// Modules: moxie/config.js (motor table), geometry.js, textures.js, face.js (canvas face +
// icons), liveness.js (idle micro-motion), bubble.js (speech bubble), stage.js (framing),
// panel.js (by-hand controls).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import {
  MOTOR_MAX, MOTOR_CENTER, MOTOR_DEFS, MOTOR_REST, COL, BODY_TOP, HEAD_PIVOT_Y, SHOULDER_Y,
  LEAN_PIVOT_Y, motorAngle, springElbowFromMotor,
} from './moxie/config.js';
import {
  welded, smoothLathe, bodyProfilePts, bodyRadiusAt, smoothSphere, makeArmShellGeometry,
  eggify, flattenFront, sphericalUVs, bentPlate, facePanelGeometry,
} from './moxie/geometry.js';
import {
  radialGlow, makeHeadTexture, makeGrilleTexture, makeWordmarkTexture, makeHeartLEDTexture,
  makeLabel,
} from './moxie/textures.js';
import { EXPRESSIONS, face, blink, mouthDrive, icons, faceTex, stepFace, drawFace } from './moxie/face.js';
import { liveness, noteCommand, updateLiveness } from './moxie/liveness.js';
import { showSpeech, updateBubbleAnchor } from './moxie/bubble.js';
import { installStageFraming } from './moxie/stage.js';
import { buildPanel, syncSlider, markFaceButton } from './moxie/panel.js';

// ---------------------------------------------------------------------------
// Renderer / scene / camera / lights
// ---------------------------------------------------------------------------

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
document.getElementById('app').appendChild(renderer.domElement);

const scene = new THREE.Scene();

// Narrow/portrait screens start further back for breathing room; a SHORT viewport (a
// landscape phone, ~375-420 px tall) pulls back further still so the bubble has headroom.
const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 60);
const portraitish = window.innerWidth < 900 || window.innerWidth < window.innerHeight;
const shortish = window.innerHeight < 520;
camera.position.set(portraitish ? 1.4 : 1.8, 2.1, shortish ? 7.4 : (portraitish ? 7.2 : 4.8));

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.15, 0);        // orbit pivots on Moxie's centre
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.enablePan = true;
controls.minDistance = 2.0;
controls.maxDistance = 12.0;
controls.maxPolarAngle = 1.48;
controls.update();

// Deterministic camera placement for screenshot/CI harnesses (debug-only).
window.__setCam = (x, y, z, tx = 0, ty = 1.22, tz = 0) => {
  camera.position.set(x, y, z);
  controls.target.set(tx, ty, tz);
  controls.update();
};

// Cool control-room lighting: white key, cold fill, cyan rim.
const hemi = new THREE.HemisphereLight(0xdcecff, 0x10151d, 0.6);
scene.add(hemi);

const key = new THREE.DirectionalLight(0xffffff, 2.1);
key.position.set(3.2, 5.2, 4.0);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -2.6, right: 2.6, top: 3.2, bottom: -1.5, near: 1, far: 14 });
key.shadow.bias = -0.0004;
key.shadow.radius = 5;
scene.add(key);

const fill = new THREE.DirectionalLight(0xa9d8ff, 0.45);
fill.position.set(-4, 2.2, 1.5);
scene.add(fill);

const rim = new THREE.DirectionalLight(0x66e6ff, 1.0);
rim.position.set(-1.2, 3.4, -4.2);
scene.add(rim);

const ground = new THREE.Mesh(new THREE.CircleGeometry(8, 64), new THREE.ShadowMaterial({ opacity: 0.4 }));
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.12;
ground.receiveShadow = true;
scene.add(ground);

// Void grid + soft cyan glow pad (docs/design/style-guide.md HUD skin).
const grid = new THREE.GridHelper(26, 52, 0x00f0ff, 0x0e7490);
grid.material.transparent = true;
grid.material.opacity = 0.09;
grid.material.depthWrite = false;
grid.position.y = -0.125;
scene.add(grid);

const glowPad = new THREE.Mesh(
  new THREE.CircleGeometry(2.6, 48),
  new THREE.MeshBasicMaterial({
    map: radialGlow([[0, 'rgba(0, 240, 255, 0.20)'], [0.5, 'rgba(0, 240, 255, 0.05)'], [1, 'rgba(0, 240, 255, 0.0)']]),
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
glowPad.rotation.x = -Math.PI / 2;
glowPad.position.y = -0.118;
scene.add(glowPad);

// moxie.setSceneLight: 1 = fully lit studio, 0 = near-dark, where the projected face
// becomes the main light source. The eased level scales the baselines above.
const sceneLight = {
  level: 0.85,
  current: 0.85,
  base: { hemi: 0.6, key: 2.1, fill: 0.45, rim: 1.0, grid: 0.09, pad: 1.0 },
};

function applySceneLight() {
  const s = sceneLight.current;
  const lit = 0.04 + 0.96 * s;                 // never a hard zero
  hemi.intensity = sceneLight.base.hemi * lit;
  key.intensity  = sceneLight.base.key * lit;
  fill.intensity = sceneLight.base.fill * lit;
  rim.intensity  = sceneLight.base.rim * (0.12 + 0.88 * s);  // keep a silhouette
  grid.material.opacity = sceneLight.base.grid * (0.35 + 0.65 * s);
  glowPad.material.opacity = sceneLight.base.pad * (0.45 + 0.55 * s);
  const dark = 1 - s;                          // the face takes over as the scene dims
  screenMat.emissiveIntensity = 0.62 + 1.5 * dark;
  faceLight.intensity = 0.05 + 1.1 * dark;
  faceHalo.material.opacity = 0.55 * dark * dark;
}

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

function plastic(color, extra = {}) {
  return new THREE.MeshPhysicalMaterial({
    color, roughness: 0.52, metalness: 0.0, clearcoat: 0.25, clearcoatRoughness: 0.6, ...extra,
  });
}

const shellMat  = plastic(COL.shell);
const armMat    = plastic(COL.arm);
const handMat   = plastic(COL.hand, { roughness: 0.45 });
const rubberMat = new THREE.MeshStandardMaterial({ color: COL.rubber, roughness: 0.95 });
const baseMat   = new THREE.MeshStandardMaterial({ color: COL.base, roughness: 0.65 });
const lensMat   = new THREE.MeshPhysicalMaterial({
  color: 0x0a0f12, roughness: 0.12, clearcoat: 1.0, clearcoatRoughness: 0.08,
});

function shadowed(mesh) { mesh.castShadow = true; mesh.receiveShadow = true; return mesh; }

// ---------------------------------------------------------------------------
// Rig
//   root
//    +- base disc + rubber ring (static)
//    +- yawG (motor 5)
//        +- breatheG (idle breathing, visual only)
//            +- lowerG: lower torso, grille, wordmark (planted — never leans)
//            +- leanG (motor 6, pivot at the chest seam) -> upperG
//                +- upper torso, neck, heart LED, arms
//                +- headTiltG (motor 4) -> headRollG -> headForm -> head, lens, face
// ---------------------------------------------------------------------------

const axisNodes = [];                      // [{name, node}] for the debug overlay
function registerAxisNode(name, node) { axisNodes.push({ name, node }); }

const root = new THREE.Group();
scene.add(root);

const baseDisc = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.60, 0.66, 0.14, 64), baseMat));
baseDisc.position.y = -0.05;
root.add(baseDisc);

const ring = new THREE.Mesh(new THREE.TorusGeometry(0.615, 0.055, 20, 80), rubberMat);
ring.rotation.x = Math.PI / 2;
ring.position.y = 0.025;
ring.castShadow = true;
root.add(ring);

const yawG = new THREE.Group();
root.add(yawG);

// Moxie leans at a single joint ABOVE the speaker, so the body mesh is split there.
const breatheG = new THREE.Group();
yawG.add(breatheG);
const lowerG = new THREE.Group();
breatheG.add(lowerG);
const leanG = new THREE.Group();
leanG.position.y = LEAN_PIVOT_Y;
breatheG.add(leanG);
const upperG = new THREE.Group();            // carrier so upper parts keep their absolute Y
upperG.position.y = -LEAN_PIVOT_Y;
leanG.add(upperG);

const seamR = bodyRadiusAt(LEAN_PIVOT_Y);
const seam = new THREE.Vector2(seamR, LEAN_PIVOT_Y);
lowerG.add(shadowed(new THREE.Mesh(
  smoothLathe(bodyProfilePts.filter(p => p.y <= LEAN_PIVOT_Y).concat([seam])), shellMat)));
upperG.add(shadowed(new THREE.Mesh(
  smoothLathe([seam].concat(bodyProfilePts.filter(p => p.y > LEAN_PIVOT_Y))), shellMat)));

// Short, chunky neck: clearance for a full forward head tilt, nearly hidden at rest.
const neckGeo = welded(new THREE.CylinderGeometry(0.30, 0.345, 0.22, 48, 2));
neckGeo.computeVertexNormals();
const neck = shadowed(new THREE.Mesh(neckGeo, new THREE.MeshPhysicalMaterial({
  color: new THREE.Color(COL.shell).multiplyScalar(0.82),   // slightly recessed/darker
  roughness: 0.62, clearcoat: 0.25, flatShading: false,
})));
neck.position.set(0, BODY_TOP - 0.05, 0);
upperG.add(neck);

// Head: a large egg sitting on the body (Moxie is top-heavy).
const headTiltG = new THREE.Group();          // motor 4
headTiltG.position.y = HEAD_PIVOT_Y;
upperG.add(headTiltG);
const headRollG = new THREE.Group();
headTiltG.add(headRollG);
const headForm = new THREE.Group();           // constant slight forward tilt
headForm.rotation.x = 0.05;
headRollG.add(headForm);

const HEAD_C = new THREE.Vector3(0, 0.64, 0.06);      // head centre (local)
const HEAD_R = new THREE.Vector3(0.66, 0.60, 0.63);   // wider than tall, pointy top

const head = shadowed(new THREE.Mesh(
  sphericalUVs(flattenFront(eggify(smoothSphere(72, 52), HEAD_R.x, HEAD_R.y, HEAD_R.z), 0.335, 0.05), HEAD_R.y),
  plastic(0xffffff, { map: makeHeadTexture() })));
head.position.copy(HEAD_C);
headForm.add(head);

// Camera: a small recessed lens on the face plane, inside the dark zone drawFace paints.
const lens = new THREE.Mesh(new THREE.SphereGeometry(0.042, 24, 16), lensMat);
lens.scale.set(1, 0.85, 0.35);
lens.rotation.x = -0.08;
lens.position.set(0, 0.980, 0.410);
headForm.add(lens);
const lensDot = new THREE.Mesh(new THREE.SphereGeometry(0.013, 12, 8),
  new THREE.MeshBasicMaterial({ color: 0x3a5560 }));
lensDot.position.set(0.010, 0.985, 0.424);
headForm.add(lensDot);

// Face screen: a large shallow elliptical panel on the head's flattened front. Matte (a
// projection screen, not glass — a clearcoat read as a light inside the head); the face
// canvas is also the emissive map, so the features glow from within.
const FACE_RX = 0.545, FACE_RY = 0.465;
const faceAssembly = new THREE.Group();
faceAssembly.position.set(0, 0.580, 0.0);
headForm.add(faceAssembly);

const screenMat = new THREE.MeshPhysicalMaterial({
  map: faceTex, emissive: 0xffffff, emissiveMap: faceTex, emissiveIntensity: 0.45,
  roughness: 1.0, metalness: 0.0, clearcoat: 0.0, reflectivity: 0.0,
});
const screen = new THREE.Mesh(facePanelGeometry(FACE_RX, FACE_RY), screenMat);
screen.position.z = 0.422;
faceAssembly.add(screen);

// Projector spill light, placed in front of and BELOW the pane so it lights the chest
// without a hotspot on the screen. Scales up as the scene dims.
const faceLight = new THREE.PointLight(0xf3eedd, 0.15, 3.4, 2);
faceLight.position.set(0, -0.34, 1.15);
faceAssembly.add(faceLight);

// Warm annulus AROUND the pane (clear centre, so it never veils the face).
const faceHalo = new THREE.Sprite(new THREE.SpriteMaterial({
  map: radialGlow([[0, 'rgba(255, 248, 224, 0.0)'], [0.52, 'rgba(255, 248, 224, 0.0)'],
                   [0.62, 'rgba(255, 248, 224, 0.50)'], [1, 'rgba(255, 244, 214, 0.0)']]),
  transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending,
}));
faceHalo.scale.set(1.65, 1.75, 1);
faceHalo.position.set(0, 0, 0.55);
faceAssembly.add(faceHalo);

// Body decals, printed on an open cylinder patch so they follow the shell.
function decal(geo, map, extra) {
  return new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
    map, transparent: true, polygonOffset: true, polygonOffsetFactor: -1, ...extra }));
}
// Speaker grille: near-square patch (arc ~ height) so the round dot field stays round.
const grille = decal(new THREE.CylinderGeometry(0.646, 0.642, 0.375, 48, 1, true, -0.2975, 0.595),
  makeGrilleTexture(), { roughness: 0.8 });
grille.position.set(0, 0.42, 0);
lowerG.add(grille);
const wordmark = decal(new THREE.CylinderGeometry(0.622, 0.602, 0.11, 32, 1, true, -0.30, 0.60),
  makeWordmarkTexture(), { roughness: 0.7 });
wordmark.position.set(0, 0.155, 0);
lowerG.add(wordmark);

// Heart LED: a light-grey marking when off, glowing in the commanded colour when on.
const heartTex = makeHeartLEDTexture();
const heartMat = new THREE.MeshStandardMaterial({
  map: heartTex, color: 0xdfe9ea, transparent: true, emissive: 0xffffff, emissiveMap: heartTex,
  emissiveIntensity: 0.10, roughness: 0.55, polygonOffset: true, polygonOffsetFactor: -2,
});
const heart = new THREE.Mesh(
  bentPlate(new THREE.CircleGeometry(1, 48), 0.17, 0.17, bodyRadiusAt(1.08), 1.6), heartMat);
heart.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.006);
heart.rotation.x = -0.20;             // follows the chest's backward taper
upperG.add(heart);

const heartLight = new THREE.PointLight(0xff5577, 0, 1.2);
heartLight.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.12);
upperG.add(heartLight);

const heartState = { on: false, color: new THREE.Color(0xff5577) };

// Arms: one constant-width curved shell per arm hugging the flank; upper arm, forearm and
// hand share a width (the hand is the lighter rounded tip). The forearm starts ABOVE the
// elbow pivot so the two shells overlap through the whole fold.
const ARM_HALF_W = 0.21;
const ARM_THICK  = 0.065;

function makeArm(side) {  // side = +1 robot-left (+X), -1 robot-right (-X)
  const shoulderPivot = new THREE.Vector3(side * (bodyRadiusAt(SHOULDER_Y) + 0.01), SHOULDER_Y, 0);
  const elbowY = 0.76;
  const elbowPivot = new THREE.Vector3(side * (bodyRadiusAt(elbowY) + 0.01), elbowY, 0.04);

  const armRoot = new THREE.Group();
  armRoot.position.copy(shoulderPivot);
  const shoulder = new THREE.Group();
  armRoot.add(shoulder);
  shoulder.add(shadowed(new THREE.Mesh(
    makeArmShellGeometry(side, 1.27, 0.767, ARM_HALF_W, ARM_THICK, 0.22, shoulderPivot), armMat)));

  // Coplanar hinge: the arm folds like a flat sheet of cardboard.
  const elbow = new THREE.Group();
  elbow.position.copy(elbowPivot).sub(shoulderPivot);
  shoulder.add(elbow);
  elbow.add(shadowed(new THREE.Mesh(
    makeArmShellGeometry(side, 0.92, 0.34, ARM_HALF_W * 0.97, ARM_THICK * 0.94, 0.26, elbowPivot, 0.012), armMat)));
  elbow.add(shadowed(new THREE.Mesh(
    makeArmShellGeometry(side, 0.44, 0.15, ARM_HALF_W, ARM_THICK, 0.30, elbowPivot, 0.012), handMat)));

  upperG.add(armRoot);
  registerAxisNode(side > 0 ? 'armRootL (robot LEFT, +X)' : 'armRootR (robot RIGHT, -X)', armRoot);
  registerAxisNode(side > 0 ? 'shoulderL (motor 0)' : 'shoulderR (motor 2)', shoulder);
  registerAxisNode(side > 0 ? 'elbowL (spring)' : 'elbowR (spring)', elbow);
  return { shoulder, elbow, elbowSign: -side };   // fold inward = toward the body
}

// Motor L/R names are from the ROBOT's perspective (the board silkscreen, fcc-teardown.md);
// the camera looks from +Z, so the robot's left arm is at +X.
const armL = makeArm(+1);
const armR = makeArm(-1);

registerAxisNode('yawG (motor 5, base pivot)', yawG);
registerAxisNode('lowerG (planted: speaker)', lowerG);
registerAxisNode('leanG (motor 6, waist ABOVE speaker)', leanG);
registerAxisNode('breatheG (torso)', breatheG);
registerAxisNode('headTiltG (motor 4)', headTiltG);

// Debug overlay (moxie.setShowAxes): labelled RGB triads at the world origin and at every
// registered rig node, so joint placement can be inspected rather than inferred.
const axisGroup = new THREE.Group();
axisGroup.visible = false;
scene.add(axisGroup);
const axisAttached = [];
let axesBuilt = false;

function setShowAxes(on) {
  if (!axesBuilt) {
    axesBuilt = true;
    const w = new THREE.AxesHelper(0.9);
    w.material.depthTest = false; w.renderOrder = 998;
    axisGroup.add(w);
    const wl = makeLabel('WORLD origin (0,0,0)  X→red Y→green Z→blue', '#00f0ff');
    wl.position.set(0.5, 0.06, 0);
    axisGroup.add(wl);
    for (const { name, node } of axisNodes) {
      const a = new THREE.AxesHelper(0.34);
      a.material.depthTest = false; a.renderOrder = 998;
      node.add(a);
      const l = makeLabel(name, '#fcee0a');
      l.position.set(0.2, 0.07, 0);
      node.add(l);
      axisAttached.push(a, l);
    }
  }
  axisGroup.visible = !!on;
  for (const o of axisAttached) o.visible = !!on;
}

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
};

window.moxie = api;
window.dispatchEvent(new CustomEvent('moxie-ready', { detail: api }));

buildPanel(api, motorTargets);

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

const clock = new THREE.Clock();

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
  const a = Array.from(motorValues, (v, i) => motorAngle(i, v));

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
  applySceneLight();

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
