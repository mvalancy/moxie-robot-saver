#!/usr/bin/env bash
# The only scheduler-safe live goodbye-tag measurement. The Python supervisor discards
# raw model/test output, emits one counts-only result, owns the total deadline, and exits
# nonzero for failed, incomplete, skipped, timed-out, or broken measurements. Six is a
# retry ceiling, never a spending target. Do not add activity or wire tests.
set -euo pipefail

PROBE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROBE_ROOT"

exec python3 sim/tools/run_live_action_tags.py
