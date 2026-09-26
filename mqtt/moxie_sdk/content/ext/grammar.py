"""The language definition: the error value, names, capabilities, limits, the op and
statement tables. Pure data plus two name normalizers; nothing here runs a program."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


EXT_FORMAT = 1
"""The only `ext_format` accepted; a format bump is a new number, never a wider grammar."""


class _Error:
    """The distinguished, falsy, propagating error value (§4.6) — not an exception.

    Produced by `/` and `%` by zero, `int("banana")`, mixed-type `sort`, and any op given
    an error. Testable with `{"has": [expr]}`; reaching a `say`/`remember`/`act`/`markup`
    fails the extension (§6.4) rather than being spoken.
    """
    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:                  # pragma: no cover - debugging only
        return "<ext.ERROR>"


ERROR = _Error()


def is_error(v) -> bool:
    return isinstance(v, _Error)


# --------------------------------------------------------------------------- #
# Identifiers — capability and op names, normalized before they are matched
# --------------------------------------------------------------------------- #

#: A capability or op name: lowercase ASCII, digits, `_`, `.`; anything else is refused.
_IDENT = re.compile(r"^[a-z0-9_.]+$")

#: A `{"var": …}` path. No segment may begin with `_`, so `__class__`/`_meta` are
#: invalid programs rather than blocked ones (X1).
_PATH = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$", re.I)

#: A `remember`/`forget`/`scratch` key: dot-segmented, no empty segment, no `/`, and no
#: `_`-leading segment (`_meta`/`_provenance` belong to `MemoryStore`).
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")

#: A `format` spec: explicit and bounded (width ≤ 5 digits) so output is byte-stable
#: across the Python and (P1) JS evaluators.
_FMT = re.compile(r"^(?P<zero>0?)(?P<width>\d{0,5})(?:\.(?P<prec>\d{1,3}))?(?P<kind>[dfs])$")


def normal_name(raw) -> str:
    """`raw` if it is already NFKC-normal and matches `_IDENT`, else `""`.

    Normalization is a check, not a repair (X8): folding `"ｍemory.write"` would grant a
    capability whose written form differs from what the parent's review rendered, so
    homoglyphs, zero-width and RTL characters and upper case are refused outright.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    if unicodedata.normalize("NFKC", raw) != raw:
        return ""
    return raw if _IDENT.match(raw) else ""


# --------------------------------------------------------------------------- #
# The capability table (§5) — parent-facing words are generated from a fixed table
# --------------------------------------------------------------------------- #

#: `{capability: the sentence a parent reads}`. Never author-supplied (a pack could write
#: a reassuring lie). T13 asserts every accepted capability has words here.
CAPABILITY_WORDS = {
    "say": "Can speak to your child",
    "handled": "Can answer on its own, without asking the AI",
    "session": "Can tell how far into a chat you are",
    "child.nickname": "Can use your child's first name",
    "child.profile": "Can read your child's pronouns, birthday and your notes",
    "clock": "Can check the time",
    "random": "Can pick things unpredictably",
    "memory.read": "Can read what it remembered from this activity",
    "memory.write": "Can remember things from this activity",
    "presence": "Can tell whether somebody is in front of Moxie",
    "markup": "Can make Moxie move and play sounds",
    "subscribe": "Can listen for things the robot notices",
    "brain": "Can ask the AI a question of its own",
    "schedule.request": "Can ask to be offered in the day's plan",
}

#: `act.<name>` is granted per *name*: an action with no parent-facing words cannot be
#: declared. This table is also the closed allowlist of `function_id`s a pack may put on
#: the wire (`content_app.execution_actions_of`) — one table, so the two cannot drift. A
#: stranger's pack, like a printed QR card, may name only reviewed things
#: (qr-launch-cards.md §P0-b).
ACTION_WORDS = {
    "eb_timer_request": "Can ask Moxie to set or cancel a timer",
    "eb_enable_qr": "Can turn Moxie's QR scanner on",
    "eb_wake": "Can wake Moxie up",
}

