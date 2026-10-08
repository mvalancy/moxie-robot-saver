"""`explain()` — the AST as English, which matters as much as `evaluate()`."""

from __future__ import annotations

import re

from .grammar import (is_error, Limits, MAX_DEPTH, MAX_REPEAT, MAX_SAY_CHARS, OPS)
from .machine import (_Breach, _Machine)
from .values import (_text)


#: Avoids every bare capability identifier so a rendered sentence can never be mistaken
#: for (or grepped as) a permission (T13).
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


#: The action tags that do something (`moxie_sdk/actions.py` `KNOWN_TAGS`), restated
#: because this package imports nothing outside itself.
_TAG_NAMES = ("exit", "sleep", "launch", "launch_if_confirmed")

#: One of those tags as `actions.parse_action_tags` takes it out of a line (its `_TAG_RE`,
#: with the name one of `_TAG_NAMES` in any case): `<`, spaces, the name, spaces, then `>`,
#: or `:` and fields up to the next `>`. A parent never reads a whole one in a line Moxie
#: says: it is lifted out of the quote, and the sentence says what it makes happen instead
#: (`_lift_parts` says when one is left in its pieces). Linear on any text, since no two
#: neighbouring repeats can take the same character. (`_TAG_RE`'s lazy fields followed by
#: `\s*>` scan a run of spaces after `<exit:` again for each of its characters when no `>`
#: follows: 0.34 s on 16,000 spaces, four times longer per doubling, measured.)
_LIFTED = re.compile(r"<\s*(?:%s)\s*(?::[^<>]*)?>" % "|".join(
    "".join(f"[{c.upper()}{c.lower()}]" if c.isalpha() else c for c in name)
    for name in sorted(_TAG_NAMES, key=len, reverse=True)))

#: Stands for a piece of a spoken line that is worked out at run time (what the child said,
#: a number, a memory). A private-use character, so it is never part of a tag by itself. It
#: may come out as some text or as nothing (null, a list and a map are said as nothing), so
#: a tag is read both ways (`_tag_effects`).
_HOLE = "\ue000"

#: Where a tag can match: a `<`, then no `<` or `>`, then a `>`. A tag matches one of these
#: exactly when the text before its first `:` is `_NAME`, so `_tag_effects` reads a tag in
#: two steps, each in one pass. (A tag regex that lets a `_HOLE` into the name and the
#: spaces on both sides of it backtracks cubically on a run of them: measured 0.6 s on 400,
#: and 11.2 s for one valid `say`.)
_SEGMENT = re.compile(r"<([^<>]*)>")
_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*")

#: A module id as a robot names one. A launch's module is said bare when it is one, and
#: quoted otherwise, so author text never reads as part of the sentence.
_MODULE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _lift(text) -> str:
    """`text` without the tags `actions.parse_action_tags` takes out of a spoken line."""
    return _LIFTED.sub(" ", str(text))


def _tag_effects(text) -> list:
    """What the action tags in one line make happen, in a parent's words, each paired with
    whether it is certain. A malformed tag does nothing, as in `actions.parse_action_tags`.
    A tag with a worked-out piece (`_HOLE`) in it may or may not form, or come out well
    formed, so it is not certain. Before its first `:` (its name and the spaces around it)
    a piece is read as nothing, the only way the tag forms there; in its fields, as some
    text, so a launch whose module is worked out starts "an activity it works out". A line
    with worked-out pieces is read again with every one of them empty, and what only that
    reading finds is not certain either."""
    text = str(text)
    out = []
    for m in _SEGMENT.finditer(text):
        head, colon, rest = m.group(1).partition(":")
        named = _NAME.fullmatch(head.replace(_HOLE, ""))
        name = named.group(1).lower() if named else ""
        if name not in _TAG_NAMES:
            continue
        fields = [f.strip() for f in rest.split(":")] if colon else []
        while fields and not fields[-1]:
            fields.pop()
        sure = _HOLE not in m.group(0)
        if name in ("exit", "sleep"):
            if not fields or not sure:
                out.append(("the conversation ends" if name == "exit"
                            else "Moxie goes to sleep", sure))
        elif fields and fields[0] and (len(fields) <= 2 or not sure):
            module = _plain(fields[0])
            what = ("an activity it works out" if _HOLE in fields[0]
                    else f"the {module} activity" if _MODULE_ID.fullmatch(module)
                    else f"the '{module}' activity")
            out.append((f"Moxie starts {what}", sure))
    if _HOLE in text:
        out += [(e, False) for e, _ in _tag_effects(text.replace(_HOLE, ""))
                if all(e != seen for seen, _ in out)]
    return out


