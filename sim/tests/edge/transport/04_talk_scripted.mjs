/* §6–6b: the injected Talk box, and the consolation line going through the FREE
 * `sendScriptedTurn` seam rather than a paid turn.
 */
import {
  advance, boot, deep, envelope, eq, join, ok, readFileSync, repo, said, say, serve,
} from "./harness.mjs";

/* =========================================================================== *
 * 6. The injected "Talk" box — the control the definition of done needs
 * =========================================================================== */
{
  const EID = "sim-typedturn1";
  const world = await boot({
    answer: serve({ "/api/chat": said("Typed and answered.", EID) }),
  });
  const input = globalThis.document.getElementById("chat-input");
  const send = globalThis.document.getElementById("chat-send");
  ok(input && send, "the transport injected #chat-input and #chat-send");
  eq(input.getAttribute("maxlength"), "500", "the input mirrors DEMO_MAX_INPUT_CHARS");
  eq(globalThis.document.getElementById("chat-status").getAttribute("aria-live"), "polite",
     "the status line is announced politely, per §7");
  ok(world.panel.children.length > 0, "…into the Comms panel, before the Mic section");

  input.value = "  a typed sentence  ";
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(input.value, "", "sending clears the box");
  const posts = world.spy.fetches.filter(([p]) => p === "/api/chat");
  eq(posts.length, 1, "clicking Send spends exactly one turn");
  eq(posts[0][1].text, "a typed sentence", "…with the sentence TRIMMED");
  ok(world.spy.transcript.includes("Typed and answered."), "…and Moxie answered it");

  // Enter sends too; an empty box does not.
  input.value = "enter works";
  world.keyHandlers["chat-input"]({ key: "Enter" });
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2, "Enter sends");
  input.value = "   ";
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2, "a blank box sends nothing");

  // The client-side length check explains itself instead of spending a request.
  input.value = "x".repeat(501);
  world.clickHandlers["chat-send"]();
  await advance(10);
  eq(world.spy.fetches.filter(([p]) => p === "/api/chat").length, 2,
     "an over-length sentence is explained locally, not spent");
  ok(globalThis.document.getElementById("chat-status").textContent.includes("500"),
     "…and the page says what the limit is");

  // And `sendUserTurn` itself ignores an empty turn, whoever calls it (mic.js does).
  const before = world.spy.fetches.length;
  await say("", 10);
  await say(null, 10);
  eq(world.spy.fetches.length, before, "sendUserTurn('') spends nothing");
}

