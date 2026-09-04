"""The soak's own accounting — what "the broker was up" means, and what A2's budget buys.

WHY THIS FILE EXISTS. `ci-deep.yml`'s **Soak — a week in an hour (quick)** is the gate on
every `dev → main` promotion, and through 2026-09-04 it failed on roughly half its runs
with `❌ A1  turn success while the broker was up = 100%` — 1 or 2 turns of ~982, on
commits whose diffs were documentation. A gate that red half the time on unrelated work is
worse than no gate: it teaches everyone reading it to merge through red, which is the one
habit that makes every other check in the tier worthless.

It was not flaky. It was two accounting bugs, both of them the shape this repo keeps
finding — *a value inferred instead of observed*:

  1. **A1 sampled an interval at its endpoints.** `_turn` read `sup.connected` once before
     publishing and once after the 8 s reply wait, and called the turn "issued while the
     broker was up" if both said yes. A broker restart that opened and closed *inside*
     that window was invisible to both reads, so a turn the fault legitimately cost was
     charged against a bar that is 100 % by definition.
  2. **A2's budget was spent by the wrong thing.** §5.3 defines A2 as *"turns **lost**
     because of a drop"*, and the code's own comment justifies the budget with "a robot can
     only lose the turn it was mid-way through" — a statement about losing. It was fed
     `during_outage`, every turn that *touched* a fault whether it was answered or not,
     which that reasoning cannot bound: a several-second window simply contains several
     turns, and answering them all is the system working, not a finding.

Together they made the two bars trade failures back and forth run to run — A1 red with
1 lost, or A2 red with 13 crossed — which reads exactly like noise and is not.

Every test here fails on the pre-fix tree.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import soak  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. The window record itself
# --------------------------------------------------------------------------- #

def test_a_fault_wholly_inside_a_turn_is_seen():
    """THE DEFECT, in one assertion.

    The endpoint samples say "up" at both ends of this turn, because the fault opened
    after the first read and closed before the second. Only a record of the window can
    say otherwise, which is the entire reason `Outages` exists.
    """
    o = soak.Outages()
    t_start = time.monotonic()
    with o.window():
        pass                      # a restart that came and went inside one reply wait
    t_end = time.monotonic()
    assert o.overlaps(t_start, t_end) is True


def test_a_fault_still_in_flight_is_seen():
    """An open window runs to +inf: a turn issued during an outage has not survived it."""
    o = soak.Outages()
    with o.window():
        now = time.monotonic()
        assert o.overlaps(now, now + 8.0) is True


def test_a_fault_before_or_after_the_turn_is_not_seen():
    """The converse, so the guard cannot pass by calling everything an outage — which
    would be the worse bug: every second inside a window is a second in which a real
    defect is excused."""
    o = soak.Outages()
    with o.window():
        pass
    after = time.monotonic() + 0.05
    assert o.overlaps(after, after + 1.0) is False
    # …and a turn that finished before the run even started touched nothing.
    assert o.overlaps(0.0, 0.0) is False


def test_windows_are_recorded_from_more_than_one_thread():
    """The injector runs on the main thread while the robots read from theirs."""
    o = soak.Outages()
    def fault():
        with o.window():
            time.sleep(0.02)
    ts = [threading.Thread(target=fault) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert o.count() == 8


# --------------------------------------------------------------------------- #
# 2. How a turn is filed
# --------------------------------------------------------------------------- #

class FakeClient:
    def publish(self, *a, **k):
        return None


class FakeVM:
    """Just enough of `VirtualMoxie` for `_turn`, with the reply outcome dictated."""

    def __init__(self, answered: bool):
        self.client = FakeClient()
        self.got_reply = self
        self._answered = answered

    def _reset_turn(self):
        pass

    def t_event(self, name):
        return f"/t/{name}"

    def wait(self, _timeout):        # stands in for got_reply.wait
        return self._answered


def _driver(up_values, outages):
    """A driver whose `sup.connected` probe returns `up_values` in order."""
    seq = list(up_values)
    calls = {"n": 0}

    def up():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    return soak.RobotDriver("127.0.0.1", 1, 0.0, up, outages, device_id="d_test")


def test_a_clean_lost_turn_is_still_charged_to_a1():
    """The bar must keep its teeth. No fault, no reply → A1 fails, which is the point."""
    d = _driver([True, True], soak.Outages())
    d._turn(FakeVM(answered=False))
    assert (d.turns_up_lost, d.turns_up_ok, d.turns_down, d.turns_down_lost) == (1, 0, 0, 0)


def test_a_turn_a_fault_slept_through_is_not_charged_to_a1():
    """Both samples read up; the injector's record says otherwise, and it wins."""
    o = soak.Outages()
    d = _driver([True, True], o)

    vm = FakeVM(answered=False)
    original = vm.wait

    def wait_with_a_restart_inside(timeout):
        with o.window():           # the fault the two samples cannot see
            pass
        return original(timeout)

    vm.wait = wait_with_a_restart_inside
    d._turn(vm)
    assert d.turns_up_lost == 0, "a turn a fault touched must never spend A1's budget"
    assert (d.turns_down, d.turns_down_lost) == (1, 1)


