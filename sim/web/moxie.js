// Moxie robot simulator — visual front-end.
// three.js r160, pinned via the importmap in index.html.
// Exposes window.moxie = { setMotor, getMotor, setFace, setSpeech, setHeartLED,
//                           showIcons, clearIcons, centerAll, setIdle,
//                           setSceneLight }.
//
// Anatomy (docs/architecture/sil-and-cicd.md "Visual reference"): a two-part
// robot — a large rounded egg-shaped HEAD sitting directly on a pear-shaped
// BODY (no visible neck, just a seam; Moxie is top-heavy). Curved arm-shell
// pads conform to the body's surface and hang down its flanks — barely
// visible from the front at rest — each a two-segment limb (shoulder +
// elbow) ending in a light rounded hand the same width as the arm. The flat
// face screen and a small camera lens live on the head; the speaker grille,
// heart-LED marking (thin line + tiny heart) and `moxie` wordmark on the body.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MOTOR_MAX = 32767;          // real hardware range (MOTOR_MAX_POS)
const MOTOR_CENTER = 16384;       // rest pose

const COL = {
  shell:    0x3bb6b0,   // matte teal body + head
  arm:      0x45bfb9,   // arm shells, a whisper lighter than the body
  hand:     0x9fdce8,   // light blue-grey rounded hands
  rubber:   0x15181a,   // base ring
  base:     0x2a2f33,   // base disc
  dark:     0x22343a,
};

const BODY_TOP = 1.50;            // top of the body (scene units; ~15 in overall)
const HEAD_PIVOT_Y = 1.30;        // head-tilt pivot, inside the head/body overlap
const SHOULDER_Y = 1.26;          // arm shell pivots high on the flank

