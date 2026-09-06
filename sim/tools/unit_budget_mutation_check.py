"""Remove each guard the SHARED day/hour ceilings rest on, and check its test goes red.

*"A test for every fix, proven in BOTH directions."* A green `sim/test_demo_proxy.mjs` §15i
and a green `sim/tests/helpers_shared_ceilings.mjs` prove the guards are **present**; this
proves they are **load-bearing**. Same shape as `turnstile_mutation_check.py`, and
deliberately the same STRICTNESS: each row names a runner, that runner prints one
`  - <label>` line per failed check, and the sixth column is a substring that must appear
IN A FAILING LABEL. A row is caught only when **the check that names that guard** is the
one that reddened, because a mutation that broke some unrelated assertion would otherwise
read as caught while the guard it targeted was never exercised.

TWO RUNNERS, ONE TABLE. Rows `U*` name `sim/test_demo_proxy.mjs` §15i, which holds the
proof for the shared MINUTE window and the budget's HOUR. Rows `W*` and `D*` name
`sim/tests/helpers_shared_ceilings.mjs`, which holds the proof for the per-IP HOUR/DAY
windows and the budget's DAY — a separate file only because `test_demo_proxy.mjs` was
reserved to another agent for the whole of that slice, as its own header explains. Nothing
about the row format changes; the runner column was always per-row.

Run it by hand after touching the cache tier in `functions/api/_lib/limits.js`:

    python3 sim/tools/unit_budget_mutation_check.py            # the whole table, ~45 s
    python3 sim/tools/unit_budget_mutation_check.py U3 D4      # two rows, ~1.5 s

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

  · **U1, U7, U8, U11, U13, U17** and **W5, W7, W8, D3, D5, D6, D10, D11** — the counter
    stops
    counting, or counts the wrong thing. An undercount. Cheap to be wrong about, and the
    table catches it anyway.
  · **U2, U3, U4, U5, U6, U9, U10, U12, U16, U18, U19** and **W1, W2, W3, W6, D1, D2, D4,
    D7, D8, D9** — the counter counts something TWICE, keeps a charge it should have dropped, or
    refuses where it should have fallen open. Every one of these is an OVERCOUNT or a
    fail-CLOSED, which is the direction `_lib/limits.js::sharedBudgetVerdict` says this
    tier may never fail in. (U14 predates the two lists and belongs in the first; it is
    left unlisted rather than quietly reclassified by somebody who did not write it.)

U1 and U3 deserve naming individually, because each is the shipped design of a REJECTED
alternative rather than a typo:

  · **U1 charges the colo at admission and refunds only locally** — the reading of §4.6.1's
    *"the same fail-open rules apply verbatim"* that the slice was briefed to evaluate. It
    re-opens the free drain PR #160 closed, in the shared dimension where it is worse: 200
    tokenless POSTs x 3 units is exactly `DEMO_UNIT_BUDGET_HOUR`.
  · **U3 keeps the unpublished units after a write attempt, to retry them** — which reads
    like resilience and is a double charge whenever a `put` lands and then times out. The
    fake cache grew a `putStoresThenHangs` shape specifically so this row has teeth.

And **D4 is the one that actually shipped.** PR #178 lifted the day ceiling onto the cache
by copying the hour's design without the hour's proof; deleting `unaccrueDayPending()` left
the ceilings suite 151/151 green and `test_demo_proxy.mjs` green, while U2 — the hour's
byte-identical branch — reddens instantly. It survived review, a 151-check suite and a
merge, and only a hand-run sweep found it. Every `W*`/`D*` row below exists because that
happened once.

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
#: is one function's arithmetic plus one isolate-local ledger — and the day/wide tier added
#: on top of it is a second copy of exactly that, with its own ledger and its own key mark.
LIMITS = WT / "functions/api/_lib/limits.js"

#: The suite. Run whole (about 1.5 s), because running it whole is what lets the selector
#: column check that the RIGHT assertion reddened.
SUITE = "sim/test_demo_proxy.mjs"

#: The DAY/WIDE tier's suite — `sim/tests/helpers_shared_ceilings.mjs`, run by
#: `sim/tests/test_shared_ceilings.py` under `pytest sim/tests`. It is a SECOND runner and
#: not a second section because `sim/test_demo_proxy.mjs` was reserved to another agent for
#: the whole of that slice; the file's own header says so. It prints the same `  - <label>`
#: line per failed check and exits non-zero, which is the only shape this table's runner
#: needs, so nothing about the row format changes when a row names it.
CEILINGS = "sim/tests/helpers_shared_ceilings.mjs"

#: Seconds one mutated run may take before it is treated as caught-by-hanging. `SUITE` is
#: ~1.5 s and `CEILINGS` ~0.3 s; a mutation that wedges a deadline would be caught, but only
#: if something ends it.
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
     SUITE, "FOUR shared entries"),

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
    # `let`, not `const`, since 2026-09-06: `chargeExtra()` raises `owed` when a re-rolled
    # turn commits a second completion (§4.9). The mutation is unchanged — drop the
    # `hourly` guard and an uncapped deployment starts accruing a ceiling it does not have.
    ("U11 accrue units on a deployment with no hourly ceiling to mirror", LIMITS,
     "  let owed = budget && budget.hourly && budget.charged && budget.charged.length",
     "  let owed = budget && budget.charged && budget.charged.length",
     SUITE, "uncapped deployment accrues nothing"),

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
    # =========================================================================
    # THE DAY/WIDE TIER. Rows W* and D*, added 2026-09-06, and the reason they exist is
    # not symmetry.
    #
    # PR #178 lifted the per-IP hour/day windows and the unit budget's DAY ceiling onto
    # `caches.default` by copying the HOUR's proven design onto the DAY — and not the
    # hour's proof. The gap was PREDICTED (live-sim-demo.md §912 warned these anchors were
    # at risk from that slice) and then not covered: neither this table nor
    # `turnstile_mutation_check.py` held a single row naming `sharedDayBudget`,
    # `sharedWideWindow`, `unitsDay`, `DAY_MARK` or `WIDE_MARK`. What that cost, measured:
    # deleting `unaccrueDayPending()` from `refundBudget()` left the ceilings suite
    # 151/151 GREEN and `sim/test_demo_proxy.mjs` green, while the HOUR's byte-identical
    # branch (row U2) reddens instantly. The day's un-accrual was dead code the suite could
    # not reach. PR #180 added the section E case that reaches it (151 -> 155 checks); row
    # D4 below is what stops that case from being deleted again.
    #
    # These rows name `CEILINGS`, not `SUITE`, because that is where the tier's proof
    # lives. Everything else about them is the same contract: one anchor, matched exactly
    # once, and a selector that must appear in a FAILING check's own label.
    # =========================================================================

    # ---- W1: the ordering #178 fixed, put back --------------------------------
    # The wide check runs BEFORE the minute's write. Moving it after is the tidier-looking
    # arrangement (one `await` further from the read it pairs with) and it costs a refused
    # request a cache write it did not earn: the minute entry ends up counting a turn the
    # hour then refused, so the stored count is ABOVE the truth. That is an OVERCOUNT, the
    # one direction §4.6.1 says this tier may never fail in.
    ("W1  the wide check moved back AFTER the minute write (a refusal that costs a write)",
     LIMITS,
     "  const wider = await sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });\n"
     "  if (wider) return wider;\n"
     "\n"
     "  // ---- op 2: write back. Unlocked and on purpose — a lost update undercounts (2).\n"
     "  // `max-age` is one window, so an entry outlives its own bucket by at most that and then\n"
     "  // evicts itself; the key already carries the bucket, so nothing stale can be believed.\n"
     "  const wrote = await withDeadline(cfg.cacheTimeoutMs, () =>\n"
     "    store.put(\n"
     "      key,\n"
     "      new Response(JSON.stringify({ n: used + 1 }), {\n"
     "        headers: {\n"
     '          "Content-Type": "application/json",\n'
     '          "Cache-Control": "max-age=" + SCALES.min,\n'
     "        },\n"
     "      }),\n"
     "    ),\n"
     "  );\n"
     "  if (wrote === CACHE_TIMEOUT) c.timeouts += 1;\n"
     "  else if (wrote === CACHE_ERROR) c.errors += 1;\n"
     "  else {\n"
     "    c.ops += 1;\n"
     "    c.wrote += 1;\n"
     "  }\n"
     "  c.allowed += 1;",
     "  // ---- op 2: write back. Unlocked and on purpose — a lost update undercounts (2).\n"
     "  // `max-age` is one window, so an entry outlives its own bucket by at most that and then\n"
     "  // evicts itself; the key already carries the bucket, so nothing stale can be believed.\n"
     "  const wrote = await withDeadline(cfg.cacheTimeoutMs, () =>\n"
     "    store.put(\n"
     "      key,\n"
     "      new Response(JSON.stringify({ n: used + 1 }), {\n"
     "        headers: {\n"
     '          "Content-Type": "application/json",\n'
     '          "Cache-Control": "max-age=" + SCALES.min,\n'
     "        },\n"
     "      }),\n"
     "    ),\n"
     "  );\n"
     "  if (wrote === CACHE_TIMEOUT) c.timeouts += 1;\n"
     "  else if (wrote === CACHE_ERROR) c.errors += 1;\n"
     "  else {\n"
     "    c.ops += 1;\n"
     "    c.wrote += 1;\n"
     "  }\n"
     "  const wider = await sharedWideWindow(store, request, { ip, route, cfg, nowS, tag });\n"
     "  if (wider) return wider;\n"
     "  c.allowed += 1;",
     CEILINGS, "a refusal may not leave a counter ABOVE the truth"),

    # ---- W2: the staleness argument, deleted ----------------------------------
    # The wide entry rotates DAILY because the day is the widest scale it holds, so the
    # HOUR's freshness cannot ride in the key the way the minute window's does — it rides
    # in the body, as a bucket stamped beside the count. Trusting the count without the
    # stamp believes an 03:00 count at 20:00 and refuses somebody who has spent nothing.
    ("W2  the wide entry's BUCKET STAMP ignored, so a closed hour's count is believed",
     LIMITS,
     "    const stored = body && Number(body[bField]) === b ? Number(body[nField]) : 0;",
     "    const stored = body ? Number(body[nField]) : 0;",
     CEILINGS, "a count stamped with a DIFFERENT bucket reads as zero, not as this hour's"),

    # ---- W3: fail open turned into fail closed, in the wide half --------------
    ("W3  a store that HANGS refuses the wide window instead of admitting (fail closed)",
     LIMITS,
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
    # `windowArity` is 3 for BOTH shapes, so arity cannot tell them apart; the mark is the
    # whole of the separation, and it works only because a decimal integer cannot begin
    # with a letter. Empty it and a wide entry can spell a minute entry.
    ("W4  the WIDE mark emptied, so a wide key can spell a narrow one", LIMITS,
     'const WIDE_MARK = "w";',
     'const WIDE_MARK = "";',
     CEILINGS, "the two wide shapes are marked by a LETTER"),

    # ---- W5: the wide entry outlives its own day ------------------------------
    # Its key rotates DAILY because the day is the widest scale it holds, so `max-age` has
    # to be the day too. A shorter one is not wrong in the dangerous direction — it throws
    # the hour count away and admits — but a ceiling that quietly stops binding is the
    # failure this whole tier exists to make visible.
    ("W5  the WIDE entry given a MINUTE's max-age instead of its own day's", LIMITS,
     "      new Response(JSON.stringify(next), {\n"
     "        headers: {\n"
     '          "Content-Type": "application/json",\n'
     '          "Cache-Control": "max-age=" + SCALES.day,',
     "      new Response(JSON.stringify(next), {\n"
     "        headers: {\n"
     '          "Content-Type": "application/json",\n'
     '          "Cache-Control": "max-age=" + SCALES.min,',
     CEILINGS, "it lives exactly ONE DAY — the widest scale it holds"),

    # ---- W6: a failed READ that writes anyway ---------------------------------
    # The wide half of U5, and the plausible tidy-up is the same one: record the error and
    # fall through instead of returning. `body` is then the error sentinel, every bucket
    # stamp reads as NaN, both counts read as zero, and the `put` RESETS a live window to
    # this isolate's single turn. NOT CAUGHT until section F grew a case whose seeded entry
    # differs from what a fresh write produces — see that block's own note.
    ("W6  a failed WIDE read publishes anyway, resetting a live window", LIMITS,
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
    # `>=` and not `>`, for the reason the day budget states next door: the local request's
    # own cost is already accounted for by the in-isolate map that ran first, so a colo
    # standing exactly ON the ceiling has spent it.
    ("W7  the wide window's ceiling comparison off by one (> instead of >=)", LIMITS,
     "    if (used >= ceiling) {\n"
     "      w.refused += 1;",
     "    if (used > ceiling) {\n"
     "      w.refused += 1;",
     CEILINGS, "the colo has seen three"),

    # ---- W8: which scale answers when both are spent --------------------------
    # `scales` is narrowest-first so a visitor who has spent both their hour and their day
    # is told to come back at the top of the hour, not tomorrow. Reversing it was caught by
    # NOTHING that names a refusal until section F grew the case this row points at — §H's
    # deep-equality on the stored body reddened, because the array order is also the JSON
    # field order, and a row caught by a serialization detail proves nothing about the
    # ordering it claims to be about.
    ("W8  the wide scales built WIDEST-first, so the day answers before the hour", LIMITS,
     '  if (limits.hour) scales.push(["hour", limits.hour, "h", "hb"]);\n'
     '  if (limits.day) scales.push(["day", limits.day, "d", "db"]);',
     '  if (limits.day) scales.push(["day", limits.day, "d", "db"]);\n'
     '  if (limits.hour) scales.push(["hour", limits.hour, "h", "hb"]);',
     CEILINGS, "the shortest Retry-After that applies wins"),

    # ---- D1: charge-at-admission, in the DAY dimension ------------------------
    # §4.6.2's refund rule is what the day inherits along with the hour's design: the colo
    # is never told about a charge that might have to be given back. Settling the DAY
    # ledger without asking whether the request was REFUNDED is that rule dropped — and it
    # is charge-at-admission's observable shape, because a shared entry has no refund write
    # to correct it with. 200 tokenless POSTs then publish 600 units nobody spent. U1 is
    # the same mistake in the hour; this row leaves the hour CORRECT, so it is caught only
    # by a proof the day owns.
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
    # The day's half of U3. The ledger clears on the `put` ATTEMPT, not on its
    # confirmation, because a write that lands and then times out has landed: keeping the
    # units to retry them publishes them TWICE, and a double charge is an overcount, which
    # refuses somebody. The fake cache's `putStoresThenHangs` exists for exactly this shape.
    ("D2  keep the DAY units after a write ATTEMPT, to retry them (a double charge)",
     LIMITS,
     "    d.published += owedDay;\n    clearDayPending(db);",
     "    d.published += owedDay;",
     CEILINGS, "a put that LANDS AND THEN HANGS still clears the ledger"),

    # ---- D3: the comparison forgets this isolate's day ledger -----------------
    ("D3  compare only the PUBLISHED day count, ignoring this isolate's unpublished spend",
     LIMITS,
     "  if (spent + owedDay >= ceiling) {",
     "  if (spent >= ceiling) {",
     CEILINGS, "the colo has seen 12"),

    # ---- D4: THE ONE THAT SHIPPED --------------------------------------------
    # Deleting this line is what PR #178 effectively did and what a 151-check green suite,
    # a 151-check review and a merge all missed. It is dead code unless a case RELEASES
    # before it REFUNDS — which no route in this repo does today and nothing prevents — so
    # section E's second half was written to reach it. This row is the lock on that case.
    ("D4  the DAY's un-accrual deleted (the dead branch #178 shipped and #180 caught)",
     LIMITS,
     "        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above\n"
     "        unaccrueDayPending(dayBucket, owedDay);",
     "        unaccruePending(hourBucket, owed); // the release-then-refund ordering; see above",
     CEILINGS, "straight back out of the DAY ledger too"),

    # ---- D5: the entry outlives its own day ----------------------------------
    ("D5  the DAY budget entry given a MINUTE's max-age instead of its own day's", LIMITS,
     "        new Response(JSON.stringify({ n: spent + owedDay }), {\n"
     "          headers: {\n"
     '            "Content-Type": "application/json",\n'
     '            "Cache-Control": "max-age=" + SCALES.day,',
     "        new Response(JSON.stringify({ n: spent + owedDay }), {\n"
     "          headers: {\n"
     '            "Content-Type": "application/json",\n'
     '            "Cache-Control": "max-age=" + SCALES.min,',
     CEILINGS, "the day entry's max-age is ONE DAY"),

    # ---- D6: the publish that always runs, day-side --------------------------
    # `owedDay > 0` is what makes a refused request cost ZERO day writes. Without it the
    # free drain is not closed structurally at all — it is 200 no-op writes on the single
    # hottest key in the colo, each one publishing `spent + 0`.
    ("D6  publish the day on every admission, even when the isolate owes nothing", LIMITS,
     "  if (owedDay > 0) {",
     "  if (owedDay >= 0) {",
     CEILINGS, "zero writes attempted, which is the structural half of the claim"),

    # ---- D7: the day roll ----------------------------------------------------
    # Yesterday's crumbs carried into today's entry are spend recorded against a day that
    # did not spend it, which refuses somebody TOMORROW. Dropping them undercounts, which
    # is the direction this tier is allowed to be wrong in; the `dropped` counter is what
    # keeps the drop from being silent.
    ("D7  carry yesterday's unpublished units INTO today's entry", LIMITS,
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
    # The CATASTROPHIC direction, one scale wider than U6: a colo whose cache is having a
    # bad day answers every visitor `budget_exhausted` for a DAY rather than for an hour,
    # and the page paints SCRIPTED the whole time.
    ("D8  a cache that HANGS refuses the DAY budget instead of admitting (fail closed)",
     LIMITS,
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
    # `unitsArity` is 2 for the hour key AND for the day key, so — exactly as for W4 — the
    # mark is the whole separation. Empty it and `.../units/d0` becomes `.../units/0`,
    # which is the hour key of hour 0: the deployment's DAY spend and its 00:00 hour merge
    # into one counter.
    ("D9  the DAY mark emptied, so the day key can spell an hour key", LIMITS,
     'const DAY_MARK = "d";',
     'const DAY_MARK = "";',
     CEILINGS, "the day budget entry is origin + prefix + 'units' + d + the DAY bucket"),

    # ---- D10: the sub-tier deleted, one scale wider --------------------------
    # U8's day. `sharedBudgetVerdict` ends by handing off to the day, and an hour refusal
    # returns above it — so deleting the hand-off leaves a deployment with an hour ceiling
    # and no day ceiling at all, silently, on a route whose hour never fills.
    ("D10 the DAY budget sub-tier never consulted at all", LIMITS,
     "  return sharedDayBudget(store, request, { cfg, nowS });\n}",
     "  return null;\n}",
     CEILINGS, "reads FOUR shared entries"),

    # ---- D11: the uncapped deployment, day-side ------------------------------
    # U11's day, and `budget.daily` is what makes a deployment that caps the HOUR but not
    # the day accrue to exactly the ledger it has a ceiling for. Without it the day ledger
    # fills for a ceiling that does not exist — harmless while it stays 0, and a backlog
    # published in one burst the day an operator sets one. NOT CAUGHT until section J grew
    # the ledger assertion; the two lines it already had watch the SUB-TIER, and this
    # accrual happens in `release()`, which consults no sub-tier at all.
    # `let`, not `const`, for U11's reason.
    ("D11 accrue DAY units on a deployment with no daily ceiling to mirror", LIMITS,
     "  let owedDay = budget && budget.daily && budget.charged && budget.charged.length",
     "  let owedDay = budget && budget.charged && budget.charged.length",
     CEILINGS, "the DAY ledger never accrued a unit either"),

    # ---- U18: the re-roll spends past the ceiling ----------------------------
    # `chargeExtra()` is the ONLY thing standing between `chat.js`'s second gateway call
    # and a budget that has already run out. It returning `true` on a refused charge is one
    # character, it costs money rather than availability, and it is invisible in a green
    # suite for exactly the reason this file's header gives: an admission that should have
    # been a refusal is a slightly larger bill and nobody's complaint.
    ("U18 the re-roll's extra charge is taken even when the ceiling refused it", LIMITS,
     "      const extra = chargeBudget(route, ctx.cfg, ctx.nowS);\n"
     "      if (!extra.ok) return false;",
     "      const extra = chargeBudget(route, ctx.cfg, ctx.nowS);\n"
     "      if (!extra.ok) return true;",
     SUITE, "with no headroom for a second completion the re-roll DOES NOT HAPPEN"),

    # ---- U19: the refund forgets what the re-roll spent ----------------------
    # The mirror of U18, and the OTHER direction: a refund that hands back only the
    # admission's 3 units leaves 3 units charged against an hour by a request that was
    # refused. An overcount, which is the direction this tier may never fail in. Unreachable
    # through `chat.js` today — every refusal that refunds is upstream of the gateway call —
    # so §6c drives the ordering directly on a bare slot rather than through a route.
    ("U19 a refund gives back only the admission's units, not the re-roll's", LIMITS,
     "      refundCharges([], budgetKeys, (budget && budget.cost) || 0);",
     "      refundCharges([], (budget && budget.charged) || [], (budget && budget.cost) || 0);",
     SUITE, "a refund gives back BOTH charges, not just the admission's"),
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
