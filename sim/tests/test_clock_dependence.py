"""Every test that reads the clock is listed here with a verdict — a ratchet.

Several flakes came from one disease: a test asked for an activity 20 minutes out and
asserted it was in *today's* plan (false for the last 20 minutes of a day); a bedtime of
`["00:00", "23:59"]` read as "all day" and was false at 23:59; packets stamped
`TODAY - 30` at import filed under yesterday for 30 s after midnight. (A look-alike can
be a product bug instead: an hour-dependent PLANNER once failed only on UTC runners. Ask
which of the two read the hour before reaching for a row.)

The shared shape is not "a test used the clock" — plenty must, and the runtime reads its
own clock so pinning the test's would prove nothing. It is **a test that reads the clock
and nobody wrote down why that is safe**. So `REVIEWED` names every clock read in the
test tree with a verdict and reason, asserted from **both** sides:

* a clock read that is not listed → **fail** (nothing new arrives unreviewed);
* a listed entry that no longer exists, or whose constructs changed → **fail** (the list
  can only shrink, and a `datetime.now()` added to a reviewed deadline loop is caught).

**What counts.** `time.time`, `time.localtime`, `time.gmtime`, `time.strftime`,
`time.ctime`, `time.asctime`, `datetime.now`, `datetime.utcnow`, `datetime.today`,
`date.today`, plus the monotonic `time.monotonic`/`time.perf_counter` (see
`PY_CLOCK_CALLS`); in the node suites `Date.now`, a no-argument `new Date()`, the
`get{Hours,Minutes,Day,Date,FullYear}` readers and `toISOString`. `time.sleep` is a
wait, not a read.

**When this fails on you.** It names the file and scope. Pick a verdict, do the work,
then add the row — never one that says "looks fine":

* `DETERMINISTIC` — the read was removable and was removed (no row needed).
* `RELATIVE` — the clock is genuinely the subject (a freshness stamp, a window the
  runtime evaluates against its own `now`, an age). Say what makes the answer the same at
  all 1440 minutes of a day, and prove it if that is not obvious.
* `BOTH BRANCHES` — the scenario cannot be built at some hours. Assert whichever branch
  is real, the other still strict, and never `pytest.skip`.

The unit is one row per *scope* (function/class): two reads that must agree about the
same instant belong in one place anyway — read once and pass it down.
"""
from __future__ import annotations