def _lift_parts(parts) -> list:
    """A `concat`'s arguments with the action tags lifted out of its literal strings, a
    tag split across them (`["<ex", "it>Bye"]`) or with a worked-out part in its fields
    (`["<launch:", {"var": "speech"}, ">"]`) included: the parts are read joined, with a
    `_HOLE` for each one that is not a string. A tag with another part before its first
    `:` (`["<ex", {"var": "x"}, "it>"]`), or whose `>` is another part's, is left in, in
    its pieces: the quote shows only the literal strings, and whether the tag forms depends
    on that part. `_say_effects` names what it does when it forms."""
    text = "".join(p if isinstance(p, str) else _HOLE for p in parts)
    lifted = _LIFTED.sub(lambda m: " " * len(m.group(0)), text)
    out, at = [], 0
    for p in parts:
        size = len(p) if isinstance(p, str) else 1
        out.append(lifted[at:at + size] if isinstance(p, str) else p)
        at += size
    return out


#: What `_plain` makes a space: braces, double quotes and line breaks.
_UNQUOTED = str.maketrans({c: " " for c in "{}\"\n\r\t"})


def _plain(text, lift: bool = False) -> str:
    """Author text made safe for a parent-facing sentence: no braces or quotes, one line,
    ≤ 80 chars — never JSON-looking, however hostile the input (T13). A line Moxie says
    (`lift`) loses its action tags as well: the sentence says what they do instead."""
    out = _lift(text) if lift else str(text)
    out = " ".join(out.translate(_UNQUOTED).split())
    return out[:80] + ("…" if len(out) > 80 else "")


def _picked_lines(value):
    """The fixed lines a `random.pick` chooses among, or None when they are computed."""
    if not isinstance(value, dict) or len(value) != 1 or "random.pick" not in value:
        return None
    arg = value["random.pick"]
    lit = (arg[0].get("lit") if isinstance(arg, list) and len(arg) == 1
           and isinstance(arg[0], dict) else None)
    if isinstance(lit, list) and lit and all(isinstance(x, str) for x in lit):
        return lit
    return None


# --------------------------------------------------------------------------- #
# Reading ahead the lines a `say` can speak, for the tags in them
# --------------------------------------------------------------------------- #

#: At most this many different lines are read out of one `say` (an `if` inside a `concat`
#: multiplies them). A `concat` whose lines would hold more than `_MAX_CHARS` characters in
#: all counts as too many as well, and a `concat` read as one text (`_Reader.joined`) stops
#: at that length. Past either, the `say` is read in its parts (`_Reader.texts_in`). Neither
#: bounds the reading as a whole: `_BUDGET` does.
_MAX_LINES = 256
_MAX_CHARS = 1_000_000

#: What one `explain()` call may build while it reads its rules' lines, counted in
#: characters plus `_KEPT` for each line, text or tag it keeps: every line and every
#: `concat`'s joins, each copy a case op makes, every value it works out, every text read in
#: parts and each `concat` joined, the tags it finds, and every `let` name's memo of these.
#: Past it, the rest is read from the program's own text in one pass (`_text_effects`).
_BUDGET = 4_000_000
_KEPT = 16

#: At most this many different tags are kept as "maybe" (`_Reader.note_tag`), and read from
#: a program's own text (`_text_effects`): past it, every effect a tag can have counts.
_MAX_MAYBE = 64

#: At most this many different activities are named in one sentence; past it, the others
#: read "Moxie starts an activity it works out" (`_capped`).
_MAX_NAMED = 16

#: Every effect a tag can have, in `_tag_effects`'s words.
_EVERY = ("the conversation ends", "Moxie goes to sleep",
          "Moxie starts an activity it works out")

#: Ops whose value is a number, yes/no, or a fact the host builds: no tag written in their
#: arguments can reach the line. Any other op `_Reader.lines` does not follow (`get`,
#: `replace`, `join`, `slice`, …) may pass an argument's text on, so a tag in one of its
#: arguments may or may not be said. `test_leave_taking.py` holds this split to `OPS`.
_NO_TEXT_OPS = frozenset({
    "+", "-", "*", "/", "%", "floor", "ceil", "round", "abs", "min", "max",
    "==", "!=", "<", "<=", ">", ">=", "not", "len", "starts_with", "ends_with",
    "contains", "has", "int", "num", "clock.ms", "clock.local", "random.int",
    "presence.face_present", "session.total_volleys", "session.is_empty"})

#: Ops that cut a piece out of a text, or rewrite one, so that a tag can come out of
#: text that holds none whole: `get` and `slice` take pieces, `split` cuts, `replace` and
#: `reverse` rewrite (`_text_effects`).
_CUTTING_OPS = frozenset({"get", "slice", "split", "replace", "reverse"})

#: Spaces and worked-out parts at an end of a line (`_trim`).
_EDGE = re.compile(r"[\s\ue000]*")


