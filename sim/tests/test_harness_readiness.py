"""A SIL script must WAIT for the stack, never guess at it.

**The finding (2026-09-03, integration pass).** PR #103 made the supervisor's readiness
line honest: `_on_connect` subscribes and *then* prints `[runtime] broker connected`, so
the line finally means what it says (`test_connect_readiness.py` asserts that order of
effects). `run_scenarios.sh` already waited on it. `sim/run_smoke.sh` — the harness CI
gates on, and the one the docs tell an operator to run first — did not: it booted on
`sleep 2` for the broker and `sleep 3` for the supervisor. So the fix landed and the
script that most needed it could not benefit.

Both numbers were wrong in both directions, measured on this box with a docker broker:

    broker listening after  0.35 s   (the script slept 2)
    supervisor ready after  0.11 s   (the script slept 3)

— 4.5 s of pure waiting per run, and still blind. Reproduced in the other direction by
making `mqtt/run.py` 8 s slow, which is a loaded CI runner:

    ❌ SIL round-trip FAILED:
       - no config pushed within timeout

Twenty seconds to a message that names the config push, the robot and the broker, and
never the boot that had not happened — the same signature the sixth integration pass
chased into the runtime. A blind sleep does not just waste time; it converts a boot
failure into a false accusation against the subject under test.

**What this file guards.** Not the fix — the *class*. Any `sim/*.sh` that boots
`mqtt/run.py` must wait on an observable condition, so the next harness cannot be written
with a `sleep` where a poll belongs. It is deliberately shaped like
`test_roster.py::test_every_sil_script_that_boots_a_supervisor_scopes_its_own_data_dir`,
which generalised the previous pass's harness finding the same way.

Pure file reading: no broker, no supervisor, no network — which is why the file is NOT
named `test_sil_*`. Both CI tiers select the hermetic suite with
`-k "not test_sil and not test_docs"`, so a guard about the SIL scripts that wore the SIL
prefix would have been deselected everywhere it was supposed to run: a test that does not
exist, which is the very shape this file is here to prevent.
"""
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")

#: The readiness line every supervisor-booting script waits for. Kept as a literal here
#: on purpose: if somebody changes the runtime's wording, this guard should go red next
#: to `test_connect_readiness.py` rather than silently start waiting for nothing.
READY_LINE = "[runtime] broker connected"


def _scripts():
    for name in sorted(os.listdir(SIM)):
        if name.endswith(".sh"):
            yield name, open(os.path.join(SIM, name), encoding="utf-8").read()


def _code(src: str) -> str:
    """The script with comment-only lines dropped — so a `sleep` *described* in a comment
    (this repo documents its history in comments) cannot trip a guard about behaviour."""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def _boots_supervisor(code: str) -> bool:
    return "mqtt/run.py" in code


def test_every_supervisor_booting_script_waits_for_the_readiness_line():
    offenders = [name for name, src in _scripts()
                 if _boots_supervisor(_code(src)) and READY_LINE not in _code(src)]
    # run_broker_outage.sh boots a supervisor with NO broker on purpose and asserts it
    # stays up; there is no readiness line to wait for, which is the point of the test.
    offenders = [n for n in offenders if n != "run_broker_outage.sh"]
    assert not offenders, (
        f"{offenders} boot mqtt/run.py without waiting for {READY_LINE!r}. A fixed sleep "
        f"is wrong in both directions: it wastes seconds on a warm box and, on a loaded "
        f"one, turns a boot that had not finished into a 20 s 'no config pushed within "
        f"timeout' against the robot. Source sim/readiness.sh and call wait_for_log.")


