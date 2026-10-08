"""Every clock read in the test tree is listed here with a verdict — a ratchet.

Flakes came from one disease: a test that reads the clock where nobody wrote down why that
is safe (an activity 20 min out asserted in *today's* plan, false in a day's last 20 min;
packets stamped `TODAY - 30` at import filed under yesterday just after midnight; an
absolute `p95 < 1 ms` that failed at load 88). So every read — wall clock in Python and
the node suites, and monotonic durations — needs a row, asserted both ways: an unlisted
read fails, and a row whose read vanished or changed fails (the list only shrinks).

Verdicts: `DETERMINISTIC` (pinned/overridden, or a bounded wait), `RELATIVE` (the clock is
the subject — say why the answer is the same at all 1440 minutes, or why load only moves it
away from failure), `BOTH BRANCHES` (assert whichever branch is real; never `skip`).
One row per scope: two reads that must agree belong together — read once, pass it down.
"""
from __future__ import annotations

import ast
import glob
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Clock calls by dotted tail (monotonic included: see the docstring's p95 flake).
PY_CLOCK_CALLS = {
    ("time", "perf_counter"): "time.perf_counter",
    ("time", "monotonic"): "time.monotonic",
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

#: `new Date(<arg>)` is absent on purpose: a pinned instant is the cure.
JS_CLOCK_PATTERNS = (
    ("Date.now", re.compile(r"\bDate\.now\b")),
    ("new Date()", re.compile(r"\bnew\s+Date\s*\(\s*\)")),
    ("getHours/getMinutes", re.compile(r"\.get(?:Hours|Minutes|Day|Date|FullYear)\s*\(")),
    ("toISOString", re.compile(r"\.toISOString\s*\(")),
)

_TP = "sim/tests/test_presence_runtime.py::"
_TS = "sim/tests/test_schedule_sil_e2e.py::"
_TT = "sim/tests/test_telemetry.py::"
_TK = "sim/tests/test_soak_accounting.py::"
_DAY_KEY = ("time.localtime", "time.strftime")
_DAY_KEY_WHY = ("RELATIVE — the expected day key is `strftime(localtime(<literal epoch>))`, "
                "computed the way `packet_day` does; no real now is read.")

#: `path::scope` (or a node suite's path) → (constructs, "VERDICT — why it is safe").
REVIEWED: dict = {
    "sim/tests/test_telemetry_runtime.py::test_telemetry_survives_a_supervisor_restart": (
        ("datetime.now",), "RELATIVE — the tail is compared against the view's own notion "
        "of today, so a run spanning midnight moves both sides together."),
    "sim/tests/test_telemetry_runtime.py::<module>": (
        ("date.today",), "RELATIVE — `TODAY` is anchored at local NOON (not now): noon "
        "exists in every zone on every DST day, so `TODAY - 30` never crosses midnight."),
    "sim/test_audio.mjs": (
        ("Date.now",), "RELATIVE — the rAF shim's frame stamps are only diffed; no "
        "assertion reads a value."),
    "sim/test_csp.mjs": (
        ("Date.now",), "RELATIVE — a ceiling on a polling wait; the value reaches no check."),
    "sim/test_console_insights.mjs": (
        ("Date.now",), "RELATIVE — an ORDERING of two reads (`a >= b`), true at every minute."),
    "sim/eval_live.mjs": (
        ("Date.now", "new Date()", "toISOString"), "RELATIVE — not a test: a hand-run live "
        "eval with no assertions (latency + artifact filename)."),
    "sim/tests/edge/demo_proxy/07_turn_features.mjs": (
        ("Date.now",), "RELATIVE — an AGE (`now - TTL - 60` expired vs `now` fresh); "
        "`chat.js` checks against the real clock."),
    "sim/test_demo_tickets.mjs": (
        ("Date.now",), "RELATIVE — a ticket aged 61 s past a 60 s expiry is expired at any hour."),
    "sim/tests/edge/turnstile/02_fail_open.mjs": (
        ("Date.now",), "RELATIVE — elapsed time between two reads, bounded both ways by "
        "wide margins (>= 120 ms deadline, << 20 s timeout); mutation row D3e."),
    "sim/tests/edge/mode/03_mode_machine.mjs": (
        ("Date.now",), "DETERMINISTIC — overrides `Date.now = () => clock` and steps it."),
    "sim/tests/helpers_stack.py::Broker.wait_ready": (
        ("time.time",), "DETERMINISTIC — a bounded deadline wait."),
    "sim/tests/helpers_stack.py::Supervisor.wait_for": (
        ("time.time",), "DETERMINISTIC — a bounded deadline wait for a log line."),
    "sim/tests/helpers_runtime.py::seed_absent": (
        ("time.time",), "RELATIVE — presence is scored as an AGE, so the seed is offsets "
        "from now (a pinned epoch would make every robot absent for years)."),
    _TP + "test_a_bedtime_window_that_wraps_midnight_is_understood": (
        ("datetime.now",), "RELATIVE — only today's DATE is borrowed; hour/minute are set "
        "explicitly (a real date surfaces DST regressions)."),
    _TP + "test_a_content_module_prompt_can_read_presence": (
        ("time.time",), "RELATIVE — the assertion is on the rendered branch, not the stamp."),
    _TP + "test_bedtime_hours_suppress_the_hello": (
        ("datetime.now",), "RELATIVE — `_in_bedtime` reads its own clock; now±30 min "
        "contains now at all 1440 minutes and both weekday keys are written."),
    _TS + "_bedtime_body": (
        ("datetime.now",), "RELATIVE — every window is built from a `now` PARAMETER, so "
        "fixture and assertions reason about one instant."),
    _TS + "_seed_behaviors": (
        ("datetime.now",), "RELATIVE — records whole days before now; recency is an age."),
    _TS + "served": (
        ("datetime.now",), "RELATIVE — the fixture's single clock read, passed down."),
    _TS + "test_a_reported_completion_reaches_the_store_and_the_next_plan": (
        ("datetime.now",), "RELATIVE — the robot's own stamp; the assertion is its age."),
    _TS + "_request_lands_today": (
        (), "DETERMINISTIC — tombstone: it used to re-read the clock; it must not again."),
    _TS + "test_a_request_for_tomorrow_is_not_pinned_into_today": (
        ("datetime.now",), "RELATIVE — one read; +1 day always changes the calendar date."),
    "sim/tests/test_sil_durable_telemetry.py::_wait": (
        ("time.time",), "DETERMINISTIC — a bounded deadline wait."),
    _TT + "test_a_day_caps_its_distinct_event_names_without_losing_the_count": (
        _DAY_KEY, _DAY_KEY_WHY),
    _TT + "test_packet_day_falls_back_to_arrival_when_the_clock_lies": (_DAY_KEY, _DAY_KEY_WHY),
    _TT + "test_packet_day_uses_recorded_at_when_it_is_plausible": (_DAY_KEY, _DAY_KEY_WHY),
    _TT + "test_roll_up_counts_a_day_by_event_and_tracks_its_span": (_DAY_KEY, _DAY_KEY_WHY),
    _TT + "test_roll_up_keeps_the_newest_days_and_counts_what_it_retired": (
        _DAY_KEY, _DAY_KEY_WHY),
    "sim/tests/test_telehealth.py::test_the_timestamp_defaults_to_milliseconds": (
        ("time.time",), "RELATIVE — ms-vs-s can only be proven against a real now; 5 s slack."),
    "sim/tests/test_telehealth_runtime.py::test_the_bedtime_warning_is_reported_and_the_line_is_still_sent": (
        ("datetime.now",), "RELATIVE — now±1h contains now at every minute; both weekday "
        "keys are written."),
    # ---- durations: a busy box must only move the answer AWAY from failure ----
    "sim/tests/helpers_audio.py::Stage.__enter__": (
        ("time.perf_counter",), "RELATIVE — a stopwatch only printed, never asserted."),
    "sim/tests/helpers_audio.py::Stage.__exit__": (
        ("time.perf_counter",), "RELATIVE — the closing read of the same stopwatch."),
    "sim/tests/test_automarkup.py::_interleaved_medians": (
        ("time.perf_counter",), "RELATIVE — a RATIO of medians timed alternately, so "
        "preemption lands on both halves alike."),
    "sim/tests/test_performance.py::_interleaved_medians": (
        ("time.perf_counter",), "RELATIVE — the same interleaved median ratio."),
    "sim/tests/test_brain_latency.py::test_slow_brain_speaks_a_filler_then_the_real_answer": (
        ("time.monotonic",), "RELATIVE — the lower bound is the subject (load only raises "
        "it); the upper bound is 25x the budget."),
    "sim/tests/test_clean_shutdown.py::_Tail.wait_for": (
        ("time.monotonic",), "DETERMINISTIC — a bounded deadline wait."),
    "sim/tests/test_sil_supervisor_readiness.py::test_a_supervisor_whose_subscribe_is_late_still_serves_the_robot": (
        ("time.monotonic",), "RELATIVE — a LOWER bound; a busy box only makes `booted` larger."),
    _TK + "test_a_fault_wholly_inside_a_turn_is_seen": (
        ("time.monotonic",), "RELATIVE — an overlap between two reads; stretching the "
        "bracket keeps it true."),
    _TK + "test_a_fault_still_in_flight_is_seen": (
        ("time.monotonic",), "RELATIVE — an open window runs to +inf."),
    _TK + "test_a_fault_before_or_after_the_turn_is_not_seen": (
        ("time.monotonic",), "RELATIVE — the window is closed before `after` is read, so "
        "delay only widens the gap."),
    "sim/tests/test_leave_taking.py::test_a_looping_transcript_cannot_stall_the_patterns": (
        ("time.perf_counter",), "RELATIVE — a ceiling 250x the measured worst case (2 ms on "
        "20 KB): load multiplies a linear match, while an ambiguous pattern grows 2^n and "
        "is past the ceiling by n=25."),
}

_TOMBSTONES = {k for k, (cons, _) in REVIEWED.items() if not cons}


def _dotted(node):
    """`time.strftime` -> ('time', 'strftime'); anything but a plain dotted name -> None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return tuple(reversed(parts + [node.id])) if isinstance(node, ast.Name) else None


class _Scan(ast.NodeVisitor):
    """Clock calls, grouped by the enclosing def/class scope."""

    def __init__(self):
        self.stack, self.hits = [], {}

    def _nest(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _nest

    def visit_Call(self, node):
        name = _dotted(node.func)
        for key in ((name, name[-3:], name[-2:]) if name else ()):
            if key in PY_CLOCK_CALLS:
                scope = ".".join(self.stack) or "<module>"
                self.hits.setdefault(scope, set()).add(PY_CLOCK_CALLS[key])
                break
        self.generic_visit(node)


#: Whole-comment lines only: a real read with a trailing `// note` is still scanned.
_JS_COMMENT_LINE = re.compile(r"^\s*(?://|\*|/\*)")


def js_constructs(src: str) -> tuple:
    code = "\n".join(ln for ln in src.splitlines() if not _JS_COMMENT_LINE.match(ln))
    return tuple(sorted(n for n, p in JS_CLOCK_PATTERNS if p.search(code)))


def _scan() -> dict:
    """`{"path::scope": (constructs...)}` for every clock read in the test tree."""
    found: dict = {}
    for path in sorted(glob.glob(os.path.join(REPO, "sim", "tests", "*.py"))):
        rel = os.path.relpath(path, REPO)
        scan = _Scan()
        scan.visit(ast.parse(open(path).read(), rel))
        for scope, constructs in scan.hits.items():
            found[f"{rel}::{scope}"] = tuple(sorted(constructs))
    mjs = glob.glob(os.path.join(REPO, "sim", "*.mjs")) + glob.glob(
        os.path.join(REPO, "sim", "tests", "edge", "**", "*.mjs"), recursive=True)
    for path in sorted(mjs):
        constructs = js_constructs(open(path).read())
        if constructs:
            found[os.path.relpath(path, REPO)] = constructs
    return found


def test_every_clock_read_in_the_test_tree_is_reviewed_and_every_row_is_current():
    found = _scan()
    unreviewed = sorted(f"{k}  {list(v)}" for k, v in found.items() if k not in REVIEWED)
    assert not unreviewed, ("these tests read the clock and are not in REVIEWED — make them "
                            "deterministic or add a row saying why they are safe:\n  "
                            + "\n  ".join(unreviewed))
    stale = sorted(k for k in REVIEWED if k not in found and k not in _TOMBSTONES)
    assert not stale, f"rows whose clock read is gone (delete them): {stale}"
    assert not [k for k in _TOMBSTONES if k in found], "a tombstoned site reads the clock again"
    drifted = sorted(k for k, v in found.items() if k in REVIEWED and REVIEWED[k][0] != v)
    assert not drifted, f"the constructs changed under these rows — re-review them: {drifted}"
    for key, (_, why) in REVIEWED.items():
        assert why.split(" — ")[0] in ("DETERMINISTIC", "RELATIVE", "BOTH BRANCHES"), key


def test_the_python_scanner_sees_planted_reads_and_ignores_waits():
    scan = _Scan()
    scan.visit(ast.parse(
        "import datetime, time\n"
        "def a():\n    return datetime.datetime.now()\n"
        "class C:\n    def d(self):\n        return datetime.date.today(), time.time()\n"
        "def dur():\n    return time.monotonic(), time.perf_counter()\n"
        "def safe():\n    return time.sleep(0)\n"))
    assert scan.hits == {"a": {"datetime.now"}, "C.d": {"date.today", "time.time"},
                         "dur": {"time.monotonic", "time.perf_counter"}}


@pytest.mark.parametrize("src, want", [
    ("const t = Date.now();", ("Date.now",)),
    ("const d = new Date();", ("new Date()",)),
    ("const h = d.getHours();", ("getHours/getMinutes",)),
    ("const s = d.toISOString();", ("toISOString",)),
    ("const d = new Date(1756800000000);", ()),
    ("const t = Date.now(); // a trailing comment hides nothing", ("Date.now",)),
    ("  // this block deliberately never calls Date.now()", ()),
    (" * the route derives its hour from Date.now(), we do not", ()),
])
def test_the_js_scanner_reads_code_not_comments(src, want):
    assert js_constructs(src) == want
