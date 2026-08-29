// Moxie robot simulator — visual front-end.
// three.js r160, pinned via the importmap in index.html.
// Exposes window.moxie = { setMotor, getMotor, setFace, setSpeech, setHeartLED,
//                           showIcons, clearIcons }.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MOTOR_MAX = 32767;          // real hardware range (MOTOR_MAX_POS)
const MOTOR_CENTER = 16384;       // rest pose

const COL = {
  shell:  0x3bb6b0,   // matte teal body
  arm:    0x5ccac3,   // lighter teal arms
  bezel:  0x2ba59f,   // face surround, slightly darker teal
  rubber: 0x15181a,   // base ring
  base:   0x2a2f33,   // base disc
  dark:   0x22343a,
};

const BODY_H = 2.30;              // shell height in scene units (~15 in real life)

// Motor table: index -> joint. neg/pos are radian magnitudes below/above center.
// sign maps "value above center" onto the node's rotation axis direction.
const MOTOR_DEFS = [
  { name: 'L shoulder (up/down)', axis: 'z', sign: -1, neg: 0.35, pos: 1.90 }, // 0
  { name: 'L elbow (in/out)',     axis: 'z', sign: +1, neg: 0.45, pos: 1.50 }, // 1
  { name: 'R shoulder (up/down)', axis: 'z', sign: +1, neg: 0.35, pos: 1.90 }, // 2
  { name: 'R elbow (in/out)',     axis: 'z', sign: -1, neg: 0.45, pos: 1.50 }, // 3
  { name: 'Head tilt (nod)',      axis: 'x', sign: -1, neg: 0.28, pos: 0.28 }, // 4
  { name: 'Body turn (yaw)',      axis: 'y', sign: +1, neg: 1.05, pos: 1.05 }, // 5
  { name: 'Body lean (F/B)',      axis: 'x', sign: +1, neg: 0.30, pos: 0.30 }, // 6
];

// ---------------------------------------------------------------------------
// Renderer / scene / camera
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

const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 60);
camera.position.set(1.7, 1.9, 4.6);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.05, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 2.0;
controls.maxDistance = 12.0;
controls.maxPolarAngle = 1.48;
controls.update();

// Lights — soft studio setup
scene.add(new THREE.HemisphereLight(0xffffff, 0x9fb3b8, 0.65));

const key = new THREE.DirectionalLight(0xffffff, 2.1);
key.position.set(3.2, 5.2, 4.0);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.left = -2.6;
key.shadow.camera.right = 2.6;
key.shadow.camera.top = 3.2;
key.shadow.camera.bottom = -1.5;
key.shadow.camera.near = 1;
key.shadow.camera.far = 14;
key.shadow.bias = -0.0004;
key.shadow.radius = 5;
scene.add(key);

const fill = new THREE.DirectionalLight(0xd8f2ff, 0.55);
fill.position.set(-4, 2.2, 1.5);
scene.add(fill);

const rim = new THREE.DirectionalLight(0xffffff, 0.7);
rim.position.set(-1.2, 3.4, -4.2);
scene.add(rim);

