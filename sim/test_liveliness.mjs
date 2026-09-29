/* test_liveliness.mjs — liveliness behaviours, in a real browser on the real page:
 *   1. Moxie stops muttering while you talk to her, and resumes once you stop
 *      (`ambient.js`'s conversation hold).
 *   2. Her self-talk appears in the comms log.
 *   3. The chat dock fills the width available to it, rail open or closed, desktop and phone.
 *   4. The speech bubble hangs on her HEAD in the 3-D scene and follows her.
 *   6. The presence badge stays hidden until a face event (§5 is her diagrams).
 *
 * EVERY ASSERTION READS RECORDED STATE, NEVER A LIVE SAMPLE (playbook rule 11): the page's
 * test seams are read back, and the hold's quiet period is shortened through its seam.
 *
 *   node sim/test_liveliness.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish, pageEyes, launchBrowser }
  from "./browser_harness.mjs";

const LABEL = "liveliness + chat layout";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const browser = await launchBrowser(puppeteer, chrome);

/* ---- WAIT FOR THE LAYOUT, NOT FOR A NUMBER OF MILLISECONDS ---------------- *
 * Fonts first (a late webfont re-flows every box), then the widths must read IDENTICALLY on
 * three consecutive frames. Bounded: a layout that never settles fails loudly. */
async function layoutSettled(page, sels = ["#chat-dock", "#panel"], timeout = 30000) {
  return page.evaluate(async (sels, timeout) => {
    await Promise.race([document.fonts.ready,
                        new Promise((r) => setTimeout(r, 5000))]);
    const read = () => sels.map((sel) => {
      const el = document.querySelector(sel);
      // Hundredths of a px: rounding would call a still-moving layout "stable".
      return el ? Math.round(el.getBoundingClientRect().width * 100) : -1;
    }).join("/");
    const t0 = performance.now();
    let last = null, same = 0;
    for (;;) {
      const now = await new Promise((r) => requestAnimationFrame(() => r(read())));
      same = now === last ? same + 1 : 0;
      last = now;
      if (same >= 2) return now;                       // three frames in agreement
      if (performance.now() - t0 > timeout)
        throw new Error(`layout never settled after ${timeout}ms (${sels.join(",")} = ${now})`);
    }
  }, sels, timeout);
}

/* EYES: a 404'd or throwing page script fails a block instead of timing out silently.
 * The optional sidecar probes (127.0.0.1 is LOCAL to env.js) are refused and COUNTED, so
 * every machine sees the same page whatever listens on :8081/:8082. */
/* The bubble's three placements (moxie/bubble.js), judged from ONE frame stash `e`:
 * `above` / `chest` are centred on an anchor and must not straddle the face line `head.y`;
 * `side` is placed by the edge NEAREST her and must stay outside her head's silhouette. */
function bubbleCovers(e) {
  if (e.mode === "side")
    return e.side === "r" ? e.bubble.left < e.face.x + e.face.r : e.bubble.right > e.face.x - e.face.r;
  return e.bubble.top <= e.head.y && e.bubble.bottom >= e.head.y;
}
function bubbleAnchorErr(e) {
  if (e.mode === "side") return Math.abs((e.side === "r" ? e.bubble.left : e.bubble.right) - e.anchorX);
  return Math.abs(e.bubble.cx - (e.above ? e.head.x : e.chest.x));
}
const EYES = pageEyes(eq);
const eyes = EYES.check;

async function open(width, height, isMobile) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, isMobile: !!isMobile, hasTouch: !!isMobile,
                           deviceScaleFactor: 1 });
  const seen = EYES.watch(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/:808[12]\//.test(u)) { seen.aborted.n++; return r.abort("connectionrefused"); }
    /* No gateway is reachable and none should be. `/api/health` answers 404 (as the static
     * server would) so its console error is COUNTED at the request, not forgiven by a pattern. */
    if (/\/api\/health\b/.test(u)) {
      seen.aborted.refused++;
      return r.respond({ status: 404, contentType: "text/plain", body: "not found" });
    }
    if (/\/api\/(chat|speech|transcribe)\b/.test(u)) { seen.aborted.n++; return r.abort(); }
    return r.continue();
  });
  await page.goto(site.url + "/sim.html", { waitUntil: "domcontentloaded" });
  // The seam has to exist before anything below means anything.
  await page.waitForFunction("window.__ambient && window.moxie && window.__bubbleAnchor", { timeout: 20000 });
  // …and the first layout must be FINISHED before any block measures a rectangle.
  await layoutSettled(page);
  return page;
}

