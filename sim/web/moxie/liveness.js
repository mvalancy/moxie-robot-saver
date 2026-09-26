// Liveness: the subtle continuous layer under everything — micro-sway, breathing (applied
// by the caller) and gaze drift. Deliberate idle behaviours live in life.js, which drives
// the REAL motor targets so the sliders move.
//
// Additive-only: offsets are applied on top of the commanded angles at render time and
// never written back, so getMotor() always reports the commanded state. Each DOF's weight
// eases to zero while that DOF is being commanded (recent setMotor/slider input, or still
// travelling), then eases back in.
import { idleEyes } from './face.js';

export const liveness = {
  enabled: true,
  master: 1,                              // eases toward enabled ? 1 : 0
  w: new Float32Array(7).fill(1),         // per-DOF blend weight
  cmdAt: new Float32Array(7).fill(-1e9),  // ms of the last command per DOF
  out: new Float32Array(7),               // final per-frame additive angles
  eyeTX: 0, eyeTY: 0, eyeNext: 1.5,       // random gaze drift
  // ms of the last USER slider drag per DOF — life.js reads it (moxie.isUserHeld) so it
  // never fights a joint you grabbed.
  userAt: new Float32Array(7).fill(-1e9),
};

export function noteCommand(i) {
  liveness.cmdAt[i] = performance.now();
}

export function updateLiveness(t, dt, now, motorTargets, motorValues) {
  const L = liveness;
  L.master += ((L.enabled ? 1 : 0) - L.master) * (1 - Math.exp(-dt * 3));

  for (let i = 0; i < 7; i++) {
    const active = (now - L.cmdAt[i] < 1200) || Math.abs(motorTargets[i] - motorValues[i]) > 80;
    const rate = active ? 8 : 1.5;          // duck fast, resume slowly
    L.w[i] += ((active ? 0 : 1) - L.w[i]) * (1 - Math.exp(-dt * rate));
  }

  const micro = [
    -0.008 * Math.sin(t * 1.05 + 0.4),          // L shoulder
    0.006 * Math.sin(t * 0.85 + 2.2),           // L in/out
    0.008 * Math.sin(t * 1.05 + 3.5),           // R shoulder
    -0.006 * Math.sin(t * 0.85 + 5.0),          // R in/out
    0.012 * Math.sin(t * 0.47 + 1.3),           // head tilt
    0.018 * Math.sin(t * 0.33) + 0.006 * Math.sin(t * 0.9 + 2.0),  // yaw
    0.006 * Math.sin(t * 0.80 + 0.5),           // lean
  ];
  for (let i = 0; i < 7; i++) L.out[i] = L.master * L.w[i] * micro[i];

  if (t > L.eyeNext) {
    if (Math.random() < 0.4) { L.eyeTX = 0; L.eyeTY = 0; }
    else {
      L.eyeTX = (Math.random() * 2 - 1) * 0.45;
      L.eyeTY = (Math.random() * 2 - 1) * 0.25;
    }
    L.eyeNext = t + 1.2 + Math.random() * 2.6;
  }
  const ke = 1 - Math.exp(-dt * 4);
  idleEyes.x += (L.eyeTX * L.master - idleEyes.x) * ke;
  idleEyes.y += (L.eyeTY * L.master - idleEyes.y) * ke;

  return L.out;
}
