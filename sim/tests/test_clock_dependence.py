"""Every test that reads the WALL CLOCK is listed here with a verdict — a ratchet.

Three flakes in two days came from the same disease, each found the hard way by a red
gate rather than by anything in the repo:

* PR #60 — `test_schedule_sil_e2e` asked for an activity 20 minutes out and asserted it
  was in *today's* plan. True for 1420 minutes a day, false for the last 20.
* PR #63 — a bedtime window of `["00:00", "23:59"]` reads as "all day" and is not:
  `in_bedtime` compares `start <= cur < end`, so it was false for exactly 23:59. One
  guaranteed CI failure a night.
* This pass — `test_telemetry_runtime.TODAY = int(time.time())`, with packets stamped
  `TODAY - 30`: an import at 00:00:10 filed them under *yesterday's* roll-up row while
  `history_view`'s "today" was the new day. ~30 red seconds a night, never seen yet.

The shared shape is not "a test used the clock" — plenty must, and the runtime reads its
own clock so pinning the test's would prove nothing. It is **a test that reads the clock
and nobody wrote down why that is safe**. So the fix is a reviewed ledger, not a ban:
`REVIEWED` names every wall-clock read in the test tree with the verdict and its reason,
and is asserted from **both** sides —

* a clock read that is not listed → **fail** (a new one can never arrive unreviewed);
* a listed entry that no longer exists, or whose set of constructs changed → **fail**
  (the list can only shrink, so nobody inherits a stale exemption, and adding a
  `datetime.now()` to an already-reviewed deadline loop is still caught).

That second direction is the whole design; it is the same ratchet
`test_ci_test_coverage.py` uses, and for the same reason.

**What counts as a wall-clock read.** `time.time`, `time.localtime`, `time.gmtime`,
`time.strftime`, `time.ctime`, `time.asctime`, `datetime.now`, `datetime.utcnow`,
`datetime.today`, `date.today` — and in the node suites `Date.now`, a no-argument
`new Date()`, the `get{Hours,Minutes,Day,Date,FullYear}` readers and `toISOString`.

**What deliberately does not.** `time.monotonic`, `time.perf_counter` and
`performance.now()` cannot see a date by construction — they are durations, and a
duration means the same thing at 03:00 as at 15:00. (They *can* make a test load-flaky,
which is a real disease with a real cure — playbook rule 11, "assert recorded state,
never live samples" — but it is a different one, and folding it in here would bury the
signal this guard exists for.) `time.sleep` is not a read at all.

**When this fails on you.** Read the failure: it names the file and the function. Decide
which of the three verdicts your test deserves, do the work, then add the row. Do not add
a row that says "looks fine".

* `DETERMINISTIC` — the read was removable and was removed. No row needed; there is
  nothing left to list.
* `RELATIVE` — the clock is genuinely part of the subject (a freshness stamp; a window
  the *runtime* evaluates against its own `now`; an age like `greet_after_s`). Say what
  makes the answer the same at every one of the 1440 minutes of a day, and prove it if
  the property is not obvious — `test_presence_runtime` asserts its synthetic windows
  exhaustively rather than claiming they hold.
* `BOTH BRANCHES` — the scenario genuinely cannot be built at some hours. Assert whichever
  branch is real, with the other still strict, and never `pytest.skip`: a silent skip is
  how a regression gets through in the tail of a day.

One clock read per *scope* is the unit, because a scope is what a reader reviews at once.
Two reads in one function are one row; two reads in two functions are two rows — and two
reads that must agree about the same instant belong in one place anyway, which is exactly
the bug `test_schedule_sil_e2e::served` now avoids by reading once and passing it down.
"""
from __future__ import annotations

