"""Total value helpers the evaluator's ops are built from (§4.6: a bad input is the
error VALUE, never an exception), plus the seeded PRNG (§6.1)."""

from __future__ import annotations

import math

from .grammar import (ERROR, _FMT, is_error)


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
    """A number, or ERROR. `bool` is deliberately not a number (`true + 1` is a type
    confusion no author needs)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return ERROR
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ERROR
    return v


def _text(v) -> str:
    """`str` semantics, host-independent (§6.1): floats to six places with trailing
    zeros trimmed (a JS port can match it), booleans as `true`/`false`, null as `""`."""
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
    """A pure-integer PRNG (mulberry32) over a host-supplied 32-bit seed — for
    determinism, not secrecy (§6.1): the conformance goldens replay turns, and the P1 JS
    evaluator must reproduce the stream bit for bit."""
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
