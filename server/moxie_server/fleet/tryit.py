"""💬 Try it — the panel's choices and one preview turn, as the card reads them.

Restated rather than imported from the runtime (the console process has no `mqtt/` on its
path); `test_console_tryit.py` drives the real supervisor through these views.
"""
from __future__ import annotations
from typing import Optional

from ._coerce import _dict, _error, _int, _num, _seq, _strs, card_view

#: Every `kind` the supervisor can answer with (`moxie_runtime/tryit.py::TRY_STATUS`),
#: plus the two the console adds: no supervisor, and a supervisor that never answered.
TRY_KINDS = ("bad_request", "empty", "too_long", "bad_brain", "unknown_module",
             "unknown_device", "pending", "too_large", "budget", "busy",
             "brain_unavailable", "brain_unreachable", "brain_refused", "brain_error",
             "timeout", "internal", "unreachable")

_UNREACHABLE = "supervisor not reachable"


def _kind(p: dict, ok: bool) -> str:
    if ok:
        return ""
    kind = str(p.get("kind") or "")
    if not kind and p.get("error") == _UNREACHABLE:
        return "unreachable"
    return kind if kind in TRY_KINDS else (kind or "bad_request")


def _budget(v) -> dict:
    b = _dict(v)
    return {"per_hour": _int(b.get("per_hour")), "remaining": _int(b.get("remaining")),
            "resets_in_s": _int(b.get("resets_in_s"))}


def _brain(v) -> dict:
    b = _dict(v)
    return {k: str(b.get(k) or "") for k in ("id", "label", "source", "note")}


def normalize_tryit_perform(entry: Optional[dict]) -> dict:
    """What one staged line asks the body to do (`read_markup` on the supervisor)."""
    e = _dict(entry)
    return {"faces": [{"mood": str(f.get("mood") or ""), "intensity": _int(f.get("intensity"))}
                      for f in _seq(e.get("faces")) if isinstance(f, dict)],
            **{k: _strs(e.get(k)) for k in ("gestures", "behaviours", "voice", "icons",
                                            "sounds", "spurts", "unknown")},
            "pauses": _int(e.get("pauses"))}


def normalize_tryit_action(entry: Optional[dict]) -> dict:
    """One action (exit, launch, sleep, …) plus the `RemoteChatAction` it would be."""
    e = _dict(entry)
    return {**{k: str(e.get(k) or "") for k in ("type", "module_id", "content_id",
                                                "function")},
            "wire": _dict(e.get("wire"))}


def normalize_tryit_chunk(entry: Optional[dict]) -> dict:
    """One piece of the answer: its words, its markup and what the markup does."""
    e = _dict(entry)
    scored = _dict(e.get("scored"))
    return {"index": _int(e.get("index")), "text": str(e.get("text") or ""),
            "markup": str(e.get("markup") or ""), "final": bool(e.get("final")),
            "result": str(e.get("result") or ""),
            "scored": {str(k): (v if isinstance(v, (int, float)) and not isinstance(v, bool)
                                else str(v)) for k, v in scored.items() if v is not None},
            "perform": normalize_tryit_perform(e.get("perform")),
            "actions": [normalize_tryit_action(a) for a in _seq(e.get("actions"))
                        if isinstance(a, dict)],
            "end_turn": bool(e.get("end_turn"))}


def _history(v) -> list:
    return [{"role": str(h.get("role") or ""), "content": str(h.get("content") or "")}
            for h in _seq(v) if isinstance(h, dict)
            and h.get("role") in ("user", "assistant")]


@card_view("try-it options", {
    "ok": False, "kind": "unreachable", "device_id": "", "child": {}, "brain": {},
    "brains": [], "pin": "", "pin_note": "", "modules": [], "module_brains": [],
    "current_module": "", "streaming": False, "safety": False, "limits": {},
    "budget": {}, "error": _UNREACHABLE})
