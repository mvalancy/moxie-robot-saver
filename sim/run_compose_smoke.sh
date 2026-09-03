#!/usr/bin/env bash
# ── PROOF for the one-command stack (DoD criterion 5 / M7) ──────────────────────────
# Brings the REAL repo-root docker-compose.yml up under a throwaway project name on
# ports nothing else uses, then asserts the whole chain works:
#
#   1. `docker compose config` parses
#   2. broker + supervisor + console all report HEALTHY (compose healthchecks)
#   3. the supervisor's /status is reachable through the composed stack
#   3b/3c. the compose default MOXIE_TTS=tone does NOT pin the engine (#77), so a
#      composed deployment's Speech dropdown keeps its full list — checked over
#      /voice and again inside the container, with MOXIE_STT=off as the control
#   3d/3e. the same coupling for the BRAIN (#88), where it does bite: MOXIE_APP pins,
#      so the compose default `${MOXIE_APP:-content}` collapses the 🧠 card to one
#      entry — asserted over /brain and inside the container, with MOXIE_APP=any
#      (the escape the pin note names) as the control
#   4. sim/virtual_moxie.py --expect-tts round-trips against the COMPOSED broker:
#      state → config(paired) → remote-chat → reply → CloudTTSResponse audio
#   5. the console's /local/fleet shows that robot while it is connected
#   6. `docker compose down -v` — no containers, images-only, no volumes left behind
#
# TWO MODES, same assertions:
#
#   build  (default)  docker-compose.yml       — build the three images from this clone
#   images            docker-compose.images.yml — the PUBLISHED-image path an owner uses.
#                     The images are built locally first and tagged with the exact names
#                     that file references, then compose runs with pull_policy=never, so
#                     the wiring is proven end to end without needing a published tag.
#
# Exits non-zero on any failure. Never touches the default ports (1883/8080/8930), so
# it is safe to run beside a stack you already have up.
#
#   sim/run_compose_smoke.sh                       # build + run + tear down
#   MOXIE_SMOKE_MODE=images sim/run_compose_smoke.sh   # the prebuilt-image path
#   MOXIE_SMOKE_KEEP=1 sim/run_compose_smoke.sh    # leave it running for debugging
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODE="${MOXIE_SMOKE_MODE:-build}"
case "$MODE" in
  build)  COMPOSE_FILE="$ROOT/docker-compose.yml";        DEFAULT_PROJECT=moxie-smoke ;;
  images) COMPOSE_FILE="$ROOT/docker-compose.images.yml"; DEFAULT_PROJECT=moxie-smoke-img ;;
  *) echo "❌ MOXIE_SMOKE_MODE must be 'build' or 'images' (got '$MODE')"; exit 2 ;;
esac

PROJECT="${MOXIE_SMOKE_PROJECT:-$DEFAULT_PROJECT}"
ENV_FILE="${MOXIE_SMOKE_ENV:-$ROOT/sim/compose-smoke.env}"
COMPOSE=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE")

# Image mode: build locally, tag with the names docker-compose.images.yml expects, and
# forbid a pull — so a green run can only mean the LOCAL bits were wired up correctly.
# (Shell env beats --env-file in compose interpolation, which is what makes this work.)
export MOXIE_IMAGE_REGISTRY="${MOXIE_IMAGE_REGISTRY:-ghcr.io/mvalancy/moxie-robot-saver}"
export MOXIE_IMAGE_TAG="${MOXIE_IMAGE_TAG:-smoke-local}"
export MOXIE_IMAGE_PULL_POLICY="${MOXIE_IMAGE_PULL_POLICY:-never}"

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

step "0. compose file parses ($MODE mode: $(basename "$COMPOSE_FILE"))"
if "${COMPOSE[@]}" config -q; then ok "docker compose config"; else bad "compose config failed"; exit 1; fi

