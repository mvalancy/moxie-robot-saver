/* setup.js — the standalone parent-app "basics" page: turns the two forms into Moxie QR
 * codes. The encoders themselves live in `qr.js` and are NOT reimplemented here, so this
 * page can never drift from the byte-parity guarantee `sim/test_qr.mjs` holds over them.
 *
 * Lived inline in `setup.html` until 2026-09-04; moved out for `script-src 'self'` (see
 * `sim/web/_headers`).
 */
(function(){
  "use strict";
  var Q = window.moxieQR;
  function show(kind, payload){
    var cv = document.getElementById("cv-"+kind);
    Q.render(cv, payload, 5);
    document.getElementById("pl-"+kind).textContent = payload;
    document.getElementById("out-"+kind).style.display = "block";
  }
  document.getElementById("go-wifi").addEventListener("click", function(){
    var ssid = document.getElementById("ssid").value.trim();
    var pl = document.getElementById("pl-wifi");
    if(!ssid){ document.getElementById("out-wifi").style.display="block";
      pl.textContent="Enter your network name (SSID) first."; return; }
    show("wifi", Q.encodeWifi(ssid, document.getElementById("pass").value, {
      hidden: document.getElementById("hidden").checked,
      band: document.getElementById("band").value }));
  });
  document.getElementById("go-ep").addEventListener("click", function(){
    show("ep", Q.encodeEndpoint(document.getElementById("endpoint").value));
  });
})();
