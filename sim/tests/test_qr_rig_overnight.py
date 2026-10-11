"""The retired QR rig's headless runner writes its findings beside itself and commits nothing
unless asked.

`tools/qr-rig/overnight.py` once wrote `docs/debugging/qr-command-findings.md`, committed it
and pushed every hour, and launched the rig against a hard-coded bench address. The QR
grammar is closed (`docs/reverse-engineering/protocol/qr-commands.md`), the docs page is now
the record of the one sweep, and the rig is a validation tool. Pinned here:

* the findings file is `tools/qr-rig/findings.md`, never a page under `docs/`;
* `--moxie-ip` is required to launch the rig and `MOXIE_BROKER_HOST` must be set: no address
  is hard-coded; a `--no-manage` run (snapshots only) needs neither;
* `commit()` runs only behind `--commit`, both inside the loop and at the end; a default run
  writes the file and nothing else;
* `commit()` stages the findings file alone and pushes the current branch (never `main`); it
  stops at the first failing git step and reports it, and "committed" is printed only when
  every step succeeded.
"""
import datetime
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
    """Run the runner's source into a fresh module. Its folder name has a hyphen, and compiling
    the text each time means a changed copy is never served from a stale .pyc (two mutants of
    the same size written in the same second would otherwise share one). Nothing runs on import."""
    mod = types.ModuleType("qr_rig_overnight")
    mod.__file__ = OVERNIGHT
    with open(OVERNIGHT, encoding="utf-8") as f:
        exec(compile(f.read(), OVERNIGHT, "exec"), mod.__dict__)
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


def _run_main(mod, argv, monkeypatch, commit_ok=True):
    """Drive `main()` through exactly one loop pass with a fake clock: the deadline is one
    minute after the first `now()`, the third `now()` (the loop test) is still before it and
    every later one is past it. `time.sleep` only records its call and `time.time` is a large
    constant, so the in-loop commit gate is due on that one pass. Nothing sleeps or touches
    the network."""
    calls = []
    t0 = datetime.datetime(2026, 1, 1, 10, 0, 0)
    ticks = itertools.chain([t0, t0, t0], itertools.repeat(t0 + datetime.timedelta(minutes=5)))

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    monkeypatch.setattr(mod, "datetime",
                        types.SimpleNamespace(datetime=Clock, timedelta=datetime.timedelta))
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(
        time=lambda: 1e9, sleep=lambda s: calls.append(("sleep", s))))
    monkeypatch.setattr(mod, "rig_up", lambda: True)
    monkeypatch.setattr(mod, "write_findings", lambda: calls.append("write") or (0, 0))
    monkeypatch.setattr(mod, "launch_chrome", lambda: calls.append("chrome"))
    monkeypatch.setattr(mod, "launch_rig", lambda ip: calls.append(("rig", ip)))
    monkeypatch.setattr(mod, "commit", lambda msg: calls.append(("commit", msg)) or commit_ok)
    monkeypatch.setattr(sys, "argv", ["overnight.py", "--until", "10:01"] + argv)
    mod.main()
    return calls


def test_a_default_run_writes_the_file_and_commits_nothing(monkeypatch):
    calls = _run_main(_load(), ["--no-manage", "--moxie-ip", "moxie.test"], monkeypatch)
    assert calls == ["write", ("sleep", 300), "write"]


def test_commit_is_opt_in_and_runs_in_the_loop_and_at_the_end(monkeypatch):
    calls = _run_main(_load(), ["--no-manage", "--moxie-ip", "moxie.test", "--commit"], monkeypatch)
    assert calls == ["write", ("commit", "qr-findings: snapshot 2026-01-01 10:05"), ("sleep", 300),
                     "write", ("commit", "qr-findings: final 2026-01-01 10:05")]


def test_a_snapshot_only_run_needs_no_robot_address(monkeypatch):
    # --no-manage never launches the rig, so the robot's address has nothing to do.
    calls = _run_main(_load(), ["--no-manage"], monkeypatch)
    assert calls == ["write", ("sleep", 300), "write"]


@pytest.mark.parametrize("ok", [True, False])
def test_committed_is_printed_only_when_every_git_step_succeeded(monkeypatch, capsys, ok):
    _run_main(_load(), ["--no-manage", "--moxie-ip", "moxie.test", "--commit"], monkeypatch,
              commit_ok=ok)
    assert ("committed" in capsys.readouterr().out) is ok


def _git_double(fail_on=None):
    """A `subprocess.run` double for `commit()`: records every argv and fails the named git step."""
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        failed = argv[3] == fail_on
        return subprocess.CompletedProcess(argv, 1 if failed else 0, stdout="",
                                           stderr="fatal: the remote end hung up\n" if failed else "")
    return seen, run


def test_commit_stages_the_findings_file_alone_and_pushes_the_current_branch(monkeypatch):
    mod = _load()
    seen, run = _git_double()
    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=run))
    assert mod.commit("qr-findings: snapshot") is True
    git = ["git", "-C", mod.REPO]
    assert seen == [git + ["add", mod.FINDINGS], git + ["commit", "-m", "qr-findings: snapshot"],
                    git + ["pull", "--rebase", "--autostash"], git + ["push", "origin", "HEAD"]]


def test_commit_stops_at_the_first_failing_git_step_and_says_so(monkeypatch, capsys):
    mod = _load()
    seen, run = _git_double(fail_on="pull")
    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=run))
    monkeypatch.setattr(mod, "now", lambda: "2026-01-01 10:05")
    assert mod.commit("qr-findings: snapshot") is False
    assert [argv[3] for argv in seen] == ["add", "commit", "pull"]
    assert "2026-01-01 10:05 git pull failed (1): fatal: the remote end hung up" in capsys.readouterr().out