// Motor table: index -> joint. neg/pos are radian magnitudes below/above center.
// sign maps "value above center" onto the node's rotation axis direction.
// Ranges: shoulders -20..+100 deg, elbows -25..+85 deg, head tilt +-22 deg,
// body yaw +-60 deg, body lean +-16 deg.
const MOTOR_DEFS = [
  { name: 'L shoulder (up/down)', axis: 'z', sign: -1, neg: 0.35, pos: 1.75 }, // 0
  { name: 'L elbow (in/out)',     axis: 'z', sign: +1, neg: 0.44, pos: 1.48 }, // 1
  { name: 'R shoulder (up/down)', axis: 'z', sign: +1, neg: 0.35, pos: 1.75 }, // 2
  { name: 'R elbow (in/out)',     axis: 'z', sign: -1, neg: 0.44, pos: 1.48 }, // 3
  { name: 'Head tilt (nod)',      axis: 'x', sign: -1, neg: 0.38, pos: 0.38 }, // 4
  { name: 'Body turn (yaw)',      axis: 'y', sign: +1, neg: 1.05, pos: 1.05 }, // 5
  { name: 'Body lean (F/B)',      axis: 'x', sign: +1, neg: 0.28, pos: 0.28 }, // 6
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
camera.position.set(1.8, 2.1, 4.8);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.22, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 2.0;
controls.maxDistance = 12.0;
controls.maxPolarAngle = 1.48;
controls.update();

// Deterministic camera placement for screenshot/CI harnesses (debug-only;
// not part of the window.moxie API).
window.__setCam = (x, y, z, tx = 0, ty = 1.22, tz = 0) => {
  camera.position.set(x, y, z);
  controls.target.set(tx, ty, tz);
  controls.update();
};

// Lights — cool control-room setup: white key, cold fill, cyan rim,
// dark ground bounce so the shell reads against the void.
const hemi = new THREE.HemisphereLight(0xdcecff, 0x10151d, 0.6);
scene.add(hemi);

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

const fill = new THREE.DirectionalLight(0xa9d8ff, 0.45);
fill.position.set(-4, 2.2, 1.5);
scene.add(fill);

const rim = new THREE.DirectionalLight(0x66e6ff, 1.0);   // neon-cyan rim from behind
rim.position.set(-1.2, 3.4, -4.2);
scene.add(rim);

// Ground shadow catcher (heavier opacity — shadows need weight on the void)
const ground = new THREE.Mesh(
  new THREE.CircleGeometry(8, 64),
  new THREE.ShadowMaterial({ opacity: 0.4 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.12;
ground.receiveShadow = true;
scene.add(ground);

// Ambient void grid + soft cyan glow pad under the robot (visual only —
// matches the mission-control HUD skin; see docs/design/style-guide.md).
const grid = new THREE.GridHelper(26, 52, 0x00f0ff, 0x0e7490);
grid.material.transparent = true;
grid.material.opacity = 0.09;
grid.material.depthWrite = false;
grid.position.y = -0.125;
scene.add(grid);

const glowTex = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0.0, 'rgba(0, 240, 255, 0.20)');
  grad.addColorStop(0.5, 'rgba(0, 240, 255, 0.05)');
  grad.addColorStop(1.0, 'rgba(0, 240, 255, 0.0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
})();
const glowPad = new THREE.Mesh(
  new THREE.CircleGeometry(2.6, 48),
  new THREE.MeshBasicMaterial({
    map: glowTex,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  })
);
glowPad.rotation.x = -Math.PI / 2;
glowPad.position.y = -0.118;
scene.add(glowPad);

// Adjustable scene lighting (moxie.setSceneLight): 1 = fully lit studio,
// 0 = near-dark, where the glowing projected face becomes the main source.
// Baselines are the restyle's values above; the eased level scales them.
const sceneLight = {
  level: 0.85,      // commanded (default per HUD spec)
  current: 0.85,    // eased
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

  // the DLP face takes over as the scene dims
  const dark = 1 - s;
  screenMat.emissiveIntensity = 0.55 + 1.6 * dark;
  faceLight.intensity = 0.15 + 2.6 * dark;
  faceHalo.material.opacity = 0.55 * dark * dark;
}

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

const shellMat    = plastic(COL.shell);
const armMat      = plastic(COL.arm);
const handMat     = plastic(COL.hand, { roughness: 0.45 });
const rubberMat   = new THREE.MeshStandardMaterial({ color: COL.rubber, roughness: 0.95 });
const baseMat     = new THREE.MeshStandardMaterial({ color: COL.base, roughness: 0.65 });
const lensMat     = new THREE.MeshPhysicalMaterial({
  color: 0x0a0f12, roughness: 0.12, clearcoat: 1.0, clearcoatRoughness: 0.08,
});

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

// Body: an upright, softly-rounded pear (lathe) — widest low, tapering to a
// broad rounded shoulder that the head sits directly on (no neck).
const bodyProfilePts = (() => {
  const ctrl = [
    [0.00, 0.05], [0.36, 0.05], [0.58, 0.10], [0.645, 0.28],
    [0.650, 0.50], [0.625, 0.78], [0.585, 1.00], [0.545, 1.18],
    [0.50, 1.32], [0.42, 1.42], [0.28, 1.48], [0.00, BODY_TOP],
  ].map(([x, y]) => new THREE.Vector3(x, y, 0));
  const curve = new THREE.CatmullRomCurve3(ctrl);
  return curve.getPoints(120).map(p => new THREE.Vector2(Math.max(0, p.x), p.y));
})();

// Radius of the body surface at height y — used to wrap the arm shells so
// they conform to the tapered body.
function bodyRadiusAt(y) {
  const pts = bodyProfilePts;
  let r = 0;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const lo = Math.min(a.y, b.y), hi = Math.max(a.y, b.y);
    if (y >= lo && y <= hi && hi - lo > 1e-6) {
      const t = (y - a.y) / (b.y - a.y);
      r = Math.max(r, a.x + (b.x - a.x) * t);
    }
  }
  return r || 0.6;
}

function makeBodyGeometry() {
  let geo = new THREE.LatheGeometry(bodyProfilePts, 128);
  geo.deleteAttribute('uv');
  geo.deleteAttribute('normal');
  geo = mergeVertices(geo, 1e-4);
  geo.computeVertexNormals();
  return geo;
}

// A unit sphere with welded verts, ready for reshaping + computeVertexNormals.
function smoothSphere(wSeg, hSeg, ...sector) {
  let g = new THREE.SphereGeometry(1, wSeg, hSeg, ...sector);
  g.deleteAttribute('uv');
  g.deleteAttribute('normal');
  g = mergeVertices(g, 1e-4);
  return g;
}

// Arm shell segments: a capsule (stadium profile — CONSTANT width along its
// length, rounded only at the ends) wrapped around the body's lathe profile,
// giving a smooth curved shell that hugs the flank. halfW is a LINEAR
// half-width (scene units), converted to an arc per-vertex so the shell stays
// the same width all the way down the tapering body — the whole arm (upper,
// forearm, hand) is one constant width on the real robot.
//   side: -1 left, +1 right. thetaBias rotates the pad toward the front.
//   pivot: the joint position (body space); geometry is re-origined there.
// NOTE the wrap mapping mirrors chirality for one side (its Jacobian
// determinant flips sign with `side`), which would invert the triangle
// winding and flip the computed normals inside-out for that arm. After
// building we check the average normal against the outward radial direction
// and re-wind the indices if needed, so BOTH arms shade identically.
function makeArmShellGeometry(side, yTop, yBot, halfW, thickness, thetaBias, pivot) {
  const height = yTop - yBot;
  const capH = Math.min(0.16, height * 0.35);        // rounded end height
  const len = Math.max(0.01, height / capH - 2);     // source capsule mid-length
  let geo = new THREE.CapsuleGeometry(1, len, 10, 36);
  geo.deleteAttribute('uv');
  geo.deleteAttribute('normal');
  geo = mergeVertices(geo, 1e-4);
  const pos = geo.attributes.position;
  const yc = (yTop + yBot) / 2;
  const yScale = height / (len + 2);
  const theta0 = side * (Math.PI / 2 - thetaBias);
  for (let i = 0; i < pos.count; i++) {
    const sx = pos.getX(i), sy = pos.getY(i), sz = pos.getZ(i);
    const y = yc + sy * yScale;
    const rBase = bodyRadiusAt(y) + 0.012;
    const theta = theta0 + side * sx * (halfW / rBase);
    const r = rBase + sz * thickness;
    pos.setXYZ(i,
      r * Math.sin(theta) - pivot.x,
      y - pivot.y,
      r * Math.cos(theta) - pivot.z);
  }
  geo.computeVertexNormals();
  const nrm = geo.attributes.normal;
  let outward = 0;
  for (let i = 0; i < pos.count; i++) {
    outward += nrm.getX(i) * (pos.getX(i) + pivot.x) +
               nrm.getZ(i) * (pos.getZ(i) + pivot.z);
  }
  if (outward < 0) {                    // winding got mirrored — flip it back
    const idx = geo.index.array;
    for (let i = 0; i < idx.length; i += 3) {
      const t = idx[i + 1]; idx[i + 1] = idx[i + 2]; idx[i + 2] = t;
    }
    geo.computeVertexNormals();
  }
  return geo;
}

// ---------------------------------------------------------------------------
// Rig
//   root
//    +- base disc + rubber ring (static)
//    +- yawG (motor 5)                       pivot at base centre
//        +- leanG (motor 6)
//            +- breatheG (idle breathing scale/bob, visual only)
//                +- body cylinder, grille, wordmark, heart-LED marking
//                +- headTiltG (motor 4) -> headRollG (idle roll, visual only)
//                     +- head (flat-front egg), face screen disk, camera lens
//                +- armRootL -> shoulderL (m0) -> upper shell
//                     +- elbowL (m1) -> forearm shell + handL (same width)
//                +- armRootR -> shoulderR (m2) -> upper shell
//                     +- elbowR (m3) -> forearm shell + handR
// ---------------------------------------------------------------------------

const root = new THREE.Group();
scene.add(root);

// Base disc (does not rotate with the body) + black rubber ring
const baseDisc = new THREE.Mesh(new THREE.CylinderGeometry(0.60, 0.66, 0.14, 64), baseMat);
baseDisc.position.y = -0.05;
baseDisc.receiveShadow = true;
baseDisc.castShadow = true;
root.add(baseDisc);

const ring = new THREE.Mesh(new THREE.TorusGeometry(0.615, 0.055, 20, 80), rubberMat);
ring.rotation.x = Math.PI / 2;
ring.position.y = 0.025;
ring.castShadow = true;
root.add(ring);

const yawG = new THREE.Group();
root.add(yawG);

const leanG = new THREE.Group();
leanG.position.y = 0.02;
yawG.add(leanG);

const breatheG = new THREE.Group();          // liveness: breathing scale/bob
leanG.add(breatheG);

// Body cylinder
const body = new THREE.Mesh(makeBodyGeometry(), shellMat);
body.castShadow = true;
body.receiveShadow = true;
breatheG.add(body);

// ---- Head: a large egg/teardrop sitting DIRECTLY on the body (no neck —
//      the head's underside overlaps the body's rounded shoulder, leaving
//      only a seam). Moxie is top-heavy: the head is about as wide as the
//      body's widest point. ----

const headTiltG = new THREE.Group();          // motor 4
headTiltG.position.y = HEAD_PIVOT_Y;
breatheG.add(headTiltG);

const headRollG = new THREE.Group();          // liveness-only curious head roll
headTiltG.add(headRollG);

const headForm = new THREE.Group();           // constant slight forward tilt
headForm.rotation.x = 0.05;
headRollG.add(headForm);

const HEAD_C = new THREE.Vector3(0, 0.64, 0.06);      // head centre (local)
const HEAD_R = new THREE.Vector3(0.58, 0.68, 0.56);   // egg radii

// Egg-shaping for the head shell: taper the crown and ease it toward the
// back so the silhouette reads as a teardrop.
function eggify(geo, rx, ry, rz) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i), z = pos.getZ(i);
    const y = pos.getY(i);
    const t = Math.max(0, y);                // 0 at equator -> 1 at crown
    const pinch = 1 - 0.22 * t * t;          // narrow toward the top
    x *= pinch;
    z = z * pinch - 0.14 * t * t;            // crown eases back (teardrop)
    pos.setXYZ(i, x * rx, y * ry, z * rz);
  }
  geo.computeVertexNormals();
  return geo;
}