// Ground shadow catcher
const ground = new THREE.Mesh(
  new THREE.CircleGeometry(8, 64),
  new THREE.ShadowMaterial({ opacity: 0.17 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.12;
ground.receiveShadow = true;
scene.add(ground);

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

function plastic(color, extra = {}) {
  return new THREE.MeshPhysicalMaterial({
    color,
    roughness: 0.52,
    metalness: 0.0,
    clearcoat: 0.25,
    clearcoatRoughness: 0.6,
    ...extra,
  });
}

const shellMat  = plastic(COL.shell);
const armMat    = plastic(COL.arm);
const bezelMat  = plastic(COL.bezel, { roughness: 0.45 });
const rubberMat = new THREE.MeshStandardMaterial({ color: COL.rubber, roughness: 0.95 });
const baseMat   = new THREE.MeshStandardMaterial({ color: COL.base, roughness: 0.65 });

// ---------------------------------------------------------------------------
// Body shell — teardrop lathe, bent so the tip points top-rear and the belly
// leans gently forward (+z is the robot's front, toward the default camera).
// ---------------------------------------------------------------------------

function makeShellGeometry() {
  const ctrl = [
    [0.00, 0.00], [0.45, 0.00], [0.62, 0.03], [0.80, 0.14],
    [0.90, 0.42], [0.93, 0.72], [0.87, 1.05], [0.75, 1.40],
    [0.57, 1.72], [0.37, 2.00], [0.19, 2.16], [0.00, BODY_H],
  ].map(([x, y]) => new THREE.Vector3(x, y, 0));

  const curve = new THREE.CatmullRomCurve3(ctrl);
  const pts = curve.getPoints(90).map(p => new THREE.Vector2(Math.max(0, p.x), p.y));

  let geo = new THREE.LatheGeometry(pts, 96);

  // Bend: forward bulge low-mid, tip swept to the rear.
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const t = pos.getY(i) / BODY_H;
    pos.setZ(i, pos.getZ(i) + 0.30 * t - 0.58 * Math.pow(Math.max(t, 0), 2.6));
  }
  geo.deleteAttribute('uv');
  geo.deleteAttribute('normal');
  geo = mergeVertices(geo, 1e-4);
  geo.computeVertexNormals();
  return geo;
}

// zOffset of the bend at height y (used to place surface details).
function bendZ(y) {
  const t = y / BODY_H;
  return 0.30 * t - 0.58 * Math.pow(Math.max(t, 0), 2.6);
}

// ---------------------------------------------------------------------------
// Rig
//   root
//    +- base disc (static)
//    +- yawG (motor 5)            pivot at base centre
//        +- leanG (motor 6 + a bit of motor 4)
//            +- shell, rubber ring, grille, heart LED
//            +- headG (motor 4) -> face screen
//            +- armRootL -> shoulderL (m0) -> upper arm -> elbowL (m1) -> forearm
//            +- armRootR -> shoulderR (m2) -> upper arm -> elbowR (m3) -> forearm
// ---------------------------------------------------------------------------

const root = new THREE.Group();
scene.add(root);

// Base disc (does not rotate with the body)
const baseDisc = new THREE.Mesh(new THREE.CylinderGeometry(0.52, 0.57, 0.12, 64), baseMat);
baseDisc.position.y = -0.06;
baseDisc.receiveShadow = true;
baseDisc.castShadow = true;
root.add(baseDisc);

const yawG = new THREE.Group();
root.add(yawG);

const leanG = new THREE.Group();
leanG.position.y = 0.02;
yawG.add(leanG);

// Shell
const shell = new THREE.Mesh(makeShellGeometry(), shellMat);
shell.castShadow = true;
shell.receiveShadow = true;
leanG.add(shell);

// Black rubber ring around the base edge of the shell
const ring = new THREE.Mesh(new THREE.TorusGeometry(0.625, 0.055, 20, 80), rubberMat);
ring.rotation.x = Math.PI / 2;
ring.position.y = 0.045;
ring.castShadow = true;
leanG.add(ring);

// ---- Face screen (canvas texture on a tilted oval) ----

const faceCanvas = document.createElement('canvas');
faceCanvas.width = 512;
faceCanvas.height = 512;
const fctx = faceCanvas.getContext('2d');
const faceTex = new THREE.CanvasTexture(faceCanvas);
faceTex.colorSpace = THREE.SRGBColorSpace;
faceTex.anisotropy = 4;

const headG = new THREE.Group();
headG.position.set(0, 1.30, 0.30);
leanG.add(headG);

const faceAssembly = new THREE.Group();
faceAssembly.position.set(0, 0.116, 0.55);    // pushed out along the plate normal
faceAssembly.rotation.x = -0.54;              // tilted up, following the shell slope
headG.add(faceAssembly);

// Bake scale into a flat geometry, then curve it in both directions so the
// plate hugs the doubly-convex shell instead of poking out at the edges.
function bentPlate(geo, sx, sy, Rx, Ry) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) * sx;
    const y = pos.getY(i) * sy;
    pos.setX(i, x);
    pos.setY(i, y);
    const sagX = Rx - Math.sqrt(Math.max(0, Rx * Rx - x * x));
    const sagY = Ry - Math.sqrt(Math.max(0, Ry * Ry - y * y));
    pos.setZ(i, pos.getZ(i) - sagX - sagY);
  }
  geo.computeVertexNormals();
  return geo;
}

