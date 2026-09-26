// The face: a parameterised cartoon drawn every frame onto a canvas texture, plus the
// icon badges (cmd:icons-v2) that overlay it.
import * as THREE from 'three';
import { roundedRectPath, heartPath } from './textures.js';

// The robot's real expression set: the 11 `ePlaybackMood` / `Bht_Eyeseme_*` moods from the
// firmware (docs/reverse-engineering/behavior-markup.md), plus `sleep` and a generic
// `thinking`. Each is a cute-companion read — Moxie is never scary. `blush` boosts the
// cheeks for the bashful moods.
export const EXPRESSIONS = {
  sleep:      { eyeW: 1.00, eyeH: 0.06, browRaise: -0.25, browTilt: 0.0, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.0, mouthCurve: 0.10, mouthOpen: 0.02, mouthWidth: 0.8, mouthX: 0.0, blush: 0.0 },
  neutral:    { eyeW: 1.00, eyeH: 1.00, browRaise: 0.0, browTilt: 0.0, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.0, mouthCurve: 0.18, mouthOpen: 0.04, mouthWidth: 1.0, mouthX: 0.0, blush: 0.0 },
  happy:      { eyeW: 1.00, eyeH: 0.62, browRaise: 0.2, browTilt: 0.0, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.0, mouthCurve: 1.00, mouthOpen: 0.35, mouthWidth: 1.1, mouthX: 0.0, blush: 0.18 },
  sad:        { eyeW: 0.95, eyeH: 0.85, browRaise: 0.15, browTilt: 1.0, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.5, mouthCurve: -0.9, mouthOpen: 0.03, mouthWidth: 0.8, mouthX: 0.0, blush: 0.0 },
  // a CUTE grumpy pout: low furrowed brows, narrow squint, tight little frown
  angry:      { eyeW: 1.00, eyeH: 0.50, browRaise: -0.35, browTilt: -1.4, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.30, mouthCurve: -0.45, mouthOpen: 0.04, mouthWidth: 0.50, mouthX: 0.0, blush: 0.08 },
  shy:        { eyeW: 0.90, eyeH: 0.62, browRaise: 0.05, browTilt: 0.2, browAsym: 0.0,
                pupilX: 0.72, pupilY: 0.32, mouthCurve: 0.45, mouthOpen: 0.03, mouthWidth: 0.60, mouthX: 0.14, blush: 0.62 },
  surprised:  { eyeW: 1.15, eyeH: 1.30, browRaise: 1.0, browTilt: 0.0, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.0, mouthCurve: 0.0, mouthOpen: 0.95, mouthWidth: 0.45, mouthX: 0.0, blush: 0.0 },
  afraid:     { eyeW: 1.14, eyeH: 1.22, browRaise: 0.75, browTilt: 0.95, browAsym: 0.0,
                pupilX: 0.0, pupilY: -0.10, mouthCurve: -0.4, mouthOpen: 0.42, mouthWidth: 0.58, mouthX: 0.0, blush: 0.0 },
  concerned:  { eyeW: 1.00, eyeH: 0.90, browRaise: 0.25, browTilt: 0.7, browAsym: 0.0,
                pupilX: 0.0, pupilY: 0.12, mouthCurve: -0.4, mouthOpen: 0.04, mouthWidth: 0.85, mouthX: 0.0, blush: 0.0 },
  confused:   { eyeW: 0.98, eyeH: 0.85, browRaise: 0.2, browTilt: 0.0, browAsym: 1.0,
                pupilX: -0.4, pupilY: 0.0, mouthCurve: -0.1, mouthOpen: 0.05, mouthWidth: 0.50, mouthX: -0.35, blush: 0.0 },
  curious:    { eyeW: 1.08, eyeH: 1.08, browRaise: 0.65, browTilt: 0.0, browAsym: 0.35,
                pupilX: 0.2, pupilY: -0.25, mouthCurve: 0.35, mouthOpen: 0.12, mouthWidth: 0.80, mouthX: 0.1, blush: 0.1 },
  embarrassed:{ eyeW: 0.95, eyeH: 0.50, browRaise: 0.15, browTilt: 0.4, browAsym: 0.0,
                pupilX: -0.45, pupilY: 0.4, mouthCurve: 0.42, mouthOpen: 0.05, mouthWidth: 0.62, mouthX: -0.12, blush: 0.70 },
  // pensive look-away (not an Eyeseme mood, kept for the UI)
  thinking:   { eyeW: 0.95, eyeH: 0.80, browRaise: 0.3, browTilt: 0.0, browAsym: 1.0,
                pupilX: 0.6, pupilY: -0.6, mouthCurve: 0.05, mouthOpen: 0.03, mouthWidth: 0.55, mouthX: 0.5, blush: 0.0 },
};

