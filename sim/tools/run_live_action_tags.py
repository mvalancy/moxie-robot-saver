#!/usr/bin/env python3
"""Supervise one live goodbye campaign without forwarding untrusted child output."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from action_tag_campaign import sanitized_state


ROOT = Path(__file__).resolve().parents[2]
TEST = "sim/tests/test_live_action_tags.py::test_the_model_ends_a_goodbye_with_a_real_exit_action"


def run_supervised(command, *, timeout_seconds: float, state_path: Path, env: dict) -> tuple[dict, int]:
    started = time.monotonic()
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        code = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        code = 124

    summary = sanitized_state(state_path)
    if summary is None:
        summary = {
            "campaign": "goodbye", "schema": 1, "planned_trials": 3,
            "attempt_limit": 6, "attempts": "unknown", "responses_received": "unknown",
            "completed": "unknown", "trials_observed": "unknown", "exit_hits": "unknown",
            "output_violations": "unknown", "elapsed_ms": "unknown",
            "measurement": "error", "adherence": "not_evaluated",
            "termination": "timeout" if timed_out else "instrument_error",
            "last_upstream_category": "none", "authoritative_attempts": None,
        }
    elif timed_out:
        summary.update(measurement="inconclusive", adherence="not_evaluated",
                       termination="timeout",
                       elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
    elif code != 0 and summary["measurement"] == "completed" and summary["adherence"] == "pass":
        summary.update(measurement="error", adherence="not_evaluated",
                       termination="child_error")

    success = (code == 0 and summary["measurement"] == "completed"
               and summary["adherence"] == "pass")
    return summary, 0 if success else 1


def main() -> int:
    timeout_seconds = float(os.environ.get("MOXIE_CAMPAIGN_TIMEOUT_SECONDS", "360"))
    with tempfile.TemporaryDirectory(prefix="moxie-goodbye-") as directory:
        state_path = Path(directory) / "state.json"
        env = os.environ.copy()
        env.update(MOXIE_MODEL_CALL_LIMIT="6")
        summary, code = run_supervised(
            [sys.executable, "-m", "pytest", TEST, "-q",
             f"--moxie-campaign-state-file={state_path}"],
            timeout_seconds=timeout_seconds, state_path=state_path, env=env)
        print(json.dumps(summary, sort_keys=True), flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
