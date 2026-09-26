/* qr.js — generate Moxie revival QR codes IN THE BROWSER (a phone, no install).
 *
 * The setup app's codes are plain JSON, so they can be built client-side:
 *   endpoint_update : re-home the robot to YOUR server   {"debug":{"command","param"}}
 *   wifi            : push Wi-Fi credentials             {"wifi":{...}}
 *   debug           : factory/debug commands             {"debug":{...}}
 * ...plus a launch card (NOT JSON; read by the runtime QR reader, answered by our cloud):
 *   launch card     : start one on-board activity        GO<launch:MODULE>
 *
 * Grammar: docs/reverse-engineering/qr-commands.md (firmware v24.10.803).
 * Byte-for-byte the same strings as tools/robot-toolkit's encoders.
 */
(function () {
  "use strict";

  // IOTEndpoint enum (embodied.logging) — the values baked into the shipped firmware.
  var ENDPOINTS = {
    IOT_DEFAULT: 0, GOOGLE_DEVELOP: 1, GOOGLE_STAGING: 2, GOOGLE_PRODUCTION: 3,
    EMBODIED_DEVELOP: 4, EMBODIED_STAGING: 5, EMBODIED_PRODUCTION: 6,
    EMBODIED_HIPAA: 7, EMBODIED_LOCAL: 8, EMBODIED_CHINA: 9, EMBODIED_HK: 10,
    OPEN_MOXIE: 11,
  };

  var KNOWN_DEBUG = {
    serial_number_display: "Show the serial-number screen",
    restore_factory: "Enter factory-restore flow",
    reset_network: "Forget all Wi-Fi and reconnect",
    bluetooth_pair: "Bluetooth-pair the device in param",
    endpoint_update: "Re-home the robot to the endpoint in param",
  };

  // Python's json.dumps puts a space after ':' and ',' — match it exactly so the
  // browser and the CLI toolkit emit byte-identical payloads.
  function j(obj) {
    return JSON.stringify(obj).replace(/":/g, '": ').replace(/,"/g, ', "');
  }

  function encodeDebug(command, param) {
    return j({ debug: { command: command, param: param || "" } });
  }
  function encodeEndpoint(name) {
    if (!(name in ENDPOINTS)) throw new Error("unknown endpoint: " + name);
    return encodeDebug("endpoint_update", name);
  }
  function encodeWifi(ssid, password, opts) {
    opts = opts || {};
    return j({ wifi: {
      ssid: ssid, password: password || "",
      is_hidden: !!opts.hidden, band_select: opts.band || "ANY" } });
  }

  /* ---- launch cards: `GO<launch:MODULE[:CONTENT]>` ---------------------------
   * A DIFFERENT reader: not the setup scanner (closed grammar, launches nothing) but the
   * runtime QR reader, surfaced to the cloud as `eb-qr-event` — so this is the action-tag
   * grammar with a literal `GO` prefix, not JSON (backlog/qr-launch-cards.md).
   * This is only the PRINTING side; `mqtt/moxie_sdk/launch_cards.py::decode` is the
   * authority, and a stale list here fails safe. sim/test_qr.mjs compares this array with
   * `launch_cards._catalog()` id for id.
   */
  var CARD_PREFIX = "GO";                 // literal, case-sensitive, never normalised
  var CARD_TAG = "launch";                // the one tag a card may carry
  var LAUNCHABLE_MODULE_IDS = [
    "AB", "AFFIRM", "ANIMALEXERCISE", "AUDMED", "BODYSCAN", "BREATHINGSHAPES",
    "COMPOSING", "DANCE", "DM", "DRAW", "FACES", "FF", "GUIDEDVIS", "JOKE",
    "JUKEBOX", "MENTORSAYS", "NONSENSE", "PASSWORDGAME", "RDL", "READ",
    "SCAVENGERHUNT", "STORY", "STORYTELLING", "WHIMSY",
  ];

  /* The ungated formatter — the browser's `--face-value` (as in `sim/virtual_moxie.py`), so a
   * hostile card string can be shown travelling the boundary. UIs call `encodeCard`.
   */
  function cardPayload(tag, moduleId, contentId) {
    var body = String(tag);
    if (moduleId) body += ":" + moduleId;
    if (moduleId && contentId) body += ":" + contentId;
    return CARD_PREFIX + "<" + body + ">";
  }

  /* One catalog id -> the payload a printed card carries. The exact inverse of
   * `launch_cards.decode`, and byte-identical to `launch_cards.encode` (asserted for all
   * 24 ids by `sim/test_qr.mjs`). Throws on an id outside the catalog, exactly as the
   * Python `encode` raises, so neither generator can print paper the reader refuses. */
  function encodeCard(moduleId, contentId) {
    if (!isLaunchable(moduleId))
      throw new Error("not a launchable module id: " + moduleId);
    return cardPayload(CARD_TAG, moduleId, contentId);
  }

  function isLaunchable(moduleId) {
    return typeof moduleId === "string" &&
           LAUNCHABLE_MODULE_IDS.indexOf(moduleId) >= 0;
  }

  // Render a payload string into a canvas element.
  function render(canvas, text, scale) {
    if (typeof qrcode === "undefined") throw new Error("qrcode.js not loaded");
    var q = qrcode(0, "M");             // auto version, medium ECC
    q.addData(text);
    q.make();
    var n = q.getModuleCount(), px = scale || 6, quiet = 4;
    var size = (n + quiet * 2) * px;
    canvas.width = canvas.height = size;
    var g = canvas.getContext("2d");
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, size, size);
    g.fillStyle = "#000000";
    for (var r = 0; r < n; r++)
      for (var c = 0; c < n; c++)
        if (q.isDark(r, c))
          g.fillRect((c + quiet) * px, (r + quiet) * px, px, px);
    return size;
  }

  window.moxieQR = {
    ENDPOINTS: ENDPOINTS,
    KNOWN_DEBUG: KNOWN_DEBUG,
    encodeDebug: encodeDebug,
    encodeEndpoint: encodeEndpoint,
    encodeWifi: encodeWifi,
    CARD_PREFIX: CARD_PREFIX,
    CARD_TAG: CARD_TAG,
    LAUNCHABLE_MODULE_IDS: LAUNCHABLE_MODULE_IDS,
    isLaunchable: isLaunchable,
    cardPayload: cardPayload,
    encodeCard: encodeCard,
    render: render,
  };
})();
