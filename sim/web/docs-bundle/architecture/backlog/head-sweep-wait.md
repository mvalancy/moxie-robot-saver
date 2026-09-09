# The head-sweep wait: a good idea, a failed implementation, and one honest number

**Status (2026-09-08): the diagnostic half is SHIPPED (`feat/sweepdiag`); the WAIT is still the
original and should stay that way — see "Step 2" below, which retires the premise the rewrite was
argued from. Rewrite REVERTED on `dev` (`sim/test_liveliness.mjs` restored to `49c35c6`). The
DEFECT it aimed at is real and still present. The rewrite is not the fix.**

## The defect, which has not gone away

`sim/test_liveliness.mjs` asserts `spread > 40` — *the drive really swung her head across the screen* —
after waiting a fixed `for (let f = 0; f < 4; f++)` per sweep. `animate()` clamps `dt` to 0.1 s, so
four frames on a starved runner advance her a fraction of what four frames advance her on an idle one.
**"Four frames happened" and "she swung" are different claims that agree only while the runner is
fast** — the same fixed-wait-standing-in-for-a-condition shape recorded a dozen times in
[the orchestration log](../orchestration-plan.md).

## What was tried

Four frames kept as a *floor*, the sweep ending instead on the observation the assertion wants (6 px of
measured head travel) or a 1.5 s per-sweep deadline, and the verdict **split in two** so a red says
which of two incompatible stories produced it:

| Check | Red means |
|---|---|
| `res.starved === 0` | the runner never gave her the frames — an environment fact |
| `spread > 40` | the drive genuinely did not move her — a real defect |

## What happened

```
r1 FIXED    load=66.74 :: FAIL  (3/16 sweeps timed out; 31px of travel)
r1 PRISTINE load=78.85 :: PASS
```

**The old code passed, at a load 18% higher than the one the rewrite failed at.** A prediction
committed *before* that arm reported said `PRISTINE` **must** fail at no more than 31 px, reasoning
that the rewrite runs a floor of four frames and exits early only on a condition the old code never
tested, so it can never run fewer frames — and fewer frames cannot mean more travel.

**That reasoning is wrong and the mechanism is still unknown.** Three attempts to derive it from source
found nothing: `setSpeech` is DOM-only, the push condition is logically equivalent to the old
`continue`, the frame floor is identical. Guesses are not recorded here, because a plausible story is
what this repo keeps mistaking for a finding.

## Why it was reverted rather than tuned

Lengthening the deadline until the red disappears is exactly what the committed terms forbade
(*"treated as a regression until that is explained — not patched until it goes green"*). It would also
have been tuning against `n=1`.

**Honest limits of that number.** One pair, at loads that differed by 12, several minutes apart — and
the machine was concurrently running unrelated test suites whose load I did not control and only
noticed afterwards. `n=1` falsifies a *"must"*, which is all a universal claim needs. It does **not**
establish that the rewrite is worse on average, and that claim is not made.

## Step 1 is done: the red now says which story it is (2026-09-08)

The split verdict was added to the *existing* wait, without changing it. `for (let f = 0; f < 4; f++)`
is verbatim, there is no new `ok()` and no new red condition, and the suite still reports 96 checks.
Only the failure TEXT changed: the head-y range inside each sweep is recorded while sampling, and a
red now also reports how many of the 16 sweeps moved her less than the 6 px the assertion is really
about. Exercised by temporarily raising the threshold, a red reads:

```
…and the drive really swung her head across the screen
  (53px of travel; 0/16 sweeps moved her <6px, so the drive itself did not swing her)
```

## Step 2 is done, and it overturned the premise this page was written on

Instrumented both shapes to report, per sweep: frames run, mean frame interval, rows placed, head-y
range, and — the measurement that settled it — **motor 6's convergence fraction toward its target**,
which unlike a pixel count is independent of viewport and camera. Run paired and interleaved, one
fresh page per arm, load sampled per run. Frame duration was varied with CDP
`Emulation.setCPUThrottlingRate` rather than by loading the machine: it reaches the same starved
regime, it is confined to the browser under test, and it is repeatable.

### Finding 1 — the dt clamp is a FLOOR on progress per frame, not a ceiling

This page said *"four frames on a starved runner advance her a fraction of what four frames advance
her on an idle one."* **That is backwards.** `animate()` does `dt = Math.min(clock.getDelta(), 0.1)`,
so a slow frame advances the simulation by up to 0.1 s where a fast 16 ms frame advances it by 0.016 s.
Motor smoothing is `k = 1 - exp(-dt * 7)`, so four frames converge motor 6 by
`1 - (1 - k)^4` — which **rises** with frame duration and then saturates at the clamp:

