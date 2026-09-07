# 🔥 `run_smoke.sh` is load-sensitive — the stack exercise cannot currently tell "broken" from "busy"

**Filed 2026-09-06. Not started.** This is the **eleventh** instance of the defect family this repo
spent 2026-09-06 removing — *an assertion whose result depends on the machine rather than on the
product* — and it is the one sitting under the INTEGRATION tier's own primary instrument.

## The evidence, and why it is not a suspicion

The `clockbudget` agent (PR #206) ran an **interleaved A/B** while measuring something else:

| tree | smoke result |
|---|---|
| pristine `origin/dev` | **6 of 7 OK** |
| `feat/clockbudget` | **6 of 9 OK** |

**Failures on both sides**, and that branch's diff touches none of the files the smoke loads. A
second, independent observation the same day: the orchestrator's own hermetic run reported
`test_p95_under_one_millisecond` failing at load average 42 and passing alone at 0.45 s on the same
commit — the pytest expression of the same cause, now fixed in #206. The smoke is the layer above,
unfixed.

## Why it matters more than an ordinary flake

`sim/run_smoke.sh` is what the INTEGRATION tier runs every fire to answer *"does a real turn
round-trip through the built backend?"*. It asserts the TTS audio round-trip and the
`state → config(paired) → remote-chat → reply` chain. **A gate that reddens when the box is busy
teaches its reader to re-run it**, and by this repo's standing rule (playbook rule 30, and the
Definition of done's criterion 6) that is how a real defect stays hidden. It has already cost a
misread once: an orchestrator recorded a smoke failure as a merge regression before an A/B showed
pristine `dev` failing identically.

## MEASURED 2026-09-07 — the rate, and a threshold claim this section then RETRACTS

The first acceptance criterion below is now satisfied. Same commit throughout (`02e7b47`), same
script, load generated with `while :; do :; done` burner processes on a 24-core box:

| condition | load average | failures |
|---|---|---|
| quiet | **3.3** | **0 of 3** |
| moderate | **19–33** | **0 of 5** |
| high | **123–156** | **2 of 7** |

**FIRST READING, SINCE RETRACTED:** *"eight consecutive passes and then ~30 % failing is a threshold,
not noise; below load 33 clean, above 120 fails about a third of the time."*

**RETRACTED THE SAME DAY, by more of the same measurement.** Two further rounds at the load that had
produced failures came back **clean**: 2 of 2 at load 75, then **3 of 3 at load 139–150** — the second
squarely inside the band the first round called failing. The corrected totals, all on `02e7b47`:

| condition | load average | failures |
|---|---|---|
| quiet → moderate | **3.3 – 75** | **0 of 13** |
| high | **123 – 156** | **2 of 10** |

So the honest statement is **not** a threshold with a clean edge. It is a **load-dependent
probability**: roughly one run in five fails above load ~120, and none of thirteen failed at or below
75. The difference between those rows is real; the sharpness the first reading claimed was not.

**Why the overstatement happened, since it is the same error twice in one day.** The first round was
8 clean runs followed by 2 failures, and *"threshold"* is the tidier story that shape suggests. Ten
more runs were enough to spoil it. A rate estimated from two failures has an interval wide enough to
hold almost any hypothesis, and calling it a threshold gave a number more authority than its sample
could carry — the same species of error as reporting a green obtained at the wrong load, which this
section already records two paragraphs down.

**A correction worth keeping, because it nearly closed this brief wrongly.** The first loaded attempt
returned **5 pass / 0 fail** and read as *"the smoke is fine"*. It was not: the burners had not ramped,
and that run peaked at load 19–33 — which the table above now shows is **below the threshold
entirely**. Reporting it would have retired a real defect on a measurement taken in the wrong regime,
which is exactly the failure mode that produced the other twelve instances. The rule that follows:
**state the load with the rate, always, or the rate means nothing.**

**Why this may matter for CI rather than only for developers.** GitHub's runners are 2–4 core, and the
browser suite takes **19–22 minutes** there against roughly 60 seconds locally. If this threshold is a
ratio of load to cores rather than an absolute, CI may sit near it routinely — which would be one
mechanism behind reds appearing on diffs that cannot reach the code, the pattern that started this
whole line of work on 2026-09-06.

## RETRACTED AGAIN, further the same day — load is NOT established as the cause

Two more rounds at high load, **8 runs, zero failures** (3 of 3 at load 139–150; 5 of 5 at load
125–150, this time with every run's full output preserved to its own file). Cumulative:

| condition | load average | failures |
|---|---|---|
| quiet → moderate | **3.3 – 75** | **0 of 13** |
| high | **123 – 156** | **2 of 15** |

**That difference is not significant.** Fisher's exact on 0/13 against 2/15 gives **p ≈ 0.49** — the
two rows are indistinguishable from a single rate. The honest statement is now weaker than either
earlier reading:

> `run_smoke.sh` failed **twice, both inside one round**, and 26 further runs across four rounds and
> four load regimes produced **no failure at all**. Load is a *hypothesis*, not a finding.

What those two failures share is not known to be load. They shared a round, a port range
(`1981`–`1985`), and whatever state the preceding runs left behind. Each of those is about as
well-supported as CPU pressure — which is to say barely.

**The methodological failure that made this take five rounds.** Every measurement wrapper used here
grepped for a success marker and **discarded the rest of the output**, so each failure was *counted*
and its evidence *destroyed in the same step*. The round that finally preserved full logs found
nothing to preserve. **A harness that records THAT something failed but not HOW converts a defect into
a statistic** — the same species of error as this repo's twelve assertion-level instances, relocated
into the instrument.

**How to settle it:** loop the smoke keeping every log, long enough to catch two or three failures,
then compare them. Roughly 30–50 runs at ~90 s each — an hour of wall clock, which is why this stays
filed rather than done.

## ANSWERED 2026-09-07 — the step is the config wait, and it is NOT the known race

A failure was finally caught **with its log intact**, on the sixth attempt, after the wrapper was
changed to write every run to its own file instead of grepping for a success marker and discarding the
rest. Run 4 of 4 at load 147:

    [virtual-moxie] connected to broker rc=Success as d_691d8ffd-…
    [virtual-moxie] subscriptions acknowledged by the broker
    [virtual-moxie] → state (software_version=24.10.803)
    ❌ SIL round-trip FAILED:
       - no config pushed within timeout

**The step is the config-push wait** — `sim/run_smoke.sh` line 241 passes `--timeout 20` to
`sim/virtual_moxie.py`, whose `got_config.wait(self.timeout)` raises exactly this message.

**And the ordering rules out the cause this project already knows about.** `subscriptions
acknowledged` precedes `→ state`, so the SUBSCRIBE had landed before the announce — this is **not**
the QoS-0-and-not-retained race that PR #143 fixed on 2026-09-04, whose whole signature is a state
published before the subscription exists. `run_smoke.sh:219-221` still carries the comment from that
episode saying *"no timeout here can be big enough"*; that remains true of the old race and is **not**
what happened here.

**So the answer to this brief's central question is: it is a fixed wait, but what it is measuring is
starvation.** Twenty seconds is not a tight budget. A supervisor that cannot answer a `/state` inside
it is not slow-by-design; it is a Python process competing with ~150 spinning cores. The defect is
that **the check cannot distinguish that from an appliance that is broken**, and its message — *"no
config pushed within timeout"* — reads unambiguously as the latter.

**Recommendation, now that the step is known:** the wait should report *which* it is. A supervisor
that is alive but starved can be told from one that is wedged by asking it (its status endpoint, or a
liveness ping on the same broker) when the wait expires, and saying so in the failure line. That
converts a 20-second silence from a verdict about the appliance into an observation about the machine
— which is the same correction `sim/test_csp.mjs` received in PR #209, where `"timeout"` was being
compared against `"loaded"` and `"refused"` as though it were a third verdict.

**Rates behind this, for completeness:** 0 failures in 21 runs at load 3.3–75; 3 in 19 above load 120.

## What the work is

1. **Measure before changing anything.** Establish the failure rate against pristine `dev` at a known
   load, and identify *which* assertion in the chain gives way first. The instrument for creating the
   condition rather than waiting for it already exists: `sim/tools/page_teeth_check.py --slow N`
   throttles a renderer over CDP, and the same CPU-oversubscription technique used in #203 and #206
   (extra burner processes, load 84–108) applies to the stack.
2. **Separate the two causes.** A smoke failure under load is either (a) a fixed wait standing in for
   a condition — the family's signature — or (b) a genuine capacity limit in the runtime, which is a
   *product* finding and far more valuable. #206's report suggests (a) but did not prove it.
3. **Fix only what is measured**, and prove each fix fails without itself.

## Acceptance criteria

- The smoke's failure rate against an unchanged tree is **stated as a number**, at a stated load.
- Every fixed wait converted to a condition wait, or annotated with why a duration is genuinely
  correct there ("prove nothing happens during this window" is honestly a duration).
- A give-up must fail **loudly**, naming which step never completed — never silently comparable to a
  real verdict. (This is the lesson from `sim/test_csp.mjs`'s three-way race, where `"timeout"` was
  asserted against `"loaded"`.)
- Any genuine capacity limit found is reported as a **product** finding, not smoothed away.

## Effort

Small-to-medium, but **it requires a quiet machine**. Filed rather than started on 2026-09-06 for
exactly that reason: the box was at load average 76–85 with two agents running, and briefing a
load-sensitivity investigation onto a thrashing box would manufacture the contaminated measurement
this whole family exists to eliminate.

## Files

`sim/run_smoke.sh`, `sim/run_scenarios.sh`, `sim/virtual_moxie.py`, `sim/tests/helpers_stack.py`.
