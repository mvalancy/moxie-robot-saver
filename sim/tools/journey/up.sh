#!/usr/bin/env bash
# journey/up.sh — from the repo root of a checkout: start a local hosted Sim on PORT.
#   sim/tools/journey/up.sh mock <port> <mockport> [VAR=VAL ...]   .dev.vars -> the zero-spend mock gateway
#   sim/tools/journey/up.sh real <port> [VAR=VAL ...]              keep the .dev.vars you wrote (a REAL gateway)
# Always adds DEMO_ALLOWED_ORIGINS for the mapped hostname (moxie.hosted.test:PORT, the name
# the probes map to 127.0.0.1). Logs under $JOURNEY_OUT (default <tmpdir>/moxie-journey/out).
# It REWRITES .dev.vars in the checkout it runs in: use a copy, never the checkout you commit
# from, and never print the real file.
set -euo pipefail
kind=$1; port=$2; shift 2
out=${JOURNEY_OUT:-${TMPDIR:-/tmp}/moxie-journey/out}
mkdir -p "$out"
if [ "$kind" = mock ]; then
  mock=$1; shift
  umask 077
  {
    echo "DEMO_ENABLED=1"
    echo "DEMO_GATEWAY_BASE_URL=http://127.0.0.1:$mock/v1"
    echo "DEMO_GATEWAY_API_KEY=mock-key-not-a-secret-0123456789abcdef"
    echo "DEMO_CHAT_MODEL=mock-brain"
    echo "DEMO_TTS_MODEL=mock-voice"
    echo "DEMO_STT_MODEL=mock-ears"
    echo "DEMO_PROMPT_LAYOUT=single"
  } > .dev.vars.base
  if ! ss -ltn | grep -q ":$mock "; then
    setsid nohup node sim/tools/journey/mockgw.mjs "$mock" "$out/mockgw-$mock.jsonl" > "$out/mockgw-$mock.log" 2>&1 < /dev/null &
    sleep 1
  fi
else
  # the REAL gateway: a .dev.vars you wrote; never print it
  [ -f .dev.vars ] || { echo "no .dev.vars (write one from .dev.vars.example first)"; exit 2; }
  grep -v '^DEMO_ALLOWED_ORIGINS=' .dev.vars > .dev.vars.base
fi
# overrides: drop any key we re-set, then append
cp .dev.vars.base .dev.vars.new
for kv in "$@"; do k=${kv%%=*}; grep -v "^$k=" .dev.vars.new > .dev.vars.tmp || true; mv .dev.vars.tmp .dev.vars.new; echo "$kv" >> .dev.vars.new; done
grep -v '^DEMO_ALLOWED_ORIGINS=' .dev.vars.new > .dev.vars.tmp || true
echo "DEMO_ALLOWED_ORIGINS=http://moxie.hosted.test:$port" >> .dev.vars.tmp
mv .dev.vars.tmp .dev.vars; rm -f .dev.vars.new .dev.vars.base; chmod 600 .dev.vars
echo "up.sh: .dev.vars keys: $(cut -d= -f1 .dev.vars | tr '\n' ' ')"
grep -E "^DEMO_(ENABLED|CHAT_PER|SPEECH_PER|STT_PER|UNIT_BUDGET|MAX_CONCURRENT|CHAT_TIMEOUT)" .dev.vars || true
if grep -q "^DEMO_GATEWAY_BASE_URL=http://127.0.0.1" .dev.vars; then echo "gateway: loopback mock"; else echo "gateway: real (not printed)"; fi
pkill -f "wrangler pages dev sim/web --port $port" 2>/dev/null || true
sleep 1
setsid nohup npx wrangler pages dev sim/web --port "$port" --ip 127.0.0.1 --log-level warn \
  --show-interactive-dev-session=false > "$out/wrangler-$port.log" 2>&1 < /dev/null &
for i in $(seq 1 60); do
  if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/health" 2>/dev/null | grep -q 200; then
    echo "up.sh: wrangler on $port answered /api/health after ${i}s: $(curl -s http://127.0.0.1:$port/api/health | head -c 160)"
    exit 0
  fi
  sleep 1
done
echo "up.sh: wrangler on $port did not come up"; tail -20 "$out/wrangler-$port.log"; exit 1