// backing plate (hides the seam against the shell when the head tilts)
const facePlateBack = new THREE.Mesh(
  bentPlate(new THREE.CircleGeometry(1, 64), 0.52, 0.63, 0.70, 1.35), bezelMat);
facePlateBack.position.z = -0.05;
faceAssembly.add(facePlateBack);

// bezel ring
const bezel = new THREE.Mesh(
  bentPlate(new THREE.RingGeometry(0.97, 1.18, 64, 1), 0.45, 0.535, 0.72, 1.5), bezelMat);
bezel.position.z = 0.004;
faceAssembly.add(bezel);

// the screen itself
const screenMat = new THREE.MeshPhysicalMaterial({
  map: faceTex,
  emissive: 0xffffff,
  emissiveMap: faceTex,
  emissiveIntensity: 0.32,
  roughness: 0.32,
  clearcoat: 0.8,
  clearcoatRoughness: 0.2,
});
const screen = new THREE.Mesh(
  bentPlate(new THREE.CircleGeometry(1, 64), 0.45, 0.535, 0.72, 1.5), screenMat);
screen.position.z = 0.012;
faceAssembly.add(screen);

// ---- Speaker grille (transparent dot texture floating just off the shell) ----

function makeGrilleTexture() {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 144;
  const g = c.getContext('2d');
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = 'rgba(18, 42, 44, 0.9)';
  const cx = 128, cy = 72, rx = 112, ry = 56;
  for (let y = 14; y <= 130; y += 14) {
    const odd = ((y / 14) % 2) === 0 ? 7 : 0;
    for (let x = 10 + odd; x <= 246; x += 14) {
      const dx = (x - cx) / rx, dy = (y - cy) / ry;
      if (dx * dx + dy * dy <= 1) {
        g.beginPath();
        g.arc(x, y, 3.4, 0, Math.PI * 2);
        g.fill();
      }
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const grille = new THREE.Mesh(
  new THREE.CylinderGeometry(0.975, 0.90, 0.24, 48, 1, true, -0.40, 0.80),
  new THREE.MeshStandardMaterial({
    map: makeGrilleTexture(),
    transparent: true,
    roughness: 0.8,
    polygonOffset: true,
    polygonOffsetFactor: -1,
  })
);
grille.position.set(0, 0.42, 0.012);
leanG.add(grille);

// ---- Heart LED ----

const heartMat = new THREE.MeshStandardMaterial({
  color: 0x2c3a3d,
  emissive: 0x000000,
  roughness: 0.4,
});
const heart = new THREE.Mesh(new THREE.SphereGeometry(0.038, 24, 16), heartMat);
heart.position.set(0, 0.72, 0.945 + bendZ(0.72));
heart.scale.z = 0.5;
leanG.add(heart);

const heartLight = new THREE.PointLight(0xff5577, 0, 1.2);
heartLight.position.copy(heart.position).z += 0.1;
leanG.add(heartLight);

const heartState = { on: false, color: new THREE.Color(0xff5577) };

// ---- Arms: shoulder (up/down flap) + elbow (fold in/out, hinge tilted so the
//      forearm folds inward and slightly across the front, like a hug) ----

function makeArm(side) {  // side = -1 left, +1 right
  const armRoot = new THREE.Group();
  armRoot.position.set(side * 0.80, 1.02, 0.10 + bendZ(1.02));
  armRoot.rotation.z = side * 0.22;           // rest: hangs slightly outward

  const shoulder = new THREE.Group();         // animated: rotation.z
  armRoot.add(shoulder);

  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.16, 32, 24), armMat);
  cap.position.x = side * 0.04;
  cap.castShadow = true;
  shoulder.add(cap);

  const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.105, 0.22, 8, 24), armMat);
  upper.position.set(0, -0.18, 0.01);
  upper.castShadow = true;
  shoulder.add(upper);

  const elbow = new THREE.Group();            // animated: rotation.z (hinge pre-tilted via rotation.y)
  elbow.position.set(0, -0.36, 0.02);
  elbow.rotation.y = side * 0.55;
  shoulder.add(elbow);

  const elbowBall = new THREE.Mesh(new THREE.SphereGeometry(0.12, 28, 20), armMat);
  elbowBall.castShadow = true;
  elbow.add(elbowBall);

  const forearm = new THREE.Mesh(new THREE.CapsuleGeometry(0.125, 0.26, 8, 24), armMat);
  forearm.position.set(0, -0.22, 0);
  forearm.scale.z = 0.62;                     // flattened paddle
  forearm.castShadow = true;
  elbow.add(forearm);

  leanG.add(armRoot);
  return { shoulder, elbow };
}

