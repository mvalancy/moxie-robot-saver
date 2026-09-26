// The speech bubble: a real DOM node (readable, selectable, in the a11y tree) projected
// onto her head every frame — not a THREE.Sprite (pixels would drop it from the a11y
// tree) and not CSS3D (second render pass, and it would rotate the text with the camera).
//
// Placement: ABOVE her head when the whole box fits under the top of the stage, otherwise
// hung at her CHEST on a leader line back to her head. MIN_HEAD_GAP is applied after every
// clamp, so no camera, zoom or viewport can put the box over her face. Behind the camera
// `project()` returns mirrored coordinates, so the bubble fades ('off-stage') instead.
import * as THREE from 'three';
import { speech } from './face.js';

const HEAD_TOP_RISE = 0.34;      // above head centre — clears the crown
const HEAD_ANCHOR_DROP = 0.16;   // just under the chin: where the leader points
const CHEST_DROP = 0.62;         // below head centre — the top of the chest bubble
const MIN_HEAD_GAP = 26;         // px: the bubble's top never comes closer to her head
const BUBBLE_METRICS_MS = 250;   // layout reads are cached (no forced layout per frame)
const M = 8;                     // stage margin, px

const bubbleEl = document.getElementById('bubble');
const bubbleText = document.getElementById('bubble-text');
const reduceMotion = window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let bubbleTimer = null, typeTimer = null;
let stage = null;
const headP = new THREE.Vector3(), chestP = new THREE.Vector3(), topP = new THREE.Vector3();
const metrics = { at: -1e9, sx: 0, sy: 0, sw: 0, sh: 0, bw: 0, bh: 0 };
// The frame stash: every number one placement used, from ONE instant, in viewport px.
// Re-projecting the head at read time would compare a box placed at frame N with a head
// at frame N+1 (measured: 15.6 px of drift during a lean). Not written on frames that
// place nothing, so `__bubbleAnchor` reports it as frozen rather than fresh.
let frame = null, frameSeq = 0;

export function invalidateBubbleMetrics() { metrics.at = -1e9; }
window.__invalidateBubbleMetrics = invalidateBubbleMetrics;

// Typewriter reveal (~32 ms/char, capped), then hold and fade. Reduced motion: all at once.
export function showSpeech(text) {
  const typeDur = reduceMotion ? 0 : Math.min(1500, text.length * 32);
  const dur = typeDur + Math.max(1600, 700 + 55 * text.length);
  if (bubbleTimer) clearTimeout(bubbleTimer);
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null; }
  bubbleEl.classList.remove('hidden');
  invalidateBubbleMetrics();              // new text, new box size
  speech.until = performance.now() + dur;

  if (typeDur === 0) {
    bubbleText.textContent = text;
    bubbleEl.classList.remove('typing');
  } else {
    bubbleText.textContent = '';
    bubbleEl.classList.add('typing');
    const step = Math.max(14, Math.floor(typeDur / text.length));
    let i = 0;
    typeTimer = setInterval(() => {
      i = Math.min(text.length, i + 1);
      bubbleText.textContent = text.slice(0, i);
      if (i >= text.length) { clearInterval(typeTimer); typeTimer = null; bubbleEl.classList.remove('typing'); }
    }, step);
  }

  bubbleTimer = setTimeout(() => {
    bubbleEl.classList.add('hidden');
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null; }
    bubbleEl.classList.remove('typing');
    bubbleText.textContent = text;
  }, dur);
}