def _trim(text: str) -> str:
    """`trim` over a line read ahead: its spaces go from both ends, and so do the
    worked-out parts there with the spaces between them, since each may come out as
    nothing. Such a run at an end reads as one worked-out part (some text, or nothing), so
    a tag that only forms once it is gone (`"<ex"`, then a trimmed `[nothing, "  it>"]`)
    is read too."""
    out = text.strip()
    if not out.startswith(_HOLE) and not out.endswith(_HOLE):
        return out
    start = _EDGE.match(out).end()
    end = len(out) - _EDGE.match(out[::-1]).end()
    if start >= end:
        return _HOLE
    return (_HOLE if start else "") + out[start:end] + (_HOLE if end < len(out) else "")


#: The string ops `_Reader.lines` applies to the lines it has read (a hole has no case).
_CASE = {"upper": str.upper, "lower": str.lower, "trim": _trim, "str": str}

#: A `<…>` whose name may be one of `_TAG_NAMES` once case ops are applied: after spaces
#: and worked-out parts, it starts with an e, l or s in either case, or a long s (ſ, which
#: `upper` makes S). `_may_act` reads the rest.
_MAYBE_TAG = re.compile(r"<[\s\ue000]*[eEsSlL\u017f][^<>]*>")

#: Every sequence of `upper` and `lower` acts on each character as one of these: either one
#: twice acts as it does once, and `upper, lower, upper` as `lower, upper` (checked over
#: every code point in Python 3.10 and 3.12, and on random strings).
_CASINGS = (str, str.upper, str.lower, lambda s: s.upper().lower(),
            lambda s: s.lower().upper(), lambda s: s.lower().upper().lower())


def _may_act(tag: str) -> bool:
    """True when `tag` (a `<…>`) may do something once case ops are applied: the text before
    its first `:`, without worked-out parts, is one of `_TAG_NAMES` after `upper` then
    `lower` (which make ı, ſ and ﬁ the ASCII letters they stand for)."""
    head = tag[1:-1].partition(":")[0].replace(_HOLE, "")
    named = _NAME.fullmatch(head.upper().lower())
    return bool(named) and named.group(1) in _TAG_NAMES


class _TooMany(Exception):
    """One `say` can speak more lines than `_MAX_LINES` (or `_MAX_CHARS`)."""


class _TooBig(Exception):
    """Reading on would build more than `_BUDGET`."""


def _would_build(name: str, a: list) -> int:
    """How long a value op `name` builds from `a` is at least, worked out without building
    it: exactly for the ops that can build more than they read (`concat`, `repeat`,
    `replace`, `join`, and the pieces `split` makes), and the length read for `upper` and
    `lower`, which never shorten a text; 0 for any other op."""
    if name == "concat":
        return sum(len(_text(x)) for x in a)
    if name == "repeat":
        times = a[1] if isinstance(a[1], int) and not isinstance(a[1], bool) else 0
        return len(_text(a[0])) * min(max(times, 0), MAX_REPEAT)
    if name == "replace":
        text, old, new = _text(a[0]), _text(a[1]), _text(a[2])
        if not old:
            return len(text) + (len(text) + 1) * len(new)
        return len(text) + text.count(old) * (len(new) - len(old))
    if name == "join":
        if not isinstance(a[0], list):
            return 0
        return sum(len(_text(x)) for x in a[0]) + max(len(a[0]) - 1, 0) * len(_text(a[1]))
    if name == "split":
        text, sep = _text(a[0]), _text(a[1])
        return text.count(sep) + 1 if sep else len(text)
    if name in ("upper", "lower"):
        return len(_text(a[0]))
    return 0


class _Probe(_Machine):
    """The evaluator as `_Reader.run` works out a part made of literals: the same ops and the
    same values, with what each op reads and builds counted in `work` (a list's items count
    one each), and a value that is sure to break the value cap refused before it is built.
    The evaluator itself builds a value and only then refuses it, and counts an empty string
    as nothing: a 32 KB program's `replace` or `join` built 256 MB before its refusal,
    measured. What this refuses, `_Reader` reads in its parts instead. Past `allowance` it
    stops (`_TooBig`)."""

    def __init__(self, visible: dict, allowance: int):
        super().__init__({}, Limits(), 0, {}, 0, None)
        self.binds = visible
        self.allowance = allowance
        self.work = 0

    def apply(self, name, a):
        size = _would_build(name, a)
        self.work += size + sum(len(x) for x in a if isinstance(x, (str, list, dict)))
        if self.work > self.allowance:
            raise _TooBig()
        if size > self.limits.max_value_bytes:
            raise _Breach("value", "too big to work out ahead")
        value = super().apply(name, a)
        self.work += len(value) if isinstance(value, (str, list, dict)) else 1
        return value


