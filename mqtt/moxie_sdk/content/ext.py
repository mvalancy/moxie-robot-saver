"""
Sandboxed content extensions — a total, JSON-AST expression language a stranger's
content pack may carry (docs/architecture/backlog/sandboxed-extensions.md, BEYOND #6).

**What this module is.** A declarative rule list over a closed operator table,
interpreted by pure-stdlib Python. There is no `exec`, no `eval`, no parser (the program
*is* JSON), no loop construct, no user-defined function, no recursion, and **no name that
resolves to a host object**. Values are JSON scalars, lists and string-keyed maps; there
is no operator that takes an object, so attribute-walking has nothing to walk to.

**Why that shape.** The whole of the upstream state of the art — OpenMoxie's four
executable content modules, nine hook functions, six `code` strings (MIT,
© Justin Beghtol; read as prior art and cited, never copied; see ATTRIBUTION.md) — uses
no loop, no user function and no recursion. A language with none of those covers 100% of
what anybody has actually written, and it is the only one of the four candidate designs
(brief §3) with structurally zero escape surface *and* a rendering back into English a
parent can read.

**The four things that make it safe**, each pinned by a test in
`sim/tests/test_ext_escapes.py`:

1. *The fact base is plain JSON, built by the host.* `evaluate()` is handed a dict of
   `str/int/float/bool/None/list/dict` and walks that and nothing else (X2).
2. *Every op is total.* Division by zero is the error **value**, a missing key is null, an
   out-of-range index is null. There is no input for which an op raises, so there is no
   state in which the evaluator does not return (§4.6).
3. *Clock and entropy are injected.* This module imports neither `time`, `random`, `os`,
   `datetime`, `secrets` nor `subprocess` — asserted over its own source (X7). Two
   `clock.ms` calls in one program return the same number, and the PRNG is a pure integer
   function of a host-supplied seed, so a turn is replayable.
4. *Capabilities are checked at load, in both directions.* An AST that uses something its
   `capabilities[]` does not declare is refused; a `capabilities[]` entry the AST does not
   use is **also** refused (X10). So the sentence a parent reads is provably equal to what
   the program can do.

**Failure is boring and total.** On any breach — steps, wall clock, a value cap, an error
value reaching an effect — `evaluate()` returns `ExtResult(ok=False, …)` and the effect
list is discarded **whole**. Nothing is half-applied, and the caller
(`content_app.ContentApp`) carries on exactly as it does with no extension at all: a
`global` falls through to the conversation, a `turn.before` is skipped and the model runs.
The child never hears an error string — that is upstream's one bad instinct (brief U6) and
we do not port it.

**`code` is still never executed.** A pack's `code` field remains inert data forever
(`packs.py`, content-module-contract.md). This is a *different* field, `extension`, and
compiling one into the other is explicitly out of scope (brief §7.4): a Python-to-AST
compiler is a parser for a Turing-complete language living in the trusted half of the
system, which is the audit surface this design exists to delete.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# NOTE (X7): the import list above is the security boundary of this module. `time`,
# `random`, `os`, `datetime`, `secrets` and `subprocess` are absent **on purpose** — the
# clock and the PRNG seed are values the host injects into `evaluate()`. A test parses
# this file with `ast` and fails if any of them appears, so the rule cannot rot.

EXT_FORMAT = 1
"""The only `ext_format` this evaluator accepts. A future format bump is a new number,
never a silently-widened grammar."""


# --------------------------------------------------------------------------- #
# The error value
# --------------------------------------------------------------------------- #

class _Error:
    """The distinguished, falsy, propagating error value (§4.6).

    Produced by `/` and `%` by zero, `int("banana")`, `sort` over mixed types, and any op
    whose own argument is already an error. It is **not** an exception: a total language
    has no exceptions to leak, and an author who wants to handle a bad capture group can
    test for it with `{"has": [expr]}` or branch on it with `if` (it is falsy).

    If an error reaches a `say`, `remember`, `act` or `markup` it **fails the extension**
    (§6.4) rather than being spoken — the child must never hear the word "error".
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

#: A capability or op name, after NFKC normalization. Deliberately narrow: lowercase
#: ASCII letters, digits, `_` and `.`. Anything else is refused rather than folded.
_IDENT = re.compile(r"^[a-z0-9_.]+$")

#: A `{"var": …}` path. Dotted; a segment may not begin with `_`, which is what makes
#: `__class__`, `__init__` and `_meta` *invalid programs* rather than blocked ones (X1).
_PATH = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$", re.I)

#: A `remember` / `forget` / `scratch` key. Dot-segmented, no empty segment (so no `..`),
#: no `/` (so no path traversal), and no `_`-leading segment (so a program cannot write
#: `_meta` or `_provenance`, which belong to `MemoryStore`, not to a pack).
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")

#: A `format` spec. Explicit and bounded — never a bare float repr — so output is
#: byte-stable across the Python evaluator and (P1) the JS one. A width beyond five
#: digits is refused at load, so `{"format": ["1000000000d", 1]}` never reaches an op.
_FMT = re.compile(r"^(?P<zero>0?)(?P<width>\d{0,5})(?:\.(?P<prec>\d{1,3}))?(?P<kind>[dfs])$")