/* ======================================================================== *
 * 1 + 2. THE CONVERSATION HOLD, AND THE SELF-TALK IN THE LOG
 * ======================================================================== */
{
  const page = await open(1280, 900);

  // ---- the master switch is untouched by any of this -------------------- //
  const idleBefore = await page.evaluate(() => document.getElementById("idle-on").checked);
  eq(idleBefore, true, "liveness starts ON (the visitor's own switch)");

  /* ---- a REAL turn puts the hold on ------------------------------------- *
   * Polls for `chatting` (painted by `noteTurn()`) rather than sleeping: the hold is only 4 s
   * wide and a late timer could read it after it lapsed. */
  const held = await page.evaluate(() => {
    window.__ambient.quietMs(4000);            // 45 s is unwatchable in a test
    const el = document.getElementById("transcript");
    const row = document.createElement("div");
    row.className = "turn user";
    row.innerHTML = '<span class="who">You</span><span class="msg">hello moxie</span>';
    el.appendChild(row);                        // exactly what addTranscript() builds
    const hud = document.getElementById("hud");
    return new Promise((resolve, reject) => {
      const t0 = performance.now();
      const poll = () => {
        if (hud.classList.contains("chatting")) return resolve({
          state: window.__ambient.state(),
          hudChatting: hud.classList.contains("chatting"),
          hintShown: !document.getElementById("liveness-hold").hidden,
          idleStillChecked: document.getElementById("idle-on").checked,
        });
        // Bounded, and it names what it was waiting for rather than timing out blankly.
        if (performance.now() - t0 > 3000)
          return reject(new Error("the transcript observer never put the hold on"));
        setTimeout(poll, 5);
      };
      poll();
    });
  });
  eq(held.state.watching, true, "ambient is watching the comms log for real turns");
  eq(held.state.conversing, true, "a turn in the log puts the conversation hold ON…");
  eq(held.hudChatting, true, "…the page records it as `chatting`…");
  eq(held.hintShown, true, "…the visitor is told the self-talk is paused…");
  eq(held.idleStillChecked, true,
     "…and the visitor's OWN liveness switch is NOT flipped: a hold is not a setting");

  /* ---- and it lifts on its own once the conversation goes quiet ---------- *
   * All three markers are waited for TOGETHER (`conversing` is derived live and would
   * return before `reflectHold()` repaints the others), then asserted separately so a
   * half-lapse names the half that did not happen. */
  const HOLD_LAPSE_MS = 30000;              // 7x the shortened 4 s hold: generous, bounded
  let lapseTimedOut = false;
  try {
    await page.waitForFunction(
      () => window.__ambient.state().conversing === false &&
            !document.getElementById("hud").classList.contains("chatting") &&
            document.getElementById("liveness-hold").hidden === true,
      // Polled on a timer, not rAF: a frame-starved page is exactly what this wait is for.
      { timeout: HOLD_LAPSE_MS, polling: 50 });
  } catch { lapseTimedOut = true; }
  const lifted = await page.evaluate(() => ({
    conversing: window.__ambient.state().conversing,
    hudChatting: document.getElementById("hud").classList.contains("chatting"),
    hintShown: !document.getElementById("liveness-hold").hidden,
  }));
  const late = lapseTimedOut
    ? ` [the wait for the lapse TIMED OUT after ${HOLD_LAPSE_MS}ms — THIS is what never arrived]`
    : "";
  eq(lifted.conversing, false, "the hold LAPSES once the conversation goes quiet…" + late);
  eq(lifted.hudChatting, false, "…the `chatting` marker is cleared…" + late);
  eq(lifted.hintShown, false, "…and the paused hint goes away with it" + late);

  // ---- her self-talk lands in the log, as its own kind of row ------------ //
  const mutter = await page.evaluate(() => {
    window.__ambient.say("I am definitely not building a robot army.");
    const el = document.getElementById("transcript");
    const m = el.querySelector(".mutter");
    return {
      present: !!m,
      text: m ? m.querySelector(".msg").textContent : "",
      ariaHidden: m ? m.getAttribute("aria-hidden") : null,
      isTurn: m ? m.classList.contains("turn") : null,
      cueVisible: !!document.getElementById("chat-cue").offsetParent,
      turns: el.querySelectorAll(".turn").length,
    };
  });
  eq(mutter.present, true, "an ambient quip appears in the comms log…");
  eq(mutter.text, "I am definitely not building a robot army.", "…with her actual words…");
  eq(mutter.ariaHidden, "true",
     "…marked aria-hidden, so an unprompted quip every 11-24 s is not read aloud over the answer");
  eq(mutter.isTurn, false,
     "…and NOT a `.turn`: that class is what addTranscript() appends streamed replies into");

  /* ---- and she cannot silence herself with her own voice ----------------- *
   * Deliberately a DURATION: an absence has no event to wait for. 250 ms is the window the
   * observer would fire in; a starved runner only makes the check more certain. */
  const selfHold = await page.evaluate(() => new Promise((r) => setTimeout(
    () => r(window.__ambient.state().conversing), 250)));
  eq(selfHold, false,
     "her own quip does NOT count as a conversation — otherwise one mutter would mute her for ever");

  eyes("the conversation hold", page);
  await page.close();
}

/* ======================================================================== *
 * 3. THE CHAT DOCK FILLS THE SPACE AVAILABLE TO IT
 * ======================================================================== */
