# Test timing under load

**Status:** partly shipped — diagnostics landed in [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py)
(`_why_no_config`, tested by `sim/tests/test_why_no_config.py`) and
[`sim/test_liveliness.mjs`](../../../sim/test_liveliness.mjs) block 4a (step counts via
`getAnimationStepCount()` in [`sim/web/moxie.js`](../../../sim/web/moxie.js)); two failures remain
unexplained and no wait has been changed.

Two checks can go red because the machine is busy rather than because the product is broken. Both
now say which of the two happened when they fail. Neither cause is fully understood. This note
merges the former `smoke-load-sensitivity`, `head-travel-threshold` and `head-sweep-wait` briefs.

## 1. The stack smoke: `sim/run_smoke.sh`

**What fails.** Under heavy CPU load, the SIL round-trip sometimes dies with
`no config pushed within …s`. The robot (`sim/virtual_moxie.py`) has announced `/state`
and waits `--timeout` seconds (`CHAT_TIMEOUT`, 20 s by default, 60 s in live-brain mode) for the
supervisor's config push.

**What it is not.** It is not the old QoS-0 startup race fixed in PR #143. In the captured failure
log, `subscriptions acknowledged by the broker` came before `→ state`, so the subscription was in
place before the announce. `run_smoke.sh` also now stops with `|| exit 1` if the supervisor never
logs its own SUBACK. Before that guard, the script launched the robot into the race anyway.

**Measured rate** (one commit, burner processes on a 24-core box): 0 failures in 21 runs at load
average 3–75, and 3 in 19 above load 120. The difference is suggestive, not a clean threshold.
Earlier readings that claimed a sharp edge were withdrawn when more runs came in.

**What shipped.** When the config wait expires, the robot asks the supervisor's `/status` endpoint
(a different transport, capped at 3 s) and adds one of three verdicts to the failure line:

| Suffix | Meaning |
|---|---|
| `the supervisor IS ALIVE …` | reachable but did not answer `/state`: a lost message or a starved process, not a wedged appliance |
| `did NOT answer /status either …` | wedged, gone, or starved past 3 s |
| `liveness NOT CHECKED …` | no `--status-url` was passed, so the line says nothing either way |

`run_smoke.sh` passes `--status-url` in both the normal and the telehealth paths.

**Still open.**
- Nobody has checked whether a 20 s config wait is a real capacity limit of the Python supervisor
  under load. If it is, that is a product finding, and a longer timeout should not hide it.
- The load-to-cores ratio on 2–4 core GitHub runners may be close to the failing regime. CI
  (`ci.yml`, `bash sim/run_smoke.sh`) has not been measured for this.
- Rule for any future measurement: record the load average next to the rate, and keep every run's
  full log. A wrapper that greps for a success marker and throws the rest away can count failures,
  but it cannot explain them.

## 2. The head-sweep floor: `sim/test_liveliness.mjs` block 4a

**What fails.** Block 4a drives motors 6 (lean) and 4 (nod) end to end, 16 sweeps, to reproduce
the bubble-anchor race. It asserts `spread > 40` px of head travel. Each sweep waits a fixed
`for (let f = 0; f < 4; f++)` of `requestAnimationFrame` turns. Pristine `dev` went red at 30–31 px
in 2 of 3 A/B pairs at load 60–80.

**What the measurements established** (CDP `Emulation.setCPUThrottlingRate`, paired runs):

1. **A slow frame moves her further, not less far.** `animate()` in `sim/web/moxie.js` uses
   `dt = Math.min(clock.getDelta(), 0.1)` and smooths each motor by `k = 1 - exp(-dt * 7)`. Four
   frames converge the motor by `1 - (1-k)^4`. That goes up with frame length until the 0.1 s cap
   and then stays flat: 0.78–0.86 at 48–69 ms frames, and exactly 0.939 at ≥100 ms frames. The
   idea that "a starved runner advances her less per frame" is false.
2. **A condition-based wait would change nothing at full viewport.** At 1280×900, frames are
   ≥85 ms, so a "wait until she moved 6 px" loop exits at its 4-frame floor on every sweep. Fixed
   and conditional waits measured 50.7 px and 51.5 px on average (n=6 each). The rewrite that tried
   this was reverted: it failed once at 31 px while the old code passed at higher load.
3. **A test rAF turn is not a physics step.** Sometimes four test frames get only one or two
   `animate()` steps. Convergence then drops to 0.33–0.63 while the frame interval stays the same.
   This is the real starvation channel, and a frame-count wait cannot guard against it. It hit
   both arms about equally.

**What shipped.** The wait and the `spread > 40` threshold are unchanged. `window.moxie`
exposes a read-only, monotonic `getAnimationStepCount()`. The test records each sweep's step
delta and head-y range. When the threshold fails, the message reports how many sweeps got fewer
than 4 renderer steps and how many moved her less than 6 px. So a red shows whether the runner
never gave her the frames or the drive itself did not swing her.

**Still open.**
- The single 31 px failure has never been reproduced, so its cause is unknown.
- An earlier fix polled `getMotor(6)` until it reached its target and stalled at 15910 of 16384
  within 90 frames. A standalone probe with the same sweep, including the per-sweep `setSpeech()`,
  converged in about 20 frames. So `setSpeech()` is ruled out. The difference comes from something
  else in the full suite (earlier blocks, the shared harness, or `open()`), and nobody has found it.
- Do not change the wait until an instrumented red shows which clock stalled. If a fix waits on a
  condition, the condition should be `animate()` steps, not rAF turns. The block must still go red
  if the anchor readout starts mixing two instants again.

## Tools

`sim/tools/page_teeth_check.py --slow N` throttles the renderer over CDP. It reproduces slow frames
without loading a shared machine. It does not reproduce memory pressure, GPU contention, or
compositor scheduling.

---
📖 [Backlog index](README.md) · [Architecture index](../README.md) · [Orchestration log](../agent-workflow.md)
