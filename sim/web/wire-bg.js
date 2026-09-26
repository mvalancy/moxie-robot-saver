/* wire-bg.js — mounts the slow rotating wireframe Moxie behind the marketing pages.
 * One file for index/setup/cloud; each page's opacity is `data-opacity` on the host element
 * (markup, which CSP does not police). Failure is silent: this is decoration and WebGL is
 * not guaranteed.
 */
import { mountMoxieWire } from "./moxie-wire.js";

var el = document.getElementById("wire-bg");
if (el) {
  var o = parseFloat(el.dataset.opacity);
  try { mountMoxieWire(el, { opacity: isFinite(o) ? o : 0.14 }); } catch (e) {}
}
