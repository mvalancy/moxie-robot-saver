"""🧠 "What Moxie remembers" — the runtime's `GET /memory` as dated, per-activity rows.

A parent thinks "what does Moxie believe about my kid, when did it learn that, and from
which activity", not in namespaces-of-lists, so each namespace is flattened into rows
carrying the item's id (what per-item erase/edit act on), its own provenance, whether a
parent `pinned` it by correcting it, and how often a prompt used it. A bare string (a
`memory.json` from before ids) still renders, with no id and the namespace's newest
provenance as its date.
"""
from __future__ import annotations
from typing import Optional

from ._coerce import _dict, _int, _num

#: The lists a namespace may hold, in reading order, with the noun one row gets.
#: Mirrors `content/memory.py::LIST_KEYS` + `summaries`.
MEMORY_KINDS = (
    ("facts", "fact"),
    ("preferences", "preference"),
    ("open_threads", "open thread"),
    ("summaries", "summary"),
)


def memory_provenance(p: Optional[dict]) -> dict:
    """One `_provenance` entry → the fields a parent row shows (never raises)."""
    p = _dict(p)
    return {"date": str(p.get("date") or ""), "at": _num(p.get("at")),
            "module_id": str(p.get("module_id") or ""),
            "content_id": str(p.get("content_id") or ""),
            "turns": _int(p.get("turns")), "reason": str(p.get("reason") or "")}


def _memory_block(block) -> tuple:
    """One namespace → `(data, provenance, meta)`, from the view's `{data, provenance}`
    wrapper or a raw `memory.json` block (`_provenance` / `_meta` beside the lists)."""
    if not isinstance(block, dict):
        return {}, [], {}
    if isinstance(block.get("data"), dict) and isinstance(block.get("provenance"), list):
        data, prov, meta = block["data"], block["provenance"], _dict(block.get("meta"))
    else:
        data = {k: v for k, v in block.items() if not str(k).startswith("_")}
        prov, meta = block.get("_provenance"), _dict(block.get("_meta"))
    return data, [p for p in (prov or []) if isinstance(p, dict)], meta


def _memory_item(value, kind: str, fallback: dict) -> dict:
    """One remembered value → `{kind, text, id, pinned, use_count, last_used,
    provenance}`. Without an id the card offers only the activity-level erase."""
    prov, item_id, pinned, uses, used_at = fallback, "", False, 0, None
    if isinstance(value, dict):
        text = value.get("text") or value.get("value") or ""
        own = value.get("_provenance") or value.get("provenance")
        if isinstance(own, list):
            own = own[0] if own else None
        if isinstance(own, dict):
            prov = own
        item_id = str(value.get("id") or "")
        pinned = bool(value.get("pinned"))
        uses = _int(value.get("use_count"))
        used_at = _num(value.get("last_used_at"))
    else:
        text = value
    return {"kind": kind, "text": str(text), "id": item_id, "pinned": pinned,
            "use_count": uses, "last_used": used_at, "provenance": memory_provenance(prov)}


def _newest_first(p: dict) -> tuple:
    return (p.get("at") if isinstance(p.get("at"), (int, float)) else 0.0, p.get("date") or "")


def normalize_namespace(namespace: str, block) -> dict:
    """One activity's memory → `{namespace, counts, items, …}`."""
    data, prov, meta = _memory_block(block)
    newest = prov[0] if prov else {}
    items, counts = [], {}
    known = [k for k, _ in MEMORY_KINDS]
    # the known lists, then anything else a module stored — a count must never hide rows
    for key, kind in list(MEMORY_KINDS) + [(k, str(k)) for k in sorted(data) if k not in known]:
        values = data.get(key)
        if not isinstance(values, list):
            values = ([values] if values else []) if key in known else [values]
        rows = [_memory_item(v, kind, newest) for v in values if v not in (None, "")]
        if rows or key in known:
            counts[key if key in known else str(key)] = len(rows)
        items.extend(rows)
    items.sort(key=lambda i: _newest_first(i.get("provenance") or {}), reverse=True)
    counts["total"] = len(items)
    through = _num(meta.get("summarized_through"))
    return {
        "namespace": str(namespace),
        "counts": counts,
        "last_learned": memory_provenance(newest),   # for a "learned … from …" header
        "conversations": len(prov),
        "summarized_through": int(through) if through is not None else None,
        "items": items,
    }


def normalize_memory(raw: Optional[dict]) -> dict:
    """Runtime `/memory` → the 🧠 card. Also accepts a bare `memory.json` dict.
    JSON-safe: every value out is a str/int/float/bool/list/dict."""
    p = _dict(raw)
    if "namespaces" in p or "ok" in p or "error" in p or not p:
        ok, blocks = bool(p.get("ok")), _dict(p.get("namespaces"))
    else:
        ok, blocks = True, p              # a raw robots/<id>/memory.json off disk
    rows = [normalize_namespace(ns, blocks[ns])
            for ns in sorted(blocks) if not str(ns).startswith("_")] if ok else []
    rows.sort(key=lambda r: _newest_first(r["last_learned"]), reverse=True)
    through = [r["summarized_through"] for r in rows if r["summarized_through"]]
    out = {
        "ok": ok,
        "device_id": p.get("device_id"),
        # NO_DATA stops new writes; reads and erase always work
        "policy": p.get("policy") if ok else None,
        "writes_allowed": bool(p.get("writes_allowed", True)) if ok else False,
        "bytes": _int(p.get("bytes")) if ok else 0,
        "namespaces": rows,
        "namespace_count": len(rows),
        "total": sum(r["counts"]["total"] for r in rows),
        "summarized_through": max(through) if through else None,
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }
    if "erased" in p or "edited" in p:    # an erase/edit reply carries its confirmation
        for k in ("erased", "edited"):
            if k in p:
                out[k] = bool(p.get(k))
        out["namespace"] = str(p.get("namespace") or "all")
        if p.get("item"):
            out["item"] = str(p.get("item"))
    return out
