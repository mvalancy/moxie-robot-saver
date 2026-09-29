/* bridge/core.js — the live MQTT bus → window.moxie, part 1 of 7 (load order: sim.html).
 *
 * The bridge connects the 3D Moxie to the topics a real robot sees, over WebSocket, and
 * acts like a robot in BOTH directions: it renders `commands/remote_chat` replies from
 * their behavior markup (docs/reverse-engineering/behavior-markup.md), hands
 * `commands/tts` (the SERVER VOICE) to voice/, applies `response_actions`, and publishes
 * the same envelopes as `sim/virtual_moxie.py`, so the two SIM clients are
 * interchangeable (docs/architecture/sim-as-a-client.md).
 *
 * CLASSIC SCRIPTS, NOT ES MODULES: mode.js and cloud-transport.js wrap
 * `window.moxieBridge` synchronously as they load, and the node suites eval the parts as
 * one source. The parts share state through `window.__moxieBridge` (`B`), which this
 * file creates fresh on every load. Uses the global `mqtt` from vendor/mqtt.min.js, which index.js loads on the first Link.
 */
(function () {
  "use strict";
  const B = window.__moxieBridge = {};

  // ---- this robot's identity on the bus ----
  // `FIRMWARE` is the analyzed build `sim/virtual_moxie.py` reports in `/state`.
  const DEVICE_ID = "d_sim";
  const FIRMWARE = "24.10.803";
  const MODULE_NAME = "sim-web";     // which client this is (the SIL says "virtual-moxie")
  B.DEVICE_ID = DEVICE_ID; B.FIRMWARE = FIRMWARE; B.MODULE_NAME = MODULE_NAME;
  B.dev = (name) => `/devices/${DEVICE_ID}/${name}`;

  B.api = {};                        // parts add to it; index.js publishes it as window.moxieBridge
  B.client = null;                   // the mqtt.js client, once connected
  B.isLive = () => !!(B.client && B.client.connected);
  B.nowMs = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
  B.status = (t) => { const el = document.getElementById("bus-status"); if (el) el.textContent = t; };
  B.parse = (s) => { try { return JSON.parse(s); } catch { return null; } };

  // Record/replay state: `route` and `publishActivity` both append while recording.
  B.rec = { recorded: [], recording: false, replaying: false };
  B.record = (topic, payload) => {
    if (B.rec.recording && !B.rec.replaying) B.rec.recorded.push({ t: B.nowMs(), topic, payload });
  };
})();