#: What `_Reader.run` returns when the evaluator stops (a breach): not a value.
_UNRUN = object()

#: Stands in `_Reader.maybe` for any tag at all, once there are more than `_MAX_MAYBE`.
_ANY = "\ue001"


def _strings(value) -> list:
    """Every string inside a JSON value, map keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k)] + _strings(v)]
    return []


class _Reader:
    """Reads ahead every line one `say` can speak, from the program's own text: through
    `if`/`and`/`or` branches, `concat` parts, `let` names, `random.pick` and the case, trim
    and `str` ops. A part made of literals only is worked out by the evaluator itself, so a
    tag assembled from literals (`{"replace": ["<exot>", "o", "i"]}`) is read exactly. Any
    other part is a `_HOLE`, and a tag written inside it lands in `maybe`, through every
    case op it then passes. Everything it builds and keeps counts against one budget
    (`spend`), shared by the rules of one `explain()` call."""

    def __init__(self, binds, budget=None):
        self.binds = dict(binds) if isinstance(binds, dict) else {}
        self.index = {name: i for i, name in enumerate(self.binds)}
        #: What may still be built, in `_BUDGET`'s units.
        self.budget = budget if budget is not None else [_BUDGET]
        #: Tags (each a `<…>`, worked-out parts in it as `_HOLE`) written in what an op that
        #: is not worked out here reads: each may or may not reach the line.
        self.maybe: list = []
        #: The `let` values made of literals only, and those the expression read can see.
        self.known: dict = {}
        self.visible: dict = {}
        #: `(kind, name)` → `(what it reads as, its maybe, why it could not be read)`, one
        #: per `let` name and kind, worked out in binding order (as `evaluate` does), so a
        #: chain of names is never walked twice or deeply.
        self.memo: dict = {}
        #: Each `concat`'s parts joined (`joined`), by the parts list and the `let` names
        #: in sight, so a nest of them is joined once, not again for each one around it.
        self.glued: dict = {}
        #: Each part made of literals, worked out once (`run`), by node and names in sight.
        self.values: dict = {}
        #: The tags in each text, by the text, so a text read through many names is
        #: searched once (`tags_in`).
        self.found: dict = {}
        #: How many `let` names the expression being read can see: a binding sees the
        #: earlier ones only, the `say` sees them all.
        self.sees = 0
        for name, expr in self.binds.items():
            try:
                if self.constant(expr):
                    value = self.run(expr)
                    if value is not _UNRUN:
                        self.known[name] = value
            except _TooBig:
                pass
            for kind in ("lines", "choices", "texts_in", "glue"):
                self.maybe = []
                try:
                    got, failed = getattr(self, kind)(expr), None
                except (_TooMany, _TooBig) as stop:
                    got, failed = None, type(stop)
                self.memo[(kind, name)] = (got, self.maybe, failed)
            if name in self.known:
                self.visible[name] = self.known[name]
            self.sees += 1
        self.maybe = []

    def spend(self, n: int) -> None:
        """Count `n` against the budget, and stop (`_TooBig`) once it is used up."""
        self.budget[0] -= n
        if self.budget[0] < 0:
            raise _TooBig()

    def bound(self, name, kind: str):
        """`let` name `name` read as `kind`: "lines", "choices" (for a `random.pick`),
        "texts_in" or "glue"."""
        if not isinstance(name, str) or self.index.get(name, self.sees) >= self.sees:
            # A fact, or a later binding (null at run time): no text of the program's own.
            return {"texts_in": [], "glue": _HOLE}.get(kind, [_HOLE])
        got, maybe, failed = self.memo[(kind, name)]
        for tag in maybe:
            self.note_tag(tag)
        if failed is not None:
            raise failed()
        return got

    def constant(self, node, depth: int = 0) -> bool:
        """True when `node` is literals only (or `let` names bound to them): the evaluator
        works it out the same on every turn, so it can be worked out now."""
        if node is None or isinstance(node, (bool, int, float, str)):
            return True
        if not isinstance(node, dict) or len(node) != 1 or depth > MAX_DEPTH:
            return False
        key, arg = next(iter(node.items()))
        if key == "lit":
            return True
        if key == "var":
            return isinstance(arg, str) and arg in self.known and self.index[arg] < self.sees
        if key not in OPS or OPS[key][2] is not None or not isinstance(arg, list):
            return False                   # a fact: the clock, a pick, the session, presence
        return all(self.constant(a, depth + 1) for a in arg)

    def run(self, node):
        """`node`'s value, worked out once by the evaluator itself (`_Probe`); `_UNRUN` if it
        stops. What it reads and builds on the way counts against the budget, whether or not
        it comes to a value."""
        key = (id(node), self.sees)
        if key not in self.values:
            if self.budget[0] < 0:
                raise _TooBig()
            probe = _Probe(self.visible, self.budget[0])
            try:
                value = probe.eval(node)
            except _TooBig:
                self.budget[0] = -1
                raise
            except Exception:              # a breach: too big, or not a program
                value = _UNRUN
            self.spend(probe.work + probe.total + _KEPT)
            self.values[key] = value
        return self.values[key]

    def union(self, groups) -> list:
        """The lines in `groups`, each once and in order."""
        out, seen = [], set()
        for group in groups:
            for line in group:
                if line not in seen:
                    seen.add(line)
                    out.append(line)
        if len(out) > _MAX_LINES:
            raise _TooMany()
        self.spend(_KEPT * len(out))
        return out

    def join(self, left, right) -> list:
        """`concat`: each line in `left` followed by each in `right` (an error stays one)."""
        chars = (sum(len(x) for x in left if x) * len(right)
                 + sum(len(x) for x in right if x) * len(left))
        if len(left) * len(right) > _MAX_LINES or chars > _MAX_CHARS:
            raise _TooMany()
        self.spend(chars)
        return self.union([[None if a is None or b is None else a + b for b in right]
                           for a in left])

    def case(self, op: str, texts: list) -> list:
        """`texts` through one case, trim or `str` op (None, an error, stays None)."""
        if op == "str":
            return texts
        size = sum(len(x) for x in texts if x is not None)
        self.spend(size + _KEPT * len(texts))
        out = [x if x is None else _CASE[op](x) for x in texts]
        grown = sum(len(x) for x in out if x is not None) - size
        if grown > 0:
            self.spend(grown)
        return out

    def kept(self, texts: list) -> list:
        """`texts`, each once."""
        self.spend(_KEPT * len(texts))
        return list(dict.fromkeys(texts))

    def lines(self, node, depth: int = 0) -> list:
        """Every line `node` can come out as: a str with `_HOLE` where a piece is worked out
        at run time, or None where the evaluator stops instead (an error is never said)."""
        if self.constant(node, depth):
            value = self.run(node)
            if value is not _UNRUN:
                return [None if is_error(value) else _text(value)]
        if depth > MAX_DEPTH or not isinstance(node, dict) or len(node) != 1:
            return [_HOLE]
        key, arg = next(iter(node.items()))
        if key == "var":
            return self.bound(arg, "lines")
        if not isinstance(arg, list):
            return [_HOLE]
        if key == "if" and len(arg) >= 2:
            return self.union([self.lines(a, depth + 1) for a in self.branches(arg, depth)])
        if key == "or" and arg:
            return self.union([self.lines(a, depth + 1) for a in arg])
        if key == "and" and arg:
            # The last operand's value, or an earlier falsy one: said as "", "0" or "false".
            return self.union(([[_HOLE]] if len(arg) > 1 else [])
                              + [self.lines(arg[-1], depth + 1)])
        if key == "concat":
            out = [""]
            for part in arg:
                out = self.join(out, self.lines(part, depth + 1))
            return out
        if key in _CASE and len(arg) == 1:
            start = len(self.maybe)
            out = self.case(key, self.lines(arg[0], depth + 1))
            if key in ("upper", "lower") and len(self.maybe) > start:
                # A tag that may pass through this op comes out of it changed too.
                passed = self.maybe[start:]
                del self.maybe[start:]
                for tag in self.case(key, passed):
                    self.note_tag(tag)
            return out
        if key == "random.pick" and len(arg) == 1:
            return self.choices(arg[0], depth + 1)
        if key not in _NO_TEXT_OPS:
            for a in arg:
                self.collect(a, depth + 1)
        return [_HOLE]

    def choices(self, node, depth: int = 0) -> list:
        """The lines a `random.pick` over `node` can pick, each item as it is said."""
        if self.constant(node, depth):
            value = self.run(node)
            if value is not _UNRUN:
                if is_error(value):
                    return [None]
                if isinstance(value, list) and value:
                    return self.union([[_text(x)] for x in value])
                return [""]
        if depth <= MAX_DEPTH and isinstance(node, dict) and len(node) == 1:
            key, arg = next(iter(node.items()))
            if key == "var":
                return self.bound(arg, "choices")
            if key == "list" and isinstance(arg, list) and arg:
                return self.union([self.lines(a, depth + 1) for a in arg])
            if key == "if" and isinstance(arg, list) and len(arg) >= 2:
                return self.union([self.choices(a, depth + 1)
                                   for a in self.branches(arg, depth)])
        self.collect(node, depth)
        return [_HOLE]

    def branches(self, arg, depth: int) -> list:
        """The branches of `{"if": arg}` that can be taken: both, or the one a fixed test
        takes (an error value is falsy). A missing `else` is null, said as nothing."""
        branches = arg[1:3] if len(arg) > 2 else [arg[1], None]
        test = self.run(arg[0]) if self.constant(arg[0], depth + 1) else _UNRUN
        if test is _UNRUN:
            return branches
        return [branches[0] if test else branches[1]]

    def collect(self, node, depth: int = 0) -> None:
        """`node` feeds an op whose value is not worked out here: each tag it can come out
        with, or hold in a list or map, may or may not reach the line (`maybe`)."""
        for line in self.lines(node, depth):
            if line is not None:
                self.note(line)
        for text in self.texts_in(node, depth):
            self.note(text)

    def note(self, text: str) -> None:
        """Each tag in `text` may reach the line."""
        for tag in self.tags_in(text):
            self.note_tag(tag)

    def note_tag(self, tag: str) -> None:
        """`tag` may reach the line: kept once, and past `_MAX_MAYBE` tags as `_ANY`."""
        if tag in self.maybe or _ANY in self.maybe:
            return
        self.maybe.append(_ANY if len(self.maybe) >= _MAX_MAYBE else tag)

    def tags_in(self, text: str) -> list:
        """The tags in `text` that may do something once case ops are applied (`_may_act`),
        each once and at most `_MAX_MAYBE` + 1 of them, searched for once per text."""
        key = id(text)
        if key not in self.found or self.found[key][0] is not text:
            tags: dict = {}
            for m in _MAYBE_TAG.finditer(text):
                tag = m.group(0)
                if tag not in tags and _may_act(tag):
                    tags[tag] = None
                    if len(tags) > _MAX_MAYBE:
                        break
            self.spend(sum(len(tag) + _KEPT for tag in tags) + _KEPT)
            self.found[key] = (text, list(tags))
        return self.found[key][1]

    def texts_in(self, node, depth: int = 0) -> list:
        """Each text in `node` a tag can be written in, once: every string, wherever it sits
        (a list, a map), what its literal-only parts work out to, the `let` names it reads,
        each `concat`'s parts joined (`joined`), and all of these through a case op as it
        changes them. Read without multiplying anything out."""
        if self.constant(node, depth):
            value = self.run(node)
            if value is not _UNRUN:
                return self.kept(_strings(value))
        if depth > MAX_DEPTH or not isinstance(node, dict) or len(node) != 1:
            return []
        key, arg = next(iter(node.items()))
        if key == "lit":                   # too big for the evaluator's value cap
            return self.kept(_strings(arg))
        if key == "var":
            return self.bound(arg, "texts_in")
        if not isinstance(arg, list):
            return []
        out = [t for a in arg for t in self.texts_in(a, depth + 1)]
        if key == "concat":
            out.append(self.joined(arg, depth))
        elif key in _CASE and len(arg) == 1:
            out = self.case(key, out)
        return self.kept(out)

    def joined(self, parts, depth: int) -> str:
        """A `concat`'s parts as one text (`glue`), so a tag split across them is read whole
        (as `_lift_parts` lifts it). Past `_MAX_CHARS` the whole is a `_HOLE`: no turn can
        say or pass on a value that long under the evaluator's default caps (`Limits`), and
        `texts_in` still reads each part on its own."""
        seen = (id(parts), self.sees)
        if seen not in self.glued:
            pieces = [self.glue(part, depth + 1) for part in parts]
            size = sum(len(piece) for piece in pieces)
            if size > _MAX_CHARS:
                self.glued[seen] = _HOLE
            else:
                self.spend(size + _KEPT)
                self.glued[seen] = "".join(pieces)
        return self.glued[seen]

    def glue(self, node, depth: int = 0) -> str:
        """`node` as one piece of a joined `concat`: what it works out to when it is built
        only from literals, a `concat` or a case op over one read through, a `let` name as
        what it is bound to, and anything else (a part that can come out as different lines,
        an op it does not follow, a fact) a `_HOLE`."""
        if self.constant(node, depth):
            value = self.run(node)
            return _HOLE if value is _UNRUN or is_error(value) else _text(value)
        if depth > MAX_DEPTH or not isinstance(node, dict) or len(node) != 1:
            return _HOLE
        op, arg = next(iter(node.items()))
        if op == "var":
            return self.bound(arg, "glue")
        if not isinstance(arg, list):
            return _HOLE
        if op == "concat":
            return self.joined(arg, depth)
        if op in _CASE and len(arg) == 1:
            return self.case(op, [self.glue(arg[0], depth + 1)])[0]
        return _HOLE


def _text_effects(value, binds) -> list:
    """What a `say` can make happen, read from the program's own text in one pass, for when
    reading it as `_Reader` does would build more than `_BUDGET`. Each tag written whole in a
    string of the `say` or of its rule's `let` values counts, through every sequence of
    `upper` and `lower` when the rule has either (`_CASINGS`). Every effect a tag can have
    counts when the pieces of one could meet (a `<` with no `>` after it in one string, and
    a `>` with no `<` before it in one), when a `<` or `>` is in what an op that cuts or
    rewrites text reads (`_CUTTING_OPS`), or past `_MAX_MAYBE` different tags."""
    seen = {"open": False, "close": False, "cased": False, "any": False}
    tags: dict = {}

    def walk(node) -> bool:
        """Reads `node`'s strings; True when one holds a `<` or a `>`."""
        if isinstance(node, str):
            first_open, first_close = node.find("<"), node.find(">")
            if node.rfind("<") > node.rfind(">"):
                seen["open"] = True
            if first_close >= 0 and (first_open < 0 or first_close < first_open):
                seen["close"] = True
            for m in _MAYBE_TAG.finditer(node):
                if m.group(0) not in tags and _may_act(m.group(0)):
                    tags[m.group(0)] = None
                    if len(tags) > _MAX_MAYBE:
                        seen["any"] = True
                        break
            return first_open >= 0 or first_close >= 0
        if isinstance(node, list):
            return any([walk(item) for item in node])
        if isinstance(node, dict):
            delimited = False
            for key, item in node.items():
                inner = walk(item)
                delimited |= walk(key) | inner
                if key in ("upper", "lower"):
                    seen["cased"] = True
                if key in _CUTTING_OPS and inner:
                    seen["any"] = True
            return delimited
        return False

    for expr in [value] + (list(binds.values()) if isinstance(binds, dict) else []):
        walk(expr)
    if seen["any"] or (seen["open"] and seen["close"]):
        return list(_EVERY)
    casings = _CASINGS if seen["cased"] else (str,)
    return list(dict.fromkeys(e for tag in tags for case in casings
                              for e, _ in _tag_effects(case(tag))))