const armL = makeArm(-1);
const armR = makeArm(+1);

// ---------------------------------------------------------------------------
// Motor state + node wiring
// ---------------------------------------------------------------------------

const motorTargets = new Float32Array(7).fill(MOTOR_CENTER);
const motorValues  = new Float32Array(7).fill(MOTOR_CENTER);

const motorNodes = [
  armL.shoulder, armL.elbow,
  armR.shoulder, armR.elbow,
  headG, yawG, leanG,
];

function motorAngle(i) {
  const d = MOTOR_DEFS[i];
  const u = (motorValues[i] - MOTOR_CENTER) / MOTOR_CENTER;   // -1 .. +1
  return d.sign * (u < 0 ? u * d.neg : u * d.pos);
}

// ---------------------------------------------------------------------------
// Face: parameterised cartoon face rendered to the canvas texture
// ---------------------------------------------------------------------------

const EXPRESSIONS = {
  neutral:   { eyeW: 1.00, eyeH: 1.00, browRaise: 0.0, browTilt: 0.0, browAsym: 0.0,
               pupilX: 0.0, pupilY: 0.0, mouthCurve: 0.18, mouthOpen: 0.04, mouthWidth: 1.0, mouthX: 0.0 },
  happy:     { eyeW: 1.00, eyeH: 0.62, browRaise: 0.2, browTilt: 0.0, browAsym: 0.0,
               pupilX: 0.0, pupilY: 0.0, mouthCurve: 1.00, mouthOpen: 0.35, mouthWidth: 1.1, mouthX: 0.0 },
  sad:       { eyeW: 0.95, eyeH: 0.85, browRaise: 0.15, browTilt: 1.0, browAsym: 0.0,
               pupilX: 0.0, pupilY: 0.5, mouthCurve: -0.9, mouthOpen: 0.03, mouthWidth: 0.8, mouthX: 0.0 },
  surprised: { eyeW: 1.15, eyeH: 1.30, browRaise: 1.0, browTilt: 0.0, browAsym: 0.0,
               pupilX: 0.0, pupilY: 0.0, mouthCurve: 0.0, mouthOpen: 0.95, mouthWidth: 0.45, mouthX: 0.0 },
  thinking:  { eyeW: 0.95, eyeH: 0.80, browRaise: 0.3, browTilt: 0.0, browAsym: 1.0,
               pupilX: 0.6, pupilY: -0.6, mouthCurve: 0.05, mouthOpen: 0.03, mouthWidth: 0.55, mouthX: 0.5 },
};

const faceParams = { ...EXPRESSIONS.neutral };
let faceTarget = { ...EXPRESSIONS.neutral };

const blink = { active: false, phase: 0, next: 2.5 + Math.random() * 3 };
const speech = { until: 0 };

// ---- Icon badges (cmd:icons-v2): up to 4 contextual chips below the mouth ----

const ICON_POP_MS = 200;      // per-badge pop-in duration
const ICON_STAGGER_MS = 60;   // delay between successive badges popping in
const ICON_FADE_MS = 180;     // fade-out duration on clearIcons()

const icons = { names: [], shownAt: 0, fading: false, fadeAt: 0 };

function roundedRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// One glyph, centered on (0,0), sized for a ~46px chip.
function drawIconGlyph(name) {
  const n = name.toLowerCase();
  if (n.includes('heart')) {
    // heart (also covers e.g. "Learning_About_Family_03_Heart_Family")
    fctx.fillStyle = '#e2607e';
    fctx.beginPath();
    fctx.moveTo(0, 11);
    fctx.bezierCurveTo(-14, 1, -13, -10, -6.5, -10);
    fctx.bezierCurveTo(-2.5, -10, 0, -7, 0, -4);
    fctx.bezierCurveTo(0, -7, 2.5, -10, 6.5, -10);
    fctx.bezierCurveTo(13, -10, 14, 1, 0, 11);
    fctx.closePath();
    fctx.fill();
  } else if (n.includes('medical')) {
    // medical cross
    fctx.fillStyle = '#dd4f4a';
    roundedRectPath(fctx, -4.5, -12, 9, 24, 2.5);
    fctx.fill();
    roundedRectPath(fctx, -12, -4.5, 24, 9, 2.5);
    fctx.fill();
  } else if (n.includes('birthday')) {
    // birthday cake with a candle
    fctx.fillStyle = '#e88ab0';                       // cake
    roundedRectPath(fctx, -11, -2, 22, 13, 3);
    fctx.fill();
    fctx.fillStyle = '#fdf6ec';                       // icing band
    roundedRectPath(fctx, -11, -2, 22, 5, 2.5);
    fctx.fill();
    fctx.fillStyle = '#4d7fc4';                       // candle
    roundedRectPath(fctx, -1.8, -10, 3.6, 8, 1.5);
    fctx.fill();
    fctx.fillStyle = '#f2a93b';                       // flame
    fctx.beginPath();
    fctx.ellipse(0, -12.5, 2.4, 3.4, 0, 0, Math.PI * 2);
    fctx.fill();
  } else if (n.includes('school')) {
    // graduation cap
    fctx.fillStyle = '#3f6fb5';
    fctx.beginPath();                                  // mortarboard
    fctx.moveTo(0, -10);
    fctx.lineTo(14, -4);
    fctx.lineTo(0, 2);
    fctx.lineTo(-14, -4);
    fctx.closePath();
    fctx.fill();
    fctx.beginPath();                                  // cap base
    fctx.moveTo(-7, -1);
    fctx.lineTo(7, -1);
    fctx.lineTo(7, 6);
    fctx.quadraticCurveTo(0, 10, -7, 6);
    fctx.closePath();
    fctx.fill();
    fctx.strokeStyle = '#f2a93b';                      // tassel
    fctx.lineWidth = 1.8;
    fctx.lineCap = 'round';
    fctx.beginPath();
    fctx.moveTo(14, -4);
    fctx.lineTo(14, 7);
    fctx.stroke();
    fctx.fillStyle = '#f2a93b';
    fctx.beginPath();
    fctx.arc(14, 8.5, 2.2, 0, Math.PI * 2);
    fctx.fill();
  } else {
    // unknown: first letter in the accent teal
    fctx.fillStyle = '#2b9a94';
    fctx.font = '700 22px "Segoe UI", system-ui, sans-serif';
    fctx.textAlign = 'center';
    fctx.textBaseline = 'middle';
    fctx.fillText((name.trim()[0] || '?').toUpperCase(), 0, 1);
  }
}

