"""Export and parse: items to a pack file and back, refusing anything unreadable."""
from __future__ import annotations

import json
import re
import time

from .items import (dropped_fields, full_key, GENERATOR, item_key, KINDS,
    normalize_data, pack_digest, PACK_FORMAT, PackError, SPEC, split_key, TEXT_FIELDS)



def export_pack(items, *, name: str, pack_id: str, details: str = "", author: str = "",
                pack_version: int = 1, generator: str = GENERATOR, now=None) -> dict:
    """Build a pack from installed items — the store's mapping
    (`{"kind:KEY": {"data", "provenance"}}`) or a list of `{"kind", "data",
    "source_version"?}`. Sorted by (kind, key), so equal content exports to equal bytes."""
    rows = []
    for kind, key, entry in _iter_items(items):
        data = normalize_data(kind, entry.get("data"))
        rows.append({"kind": kind, "key": key or item_key(kind, data),
                     "source_version": _source_version(entry),
                     "data": data})
    rows.sort(key=lambda r: (KINDS.index(r["kind"]), r["key"]))
    stamp = time.gmtime(now if now is not None else time.time())
    pack = {
        "pack_format": PACK_FORMAT,
        "id": sanitize_pack_id(pack_id),
        "name": str(name or ""),
        "details": str(details or ""),
        "author": str(author or ""),
        "pack_version": int(pack_version or 1),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", stamp),
        "generator": str(generator or GENERATOR),
        "items": rows,
        "signatures": [],                     # reserved, unread — see the module docstring
    }
    pack["digest"] = pack_digest(pack)
    return pack


def sanitize_pack_id(pack_id: str) -> str:
    """`[a-z0-9-]`, ≤ 64 — a pack id is a filename and a store key, never free text."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", str(pack_id or "").strip().lower()).strip("-")
    return (cleaned or "pack")[:64]


def dumps_pack(pack: dict) -> str:
    """A pack as a person receives it: pretty, sorted, newline-terminated (the digest is
    over `canonical`, so formatting does not disturb it)."""
    return json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _source_version(entry) -> int:
    """An item's author-owned version counter: the entry's, else its provenance's, else 1."""
    if not isinstance(entry, dict):
        return 1
    for candidate in (entry.get("source_version"),
                      (entry.get("provenance") or {}).get("source_version")
                      if isinstance(entry.get("provenance"), dict) else None,
                      (entry.get("data") or {}).get("source_version")
                      if isinstance(entry.get("data"), dict) else None):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
    return 1


def _iter_items(items):
    """`(kind, key, entry)` for either accepted `items` shape."""
    if isinstance(items, dict):
        for full, entry in sorted(items.items()):
            kind, key = split_key(full)
            yield kind, key, (entry if isinstance(entry, dict) else {"data": entry})
        return
    for entry in (items or []):
        if not isinstance(entry, dict):
            raise PackError("each item must be an object")
        kind = entry.get("kind")
        yield kind, entry.get("key") or "", entry


def scan_outgoing(items, known_names) -> list:
    """Known child names that appear in text a pack is about to carry, so the export UI
    can warn ("this prompt mentions Ada"). Catches only names we know; never blocks."""
    names = [str(n).strip() for n in (known_names or []) if str(n or "").strip()]
    hits = []
    for kind, key, entry in _iter_items(items):
        try:
            data = normalize_data(kind, entry.get("data"))
        except PackError:
            continue
        for field in TEXT_FIELDS:
            text = data.get(field)
            if not isinstance(text, str) or not text:
                continue
            for name in names:
                if re.search(r"\b%s\b" % re.escape(name), text, re.I):
                    hits.append({"kind": kind, "key": key or item_key(kind, data),
                                 "field": field, "name": name})
    return hits


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #

def parse_pack(raw) -> tuple:
    """`bytes | str | dict` → `(pack, meta)`; raises `PackError` with a readable reason.

    `meta` is `{"digest": "ok" | "mismatch" | "absent", "warnings", "computed",
    "claimed"}`. A mismatch is not fatal (hand-written packs are legitimate), but then the
    review pre-ticks nothing. The digest is checked on the body as delivered; the returned
    pack is sanitized through the allowlist.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as e:
            raise PackError(f"this file is not UTF-8 text: {e}")
    if isinstance(raw, str):
        try:
            body = json.loads(raw or "null")
        except ValueError as e:
            raise PackError(f"this file is not valid JSON: {e}")
    else:
        body = raw
    if not isinstance(body, dict):
        raise PackError("a pack must be a JSON object with `pack_format` and `items`")

    fmt = body.get("pack_format")
    if fmt is None:
        raise PackError("no `pack_format`: this file was not written as a content pack "
                        "(an OpenMoxie module file is not one — that is P2)")
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt != PACK_FORMAT:
        raise PackError(f"pack_format {fmt!r} — this appliance reads format "
                        f"{PACK_FORMAT}. Update the appliance, or ask for an older pack.")

    claimed = body.get("digest")
    if claimed is None:
        state = "absent"
    else:
        state = "ok" if str(claimed) == pack_digest(body) else "mismatch"

    raw_items = body.get("items")
    if raw_items is None:
        raise PackError("no `items`: a pack with nothing in it cannot be installed")
    if not isinstance(raw_items, list):
        raise PackError(f"`items` must be a list, got {type(raw_items).__name__}")

    warnings = []
    items = []
    seen = set()
    for n, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise PackError(f"item {n}: expected an object, "
                            f"got {type(raw_item).__name__}")
        kind = raw_item.get("kind")
        if kind not in SPEC:
            raise PackError(f"item {n}: unknown kind {kind!r} "
                            f"(expected one of {', '.join(KINDS)})")
        data = normalize_data(kind, raw_item.get("data"))
        dropped = dropped_fields(kind, raw_item.get("data"))
        key = str(raw_item.get("key") or "") or item_key(kind, data)
        if full_key(kind, key) in seen:
            raise PackError(f"item {n}: {full_key(kind, key)} appears twice")
        seen.add(full_key(kind, key))
        if dropped:
            warnings.append(f"{full_key(kind, key)}: ignored unknown "
                            f"field(s) {', '.join(dropped)}")
        items.append({"kind": kind, "key": key,
                      "source_version": raw_item.get("source_version", 1),
                      "data": data})

    pack = {
        "pack_format": PACK_FORMAT,
        "id": sanitize_pack_id(body.get("id") or body.get("name") or "pack"),
        "name": str(body.get("name") or ""),
        "details": str(body.get("details") or ""),
        "author": str(body.get("author") or ""),
        "pack_version": _int_or(body.get("pack_version"), 1),
        "created_at": str(body.get("created_at") or ""),
        "generator": str(body.get("generator") or ""),
        "items": items,
        "signatures": [],
        "digest": str(claimed or ""),
    }
    return pack, {"digest": state, "warnings": warnings,
                  "computed": pack_digest(body), "claimed": str(claimed or "")}


def _int_or(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def pack_summary(pack: dict, *, imported_at=None) -> dict:
    """One row of the `fleet/content_packs.json` ledger the 📦 card lists."""
    p = pack or {}
    return {"id": p.get("id") or "", "name": p.get("name") or "",
            "details": p.get("details") or "", "author": p.get("author") or "",
            "pack_version": _int_or(p.get("pack_version"), 1),
            "digest": p.get("digest") or "",
            "imported_at": int(imported_at if imported_at is not None else time.time()),
            "item_count": len(p.get("items") or [])}
