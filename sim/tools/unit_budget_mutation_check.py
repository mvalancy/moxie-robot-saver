"""Remove each guard the SHARED day/hour ceilings rest on, and check its test goes red.

A green `sim/test_demo_proxy.mjs` §15i and `sim/tests/helpers_shared_ceilings.mjs` prove
the guards are PRESENT; this proves they are LOAD-BEARING. Each row names a runner that
prints one `  - <label>` line per failed check, and a row is caught only when its
selector appears IN A FAILING LABEL — a mutation that broke some unrelated assertion
would otherwise read as caught while its guard was never exercised. Rows `U*` run
`test_demo_proxy.mjs` (the shared minute window and the budget's hour); `W*`/`D*` run
`helpers_shared_ceilings.mjs` (the per-IP hour/day windows and the budget's day).

    python3 sim/tools/unit_budget_mutation_check.py            # the whole table, ~45 s
    python3 sim/tools/unit_budget_mutation_check.py U3 D4      # two rows, ~1.5 s

Run it after touching `functions/api/_lib/limits.js`, `counters.js` or `sharedtier.js`.

Why this table matters: the shared budget's usual failure is an admission that should
have been a refusal — a slightly larger gateway bill nobody notices — and its catastrophic
one is the mirror: a lost or doubled write that makes the colo's hour look fuller than it
is, so real visitors get `budget_exhausted` and a SCRIPTED page. Neither shows in a green
suite; both are one deleted line. So the rows come in two families:

  · undercounts (the counter stops counting): U1, U7, U8, U11, U13, U14, U17, W5, W7, W8,
    D3, D5, D6, D10, D11;
  · OVERCOUNTS or fail-CLOSED — the direction `sharedtier.js::sharedBudgetVerdict` says this
    tier may never fail in: U2-U6, U9, U10, U12, U16, U18, U19, W1-W3, W6, D1, D2, D4, D7-D9.

U1 and U3 are rejected designs rather than typos (charge the colo at admission and refund
only locally; keep unpublished units after a write attempt to retry them). D4 is the one
that actually shipped: the day ceiling copied the hour's design without its proof, and
deleting `unaccrueDayPending()` left every suite green until a hand-run sweep found it.

It never touches your checkout: every mutation runs in a throwaway `cp -al` copy with the
mutated files replaced by real copies, so no write reaches the original inode.
"""
import pathlib
import shutil
import subprocess
import tempfile

WT = pathlib.Path(__file__).resolve().parents[2]

#: The subtrees the suites read (`mqtt/` only for an optional §5 oracle — copied so the
#: run is not narrated by an unrelated Python traceback).
TREES = ("functions", "sim", "mqtt")

#: Loose root files the suite opens: without `wrangler.toml` §12 throws before any check.
ROOT_FILES = ("wrangler.toml",)

#: The files this table mutates: `admit()`'s arithmetic, the isolate-local ledger, and
#: the Cache API sub-tiers that publish it.
LIMITS = WT / "functions/api/_lib/limits.js"
COUNTERS = WT / "functions/api/_lib/counters.js"
SHARED = WT / "functions/api/_lib/sharedtier.js"

#: Run whole (~1.5 s), so the selector column can check the RIGHT assertion reddened.
SUITE = "sim/test_demo_proxy.mjs"

#: The DAY/WIDE tier's suite, run by `sim/tests/test_shared_ceilings.py`. Same output
#: contract, so the row format does not change.
CEILINGS = "sim/tests/helpers_shared_ceilings.mjs"

