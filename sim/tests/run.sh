#!/usr/bin/env bash
# One-shot runner: sets up the venv (pytest + playwright) if needed, then runs the
# SIL/static-site suite against a real Chromium. Reuses the locally-cached Chrome
# under ~/.cache/puppeteer (no browser download); skips cleanly if none is found.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
venv="$here/.venv"
# Re-install whenever requirements.txt changes, not merely when the venv is absent.
# The old guard was `[ ! -x $venv/bin/pytest ]`, which meant a venv that had pytest and
# nothing else was never repaired — so the suite ran under-provisioned and tests skipped
# themselves rather than failing. A stamp of the requirements file makes drift cheap to
# detect and impossible to ignore.
stamp="$venv/.requirements.sha"
# BOTH files, because `requirements.txt` is now two lines and the packages live in
# `requirements-hermetic.txt` beside it (declared once — see the header of either file).
# Hashing only the outer file would mean adding a dependency to the inner one left every
# existing venv stale while the stamp still matched: the exact under-provisioned venv the
# stamp was introduced to make impossible.
want="$(sha256sum "$here/requirements.txt" "$here/requirements-hermetic.txt" \
        | sha256sum | cut -d" " -f1)"
if [ ! -x "$venv/bin/pytest" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  [ -x "$venv/bin/python" ] || python3 -m venv "$venv"
  "$venv/bin/pip" install -q -r "$here/requirements.txt"
  printf '%s' "$want" > "$stamp"
fi
exec "$venv/bin/python" -m pytest "$here" "$@"