/* =========================================================================== *
 * 6b. THE CONSOLATION LINE IS FREE — `sendScriptedTurn`
 * =========================================================================== *
 * `mic.js` consoles a visitor whose ears failed with a scripted child line; nobody said those
 * words, so they may not buy a chat + speech turn (`sim/test_mic_spend.mjs` counts the same
 * thing in a real browser).
 * =========================================================================== */
{
  const answer = serve({ "/api/chat": said("A paid answer.", "sim-paid1") });

  // (a) A LIVE page: the line is shown, spoken and ANSWERED — for nothing.
  {
    const world = await boot({ answer });
    eq(globalThis.window.moxieMode.canSpendLiveTurn(), true, "the page really is live and spendable");
    eq(typeof globalThis.window.moxieBridge.sendScriptedTurn, "function",
       "the transport exposes sendScriptedTurn for the degraded path");

    const before = world.spy.fetches.length;
    const p = globalThis.window.moxieBridge.sendScriptedTurn("Guess what, it's my birthday today!");
    await advance(1000);
    await p;

    deep(world.spy.fetches.slice(before), [],
         "A SCRIPTED CONSOLATION LINE MAKES NO REQUEST AT ALL on a live page — not /api/chat, not /api/speech");
    ok(world.spy.transcript.includes("Guess what, it's my birthday today!"),
       "…the child's line is still on the page, so the visitor is still consoled");
    ok(world.spy.transcript.includes("Happy birthday! I hope your day is amazing."),
       "…and Moxie still ANSWERS it, from stub.js, after the same 450 ms beat");
    ok(world.spy.sfx.includes("listen"), "…with the same listen SFX a child's turn always fires");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.scripted, 1, "…recorded as one scripted line");
    eq(st.scriptedFree, 1, "…answered for free");
    eq(st.turns, 0, "…and NOT counted as a turn: nobody took one");
    eq(st.live, 0, "…no live turn was opened");
  }

  // (b) A real transcript on the same page still spends exactly one of each. The fix must
  //     not have quietly turned the microphone off.
  {
    const world = await boot({ answer });
    await say("what the visitor actually said", 1000);
    const paid = world.spy.fetches.filter(([pth]) => pth !== "/api/health").map(([pth]) => pth);
    deep(paid, ["/api/chat"], "a REAL transcript still spends its /api/chat, exactly as before");
    eq(globalThis.window.moxieBridge.transportStats().live, 1, "…as a live turn");
    eq(globalThis.window.moxieBridge.transportStats().scripted, 0, "…and not as a scripted one");
  }

  // (c) A page with nothing spendable takes the path it takes today: inner.sendUserTurn,
  //     i.e. stub.js, which echoes AND answers. Byte-for-byte the old behaviour.
  {
    const world = await boot({ answer: () => ({ status: 200, json: envelope({ ok: false, reason: "gateway_not_configured", mode: "degraded" }) }) });
    eq(globalThis.window.moxieMode.canSpendLiveTurn(), false, "an unconfigured deployment spends nothing");
    const before = world.spy.fetches.length;
    globalThis.window.moxieBridge.sendScriptedTurn("Thank you Moxie!");
    await advance(1000);
    deep(world.spy.fetches.slice(before), [], "…so the scripted line makes no request either");
    ok(world.spy.transcript.includes("Thank you Moxie!"), "…the line is still shown");
    ok(world.spy.transcript.includes("You're so welcome. I love celebrating with you!"),
       "…and stub.js still answers it, unchanged");
    eq(globalThis.window.moxieBridge.transportStats().scriptedFree, 0,
       "…through inner.sendUserTurn, not the local assembly — nothing here needed changing");
  }

  // (d) A connected broker is a self-hoster's OWN backend: it still gets the line, exactly
  //     as it does today. This slice is about the shared demo budget, not about them.
  {
    const world = await boot({ answer });
    const published = [];
    globalThis.mqtt.connect = () => ({
      connected: true, on() {}, subscribe() {}, end() {},
      publish: (t, pl) => published.push([t, pl]),
    });
    world.clickHandlers["bus-connect"] && world.clickHandlers["bus-connect"]();
    const before = world.spy.fetches.length;
    globalThis.window.moxieBridge.sendScriptedTurn("Thank you Moxie!");
    await advance(1000);
    ok(published.some(([t]) => t.endsWith("/events/remote-chat")),
       "with a broker connected the scripted line STILL goes onto the bus, unchanged");
    deep(world.spy.fetches.slice(before), [], "…and still costs the hosted gateway nothing");
  }

  // (e) An empty consolation is not a turn.
  {
    const world = await boot({ answer });
    const before = world.spy.fetches.length;
    await globalThis.window.moxieBridge.sendScriptedTurn("");
    await globalThis.window.moxieBridge.sendScriptedTurn(null);
    await advance(1000);
    deep(world.spy.fetches.slice(before), [], "an empty scripted line does nothing at all");
    eq(globalThis.window.moxieBridge.transportStats().scripted, 0, "…and is not recorded as one");
  }

  // (f) The source-level rule, so a future edit cannot quietly put it back: `mic.js`'s
  //     degraded path may not name `sendUserTurn`.
  {
    const mic = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");
    const fb = mic.slice(mic.indexOf("function fallback("), mic.indexOf("/* ---- capture"));
    ok(fb.length > 100, "found mic.js's fallback body");
    ok(!/sendUserTurn/.test(fb),
       "mic.js's fallback never names sendUserTurn — the consolation line cannot reach the paid path");
    ok(/publishScripted/.test(fb), "…it publishes through publishScripted instead");
  }
}
