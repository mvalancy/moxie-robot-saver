"""The overlay: shipped defaults ⊕ installed items → one ContentModule."""
from __future__ import annotations

from dataclasses import fields as _dc_fields

from ..module import load_modules
from .items import (DATACLASS, digest_of, full_key, item_key, KINDS, normalize_data,
    PackError, SECTION, SPEC, split_key)
from .review import is_local_edited, _warnings
from .wire import _int_or, scan_outgoing, _source_version



def shipped_items(raw) -> dict:
    """A shipped module file (dict or list) as an items mapping with `origin: "shipped"`
    provenance, so our own upgrades obey the same rule as a community pack."""
    modules = raw if isinstance(raw, list) else [raw or {}]
    out = {}
    for module in modules:
        if not isinstance(module, dict):
            continue
        for kind in KINDS:
            for record in (module.get(SECTION[kind]) or []):
                if not isinstance(record, dict):
                    continue
                data = normalize_data(kind, record)
                key = item_key(kind, data)
                if not key:
                    continue
                sv = _int_or(record.get("source_version"), 1)
                out[full_key(kind, key)] = {
                    "kind": kind, "key": key, "data": data,
                    "provenance": {"kind": kind, "pack_id": "", "pack_version": 1,
                                   "source_version": sv, "imported_at": 0,
                                   "imported_rev": digest_of(data),
                                   "origin": "shipped"},
                }
    return out


def items_from_module(module) -> dict:
    """A loaded `ContentModule` back into an items mapping (used when no shipped baseline
    was recorded; the merge is idempotent)."""
    out = {}
    if module is None:
        return out
    for kind in KINDS:
        for obj in getattr(module, SECTION[kind], None) or []:
            data = {f: getattr(obj, f, d) for f, _c, d in SPEC[kind]}
            data = normalize_data(kind, data)
            key = item_key(kind, data)
            if not key:
                continue
            sv = _int_or(getattr(obj, "source_version", 1), 1)
            out[full_key(kind, key)] = {
                "kind": kind, "key": key, "data": data,
                "provenance": {"kind": kind, "pack_id": "", "pack_version": 1,
                               "source_version": sv, "imported_at": 0,
                               "imported_rev": digest_of(data), "origin": "shipped"},
            }
    return out


def merge_items(defaults: dict, overlay: dict) -> dict:
    """Effective content = shipped defaults, then the overlay by key (never deletes)."""
    out = {k: v for k, v in (defaults or {}).items()}
    for k, v in (overlay or {}).items():
        out[k] = v
    return out


def module_data(items: dict) -> dict:
    """An items mapping → module JSON for `load_modules`, ordered by (kind, key) so a
    global's match order is stable across reloads."""
    out = {SECTION[k]: [] for k in KINDS}
    for full, entry in sorted((items or {}).items()):
        kind, _key = split_key(full)
        if kind not in SPEC:
            continue
        e = entry if isinstance(entry, dict) else {}
        record = dict(normalize_data(kind, e.get("data")))
        record["source_version"] = _source_version(e)
        out[SECTION[kind]].append(record)
    return out


def build_module(defaults: dict, overlay: dict):
    """`defaults ⊕ overlay` → a live `ContentModule`. The one call `reload_content` makes."""
    return load_modules(module_data(merge_items(defaults, overlay)))


def inventory(items: dict, *, catalog=None, known_names=()) -> list:
    """The 📦 card's list: one row per installed item with provenance and flags
    (`known_names` feeds the per-row `scan_outgoing` PII check)."""
    rows = []
    for full, entry in sorted((items or {}).items()):
        kind, key = split_key(full)
        if kind not in SPEC:
            continue
        e = entry if isinstance(entry, dict) else {}
        prov = e.get("provenance") if isinstance(e.get("provenance"), dict) else {}
        try:
            data = normalize_data(kind, e.get("data"))
        except PackError:
            continue
        rows.append({
            "id": full, "kind": kind, "key": key,
            "name": data.get("name") or key,
            "source_version": _source_version(e),
            "origin": str(prov.get("origin") or ""),
            "pack_id": str(prov.get("pack_id") or ""),
            "imported_at": _int_or(prov.get("imported_at"), 0),
            "local_edited": is_local_edited({"kind": kind, **e}),
            "has_code": bool(data.get("code")),
            "warnings": _warnings(kind, data, catalog=catalog),
            "pii": [{"field": h["field"], "name": h["name"]}
                    for h in scan_outgoing([{"kind": kind, "key": key, "data": data}],
                                           known_names)],
        })
    return rows


def dataclass_fields(kind: str) -> tuple:
    """A kind's public dataclass fields minus `source_version` (`FIELDS` is pinned to it)."""
    return tuple(f.name for f in _dc_fields(DATACLASS[kind])
                 if not f.name.startswith("_") and f.name != "source_version")
