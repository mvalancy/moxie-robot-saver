"""Remove each guard the Turnstile bot control rests on, and check its test goes red.

*"A test for every fix, proven in BOTH directions."* A green `sim/test_turnstile.mjs` proves
the guards are **present**; this proves they are **load-bearing**. Same shape as
`hardening_mutation_check.py` / `ext_mutation_check.py` / `brain_mutation_check.py`, and it
exists for the same reason every one of those did.

Run it by hand after touching `functions/api/_lib/turnstile.js`, the Turnstile step in
`functions/api/chat.js`, or `sim/web/turnstile.js`:

    python3 sim/tools/turnstile_mutation_check.py

Every row must say "caught". A row that says NOT CAUGHT means the assertion passes with the
guard deleted, i.e. it is not testing what its name claims.

**It takes about six minutes**, and both halves of that are deliberate: the suite spends ~8 s
per run waiting out `sim/web/turnstile.js`'s real 8 s deadline (§9 asserts on a mint that has
NOT resolved, so the deadline is the thing under test and cannot be shortened from the test),
and row D3e is caught by hanging, which costs `MUTATION_TIMEOUT_S` on its own. Pass a row name
to re-check one row in ~9 s.

=============================================================================
WHY THIS TABLE IS DIFFERENT FROM THE OTHER FIVE, AND WHY IT IS STRICTER.

The others run `pytest <file> -k <selector>` and treat ANY non-zero exit as "caught". That
is too weak for a security control: a mutation that broke some unrelated assertion would
read as caught while the guard it targeted was never actually exercised. Here the runner is
`node sim/test_turnstile.mjs`, which prints one `FAIL: <label>` line per failed check, and
the sixth column is a substring that must appear IN A FAILING LABEL. So a row is caught only
when **the check that names that guard** is the one that reddened.

That distinction is not theoretical. The first draft of the fail-open row (`unreachable` ->
refuse) reddened this suite through the *slot-release* block, because a refusal there also
changes the in-flight arithmetic — it would have "passed" while proving nothing about
fail-open at all.
=============================================================================

THE SIX PROPERTIES EVERY ROW BELOW MAPS BACK TO, in the brief's own terms:

  · the THREE MANDATORY CHECKS — `success`, `action`, `hostname` — each deleted separately,
    because a table that deletes them together cannot tell which one the suite can see;
  · D2, THE SLOT RELEASE. Two mutations: return the refusal from OUTSIDE the `try` (the
    plausible edit — it reads as tidier), and neuter `release()` itself. A leaked slot fails
    CLOSED, which is the direction this project has already rejected a design over;
  · D3's SPLIT, BOTH HALVES. Fail-closed turned into fail-open (the control removed) and
    fail-open turned into fail-closed (the demo taken down by a third-party outage). Neither
    shows up in a green suite, which is exactly why both are here;
  · D1's ORDER — move the check in front of the safety floor, and in front of `admit()`;
  · D4's CONFIG GATE — enforce with no secret, and skip when one is present;
  · the CLIENT's per-send freshness — drop the `reset()` before `execute()`, which is the
    one-character version of "reuse the same token", and refuses every turn after the first.

=============================================================================
**IT NEVER TOUCHES YOUR CHECKOUT.** Every mutation is applied inside a THROWAWAY COPY.

The first version rewrote the live worktree files and restored them in a `finally`. Two
things went wrong with that, both observed rather than imagined:

  · A RUN THAT DOES NOT REACH ITS `finally` LEAVES A DISABLED SECURITY CHECK IN THE TREE.
    At one review's start `git status` showed `functions/api/_lib/turnstile.js` dirty with
    row C2 still applied — mandatory check 2 replaced by `if (false)`, in the tree the
    orchestrator was about to push. `finally` does not run on `SIGKILL`, and anything that
    reads the working tree in that window ships it: `git commit -a`, `git add -A`, a
    `wrangler pages dev`, a build.
  · TWO RUNS AT ONCE POISON EACH OTHER. With a second session running the suite, three
    consecutive runs failed on three DIFFERENT rows, because `sim/web/turnstile.js` was
    being rewritten underneath them. A red security suite with no defect behind it is the
    worst kind of noise to hand a reviewer.

So `main()` hardlink-copies `functions/` and `sim/` into a fresh temporary directory
(~0.2 s for this repo, because hardlinks copy metadata and not bytes), replaces the few
files the table mutates with REAL copies so that no write can ever reach the original
inode through a shared one, and runs `node` there. The checkout is never opened for
writing at all, two runs cannot see each other, and a `kill -9` leaves nothing behind but
a directory under `/tmp`.
=============================================================================

Nothing here changes the tree permanently, because nothing here changes the tree.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile

WT = pathlib.Path(__file__).resolve().parents[2]

#: The subtrees the suites need. `sim/test_turnstile.mjs` computes its repo root as
#: `sim/..`, imports `functions/api/**` and reads `sim/web/**` as text; nothing else in the
#: repo is touched by any suite in the table.
TREES = ("functions", "sim")

#: The files the table mutates, as `WT / ...` paths — the shape `sim/tests/
#: test_mutation_tables.py` reads with `ast` so it can check every anchor still resolves
#: against the REAL checkout and that no mutation has been committed into it. `main()`
#: maps each one into its throwaway copy with `.relative_to(WT)`; nothing here is ever
#: opened for writing.
LIB = WT / "functions/api/_lib/turnstile.js"
LIMITS = WT / "functions/api/_lib/limits.js"
CHAT = WT / "functions/api/chat.js"
TRANSCRIBE = WT / "functions/api/transcribe.js"
HEALTH = WT / "functions/api/health.js"
SPEECH = WT / "functions/api/speech.js"
ENV = WT / "functions/api/_lib/env.js"
CLIENT = WT / "sim/web/turnstile.js"
TRANSPORT = WT / "sim/web/cloud-transport.js"
MIC = WT / "sim/web/mic.js"
HEADERS = WT / "sim/web/_headers"

#: The suite most of these guards live in. Every row runs it whole — it takes under a
#: second, so there is nothing to gain from narrowing it, and running it whole is what lets
#: the selector column check that the RIGHT assertion reddened.
SUITE = "sim/test_turnstile.mjs"

#: ...and the one that owns the SEND PATH's behaviour rather than the control's: whether a
#: page whose token could not be minted degrades like every other failure on it, or repeats
#: one sentence under a LIVE badge. Same runner, same rule about the selector.
UX = "sim/test_cloud_transport.mjs"

#: Seconds one mutated run may take before it is treated as caught-by-hanging. Generous:
#: the suite is ~1 s, and a mutation that makes it hang (a promise that never settles in
#: the client half) is caught — but only if something ends it.
MUTATION_TIMEOUT_S = 120

MUTATIONS = [
    # ---- the three mandatory checks, one at a time -----------------------------
    ("C1  accept a verdict whose success is FALSE (the control, removed)", LIB,
     "  if (body.success !== true) {",
     "  if (false) {",
     SUITE, "success:false REFUSES"),
    ("C2  stop comparing the action (any widget's token becomes spendable here)", LIB,
     '  if (String(body.action || "") !== wantAction) {',
     "  if (false) {",
     SUITE, "ANOTHER action"),
    ("C3  stop checking the hostname (a token solved anywhere is accepted)", LIB,
     "  if (!hostAllowed(cfg, request, body.hostname)) {",
     "  if (false) {",
     SUITE, "foreign hostname"),
    # The plausible WRONG version of check 3 rather than its deletion: suffix matching
    # reads as more permissive-in-the-right-way and quietly accepts `evil-<ourhost>`.
    ("C3b hostname compared with endsWith instead of an exact match", LIB,
     "  if (configured.length) return configured.includes(got);",
     "  if (configured.length) return configured.some((h) => got.endsWith(h));",
     SUITE, "the configured match is exact too"),
    # ...and the OTHER plausible wrong version: an absent hostname read as "unknown, allow".
    ("C3c an empty hostname treated as 'nothing to check, allow'", LIB,
     '  if (!got) return false;',
     "  if (!got) return true;",
     SUITE, "EMPTY hostname"),

    # ---- D2: the concurrency slot ----------------------------------------------
    # THE PLAUSIBLE EDIT. Hoisting the check above `try` reads as tidier — the token is
    # "part of admission", after all — and it silently leaks a slot on every refusal.
    ("D2  return the refusal from OUTSIDE the try, so the finally never runs", CHAT,
     '    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD], "chat");\n'
     "    if (!bot.ok) {\n"
     "      return spentNothing(bot.reason);\n"
     "    }",
     '    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD], "chat");\n'
     "    if (!bot.ok) {\n"
     "      slot.__leak = true;\n"
     "      slot.release = () => {};\n"
     "      return spentNothing(bot.reason);\n"
     "    }",
     SUITE, "in-flight count is back to ZERO"),
    # THE NEGATIVE CONTROL FOR D2's ASSERTION, and it is a different claim from D2 itself:
    # D2 proves the route calls `release()`, this proves the test can SEE a slot that was
    # not given back. Without it, "the in-flight count is back to zero" is equally
    # consistent with "release works" and "the counter is always zero".
    ("D2b `release()` runs but hands the count back to nobody (the teeth for D2)", LIMITS,
     "  state.inflight[route] = Math.max(0, (state.inflight[route] || 0) - 1);",
     "  state.inflight[route] = Math.max(0, (state.inflight[route] || 0) - 0);",
     SUITE, "in-flight count is back to ZERO"),

    # ---- D3: the split, in both directions -------------------------------------
    ("D3  FAIL CLOSED turned into fail open: a refused challenge is let through", LIB,
     "    return ours\n"
     "      ? { ok: false, reason: \"turnstile_misconfigured\", outcome: record(\"misconfigured\") }\n"
     "      : { ok: false, reason: \"turnstile_failed\", outcome: record(\"failed\") };",
     "    return { ok: true, reason: null, outcome: record(\"failed\") };",
     SUITE, "success:false REFUSES"),
    ("D3b FAIL OPEN turned into fail closed: a Cloudflare outage kills the demo", LIB,
     "  } catch {\n"
     "    // A timeout or an unreachable endpoint. The error's message is not inspected at all —\n"
     "    // an error string can carry the URL, and there is nothing here worth the risk.\n"
     "    return { ok: true, reason: null, outcome: record(\"unreachable\") };\n"
     "  }",
     "  } catch {\n"
     "    return { ok: false, reason: \"turnstile_failed\", outcome: record(\"unreachable\") };\n"
     "  }",
     SUITE, "the endpoint is unreachable: the turn is still served"),
    ("D3c a non-200 from siteverify read as a verdict of 'no' rather than as transport", LIB,
     "  if (!res.ok) {",
     "  if (false) {",
     SUITE, "PARSES as a failed verdict: the turn is still served"),
    ("D3d Cloudflare's own internal-error read as a failed challenge", LIB,
     "  if (codes.some((c) => THEIR_FAULT_CODES.includes(c))) {",
     "  if (false) {",
     SUITE, "internal-error: the turn is still served"),
    # A siteverify that never answers holds a CONCURRENCY SLOT. With no deadline the route
    # hangs until its own 20 s upstream timeout, so this row is caught by HANGING — which
    # the runner reports as caught and says so, because "it never finished" is a different
    # fact from "it went red" and the next reader should not have to guess which.
    ("D3e no deadline on the siteverify call at all (a hung endpoint holds a slot)", LIB,
     "      signal: AbortSignal.timeout(cfg.turnstileTimeoutMs),",
     "      signal: undefined,",
     SUITE, "our own deadline fired"),

    # ---- D1: the order ---------------------------------------------------------
    # A hard-blocked utterance buying a round trip to prove the visitor is human before
    # being told no — and, worse, `admit()` no longer standing between a flood and
    # siteverify. Expressed by moving the check ABOVE the safety floor.
    ("D1  the bot check moved IN FRONT of the free safety floor", CHAT,
     "    const verdict = assess(text);\n    if (verdict.blocked) {",
     '    const __bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD], "chat");\n'
     "    if (!__bot.ok) return spentNothing(__bot.reason);\n"
     "    const verdict = assess(text);\n    if (verdict.blocked) {",
     SUITE, "(the safety floor): ZERO siteverify calls"),
    ("D1b a missing token verified anyway, turning the route into an amplifier", LIB,
     '  if (!response) return { ok: false, reason: "turnstile_failed", outcome: record("no_token") };',
     "  if (false) return null;",
     SUITE, "no field at all is refused"),

    # ---- D4: the config gate ---------------------------------------------------
    ("D4  enforce even with no secret configured (every fork and preview refused)", LIB,
     '  if (!cfg || !cfg.turnstile) return { ok: true, reason: null, outcome: record("skipped") };',
     '  if (false) return { ok: true, reason: null, outcome: record("skipped") };',
     SUITE, "ZERO siteverify calls: the check is a no-op"),
    ("D4b half a pair enforces, so a sitekey-less deployment refuses everyone", ENV,
     "  cfg.turnstile = !!(turnstileSecret && turnstileSitekey);",
     "  cfg.turnstile = !!(turnstileSecret || turnstileSitekey);",
     SUITE, "enforcement is off — half a pair enforces nothing"),
    ("D4c half a pair is not reported as missing, so the route runs half-configured", ENV,
     '    missing.push("DEMO_TURNSTILE_SITEKEY");',
     "    void 0;",
     SUITE, "the deployment reads as UNCONFIGURED"),

    # ---- D5: how the browser learns the sitekey --------------------------------
    ("D5  publish the sitekey even when the control is not enforced", ENV,
     '  return cfg && cfg.turnstile ? String(cfg.turnstileSitekey || "") : "";',
     '  return String((cfg && cfg.turnstileSitekey) || "");',
     SUITE, "a widget the server will not check must never be rendered"),
    ("D5b the secret becomes enumerable, so JSON.stringify(cfg) carries it", ENV,
     '    ["turnstileSecret", turnstileSecret],',
     "    ",
     SUITE, "the secret is DEFINED on the config"),

    # ---- D8: the operator's diagnosis ------------------------------------------
    ("D8  one reason for both faults: a wrong secret indistinguishable from a bad token", LIB,
     "    const ours = codes.some((c) => OUR_FAULT_CODES.includes(c));",
     "    const ours = false;",
     SUITE, "invalid-input-secret maps to turnstile_misconfigured"),
    # WHAT A "HELPFUL" DIAGNOSTIC ACTUALLY LOOKS LIKE: the codes appended to the reason,
    # which is the shortest path from Cloudflare's reply to a visitor's browser. It is
    # caught by the CLOSED REASON SET rather than by a scrub — `envelope.js` coerces an
    # unrecognised reason to `bad_request`, so the codes never ship AND the diagnosis is
    # destroyed. That is the right failure and this row is what proves the coercion is
    # load-bearing here.
    ("D8b the raw error codes appended to the reason (a 'helpful' diagnostic)", LIB,
     '      ? { ok: false, reason: "turnstile_misconfigured", outcome: record("misconfigured") }',
     '      ? { ok: false, reason: "turnstile_misconfigured " + codes.join(","), outcome: record("misconfigured") }',
     SUITE, "a misconfiguration is diagnosable"),

    # ---- D6: the client mints a FRESH token per send ---------------------------
    # THE INTERACTIVE PATH, in both of its failure directions. Trusting `getResponse()`
    # blindly replays the last spent token (the server refuses it, only ever on the turn
    # AFTER a challenge); never consulting it resets a solved challenge and an interactive
    # visitor can never complete a turn at all.
    ("D6f trust a held token blindly — replays the one already spent", CLIENT,
     "        if (held && held !== w.spent) { stats.reused++; done(held); return; }",
     "        if (held) { stats.reused++; done(held); return; }",
     SUITE, "a spent token is NEVER handed out again"),
    ("D6g never look at a held token — an interactive solve is reset away for ever", CLIENT,
     "          held = typeof t.getResponse === \"function\" ? String(t.getResponse(w.id) || \"\") : \"\";",
     '          held = "";',
     SUITE, "spends the token the widget was left holding"),
    ("D6  drop the reset() before execute(): the same token is replayed every turn", CLIENT,
     "            if (typeof t.reset === \"function\") t.reset(w.id);",
     "            void 0;",
     SUITE, "reset() before EVERY execute"),
    ("D6b the widget rendered with a visible checkbox instead of interaction-only", CLIENT,
     '          appearance: "interaction-only",',
     '          appearance: "always",',
     SUITE, "NO checkbox in front of a child"),
    ("D6c the challenge runs at render time, so a token cannot be minted per send", CLIENT,
     '          execution: "execute",',
     '          execution: "render",',
     SUITE, "the challenge runs when we ASK"),
    ("D6d a token that could not be minted resolves as \"\" — a SILENT unprotected send", CLIENT,
     "      return ready ? mint(w) : null;",
     '      return ready ? mint(w) : "";',
     SUITE, "a script that cannot load resolves null"),
    ("D6e the client's action table drifts from the server's", CLIENT,
     '  var ACTIONS = { chat: "chat", transcribe: "transcribe" };',
     '  var ACTIONS = { chat: "chat-turn", transcribe: "transcribe" };',
     SUITE, "equals the server's TURNSTILE_ACTIONS"),
    ("D6h a FAILED script load is memoised, disabling every turn for the page's life", CLIENT,
     "      if (!loaded) loading = null;",
     "      void loaded;",
     SUITE, "REQUESTS THE SCRIPT AGAIN"),
    # The other half of the same bug: a load that SUCCEEDED but arrived after the deadline,
    # or one whose `onload` never fired at all. The deadline answering a flat `false`
    # instead of `!!api()` writes off a script that is demonstrably on the page.
    ("D6i the load deadline answers `false` instead of asking whether the API is here", CLIENT,
     "        if (!settled) { if (!api()) stats.scriptErrors++; done(!!api()); }",
     "        if (!settled) { stats.scriptErrors++; done(false); }",
     SUITE, "arrives with no onload is USED"),
    # ...and the line that makes "stop asking" different from "give up": once the tag budget
    # is spent, an API that turned up by any other means must still be used.
    ("D6n the memo and the tag budget consulted BEFORE `window.turnstile`", CLIENT,
     "    if (api()) return Promise.resolve(true);\n    if (loading) return loading;",
     "    if (loading) return loading;",
     SUITE, "IS used, budget spent or not"),
    ("D6j a send during a live challenge RESETS it out from under the visitor", CLIENT,
     "          if (w.outstanding) {",
     "          if (false) {",
     SUITE, "does NOT reset the live challenge"),
    ("D6k an unknown action defaults to chat, so the mic spends a typed turn's token", CLIENT,
     '    if (!w) { stats.unknownAction++; return Promise.resolve(null); }',
     '    if (!w) { stats.unknownAction++; w = slot("chat"); action = "chat"; }',
     SUITE, "getToken() resolves null, not a token"),
    # ---- the holder's geometry, which is a UX defect with the same shape as a security
    # one: the control the visitor needs becomes untappable and nothing errors.
    ("D6l the widget holder anchored to the bottom, on top of the controls", CLIENT,
     '    "#turnstile-holder{position:fixed;inset:0;z-index:210;display:flex;align-items:center;" +\n'
     '    "justify-content:center;gap:8px;pointer-events:none}" +',
     '    "#turnstile-holder{position:fixed;left:50%;bottom:16px;z-index:210;display:flex;" +\n'
     '    "justify-content:center;gap:8px;pointer-events:auto}" +',
     SUITE, "NOT anchored to the bottom"),
    ("D6m the holder layer takes pointer events, so an empty box swallows taps", CLIENT,
     "justify-content:center;gap:8px;pointer-events:none}\" +",
     "justify-content:center;gap:8px;pointer-events:auto}\" +",
     SUITE, "cannot swallow a tap"),

    # ---- C2 loosened rather than deleted --------------------------------------
    # Row C2 deletes the comparison. These two WEAKEN it, which is the edit somebody
    # actually makes — and both passed the whole suite green before §3 grew the cases that
    # catch them. Measured with (a) applied: a verdict of `action: "chat-newsletter"` was
    # SERVED, with a real gateway call.
    ("C2b the action compared with startsWith instead of an exact match", LIB,
     '  if (String(body.action || "") !== wantAction) {',
     "  if (!String(body.action || \"\").startsWith(wantAction)) {",
     SUITE, "is a PREFIX of ours"),
    ("C2c the action compared case-insensitively and trimmed", LIB,
     '  if (String(body.action || "") !== wantAction) {',
     '  if (String(body.action || "").trim().toLowerCase() !== wantAction) {',
     SUITE, "in the WRONG CASE"),

    # ---- the 400 that a wrong secret really produces --------------------------
    # THE BLOCKER THIS ROW EXISTS FOR. `invalid-input-secret` and `missing-input-secret`
    # come back as HTTP 400, so a bare fail-open on `!res.ok` switched the entire control
    # off — silently, permanently — for a secret wrong by one character, and made
    # `turnstile_misconfigured` unreachable for the exact fault it was designed to report.
    ("C4  a wrong secret's 400 read as a transport failure (the control, switched off)", LIB,
     "    if (failCodes.some((c) => OUR_FAULT_CODES.includes(c))) {",
     "    if (false) {",
     SUITE, "is OUR fault and REFUSES"),
    # ...and the OVER-correction, which is the other way to get this wrong: believing every
    # non-2xx body as a verdict turns a Cloudflare 5xx into a refusal for every visitor.
    ("C4b every non-2xx treated as OUR fault (a Cloudflare 5xx kills the demo)", LIB,
     "    const failCodes = codesOf(await readJsonBody(res));\n"
     "    if (failCodes.some((c) => OUR_FAULT_CODES.includes(c))) {",
     "    const failCodes = codesOf(await readJsonBody(res));\n"
     "    if (true) {",
     SUITE, "name the VISITOR's token"),
    # Targeted at `actionFor`'s fallback rather than at `verify`'s guard, because the
    # guard's variable is a `const` and assigning to it throws — which exits node before a
    # single named red is printed, and an unattributable crash is not a caught mutation.
    ("C5  an unknown route name defaults to the chat action instead of refusing", LIB,
     '  return Object.prototype.hasOwnProperty.call(TURNSTILE_ACTIONS, key) ? TURNSTILE_ACTIONS[key] : "";',
     "  return TURNSTILE_ACTIONS[key] || TURNSTILE_ACTIONS.chat;",
     SUITE, "REFUSES rather than guessing"),

    # ---- T: the ears, which the first version of this slice left wide open ----
    ("T1  the ears verify nothing (the curl loop that used to be served)", TRANSCRIBE,
     '    const bot = await verifyTurnstile(cfg, request, tokenFromHeader(request), "transcribe");\n'
     "    if (!bot.ok) return spentNothing(bot.reason);",
     "    void 0;",
     SUITE, "is REFUSED by the ears"),
    ("T2  the ears accept the CHAT action, so a typed token buys 15 s of STT", TRANSCRIBE,
     'tokenFromHeader(request), "transcribe");',
     'tokenFromHeader(request), "chat");',
     SUITE, "a CHAT token presented to the ears is refused"),
    ("T3  the ears never read the token header, so every clip is tokenless", LIB,
     "    return String((request && request.headers && request.headers.get(TOKEN_HEADER)) || \"\").trim();",
     '    return "";',
     SUITE, "a clip with a valid TRANSCRIBE token is served"),

    # ---- R: the refund, which is what makes a refusal actually free ----------
    # THE ATTACK THIS ROW EXISTS FOR: 200 tokenless requests, all correctly refused, zero
    # gateway calls — and the shared hourly budget gone, so the next real visitor gets
    # `budget_exhausted` and a SCRIPTED page. A free drain in place of a paid one.
    ("R1  a Turnstile refusal keeps the units admission charged (the free drain)", CHAT,
     "    const spentNothing = (reason, extra) => {\n      slot.refundBudget();",
     "    const spentNothing = (reason, extra) => {",
     SUITE, "leaves the SHARED unit budget exactly where it found it"),
    ("R2  the refund is not idempotent, so it credits away another request's charge", LIMITS,
     "      if (refunded) return; // idempotent: a double refund would credit units never spent",
     "      void 0;",
     SUITE, "does NOT credit away the second's charge"),
    # The opposite error, and it is the one that costs real money: refunding a request that
    # DID call the gateway means the budget stops describing what was spent.
    ("R3  the upstream-failure path refunds too, so real spend is credited back", CHAT,
     "    const upstream = await callGateway(cfg, buildUpstreamBody(cfg, turns, text));\n"
     "    if (!upstream.ok) {\n      return refusal(cfg, \"chat\", upstream.reason, {",
     "    const upstream = await callGateway(cfg, buildUpstreamBody(cfg, turns, text));\n"
     "    if (!upstream.ok) {\n      slot.refundBudget();\n      return refusal(cfg, \"chat\", upstream.reason, {",
     SUITE, "its units stay spent"),
    ("R4  the ears' refusals keep their charge (the same drain, 2 units at a time)", TRANSCRIBE,
     "    const spentNothing = (reason, extra) => {\n      slot.refundBudget();",
     "    const spentNothing = (reason, extra) => {",
     SUITE, "which is what the ears cost"),
    ("R6  the VOICE keeps its charge, so the same drain works with no token at all", SPEECH,
     "    const spentNothing = (reason, extra) => {\n      slot.refundBudget();",
     "    const spentNothing = (reason, extra) => {",
     SUITE, "the same drain needs no token here"),
    ("R5  the safety floor keeps its charge, contradicting its own doc comment", CHAT,
     "      slot.refundBudget();\n      return blocked(cfg, slot, verdict);",
     "      return blocked(cfg, slot, verdict);",
     SUITE, "a hard-blocked utterance leaves the shared budget untouched"),

    # ---- H: how the sitekey reaches the browser ------------------------------
    # THE ONE GUARD IN THIS SLICE THAT HAD NO TEST AT ALL. `/api/health` is the browser's
    # only source of the sitekey; deleting this line left ELEVEN suites green while the
    # live demo rendered no widget and refused 100% of turns under a LIVE badge.
    # The two copies of the sitekey that are NOT a delivery path but ARE the envelope's
    # shape. `str.replace(..., 1)` would hit the success shape first, so each anchor
    # carries the line after it to make it unique.
    ("D5c the sitekey dropped from the REFUSAL envelope", CHAT,
     "      turnstile: publicTurnstile(cfg),\n      messages: [],",
     '      turnstile: "",\n      messages: [],',
     SUITE, "a REFUSAL envelope carries the sitekey too"),
    ("D5d the sitekey dropped from the BLOCKED envelope", CHAT,
     "      turnstile: publicTurnstile(cfg),\n      messages,",
     '      turnstile: "",\n      messages,',
     SUITE, "which carries it as well"),
    ("H1  /api/health stops publishing the sitekey (no widget, every turn refused)", HEALTH,
     "      turnstile: publicTurnstile(cfg),",
     '      turnstile: "",',
     SUITE, "PUBLISHES the sitekey when the control is enforced"),

    # ---- B: Trap B, as a class ------------------------------------------------
    ("B1  a client script dropped from the app-script no-cache list", HEADERS,
     "/turnstile.js\n  Cache-Control: no-cache",
     "# /turnstile.js\n#   Cache-Control: no-cache",
     SUITE, "has its own no-cache entry"),

    # ---- UX: the send path, when no token can be minted ----------------------
    # A page that repeats one sentence under a LIVE badge, inviting a retry that cannot
    # work, is strictly worse than the unreachable-gateway path it sits next to.
    ("UX1 a local token failure records no strike, so the badge keeps saying LIVE", TRANSPORT,
     "    botStrikes++;\n    // Counted against the same 3-strike degrade an unreachable gateway uses, so the badge\n"
     "    // and the copy stop claiming a live brain the page cannot reach.\n    noteTransportError();",
     "    botStrikes++;",
     UX, "the page is DEGRADED, not still claiming LIVE"),
    ("UX2 every failure repeats the same sentence instead of answering from stub.js", TRANSPORT,
     "    if (botStrikes > 1 && haveStub) {",
     "    if (false) {",
     UX, "answered from stub.js"),
    ("UX3 mic.js mints a CHAT token, so the ears refuse every clip", MIC,
     'return Promise.resolve(t.getToken("transcribe")).then(function (tok) {',
     'return Promise.resolve(t.getToken("chat")).then(function (tok) {',
     SUITE, "mic.js mints for the TRANSCRIBE action"),
    ("UX4 mic.js sends no token header at all", MIC,
     '    if (token) opt.headers["X-Turnstile-Response"] = token;',
     "    void token;",
     SUITE, "sends it on the header the transcribe route reads"),
]


def _scratch_tree() -> pathlib.Path:
    """A throwaway copy of the subtrees the suites read, safe to rewrite.

    `cp -al` (hardlinks, metadata only) rather than a byte copy: this repo is 320 MB and
    9 000 files, and the link farm takes about 0.2 s. THE CATCH IS THE WHOLE POINT OF THE
    NEXT LOOP — a hardlink shares its inode, and `open(..., "w")` truncates in place, which
    would write straight THROUGH to the checkout. So every file this table can mutate is
    immediately replaced by a real copy, breaking that link before any row runs.

    `shutil.copytree` is not used: it copies bytes, which for this tree is seconds per row's
    worth of setup rather than one fifth of a second for the whole run.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="turnstile-mutation-"))
    for tree in TREES:
        subprocess.run(["cp", "-al", str(WT / tree), str(root / tree)], check=True)
    for real in sorted({row[1] for row in MUTATIONS}):
        target = root / real.relative_to(WT)
        data = real.read_bytes()
        target.unlink()                     # break the hardlink; do NOT truncate through it
        target.write_bytes(data)
        assert target.stat().st_nlink == 1, f"{real} is still hardlinked to the checkout"
    return root


