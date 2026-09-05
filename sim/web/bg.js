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

  /* ---- one clock for the producers AND the consumer -------------------------
   *
   * `packets` and `pings` used to be filled by two setInterval()s and drained only
   * inside step(). That is a producer/consumer mismatch with a name: a browser PAUSES
   * requestAnimationFrame in a hidden tab but keeps timers running, so a backgrounded
   * page piled up entries with nothing consuming them, and the whole pile came due on
   * the frame the visitor came back to. Spawning from inside step() means the producers
   * stop exactly when the consumer stops — there is no state that can only grow.
   *
   * SPAWN_CREDIT_MS caps how much elapsed time a single frame may bank, so returning
   * after four hours (or a laptop waking from sleep) credits one frame's worth of
   * spawning rather than four hours' worth. It is far above any real frame interval on
   * a page anyone would call working, so the visible spawn rate is unchanged.
   *
   * MAX_* are the belt to that braces: a ceiling that holds whatever the scheduler
   * does, including one nobody has thought of yet. Visible steady state is 1-2 of each
   * (packet life ~1-2 s at 0.7 spawns/s; ping life ~2-4 s at 0.38/s), so a cap 20x that
   * is never reached in normal operation and changes nothing about how the field looks.
   */
  var PACKET_MS = 900, PING_MS = 2600, SPAWN_CREDIT_MS = 250;
  var MAX_PACKETS = 48, MAX_PINGS = 24;
  var packetAcc = 0, pingAcc = 0;

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
    if (!nodes.length || packets.length >= MAX_PACKETS) return;
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

  function spawnPing() {
    if (!hubs.length || pings.length >= MAX_PINGS) return;
    var h = hubs[(Math.random() * hubs.length) | 0];
    pings.push({ x: h.x, y: h.y, r: 2, a: 0.5 });
  }

  /** Bank `ms` of elapsed time and spawn whatever that buys. Called once per frame.
   *
   * A HIDDEN TAB BANKS NOTHING, AND THE ACCUMULATORS DO NOT CARRY ACROSS THE BOUNDARY.
   * Moving the producers into the frame loop already stopped the original pile-up, because
   * a real browser pauses `requestAnimationFrame` in a background tab. That is the belt,
   * and it is what the owner's "streaking points after a day" needed. This is the braces,
   * for two holes the belt leaves:
   *
   *   1. `pingAcc` SURVIVES the transition. A tab hidden with the accumulator already at
   *      ~2 500 ms of the 2 600 ms ping interval needs one more frame — up to 250 ms — to
   *      tip over and emit a ping nobody is there to see. That is exactly the
   *      `pings grew while the tab was hidden (1 -> 2 in 20s)` this file's own guard caught
   *      in CI on 2026-09-05, on a PR whose diff was `functions/api/` and could not reach
   *      this file.
   *   2. `requestAnimationFrame` is not reliably paused everywhere this page runs. Headless
   *      Chrome never truly backgrounds a tab, which is why the guard has to simulate
   *      hiding at all — and a mechanism that works only because the browser stops calling
   *      us is a mechanism we do not control. `document.hidden` is the part we do.
   *
   * The check lives HERE rather than at the `spawn(...)` call site because that call's
   * shape is itself asserted: `sim/test_bg_perf.mjs` greps `step()` for it to catch a
   * regression to `setInterval` producers. Restructuring the call site to add this guard
   * trips that assertion — which is the guard working, and worth leaving intact.
   *
   * Zeroing rather than freezing is deliberate: a returning visitor gets the animation
   * resuming from now, not a burst paying out the time they spent elsewhere. */
  function spawn(ms) {
    if (typeof document !== "undefined" && document.hidden) { packetAcc = 0; pingAcc = 0; return; }
    packetAcc += ms; pingAcc += ms;
    while (packetAcc >= PACKET_MS) { packetAcc -= PACKET_MS; spawnPacket(); }
    while (pingAcc >= PING_MS) { pingAcc -= PING_MS; spawnPing(); }
  }

  function step(now) {
    var elapsed = now - t0;
    var dt = Math.min(2.4, elapsed / 16.7); t0 = now;
    if (!reduce) spawn(Math.max(0, Math.min(elapsed, SPAWN_CREDIT_MS)));
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
      /* NOT CHANGED, deliberately, and this is the reasoning. The radius advances with
       * elapsed time (`* dt`); the alpha decays PER FRAME. So a ping lives a fixed ~113
       * frames — 1.9 s and a 68 px ring at 60 fps, 3.8 s and a 136 px ring at 30 — and
       * the effect is a different size on a slower machine. `Math.pow(0.972, dt)` fixes
       * that and is bit-identical at dt === 1, which is the 60 fps the 0.972 was tuned
       * against. It was written, measured and then REVERTED: below 60 fps it visibly
       * shrinks and shortens the rings, and this page is the project's front door whose
       * look is the constraint. The practical harm it would have addressed — pings
       * draining slowest exactly when a backlog has made them most numerous — is already
       * gone, because after this file's other change a backlog cannot form and MAX_PINGS
       * bounds the array at 24 regardless. Left as a look the owner chose, not a bug the
       * fix forgot. */
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
  requestAnimationFrame(step);
})();