import ast
import glob
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Clock calls by dotted tail. Monotonic clocks are included because a DURATION
#: asserted under a constant asks the machine a question instead of the product (an
#: absolute `p95 < 1.0` ms passed on a quiet box and failed at load 88). The duration
#: rows below say what keeps each honest: a timeout that only waits longer, a lower bound
#: a slow box pushes further from failure, or a ratio whose halves are preempted alike.
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

    # ---- import-time state vs call-time state ----------------------------------------
    "sim/tests/test_telemetry_runtime.py::test_telemetry_survives_a_supervisor_restart": (
        ("datetime.now",),
        "RELATIVE — two reads of the SAME clock at the same moment, never a fixed date. "
        "`history_view` counts back from today at CALL time while the fixture's `TODAY` is "
        "fixed at IMPORT; a run spanning midnight once saw the window advance under it. "
        "Pinning the stamp to noon stops an offset crossing a boundary but not the WINDOW "
        "moving, so the tail is compared against the view's own notion of today. Proved by "
        "simulation: with `TODAY` pinned to yesterday noon the old assertion fails and this "
        "one passes. General shape: any assertion comparing import-time to call-time state "
        "is clock-dependent however it is stamped."),

    # ---- node suites (file-level: these have no scope the scanner can name) ----------
    "sim/test_audio.mjs": (
        ("Date.now",),
        "RELATIVE — the requestAnimationFrame shim hands `cb(Date.now())` to code that "
        "only ever diffs consecutive frame stamps. A duration in disguise; no assertion "
        "reads the value."),
    "sim/test_csp.mjs": (
        ("Date.now",),
        "RELATIVE — a CEILING on a wait, and nothing else: `until()` compares "
        "`Date.now() - t0` against a fixed budget purely to stop polling for puppeteer's "
        "console listeners, which fill in THIS process. The value never reaches a check."),
    "sim/test_console_insights.mjs": (
        ("Date.now",),
        "RELATIVE — an ORDERING of two reads of the same clock, never a date: each "
        "intercepted DELETE must land at or after the stamp taken just before the SECOND "
        "click, proving the two-click arming on the wire. `a >= b` holds at every minute; "
        "a clock stepping backwards would redden the suite rather than hide a bug."),
    "sim/eval_live.mjs": (
        ("Date.now", "new Date()", "toISOString"),
        "RELATIVE — and NOT A TEST: a hand-run live evaluation (`--yes` required) with no "
        "assertions. `Date.now()` around a `fetch` is a latency and `new Date()."
        "toISOString()` names the artifact file. Kept as a visible row rather than a glob "
        "exception, so a future assertion here meets this reasoning."),
    "sim/tests/edge/demo_proxy/07_turn_features.mjs": (
        ("Date.now",),
        "RELATIVE — an AGE, never a date: a context blob stamped `now - CONTEXT_TTL_S - 60` "
        "must be expired and one at `now` carried. `chat.js` verifies against the real "
        "clock (no injectable `nowS`), so a pinned stamp would test a clock the code does "
        "not use. Expired/fresh holds at every minute; no assertion reads either value."),
    "sim/test_demo_tickets.mjs": (
        ("Date.now",),
        "RELATIVE — a ticket is aged `Date.now()/1000 - 61` to make it one second past a "
        "60 s expiry. The subject IS the age, and 61 s from any instant is expired at "
        "every hour."),
    "sim/tests/edge/turnstile/02_fail_open.mjs": (
        ("Date.now",),
        "RELATIVE — an ELAPSED TIME that is the subject: with a `siteverify` that never "
        "answers, `Date.now() - started` must be at least the configured 120 ms deadline "
        "and well under the 20 s upstream timeout (mutation row D3e — a deadline never "
        "passed to `fetch` looks identical otherwise). Both bounds are durations between "
        "two reads of one clock; a jump would redden rather than hide a hang."),
    "sim/tests/edge/mode/03_mode_machine.mjs": (
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
    "sim/tests/helpers_runtime.py::seed_absent": (
        ("time.time",),
        "RELATIVE — presence is scored as an AGE against `greet_after_s`, so the seeded "
        "state is offsets from now. A pinned epoch would make every robot absent for "
        "years and the suites would assert nothing."),
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
        "RELATIVE by necessity — `rt._in_bedtime` reads the real clock itself, so pinning "
        "the test's clock would test a different function. A now±30 min window contains "
        "now at all 1440 minutes (asserted exhaustively by `test_the_synthetic_windows_the_"
        "two_tests_above_build_hold_at_every_minute`), and both bedtime keys are written so "
        "a Fri→Sat midnight between the two reads cannot pick the other one."),
    "sim/tests/test_presence_runtime.py::test_outside_the_bedtime_window_the_hello_is_allowed": (
        ("datetime.now",),
        "RELATIVE by necessity — the mirror of the row above; a now+2h…+4h window excludes "
        "now at all 1440 minutes, asserted by the same exhaustive test. No `pytest.skip`: "
        "a skip that cannot fire is an escape hatch for a regression."),

    # ---- the day plan ----------------------------------------------------------------
    "sim/tests/test_schedule_sil_e2e.py::_bedtime_body": (
        ("datetime.now",),
        "RELATIVE — bedtime and 'due today' are wall-clock by contract, so every window is "
        "built relative to a `now` PARAMETER, letting the fixture and the assertions reason "
        "about ONE instant. `_request_offset` asks two slots ahead (or behind, late in the "
        "day), so the request lands today at all 1440 minutes and the pinning test has one "
        "strict branch."),
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
        "DETERMINISTIC — a tombstone recording the fix: it used to read the clock a second "
        "time, independently of the config it asked about. It now takes the fixture's "
        "instant and is used to BUILD a request that always lands today. If this row ever "
        "gains a construct, that regressed."),
    "sim/tests/test_schedule_sil_e2e.py::test_a_request_for_tomorrow_is_not_pinned_into_today": (
        ("datetime.now",),
        "RELATIVE — one read, used as both the bedtime anchor and the base for a request "
        "stamped a whole day out. Naive +1 day always changes the calendar date (DST "
        "included), so this holds at every hour instead of a branch reachable only in the "
        "last 20 minutes of a day."),

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
        "written so the weekday never matters. A fully deterministic pair sits beside it "
        "pinning the helper's real semantics."),

    # ---- DURATIONS (monotonic clocks) ------------------------------------------------
    # A duration reads no date, so none of these can fail at 23:59. The question each
    # row answers is the other one: does a BUSY machine change the answer? A timeout
    # that only ever waits longer does not; a lower bound a slow box pushes further
    # from failure does not; a ratio whose halves are preempted alike does not. An
    # upper bound on a measured duration DOES.

    "sim/tests/helpers_audio.py::Stage.__enter__": (
        ("time.perf_counter",),
        "RELATIVE — and asserted on by nothing. `Stage` is a stopwatch whose `seconds` is "
        "only interpolated into `print()` by the live suites, so a slow box makes the "
        "number bigger and no test redder. If an assert on `.seconds` or `timing_line` is "
        "ever added it must be a RATIO, not a ceiling."),
    "sim/tests/helpers_audio.py::Stage.__exit__": (
        ("time.perf_counter",),
        "RELATIVE — the closing read of the pair above, same reason. The subtraction of "
        "two reads of one monotonic clock is an ELAPSED TIME that is reported, never "
        "gated."),

    "sim/tests/test_automarkup.py::_interleaved_medians": (
        ("time.perf_counter",),
        "RELATIVE — a RATIO. An absolute `p95 < 1.0` ms passed on a quiet box and failed "
        "at load 88 (p95 7.3 ms vs median 0.34 ms: the scheduler, not the code). It is now "
        "the median cost of `annotate` over the median of a fixed calibration pass, timed "
        "ALTERNATELY in one loop so a preemption lands on both alike (clean band 0.86-0.91 "
        "at load 88-104, gate 2.0). Separate loops drifted up to 38% apart."),
    "sim/tests/test_performance.py::_interleaved_medians": (
        ("time.perf_counter",),
        "RELATIVE — the same ratio, against the FLOOR the planner replaces (a real "
        "alternative implementation). Taken at the MEDIAN: at load 104 median ratios held "
        "1.999-2.025 while p95 ratios over the same samples read 1.80 to 45.48. A ratio "
        "is only load-immune at a percentile where the signal, not the scheduler, decides "
        "the value."),

    "sim/tests/test_brain_latency.py::test_slow_brain_speaks_a_filler_then_the_real_answer": (
        ("time.monotonic",),
        "RELATIVE — `0.2 <= heard_at < 5.0`, and both halves are honest. The lower bound "
        "is the subject (the filler must NOT precede the budget) and a busy box only "
        "raises `heard_at`, away from it. The upper bound is 25x the budget: 'did it land "
        "inside the window', which preemption cannot reach without a genuinely broken "
        "runtime."),

    "sim/tests/test_clean_shutdown.py::_Tail.wait_for": (
        ("time.monotonic",),
        "DETERMINISTIC — `deadline = monotonic() + timeout`, then poll until a needle is "
        "printed. A bounded WAIT, not a measurement: a slow box waits longer and still "
        "passes; it reddens only if the line genuinely never appears (callers pass "
        "180 s / 30 s)."),

    "sim/tests/test_sil_handshake.py::test_the_announcement_really_did_wait_for_the_suback": (
        ("time.monotonic",),
        "RELATIVE — a LOWER bound: `waited >= LATE_SUBSCRIBE_S * 0.5` proves `announce()` "
        "blocked for the SUBACK instead of returning early. Preemption inflates `waited`, "
        "which pushes the assertion further from failure, so load can only make this "
        "greener. The same expression as an upper bound would be the defect."),
    "sim/tests/test_sil_supervisor_readiness.py::test_a_supervisor_whose_subscribe_is_late_still_serves_the_robot": (
        ("time.monotonic",),
        "RELATIVE — the same shape and the same direction: `booted >= HOLD_SUBSCRIBE_S * "
        "0.5` proves the boot really blocked on the held SUBSCRIBE. A busy machine only "
        "makes `booted` larger."),

    "sim/tests/test_soak_accounting.py::test_a_fault_wholly_inside_a_turn_is_seen": (
        ("time.monotonic",),
        "RELATIVE — an OVERLAP between two reads of the same monotonic clock, with no "
        "duration compared to any constant. `t_start` and `t_end` bracket the window and "
        "the assertion is that the recorded outage intersects them; stretching the "
        "bracket by preempting it keeps that true."),
    "sim/tests/test_soak_accounting.py::test_a_fault_still_in_flight_is_seen": (
        ("time.monotonic",),
        "RELATIVE — an OPEN window runs to +inf, so `overlaps(now, now + 8.0)` is true "
        "for any `now` the clock returns. No timing can change the answer."),
    "sim/tests/test_soak_accounting.py::test_a_fault_before_or_after_the_turn_is_not_seen": (
        ("time.monotonic",),
        "RELATIVE — the converse, and safe in the same direction: the window is already "
        "CLOSED when `after = monotonic() + 0.05` is computed, so `after` is strictly "
        "past it however long the process was descheduled first. Delay only widens the "
        "gap the assertion needs."),
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


