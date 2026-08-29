# sim/web/vendor — bundled third-party libraries

Vendored locally so the simulator runs with **no network / no CDN** (self-sufficiency
doctrine — the sim must work if every external link dies). Pinned versions:

- `three/three.module.js` + `three/addons/{controls/OrbitControls,utils/BufferGeometryUtils}.js`
  — **three.js r160** (`three@0.160.0`). MIT License, © three.js authors.
- `mqtt.min.js` — **MQTT.js v5.10.1**. MIT License, © the MQTT.js contributors.

To update: re-fetch the same paths from `https://unpkg.com/three@<ver>/` and
`https://unpkg.com/mqtt@<ver>/dist/mqtt.min.js`, then bump the versions here.
