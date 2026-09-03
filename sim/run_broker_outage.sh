#!/usr/bin/env bash
# Broker-outage proof: take a real broker away from a RUNNING supervisor, and give it back.
#
# Why this exists. The production-hardening P0 slice rewrote how the supervisor connects
# (`connect_async` + `loop_forever(retry_first_connection=True)`), what a CONNACK means,
# what a disconnect records, and what every publish site does with `info.rc`. All of that
# was tested with fakes and injected transports — nothing had ever taken a real broker
# away from a real supervisor process and watched what happened. This script does exactly
# that, in five phases, against a real mosquitto and a real `mqtt/run.py`:
#
#   1  COLD START, NO BROKER   the supervisor is started with nothing listening. It must
#                              WAIT AND RETRY, not die. (`retry_first_connection=True`;
#                              the claim `sim/tests/test_connection_resilience.py::S6`
#                              makes about a stub, made about a process.)
#   2  BROKER APPEARS          it must connect on its own, with no restart.
#   3  A TURN                  a SIL robot completes a full conversation round-trip.
#   4  BROKER TAKEN AWAY       `/status` must flip `broker_connected` false and stamp
#                              `last_broker_disconnect`; a publish attempted in the gap
#                              must increment `publish_drops` instead of vanishing; and
#                              `POST /wakeup` must answer **409 with a reason** rather
#                              than `published: true`.
#   5  BROKER RETURNS          the supervisor must reconnect by itself and the NEXT turn
#                              must work end to end.
#
# The broker is a container this script creates and owns, so it can be stopped and started
# (`docker stop` / `docker start` keeps the port binding). Nothing else on the machine is
# touched — no process this script did not start is ever signalled.
#
#   PORT overrides:  MOXIE_OUTAGE_PORT=18942 MOXIE_OUTAGE_STATUS=8945 sim/run_broker_outage.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PORT="${MOXIE_OUTAGE_PORT:-18942}"
STATUS="${MOXIE_OUTAGE_STATUS:-8945}"
PY="${MOXIE_PY:-python3}"
CID_NAME="moxie-outage-$$"
DEVICE="d_outage$$"
SUP_LOG="${TMPDIR:-/tmp}/moxie-outage-supervisor.log"
SUP_PID=""

cleanup() {
  [ -n "$SUP_PID" ] && kill "$SUP_PID" 2>/dev/null
  docker rm -f "$CID_NAME" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

fail() {
  echo "❌ $*"
  echo "── /status (final) ──"; snap
  for k in broker_connected last_broker_connect last_broker_disconnect \
           last_connect_error publish_drops store_lock_timeouts; do
    printf '   %-24s %s\n' "$k" "$(field "$k")"
  done
  echo "── supervisor log tail ──"; tail -30 "$SUP_LOG" 2>/dev/null
  exit 1
}
ok()   { echo "   ✅ $*"; }

SNAP="${TMPDIR:-/tmp}/moxie-outage-status.json"

# snap — refresh the /status snapshot on disk (so no JSON ever rides a shell variable).
snap() { curl -s --max-time 3 -o "$SNAP" "http://127.0.0.1:$STATUS/status" 2>/dev/null; }

# field <key> — one top-level scalar out of the last snapshot, "" if absent/unparseable.
field() {
  $PY -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))
except Exception: print("")' "$SNAP" "$1" 2>/dev/null
}

# wait_for <key> <value> <seconds> — poll /status until key == value.
wait_for() {
  local key="$1" want="$2" secs="$3" i=0
  while [ "$i" -lt "$((secs * 4))" ]; do
    snap; [ "$(field "$key")" = "$want" ] && return 0
    i=$((i + 1)); sleep 0.25
  done
  return 1
}

start_broker() {
  docker run -d --name "$CID_NAME" -p "127.0.0.1:$PORT:1883" \
    -v "$PWD/sim/broker/ci-mosquitto.conf":/mosquitto/config/mosquitto.conf:ro \
    eclipse-mosquitto:2 >/dev/null || fail "could not start the broker container"
}

echo "══ broker-outage proof · broker :$PORT · status :$STATUS ══"
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then fail "port $PORT is busy — pick another"; fi
if ss -ltn 2>/dev/null | grep -q ":$STATUS "; then fail "port $STATUS is busy — pick another"; fi

