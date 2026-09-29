// Keep Moxie framed inside the part of the viewport nothing covers. Only the camera's VIEW
// OFFSET moves; the canvas stays full-screen at every width (test_responsive.mjs).
//
// Three boxes can eat the viewport: the header (topbar + notice; #stage's top edge),
// `#chat-dock` (always present — a bottom row, or a right column on a short landscape
// screen) and `#panel` while open (a right column >=900 px, a bottom drawer below). Each
// contributes a bound and one frameInRect call uses them all.
// Re-run on resize and drawer toggle only — never on dock growth, which would move the
// camera under someone who is reading.
import { invalidateBubbleMetrics } from './bubble.js';

// Headroom: frame her a little low so the bubble fits above her head. A fraction of the
// free band, so tall portrait screens get proportionally more.
const HEADROOM = 0.09;

// Render the base frustum onto a k-times larger virtual image, offset so its centre lands
// at (cx, cy): the whole normal framing shrinks to fit the free vw x vh band.
function frameInRect(camera, vw, vh, cx, cy) {
  const W = window.innerWidth, H = window.innerHeight;
  const k = Math.max(W / vw, H / vh);
  const offX = (W - W * k) / 2 - (cx - W / 2) * k;
  const offY = (H - H * k) / 2 - (cy - H / 2) * k;
  camera.setViewOffset(W, H, offX, offY, W * k, H * k);
}

export function installStageFraming(camera, renderer) {
  function applyStageOffset() {
    const W = window.innerWidth, H = window.innerHeight;
    camera.aspect = W / H;
    camera.clearViewOffset();
    let right = W, bottom = H;
    const dock = document.getElementById('chat-dock');
    if (dock) {
      const d = dock.getBoundingClientRect();
      if (d.height > 0 && d.top > H * 0.4) bottom = d.top;
      else if (d.width > 0 && d.left > W * 0.45 && d.height > H * 0.6) right = Math.min(right, d.left);
    }
    const panel = document.getElementById('panel');
    const hud = document.getElementById('hud');
    if (panel && hud && !hud.classList.contains('rail-closed')) {
      const r = panel.getBoundingClientRect();
      if (r.width > 20 && r.height > 20) {
        const isRightColumn = r.right >= W - 8 && r.top < H * 0.4 && r.height > H * 0.55 && r.width < W * 0.9;
        // the drawer's bottom edge is the top of the dock, not the bottom of the window
        const isBottomDrawer = r.left < W * 0.2 && r.width > W * 0.6 && r.top > H * 0.3 && r.bottom <= bottom + 8;
        if (isRightColumn) right = Math.min(right, r.left);
        else if (isBottomDrawer) bottom = Math.min(bottom, r.top);
      }
    }
    // …and the topbar + notice above: frame her in the band BELOW them, or her crown sits
    // under the header on a short laptop screen (1280x720 clipped it). Ignored if taking
    // it would leave a sliver.
    let top = 0;
    const stageEl = document.getElementById('stage');
    if (stageEl) {
      const t = stageEl.getBoundingClientRect().top;
      if (t > 0 && bottom - t > H * 0.3) top = t;
    }
    const band = bottom - top;
    frameInRect(camera, right, band, right / 2, top + band / 2 + band * HEADROOM);
    camera.updateProjectionMatrix();
    renderer.setSize(W, H);
    invalidateBubbleMetrics();            // the stage box just moved
  }
  window.addEventListener('resize', applyStageOffset);
  window.__applyStageOffset = applyStageOffset;   // re-run when the drawer toggles
  applyStageOffset();
}
