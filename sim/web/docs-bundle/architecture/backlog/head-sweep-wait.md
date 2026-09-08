# The head-sweep wait: a good idea, a failed implementation, and one honest number

**Status (2026-09-08): REVERTED on `dev` (`sim/test_liveliness.mjs` restored to `49c35c6`). The
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

## What to do next

1. **Keep the split verdict.** It is the part that demonstrably worked: `3/16 sweeps timed out`
   alongside `31px of travel` reads in one glance, where the old single assertion prints `31px` and
   leaves the reader guessing. It can be added to the *existing* wait without changing the wait.
2. **Measure the mechanism before rewriting the wait again.** Instrument per-sweep frame counts and
   head-y ranges for both versions under matched load. The question is concrete: why does a loop that
   runs *at least* as many frames per sweep record *less* total head travel?
3. **Run it on a quiet machine, paired and interleaved**, with load sampled per run rather than assumed.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Orchestration log](../orchestration-plan.md)
