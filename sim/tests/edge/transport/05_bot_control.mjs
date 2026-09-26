/* §7–8: the bot-control seam (one fresh token per send, never a dead Send; turnstile
 * mutation rows `UX` select labels here — keep them verbatim), and no secret, key or
 * hostname in the client.
 */
import {
  HI, SRC, boot, chatMsg, deep, envelope, eq, live, ok, say,
} from "./harness.mjs";

/* =========================================================================== *
 * 7. THE BOT-CONTROL SEAM — one fresh token per send, and never a dead Send
 * =========================================================================== *
 * The join between `sim/web/turnstile.js` (the widget) and the send path, in its three
 * states — the part `sim/test_turnstile.mjs` cannot see:
 *   · ABSENT — the turn goes out exactly as before the control existed;
 *   · a TOKEN — in Cloudflare's own field name, and a fresh one per send (single-use);
 *   · NULL — NO REQUEST is made, and the page still says something (never a dead Send).
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
    ok(!globalThis.window.moxieTurnstile, "no turnstile.js loaded: the module really is absent");
    await say("hello");
    const chatPost = world.spy.fetches.find((f) => f[0] === "/api/chat");
    ok(!!chatPost, "the turn still reaches /api/chat with no minter present");
    deep(Object.keys(chatPost[1]).sort(), ["context", "text"],
         "…and the body is EXACTLY what it was before the bot control: no empty token field");
    eq(globalThis.window.moxieBridge.transportStats().chatOk, 1, "…and the turn succeeded");
    eq(globalThis.window.moxieBridge.transportStats().botUnavailable, 0, "…with nothing refused locally");
  }

  /* ---- A TOKEN: it lands where the route reads it, and it is FRESH each send - */
  {
    const world = await boot({ answer: live(HI) });
    const calls = minter((n) => "tok-" + n);
    await say("hello");
    await say("hello again");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    eq(posts.length, 2, "two turns, two posts");
    eq(calls.n, 2, "…and the minter was asked once per turn, not once per page");
    deep(calls.actions, ["chat", "chat"],
         "…for the CHAT action every time — `mic.js` asks for `transcribe`, and the server " +
         "refuses each in the other's place");
    eq(posts[0][1]["cf-turnstile-response"], "tok-1",
       "the token rides Cloudflare's own field name, which is what the route reads");
    eq(posts[1][1]["cf-turnstile-response"], "tok-2",
       "…and the SECOND turn carries a SECOND token: they are single-use");
    eq(globalThis.window.moxieBridge.transportStats().botTokens, 2, "both sends are recorded");
    // The rest of the body is untouched — the token is additive, not a rewrite.
    deep(Object.keys(posts[0][1]).sort(), ["cf-turnstile-response", "context", "text"],
         "…and nothing else about the request changed");
  }

  /* ---- NULL: no request, and Moxie says one honest sentence ---------------- */
  {
    const world = await boot({ answer: live(HI) });
    minter(null);
    await say("hello");
    const posts = world.spy.fetches.filter((f) => f[0] === "/api/chat");
    eq(posts.length, 0, "a token that could not be minted makes NO /api/chat request at all");
    const st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 1, "…it is recorded as a local refusal");
    eq(st.chatOk, 0, "…nothing succeeded");
    eq(st.chatErrors, 0, "…and it is NOT reported as a transport error: nothing was sent");

    /* THE PART THAT MATTERS: the page did not go quiet. The child's line is in the
     * transcript AND so is Moxie's — through the same `route()` a real reply takes. */
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
    eq(st.botUnavailable, 1, "the FIRST failure is recorded…");
    eq(st.fallbacks, 0, "…and answers with Moxie's own honest line rather than a stub reply");
    ok(/visitor check/i.test(world.spy.transcript.join(" ")),
       "…which is the line that says what happened");

    await say("two");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.botUnavailable, 2, "the SECOND consecutive failure is recorded…");
    eq(st.fallbacks, 1, "…and is answered from stub.js — not the same sentence again");

    await say("three");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.fallbacks, 2, "…and so is the third");
    eq(st.chatErrors, 0, "…none of them is reported as a transport error: nothing was sent");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0,
       "…and NOT ONE /api/chat request was made by any of them");

    eq(globalThis.window.moxieMode.state(), "degraded",
       "after three local failures the page is DEGRADED, not still claiming LIVE");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED",
       "…with the SCRIPTED badge, like every other unreachable transport");

    // The degrade has teeth: a degraded page STOPS SPENDING and delegates to bridge/.
    const before = globalThis.window.moxieBridge.transportStats().botUnavailable;
    await say("four");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.delegated, 1, "a degraded page delegates the next turn instead of trying again…");
    eq(st.botUnavailable, before, "…so the minter is not even asked");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0, "…and nothing is sent");

    // It recovers through the /api/health probe, and the CONSECUTIVE counter resets: a later
    // single failure says the honest line again rather than a stub.
    minter("tok-recovered");
    await globalThis.window.moxieMode.refresh();
    eq(globalThis.window.moxieMode.state(), "live", "a healthy probe brings the page back…");
    await say("five");
    st = globalThis.window.moxieBridge.transportStats();
    eq(st.botTokens, 1, "…the next turn mints a token and is sent…");
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 1, "…as one real request");

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
    eq(world.spy.fetches.filter((f) => f[0] === "/api/chat").length, 0,
       "a minter that throws sends nothing…");
    eq(globalThis.window.moxieBridge.transportStats().botUnavailable, 1,
       "…and is handled as the same honest refusal, not as an unhandled rejection");
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
    eq(globalThis.window.moxieMode.state(), "live", "…and the page STAYS live");
    eq(globalThis.window.moxieMode.badge(), "MOXIE ONLINE", "…with the LIVE badge intact");
    ok(/real person/i.test(globalThis.window.moxieMode.message()),
       `…and copy that tells the visitor to try again (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    ok(world.spy.transcript.join(" ").length > 0, "…and the transcript is not empty");

    /* AND THE NOTE IS CLEARED BY THE TURN THAT SUCCEEDS, not by the next 30 s poll.
     * `turnstile_failed` carries no suppression window — a fresh token is a tap away — so
     * nothing else would clear it, and the copy would sit under a working box telling the
     * visitor to try again for up to half a minute after they already had. */
    refusing = false;
    await say("hello once more");
    eq(globalThis.window.moxieMode.reason(), null,
       "a successful turn clears the bot-check note immediately");
    eq(globalThis.window.moxieMode.message(), "",
       "…so the copy under the box goes back to saying nothing");
  }

  /* ---- while turnstile_misconfigured degrades the whole page ---------------- */
  {
    await boot({ answer: live({ status: 503, json: envelope({
      ok: false, degraded: true, reason: "turnstile_misconfigured", mode: "degraded", retry_after_s: 60 }) }) });
    minter("tok-y");
    await say("hello");
    eq(globalThis.window.moxieMode.state(), "degraded",
       "a MISCONFIGURED control degrades the page — it will refuse every visitor identically");
    eq(globalThis.window.moxieMode.badge(), "HOSTED DEMO · SCRIPTED", "…with the SCRIPTED badge");
    ok(/isn’t set up right/i.test(globalThis.window.moxieMode.message()),
       `…and copy that names the deployment, not the visitor (${JSON.stringify(globalThis.window.moxieMode.message())})`);
    delete globalThis.window.moxieTurnstile;
  }
}

/* =========================================================================== *
 * 8. Nothing in the client holds a secret, a key or a hostname
 * =========================================================================== */
{
  // THE `\b` IS LOAD-BEARING: without it the scan fires on prose like "task-scheduler", and
  // a scanner that cries wolf gets waved through. Proven in both directions below.
  const KEY_SHAPED = /\bsk-[A-Za-z0-9_-]{8}/;
  ok(KEY_SHAPED.test("sk-AbCdEfGhIjKlMnOpQr"), "…the scan still catches a key-shaped literal");
  ok(!KEY_SHAPED.test("a task-scheduler runs the retry"),
     "…and does NOT fire on ordinary prose that merely ends a word in 'sk'");
  ok(!KEY_SHAPED.test(SRC.transport), "cloud-transport.js contains no key-shaped string");
  ok(!/mattvalancy|graphlings|pages\.dev/i.test(SRC.transport), "…and no deployment hostname");
  ok(!/https?:\/\//.test(SRC.transport.replace(/^\s*\*.*$/gm, "")),
     "…and no absolute URL outside its header comment — the base is location.origin");
  ok(SRC.transport.includes("window.moxieMode"), "it derives everything it can do from window.moxieMode");
  ok(SRC.transport.includes("credentials: \"omit\""), "…and sends no credentials");
  // A ticket and a context blob are carried opaquely: the transport never parses either.
  ok(!/atob|JSON\.parse\(\s*(ticket|contextBlob)/.test(SRC.transport),
     "the transport never opens a ticket or a context blob — they are opaque to the browser");
}