def normal_name(raw) -> str:
    """The NFKC-normalized name, or `""` if `raw` is not a plain lowercase ASCII name.

    **Normalization is a check, not a repair** (X8). `"ｍemory.write"` NFKC-folds *to*
    `"memory.write"`, so folding-then-matching would silently grant a capability whose
    written form is not the one the parent's review rendered. So the name must already be
    in normal form **and** match `_IDENT`: a homoglyph, a dotless `ı`, a zero-width space,
    an RTL override and `"MEMORY.WRITE"` are each refused outright.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    if unicodedata.normalize("NFKC", raw) != raw:
        return ""
    return raw if _IDENT.match(raw) else ""


# --------------------------------------------------------------------------- #
# The capability table (§5) — parent-facing words are generated from a fixed table
# --------------------------------------------------------------------------- #

#: `{capability: the sentence a parent reads}`. Never author-supplied text — a pack that
#: could write its own grant sentence could write a reassuring lie. `T13` asserts this
#: table covers every capability the validator will accept, so a new capability cannot
#: ship without parent-facing words.
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

#: `act.<name>` is granted per *name*, not per category: "can set a timer" and "can turn
#: on the camera" are not the same sentence to a parent. The words are built from this
#: table, and an action nobody has written words for cannot be declared at all.
#:
#: **This table is also the closed allowlist of `function_id`s a pack may put on the
#: wire.** `content_app.execution_actions_of` maps a name through these keys and refuses
#: anything else, so the set of nameable robot functions is bounded by the set of things
#: somebody wrote a parent-facing sentence for — one table, not two that can drift. The
#: reasoning is transplanted from docs/architecture/backlog/qr-launch-cards.md §P0-b:
#: *"The catalog is a closed allowlist, and this is a safety property, not tidiness."* A
#: printed card is an input any stranger can leave on a table in front of a child; so is a
#: pack a stranger authored. Either may name one of a few reviewed things, and may name
#: nothing else.
ACTION_WORDS = {
    "eb_timer_request": "Can ask Moxie to set or cancel a timer",
    "eb_enable_qr": "Can turn Moxie's QR scanner on",
    "eb_wake": "Can wake Moxie up",
}

#: The closed event vocabulary a `subscribe` statement may name — the twin of
#: `ACTION_WORDS` on the *inbound* side, and the same argument: an event nobody has
#: recovered from the robot's own catalog cannot be declared, cannot be granted and
#: cannot be put on the wire in an `EventSubscription.active[]` list.
#:
#: **These six strings are a TRANSCRIPTION of `moxie_sdk.presence.VISION_EVENTS`, not an
#: import of it, and that is deliberate.** X7 makes this module's import list a security
#: boundary — `ext.py` imports `math`, `re`, `unicodedata` and nothing else, asserted by
#: parsing its own source — so reaching into `presence` (which imports `os` for its
#: hysteresis knobs) to borrow a tuple would trade a real invariant for a saved line.
#: The duplication is held honest from the other direction instead, by
#: `test_ext_subscribe.py::test_the_subscribable_events_are_exactly_the_recovered_vision_catalog`,
#: which fails the moment the two lists disagree. Two tables plus an equality test is
#: strictly better here than one table plus a widened import boundary.
#:
#: Recovered catalog: docs/architecture/vision.md §1.1-1.2 (`eb-lost-face` is the alias
#: RemoteModuleAPI lists for `eb-lost-target`, which is why both appear).
SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",
                    "eb-qr-event", "eb-dr-event", "eb-br-event")

#: Granted with no parent action at all (§5.1's "granted" column, and acceptance
#: criterion 5). Everything else needs an explicit grant, which at P0 means a caller
#: passing a wider `grants` set — there is deliberately no env var and no console control,
#: because the parent-facing grant flow is P1.
DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname"})

#: Declared, rendered in the review, and **still refused at load**. Not because the
#: grammar cannot express them, but because each is a capability that cannot yet do
#: anything, and shipping one of those would be worse than refusing it out loud.
#:
#: `act.<name>` **left this set on 2026-09-04** and is now honoured: `volley
#: .execution_actions` reaches `RemoteChatAction` through `content_app
#: .execution_actions_of` → `Reply.actions` → `wire.encode_action`, which since #119
#: carries `function_id` / `function_args` (RemoteChat.proto:255-281). Brief S5 — *"the
#: single most important scoping fact in this brief"* — is therefore closed for `act`.
#:
#: `subscribe` **left this set on 2026-09-05** and is now honoured: a `subscribe` effect
#: reaches `volley.subscriptions` (merging, never replacing), `content_app
#: .subscriptions_of` bounds it by `SUBSCRIBE_EVENTS` and turns it into `Reply.subscribe`,
#: and `moxie_runtime._publish_chat` **merges** it into the supervisor's own vision
#: subscription before `wire.build_chat_response(subscribe_events=…)` puts it in
#: `RemoteChatAction.EventSubscription.active[]`. The direction of that merge is the
#: safety property: a pack may add an event it wants to perceive and can never remove one
#: the runtime's presence/greeting behaviour depends on.
#:
#: What is left, and why each is still refused:
#:   * `brain` — needs the one-call-per-turn budget of brief §5.1 before a pack may
#:     spend money and latency inside the 6 s turn.
#:   * `schedule.request` — needs the recommender's parent-request channel (P2).
P1_CAPABILITIES = frozenset({"brain", "schedule.request"})

def _is_p1(cap: str) -> bool:
    """True for a capability this appliance declares, renders and **refuses**. One
    predicate, so the §8 conformance generator has exactly one thing to lift each time a
    capability becomes real.

    `act.<name>` is deliberately *not* here any more: an `act` capability is bounded by
    `ACTION_WORDS`, granted per name, and plumbed to the wire — so the only thing standing
    between a pack and an action is whether the host granted it, which is a decision and
    not a gap.
    """
    return cap in P1_CAPABILITIES


#: Hook points. `turn.after` and `session.end` are P1 — the first needs the output-safety
#: ordering settled, the second overlaps the already-shipped declarative `memory` block.
HOOKS = ("global", "turn.before")

#: The roots `{"var": "…"}` may name, and the capability each one costs. `None` = free.
#: This mapping plus `OPS` is the **complete** set of strings that resolve to anything at
#: all (§5.2's invariant) — both are finite and enumerated here, in our own source.
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
# Limits (§6.2). Defaults live here; `mqtt/config.py` reads the env and overrides.
# --------------------------------------------------------------------------- #

MAX_DEPTH = 32                 # expression nesting; the evaluator is depth-counted
MAX_NODES_PER_EXPR = 512
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


# --------------------------------------------------------------------------- #
# The operator table — closed, total, and the audit surface
# --------------------------------------------------------------------------- #

def _size(v) -> int:
    """A value's cost against the byte caps. Cheap, approximate, and monotone."""
    if isinstance(v, str):
        return len(v)
    if isinstance(v, (list, tuple)):
        return 8 + sum(_size(x) for x in v)
    if isinstance(v, dict):
        return 8 + sum(len(str(k)) + _size(x) for k, x in v.items())
    return 8


def _num(v):
    """A number, or ERROR. `bool` is deliberately **not** a number here: `true + 1` is a
    type confusion in every language that allows it, and this one has no need of it."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ERROR
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ERROR
    return v


def _text(v) -> str:
    """`str` semantics — one fixed rule, host-independent (§6.1).

    No language's default float repr ever reaches output: a float renders through six
    decimal places with the trailing zeros trimmed, which is a rule a JS port can follow
    exactly. Booleans render as `true`/`false` (JSON's spelling, not Python's) and null
    renders as the empty string.
    """
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-") else "0"
    if isinstance(v, str):
        return v
    return ""


def _scalar_key(v):
    """A total ordering key for `sort`, or None when the value is not a sortable scalar."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return (0, float(v), "")
    if isinstance(v, str):
        return (1, 0.0, v)
    return None


def _get(container, key, default=None):
    """`get` — total over lists, maps and strings. Anything unreachable is the default."""
    if isinstance(container, dict):
        if isinstance(key, str):
            return container.get(key, default)
        return default
    if isinstance(container, (list, str)):
        if isinstance(key, bool) or not isinstance(key, int):
            return default
        if 0 <= key < len(container):
            return container[key]
        return default
    return default


def _slice(v, start, end=None):
    lo = start if isinstance(start, int) and not isinstance(start, bool) else 0
    if end is None:
        hi = len(v)
    elif isinstance(end, int) and not isinstance(end, bool):
        hi = end
    else:
        return ERROR
    return v[lo:hi]


