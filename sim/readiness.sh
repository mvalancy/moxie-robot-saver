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
# WHICH LINE TO WAIT FOR, AND WHY IT IS NOT THE OBVIOUS ONE. PR #103 made
# `[runtime] broker connected` honest about its own claim — `_on_connect` subscribes and
# *then* prints — and that is still true. It is the wrong needle anyway: `subscribe()`
# generates a mid, queues a SUBSCRIBE packet and returns, and under `loop_forever()` the
# bytes leave on the network thread after the callback. So the line means "we asked", not
# "the broker agreed", and a robot booted on it announces `/state` into a broker holding
# no matching subscription. The supervisor answers a `/state` with a config push at QoS 0,
# not retained, so the loser of that race does not get a late config — it gets none, ever.
# A bigger timeout cannot recover a message nobody stored. (2026-09-05, HIL:
# `0/4 turns OK — no config pushed within timeout` on the FIRST scenario while the second
# passed 4/4. PR #143 is the same fix on the robot's side of the same wire.)
#
# So the harnesses wait for `[runtime] subscriptions acknowledged by the broker`, printed
# from the runtime's `_on_subscribe` — an observation of the SUBACK rather than an
# estimate of it. It is a SECOND line, deliberately: `broker_connected` in `/status`, the
# console's connection card and the rc=5 guards all still mean the CONNACK, and
# redefining the first line would have quietly moved the ground under them.
#
# The status endpoint is bound before the broker connect is even attempted
# (`MoxieRuntime.run`), so a script that waits for either line — `run_smoke.sh
# --telehealth` does — has its status HTTP up as well.

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