import ast
import glob
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Wall-clock calls, by their dotted tail. Monotonic clocks are absent on purpose.
PY_CLOCK_CALLS = {
    ("datetime", "datetime", "now"): "datetime.now",
    ("datetime", "now"): "datetime.now",
    ("datetime", "datetime", "today"): "datetime.today",
    ("datetime", "today"): "datetime.today",
    ("datetime", "datetime", "utcnow"): "datetime.utcnow",
    ("datetime", "utcnow"): "datetime.utcnow",
    ("datetime", "date", "today"): "date.today",
    ("date", "today"): "date.today",
    ("time", "time"): "time.time",
    ("time", "localtime"): "time.localtime",
    ("time", "gmtime"): "time.gmtime",
    ("time", "strftime"): "time.strftime",
    ("time", "ctime"): "time.ctime",
    ("time", "asctime"): "time.asctime",
}

#: The node side. `new Date(<something>)` is not here: an explicit argument is a pinned
#: instant, which is the cure rather than the disease.
JS_CLOCK_PATTERNS = (
    ("Date.now", re.compile(r"\bDate\.now\b")),
    ("new Date()", re.compile(r"\bnew\s+Date\s*\(\s*\)")),
    ("getHours/getMinutes", re.compile(r"\.get(?:Hours|Minutes|Day|Date|FullYear)\s*\(")),
    ("toISOString", re.compile(r"\.toISOString\s*\(")),
)

