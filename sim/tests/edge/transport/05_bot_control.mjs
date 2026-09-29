/* §7: the bot-control seam — one fresh token per send, never a dead Send. Turnstile mutation
 * rows `UX` select labels here; keep them verbatim.
 */
import { HI, boot, chatMsg, deep, envelope, eq, live, ok, say } from "./harness.mjs";

/* =========================================================================== *
 * 7. THE BOT-CONTROL SEAM — the minter ABSENT (the turn goes out as before), a TOKEN (in
 *    Cloudflare's field name, fresh per send), NULL (no request, and the page still speaks).
 * =========================================================================== */
{
  /** A fake `window.moxieTurnstile`: `getToken()` resolves `hand`; `calls` counts the asks and
   *  records the ACTION each named (a wrong action is refused server-side on every turn). */
  function minter(hand) {
    const calls = { n: 0, actions: [] };
    globalThis.window.moxieTurnstile = {
      getToken: function (action) {
        calls.n += 1;
        calls.actions.push(action);
        return Promise.resolve(typeof hand === "function" ? hand(calls.n) : hand);
      },
    };
    return calls;
  }

  /* ---- ABSENT: byte-identical to the behaviour before the control existed --- */
  {
    const world = await boot({ answer: live(HI) });
    await say("hello");
    const chatPost = world.spy.fetches.find((f) => f[0] === "/api/chat") || [];
    deep(Object.keys(chatPost[1] || {}).sort(), ["context", "text"],
         "with no minter the turn reaches /api/chat with EXACTLY the pre-control body: no empty token field");
    const st = globalThis.window.moxieBridge.transportStats();
    deep([st.chatOk, st.botUnavailable], [1, 0], "…and succeeded, with nothing refused locally");
  }

  /* ---- A TOKEN: it lands where the route reads it, and it is FRESH each send - */
  {
    const world = await boot({ answer: live(HI) });
    const calls = minter((n) => "tok-" + n);
    await say("hello");
    await say("hello again");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    deep(calls.actions, ["chat", "chat"], "the minter was asked once per turn, for the CHAT action every time");
    deep(posts.map(([, b]) => b["cf-turnstile-response"]), ["tok-1", "tok-2"],
         "each turn carries a FRESH token in Cloudflare's own field name (tokens are single-use)");
    deep(Object.keys(posts[0][1]).sort(), ["cf-turnstile-response", "context", "text"], "…and nothing else about the request changed");
    eq(globalThis.window.moxieBridge.transportStats().botTokens, 2, "both sends are recorded");
  }

  /* ---- NULL: no request, and Moxie says one honest sentence ---------------- */
  {
    const world = await boot({ answer: live(HI) });
    minter(null);
    await say("hello");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    eq(posts.length, 0, "a token that could not be minted makes NO /api/chat request at all");
    const st = globalThis.window.moxieBridge.transportStats();
    deep([st.botUnavailable, st.chatOk, st.chatErrors], [1, 0, 0], "…recorded as a local refusal, NOT a transport error");
    // The page did not go quiet: Moxie answers through the same route() a real reply takes.
    const rows = world.spy.transcript.join(" | ");
    ok(/hello/.test(rows), "the child's line is still echoed to the transcript");
    ok(/visitor check/i.test(rows),
       `…and Moxie ANSWERS with an honest sentence rather than nothing (${JSON.stringify(rows.slice(0, 160))})`);
    ok(/try/i.test((world.els["chat-status"] || {}).textContent || ""),
       "…with the status line under the box telling the visitor what to do");
    ok(world.spy.speak.length > 0 || world.spy.setSpeech.length > 0,
       "…and she says it out loud, like any other line");
  }

  /* ---- REPEATED local failures DEGRADE the page, and stop repeating one line - *
   * With the widget host blocked (ad-blocker, DNS filter), every send used to repeat one
   * sentence under a LIVE badge. Each failure is now a transport STRIKE (§6.3's 3-strike
   * degrade), and from the SECOND consecutive failure `stub.js` answers instead. */
  {
    const world = await boot({ answer: live(HI) });
    minter(null);
    eq(globalThis.window.moxieMode.state(), "live", "the page starts live…");

    await say("one");
    let st = globalThis.window.moxieBridge.transportStats();
    deep([st.botUnavailable, st.fallbacks], [1, 0], "the FIRST failure answers with Moxie's own honest line, not a stub reply");
    ok(/visitor check/i.test(world.spy.transcript.join(" ")), "…which is the line that says what happened");

    await say("two");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 2, "the SECOND consecutive failure is recorded…");
    eq(st.fallbacks, 1, "…and is answered from stub.js — not the same sentence again");

    await say("three");
    st = globalThis.window.moxieBridge.transportStats();
    deep([st.fallbacks, st.chatErrors, world.spy.fetches.filter((f) => f[0] === "/api/chat").length], [2, 0, 0],
         "…and so is the third; none is a transport error and NOT ONE /api/chat request was made");

    eq(globalThis.window.moxieMode.state(), "degraded",
       "after three local failures the page is DEGRADED, not still claiming LIVE");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED",
       "…with the SCRIPTED badge, like every other unreachable transport");

    // The degrade has teeth: a degraded page STOPS SPENDING and delegates to bridge/.
    const before = globalThis.window.moxieBridge.transportStats().botUnavailable;
    await say("four");
    st = globalThis.window.moxieBridge.transportStats();
    deep([st.delegated, st.botUnavailable, world.spy.fetches.filter((f) => f[0] === "/api/chat").length], [1, before, 0],
         "a degraded page delegates the next turn: the minter is not asked and nothing is sent");

    // It recovers through the /api/health probe, and the CONSECUTIVE counter resets: a later
    // single failure says the honest line again rather than a stub.
    minter("tok-recovered");
    await globalThis.window.moxieMode.refresh();
    eq(globalThis.window.moxieMode.state(), "live", "a healthy probe brings the page back…");
    await say("five");
    st = globalThis.window.moxieBridge.transportStats();
    deep([st.botTokens, world.spy.fetches.filter((f) => f[0] === "/api/chat").length], [1, 1], "…the next turn mints a token and is sent as one real request");

    minter(null);
    await say("six");
    eq(globalThis.window.moxieBridge.transportStats().fallbacks, 2,
       "…and the NEXT failure is a FIRST failure again: the honest line, not a stub");
    delete globalThis.window.moxieTurnstile;
  }

  /* ---- a minter that THROWS is the null case, not an exception ------------- */
  {
    const world = await boot({ answer: live(HI) });
    globalThis.window.moxieTurnstile = { getToken: function () { throw new Error("boom"); } };
    await say("hello");
    deep([world.spy.fetches.filter((f) => f[0] === "/api/chat").length, globalThis.window.moxieBridge.transportStats().botUnavailable], [0, 1],
         "a minter that THROWS sends nothing and is the same honest refusal, not an unhandled rejection");
    delete globalThis.window.moxieTurnstile;
  }

  /* ---- and a REFUSAL from the server still answers, as every refusal must --- */
  {
    // `refusing` is flipped mid-block (read by the answer closure), so the SAME page sees a
    // refusal and then a good turn.
    let refusing = true;
    const world = await boot({ answer: live(() => (refusing
      ? { status: 403, json: envelope({ ok: false, degraded: true, reason: "turnstile_failed", mode: "degraded" }) }
      : { status: 200, json: envelope({ messages: [chatMsg("Hi!", "e2")], speech: [] }) })) });
    minter("tok-x");
    await say("hello");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.chatRefused, 1, "a server-side turnstile_failed is a refusal like any other…");
    ok(st.fallbacks >= 1, "…answered from stub.js for this one turn");
    // §6.3: the mode STAYS live — a stale token is not a broken deployment.
    deep([globalThis.window.moxieMode.state(), globalThis.window.moxieMode.badge()], ["live", "MOXIE ONLINE"], "…and the page STAYS live");
    ok(/real person/i.test(globalThis.window.moxieMode.message()),
       `…and copy that tells the visitor to try again (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    // The note is cleared by the turn that SUCCEEDS, not the next 30 s poll (nothing else would).
    refusing = false;
    await say("hello once more");
    deep([globalThis.window.moxieMode.reason(), globalThis.window.moxieMode.message()], [null, ""],
         "a successful turn clears the bot-check note and its copy immediately");
  }

  /* ---- while turnstile_misconfigured degrades the whole page ---------------- */
  {
    await boot({ answer: live({ status: 503, json: envelope({
      ok: false, degraded: true, reason: "turnstile_misconfigured", mode: "degraded", retry_after_s: 60 }) }) });
    minter("tok-y");
    await say("hello");
    deep([globalThis.window.moxieMode.state(), globalThis.window.moxieMode.badge()], ["degraded", "HOSTED DEMO · SCRIPTED"],
         "a MISCONFIGURED control degrades the page with the SCRIPTED badge — it refuses every visitor alike");
    ok(/isn’t set up right/i.test(globalThis.window.moxieMode.message()),
       `…and copy that names the deployment, not the visitor (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    delete globalThis.window.moxieTurnstile;
  }
}
