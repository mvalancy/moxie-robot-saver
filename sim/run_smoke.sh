#!/usr/bin/env bash
# Local SIL smoke test: broker + supervisor(echo app) + virtual robot round-trip.
# Mirrors CI. Uses the mosquitto binary if present, else docker.
#   PORT override:  MOXIE_SIL_PORT=18831 sim/run_smoke.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PORT="${MOXIE_SIL_PORT:-1883}"
PIDS=(); BROKER_CID=""
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
           [ -n "$BROKER_CID" ] && docker rm -f "$BROKER_CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "── broker on :$PORT ──"
if command -v mosquitto >/dev/null 2>&1; then
  sed "s/^listener 1883/listener $PORT/" sim/broker/ci-mosquitto.conf > /tmp/moxie-ci-mosq.conf
  mosquitto -c /tmp/moxie-ci-mosq.conf >/tmp/moxie-broker.log 2>&1 & PIDS+=($!)
else
  echo "  (no mosquitto binary — using docker eclipse-mosquitto:2)"
  BROKER_CID=$(docker run -d -p 127.0.0.1:$PORT:1883 \
    -v "$PWD/sim/broker/ci-mosquitto.conf":/mosquitto/config/mosquitto.conf:ro \
    eclipse-mosquitto:2)
fi
sleep 2

echo "── supervisor (echo app) ──"
MOXIE_APP=echo MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT \
  python3 mqtt/run.py >/tmp/moxie-supervisor.log 2>&1 & PIDS+=($!)
sleep 3

echo "── virtual Moxie (SIL round-trip) ──"
python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout 20
rc=$?
echo "── supervisor log tail ──"; tail -6 /tmp/moxie-supervisor.log || true
exit $rc
