#!/usr/bin/env bash
# Local SIL smoke test: broker + supervisor(echo app) + virtual robot round-trip.
# Mirrors CI. Uses the mosquitto binary if present, else docker.
#   PORT override:  MOXIE_SIL_PORT=18831 sim/run_smoke.sh
#   TELEHEALTH:     sim/run_smoke.sh --telehealth   (🎭 puppet round-trip instead)
#   LIVE BRAIN:     sim/run_smoke.sh --live-brain   (🧠 a real model, not the echo app;
#                   MOXIE_SMOKE_APP=llm|content picks which; SKIPS with status 0 when
#                   there is no gateway key)
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
# 🧠 --live-brain swaps the built-in echo app for a REAL brain on the gateway. That was
# the ONE mocked layer left in this harness: the broker is a broker, the supervisor is
# the supervisor process, the audio is synthesized and decoded, the robot decodes the
# wire — and then `MOXIE_APP=echo` was pinned below with no lever, so the whole run
# proved five real layers around the string `You said: hello Moxie`. Meanwhile
# sim/tests/test_live_gateway.py drives a real brain with no broker. Nothing ran BOTH
# halves in one process tree, which is what "a child can talk to Moxie end to end,
# proven by a live scenario, not a mock" asks for
# (docs/architecture/implementation-plan.md, Definition of done #1).
#
# `MOXIE_SMOKE_APP` picks WHICH brain — `content` (the shipped starter modules, the
# production default) unless told otherwise, `llm` for the free-form companion. The flag
# and the variable are two halves of one lever, not two levers: without --live-brain the
# variable is ignored and this script behaves byte for byte as it always has.
MODE="smoke"
LIVE_BRAIN=""
for arg in "$@"; do case "$arg" in
  --telehealth) MODE="telehealth";;
  --live-brain) LIVE_BRAIN=1;;
esac; done
#: The brain the supervisor below is started with. `echo` unless --live-brain says
#: otherwise — the default must stay exactly what every existing caller and CI job has
#: been getting since this script was written.
SMOKE_APP="echo"
REJECT_ECHO=""
DOTENV=""
CHAT_TIMEOUT=20

