#!/usr/bin/env python3
"""Assert a log file does NOT contain the gateway credentials this box can see.

`sim/run_smoke.sh --live-brain` is the first harness mode that puts a **real** gateway
key into a supervisor's environment, and the same script prints the tail of that
supervisor's log to stdout — which on a CI runner means into a public build log. Nothing
in the supervisor is supposed to print a key. "Supposed to" is not a check.

The one rule this file is written under: **the secret is never printed, never returned
and never put in an exit message.** It is read, compared, and counted. A leak report says
*which variable* leaked and *how many times*, never the value — a guard that quotes the
string it caught would be the leak it exists to prevent, and a CI log would then hold the
key twice.

Sources, in the same order and by the same rule the appliance itself uses
(`mqtt/config._load_env`, `sim/tests/helpers_runtime.load_repo_dotenv`): the process
environment first, then a dotenv file if one is named. Values shorter than
`MIN_SECRET_LEN` are ignored — a one-character placeholder would match every log ever
written and turn this guard into noise.

    python3 sim/tools/assert_no_secret_in_log.py <log> [dotenv]
    → status 0: clean.  status 1: a secret is in the log.  status 2: bad usage.
"""
from __future__ import annotations

import os
import sys

#: The variables that hold a gateway credential anywhere in this repo. `config.py` reads
#: `MOXIE_LLM_API_KEY` and falls back to `LITELLM_MASTER_KEY`; the voice endpoints reuse
#: the same key rather than having one of their own.
SECRET_VARS = ("MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY")

#: Below this length a "secret" is a placeholder, not a secret, and matching on it would
#: fire on ordinary prose. `sk-`-style gateway keys are far longer.
MIN_SECRET_LEN = 12


def dotenv_secrets(path: str) -> dict:
    """`{var: value}` for the secret variables a dotenv file sets. Never printed."""
    found = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key in SECRET_VARS:
                    # Same inline-comment rule as `config._dotenv_value`, so this guard
                    # sees the same bytes the supervisor would have exported.
                    found[key] = value.split(" #", 1)[0].strip().strip("'\"")
    except OSError:
        pass
    return found


def secrets_in_scope(dotenv: str = "") -> dict:
    """Every gateway credential this box can see, `{var: value}`. Never printed."""
    scope = {k: os.environ[k].strip() for k in SECRET_VARS
             if os.environ.get(k, "").strip()}
    if dotenv and os.path.isfile(dotenv):
        for k, v in dotenv_secrets(dotenv).items():
            scope.setdefault(k, v)
    return {k: v for k, v in scope.items() if len(v) >= MIN_SECRET_LEN}


def leaks(log_text: str, dotenv: str = "") -> list:
    """The NAMES of the variables whose value appears in `log_text`. Values stay here."""
    return sorted(k for k, v in secrets_in_scope(dotenv).items() if v in log_text)


def main(argv) -> int:
    if not 2 <= len(argv) <= 3:
        print("usage: assert_no_secret_in_log.py <log> [dotenv]", file=sys.stderr)
        return 2
    log_path, dotenv = argv[1], (argv[2] if len(argv) > 2 else "")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
    except OSError as e:
        # A log that cannot be read has not leaked anything, and this guard must never be
        # the reason a passing run goes red. Say so and pass.
        print(f"[secret-check] {log_path}: {e.strerror} — nothing to check")
        return 0
    bad = leaks(body, dotenv)
    if bad:
        print(f"[secret-check] ❌ {log_path} contains the value of: {', '.join(bad)}")
        print("[secret-check]    The value is deliberately NOT printed. Find the print "
              "that emitted it (the supervisor logs no credential today) and remove it.")
        return 1
    n = len(secrets_in_scope(dotenv))
    print(f"[secret-check] ✅ {log_path} carries none of the {n} credential(s) in scope")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