# docker-compose.images.yml has to stand alone (an owner downloads that ONE file), so it
# inlines the broker config AND its two ACL files instead of bind-mounting them from
# mqtt/broker/. Those are copies, and copies drift — so assert all three still say the
# same thing, in BOTH modes, before anything else runs.
step "0b. inlined broker config + ACLs still match mqtt/broker/"
if "$PY" - "$ROOT" <<'PYEOF'
# Dependency-free on purpose (a CI runner may have no PyYAML): pull each literal block
# scalar out by indentation, exactly as the YAML spec folds it.
import sys, pathlib, difflib
root = pathlib.Path(sys.argv[1])
lines = (root / "docker-compose.images.yml").read_text().splitlines()
PAIRS = [("mosquitto-conf", "compose-mosquitto.conf"),
         ("mosquitto-acl", "acl"),
         ("mosquitto-acl-robot", "acl-robot")]
def norm(seq):   # "$$" is compose's escape for a literal "$"
    out = [l.replace("$$", "$").rstrip() for l in seq]
    while out and not out[-1]:
        out.pop()
    return out
rc = 0
for config_name, filename in PAIRS:
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("    content: |")
                 and lines[i - 1].strip() == config_name + ":") + 1
    block, indent = [], "      "
    for l in lines[start:]:
        if l.strip() and not l.startswith(indent):
            break
        block.append(l[len(indent):] if l.startswith(indent) else "")
    onfile = (root / "mqtt" / "broker" / filename).read_text()
    a, b = norm(block), norm(onfile.splitlines())
    if a != b:
        print("   " + "\n   ".join(list(difflib.unified_diff(b, a, filename,
                                          "docker-compose.images.yml", lineterm=""))[:20]))
        rc = 1
        continue
    print(f"   {filename}: {len(b)} lines identical")
sys.exit(rc)
PYEOF
then ok "inlined broker config + ACLs are in sync"; else bad "an inlined broker file has DRIFTED"; exit 1; fi

if [ "$MODE" = "images" ]; then
  step "0c. building the three images locally under their published names"
  # Same Dockerfiles and contexts the release workflow's matrix uses.
  for spec in "supervisor|$ROOT/mqtt|$ROOT/mqtt/Dockerfile" \
              "console|$ROOT|$ROOT/server/Dockerfile" \
              "broker-certs|$ROOT/mqtt/broker|$ROOT/mqtt/broker/Dockerfile"; do
    IFS='|' read -r name ctx dfile <<<"$spec"
    tag="$MOXIE_IMAGE_REGISTRY/$name:$MOXIE_IMAGE_TAG"
    if docker build -q -t "$tag" -f "$dfile" "$ctx" >"$WORK/build-$name.log" 2>&1; then
      ok "$tag  (~$(docker image inspect -f '{{.Size}}' "$tag" | awk '{printf "%.0f", $1/1048576}') MB)"
    else
      bad "docker build failed for $name"; tail -30 "$WORK/build-$name.log"; exit 1
    fi
  done
fi

step "1. bringing the stack up (up -d)"
# Build/pull chatter goes to a log; it is only interesting when something breaks.
UP=("${COMPOSE[@]}" up -d)
[ "$MODE" = "build" ] && UP+=(--build)
if ! "${UP[@]}" >"$WORK/up.log" 2>&1; then
  bad "docker compose up failed"; tail -60 "$WORK/up.log"; "${COMPOSE[@]}" logs --tail 40; exit 1
fi
ok "started: $("${COMPOSE[@]}" ps --services --filter status=running | sort | tr '\n' ' ')"

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

