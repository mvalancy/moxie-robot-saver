"""📦 Content packs and ✍️ authoring — the review table, the inventory, the results.

Restated rather than imported from `moxie_sdk.content.packs` (the console process has no
`mqtt/` on its path); `test_fleet.py` and the console round trip diff these shapes
against the real runtime.
"""
from __future__ import annotations
from typing import Optional

from ._coerce import _dict, _error, _int, _ints, _reload, _seq, _strs, card_view

#: Review states in the card's sort order: things to act on first, then noise.
CONTENT_STATES = ("conflict", "downgrade_conflict", "new", "upgrade", "fork",
                  "downgrade", "keep_local", "same", "invalid")

#: The three decisions a parent can make per row of the review table.
CONTENT_DECISIONS = ("accept", "keep", "skip")


def _identity(e: dict) -> dict:
    return {"id": str(e.get("id") or ""), "kind": str(e.get("kind") or ""),
            "key": str(e.get("key") or ""),
            "name": str(e.get("name") or e.get("key") or "")}


def normalize_content_item(entry: Optional[dict]) -> dict:
    """One inventory row: what it is, where it came from, and what to warn about."""
    e = _dict(entry)
    return {
        **_identity(e),
        "source_version": _int(e.get("source_version"), 1),
        "origin": str(e.get("origin") or ""),
        "pack_id": str(e.get("pack_id") or ""),
        "imported_at": _int(e.get("imported_at")),
        "local_edited": bool(e.get("local_edited")),
        "has_code": bool(e.get("has_code")),
        "warnings": _strs(e.get("warnings")),
        "pii": [{"field": str(h.get("field") or ""), "name": str(h.get("name") or "")}
                for h in _seq(e.get("pii")) if isinstance(h, dict)],
    }


def normalize_pack_row(entry: Optional[dict]) -> dict:
    """One row of the installed-packs ledger."""
    e = _dict(entry)
    return {**{k: str(e.get(k) or "") for k in ("id", "name", "details", "author")},
            "pack_version": _int(e.get("pack_version"), 1),
            "digest": str(e.get("digest") or ""),
            "imported_at": _int(e.get("imported_at")),
            "item_count": _int(e.get("item_count"))}


@card_view("content", {
    "ok": False, "items": [], "packs": [], "counts": {}, "undo_available": False,
    "undo_label": "", "max_bytes": 0, "pack_format": 0, "error": "supervisor not reachable"})
def normalize_content_view(p: dict) -> dict:
    """Runtime `GET /content` → the 📦 card: inventory, ledger, undo."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        "items": [normalize_content_item(i) for i in _seq(p.get("items")) if isinstance(i, dict)],
        "packs": [normalize_pack_row(r) for r in _seq(p.get("packs")) if isinstance(r, dict)],
        "counts": _ints(p.get("counts")),
        "undo_available": bool(p.get("undo_available")),
        "undo_label": str(p.get("undo_label") or ""),
        "max_bytes": _int(p.get("max_bytes")),
        "pack_format": _int(p.get("pack_format")),
        "error": _error(p, ok, "no content available", "error", "reason"),
    }


def normalize_content_diff(entry: Optional[dict]) -> dict:
    """One field-level difference — a unified diff for prose, `old → new` for a scalar."""
    e = _dict(entry)
    return {"field": str(e.get("field") or ""), "kind": str(e.get("kind") or "scalar"),
            "old": str(e.get("old") or ""), "new": str(e.get("new") or ""),
            "diff": _strs(e.get("diff"))}


def normalize_content_row(entry: Optional[dict]) -> dict:
    """One review row. `decision` pre-sets the card's radio: `accept` for what the runtime
    pre-ticks, `keep` for anything that would replace a local edit (the safe choice is
    the selected one), else `skip`. An `invalid` row cannot be accepted at all."""
    e = _dict(entry)
    state = str(e.get("state") or "")
    default = bool(e.get("default"))
    edited = bool(e.get("local_edited"))
    decision = ("skip" if state == "invalid" else "accept" if default
                else "keep" if edited else "skip")
    installed = e.get("installed_version")
    return {
        **_identity(e),
        "state": state if state in CONTENT_STATES else (state or "unknown"),
        "label": str(e.get("label") or ""),
        "default": default,
        "decision": decision,
        "installable": state != "invalid",
        "local_edited": edited,
        "source_version": _int(e.get("source_version"), 1),
        "installed_version": None if installed is None else _int(installed),
        "origin": str(e.get("origin") or ""),
        "pack_id": str(e.get("pack_id") or ""),
        "warnings": _strs(e.get("warnings")),
        "reasons": _strs(e.get("reasons")),
        "diff": [normalize_content_diff(d) for d in _seq(e.get("diff")) if isinstance(d, dict)],
    }


@card_view("review", {
    "ok": False, "pack": {}, "digest": "", "expect_digest": "", "items": [], "accept": [],
    "counts": {}, "warnings": [], "error": "supervisor not reachable"})
def normalize_content_review(p: dict) -> dict:
    """Runtime `POST /content/review` → the review table. `digest` is `ok`, `mismatch`
    (changed after export — nothing pre-selected) or `absent` (hand-written, flagged)."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        "pack": normalize_pack_row(p.get("pack")),
        "digest": str(p.get("digest") or ""),
        "expect_digest": str(p.get("expect_digest") or ""),
        "items": [normalize_content_row(i) for i in _seq(p.get("items")) if isinstance(i, dict)],
        "accept": _strs(p.get("accept")),
        "counts": _ints(p.get("counts")),
        "warnings": _strs(p.get("warnings")),
        "error": _error(p, ok, "this file could not be read as a content pack",
                        "error", "reason"),
    }


