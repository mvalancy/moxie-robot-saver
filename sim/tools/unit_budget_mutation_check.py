"""Remove each guard the SHARED unit budget rests on, and check its test goes red.

*"A test for every fix, proven in BOTH directions."* A green `sim/test_demo_proxy.mjs` §15i
proves the guards are **present**; this proves they are **load-bearing**. Same shape as
`turnstile_mutation_check.py`, and deliberately the same STRICTNESS: the runner is
`node sim/test_demo_proxy.mjs`, which prints one `  - <label>` line per failed check, and
the sixth column is a substring that must appear IN A FAILING LABEL. A row is caught only
when **the check that names that guard** is the one that reddened, because a mutation that
broke some unrelated assertion would otherwise read as caught while the guard it targeted
was never exercised.

Run it by hand after touching the cache tier in `functions/api/_lib/limits.js`:

    python3 sim/tools/unit_budget_mutation_check.py            # the whole table, ~25 s
    python3 sim/tools/unit_budget_mutation_check.py U3         # one row, ~1.5 s

=============================================================================
WHY THIS TABLE EXISTS AT ALL, WHICH IS A DIFFERENT QUESTION FROM WHY THE OTHERS DO.

Every other guard in this repo is wrong in a way somebody notices: a refusal that should
have been an admission is a visitor complaining, a leaked secret is a leaked secret. The
shared unit budget's characteristic failure is **an admission that should have been a
refusal**, which nobody notices at all — it is a slightly larger gateway bill — and its
one CATASTROPHIC failure is the mirror image: a lost write that leaves the colo's hour
looking fuller than it is, so real visitors are answered `budget_exhausted` and the page
paints SCRIPTED for an hour. Neither shows up in a green suite. Both are one deleted line.

So the rows come in two families and the second is the point:

  · **U1, U7, U8, U11, U13, U17** — the counter stops counting, or counts the wrong thing.
    An undercount. Cheap to be wrong about, and the table catches it anyway.
  · **U2, U3, U4, U5, U6, U9, U10, U12, U16** — the counter counts something TWICE, keeps
    a charge it should have dropped, or refuses where it should have fallen open. Every
    one of these is an OVERCOUNT or a fail-CLOSED, which is the direction
    `_lib/limits.js::sharedBudgetVerdict` says this tier may never fail in.

U1 and U3 deserve naming individually, because each is the shipped design of a REJECTED
alternative rather than a typo:

  · **U1 charges the colo at admission and refunds only locally** — the reading of §4.6.1's
    *"the same fail-open rules apply verbatim"* that the slice was briefed to evaluate. It
    re-opens the free drain PR #160 closed, in the shared dimension where it is worse: 200
    tokenless POSTs x 3 units is exactly `DEMO_UNIT_BUDGET_HOUR`.
  · **U3 keeps the unpublished units after a write attempt, to retry them** — which reads
    like resilience and is a double charge whenever a `put` lands and then times out. The
    fake cache grew a `putStoresThenHangs` shape specifically so this row has teeth.

=============================================================================
**IT NEVER TOUCHES YOUR CHECKOUT.** Every mutation is applied inside a THROWAWAY COPY —
`cp -al` of `functions/` and `sim/`, with the mutated files replaced by real copies so no
write can reach the original inode through a shared one. See
`turnstile_mutation_check.py`'s header for the two incidents that made that non-negotiable
(a disabled security check left in the tree by a run that was killed, and two concurrent
runs reddening each other's suites).
"""
import pathlib
import shutil
import subprocess
import tempfile

WT = pathlib.Path(__file__).resolve().parents[2]

#: The subtrees the suite needs. `sim/test_demo_proxy.mjs` computes its repo root as
#: `sim/..`, imports `functions/api/**`, reads `sim/**` as text, and — for its §5 oracle —
#: shells out to `python3 -c "…from moxie_sdk.wire import build_chat_response…"` with
#: `mqtt` on `sys.path`. That last one is inside a `try`, so a missing `mqtt/` is not a
#: failure; it is copied anyway so the run is not narrated by a Python traceback that has
#: nothing to do with the row being checked.
TREES = ("functions", "sim", "mqtt")

