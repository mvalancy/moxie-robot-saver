#!/usr/bin/env bash
# ── PROOF for the one-command stack (DoD criterion 5 / M7) ──────────────────────────
# Brings the REAL repo-root docker-compose.yml up under a throwaway project name on
# ports nothing else uses, then asserts the whole chain works:
#
#   1. `docker compose config` parses
#   2. broker + supervisor + console all report HEALTHY (compose healthchecks)
#   3. the supervisor's /status is reachable through the composed stack
#   4. sim/virtual_moxie.py --expect-tts round-trips against the COMPOSED broker:
#      state → config(paired) → remote-chat → reply → CloudTTSResponse audio
#   5. the console's /local/fleet shows that robot while it is connected
#   6. `docker compose down -v` — no containers, images-only, no volumes left behind
#
# Exits non-zero on any failure. Never touches the default ports (1883/8080/8930), so
# it is safe to run beside a stack you already have up.
#
#   sim/run_compose_smoke.sh                # build + run + tear down
#   MOXIE_SMOKE_KEEP=1 sim/run_compose_smoke.sh    # leave it running for debugging
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${MOXIE_SMOKE_PROJECT:-moxie-smoke}"
ENV_FILE="${MOXIE_SMOKE_ENV:-$ROOT/sim/compose-smoke.env}"
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/docker-compose.yml" --env-file "$ENV_FILE")

# Ports come from the env file so the script and the stack can never disagree.
port() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2; }
MQTT_PORT="$(port MOXIE_PORT_MQTT)"
CONSOLE_PORT="$(port MOXIE_PORT_CONSOLE)"
STATUS_PORT="$(port MOXIE_PORT_STATUS)"

# Prefer the repo venv (it has paho-mqtt for the virtual robot).
PY="${MOXIE_PY:-}"
[ -z "$PY" ] && [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
[ -z "$PY" ] && PY="python3"

WORK="$(mktemp -d)"
FAILED=0
step()  { printf '\n\033[1m── %s\033[0m\n' "$*"; }
ok()    { printf '   ✅ %s\n' "$*"; }
bad()   { printf '   ❌ %s\n' "$*"; FAILED=1; }

cleanup() {
  if [ "${MOXIE_SMOKE_KEEP:-}" = "1" ]; then
    printf '\n(MOXIE_SMOKE_KEEP=1 — leaving project %s up; tear down with:\n  %s down -v)\n' \
      "$PROJECT" "docker compose -p $PROJECT --env-file $ENV_FILE down -v"
  else
    printf '\n── tearing down (down -v) ──\n'
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1
    printf '   removed project %s (containers + volumes)\n' "$PROJECT"
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# ── 0. sanity ────────────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || { echo "❌ docker not installed"; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "❌ 'docker compose' not available"; exit 2; }
"$PY" -c "import paho.mqtt" 2>/dev/null || {
  echo "❌ the virtual robot needs paho-mqtt: pip install 'paho-mqtt>=2.0' (or MOXIE_PY=…)"; exit 2; }
for p in "$MQTT_PORT" "$CONSOLE_PORT" "$STATUS_PORT"; do
  if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :$p )" 2>/dev/null | grep -q LISTEN; then
    echo "❌ port $p is already in use — edit $ENV_FILE"; exit 2
  fi
done

step "0. compose file parses"
if "${COMPOSE[@]}" config -q; then ok "docker compose config"; else bad "compose config failed"; exit 1; fi

step "1. bringing the stack up (build + up -d)"
# Build/pull chatter goes to a log; it is only interesting when something breaks.
if ! "${COMPOSE[@]}" up -d --build >"$WORK/up.log" 2>&1; then
  bad "docker compose up failed"; tail -60 "$WORK/up.log"; "${COMPOSE[@]}" logs --tail 40; exit 1
fi
ok "built + started: $("${COMPOSE[@]}" ps --services --filter status=running | sort | tr '\n' ' ')"

step "2. waiting for healthchecks (broker · supervisor · console)"
health() {
  local cid; cid="$("${COMPOSE[@]}" ps -q "$1" 2>/dev/null)"
  [ -n "$cid" ] || { echo "missing"; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null
}
for svc in broker supervisor console; do
  for _ in $(seq 1 60); do
    st="$(health "$svc")"
    [ "$st" = "healthy" ] && break
    [ "$st" = "unhealthy" ] && break
    sleep 2
  done
  if [ "$(health "$svc")" = "healthy" ]; then ok "$svc healthy"; else
    bad "$svc is '$(health "$svc")'"; "${COMPOSE[@]}" logs --tail 30 "$svc"
  fi
done
[ "$FAILED" = "0" ] || exit 1

step "3. supervisor /status through the stack (127.0.0.1:$STATUS_PORT)"
if "$PY" - "$STATUS_PORT" <<'PYEOF'
import json, sys, urllib.request
d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/status", timeout=5))
assert d.get("ok"), d
print(f"   app={d.get('app')} uptime={d.get('uptime_s')}s robots={len(d.get('robots') or [])}")
PYEOF
then ok "/status ok"; else bad "/status unreachable or not ok"; fi

step "4. virtual Moxie against the COMPOSED broker (127.0.0.1:$MQTT_PORT)"
# Poll the console's fleet view in the background, while the robot is connected.
( "$PY" - "$CONSOLE_PORT" "$WORK/fleet.json" <<'PYEOF'
import json, sys, time, urllib.request
port, out = sys.argv[1], sys.argv[2]
deadline = time.time() + 60
while time.time() < deadline:
    try:
        d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/local/fleet", timeout=3))
        if d.get("ok") and d.get("robot_count", 0) >= 1:
            with open(out, "w") as fh:
                json.dump(d, fh)
            break
    except Exception:
        pass
    time.sleep(0.25)
PYEOF
) & POLLER=$!

"$PY" sim/virtual_moxie.py --host 127.0.0.1 --port "$MQTT_PORT" --timeout 30 --expect-tts
VM_RC=$?
[ "$VM_RC" = "0" ] && ok "SIL round-trip + TTS audio through the composed supervisor" \
                   || bad "virtual_moxie failed (rc=$VM_RC)"

step "5. parent console /local/fleet sees the robot (127.0.0.1:$CONSOLE_PORT)"
wait "$POLLER" 2>/dev/null
if [ -s "$WORK/fleet.json" ]; then
  "$PY" - "$WORK/fleet.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["robots"][0]
print(f"   robot_count={d['robot_count']} device_id={r['device_id']} "
      f"firmware={r.get('firmware')} online={r.get('online')} summary={r.get('summary')!r}")
PYEOF
  ok "/local/fleet listed the connected robot"
else
  bad "/local/fleet never showed a robot"
  "${COMPOSE[@]}" logs --tail 30 supervisor console
fi

step "result"
if [ "$FAILED" = "0" ]; then
  echo "   ✅ one-command stack PROVEN: broker + supervisor + console, robot round-trip, TTS audio, fleet view"
  exit 0
fi
echo "   ❌ compose smoke FAILED"
exit 1