def normalize_tryit_options(p: dict) -> dict:
    """Runtime `GET /tryit` → the 💬 card's selectors: who answers by default (and which
    layer decided), what else may answer, the conversations, the child, the limits."""
    ok = bool(p.get("ok"))
    child = _dict(p.get("child"))
    limits = _dict(p.get("limits"))
    return {
        "ok": ok, "kind": _kind(p, ok),
        "device_id": str(p.get("device_id") or ""),
        "child": {"nickname": str(child.get("nickname") or ""),
                  "source": str(child.get("source") or "")},
        "brain": _brain(p.get("brain")),
        "brains": [{k: str(b.get(k) or "") for k in ("id", "label", "group", "blurb")}
                   for b in _seq(p.get("brains")) if isinstance(b, dict) and b.get("id")],
        "pin": str(p.get("pin") or ""), "pin_note": str(p.get("pin_note") or ""),
        "modules": [{k: str(m.get(k) or "") for k in ("key", "module_id", "content_id",
                                                      "name")}
                    for m in _seq(p.get("modules")) if isinstance(m, dict) and m.get("key")],
        "module_brains": _strs(p.get("module_brains")),
        "current_module": str(p.get("current_module") or ""),
        "streaming": bool(p.get("streaming")), "safety": bool(p.get("safety")),
        "limits": {"max_chars": _int(limits.get("max_chars"), 500),
                   "max_history": _int(limits.get("max_history")),
                   "max_name_chars": _int(limits.get("max_name_chars"), 40),
                   "timeout_s": _num(limits.get("timeout_s")) or 0},
        "budget": _budget(p.get("budget")),
        "error": _error(p, ok, "the try-it panel is unavailable", "reason", "error"),
    }


@card_view("try-it", {
    "ok": False, "kind": "unreachable", "preview": True, "published": False,
    "device_id": "", "speech": "", "child": {}, "brain": {}, "module": None, "notes": [],
    "delivery": "", "result": "",
    "reply": {"text": "", "chunks": [], "actions": [], "end_turn": False},
    "safety": [], "history": [], "history_trimmed": 0, "model_calls": 0,
    "elapsed_ms": 0, "budget": {}, "detail": None, "error": _UNREACHABLE})
def normalize_tryit(p: dict) -> dict:
    """Runtime `POST /tryit` → one turn of the 💬 card. `published` is always false: a try
    never reaches a robot. On a brain failure `ok` is false and `reply` still holds the
    line the child would have heard; `history` is then the session as it was."""
    ok = bool(p.get("ok"))
    reply = _dict(p.get("reply"))
    child = _dict(p.get("child"))
    module = p.get("module") if isinstance(p.get("module"), dict) else None
    detail = p.get("detail") if isinstance(p.get("detail"), dict) else None
    return {
        "ok": ok, "kind": _kind(p, ok), "preview": True, "published": False,
        "device_id": str(p.get("device_id") or ""), "speech": str(p.get("speech") or ""),
        "child": {"nickname": str(child.get("nickname") or ""),
                  "source": str(child.get("source") or "")},
        "brain": _brain(p.get("brain")),
        "module": None if module is None else
        {k: str(module.get(k) or "") for k in ("key", "module_id", "content_id", "name")},
        "notes": _strs(p.get("notes")),
        "delivery": str(p.get("delivery") or ""), "result": str(p.get("result") or ""),
        "reply": {"text": str(reply.get("text") or ""),
                  "chunks": [normalize_tryit_chunk(c) for c in _seq(reply.get("chunks"))
                             if isinstance(c, dict)],
                  "actions": [normalize_tryit_action(a) for a in _seq(reply.get("actions"))
                              if isinstance(a, dict)],
                  "end_turn": bool(reply.get("end_turn"))},
        "safety": [{"stage": str(s.get("stage") or ""), "action": str(s.get("action") or ""),
                    "categories": _strs(s.get("categories")),
                    "labels": _strs(s.get("labels")), "escalate": bool(s.get("escalate"))}
                   for s in _seq(p.get("safety")) if isinstance(s, dict)],
        "history": _history(p.get("history")),
        "history_trimmed": _int(p.get("history_trimmed")),
        "model_calls": _int(p.get("model_calls")),
        "elapsed_ms": _int(p.get("elapsed_ms")),
        "budget": _budget(p.get("budget")),
        "detail": None if detail is None else
        {"type": str(detail.get("type") or ""), "status": _num(detail.get("status")),
         "message": str(detail.get("message") or "")},
        "error": _error(p, ok, "the try did not go through", "reason", "error"),
    }
