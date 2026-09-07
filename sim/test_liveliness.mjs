/* test_liveliness.mjs — the four things the owner asked for on 2026-09-06, driven in a
 * real browser against the real page.
 *
 *   1. Moxie stops muttering to herself while you are talking to her, and starts again
 *      once you have stopped (`ambient.js`'s conversation hold).
 *   2. Her self-talk appears in the comms log, where it can be read rather than only
 *      caught in passing.
 *   3. The chat dock fills the width actually available to it — which depends on whether
 *      the engineering panel is open — at desktop AND phone widths.
 *   4. The speech bubble hangs above her HEAD in the 3-D scene, not pinned to the top of
 *      the screen, so it stays with her when she moves.
 *
 * EVERY ASSERTION READS RECORDED STATE, NEVER A LIVE SAMPLE (playbook rule 11). The page
 * exports `window.__ambient.state()` and `window.__moxieAnchor`-style readouts for exactly
 * this reason: a test that waited 45 real seconds for the hold to lapse, or that sampled
 * "is she talking right now", would be the flaky-by-construction shape this repo has been
 * bitten by three times. The hold's quiet period is SHORTENED through its test seam and
 * then the recorded flag is read back.
 *
 *   node sim/test_liveliness.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "liveliness + chat layout";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});

const settle = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---- EYES, and the hermeticity they required ------------------------------ *
 *
 * Until 2026-09-06 this suite installed no `console` and no `pageerror` listener, so a
 * page script that 404'd or threw was structurally invisible to it: the seams it waits
 * for (`window.__ambient`, `window.__bubbleAnchor`) would simply never appear and the run
 * would time out with nothing saying why. `watchPage()` gives it both listeners.
 *
 * ADDING THEM FORCED THE FIXTURE TO BECOME HERMETIC, and that is a fix in its own right.
 * This suite intercepted nothing, so on a `127.0.0.1` origin — which `env.js` treats as
 * LOCAL — the page's two optional-sidecar probes went to the real loopback ports. On a
 * developer's box with Piper on :8081 they succeed; in CI they are refused, and the
 * refusal is two console errors. What the page then believes about Piper decides whether
 * `#speech-btn` is the typed turn or the local "Say" control, so this suite's own
 * measurements already depended on what happened to be running on the machine. Refusing
 * both probes here — and COUNTING the refusals, so `notable()` forgives exactly two —
 * makes every box see the same page, which is the same idiom `test_mobile_layout.mjs` and
 * `test_ambient_guard.mjs` already use.
 */
const EYES = new WeakMap();
const eyes = (label, page) => {
  const seen = EYES.get(page) || { errs: [], aborted: null };
  const left = notable(seen.errs, seen.aborted);
  eq(left.length, 0,
     `${label}: the page raised console errors nobody asked for — ${left.length}, ` +
     `first: ${left.slice(0, 3).join(" | ")}`);
};

