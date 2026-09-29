# `sim/web/bridge/` — the live MQTT bus → the 3D Moxie

Classic scripts (not ES modules), loaded by [`../sim.html`](../sim.html) **in this order**:
`mode.js` and `cloud-transport.js` wrap `window.moxieBridge` synchronously as they load, and
the node suites evaluate the parts as one source (`sim/bridge_harness.mjs::scriptGroup`,
`sim/tests/helpers_web.py::script_group` — both read the order from sim.html). The parts
share state through `window.__moxieBridge`, which `core.js` creates fresh on every load.

- **`core.js`** — identity on the bus (`d_sim`, firmware, module name), `status()`, the
  record/replay state, and the namespace itself.
- **`body.js`** — behavior markup → face, gestures, `Bht_*` trees, icon badges.
- **`actions.js`** — `response_actions` (launch/exit/sleep/enable_qr/execute +
  `event_subscription`), mirrored by `sim/virtual_moxie.py`; `actionStats()`.
- **`presence.js`** — the robot's eyes: vision events, the presence badge (hidden until
  one arrives), `faceEvent()` / `presenceStats()`.
- **`activity.js`** — robot → cloud activity log (query / mentor_behavior / telehealth
  state), pinned by `sim/tests/goldens/robot_to_cloud_activity.json`.
- **`alive.js`** — `window.moxieAlive`: listening / thinking / settled cues and fillers.
- **`index.js`** — voice arbitration, `remote_chat` / `tts` / telehealth / child-turn /
  motor handlers, `route()`, `connect()`, record/replay, and `window.moxieBridge`.

Covered by the `/bridge/*` `no-cache` rule in [`../_headers`](../_headers). Tests:
`sim/test_bridge.mjs` (incl. presence), `test_action_payload.mjs`,
`test_*_render.mjs`, `sim/tests/test_sim_client_parity.py`.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
