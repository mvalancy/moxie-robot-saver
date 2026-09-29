/* §5 (§6.3): every degraded path answers, and none of them goes quiet.
 */
import {
  boot, chatMsg, deep, envelope, eq, live, ok, said, say, serve,
} from "./harness.mjs";

/* =========================================================================== *
 * 5. §6.3 — every degraded path answers, and none of them goes quiet
 * =========================================================================== */
{
  // (a) `gateway_not_configured`: the mode is degraded, the transport delegates, and
  // `bridge/` + `stub.js` answer exactly as they do on today's site.
  {
    const world = await boot({
      answer: (path) => (path === "/api/health"
        ? { status: 200, json: envelope({ mode: "degraded", reason: "gateway_not_configured", ok: true, degraded: true, voice: false }) }
        : { status: 503, json: envelope({ mode: "degraded", reason: "gateway_not_configured" }) }),
    });
    eq(globalThis.window.moxieMode.state(), "degraded", "an unconfigured deployment reads as degraded");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO", "…with today's badge, unchanged");
    await say("hi moxie", 1000);
    const posts = world.spy.fetches.filter(([p]) => p !== "/api/health");
    deep(posts, [], "NO /api/chat request is made at all when the mode is not live");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.delegated, 1, "the turn was delegated to bridge/");
    eq(st.live, 0, "…and no live turn was attempted");
    deep(world.spy.transcript, ["hi moxie", "Hi there! It's so good to see you."],
         "…and stub.js answered, through the real bridge");
    eq(world.spy.speak.length, 1, "…spoken by the local voice, as today");
  }

  // (b) `/api/health` absent (404): `offline` — byte-identical to today's page.
  {
    const world = await boot({ answer: () => ({ status: 404, text: "not found" }) });
    eq(globalThis.window.moxieMode.state(), "offline", "a 404 health probe reads as offline");
    await say("hi moxie", 1000);
    deep(world.spy.fetches.filter(([p]) => p !== "/api/health"), [], "offline makes no /api/* request");
    ok(world.spy.transcript.length === 2, "…and the stub still answers");
  }

  // (c) A 429 mid-conversation: the mode STAYS live (a rate-limited visitor is not a
  // broken deployment), this turn is answered from the stub, and the page is not quiet.
  {
    let refuse = false;
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope() };
        if (path === "/api/chat" && refuse) {
          return { status: 429, json: envelope({
            ok: false, degraded: true, reason: "rate_limited", retry_after_s: 20, mode: "degraded" }) };
        }
        if (path === "/api/chat") {
          return { status: 200, json: envelope({ messages: [chatMsg("Sure!", "sim-e1")] }) };
        }
        return { status: 404, text: "" };
      },
    });
    await say("first", 10);
    refuse = true;
    await say("tell me a joke", 1000);

    const M = globalThis.window.moxieMode;
    deep([M.state(), M.reason(), M.badge(), M.message(), M.retryAfterS() > 0, M.canSpendLiveTurn()],
         ["live", "rate_limited", "MOXIE ONLINE", "One at a time! Give Moxie a few seconds.", true, false],
         "a 429 does NOT leave the live state (§6.3 soft degrade): §7's chip copy, a Retry-After window, live turns suppressed");
    ok(world.spy.transcript.includes("Why did the robot cross the road? To recharge on the other side!"),
       "THE REFUSED TURN IS STILL ANSWERED, from stub.js — the page never goes silent (A5)");
    eq(globalThis.window.moxieBridge.transportStats().fallbacks, 1, "…recorded as one fallback");

    // And while the window is open, the next turn is delegated without a request.
    const before = world.spy.fetches.length;
    await say("and another", 1000);
    eq(world.spy.fetches.length, before, "no /api/chat is spent while the Retry-After window is open");
  }

  // (d) `upstream_down`, and `gateway_unreachable_or_gated` (an Access login page in front of
  // the tunnel): the visitor sees the same thing; mode.js must RECOGNISE the second, since an
  // unknown reason is coerced to null and would read as a healthy turn.
  for (const reason of ["upstream_down", "gateway_unreachable_or_gated"]) {
    const world = await boot({
      answer: live({ status: 503, json: envelope({ ok: false, degraded: true, reason, retry_after_s: 60, mode: "degraded" }) }),
    });
    await say("hi moxie", 1000);
    const M = globalThis.window.moxieMode;
    deep([M.state(), M.reason(), M.badge(), M.message()],
         ["degraded", reason, "HOSTED DEMO · SCRIPTED", "Moxie’s brain is unreachable right now — she’s running on what she remembers."],
         `${reason} degrades the mode (reason kept) with §7's SCRIPTED badge and copy`);
    deep([world.spy.transcript.length, globalThis.window.moxieBridge.transportStats().fallbacks], [2, 1],
         `…and the ${reason} turn is still answered from the stub`);
  }

  // (e) A transport error with no envelope at all — the browser could not even reach the
  // route. Three of those degrade the mode (§6.3), and every one still answers.
  {
    const world = await boot({
      answer: live({ reject: true }),
    });
    for (const t of ["one", "two", "three"]) {
      await say(t, 1000);
    }
    const st = globalThis.window.moxieBridge.transportStats();
    deep([st.chatErrors, st.fallbacks, globalThis.window.moxieMode.state()], [3, 3, "degraded"],
         "three transport errors, three stub answers, and the 3-strike rule degraded the mode (§6.3)");
  }

  // (f) A safety BLOCK: `ok: true`, `reason: "blocked"`, and the route's own redirect line
  // is spoken rather than a stub line about the weather.
  {
    const world = await boot({
      answer: serve({ "/api/chat": said("Thank you for telling me. Feelings this big need a grown-up.",
                                        "sim-blocked1", { ok: true, degraded: true, reason: "blocked", mode: "live" }) }),
    });
    await say("something the floor blocks", 1000);
    eq(globalThis.window.moxieMode.state(), "live", "a block does NOT change the mode (§4.5)");
    ok(world.spy.transcript.includes("Thank you for telling me. Feelings this big need a grown-up."),
       "the redirect line is what Moxie says");
    const st = globalThis.window.moxieBridge.transportStats();
    deep([st.blocked, st.fallbacks, world.spy.fetches.filter(([p]) => p === "/api/speech").length], [1, 0, 0],
         "…recorded as a block, the stub NOT used (a kind line was supplied), and no /api/speech call");
  }

  // (g) No voice configured: the words render and speak locally, with no speech request.
  {
    const world = await boot({
      answer: (path) => {
        if (path === "/api/health") return { status: 200, json: envelope({ voice: false }) };
        if (path === "/api/chat") {
          return { status: 200, json: envelope({ voice: false, messages: [chatMsg("No voice here.", "sim-novoice1")], speech: [] }) };
        }
        return { status: 404, text: "" };
      },
    });
    await say("hi", 1000);
    deep(world.spy.fetches.filter(([p]) => p === "/api/speech"), [], "no ticket => no /api/speech request");
    deep(world.spy.setSpeech, ["No voice here."], "…the words still render");
    eq(world.spy.speak.length, 1, "…and speak from the clips, which is today's behaviour");
  }

  // (h) A connected MQTT broker ALWAYS wins, even when the hosted mode is live.
  {
    const world = await boot({
      answer: live({ status: 404, text: "" }),
    });
    const published = [];
    globalThis.mqtt.connect = () => ({
      connected: true, on() {}, subscribe() {}, end() {},
      publish: (t, p) => published.push([t, p]),
    });
    world.clickHandlers["bus-connect"] && world.clickHandlers["bus-connect"]();
    await say("over the bus please", 1000);
    deep(world.spy.fetches.filter(([p]) => p !== "/api/health"), [],
         "with a broker connected, NO /api/chat request is made — the supervisor gets the turn");
    ok(published.some(([t]) => t.endsWith("/events/remote-chat")), "…and the turn went onto the bus");
    eq(globalThis.window.moxieBridge.transportStats().delegated, 1, "…delegated to bridge/");
  }
}
