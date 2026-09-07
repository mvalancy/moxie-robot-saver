# 🎯 `test_liveliness.mjs`'s head-travel floor is a measurement of the machine

**Filed 2026-09-07. Not started.** The **twelfth** instance of the defect family this project spent
2026-09-06 removing — *an assertion whose result depends on the machine rather than on the product* —
and the first one **this project introduced while fixing that very family.**

## Where

`sim/test_liveliness.mjs`, block 4a, the drive that reproduces the bubble-anchor race:

    ok(spread > 40, `…and the drive really swung her head across the screen (${spread}px of travel)`);

Block 4a arrived in **PR #203 (`bubbleframe`)**, whose entire purpose was replacing a live-sample
comparison with a single-instant one. It succeeded at that and, in the same commit, added a floor that
only clears when the machine is fast enough.

## The evidence

Interleaved A/B, three pairs, 40 burner processes, load average 60–80, pristine `origin/dev` against a
branch that does not touch this block:

| pair | pristine `dev` | the branch |
|---|---|---|
| 1 | ✗ (hold checks only) | ✓ |
| 2 | **✗ travel = 31 px** | **✗ travel = 37 px** |
| 3 | **✗ travel = 30 px** | ✓ |

**Red on both sides**, which is how it is known to be independent of any branch — and 2 of 3 on
pristine, against a 40 px floor.

## The mechanism

Each sweep commands motor 6 and motor 4 to opposite ends and then waits a **fixed four frames**. Four
frames is a quantity of *machine*, not of motion: `animate()` smooths each DOF by
`k = 1 - exp(-dt * 7)` with `dt` **clamped to 0.1 s**, so a starved runner advances the arc *less* per
sweep, not more. Frames where the anchor is frozen are skipped as well, shrinking the sample further.
The assertion then reads that shortfall as a defect in the drive.

## What was tried, and why it is filed rather than fixed

The obvious repair — poll `window.moxie.getMotor(6)` until the smoothed value reaches the commanded
end, bounded and loud — **was written and did not work**. It failed with
`motor 6 never reached 0 in 90 frames (stalled at 15910)`, i.e. barely moved from its 16384 start. A
standalone probe then showed the motor converging **16384 → 0 in ~20 frames** with no trouble at all,
so the stall is caused by something the test does and the probe does not (the per-sweep `setSpeech()`
re-issue is the first suspect, not a conclusion). **That contradiction is unexplained, and the fix was
reverted rather than shipped on a guess.**

## Acceptance criteria

- The mechanism behind the 90-frame stall is **measured**, not reasoned about — the probe/test
  divergence is the thread to pull.
- The sweep waits on a **condition** (the drive having arrived) rather than a frame count, bounded,
  failing loudly and naming what never happened.
- Proven both directions: reddens on the unfixed anchor, and survives an interleaved A/B under load
  that reddens pristine `dev`.
- The block keeps its purpose — it exists to make the anchor race reproducible, and must still redden
  if the readout goes back to mixing two instants.

## Effort

Small, but genuinely unknown until the stall is explained. Needs a quiet machine for the A/B.

## Files

`sim/test_liveliness.mjs` (block 4a only), `sim/web/moxie.js` (read-only: the `animate()` smoothing and
the `getMotor` seam).
