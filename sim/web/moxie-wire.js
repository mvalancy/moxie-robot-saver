/* moxie-wire.js — a faint, slowly-rotating WIREFRAME Moxie, built from the same
 * proportions as the simulator model (moxie.js). Used as a "blueprint" background
 * decoration. Self-contained ES module; degrades silently if WebGL is unavailable.
 *
 *   import { mountMoxieWire } from "./moxie-wire.js";
 *   mountMoxieWire(document.getElementById("wire"), { opacity: 0.18 });
 */
/* The VENDORED path, not the bare specifier `"three"`, and that is load-bearing.
 * index/setup/cloud used to carry a `<script type="importmap">` whose only job was to
 * resolve that one word — an inline block, so it could only run under
 * `script-src 'unsafe-inline'` or a SHA-256 hash that blanks the page when it drifts.
 * Naming the file deletes all three maps. (`sim.html` still needs its own importmap:
 * `moxie.js` pulls three/addons, and the vendored addons import bare `"three"`
 * themselves — see `sim/web/_headers`.)
 */
import * as THREE from "./vendor/three/three.module.js";

// Body silhouette (lathe profile), simplified from moxie.js bodyProfilePts:
// base → speaker → chest seam (steps out) → upper chest tapering to the neck.
const PROFILE = [
  [0.00, 0.00], [0.42, 0.02], [0.52, 0.12], [0.56, 0.32], [0.585, 0.55],
  [0.60, 0.62], [0.658, 0.66], [0.664, 0.70],
  [0.650, 0.86], [0.612, 1.05], [0.560, 1.22], [0.50, 1.34], [0.40, 1.43],
  [0.26, 1.49], [0.00, 1.50],
].map(([x, y]) => new THREE.Vector2(x, y));

function wire(geo, color, opacity) {
  return new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    color, wireframe: true, transparent: true, opacity, depthWrite: false,
  }));
}

export function mountMoxieWire(container, opts) {
  opts = opts || {};
  var op = opts.opacity != null ? opts.opacity : 0.18;
  var cyan = 0x2fe6ff, teal = 0x12b5b0;
  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  } catch (e) { return function () {}; }          // no WebGL → no decoration
  if (!renderer || !renderer.getContext()) return function () {};

  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
  container.appendChild(renderer.domElement);

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);

  var moxie = new THREE.Group();

  // body (two visual masses share one lathe silhouette)
  moxie.add(wire(new THREE.LatheGeometry(PROFILE, 40), cyan, op));
  // a seam ring at the chest divider (~y 0.66)
  var seam = wire(new THREE.TorusGeometry(0.655, 0.006, 6, 44), teal, op * 1.1);
  seam.position.y = 0.665; seam.rotation.x = Math.PI / 2; moxie.add(seam);
  // base
  var base = wire(new THREE.CylinderGeometry(0.44, 0.5, 0.08, 40), teal, op);
  base.position.y = 0.02; moxie.add(base);
  // neck
  var neck = wire(new THREE.CylinderGeometry(0.22, 0.24, 0.12, 24), cyan, op);
  neck.position.y = 1.5; moxie.add(neck);
  // head — wider than tall, pointy-ish top (scaled sphere)
  var head = wire(new THREE.SphereGeometry(0.6, 30, 22), cyan, op);
  head.scale.set(1.08, 0.98, 1.03); head.position.y = 2.14; moxie.add(head);
  // face plate ring (the projector face)
  var face = wire(new THREE.CircleGeometry(0.46, 40), teal, op * 1.2);
  face.position.set(0, 2.16, 0.5); moxie.add(face);
  // ears (recessed oval mic cutouts)
  [-1, 1].forEach(function (s) {
    var ear = wire(new THREE.CylinderGeometry(0.12, 0.12, 0.05, 18), teal, op);
    ear.rotation.z = Math.PI / 2; ear.scale.set(1, 1, 0.6);
    ear.position.set(s * 0.62, 2.16, 0); moxie.add(ear);
  });
  // arms — thin capsules along the flanks
  [-1, 1].forEach(function (s) {
    var arm = new THREE.Group();
    var upper = wire(new THREE.CapsuleGeometry(0.09, 0.42, 4, 10), cyan, op);
    upper.position.y = -0.24; arm.add(upper);
    var fore = wire(new THREE.CapsuleGeometry(0.085, 0.34, 4, 10), cyan, op);
    fore.position.y = -0.66; arm.add(fore);
    arm.position.set(s * 0.70, 1.10, 0.02);
    arm.rotation.z = s * 0.06;
    moxie.add(arm);
  });

  // centre the group vertically in view
  moxie.position.y = -1.05;
  scene.add(moxie);

  camera.position.set(0, 0.15, 5.6);
  camera.lookAt(0, 0.05, 0);

  var raf = 0, reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  function size() {
    var w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  size();
  var ro = ("ResizeObserver" in window) ? new ResizeObserver(size) : null;
  if (ro) ro.observe(container); else window.addEventListener("resize", size);

  var t = 0;
  function loop() {
    t += reduce ? 0 : 0.0032;
    moxie.rotation.y = reduce ? -0.5 : (-0.5 + Math.sin(t) * 0.55);   // gentle sway, never a full spin
    moxie.rotation.x = Math.sin(t * 0.6) * 0.04;
    renderer.render(scene, camera);
    raf = requestAnimationFrame(loop);
  }
  loop();

  return function cleanup() {
    cancelAnimationFrame(raf);
    if (ro) ro.disconnect(); else window.removeEventListener("resize", size);
    renderer.dispose(); if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
  };
}
