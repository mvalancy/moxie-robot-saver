// The by-hand control panel: motor sliders, expression chips, speech box, heart LED.
import { MOTOR_DEFS, MOTOR_MAX, MOTOR_REST } from './config.js';
import { liveness, noteCommand } from './liveness.js';

const sliderEls = [];

// Glyph per expression: the 11 Bht_Eyeseme_* moods + sleep + thinking + blink.
const EXPR_EMOJI = {
  neutral: '😐', happy: '😄', sad: '😢', angry: '😠', shy: '☺️', surprised: '😮',
  afraid: '😨', concerned: '😟', confused: '😕', curious: '🧐', embarrassed: '😳',
  sleep: '😴', thinking: '🤔', blink: '😉',
};

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
    b.className = 'face-emoji';
    b.textContent = EXPR_EMOJI[name] || name;
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