async function dockGeometry(page) {
  return page.evaluate(() => {
    const d = document.getElementById("chat-dock").getBoundingClientRect();
    const p = document.getElementById("panel").getBoundingClientRect();
    const hud = document.getElementById("hud");
    return { dockW: Math.round(d.width), dockLeft: Math.round(d.left), dockRight: Math.round(d.right),
             panelLeft: Math.round(p.left), panelW: Math.round(p.width),
             vw: window.innerWidth, closed: hud.classList.contains("rail-closed") };
  });
}
{
  const page = await open(1600, 900);
  const openRail = await dockGeometry(page);
  ok(openRail.dockW > 900,
     `desktop, panel OPEN: the dock is no longer a 760 px column — got ${openRail.dockW}px of ${openRail.vw}`);
  ok(Math.abs(openRail.dockRight - openRail.panelLeft) <= 14,
     `…it runs right up to the engineering panel (dock right ${openRail.dockRight} vs panel left ${openRail.panelLeft})`);

  // …and closing the panel really does hand it the rest of the window.
  await page.click("#rail-toggle");
  // The re-frame is two animation frames away: wait for the widths to stop moving.
  await layoutSettled(page);
  const closedRail = await dockGeometry(page);
  eq(closedRail.closed, true, "desktop: the engineering panel can now be CLOSED at all");
  ok(closedRail.dockW > openRail.dockW + 200,
     `…and the dock takes the freed width (${openRail.dockW} -> ${closedRail.dockW}px)`);
  ok(closedRail.vw - closedRail.dockW < 140,
     `…which is very nearly the whole window (${closedRail.dockW} of ${closedRail.vw})`);
  ok(closedRail.panelW > 0 && closedRail.panelW < 220,
     `…while the panel stays on screen as a handle you can re-open (${closedRail.panelW}px)`);
  eyes("the desktop dock", page);
  await page.close();
}
{
  const page = await open(393, 851, true);
  const phone = await dockGeometry(page);
  ok(phone.vw - phone.dockW < 40,
     `phone: the dock spans the viewport (${phone.dockW} of ${phone.vw})`);
  ok(phone.dockLeft < 20, `…starting at the left edge (${phone.dockLeft})`);
  eyes("the phone dock", page);
  await page.close();
}

/* ======================================================================== *
 * 3b. SHE REACTS, AND SHE THINKS VISIBLY — the loading-bar layer
 * ======================================================================== *
 * The rules that keep it from being annoying are what is asserted: it is subtle, never
 * repeats back to back, does NOT fire on a fast turn, and yields.
 */
{
  const page = await open(1280, 900);
  const faces = await page.evaluate(() => {
    // Record every face and gesture the aliveness layer asks for, without a robot.
    window.__seen = { faces: [], gestures: [] };
    const realFace = window.moxie.setFace;
    window.moxie.setFace = (f) => { window.__seen.faces.push(f); return realFace.call(window.moxie, f); };
    return typeof window.moxieAlive;
  });
  eq(faces, "object", "the aliveness layer is exposed for the page to drive");

  // ---- the tap is acknowledged IMMEDIATELY -------------------------------- //
  const listened = await page.evaluate(() => {
    window.__seen.faces.length = 0;
    window.moxieAlive.listening();
    return { faces: window.__seen.faces.slice(), state: window.moxieAlive.__state() };
  });
  // The layer's own recorded pick, not a `setFace` count (blinks and self-talk share it).
  ok(["curious", "happy"].includes(listened.state.last.listen),
     `opening the mic picks an attentive face at once (got ${listened.state.last.listen})`);
  ok(listened.faces.length >= 1, "…and it really did reach the avatar");
  eq(listened.state.armed, false, "…and listening arms no thinking timer");

  // ---- thinking is DELAYED, so a fast turn never flashes a pose ----------- //
  const fast = await page.evaluate(() => {
    window.__seen.faces.length = 0;
    window.moxieAlive.thinking();
    const armed = window.moxieAlive.__state().armed;
    window.moxieAlive.settled();                       // an answer inside the delay
    return { armed, faces: window.__seen.faces.slice(), after: window.moxieAlive.__state() };
  });
  eq(fast.armed, true, "a turn in flight arms the thinking cue…");
  eq(fast.after.stage, 0,
     "…but a turn answered inside the delay never reaches a beat: a pose that flashes for 200 ms is noise");
  eq(fast.after.armed, false, "…and settling disarms it");
  eq(fast.after.stage, 0, "…leaving no stage behind");

  // ---- a SLOW turn does get a thinking face ------------------------------ //
  const slow = await page.evaluate(() => new Promise((r) => {
    window.__seen.faces.length = 0;
    window.moxieAlive.thinking();
    setTimeout(() => r({ faces: window.__seen.faces.slice(), state: window.moxieAlive.__state() }), 1400);
  }));
  eq(slow.state.stage, 1, "a turn still waiting after the delay DOES reach the first thinking beat");
  ok(["thinking", "curious"].includes(slow.state.last.thinkFace),
     `…and picked a thinking face (got ${slow.state.last.thinkFace})`);
  ok(slow.faces.includes(slow.state.last.thinkFace), "…which really reached the avatar");
  await page.evaluate(() => window.moxieAlive.settled());

  // ---- and it never repeats itself back to back -------------------------- //
  // As `filler.py::pick_filler` on the robot path: ten picks from a two-item list alternate.
  const picks = await page.evaluate(() => {
    const out = [];
    for (let i = 0; i < 10; i++) {
      window.moxieAlive.listening();
      out.push(window.moxieAlive.__state().last.listen);   // the layer's own choice
    }
    return out;
  });
  let backToBack = 0;
  for (let i = 1; i < picks.length; i++) if (picks[i] === picks[i - 1]) backToBack++;
  eq(backToBack, 0, `no cue is ever shown twice in a row (${picks.join(",")})`);
  ok(new Set(picks).size > 1, "…and it really does vary rather than being one fixed cue");
  await page.close();
}