# ── 3b. THE PIN vs THE COMPOSE DEFAULT ───────────────────────────────────────────────
# Both compose files default to `MOXIE_TTS: ${MOXIE_TTS:-tone}`, and PR #77 made an
# explicit MOXIE_TTS/MOXIE_STT *pin* the engine so a console dropdown cannot overrule the
# operator. If `tone` ever pinned, every `docker compose up` deployment's Speech dropdown
# would silently collapse to one entry. `voice_settings.ENV_PIN` deliberately leaves
# `tone` out — this asserts that, through the RUNNING stack rather than in a unit test:
#
#   * `/voice` on the composed supervisor reports NO speech pin and no pin note, while
#   * `MOXIE_STT=off` (also in this env file) DOES pin — the positive control that proves
#     the assertion can tell the two apart instead of passing vacuously;
#   * and inside the container, with the composed environment, a gateway listing still
#     produces a MULTI-ENTRY speech list (gateway voices + the built-in tone). That is
#     the collapse the coupling was about, and it is the only place it can be seen: the
#     smoke's own stack has no gateway configured, so its honest speech list is `tone`
#     alone whether or not the pin is in force.
step "3b. MOXIE_TTS=tone must not PIN the engine (compose's own default)"
if "$PY" - "$STATUS_PORT" <<'PYEOF'
import json, sys, urllib.request
v = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/voice", timeout=5))
assert v.get("ok"), v
pins, notes = v.get("pins") or {}, v.get("pin_notes") or {}
speech = [e["id"] for e in (v.get("available") or {}).get("speech") or []]
listening = [e["id"] for e in (v.get("available") or {}).get("listening") or []]
assert pins.get("speech") == "", f"MOXIE_TTS=tone PINNED the speech engine: {pins!r}"
assert not notes.get("speech"), f"a pin note for an unpinned side: {notes['speech']!r}"
assert "tone" in speech, f"the built-in voice vanished from the dropdown: {speech!r}"
# positive control: `off` really does pin, so the check above is not vacuous.
assert pins.get("listening") == "off", f"MOXIE_STT=off did not pin: {pins!r}"
assert listening == ["off"], f"a pinned side offered more than its engine: {listening!r}"
print(f"   pins={pins} speech={speech} listening={listening}")
PYEOF
then ok "/voice: tone does not pin; off does (control)"; else bad "/voice pin check failed"; fi

step "3c. …and a gateway listing still fills that dropdown inside the container"
# Runs in the SUPERVISOR CONTAINER with the composed environment (MOXIE_TTS=tone), so it
# reads the same config module the appliance booted with. Only the gateway *listing* is
# faked — a catalog seam, not an engine — because the smoke has no gateway to ask.
if "${COMPOSE[@]}" exec -T supervisor python -c '
import os, sys
sys.path.insert(0, "/app")
import config
from moxie_sdk import voice_settings as vs
cat = vs.GatewayCatalog(lambda: ["piper-amy", "piper-ryan", "stt-whisper"],
                        submit=lambda fn: fn())
out = config.voice_engines(cat).available()
speech = [e["id"] for e in out["available"]["speech"]]
assert os.environ.get("MOXIE_TTS") == "tone", os.environ.get("MOXIE_TTS")
assert out["pins"]["speech"] == "", out["pins"]
assert speech == ["gateway:piper-amy", "gateway:piper-ryan", "tone"], speech
print("   in-container MOXIE_TTS=%s -> speech=%s" % (os.environ.get("MOXIE_TTS"), speech))
'; then ok "the composed supervisor still offers its full engine list"
else bad "the composed supervisor's Speech dropdown COLLAPSED under MOXIE_TTS=tone"; fi

