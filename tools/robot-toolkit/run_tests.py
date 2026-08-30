#!/usr/bin/env python3
"""Run every toolkit round-trip test (test_*.py in this directory) and report.

Each test graceful-skips (exit 0 with an "ℹ️ … skipped" line) when the protobuf
bindings aren't importable, so this is safe to run anywhere — in CI (with `protobuf`
installed + the committed embodied.* bindings) they actually execute the wire
round-trips; without it they skip. Exits non-zero iff a test fails.

    python3 tools/robot-toolkit/run_tests.py
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
tests = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
if not tests:
    print("no toolkit tests found"); sys.exit(0)

failed = []
for t in tests:
    name = os.path.basename(t)
    r = subprocess.run([sys.executable, t], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    status = "FAIL" if r.returncode != 0 else ("skip" if "skipped" in r.stdout else "ok")
    print(f"  [{status:>4}] {name}  {last[:88]}")
    if r.returncode != 0:
        failed.append(name)
        sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)

print(f"\ntoolkit round-trip tests: {len(tests)-len(failed)}/{len(tests)} ok"
      + (f" — FAILED: {', '.join(failed)}" if failed else ""))
sys.exit(1 if failed else 0)
