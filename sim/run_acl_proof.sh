#!/usr/bin/env bash
# ── PROOF for broker hardening P0 (security-broker-auth.md §2) ──────────────────────
# Starts a THROWAWAY mosquitto from the repo's own broker config + ACLs, mints a
# throwaway supervisor credential with the repo's own gen-passwd.sh, and then asserts —
# by MESSAGE DELIVERY, because MQTT 3.1.1 acks an authorization failure as success —
# that the ACL does what §2.1 claims:
#
#   * the supervisor authenticates, reaches /devices/# and $SYS/broker/log/#
#   * a WRONG password, and a bare `-u supervisor` with none, are refused at CONNECT
#   * an anonymous robot still connects (P0 refuses nothing — that is P2)
#   * ...and is confined to its own /devices/<client id>/ subtree: it cannot read
#     another robot's config, cannot write another robot's state, and cannot read
#     $SYS/broker/log (which is where every d_<uuid> on the appliance is announced)
#   * on the robot listener, claiming `username=supervisor` buys nothing
#   * the browser SIM over websockets keeps its observer read and its d_sim writes,
#     but cannot drive a real robot or enumerate the fleet
#
# Needs docker (for eclipse-mosquitto) and paho-mqtt. Never touches a real stack: the
# ports come from MOXIE_ACL_PORT (default 2095) and nothing is killed.
#
#   sim/run_acl_proof.sh
#   MOXIE_ACL_PORT=3095 sim/run_acl_proof.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

IMAGE="${MOXIE_MOSQUITTO_IMAGE:-eclipse-mosquitto:2.0.20}"
BASE="${MOXIE_ACL_PORT:-2095}"
P_PLAIN=$BASE; P_WS=$((BASE + 1)); P_ROBOT=$((BASE + 2))
NAME="moxie-acl-proof-$$"
WORK="$(mktemp -d)"

cleanup(){ docker rm -f "$NAME" >/dev/null 2>&1; rm -rf "$WORK"; }
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "❌ docker not installed"; exit 2; }

PY="${MOXIE_PY:-}"
[ -z "$PY" ] && [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
[ -z "$PY" ] && PY="python3"
"$PY" -c "import paho.mqtt" 2>/dev/null || {
  echo "❌ needs paho-mqtt: pip install 'paho-mqtt>=2.0' (or MOXIE_PY=…)"; exit 2; }

for p in "$P_PLAIN" "$P_WS" "$P_ROBOT"; do
  if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$p )" 2>/dev/null | grep -q LISTEN; then
    echo "❌ port $p is already in use — set MOXIE_ACL_PORT"; exit 2
  fi
done

echo "── scratch broker from the repo's own config ──"
mkdir -p "$WORK/config/keys"
cp mqtt/broker/acl mqtt/broker/acl-robot "$WORK/config/"
# The real compose config, with the TLS listener's certs stripped: this proof is about
# the ACL, and minting a cert per run would only slow it down. The listener KEEPS its
# acl_file, which is the line under test.
"$PY" - "$ROOT/mqtt/broker/compose-mosquitto.conf" "$WORK/config/mosquitto.conf" <<'PYEOF'
import sys
src, dst = sys.argv[1], sys.argv[2]
out, in_tls = [], False
for line in open(src).read().splitlines():
    if line.startswith("listener 8883"):
        in_tls = True
        out += ["listener 8883", "allow_anonymous true",
                "acl_file /mosquitto/config/acl-robot"]
        continue
    if in_tls:
        if not line.strip():
            in_tls = False
        else:
            continue
    out.append(line)
open(dst, "w").write("\n".join(out) + "\n")
PYEOF
grep -q "acl_file /mosquitto/config/acl$" "$WORK/config/mosquitto.conf" || {
  echo "❌ the broker config no longer loads /mosquitto/config/acl"; exit 1; }

# The repo's own credential minter, run inside a container that has mosquitto_passwd.
SECRET="proof-$(head -c 12 /dev/urandom | base64 | tr -d '\n=+/')"
docker run --rm --entrypoint sh -v "$WORK/config/keys":/keys "$IMAGE" \
  -c "mosquitto_passwd -b -c /keys/passwd supervisor '$SECRET' && chmod 0644 /keys/passwd \
      && chown -R $(id -u):$(id -g) /keys" >/dev/null 2>&1 || {
  echo "❌ could not mint a scratch credential"; exit 1; }
echo "   config + acl + acl-robot + passwd in $WORK/config"

echo "── mosquitto on 127.0.0.1:$P_PLAIN (plain) :$P_WS (ws) :$P_ROBOT (robot) ──"
docker run -d --name "$NAME" \
  -p "127.0.0.1:$P_PLAIN:1883" -p "127.0.0.1:$P_WS:9001" -p "127.0.0.1:$P_ROBOT:8883" \
  -v "$WORK/config":/mosquitto/config:ro "$IMAGE" >/dev/null 2>&1 || {
  echo "❌ could not start $IMAGE"; exit 1; }
for _ in $(seq 1 40); do
  docker logs "$NAME" 2>&1 | grep -q "mosquitto version .* running" && break
  sleep 0.25
done
docker logs "$NAME" 2>&1 | grep -q "mosquitto version .* running" || {
  echo "❌ broker did not start"; docker logs "$NAME" 2>&1 | tail -20; exit 1; }

MOXIE_ACL_SECRET="$SECRET" MOXIE_ACL_PORT_PLAIN="$P_PLAIN" \
MOXIE_ACL_PORT_WS="$P_WS" MOXIE_ACL_PORT_ROBOT="$P_ROBOT" \
  "$PY" sim/tools/prove_broker_acl.py
RC=$?
[ "$RC" = "0" ] && echo "   ✅ broker ACL PROVEN against $IMAGE" || echo "   ❌ broker ACL proof FAILED"
exit $RC
