#!/usr/bin/env bash
# The only scheduler-safe live goodbye-tag measurement. Its one rate check needs three
# successful completions; six is a ceiling for retries, never a spending target.
# MOXIE_MODEL_CALL_LIMIT is enforced in the SDK immediately before every request attempt
# (including retries); GNU timeout owns one deadline for the entire pytest process. Do
# not add the activity or wire test without a separate budget and owner decision.
set -euo pipefail

PROBE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROBE_ROOT"

exec timeout --foreground --kill-after=5s 360s \
  env MOXIE_MODEL_CALL_LIMIT=6 \
  python3 -m pytest \
    sim/tests/test_live_action_tags.py::test_the_model_ends_a_goodbye_with_a_real_exit_action \
    -q -ra