/* ======================================================================== *
 * 4. THE SPEECH BUBBLE HANGS OVER HER HEAD
 * ======================================================================== */
{
  const page = await open(1280, 900);
  const a = await page.evaluate(() => {
    window.moxie.setSpeech("Do you ever think about the sky?");
    return new Promise((r) => requestAnimationFrame(() =>
      requestAnimationFrame(() => r(window.__bubbleAnchor()))));
  });
  eq(a.hidden, false, "saying something shows the bubble");
  eq(a.anchored, true, "…and the bubble is ANCHORED rather than pinned to the viewport");
  eq(a.offStage, false, "…with her head in front of the camera");

  /* ---- ONE INSTANT, WHICH IS WHY THE TOLERANCES BELOW ARE TENTHS ---------- *
   * `a.exact` is the frame stash `updateBubbleAnchor` placed the box from (unrounded px).
   * Re-projecting at readout time would compare frame N's box with frame N+1's head (up to
   * 15.6 px while she leans; 0.06 px against the stash). */
  const e = a.exact;
  eq(a.stamped, true, "…and the page recorded the frame it placed the bubble from");
  eq(a.frozen, false, "…a CURRENT frame, not a stash frozen behind a hidden bubble");

  if (e.mode === "chest") {
    // At her chest it anchors on the CHEST, a few px off the head off-axis; 8 px covers it.
    ok(Math.abs(e.bubble.cx - e.head.x) <= 8,
       `…horizontally centred on her head (bubble ${e.bubble.cx.toFixed(1)} vs head ${e.head.x.toFixed(1)})`);
  }
  ok(bubbleAnchorErr(e) <= 0.5,
     `…and placed on the anchor it computed (${e.mode}, off by ${bubbleAnchorErr(e).toFixed(2)}px)`);
  // CSS honoured the arithmetic: `--bx`/`--by` are `.toFixed(1)`, so 0.1 px is the budget.
  ok(Math.abs(e.bubble.top - e.box.top) <= 0.1,
     `…and CSS put the box where the anchor computed it (top ${e.bubble.top.toFixed(2)} vs ${e.box.top.toFixed(2)})`);
  /* THE INVARIANT: it never covers her face — above her head where there is room, beside
   * it on a wide stage, at her chest on a leader where there is neither. */
  eq(bubbleCovers(e), false,
     `the bubble never covers her face (${e.mode}; head ${e.head.y.toFixed(1)}, bubble ${e.bubble.top.toFixed(1)}..${e.bubble.bottom.toFixed(1)} x ${e.bubble.left.toFixed(0)}..${e.bubble.right.toFixed(0)})`);
  if (e.leader > 0) {
    ok(e.bubble.top > e.head.y,
       `…at her chest, below the head (bubble top ${e.bubble.top.toFixed(1)} vs head ${e.head.y.toFixed(1)})`);
    // Both sides come from one instant; the only residual is `--by`'s `.toFixed(1)`.
    const gap = e.bubble.top - e.head.y;
    ok(Math.abs(e.leader - gap) <= 0.2,
       `…on a leader that spans exactly the gap (${e.leader.toFixed(2)}px for ${gap.toFixed(2)}px)`);
    /* …AND THE LINE ON SCREEN IS THAT LEADER, not the corner tick (`::before` with
     * border-left) stretched down through the text. */
    const drawn = await page.evaluate(() => {
      const cs = getComputedStyle(document.getElementById("bubble"), "::before");
      return { top: parseFloat(cs.top), h: parseFloat(cs.height), bl: cs.borderLeftWidth, bt: cs.borderTopWidth };
    });
    ok(Math.abs(drawn.top + drawn.h) <= 1.5 && drawn.h > 0,
       `…and the DRAWN leader rises from the box top to her head (top ${drawn.top}, height ${drawn.h})`);
    ok(drawn.bl === "0px" && drawn.bt === "0px",
       `…as a plain line, not the corner tick stretched (border-left ${drawn.bl}, border-top ${drawn.bt})`);
  } else if (e.mode === "above") {
    ok(e.bubble.bottom < e.head.y,
       `…above the head (bubble bottom ${e.bubble.bottom.toFixed(1)} vs head ${e.head.y.toFixed(1)})`);
    /* Its tail rests on her crown (the anchor sits just above her face panel), so it
     * neither hides her face nor floats away. `bh` is cached for 250 ms while the
     * typewriter wraps, hence a band rather than equality. */
    ok(e.bubble.bottom <= e.crown.y + 2 && e.crown.y - e.bubble.bottom < 40,
       `…and its tail rests on her crown (bubble bottom ${e.bubble.bottom.toFixed(1)} vs crown ${e.crown.y.toFixed(1)})`);
  }
  ok(a.bubble.top > 0 && a.bubble.left >= 0 && a.bubble.right <= 1280,
     "…entirely on screen");

  /* MOVE THE CAMERA (`window.__setCam`) and the bubble stays on her head; a viewport-pinned
   * bubble cannot move at all. Waited on the placement counter `seq`, not a clock. */
  const b = await page.evaluate(() => {
    // Read AT the pan: an older seq could already be two past and return a stale frame.
    const seq0 = window.__bubbleAnchor().seq;
    // PAN, not orbit: orbiting keeps her dead centre; moving the target slides her across.
    window.__setCam(1.8, 2.1, 4.8, 1.5, 1.22, 0);
    return new Promise((r, reject) => {
      const t0 = performance.now();
      const poll = () => {
        // A hidden bubble places nothing by design, so keep her talking while we wait.
        if (document.getElementById("bubble").classList.contains("hidden"))
          window.moxie.setSpeech("Do you ever think about the sky?");
        const a = window.__bubbleAnchor();
        // Two placements past the pan: the first may have been mid-flight when it landed.
        if (a.seq >= seq0 + 2) return r(a);
        if (performance.now() - t0 > 8000) return reject(new Error("anchor never re-placed"));
        requestAnimationFrame(poll);
      };
      poll();
    });
  });
  const be = b.exact;
  ok(b.seq > a.seq, `the anchor was re-placed after the camera moved (frame ${a.seq} -> ${b.seq})`);
  ok(Math.abs(be.head.x - e.head.x) > 40,
     `orbiting the camera really moves her head on screen (${e.head.x.toFixed(0)} -> ${be.head.x.toFixed(0)})`);
  ok(bubbleAnchorErr(be) <= 0.5 && !bubbleCovers(be),
     `…and the bubble went with it (${be.mode}, anchor off by ${bubbleAnchorErr(be).toFixed(2)}px)`);
  /* The rendered BOX moved (either edge: a side bubble that swapped sides can end with its
   * near edge exactly where it started, while the box itself jumped a whole width). */
  const boxMoved = Math.max(Math.abs(be.bubble.left - e.bubble.left), Math.abs(be.bubble.right - e.bubble.right));
  ok(boxMoved > 40,
     `…which the old viewport-pinned bubble could not have done (moved ${boxMoved.toFixed(0)}px; ${e.mode}/${e.side} -> ${be.mode}/${be.side})`);

  /* ---- AND THE READOUT IS HONEST ABOUT BEING STALE ------------------------ *
   * Hidden, the anchor stops updating, so the readout must SAY it is frozen. */
  const frozen = await page.evaluate(() => {
    document.getElementById("bubble").classList.add("hidden");     // what the hold timer does
    const first = window.__bubbleAnchor();
    return new Promise((r) => setTimeout(() => r({ first, later: window.__bubbleAnchor() }), 300));
  });
  eq(frozen.later.frozen, true, "a hidden bubble reports its anchor as FROZEN, not as current");
  ok(frozen.later.ageMs >= frozen.first.ageMs + 250,
     `…and the age keeps growing to prove it (${frozen.first.ageMs}ms -> ${frozen.later.ageMs}ms)`);
  eq(frozen.later.exact.head.y, frozen.first.exact.head.y,
     "…while the frozen numbers themselves do not move a hair, however long she breathes");
  eyes("the speech bubble", page);
  await page.close();
}

