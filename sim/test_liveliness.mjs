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
import { requireBrowser, serveWeb, makeChecks, finish } from "./browser_harness.mjs";

const LABEL = "liveliness + chat layout";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});

const settle = (ms) => new Promise((r) => setTimeout(r, ms));

async function open(width, height, isMobile) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, isMobile: !!isMobile, hasTouch: !!isMobile,
                           deviceScaleFactor: 1 });
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
  await page.close();
}
{
  const page = await open(393, 851, true);
  const phone = await dockGeometry(page);
  ok(phone.vw - phone.dockW < 40,
     `phone: the dock spans the viewport (${phone.dockW} of ${phone.vw})`);
  ok(phone.dockLeft < 20, `…starting at the left edge (${phone.dockLeft})`);
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
  ok(Math.abs(a.bubble.cx - a.head.x) <= 24,
     `…horizontally centred on her head (bubble ${a.bubble.cx} vs head ${a.head.x})`);
  /* ABOVE, OR DIRECTLY BELOW WHEN THERE IS NO ROOM ABOVE — and the test says which,
   * because "it is somewhere near her head" would pass for a bubble that had simply been
   * clamped against the ceiling and stopped tracking. The default camera frames her head
   * ~100 px from the top, which is exactly the case that flips. */
  if (a.below) {
    ok(a.bubble.top >= a.head.y - 24,
       `…flipped BELOW her head, because there is no room above (bubble top ${a.bubble.top} vs head ${a.head.y})`);
    ok(a.bubble.top - a.head.y < 40,
       `…and still touching it rather than floating away (gap ${a.bubble.top - a.head.y}px)`);
  } else {
    ok(a.bubble.bottom <= a.head.y + 24,
       `…sitting ABOVE it (bubble bottom ${a.bubble.bottom} vs head ${a.head.y})`);
    ok(a.head.y - a.bubble.bottom < 40,
       `…and still touching it rather than floating away (gap ${a.head.y - a.bubble.bottom}px)`);
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
  const b = await page.evaluate(() => {
    // PAN, not orbit: orbiting keeps the camera TARGETED on her, so she stays dead centre
    // and the screen position barely changes (measured: 30 px, which proves nothing).
    // Moving the target sideways is what slides her across the viewport.
    window.__setCam(1.8, 2.1, 4.8, 1.5, 1.22, 0);
    return new Promise((r) => setTimeout(() => r(window.__bubbleAnchor()), 600));
  });
  ok(Math.abs(b.head.x - a.head.x) > 40,
     `orbiting the camera really moves her head on screen (${a.head.x} -> ${b.head.x})`);
  ok(Math.abs(b.bubble.cx - b.head.x) <= 24,
     `…and the bubble went with it (bubble ${b.bubble.cx} vs head ${b.head.x})`);
  ok(Math.abs(b.bubble.cx - a.bubble.cx) > 40,
     `…which the old viewport-pinned bubble could not have done (${a.bubble.cx} -> ${b.bubble.cx})`);
  await page.close();
}

await browser.close();
await site.close();
finish(LABEL, { fails, count });
