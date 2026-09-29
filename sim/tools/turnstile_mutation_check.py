"""Break each guard the Turnstile bot control rests on; the row's node suite must go red
ON THE CHECK its selector names (an unrelated red reads WRONG CHECK — the first fail-open
draft reddened only the slot-release block and proved nothing about fail-open).

Rows cover the three mandatory checks one at a time, D2's slot release (and its negative
control), both halves of D3's fail-closed/fail-open split, D1's order, D4's config gate,
and the client's per-send token freshness. Mutations run in a throwaway `cp -al` copy:
the first version edited the checkout and once left check C2 disabled in a tree about to
be pushed. ~6 minutes (the suite waits out a real 8 s deadline; D3e is caught by hanging).

    python3 sim/tools/turnstile_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

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
SUITE = "sim/test_turnstile.mjs"
UX = "sim/test_cloud_transport.mjs"
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
    # ANCHOR REPAIRED 2026-09-07, not the row rewritten: `buildUpstreamBody` grew a
    # documentation-excerpt parameter, so the call this row pinned changed shape. The
    # mutation is unchanged — insert a refund on the path that DID reach the gateway — and
    # it now anchors on the `if (!upstream.ok)` block alone, which is the thing the row is
    # actually about and does not move when the call's arguments do.
    ("R3  the upstream-failure path refunds too, so real spend is credited back", CHAT,
     "    if (!upstream.ok) {\n      return refusal(cfg, upstream.reason, {",
     "    if (!upstream.ok) {\n      slot.refundBudget();\n      return refusal(cfg, upstream.reason, {",
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
     "{ turnstile: publicTurnstile(cfg) }",
     '{ turnstile: "" }',
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


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: ["node", r[4]], verdict=node_verdict,
                     timeout=MUTATION_TIMEOUT_S, scratch=("functions", "sim"),
                     baseline=[["node", SUITE], ["node", UX]]))