| frame interval | measured convergence in 4 frames | predicted `1-(1-k)^4` |
|---|---|---|
| 48–69 ms (400×300, unthrottled) | 0.777 – 0.864 | 0.78 – 0.87 |
| ≥ 100 ms (1280×900, every throttle) | **0.939, exactly, every sweep** | 0.939 |

A starved runner does not measure less travel. It measures *more*, up to the clamp, and then flat.

### Finding 2 — in the whole full-viewport regime the two shapes are the same program

At 1280×900 on this machine frames are ≥ 85 ms, dt is at or near the clamp, and four frames converge
the motor to 0.939 — which is far more than the 6 px the rewrite's break condition asks for. So the
condition fires at its floor `f === 3` in **every one of the 16 sweeps of every full-viewport run**:

```
throttle  1x   fixed 4f/sweep  spread 47.8 48.6 52.5      cond 4f/sweep starved=0  spread 50.5 50.3 46.7
throttle  4x   fixed 4f/sweep  spread 53.5                cond 4f/sweep starved=0  spread 52.7
throttle 10x   fixed 4f/sweep  spread 51.6                cond 4f/sweep starved=0  spread 55.4
throttle 20x   fixed 4f/sweep  spread 50.3                cond 4f/sweep starved=0  spread 53.5
```

fixed mean **50.7** (n=6), cond mean **51.5** (n=6), interleaved, frame durations spanning 85→450 ms.
**The rewrite never ran an extra frame here.** Whatever produced `31px`, it was not the difference
between the two waits, because in this regime there is no difference between them to produce it.

The shapes diverge in exactly one regime, and it is the *fast* one: at 400×300, where frames are
48–69 ms and dt is below the clamp, `fixed` ran 4 frames a sweep (conv 0.78–0.86, motor 6 oscillating
only between ~5 000 and ~27 000) while `cond` ran 4–29 and starved 4/16 (conv 1.0, motor 6 reaching
0 and 32767 outright). There `cond` recorded **more** travel, 8 px against 5.7 px — the direction the
rewrite's author predicted, in the only conditions where the rewrite is a different program.

### Finding 3 — a rAF frame is not an `animate()` step, and that is the real starvation channel

`conv` intermittently collapses while the frame interval is unchanged: 0.563 at 88 ms, 0.626 and
0.330 at ~170 ms, 0.463 at 249 ms. Those are not noise — 0.939 is four clamped steps, 0.75 is two,
0.503 is one, and the observed values sit on that ladder. **A sweep can burn four of the test's
`requestAnimationFrame` turns while the page's own `animate()` rAF advances the physics only once or
twice.** It hit both arms about equally (3 runs each), so it favours neither — but it is precisely
what a wait on a *frame count* cannot protect against, because the frames genuinely happened.

## What is still NOT established

**Why that one run recorded 31 px is unknown, and this page no longer has a mechanism to offer for it.**
It was observed at machine load 66.74; that load was not recreated here (the machine was in use by
other agents, and manufacturing load on a shared box was ruled out in favour of CDP throttling, which
reproduces slow frames but not memory pressure, GPU contention, or scheduler starvation of the
compositor). Ruled out, with numbers: frame-duration effects on convergence (Finding 1), any
behavioural difference between the two waits in this regime (Finding 2, where the rewrite executes
the identical 4-frame loop). Finding 3 is a candidate — sustained rAF/animate decoupling would starve
whichever arm ran during it — but it was measured as symmetric, so it explains a bad *run*, not a bad
*shape*.

## What to do next

1. **Do not re-land the rewrite on the reasoning this page used to carry.** Its stated justification —
   that a starved runner advances her less per frame — is measurably false. If it is re-landed it needs
   a different argument, and one that survives Finding 2: on any runner slow enough to matter, it is
   the same program as the code it replaces.
2. **Any real fix is upstream of the wait.** Finding 3 says the quantity the test needs is `animate()`
   steps, not rAF turns. The page could simply count them and expose the counter; then "she was given
   room to move" becomes a fact the page reports rather than a proxy the test guesses at.
3. **Reproduce 31 px before theorising about it again.** It has been seen once, and three explanations
   have now been offered for it, of which this page has retired two.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Orchestration log](../orchestration-plan.md)