/* ======================================================================== *
 * 4-side. NO HEADROOM, WIDE STAGE: BESIDE HER HEAD, NOT ON HER CHIN
 * ======================================================================== *
 * 1280x600 with the rail open (her crown sits ~20 px short of the headroom cut at every
 * point of her breathing, so this is clearly-no-headroom geometry, not a borderline one):
 * the bubble does not fit above her, and the chest fallback
 * used to draw its leader up through her mouth. It goes beside her head instead, placed by
 * the edge NEAREST her (so the typewriter grows it away from her face), tail at her eyes.
 */
{
  const page = await open(1280, 600);
  const a = await page.evaluate(() => {
    window.moxie.setSpeech("Do you ever think about the sky?");
    return new Promise((r) => requestAnimationFrame(() =>
      requestAnimationFrame(() => r(window.__bubbleAnchor()))));
  });
  const e = a.exact;
  eq(e.mode, "side", `1280x600: a wide stage with no headroom puts the bubble BESIDE her head (${e.mode})`);
  ok(e.face.r > 30, `…measured against her real on-screen head width (r ${e.face.r.toFixed(1)}px)`);
  ok(Math.abs(e.bubble.top + 22 - e.face.y) <= 1,
     `…with its tail at her eyes (tail ${(e.bubble.top + 22).toFixed(1)} vs face ${e.face.y.toFixed(1)})`);
  ok(bubbleAnchorErr(e) <= 0.5, `…its near edge on the anchor (off by ${bubbleAnchorErr(e).toFixed(2)}px)`);
  eq(bubbleCovers(e), false,
     `…and outside her head (box ${e.bubble.left.toFixed(0)}..${e.bubble.right.toFixed(0)}, head ${(e.face.x - e.face.r).toFixed(0)}..${(e.face.x + e.face.r).toFixed(0)})`);
  // Let the typewriter finish: the box has grown to full width, still clear of her face.
  const done = await page.evaluate(() => new Promise((r) => setTimeout(() =>
    requestAnimationFrame(() => r(window.__bubbleAnchor())), 1400)));
  ok(!done.hidden && done.exact && !bubbleCovers(done.exact) && done.bubble.right <= 1280 && done.bubble.left >= 0,
     `…and once the whole line is typed it has grown AWAY from her, still on screen (${done.bubble.left}..${done.bubble.right})`);
  eyes("the side bubble", page);
  await page.close();
}

