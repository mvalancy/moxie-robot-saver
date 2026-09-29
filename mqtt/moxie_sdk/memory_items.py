"""
The long-term memory **item model** — ids, provenance, decay and caps — used by
`memory_store.MemoryStore`. Pure functions over JSON-shaped data; no disk I/O.
"""
from __future__ import annotations

import hashlib
import os

#: Collection (file) name under the robot's data dir: `robots/<id>/memory.json`.
MEMORY_COLLECTION = "memory"

#: `LoggingPolicy.NO_DATA` — nothing may be stored about the child (enums.proto).
POLICY_NO_DATA = 0

#: Caps. Deliberately small: a handful of durable facts, not a transcript.
MAX_MEMORY_NAMESPACES = 32
MAX_MEMORY_ITEMS = 25            # items in any one list (facts, preferences, …)
MAX_MEMORY_ITEM_CHARS = 240      # one fact is a sentence, not a paragraph
# The whole memory.json, serialized. Sized for per-item records ({id, text, _provenance,
# …}); the byte cap drops whole trailing namespaces, so too small silently forgets activities.
MAX_MEMORY_BYTES = 65536

#: Bytes of blake2b in an item id → 8 hex characters: URL-short, collision-unlikely.
MEMORY_ID_BYTES = 4

#: How long an unused item survives, in days (`MOXIE_MEMORY_MAX_AGE_DAYS`, 0 = off).
#: ~A school term: a holiday does not wipe it; a year-old unused fact stops being re-fed.
MEMORY_MAX_AGE_DAYS = 90

#: Per-item provenance kept **on the item** (what the console renders); the namespace's
#: `_provenance` log keeps the full record.
ITEM_PROVENANCE_KEYS = ("at", "date", "module_id", "content_id", "turns", "reason")


def memory_max_age_days() -> int:
    """`MOXIE_MEMORY_MAX_AGE_DAYS` as a non-negative int (0 = decay off)."""
    raw = os.environ.get("MOXIE_MEMORY_MAX_AGE_DAYS", "").strip()
    if not raw:
        return MEMORY_MAX_AGE_DAYS
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return MEMORY_MAX_AGE_DAYS


# ---------------------------------------------------------------------------
# Items — a stable id, per-item provenance, and a use clock on every fact
# ---------------------------------------------------------------------------
# Each remembered thing is a small record, so a parent can erase or correct one line::
#
#     {"id": "9f3ac1d0", "text": "Sam has a beagle named Pepper",
#      "_provenance": {"at": …, "date": "2026-09-02", "module_id": "MEMORY_CHAT",
#                      "content_id": "default", "turns": 4, "reason": "exit"},
#      "use_count": 3, "last_used_at": 1788352646.0, "pinned": true}
#
# `id` is `blake2b(namespace \0 kind \0 text)` taken at creation and then carried (an
# edit keeps it). Being derived, bare-string items from older files migrate to exactly
# the ids they would have had (`normalize_items`). Defaults are omitted from the file.

def item_id(namespace: str, kind: str, text: str) -> str:
    """The stable id of one remembered item — 8 hex of blake2b(namespace|kind|text)."""
    raw = f"{namespace}\x00{kind}\x00{text}".encode("utf-8", "replace")
    return hashlib.blake2b(raw, digest_size=MEMORY_ID_BYTES).hexdigest()


def item_text(value):
    """The sentence a stored value carries (bare string or `{text: …}` record), or None
    when it is not a memory item."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def _unique_id(candidate: str, taken: set) -> str:
    """`candidate`, widened until free (only fires on a real hash collision)."""
    if candidate not in taken:
        return candidate
    n = 1
    while f"{candidate}{n:x}" in taken:
        n += 1
    return f"{candidate}{n:x}"


def item_provenance(prov) -> dict:
    """The subset of a merge's provenance worth carrying on every item."""
    p = prov if isinstance(prov, dict) else {}
    return {k: p[k] for k in ITEM_PROVENANCE_KEYS if p.get(k) not in (None, "")}


