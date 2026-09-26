// Canvas-drawn textures for the model: shell markings, decals, glows, debug labels.
import * as THREE from 'three';

export function canvasTexture(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  draw(c.getContext('2d'), c);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function roundedRectPath(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

// A small heart centred on (0,0), ~28 x 21 units (the LED marking and the icon glyph).
export function heartPath(g) {
  g.beginPath();
  g.moveTo(0, 11);
  g.bezierCurveTo(-14, 1, -13, -10, -6.5, -10);
  g.bezierCurveTo(-2.5, -10, 0, -7, 0, -4);
  g.bezierCurveTo(0, -7, 2.5, -10, 6.5, -10);
  g.bezierCurveTo(13, -10, 14, 1, 0, 11);
  g.closePath();
}

// Radial gradient on a 256 square: stops are [offset, css colour].
export function radialGlow(stops) {
  return canvasTexture(256, 256, (g) => {
    const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    for (const [o, col] of stops) grad.addColorStop(o, col);
    g.fillStyle = grad;
    g.fillRect(0, 0, 256, 256);
  });
}

// Uniform shell teal with the two ear ovals PAINTED on (no geometry, so nothing clips or
// z-fights). One oval sits on the texture's wrap edge, drawn at both canvas edges; the
// mirrored UV mapping (geometry.js::sphericalUVs) places that edge at each side of the head.
export function makeHeadTexture() {
  const t = canvasTexture(1024, 512, (g) => {
    g.fillStyle = '#3bb6b0';                    // must match COL.shell exactly
    g.fillRect(0, 0, 1024, 512);
    for (const cx of [0, 1024]) {
      g.save();
      g.translate(cx, 250);
      g.fillStyle = 'rgba(10, 25, 30, 0.10)';   // whisper-faint inset shading
      g.beginPath();
      g.ellipse(0, 0, 74, 26, 0, 0, Math.PI * 2);   // horizontal oval mic port
      g.fill();
      g.strokeStyle = 'rgba(14, 34, 40, 0.55)';
      g.lineWidth = 5;
      g.stroke();
      g.restore();
    }
  });
  t.wrapS = THREE.RepeatWrapping;
  t.anisotropy = 4;
  return t;
}

// A ROUND speaker port: a circular field of dots on a square canvas.
export function makeGrilleTexture() {
  return canvasTexture(200, 200, (g) => {
    g.fillStyle = 'rgba(18, 42, 44, 0.9)';
    const cx = 100, cy = 100, R = 88;
    for (let y = 16, row = 0; y <= 184; y += 13, row++) {
      const odd = (row % 2) === 0 ? 6.5 : 0;
      for (let x = 12 + odd; x <= 188; x += 13) {
        const dx = x - cx, dy = y - cy;
        if (dx * dx + dy * dy <= R * R) {
          g.beginPath();
          g.arc(x, y, 3.2, 0, Math.PI * 2);
          g.fill();
        }
      }
    }
  });
}

export function makeWordmarkTexture() {
  return canvasTexture(256, 64, (g) => {
    g.fillStyle = 'rgba(16, 49, 52, 0.88)';
    g.font = '600 38px "Trebuchet MS", "Segoe UI", system-ui, sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('moxie', 128, 34);
  });
}

// Heart LED marking: a thin white line with a tiny heart beneath it. The same texture is
// the emissive mask, so it glows in the commanded colour when on.
export function makeHeartLEDTexture() {
  return canvasTexture(256, 256, (g) => {
    g.fillStyle = '#ffffff';
    roundedRectPath(g, 48, 100, 160, 9, 4.5);
    g.fill();
    g.save();
    g.translate(128, 146);
    g.scale(1.35, 1.35);
    heartPath(g);
    g.fill();
    g.restore();
  });
}

// Debug-axis label sprite.
export function makeLabel(text, color) {
  const tex = canvasTexture(512, 96, (g, c) => {
    g.fillStyle = 'rgba(6,6,9,0.78)';
    g.fillRect(0, 0, c.width, c.height);
    g.font = 'bold 44px "JetBrains Mono", monospace';
    g.fillStyle = color || '#e8edf5';
    g.textBaseline = 'middle';
    g.fillText(text, 12, c.height / 2);
  });
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  spr.scale.set(0.62, 0.116, 1);
  spr.renderOrder = 999;
  return spr;
}