# ── 3d/3e. THE BRAIN PIN vs THE COMPOSE DEFAULT ──────────────────────────────────────
# Same coupling class as 3b, opposite polarity, and this one really bites. PR #88 made an
# explicit MOXIE_APP *pin* the appliance's brain (the owner rule #77 wrote for
# MOXIE_TTS/MOXIE_STT), and this repo's own docker-compose.yml interpolates
# `MOXIE_APP: ${MOXIE_APP:-content}` — so a `docker compose up` with nothing set arrives
# in the container as an EXPLICIT `content` and pins. `brains.py` documents that as a
# known, deliberate consequence; these two steps hold it to what it says, through the
# running stack rather than in a unit test, in both directions.
step "3d. the brain pin is real through the composed stack (/brain)"
if "$PY" - "$STATUS_PORT" <<'PYEOF'
import json, sys, urllib.request
b = json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/brain", timeout=5))
assert b.get("ok"), b
ids = [e["id"] for e in b.get("available") or []]
# This env file sets MOXIE_APP=echo, so the pin must be echo and the card must offer it
# ALONE — a picker that offered a brain this appliance would then refuse is the failure.
assert b.get("pin") == "echo", f"MOXIE_APP=echo did not pin the brain: {b.get('pin')!r}"
assert ids == ["echo"], f"a pinned appliance offered more than its brain: {ids!r}"
assert "MOXIE_APP" in (b.get("pin_note") or ""), f"the pin note never names the variable: {b.get('pin_note')!r}"
assert "MOXIE_APP=any" in b["pin_note"], "the pin note never names the escape"
assert b.get("appliance") == "echo" and b.get("default") == "echo", b
print(f"   pin={b['pin']!r} card={ids} fleet={b.get('fleet')!r}")
PYEOF
then ok "/brain: MOXIE_APP pins, the card offers exactly the pinned brain, the note names the escape"
else bad "/brain pin check failed"; fi

step "3e. …and the compose file's OWN default (MOXIE_APP=content) is what a bare deployment gets"
# Runs in the SUPERVISOR CONTAINER with the composed environment, overriding only
# MOXIE_APP, so it reads the same config module the appliance booted with. Two facts a
# `docker compose up` owner needs, and neither is visible from outside the container:
#   * `content` (the compose default) PINS — the 🧠 card collapses to one entry, so the
#     per-child picker is unavailable in a bare deployment until someone writes a line;
#   * `MOXIE_APP=any` — the escape the pin note names — really does restore all four and
#     pin nothing. That is the positive control that keeps this from passing vacuously.
# It also records the boot verdict for each, because `content` with no MOXIE_LLM_BASE_URL
# exits at assembly (config.require_llm_base_url, PR #68) and `restart: unless-stopped`
# turns that into a crash loop — see docs/architecture/implementation-plan.md, Known gaps.
if "${COMPOSE[@]}" exec -T -e MOXIE_APP=content -e MOXIE_LLM_BASE_URL= supervisor python -c '
import sys; sys.path.insert(0, "/app")
import config
ids = [e["id"] for e in config.brain_engines().available()["available"]]
assert config.brain_pin() == "content", config.brain_pin()
assert ids == ["content"], ids
try:
    config.build_app(); boot = "boots"
except SystemExit as e:
    boot = "exits: " + str(e).split(" —")[0]
print("   MOXIE_APP=content (compose default) -> pin=content card=%s, %s" % (ids, boot))
'; then ok "the compose default pins the brain — deliberate, documented, and now fenced"
else bad "the compose default no longer behaves as brains.py documents"; fi

if "${COMPOSE[@]}" exec -T -e MOXIE_APP=any -e MOXIE_LLM_BASE_URL= supervisor python -c '
import sys; sys.path.insert(0, "/app")
import config
from moxie_sdk import brains
ids = [e["id"] for e in config.brain_engines().available()["available"]]
assert config.brain_pin() == "", config.brain_pin()
assert ids == list(brains.BRAIN_IDS), (ids, list(brains.BRAIN_IDS))
print("   MOXIE_APP=any (the documented escape) -> pin=<none> card=%s" % (ids,))
'; then ok "MOXIE_APP=any hands the choice back — the control that makes 3e non-vacuous"
else bad "MOXIE_APP=any did NOT restore the per-child picker"; fi

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
  echo "   ✅ one-command stack PROVEN ($MODE mode): broker + supervisor + console, robot round-trip, TTS audio, fleet view"
  exit 0
fi
echo "   ❌ compose smoke FAILED"
exit 1
