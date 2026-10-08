"""`explain()` — the AST as English, which matters as much as `evaluate()`."""

from __future__ import annotations

import re

from .grammar import (is_error, Limits, MAX_DEPTH, OPS)
from .machine import (_Machine)
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


#: An action tag as `moxie_sdk/actions.py` reads one (`<name>` or `<name:field:…>`),
#: restated because this package imports nothing outside itself; only `_TAG_NAMES` do
#: anything. A parent never reads one: it is lifted out of quoted text, and the sentence
#: says what it makes happen instead.
_TAG = re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_]*)\s*((?::[^<>]*?)?)\s*>")
_TAG_NAMES = ("exit", "sleep", "launch", "launch_if_confirmed")

#: Stands for a piece of a spoken line that is worked out at run time (what the child said,
#: a number, a memory). A private-use character: it can sit in a tag's fields, never in its
#: name.
_HOLE = "\ue000"


def _lift(text) -> str:
    """`text` without the tags `actions.parse_action_tags` takes out of a spoken line."""
    return _TAG.sub(lambda m: " " if m.group(1).lower() in _TAG_NAMES else m.group(0),
                    str(text))


def _tag_effects(text) -> list:
    """What the action tags in one line make happen, in a parent's words, each paired with
    whether it is certain. A malformed tag does nothing, as in `actions.parse_action_tags`.
    A tag with a worked-out piece (`_HOLE`) in its fields may or may not come out well
    formed, so it is not certain; a launch whose module is worked out starts "an activity it
    works out"."""
    out = []
    for m in _TAG.finditer(str(text)):
        name = m.group(1).lower()
        if name not in _TAG_NAMES:
            continue
        fields = [f.strip() for f in m.group(2)[1:].split(":")] if m.group(2) else []
        while fields and not fields[-1]:
            fields.pop()
        sure = _HOLE not in m.group(0)
        if name in ("exit", "sleep"):
            if not fields or not sure:
                out.append(("the conversation ends" if name == "exit"
                            else "Moxie goes to sleep", sure))
        elif fields and fields[0] and (len(fields) <= 2 or not sure):
            what = ("an activity it works out" if _HOLE in fields[0]
                    else f"the {_plain(fields[0])} activity")
            out.append((f"Moxie starts {what}", sure))
    return out


def _lift_parts(parts) -> list:
    """A `concat`'s arguments with the action tags lifted out of its literal strings, a
    tag split across them (`["<ex", "it>Bye"]`) or around a worked-out part (`["<launch:",
    {"var": "speech"}, ">"]`) included: the parts are read joined, with a `_HOLE` for each
    one that is not a string."""
    text = "".join(p if isinstance(p, str) else _HOLE for p in parts)
    lifted = list(text)
    for m in _TAG.finditer(text):
        if m.group(1).lower() in _TAG_NAMES:
            lifted[m.start():m.end()] = " " * (m.end() - m.start())
    out, at = [], 0
    for p in parts:
        size = len(p) if isinstance(p, str) else 1
        out.append("".join(lifted[at:at + size]) if isinstance(p, str) else p)
        at += size
    return out


def _plain(text: str) -> str:
    """Author text made safe for a parent-facing sentence: no action tags, braces or quotes,
    one line, ≤ 80 chars — never JSON-looking, however hostile the input (T13)."""
    out = "".join(" " if c in "{}\"\n\r\t" else c for c in _lift(text))
    out = " ".join(out.split())
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
#: multiplies them), and this many characters across them. Past either, every tag written
#: in the `say` and its `let` names counts, as "sometimes".
_MAX_LINES = 256
_MAX_CHARS = 1_000_000

#: Ops whose value is a number, yes/no, or a fact the host builds: no tag written in their
#: arguments can reach the line. Any other op `_Reader.lines` does not follow (`get`,
#: `replace`, `join`, `slice`, …) may pass an argument's text on, so a tag in one of its
#: arguments may or may not be said. `test_leave_taking.py` holds this split to `OPS`.
_NO_TEXT_OPS = frozenset({
    "+", "-", "*", "/", "%", "floor", "ceil", "round", "abs", "min", "max",
    "==", "!=", "<", "<=", ">", ">=", "not", "len", "starts_with", "ends_with",
    "contains", "has", "int", "num", "clock.ms", "clock.local", "random.int",
    "presence.face_present", "session.total_volleys", "session.is_empty"})