def _format(spec, value):
    m = _FMT.match(spec) if isinstance(spec, str) else None
    if m is None:
        return ERROR
    kind = m.group("kind")
    width = int(m.group("width") or 0)
    prec = int(m.group("prec")) if m.group("prec") else None
    if kind == "s":
        s = _text(value)
    else:
        n = _num(value)
        if is_error(n):
            return ERROR
        if kind == "d":
            s = str(int(n))
        else:
            s = f"{float(n):.{prec if prec is not None else 2}f}"
    if len(s) >= width:
        return s
    pad = "0" if m.group("zero") else " "
    if pad == "0" and s.startswith("-"):
        return "-" + s[1:].rjust(width - 1, "0")
    return s.rjust(width, pad)


def _plural(word, count):
    n = _num(count)
    if is_error(n):
        return ERROR
    w = _text(word)
    return w if n == 1 else (w + "s")


class _Prng:
    """A pure-integer PRNG (mulberry32) over a host-supplied 32-bit seed.

    Not for secrecy — for **determinism** (§6.1). An extension with real entropy cannot be
    replayed, and replay is how the conformance goldens work. The algorithm is integer-only
    on purpose: the P1 JavaScript evaluator must reproduce the same stream bit for bit, and
    it cannot if either side reaches for its own `random`.
    """
    __slots__ = ("_s",)

    def __init__(self, seed: int):
        self._s = int(seed) & 0xFFFFFFFF

    def next_u32(self) -> int:
        self._s = (self._s + 0x6D2B79F5) & 0xFFFFFFFF
        t = self._s
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF))) & 0xFFFFFFFF
        return (t ^ (t >> 14)) & 0xFFFFFFFF

    def below(self, n: int) -> int:
        return self.next_u32() % n if n > 0 else 0


#: `{op: (min_args, max_args, capability_or_None)}`. **This table is closed**, it is
#: frozen as a literal in `test_ext_escapes.py::X1`, and adding to it therefore requires a
#: test edit and a reviewer (risk R1). Deliberately absent, and not addable without
#: re-opening the brief: any name-to-object resolution, attribute or index access on a
#: non-JSON value, regex construction, string multiplication (`repeat` is bounded
#: instead), `eval` of any kind, and anything that returns a host handle.
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

#: The eleven ops whose names are punctuation rather than words. Enumerated from `OPS`
#: rather than written out, so the two can never disagree.
SYMBOLIC_OPS = frozenset(k for k in OPS if not _IDENT.match(k))


def normal_op(raw) -> str:
    """`normal_name`, widened by exactly the symbolic operator names.

    The NFKC-equality half of the check still applies, so a fullwidth `＋` (which folds
    *to* `+`) and a mathematical `∗` are refused rather than folded into the real op — the
    same reasoning as `normal_name`, and the reason this is not simply a membership test
    against `OPS`.
    """
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

#: `{statement key: (capability_or_None)}`. Frozen as a literal in X1 and X4 alongside
#: `OPS`: together they are the proof that no loop, no jump and no function definition
#: exists in the grammar to begin with.
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


# --------------------------------------------------------------------------- #
# Validation — everything that can be decided without running anything
# --------------------------------------------------------------------------- #