def test_a_turn_answered_through_a_fault_spends_nothing():
    """§5.3's A2 counts turns LOST to a drop. One answered anyway is the system working
    through a fault — the opposite of a finding — and must not spend the budget."""
    o = soak.Outages()
    d = _driver([True, True], o)

    vm = FakeVM(answered=True)
    original = vm.wait

    def wait_with_a_restart_inside(timeout):
        with o.window():
            pass
        return original(timeout)

    vm.wait = wait_with_a_restart_inside
    d._turn(vm)
    assert d.turns_down == 1, "it is still recorded as having crossed an outage"
    assert d.turns_down_lost == 0, "…but nothing was lost, so nothing is spent"
    assert d.turns_up_ok == 0, "…and it is not evidence for A1 either"


def test_the_endpoint_samples_still_catch_an_unscheduled_outage():
    """The injector's record is added to the old signal, not substituted for it. An outage
    nobody scheduled is a real finding this must never stop noticing."""
    d = _driver([True, False], soak.Outages())   # up on the way in, down on the way out
    d._turn(FakeVM(answered=False))
    assert d.turns_up_lost == 0
    assert (d.turns_down, d.turns_down_lost) == (1, 1)


# --------------------------------------------------------------------------- #
# 3. What the bars then read
# --------------------------------------------------------------------------- #

def _result(**turns):
    """A whole-run result with every OTHER bar deliberately inert.

    Only A1 and A2 are under test, so the rest are given shapes that grade to "not
    exercised" rather than to a pass — a fixture that quietly made ten bars green would be
    a fixture nobody could read a failure out of.
    """
    t = {"up_ok": 0, "up_lost": 0, "during_outage": 0, "lost_during_outage": 0,
         "session_failures": 0}
    t.update(turns)
    return {
        "turns": t,
        "conn": {"summary": {"by_kind": {"disconnect": 4, "publish_drop": 0}, "count": 0},
                 "retention": {"events": 0}},
        "broker_restarts": 4,
        "supervisor_restarts": 2,
        "config": {"robots": 2},
        "reconnects": [], "resumes": [], "samples": [],
        "baseline": {}, "final": {},
        "contention": {}, "kills": {},
        "status": {"recent": [], "robots": [], "roster": {"known": 0}},
        "log_findings": {"tracebacks": 0, "lines": []},
        "duration_s": 300.0,
    }


def _bar(bars, name):
    return next(b for b in bars if b[0] == name)


def test_a2_is_green_when_many_turns_crossed_and_none_were_lost():
    """The exact run this gate kept failing: turns crossed a window and were all answered.
    Budget is 2 robots x (4 + 2) = 12; 40 crossings is far past it and must not matter."""
    bars = soak.grade(_result(up_ok=900, during_outage=40, lost_during_outage=0))
    assert _bar(bars, "A2")[3] is True


def test_a2_still_fails_when_turns_are_actually_lost():
    """…and the budget is real: 13 losses against a budget of 12 is red."""
    bars = soak.grade(_result(up_ok=900, during_outage=40, lost_during_outage=13))
    assert _bar(bars, "A2")[3] is False


def test_a2_reports_both_numbers_so_the_line_can_be_read():
    """A measured line that showed only one of the two would hide the distinction this
    whole file is about."""
    measured = _bar(soak.grade(_result(up_ok=900, during_outage=40,
                                       lost_during_outage=3)), "A2")[2]
    assert "3 lost of 40" in measured


def test_a2_still_fails_when_a_loss_was_never_recorded():
    """§5.3's real bar, untouched: *an unrecorded loss is a failure even if the count is
    0*. Five restarts, four disconnect rows → red however few turns were lost."""
    r = _result(up_ok=900, during_outage=0, lost_during_outage=0)
    r["broker_restarts"] = 5
    assert _bar(soak.grade(r), "A2")[3] is False


def test_a1_is_untouched_and_still_demands_a_hundred_percent():
    """Nothing here relaxes A1. It just stops being fed turns that were never its."""
    assert _bar(soak.grade(_result(up_ok=900, up_lost=1)), "A1")[3] is False
    assert _bar(soak.grade(_result(up_ok=900, up_lost=0)), "A1")[3] is True
