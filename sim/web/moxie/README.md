# `sim/web/moxie/` — the 3D Moxie, in modules

ES modules imported by [`../moxie.js`](../moxie.js) (the entry point `sim.html` loads; it
keeps only the public `window.moxie` API and the per-frame loop). Bare
`three` resolves through `sim.html`'s importmap; nothing here is loaded directly by a page.

- **`scene.js`** — renderer, camera + orbit controls, lights, floor, the scene-light dimmer.
- **`rig.js`** — materials and the jointed robot (body split at the lean joint, head, face
  screen, arms, heart LED, decals) plus the debug axis overlay.
- **`config.js`** — the motor table (`MOTOR_DEFS`), rest pose, `motorAngle`, spring-elbow curve.
- **`geometry.js`** — pure geometry builders: body lathe profile, arm shells, egg head, face panel.
- **`textures.js`** — canvas textures and the shared `roundedRectPath` / `heartPath` helpers.
- **`face.js`** — expressions, the per-frame canvas face, icon badges, blink and easing.
- **`liveness.js`** — additive idle micro-motion and gaze drift (never written back to motor state).
- **`bubble.js`** — the speech bubble: typewriter, above/beside/chest anchoring, `window.__bubbleAnchor`.
- **`stage.js`** — keeps her framed in the viewport the chat dock and rail leave free.
- **`panel.js`** — the by-hand control panel.

Every file here is covered by the `/moxie/*` `no-cache` rule in [`../_headers`](../_headers)
(`sim/test_csp.mjs` fails if a script subdirectory lacks one).

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
