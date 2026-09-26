/* sw-reset.js — dev self-heal: if a stale service worker from another app on this port is
 * intercepting requests, unregister it and reload once so the current files load. */
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
