// The stage: renderer, camera + orbit controls, control-room lighting, the floor, the
// scene-light dimmer (moxie.setSceneLight), and what a tap on the stage hits (onStageTap,
// hitsAt). No robot here — that is rig.js.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { radialGlow } from './textures.js';

export const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
document.getElementById('app').appendChild(renderer.domElement);

export const scene = new THREE.Scene();

// Narrow/portrait screens start further back for breathing room; a SHORT viewport (a
// landscape phone, ~375-420 px tall) pulls back further still so the bubble has headroom.
export const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 60);
const portraitish = window.innerWidth < 900 || window.innerWidth < window.innerHeight;
const shortish = window.innerHeight < 520;
camera.position.set(portraitish ? 1.4 : 1.8, 2.1, shortish ? 7.4 : (portraitish ? 7.2 : 4.8));

export const controls = new OrbitControls(camera, renderer.domElement);
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

/* A TAP ON THE STAGE. OrbitControls owns this canvas (drag to orbit, pinch to zoom); a
 * press by ONE pointer that ends where it began, soon, is a tap instead, and `fn({x, y,
 * touch})` gets its client position. It is read on POINTERUP: for a finger that is an
 * activation (for a mouse the press before it was), and the same gesture's touchend or click
 * is what voice/index.js unlocks audio on, so whatever a tap starts may make sound. */
const TAP_SLOP_PX = 10;     // moved further than this: a drag (an orbit), not a tap
const TAP_MAX_MS = 700;     // held longer than this: a press-and-hold, not a tap
/* How long the finger was down is read off the EVENTS' own clocks, not off when their handlers
 * ran: a slow phone's first frames can hold the main thread for most of a second, and a quick
 * tap landing then had its lift handled 700+ ms after its press and was dropped as a hold. */
const stamp = (e) => (e.timeStamp > 0 ? e.timeStamp : performance.now());
export function onStageTap(fn) {
  const el = renderer.domElement;
  const down = new Map();   // pointerId -> where and when it went down
  let pinch = false;        // a second pointer joined: nothing in this gesture is a tap
  el.addEventListener('pointerdown', (e) => {
    if (e.button > 0) return;                  // a right or middle button is not a tap
    if (down.size) pinch = true;
    down.set(e.pointerId, { x: e.clientX, y: e.clientY, t: stamp(e), moved: false });
  });
  el.addEventListener('pointermove', (e) => {
    const d = down.get(e.pointerId);
    if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > TAP_SLOP_PX) d.moved = true;
  });
  const lift = (e, cancelled) => {
    const d = down.get(e.pointerId);
    down.delete(e.pointerId);
    const many = pinch;
    if (!down.size) pinch = false;
    if (cancelled || !d || d.moved || many || stamp(e) - d.t > TAP_MAX_MS) return;
    if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > TAP_SLOP_PX) return;
    try { fn({ x: e.clientX, y: e.clientY, touch: e.pointerType !== 'mouse' }); } catch (err) {}
  };
  el.addEventListener('pointerup', (e) => lift(e, false));
  el.addEventListener('pointercancel', (e) => lift(e, true));
}

/* Is `object` under client point (x, y)? A ray through the camera, view offset included (the
 * framing in stage.js), and through points `slop` px around it, so a fingertip that lands just
 * beside her arm still counts. */
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
export function hitsAt(x, y, object, slop = 0) {
  const r = renderer.domElement.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const around = slop ? [[0, 0], [slop, 0], [-slop, 0], [0, slop], [0, -slop]] : [[0, 0]];
  for (const [dx, dy] of around) {
    ndc.set(((x + dx - r.left) / r.width) * 2 - 1, -((y + dy - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    if (raycaster.intersectObject(object, true).length) return true;
  }
  return false;
}

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
export const sceneLight = {
  level: 0.85,
  current: 0.85,
  base: { hemi: 0.6, key: 2.1, fill: 0.45, rim: 1.0, grid: 0.09, pad: 1.0 },
};

// `f` is the rig's face lighting ({ screenMat, faceLight, faceHalo }), which takes over
// as the room dims.
export function applySceneLight(f) {
  const s = sceneLight.current;
  const lit = 0.04 + 0.96 * s;                 // never a hard zero
  hemi.intensity = sceneLight.base.hemi * lit;
  key.intensity  = sceneLight.base.key * lit;
  fill.intensity = sceneLight.base.fill * lit;
  rim.intensity  = sceneLight.base.rim * (0.12 + 0.88 * s);  // keep a silhouette
  grid.material.opacity = sceneLight.base.grid * (0.35 + 0.65 * s);
  glowPad.material.opacity = sceneLight.base.pad * (0.45 + 0.55 * s);
  const dark = 1 - s;                          // the face takes over as the scene dims
  f.screenMat.emissiveIntensity = 0.62 + 1.5 * dark;
  f.faceLight.intensity = 0.05 + 1.1 * dark;
  f.faceHalo.material.opacity = 0.55 * dark * dark;
}
