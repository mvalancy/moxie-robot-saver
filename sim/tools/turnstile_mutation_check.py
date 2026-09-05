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

Nothing here changes the tree permanently: each mutation is reverted in a `finally`.
"""
import pathlib
import subprocess

WT = pathlib.Path(__file__).resolve().parents[2]
LIB = WT / "functions/api/_lib/turnstile.js"
LIMITS = WT / "functions/api/_lib/limits.js"
CHAT = WT / "functions/api/chat.js"
ENV = WT / "functions/api/_lib/env.js"
CLIENT = WT / "sim/web/turnstile.js"

#: The one suite these guards live in. Every row runs it whole — it takes under a second,
#: so there is nothing to gain from narrowing it, and running it whole is what lets the
#: selector column check that the RIGHT assertion reddened.
SUITE = "sim/test_turnstile.mjs"

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
     '  if (String(body.action || "") !== TURNSTILE_ACTION) {',
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
     "    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD]);\n"
     "    if (!bot.ok) {\n"
     "      return refusal(cfg, \"chat\", bot.reason, { load: slot.load, rateLimit: slot.rateLimit });\n"
     "    }",
     "    const bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD]);\n"
     "    if (!bot.ok) {\n"
     "      slot.__leak = true;\n"
     "      slot.release = () => {};\n"
     "      return refusal(cfg, \"chat\", bot.reason, { load: slot.load, rateLimit: slot.rateLimit });\n"
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
     "    const verdict = assess(text);\n    if (verdict.blocked) return blocked(cfg, slot, verdict);",
     "    const __bot = await verifyTurnstile(cfg, request, parsed.body[TOKEN_FIELD]);\n"
     "    if (!__bot.ok) return refusal(cfg, \"chat\", __bot.reason, { load: slot.load, rateLimit: slot.rateLimit });\n"
     "    const verdict = assess(text);\n    if (verdict.blocked) return blocked(cfg, slot, verdict);",
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
     "        if (held && held !== spent) { stats.reused++; done(held); return; }",
     "        if (held) { stats.reused++; done(held); return; }",
     SUITE, "a spent token is NEVER handed out again"),
    ("D6g never look at a held token — an interactive solve is reset away for ever", CLIENT,
     "          held = typeof t.getResponse === \"function\" ? String(t.getResponse(widgetId) || \"\") : \"\";",
     '          held = "";',
     SUITE, "spends the token the widget was left holding"),
    ("D6  drop the reset() before execute(): the same token is replayed every turn", CLIENT,
     "          if (typeof t.reset === \"function\") t.reset(widgetId);",
     "          void 0;",
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
     "      return ready ? mint() : null;",
     '      return ready ? mint() : "";',
     SUITE, "a script that cannot load resolves null"),
    ("D6e the client's action drifts from the server's", CLIENT,
     '  var ACTION = "chat";',
     '  var ACTION = "chat-turn";',
     SUITE, "equals the server's TURNSTILE_ACTION"),
]


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
    caught = missed = noop = wrong = 0
    for name, path, old, new, suite, selector in rows:
        src = path.read_text()
        if old not in src:
            print(f"  NO-OP       {name}  (anchor not found)")
            noop += 1
            continue
        backup = src
        path.write_text(src.replace(old, new, 1))
        try:
            try:
                r = subprocess.run(
                    ["node", suite], cwd=WT, capture_output=True, text=True,
                    timeout=MUTATION_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Counted as caught, and SAID so rather than silently: the guard's test did
                # not pass, but "it never finished" is a different fact from "it went red".
                print(f"  caught      {name}  (hung — killed after {MUTATION_TIMEOUT_S}s)")
                caught += 1
                continue
            out = r.stdout + r.stderr
            failing = [ln for ln in out.splitlines() if "FAIL:" in ln]
            if r.returncode == 0:
                print(f"  NOT CAUGHT  {name}")
                missed += 1
            elif any(selector in ln for ln in failing):
                hits = sum(1 for ln in failing if selector in ln)
                print(f"  caught      {name}  ({len(failing)} red, {hits} naming {selector!r})")
                caught += 1
            else:
                # The suite reddened, but not on the assertion this row is about. That is
                # NOT a pass: see the header — it is how a row comes to prove nothing.
                print(f"  WRONG CHECK {name}  ({len(failing)} red, none naming {selector!r})")
                if failing:
                    print(f"                 first red: {failing[0].strip()[:120]}")
                wrong += 1
        finally:
            path.write_text(backup)
    total = caught + missed + noop + wrong
    print(f"\nMUTATIONS: {caught} caught, {missed} missed, {noop} no-op, {wrong} wrong-check "
          f"({total} rows run, {len(MUTATIONS)} in the table)")
    return 1 if (missed or noop or wrong) else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main([a for a in sys.argv[1:] if not a.startswith("-")]))
