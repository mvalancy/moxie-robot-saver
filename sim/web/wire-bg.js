/* wire-bg.js — mounts the slow rotating wireframe Moxie behind the marketing pages.
 *
 * ONE FILE FOR THREE PAGES. index.html, setup.html and cloud.html each carried an inline
 * `<script type="module">` that differed only in an opacity number (0.16 / 0.13 / 0.12).
 * Three near-identical inline blocks meant three SHA-256 hashes to keep in step with
 * `script-src`, and a hash that drifts from its block does not degrade — it BLANKS THE
 * PAGE. So the number moved into markup, where CSP has no opinion about it: the host
 * element carries `data-opacity`, and this file reads it.
 *
 * Failure here is deliberately silent. This is decoration behind the real content, and a
 * WebGL context is not guaranteed (headless runners, blocklisted drivers, reduced-motion
 * users on low-power devices). The page must be complete without it.
 */
import { mountMoxieWire } from "./moxie-wire.js";

var el = document.getElementById("wire-bg");
if (el) {
  var o = parseFloat(el.dataset.opacity);
  try { mountMoxieWire(el, { opacity: isFinite(o) ? o : 0.14 }); } catch (e) {}
}
