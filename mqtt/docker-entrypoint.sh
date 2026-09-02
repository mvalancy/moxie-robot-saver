#!/bin/sh
# Supervisor container entrypoint. Optionally starts the status proxy (so the parent
# console — a different container — can reach the runtime's localhost-only /status),
# then execs the real command. Any other command (e.g. the SIL virtual robot) passes
# straight through.
set -eu

if [ -n "${MOXIE_STATUS_PROXY_PORT:-}" ]; then
  python /app/status_proxy.py "$MOXIE_STATUS_PROXY_PORT" "${MOXIE_STATUS_PORT:-8930}" &
fi

exec "$@"