export const face = {
  params: { ...EXPRESSIONS.neutral },
  target: { ...EXPRESSIONS.neutral },
};
export const blink = { active: false, phase: 0, next: 2.5 + Math.random() * 3 };
export const speech = { until: 0 };        // talking-mouth deadline (performance.now ms)
export const mouthDrive = { v: 0 };        // external lip-sync; only ever opens further
export const idleEyes = { x: 0, y: 0 };    // liveness gaze drift, additive on the pupils

const ICON_POP_MS = 200;      // per-badge pop-in duration
const ICON_STAGGER_MS = 60;   // delay between successive badges popping in
const ICON_FADE_MS = 180;     // fade-out duration on clearIcons()
export const icons = { names: [], shownAt: 0, fading: false, fadeAt: 0 };

const faceCanvas = document.createElement('canvas');
faceCanvas.width = 512;
faceCanvas.height = 512;
const fctx = faceCanvas.getContext('2d');
export const faceTex = new THREE.CanvasTexture(faceCanvas);
faceTex.colorSpace = THREE.SRGBColorSpace;
faceTex.anisotropy = 4;

// One glyph, centred on (0,0), sized for a ~46px chip.
function drawIconGlyph(name) {
  const n = name.toLowerCase();
  const g = fctx;
  if (n.includes('heart')) {             // also e.g. "Learning_About_Family_03_Heart_Family"
    g.fillStyle = '#e2607e';
    heartPath(g);
    g.fill();
  } else if (n.includes('medical')) {
    g.fillStyle = '#dd4f4a';
    roundedRectPath(g, -4.5, -12, 9, 24, 2.5);
    g.fill();
    roundedRectPath(g, -12, -4.5, 24, 9, 2.5);
    g.fill();
  } else if (n.includes('birthday')) {   // cake + candle
    g.fillStyle = '#e88ab0';
    roundedRectPath(g, -11, -2, 22, 13, 3);
    g.fill();
    g.fillStyle = '#fdf6ec';
    roundedRectPath(g, -11, -2, 22, 5, 2.5);
    g.fill();
    g.fillStyle = '#4d7fc4';
    roundedRectPath(g, -1.8, -10, 3.6, 8, 1.5);
    g.fill();
    g.fillStyle = '#f2a93b';
    g.beginPath();
    g.ellipse(0, -12.5, 2.4, 3.4, 0, 0, Math.PI * 2);
    g.fill();
  } else if (n.includes('school')) {     // graduation cap
    g.fillStyle = '#3f6fb5';
    g.beginPath();
    g.moveTo(0, -10);
    g.lineTo(14, -4);
    g.lineTo(0, 2);
    g.lineTo(-14, -4);
    g.closePath();
    g.fill();
    g.beginPath();
    g.moveTo(-7, -1);
    g.lineTo(7, -1);
    g.lineTo(7, 6);
    g.quadraticCurveTo(0, 10, -7, 6);
    g.closePath();
    g.fill();
    g.strokeStyle = '#f2a93b';
    g.lineWidth = 1.8;
    g.lineCap = 'round';
    g.beginPath();
    g.moveTo(14, -4);
    g.lineTo(14, 7);
    g.stroke();
    g.fillStyle = '#f2a93b';
    g.beginPath();
    g.arc(14, 8.5, 2.2, 0, Math.PI * 2);
    g.fill();
  } else {                               // unknown: first letter in the accent teal
    g.fillStyle = '#2b9a94';
    g.font = '700 22px "Segoe UI", system-ui, sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText((name.trim()[0] || '?').toUpperCase(), 0, 1);
  }
}

