#!/usr/bin/env bash
# The only scheduler-safe live action-tag measurement. The two selected rate checks
# need exactly six successful completions. MOXIE_MODEL_CALL_LIMIT is enforced in the
# SDK immediately before every request attempt (including retries); GNU timeout owns
# one deadline for the entire pytest process. Do not add the wire test or another live
# suite here without lowering the trial count or raising an explicit owner decision.
set -euo pipefail

PROBE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROBE_ROOT"

exec timeout --foreground --kill-after=5s 360s \
  env MOXIE_MODEL_CALL_LIMIT=6 \
  python3 -m pytest \
    sim/tests/test_live_action_tags.py::test_the_model_ends_a_goodbye_with_a_real_exit_action \
    sim/tests/test_live_action_tags.py::test_the_model_launches_an_activity_it_was_told_about \
    -q -ra
