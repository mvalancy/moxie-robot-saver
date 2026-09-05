/* qr.js — generate Moxie revival QR codes IN THE BROWSER.
 *
 * The QR types that matter for reviving a robot are plain JSON (no protobuf), so
 * they can be built client-side — meaning a static page (phone, no install) can
 * produce the exact codes the robot's setup app parses:
 *
 *   endpoint_update : re-home the robot to YOUR server   {"debug":{"command","param"}}
 *   wifi            : push Wi-Fi credentials             {"wifi":{...}}
 *   debug           : factory/debug commands             {"debug":{...}}
 *
 * ...and one that is NOT a setup code and NOT JSON — a launch card, read by the
 * robot's runtime QR reader and answered by our cloud, not by its setup app:
 *
 *   launch card     : start one on-board activity            GO<launch:MODULE>
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
   *
   * A DIFFERENT QR reader from everything above. The three encoders above feed the
   * robot's *setup* scanner (`bo-wifi`), whose grammar is provably closed and cannot
   * launch anything. A launch card feeds the *runtime* reader, which surfaces to the
   * cloud as the `eb-qr-event` vision event — so the payload here is not JSON at all,
   * it is the action-tag grammar with a literal `GO` marker in front of it.
   * Brief: docs/architecture/backlog/qr-launch-cards.md.
   *
   * WHAT THIS SIDE IS AND IS NOT. This is the *printing* side. The authority on what a
   * card may do is `mqtt/moxie_sdk/launch_cards.py::decode`, which runs on the server
   * against bytes a stranger chose; the list below is a print-side convenience so the
   * browser cannot make paper the server will refuse. Nothing here is a security
   * boundary, and a stale list here fails safe in both directions (a card that will not
   * scan, or a card we decline to print) — never in the permissive one.
   *
   * The list is TRANSCRIBED, and the Python one is DERIVED from `schedule.py`. That is a
   * real asymmetry and it is held closed by a test, not by discipline:
   * `sim/test_qr.mjs` compares this array against `launch_cards._catalog()` id for id, so
   * a change to `schedule.ONBOARD_MODULES` reddens CI here rather than rotting quietly.
   */
  var CARD_PREFIX = "GO";                 // literal, case-sensitive, never normalised
  var CARD_TAG = "launch";                // the one tag a card may carry
  var LAUNCHABLE_MODULE_IDS = [
    "AB", "AFFIRM", "ANIMALEXERCISE", "AUDMED", "BODYSCAN", "BREATHINGSHAPES",
    "COMPOSING", "DANCE", "DM", "DRAW", "FACES", "FF", "GUIDEDVIS", "JOKE",
    "JUKEBOX", "MENTORSAYS", "NONSENSE", "PASSWORDGAME", "RDL", "READ",
    "SCAVENGERHUNT", "STORY", "STORYTELLING", "WHIMSY",
  ];

  /* The ungated formatter — the browser's `--face-value`.
   *
   * The SIM is a robot as well as a phone: `sim/virtual_moxie.py` grew `--face-value` so a
   * SIL robot could publish the exact hostile strings a stranger's card might carry, and
   * this is the same lever in the browser. It is how a refusal can be shown to travel the
   * boundary rather than merely to hold on the server side of it. `encodeCard` below is
   * the only thing a UI should call.
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