#: `path::scope` (or just `path` for a node suite) → (constructs, verdict + reason).
#: **This list may only shrink.** Sorted by file, and each reason says what makes the
#: answer the same at every minute of a day — or which branch is asserted when it is not.
REVIEWED: dict = {

    # ---- node suites (file-level: these have no scope the scanner can name) ----------
    "sim/test_audio.mjs": (
        ("Date.now",),
        "RELATIVE — the requestAnimationFrame shim hands `cb(Date.now())` to code that "
        "only ever diffs consecutive frame stamps. A duration in disguise; no assertion "
        "reads the value."),
    "sim/test_demo_tickets.mjs": (
        ("Date.now",),
        "RELATIVE — a ticket is aged `Date.now()/1000 - 61` to make it one second past a "
        "60 s expiry. The subject IS the age, and 61 s from any instant is expired at "
        "every hour. (RESERVED file: owned by the live-Sim ears slice, 2026-09-03.)"),
    "sim/test_mode.mjs": (
        ("Date.now",),
        "DETERMINISTIC — it *overrides* `Date.now = () => clock` and steps `clock` by "
        "hand. This is the pinned-clock pattern the other rows aspire to, and the row "
        "exists only so the scanner's match is accounted for."),

    # ---- helpers ---------------------------------------------------------------------
    "sim/tests/helpers_stack.py::Broker.wait_ready": (
        ("time.time",),
        "RELATIVE — `deadline = time.time() + timeout`, a duration. No date is read, so "
        "the loop behaves identically at every hour."),
    "sim/tests/helpers_stack.py::Supervisor.wait_for": (
        ("time.time",),
        "RELATIVE — the same deadline loop, waiting for a line to appear in the "
        "supervisor log. Duration, not date; the only failure it can produce is a real "
        "timeout with a named reason."),

    # ---- presence --------------------------------------------------------------------
    "sim/tests/test_presence_runtime.py::_seed_absent": (
        ("time.time",),
        "RELATIVE — presence is scored as an AGE against `greet_after_s`, so the seeded "
        "state is offsets from now. A pinned epoch would make every robot absent for "
        "years and the suite would assert nothing."),
    "sim/tests/test_presence_runtime.py::test_a_bedtime_window_that_wraps_midnight_is_understood": (
        ("datetime.now",),
        "RELATIVE (but hour-independent) — only today's *date* is borrowed; hour and "
        "minute are overwritten and the timestamp is passed to `_in_bedtime` explicitly. "
        "20:30-07:00 answers the same for 21:30/03:00/12:00 on every date. Keeping a real "
        "date is deliberate: it is what would surface a DST/timezone regression."),
    "sim/tests/test_presence_runtime.py::test_a_content_module_prompt_can_read_presence": (
        ("time.time",),
        "RELATIVE — `present_since` is an age the prompt may phrase; the assertion is on "
        "the rendered `{% if %}` branch and never reads the stamp."),
    "sim/tests/test_presence_runtime.py::test_bedtime_hours_suppress_the_hello": (
        ("datetime.now",),
        "RELATIVE by necessity — the subject `rt._in_bedtime` reads the real clock itself "
        "(moxie_runtime.py:1723), so pinning the test's clock would test a different "
        "function. A now±30 min window contains now at all 1440 minutes; asserted "
        "exhaustively by `test_the_synthetic_windows_the_two_tests_above_build_hold_at_"
        "every_minute`. Both bedtime keys are written so a Fri→Sat midnight between the "
        "test's read and the runtime's cannot pick the other one."),
    "sim/tests/test_presence_runtime.py::test_outside_the_bedtime_window_the_hello_is_allowed": (
        ("datetime.now",),
        "RELATIVE by necessity — the mirror of the row above; a now+2h…+4h window excludes "
        "now at all 1440 minutes, asserted by the same exhaustive test. Its "
        "`pytest.skip(\"the synthetic window wrapped onto now\")` was removed here: it "
        "could never fire, and a skip that cannot fire is an escape hatch for a regression."),
    "sim/tests/test_presence_sil.py::_seed_absent": (
        ("time.time",),
        "RELATIVE — offsets from now, for the same reason as the runtime suite's "
        "`_seed_absent`: the SIL robot's presence record is read as an age, so a pinned "
        "epoch would describe a robot that left years ago."),

    # ---- the day plan ----------------------------------------------------------------
    "sim/tests/test_schedule_sil_e2e.py::_bedtime_body": (
        ("datetime.now",),
        "RELATIVE — bedtime and 'due today' are wall-clock by contract, so every window is "
        "built relative to now rather than pinned to a literal hour. It takes `now` as a "
        "parameter so the fixture and the assertions reason about ONE instant; two "
        "independent reads either side of local midnight answer for different days."),
    "sim/tests/test_schedule_sil_e2e.py::_seed_behaviors": (
        ("datetime.now",),
        "RELATIVE — records are placed a whole number of days before now and the "
        "recommender scores recency as an age, not a calendar date. A pinned epoch would "
        "age out of the recency window and silently stop testing anything."),
    "sim/tests/test_schedule_sil_e2e.py::served": (
        ("datetime.now",),
        "RELATIVE — the fixture's single clock read, handed to `_bedtime_body` and back to "
        "the tests in `served[\"now\"]`. This is the row that makes the file's discipline "
        "true: read once, pass it down."),
    "sim/tests/test_schedule_sil_e2e.py::test_a_reported_completion_reaches_the_store_and_the_next_plan": (
        ("datetime.now",),
        "RELATIVE — a robot stamps a completion with its own clock, and the assertion "
        "('played today, so not offered again') is about that stamp's age. Nothing "
        "compares it to a calendar boundary."),
    "sim/tests/test_schedule_sil_e2e.py::_request_lands_today": (
        (),
        "DETERMINISTIC — kept as a row only to record the fix: it used to read the clock "
        "a second time, independently of the config it was asking about. It now takes the "
        "fixture's instant. If this row ever gains a construct, that regressed."),

    # ---- telemetry -------------------------------------------------------------------
    "sim/tests/test_sil_durable_telemetry.py::_wait": (
        ("time.time",),
        "RELATIVE — a deadline loop polling a real broker and a real store until a row "
        "appears. Duration, not date: nothing reads the hour, and a genuine failure still "
        "times out with the predicate's name."),
    "sim/tests/test_telemetry.py::test_a_day_caps_its_distinct_event_names_without_losing_the_count": (
        ("time.localtime", "time.strftime"),
        "RELATIVE — `strftime(localtime(<literal epoch>))` computes the EXPECTED day key "
        "the same way `packet_day` computes the answer. Timezone-aware on purpose (the "
        "roll-up is keyed on the LOCAL day, so a hard-coded '2026-09-02' would fail west "
        "of UTC); no real 'now' is read, so it is hour-independent."),
    "sim/tests/test_telemetry.py::test_packet_day_falls_back_to_arrival_when_the_clock_lies": (
        ("time.localtime", "time.strftime"),
        "RELATIVE — same shape: a literal epoch formatted the way `packet_day` formats "
        "it, so the expectation follows the runner's zone without reading a real now."),
    "sim/tests/test_telemetry.py::test_packet_day_uses_recorded_at_when_it_is_plausible": (
        ("time.localtime", "time.strftime"),
        "RELATIVE — same shape: the expected day key is derived from the literal epoch "
        "the packet carries, never from the clock the test runs on."),
    "sim/tests/test_telemetry.py::test_roll_up_counts_a_day_by_event_and_tracks_its_span": (
        ("time.localtime", "time.strftime"),
        "RELATIVE — same shape: three literal stamps on one literal day, and the day key "
        "they must land under computed the product's own way."),
    "sim/tests/test_telemetry.py::test_roll_up_keeps_the_newest_days_and_counts_what_it_retired": (
        ("time.localtime", "time.strftime"),
        "RELATIVE — same shape: ten literal consecutive days, and the four expected "
        "survivors' keys derived from those same literals."),
    "sim/tests/test_telemetry_runtime.py::<module>": (
        ("date.today",),
        "RELATIVE — `TODAY` must land on today's LOCAL calendar day or the roll-up row it "
        "asserts falls outside `history_view`'s week. It is anchored at **noon** today, "
        "not `time.time()`: packets stamped `TODAY - 30` used to cross into yesterday for "
        "the ~30 s after local midnight. Noon exists in every zone on every DST day."),

    # ---- telehealth ------------------------------------------------------------------
    "sim/tests/test_telehealth.py::test_the_timestamp_defaults_to_milliseconds": (
        ("time.time",),
        "RELATIVE — the subject IS the default clock read: the only way to prove the "
        "stamp is milliseconds and not seconds is to compare it against a real now. The "
        "5 s tolerance is slack for a loaded runner, not a window the hour can move."),
    "sim/tests/test_telehealth_runtime.py::test_the_bedtime_warning_is_reported_and_the_line_is_still_sent": (
        ("datetime.now",),
        "RELATIVE by necessity — `telehealth_view` reads its own clock. A now±1h window "
        "contains now at every minute including the wrap, and both bedtime keys are "
        "written so the weekday never matters (PR #63). A fully deterministic pair sits "
        "beside it pinning the helper's real semantics."),
}

