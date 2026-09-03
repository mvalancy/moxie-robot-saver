"""Behavior-markup seam — the one place a reply without its own markup gets performed.

Three generations live behind one signature, chosen by `MOXIE_EXPRESSIVE`:

| `MOXIE_EXPRESSIVE` | What answers | Scored fields |
|---|---|---|
| `planner` (default) | the **behavior planner** — `moxie_sdk.performance.render(validate(plan(…)))` | yes |
| `floor` | the **markup floor** — `moxie_sdk.automarkup.annotate` | yes (scored, not rendered) |
| `off` | v1's passthrough: Moxie reads the line out like a speaker | no |

**The planner always degrades to the floor.** `plan()` returns a `Performance`, returns
`None`, or blows its budget; in the last two the seam calls `annotate()` and the wire
shape is *identical* — a child never notices which one answered
(`docs/architecture/backlog/expressiveness.md` §2.6). A planner failure is a downgrade in
expressiveness, never an error, because the floor already produces good markup. Every
exception path is proven to land there by `sim/tests/test_performance.py`'s fault
injection, and a repeat offender is latched off by the budget breaker below rather than
being allowed to tax every turn.

**Scoring is separate from rendering.** `plan()` is the scorer in *both* `planner` and
`floor` mode, so `floor` is a pure rendering rollback: the wire keeps its `mood`,
`mood_intensity`, `dialog_act`, `emotion` and `signal` while the markup goes back to the
word-level generator. `off` is the one-variable rollback all the way to v1.

The seam itself runs once per spoken chunk, on the hot path between the first token and
the first audio, which is why everything behind it is pure, stdlib-only and deterministic.

`MOXIE_AUTOMARKUP=0` still forces the passthrough (it predates `MOXIE_EXPRESSIVE` and is
kept as an alias for `off`).
"""
import os
import sys
import time

# The SDK is a sibling package of `supervisor/` in the image; the runtime already puts
# `mqtt/` on the path, but keep the seam importable on its own for tests and tools.
_MQTT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MQTT not in sys.path:
    sys.path.insert(0, _MQTT)

from moxie_sdk.automarkup import annotate, enabled     # noqa: E402
from moxie_sdk import performance as _perf             # noqa: E402

MODE_OFF, MODE_FLOOR, MODE_PLANNER = "off", "floor", "planner"
_MODES = (MODE_OFF, MODE_FLOOR, MODE_PLANNER)

#: A staged line may not cost more than this on the hot path. It is a ceiling, not a
#: target: the planner measures at ~0.1 ms/line, so anything near this is a regression or
#: a machine under real load, and either way the floor should answer.
PLAN_BUDGET_MS = 8.0
#: Over-budget lines in a row before the seam latches to the floor for the rest of the
#: process. One slow line is noise; a run of them is a planner that must stop taxing every
#: turn (§2.6: "when the budget blows, the floor answers").
PLAN_BUDGET_STRIKES = 3

_strikes = 0
_latched = False
_reported = False


class Staged:
    """One line, staged: the markup to speak plus the scored output the wire carries.

    `performance` is the validated `Performance` (or None when the floor answered), kept
    so the preview hook can show an author what was planned and flag any dropped id.
    """
    __slots__ = ("markup", "scored", "performance", "mode")

    def __init__(self, markup, scored=None, performance=None, mode=MODE_OFF):
        self.markup = markup
        self.scored = dict(scored or {})
        self.performance = performance
        self.mode = mode

    def __repr__(self):                       # pragma: no cover - diagnostics only
        return f"Staged(mode={self.mode!r}, scored={self.scored!r})"


def expressive_mode() -> str:
    """`planner` | `floor` | `off` — which generation answers this line.

    An unrecognized value is treated as the default rather than refused: this variable is
    a rollback lever, and a typo in it must not take the appliance's voice away.
    """
    if not enabled():                         # MOXIE_AUTOMARKUP=0 predates this variable
        return MODE_OFF
    mode = os.environ.get("MOXIE_EXPRESSIVE", MODE_PLANNER).strip().lower()
    return mode if mode in _MODES else MODE_PLANNER


