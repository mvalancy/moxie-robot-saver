#!/usr/bin/env bash
# Boot broker + echo supervisor, run every sim/scenarios/*.json. Mirrors run_smoke.sh.
#
# READINESS IS POLLED, NOT SLEPT. This script used to `sleep 2` for the broker and
# `sleep 3` for the supervisor. Both numbers were wrong in both directions: on a warm
# laptop the stack is up in a few hundred ms (4.5 s of pure waiting on every CI run), and
# on a loaded runner `mqtt/run.py` can take longer than 3 s to reach the broker — in which
# case every scenario failed 20 s later with "no reply", pointing at the scenario rather
# than at the boot. The two conditions are observable, so they are waited *on*: a TCP
# connect to the broker, and the supervisor's own `[runtime] broker connected` line. Same
# pattern as `sim/tests/helpers_stack.py`, which is where a pytest gets it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PORT="${MOXIE_SIL_PORT:-1883}"
SUP_LOG=/tmp/moxie-supervisor.log
# ── bench hygiene: this run gets its OWN MOXIE_DATA_DIR ────────────────────────────────
# Same reason as `run_smoke.sh`, where the note lives: the harness mints a throwaway
# `d_<uuid>` per invocation and the supervisor's data dir defaults to the repo's
# `mqtt/data`, so without this every SIL script on the box shares one durable roster and
# inherits the device ids of every unrelated run before it. An operator's MOXIE_DATA_DIR
# is kept, and only a directory this script created is removed.
MOXIE_DATA_DIR_OWNED=""
if [ -z "${MOXIE_DATA_DIR:-}" ]; then
  MOXIE_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moxie-scenarios-data-XXXXXX")"
  MOXIE_DATA_DIR_OWNED=1
fi
export MOXIE_DATA_DIR

PIDS=(); BROKER_CID=""
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
           [ -n "$BROKER_CID" ] && docker rm -f "$BROKER_CID" >/dev/null 2>&1
           [ -n "${MOXIE_DATA_DIR_OWNED:-}" ] && rm -rf "$MOXIE_DATA_DIR"
           return 0; }
trap cleanup EXIT

# Wait until something is listening on 127.0.0.1:$1, or give up after $2 seconds.
wait_for_port(){
  local port="$1" deadline=$((SECONDS + ${2:-20}))
  while [ $SECONDS -lt "$deadline" ]; do
    if python3 -c "import socket,sys; s=socket.socket();
sys.exit(0 if s.connect_ex(('127.0.0.1', $port)) == 0 else 1)" 2>/dev/null; then return 0; fi
    sleep 0.2
  done
  echo "❌ nothing listening on 127.0.0.1:$port after ${2:-20}s"; return 1
}

# Wait for a line in the supervisor log — and fail fast if the process died instead.
wait_for_log(){
  local needle="$1" pid="$2" deadline=$((SECONDS + ${3:-30}))
  while [ $SECONDS -lt "$deadline" ]; do
    grep -qF "$needle" "$SUP_LOG" 2>/dev/null && return 0
    kill -0 "$pid" 2>/dev/null || { echo "❌ supervisor exited during boot"; tail -20 "$SUP_LOG"; return 1; }
    sleep 0.2
  done
  echo "❌ supervisor never logged '$needle' in ${3:-30}s"; tail -20 "$SUP_LOG"; return 1
}

if command -v mosquitto >/dev/null 2>&1; then
  sed "s/^listener 1883/listener $PORT/" sim/broker/ci-mosquitto.conf > /tmp/moxie-ci-mosq.conf
  mosquitto -c /tmp/moxie-ci-mosq.conf >/tmp/moxie-broker.log 2>&1 & PIDS+=($!)
else
  BROKER_CID=$(docker run -d -p 127.0.0.1:$PORT:1883 \
    -v "$PWD/sim/broker/ci-mosquitto.conf":/mosquitto/config/mosquitto.conf:ro eclipse-mosquitto:2)
fi
wait_for_port "$PORT" 30
# MOXIE_ALLOW_UNVERIFIED_BOTS=1: the scenario robots are throwaway `d_<uuid>`s, so the
# lab runs in open mode — see the note in run_smoke.sh.
: > "$SUP_LOG"
MOXIE_APP=echo MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT MOXIE_ALLOW_UNVERIFIED_BOTS=1 MOXIE_STATUS_PORT=${MOXIE_STATUS_PORT:-$((7000 + ((PORT + 1) % 2000)))} PYTHONUNBUFFERED=1 python3 mqtt/run.py >"$SUP_LOG" 2>&1 & PIDS+=($!)
SUP_PID=${PIDS[-1]}
wait_for_log "[runtime] broker connected" "$SUP_PID" 40

rc=0; total=0; failed=0
for s in sim/scenarios/*.json; do
  total=$((total + 1))
  python3 sim/virtual_moxie.py --scenario "$s" --port $PORT --timeout 20 --quiet \
    || { rc=1; failed=$((failed + 1)); }
done
# A verdict line, like run_smoke.sh's — so a caller (or an integration report) has one
# line to quote instead of counting ✅s.
if [ "$rc" -eq 0 ]; then
  echo "✅ SIL scenarios OK — $total/$total scenarios passed"
else
  echo "❌ SIL scenarios FAILED — $failed/$total scenarios failed"
  echo "── supervisor log tail ──"; tail -10 "$SUP_LOG" || true
fi
exit $rc