# ── 1 · cold start with NO broker: it must wait, not die ────────────────────────────────
echo "── 1 · supervisor starts with no broker at all ──"
PYTHONUNBUFFERED=1 MOXIE_APP=echo MOXIE_TTS=off MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT \
  MOXIE_STATUS_PORT=$STATUS MOXIE_ALLOW_UNVERIFIED_BOTS=1 \
  MOXIE_LLM_API_KEY= MOXIE_VOICE_BASE_URL= MOXIE_STT_BASE_URL= MOXIE_LLM_BASE_URL= \
  $PY mqtt/run.py >"$SUP_LOG" 2>&1 &
SUP_PID=$!
sleep 6
kill -0 "$SUP_PID" 2>/dev/null || { tail -20 "$SUP_LOG"; fail "the supervisor DIED with no broker (retry_first_connection regression)"; }
ok "still alive after 6 s with nothing listening on :$PORT"
grep -q "could not reach the broker" "$SUP_LOG" \
  || { tail -20 "$SUP_LOG"; fail "no on_connect_fail line — the retry loop is invisible"; }
ok "on_connect_fail is printed: $(grep -m1 'could not reach the broker' "$SUP_LOG" | tr -d '\r')"
snap
[ -s "$SNAP" ] || fail "/status did not answer while the broker was down"
[ "$(field broker_connected)" = "False" ] || fail "broker_connected is not False before any broker existed"
[ -n "$(field last_connect_error)" ] || fail "last_connect_error is empty after a failed connect"
ok "/status: broker_connected=False last_connect_error=$(field last_connect_error)"

# ── 2 · the broker appears: it must connect on its own ──────────────────────────────────
echo "── 2 · the broker appears ──"
start_broker
wait_for broker_connected True 40 || { tail -20 "$SUP_LOG"; fail "the supervisor never connected once the broker came up"; }
snap
FIRST_CONNECT="$(field last_broker_connect)"
ok "connected with no restart · last_broker_connect=$FIRST_CONNECT"

# ── 3 · a real turn ─────────────────────────────────────────────────────────────────────
echo "── 3 · a SIL turn over that connection ──"
$PY sim/virtual_moxie.py --host 127.0.0.1 --port "$PORT" --device-id "$DEVICE" \
  --timeout 20 --quiet || fail "the SIL round-trip failed before the outage"
ok "SIL round-trip SUCCESS"

# ── 4 · take the broker away ────────────────────────────────────────────────────────────
echo "── 4 · the broker is taken away ──"
mark(){ echo "   ⏱  $(date +%s.%N) $*"; }
mark "before docker stop"
snap; DROPS_BEFORE="$(field publish_drops)"
docker stop -t 2 "$CID_NAME" >/dev/null || fail "could not stop the broker container"
mark "after docker stop (state=$(docker inspect -f "{{.State.Status}}" "$CID_NAME"))"
wait_for broker_connected False 30 || fail "/status still says broker_connected after the broker stopped"
snap
DISC="$(field last_broker_disconnect)"
[ -n "$DISC" ] && [ "$DISC" != "0.0" ] || fail "last_broker_disconnect was never stamped"
ok "/status: broker_connected=False last_broker_disconnect=$DISC"

# A publish attempted in the gap. `POST /config` reaches `_push_config` → `_publish`,
# which is the path every reply takes; the drop must be COUNTED, not swallowed.
curl -s -X POST --max-time 5 -d '{"audio_volume": 0.5}' \
  "http://127.0.0.1:$STATUS/config?device_id=$DEVICE" >/dev/null
snap; DROPS_AFTER="$(field publish_drops)"
[ "$DROPS_AFTER" -gt "$DROPS_BEFORE" ] \
  || fail "a publish during the outage did NOT increment publish_drops ($DROPS_BEFORE → $DROPS_AFTER)"
ok "publish during the gap counted: publish_drops $DROPS_BEFORE → $DROPS_AFTER"

# The console's wakeup button. 409 + a reason a parent can read, never `published: true`.
WAKE_CODE="$(curl -s -o "${TMPDIR:-/tmp}/moxie-outage-wake.json" -w '%{http_code}' \
  -X POST --max-time 5 "http://127.0.0.1:$STATUS/wakeup?device_id=$DEVICE")"