// A large symbol panel over the upper/middle face with the face dimmed behind it — how the
// robot shows scan/QR cues. Multiple icons sit side by side.
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

  const big = N === 1;
  const size = big ? 250 : 150, gap = big ? 0 : 16, rowY = 250;
  const rowW = N * size + (N - 1) * gap;

  fctx.save();
  fctx.globalAlpha = fadeA * 0.62;
  fctx.fillStyle = '#0f1e24';
  fctx.fillRect(0, 0, 512, 512);
  fctx.restore();

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

    fctx.shadowColor = 'rgba(29, 49, 56, 0.22)';
    fctx.shadowBlur = 6;
    fctx.shadowOffsetY = 2;
    fctx.fillStyle = '#ffffff';
    roundedRectPath(fctx, -size / 2, -size / 2, size, size, size * 0.22);
    fctx.fill();
    fctx.shadowColor = 'transparent';
    fctx.shadowBlur = 0;
    fctx.shadowOffsetY = 0;
    fctx.strokeStyle = 'rgba(29, 49, 56, 0.10)';
    fctx.lineWidth = 1.5;
    fctx.stroke();

    fctx.scale(size / 46 * 1.15, size / 46 * 1.15);   // glyph scales with the panel
    drawIconGlyph(icons.names[i]);
    fctx.restore();
  }
}

// Ease the face toward its target expression and run the randomized blink.
export function stepFace(t, dt) {
  const P = face.params, T = face.target;
  const fk = 1 - Math.exp(-dt * 9);
  for (const key of Object.keys(P)) P[key] += (T[key] - P[key]) * fk;

  if (blink.active) {
    blink.phase += dt / 0.22;
    if (blink.phase >= 1) {
      blink.active = false;
      blink.next = t + (Math.random() < 0.12 ? 0.25 : 1.8 + Math.random() * 4.2);   // occasional double-blink
    }
  } else if (t > blink.next) {
    blink.active = true;
    blink.phase = 0;
  }
}