#: The closed event vocabulary a `subscribe` statement may name — `ACTION_WORDS`'s inbound
#: twin (vision.md §1.1-1.2; `eb-lost-face` is RemoteModuleAPI's alias of
#: `eb-lost-target`). A transcription of `presence.VISION_EVENTS`, not an import, because
#: X7 bounds this module's imports; `test_ext_subscribe.py` asserts the two stay equal.
SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",
                    "eb-qr-event", "eb-dr-event", "eb-br-event")

#: Granted with no parent action (§5.1). Anything else needs a caller to pass a wider
#: `grants` set; deliberately no env var or console control until the P1 grant flow.
DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname"})

#: Declared and rendered in the review, but still refused at load because nothing can
#: honour them yet (better refused out loud than silently inert):
#:   * `brain` — needs the one-call-per-turn budget (brief §5.1) first.
#:   * `schedule.request` — needs the recommender's parent-request channel (P2).
#: (`act.<name>` and `subscribe` are honoured end to end; a pack's `subscribe` is merged
#: into, never replaces, the runtime's own vision subscription.)
P1_CAPABILITIES = frozenset({"brain", "schedule.request"})

def _is_p1(cap: str) -> bool:
    """True for a capability this appliance declares, renders and **refuses** — one
    predicate for the §8 conformance generator to lift when a capability becomes real."""
    return cap in P1_CAPABILITIES


#: Hook points. `turn.after` (output-safety ordering) and `session.end` (overlaps the
#: declarative `memory` block) are P1.
HOOKS = ("global", "turn.before")

#: The roots `{"var": "…"}` may name and the capability each costs (`None` = free). With
#: `OPS` this is the complete set of strings that resolve to anything (§5.2).
FACT_ROOTS = {
    "speech": None,
    "entities": None,
    "input_vars": None,
    "scratch": None,
    "child": "child.nickname",       # refined below: any field but `nickname` is profile
    "memory": "memory.read",
    "session": "session",
    "presence": "presence",
}


def _path_capability(path: str) -> str | None:
    """The capability a `{"var": path}` costs, or None when it is free."""
    root, _, rest = path.partition(".")
    if root == "child":
        return "child.nickname" if rest == "nickname" else "child.profile"
    return FACT_ROOTS.get(root)


# --------------------------------------------------------------------------- #
# Limits (§6.2). `mqtt/config.py` overrides these from the env.
# --------------------------------------------------------------------------- #

MAX_DEPTH = 32                 # expression nesting; the evaluator is depth-counted
MAX_STATEMENTS_PER_RULE = 32
MAX_RULES = 64
MAX_NODES = 4096               # whole extension; a giant AST is refused at import
MAX_CAPABILITIES = 32
MAX_REPEAT = 16                # the corpus's `snd * 3`, with room
MAX_ARGS = 32

DEFAULT_MAX_STEPS = 10000
DEFAULT_BUDGET_S = 0.25
DEFAULT_MAX_VALUE_BYTES = 16384
DEFAULT_MAX_TOTAL_BYTES = 262144
DEFAULT_MAX_BREACHES = 3

#: Output caps (§6.3), applied by the host when it applies the effect list.
MAX_SAY_CHARS = 1000
MAX_MARKUP_CHARS = 8192
MAX_ACTIONS = 4
MAX_SUBSCRIPTIONS = 8
MAX_MEMORY_WRITES = 8
MAX_NOTES = 4
MAX_NOTE_CHARS = 200


@dataclass
class Limits:
    """One turn's budget. Every field is an env var in `mqtt/config.py` (§6.2)."""
    max_steps: int = DEFAULT_MAX_STEPS
    budget_s: float = DEFAULT_BUDGET_S
    max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES


@dataclass
class ExtResult:
    """What the evaluator returns. Always returns; never raises (§6.4)."""
    ok: bool
    effects: list = field(default_factory=list)
    reason: str = ""
    breach: str = ""
    steps: int = 0
    notes: list = field(default_factory=list)
    handled: bool = False

    #: A sentence for the parent-facing `ext_events` ring — plain language, no jargon.
    @property
    def sentence(self) -> str:
        return BREACH_WORDS.get(self.breach, self.reason or "it stopped working")


