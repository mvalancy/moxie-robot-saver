#!/usr/bin/env bash
# Readiness helpers for the SIL shell harnesses — sourced, never executed.
#
# WHY THIS FILE EXISTS. A boot has two observable conditions — "the broker accepts a TCP
# connection" and "the supervisor logged that it is subscribed" — and for a long time the
# scripts *guessed* at both with a fixed `sleep`. `run_scenarios.sh` was fixed first and
# grew its own private copies of these two functions; `run_smoke.sh`, the harness CI
# actually gates on, kept `sleep 2` + `sleep 3`. Measured on a warm laptop (2026-09-03,
# docker broker): the broker is listening in **0.35 s** and the supervisor is connected
# **0.11 s** later, so the smoke burned **4.5 s of pure waiting** on every run — and was
# still blind in the other direction. With the supervisor made 8 s slow (a loaded runner,
# reproduced) the smoke failed 20 s later as:
#
#     ❌ SIL round-trip FAILED:
#        - no config pushed within timeout
#
# which names the config push, the robot and the broker — everything except the boot that
# had not happened. Two copies of a wait are two waits, so the copies live here once.
#
# `[runtime] broker connected` is an HONEST readiness signal as of PR #103: `_on_connect`
# subscribes and *then* prints (`sim/tests/test_connect_readiness.py` asserts that order
# of effects). Waiting on it therefore means what it says. The status endpoint is bound
# before the broker connect is even attempted (`MoxieRuntime.run`), so a script that waits
# for this line — `run_smoke.sh --telehealth` does — has its status HTTP up as well.

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

# Wait for a line in a log file — and fail fast if the process died instead.
#   wait_for_log <needle> <pid> [timeout] [logfile]
# The log defaults to $SUP_LOG so the callers read the way they always have.
wait_for_log(){
  local needle="$1" pid="$2" deadline=$((SECONDS + ${3:-30})) log="${4:-$SUP_LOG}"
  while [ $SECONDS -lt "$deadline" ]; do
    grep -qF "$needle" "$log" 2>/dev/null && return 0
    kill -0 "$pid" 2>/dev/null || { echo "❌ supervisor exited during boot"; tail -20 "$log"; return 1; }
    sleep 0.2
  done
  echo "❌ supervisor never logged '$needle' in ${3:-30}s"; tail -20 "$log"; return 1
}
