"""The evaluator: a step-, time- and byte-budgeted interpreter over host-built facts."""

from __future__ import annotations

import math
import re

from .grammar import (ERROR, ERROR_TRANSPARENT, ExtResult, is_error, LAZY_OPS, Limits,
    MAX_ACTIONS, MAX_DEPTH, MAX_MARKUP_CHARS, MAX_MEMORY_WRITES, MAX_NOTE_CHARS,
    MAX_NOTES, MAX_REPEAT, MAX_SAY_CHARS, MAX_SUBSCRIPTIONS, normal_op, OPS)
from .validate import validate
from .values import (_format, _get, _num, _plural, _Prng, _scalar_key, _size, _slice,
    _text)


class _Breach(Exception):
    """Internal: unwinds the recursive walk on a breach. Always caught by `evaluate`,
    which discards the effect list whole (§4.5)."""

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
        # Every 256 steps against an injected monotonic clock — no threads or signals, so
        # it behaves the same in the supervisor and in a Worker isolate (§6.2).
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
            # Unreachable once validated; keeps `RecursionError` impossible regardless (X6).
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
                # `int("banana")` is ERROR, so a junk capture fails loudly rather than 0.
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
            # Sorted: iteration order must be host-independent (§6.1).
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
    """JSON equality, with `true == 1` deliberately false."""
    if isinstance(x, bool) != isinstance(y, bool):
        return False
    if is_error(x) or is_error(y):
        return False
    return x == y


def _json_copy(v):
    """A structural copy of plain JSON (never `copy.deepcopy`, which walks objects)."""
    if isinstance(v, dict):
        return {str(k): _json_copy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_copy(x) for x in v]
    return v


def evaluate(ext, facts, *, grants=None, now_ms: int = 0, clock_local=None,
             seed: int = 0, monotonic=None, limits: Limits | None = None) -> ExtResult:
    """Run one extension over one turn's facts. **Always returns.**

    `facts` is host-built plain JSON (§4.4). `now_ms` and `seed` are injected so a turn is
    replayable (§6.1); without `monotonic` only the step budget bounds the run. Effects are
    collected, never applied here, so a breach leaves nothing half-written (§4.5).
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
    """§6.3 output caps, checked before any effect is applied (all or nothing)."""
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
