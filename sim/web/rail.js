/* rail.js — the SIM rail drawer: on phone widths the HUD rail collapses to a handle so
 * the 3D stage stays visible; the handle toggles it. Pure presentation.
 * At >= 900 px it is a side column: OPEN on a local page (the developer's bench, exactly as
 * before) and, on a HOSTED page, CLOSED until the visitor opens it — a stranger meets a toy,
 * not servo sliders. That choice is remembered for their next visit. `body[data-env]` is
 * env.js's, which sim.html loads first; without it the page counts as local. */
(function () {
  "use strict";
  var hud = document.getElementById("hud");
  var t = document.getElementById("rail-toggle");
  if (!hud || !t) return;
  var mq = window.matchMedia("(max-width: 899px)");  // must match the CSS drawer breakpoint
  var hosted = !!document.body && document.body.getAttribute("data-env") === "hosted";
  var KEY = "moxie.railOpen";                          // "1" | "0": the column, hosted only
  function remembered() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  /** The column's resting state at >= 900 px. */
  function columnOpen() { return !hosted || remembered() === "1"; }
  function setClosed(closed) {
    hud.classList.toggle("rail-closed", closed);
    t.setAttribute("aria-expanded", String(!closed));
  }
  // start collapsed in drawer mode so Moxie stays visible; the column at its resting state
  setClosed(mq.matches || !columnOpen());
  // entering drawer mode collapses (robot visible); leaving restores the column's resting state
  mq.addEventListener("change", function (e) { setClosed(e.matches || !columnOpen()); });
  t.addEventListener("click", function () {
    var closed = !hud.classList.contains("rail-closed");
    setClosed(closed);
    // Only a choice made on the column is remembered: the drawer always starts shut.
    if (hosted && !mq.matches) { try { localStorage.setItem(KEY, closed ? "0" : "1"); } catch (e) {} }
    if (window.__applyStageOffset) requestAnimationFrame(function(){ requestAnimationFrame(window.__applyStageOffset); });
  });
})();