#: …and the loose files it opens by name from the repo root. `wrangler.toml` is NOT
#: optional: §12's deploy-only-failure guard reads it with `readFileSync` and an absent one
#: throws before a single check runs, which the runner would then report as sixteen
#: identical WRONG CHECK rows. (It did, on the first run of this table.)
ROOT_FILES = ("wrangler.toml",)

#: The one file this table mutates. Every guard in this slice lives in the admission
#: module, which is the point: the shared budget is not a policy spread across routes, it
#: is one function's arithmetic plus one isolate-local ledger.
LIMITS = WT / "functions/api/_lib/limits.js"

#: The suite. Run whole (about 1.5 s), because running it whole is what lets the selector
#: column check that the RIGHT assertion reddened.
SUITE = "sim/test_demo_proxy.mjs"

#: Seconds one mutated run may take before it is treated as caught-by-hanging. The suite is
#: ~1.5 s; a mutation that wedges a deadline would be caught, but only if something ends it.
MUTATION_TIMEOUT_S = 90

MUTATIONS = [
    # ---- U1: the rejected design, shipped ------------------------------------
    ("U1  charge the colo at ADMISSION and refund only locally (the free drain, shared)",
     LIMITS,
     "      if (!refunded && !settled) {",
     "      if (!settled) {",
     SUITE, "wrote NOTHING to the colo's hour"),

    # ---- U2: the release-then-refund ordering --------------------------------
    ("U2  a refund AFTER the release leaves the units in the ledger", LIMITS,
     "        settled = false;\n"
     "        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above",
     "        settled = false;",
     SUITE, "takes them straight back out again"),

    # ---- U3: the other rejected design — retry the unpublished units ---------
    ("U3  keep the units after a write ATTEMPT, to retry them (a double charge)", LIMITS,
     "    c.published += owed;\n    clearPending(b);",
     "    c.published += owed;",
     SUITE, "the colo holds 9 units, not 18"),

    # ---- U4: the hour roll ---------------------------------------------------
    ("U4  carry last hour's unpublished units INTO this hour's entry", LIMITS,
     "  if (u.bucket !== b) {\n"
     "    if (u.pending > 0) state.stats.cache.units.dropped += u.pending;\n"
     "    u.bucket = b;\n"
     "    u.pending = 0;\n"
     "  }",
     "  if (u.bucket !== b) {\n"
     "    u.bucket = b;\n"
     "  }",
     SUITE, "never carried into hour 3's entry"),

    # ---- U5: a failed READ that writes anyway --------------------------------
    # The plausible tidy-up: fall through instead of returning early. `published` is then 0
    # and the publish RESETS a live hour to this isolate's share — a far larger undercount
    # than not writing, and the exact mistake the window sub-tier's own note warns about.
    ("U5  a failed budget READ publishes anyway, resetting a live hour", LIMITS,
     "  if (seen === CACHE_ERROR) {\n"
     "    c.errors += 1;\n"
     "    c.allowed += 1;\n"
     "    // FAIL OPEN — and, like the timeout above, the ledger is KEPT: no write was attempted,\n"
     "    // so nothing can have landed, so nothing can be published twice by keeping it.\n"
     "    return null;\n"
     "  }",
     "  if (seen === CACHE_ERROR) {\n"
     "    c.errors += 1;\n"
     "  }",
     SUITE, "never reset to this isolate's share"),

    # ---- U6: fail open turned into fail closed -------------------------------
    ("U6  a cache that HANGS refuses instead of admitting (fail closed)", LIMITS,
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    c.timeouts += 1;\n"
     "    c.allowed += 1;\n"
     "    return null; // FAIL OPEN — and the ledger is KEPT, because nothing was written\n"
     "  }",
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    c.timeouts += 1;\n"
     "    c.refused += 1;\n"
     "    return { retryAfterS: 1 };\n"
     "  }",
     SUITE, "a match that HANGS FOR EVER must still ADMIT"),

    # ---- U7: the comparison forgets what this isolate owes -------------------
    ("U7  compare only the PUBLISHED count, ignoring this isolate's unpublished spend",
     LIMITS,
     "  if (published + owed >= ceiling) {",
     "  if (published >= ceiling) {",
     SUITE, "isolate B's SECOND is REFUSED"),

    # ---- U8: the sub-tier deleted -------------------------------------------
    ("U8  the budget sub-tier never consulted at all", LIMITS,
     "      const over = await sharedBudgetVerdict(store, o.request, { cfg, nowS });",
     "      const over = null;",
     SUITE, "then the budget entry"),

    # ---- U9: the visitor told the wrong thing --------------------------------
    ("U9  a spent colo hour reported as rate_limited (a 429 for a 503 condition)", LIMITS,
     '        reason = "budget_exhausted";',
     '        reason = "rate_limited";',
     SUITE, "the reason the in-isolate budget gives for the same fact"),

    # ---- U10: the cheaper, wrong order ---------------------------------------
    # Checking the budget first saves two ops when the hour is spent, and answers a
    # per-visitor condition with a deployment-wide 503. See `sharedThenGrant`'s note.
    ("U10 the budget checked BEFORE the per-IP window (the cheaper, wrong order)", LIMITS,
     "    verdict = await sharedWindowVerdict(store, o.request, { ip, route, cfg, nowS });\n"
     "    if (!verdict) {\n"
     "      const over = await sharedBudgetVerdict(store, o.request, { cfg, nowS });",
     "    const first = await sharedBudgetVerdict(store, o.request, { cfg, nowS });\n"
     "    if (first) { verdict = { retryAfterS: first.retryAfterS, rateLimit: win.rateLimit };\n"
     '                 reason = "budget_exhausted"; }\n'
     "    else verdict = await sharedWindowVerdict(store, o.request, { ip, route, cfg, nowS });\n"
     "    if (!verdict) {\n"
     "      const over = await sharedBudgetVerdict(store, o.request, { cfg, nowS });",
     SUITE, "a spent colo hour AND a spent minute answers rate_limited"),

    # ---- U11: the uncapped deployment ----------------------------------------
    ("U11 accrue units on a deployment with no hourly ceiling to mirror", LIMITS,
     "  const owed = budget && budget.hourly && budget.charged && budget.charged.length",
     "  const owed = budget && budget.charged && budget.charged.length",
     SUITE, "an uncapped deployment accrues nothing"),

    # ---- U12: which hour pays --------------------------------------------
    # The clock read again at settle time rather than the hour the charge was MADE in.
    # Right almost always and wrong exactly at a bucket boundary, which is the shape of bug
    # this repo has already shipped twice under the name "a cached belief about a moving
    # thing" (orchestration-plan rule 23).
    ("U12 settle against the clock at RELEASE time, not the hour the charge was made in",
     LIMITS,
     "        accruePending(hourBucket, owed);",
     "        accruePending(bucket(Math.floor(Date.now() / 1000), SCALES.hour), owed);",
     SUITE, "sit in this isolate's ledger as a RECORDED fact"),

    # ---- U13: the namespace --------------------------------------------------
    ("U13 the budget's key namespace collides with a route name", LIMITS,
     'const UNITS_PATH = "units";',
     'const UNITS_PATH = "chat";',
     SUITE, "is not a route name, so a window key can never spell a budget key by route"),

    # ---- U14: the entry outlives its own hour --------------------------------
    ("U14 the budget entry given a MINUTE's max-age instead of its own hour's", LIMITS,
     '            "Cache-Control": "max-age=" + SCALES.hour,',
     '            "Cache-Control": "max-age=" + SCALES.min,',
     SUITE, "an entry that outlives its own"),

    # ---- U16: the refusal's own housekeeping ---------------------------------
    # The shared refusal must undo the in-isolate charge, or a tier refusal costs the
    # visitor a unit they never spent. (There is no U15: the row it was drafted for turned
    # out to be U3 said twice.)
    ("U16 a shared-tier refusal keeps the in-isolate charge it refused", LIMITS,
     "  handOffOrRelease(route);\n"
     "  refundCharges(win.charged, budget.charged, budget.cost);",
     "  handOffOrRelease(route);",
     SUITE, "refunds the in-isolate units it charged"),

    # ---- U17: the publish that always runs -----------------------------------
    # `owed > 0` is what makes a refused request cost ZERO writes. Without it every
    # admission writes, including the 200 that were about to be refunded — which is the
    # op cost the latency note promises is not spent, and a stream of no-op writes on the
    # single hottest key in the colo.
    ("U17 publish on every admission, even when the isolate owes nothing", LIMITS,
     "  if (owed > 0) {",
     "  if (owed >= 0) {",
     SUITE, "which is the structural half of the claim"),
]


