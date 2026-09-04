/* sw-reset.js — dev self-heal for a stale service worker left by another app on this port.
 *
 * Lived inline in `sim.html` until 2026-09-04. It is a file now for one reason: an inline
 * <script> can only run under `script-src 'unsafe-inline'` or a SHA-256 hash that silently
 * BLANKS THE PAGE when it drifts. A file needs neither — `'self'` covers it.
 */
/* Dev self-heal: if a stale service worker from another app on this port is
   intercepting requests, unregister it and reload once so the current files load. */
(function () {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.getRegistrations().then(function (rs) {
    if (!rs.length) return;
    Promise.all(rs.map(function (r) { return r.unregister(); })).then(function () {
      if (window.caches && caches.keys) caches.keys().then(function (ks) {
        return Promise.all(ks.map(function (k) { return caches.delete(k); }));
      }).then(function () { location.reload(); });
      else location.reload();
    });
  }).catch(function () {});
})();