class _Validator:
    def __init__(self):
        self.reasons: list[str] = []
        self.used: set[str] = set()
        self.nodes = 0
        #: `let` names visible at this point in the rule. Ordered: a binding sees the
        #: ones before it, and `when`/`do` see them all (§4.3).
        self.binds: set[str] = set()

    def fail(self, msg: str) -> None:
        if len(self.reasons) < 12:
            self.reasons.append(msg)

    # ---- expressions ----
    def expr(self, node, where: str, depth: int = 1) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            return self.fail(f"the whole extension is more than {MAX_NODES} nodes")
        if depth > MAX_DEPTH:
            return self.fail(f"{where}: nested deeper than {MAX_DEPTH}")
        if node is None or isinstance(node, (bool, int, float, str)):
            return                                    # a literal is itself
        if not isinstance(node, dict):
            return self.fail(f"{where}: a list is not an expression "
                             f"(use {{\"list\": [...]}} or {{\"lit\": [...]}})")
        if len(node) != 1:
            return self.fail(f"{where}: an expression is exactly one key, "
                             f"got {len(node)}")
        key = next(iter(node))
        arg = node[key]
        if key == "lit":
            return self._lit(arg, where)
        if key == "var":
            return self._var(arg, where)
        name = normal_op(key)
        if not name or name not in OPS:
            return self.fail(f"{where}: unknown operator {key!r}")
        lo, hi, cap = OPS[name]
        if cap:
            self.used.add(cap)
        if not isinstance(arg, list):
            return self.fail(f"{where}: {name} takes a list of arguments")
        if not (lo <= len(arg) <= hi):
            return self.fail(f"{where}: {name} takes {lo}..{hi} arguments, "
                             f"got {len(arg)}")
        for i, sub in enumerate(arg):
            self.expr(sub, f"{where}.{name}[{i}]", depth + 1)

    def _lit(self, arg, where: str) -> None:
        """A literal is data, and must be *only* data — no nested op is evaluated inside
        one, which is what stops `lit` becoming a second, unchecked grammar."""
        stack = [(arg, 1)]
        while stack:
            v, d = stack.pop()
            self.nodes += 1
            if self.nodes > MAX_NODES:
                return self.fail(f"the whole extension is more than {MAX_NODES} nodes")
            if d > MAX_DEPTH:
                return self.fail(f"{where}: literal nested deeper than {MAX_DEPTH}")
            if isinstance(v, dict):
                for k, sub in v.items():
                    if not isinstance(k, str):
                        return self.fail(f"{where}: a literal map needs string keys")
                    stack.append((sub, d + 1))
            elif isinstance(v, list):
                for sub in v:
                    stack.append((sub, d + 1))
            elif not (v is None or isinstance(v, (bool, int, float, str))):
                return self.fail(f"{where}: a literal must be plain JSON")

    def _var(self, arg, where: str) -> None:
        if not isinstance(arg, str) or not _PATH.match(arg):
            return self.fail(f"{where}: {arg!r} is not a fact path")
        for seg in arg.split("."):
            if seg.startswith("_"):
                # Not "blocked at runtime" — an *invalid program*. Combined with a fact
                # base of plain JSON (X2) it is also pointless, which is the belt and the
                # braces of §4.4.
                return self.fail(f"{where}: a path segment may not begin with '_' "
                                 f"({arg!r})")
        root = arg.split(".")[0]
        if root in self.binds:
            return                                  # a `let` value, not a fact
        if root not in FACT_ROOTS:
            return self.fail(f"{where}: {root!r} is not a fact "
                             f"(known: {', '.join(sorted(FACT_ROOTS))})")
        cap = _path_capability(arg)
        if cap:
            self.used.add(cap)

    # ---- statements ----
    def stmt(self, s, where: str) -> None:
        self.nodes += 1
        if not isinstance(s, dict) or not s:
            return self.fail(f"{where}: a statement is an object")
        keys = set(s)
        if keys == {"say", "markup"}:
            head = "say"
        elif len(keys) == 1:
            head = next(iter(keys))
        else:
            return self.fail(f"{where}: a statement is one of {sorted(STATEMENTS)}, "
                             f"got {sorted(keys)}")
        name = normal_name(head)
        if not name or name not in STATEMENTS:
            return self.fail(f"{where}: unknown statement {head!r}")
        cap = STATEMENTS[name]
        if cap:
            self.used.add(cap)
        getattr(self, f"_st_{name.replace('.', '_')}")(s, where)

    def _st_say(self, s, where):
        self.expr(s["say"], f"{where}.say")
        if "markup" in s:
            self.used.add("markup")
            self.expr(s["markup"], f"{where}.markup")

    def _st_markup(self, s, where):
        self.expr(s["markup"], f"{where}.markup")

    def _st_note(self, s, where):
        self.expr(s["note"], f"{where}.note")

    def _st_handled(self, s, where):
        if not isinstance(s["handled"], bool):
            self.fail(f"{where}.handled: expected true or false")

    def _key_value(self, body, where, *, value: bool):
        if not isinstance(body, dict):
            return self.fail(f"{where}: expected an object with a key")
        allowed = {"key", "value"} if value else {"key"}
        if set(body) != allowed:
            return self.fail(f"{where}: expected exactly {sorted(allowed)}")
        k = body.get("key")
        if not isinstance(k, str) or not _KEY.match(k):
            return self.fail(f"{where}.key: {k!r} is not a memory key "
                             f"(letters, digits, '_', '-', dot-separated)")
        if value:
            self.expr(body["value"], f"{where}.value")

    def _st_remember(self, s, where):
        self._key_value(s["remember"], f"{where}.remember", value=True)

    def _st_forget(self, s, where):
        self._key_value(s["forget"], f"{where}.forget", value=False)

    def _st_scratch(self, s, where):
        self._key_value(s["scratch"], f"{where}.scratch", value=True)

    def _st_act(self, s, where):
        body = s["act"]
        if not isinstance(body, dict) or set(body) - {"name", "args"} or "name" not in body:
            return self.fail(f"{where}.act: expected {{name, args}}")
        name = normal_name(body.get("name"))
        if not name or name not in ACTION_WORDS:
            return self.fail(f"{where}.act.name: {body.get('name')!r} is not an "
                             f"action this appliance knows "
                             f"({', '.join(sorted(ACTION_WORDS))})")
        self.used.add(f"act.{name}")
        args = body.get("args", [])
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            return self.fail(f"{where}.act.args: expected a list of at most {MAX_ARGS}")
        for i, a in enumerate(args):
            self.expr(a, f"{where}.act.args[{i}]")

    def _st_brain(self, s, where):
        body = s["brain"]
        if not isinstance(body, dict) or set(body) != {"prompt"}:
            return self.fail(f"{where}.brain: expected {{prompt}}")
        self.expr(body["prompt"], f"{where}.brain.prompt")

    def _st_subscribe(self, s, where):
        """`{"subscribe": [event, …]}` — bounded by the closed vocabulary, at load.

        The `act` shape exactly (`_st_act` above): the name is checked against a table in
        this file rather than at runtime, so a pack that names an event the robot's
        recovered catalog does not have is **not a program** and never installs. The
        second, host-side check on the same table lives in
        `content_app.subscriptions_of`, because that is the last function before a string
        becomes an `EventSubscription.active[]` entry addressed to a robot — and because a
        Python global handler calling `volley.update_subscriptions` never met this
        validator at all.

        Events are compared **literally**, not through `normal_name`: these are wire
        strings with hyphens in them (`eb-found-face`), not identifiers, so the identifier
        grammar would reject every legal one. The homoglyph argument X8 makes for
        capability names is answered here by the membership test itself — a
        confusable-looking `eb‑qr‑event` (U+2011 hyphens) is simply not in the tuple.
        """
        events = s["subscribe"]
        if not isinstance(events, list) or not events or len(events) > MAX_SUBSCRIPTIONS:
            return self.fail(f"{where}.subscribe: expected 1..{MAX_SUBSCRIPTIONS} events")
        for e in events:
            if not isinstance(e, str) or e not in SUBSCRIBE_EVENTS:
                return self.fail(f"{where}.subscribe: {e!r} is not an event this "
                                 f"appliance can ask the robot for "
                                 f"({', '.join(SUBSCRIBE_EVENTS)})")


