// Pure geometry builders for the Moxie model (no scene state).
import * as THREE from 'three';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { BODY_TOP } from './config.js';

// Drop UVs/normals and weld seams so computeVertexNormals() shades smoothly.
export function welded(geo) {
  geo.deleteAttribute('uv');
  geo.deleteAttribute('normal');
  return mergeVertices(geo, 1e-4);
}

export function smoothLathe(profile) {
  const g = welded(new THREE.LatheGeometry(profile, 128));
  g.computeVertexNormals();
  return g;
}

// Body: an upright, softly-rounded pear (lathe) — widest low, tapering to a broad rounded
// shoulder the head sits on.
export const bodyProfilePts = (() => {
  const ctrl = [
    // LOWER CHEST (speaker section) — ends just under the seam
    [0.00, 0.05], [0.36, 0.05], [0.58, 0.10], [0.628, 0.28],
    [0.632, 0.46], [0.628, 0.60],
    // chest seam: the lower chest tucks IN and the upper chest starts slightly WIDER, so
    // the upper overhangs and casts a shadow line (real Moxie's two-segment torso)
    [0.612, 0.645], [0.606, 0.660],
    [0.658, 0.678], [0.664, 0.700],
    // UPPER CHEST (arms + heart LED) — tapers up to the neck
    [0.650, 0.86], [0.612, 1.05], [0.560, 1.22],
    [0.50, 1.34], [0.40, 1.43], [0.26, 1.49], [0.00, BODY_TOP],
  ].map(([x, y]) => new THREE.Vector3(x, y, 0));
  const curve = new THREE.CatmullRomCurve3(ctrl);
  return curve.getPoints(120).map(p => new THREE.Vector2(Math.max(0, p.x), p.y));
})();

// Radius of the body surface at height y — used to wrap the arm shells and decals.
export function bodyRadiusAt(y) {
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

// A unit sphere with welded verts, ready for reshaping + computeVertexNormals.
export function smoothSphere(wSeg, hSeg, ...sector) {
  return welded(new THREE.SphereGeometry(1, wSeg, hSeg, ...sector));
}

// Arm shell segment: a capsule (constant width, rounded ends) wrapped around the body's
// lathe profile. halfW is a LINEAR half-width converted to an arc per vertex, so the shell
// keeps one width down the tapering body. `pivot` is the joint (body space); the geometry
// is re-origined there. The wrap mirrors chirality for one side, which would flip that
// arm's normals inside-out — so the winding is checked against the outward direction and
// re-wound if needed.
export function makeArmShellGeometry(side, yTop, yBot, halfW, thickness, thetaBias, pivot,
                                     standoff = 0) {
  const height = yTop - yBot;
  const capH = Math.min(0.16, height * 0.35);
  const len = Math.max(0.01, height / capH - 2);
  const geo = welded(new THREE.CapsuleGeometry(1, len, 10, 36));
  const pos = geo.attributes.position;
  const yc = (yTop + yBot) / 2;
  const yScale = height / (len + 2);
  const theta0 = side * (Math.PI / 2 - thetaBias);
  for (let i = 0; i < pos.count; i++) {
    const sx = pos.getX(i), sy = pos.getY(i), sz = pos.getZ(i);
    const y = yc + sy * yScale;
    const rBase = bodyRadiusAt(y) + 0.012 + standoff + thickness;   // ride ON the flank
    const theta = theta0 + side * sx * (halfW / rBase);
    const r = rBase + sz * thickness;
    pos.setXYZ(i, r * Math.sin(theta) - pivot.x, y - pivot.y, r * Math.cos(theta) - pivot.z);
  }
  geo.computeVertexNormals();
  const nrm = geo.attributes.normal;
  let outward = 0;
  for (let i = 0; i < pos.count; i++) {
    outward += nrm.getX(i) * (pos.getX(i) + pivot.x) + nrm.getZ(i) * (pos.getZ(i) + pivot.z);
  }
  if (outward < 0) {
    const idx = geo.index.array;
    for (let i = 0; i < idx.length; i += 3) {
      const t = idx[i + 1]; idx[i + 1] = idx[i + 2]; idx[i + 2] = t;
    }
    geo.computeVertexNormals();
  }
  return geo;
}

// Head shell: taper the crown and ease it back so the silhouette reads as a teardrop.
export function eggify(geo, rx, ry, rz) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i), z = pos.getZ(i);
    const y = pos.getY(i);
    const t = Math.max(0, y);                // 0 at equator -> 1 at crown
    const pinch = 1 - 0.22 * t * t;
    x *= pinch;
    z = z * pinch - 0.14 * t * t;
    pos.setXYZ(i, x * rx, y * ry, z * rz);
  }
  geo.computeVertexNormals();
  return geo;
}

// Slice the front of the egg flat for the screen, soft-clamping z so the crease is a fillet.
export function flattenFront(geo, zPlane, fillet) {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const d = pos.getZ(i) - (zPlane - fillet);
    if (d > 0) pos.setZ(i, zPlane - fillet + fillet * (1 - Math.exp(-d / fillet)));
  }
  geo.computeVertexNormals();
  return geo;
}

// UVs from final positions: u is the MIRRORED side angle atan2(x, |z|), continuous over the
// head (no wrap seam), mapping each side onto one texture edge where the ear is painted.
export function sphericalUVs(geo, ry) {
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

// Bake scale into a flat geometry, then give it a shallow curve (body decals).
export function bentPlate(geo, sx, sy, Rx, Ry) {
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

// The face screen: flat across the middle, diving only in the last ~10% of the radius so
// the rim tucks behind the shell. RingGeometry (inner radius ~0) rather than
// CircleGeometry, because only it has radial segments to carry that profile. Its planar
// UVs map the face canvas 1:1.
export function facePanelGeometry(rx, ry) {
  const geo = new THREE.RingGeometry(0.001, 1, 96, 24);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const ux = pos.getX(i), uy = pos.getY(i);
    const s = Math.min(1, Math.hypot(ux, uy));
    const dive = 0.012 * s * s + 0.055 * Math.pow(s, 10);
    pos.setXYZ(i, ux * rx, uy * ry, -dive);
  }
  geo.computeVertexNormals();
  return geo;
}