#: The string ops `_Reader.lines` applies to the lines it has read (a hole has no case).
_CASE = {"upper": str.upper, "lower": str.lower, "trim": str.strip, "str": str}


class _TooMany(Exception):
    """One `say` can speak more lines than `_MAX_LINES` (or `_MAX_CHARS`)."""


#: What `_Reader.run` returns when the evaluator stops (a breach): not a value.
_UNRUN = object()


def _union(groups) -> list:
    """The lines in `groups`, each once and in order."""
    out, seen = [], set()
    for group in groups:
        for line in group:
            if line not in seen:
                seen.add(line)
                out.append(line)
    if len(out) > _MAX_LINES:
        raise _TooMany()
    return out


def _join(left, right) -> list:
    """`concat`: each line in `left` followed by each in `right` (an error stays one)."""
    chars = (sum(len(x) for x in left if x) * len(right)
             + sum(len(x) for x in right if x) * len(left))
    if len(left) * len(right) > _MAX_LINES or chars > _MAX_CHARS:
        raise _TooMany()
    return _union([[None if a is None or b is None else a + b for b in right]
                   for a in left])


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
    other part is a `_HOLE`, and a tag written inside it lands in `maybe`."""

    def __init__(self, binds):
        self.binds = dict(binds) if isinstance(binds, dict) else {}
        self.index = {name: i for i, name in enumerate(self.binds)}
        #: Effects of tags that feed an op whose value is not worked out here.
        self.maybe: list = []
        #: The `let` values made of literals only.
        self.known: dict = {}
        #: `(kind, name)` → `(lines, maybe)`, one per `let` name, worked out in binding
        #: order (as `evaluate` does), so a chain of names is never walked twice or deeply.
        self.memo: dict = {}
        #: How many `let` names the expression being read can see: a binding sees the
        #: earlier ones only, the `say` sees them all.
        self.sees = 0
        for name, expr in self.binds.items():
            if self.constant(expr):
                value = self.run(expr)
                if value is not _UNRUN:
                    self.known[name] = value
            for kind in ("lines", "choices"):
                self.maybe = []
                try:
                    got = getattr(self, kind)(expr)
                except _TooMany:
                    got = None
                self.memo[(kind, name)] = (got, self.maybe)
            self.sees += 1
        self.maybe = []

    def bound(self, name, kind: str) -> list:
        """`let` name `name` read as `kind` ("lines", or "choices" for a `random.pick`)."""
        if not isinstance(name, str) or self.index.get(name, self.sees) >= self.sees:
            return [_HOLE]                 # a fact, or a later binding (null at run time)
        got, maybe = self.memo[(kind, name)]
        self.maybe += [e for e in maybe if e not in self.maybe]
        if got is None:
            raise _TooMany()
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
        """`node`'s value, worked out by the evaluator itself; `_UNRUN` if it stops."""
        machine = _Machine({}, Limits(), 0, {}, 0, None)
        machine.binds = {k: v for k, v in self.known.items() if self.index[k] < self.sees}
        try:
            return machine.eval(node)
        except Exception:                  # a breach: too big, or not a program
            return _UNRUN

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
            return _union([self.lines(a, depth + 1) for a in self.branches(arg, depth)])
        if key == "or" and arg:
            return _union([self.lines(a, depth + 1) for a in arg])
        if key == "and" and arg:
            # The last operand's value, or an earlier falsy one (which holds no tag).
            return _union(([[""]] if len(arg) > 1 else [])
                          + [self.lines(arg[-1], depth + 1)])
        if key == "concat":
            out = [""]
            for part in arg:
                out = _join(out, self.lines(part, depth + 1))
            return out
        if key in _CASE and len(arg) == 1:
            return [x if x is None else _CASE[key](x)
                    for x in self.lines(arg[0], depth + 1)]
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
                    return _union([[_text(x)] for x in value])
                return [""]
        if depth <= MAX_DEPTH and isinstance(node, dict) and len(node) == 1:
            key, arg = next(iter(node.items()))
            if key == "var":
                return self.bound(arg, "choices")
            if key == "list" and isinstance(arg, list) and arg:
                return _union([self.lines(a, depth + 1) for a in arg])
            if key == "if" and isinstance(arg, list) and len(arg) >= 2:
                return _union([self.choices(a, depth + 1)
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
        """`node` feeds an op whose value is not worked out here: each tag in a string it
        can hold may or may not reach the line (`maybe`)."""
        if self.constant(node, depth):
            value = self.run(node)
            if value is not _UNRUN:
                texts = _strings(value)
            else:
                texts = self.written(node, depth)
        else:
            texts = [x for x in self.lines(node, depth) if x is not None]
        for text in texts:
            self.note(text)

    def written(self, node, depth: int = 0) -> list:
        """Every string written in `node`, or worked out from literals in it; `let` names
        are not followed (their expressions are read on their own)."""
        if self.constant(node, depth):
            value = self.run(node)
            if value is not _UNRUN:
                return _strings(value)
        if depth > MAX_DEPTH or not isinstance(node, dict) or len(node) != 1:
            return []
        key, arg = next(iter(node.items()))
        if key == "lit":                   # too big for the evaluator's value cap
            return _strings(arg)
        if isinstance(arg, list):
            return [s for a in arg for s in self.written(a, depth + 1)]
        return []

    def note(self, text) -> None:
        for effect, _ in _tag_effects(text):
            if effect not in self.maybe:
                self.maybe.append(effect)


def _say_effects(value, binds=None) -> list:
    """What a `say` makes happen through the tags in the line it speaks, in a parent's
    words, read from every line it can speak (`_Reader`). An effect that is not certain on
    every one of those lines happens "sometimes". What the program reads at run time (what
    the child said, a memory) is not its own text and is not read here."""
    reader = _Reader(binds)
    try:
        lines = reader.lines(value)
    except _TooMany:
        # Too many lines to read one by one: each tag written in it may be said.
        lines, reader.maybe = [], []
        for expr in [value] + list(reader.binds.values()):
            for text in reader.written(expr):
                reader.note(text)
    each = [[] if line is None else _tag_effects(line) for line in lines]
    order = []
    for effects in each:
        for effect, _ in effects:
            if effect not in order:
                order.append(effect)
    order += [e for e in reader.maybe if e not in order]
    return [e if each and all((e, True) in effects for effects in each)
            else f"sometimes {e}" for e in order]


#: Ops that shape a value without changing what a parent would call it; described by
#: their argument.
_TRANSPARENT_OPS = ("lower", "upper", "trim", "str", "int", "num", "abs", "floor",
                    "ceil", "round")


def _describe(node, depth: int = 0, binds=None) -> str:
    """One expression as a short English phrase; never JSON (T13). `binds` are the rule's
    `let` names, described by what they were bound to."""
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
        # A line that is only a tag says nothing; its tag is read in `_say_effects`.
        return f"'{_plain(node)}'" if _lift(node).strip() else "an empty phrase"
    if not isinstance(node, dict) or len(node) != 1:
        return "a value it works out"
    key = next(iter(node))
    arg = node[key]
    picked = _picked_lines(node)
    if picked:
        return (_describe(picked[0], depth, binds) if len(picked) == 1
                else f"one of {len(picked)} options (picked unpredictably)")
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
        # The gist, not the recipe: quote the literal words only.
        words = [a for a in arg if isinstance(a, str) and re.search("[A-Za-z]", a)]
        lits = [a.strip() for a in _lift_parts(arg)
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
    """One English sentence per rule (§5.4). The grant list says what a pack *may* do;
    these say what it *will* do, and the pack review shows both."""
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
        # What the spoken line's tags make happen, said last: it happens after the line.
        # Each `say` replaces the line before it (`Volley.set_output`), so the robot is sent
        # the last one's tags only.
        says = [s for s in do if "say" in s]
        effects = _say_effects(says[-1]["say"], binds) if says else []
        then = f"; then {' and '.join(effects)}" if effects else ""
        out.append(f"{head}: {body}{then}.")
    return out