/* ======================================================================== *
 * 4-steady. AT A BORDERLINE SIZE THE PLACEMENT DOES NOT FLICKER WITH HER BREATHING
 * ======================================================================== *
 * 1280x720 is ON the headroom threshold: breathing and the idle nod move her crown ~8 px
 * across it. With a bare threshold the bubble jumped between above and beside about once a
 * second (and a CI runner caught whichever phase it sampled). Whatever it picks first, it
 * must hold that placement through several breaths — and never cover her face.
 */
{
  const page = await open(1280, 720);
  const modes = await page.evaluate(async () => {
    const out = [];
    for (let i = 0; i < 24; i++) {         // ~4.8 s: more than one 4.3 s breath
      if (document.getElementById("bubble").classList.contains("hidden") || i % 6 === 0)
        window.moxie.setSpeech("Do you ever think about the sky?");
      await new Promise((r) => setTimeout(r, 200));
      await new Promise((r) => requestAnimationFrame(r));
      const a = window.__bubbleAnchor();
      if (!a.frozen && a.exact) out.push({ mode: a.exact.mode, e: a.exact });
    }
    return out;
  });
  ok(modes.length >= 15, `1280x720: sampled through her breathing (${modes.length} placed frames)`);
  const seq = modes.map((m) => m.mode);
  let flips = 0;
  for (let i = 1; i < seq.length; i++) if (seq[i] !== seq[i - 1]) flips++;
  eq(flips, 0, `1280x720: the placement holds steady while she breathes (${seq.join(",")})`);
  eq(modes.filter((m) => bubbleCovers(m.e)).length, 0, "1280x720: …and never covers her face");
  await page.close();
}

/* ======================================================================== *
 * 4a. THE ANCHOR READOUT IS ONE INSTANT — PROVED BY MOVING HER FAST
 * ======================================================================== *
 * The race is CREATED here: motors 6 (lean, the widest head arc) and 4 (nod) are stepped end
 * to end while the anchor is read every frame. A readout that re-projects the head at call
 * time drifts up to 15.6 px; the frame stash stays near 0.06, and these tolerances redden
 * if the readout ever mixes two instants again.
 */
