/* test_turnstile — §9: the browser half, sim/web/turnstile.js under a stub window. Run via the entry file, never alone. */
import { ACT, SITEKEY, deep, eq, join, ok, readFileSync, repo, ts } from "./harness.mjs";

/* =========================================================================== *
 * 9. THE BROWSER HALF — `sim/web/turnstile.js` loaded as source under a fake window and a
 *    fake Cloudflare API. A FRESH token per send (tokens are single-use); ONE widget per
 *    action; a failed or late script load is never memoised; a challenge on screen is never
 *    reset under the visitor. `|| {}` reads keep a mutant failing a NAMED check, not a throw.
 * =========================================================================== */
{
  const SRC = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");

  /** A fake page (the module injects a <style>, a holder and a per-action box).
   *  `moxieMode.onChange` calls back IMMEDIATELY, as mode.js does — that is how the chat
   *  widget renders at boot rather than during the first Send. */
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
    globalThis.window = { moxieMode: { turnstile: () => sitekey, onChange: (fn) => { fn({}); return () => {}; } } };
    return made;
  }

  /** Cloudflare's widget API, per widget. `held` is what `getResponse()` answers — mutable,
   *  because an interactive solve lands LATER. `silent` never calls back. */
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
        if (behaviour === "silent") return;
        const tok = w.opts.action + "-token-" + calls.execute;
        w.held = tok;
        w.opts.callback(tok);
      },
      getResponse: (id) => (widgets[id] ? widgets[id].held : ""),
    };
    return calls;
  }

  /** Boot the module on a fresh page; with `behaviour`, Cloudflare's API is already present. */
  function page(sitekey, behaviour) {
    const w = world(sitekey);
    const calls = behaviour ? fakeApi(behaviour) : null;
    (0, eval)(SRC);
    return { w, calls };
  }
  const T = () => globalThis.window.moxieTurnstile;
  const tick = (ms) => new Promise((r) => setTimeout(r, ms));
  /** Race a pending mint against 50 ms: the module's own deadline is what `silent` tests. */
  const soon = (pr) => Promise.race([pr, tick(50).then(() => "__pending")]);

  /* ---- not enforced: inert, and no third-party script is even requested --- */
  {
    const { w } = page("");
    eq(await T().getToken("chat"), "", "no sitekey published => getToken() resolves \"\" (send as-is)");
    deep([w.scripts.length, w.body.length, T().enforced(), T().stats().skipped], [0, 0, false, 1],
         "…Cloudflare's script is never requested, nothing is added to the page, recorded as skipped");
  }

  /* ---- enforced: ONE widget per action, a FRESH token per send ------------- */
  {
    const { calls } = page(SITEKEY, "ok");
    const toks = [await T().getToken("chat"), await T().getToken("chat"), await T().getToken("chat")];
    eq(calls.render, 1, "the chat widget is rendered ONCE, however many sends there are");
    eq(calls.execute, 3, "…and executed once per send");
    eq(calls.reset, 3, "…with reset() before EVERY execute: tokens are single-use");
    eq(new Set(toks.filter(Boolean)).size, 3, "every send got a DIFFERENT token — not one token reused");
    const opts = calls.opts[ACT.chat] || {};
    deep([opts.sitekey, opts.action], [SITEKEY, ACT.chat], "the widget is rendered with the PUBLISHED sitekey and the chat route's action");
    eq(opts.appearance, "interaction-only", "…invisible unless Cloudflare wants an interaction: NO checkbox in front of a child");
    eq(opts.execution, "execute", "…and the challenge runs when we ASK, which is what makes a per-send token possible");
  }

  /* ---- TWO ACTIONS, TWO WIDGETS, NO CROSSING OVER (the client half of check 2) --- */
  {
    const { calls } = page(SITEKEY, "ok");
    const chatTok = await T().getToken("chat");
    const earTok = await T().getToken("transcribe");
    deep(calls.order, [ACT.chat, ACT.transcribe], "two actions render TWO widgets: chat eagerly at boot, the ears on first use");
    deep([(calls.opts[ACT.chat] || {}).action, (calls.opts[ACT.transcribe] || {}).action], [ACT.chat, ACT.transcribe], "…each with its OWN action");
    ok(String(chatTok).startsWith(ACT.chat + "-") && String(earTok).startsWith(ACT.transcribe + "-"),
       `the chat send got a chat token and the mic send a mic token (${chatTok}, ${earTok})`);
    deep(T().actions(), ts.TURNSTILE_ACTIONS, "the table the client mints from equals the server's TURNSTILE_ACTIONS");
    const before = calls.reset;
    await T().getToken("chat");
    eq(calls.reset, before + 1, "a chat send resets one widget, not both");
  }

  /* ---- AN UNKNOWN ACTION IS `null`, NEVER A CHAT TOKEN (no default, as server-side) --- */
  {
    const { calls } = page(SITEKEY, "ok");
    for (const [label, arg] of [["no action at all", undefined], ["an empty string", ""], ["a misspelt one", "chatt"], ["a non-string", 7]]) {
      eq(await T().getToken(arg), null, `${label}: getToken() resolves null, not a token`);
    }
    deep(calls.order, [ACT.chat], "…no NEW widget is rendered for an action we do not have");
    deep([T().stats().unknownAction, T().stats().tokens], [4, 0], "…recorded as unknownAction, and no token was minted");
  }

  /* ---- enforced and broken: `null`, never a hang and never a lie ---------- */
  page(SITEKEY, "error");
  eq(await soon(T().getToken("chat")), null, "the widget errors: getToken() resolves null — the caller must not send");
  page(SITEKEY, "silent");
  eq(await soon(T().getToken("chat")), "__pending", "the widget never answers: getToken() has NOT resolved a token (the deadline owns it)");

  /* ---- AN INTERACTIVE SOLVE THAT LANDS PAST THE DEADLINE is spent by the NEXT send, once --- */
  {
    const { calls } = page(SITEKEY, "silent");
    T().__deadlineMs(200);            // longer than soon()'s 50 ms, so PENDING is really observed
    eq(await soon(T().getToken("chat")), "__pending", "the first send is still waiting on an interactive challenge…");
    (Object.values(calls.widgets)[0] || {}).held = "solved-late";
    eq(await T().getToken("chat"), "solved-late", "…and the NEXT send spends the token the widget was left holding");
    eq(T().stats().reused, 1, "…recorded as reused, not minted");
    // getResponse() still answers it; handing it out again would be refused as a duplicate.
    const third = await soon(T().getToken("chat"));
    ok(third !== "solved-late", `a spent token is NEVER handed out again (got ${JSON.stringify(third)})`);
    T().__deadlineMs(0);
  }

  /* ---- A SEND DURING A LIVE CHALLENGE JOINS IT; IT DOES NOT RESTART IT ---- */
  {
    const { calls } = page(SITEKEY, "silent");
    eq(T().__deadlineMs(40), 40, "the mint deadline is shortened for this one case");
    eq(await T().getToken("chat"), null, "send #1 gives up when its deadline fires…");
    deep([calls.reset, calls.execute, T().stats().timeouts], [1, 1, 1], "…after one reset and one execute, recorded as a timeout");
    const second = T().getToken("chat");
    await tick(5);
    deep([calls.reset, calls.execute], [1, 1], "send #2 mid-challenge does NOT reset the live challenge, nor start a second one");
    eq(T().stats().rejoined, 1, "…it JOINS the one already on screen (recorded)");
    const w = Object.values(calls.widgets)[0] || { opts: { callback() {} } };
    w.held = "solved-at-last";
    w.opts.callback("solved-at-last");
    eq(await second, "solved-at-last", "…and the waiting send is answered by that solve");
    // Once concluded, the next send DOES ask again — "never reset" is not "reset is broken".
    await soon(T().getToken("chat"));
    deep([calls.reset, calls.execute], [2, 2], "a send AFTER the challenge concluded resets and asks again");
    eq(T().__deadlineMs(0), 8000, "…and the SHIPPED deadline is 8 s");
  }

  /* ---- THE SCRIPT CANNOT LOAD, AND THE NEXT SEND MAY TRY AGAIN (bounded) --- */
  {
    const { w } = page(SITEKEY);                         // no window.turnstile yet
    eq((w.scripts[0] || {}).src, "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
       "with a sitekey published, the script is requested from Cloudflare's documented explicit-render URL");
    w.scripts[0].onerror();                           // what a CSP refusal fires
    eq(await T().getToken("chat"), null, "a script that cannot load resolves null — never a silent dead send");
    eq(T().stats().scriptErrors, 1, "…and it is recorded");
    const retry = T().getToken("chat");
    await tick(5);
    eq(w.scripts.length, 2, "…and the NEXT send REQUESTS THE SCRIPT AGAIN (no cached failure)");
    const calls = fakeApi("ok");
    (w.scripts[1] || { onload() {} }).onload();
    ok(!!(await retry), "…and that send gets a token once the script lands");
    deep([calls.render, T().stats().scriptTries], [1, 2], "…the widget renders on the retry, after exactly two requests");
  }

  /* ---- IT GIVES UP *ASKING*, YET USES AN API THAT TURNS UP ANYWAY --------- */
  {
    const { w } = page(SITEKEY);
    for (let i = 1; i <= 3; i++) {
      const pr = T().getToken("chat");
      await tick(5);
      eq(w.scripts.length, i, `attempt ${i}: exactly ${i} script request(s) so far`);
      (w.scripts[i - 1] || { onerror() {} }).onerror();
      eq(await pr, null, `attempt ${i}: …resolves null when that request fails`);
      await tick(0);                                   // let the memo clear
    }
    eq(await T().getToken("chat"), null, "a fourth send still resolves null…");
    deep([w.scripts.length, T().stats().scriptTries], [3, 3], "…and does NOT append a fourth tag: the retry is BOUNDED");
    const calls = fakeApi("ok");                      // another copy, an extension, a late run
    ok(!!(await T().getToken("chat")), "…yet an API that turns up anyway IS used, budget spent or not");
    deep([calls.render, w.scripts.length], [1, 3], "…the widget renders from it without asking for the script again");
  }

  /* ---- A SCRIPT WHOSE API ARRIVES WITH NO onload IS STILL USED ------------ *
   * What decides is whether `window.turnstile` is HERE, not what a boolean said at the deadline. */
  {
    const { w } = page(SITEKEY);
    w.scripts[0].onerror();
    await tick(0);                                     // the failed load clears its memo in a .then
    T().__deadlineMs(40);
    const pending = T().getToken("chat");
    await tick(5);
    eq(w.scripts.length, 2, "the second request is made, and never fires onload or onerror");
    const calls = fakeApi("ok");
    ok(!!(await pending), "a script whose API arrives with no onload is USED, not written off");
    deep([calls.render, w.scripts.length, T().stats().scriptLoads], [1, 2, 0], "…rendered from the API that is PRESENT, with no third request");
    T().__deadlineMs(0);
  }

  /* ---- THE HOLDER cannot swallow a control under the visitor's thumb ------ *
   * A challenge at `bottom:16px` once sat on `#rail-toggle`; test_mobile_layout.mjs has the
   * in-browser half. The holder is built lazily, so one send happens first. */
  {
    const { w } = page(SITEKEY, "ok");
    await T().getToken("chat");
    eq(w.styles.length, 1, "the module injects exactly one <style> for its holder");
    const css = (w.styles[0] && w.styles[0].children[0] && w.styles[0].children[0].text) || "";
    const holder = (/#turnstile-holder\{([^}]*)\}/.exec(css) || [])[1] || "";
    ok(/pointer-events:none/.test(holder), `the holder layer itself is pointer-events:none — an empty box cannot swallow a tap (${holder.slice(0, 80)})`);
    ok(/#turnstile-holder>\*\{[^}]*pointer-events:auto/.test(css), "…while the challenge inside it IS clickable");
    ok(!/bottom:/.test(holder), "…and it is NOT anchored to the bottom, where every control on this page lives");
    ok(/align-items:center/.test(holder) && /justify-content:center/.test(holder), "…it is centred in the viewport");
    const holderEl = w.body.find((e) => e.id === "turnstile-holder") || { children: [] };
    deep(holderEl.children.map((c) => c.attrs["data-action"]), [ACT.chat],
         "the holder hangs off document.body, with one child box per action, tagged with its action");
  }
  delete globalThis.window;
  delete globalThis.document;
}