export function updateBubbleAnchor(head, camera) {
  if (stage === null) stage = document.getElementById('stage') || false;
  if (!bubbleEl || !stage || bubbleEl.classList.contains('hidden')) return;

  head.getWorldPosition(headP);
  chestP.copy(headP);
  topP.copy(headP);
  headP.y -= HEAD_ANCHOR_DROP;
  chestP.y -= CHEST_DROP;
  topP.y += HEAD_TOP_RISE;

  const hv = headP.clone().project(camera);
  const cv = chestP.clone().project(camera);
  const tv = topP.clone().project(camera);
  if (hv.z > 1 || cv.z > 1 || tv.z > 1) { bubbleEl.classList.add('off-stage'); return; }
  bubbleEl.classList.remove('off-stage');

  const W = window.innerWidth, H = window.innerHeight;
  const toX = (v) => (v.x * 0.5 + 0.5) * W;
  const toY = (v) => (1 - (v.y * 0.5 + 0.5)) * H;

  const now = performance.now();
  if (now - metrics.at > BUBBLE_METRICS_MS) {
    const r = stage.getBoundingClientRect();
    Object.assign(metrics, { at: now, sx: r.left, sy: r.top, sw: r.width, sh: r.height,
      bw: bubbleEl.offsetWidth, bh: bubbleEl.offsetHeight });
  }
  const st = metrics, bw = metrics.bw, bh = metrics.bh;
  if (!st.sw || !bw) return;                     // nothing measured yet: wait a frame

  const headVX = toX(hv), headVY = toY(hv);
  const chestVX = toX(cv), chestVY = toY(cv);
  const crownVX = toX(tv), crownVY = toY(tv);
  const headX = headVX - st.sx, headY = headVY - st.sy;
  const chestX = chestVX - st.sx, chestY = chestVY - st.sy;
  const crownY = crownVY - st.sy;

  const above = (crownY - bh - M) >= M;
  bubbleEl.classList.toggle('leadered', !above);

  const anchorX = above ? headX : chestX;
  const x = Math.min(Math.max(anchorX, bw / 2 + M), Math.max(bw / 2 + M, st.sw - bw / 2 - M));

  let y, leader;
  if (above) {
    y = Math.min(Math.max(crownY - bh, M), Math.max(M, st.sh - bh - M));
    leader = 0;
    bubbleEl.style.setProperty('--leader', '0px');
  } else {
    // keep the box on screen first, then apply the face rule LAST so it always wins
    y = Math.min(Math.max(chestY, M), Math.max(M, st.sh - bh - M));
    y = Math.max(y, headY + MIN_HEAD_GAP);
    leader = Math.max(0, y - headY);
    bubbleEl.style.setProperty('--leader', leader.toFixed(1) + 'px');
  }

  bubbleEl.style.setProperty('--bx', x.toFixed(1) + 'px');
  bubbleEl.style.setProperty('--by', y.toFixed(1) + 'px');
  bubbleEl.classList.add('anchored');

  // `leader` unrounded on purpose: `--leader` is quantised to 0.1 px
  frame = {
    at: now, seq: ++frameSeq, above,
    head: { x: headVX, y: headVY },
    crown: { x: crownVX, y: crownVY },
    chest: { x: chestVX, y: chestVY },
    leader,
    box: { left: st.sx + x - bw / 2, top: st.sy + y, width: bw, height: bh },
  };
}

/** Recorded anchor state for the layout tests (not a screenshot). `head` is the head THIS
 *  placement projected; `bubble` is the real DOM rect. `frozen`/`ageMs` report staleness
 *  while hidden/off-stage; `seq` lets a caller wait for a re-placement instead of sleeping. */
window.__bubbleAnchor = function () {
  const el = document.getElementById('bubble');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const f = frame;
  const hidden = el.classList.contains('hidden');
  const offStage = el.classList.contains('off-stage');
  const head = f ? f.head : { x: NaN, y: NaN };
  return {
    anchored: el.classList.contains('anchored'),
    leader: Number(String(el.style.getPropertyValue('--leader') || '0').replace('px', '')),
    offStage, hidden,
    stamped: !!f,
    frozen: !f || hidden || offStage,
    ageMs: f ? Math.round(performance.now() - f.at) : null,
    seq: f ? f.seq : 0,
    head: { x: Math.round(head.x), y: Math.round(head.y) },
    bubble: { cx: Math.round(r.left + r.width / 2), bottom: Math.round(r.bottom),
              left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top) },
    // unrounded, for assertions whose honest residual is a tenth of a pixel
    exact: f ? {
      leader: f.leader, above: f.above,
      head: { x: f.head.x, y: f.head.y },
      crown: { x: f.crown.x, y: f.crown.y },
      chest: { x: f.chest.x, y: f.chest.y },
      box: f.box,
      bubble: { cx: r.left + r.width / 2, top: r.top, bottom: r.bottom,
                left: r.left, right: r.right },
    } : null,
  };
};