async function open(width, height, isMobile) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, isMobile: !!isMobile, hasTouch: !!isMobile,
                           deviceScaleFactor: 1 });
  const seen = watchPage(page);
  EYES.set(page, seen);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/:808[12]\//.test(u)) { seen.aborted.n++; return r.abort("connectionrefused"); }
    /* No gateway is reachable from this suite and none should be. Answering `/api/health`
     * 404 is byte-for-byte what the static server did before (it holds no such file); it
     * is written out here only so the console error it causes can be COUNTED at the
     * request that causes it, rather than forgiven by a widened pattern. */
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
  await settle(500);
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

  // ---- a REAL turn puts the hold on ------------------------------------- //
  const held = await page.evaluate(() => {
    window.__ambient.quietMs(4000);            // 45 s is unwatchable in a test
    const el = document.getElementById("transcript");
    const row = document.createElement("div");
    row.className = "turn user";
    row.innerHTML = '<span class="who">Child</span><span class="msg">hello moxie</span>';
    el.appendChild(row);                        // exactly what addTranscript() builds
    return new Promise((r) => setTimeout(() => r({
      state: window.__ambient.state(),
      hudChatting: document.getElementById("hud").classList.contains("chatting"),
      hintShown: !document.getElementById("liveness-hold").hidden,
      idleStillChecked: document.getElementById("idle-on").checked,
    }), 250));
  });
  eq(held.state.watching, true, "ambient is watching the comms log for real turns");
  eq(held.state.conversing, true, "a turn in the log puts the conversation hold ON…");
  eq(held.hudChatting, true, "…the page records it as `chatting`…");
  eq(held.hintShown, true, "…the visitor is told the self-talk is paused…");
  eq(held.idleStillChecked, true,
     "…and the visitor's OWN liveness switch is NOT flipped: a hold is not a setting");

  // ---- and it lifts on its own once the conversation goes quiet ---------- //
  await settle(4600);
  const lifted = await page.evaluate(() => ({
    conversing: window.__ambient.state().conversing,
    hudChatting: document.getElementById("hud").classList.contains("chatting"),
    hintShown: !document.getElementById("liveness-hold").hidden,
  }));
  eq(lifted.conversing, false, "the hold LAPSES once the conversation goes quiet…");
  eq(lifted.hudChatting, false, "…the `chatting` marker is cleared…");
  eq(lifted.hintShown, false, "…and the paused hint goes away with it");

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

  // ---- and she cannot silence herself with her own voice ----------------- //
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
  await settle(400);
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
 *
 * "Reduce the amount of time where Moxie is idle or what she is doing is unclear."
 * Between a tap and an answer there were two dead gaps — the recording and the gateway
 * round trip — and the only feedback in either was grey text. The rules that keep the fix
 * from being annoying are the ones worth testing, so all four are asserted: it is subtle,
 * it never repeats itself back to back, it does NOT fire on a fast turn, and it yields.
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
  /* ASSERTED ON THE LAYER'S OWN RECORDED PICK, not on a count of `setFace` calls. The
   * first draft counted them and read 3 where it expected 1, because `setFace` is a SHARED
   * channel: the avatar blinks through it and ambient self-talk drives it too. Counting
   * calls on a channel three systems write to measures the page, not the feature
   * (playbook rule 11). `__state().last` is what THIS layer chose. */
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
  // The rule `mqtt/moxie_sdk/filler.py::pick_filler` uses on the robot path: a stuck line
  // reads as a broken robot rather than a thinking one. Ten consecutive picks from a
  // two-item list must alternate — never the same twice running.
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
   *
   * `a.exact` is the frame stash `updateBubbleAnchor` wrote when it placed the box: the
   * head/crown/chest it actually projected, unrounded, in viewport px. Before it existed
   * `a.head` was RE-PROJECTED at readout time, so every geometry check below compared a
   * box placed at frame N against a head sampled at frame N+1 — a live sample in a file
   * whose header promises none (playbook rule 11). That is what reddened CI on PR #202,
   * on a diff of Python and YAML that cannot reach browser layout code.
   *
   * Reproduced by construction rather than by waiting: stepping motor 6 (body lean, which
   * pivots at her base so her head swings furthest) end to end and sampling 120 frames put
   * up to 15.6 px between `--leader` and the box's real gap on an UNLOADED machine. The
   * same script against the stash: 0.06 px. The CI number, 2.5 px, is that error with only
   * breathing and liveness moving her — and the stash here is routinely ~85 ms old when a
   * puppeteer round-trip reads it, which is the whole story of why a loaded runner sees
   * more of it than a developer's box does. */
  const e = a.exact;
  eq(a.stamped, true, "…and the page recorded the frame it placed the bubble from");
  eq(a.frozen, false, "…a CURRENT frame, not a stash frozen behind a hidden bubble");

  /* The residual is NAMED, not absorbed: when the bubble is at her chest the placement
   * anchors on the CHEST, and projecting a point 62 cm below her head lands a few px to
   * the side of the head once she is off the camera axis (measured 5.2 px after the pan
   * below, 0.15 px dead centre). 8 px covers that with room; the next assertion pins the
   * rest of the difference on that anchor rather than leaving it unexplained. */
  ok(Math.abs(e.bubble.cx - e.head.x) <= 8,
     `…horizontally centred on her head (bubble ${e.bubble.cx.toFixed(1)} vs head ${e.head.x.toFixed(1)})`);
  const anchorX = e.above ? e.head.x : e.chest.x;
  ok(Math.abs(e.bubble.cx - anchorX) <= 0.5,
     `…and centred on the anchor it placed from (bubble ${e.bubble.cx.toFixed(1)} vs anchor ${anchorX.toFixed(1)})`);
  // CSS honoured the arithmetic: `--bx`/`--by` are written `.toFixed(1)`, so 0.1 px is the
  // whole budget a correct stylesheet needs. Measured worst case 0.06.
  ok(Math.abs(e.bubble.top - e.box.top) <= 0.1 &&
     Math.abs(e.bubble.cx - (e.box.left + e.box.width / 2)) <= 0.1,
     `…and CSS put the box where the anchor computed it (top ${e.bubble.top.toFixed(2)} vs ${e.box.top.toFixed(2)})`);
  /* THE INVARIANT IS THAT IT NEVER COVERS HER FACE, and that is what is asserted —
   * not one particular placement.
   *
   * There are two legal positions and which one is used depends on the viewport: above
   * her head where the framing headroom leaves room (portrait and landscape phones), and
   * at her chest on a leader where it does not (a desktop frames her large, so her crown
   * is near the top of the stage). Pinning "always above" is what produced the reported
   * bug in the first place — the old code flipped to "below the crown", which IS her
   * face. So the test asserts the rule rather than the outcome: the box may not overlap
   * the head anchor, whichever side it is on. */
  const overlapsHead = e.bubble.top <= e.head.y && e.bubble.bottom >= e.head.y;
  eq(overlapsHead, false,
     `the bubble never covers her face (head ${e.head.y.toFixed(1)}, bubble ${e.bubble.top.toFixed(1)}..${e.bubble.bottom.toFixed(1)})`);
  if (e.leader > 0) {
    ok(e.bubble.top > e.head.y,
       `…at her chest, below the head (bubble top ${e.bubble.top.toFixed(1)} vs head ${e.head.y.toFixed(1)})`);
    /* THE LEADER SPANS THE GAP — to a TENTH of a pixel, because both sides now come from
     * one instant. The only residual left is `--by`'s own `.toFixed(1)` quantisation, so
     * 0.2 px is the budget with double the headroom it needs (measured worst case 0.05
     * over 48 frames of her body lean swinging end to end, and 0.044 across a camera pan).
     * A looser number here would be absorbing an error nobody had explained, which is the
     * shape of the bug this replaced rather than a fix for it. */
    const gap = e.bubble.top - e.head.y;
    ok(Math.abs(e.leader - gap) <= 0.2,
       `…on a leader that spans exactly the gap (${e.leader.toFixed(2)}px for ${gap.toFixed(2)}px)`);
  } else {
    ok(e.bubble.bottom < e.head.y,
       `…above the head (bubble bottom ${e.bubble.bottom.toFixed(1)} vs head ${e.head.y.toFixed(1)})`);
    ok(e.head.y - e.bubble.bottom < 90,
       `…and still close to her rather than floating away (gap ${(e.head.y - e.bubble.bottom).toFixed(1)}px)`);
  }
  ok(a.bubble.top > 0 && a.bubble.left >= 0 && a.bubble.right <= 1280,
     "…entirely on screen");

  /* THE WHOLE POINT, and it is stated as the owner stated it: MOVE THE CAMERA and the
   * bubble stays on her head. `window.__setCam` is the deterministic placement hook the
   * screenshot harnesses already use, so this is a real orbit rather than a synthetic
   * drag — and a viewport-pinned bubble (the old `top: 22px; left: 50%`) cannot move at
   * all when the camera does, which is what makes the third assertion the load-bearing
   * one rather than decoration.
   *
   * Head YAW was tried here first and is the wrong instrument: rotating the head about
   * its own axis moves its CENTRE by about seven pixels, so the test could not tell
   * "tracks the head" from "does nothing". */
  /* WAITED ON THE PLACEMENT COUNTER, NOT ON A CLOCK. The old form slept 600 ms and read
   * whatever was there. That was only ever safe because the readout re-projected the head
   * live, so it answered even on a frame the page never rendered; now that it reports the
   * placement it actually made, a runner starved enough to skip 600 ms of frames would be
   * asked "where did the bubble go" before it had gone anywhere — and would truthfully
   * answer "nowhere". Measured: under 2x CPU oversubscription this suite reached the read
   * with `head.x` still 638, unmoved, because no frame had been placed since the pan. So
   * the test waits for `seq` to advance instead, which is the page telling it the anchor
   * has been re-placed rather than the test guessing. */
  const b = await page.evaluate((seq0) => {
    // PAN, not orbit: orbiting keeps the camera TARGETED on her, so she stays dead centre
    // and the screen position barely changes (measured: 30 px, which proves nothing).
    // Moving the target sideways is what slides her across the viewport.
    window.__setCam(1.8, 2.1, 4.8, 1.5, 1.22, 0);
    return new Promise((r, reject) => {
      const t0 = performance.now();
      const poll = () => {
        // A HIDDEN BUBBLE PLACES NOTHING, by design — so keep her talking while we wait.
        // On a starved runner her hold timer can lapse before the next frame lands, and a
        // wait for a placement that the page has correctly decided not to make never ends.
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
  }, a.seq);
  const be = b.exact;
  ok(b.seq > a.seq, `the anchor was re-placed after the camera moved (frame ${a.seq} -> ${b.seq})`);
  ok(Math.abs(be.head.x - e.head.x) > 40,
     `orbiting the camera really moves her head on screen (${e.head.x.toFixed(0)} -> ${be.head.x.toFixed(0)})`);
  ok(Math.abs(be.bubble.cx - be.head.x) <= 8,
     `…and the bubble went with it (bubble ${be.bubble.cx.toFixed(1)} vs head ${be.head.x.toFixed(1)})`);
  ok(Math.abs(be.bubble.cx - e.bubble.cx) > 40,
     `…which the old viewport-pinned bubble could not have done (${e.bubble.cx.toFixed(0)} -> ${be.bubble.cx.toFixed(0)})`);

  /* ---- AND THE READOUT IS HONEST ABOUT BEING STALE ------------------------ *
   *
   * `updateBubbleAnchor` deliberately early-returns while the bubble is hidden: there is
   * no sense burning a forced synchronous layout every frame for a box nobody can see, so
   * her head walks away from the last recorded placement and the stash goes stale by
   * design. That is correct, and it is exactly why the readout must SAY so — a caller
   * that cannot tell "frozen because hidden" from "current" is back to guessing which
   * instant it is holding, which is the defect this whole slice is about. */
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
 * 4a. THE ANCHOR READOUT IS ONE INSTANT — PROVED BY MOVING HER FAST
 * ======================================================================== *
 *
 * THE RACE IS CREATED HERE, NOT WAITED FOR. Every check above samples her standing more
 * or less still, and at rest the defect this block exists for is a fraction of a pixel:
 * it is why `sim/test_liveliness.mjs` was green on every developer's machine while PR
 * #202 — a Python promotion checker, a workflow and a Markdown file, none of which can
 * reach browser layout code — went red on a loaded CI runner at 88.5px for 91px.
 *
 * The mechanism, measured rather than reasoned about: `updateBubbleAnchor` runs at the top
 * of `animate()` and places the box from the head it projects there; the rest of `animate`
 * then moves her (motor smoothing, breathing, liveness offsets) and the next frame moves
 * her again. A readout that RE-PROJECTS the head at call time therefore compares a box
 * from frame N against a head from frame N+1, and the gap between them is one frame of
 * head travel. Slow frames — swiftshader on a loaded runner — are bigger frames.
 *
 * So this block does what a loaded runner does, deliberately and in a tenth of a second:
 * it steps motor 6 (body lean, which pivots at her base, so her head describes the widest
 * arc she has) and motor 4 (nod) end to end, and reads the anchor every frame. Measured
 * against the old live-re-projecting readout, this reaches 15.6 px of drift on an UNLOADED
 * machine. Against the frame stash it is 0.06. The tolerances below are the second number
 * with headroom, and every one of these checks reddens if the readout goes back to mixing
 * two instants — which is what makes them the assertion that the fix is a fix.
 */
{
  const page = await open(1280, 900);
  const m = await page.evaluate(async () => {
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const rows = [];
    const ends = [0, 32767];
    for (let i = 0; i < 16; i++) {
      // Re-said every sweep: the bubble's own hold timer would otherwise hide it midway,
      // and a hidden bubble freezes the anchor BY DESIGN (nothing is placed, so nothing is
      // recorded) — which would quietly turn this into a test of nothing.
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
          anchorErr: Math.abs(e.bubble.cx - (e.above ? e.head.x : e.chest.x)),
          covers: e.bubble.top <= e.head.y && e.bubble.bottom >= e.head.y,
          headMoved: e.head.y,
        });
      }
    }
    return rows;
  });

  ok(m.length >= 30, `she was sampled while actually moving (${m.length} placed frames)`);
  const spread = Math.max(...m.map((r) => r.headMoved)) - Math.min(...m.map((r) => r.headMoved));
  ok(spread > 40,
     `…and the drive really swung her head across the screen (${spread.toFixed(0)}px of travel)`);
  const leadered = m.filter((r) => r.leader > 0);
  ok(leadered.length >= 20, `…on a leader for most of it (${leadered.length} frames)`);

  const worstGap = Math.max(...leadered.map((r) => r.gapErr));
  ok(worstGap <= 0.2,
     `THE LEADER SPANS THE GAP IN EVERY FRAME, however fast she moves (worst ${worstGap.toFixed(2)}px)`);
  const worstAnchor = Math.max(...m.map((r) => r.anchorErr));
  ok(worstAnchor <= 0.5,
     `…and the box stays on the anchor it was placed from (worst ${worstAnchor.toFixed(2)}px)`);
  eq(m.filter((r) => r.covers).length, 0,
     "…and NOT ONE of those frames put the bubble over her face");
  await page.close();
}

