#!/usr/bin/env bash
# One-shot runner: sets up the venv (pytest + playwright) if needed, then runs the
# SIL/static-site suite against a real Chromium. Reuses the locally-cached Chrome
# under ~/.cache/puppeteer (no browser download); skips cleanly if none is found.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
venv="$here/.venv"
if [ ! -x "$venv/bin/pytest" ]; then
  python3 -m venv "$venv"
  "$venv/bin/pip" install -q -r "$here/requirements.txt"
fi
exec "$venv/bin/python" -m pytest "$here" "$@"
