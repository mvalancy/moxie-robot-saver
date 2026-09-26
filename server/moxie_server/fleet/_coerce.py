"""Defensive coercion shared by every card view.

Everything here reads another process's JSON, so nothing may raise: odd types become
empty values, and a card view that cannot read its payload says so in `error`.
"""
from __future__ import annotations
import copy
import functools


def _num(v):
    """Coerce to int/float when it looks numeric; else None (a bool isn't a number)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _int(v, default: int = 0) -> int:
    return int(_num(v) or default)


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _seq(v) -> list:
    """A list/tuple as a list; anything else (a string is iterable!) as `[]`."""
    return list(v) if isinstance(v, (list, tuple)) else []


def _strs(v) -> list:
    return [str(x) for x in _seq(v)]


def _ints(d) -> dict:
    return {str(k): _int(v) for k, v in _dict(d).items()}


def _reload(d) -> dict:
    return {str(k): (v if isinstance(v, bool) else _int(v)) for k, v in _dict(d).items()}


def _error(p: dict, ok: bool, fallback: str, *order: str):
    """None when ok, else the first of `p[order…]` that is set, else `fallback`."""
    if ok:
        return None
    return next((p[k] for k in (order or ("error",)) if p.get(k)), None) or fallback


def card_view(what: str, empty: dict):
    """Wrap a `(payload: dict) -> view` so a card is never a 500.

    A missing/non-dict/empty payload is `empty` (whose `error` says the supervisor is not
    reachable); an exception is `empty` with `error: "unreadable <what> payload: …"`."""
    def wrap(fn):
        @functools.wraps(fn)
        def view(payload=None):
            p = _dict(payload)
            if not p:
                return copy.deepcopy(empty)
            try:
                return fn(p)
            except Exception as e:
                return {**copy.deepcopy(empty), "error": f"unreadable {what} payload: {e}"}
        return view
    return wrap
