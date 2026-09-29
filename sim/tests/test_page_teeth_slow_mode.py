"""`page_teeth_check.py --slow` — the plumbing, without a browser.

`--slow` reports a finding only when a check FLIPS green -> red on a throttled renderer, so
an instrument that silently failed to throttle reads as a clean sweep (it happened to the
network throttle: a bare `except` swallowed it on every page). `--selftest` proves the live
half in a minute of Chrome; this proves the wiring and the verdict logic in milliseconds.
"""
import importlib.util
import pathlib

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
_spec = importlib.util.spec_from_file_location("page_teeth_check", TOOLS / "page_teeth_check.py")
ptc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ptc)


def _led(throttled, **checks):
    return {"checks": [{"key": k, "pass": ok, "msg": msg} for k, (ok, msg) in checks.items()],
            "cpuThrottled": throttled, "notes": [], "rc": 0, "secs": 1.0, "tail": ""}


@pytest.fixture
def sweep(monkeypatch):
    """Run `slow_sweep` over fake ledgers; returns (rc, the env each run was given)."""
    def go(slow_ledgers):
        healthy = {s: _led(0, **{k: (True, c["msg"]) for k, c in
                                 ((c["key"], c) for c in led["checks"])})
                   for s, led in slow_ledgers.items()}
        envs = []
        monkeypatch.setattr(ptc, "SUITES", [(s, True, []) for s in slow_ledgers])
        monkeypatch.setattr(ptc, "_baseline", lambda *a, **k: (healthy, []))

        def run_suite(suite, extra_env, argv=(), timeout=0):
            envs.append(extra_env)
            return slow_ledgers[suite]
        monkeypatch.setattr(ptc, "run_suite", run_suite)
        return ptc.slow_sweep(6), envs
    return go


def test_a_real_flip_is_a_finding_and_the_rate_travels_in_the_env_the_ledger_reads(sweep):
    rc, envs = sweep({"s": _led(3, drawer=(False, "phone: tapping the handle opens the drawer"))})
    assert rc == 1
    assert envs == [{"MOXIE_TEETH_CPU": "6"}]
    ledger = (TOOLS / "teeth_ledger.mjs").read_text()
    assert "process.env.MOXIE_TEETH_CPU" in ledger
    # puppeteer's Page has no emulateCPUThrottlingRate on the pinned version: CDP only
    assert "Emulation.setCPUThrottlingRate" in ledger


def test_a_flip_whose_own_message_is_about_time_is_set_aside(sweep):
    rc, _ = sweep({"s": _led(3, fps=(False, "hidden page: 3 frames in 200 ms is under budget"))})
    assert rc == 0


def test_a_run_the_throttle_never_reached_is_skipped_not_read_as_clean(sweep, capsys):
    rc, _ = sweep({"s": _led(0, drawer=(False, "the drawer opens"))})
    assert rc == 0 and "SKIPPED" in capsys.readouterr().out
    body = (TOOLS / "teeth_ledger.mjs").read_text().split("if (CPU > 1)", 1)[1][:800]
    assert "notes.push(" in body, "a failed CPU throttle must be recorded, not swallowed"


def test_the_slow_flag_dispatches_to_the_slow_sweep(monkeypatch):
    monkeypatch.setattr("sys.argv", ["page_teeth_check.py", "--slow", "4", "--suite", "x"])
    monkeypatch.setattr(ptc, "slow_sweep", lambda *a: ("slow", a))
    monkeypatch.setattr(ptc, "sweep", lambda *a: "sweep")
    assert ptc.main() in (("slow", (4, "x", None)), 0)   # 0 only where node is absent