def test_no_supervisor_booting_script_guesses_the_boot_with_a_bare_sleep():
    """A `sleep N` with N >= 1 in a boot path is the anti-pattern itself.

    Sub-second sleeps are poll cadences (`sleep 0.2` inside a wait loop) and stay. The one
    long sleep that survives is `run_broker_outage.sh`'s `sleep 6`, which is not a guess
    at readiness but the assertion *"still alive after 6 s with nothing listening"* — a
    duration under test, not a duration hoped for."""
    allowed = {"run_broker_outage.sh": {"6"}, "run_compose_smoke.sh": {"2"}}
    offenders = []
    for name, src in _scripts():
        code = _code(src)
        if not _boots_supervisor(code) and name not in allowed:
            continue
        for n in re.findall(r"^\s*sleep\s+(\d+(?:\.\d+)?)\s*$", code, re.M):
            if float(n) >= 1 and n not in allowed.get(name, set()):
                offenders.append(f"{name}: sleep {n}")
    assert not offenders, (
        f"{offenders} — a whole-second sleep in a boot path is a guess. The conditions are "
        f"observable: wait_for_port for the broker, wait_for_log for the supervisor.")


def test_the_readiness_helpers_live_in_one_place():
    """Two copies of a wait are two waits.

    `run_scenarios.sh` grew private copies when it was fixed first; `run_smoke.sh` needed
    the identical pair. Both now source `sim/readiness.sh`, so a timeout or a poll cadence
    is changed once."""
    helpers = os.path.join(SIM, "readiness.sh")
    assert os.path.isfile(helpers), "sim/readiness.sh is gone"
    text = open(helpers, encoding="utf-8").read()
    assert "wait_for_port(){" in text and "wait_for_log(){" in text

    definers = [name for name, src in _scripts()
                if name != "readiness.sh"
                and re.search(r"^\s*wait_for_(port|log)\(\)\s*\{", _code(src), re.M)]
    assert not definers, (
        f"{definers} define their own wait_for_* instead of sourcing sim/readiness.sh")

    for name in ("run_smoke.sh", "run_scenarios.sh"):
        code = _code(open(os.path.join(SIM, name), encoding="utf-8").read())
        assert re.search(r"^\s*\.\s+sim/readiness\.sh\s*$", code, re.M), \
            f"{name} does not source sim/readiness.sh"


def test_the_readiness_line_is_the_one_the_runtime_actually_prints():
    """The guard above is only worth anything if the needle still exists in the runtime.

    A rename in `moxie_runtime.py` would otherwise leave every script waiting 40 s for a
    line nobody prints — a boot failure disguised as a slow boot, which is where this
    whole thread started."""
    src = open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py"),
               encoding="utf-8").read()
    # The needle, not the whole call — the call also carries `flush=True`, which the
    # behavioural test at the bottom of this file owns.
    assert '"[runtime] broker connected rc=' in src, (
        "the runtime no longer prints the readiness line the SIL scripts wait for; "
        "update READY_LINE here and in sim/readiness.sh together")


# --------------------------------------------------------------------------- #
# The other half of the readiness contract: printed last AND actually observable.
# --------------------------------------------------------------------------- #
# `test_connect_readiness.py` proves the line is printed only after every subscribe.
# That makes it TRUE. It does not make it VISIBLE: every consumer of this signal redirects
# the supervisor's stdout to a FILE, where Python is block-buffered, so an unflushed line
# sits in an 8 KB buffer until the process exits or says 8 KB more. Four callers carried
# `PYTHONUNBUFFERED=1` to compensate (`helpers_stack.py` even says why in a comment) —
# and the fifth, a straightforward rewrite of `run_smoke.sh` on 2026-09-03, did not, and
# waited the full 40 s for a supervisor that had connected in 0.11 s. The refusal branch
# beside it had always flushed. So the environment variable is now belt, and the keyword
# is braces.
class _FlushRecordingIO:
    """Enough of a text stream for `print`, recording the order of writes and flushes."""

    def __init__(self):
        self.events = []

    def write(self, s):
        if s.strip():
            self.events.append(("write", s))
        return len(s)

    def flush(self):
        self.events.append(("flush", None))


