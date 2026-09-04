#!/usr/bin/env bash
# The SIL soak — "a week in an hour", and honest that it is neither a week nor a proof.
#
#   bash sim/run_soak.sh                       # --profile quick  (~5 min)
#   bash sim/run_soak.sh --profile smoke       # ~1 min, everything happens once
#   bash sim/run_soak.sh --profile week        # 60 min, production-hardening.md §5.2
#   bash sim/run_soak.sh --only-contention --writers 8 --appends 250
#
# Build document: docs/architecture/backlog/production-hardening.md §5. The eleven
# acceptance bars (§5.3, A1–A11) are computed and printed by sim/tools/soak.py — pass or
# fail, never inferred — together with §5.4, which says what none of them can prove.
#
# READ §5.4 BEFORE QUOTING ANY NUMBER THIS PRINTS. It proves *our* half: a socket that
# dies, a process that restarts, two writers on one file. It is an hour at a RAISED RATE
# against a simulator, and a rate substitution is a different claim from a duration. No
# physical Moxie has ever been on this broker — not for a week, not for an hour.
#
# This is a DEEP-TIER, opt-in job and never a fast-tier one (§8 R5): the fast tier must
# stay something a contributor can run in a minute, and a soak that got disabled for being
# slow or flaky would prove less than no soak at all.
#
# Like run_scenarios.sh, readiness is POLLED rather than slept — that lesson cost 4.5 s of
# pure waiting on every CI run and a class of false failure on loaded runners. Everything
# it waits for here (a listening port, `broker_connected` on /status, a roster resume in
# `recent`) is an observable condition, so it is waited *on*.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

# The broker is a container: mosquitto is not installed on every box, and the soak has to
# restart it two dozen times, which `docker restart` does cleanly and a package install
# does not. (`run_scenarios.sh` prefers a local mosquitto binary and falls back to Docker;
# here the fallback IS the requirement.)
if ! docker info >/dev/null 2>&1; then
  echo "❌ the soak needs Docker (it restarts a real mosquitto ~24 times)."
  echo "   For the store half alone, which needs neither Docker nor a broker:"
  echo "     python3 sim/tools/soak.py --only-contention --writers 4 --appends 250"
  exit 2
fi

# The venv the caller is already in, else the repo's usual test venv, else system python.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in "$ROOT/.venv/bin/python" "$ROOT/sim/tests/.venv/bin/python" "$(command -v python3)"; do
    [ -x "$cand" ] && { PY="$cand"; break; }
  done
fi
"$PY" -c "import paho.mqtt.client" 2>/dev/null || {
  echo "❌ $PY has no paho-mqtt — the virtual robots cannot connect."
  echo "   pip install 'paho-mqtt>=2.0'"; exit 2; }

# Creds blanked here as well as inside the tool. `find_repo_dotenv()` resolves the MAIN
# worktree's mqtt/.env from anywhere, so an unguarded soak would spend an hour of real
# gateway calls to prove something about a broker. MOXIE_APP=echo means no brain is
# reached at all; this is the belt to that pair of braces.
export MOXIE_LLM_API_KEY="" MOXIE_LLM_BASE_URL=""
export MOXIE_VOICE_BASE_URL="" MOXIE_STT_BASE_URL=""

exec "$PY" "$ROOT/sim/tools/soak.py" "$@"