// Flatten the front of the egg into the face plane — the real head is an egg
// with a flat slice off the front where the screen (and, above it, the small
// camera lens) sit. Soft-clamps z toward the plane so the crease is a smooth
// fillet, not a hard edge. Geometry-local coords (head centre at origin).
function flattenFront(geo, zPlane, fillet) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const z = pos.getZ(i);
    const d = z - (zPlane - fillet);
    if (d > 0) pos.setZ(i, zPlane - fillet + fillet * (1 - Math.exp(-d / fillet)));
  }
  geo.computeVertexNormals();
  return geo;
}

// Head texture: uniform shell teal with the two ear ovals PAINTED on — flat
// markings on the shell, zero geometry, so nothing clips or z-fights at any
// angle. The map is flat colour everywhere else, which keeps the spherical-UV
// wrap seam (back of head) and pole pinch invisible.
function makeHeadTexture() {
  const c = document.createElement('canvas');
  c.width = 1024; c.height = 512;
  const g = c.getContext('2d');
  g.fillStyle = '#3bb6b0';                    // must match COL.shell exactly
  g.fillRect(0, 0, 1024, 512);
  // One ear oval, centred on the texture's wrap edge (u=0 == u=1, which the
  // mirrored side-angle UV mapping places at the exact left/right side of the
  // head) — drawn at both canvas edges so the wrap shows the full ellipse.
  for (const cx of [0, 1024]) {
    g.save();
    g.translate(cx, 250);
    g.fillStyle = 'rgba(10, 25, 30, 0.10)';   // whisper-faint inset shading
    g.beginPath();
    g.ellipse(0, 0, 52, 30, 0, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = 'rgba(14, 34, 40, 0.55)'; // thin seam outline
    g.lineWidth = 5;
    g.stroke();
    g.restore();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = THREE.RepeatWrapping;
  t.anisotropy = 4;
  return t;
}

// UVs recomputed from the final vertex positions (the source sphere's UVs
// were deleted for vertex welding). u is the MIRRORED side angle
// atan2(x, |z|) — continuous over the whole head (no wrap seam to smear),
// mapping each side of the head onto one texture edge; the map is symmetric
// front/back, which suits the symmetric ear marking.
function sphericalUVs(geo, ry) {
  const pos = geo.attributes.position;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    const sy = Math.max(-1, Math.min(1, pos.getY(i) / ry));
    uv[2 * i] = 0.5 + Math.atan2(pos.getX(i), Math.abs(pos.getZ(i))) / Math.PI;
    uv[2 * i + 1] = 0.5 + Math.asin(sy) / Math.PI;
  }
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  return geo;
}

const headMat = plastic(0xffffff, { map: makeHeadTexture() });
const head = new THREE.Mesh(
  sphericalUVs(
    flattenFront(eggify(smoothSphere(72, 52), HEAD_R.x, HEAD_R.y, HEAD_R.z), 0.335, 0.05),
    HEAD_R.y),
  headMat);
head.position.copy(HEAD_C);
head.castShadow = true;
head.receiveShadow = true;
headForm.add(head);

// Camera: just a SMALL lens — a modest dark circle, slightly recessed-looking,
// nothing more (no band, no visor). It sits ON the face panel, inside the dark
// camera zone painted across the panel's top (see drawFace), so screen +
// camera read as one continuous front assembly.
const lens = new THREE.Mesh(new THREE.SphereGeometry(0.042, 24, 16), lensMat);
lens.scale.set(1, 0.85, 0.35);
lens.rotation.x = -0.08;                      // sits on the flat face plane
lens.position.set(0, 0.980, 0.410);
headForm.add(lens);
const lensDot = new THREE.Mesh(
  new THREE.SphereGeometry(0.013, 12, 8),
  new THREE.MeshBasicMaterial({ color: 0x3a5560 }));
lensDot.position.set(0.010, 0.985, 0.424);
headForm.add(lensDot);

// ---- Face screen (canvas texture on a curved oval, front of the HEAD) ----

const faceCanvas = document.createElement('canvas');
faceCanvas.width = 512;
faceCanvas.height = 512;
const fctx = faceCanvas.getContext('2d');
const faceTex = new THREE.CanvasTexture(faceCanvas);
faceTex.colorSpace = THREE.SRGBColorSpace;
faceTex.anisotropy = 4;

const faceAssembly = new THREE.Group();
faceAssembly.position.set(0, 0.62, 0.0);      // pane parallel to the flat face plane
headForm.add(faceAssembly);

// Bake scale into a flat geometry, then give it a very shallow curve; used by
// the face pane (nearly flush disk) and body decals (bend around the shell).
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

// The screen itself — a LARGE flat, shallow elliptical panel filling most of
// the head's flattened front (world plane z ~0.395 here), leaving only a
// modest teal border. It runs from below the chin area right up under the
// crown, meeting the darker camera zone painted across its top (see
// drawFace) so screen + camera read as one continuous front assembly.
// The profile is flat across the middle and dives only in the last ~10% of
// the radius, tucking the rim just behind the shell so the seam self-hides
// without burying the visible face area. Backlit: emissiveMap is the face
// canvas, so the drawn features glow from within.
const FACE_RX = 0.46;         // panel half-width  (flattened front chord ~0.465)
const FACE_RY = 0.50;         // panel half-height (spans y ~0.12..1.12 in headForm)

function facePanelGeometry(rx, ry) {
  // RingGeometry with inner radius ~0 gives a disk WITH radial segments, so
  // the flat-then-dive profile is real geometry (CircleGeometry has only a
  // single rim ring and would render any profile as a cone).
  const geo = new THREE.RingGeometry(0.001, 1, 96, 24);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const ux = pos.getX(i), uy = pos.getY(i);
    const s = Math.min(1, Math.hypot(ux, uy));         // 0 centre -> 1 rim
    const dive = 0.012 * s * s + 0.055 * Math.pow(s, 10);
    pos.setXYZ(i, ux * rx, uy * ry, -dive);
  }
  geo.computeVertexNormals();
  return geo;                  // RingGeometry UVs are planar — canvas maps 1:1
}

