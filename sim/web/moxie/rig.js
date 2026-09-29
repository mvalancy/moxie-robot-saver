// The robot: materials and the jointed rig (base, body split at the lean joint, head with
// face screen + lens, arms with spring elbows, heart LED, decals), plus the debug axis
// overlay. Pure construction — moxie.js drives the joints every frame.
import * as THREE from 'three';
import { COL, BODY_TOP, HEAD_PIVOT_Y, SHOULDER_Y, LEAN_PIVOT_Y } from './config.js';
import {
  welded, smoothLathe, bodyProfilePts, bodyRadiusAt, smoothSphere, makeArmShellGeometry,
  eggify, flattenFront, sphericalUVs, bentPlate, facePanelGeometry,
} from './geometry.js';
import {
  radialGlow, makeHeadTexture, makeGrilleTexture, makeWordmarkTexture, makeHeartLEDTexture,
  makeLabel,
} from './textures.js';
import { faceTex } from './face.js';
import { scene } from './scene.js';

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

export const yawG = new THREE.Group();
root.add(yawG);

// Moxie leans at a single joint ABOVE the speaker, so the body mesh is split there.
export const breatheG = new THREE.Group();
yawG.add(breatheG);
const lowerG = new THREE.Group();
breatheG.add(lowerG);
export const leanG = new THREE.Group();
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
export const headTiltG = new THREE.Group();          // motor 4
headTiltG.position.y = HEAD_PIVOT_Y;
upperG.add(headTiltG);
const headRollG = new THREE.Group();
headTiltG.add(headRollG);
const headForm = new THREE.Group();           // constant slight forward tilt
headForm.rotation.x = 0.05;
headRollG.add(headForm);

const HEAD_C = new THREE.Vector3(0, 0.64, 0.06);      // head centre (local)
const HEAD_R = new THREE.Vector3(0.66, 0.60, 0.63);   // wider than tall, pointy top

export const head = shadowed(new THREE.Mesh(
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

export const screenMat = new THREE.MeshPhysicalMaterial({
  map: faceTex, emissive: 0xffffff, emissiveMap: faceTex, emissiveIntensity: 0.45,
  roughness: 1.0, metalness: 0.0, clearcoat: 0.0, reflectivity: 0.0,
});
const screen = new THREE.Mesh(facePanelGeometry(FACE_RX, FACE_RY), screenMat);
screen.position.z = 0.422;
faceAssembly.add(screen);

// Projector spill light, placed in front of and BELOW the pane so it lights the chest
// without a hotspot on the screen. Scales up as the scene dims.
export const faceLight = new THREE.PointLight(0xf3eedd, 0.15, 3.4, 2);
faceLight.position.set(0, -0.34, 1.15);
faceAssembly.add(faceLight);

// Warm annulus AROUND the pane (clear centre, so it never veils the face).
export const faceHalo = new THREE.Sprite(new THREE.SpriteMaterial({
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
export const heartMat = new THREE.MeshStandardMaterial({
  map: heartTex, color: 0xdfe9ea, transparent: true, emissive: 0xffffff, emissiveMap: heartTex,
  emissiveIntensity: 0.10, roughness: 0.55, polygonOffset: true, polygonOffsetFactor: -2,
});
const heart = new THREE.Mesh(
  bentPlate(new THREE.CircleGeometry(1, 48), 0.17, 0.17, bodyRadiusAt(1.08), 1.6), heartMat);
heart.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.006);
heart.rotation.x = -0.20;             // follows the chest's backward taper
upperG.add(heart);

export const heartLight = new THREE.PointLight(0xff5577, 0, 1.2);
heartLight.position.set(0, 1.08, bodyRadiusAt(1.08) + 0.12);
upperG.add(heartLight);

export const heartState = { on: false, color: new THREE.Color(0xff5577) };

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
export const armL = makeArm(+1);
export const armR = makeArm(-1);

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

export function setShowAxes(on) {
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
