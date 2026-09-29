# 📦 `sim/web/vendor/` — pinned third-party libraries

Vendored so the site runs with no network or CDN. All are served under the `/vendor/*` rule in [`../_headers`](../_headers).

| file | library | license | used by |
|---|---|---|---|
| [`three/`](three/README.md) | three.js r160 (`three@0.160.0`) + two addons | MIT | the 3D model, wireframe backgrounds |
| `mqtt.min.js` | MQTT.js v5.10.1 | MIT | `bridge/` |
| `qrcode.js` | qrcode-generator v1.4.4 (Kazuhiko Arase) | MIT | `qr.js` |
| `marked.min.js` | marked v12.0.0 | MIT | docs explorer (Markdown to HTML) |
| `mermaid.min.js` | mermaid v10.9.1 | MIT | docs explorer + `diagram.js` |
| `highlight.min.js` | highlight.js v11.9.0 common build + protobuf | BSD-3-Clause | docs explorer code blocks |
| [`fonts/`](fonts/README.md) | Inter, JetBrains Mono | SIL OFL 1.1 | every page |

To update: re-fetch the same paths from `https://unpkg.com/<pkg>@<ver>/` (MQTT.js: `dist/mqtt.min.js`)
and bump the version here.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