@card_view("save", {
    "ok": False, "id": "", "key": "", "kind": "", "created": False, "item": {},
    "shadow": [], "local_rev": "", "conflict": False, "reload": {},
    "undo_available": False, "undo_slots": 1, "reasons": [],
    "error": "supervisor not reachable"})
def normalize_content_item_result(p: dict) -> dict:
    """Runtime `POST /content/item` → what the ✍️ editor shows after a Save.

    `shadow` is advice, not a result: an installed command that answers a phrase the
    author typed before this one would. It is exact for the phrases typed and claims
    nothing about any other utterance — the card must not round it up to "no conflicts"."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        **{k: str(p.get(k) or "") for k in ("id", "key", "kind")},
        "created": bool(p.get("created")),
        "item": normalize_content_item(p.get("item")),
        "shadow": [{k: str(s.get(k) or "") for k in ("phrase", "id", "name", "sentence")}
                   for s in _seq(p.get("shadow")) if isinstance(s, dict)],
        "local_rev": str(p.get("local_rev") or ""),
        "conflict": bool(p.get("conflict")),
        "reload": _reload(p.get("reload")),
        "undo_available": bool(p.get("undo_available")),
        "undo_slots": _int(p.get("undo_slots"), 1),
        "reasons": _strs(p.get("reasons")),
        "error": _error(p, ok, "this item could not be saved", "reason", "error"),
    }


@card_view("render", {
    "ok": False, "prompt": "", "opener": "", "openers": [], "portable": "",
    "portable_identical": True, "counts": {}, "counts_advisory": True, "context": {},
    "error": "supervisor not reachable"})
def normalize_content_render(p: dict) -> dict:
    """Runtime `POST /content/render` → the resolved-prompt panel. `portable_identical`
    answers "does this prompt mean the same on a box without jinja2?"; `counts` is
    advisory (the renderer's counters are process-global), never one render's measure."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        "prompt": str(p.get("prompt") or ""),
        "opener": str(p.get("opener") or ""),
        "openers": _strs(p.get("openers")),
        "portable": str(p.get("portable") or ""),
        "portable_identical": bool(p.get("portable_identical", True)),
        "counts": _ints(p.get("counts")),
        "counts_advisory": bool(p.get("counts_advisory", True)),
        "context": {str(k): v for k, v in _dict(p.get("context")).items()},
        "error": _error(p, ok, "this draft could not be resolved", "reason", "error"),
    }


@card_view("import", {
    "ok": False, "applied": [], "replaced": [], "skipped": [], "count": 0, "restored": 0,
    "conflict": False, "undo_available": False, "pack": {}, "reload": {}, "label": "",
    "error": "supervisor not reachable"})
def normalize_content_result(p: dict) -> dict:
    """Runtime `POST /content/import` or `/content/undo` → what actually happened.
    `conflict` is the 409: the file changed between the review and the import."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        **{k: _strs(p.get(k)) for k in ("applied", "replaced", "skipped")},
        "count": _int(p.get("count")),
        "restored": _int(p.get("restored")),
        "conflict": bool(p.get("conflict")),
        "undo_available": bool(p.get("undo_available")),
        "pack": normalize_pack_row(p.get("pack")),
        "reload": _reload(p.get("reload")),
        "label": str(p.get("label") or ""),
        "error": _error(p, ok, "the import did not go through", "reason", "error"),
    }
