/* test_console_tryit.mjs — the PARENT CONSOLE's 💬 Try it card (server/static/js/tryit.js) in a
 * real browser. The card is the console's one path to a paid brain call, so the claims are
 * about WHEN it calls and WHAT it carries, read off intercepted requests and the DOM:
 *
 *   1 it shows with NO robot connected (the point of the card), labelled a preview
 *   2 typing alone never calls the brain, nor does the Enter that commits an IME
 *     composition; a real click (and Enter) calls it exactly once
 *   3 each send carries the session the previous answer returned (history threading)
 *   4 a reply renders her words, her face and moves read from the markup, and her actions
 *   5 a refused brain renders its sentence and "she would have said", keeps the line in the
 *     box, and does NOT advance the session
 *   6 "Start over" and a change of who answers start an empty session; a brain that does
 *     not read activities disables the activity picker
 *   7 the same holds while an answer is still on its way: Start over, another brain,
 *     another activity (on the very first line) or another robot wins, and the late answer
 *     is neither shown nor carried — the next send carries an empty session
 *   8 a refresh (a save, a permit) keeps the parent's activity pick and the session, and a
 *     line typed while she was answering stays in the box
 *
 * No FastAPI and no supervisor: assets come from the harness's static server, and every
 * `/local/*` call is answered at the browser with payloads built by the REAL console views
 * (`server/moxie_server/fleet/tryit.py`, dependency-free) in a python3 subprocess, so the
 * card cannot pass against a shape the route stopped sending. An answer can be HELD at the
 * interceptor until the sweep releases it, which is how 7 puts a click between a send and
 * its answer without a timing guess. The supervisor side is `sim/tests/test_console_tryit.py`.
 * TEETH: mutated copies of js/tryit.js must each redden the sweep — one that sends on every
 * keystroke (a debounced "try as you type"), one that forgets to carry the session, one
 * that lets a late answer back in, one that forgets a first line on its way, one that sends
 * on an IME Enter, one whose refresh moves the activity, and one that clears a line typed
 * meanwhile.
 *
 *   node sim/test_console_tryit.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "console-tryit test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const STATIC = join(repo, "server", "static");
const CARD_JS = "/js/tryit.js";
const APPJS = readFileSync(join(STATIC, CARD_JS), "utf8");
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

/* ---- fixtures, through the real console views ---------------------------------------
 * Inputs are SUPERVISOR payloads (the shapes `moxie_runtime/tryit.py` returns); outputs
 * are exactly what `GET/POST /local/tryit` returns, because the same function computes
 * them. The markup readout (`perform`) is what `read_markup` makes of the shown markup. */
