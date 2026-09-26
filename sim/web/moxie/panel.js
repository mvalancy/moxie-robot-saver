// The by-hand control panel: motor sliders, expression chips, speech box, heart LED.
import { MOTOR_DEFS, MOTOR_MAX, MOTOR_REST } from './config.js';
import { liveness, noteCommand } from './liveness.js';
import { EXPRESSIONS } from './face.js';

const sliderEls = [];

/* A mini line-drawing of each expression, built from the SAME parameters the canvas face
 * uses (face.js::drawFace geometry, simplified), so a chip always looks like what it does.
 * `currentColor` lets the chip's CSS theme it. `blink` is neutral with one eye shut. */
function faceGlyph(name) {
  const P = EXPRESSIONS[name] || EXPRESSIONS.neutral;
  const r = (v) => Math.round(v);
  const eyeY = 258 + P.pupilY * 10, ry = Math.max(6, 50 * P.eyeH), rx = 40 * P.eyeW;
  let g = '';
  for (const s of [-1, 1]) {
    const ex = 256 + s * 86 + P.pupilX * 10;
    g += (name === 'blink' && s < 0) || ry < 10
      ? `<path d="M${r(ex - rx)} ${r(eyeY)}h${r(2 * rx)}" stroke-width="18"/>`
      : `<ellipse cx="${r(ex)}" cy="${r(eyeY)}" rx="${r(rx)}" ry="${r(ry)}" fill="currentColor" stroke="none"/>`;
    if (Math.min(1, P.browRaise + Math.abs(P.browTilt) + P.browAsym) > 0.05) {
      const by = eyeY - ry - 28 - P.browRaise * 18 - P.browAsym * (s < 0 ? 16 : -2);
      const tilt = P.browTilt * 14 * -s;
      g += `<path d="M${r(256 + s * 58)} ${r(by + 5 - tilt)}Q${r(256 + s * 86)} ${r(by - 8 + tilt * 0.4)} ` +
           `${r(256 + s * 116)} ${r(by + tilt)}" stroke-width="14"/>`;
    }
  }
  const mx = 256 + P.mouthX * 40, my = 378, mw = 66 * P.mouthWidth, c = P.mouthCurve;
  const endY = my - c * 16;
  g += P.mouthOpen < 0.1
    ? `<path d="M${r(mx - mw)} ${r(endY)}Q${r(mx)} ${r(my + c * 34)} ${r(mx + mw)} ${r(endY)}" stroke-width="18"/>`
    : `<path d="M${r(mx - mw)} ${r(endY)}Q${r(mx)} ${r(my + c * 26 - P.mouthOpen * 10)} ${r(mx + mw)} ${r(endY)}` +
      `Q${r(mx)} ${r(my + c * 26 + P.mouthOpen * 88 + 14)} ${r(mx - mw)} ${r(endY)}Z" fill="currentColor" stroke="none"/>`;
  return `<svg viewBox="126 150 260 260" aria-hidden="true" focusable="false" fill="none" ` +
         `stroke="currentColor" stroke-linecap="round">${g}</svg>`;
}

export function syncSlider(i, target) {
  const s = sliderEls[i];
  if (!s) return;
  s.input.value = String(Math.round(target));
  s.val.textContent = s.input.value;
}

export function markFaceButton(name) {
  document.querySelectorAll('#faces button').forEach(b =>
    b.classList.toggle('active', b.dataset.expr === name));
}

export function buildPanel(api, motorTargets) {
  const motorsEl = document.getElementById('motors');
  MOTOR_DEFS.forEach((d, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'motor';
    const rest = MOTOR_REST[i];
    wrap.innerHTML =
      `<label><span>${i} &middot; ${d.name}</span><span class="val">${rest}</span></label>` +
      `<input type="range" min="0" max="${MOTOR_MAX}" step="1" value="${rest}">`;
    const input = wrap.querySelector('input');
    const val = wrap.querySelector('.val');
    input.addEventListener('input', () => {
      val.textContent = input.value;
      motorTargets[i] = +input.value;
      noteCommand(i);
      liveness.userAt[i] = performance.now();   // user grabbed this joint — life.js backs off
    });
    motorsEl.appendChild(wrap);
    sliderEls[i] = { input, val };
  });

  const facesEl = document.getElementById('faces');
  api.expressions.forEach(name => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'face-chip';
    b.innerHTML = faceGlyph(name);
    b.title = name;
    b.setAttribute('aria-label', name);
    b.dataset.expr = name;
    b.addEventListener('click', () => api.setFace(name));
    facesEl.appendChild(b);
  });
  markFaceButton('neutral');

  document.getElementById('center-btn').addEventListener('click', () => api.centerAll());

  // When cloud-transport.js has adopted this box as the "ask Moxie" control, the visitor's
  // question must not be painted as MOXIE's speech bubble.
  const sayInput = document.getElementById('speech-input');
  const typedTurnOwnsBox = () => {
    try { return !!(window.moxieTypedTurn && window.moxieTypedTurn.adopted()); } catch { return false; }
  };
  const say = () => {
    if (typedTurnOwnsBox()) return;
    api.setSpeech(sayInput.value); sayInput.value = '';
  };
  document.getElementById('speech-btn').addEventListener('click', say);
  sayInput.addEventListener('keydown', e => { if (e.key === 'Enter') say(); });

  const ledOn = document.getElementById('led-on');
  const ledColor = document.getElementById('led-color');
  ledOn.addEventListener('change', () => api.setHeartLED(ledOn.checked, ledColor.value));
  ledColor.addEventListener('input', () => api.setHeartLED(ledOn.checked, ledColor.value));
}