WAKE_BODY="$(cat "${TMPDIR:-/tmp}/moxie-outage-wake.json")"
[ "$WAKE_CODE" = "409" ] || fail "POST /wakeup answered $WAKE_CODE during the outage (expected 409) — $WAKE_BODY"
grep -q '"published": *false' <<<"$WAKE_BODY" || fail "wakeup did not say published:false — $WAKE_BODY"
grep -q '"reason"' <<<"$WAKE_BODY" || fail "wakeup gave no reason — $WAKE_BODY"
ok "POST /wakeup → 409 $WAKE_BODY"

kill -0 "$SUP_PID" 2>/dev/null || { tail -20 "$SUP_LOG"; fail "the supervisor died during the outage"; }
ok "the supervisor survived the outage"

# ── 5 · give the broker back ────────────────────────────────────────────────────────────
echo "── 5 · the broker comes back ──"
mark "before docker start"
docker start "$CID_NAME" >/dev/null || fail "could not restart the broker container"
mark "after docker start"
wait_for broker_connected True 60 || { tail -20 "$SUP_LOG"; fail "the supervisor did NOT reconnect on its own"; }
snap
RECONNECT="$(field last_broker_connect)"
ok "reconnected with no restart · last_broker_connect=$RECONNECT (was $FIRST_CONNECT)"
$PY - "$RECONNECT" "$DISC" <<'PY' || fail "the reconnect is not newer than the disconnect"
import sys
sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)
PY

echo "── 5b · the NEXT turn, end to end ──"
# A FRESH robot: the honest test of "is the appliance working again" — subscriptions
# restored, `$SYS` connect watch alive, config pushed, remote-chat answered.
$PY sim/virtual_moxie.py --host 127.0.0.1 --port "$PORT" --device-id "${DEVICE}b" \
  --timeout 25 --quiet || fail "the SIL round-trip failed AFTER the reconnect"
ok "SIL round-trip SUCCESS after the outage (a robot the appliance had not seen)"

# ── 5c · the RETURNING robot — a real defect this harness found ────────────────────────
# The robot that was talking when the broker died comes back with the same device id.
# `_device_connect` early-returns for a device already in `self.robots`, and the only
# thing that ever removes one is `_device_disconnect`, which fires off a `$SYS/broker/log`
# line — a line that died with the broker. So after a broker restart the roster is stale
# and the returning robot is never re-onboarded: no config push, no `app.on_connect`.
# `_on_disconnect` bumps `_turn_seq` (it stales in-flight turns) but does not clear the
# roster. Reported, not fixed: `mqtt/supervisor/moxie_runtime.py` is owned by the
# hardening P1 slice. Set MOXIE_OUTAGE_STRICT_ROSTER=1 to make this fatal once it is.
echo "── 5c · the RETURNING robot (same device id) ──"
if $PY sim/virtual_moxie.py --host 127.0.0.1 --port "$PORT" --device-id "$DEVICE" \
     --timeout 15 --quiet; then
  ok "the returning robot was re-onboarded"
else
  echo "   ⚠️  FINDING · the returning robot got NO config push after the broker restart."
  echo "      The supervisor still lists it as connected: the \$SYS/broker/log line that"
  echo "      would have called _device_disconnect died with the broker, so _device_connect"
  echo "      early-returns on 'device already in self.robots'. Stale roster + no re-onboard."
  echo "      → mqtt/supervisor/moxie_runtime.py:_device_connect / _on_disconnect"
  if [ "${MOXIE_OUTAGE_STRICT_ROSTER:-0}" = "1" ]; then
    fail "MOXIE_OUTAGE_STRICT_ROSTER=1 and the returning robot was not re-onboarded"
  fi
  FINDINGS=1
fi

snap
echo "── final /status broker fields ──"
for k in broker_connected last_broker_connect last_broker_disconnect last_connect_error \
         publish_drops store_lock_timeouts; do
  printf '   %-24s %s\n' "$k" "$(field "$k")"
done
echo "✅ broker-outage proof PASSED (cold start · reconnect · honest /status · 409 · a turn after)"
[ "${FINDINGS:-0}" = "1" ] && echo "⚠️  with 1 finding above (stale roster after a broker restart)"
exit 0
