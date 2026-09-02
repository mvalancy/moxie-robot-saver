#!/usr/bin/env bash
# Boot broker + echo supervisor, run every sim/scenarios/*.json. Mirrors run_smoke.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PORT="${MOXIE_SIL_PORT:-1883}"
PIDS=(); BROKER_CID=""
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
           [ -n "$BROKER_CID" ] && docker rm -f "$BROKER_CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if command -v mosquitto >/dev/null 2>&1; then
  sed "s/^listener 1883/listener $PORT/" sim/broker/ci-mosquitto.conf > /tmp/moxie-ci-mosq.conf
  mosquitto -c /tmp/moxie-ci-mosq.conf >/tmp/moxie-broker.log 2>&1 & PIDS+=($!)
else
  BROKER_CID=$(docker run -d -p 127.0.0.1:$PORT:1883 \
    -v "$PWD/sim/broker/ci-mosquitto.conf":/mosquitto/config/mosquitto.conf:ro eclipse-mosquitto:2)
fi
sleep 2
# MOXIE_ALLOW_UNVERIFIED_BOTS=1: the scenario robots are throwaway `d_<uuid>`s, so the
# lab runs in open mode — see the note in run_smoke.sh.
MOXIE_APP=echo MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT MOXIE_ALLOW_UNVERIFIED_BOTS=1 MOXIE_STATUS_PORT=${MOXIE_STATUS_PORT:-$((7000 + ((PORT + 1) % 2000)))} python3 mqtt/run.py >/tmp/moxie-supervisor.log 2>&1 & PIDS+=($!)
sleep 3
rc=0
for s in sim/scenarios/*.json; do
  python3 sim/virtual_moxie.py --scenario "$s" --port $PORT --timeout 20 --quiet || rc=1
done
exit $rc