#: Lines that are ENTIRELY a comment. Anything else — including a code line with a
#: trailing `// note` — is still scanned, so nothing is hidden by appending a comment.
_JS_COMMENT_LINE = re.compile(r"^\s*(?://|\*|/\*)")


def js_constructs(src: str) -> tuple:
    """The clock constructs a `.mjs` suite READS, ignoring the ones it merely NAMES.

    A guard must assert over code, not the whole file: a comment explaining why a block
    does NOT read the clock would otherwise count as a read. Only whole-comment lines are
    stripped, so a real read with a trailing comment is still found
    (`test_the_comment_strip_does_not_hide_a_real_clock_read`).
    """
    code = "\n".join(ln for ln in src.splitlines() if not _JS_COMMENT_LINE.match(ln))
    return tuple(sorted(n for n, p in JS_CLOCK_PATTERNS if p.search(code)))


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
    # the edge suites' section modules too, so a read cannot escape by moving there
    mjs = glob.glob(os.path.join(REPO, "sim", "*.mjs")) + glob.glob(
        os.path.join(REPO, "sim", "tests", "edge", "**", "*.mjs"), recursive=True)
    for path in sorted(mjs):
        rel = os.path.relpath(path, REPO)
        with open(path) as fh:
            constructs = js_constructs(fh.read())
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