if [ -n "$LIVE_BRAIN" ]; then
  if [ "$MODE" = "telehealth" ]; then
    echo "❌ --live-brain and --telehealth are mutually exclusive."
    echo "   The puppet round-trip never consults a brain — the OPERATOR supplies the"
    echo "   words — so a live-brain telehealth run would spend a gateway call and prove"
    echo "   nothing about the AI seam. Run them as two invocations."
    exit 2
  fi
  SMOKE_APP="${MOXIE_SMOKE_APP:-content}"
  if [ "$SMOKE_APP" = "echo" ]; then
    echo "❌ MOXIE_SMOKE_APP=echo with --live-brain is a contradiction: --live-brain"
    echo "   exists to get OFF the echo app. Drop the flag, or name a real brain."
    exit 2
  fi

  # ── credentials ───────────────────────────────────────────────────────────────────
  # Resolved exactly the way sim/tests/helpers_runtime.load_repo_dotenv resolves them,
  # and for the same reason: `mqtt/.env` is git-ignored, so it exists in the MAIN
  # checkout and NOT in a `git worktree` — and a live tier that silently skips in every
  # worktree is the PR #12 finding all over again. This tree's file first, then the main
  # checkout's. A linked worktree's `.git` is a FILE holding
  # `gitdir: <main>/.git/worktrees/<name>`; in the main checkout it is a directory.
  MAIN_CHECKOUT="$ROOT"
  if [ -f "$ROOT/.git" ]; then
    gitdir="$(sed -n 's/^gitdir: //p' "$ROOT/.git" | head -1)"
    case "$gitdir" in */.git/worktrees/*) MAIN_CHECKOUT="${gitdir%%/.git/worktrees/*}";; esac
  fi
  for cand in "$ROOT" "$MAIN_CHECKOUT"; do
    if [ -f "$cand/mqtt/.env" ]; then DOTENV="$cand/mqtt/.env"; break; fi
  done

  # THE ENVIRONMENT WINS OVER THE DOTENV, because `config._load_env()` uses `setdefault`
  # and so an explicitly-empty MOXIE_LLM_API_KEY stays empty even next to a populated
  # `mqtt/.env`. This gate has to make the same call: otherwise
  # `MOXIE_LLM_API_KEY= sim/run_smoke.sh --live-brain` would boot a supervisor with no
  # key, 401 at the first turn, and report a *failure* where the contract says *skip*.
  # Values are tested for emptiness and never read into a variable, printed or logged.
  HAVE_KEY=""
  if [ -n "${MOXIE_LLM_API_KEY+x}" ]; then
    if [ -n "$MOXIE_LLM_API_KEY" ]; then HAVE_KEY=1; fi
  elif [ -n "${LITELLM_MASTER_KEY+x}" ]; then
    if [ -n "$LITELLM_MASTER_KEY" ]; then HAVE_KEY=1; fi
  elif [ -n "$DOTENV" ] && \
       grep -qE '^[[:space:]]*(MOXIE_LLM_API_KEY|LITELLM_MASTER_KEY)=[^[:space:]#]' "$DOTENV"; then
    HAVE_KEY=1
  fi
  # A key with nowhere to send it is not a gateway. `config._require_llm_base` EXITS the
  # supervisor when a model brain has no base URL, so an unset one has to skip here too —
  # a hard exit inside the supervisor would surface as "broker connected never appeared",
  # which blames the wrong layer.
  HAVE_BASE=""
  if [ -n "${MOXIE_LLM_BASE_URL+x}" ]; then
    if [ -n "$MOXIE_LLM_BASE_URL" ]; then HAVE_BASE=1; fi
  elif [ -n "$DOTENV" ] && grep -qE '^[[:space:]]*MOXIE_LLM_BASE_URL=[^[:space:]#]' "$DOTENV"; then
    HAVE_BASE=1
  fi

  if [ -z "$HAVE_KEY" ] || [ -z "$HAVE_BASE" ]; then
    # SKIP LOUDLY, WITH STATUS 0 — the contract sim/tests/test_live_gateway.py has
    # honoured since it was written, and the reason CI stays green on a runner with no
    # secret. A live path that reddens the build wherever a key is absent is worse than
    # no live path: it gets deleted, or worse, everybody learns to ignore the red.
    echo "⏭️  SKIPPED — sim/run_smoke.sh --live-brain needs a gateway and this box has:"
    echo "     MOXIE_LLM_API_KEY   $([ -n "$HAVE_KEY" ]  && echo present || echo "MISSING or empty")"
    echo "     MOXIE_LLM_BASE_URL  $([ -n "$HAVE_BASE" ] && echo present || echo "MISSING or empty")"
    echo "   (from the environment, or a git-ignored mqtt/.env in this tree or the main"
    echo "    checkout${DOTENV:+ — read $DOTENV}.)"
    echo "   Exit status is 0 ON PURPOSE. Nothing was started and no gateway call was"
    echo "   spent — and NOTHING WAS PROVEN. A tier that needs this proof must check for"
    echo "   the secret itself before invoking us; see sim/ci/ci-deep.yml."
    exit 0
  fi

  # Point the supervisor's own loader at the file we found, so a run from a `git
  # worktree` reads the main checkout's key instead of finding no `mqtt/.env` beside
  # `config.py` and booting brainless. An operator's own MOXIE_DOTENV is left alone.
  if [ -n "$DOTENV" ] && [ -z "${MOXIE_DOTENV:-}" ]; then export MOXIE_DOTENV="$DOTENV"; fi
  # The robot must assert the reply is not the echo app's own answer, or this mode is
  # just the ordinary smoke with a slower brain and no way to tell the difference.
  REJECT_ECHO="--reject-echo"
  # A model on the far side of the internet is not a dict lookup. The default 20 s is
  # sized for the echo app; a first token from a cold gateway can take longer than that,
  # and a timeout here would read as "the appliance did not answer".
  CHAT_TIMEOUT=60
fi
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
# TEARDOWN MUST NEVER FAIL A PASSING RUN, and it must not race the processes it
# just signalled. Both halves were real: `kill` only REQUESTS an exit, and since the
# supervisor grew a SIGTERM handler it flushes state on the way out — so a plain
# `kill` followed by an immediate `rm -rf` could delete the tree while a dying
# process was still writing into it, which surfaces as
# `rm: cannot remove '.../fleet': Directory not empty`. Under `bash -e` that failing
# `rm` aborted this function BEFORE its `return 0`, turning a green run red: CI
# reported a failure for a run whose scenarios had all passed.
# So: signal, WAIT for the processes to actually be gone (bounded — never hang a
# CI job on a wedged child), then remove, and swallow anything teardown still hits.
cleanup(){ for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
           for p in "${PIDS[@]:-}"; do
             for _ in $(seq 1 50); do kill -0 "$p" 2>/dev/null || break; sleep 0.1; done
             kill -9 "$p" 2>/dev/null || true
           done
           [ -n "$BROKER_CID" ] && docker rm -f "$BROKER_CID" >/dev/null 2>&1
           [ -n "${MOXIE_DATA_DIR_OWNED:-}" ] && rm -rf "$MOXIE_DATA_DIR" 2>/dev/null || true
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

echo "── supervisor ($SMOKE_APP app) ──"
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
MOXIE_APP="$SMOKE_APP" MOXIE_TTS="$TTS_ENGINE" MOXIE_MQTT_HOST=127.0.0.1 MOXIE_MQTT_PORT=$PORT \
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
  echo "── virtual Moxie (SIL round-trip${EXPECT_TTS:+ + tts audio}${EXPECT_SCORED:+ + scored output}${REJECT_ECHO:+ + 🧠 live brain}) ──"
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT --timeout $CHAT_TIMEOUT \
    $EXPECT_SCORED $EXPECT_TTS $REJECT_ECHO
  rc=$?
fi
# SECRET HYGIENE, and it runs BEFORE the tail is printed. --live-brain is the first mode
# of this harness that puts a real gateway key into a child process's environment, and the
# next line copies that child's log to stdout — which on a CI runner is a public build
# log. Nothing in the supervisor prints a credential today; "today" is not a guarantee, so
# it is checked rather than trusted. The checker never prints the value it looks for.
if [ -n "$LIVE_BRAIN" ]; then
  if ! python3 sim/tools/assert_no_secret_in_log.py "$SUP_LOG" "$DOTENV"; then
    echo "   Refusing to print the supervisor log tail — read $SUP_LOG yourself."
    exit 3
  fi
fi
echo "── supervisor log tail ──"; tail -6 "$SUP_LOG" || true
exit $rc