const PY = `
import json, sys
sys.path.insert(0, sys.argv[1] + "/server")
from moxie_server import fleet as m

opts = {"ok": True, "preview": True, "device_id": "",
        "child": {"nickname": "Sam", "source": "appliance"},
        "brain": {"id": "content", "label": "Content modules (content)", "source": "default",
                  "note": ""},
        "brains": [{"id": b, "label": l, "group": "", "blurb": ""} for b, l in (
            ("llm", "Free-form companion (llm)"), ("content", "Content modules (content)"),
            ("echo", "Echo (no model) (echo)"))],
        "pin": "", "pin_note": "",
        "modules": [{"key": "FREE_CHAT/default", "module_id": "FREE_CHAT",
                     "content_id": "default", "name": "Free Chat"},
                    {"key": "BEDTIME/default", "module_id": "BEDTIME",
                     "content_id": "default", "name": "Bedtime"}],
        "module_brains": ["content", "webhook"], "current_module": "",
        "streaming": True, "safety": True,
        "limits": {"max_chars": 500, "max_history": 40, "max_name_chars": 40,
                   "timeout_s": 30.0},
        "budget": {"per_hour": 40, "remaining": 40, "resets_in_s": 0}}

def chunk(i, text, final, faces, gestures, actions=()):
    return {"index": i, "text": text, "final": final,
            "markup": "<mark name=x/>" + text, "result": "SUCCESS" if final else "REPLY_PENDING",
            "scored": {"mood": faces[0]["mood"] if faces else "neutral",
                       "dialog_act": "comment"},
            "perform": {"faces": faces, "gestures": gestures, "behaviours": [],
                        "voice": ["excited"], "icons": [], "sounds": [], "spurts": [],
                        "pauses": 1, "unknown": []},
            "actions": list(actions), "end_turn": False}

def turn(speech, said, history, chunks, actions=()):
    return {"ok": True, "kind": "", "preview": True, "published": False, "device_id": "",
            "speech": speech, "child": {"nickname": "Sam", "source": "appliance"},
            "brain": opts["brain"], "module": None, "notes": [], "delivery": "stream",
            "result": "SUCCESS",
            "reply": {"text": said, "chunks": chunks, "actions": list(actions),
                      "end_turn": False},
            "safety": [], "history": history + [{"role": "user", "content": speech},
                                                {"role": "assistant", "content": said}],
            "history_trimmed": 0, "model_calls": 1, "elapsed_ms": 1200,
            "budget": {"per_hour": 40, "remaining": 39, "resets_in_s": 3599}}

H1 = []
t1 = turn("can we draw?", "Hi Sam! I love that idea. What first?", H1,
          [chunk(0, "Hi Sam! I love that idea.", False, [{"mood": "happy", "intensity": 1}],
                 ["Gesture_Self"]),
           chunk(1, "What first?", True, [], ["Gesture_Celebrate"])])
EXIT = {"type": "exit", "module_id": "", "content_id": "", "function": "",
        "wire": {"output_type": "GLOBAL", "action": "exit", "module_id": None,
                 "content_id": None}}
t2 = turn("bye Moxie", "Bye Sam! See you soon.", t1["history"],
          [chunk(0, "Bye Sam! See you soon.", True, [{"mood": "happy", "intensity": 1}],
                 ["Gesture_Talk"], [EXIT])], [EXIT])
refused = {"ok": False, "kind": "brain_refused", "preview": True, "published": False,
           "error": "The brain's server refused the key (HTTP 401). Check MOXIE_LLM_API_KEY. "
                    "Moxie would have covered it with a stock line.",
           "reason": "The brain's server refused the key (HTTP 401).",
           "detail": {"type": "AuthenticationError", "status": 401, "message": "denied"},
           "reply": {"text": "Hmm, my brain got a little fuzzy.", "chunks": [], "actions": [],
                     "end_turn": False},
           "history": t2["history"], "model_calls": 2, "elapsed_ms": 300,
           "budget": {"per_hour": 40, "remaining": 37}}
print(json.dumps({
    "opts": m.normalize_tryit_options(opts),
    "opts_bedtime": m.normalize_tryit_options(dict(opts, current_module="BEDTIME/default")),
    "t1": m.normalize_tryit(t1),
    "t2": m.normalize_tryit(t2), "refused": m.normalize_tryit(refused),
    "t3": m.normalize_tryit(turn("again", "Again!", [], [chunk(0, "Again!", True, [], [])])),
    "fleet_none": m.normalize_fleet({"ok": True, "app": "moxie-supervisor", "robots": []}),
}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/moxie_server/fleet/ — " + e.message);
}
ok(FIX.opts.ok === true && FIX.opts.modules.length === 2 && FIX.opts.brains.length === 3,
   "fixture: the real normalize_tryit_options produced the card's choices");
ok(FIX.t1.ok === true && FIX.t1.reply.chunks.length === 2 && FIX.t1.history.length === 2,
   "fixture: the real normalize_tryit produced a two-piece answer");
ok(FIX.refused.ok === false && FIX.refused.kind === "brain_refused",
   "fixture: the real normalize_tryit kept the refusal's kind");
ok(FIX.opts_bedtime.current_module === "BEDTIME/default",
   "fixture: the real normalize_tryit_options kept the robot's current activity");

const STATE = { robots: [] };
const site = await serveStatic(STATIC, { extIsHtml: false });
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  defaultViewport: { width: 1280, height: 1400 },
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function clickReal(page, sel) {
  const el = await page.$(sel);
  if (!el) return false;
  await el.click();
  return true;
}

/** The console with no robot, the Moxie tab open, and the 💬 card answered from `queue`.
 *  A `viewport` is set BEFORE navigation: changing `isMobile` later reloads the page.
 *  `ctl.opts` is what `GET /local/tryit` answers; while `ctl.hold` is a promise, a try's
 *  answer waits at the interceptor until it settles (`sendHeld`). */
async function open(mutate, viewport = null) {
  const posts = [];
  const ctl = { opts: FIX.opts, hold: null };
  const queue = [FIX.t1, FIX.t2, FIX.refused, FIX.t3, FIX.t3, FIX.t3];
  const page = await browser.newPage();
  if (viewport) await page.setViewport(viewport);
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const p = new URL(r.url()).pathname;
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === CARD_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(APPJS) });
    if (/\.png$/.test(p)) return r.respond({ status: 200, contentType: "image/png", body: PNG });
    if (p === "/local/quicklogin") return J({ token: "t-fixture", email: "parent@home.lan" });
    if (p === "/local/state") return J(STATE);
    if (p === "/local/fleet") return J(FIX.fleet_none);
    if (p === "/local/tryit" && r.method() === "GET") return J(ctl.opts);
    if (p === "/local/tryit" && r.method() === "POST") {
      posts.push(JSON.parse(r.postData() || "{}"));
      const next = queue.shift() || FIX.t3;
      /* A refusal travels as its real status; the card reads the body either way. */
      const answer = () => {
        if (!next.ok) { aborted.refused++; return J(next, 502); }
        return J(next);
      };
      return ctl.hold ? ctl.hold.then(answer) : answer();
    }
    /* Every other card answers {ok:false}: honest, inert, and outside this suite's claims. */
    if (p.startsWith("/local/") || p.startsWith("/api/"))
      return J({ ok: false, error: "not in this fixture" });
    if (p === "/favicon.ico") aborted.refused++;
    return r.continue();
  });
  await page.goto(site.url + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("#btn-login", { timeout: 10000 });
  await clickReal(page, "#btn-login");
  await page.waitForFunction(
    "!document.querySelector('#tabs').classList.contains('hidden')", { timeout: 10000 });
  /* The desktop sweep clicks the tab for real; the phone layout check only needs the tab
   * open (its subject is the card's width, not the tab bar). */
  if (viewport) await page.evaluate(() => activateTab("moxie"));
  else await clickReal(page, '.tab[data-tab="moxie"]');
  await page.waitForFunction(
    "!document.querySelector('#tryit-card').classList.contains('hidden') && " +
    "document.querySelector('#try-brain').options.length > 0", { timeout: 10000 });
  return { page, posts, errs, aborted, ctl };
}

const readCard = (page) => page.evaluate(() => {
  const q = (s) => document.querySelector(s);
  return {
    text: q("#tryit-card").textContent,
    turns: [...document.querySelectorAll("#try-log .try-turn")].map((t) => t.textContent),
    box: q("#try-text").value,
    status: q("#try-status").textContent,
    moduleDisabled: q("#try-module").disabled,
    brains: [...q("#try-brain").options].map((o) => o.value),
    modules: [...q("#try-module").options].map((o) => o.value),
    brain: q("#try-brain").value,
    module: q("#try-module").value,
  };
});

const shownTurns = (page) =>
  page.evaluate(() => document.querySelectorAll("#try-log .try-turn").length);

/** Replace whatever is in the box with `text`, typed as real keystrokes (`""` keeps it).
 *  The box is selected in the page, not by a triple-click: puppeteer 25 ignores
 *  `clickCount`, so `click({clickCount: 3})` there is a single click that selects
 *  nothing and the typing lands after the old line. */
async function typeOver(page, text) {
  await page.$eval("#try-text", (el) => { el.focus(); el.select(); });
  await page.keyboard.type(text);
}

/** Type `text` into the box and send it with a real click; wait for the turn to render. */
async function send(page, posts, text, { enter = false } = {}) {
  const before = posts.length, shown = await shownTurns(page);
  await typeOver(page, text);
  if (enter) await page.keyboard.press("Enter");
  else await clickReal(page, "#btn-try-send");
  await page.waitForFunction((n) => document.querySelectorAll("#try-log .try-turn").length >= n,
                             { timeout: 8000 }, shown + 1).catch(() => {});
  return posts.length - before;
}

/** Send `text` with its answer HELD at the interceptor. Resolves once the request has left
 *  the page and the card shows it in flight (Send disabled), so the caller's next click
 *  lands mid-try. Returns `land()`: let the answer through and wait until the card has
 *  taken it (Send enabled again). Never throws: a card that misbehaves fails the checks. */
async function sendHeld(page, posts, ctl, text) {
  let release;
  ctl.hold = new Promise((resolve) => { release = resolve; });
  const before = posts.length;
  await typeOver(page, text);
  await clickReal(page, "#btn-try-send");
  await page.waitForFunction(() => document.querySelector("#btn-try-send").disabled,
                             { timeout: 8000 }).catch(() => {});
  for (let i = 0; i < 400 && posts.length === before; i++) await sleep(10);
  return async () => {
    ctl.hold = null;
    release();
    await page.waitForFunction(() => !document.querySelector("#btn-try-send").disabled,
                               { timeout: 8000 }).catch(() => {});
  };
}

const lastPost = (posts) => posts[posts.length - 1] || {};

async function sweep(C, mutate) {
  const { page, posts, errs, aborted, ctl } = await open(mutate);
  try {
    /* 1 — no robot, and still a card; labelled a preview */
    let card = await readCard(page);
    C.ok(/preview/i.test(card.text) && /nothing is sent to a robot/i.test(card.text),
         "1: the card must say it is a preview and that nothing reaches a robot");
    C.eq(JSON.stringify(card.brains), JSON.stringify(["", "llm", "echo"]),
         "1: who answers = this appliance's brain, then the others (its own not repeated)");
    C.eq(JSON.stringify(card.modules), JSON.stringify(["", "FREE_CHAT/default", "BEDTIME/default"]),
         "1: the activities are the installed conversations");
    C.eq(card.moduleDisabled, false, "1: the content brain reads activities");

    /* 2 — typing alone never reaches the brain, nor does the Enter that commits an IME
     *     composition (a send starts in the keydown handler itself: Send goes disabled) */
    await page.click("#try-text");
    await page.keyboard.type("can we draw?");
    const composing = await page.$eval("#try-text", (el) => {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true,
                                                      bubbles: true, cancelable: true }));
      return document.querySelector("#btn-try-send").disabled;
    });
    C.eq(composing, false, "2: the Enter that commits an IME composition must not send");
    await sleep(900);                         // longer than any debounce worth having
    C.eq(posts.length, 0, "2: typing alone must never call the brain");

    /* 2+3+4 — one click, one call, carrying an empty session; the answer rendered */
    await page.$eval("#try-text", (e) => { e.value = ""; });
    C.eq(await send(page, posts, "can we draw?"), 1, "2: one click must make exactly one call");
    C.eq(JSON.stringify(posts[0]),
         JSON.stringify({ speech: "can we draw?", history: [], device_id: "", brain: "",
                          module: "", nickname: "" }),
         "3: the first send carries the line and an empty session");
    card = await readCard(page);
    const first = card.turns[0] || "";
    C.ok(first.includes("can we draw?") && first.includes("Hi Sam! I love that idea.")
         && first.includes("What first?"), "4: both pieces of her answer are shown");
    C.ok(first.includes("face: happy (1)") && first.includes("moves: Self")
         && first.includes("moves: Celebrate"), "4: her face and moves are read out");
    C.eq(card.box, "", "4: a delivered line leaves the box");
    C.ok(/1 model call/.test(card.status) && /nothing was sent to a robot/.test(card.status),
         "4: the receipt says what the try cost and that it stayed a preview");

    /* 3 — Enter sends too, carrying the session the first answer returned */
    C.eq(await send(page, posts, "bye Moxie", { enter: true }), 1, "3: Enter sends once");
    C.eq(JSON.stringify(posts[1] && posts[1].history), JSON.stringify(FIX.t1.history),
         "3: the second send carries the session the first answer returned");
    card = await readCard(page);
    C.ok((card.turns[1] || "").includes("Moxie would end this activity"),
         "4: an action is shown as what she would do");

    /* 5 — a refused brain: its sentence, her stock line, the line kept, no session move */
    C.eq(await send(page, posts, "are you there?"), 1, "5: the refused send is one call");
    card = await readCard(page);
    const bad = card.turns[2] || "";
    C.ok(bad.includes("HTTP 401") && bad.includes("She would have said"),
         "5: a refusal shows why, and the line the child would have heard");
    C.eq(card.box, "are you there?", "5: a refused line stays in the box to send again");
    C.eq(await send(page, posts, ""), 1, "5: resending is one call");
    C.eq(JSON.stringify(posts[3] && posts[3].history), JSON.stringify(FIX.t2.history),
         "5: a refused try must not advance the session");

    /* 6 — start over, and a change of who answers, both start an empty session */
    await clickReal(page, "#btn-try-reset");
    card = await readCard(page);
    C.eq(card.turns.length, 0, "6: Start over empties the transcript");
    C.eq(await send(page, posts, "again"), 1, "6: a send after Start over is one call");
    C.eq(JSON.stringify(posts[4] && posts[4].history), JSON.stringify([]),
         "6: after Start over the session is empty");
    await page.select("#try-brain", "llm");
    card = await readCard(page);
    C.eq(card.turns.length, 0, "6: picking another brain starts a new session");
    C.eq(card.moduleDisabled, true, "6: a brain that does not read activities disables them");
    C.eq(await send(page, posts, "hi"), 1, "6: the next send is one call");
    const last = posts[5] || {};
    C.ok(last.brain === "llm" && last.module === "" && JSON.stringify(last.history) === "[]",
         "6: it names the picked brain, no activity, and an empty session");

    /* 7 — while an answer is still on its way, a new session wins: the late answer is
     *     neither shown nor carried, and the next send carries an empty session */
    // (a) Start over, with a session behind it (the "hi" exchange of 6)
    let land = await sendHeld(page, posts, ctl, "one more");
    C.eq((lastPost(posts).history || []).length, 2, "7a: the held send carried a session");
    await clickReal(page, "#btn-try-reset");
    await land();
    card = await readCard(page);
    C.eq(card.turns.length, 0, "7a: an answer that lands after Start over is not shown");
    C.ok(/^Started over\./.test(card.status) && /set aside/.test(card.status),
         `7a: the card says the answer on its way is set aside — "${card.status}"`);
    C.eq(await send(page, posts, "fresh start"), 1, "7a: the next send is one call");
    C.eq(lastPost(posts).speech, "fresh start",
         "7a: it carries the line typed over the set-aside one, and only that");
    C.eq(JSON.stringify(lastPost(posts).history), "[]",
         "7a: after Start over mid-try, the next send carries an empty session");

    // (b) another brain, with a session behind it ("fresh start")
    land = await sendHeld(page, posts, ctl, "and another");
    C.eq((lastPost(posts).history || []).length, 2, "7b: the held send carried a session");
    await page.select("#try-brain", "");
    await land();
    card = await readCard(page);
    C.eq(card.turns.length, 0, "7b: an answer for the old brain is not shown");
    C.ok(/who answers changed/.test(card.status) && /set aside/.test(card.status),
         `7b: the card says the answer on its way is set aside — "${card.status}"`);
    C.eq(await send(page, posts, "hello again"), 1, "7b: the next send is one call");
    C.ok(lastPost(posts).brain === "" && JSON.stringify(lastPost(posts).history) === "[]",
         "7b: the next send names the new brain and carries an empty session");

    // (c) another activity on the very FIRST line: the answer on its way is all there is
    await clickReal(page, "#btn-try-reset");
    land = await sendHeld(page, posts, ctl, "first line");
    C.eq(JSON.stringify(lastPost(posts).history), "[]", "7c: the held send opened a session");
    await page.select("#try-module", "BEDTIME/default");
    await land();
    C.eq((await readCard(page)).turns.length, 0, "7c: an answer for the old activity is not shown");
    C.eq(await send(page, posts, "bedtime?"), 1, "7c: the next send is one call");
    C.ok(lastPost(posts).module === "BEDTIME/default"
         && JSON.stringify(lastPost(posts).history) === "[]",
         "7c: the next send names the new activity and carries an empty session");

    // (d) another robot (a refresh with a new device id), with a session behind it
    land = await sendHeld(page, posts, ctl, "robot time");
    await page.evaluate(() => refreshTryit("d_robot"));
    await land();
    card = await readCard(page);
    C.eq(card.turns.length, 0, "7d: an answer for the old robot is not shown");
    C.ok(/another robot/.test(card.status) && /set aside/.test(card.status),
         `7d: the card says the answer on its way is set aside — "${card.status}"`);
    C.eq(await send(page, posts, "are you my robot?"), 1, "7d: the next send is one call");
    C.ok(lastPost(posts).device_id === "d_robot"
         && JSON.stringify(lastPost(posts).history) === "[]",
         "7d: the next send names the new robot and carries an empty session");
    await page.evaluate(() => refreshTryit(""));      // back to no robot: a new session

    /* 8 — a refresh keeps the parent's pick and the session; a line typed meanwhile stays */
    await page.select("#try-module", "");             // the parent's pick: no activity
    land = await sendHeld(page, posts, ctl, "a session");
    await typeOver(page, "my next line");
    await land();
    card = await readCard(page);
    C.eq(card.turns.length, 1, "8: an answer for this session is shown");
    C.eq(card.box, "my next line", "8: a line typed while she was answering stays in the box");
    C.eq(card.module, "", "8: the parent's pick is no particular activity");
    ctl.opts = FIX.opts_bedtime;                       // the robot moved on to Bedtime
    await page.evaluate(() => refreshTryit(""));
    card = await readCard(page);
    C.eq(card.module, "", "8: a refresh must not move the parent's activity pick");
    C.eq(card.turns.length, 1, "8: a refresh that changes nothing keeps the session");
    C.eq(await send(page, posts, ""), 1, "8: sending the typed line is one call");
    C.ok(lastPost(posts).speech === "my next line" && lastPost(posts).module === ""
         && (lastPost(posts).history || []).length === 2,
         "8: it carries the kept line, the parent's pick and the session");

    C.eq(posts.length, 16, "every call came from a click or Enter: sixteen sends, sixteen calls");
    const left = notable(errs, aborted);
    C.eq(left.length, 0, `the page raised no unexplained errors — ${left.slice(0, 3).join(" | ")}`);
  } finally {
    await page.close();
  }
}

/* ---- the honest run ------------------------------------------------------------- */
await sweep({ ok, eq });

/* ---- a phone: the card must fit (long brain labels sit in two side-by-side pickers) -- */
{
  const { page } = await open(null, { width: 390, height: 844, isMobile: true,
                                      hasTouch: true });
  try {
    const fit = await page.evaluate(() => {
      const card = document.querySelector("#tryit-card");
      return { width: card.getBoundingClientRect().width,
               card: card.scrollWidth - card.clientWidth,
               page: document.documentElement.scrollWidth - window.innerWidth };
    });
    ok(fit.width > 0 && fit.width < 390,
       `phone: the card must be on screen to be measured (width ${fit.width}px)`);
    ok(fit.card <= 1, `phone: the card must not overflow sideways (by ${fit.card}px)`);
    ok(fit.page <= 1, `phone: the page must not scroll sideways (by ${fit.page}px)`);
  } finally { await page.close(); }
}

/* ---- TEETH ---------------------------------------------------------------------- */
const TEETH = {
  "sends as you type": (src) => src.replace(
    "{ const t=$('#try-text'); if(t) t.onkeydown=",
    "{ const t=$('#try-text'); if(t) t.oninput=()=>trySend(); if(t) t.onkeydown="),
  "forgets the session": (src) => src.replace("tryHistory=r.history||[];", "void 0;"),
  "lets a late answer back in": (src) => src.replace("if(session!==trySession) return;", ""),
  "forgets a first line on its way": (src) => src.replace(
    "if(tryHistory.length || tryBusy){", "if(tryHistory.length){"),
  "sends on an IME Enter": (src) => src.replace(
    "e.key==='Enter' && !e.isComposing && e.keyCode!==229", "e.key==='Enter'"),
  "moves the activity on a refresh": (src) => src
    .replace("some(o=>o.value===mkeep) && (mkeep || !fresh)) mod.value=mkeep;",
             "some(o=>o.value===mkeep) && mkeep) mod.value=mkeep;")
    .replace("is a change too.\n  tryChanged();",
             "is a change too.\n  tryPick=trySelection(); renderTryNote();"),
  "clears a line typed meanwhile": (src) => src.replace(
    "if(box.value.trim()===speech) box.value='';", "box.value='';"),
};
for (const [name, mutate] of Object.entries(TEETH)) {
  ok(mutate(APPJS) !== APPJS, `teeth: "${name}" must actually change js/tryit.js`);
  const C = makeChecks();
  try { await sweep(C, mutate); }
  catch (e) { C.fails.push(`the sweep threw: ${e.message}`); }
  ok(C.fails.length > 0, `teeth: a card that ${name} must redden the sweep`);
}
await browser.close();
site.close();
finish(LABEL, { fails, count });