def _scratch_tree() -> pathlib.Path:
    """A throwaway copy of the subtrees the suite reads, safe to rewrite.

    `cp -al` (hardlinks, metadata only) rather than a byte copy. THE CATCH IS THE POINT OF
    THE SECOND LOOP — a hardlink shares its inode and `open(..., "w")` truncates in place,
    which would write straight THROUGH to the checkout — so every file the table can mutate
    is immediately replaced by a real copy, breaking that link before any row runs.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="unit-budget-mutation-"))
    for tree in TREES:
        subprocess.run(["cp", "-al", str(WT / tree), str(root / tree)], check=True)
    for name in ROOT_FILES:
        shutil.copyfile(WT / name, root / name)
    for real in sorted({row[1] for row in MUTATIONS}):
        target = root / real.relative_to(WT)
        data = real.read_bytes()
        target.unlink()  # break the hardlink; do NOT truncate through it
        target.write_bytes(data)
        assert target.stat().st_nlink == 1, f"{real} is still hardlinked to the checkout"
    return root


def main(argv=()) -> int:
    """Run the table, or only the rows whose name starts with one of `argv`."""
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
            hits_in_src = src.count(old)
            if hits_in_src == 0:
                print(f"  NO-OP       {name}  (anchor not found)")
                noop += 1
                continue
            if hits_in_src > 1:
                # AMBIGUOUS IS NOT CAUGHT, and this is not a hypothetical refinement.
                # `str.replace(old, new, 1)` mutates whichever copy comes FIRST, and on this
                # table's first run row U5's anchor matched the per-IP window sub-tier's
                # fail-open block as well as the budget's — they were byte-identical — so
                # the row spent a whole run checking a guard it is not about, and reported
                # WRONG CHECK for a reason no reader could have guessed from the output.
                # The other five tables in this directory have the same latent defect.
                print(f"  AMBIGUOUS   {name}  (anchor matches {hits_in_src} places; "
                      f"it would mutate whichever comes first)")
                noop += 1
                continue
            path.write_text(src.replace(old, new, 1))
            try:
                try:
                    r = subprocess.run(
                        ["node", suite], cwd=root, capture_output=True, text=True,
                        timeout=MUTATION_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    print(f"  caught      {name}  (hung — killed after {MUTATION_TIMEOUT_S}s)")
                    caught += 1
                    continue
                out = r.stdout + r.stderr
                #: `sim/test_demo_proxy.mjs` prints `  - <label>` per failed check.
                failing = [ln for ln in out.splitlines() if ln.startswith("  - ")]
                if r.returncode == 0:
                    print(f"  NOT CAUGHT  {name}")
                    missed += 1
                elif any(selector in ln for ln in failing):
                    hits = sum(1 for ln in failing if selector in ln)
                    print(f"  caught      {name}  ({len(failing)} red, {hits} naming {selector!r})")
                    caught += 1
                else:
                    # The suite reddened, but not on the assertion this row is about. That
                    # is NOT a pass: it is how a row comes to prove nothing.
                    print(f"  WRONG CHECK {name}  ({len(failing)} red, none naming {selector!r})")
                    if failing:
                        print(f"                 first red: {failing[0].strip()[:120]}")
                    wrong += 1
            finally:
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