def main(argv=()) -> int:
    """Run the table, or only the rows whose name starts with one of `argv`.

    The filter exists because row D3e is caught BY HANGING, which costs
    `MUTATION_TIMEOUT_S` on its own — so re-checking one row after repairing it would
    otherwise mean waiting out somebody else's deliberate hang.
    """
    rows = [r for r in MUTATIONS
            if not argv or any(r[0].split()[0] == a or r[0].startswith(a) for a in argv)]
    if argv and not rows:
        print(f"no row matches {list(argv)}; rows are: "
              + ", ".join(r[0].split()[0] for r in MUTATIONS))
        return 1
    root = _scratch_tree()
    print(f"  (mutating a throwaway copy at {root} — the checkout is never written)")
    caught = missed = noop = wrong = 0
    try:
        for name, real, old, new, suite, selector in rows:
            path = root / real.relative_to(WT)
            pristine = real.read_text()
            src = path.read_text()
            if old not in src:
                print(f"  NO-OP       {name}  (anchor not found)")
                noop += 1
                continue
            path.write_text(src.replace(old, new, 1))
            try:
                try:
                    r = subprocess.run(
                        ["node", suite], cwd=root, capture_output=True, text=True,
                        timeout=MUTATION_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    # Counted as caught, and SAID so rather than silently: the guard's test
                    # did not pass, but "it never finished" is a different fact from "it
                    # went red".
                    print(f"  caught      {name}  (hung — killed after {MUTATION_TIMEOUT_S}s)")
                    caught += 1
                    continue
                out = r.stdout + r.stderr
                # Three failure formats ship in this repo and a row may target any of
                # them: `  FAIL: <label>` (sim/test_turnstile.mjs), `  - <label>`
                # (sim/test_cloud_transport.mjs's `finish`) and `   · <label>`
                # (browser_harness.mjs). Matching only the first would report a real red
                # as WRONG CHECK for every row whose suite is not test_turnstile.
                failing = [ln for ln in out.splitlines()
                           if "FAIL:" in ln or ln.startswith("  - ") or ln.startswith("   \u00b7 ")]
                if r.returncode == 0:
                    print(f"  NOT CAUGHT  {name}")
                    missed += 1
                elif any(selector in ln for ln in failing):
                    hits = sum(1 for ln in failing if selector in ln)
                    print(f"  caught      {name}  ({len(failing)} red, {hits} naming {selector!r})")
                    caught += 1
                else:
                    # The suite reddened, but not on the assertion this row is about. That
                    # is NOT a pass: see the header — it is how a row comes to prove nothing.
                    print(f"  WRONG CHECK {name}  ({len(failing)} red, none naming {selector!r})")
                    if failing:
                        print(f"                 first red: {failing[0].strip()[:120]}")
                    wrong += 1
            finally:
                # Back to pristine INSIDE the scratch tree, so the next row starts from the
                # committed text rather than from this row's edit. (Restoring the checkout
                # is not a thing this tool has to do any more — it never changed it.)
                path.write_text(pristine)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    total = caught + missed + noop + wrong
    print(f"\nMUTATIONS: {caught} caught, {missed} missed, {noop} no-op, {wrong} wrong-check "
          f"({total} rows run, {len(MUTATIONS)} in the table)")
    return 1 if (missed or noop or wrong) else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main([a for a in sys.argv[1:] if not a.startswith("-")]))