def validate(ext, *, grants=None, allow_p1: bool = False) -> list:
    """Every reason this extension cannot be installed, as sentences. Empty ⇒ installable.

    Run at **import** (`packs.validate_item`) and again at **load**
    (`ContentApp.reload_content`), so an extension written straight into the store — or one
    that would fail under a *newer* validator — simply stops loading rather than running
    under old rules (T17).

    `allow_p1` checks the **grammar only**, skipping the refusal of the capabilities that
    still have no host (`brain`, `schedule.request`): it is how §8's not-yet-grantable
    conformance ASTs are proven valid today rather than written the day their host lands.
    `act` left that set on 2026-09-04 and `subscribe` on 2026-09-05, so `allow_p1` no
    longer changes the verdict for either. Never pass it from a code path that then
    evaluates.

    `grants` is the host's granted capability set. When given, a declared capability the
    host does not grant is a load refusal too: "absent, not refused, when not granted"
    (§4.2) means the program never runs, so the turn is never at risk.
    """
    v = _Validator()
    if not isinstance(ext, dict):
        return ["extension: expected an object"]
    if not ext:
        return []                                   # `{}` = no extension at all
    unknown = sorted(set(ext) - {"ext_format", "capabilities", "on", "rules"})
    if unknown:
        v.fail(f"extension: unknown key(s) {', '.join(unknown)}")
    if ext.get("ext_format") != EXT_FORMAT:
        v.fail(f"extension: ext_format must be {EXT_FORMAT}, "
               f"got {ext.get('ext_format')!r}")
    on = ext.get("on")
    if on not in HOOKS:
        v.fail(f"extension: `on` must be one of {', '.join(HOOKS)}, got {on!r}")

    declared_raw = ext.get("capabilities")
    declared: set[str] = set()
    if not isinstance(declared_raw, list) or len(declared_raw) > MAX_CAPABILITIES:
        v.fail(f"extension: `capabilities` must be a list of at most "
               f"{MAX_CAPABILITIES} names")
        declared_raw = []
    for raw in declared_raw:
        name = normal_name(raw)
        if not name:
            v.fail(f"capability {raw!r} is not a capability name")
            continue
        if name.startswith("act."):
            if name[4:] not in ACTION_WORDS:
                v.fail(f"capability {name!r} names an action this appliance does not know")
                continue
        elif name not in CAPABILITY_WORDS:
            v.fail(f"capability {name!r} is not one this appliance has")
            continue
        if name in declared:
            v.fail(f"capability {name!r} is declared twice")
        declared.add(name)

    rules = ext.get("rules")
    if not isinstance(rules, list) or not rules:
        v.fail("extension: `rules` must be a non-empty list")
        rules = []
    if len(rules) > MAX_RULES:
        v.fail(f"extension: {len(rules)} rules (the limit is {MAX_RULES})")
        rules = rules[:MAX_RULES]
    for ri, rule in enumerate(rules):
        where = f"rules[{ri}]"
        if not isinstance(rule, dict):
            v.fail(f"{where}: expected an object")
            continue
        extra = sorted(set(rule) - {"when", "let", "do"})
        if extra:
            v.fail(f"{where}: unknown key(s) {', '.join(extra)}")
        v.binds = set()
        binds = rule.get("let")
        if binds is not None:
            if not isinstance(binds, dict):
                v.fail(f"{where}.let: expected an object of name → expression")
            else:
                for bn, bexpr in binds.items():
                    if not isinstance(bn, str) or not _PATH.match(bn) or "." in bn:
                        v.fail(f"{where}.let: {bn!r} is not a binding name")
                        continue
                    if bn in FACT_ROOTS:
                        v.fail(f"{where}.let: {bn!r} is a fact and cannot be rebound")
                    v.expr(bexpr, f"{where}.let.{bn}")
                    v.binds.add(bn)
        if "when" in rule:
            v.expr(rule["when"], f"{where}.when")
        do = rule.get("do")
        if not isinstance(do, list) or not do:
            v.fail(f"{where}.do: expected a non-empty list of statements")
            continue
        if len(do) > MAX_STATEMENTS_PER_RULE:
            v.fail(f"{where}.do: {len(do)} statements "
                   f"(the limit is {MAX_STATEMENTS_PER_RULE})")
            continue
        for si, s in enumerate(do):
            v.stmt(s, f"{where}.do[{si}]")

    if v.reasons:
        return v.reasons

    # The two-directional capability rule (§5, X10). Equality — not containment — is what
    # makes the parent's grant list provably equal to what the program can do.
    missing = sorted(v.used - declared)
    if missing:
        v.fail("uses things it did not declare: " + ", ".join(missing))
    spare = sorted(declared - v.used)
    if spare:
        v.fail("declares things it never uses: " + ", ".join(spare))

    p1 = sorted(c for c in declared if _is_p1(c))
    if p1 and not allow_p1:
        v.fail("needs something this appliance cannot grant yet: " + ", ".join(p1)
               + " (see `P1_CAPABILITIES` for what each one is still waiting on)")

    if grants is not None:
        ungranted = sorted(declared - set(grants))
        if ungranted:
            v.fail("has not been granted: " + ", ".join(ungranted))
    return v.reasons


def capabilities_of(ext) -> list:
    """The declared capability names, normalized and sorted. `[]` for a non-extension."""
    if not isinstance(ext, dict):
        return []
    out = set()
    for raw in ext.get("capabilities") or []:
        name = normal_name(raw)
        if name:
            out.add(name)
    return sorted(out)


def grant_list(ext) -> list:
    """One plain sentence per capability, from the fixed table (§5.4).

    Generated from the **normalized** name, so a homoglyph cannot make a scary grant read
    as a harmless one — and a name that does not normalize was already refused at load,
    so it can never reach this function with a pack installed.
    """
    out = []
    for name in capabilities_of(ext):
        if name.startswith("act."):
            words = ACTION_WORDS.get(name[4:])
        else:
            words = CAPABILITY_WORDS.get(name)
        out.append(words or f"Can do something this appliance does not have words for "
                            f"({name})")
    return out


# --------------------------------------------------------------------------- #
# explain() — the AST as English, which matters as much as evaluate()
# --------------------------------------------------------------------------- #

#: Deliberately avoids every bare capability identifier (`say`, `clock`, `random`,
#: `markup`, `session`, `presence`, `handled`, `brain`, `subscribe`) so a rendered
#: sentence can never be mistaken for, or grepped as, a permission (T13).
_FACT_WORDS = {
    "speech": "what your child said",
    "entities": "part of what your child said",
    "input_vars": "something the robot sent",
    "scratch": "a note from earlier this turn",
    "child": "your child's details",
    "memory": "something it remembered",
    "session": "how far into the chat you are",
    "presence": "whether somebody is in front of Moxie",
}

_OP_WORDS = {
    "==": "is", "!=": "is not", "<": "is less than", ">": "is more than",
    "<=": "is at most", ">=": "is at least",
    "and": "and", "or": "or", "not": "it is not the case that",
    "starts_with": "starts with", "ends_with": "ends with", "contains": "contains",
    "clock.ms": "the current time", "clock.local": "today's date and time",
    "session.is_empty": "the chat has just started",
    "session.total_volleys": "how many turns you have had",
    "presence.face_present": "somebody is in front of Moxie",
    "random.pick": "one of them, picked unpredictably",
    "random.int": "a number picked unpredictably",
}


def _plain(text: str) -> str:
    """Author text, made safe to drop into a sentence: braces and quotes out, one line,
    bounded. An `explain()` line is read by a parent, so it must never come back looking
    like JSON however hostile the string in the pack was (T13)."""
    out = "".join(" " if c in "{}\"\n\r\t" else c for c in str(text))
    out = " ".join(out.split())
    return out[:80] + ("…" if len(out) > 80 else "")


#: Ops that shape a value without changing what a parent would call it, so a sentence
#: reads better describing the thing inside than the wrapper around it.
_TRANSPARENT_OPS = ("lower", "upper", "trim", "str", "int", "num", "abs", "floor",
                    "ceil", "round")