def test_the_readiness_line_is_flushed_when_it_is_printed():
    """Order of effects, not source text: the readiness write must be followed by a flush
    before `_on_connect` returns, whatever buffering the caller happens to have set up."""
    import sys as _sys
    from contextlib import redirect_stdout

    _sys.path.insert(0, os.path.join(REPO, "mqtt"))
    _sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import pytest
    pytest.importorskip("paho.mqtt.client")
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile

    class _App(MoxieApp):
        name = "echo"

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))

    class _Client:
        def subscribe(self, topic):
            pass

    out = _FlushRecordingIO()
    with redirect_stdout(out):
        rt._on_connect(_Client(), None, {}, 0)

    idx = next((i for i, (kind, payload) in enumerate(out.events)
                if kind == "write" and READY_LINE in payload), None)
    assert idx is not None, f"the readiness line was never written: {out.events}"
    assert any(kind == "flush" for kind, _ in out.events[idx:]), (
        f"{READY_LINE!r} was written but never flushed. Every waiter reads it from a "
        f"redirected stdout, where Python block-buffers, so an unflushed readiness signal "
        f"is a supervisor that looks hung for 40 s after connecting in 0.11 s. "
        f"print(..., flush=True) — the refusal branch beside it already does.")


# --------------------------------------------------------------------------- #
# The same class again, one port along: a precondition nobody looked at.
# --------------------------------------------------------------------------- #
# `run_smoke.sh --telehealth` drives the robot over the supervisor's own status HTTP, so
# that endpoint is the mode's SUBJECT. The script derived its port from the broker port
# and called the bind "best-effort either way" — true when nothing read it, and never
# revisited when `--telehealth` made it load-bearing. Observed 2026-09-03 on
# `MOXIE_SIL_PORT=1930` → `:8930`, held by a stale supervisor from an unrelated run: the
# runtime logged `status server failed: [Errno 98] Address already in use`, carried on,
# and the telehealth robot POSTed **into that stranger**, failing 20 s later as
# `exception: Expecting value: line 1 column 1 (char 0)` — a JSON error blamed on the
# TeleHealth wire. Both outcomes are printed by `_start_status_server`, so both are
# observable; the script simply did not look.
def _smoke() -> str:
    return open(os.path.join(SIM, "run_smoke.sh"), encoding="utf-8").read()


def test_the_telehealth_arm_checks_the_status_endpoint_it_is_about_to_drive():
    code = _code(_smoke())
    assert "status server failed" in code, (
        "run_smoke.sh --telehealth drives the supervisor's status HTTP but never checks "
        "that the bind succeeded; on a taken port it silently drives whatever else is "
        "listening there")
    assert "[runtime] status endpoint on http://127.0.0.1:$STATUS_PORT/status" in code, (
        "the positive signal is not waited for — the runtime prints that line only after "
        "HTTPServer(...) has bound, so it is the honest one to wait on")


def test_the_operator_can_choose_the_status_port_in_both_scripts():
    """A derived port can collide too, and until 2026-09-03 `run_smoke.sh` offered no
    lever when it did — it overwrote `MOXIE_STATUS_PORT` unconditionally, while
    `run_scenarios.sh` had always honoured it."""
    for name in ("run_smoke.sh", "run_scenarios.sh"):
        code = _code(open(os.path.join(SIM, name), encoding="utf-8").read())
        assert re.search(r"MOXIE_STATUS_PORT:-", code), (
            f"{name} ignores an operator's MOXIE_STATUS_PORT")


def test_the_runtime_still_prints_both_status_bind_outcomes():
    """The two needles above are only worth anything while the runtime prints them —
    and it must print BOTH, because a bind that fails silently is the original bug."""
    src = open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py"),
               encoding="utf-8").read()
    assert '"[runtime] status endpoint on http://127.0.0.1:{port}/status"' in src \
        or '[runtime] status endpoint on http://127.0.0.1:' in src, \
        "the success line moved; update run_smoke.sh's wait with it"
    assert "[runtime] status server failed" in src, \
        "the failure line moved; run_smoke.sh's --telehealth guard greps for it"