def _too_long(line: str) -> bool:
    """True when `line` can never be said: the evaluator refuses a `say` longer than
    `MAX_SAY_CHARS` whole, and the turn goes on as if there were no extension. Spaces (which
    `trim` may take off) and worked-out parts (which may come out as nothing) do not count;
    no case op makes a line shorter."""
    return len("".join(line.split())) - line.count(_HOLE) > MAX_SAY_CHARS


def _capped(effects: list) -> list:
    """`effects` naming at most `_MAX_NAMED` different activities: past them, one
    "sometimes Moxie starts an activity it works out" stands for the rest."""
    out, named = [], 0
    for effect in effects:
        bare = effect[len("sometimes "):] if effect.startswith("sometimes ") else effect
        if bare.startswith("Moxie starts the "):
            named += 1
            if named > _MAX_NAMED:
                continue
        out.append(effect)
    rest = f"sometimes {_EVERY[2]}"
    if named > _MAX_NAMED and rest not in out:
        out.append(rest)
    return out


def _say_effects(value, binds=None, budget=None) -> list:
    """What a `say` makes happen through the tags in the line it speaks, in a parent's
    words, read from every line it can speak (`_Reader`) but one too long to be said at all
    (`_too_long`). An effect that is not certain on every one of those lines happens
    "sometimes", and at most `_MAX_NAMED` activities are named (`_capped`). Past
    `_MAX_LINES`, the `say` is read in
    its parts, every effect "sometimes"; once reading would build more than `budget` (a
    one-item list, `_BUDGET` by default), from the program's own text in one pass
    (`_text_effects`). Not read: a tag that needs text the program reads at run time (what
    the child said, a memory, something the robot sent) for its `<`, name, `:` or `>`,
    which is not its own text; and a tag that needs the text an op `_Reader` does not follow
    hands on (`get`, `slice`, `replace`, `join`, …), whose arguments are read for whole
    tags only. Past `_MAX_LINES`, also not read: a tag that needs the text of a `concat`
    part that can come out as different lines (an `if`, `and`/`or`, `random.pick`, or a
    `let` name bound to one)."""
    reader = _Reader(binds, budget)
    try:
        try:
            lines = reader.lines(value)
        except _TooMany:
            # Too many lines to read one by one: each tag written in it may be said.
            lines = []
            for text in reader.texts_in(value):
                reader.note(text)
    except _TooBig:
        return [f"sometimes {e}" for e in _text_effects(value, binds)]
    read = [[] if line is None or _too_long(line) else _tag_effects(line) for line in lines]
    order = dict.fromkeys(effect for effects in read for effect, _ in effects)
    for tag in reader.maybe:
        order.update(dict.fromkeys(_EVERY if tag == _ANY
                                   else [e for e, _ in _tag_effects(tag)]))
    each = [set(effects) for effects in read]
    return _capped([e if each and all((e, True) in effects for effects in each)
                    else f"sometimes {e}" for e in order])


