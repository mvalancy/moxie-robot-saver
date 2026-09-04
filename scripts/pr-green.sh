#!/usr/bin/env bash
# Merge gate for a PR into dev: exit 0 only when EVERY check in the PR's status rollup is
# COMPLETED with conclusion SUCCESS, at least MIN checks exist, and EVERY REQUIRED JOB is
# among them by name.
# `gh pr checks` alone is not a verdict — a queued job can be missing from its output and a
# transient API error prints nothing, both of which an "any non-pass?" grep reads as green
# (PRs #43–#48 on 2026-09-02 merged ~2 min after opening while the 6-min SIL job still ran).
#
# WHY THE REQUIRED LIST IS A LIST AND NOT JUST "SIL". Absence is the failure mode this gate
# was written for, and absence is per-job: a rollup that has not listed a job yet looks
# exactly like a tier that does not have one. On 2026-09-04 the fast tier gained a third
# job — the Chrome-launching suites moved out of `sil` into a parallel `browser` job, so
# they would stop contending with ~5,000 broker-backed pytest tests on one runner — and a
# new job that the gate does not require is the SAME BUG IN A NEW SHAPE: a suite that
# cannot redden the gate. `MIN` alone would not have caught it either, since three jobs
# minus the missing one still meets the old default of 3.
#
# This list is held to the workflow by
# `sim/tests/test_ci_workflows.py::test_the_merge_gate_requires_every_job_in_the_fast_tier`,
# which fails if any job in sim/ci/ci.yml has a name no entry here matches. Add a job, and
# the suite reddens until this line names it.
#
#   usage: bash scripts/pr-green.sh <pr-number> [min-checks]   (needs GH_TOKEN)
set -euo pipefail
#: Case-sensitive substrings, one per job of the fast tier (sim/ci/ci.yml).
REQUIRED_JOBS="Docs,SIL,Browser"
PR="${1:?pr number}"; MIN="${2:-3}"
json=$(gh pr view "$PR" --json statusCheckRollup --jq '.statusCheckRollup') || { echo "gate: gh failed"; exit 2; }
python3 - "$json" "$MIN" "$REQUIRED_JOBS" <<'PY'
import sys,json
rs=json.loads(sys.argv[1]); need=int(sys.argv[2])
required=[s for s in sys.argv[3].split(",") if s]
names=[(r.get("name") or r.get("context") or "") for r in rs]
bad=[r for r in rs if r.get("status")!="COMPLETED" or r.get("conclusion")!="SUCCESS"]
absent=[req for req in required if not any(req in n for n in names)]
for r in bad: print("  not green:", (r.get("name") or r.get("context")), r.get("status"), r.get("conclusion") or "-")
if len(rs) < need: print(f"gate: only {len(rs)} checks reported (need >= {need})"); sys.exit(1)
if absent:
    print("gate: required job(s) not in the rollup yet: " + ", ".join(absent))
    print("      rollup names: " + ", ".join(names))
    sys.exit(1)
if bad: print(f"gate: {len(bad)} of {len(rs)} checks not green"); sys.exit(1)
print(f"gate: all {len(rs)} checks completed + successful ({', '.join(required)} all present)")
PY