const screenMat = new THREE.MeshPhysicalMaterial({
  map: faceTex,
  emissive: 0xffffff,
  emissiveMap: faceTex,
  emissiveIntensity: 0.32,
  roughness: 0.32,
  clearcoat: 0.8,
  clearcoatRoughness: 0.2,
});
const screen = new THREE.Mesh(facePanelGeometry(FACE_RX, FACE_RY), screenMat);
screen.position.z = 0.422;
faceAssembly.add(screen);

// Projector light: the DLP face casts real light on the surroundings.
// Intensity scales up as the scene dims (see setSceneLight / animate loop).
const faceLight = new THREE.PointLight(0xf3eedd, 0.15, 3.4, 2);
faceLight.position.set(0, 0, 0.85);
faceAssembly.add(faceLight);

// Soft warm spill AROUND the pane — an annulus with a fully transparent
// centre, so it rims the screen's edges as the scene darkens without ever
// veiling the face itself (legibility beats glow: no overlay on the pane).
const faceHaloTex = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0.00, 'rgba(255, 248, 224, 0.0)');   // clear over the pane
  grad.addColorStop(0.52, 'rgba(255, 248, 224, 0.0)');
  grad.addColorStop(0.62, 'rgba(255, 248, 224, 0.50)');  // peak at the rim
  grad.addColorStop(1.00, 'rgba(255, 244, 214, 0.0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
})();
const faceHalo = new THREE.Sprite(new THREE.SpriteMaterial({
  map: faceHaloTex,
  transparent: true,
  opacity: 0,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
}));
faceHalo.scale.set(1.65, 1.75, 1);
faceHalo.position.set(0, 0, 0.55);
faceAssembly.add(faceHalo);