for (const [label, w, h, cam, want] of [
  // BESIDE her head: a closer camera, no headroom, a wide stage either side of her
  ["beside, near 1280x900", 1280, 900, [0, 2.0, 3.0, 0, 1.6, 0], "side"],
  // AT HER CHEST on a leader: a close-up (the visitor scroll-zoomed in) fills the stage
  ["chest, close-up 1280x900", 1280, 900, [0, 1.9, 2.2, 0, 1.2, 0], "chest"],
]) {
  const page = await open(w, h);
  if (cam) await page.evaluate((c) => window.__setCam(...c), cam);
  // Per-sweep head-y RANGE, used only in the failure text (backlog/test-timing-under-load.md §2).
  const probe = await page.evaluate(async () => {
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const rows = [];
    const sweeps = [];        // head-y range measured within each sweep, 0 if unsampled
    const stepDeltas = [];    // actual renderer/physics steps, not this test's rAF turns
    const ends = [0, 32767];
    for (let i = 0; i < 16; i++) {
      const start = rows.length;
      const stepStart = window.moxie.getAnimationStepCount();
      // Re-said every sweep: a hidden bubble freezes the anchor by design.
      window.moxie.setSpeech("Do you ever think about the sky?");
      window.moxie.setMotor(6, ends[i % 2]);          // body lean: the biggest head arc
      window.moxie.setMotor(4, ends[(i + 1) % 2]);    // nod, on the opposite phase
      for (let f = 0; f < 4; f++) {
        await frame();
        // Same reason as the camera block: a starved runner can outrun the hold timer.
        if (document.getElementById("bubble").classList.contains("hidden"))
          window.moxie.setSpeech("Do you ever think about the sky?");
        const a = window.__bubbleAnchor();
        if (a.frozen || !a.exact) continue;
        const e = a.exact;
        rows.push({
          leader: e.leader,
          gapErr: Math.abs(e.leader - (e.bubble.top - e.head.y)),
          anchorErr: e.mode === "side"
            ? Math.abs((e.side === "r" ? e.bubble.left : e.bubble.right) - e.anchorX)
            : Math.abs(e.bubble.cx - (e.above ? e.head.x : e.chest.x)),
          covers: e.mode === "side"
            ? (e.side === "r" ? e.bubble.left < e.face.x + e.face.r : e.bubble.right > e.face.x - e.face.r)
            : e.bubble.top <= e.head.y && e.bubble.bottom >= e.head.y,
          mode: e.mode,
          headMoved: e.head.y,
        });
      }
      const ys = rows.slice(start).map((r) => r.headMoved);
      sweeps.push(ys.length ? Math.max(...ys) - Math.min(...ys) : 0);
      stepDeltas.push(window.moxie.getAnimationStepCount() - stepStart);
    }
    return { rows, sweeps, stepDeltas };
  });
  const m = probe.rows;

  ok(m.length >= 30, `${label}: she was sampled while actually moving (${m.length} placed frames)`);
  const validStepDeltas = (deltas) => deltas.length === 16 &&
    deltas.every((steps) => Number.isInteger(steps) && steps >= 0) &&
    deltas.some((steps) => steps > 0);
  ok(validStepDeltas(probe.stepDeltas) && !validStepDeltas(Array(16).fill(0)),
     `the page counted its own animation steps (${probe.stepDeltas.join(", ")})`);
  const spread = Math.max(...m.map((r) => r.headMoved)) - Math.min(...m.map((r) => r.headMoved));
  // A small `spread` means EITHER the drive never moved her OR the runner was too starved to
  // move her far; the red message counts sweeps that moved < 6 px to say which.
  const stalled = probe.sweeps.filter((r) => r < 6).length;
  const underStepped = probe.stepDeltas.filter((steps) => steps < 4).length;
  ok(spread > 40,
     `…and the drive really swung her head across the screen (${spread.toFixed(0)}px of travel${
       spread > 40 ? "" : `; ${underStepped}/16 sweeps received <4 renderer steps; ` +
       `${stalled}/16 moved her <6px, so ${
         stalled > 8 ? "this runner never gave her the frames to move in" : "the drive itself did not swing her"}`})`);
  const leadered = m.filter((r) => r.leader > 0);
  const placed = m.filter((r) => r.mode === want);
  ok(placed.length >= 20,
     `${label}: …placed ${want === "side" ? "BESIDE her head" : "at her chest on a leader"} for most of it (${placed.length} of ${m.length} frames)`);
  if (want === "chest")
    ok(leadered.length >= 20, `${label}: …with a real leader drawn (${leadered.length} frames)`);

  const worstGap = leadered.length ? Math.max(...leadered.map((r) => r.gapErr)) : 0;
  ok(worstGap <= 0.2,
     `${label}: THE LEADER SPANS THE GAP IN EVERY FRAME, however fast she moves (worst ${worstGap.toFixed(2)}px)`);
  const worstAnchor = Math.max(...m.map((r) => r.anchorErr));
  ok(worstAnchor <= 0.5,
     `${label}: …and the box stays on the anchor it was placed from (worst ${worstAnchor.toFixed(2)}px)`);
  eq(m.filter((r) => r.covers).length, 0,
     `${label}: …and NOT ONE of those frames put the bubble over her face`);
  await page.close();
}

/* ======================================================================== *
 * 4b. THE FACE IS SAFE AT EVERY VIEWPORT — including the ones that broke
 * ======================================================================== *
 * Portrait phones once had the bubble flipped over her face; landscape phones once squeezed
 * the stage to a sliver. Both are asserted here.
 */
for (const [label, w, h] of [
  ["portrait 393x851", 393, 851],
  ["landscape 851x393", 851, 393],
  ["landscape 667x375", 667, 375],
]) {
  const page = await open(w, h, true);
  const r = await page.evaluate(() => {
    window.moxie.setSpeech("Do you ever think about the sky and all the stars up there?");
    return new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(() => {
      const st = document.getElementById("stage").getBoundingClientRect();
      res({ a: window.__bubbleAnchor(), stageH: Math.round(st.height), vh: window.innerHeight });
    })));
  });
  const a = r.a;
  eq(a.hidden, false, `${label}: she is speaking`);
  eq(a.anchored, true, `${label}: the bubble is anchored to her, not pinned to the viewport`);
  eq(a.frozen, false, `${label}: …and its anchor readout is a current frame, not a frozen one`);
  /* THE SAFETY-CRITICAL ONE, judged on the head THIS placement projected (one instant). */
  const ex = a.exact;
  const covers = bubbleCovers(ex);
  eq(covers, false,
     `${label}: THE BUBBLE DOES NOT COVER HER FACE (head ${ex.head.y.toFixed(1)}, bubble ${ex.bubble.top.toFixed(1)}..${ex.bubble.bottom.toFixed(1)})`);
  ok(a.bubble.top >= 0 && a.bubble.bottom <= h,
     `${label}: …and the whole box is on screen`);
  if (ex.leader > 0) {
    const gap = ex.bubble.top - ex.head.y;
    ok(Math.abs(ex.leader - gap) <= 0.2,
       `${label}: …the leader spans exactly the gap (${ex.leader.toFixed(2)}px for ${gap.toFixed(2)}px)`);
  }
  // The stage must be a stage, not a sliver. Before the landscape layout it was 14% of a
  // 393 px screen; a third of the viewport is the floor worth defending.
  ok(r.stageH > r.vh * 0.33,
     `${label}: the 3-D stage gets real height (${r.stageH} of ${r.vh})`);
  await page.close();
}

