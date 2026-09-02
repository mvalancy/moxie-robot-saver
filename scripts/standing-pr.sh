#!/usr/bin/env bash
# Print the number of the open standing promotion PR (dev → main), or "none".
# The PR number changes on every promotion (a squash-merge closes the old one),
# so loops MUST resolve it here rather than hardcoding "1". See
# .claude/skills/running-layered-session-loops/SKILL.md rule 7.
set -euo pipefail
gh pr list --base main --head dev --state open --json number \
  --jq 'if length > 0 then .[0].number else "none" end' 2>/dev/null || echo "none"