// Row of icon chips, drawn onto the face canvas below the mouth.
function drawIconBadges() {
  const N = icons.names.length;
  if (!N) return;
  const now = performance.now();

  let fadeA = 1, fadeS = 1;
  if (icons.fading) {
    const f = (now - icons.fadeAt) / ICON_FADE_MS;
    if (f >= 1) { icons.names = []; icons.fading = false; return; }
    fadeA = 1 - f;
    fadeS = 1 - 0.15 * f;
  }

  const size = 46, gap = 12, rowY = 438;
  const rowW = N * size + (N - 1) * gap;

  for (let i = 0; i < N; i++) {
    let a = fadeA, s = fadeS;
    if (!icons.fading) {
      const p = Math.min(1, Math.max(0, (now - icons.shownAt - i * ICON_STAGGER_MS) / ICON_POP_MS));
      if (p <= 0) continue;                           // not popped in yet
      const e = 1 - Math.pow(1 - p, 3);               // ease-out cubic
      s = 0.6 + 0.4 * e + 0.06 * Math.sin(p * Math.PI);   // tiny overshoot
      a = e;
    }

    const x = 256 - rowW / 2 + size / 2 + i * (size + gap);
    fctx.save();
    fctx.translate(x, rowY);
    fctx.scale(s, s);
    fctx.globalAlpha = a;

    // chip
    fctx.shadowColor = 'rgba(29, 49, 56, 0.22)';
    fctx.shadowBlur = 6;
    fctx.shadowOffsetY = 2;
    fctx.fillStyle = '#ffffff';
    roundedRectPath(fctx, -size / 2, -size / 2, size, size, 12);
    fctx.fill();
    fctx.shadowColor = 'transparent';
    fctx.shadowBlur = 0;
    fctx.shadowOffsetY = 0;
    fctx.strokeStyle = 'rgba(29, 49, 56, 0.10)';
    fctx.lineWidth = 1.5;
    fctx.stroke();

    fctx.scale(1.15, 1.15);   // glyphs slightly oversized for legibility
    drawIconGlyph(icons.names[i]);
    fctx.restore();
  }
}

function drawFace(t) {
  const P = faceParams;
  const W = 512, H = 512;

  // background — warm off-white screen
  const bg = fctx.createRadialGradient(256, 230, 60, 256, 256, 330);
  bg.addColorStop(0, '#f2efe5');
  bg.addColorStop(1, '#e4dfd1');
  fctx.fillStyle = bg;
  fctx.fillRect(0, 0, W, H);

  const ink = '#1d3138';

  // blink factor
  let blinkF = 1;
  if (blink.active) blinkF = Math.max(0.04, 1 - Math.sin(Math.PI * blink.phase));

  // eyes
  const eyeY = 212 + P.pupilY * 10;
  const eyeDX = 86;
  const rx = 44 * P.eyeW;
  const ry = Math.max(4, 60 * P.eyeH * blinkF);
  for (const s of [-1, 1]) {
    const ex = 256 + s * eyeDX + P.pupilX * 12;
    fctx.fillStyle = ink;
    fctx.beginPath();
    fctx.ellipse(ex, eyeY, rx, ry, 0, 0, Math.PI * 2);
    fctx.fill();
    if (ry > 14) {
      fctx.fillStyle = 'rgba(255,255,255,0.92)';
      fctx.beginPath();
      fctx.ellipse(ex - 13 + P.pupilX * 6, eyeY - ry * 0.35 + P.pupilY * 4, 11, 12, 0, 0, Math.PI * 2);
      fctx.fill();
      fctx.beginPath();
      fctx.ellipse(ex + 12, eyeY + ry * 0.3, 4.5, 5, 0, 0, Math.PI * 2);
      fctx.fill();
    }
  }

  // brows (only when an expression needs them)
  const browAmt = Math.min(1, P.browRaise + Math.abs(P.browTilt) + P.browAsym);
  if (browAmt > 0.05) {
    fctx.strokeStyle = ink;
    fctx.globalAlpha = browAmt;
    fctx.lineWidth = 11;
    fctx.lineCap = 'round';
    for (const s of [-1, 1]) {
      const asymLift = (s < 0 ? P.browAsym * 16 : P.browAsym * -2);
      const by = eyeY - 78 - P.browRaise * 18 - asymLift;
      const tilt = P.browTilt * 14 * -s;   // sad: inner ends up
      fctx.beginPath();
      fctx.moveTo(256 + s * (eyeDX - 34), by + tilt * -1);
      fctx.lineTo(256 + s * (eyeDX + 30), by + tilt);
      fctx.stroke();
    }
    fctx.globalAlpha = 1;
  }

  // blush when very happy
  if (P.mouthCurve > 0.55) {
    fctx.fillStyle = `rgba(233, 150, 138, ${0.30 * (P.mouthCurve - 0.55) / 0.45})`;
    for (const s of [-1, 1]) {
      fctx.beginPath();
      fctx.ellipse(256 + s * 148, 296, 26, 16, 0, 0, Math.PI * 2);
      fctx.fill();
    }
  }

  // mouth (talking overlays extra openness)
  let open = P.mouthOpen;
  if (performance.now() < speech.until) {
    const n = Math.abs(Math.sin(t * 11)) * (0.6 + 0.4 * Math.sin(t * 3.7 + 1));
    open = Math.max(open, 0.15 + 0.5 * Math.abs(n));
  }
  const mx = 256 + P.mouthX * 40;
  const my = 334;
  const mw = 62 * P.mouthWidth;
  const c = P.mouthCurve;
  const endY = my - c * 16;

  if (open < 0.1) {
    fctx.strokeStyle = ink;
    fctx.lineWidth = 13;
    fctx.lineCap = 'round';
    fctx.beginPath();
    fctx.moveTo(mx - mw, endY);
    fctx.quadraticCurveTo(mx, my + c * 34, mx + mw, endY);
    fctx.stroke();
  } else {
    const topCtl = my + c * 26 - open * 10;
    const botCtl = my + c * 26 + open * 95 + 14;
    fctx.fillStyle = ink;
    fctx.beginPath();
    fctx.moveTo(mx - mw, endY);
    fctx.quadraticCurveTo(mx, topCtl, mx + mw, endY);
    fctx.quadraticCurveTo(mx, botCtl, mx - mw, endY);
    fctx.closePath();
    fctx.fill();
    if (open > 0.4) {
      fctx.save();
      fctx.clip();
      fctx.fillStyle = '#d97b74';
      fctx.beginPath();
      fctx.ellipse(mx, my + open * 52 + 14, mw * 0.5, open * 26, 0, 0, Math.PI * 2);
      fctx.fill();
      fctx.restore();
    }
  }

  // icon badges (additive; drawn last so they sit on top of the face)
  drawIconBadges();

  faceTex.needsUpdate = true;
}