// Only the inscribed circle of the canvas maps onto the elliptical panel (planar UVs,
// centre 256,256, radius 256); the outermost ~10% tucks behind the shell.
export function drawFace(t) {
  const P = face.params;
  const W = 512, H = 512;

  // warm off-white screen, brightest at the centre — a PROJECTOR beam, not a flat LCD
  const bg = fctx.createRadialGradient(256, 280, 40, 256, 280, 300);
  bg.addColorStop(0, '#fdfbf5');
  bg.addColorStop(0.55, '#efeadd');
  bg.addColorStop(1, '#d3ccbc');
  fctx.fillStyle = bg;
  fctx.fillRect(0, 0, W, H);

  // small dark zone at the very top holds the camera (dark on the emissive map = no glow)
  fctx.fillStyle = '#15232a';
  fctx.beginPath();
  fctx.moveTo(96, 0);
  fctx.lineTo(416, 0);
  fctx.quadraticCurveTo(430, 46, 256, 62);
  fctx.quadraticCurveTo(82, 46, 96, 0);
  fctx.closePath();
  fctx.fill();

  // Soft, KIND eyes: a gently lidded rounded shape with one soft shine, no glow halo.
  const ink = '#3f6f7d';
  const eyeTop = '#d6f0ff', eyeBot = '#57a9dd', eyeEdge = '#3182b4';
  const screenCol = '#fcf8f0';

  let blinkF = 1;
  if (blink.active) blinkF = Math.max(0.04, 1 - Math.sin(Math.PI * blink.phase));

  const eyeY = 258 + (P.pupilY + idleEyes.y) * 10;
  const eyeDX = 86;
  const rx = 43 * P.eyeW;
  const ry = Math.max(4, 56 * P.eyeH * blinkF);
  for (const s of [-1, 1]) {
    const ex = 256 + s * eyeDX + (P.pupilX + idleEyes.x) * 10;
    const eg = fctx.createLinearGradient(ex, eyeY - ry, ex, eyeY + ry);
    eg.addColorStop(0, eyeTop);
    eg.addColorStop(0.5, eyeBot);
    eg.addColorStop(1, eyeEdge);
    fctx.fillStyle = eg;
    fctx.beginPath();
    fctx.ellipse(ex, eyeY, rx, ry, 0, 0, Math.PI * 2);
    fctx.fill();
    if (ry > 16) {
      // upper lid in the screen colour: a soft curve instead of a wide-open stare
      fctx.fillStyle = screenCol;
      fctx.beginPath();
      fctx.ellipse(ex, eyeY - ry * 1.02, rx * 1.25, ry * 0.5, 0, 0, Math.PI * 2);
      fctx.fill();
      fctx.fillStyle = 'rgba(255,255,255,0.9)';
      fctx.beginPath();
      fctx.ellipse(ex - rx * 0.26 + P.pupilX * 5, eyeY - ry * 0.02, rx * 0.24, ry * 0.2, 0, 0, Math.PI * 2);
      fctx.fill();
    }
  }

  // eyebrows only when an expression asks for them (prominent brows read as stern)
  const browAmt = Math.min(1, P.browRaise + Math.abs(P.browTilt) + P.browAsym);
  if (browAmt > 0.05) {
    fctx.strokeStyle = '#7a97a1';
    fctx.globalAlpha = Math.min(1, browAmt + 0.25);
    fctx.lineWidth = 11;
    fctx.lineCap = 'round';
    for (const s of [-1, 1]) {
      const asymLift = P.browAsym * (s < 0 ? 16 : -2);
      const by = eyeY - ry - 24 - P.browRaise * 18 - asymLift;
      const tilt = P.browTilt * 14 * -s;                          // sad: inner ends up
      const x0 = 256 + s * (eyeDX - 28), x1 = 256 + s * (eyeDX + 30);
      const midX = 256 + s * eyeDX;
      fctx.beginPath();
      fctx.moveTo(x0, by + 5 + tilt * -1);
      fctx.quadraticCurveTo(midX, by - 8 + tilt * 0.4, x1, by + tilt);
      fctx.stroke();
    }
    fctx.globalAlpha = 1;
  }

  // rosy cheeks — a hint always, warmer when smiling, deeper for the bashful moods
  {
    const b = P.blush || 0;
    const cheekA = Math.min(0.62, 0.15 + 0.4 * Math.max(0, P.mouthCurve - 0.15) + b * 0.5);
    const cw = 30 + b * 9, ch = 17 + b * 5;
    fctx.fillStyle = `rgba(244, ${Math.round(156 - b * 26)}, ${Math.round(156 - b * 20)}, ${cheekA})`;
    for (const s of [-1, 1]) {
      fctx.beginPath();
      fctx.ellipse(256 + s * 148, eyeY + 66, cw, ch, 0, 0, Math.PI * 2);
      fctx.fill();
    }
  }

  // mouth (talking + external lip-sync add openness)
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

  // projector falloff over the features; icons are drawn after so overlays stay bright
  const proj = fctx.createRadialGradient(256, 282, 120, 256, 282, 262);
  proj.addColorStop(0, 'rgba(6,14,18,0)');
  proj.addColorStop(0.62, 'rgba(6,14,18,0.06)');
  proj.addColorStop(1, 'rgba(4,10,14,0.62)');
  fctx.fillStyle = proj;
  fctx.fillRect(0, 0, W, H);

  drawIconBadges();
  faceTex.needsUpdate = true;
}