// (Ears are painted into the head's texture map above — see makeHeadTexture —
// so there is no separate ear geometry to clip or z-fight with the shell.)

// ---- Speaker grille (transparent dot texture, LOW on the body front) ----

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
  new THREE.CylinderGeometry(0.660, 0.642, 0.26, 48, 1, true, -0.45, 0.90),
  new THREE.MeshStandardMaterial({
    map: makeGrilleTexture(),
    transparent: true,
    roughness: 0.8,
    polygonOffset: true,
    polygonOffsetFactor: -1,
  })
);
grille.position.set(0, 0.34, 0);
breatheG.add(grille);

// ---- `moxie` wordmark near the base ----

function makeWordmarkTexture() {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = 'rgba(16, 49, 52, 0.88)';
  g.font = '600 38px "Trebuchet MS", "Segoe UI", system-ui, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText('moxie', 128, 34);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const wordmark = new THREE.Mesh(
  new THREE.CylinderGeometry(0.622, 0.602, 0.11, 32, 1, true, -0.30, 0.60),
  new THREE.MeshStandardMaterial({
    map: makeWordmarkTexture(),
    transparent: true,
    roughness: 0.7,
    polygonOffset: true,
    polygonOffsetFactor: -1,
  })
);
wordmark.position.set(0, 0.155, 0);
breatheG.add(wordmark);

// ---- Heart LED: a THIN white horizontal line with a TINY white heart
//      directly beneath it, on the upper chest just under the head — a small,
//      delicate LED marking (not a big solid heart). Painted decal, flat on
//      the shell; the texture doubles as the emissive mask so it glows in the
//      commanded colour when on and reads as a light-grey marking when off.

function makeHeartLEDTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  g.clearRect(0, 0, 256, 256);
  g.fillStyle = '#ffffff';
  roundedRectPath2(g, 48, 100, 160, 9, 4.5);   // thin horizontal line
  g.fill();
  g.save();                                     // tiny heart just beneath it
  g.translate(128, 146);
  g.scale(1.35, 1.35);
  g.beginPath();
  g.moveTo(0, 11);
  g.bezierCurveTo(-14, 1, -13, -10, -6.5, -10);
  g.bezierCurveTo(-2.5, -10, 0, -7, 0, -4);
  g.bezierCurveTo(0, -7, 2.5, -10, 6.5, -10);
  g.bezierCurveTo(13, -10, 14, 1, 0, 11);
  g.closePath();
  g.fill();
  g.restore();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function roundedRectPath2(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

const heartTex = makeHeartLEDTexture();
const heartMat = new THREE.MeshStandardMaterial({
  map: heartTex,
  color: 0xdfe9ea,                 // subtle light-grey/white marking when off
  transparent: true,
  emissive: 0xffffff,
  emissiveMap: heartTex,
  emissiveIntensity: 0.10,
  roughness: 0.55,
  polygonOffset: true,
  polygonOffsetFactor: -2,
});
const heart = new THREE.Mesh(
  bentPlate(new THREE.CircleGeometry(1, 48), 0.17, 0.17, bodyRadiusAt(1.08), 1.6),
  heartMat);
heart.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.006);
heart.rotation.x = -0.20;             // follows the chest's backward taper
breatheG.add(heart);

const heartLight = new THREE.PointLight(0xff5577, 0, 1.2);
heartLight.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.12);
breatheG.add(heartLight);

const heartState = { on: false, color: new THREE.Color(0xff5577) };

// ---- Arms: ONE constant-width curved shell per arm, conforming to the
//      body's surface and hanging down the flank at rest (a soft curved
//      blister on the body, like the real robot). Upper arm, forearm and
//      hand are all the SAME width — the hand is simply the lighter-blue
//      rounded end of the same shell, rounding off at the tip. The shoulder
//      swings the whole arm out/up; the elbow folds the forearm+hand on a
//      pre-tilted hinge so the fold sweeps inward/front. ----

const ARM_HALF_W = 0.21;   // uniform linear half-width: upper == forearm == hand
const ARM_THICK  = 0.065;