// ---------------------------------------------------------------------------
// Speech bubble
// ---------------------------------------------------------------------------

const bubbleEl = document.getElementById('bubble');
const bubbleText = document.getElementById('bubble-text');
let bubbleTimer = null;

function showSpeech(text) {
  const dur = Math.max(1800, 900 + 70 * text.length);
  bubbleText.textContent = text;
  bubbleEl.classList.remove('hidden');
  speech.until = performance.now() + dur;
  if (bubbleTimer) clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => bubbleEl.classList.add('hidden'), dur);
}

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
    syncSlider(i);
  },

  getMotor(index) {
    const i = index | 0;
    if (i < 0 || i > 6) return undefined;
    return Math.round(motorValues[i]);
  },

  setFace(expression) {
    if (expression === 'blink') {
      blink.active = true;
      blink.phase = 0;
      return;
    }
    const e = EXPRESSIONS[expression];
    if (!e) { console.warn('moxie.setFace: unknown expression', expression); return; }
    faceTarget = { ...e };
    markFaceButton(expression);
  },

  setSpeech(text) {
    if (typeof text !== 'string' || !text.trim()) return;
    showSpeech(text.trim());
  },

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
    const list = names
      .filter(n => typeof n === 'string' && n.trim().length)
      .slice(0, 4)
      .map(n => n.trim());
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
    for (let i = 0; i < 7; i++) api.setMotor(i, MOTOR_CENTER);
  },
};

window.moxie = api;
window.dispatchEvent(new CustomEvent('moxie-ready', { detail: api }));

// ---------------------------------------------------------------------------
// Control panel
// ---------------------------------------------------------------------------

const sliderEls = [];

