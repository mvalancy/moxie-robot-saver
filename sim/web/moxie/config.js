// Rig constants and the motor table shared by every part of the model.

export const MOTOR_MAX = 32767;          // real hardware range (MOTOR_MAX_POS)
export const MOTOR_CENTER = 16384;       // rest pose

export const COL = {
  shell:    0x3bb6b0,   // matte teal body + head
  arm:      0x45bfb9,   // arm shells, a whisper lighter than the body
  hand:     0x9fdce8,   // light blue-grey rounded hands
  rubber:   0x15181a,   // base ring
  base:     0x2a2f33,   // base disc
};

export const BODY_TOP = 1.50;            // top of the body (scene units; ~15 in overall)
export const HEAD_PIVOT_Y = 1.52;        // head underside sits just above BODY_TOP, so it
                                         // clears the chest when it tilts
export const SHOULDER_Y = 1.11;          // CENTRE of the rounded boss at the top of the arm
                                         // plate — the plate hinges about that circle
export const LEAN_PIVOT_Y = 0.66;        // the chest seam, just above the speaker

// Motor table: index -> joint. neg/pos are radian magnitudes below/above center; `sign`
// maps "value above center" onto the node's rotation axis.
//
// The arm is a flat plate against the body's side. Rotation about X swings it up/down in
// its own plane (identical on both sides); rotation about Z lifts it away from the body
// (mirrored per side). The in/out axes are OUT-ONLY (`fromZero`): the arm cannot swing
// into the body, so 0..32767 maps to 0..pos and the rest pose is 0. The elbow has no
// motor at all — it is spring-driven off the shoulder (springElbowFromMotor).
export const MOTOR_DEFS = [
  { name: 'L shoulder (up/down)', axis: 'x', sign: -1, neg: 0.30, pos: 1.90 }, // 0  (+X arm)
  { name: 'L shoulder (in/out)',  axis: 'z', sign: +1, pos: 1.05, fromZero: true }, // 1
  { name: 'R shoulder (up/down)', axis: 'x', sign: -1, neg: 0.30, pos: 1.90 }, // 2  (-X arm)
  { name: 'R shoulder (in/out)',  axis: 'z', sign: -1, pos: 1.05, fromZero: true }, // 3
  { name: 'Head tilt (nod)',      axis: 'x', sign: -1, neg: 0.38, pos: 0.38 }, // 4
  { name: 'Body turn (yaw)',      axis: 'y', sign: +1, neg: 1.05, pos: 1.05 }, // 5
  { name: 'Body lean (F/B)',      axis: 'x', sign: +1, neg: 0.28, pos: 0.28 }, // 6
];

export const MOTOR_REST = MOTOR_DEFS.map(d => (d.fromZero ? 0 : MOTOR_CENTER));

export function motorAngle(i, value) {
  const d = MOTOR_DEFS[i];
  if (d.fromZero) return d.sign * (value / MOTOR_MAX) * d.pos;
  const u = (value - MOTOR_CENTER) / MOTOR_CENTER;   // -1 .. +1
  return d.sign * (u < 0 ? u * d.neg : u * d.pos);
}

// Spring elbow (hardware-map.md "Arm anatomy"): the spring pulls the forearm closed and
// the body holds it open while the arm rests against the side. So the fold is a function
// of how far the shoulder's OUT/IN axis has swung the arm clear: 0 at rest, max bend at
// ELBOW_MAX_AT, smoothstepped so there is no step at either end.
const ELBOW_MAX_BEND = 0.85;        // mechanical stop (~49deg)
const ELBOW_MAX_AT = 13064;         // shoulder value where the fold is fully closed
export function springElbowFromMotor(v) {
  const t = Math.min(1, Math.max(0, v / ELBOW_MAX_AT));
  return ELBOW_MAX_BEND * t * t * (3 - 2 * t);
}