function makeArm(side) {  // side = -1 left, +1 right
  const shoulderPivot = new THREE.Vector3(
    side * (bodyRadiusAt(SHOULDER_Y) + 0.01), SHOULDER_Y, 0);
  const elbowY = 0.76;
  const elbowPivot = new THREE.Vector3(
    side * (bodyRadiusAt(elbowY) + 0.01), elbowY, 0.04);

  const armRoot = new THREE.Group();
  armRoot.position.copy(shoulderPivot);

  const shoulder = new THREE.Group();         // animated: rotation.z (motor 0/2)
  armRoot.add(shoulder);

  // upper arm: constant-width shell from the shoulder down past the elbow
  const upper = new THREE.Mesh(
    makeArmShellGeometry(side, 1.27, 0.60, ARM_HALF_W, ARM_THICK, 0.22, shoulderPivot),
    armMat);
  upper.castShadow = true;
  upper.receiveShadow = true;
  shoulder.add(upper);

  // elbow hinge: pre-tilted frame so the motor's z-fold sweeps the forearm
  // inward and slightly across the front (like a hug); the counter-rotation
  // keeps the rest pose flush against the body.
  const elbowPre = new THREE.Group();
  elbowPre.position.copy(elbowPivot).sub(shoulderPivot);
  elbowPre.rotation.y = side * 0.5;
  shoulder.add(elbowPre);

  const elbow = new THREE.Group();            // animated: rotation.z (motor 1/3)
  elbowPre.add(elbow);

  const elbowPost = new THREE.Group();
  elbowPost.rotation.y = -side * 0.5;
  elbow.add(elbowPost);

  // forearm: SAME width as the upper arm, overlapping it at the elbow
  const forearm = new THREE.Mesh(
    makeArmShellGeometry(side, 0.74, 0.34, ARM_HALF_W, ARM_THICK, 0.26, elbowPivot),
    armMat);
  forearm.castShadow = true;
  forearm.receiveShadow = true;
  elbowPost.add(forearm);

  // hand: the same-width continuation of the shell in the lighter blue,
  // rounding off at the tip like a single finger
  const hand = new THREE.Mesh(
    makeArmShellGeometry(side, 0.44, 0.15, ARM_HALF_W, ARM_THICK, 0.30, elbowPivot),
    handMat);
  hand.castShadow = true;
  hand.receiveShadow = true;
  elbowPost.add(hand);

  breatheG.add(armRoot);
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
  headTiltG, yawG, leanG,
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

// external lip-sync drive (moxie.setMouthOpen, called by the audio layer);
// additive — it only ever opens the mouth further than the expression does
const mouthDrive = { v: 0 };

// idle gaze drift (liveness layer; additive on top of expression pupils)
const idleEyes = { x: 0, y: 0 };

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

  const size = 44, gap = 12, rowY = 435;
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

  // Only the inscribed circle of the canvas is mapped onto the elliptical
  // panel (planar disk UVs), centre (256,256), radius 256; the outermost
  // ~10% of that radius tucks behind the shell (see facePanelGeometry).

  // background — warm off-white screen
  const bg = fctx.createRadialGradient(256, 270, 70, 256, 270, 310);
  bg.addColorStop(0, '#f2efe5');
  bg.addColorStop(1, '#e4dfd1');
  fctx.fillStyle = bg;
  fctx.fillRect(0, 0, W, H);

  // dark bezel ring around the panel edge — the gasket between the glowing
  // screen and the teal shell (its outer part hides under the shell rim)
  fctx.strokeStyle = '#15232a';
  fctx.lineWidth = 44;
  fctx.beginPath();
  fctx.arc(256, 256, 236, 0, Math.PI * 2);
  fctx.stroke();

  // dark camera zone across the panel's top — merges into the bezel ring at
  // the sides so screen + camera read as one assembly; the 3D lens sits on
  // the panel inside this zone. Dark on the emissive map = it never glows.
  fctx.fillStyle = '#15232a';
  fctx.beginPath();
  fctx.moveTo(0, 0);
  fctx.lineTo(512, 0);
  fctx.lineTo(512, 96);
  fctx.quadraticCurveTo(256, 160, 0, 96);
  fctx.closePath();
  fctx.fill();

  const ink = '#1d3138';

  // blink factor
  let blinkF = 1;
  if (blink.active) blinkF = Math.max(0.04, 1 - Math.sin(Math.PI * blink.phase));

  // eyes (idleEyes adds the liveness gaze drift on top of the expression)
  const eyeY = 252 + (P.pupilY + idleEyes.y) * 10;
  const eyeDX = 90;
  const rx = 48 * P.eyeW;
  const ry = Math.max(4, 64 * P.eyeH * blinkF);
  for (const s of [-1, 1]) {
    const ex = 256 + s * eyeDX + (P.pupilX + idleEyes.x) * 12;
    fctx.fillStyle = ink;
    fctx.beginPath();
    fctx.ellipse(ex, eyeY, rx, ry, 0, 0, Math.PI * 2);
    fctx.fill();
    if (ry > 14) {
      fctx.fillStyle = 'rgba(255,255,255,0.92)';
      fctx.beginPath();
      fctx.ellipse(ex - 14 + P.pupilX * 6, eyeY - ry * 0.35 + P.pupilY * 4, 12, 13, 0, 0, Math.PI * 2);
      fctx.fill();
      fctx.beginPath();
      fctx.ellipse(ex + 13, eyeY + ry * 0.3, 5, 5.5, 0, 0, Math.PI * 2);
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
      fctx.ellipse(256 + s * 160, 335, 29, 17, 0, 0, Math.PI * 2);
      fctx.fill();
    }
  }

  // mouth (talking + external lip-sync overlay extra openness)
  let open = Math.max(P.mouthOpen, mouthDrive.v);
  if (performance.now() < speech.until) {
    const n = Math.abs(Math.sin(t * 11)) * (0.6 + 0.4 * Math.sin(t * 3.7 + 1));
    open = Math.max(open, 0.15 + 0.5 * Math.abs(n));
  }
  const mx = 256 + P.mouthX * 40;
  const my = 378;
  const mw = 66 * P.mouthWidth;
  const c = P.mouthCurve;
  const endY = my - c * 16;

  if (open < 0.1) {
    fctx.strokeStyle = ink;
    fctx.lineWidth = 14;
    fctx.lineCap = 'round';
    fctx.beginPath();
    fctx.moveTo(mx - mw, endY);
    fctx.quadraticCurveTo(mx, my + c * 34, mx + mw, endY);
    fctx.stroke();
  } else {
    const topCtl = my + c * 26 - open * 10;
    const botCtl = my + c * 26 + open * 88 + 14;
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
// Liveness — idle animation layer so Moxie feels alive.
//
// Additive-only: offsets are applied on top of the commanded motor angles at
// render time and are never written into motorTargets/motorValues, so
// getMotor() always reports the commanded state. Each DOF's liveness weight
// eases to zero while that DOF is being actively commanded (recent setMotor /
// slider input, or still travelling to its target), then eases back in.
//
// Layers:
//   * breathing  — ~4.3 s body scale + vertical bob (breatheG, visual only)
//   * micro      — continuous tiny sways on head/yaw/lean/arms
//   * behaviors  — randomized idle behaviors mirroring the robot's set:
//        Bht_Idle_Curious           head tilt + roll + slight body turn, gaze
//        Bht_Idle_Active_Listening  slight lean in, head tips down a touch
//        weight-shift               tiny yaw/lean drift
//        arm-settle                 one arm eases out and back
//   * gaze drift — small randomized pupil offsets (idleEyes)
// ---------------------------------------------------------------------------

const liveness = {
  enabled: true,
  master: 1,                              // eases toward enabled ? 1 : 0
  w: new Float32Array(7).fill(1),         // per-DOF blend weight
  cmdAt: new Float32Array(7).fill(-1e9),  // ms timestamp of last command per DOF
  off: new Float32Array(7),               // eased behavior offsets (radians)
  tgt: new Float32Array(7),               // behavior target offsets
  out: new Float32Array(7),               // final per-frame additive angles
  roll: 0, rollTgt: 0,                    // visual-only curious head roll
  gazeX: 0, gazeY: 0,                     // behavior-driven gaze target
  eyeTX: 0, eyeTY: 0, eyeNext: 1.5,       // random gaze drift
  mode: 'idle', until: 0, nextAt: 2.0 + Math.random() * 3,
};

function noteCommand(i) {
  liveness.cmdAt[i] = performance.now();
}

function pickIdleBehavior(t) {
  const L = liveness;
  const r = Math.random();
  const dir = Math.random() < 0.5 ? -1 : 1;
  L.tgt.fill(0);
  L.rollTgt = 0;
  L.gazeX = 0;
  L.gazeY = 0;

  const speaking = performance.now() < speech.until;
  if (speaking || r < 0.30) {
    // Bht_Idle_Active_Listening — lean in slightly, head tips down a touch
    L.mode = 'listen';
    L.tgt[6] = 0.09 + Math.random() * 0.05;      // lean forward
    L.tgt[4] = 0.04 + Math.random() * 0.03;      // head pitches down slightly
    L.until = t + 2.2 + Math.random() * 1.6;
  } else if (r < 0.62) {
    // Bht_Idle_Curious — head tilt + roll, slight body turn, gaze follows
    L.mode = 'curious';
    L.tgt[4] = -(0.05 + Math.random() * 0.08);   // head tips up a bit
    L.tgt[5] = dir * (0.10 + Math.random() * 0.16);
    L.rollTgt = -dir * (0.08 + Math.random() * 0.08);
    L.gazeX = dir * (0.35 + Math.random() * 0.25);
    L.gazeY = -(Math.random() * 0.2);
    L.until = t + 1.8 + Math.random() * 1.8;
  } else if (r < 0.82) {
    // weight shift — tiny yaw + lean drift
    L.mode = 'shift';
    L.tgt[5] = dir * (0.04 + Math.random() * 0.05);
    L.tgt[6] = (Math.random() - 0.5) * 0.06;
    L.until = t + 1.6 + Math.random() * 1.4;
  } else {
    // arm settle — one arm eases out and back
    L.mode = 'settle';
    if (Math.random() < 0.5) {
      L.tgt[0] = -(0.04 + Math.random() * 0.04);  // left shell flaps out a touch
      L.tgt[1] = 0.04 + Math.random() * 0.04;
    } else {
      L.tgt[2] = 0.04 + Math.random() * 0.04;
      L.tgt[3] = -(0.04 + Math.random() * 0.04);
    }
    L.until = t + 1.2 + Math.random() * 1.0;
  }
}

function updateLiveness(t, dt, now) {
  const L = liveness;

  // master fade for setIdle()
  const mTgt = L.enabled ? 1 : 0;
  L.master += (mTgt - L.master) * (1 - Math.exp(-dt * 3));

  // behavior scheduler
  if (t >= L.until && L.mode !== 'idle') {
    L.mode = 'idle';
    L.tgt.fill(0);
    L.rollTgt = 0;
    L.gazeX = 0;
    L.gazeY = 0;
    L.nextAt = t + 2.0 + Math.random() * 5.0;
  }
  if (L.mode === 'idle' && t >= L.nextAt && L.master > 0.05) {
    pickIdleBehavior(t);
  }

  // eased behavior offsets
  const kb = 1 - Math.exp(-dt * 2.5);
  for (let i = 0; i < 7; i++) L.off[i] += (L.tgt[i] - L.off[i]) * kb;
  L.roll += (L.rollTgt - L.roll) * kb;

  // per-DOF suppression: recently commanded or still travelling -> weight 0
  for (let i = 0; i < 7; i++) {
    const active = (now - L.cmdAt[i] < 1200) ||
                   Math.abs(motorTargets[i] - motorValues[i]) > 80;
    const wTgt = active ? 0 : 1;
    const rate = active ? 8 : 1.5;          // duck fast, resume slowly
    L.w[i] += (wTgt - L.w[i]) * (1 - Math.exp(-dt * rate));
  }

  // continuous micro-motion (gentle head sway, weight shift, arm drift)
  const micro = [
    -0.008 * Math.sin(t * 1.05 + 0.4),          // L shoulder
    0.006 * Math.sin(t * 0.85 + 2.2),           // L elbow
    0.008 * Math.sin(t * 1.05 + 3.5),           // R shoulder
    -0.006 * Math.sin(t * 0.85 + 5.0),          // R elbow
    0.012 * Math.sin(t * 0.47 + 1.3),           // head tilt
    0.018 * Math.sin(t * 0.33) + 0.006 * Math.sin(t * 0.9 + 2.0),  // yaw
    0.006 * Math.sin(t * 0.80 + 0.5),           // lean
  ];

  for (let i = 0; i < 7; i++) {
    L.out[i] = L.master * L.w[i] * (L.off[i] + micro[i]);
  }

  // gaze: behavior gaze + random small drift
  if (t > L.eyeNext) {
    if (Math.random() < 0.4) { L.eyeTX = 0; L.eyeTY = 0; }
    else {
      L.eyeTX = (Math.random() * 2 - 1) * 0.45;
      L.eyeTY = (Math.random() * 2 - 1) * 0.25;
    }
    L.eyeNext = t + 1.2 + Math.random() * 2.6;
  }
  const ke = 1 - Math.exp(-dt * 4);
  idleEyes.x += ((L.eyeTX + L.gazeX) * L.master - idleEyes.x) * ke;
  idleEyes.y += ((L.eyeTY + L.gazeY) * L.master - idleEyes.y) * ke;

  return L.out;
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
    noteCommand(i);
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

  // Lip-sync drive from the audio layer: 0 = closed, 1 = fully open.
  // Additive/harmless when unused — it only opens the mouth further than the
  // current expression does.
  setMouthOpen(v) {
    if (!Number.isFinite(v)) return;
    mouthDrive.v = Math.min(1, Math.max(0, v));
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

  // Toggle the idle liveness layer (breathing/blink stay subtle regardless).
  setIdle(on) {
    liveness.enabled = on !== false;
  },

  // Scene lighting, 0 (near-dark — the projected face lights the room)
  // to 1 (fully lit). Eased over a few frames.
  setSceneLight(level) {
    if (!Number.isFinite(level)) return;
    sceneLight.level = Math.min(1, Math.max(0, level));
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
      noteCommand(i);
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
  const now = performance.now();

  // smooth motor motion
  const k = 1 - Math.exp(-dt * 7);
  for (let i = 0; i < 7; i++) {
    motorValues[i] += (motorTargets[i] - motorValues[i]) * k;
    if (Math.abs(motorTargets[i] - motorValues[i]) < 2) motorValues[i] = motorTargets[i];
  }

  // liveness offsets (additive, never stored back into motor state)
  const live = updateLiveness(t, dt, now);

  const a = [0, 1, 2, 3, 4, 5, 6].map(motorAngle);

  armL.shoulder.rotation.z = a[0] + live[0];
  armL.elbow.rotation.z    = a[1] + live[1];
  armR.shoulder.rotation.z = a[2] + live[2];
  armR.elbow.rotation.z    = a[3] + live[3];
  headTiltG.rotation.x     = a[4] + live[4];
  headRollG.rotation.z     = liveness.master * liveness.w[4] * liveness.roll;
  yawG.rotation.y          = a[5] + live[5];
  leanG.rotation.x         = a[6] + live[6];

  // breathing — slow body scale + vertical bob (~4.3 s cycle)
  const breath = Math.sin(t * (Math.PI * 2 / 4.3)) * liveness.master;
  breatheG.scale.set(1 - 0.004 * breath, 1 + 0.011 * breath, 1 - 0.004 * breath);
  breatheG.position.y = 0.005 * breath;

  // scene lighting eases toward the commanded level
  sceneLight.current += (sceneLight.level - sceneLight.current) * (1 - Math.exp(-dt * 5));
  applySceneLight();

  // face params ease toward target
  const fk = 1 - Math.exp(-dt * 9);
  for (const key of Object.keys(faceParams)) {
    faceParams[key] += (faceTarget[key] - faceParams[key]) * fk;
  }

  // blinking — randomized, with an occasional quick double-blink
  if (blink.active) {
    blink.phase += dt / 0.22;
    if (blink.phase >= 1) {
      blink.active = false;
      blink.next = t + (Math.random() < 0.12 ? 0.25 : 1.8 + Math.random() * 4.2);
    }
  } else if (t > blink.next) {
    blink.active = true;
    blink.phase = 0;
  }

  drawFace(t);

  // heart LED — glows in the commanded colour when on; when off it stays a
  // subtle light-grey/white marking (never a dark shape)
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

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
