#!/usr/bin/env bash
# Merge gate for a PR into dev: exit 0 only when EVERY check in the PR's status rollup is
# COMPLETED with conclusion SUCCESS, at least MIN checks exist, and the SIL job is among them.
# `gh pr checks` alone is not a verdict — a queued job can be missing from its output and a
# transient API error prints nothing, both of which an "any non-pass?" grep reads as green
# (PRs #43–#48 on 2026-09-02 merged ~2 min after opening while the 6-min SIL job still ran).
#   usage: bash scripts/pr-green.sh <pr-number> [min-checks]   (needs GH_TOKEN)
set -euo pipefail
PR="${1:?pr number}"; MIN="${2:-3}"
json=$(gh pr view "$PR" --json statusCheckRollup --jq '.statusCheckRollup') || { echo "gate: gh failed"; exit 2; }
python3 - "$json" "$MIN" <<'PY'
import sys,json
rs=json.loads(sys.argv[1]); need=int(sys.argv[2])
names=[(r.get("name") or r.get("context") or "") for r in rs]
bad=[r for r in rs if r.get("status")!="COMPLETED" or r.get("conclusion")!="SUCCESS"]
sil=any("SIL" in n for n in names)
for r in bad: print("  not green:", (r.get("name") or r.get("context")), r.get("status"), r.get("conclusion") or "-")
if len(rs) < need: print(f"gate: only {len(rs)} checks reported (need >= {need})"); sys.exit(1)
if not sil: print("gate: the SIL job is not in the rollup yet"); sys.exit(1)
if bad: print(f"gate: {len(bad)} of {len(rs)} checks not green"); sys.exit(1)
print(f"gate: all {len(rs)} checks completed + successful (SIL included)")
PY
