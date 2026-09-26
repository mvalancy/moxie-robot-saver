/* test_turnstile — §9: the browser half, sim/web/turnstile.js under a stub window. Run via the entry file, never alone. */
import {
  ACT, SITEKEY, chat, deep, eq, fails, join, ok,
  readFileSync, repo, transcribe,
} from "./harness.mjs";

/* =========================================================================== *
 * 9. THE BROWSER HALF — `sim/web/turnstile.js`, under a stub window
 * =========================================================================== *
 * Loaded as SOURCE under a fake window/document with a fake Cloudflare API, the idiom
 * `sim/test_bridge.mjs` established. No browser, no network, no widget.
 *
 * FOUR behaviours that matter and that nothing else can check:
 *
 *   · A FRESH TOKEN PER SEND. Tokens are single-use and live 300 s, so a module that
 *     minted one per page load would work for exactly the first turn of a conversation
 *     and then refuse every later one — a demo that breaks after one sentence, in a way
 *     that looks like the brain failing.
 *   · ONE WIDGET PER ACTION. The two spending routes require different actions back, so a
 *     token minted for a typed sentence must not be what the microphone sends.
 *   · A FAILED SCRIPT LOAD IS NEVER MEMOISED. One `onerror` — or one load that merely
 *     arrived after the 8 s deadline — used to disable every live turn for the rest of the
 *     page session while the page told the visitor to retry, which could never work.
 *   · A CHALLENGE ON SCREEN IS NEVER RESET OUT FROM UNDER THE VISITOR. The page says "try
 *     me once more"; doing that mid-challenge used to discard the half-finished puzzle.
 */
{
  const SRC = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");

  /** A fake page. `sitekey` is what `mode.js` would have published.
   *
   *  Richer than a stub needs to be for one assertion, and deliberately: the module now
   *  injects a `<style>`, creates a holder AND a per-action child box, so a document fake
   *  that only counted `<script>` tags would silently stop exercising most of it. */
  function world(sitekey) {
    const made = { scripts: [], styles: [], body: [], head: [] };
    const el = (tag) => ({
      tag, id: "", className: "", attrs: {}, children: [], style: {},
      setAttribute(k, v) { this.attrs[k] = String(v); },
      appendChild(c) { this.children.push(c); return c; },
    });
    globalThis.document = {
      getElementById: () => null,
      createElement: (tag) => {
        const e = el(tag);
        if (tag === "script") made.scripts.push(e);
        if (tag === "style") made.styles.push(e);
        return e;
      },
      createTextNode: (t) => ({ text: String(t) }),
      head: { appendChild(e) { made.head.push(e); return e; } },
      body: { appendChild(e) { made.body.push(e); return e; } },
      documentElement: { appendChild(e) { made.head.push(e); return e; } },
      addEventListener() {},
    };
    globalThis.window = {
      // Faithful to `mode.js`: `onChange` invokes the listener IMMEDIATELY with the
      // current snapshot and again on every change, and returns an unsubscribe. That
      // immediate call is how the real page renders the chat widget before the first Send
      // rather than during it, so a fake that never called back would be testing a
      // different module.
      moxieMode: { turnstile: () => sitekey, onChange: (fn) => { fn({}); return () => {}; } },
    };
    return made;
  }

  /** Cloudflare's widget API, faked to the documented surface — and PER WIDGET, because
   *  the module now renders one for each action and a single-widget fake could not tell a
   *  chat token from a microphone one.
   *
   *  `held` is what `getResponse()` answers — the token a widget is sitting on. It is a
   *  mutable field rather than a constant because the interactive case is precisely a
   *  widget whose token appears LATER, and a fake that could only ever answer "" would
   *  make that path untestable. */
  function fakeApi(behaviour) {
    const widgets = {};
    const calls = { render: 0, reset: 0, execute: 0, opts: {}, widgets, order: [] };
    let seq = 0;
    globalThis.window.turnstile = {
      render(box, opts) {
        calls.render++;
        const id = "widget-" + ++seq;
        widgets[id] = { id, box, opts, held: "" };
        calls.opts[opts.action] = opts;
        calls.order.push(opts.action);
        return id;
      },
      reset(id) { calls.reset++; if (widgets[id]) widgets[id].held = ""; },
      execute(id) {
        calls.execute++;
        const w = widgets[id];
        if (!w) return;
        if (behaviour === "error") { w.opts["error-callback"](); return; }
        if (behaviour === "silent") return;                  // never calls back at all
        const tok = w.opts.action + "-token-" + calls.execute;
        w.held = tok;
        w.opts.callback(tok);
      },
      getResponse: (id) => (widgets[id] ? widgets[id].held : ""),
    };
    return calls;
  }

  const T = () => globalThis.window.moxieTurnstile;
  /** Race a pending mint against a short timer: the module's own 8 s deadline is the thing
   *  under test in the `silent` cases, so it cannot be shortened from here. */
  const soon = (pr) => Promise.race([pr, new Promise((r) => setTimeout(() => r("__pending"), 50))]);

  /* ---- not enforced: inert, and no third-party script is even requested --- */
  {
    const w = world("");
    (0, eval)(SRC);
    const tok = await T().getToken("chat");
    eq(tok, "", "no sitekey published => getToken() resolves \"\" (send as-is)");
    eq(w.scripts.length, 0, "…and Cloudflare's script is NEVER requested on an unenforced page");
    eq(w.body.length, 0, "…and NOTHING is added to the page — no holder, no box");
    eq(T().enforced(), false, "…and the module says it is not enforced");
    eq(T().stats().skipped, 1, "…recorded as skipped");
  }

  /* ---- enforced and working: ONE widget per action, a FRESH token per send - */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    const t1 = await T().getToken("chat");
    const t2 = await T().getToken("chat");
    const t3 = await T().getToken("chat");
    eq(calls.render, 1, "the chat widget is rendered ONCE, however many sends there are");
    eq(calls.execute, 3, "…and executed once per send");
    eq(calls.reset, 3, "…with reset() before EVERY execute: tokens are single-use");
    ok(t1 && t2 && t3, "every send got a token");
    ok(t1 !== t2 && t2 !== t3, "…and they are DIFFERENT tokens — not one token reused");

    // The render options, which are the UX decision and the server contract in one object.
    /* `|| {}` on purpose. With the client's action table drifted (mutation row D6e) there
      * is no entry under `ACT.chat` at all, and a bare property read THREW — which exits
      * node before a single `FAIL:` line is printed, so the mutation table saw an
      * unattributable crash instead of a named red check. A guard's test must fail
      * legibly; the same defensive read is applied everywhere below for the same reason. */
    const opts = calls.opts[ACT.chat] || {};
    eq(opts.sitekey, SITEKEY, "the widget is rendered with the PUBLISHED sitekey");
    eq(opts.action, ACT.chat,
       "…and the action the server requires back for THIS route (`TURNSTILE_ACTIONS.chat`)");
    eq(opts.appearance, "interaction-only",
       "…invisible unless Cloudflare wants an interaction: NO checkbox in front of a child");
    eq(opts.execution, "execute",
       "…and the challenge runs when we ASK, which is what makes a per-send token possible");
  }

  /* ---- TWO ACTIONS, TWO WIDGETS, AND NO CROSSING OVER -------------------- *
   * The client half of the cross-route replay the server's check 2 refuses. If this page
   * minted one token type for both routes, check 2 would refuse every microphone turn on
   * a correctly-configured deployment — and if it minted a `chat` token for the ears, the
   * ears would be spendable with a typed turn's challenge. */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    const chatTok = await T().getToken("chat");
    const earTok = await T().getToken("transcribe");
    eq(calls.render, 2, "asking for both actions renders TWO widgets");
    deep(calls.order, [ACT.chat, ACT.transcribe],
         "…the chat one first (it is rendered eagerly at boot), the ears on first use");
    eq((calls.opts[ACT.chat] || {}).action, ACT.chat, "…each with its OWN action: chat");
    eq((calls.opts[ACT.transcribe] || {}).action, ACT.transcribe, "…and transcribe");
    ok(String(chatTok).startsWith(ACT.chat + "-"), `the chat send got a chat token (${chatTok})`);
    ok(String(earTok).startsWith(ACT.transcribe + "-"), `…and the mic send got a mic token (${earTok})`);
    ok(chatTok !== earTok, "…and they are not the same string");
    deep(T().actions(), { chat: ACT.chat, transcribe: ACT.transcribe },
         "…and the module publishes the table it mints from");

    // A second chat send resets ONLY the chat widget: the mic's solved challenge is not
    // collateral damage.
    const before = calls.reset;
    await T().getToken("chat");
    eq(calls.reset, before + 1, "a chat send resets one widget, not both");
  }

  /* ---- AN ACTION THIS MODULE DOES NOT KNOW IS `null`, NEVER A CHAT TOKEN -- *
   * The client's half of `_lib/turnstile.js::actionFor`'s "there is no default". A missing
   * or misspelt action must refuse, because the default that reads best (`chat`) is
   * exactly how a microphone turn would come to be paid for with a typed turn's token. */
  {
    world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    for (const [label, arg] of [["no action at all", undefined], ["an empty string", ""],
                                ["a misspelt one", "chatt"], ["a non-string", 7]]) {
      eq(await T().getToken(arg), null, `${label}: getToken() resolves null, not a token`);
    }
    deep(calls.order, [ACT.chat],
         "…and no NEW widget is rendered for an action we do not have (only the eager chat one)");
    eq(T().stats().unknownAction, 4, "…recorded, so the caller's bug is diagnosable");
    eq(T().stats().tokens, 0, "…and no token was minted by any of them");
  }

  /* ---- enforced and broken: `null`, never a hang and never a lie ---------- */
  for (const [label, behaviour] of [["the widget errors", "error"], ["the widget never answers", "silent"]]) {
    world(SITEKEY);
    fakeApi(behaviour);
    (0, eval)(SRC);
    const got = await soon(T().getToken("chat"));
    if (behaviour === "error") {
      eq(got, null, `${label}: getToken() resolves null — the caller must not send`);
    } else {
      eq(got, "__pending", `${label}: getToken() has NOT resolved a token (the deadline owns it)`);
    }
  }

  /* ---- THE INTERACTIVE CASE: a solve that lands past the deadline ---------- *
   * The first of the two paths by which a visitor who actually had to click something ever
   * gets a turn. Their solve arrives after `getToken()` already resolved `null` and the
   * page already said "try me once more", so the widget is left holding a perfectly good
   * unspent token — and the NEXT send must spend that rather than reset it and start the
   * challenge over. An earlier draft called `reset()` unconditionally, which would have
   * made the page unusable for exactly the visitors Turnstile decided to challenge. */
  {
    world(SITEKEY);
    const calls = fakeApi("silent");                 // execute() never calls back
    (0, eval)(SRC);
    /* Mints are serialised (one `pending` resolver per widget), so the second send below
     * queues behind the first until the first gives up — which is correct behaviour and,
     * at the shipped 8 s, eight seconds of suite. Shortened here for the same reason as
     * the rejoin case, with the shipped constant pinned from the source separately. It is
     * 200 ms rather than 40 so that `soon()`'s own 50 ms race still observes the mint as
     * PENDING — the assertion below is that it has not resolved a token, which a deadline
     * shorter than the race would satisfy by resolving `null` instead. */
    T().__deadlineMs(200);
    const first = await soon(T().getToken("chat"));
    eq(first, "__pending", "the first send is still waiting on an interactive challenge…");
    // The human finishes clicking, late. Cloudflare hands the widget a token.
    (Object.values(calls.widgets)[0] || {}).held = "solved-late";
    const second = await T().getToken("chat");
    eq(second, "solved-late",
       "…and the NEXT send spends the token the widget was left holding");
    eq(T().stats().reused, 1, "…recorded as reused, not minted");

    /* AND IT IS NEVER HANDED OUT TWICE. `getResponse()` still answers `solved-late`, so a
     * module that trusted it blindly would replay the token and the server would refuse
     * the turn as `timeout-or-duplicate` — a bug that appears only on the turn AFTER an
     * interactive challenge, which is about as hard to notice as a bug gets. */
    const third = await soon(T().getToken("chat"));
    ok(third !== "solved-late", `a spent token is NEVER handed out again (got ${JSON.stringify(third)})`);
    T().__deadlineMs(0);
  }

  /* ---- A SEND DURING A LIVE CHALLENGE WAITS FOR IT; IT DOES NOT RESTART IT - *
   * The second interactive path, and the one the page's own copy walks a visitor straight
   * into: send #1's deadline fires, Moxie says "try me once more", and the visitor does
   * exactly that WHILE still working through the challenge on screen. An unconditional
   * `reset()` there discarded the half-finished puzzle and drew a new one — every single
   * time they followed the instruction — so the only way to complete a turn was to ignore
   * the page. Now the second send becomes the waiter for the challenge already running. */
  {
    world(SITEKEY);
    const calls = fakeApi("silent");
    (0, eval)(SRC);
    /* THE REAL SEQUENCE, at 1/200th of the wall clock. The visitor's second Send happens
     * AFTER the page told them to try again — which happens when the first mint's deadline
     * fires — so this case is unreachable without waiting one deadline out. `__deadlineMs`
     * is the test hook that buys that for 40 ms instead of 8 s; the shipped constant is
     * pinned from the source at the end of this block, so shortening it here cannot hide a
     * change to it. */
    eq(T().__deadlineMs(40), 40, "the mint deadline is shortened for this one case");
    eq(await T().getToken("chat"), null, "send #1 gives up when its deadline fires…");
    eq(calls.reset, 1, "…after one reset");
    eq(calls.execute, 1, "…and one execute");
    eq(T().stats().timeouts, 1, "…recorded as a timeout, not as an error");

    // The visitor is still working through the challenge and does what the page said.
    const second = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(calls.reset, 1, "send #2 mid-challenge does NOT reset the live challenge");
    eq(calls.execute, 1, "…and does NOT start a second one");
    eq(T().stats().rejoined, 1, "…it JOINS the one already on screen (recorded)");

    // They finish. The waiting send is the one that gets the token.
    const w = Object.values(calls.widgets)[0] || { opts: { callback() {} } };
    w.held = "solved-at-last";
    w.opts.callback("solved-at-last");
    eq(await second, "solved-at-last", "…and the waiting send is answered by that solve");
    eq(calls.reset, 1, "…still one reset in total: nothing was thrown away");

    // AND ONCE THE CHALLENGE HAS CONCLUDED, THE NEXT SEND DOES ASK FOR A NEW ONE. Without
    // this, "never reset" would be indistinguishable from "reset is broken".
    await soon(T().getToken("chat"));
    eq(calls.reset, 2, "a send AFTER the challenge concluded resets and asks again");
    eq(calls.execute, 2, "…so `outstanding` is cleared by the callback, not sticky");
    T().__deadlineMs(0);                             // back to the shipped value
    ok(/var EXECUTE_TIMEOUT_MS = 8000;/.test(SRC),
       "…and the SHIPPED deadline is still 8 s, read out of the source");
  }

  /* ---- Cloudflare's script cannot load, AND THE NEXT SEND MAY TRY AGAIN ---- *
   * THE BUG THIS BLOCK EXISTS FOR. The first version memoised the promise, so ONE failed
   * load — an ad-blocker rule, a captive portal, a cell handoff, a single edge 5xx — or a
   * load that merely took longer than the 8 s deadline, disabled every live turn for the
   * REST OF THE PAGE SESSION: the cached `false` was handed to every later caller, no
   * second request was ever made, no widget was ever rendered, and the page kept telling
   * the visitor to retry something that could not succeed. Only a reload recovered, and
   * nothing said so. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);                       // no window.turnstile: nothing to render with
    eq(w.scripts.length, 1, "the script IS requested when a sitekey is published");
    ok(/^https:\/\/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js\?render=explicit$/.test(w.scripts[0].src),
       `…from Cloudflare's documented explicit-render URL (${w.scripts[0].src})`);
    w.scripts[0].onerror();               // what a CSP refusal fires
    eq(await T().getToken("chat"), null,
       "a script that cannot load resolves null — never a silent dead send");
    eq(T().stats().scriptErrors, 1, "…and it is recorded");

    // THE PART THAT WAS BROKEN: the next send asks again.
    const retry = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(w.scripts.length, 2, "…and the NEXT send REQUESTS THE SCRIPT AGAIN (no cached failure)");
    // This time it arrives, and the send that asked for it is served.
    const calls = fakeApi("ok");
    // `|| {onload(){}}` for the same reason as the defensive reads above: with the memo
    // bug (row D6h) there IS no second tag, and indexing it threw before any named red
    // could print.
    (w.scripts[1] || { onload() {} }).onload();
    const tok = await retry;
    ok(!!tok && tok !== null, `…and that send gets a token once the script lands (${tok})`);
    eq(calls.render, 1, "…the widget renders on the retry rather than never");

    // ...and it is BOUNDED: a permanently blocked host does not get a tag per send.
    eq(T().stats().scriptTries, 2, "the script has been requested exactly twice so far");
  }

  /* ---- IT GIVES UP *ASKING*, AND STILL USES AN API THAT TURNS UP ANYWAY --- *
   * The bound on the retry above is real — a host that is blocked permanently (an
   * extension, a DNS filter, a corporate policy) must not get one `<script>` tag per Send.
   * But "stop asking" and "give up" are different things: if `window.turnstile` is present
   * for ANY reason once the budget is spent, a page that refuses to look is refusing turns
   * it could serve. `loadApi()` answers from `api()` BEFORE it consults its own memo or its
   * own counter, which is what makes both true at once. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);
    for (let i = 1; i <= 3; i++) {
      const pr = T().getToken("chat");
      await new Promise((r) => setTimeout(r, 5));
      eq(w.scripts.length, i, `attempt ${i}: exactly ${i} script request(s) so far`);
      (w.scripts[i - 1] || { onerror() {} }).onerror();
      eq(await pr, null, `attempt ${i}: …resolves null when that request fails`);
      await new Promise((r) => setTimeout(r, 0));    // let the memo clear
    }
    eq(await T().getToken("chat"), null, "a fourth send still resolves null…");
    eq(w.scripts.length, 3, "…and does NOT append a fourth tag: the retry is BOUNDED");
    eq(T().stats().scriptTries, 3, "…at exactly MAX_SCRIPT_TRIES requests");

    // The API turns up anyway — another copy of the script, a late execution, an extension
    // that injected it. The page must use it.
    const calls = fakeApi("ok");
    const tok = await T().getToken("chat");
    ok(!!tok, `…yet an API that turns up anyway IS used, budget spent or not (${tok})`);
    eq(calls.render, 1, "…the widget renders from it");
    eq(w.scripts.length, 3, "…without asking for the script again");
  }

  /* ---- A SCRIPT THAT LOADS *LATE* IS STILL USED --------------------------- *
   * The other half of the same bug, and the sneakier half: the request SUCCEEDS but takes
   * longer than the 8 s deadline, so the deadline resolved `false` while the API was on
   * its way. The module's own stats read `scriptLoads: 1, renders: 0` — the script
   * demonstrably loaded and was never used. What decides must be whether `window.turnstile`
   * is HERE, not what a boolean said eight seconds ago. */
  {
    const w = world(SITEKEY);
    (0, eval)(SRC);
    eq(w.scripts.length, 1, "the script was requested");
    /* The load that goes quiet: the first request is settled as failed (so the memo is
     * clear) and the SECOND is left to the deadline with neither `onload` nor `onerror`
     * ever firing — which is what an extension or a proxy that swallows the events looks
     * like, and what the slow-load case looks like from this module's point of view. The
     * deadline is shortened for the same reason as the rejoin case above: reaching this
     * state honestly costs eight seconds and the shipped constant is pinned from the
     * source separately. */
    w.scripts[0].onerror();
    // One tick, so the failed load clears its own memo before the next ask (the memo is
    // cleared in a `.then`, which is a microtask — a send issued in the very same tick as
    // the failure legitimately still sees it).
    await new Promise((r) => setTimeout(r, 0));
    T().__deadlineMs(40);
    const pending = T().getToken("chat");
    await new Promise((r) => setTimeout(r, 5));
    eq(w.scripts.length, 2, "…and asked again");
    // The API turns up without either event: only `api()` knows.
    const calls = fakeApi("ok");
    const tok = await pending;
    ok(!!tok, `a script whose API arrives with no onload is USED, not written off (${tok})`);
    eq(calls.render, 1, "…the widget renders from the API that is PRESENT");
    eq(w.scripts.length, 2, "…and no third request was needed: `api()` is what decides");
    eq(T().stats().scriptLoads, 0, "…even though `onload` never fired at all");
    T().__deadlineMs(0);
  }

  /* ---- THE HOLDER: it cannot swallow the control under the visitor's thumb - *
   * The structural half of a defect measured in a real browser (the behavioural half is
   * `sim/test_mobile_layout.mjs`): a 300x65 challenge at `bottom: 16px` sat exactly on top
   * of `#rail-toggle`, the only way to open the drawer that holds the text box on a phone,
   * and `elementFromPoint()` at the toggle's centre returned the widget. Two properties
   * are asserted from the SOURCE here because they are cheap, they are the whole fix, and
   * this suite runs in the fast tier with no browser at all. */
  {
    const w = world(SITEKEY);
    const calls = fakeApi("ok");
    (0, eval)(SRC);
    // The holder and its stylesheet are built LAZILY, on the first render — an unenforced
    // page must add nothing to the document at all (asserted at the top of this section) —
    // so one send has to happen before there is any geometry to look at.
    await T().getToken("chat");
    eq(calls.render, 1, "one send rendered the widget that needs somewhere to draw");
    const css = (w.styles[0] && w.styles[0].children[0] && w.styles[0].children[0].text) || "";
    ok(w.styles.length === 1, "the module injects exactly one <style> for its holder");
    ok(/#turnstile-holder\{[^}]*pointer-events:none/.test(css),
       `the holder layer itself is pointer-events:none — an empty box cannot swallow a tap (${css.slice(0, 80)})`);
    ok(/#turnstile-holder>\*\{[^}]*pointer-events:auto/.test(css),
       "…while the challenge inside it IS clickable: an unusable challenge is a dead page");
    ok(!/#turnstile-holder\{[^}]*bottom:/.test(css),
       "…and it is NOT anchored to the bottom, where every control on this page lives");
    ok(/#turnstile-holder\{[^}]*align-items:center/.test(css) &&
       /#turnstile-holder\{[^}]*justify-content:center/.test(css),
       "…it is centred in the viewport, which holds no controls at any width");
    // The per-action boxes are children of the holder, so the `> *` rule reaches them.
    const holderEl = w.body.find((e) => e.id === "turnstile-holder");
    ok(!!holderEl, "the holder is appended to document.body, outside every scrolling panel");
    eq((holderEl || { children: [] }).children.length, 1,
       "…and each action's widget gets its own child box");
    eq(((holderEl || { children: [{ attrs: {} }] }).children[0] || { attrs: {} }).attrs["data-action"],
       ACT.chat, "…tagged with the action it is for");
  }
  delete globalThis.window;
  delete globalThis.document;
}