def _describe(node, depth: int = 0, binds=None) -> str:
    """One expression as a short English phrase. Never emits JSON (T13).

    `binds` are the rule's `let` names, so `{"var": "line"}` reads as the *sentence it was
    bound to* rather than as "something it can read" — an author's intermediate name is
    bookkeeping, and a parent should not have to follow it."""
    binds = binds or {}
    if depth > 4:
        return "a value it works out"
    if node is None:
        return "nothing"
    if node is True:
        return "yes"
    if node is False:
        return "no"
    if isinstance(node, (int, float)):
        return _text(node)
    if isinstance(node, str):
        return f"'{_plain(node)}'" if node.strip() else "an empty phrase"
    if not isinstance(node, dict) or len(node) != 1:
        return "a value it works out"
    key = next(iter(node))
    arg = node[key]
    if key == "lit":
        return ("a fixed list of options" if isinstance(arg, (list, dict))
                else _describe(arg, depth, binds))
    if key == "var":
        root = str(arg).split(".")[0]
        if root in binds and "." not in str(arg):
            return _describe(binds[root], depth + 1)          # a `let`, not a fact
        base = _FACT_WORDS.get(root, "something it can read")
        rest = str(arg).partition(".")[2]
        return f"{base} ({rest})" if rest and root in ("memory", "input_vars",
                                                       "entities", "child") else base
    if key in _TRANSPARENT_OPS and isinstance(arg, list) and arg:
        return _describe(arg[0], depth, binds)
    if key in _OP_WORDS and isinstance(arg, list):
        words = _OP_WORDS[key]
        if len(arg) == 0:
            return words
        if len(arg) == 1:
            return f"{words} {_describe(arg[0], depth + 1, binds)}"
        if len(arg) == 2 and key in ("==", "!=", "<", ">", "<=", ">=",
                                     "starts_with", "ends_with", "contains"):
            return (f"{_describe(arg[0], depth + 1, binds)} {words} "
                    f"{_describe(arg[1], depth + 1, binds)}")
        joined = f" {words} ".join(_describe(a, depth + 1, binds) for a in arg)
        return joined
    if key == "if" and isinstance(arg, list) and len(arg) >= 2:
        return (f"{_describe(arg[1], depth + 1, binds)} when "
                f"{_describe(arg[0], depth + 1, binds)}"
                + (f", otherwise {_describe(arg[2], depth + 1, binds)}" if len(arg) > 2 else ""))
    if key == "concat" and isinstance(arg, list):
        # The gist, not the recipe: a parent wants "'Starting timer for …'", not a
        # transcription of six arguments two of which are a space and a colon.
        lits = [a.strip() for a in arg if isinstance(a, str) and re.search("[A-Za-z]", a)]
        if lits:
            gist = " … ".join(_plain(x) for x in lits[:3])
            return f"'{gist} …'" if len(lits) < len(arg) else f"'{gist}'"
    return "a value it works out"


def _describe_stmt(s, binds=None) -> str:
    keys = set(s)
    if "say" in keys:
        return f"tells your child {_describe(s['say'], 0, binds)}"
    if "markup" in keys:
        return "makes Moxie move or play a sound"
    if "remember" in keys:
        return f"remembers {_plain(s['remember'].get('key'))}"
    if "forget" in keys:
        return f"forgets {_plain(s['forget'].get('key'))}"
    if "scratch" in keys:
        return f"keeps {_plain(s['scratch'].get('key'))} for the rest of this turn"
    if "act" in keys:
        return f"asks Moxie to {_plain(s['act'].get('name', '')).replace('_', ' ')}"
    if "subscribe" in keys:
        return "starts listening for something the robot notices"
    if "brain" in keys:
        return "asks the AI a question of its own"
    if "handled" in keys:
        return ("answers without asking the AI" if s["handled"]
                else "lets the AI answer as usual")
    if "note" in keys:
        return "writes one line to this appliance's log"
    return "does something"


def explain(ext) -> list:
    """One English sentence per rule, from the AST (§5.4).

    The same idiom as the 📅 card's *"why this activity today"* line: a parent reads
    sentences, and the JSON stays behind a disclosure for the one parent in a hundred who
    wants it. A grant list tells a parent what a pack *may* do; these tell them what it
    *will* do — which is why both appear in the pack review.
    """
    if not isinstance(ext, dict) or not ext.get("rules"):
        return []
    out = []
    for rule in ext["rules"]:
        if not isinstance(rule, dict):
            continue
        do = [s for s in (rule.get("do") or []) if isinstance(s, dict)]
        binds = rule.get("let") if isinstance(rule.get("let"), dict) else {}
        acts = [_describe_stmt(s, binds) for s in do]
        if not acts:
            continue
        if len(acts) == 1:
            body = acts[0]
        else:
            body = ", ".join(acts[:-1]) + " and " + acts[-1]
        if "when" in rule:
            head = f"When {_describe(rule['when'], 0, binds)}"
        else:
            head = "Whenever this activity is triggered"
        out.append(f"{head}: {body}.")
    return out


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #

class _Breach(Exception):
    """Internal only. Caught by `evaluate`, which always returns an `ExtResult`.

    A budget breach has to unwind a walk that is genuinely recursive over the AST, and an
    exception is the honest way to do that. It never escapes this module: `evaluate`'s
    `except` is total, and the effect list is discarded whole on the way out (§4.5).
    """

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


