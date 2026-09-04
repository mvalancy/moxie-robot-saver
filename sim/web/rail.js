/* rail.js — the SIM rail drawer: on phone widths the HUD rail collapses to a handle so
 * the 3D stage stays visible. Pure presentation.
 *
 * Lived inline in `sim.html` until 2026-09-04; moved out for `script-src 'self'` (see
 * `sim/web/_headers`).
 */
/* Rail drawer — on phone-width screens the rail collapses to a handle so the
 * 3D stage stays visible; the handle toggles it. Pure presentation. */
(function () {
  "use strict";
  var hud = document.getElementById("hud");
  var t = document.getElementById("rail-toggle");
  if (!hud || !t) return;
  var mq = window.matchMedia("(max-width: 899px)");  // must match the CSS drawer breakpoint
  function setClosed(closed) {
    hud.classList.toggle("rail-closed", closed);
    t.setAttribute("aria-expanded", String(!closed));
  }
  if (mq.matches) setClosed(true);           // start collapsed in drawer mode so Moxie stays visible
  // entering drawer mode collapses (robot visible); leaving opens the side panel
  mq.addEventListener("change", function (e) { setClosed(e.matches); });
  t.addEventListener("click", function () {
    setClosed(!hud.classList.contains("rail-closed"));
    if (window.__applyStageOffset) requestAnimationFrame(function(){ requestAnimationFrame(window.__applyStageOffset); });
  });
})();
