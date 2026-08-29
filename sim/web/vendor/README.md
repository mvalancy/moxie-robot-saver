# sim/web/vendor — bundled third-party libraries

Vendored locally so the simulator runs with **no network / no CDN** (self-sufficiency
doctrine — the sim must work if every external link dies). Pinned versions:

- `three/three.module.js` + `three/addons/{controls/OrbitControls,utils/BufferGeometryUtils}.js`
  — **three.js r160** (`three@0.160.0`). MIT License, © three.js authors.
- `mqtt.min.js` — **MQTT.js v5.10.1**. MIT License, © the MQTT.js contributors.

To update: re-fetch the same paths from `https://unpkg.com/three@<ver>/` and
`https://unpkg.com/mqtt@<ver>/dist/mqtt.min.js`, then bump the versions here.
- `qrcode.js` — **qrcode-generator** v1.4.4 (Kazuhiko Arase). MIT License. Used by `qr.js` to render revival QR codes in-browser.
- `marked.min.js` — **marked** v12.0.0 (MIT). Markdown → HTML for the docs explorer (`docs.html`).
- `mermaid.min.js` — **mermaid** v10.9.1 (MIT). Renders ```mermaid diagrams in the docs explorer.
