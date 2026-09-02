#!/usr/bin/env bash
# Local SIL smoke test: broker + supervisor(echo app) + virtual robot round-trip.
# Mirrors CI. Uses the mosquitto binary if present, else docker.
#   PORT override:  MOXIE_SIL_PORT=18831 sim/run_smoke.sh
#   TELEHEALTH:     sim/run_smoke.sh --telehealth   (🎭 puppet round-trip instead)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PORT="${MOXIE_SIL_PORT:-1883}"
# 🎭 --telehealth swaps the conversation round-trip for the puppet one: an operator drives
# the SIL robot over the supervisor's own status HTTP (the seam the console proxies), and
# the robot asserts the recovered TeleHealth wire at every step. Same broker, same
# supervisor, same harness — only the subject under test changes.
MODE="smoke"
for arg in "$@"; do case "$arg" in --telehealth) MODE="telehealth";; esac; done
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
# Derive the status port from the broker port so a leftover supervisor on the default
# 8930 doesn't make this run's status endpoint fail to bind (it's best-effort either way).
STATUS_PORT=$((7000 + (PORT % 2000)))
# The built-in zero-dep "tone" voice by default → the smoke proves the full audio
# round-trip (supervisor synthesizes → CloudTTSResponse → SIM decodes it). MOXIE_TTS=off
# to skip; MOXIE_TTS=tone|piper|… otherwise honored.
TTS_ENGINE="${MOXIE_TTS:-tone}"
# The device allowlist is CLOSED by default (a robot must be permitted before it is
# served the child's config). This harness mints a throwaway `d_<uuid>` on every run, so
# there is nothing to pre-permit; and the subject under test here is the conversation
# round-trip, not the gate. So the lab runs in the documented open mode — exactly the
# migration switch a pre-gate deployment uses. The gate itself has its own hermetic
# tests (sim/tests/test_device_permits.py) and `virtual_moxie.py --expect-unpaired`.
MOXIE_APP=echo MOXIE_TTS="$TTS_ENGINE" MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT \
  MOXIE_STATUS_PORT=$STATUS_PORT MOXIE_ALLOW_UNVERIFIED_BOTS=1 \
  python3 mqtt/run.py >/tmp/moxie-supervisor.log 2>&1 & PIDS+=($!)
sleep 3

if [ "$MODE" = "telehealth" ]; then
  echo "── virtual Moxie (🎭 telehealth: operator → PLAY_OUTPUT → robot) ──"
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout 20 \
    --telehealth --status-url "http://127.0.0.1:$STATUS_PORT"
  rc=$?
else
  EXPECT_TTS=""; [ "$TTS_ENGINE" != "off" ] && EXPECT_TTS="--expect-tts"
  echo "── virtual Moxie (SIL round-trip${EXPECT_TTS:+ + tts audio}) ──"
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout 20 $EXPECT_TTS
  rc=$?
fi
echo "── supervisor log tail ──"; tail -6 /tmp/moxie-supervisor.log || true
exit $rc