#: Ops that shape a value without changing what a parent would call it; described by
#: their argument.
_TRANSPARENT_OPS = ("lower", "upper", "trim", "str", "int", "num", "abs", "floor",
                    "ceil", "round")


def _describe(node, depth: int = 0, binds=None, spoken: bool = False) -> str:
    """One expression as a short English phrase; never JSON (T13). `binds` are the rule's
    `let` names, described by what they were bound to. In a line Moxie says (`spoken`) a
    quote loses its action tags, and the sentence says what they do (`_say_effects`);
    anywhere else, such as a test on what the child said, a quote shows them as written."""
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
        if not spoken:
            return f"'{_plain(node)}'" if node.strip() else "an empty phrase"
        # A line that is only a tag says nothing; its tag is read in `_say_effects`.
        return f"'{_plain(node, lift=True)}'" if _lift(node).strip() else "an empty phrase"
    if not isinstance(node, dict) or len(node) != 1:
        return "a value it works out"
    key = next(iter(node))
    arg = node[key]
    picked = _picked_lines(node)
    if picked:
        return (_describe(picked[0], depth, binds, spoken) if len(picked) == 1
                else f"one of {len(picked)} options (picked unpredictably)")
    if key == "lit":
        return ("a fixed list of options" if isinstance(arg, (list, dict))
                else _describe(arg, depth, binds, spoken))
    if key == "var":
        root = str(arg).split(".")[0]
        if root in binds and "." not in str(arg):
            return _describe(binds[root], depth + 1, spoken=spoken)   # a `let`, not a fact
        base = _FACT_WORDS.get(root, "something it can read")
        rest = str(arg).partition(".")[2]
        return f"{base} ({rest})" if rest and root in ("memory", "input_vars",
                                                       "entities", "child") else base
    if key in _TRANSPARENT_OPS and isinstance(arg, list) and arg:
        return _describe(arg[0], depth, binds, spoken and key in _CASE)
    if key in _OP_WORDS and isinstance(arg, list):
        words = _OP_WORDS[key]
        # What `and`, `or` and a pick come out as is one of their operands.
        said = spoken and key in ("and", "or", "random.pick")
        if len(arg) == 0:
            return words
        if len(arg) == 1:
            return f"{words} {_describe(arg[0], depth + 1, binds, said)}"
        if len(arg) == 2 and key in ("==", "!=", "<", ">", "<=", ">=",
                                     "starts_with", "ends_with", "contains"):
            return (f"{_describe(arg[0], depth + 1, binds)} {words} "
                    f"{_describe(arg[1], depth + 1, binds)}")
        joined = f" {words} ".join(_describe(a, depth + 1, binds, said) for a in arg)
        return joined
    if key == "if" and isinstance(arg, list) and len(arg) >= 2:
        return (f"{_describe(arg[1], depth + 1, binds, spoken)} when "
                f"{_describe(arg[0], depth + 1, binds)}"
                + (f", otherwise {_describe(arg[2], depth + 1, binds, spoken)}"
                   if len(arg) > 2 else ""))
    if key == "concat" and isinstance(arg, list):
        # The gist, not the recipe: quote the literal words only.
        words = [a for a in arg if isinstance(a, str) and re.search("[A-Za-z]", a)]
        lits = [a.strip() for a in (_lift_parts(arg) if spoken else arg)
                if isinstance(a, str) and re.search("[A-Za-z]", a)]
        if lits:
            gist = " … ".join(_plain(x) for x in lits[:3])
            return f"'{gist} …'" if len(words) < len(arg) else f"'{gist}'"
    return "a value it works out"


def _describe_stmt(s, binds=None) -> str:
    keys = set(s)
    if "say" in keys:
        lines = _picked_lines(s["say"])
        if lines and len(lines) > 1:
            # Lines that all end the conversation are goodbyes, whatever their words.
            noun = ("goodbyes" if all(("the conversation ends", True) in _tag_effects(x)
                                      for x in lines) else "lines")
            return f"says one of {len(lines)} {noun} (picked unpredictably)"
        return f"tells your child {_describe(s['say'], 0, binds, spoken=True)}"
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
    """One English sentence per rule (§5.4). The grant list says what a pack *may* do;
    these say what it *will* do, and the pack review shows both."""
    if not isinstance(ext, dict) or not ext.get("rules"):
        return []
    out = []
    budget = [_BUDGET]                     # one for the whole call (`_Reader.spend`)
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
        # What the spoken line's tags make happen, said last: it happens after the line.
        # Each `say` replaces the line before it (`Volley.set_output`), so the robot is sent
        # the last one's tags only.
        says = [s for s in do if "say" in s]
        effects = _say_effects(says[-1]["say"], binds, budget) if says else []
        then = f"; then {' and '.join(effects)}" if effects else ""
        out.append(f"{head}: {body}{then}.")
    return out
