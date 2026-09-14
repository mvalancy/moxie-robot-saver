#!/usr/bin/env bash
# Print the open milestone promotion PR number (dev → main), or "none" between
# milestones. A squash merge closes it, so callers must resolve it rather than
# hardcoding an old number.
set -euo pipefail
gh pr list --base main --head dev --state open --json number \
  --jq 'if length > 0 then .[0].number else "none" end' 2>/dev/null || echo "none"