#: `breach` codes → what the console tells a parent. The child is told nothing (§6.4).
BREACH_WORDS = {
    "steps": "it took too many steps",
    "budget": "it took too long",
    "value": "it tried to build something too big",
    "total": "it tried to build too much",
    "error": "one of its sums did not work out",
    "capability": "it asked for something it is not allowed to do",
    "invalid": "it is not a program this appliance can read",
    "output": "it tried to say more than it is allowed to",
}


#: `{op: (min_args, max_args, capability_or_None)}`. Closed and frozen as a literal in
#: `test_ext_escapes.py::X1`, so growing it needs a test edit and a reviewer (R1). Never
#: add: name-to-object resolution, access on non-JSON values, regex construction, unbounded
#: string multiplication, `eval`, or anything returning a host handle.
OPS = {
    # arithmetic
    "+": (1, MAX_ARGS, None), "-": (1, 2, None), "*": (1, MAX_ARGS, None),
    "/": (2, 2, None), "%": (2, 2, None),
    "floor": (1, 1, None), "ceil": (1, 1, None), "round": (1, 2, None),
    "abs": (1, 1, None), "min": (1, MAX_ARGS, None), "max": (1, MAX_ARGS, None),
    # comparison
    "==": (2, 2, None), "!=": (2, 2, None), "<": (2, 2, None), "<=": (2, 2, None),
    ">": (2, 2, None), ">=": (2, 2, None),
    # logic + conditional (lazy)
    "and": (1, MAX_ARGS, None), "or": (1, MAX_ARGS, None), "not": (1, 1, None),
    "if": (2, 3, None),
    # strings
    "concat": (0, MAX_ARGS, None), "lower": (1, 1, None), "upper": (1, 1, None),
    "trim": (1, 1, None), "len": (1, 1, None), "slice": (2, 3, None),
    "starts_with": (2, 2, None), "ends_with": (2, 2, None), "contains": (2, 2, None),
    "replace": (3, 3, None), "split": (2, 2, None), "join": (2, 2, None),
    "repeat": (2, 2, None), "format": (2, 2, None), "str": (1, 1, None),
    "plural": (2, 2, None),
    # numbers
    "int": (1, 1, None), "num": (1, 1, None),
    # lists
    "list": (0, MAX_ARGS, None), "get": (2, 3, None), "compact": (1, 1, None),
    "reverse": (1, 1, None), "sort": (1, 1, None),
    # maps
    "has": (1, 2, None), "keys": (1, 1, None),
    # facts — each present only when its capability is declared
    "clock.ms": (0, 0, "clock"), "clock.local": (0, 0, "clock"),
    "random.int": (2, 2, "random"), "random.pick": (1, 1, "random"),
    "presence.face_present": (0, 0, "presence"),
    "session.total_volleys": (0, 0, "session"),
    "session.is_empty": (0, 0, "session"),
}

#: The ops whose names are punctuation, derived from `OPS` so the two cannot disagree.
SYMBOLIC_OPS = frozenset(k for k in OPS if not _IDENT.match(k))


def normal_op(raw) -> str:
    """`normal_name`, widened by exactly the symbolic operator names. The NFKC check
    still applies, so a fullwidth `＋` is refused rather than folded into `+`."""
    if not isinstance(raw, str) or not raw:
        return ""
    if unicodedata.normalize("NFKC", raw) != raw:
        return ""
    if raw in SYMBOLIC_OPS:
        return raw
    return raw if _IDENT.match(raw) else ""


#: Ops that decide for themselves whether to evaluate their arguments.
LAZY_OPS = frozenset({"and", "or", "if"})

#: The only op that does not propagate an error argument — it is the *test* for one.
ERROR_TRANSPARENT = frozenset({"has"})

#: `{statement key: capability_or_None}`. Frozen with `OPS` in X1/X4: together they show
#: the grammar has no loop, jump or function definition.
STATEMENTS = {
    "say": "say",
    "markup": "markup",
    "remember": "memory.write",
    "forget": "memory.write",
    "scratch": None,
    "act": None,            # refined to `act.<name>` from the statement itself
    "subscribe": "subscribe",
    "brain": "brain",       # {"brain": {"prompt": expr}} — P1; refused at load in P0
    "handled": "handled",
    "note": None,
}