class _Machine:
    def __init__(self, facts, limits, now_ms, clock_local, seed, monotonic):
        self.facts = facts
        self.limits = limits
        self.now_ms = int(now_ms)
        self.clock_local = _json_copy(clock_local or {})
        self.rng = _Prng(seed)
        self.monotonic = monotonic
        self.deadline = (monotonic() + limits.budget_s) if monotonic else None
        self.steps = 0
        self.total = 0
        self.binds: dict = {}

    # ---- budgets ----
    def step(self, n: int = 1) -> None:
        self.steps += n
        if self.steps > self.limits.max_steps:
            raise _Breach("steps", f"more than {self.limits.max_steps} steps")
        # Every 256 steps, against an **injected** monotonic clock: no threads and no
        # signals, so this behaves identically in the supervisor's handler thread and in a
        # Cloudflare Worker isolate (§6.2).
        if self.deadline is not None and (self.steps & 0xFF) == 0:
            if self.monotonic() > self.deadline:
                raise _Breach("budget", f"longer than {self.limits.budget_s}s")

    def charge(self, value):
        n = _size(value)
        if n > self.limits.max_value_bytes:
            raise _Breach("value", f"a value of {n} bytes "
                                   f"(the limit is {self.limits.max_value_bytes})")
        self.total += n
        if self.total > self.limits.max_total_bytes:
            raise _Breach("total", f"more than {self.limits.max_total_bytes} bytes in all")
        return value

    # ---- expressions ----
    def eval(self, node, depth: int = 1):
        self.step()
        if depth > MAX_DEPTH:
            # Unreachable for a validated AST (the depth cap is a load refusal), so this
            # is the belt to that brace: the evaluator is depth-counted, and a
            # `RecursionError` therefore cannot escape even from an unvalidated AST (X6).
            raise _Breach("invalid", f"nested deeper than {MAX_DEPTH}")
        if node is None or isinstance(node, (bool, int, float, str)):
            return node
        if not isinstance(node, dict) or len(node) != 1:
            raise _Breach("invalid", "not an expression")
        key = next(iter(node))
        arg = node[key]
        if key == "lit":
            return self.charge(_json_copy(arg))
        if key == "var":
            return self.lookup(arg)
        name = normal_op(key)
        if name not in OPS:
            raise _Breach("invalid", f"unknown operator {key!r}")
        if name in LAZY_OPS:
            return self.charge(self.lazy(name, arg, depth))
        args = [self.eval(a, depth + 1) for a in arg]
        if name not in ERROR_TRANSPARENT and any(is_error(a) for a in args):
            return ERROR
        return self.charge(self.apply(name, args))

    def lookup(self, path: str):
        """Walk the plain-JSON fact base, and nothing else. Missing ⇒ null."""
        if path in self.binds:
            return self.binds[path]
        cur = self.facts
        for seg in str(path).split("."):
            if seg.startswith("_"):
                return None                        # refused at load; null here too
            if isinstance(cur, dict):
                cur = cur.get(seg)
            elif isinstance(cur, list):
                if seg.isdigit() and int(seg) < len(cur):
                    cur = cur[int(seg)]
                else:
                    return None
            else:
                return None                        # no attribute access, ever
            if cur is None:
                return None
        return cur

    def lazy(self, name, arg, depth):
        if name == "and":
            out = True
            for a in arg:
                out = self.eval(a, depth + 1)
                if is_error(out) or not out:
                    return out
            return out
        if name == "or":
            out = False
            for a in arg:
                out = self.eval(a, depth + 1)
                if is_error(out):
                    return out
                if out:
                    return out
            return out
        test = self.eval(arg[0], depth + 1)        # `if` — falsy covers ERROR and null
        if test:
            return self.eval(arg[1], depth + 1)
        return self.eval(arg[2], depth + 1) if len(arg) > 2 else None

    def apply(self, name, a):                      # noqa: C901 - one closed table
        """The op table, applied. Every branch is total: it returns a value for every
        input, including the bad ones."""
        # ---- arithmetic ----
        if name in ("+", "*", "min", "max"):
            nums = [_num(x) for x in a]
            if any(is_error(n) for n in nums):
                return ERROR
            if name == "+":
                return sum(nums)
            if name == "*":
                out = 1
                for n in nums:
                    out *= n
                return out
            return min(nums) if name == "min" else max(nums)
        if name == "-":
            nums = [_num(x) for x in a]
            if any(is_error(n) for n in nums):
                return ERROR
            return -nums[0] if len(nums) == 1 else nums[0] - nums[1]
        if name in ("/", "%"):
            x, y = _num(a[0]), _num(a[1])
            if is_error(x) or is_error(y) or y == 0:
                return ERROR                        # never an exception (§4.6)
            return (x / y) if name == "/" else (x - y * math.floor(x / y))
        if name in ("floor", "ceil", "abs"):
            x = _num(a[0])
            if is_error(x):
                return ERROR
            return {"floor": math.floor, "ceil": math.ceil, "abs": abs}[name](x)
        if name == "round":
            x = _num(a[0])
            if is_error(x):
                return ERROR
            if len(a) == 1:
                return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)
            d = _num(a[1])
            if is_error(d) or not isinstance(d, int) or not (0 <= d <= 8):
                return ERROR
            return round(float(x), d)
        # ---- comparison ----
        if name in ("==", "!="):
            same = _equal(a[0], a[1])
            return same if name == "==" else (not same)
        if name in ("<", "<=", ">", ">="):
            x, y = a[0], a[1]
            if isinstance(x, str) and isinstance(y, str):
                pass
            else:
                x, y = _num(x), _num(y)
                if is_error(x) or is_error(y):
                    return False                    # cross-type is false, never an error
            return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[name]
        if name == "not":
            return not a[0]
        # ---- strings ----
        if name == "concat":
            return "".join(_text(x) for x in a)
        if name == "lower":
            return _text(a[0]).lower()
        if name == "upper":
            return _text(a[0]).upper()
        if name == "trim":
            return _text(a[0]).strip()
        if name == "len":
            v = a[0]
            return len(v) if isinstance(v, (str, list, dict)) else 0
        if name == "slice":
            v = a[0]
            if not isinstance(v, (str, list)):
                return ERROR
            return _slice(v, a[1], a[2] if len(a) > 2 else None)
        if name == "starts_with":
            return _text(a[0]).startswith(_text(a[1]))
        if name == "ends_with":
            return _text(a[0]).endswith(_text(a[1]))
        if name == "contains":
            if isinstance(a[0], list):
                return any(_equal(x, a[1]) for x in a[0])
            if isinstance(a[0], dict):
                return isinstance(a[1], str) and a[1] in a[0]
            return _text(a[1]) in _text(a[0])
        if name == "replace":
            return _text(a[0]).replace(_text(a[1]), _text(a[2]))
        if name == "split":
            sep = _text(a[1])
            return _text(a[0]).split(sep) if sep else list(_text(a[0]))
        if name == "join":
            if not isinstance(a[0], list):
                return ERROR
            return _text(a[1]).join(_text(x) for x in a[0])
        if name == "repeat":
            n = _num(a[1])
            if is_error(n) or not isinstance(n, int) or n < 0:
                return ERROR
            return _text(a[0]) * min(n, MAX_REPEAT)     # bounded, never a multiply
        if name == "format":
            return _format(a[0], a[1])
        if name == "str":
            return _text(a[0])
        if name == "plural":
            return _plural(a[0], a[1])
        # ---- numbers ----
        if name == "int":
            v = a[0]
            if isinstance(v, bool):
                return ERROR
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return ERROR if (math.isnan(v) or math.isinf(v)) else math.floor(v)
            if isinstance(v, str):
                s = v.strip()
                # `int("banana")` is the error value, which is how a capture group that
                # caught junk fails *loudly* instead of quietly becoming 0.
                if re.match(r"^-?\d{1,15}$", s):
                    return int(s)
            return ERROR
        if name == "num":
            v = a[0]
            if isinstance(v, bool):
                return ERROR
            if isinstance(v, (int, float)):
                return _num(v)
            if isinstance(v, str) and re.match(r"^-?\d{1,15}(\.\d{1,6})?$", v.strip()):
                return float(v.strip())
            return ERROR
        # ---- lists and maps ----
        if name == "list":
            return list(a)
        if name == "get":
            return _get(a[0], a[1], a[2] if len(a) > 2 else None)
        if name == "compact":
            if not isinstance(a[0], list):
                return ERROR
            return [x for x in a[0] if x is not None and x != "" and not is_error(x)]
        if name == "reverse":
            v = a[0]
            if isinstance(v, list):
                return list(reversed(v))
            return _text(v)[::-1] if isinstance(v, str) else ERROR
        if name == "sort":
            if not isinstance(a[0], list):
                return ERROR
            keys = [_scalar_key(x) for x in a[0]]
            if any(k is None for k in keys):
                return ERROR                        # a total order over scalars only
            return [x for _, x in sorted(zip(keys, a[0]), key=lambda p: p[0])]
        if name == "has":
            if len(a) == 1:
                return not is_error(a[0]) and a[0] is not None
            if is_error(a[0]) or is_error(a[1]):
                return False
            if isinstance(a[0], dict):
                return isinstance(a[1], str) and a[1] in a[0]
            if isinstance(a[0], list):
                return (isinstance(a[1], int) and not isinstance(a[1], bool)
                        and 0 <= a[1] < len(a[0]))
            return False
        if name == "keys":
            # Sorted, so iteration order is host-independent — a determinism requirement,
            # not a convenience (§6.1).
            return sorted(a[0]) if isinstance(a[0], dict) else []
        # ---- facts ----
        if name == "clock.ms":
            return self.now_ms                      # injected once per turn
        if name == "clock.local":
            return _json_copy(self.clock_local)
        if name == "random.int":
            lo, hi = _num(a[0]), _num(a[1])
            if is_error(lo) or is_error(hi):
                return ERROR
            lo, hi = int(lo), int(hi)
            return lo if hi <= lo else lo + self.rng.below(hi - lo + 1)
        if name == "random.pick":
            v = a[0]
            if not isinstance(v, list) or not v:
                return None
            return _json_copy(v[self.rng.below(len(v))])
        if name == "presence.face_present":
            return bool((self.facts.get("presence") or {}).get("face_present"))
        if name == "session.total_volleys":
            return int((self.facts.get("session") or {}).get("total_volleys") or 0)
        if name == "session.is_empty":
            return bool((self.facts.get("session") or {}).get("is_empty"))
        raise _Breach("invalid", f"unknown operator {name!r}")   # pragma: no cover