_JS_SNIPPETS = {"Date.now": "const t = Date.now();",
                "new Date()": "const d = new Date();",
                "getHours/getMinutes": "const h = d.getHours();",
                "toISOString": "const s = d.toISOString();"}


@pytest.mark.parametrize("src, want", [
    *((snippet, (name,)) for name, snippet in _JS_SNIPPETS.items()),
    ("const t = Date.now(); // a trailing comment hides nothing", ("Date.now",)),
    ("  // this block deliberately never calls Date.now()", ()),
    (" * the route derives its hour from Date.now(), we do not", ()),
])
def test_the_comment_strip_does_not_hide_a_real_clock_read(src, want):
    """The loosened guard must still bite: every construct on a code line (even one
    carrying a trailing comment) is found; a line of pure prose is not."""
    assert js_constructs(src) == want


def test_the_reviewed_list_can_only_shrink():
    """Direction 2: a row that no longer describes reality is a stale exemption."""
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
    """A row that says nothing launders an unreviewed test into a reviewed-looking one."""
    verdicts = ("DETERMINISTIC", "RELATIVE", "BOTH BRANCHES")
    for key, (_, reason) in sorted(REVIEWED.items()):
        assert any(reason.startswith(v) for v in verdicts), \
            f"{key}: the reason must open with one of {verdicts}, got {reason[:40]!r}"
        assert len(reason) > 80, f"{key}: 'why is this safe' needs more than {reason!r}"


def test_the_scanner_sees_a_clock_read_that_is_deliberately_planted():
    """The guard's own guard: a scanner that silently matched nothing would pass every
    assertion above forever."""
    src = ("import datetime, time\n"
           "def a():\n"
           "    return datetime.datetime.now()\n"
           "def b():\n"
           "    return time.strftime('%Y', time.localtime())\n"
           "class C:\n"
           "    def d(self):\n"
           "        return datetime.date.today(), time.time()\n"
           "def dur():\n"
           "    return time.monotonic(), time.perf_counter()\n"
           "def safe():\n"
           "    return time.sleep(0)\n")
    scan = _Scan()
    scan.visit(ast.parse(src))
    assert scan.hits["a"] == {"datetime.now"}
    assert scan.hits["b"] == {"time.strftime", "time.localtime"}
    assert scan.hits["C.d"] == {"date.today", "time.time"}
    assert scan.hits["dur"] == {"time.monotonic", "time.perf_counter"}, \
        "durations are scanned too — see PY_CLOCK_CALLS for why"
    assert "safe" not in scan.hits, (
        "`time.sleep` is deliberately NOT flagged: a WAIT yields no value to assert on; "
        "the read that TIMES it is the one that can carry the defect, and it is scanned.")
    for name, pattern in JS_CLOCK_PATTERNS:
        assert pattern.search(_JS_SNIPPETS[name]), name
    assert not JS_CLOCK_PATTERNS[1][1].search("new Date(1756800000000)"), \
        "a pinned instant is the cure, not the disease"