def planner_latched() -> bool:
    """True once the budget breaker has given up on the planner for this process."""
    return _latched


def reset_budget() -> None:
    """Clear the breaker (tests, and a supervisor that reloads its config)."""
    global _strikes, _latched, _reported
    _strikes, _latched, _reported = 0, False, False


def _ctx(kw: dict) -> dict:
    """The seam's kwargs -> the planner's `ctx`. `annotate`'s names are the seam's names."""
    return {
        "mood": kw.get("mood_hint"),
        "gesture": kw.get("gesture_hint"),
        "dialog_act": kw.get("dialog_act"),
        "emotion": kw.get("emotion"),
        "signal": kw.get("signal"),
        "intensity": kw.get("intensity"),
        "look": kw.get("look"),
        "timed_out": kw.get("timed_out"),
        "icons": kw.get("icons", False),
        "icon": kw.get("icon"),
        "sfx": kw.get("sfx", False),
        "turn_key": kw.get("turn_key", ""),
        "chunk_index": kw.get("chunk_index", 0),
    }


def _floor_kwargs(kw: dict) -> dict:
    """Only the kwargs `annotate` actually takes — the planner's extras are dropped."""
    keep = ("mood_hint", "gesture_hint", "look", "intensity", "turn_key",
            "chunk_index", "icons", "sfx", "trees")
    return {k: v for k, v in kw.items() if k in keep}


def _blew_budget(elapsed_ms: float) -> None:
    global _strikes, _latched, _reported
    if elapsed_ms <= PLAN_BUDGET_MS:
        _strikes = 0
        return
    _strikes += 1
    if _strikes >= PLAN_BUDGET_STRIKES and not _latched:
        _latched = True
        print(f"[markup] behavior planner over budget "
              f"({elapsed_ms:.1f} ms > {PLAN_BUDGET_MS:g} ms) "
              f"{_strikes}x in a row; falling back to the markup floor", flush=True)


def _report_failure(exc: BaseException) -> None:
    """Say it once. A planner that is failing every line must not fill the journal."""
    global _reported
    if not _reported:
        _reported = True
        print(f"[markup] behavior planner failed ({type(exc).__name__}: {exc}); "
              f"falling back to the markup floor", flush=True)


def perform(text: str, **kw) -> Staged:
    """One spoken line -> markup **and** its scored output. The seam's full answer.

    Never raises and never returns None markup: on any planner failure — an exception, a
    `plan()` that declined, a blown budget, or `MOXIE_EXPRESSIVE=floor` — the floor
    answers with the identical wire shape.
    """
    mode = expressive_mode()
    if mode == MODE_OFF:
        return Staged(text, mode=MODE_OFF)

    scored, staged = {}, None
    if not _latched:
        started = time.perf_counter()
        try:
            staged = _perf.validate(_perf.plan(text, ctx=_ctx(kw)))
        except Exception as e:                # a planner failure may never cost a turn
            _report_failure(e)
            staged = None
        else:
            _blew_budget((time.perf_counter() - started) * 1000.0)
        if staged is not None:
            scored = staged.scored()

    if mode == MODE_PLANNER and staged is not None and not _latched:
        try:
            markup = _perf.render(staged)
        except Exception as e:
            _report_failure(e)
        else:
            if markup:
                return Staged(markup, scored, staged, MODE_PLANNER)
    return Staged(annotate(text, **_floor_kwargs(kw)), scored, staged, MODE_FLOOR)


def make_markup(text: str, **kw) -> str:
    """One spoken line -> behavior markup. `turn_key`/`chunk_index` keep a streamed
    answer stable; see `moxie_sdk.performance.plan` and `moxie_sdk.automarkup.annotate`
    for the rules. Kept as the seam's original signature so every call site still works."""
    return perform(text, **kw).markup
