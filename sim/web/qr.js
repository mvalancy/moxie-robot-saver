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
    render: render,
  };
})();