#: A mutation that wedges a deadline is caught, but only if something ends the run.
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
    ("U3  keep the units after a write ATTEMPT, to retry them (a double charge)", SHARED,
     "    c.published += owed;\n    clearPending(b);",
     "    c.published += owed;",
     SUITE, "the colo holds 9 units, not 18"),

    # ---- U4: the hour roll ---------------------------------------------------
    ("U4  carry last hour's unpublished units INTO this hour's entry", COUNTERS,
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
    # Falling through instead of returning resets a live hour to this isolate's share.
    ("U5  a failed budget READ publishes anyway, resetting a live hour", SHARED,
     "  if (seen === CACHE_ERROR) {\n"
     "    c.errors += 1;\n"
     "    c.allowed += 1;\n"
     "    return null; // FAIL OPEN; ledger KEPT (no write was attempted)\n"
     "  }",
     "  if (seen === CACHE_ERROR) {\n"
     "    c.errors += 1;\n"
     "  }",
     SUITE, "never reset to this isolate's share"),

    # ---- U6: fail open turned into fail closed -------------------------------
    ("U6  a cache that HANGS refuses instead of admitting (fail closed)", SHARED,
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    c.timeouts += 1;\n"
     "    c.allowed += 1;\n"
     "    return null; // FAIL OPEN; ledger KEPT (nothing was written)\n"
     "  }",
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    c.timeouts += 1;\n"
     "    c.refused += 1;\n"
     "    return { retryAfterS: 1 };\n"
     "  }",
     SUITE, "a match that HANGS FOR EVER must still ADMIT"),

    # ---- U7: the comparison forgets what this isolate owes -------------------
    ("U7  compare only the PUBLISHED count, ignoring this isolate's unpublished spend",
     SHARED,
     "  if (published + owed >= ceiling) {",
     "  if (published >= ceiling) {",
     SUITE, "isolate B's SECOND is REFUSED"),

    # ---- U8: the sub-tier deleted -------------------------------------------
    ("U8  the budget sub-tier never consulted at all", LIMITS,
     "      const over = await sharedBudgetVerdict(store, o.request, { cfg, nowS });",
     "      const over = null;",
     SUITE, "FOUR shared entries"),

    # ---- U9: the visitor told the wrong thing --------------------------------
    ("U9  a spent colo hour reported as rate_limited (a 429 for a 503 condition)", LIMITS,
     '        reason = "budget_exhausted";',
     '        reason = "rate_limited";',
     SUITE, "the reason the in-isolate budget gives for the same fact"),

    # ---- U10: the cheaper, wrong order ---------------------------------------
    # Budget first saves two ops but answers a per-visitor condition with a global 503.
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
    # (`let` because `chargeExtra()` raises `owed` for a re-rolled turn.)
    ("U11 accrue units on a deployment with no hourly ceiling to mirror", LIMITS,
     "  let owed = budget && budget.hourly && budget.charged && budget.charged.length",
     "  let owed = budget && budget.charged && budget.charged.length",
     SUITE, "uncapped deployment accrues nothing"),

    # ---- U12: which hour pays --------------------------------------------
    # Re-reading the clock at settle time is wrong exactly at a bucket boundary.
    ("U12 settle against the clock at RELEASE time, not the hour the charge was made in",
     LIMITS,
     "        accruePending(hourBucket, owed);",
     "        accruePending(bucket(Math.floor(Date.now() / 1000), SCALES.hour), owed);",
     SUITE, "sit in this isolate's ledger as a RECORDED fact"),

    # ---- U13: the namespace --------------------------------------------------
    ("U13 the budget's key namespace collides with a route name", SHARED,
     'const UNITS_PATH = "units";',
     'const UNITS_PATH = "chat";',
     SUITE, "is not a route name, so a window key can never spell a budget key by route"),

    # ---- U14: the entry outlives its own hour --------------------------------
    ("U14 the budget entry given a MINUTE's max-age instead of its own hour's", SHARED,
     "    await putEntry(c, store, key, { n: published + owed }, SCALES.hour, cfg);",
     "    await putEntry(c, store, key, { n: published + owed }, SCALES.min, cfg);",
     SUITE, "an entry that outlives its own"),

    # ---- U16: the refusal's own housekeeping ---------------------------------
    # A tier refusal must refund the in-isolate charge. (There is no U15.)
    ("U16 a shared-tier refusal keeps the in-isolate charge it refused", LIMITS,
     "  handOffOrRelease(route);\n"
     "  refundCharges(win.charged, budget.charged, budget.cost);",
     "  handOffOrRelease(route);",
     SUITE, "refunds the in-isolate units it charged"),

    # ---- U17: the publish that always runs -----------------------------------
    # `owed > 0` is what makes a refused request cost ZERO writes on the hottest key.
    ("U17 publish on every admission, even when the isolate owes nothing", SHARED,
     "  if (owed > 0) {",
     "  if (owed >= 0) {",
     SUITE, "which is the structural half of the claim"),
    # =========================================================================
    # THE DAY/WIDE TIER (runner: CEILINGS). The day ceiling was lifted onto the cache by
    # copying the hour's design without its proof; these rows are that proof.
    # =========================================================================

    # ---- W1: the fixed ordering, put back -------------------------------------
    # Wide check AFTER the minute write makes a refusal cost a write: an OVERCOUNT.
    ("W1  the wide check moved back AFTER the minute write (a refusal that costs a write)",
     SHARED,
     "  const wider = await sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });\n"
     "  if (wider) return wider;\n"
     "\n"
     "  // The key carries the bucket, so one window of `max-age` is enough.\n"
     "  await putEntry(c, store, key, { n: used + 1 }, SCALES.min, cfg);\n"
     "  c.allowed += 1;",
     "  // The key carries the bucket, so one window of `max-age` is enough.\n"
     "  await putEntry(c, store, key, { n: used + 1 }, SCALES.min, cfg);\n"
     "  const wider = await sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });\n"
     "  if (wider) return wider;\n"
     "  c.allowed += 1;",
     CEILINGS, "a refusal may not leave a counter ABOVE the truth"),

    # ---- W2: the staleness argument, deleted ----------------------------------
    # The hour's freshness rides in a bucket stamp in the body; ignore it and an 03:00
    # count is believed at 20:00.
    ("W2  the wide entry's BUCKET STAMP ignored, so a closed hour's count is believed",
     SHARED,
     "    const stored = body && Number(body[bField]) === b ? Number(body[nField]) : 0;",
     "    const stored = body ? Number(body[nField]) : 0;",
     CEILINGS, "a count stamped with a DIFFERENT bucket reads as zero, not as this hour's"),

    # ---- W3: fail open turned into fail closed, in the wide half --------------
    ("W3  a store that HANGS refuses the wide window instead of admitting (fail closed)",
     SHARED,
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    w.timeouts += 1;\n"
     "    w.allowed += 1;\n"
     "    return null; // FAIL OPEN: a deadline is not evidence that anybody is over their hour\n"
     "  }",
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    w.timeouts += 1;\n"
     "    w.refused += 1;\n"
     "    const reset = (bucket(nowS, SCALES.min) + 1) * SCALES.min;\n"
     "    return { retryAfterS: 1, rateLimit: { limit: limits.min, remaining: 0, reset } };\n"
     "  }",
     CEILINGS, "WIDE WINDOW FAILS OPEN: a match that HANGS FOR EVER still ADMITS"),

    # ---- W4: the mark that separates a wide key from a narrow one -------------
    # Arity is 3 for both shapes; the letter mark is the whole separation.
    ("W4  the WIDE mark emptied, so a wide key can spell a narrow one", SHARED,
     'const WIDE_MARK = "w";',
     'const WIDE_MARK = "";',
     CEILINGS, "the two wide shapes are marked by a LETTER"),

    # ---- W5: the wide entry outlives its own day ------------------------------
    # The key rotates daily, so max-age must be the day or the ceiling quietly unbinds.
    ("W5  the WIDE entry given a MINUTE's max-age instead of its own day's", SHARED,
     "  await putEntry(w, store, key, next, SCALES.day, cfg);",
     "  await putEntry(w, store, key, next, SCALES.min, cfg);",
     CEILINGS, "it lives exactly ONE DAY — the widest scale it holds"),

    # ---- W6: a failed READ that writes anyway ---------------------------------
    # The wide half of U5: falling through resets a live window to one turn.
    ("W6  a failed WIDE read publishes anyway, resetting a live window", SHARED,
     "  if (seen === CACHE_ERROR) {\n"
     "    w.errors += 1;\n"
     "    w.allowed += 1;\n"
     "    return null; // FAIL OPEN: neither is a throw from a store having a bad day\n"
     "  }",
     "  if (seen === CACHE_ERROR) {\n"
     "    w.errors += 1;\n"
     "  }",
     CEILINGS, "publishing after a failed read would RESET a live hour to one"),

    # ---- W7: the wide ceiling off by one --------------------------------------
    # `>=`: the local cost is already counted in-isolate, so ON the ceiling is spent.
    ("W7  the wide window's ceiling comparison off by one (> instead of >=)", SHARED,
     "    if (used >= ceiling) {\n"
     "      w.refused += 1;",
     "    if (used > ceiling) {\n"
     "      w.refused += 1;",
     CEILINGS, "the colo has seen three"),

    # ---- W8: which scale answers when both are spent --------------------------
    # Narrowest-first so a visitor over both is told the top of the hour, not tomorrow.
    ("W8  the wide scales built WIDEST-first, so the day answers before the hour", SHARED,
     '  if (limits.hour) scales.push(["hour", limits.hour, "h", "hb"]);\n'
     '  if (limits.day) scales.push(["day", limits.day, "d", "db"]);',
     '  if (limits.day) scales.push(["day", limits.day, "d", "db"]);\n'
     '  if (limits.hour) scales.push(["hour", limits.hour, "h", "hb"]);',
     CEILINGS, "the shortest Retry-After that applies wins"),

    # ---- D1: charge-at-admission, in the DAY dimension ------------------------
    # Settling the day without asking whether the request was REFUNDED is
    # charge-at-admission day-side (U1 with the hour left correct).
    ("D1  the DAY ledger settled even for a REFUNDED request (the free drain, day-side)",
     LIMITS,
     "      if (!refunded && !settled) {\n"
     "        settled = true;\n"
     "        accruePending(hourBucket, owed);\n"
     "        accrueDayPending(dayBucket, owedDay);\n"
     "      }",
     "      if (!refunded && !settled) {\n"
     "        settled = true;\n"
     "        accruePending(hourBucket, owed);\n"
     "      }\n"
     "      accrueDayPending(dayBucket, owedDay);",
     CEILINGS, "200 refunded requests wrote NOTHING to the colo's day"),

    # ---- D2: retain after the ATTEMPT ----------------------------------------
    # The day's half of U3: a put that lands then times out HAS landed.
    ("D2  keep the DAY units after a write ATTEMPT, to retry them (a double charge)",
     SHARED,
     "    d.published += owedDay;\n    clearDayPending(db);",
     "    d.published += owedDay;",
     CEILINGS, "a put that LANDS AND THEN HANGS still clears the ledger"),

    # ---- D3: the comparison forgets this isolate's day ledger -----------------
    ("D3  compare only the PUBLISHED day count, ignoring this isolate's unpublished spend",
     SHARED,
     "  if (spent + owedDay >= ceiling) {",
     "  if (spent >= ceiling) {",
     CEILINGS, "the colo has seen 12"),

    # ---- D4: THE ONE THAT SHIPPED --------------------------------------------
    # Dead code unless a case releases before it refunds; section E reaches it.
    ("D4  the DAY's un-accrual deleted (the dead branch #178 shipped and #180 caught)",
     LIMITS,
     "        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above\n"
     "        unaccrueDayPending(dayBucket, owedDay);",
     "        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above",
     CEILINGS, "straight back out of the DAY ledger too"),

    # ---- D5: the entry outlives its own day ----------------------------------
    ("D5  the DAY budget entry given a MINUTE's max-age instead of its own day's", SHARED,
     "    await putEntry(d, store, key, { n: spent + owedDay }, SCALES.day, cfg);",
     "    await putEntry(d, store, key, { n: spent + owedDay }, SCALES.min, cfg);",
     CEILINGS, "the day entry's max-age is ONE DAY"),

    # ---- D6: the publish that always runs, day-side --------------------------
    # `owedDay > 0` is what makes a refused request cost ZERO day writes.
    ("D6  publish the day on every admission, even when the isolate owes nothing", SHARED,
     "  if (owedDay > 0) {",
     "  if (owedDay >= 0) {",
     CEILINGS, "zero writes attempted, which is the structural half of the claim"),

    # ---- D7: the day roll ----------------------------------------------------
    # Yesterday's crumbs in today's entry refuse somebody TOMORROW; dropping is the
    # allowed direction, and `dropped` keeps it visible.
    ("D7  carry yesterday's unpublished units INTO today's entry", COUNTERS,
     "function pendingDayUnits(b) {\n"
     "  const u = state.unitsDay;\n"
     "  if (u.bucket !== b) {\n"
     "    if (u.pending > 0) state.stats.cache.unitsDay.dropped += u.pending;\n"
     "    u.bucket = b;\n"
     "    u.pending = 0;\n"
     "  }\n"
     "  return u.pending;\n"
     "}",
     "function pendingDayUnits(b) {\n"
     "  const u = state.unitsDay;\n"
     "  if (u.bucket !== b) {\n"
     "    u.bucket = b;\n"
     "  }\n"
     "  return u.pending;\n"
     "}",
     CEILINGS, "day 0's unpublished units are never carried into day 1's entry"),

    # ---- D8: fail open turned into fail closed, day budget -------------------
    # U6 one scale wider: a bad cache day answers `budget_exhausted` for a whole DAY.
    ("D8  a cache that HANGS refuses the DAY budget instead of admitting (fail closed)",
     SHARED,
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    d.timeouts += 1;\n"
     "    d.allowed += 1;\n"
     "    return null; // FAIL OPEN, ledger KEPT: nothing was written, so nothing can have landed\n"
     "  }",
     "  if (seen === CACHE_TIMEOUT) {\n"
     "    d.timeouts += 1;\n"
     "    d.refused += 1;\n"
     "    return { retryAfterS: 1 };\n"
     "  }",
     CEILINGS, "DAY BUDGET FAILS OPEN: a match that HANGS FOR EVER still ADMITS"),

    # ---- D9: the day budget's own mark ---------------------------------------
    # As W4: arity is 2 for hour and day keys, so the mark is the whole separation.
    ("D9  the DAY mark emptied, so the day key can spell an hour key", SHARED,
     'const DAY_MARK = "d";',
     'const DAY_MARK = "";',
     CEILINGS, "the day budget entry is origin + prefix + 'units' + d + the DAY bucket"),

    # ---- D10: the sub-tier deleted, one scale wider --------------------------
    # U8's day: without the hand-off an hour ceiling silently has no day ceiling.
    ("D10 the DAY budget sub-tier never consulted at all", SHARED,
     "  return sharedDayBudget(store, request, { cfg, nowS });\n}",
     "  return null;\n}",
     CEILINGS, "reads FOUR shared entries"),

    # ---- D11: the uncapped deployment, day-side ------------------------------
    # U11's day: a ledger filling for a ceiling that does not exist, published in one
    # burst the day an operator sets one. (`let` for U11's reason.)
    ("D11 accrue DAY units on a deployment with no daily ceiling to mirror", LIMITS,
     "  let owedDay = budget && budget.daily && budget.charged && budget.charged.length",
     "  let owedDay = budget && budget.charged && budget.charged.length",
     CEILINGS, "the DAY ledger never accrued a unit either"),

    # ---- U18: the re-roll spends past the ceiling ----------------------------
    # `chargeExtra()` alone stands between a second gateway call and a spent budget.
    ("U18 the re-roll's extra charge is taken even when the ceiling refused it", LIMITS,
     "      const extra = chargeBudget(route, ctx.cfg, ctx.nowS);\n"
     "      if (!extra.ok) return false;",
     "      const extra = chargeBudget(route, ctx.cfg, ctx.nowS);\n"
     "      if (!extra.ok) return true;",
     SUITE, "with no headroom for a second completion the re-roll DOES NOT HAPPEN"),

    # ---- U19: the refund forgets what the re-roll spent ----------------------
    # U18's mirror: a refund of only the admission's units leaves the re-roll's charged.
    ("U19 a refund gives back only the admission's units, not the re-roll's", LIMITS,
     "      refundCharges([], budgetKeys, (budget && budget.cost) || 0);",
     "      refundCharges([], (budget && budget.charged) || [], (budget && budget.cost) || 0);",
     SUITE, "a refund gives back BOTH charges, not just the admission's"),
]


def _scratch_tree() -> pathlib.Path:
    """A throwaway `cp -al` copy of the subtrees the suite reads. A hardlink shares its
    inode and a write would truncate THROUGH to the checkout, so every file the table can
    mutate is replaced by a real copy before any row runs."""
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
                # AMBIGUOUS IS NOT CAUGHT: `replace(old, new, 1)` would mutate whichever
                # copy comes first, and the row would test a guard it is not about.
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
                    # red, but not on this row's assertion: NOT a pass
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