#: Rows whose construct tuple is empty are kept as tombstones — a fixed site whose
#: regression we want the ratchet to catch. They must NOT appear in the scan.
_TOMBSTONES = {k for k, (cons, _) in REVIEWED.items() if not cons}


# --------------------------------------------------------------------------- #
# the scanner
# --------------------------------------------------------------------------- #
def _dotted(node):
    """`time.strftime` → ('time', 'strftime'); anything not a plain dotted name → None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


class _Scan(ast.NodeVisitor):
    """Wall-clock calls, grouped by the enclosing def/class scope."""

    def __init__(self):
        self.stack: list = []
        self.hits: dict = {}

    def _scope(self) -> str:
        return ".".join(self.stack) or "<module>"

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):
        name = _dotted(node.func)
        if name:
            for key in (name, name[-3:], name[-2:]):
                if key in PY_CLOCK_CALLS:
                    self.hits.setdefault(self._scope(), set()).add(PY_CLOCK_CALLS[key])
                    break
        self.generic_visit(node)


def _scan() -> dict:
    """`{"path::scope": (constructs…)}` for every wall-clock read in the test tree."""
    found: dict = {}
    for path in sorted(glob.glob(os.path.join(REPO, "sim", "tests", "*.py"))):
        rel = os.path.relpath(path, REPO)
        with open(path) as fh:
            scan = _Scan()
            scan.visit(ast.parse(fh.read(), rel))
        for scope, constructs in scan.hits.items():
            found[f"{rel}::{scope}"] = tuple(sorted(constructs))
    for path in sorted(glob.glob(os.path.join(REPO, "sim", "*.mjs"))):
        rel = os.path.relpath(path, REPO)
        with open(path) as fh:
            src = fh.read()
        constructs = tuple(sorted(n for n, p in JS_CLOCK_PATTERNS if p.search(src)))
        if constructs:
            found[rel] = constructs
    return found


# --------------------------------------------------------------------------- #
# the ratchet, asserted in both directions
# --------------------------------------------------------------------------- #
def test_every_wall_clock_read_in_the_test_tree_has_been_reviewed():
    """Direction 1: nothing new arrives unreviewed."""
    found = _scan()
    unreviewed = {k: v for k, v in found.items() if k not in REVIEWED}
    assert not unreviewed, (
        "these tests read the wall clock and are not in REVIEWED:\n  "
        + "\n  ".join(f"{k}  {list(v)}" for k, v in sorted(unreviewed.items()))
        + "\n\nDecide per test whether it is genuinely time-independent. Make it "
          "deterministic, or assert both real branches, or keep it clock-relative WITH "
          "the reason written down — then add the row. See this module's docstring.")


def test_the_reviewed_list_can_only_shrink():
    """Direction 2: a row that no longer describes reality is a stale exemption.

    Without this the ledger rots exactly the way the bug it guards does — quietly, while
    everything stays green."""
    found = _scan()
    gone = sorted(k for k in REVIEWED if k not in found and k not in _TOMBSTONES)
    assert not gone, (
        "REVIEWED rows whose clock read no longer exists (delete the row — the list may "
        "only shrink):\n  " + "\n  ".join(gone))
    resurrected = sorted(k for k in _TOMBSTONES if k in found)
    assert not resurrected, (
        "these were fixed to read no clock at all and now read one again:\n  "
        + "\n  ".join(resurrected))


def test_a_reviewed_row_still_matches_what_the_test_actually_does():
    """Direction 2b: adding a `datetime.now()` to an already-reviewed deadline loop is a
    new clock read, and the row that covers it was written about a different function."""
    found = _scan()
    drifted = {k: (REVIEWED[k][0], v) for k, v in found.items()
               if k in REVIEWED and tuple(REVIEWED[k][0]) != v}
    assert not drifted, (
        "the constructs changed under a REVIEWED row — re-review it and update the row:\n  "
        + "\n  ".join(f"{k}: reviewed {list(a)} → now {list(b)}"
                      for k, (a, b) in sorted(drifted.items())))


def test_every_row_carries_a_verdict_and_a_reason():
    """A row that says nothing is worse than no row: it launders an unreviewed test into
    a reviewed-looking one. Each must name one of the three verdicts and explain it."""
    verdicts = ("DETERMINISTIC", "RELATIVE", "BOTH BRANCHES")
    for key, (_, reason) in sorted(REVIEWED.items()):
        assert any(reason.startswith(v) for v in verdicts), \
            f"{key}: the reason must open with one of {verdicts}, got {reason[:40]!r}"
        assert len(reason) > 80, f"{key}: 'why is this safe' needs more than {reason!r}"


def test_the_scanner_sees_a_clock_read_that_is_deliberately_planted():
    """The guard's own guard. A scanner that silently matched nothing would pass every
    assertion above forever — the exact failure mode `test_ci_test_coverage` was written
    against. So parse a synthetic file and require each family to be found."""
    src = ("import datetime, time\n"
           "def a():\n"
           "    return datetime.datetime.now()\n"
           "def b():\n"
           "    return time.strftime('%Y', time.localtime())\n"
           "class C:\n"
           "    def d(self):\n"
           "        return datetime.date.today(), time.time()\n"
           "def safe():\n"
           "    return time.monotonic(), time.perf_counter(), time.sleep(0)\n")
    scan = _Scan()
    scan.visit(ast.parse(src))
    assert scan.hits["a"] == {"datetime.now"}
    assert scan.hits["b"] == {"time.strftime", "time.localtime"}
    assert scan.hits["C.d"] == {"date.today", "time.time"}
    assert "safe" not in scan.hits, "monotonic clocks must NOT be flagged"
    for name, pattern in JS_CLOCK_PATTERNS:
        assert pattern.search({"Date.now": "const t = Date.now();",
                               "new Date()": "const d = new Date();",
                               "getHours/getMinutes": "d.getHours()",
                               "toISOString": "d.toISOString()"}[name]), name
    assert not JS_CLOCK_PATTERNS[1][1].search("new Date(1756800000000)"), \
        "a pinned instant is the cure, not the disease"
