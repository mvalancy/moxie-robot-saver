"""The retired QR rig's headless runner writes its findings beside itself and commits nothing
unless asked.

`tools/qr-rig/overnight.py` once wrote `docs/debugging/qr-command-findings.md`, committed it
and pushed every hour, and launched the rig against a hard-coded bench address. The QR
grammar is closed (`docs/reverse-engineering/protocol/qr-commands.md`), the docs page is now
the record of the one sweep, and the rig is a validation tool. Pinned here:

* the findings file is `tools/qr-rig/findings.md`, never a page under `docs/`;
* `--moxie-ip` is required and `MOXIE_BROKER_HOST` must be set: no address is hard-coded;
* `commit()` runs only behind `--commit`; a default run writes the file and nothing else.
"""
import datetime
import importlib.util
import itertools
import os
import re
import subprocess
import sys
import types

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OVERNIGHT = os.path.join(REPO, "tools", "qr-rig", "overnight.py")


def _load():
    """Import the runner by path (its folder name has a hyphen); nothing runs on import."""
    spec = importlib.util.spec_from_file_location("qr_rig_overnight", OVERNIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_findings_file_lives_beside_the_rig_not_under_docs():
    mod = _load()
    rel = os.path.relpath(mod.FINDINGS, REPO).replace(os.sep, "/")
    assert rel == "tools/qr-rig/findings.md"
    # The only files it opens for writing are the findings file and the rig's log, both
    # beside the script; the docs page is linked from the template, never written.
    src = open(OVERNIGHT, encoding="utf-8").read()
    writes = set(re.findall(r'open\(((?:[^(),]|\([^()]*\))+),\s*"[wa]"\)', src))
    assert writes == {"FINDINGS", 'os.path.join(RIG_DIR,"rig.log")'}, writes


def test_no_bench_address_is_hard_coded(tmp_path):
    src = open(OVERNIGHT, encoding="utf-8").read()
    quads = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", src))
    assert quads <= {"127.0.0.1"}, f"dotted-quad literals in overnight.py: {sorted(quads)}"

    # An empty PATH: a runner that got past its own checks could find no pkill, chrome
    # or rig to launch, and the timeout bounds a mutant that would otherwise sleep.
    nobin = tmp_path / "nobin"
    nobin.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "MOXIE_BROKER_HOST"}
    env["PATH"] = str(nobin)
    r = subprocess.run([sys.executable, OVERNIGHT], capture_output=True, text=True,
                       env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode == 2 and "--moxie-ip" in r.stderr, r.stderr

    r = subprocess.run([sys.executable, OVERNIGHT, "--moxie-ip", "moxie.test"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode != 0 and "MOXIE_BROKER_HOST" in r.stderr, (r.returncode, r.stderr)


def _run_main(mod, extra_argv, monkeypatch):
    """Drive `main()` through one tick with a fake clock: the deadline is one minute after
    the first `now()`, every later `now()` is past it, so the loop never runs and nothing
    sleeps or touches the network."""
    calls = []
    t0 = datetime.datetime(2026, 1, 1, 10, 0, 0)
    ticks = itertools.chain([t0, t0], itertools.repeat(t0 + datetime.timedelta(minutes=5)))

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    monkeypatch.setattr(mod, "datetime",
                        types.SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta))
    monkeypatch.setattr(mod, "rig_up", lambda: True)
    monkeypatch.setattr(mod, "write_findings", lambda: calls.append("write") or (0, 0))
    monkeypatch.setattr(mod, "launch_chrome", lambda: calls.append("chrome"))
    monkeypatch.setattr(mod, "launch_rig", lambda ip: calls.append(("rig", ip)))
    monkeypatch.setattr(mod, "commit", lambda msg: calls.append(("commit", msg)))
    monkeypatch.setattr(mod.time, "sleep", lambda s: pytest.fail("the overnight loop ran"))
    monkeypatch.setattr(sys, "argv", ["overnight.py", "--no-manage", "--until", "10:01",
                                      "--moxie-ip", "moxie.test"] + extra_argv)
    mod.main()
    return calls


def test_a_default_run_writes_the_file_and_commits_nothing(monkeypatch):
    calls = _run_main(_load(), [], monkeypatch)
    assert calls == ["write"]


def test_commit_is_opt_in(monkeypatch):
    calls = _run_main(_load(), ["--commit"], monkeypatch)
    assert calls == ["write", ("commit", "qr-findings: final 2026-01-01 10:05")]