/* ======================================================================== *
 * 5. SHE DRAWS — lazily, strictly, and never at the cost of her words
 * ======================================================================== *
 * Browser half (server half: `sim/test_demo_proxy.mjs` §15m): a diagram renders, bad syntax
 * draws nothing, and the 3.3 MB mermaid bundle is not on the critical path.
 */
{
  const page = await open(1280, 900);

  // ---- the bundle is NOT loaded until she draws --------------------------- //
  const before = await page.evaluate(() => ({
    api: typeof window.moxieDiagram,
    mermaid: typeof window.mermaid,
    scripts: [...document.querySelectorAll("script[src]")].filter((s) => /mermaid/.test(s.src)).length,
  }));
  eq(before.api, "object", "the renderer is present on the page…");
  eq(before.mermaid, "undefined", "…but 3.3 MB of mermaid is NOT loaded before she needs it");
  eq(before.scripts, 0, "…and no mermaid script tag exists yet");

  // ---- a real diagram renders into the log -------------------------------- //
  const drew = await page.evaluate(() =>
    window.moxieDiagram.render("graph TD;\n  Child-->Moxie;\n  Moxie-->Gateway;")
      .then((ok) => ({
        ok,
        rows: document.querySelectorAll("#transcript .diagram").length,
        svg: document.querySelectorAll("#transcript .diagram svg").length,
        isTurn: document.querySelectorAll("#transcript .diagram.turn").length,
        label: (document.querySelector("#transcript .diagram") || {}).getAttribute
          ? document.querySelector("#transcript .diagram").getAttribute("aria-label") : "",
        stats: window.moxieDiagram.stats,
      })));
  eq(drew.ok, true, "a valid diagram renders");
  eq(drew.rows, 1, "…as one row in the comms log");
  eq(drew.svg, 1, "…containing real SVG");
  eq(drew.isTurn, 0,
     "…and NOT as a `.turn`: addTranscript appends streamed reply chunks into the last " +
     "`.turn.moxie`, which would weld half a sentence into the picture");
  eq(drew.label, "A diagram Moxie drew", "…with an accessible label, since SVG is not text");
  eq(drew.stats.rendered, 1, "…recorded as one render");

  // ---- broken syntax draws NOTHING ---------------------------------------- //
  const broke = await page.evaluate(() =>
    window.moxieDiagram.render("this is not mermaid at all {{{")
      .then((ok) => ({ ok, rows: document.querySelectorAll("#transcript .diagram").length,
                       stats: window.moxieDiagram.stats })));
  eq(broke.ok, false, "syntax mermaid rejects resolves FALSE…");
  eq(broke.rows, 1, "…and adds no row: a broken picture is worse than none");
  eq(broke.stats.invalid, 1, "…recorded as invalid rather than inferred");
  eq(broke.stats.rendered, 1, "…and the earlier render still stands");

  // ---- an empty source is a no-op, not an error --------------------------- //
  eq(await page.evaluate(() => window.moxieDiagram.render("")), false,
     "an empty diagram draws nothing and does not throw");

  await page.close();
}

/* ======================================================================== *
 * 6. THE PRESENCE BADGE STAYS HIDDEN UNTIL HER EYES REPORT SOMETHING
 * ======================================================================== *
 * It carried `hidden` from the start, but an inline `display:flex` beat the UA's
 * `[hidden]` rule, so "PRESENCE UNKNOWN" showed on every local serve. Asserted on the
 * RENDERED box (computed display + size), which is what the visitor sees, not the attribute.
 */
for (const [label, w, h, mobile] of [["desktop", 1280, 900, false], ["phone", 390, 844, true]]) {
  const page = await open(w, h, mobile);
  const shown = () => page.evaluate(() => {
    const b = document.getElementById("presence-badge");
    const r = b.getBoundingClientRect();
    return { display: getComputedStyle(b).display, area: r.width * r.height,
             state: b.getAttribute("data-presence"),
             label: document.getElementById("presence-state").textContent };
  });
  const before = await shown();
  eq(before.state, "unknown", `${label}: no face event yet, so presence is unknown…`);
  ok(before.display === "none" && before.area === 0,
     `${label}: …and the badge is NOT rendered (got display=${before.display}, area=${before.area})`);
  await page.evaluate(() => window.moxieBridge.faceEvent("found"));
  const after = await shown();
  eq(after.label, "HERE", `${label}: a face event names what she saw`);
  ok(after.display === "flex" && after.area > 0,
     `${label}: …and reveals the badge (got display=${after.display}, area=${after.area})`);
  await page.close();
}

await browser.close();
await site.close();
finish(LABEL, { fails, count });