function buildPanel() {
  const motorsEl = document.getElementById('motors');
  MOTOR_DEFS.forEach((d, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'motor';
    wrap.innerHTML =
      `<label><span>${i} &middot; ${d.name}</span><span class="val">${MOTOR_CENTER}</span></label>` +
      `<input type="range" min="0" max="${MOTOR_MAX}" step="1" value="${MOTOR_CENTER}">`;
    const input = wrap.querySelector('input');
    const val = wrap.querySelector('.val');
    input.addEventListener('input', () => {
      val.textContent = input.value;
      motorTargets[i] = +input.value;
    });
    motorsEl.appendChild(wrap);
    sliderEls[i] = { input, val };
  });

  const facesEl = document.getElementById('faces');
  api.expressions.forEach(name => {
    const b = document.createElement('button');
    b.textContent = name;
    b.dataset.expr = name;
    b.addEventListener('click', () => api.setFace(name));
    facesEl.appendChild(b);
  });
  markFaceButton('neutral');

  document.getElementById('center-btn').addEventListener('click', () => api.centerAll());

  const sayInput = document.getElementById('speech-input');
  const say = () => { api.setSpeech(sayInput.value); sayInput.value = ''; };
  document.getElementById('speech-btn').addEventListener('click', say);
  sayInput.addEventListener('keydown', e => { if (e.key === 'Enter') say(); });

  const ledOn = document.getElementById('led-on');
  const ledColor = document.getElementById('led-color');
  ledOn.addEventListener('change', () => api.setHeartLED(ledOn.checked, ledColor.value));
  ledColor.addEventListener('input', () => api.setHeartLED(ledOn.checked, ledColor.value));
}

function syncSlider(i) {
  const s = sliderEls[i];
  if (!s) return;
  s.input.value = String(Math.round(motorTargets[i]));
  s.val.textContent = s.input.value;
}

function markFaceButton(name) {
  document.querySelectorAll('#faces button').forEach(b =>
    b.classList.toggle('active', b.dataset.expr === name));
}

buildPanel();

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = clock.elapsedTime;

  // smooth motor motion
  const k = 1 - Math.exp(-dt * 7);
  for (let i = 0; i < 7; i++) {
    motorValues[i] += (motorTargets[i] - motorValues[i]) * k;
    if (Math.abs(motorTargets[i] - motorValues[i]) < 2) motorValues[i] = motorTargets[i];
  }

  // gentle idle life (additive, never stored back into motor state)
  const breathe = 0.010 * Math.sin(t * 1.6);
  const sway    = 0.014 * Math.sin(t * 0.5);
  const headIdle = 0.012 * Math.sin(t * 0.9 + 1.0);
  const armIdleL = 0.015 * Math.sin(t * 1.3);
  const armIdleR = 0.015 * Math.sin(t * 1.3 + 2.1);

  const a = [0, 1, 2, 3, 4, 5, 6].map(motorAngle);

  armL.shoulder.rotation.z = a[0] + armIdleL;
  armL.elbow.rotation.z    = a[1];
  armR.shoulder.rotation.z = a[2] + armIdleR;
  armR.elbow.rotation.z    = a[3];
  headG.rotation.x = a[4] + headIdle;
  yawG.rotation.y  = a[5] + sway;
  leanG.rotation.x = a[6] + 0.30 * a[4] + breathe;

  // face params ease toward target
  const fk = 1 - Math.exp(-dt * 9);
  for (const key of Object.keys(faceParams)) {
    faceParams[key] += (faceTarget[key] - faceParams[key]) * fk;
  }

  // blinking
  if (blink.active) {
    blink.phase += dt / 0.22;
    if (blink.phase >= 1) {
      blink.active = false;
      blink.next = t + 2.5 + Math.random() * 4;
    }
  } else if (t > blink.next) {
    blink.active = true;
    blink.phase = 0;
  }

  drawFace(t);

  // heart LED
  if (heartState.on) {
    const pulse = 0.75 + 0.35 * Math.sin(t * 2.6);
    heartMat.emissive.copy(heartState.color);
    heartMat.emissiveIntensity = pulse;
    heartLight.color.copy(heartState.color);
    heartLight.intensity = 0.6 * pulse;
  } else {
    heartMat.emissiveIntensity = 0;
    heartLight.intensity = 0;
  }

  controls.update();
  renderer.render(scene, camera);
}

animate();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