def _equal(x, y) -> bool:
    """JSON equality, with `true == 1` deliberately false — a type confusion no author
    needs and every reviewer would misread."""
    if isinstance(x, bool) != isinstance(y, bool):
        return False
    if is_error(x) or is_error(y):
        return False
    return x == y


def _json_copy(v):
    """A structural copy of plain JSON. Never `copy.deepcopy` — that walks objects, and
    the point of this module is that there are none to walk."""
    if isinstance(v, dict):
        return {str(k): _json_copy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_copy(x) for x in v]
    return v


def evaluate(ext, facts, *, grants=None, now_ms: int = 0, clock_local=None,
             seed: int = 0, monotonic=None, limits: Limits | None = None) -> ExtResult:
    """Run one validated extension over one turn's facts. **Always returns.**

    `facts` is a plain-JSON dict the host built (§4.4) — the evaluator never sees a
    `Volley`, a `Session`, a `MemoryStore` or any other live object. `now_ms` and `seed`
    are injected, which is what makes a turn replayable (§6.1). `monotonic` is an injected
    `() -> float`; without one the wall-clock budget is not checked and the step budget
    alone bounds the run.

    Effects are **collected, never applied**: statements append to a list the host applies
    afterwards, so a breach mid-program leaves nothing half-written (§4.5).
    """
    limits = limits or Limits()
    reasons = validate(ext, grants=grants)   # never `allow_p1` — this one runs it
    if reasons:
        return ExtResult(ok=False, reason=reasons[0], breach="invalid")
    m = _Machine(facts if isinstance(facts, dict) else {}, limits, now_ms,
                 clock_local, seed, monotonic)
    effects: list = []
    notes: list = []
    handled = False
    try:
        for rule in ext["rules"]:
            m.binds = {}
            m.step()
            for bname, bexpr in (rule.get("let") or {}).items():
                m.binds[bname] = m.eval(bexpr)
            if "when" in rule:
                test = m.eval(rule["when"])
                if is_error(test) or not test:
                    continue
            for s in rule["do"]:
                m.step()
                eff = _run_stmt(m, s)
                if eff is None:
                    continue
                if eff.get("kind") == "handled":
                    handled = bool(eff["value"])
                elif eff.get("kind") == "note":
                    if len(notes) < MAX_NOTES:
                        notes.append(eff["text"][:MAX_NOTE_CHARS])
                else:
                    effects.append(eff)
            break                                   # first matching rule wins (§4.3)
    except _Breach as b:
        return ExtResult(ok=False, reason=b.reason, breach=b.kind, steps=m.steps)
    except Exception as e:                          # pragma: no cover - belt and braces
        return ExtResult(ok=False, reason=f"{type(e).__name__}", breach="invalid",
                         steps=m.steps)
    over = _over_output_caps(effects)
    if over:
        return ExtResult(ok=False, reason=over, breach="output", steps=m.steps)
    return ExtResult(ok=True, effects=effects, steps=m.steps, notes=notes,
                     handled=handled)


def _run_stmt(m: _Machine, s: dict):
    keys = set(s)
    if "say" in keys:
        text = m.eval(s["say"])
        markup = m.eval(s["markup"]) if "markup" in keys else None
        if is_error(text) or is_error(markup):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "say", "text": _text(text),
                "markup": None if markup is None else _text(markup)}
    if "markup" in keys:
        markup = m.eval(s["markup"])
        if is_error(markup):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "markup", "markup": _text(markup)}
    if "remember" in keys:
        value = m.eval(s["remember"]["value"])
        if is_error(value):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "remember", "key": s["remember"]["key"], "value": value}
    if "forget" in keys:
        return {"kind": "forget", "key": s["forget"]["key"]}
    if "scratch" in keys:
        value = m.eval(s["scratch"]["value"])
        if is_error(value):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "scratch", "key": s["scratch"]["key"], "value": value}
    if "act" in keys:
        args = [m.eval(x) for x in (s["act"].get("args") or [])]
        if any(is_error(x) for x in args):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "act", "name": s["act"]["name"],
                "args": [_text(x) for x in args]}
    if "subscribe" in keys:
        return {"kind": "subscribe", "events": list(s["subscribe"])}
    if "brain" in keys:                             # pragma: no cover - P1
        prompt = m.eval(s["brain"]["prompt"])
        if is_error(prompt):
            raise _Breach("error", "a value it worked out did not come out right")
        return {"kind": "brain", "prompt": _text(prompt)}
    if "handled" in keys:
        return {"kind": "handled", "value": bool(s["handled"])}
    if "note" in keys:
        text = m.eval(s["note"])
        return {"kind": "note", "text": "" if is_error(text) else _text(text)}
    raise _Breach("invalid", "unknown statement")   # pragma: no cover


def _over_output_caps(effects) -> str:
    """§6.3, checked before a single effect is applied — so an over-cap program applies
    *nothing*, rather than the prefix that happened to fit."""
    says = markups = acts = subs = writes = 0
    for e in effects:
        k = e["kind"]
        if k == "say":
            says += 1
            if len(e["text"]) > MAX_SAY_CHARS:
                return f"a spoken line of {len(e['text'])} characters"
            if e.get("markup") and len(e["markup"]) > MAX_MARKUP_CHARS:
                return f"markup of {len(e['markup'])} characters"
        elif k == "markup":
            markups += 1
            if len(e["markup"]) > MAX_MARKUP_CHARS:
                return f"markup of {len(e['markup'])} characters"
        elif k == "act":
            acts += 1
        elif k == "subscribe":
            subs += len(e["events"])
        elif k in ("remember", "forget"):
            writes += 1
    if acts > MAX_ACTIONS:
        return f"{acts} robot actions (the limit is {MAX_ACTIONS})"
    if subs > MAX_SUBSCRIPTIONS:
        return f"{subs} subscriptions (the limit is {MAX_SUBSCRIPTIONS})"
    if writes > MAX_MEMORY_WRITES:
        return f"{writes} memory writes (the limit is {MAX_MEMORY_WRITES})"
    if says + markups > MAX_ACTIONS:
        return f"{says + markups} spoken lines"
    return ""
