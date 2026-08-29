/* bg.js — shared animated TECHNICAL background for the Moxie static site.
 *
 * A dark telemetry / network-graph field (valpatel.com aesthetic): drifting
 * nodes wired into a live constellation, data packets travelling the edges, a
 * faint drifting grid, and occasional radar pings from hub nodes. Self-contained
 * (injects its own fixed <canvas> behind everything), theme-tokened, and quiet
 * enough that content stays readable. Respects prefers-reduced-motion.
 *
 * Usage:  <script src="bg.js" data-density="0.9" data-accent="cyan"></script>
 * The canvas sits at z-index 0; give page content `position:relative;z-index:1`.
 */
(function () {
  "use strict";
  var s = document.currentScript || {};
  var DENSITY = parseFloat((s.dataset && s.dataset.density) || "1");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches;

  var C = { cyan: "#00f0ff", mint: "#05ffa1", purple: "#a855f7", teal: "#12b5b0", dim: "#3a4557" };

  var cv = document.createElement("canvas");
  cv.id = "bg-canvas";
  cv.setAttribute("aria-hidden", "true");
  cv.style.cssText = "position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;display:block";
  (document.body || document.documentElement).insertBefore(cv, document.body ? document.body.firstChild : null);
  var g = cv.getContext("2d");

  var W = 0, H = 0, DPR = Math.min(2, window.devicePixelRatio || 1);
  var nodes = [], hubs = [], packets = [], pings = [], LINK = 150, t0 = performance.now();

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function build() {
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = Math.floor(W * DPR); cv.height = Math.floor(H * DPR);
    g.setTransform(DPR, 0, 0, DPR, 0, 0);
    var area = W * H, n = Math.max(24, Math.min(90, Math.round(area / 20000 * DENSITY)));
    nodes = []; hubs = [];
    for (var i = 0; i < n; i++) {
      var isHub = Math.random() < 0.12;
      var nd = { x: rnd(0, W), y: rnd(0, H), vx: rnd(-0.14, 0.14), vy: rnd(-0.14, 0.14),
                 r: isHub ? rnd(2.4, 3.6) : rnd(0.8, 1.7), hub: isHub,
                 c: isHub ? (Math.random() < 0.5 ? C.mint : C.purple) : C.cyan,
                 pulse: rnd(0, Math.PI * 2) };
      nodes.push(nd); if (isHub) hubs.push(nd);
    }
    LINK = Math.max(120, Math.min(190, Math.sqrt(area) / 9));
  }

  function spawnPacket() {
    if (!nodes.length) return;
    var a = nodes[(Math.random() * nodes.length) | 0];
    // pick a nearby node as destination
    var best = null, bd = LINK * LINK;
    for (var i = 0; i < nodes.length; i++) {
      var b = nodes[i]; if (b === a) continue;
      var dx = b.x - a.x, dy = b.y - a.y, d = dx * dx + dy * dy;
      if (d < bd && Math.random() < 0.4) { best = b; bd = d; }
    }
    if (best) packets.push({ a: a, b: best, t: 0, sp: rnd(0.008, 0.02),
      c: Math.random() < 0.5 ? C.mint : C.cyan });
  }

  function step(now) {
    var dt = Math.min(2.4, (now - t0) / 16.7); t0 = now;
    g.clearRect(0, 0, W, H);

    // --- drifting grid ---
    var gx = ((now * 0.006) % 46), gy = ((now * 0.004) % 46);
    g.lineWidth = 1; g.strokeStyle = "rgba(120,150,180,0.045)";
    g.beginPath();
    for (var x = -46 + gx; x < W; x += 46) { g.moveTo(x, 0); g.lineTo(x, H); }
    for (var y = -46 + gy; y < H; y += 46) { g.moveTo(0, y); g.lineTo(W, y); }
    g.stroke();

    // --- move nodes ---
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      if (!reduce) { nd.x += nd.vx * dt; nd.y += nd.vy * dt; }
      if (nd.x < -20) nd.x = W + 20; if (nd.x > W + 20) nd.x = -20;
      if (nd.y < -20) nd.y = H + 20; if (nd.y > H + 20) nd.y = -20;
    }

    // --- edges (constellation) ---
    for (var a = 0; a < nodes.length; a++) {
      for (var b = a + 1; b < nodes.length; b++) {
        var na = nodes[a], nb = nodes[b];
        var dx = na.x - nb.x, dy = na.y - nb.y, dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < LINK) {
          var al = (1 - dist / LINK) * 0.16;
          g.strokeStyle = "rgba(0,240,255," + al.toFixed(3) + ")";
          g.lineWidth = (na.hub || nb.hub) ? 0.9 : 0.6;
          g.beginPath(); g.moveTo(na.x, na.y); g.lineTo(nb.x, nb.y); g.stroke();
        }
      }
    }

    // --- packets travelling edges ---
    for (var p = packets.length - 1; p >= 0; p--) {
      var pk = packets[p]; pk.t += reduce ? 0 : pk.sp * dt;
      if (pk.t >= 1) { packets.splice(p, 1); continue; }
      var px = pk.a.x + (pk.b.x - pk.a.x) * pk.t, py = pk.a.y + (pk.b.y - pk.a.y) * pk.t;
      g.fillStyle = pk.c; g.shadowColor = pk.c; g.shadowBlur = 8;
      g.beginPath(); g.arc(px, py, 1.7, 0, 6.283); g.fill(); g.shadowBlur = 0;
    }

    // --- radar pings from hubs ---
    for (var r = pings.length - 1; r >= 0; r--) {
      var pg = pings[r]; pg.r += 0.6 * dt; pg.a *= 0.972;
      if (pg.a < 0.02) { pings.splice(r, 1); continue; }
      g.strokeStyle = "rgba(5,255,161," + pg.a.toFixed(3) + ")";
      g.lineWidth = 1; g.beginPath(); g.arc(pg.x, pg.y, pg.r, 0, 6.283); g.stroke();
    }

    // --- nodes ---
    for (var k = 0; k < nodes.length; k++) {
      var d = nodes[k]; d.pulse += 0.03 * dt;
      var glow = d.hub ? (1.6 + Math.sin(d.pulse) * 0.5) : 1;
      g.fillStyle = d.c;
      if (d.hub) { g.shadowColor = d.c; g.shadowBlur = 10 * glow; }
      g.beginPath(); g.arc(d.x, d.y, d.r, 0, 6.283); g.fill(); g.shadowBlur = 0;
    }

    requestAnimationFrame(step);
  }

  function resize() { build(); }
  build();
  window.addEventListener("resize", function () { clearTimeout(cv._rz); cv._rz = setTimeout(resize, 200); });
  if (!reduce) {
    setInterval(spawnPacket, 900);
    setInterval(function () { if (hubs.length) { var h = hubs[(Math.random() * hubs.length) | 0]; pings.push({ x: h.x, y: h.y, r: 2, a: 0.5 }); } }, 2600);
  }
  requestAnimationFrame(step);
})();