def make_item(namespace: str, kind: str, text: str, *, provenance=None,
              taken: set | None = None) -> dict:
    """A fresh item record for `text` under `namespace`/`kind`."""
    item = {"id": _unique_id(item_id(namespace, kind, text), taken or set()),
            "text": text}
    prov = item_provenance(provenance)
    if prov:
        item["_provenance"] = prov
    return item


def normalize_items(namespace: str, kind: str, values, *, provenance=None) -> list:
    """One stored list → items with ids (migrating bare strings and id-less dicts).
    Pure and idempotent, so ids are stable across reads of an old file."""
    out, taken = [], set()
    for value in list(values or []):
        text = item_text(value)
        if text is None:
            out.append(value)                  # not a memory item — never rewritten
            continue
        if isinstance(value, dict):
            item = dict(value)
            got = item.get("id")
            item["id"] = _unique_id(
                got if isinstance(got, str) and got else item_id(namespace, kind, text),
                taken)
            if provenance and not isinstance(item.get("_provenance"), dict):
                prov = item_provenance(provenance)
                if prov:
                    item["_provenance"] = prov
        else:
            item = make_item(namespace, kind, text, provenance=provenance, taken=taken)
        taken.add(item["id"])
        out.append(item)
    return out


def normalize_block(namespace: str, block, *, provenance=None) -> dict:
    """One stored namespace with every list migrated to items. `_`-keys are untouched."""
    if not isinstance(block, dict):
        return block
    out = {}
    for key, value in block.items():
        if not str(key).startswith("_") and isinstance(value, list):
            out[key] = normalize_items(namespace, str(key), value,
                                       provenance=provenance)
        else:
            out[key] = value
    return out


def item_clock(item) -> float | None:
    """When this item was last worth having — last rendered, else learned. **None** when
    neither is known: an undated item must never age out."""
    if not isinstance(item, dict):
        return None
    used = item.get("last_used_at")
    if isinstance(used, (int, float)) and not isinstance(used, bool) and used > 0:
        return float(used)
    prov = item.get("_provenance")
    born = prov.get("at") if isinstance(prov, dict) else None
    if isinstance(born, (int, float)) and not isinstance(born, bool) and born > 0:
        return float(born)
    return None


def prune_stale(data: dict, *, max_age_days: int, now: float) -> tuple:
    """Drop unpinned items nothing has used for `max_age_days`. → `(data, removed)`.

    Deliberately dumb: it sees only *whether* an item was rendered into a prompt, never
    whether it mattered. A parent's edit pins an item and exempts it from decay.
    """
    removed = 0
    if not isinstance(data, dict) or not max_age_days:
        return data, 0
    horizon = float(now) - (float(max_age_days) * 86400.0)
    for ns, block in list(data.items()):
        if str(ns).startswith("_") or not isinstance(block, dict):
            continue
        for key, values in list(block.items()):
            if str(key).startswith("_") or not isinstance(values, list):
                continue
            kept = []
            for value in values:
                clock = item_clock(value)
                pinned = isinstance(value, dict) and bool(value.get("pinned"))
                if clock is not None and not pinned and clock < horizon:
                    removed += 1
                    continue
                kept.append(value)
            block[key] = kept
    return data, removed


def _policy_value(policy) -> int | None:
    """A LoggingPolicy (enum / int / name string) as its int value; None if unknown."""
    if policy is None:
        return None
    if isinstance(policy, bool):
        return None
    if isinstance(policy, int):
        return int(policy)
    name = str(policy).strip().upper()
    return {"NO_DATA": 0, "NO_MEDIA": 1, "FULL": 2}.get(name)


def json_safe(value, *, _depth: int = 0):
    """`value` reduced to something `json.dump` accepts, or None. Module code and LLM
    summaries arrive as arbitrary Python; one bad value must not lose the memory file."""
    if _depth > 6:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_MEMORY_ITEM_CHARS]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                continue
            sv = json_safe(v, _depth=_depth + 1)
            if sv is not None or v is None:
                out[k] = sv
        return out
    if isinstance(value, (list, tuple, set)):
        items = []
        for v in list(value)[:MAX_MEMORY_ITEMS]:
            sv = json_safe(v, _depth=_depth + 1)
            if sv is not None:
                items.append(sv)
        return items
    return None                      # objects/callables/bytes are simply not memory
