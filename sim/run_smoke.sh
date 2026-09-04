#!/usr/bin/env bash
# Local SIL smoke test: broker + supervisor(echo app) + virtual robot round-trip.
# Mirrors CI. Uses the mosquitto binary if present, else docker.
#   PORT override:  MOXIE_SIL_PORT=18831 sim/run_smoke.sh
#   TELEHEALTH:     sim/run_smoke.sh --telehealth   (🎭 puppet round-trip instead)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
# READINESS IS POLLED, NOT SLEPT — see sim/readiness.sh for the two numbers that
# retired the `sleep 2` + `sleep 3` this script used to boot on.
. sim/readiness.sh
PORT="${MOXIE_SIL_PORT:-1883}"
SUP_LOG=/tmp/moxie-supervisor.log
# 🎭 --telehealth swaps the conversation round-trip for the puppet one: an operator drives
# the SIL robot over the supervisor's own status HTTP (the seam the console proxies), and
# the robot asserts the recovered TeleHealth wire at every step. Same broker, same
# supervisor, same harness — only the subject under test changes.
MODE="smoke"
for arg in "$@"; do case "$arg" in --telehealth) MODE="telehealth";; esac; done
# ── bench hygiene: this run gets its OWN MOXIE_DATA_DIR ────────────────────────────────
# The harness mints a throwaway `d_<uuid>` on every invocation, and the supervisor's data
# directory defaults to the repo's `mqtt/data` — so without this line every SIL script on
# the box shares one durable roster, and a run inherits the device ids of every unrelated
# run before it. Observed 2026-09-03: a fresh `run_smoke.sh` re-pushed config to two
# `d_outage…` ids minted by `run_broker_outage.sh` a quarter of an hour earlier, on a
# different broker. That is noise, but it is also a hermeticity hole of exactly the shape
# `run_broker_outage.sh` phase 5c was rewritten to close — a leftover mechanism quietly
# satisfying an assertion the test did not intend, in this case "a config push happened".
# `sim/tools/soak.py` has always done this; these scripts had not. An operator who sets
# MOXIE_DATA_DIR keeps theirs, and only a directory this script created is removed.
MOXIE_DATA_DIR_OWNED=""
if [ -z "${MOXIE_DATA_DIR:-}" ]; then
  MOXIE_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moxie-smoke-data-XXXXXX")"
  MOXIE_DATA_DIR_OWNED=1
fi
export MOXIE_DATA_DIR

PIDS=(); BROKER_CID=""
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
           [ -n "$BROKER_CID" ] && docker rm -f "$BROKER_CID" >/dev/null 2>&1
           [ -n "${MOXIE_DATA_DIR_OWNED:-}" ] && rm -rf "$MOXIE_DATA_DIR"
           return 0; }
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
wait_for_port "$PORT" 30

echo "── supervisor (echo app) ──"
# Derive the status port from the broker port so a leftover supervisor on the default
# 8930 doesn't make this run's status endpoint fail to bind — and honour an operator's
# own MOXIE_STATUS_PORT, the way run_scenarios.sh always has, because the derivation can
# collide too and until now this script offered no lever when it did.
#
# "Best-effort either way" is what the note here used to say, and it was true when nothing
# read the endpoint. `--telehealth` made it LOAD-BEARING and the note was never revisited:
# the operator drives the robot over this exact HTTP port. Observed 2026-09-03 on
# MOXIE_SIL_PORT=1930 → :8930, a port a stale supervisor from an unrelated run already
# held: the runtime logged `status server failed: [Errno 98] Address already in use`, kept
# going, and the telehealth robot POSTed its commands **into the stranger on that port**,
# failing 20 s later as `exception: Expecting value: line 1 column 1 (char 0)` — a JSON
# error blamed on the TeleHealth wire. Same shape as the boot race above and as the
# MOXIE_DATA_DIR leak below: an unobserved precondition turning into a false accusation
# against the subject under test, and here also a run reaching into another run's process.
STATUS_PORT="${MOXIE_STATUS_PORT:-$((7000 + (PORT % 2000)))}"
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
  MOXIE_STATUS_PORT=$STATUS_PORT MOXIE_ALLOW_UNVERIFIED_BOTS=1 PYTHONUNBUFFERED=1 \
  python3 mqtt/run.py >"$SUP_LOG" 2>&1 & PIDS+=($!)
SUP_PID=${PIDS[-1]}
wait_for_log "[runtime] broker connected" "$SUP_PID" 40

if [ "$MODE" = "telehealth" ]; then
  # The status endpoint is this mode's subject, so it is checked rather than assumed. The
  # runtime prints the line only after `HTTPServer(...)` has BOUND, and prints
  # `status server failed: …` when it has not — so both outcomes are observable and
  # neither needs a sleep.
  if grep -q "status server failed" "$SUP_LOG" 2>/dev/null; then
    echo "❌ the supervisor could not bind its status endpoint on :$STATUS_PORT —"
    echo "   $(grep -m1 'status server failed' "$SUP_LOG")"
    echo "   --telehealth drives the robot over that port, so this run would be talking to"
    echo "   whatever else is listening on it. Re-run with a free MOXIE_STATUS_PORT (or a"
    echo "   different MOXIE_SIL_PORT, which is what derives it)."
    exit 1
  fi
  wait_for_log "[runtime] status endpoint on http://127.0.0.1:$STATUS_PORT/status" \
               "$SUP_PID" 10 || exit 1
  echo "── virtual Moxie (🎭 telehealth: operator → PLAY_OUTPUT → robot) ──"
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout 20 \
    --telehealth --status-url "http://127.0.0.1:$STATUS_PORT"
  rc=$?
else
  EXPECT_TTS=""; [ "$TTS_ENGINE" != "off" ] && EXPECT_TTS="--expect-tts"
  # --expect-scored: the reply must carry the SCORED half of RemoteChatOutput (mood,
  # mood_intensity, dialog_act, emotion, signals) and not just words. The behavior
  # planner fills those on every published turn (backlog/expressiveness.md §2.3), and
  # until this flag existed the standing smoke would have stayed green with every one of
  # them missing from the wire. Both `planner` and `floor` score; `MOXIE_EXPRESSIVE=off`
  # (and its predecessor `MOXIE_AUTOMARKUP=0`) is the documented passthrough rollback and
  # publishes an unscored line ON PURPOSE, so the check is asked for only when the
  # appliance claims to score — a rollback lever that reddened the smoke would be a
  # rollback lever nobody could use. Verified in both directions: with the default this
  # run prints the five fields, and with MOXIE_EXPRESSIVE=off the same robot reports
  # "reply (SUCCESS) carried no [...]" — that is why the flag is opt-in and not a
  # hard-wired assertion.
  EXPECT_SCORED="--expect-scored"
  case "${MOXIE_EXPRESSIVE:-planner}" in off) EXPECT_SCORED="";; esac
  [ "${MOXIE_AUTOMARKUP:-1}" = "0" ] && EXPECT_SCORED=""
  echo "── virtual Moxie (SIL round-trip${EXPECT_TTS:+ + tts audio}${EXPECT_SCORED:+ + scored output}) ──"
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout 20 \
    $EXPECT_SCORED $EXPECT_TTS
  rc=$?
fi
echo "── supervisor log tail ──"; tail -6 "$SUP_LOG" || true
exit $rc