/* ======================================================================== *
 * 4b. THE FACE IS SAFE AT EVERY VIEWPORT — including the ones that broke
 * ======================================================================== *
 *
 * The reported bug was PHONE-ONLY and the desktop check above would never have caught it:
 * "on mobile in portrait the word bubbles at the top of the screen completely block
 * Moxie's face". Measured at 393x851 before the fix — bubble 88..149 with her head at 148.
 * The old code flipped the bubble "below its anchor" when there was no room above, and the
 * anchor was just above her CROWN, so below it was her face.
 *
 * Landscape is here for the same reason and a second one: at 851x393 the stage had been
 * squeezed to 56 px of a 393 px screen (38 of 375 on a smaller phone) by a layout that
 * stacked five rows on the axis a landscape phone has least of.
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
  /* THE SAFETY-CRITICAL ONE, and it reads the same single instant as everything else now:
   * `a.exact.head` is the head this frame's placement projected, not one re-projected a
   * frame later. A face rule judged against a head from a different instant is a face rule
   * that can pass while the box is over her face — the exact failure mode it exists for. */
  const ex = a.exact;
  const covers = ex.bubble.top <= ex.head.y && ex.bubble.bottom >= ex.head.y;
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

await browser.close();
await site.close();
finish(LABEL, { fails, count });
