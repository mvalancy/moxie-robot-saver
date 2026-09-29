"""
Long-term memory — `persist_data` (content-module-contract.md → volley/session API).

One `memory.json` per robot: a dict of **namespaces** (one per content module), each
holding durable facts plus the provenance of how they got there. Non-negotiable on a
child's device: **bounded** (caps on namespaces, items, bytes), **erasable** (`erase()`,
exposed as `DELETE /memory`), and **policy-gated** (`LoggingPolicy.NO_DATA` = nothing
written; reads still work so a parent can inspect and erase). NO_DATA is compared by
value (0) to keep this module free of a config import.

The item model (ids, provenance, decay, caps) lives in `memory_items.py`; the durable,
cross-process-locked file I/O in `store.py`.

Credit: module-namespaced durable facts are OpenMoxie's (MIT) `MemoryChat.json`
`complete_handler`; per-item provenance is from OpenMoxie Fork A's
`conversation_memory.py` (openmoxie-feature-audit.md §3.2, §4.2 BEYOND #4). This code,
the caps, the policy gate and the JSON shape are ours.
"""
from __future__ import annotations

import functools
import json
import time

from .memory_items import (
    MEMORY_COLLECTION, POLICY_NO_DATA, MAX_MEMORY_NAMESPACES, MAX_MEMORY_ITEMS,
    MAX_MEMORY_ITEM_CHARS, MAX_MEMORY_BYTES, memory_max_age_days, item_text,
    normalize_items, normalize_block, prune_stale, _policy_value, json_safe)
from .store import JsonStore, StoreLockTimeout


#: `refuses_on_lock` marker for a method whose caller expects an exception rather than a
#: falsy value (the parent's memory *edit*: an unsaved correction must be a 400, not silence).
RAISE_INSTEAD = object()


def refuses_on_lock(what: str, fallback):
    """Turn a `StoreLockTimeout` in one read-modify-write into that method's own
    *"nothing was stored"* answer, plus a printed line.

    Reuses each `MemoryStore` method's existing falsy answer rather than inventing a new
    failure shape; it must never escape into a turn as a traceback. `JsonStore.lock_timeouts`
    counts every one (§5.3 A11).
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, device_id, *a, **kw):
            try:
                return fn(self, device_id, *a, **kw)
            except StoreLockTimeout as e:
                print(f"[memory] ⏳ {what} for {device_id} was refused — {e}", flush=True)
                if fallback is RAISE_INSTEAD:
                    raise ValueError(
                        "the store is busy (another process is writing this robot's "
                        "memory) — that correction was not saved; try again") from e
                return fallback
        return wrapper
    return deco


def _stamp(now=None) -> float:
    return round(float(time.time() if now is None else now), 3)


def _item_lists(namespace: str, block: dict):
    """`(key, items)` for every item list in one namespace block, migrated to items.
    `_`-prefixed keys are bookkeeping, never items."""
    for key, values in list(block.items()):
        if not str(key).startswith("_") and isinstance(values, list):
            yield key, normalize_items(namespace, str(key), values)


def _has_id(item, item_id: str) -> bool:
    return isinstance(item, dict) and item.get("id") == str(item_id)


def _text_key(item) -> str:
    text = item_text(item)
    return text.strip().lower() if text is not None else repr(item)


class MemoryStore:
    """Durable, namespaced, bounded `persist_data` for one robot fleet.

    ``load`` / ``save`` move the whole per-robot dict; ``merge`` folds one namespace's
    new values in with provenance; ``view`` is what a parent reads; ``erase`` what a
    parent deletes. Every write goes through the same caps and policy gate.

    ``policy`` is an optional ``policy(device_id) -> LoggingPolicy | int | str``. Absent
    or None means "writes allowed" — defaulting to RobotCloudConfig's own `NO_DATA` would
    store nothing, so the gate is an explicit parent choice.
    """

    def __init__(self, store: "JsonStore | None" = None, *, policy=None,
                 collection: str = MEMORY_COLLECTION,
                 max_namespaces: int = MAX_MEMORY_NAMESPACES,
                 max_items: int = MAX_MEMORY_ITEMS,
                 max_bytes: int = MAX_MEMORY_BYTES):
        self.store = store if store is not None else JsonStore()
        self.policy = policy
        self.collection = collection
        self.max_namespaces = max_namespaces
        self.max_items = max_items
        self.max_bytes = max_bytes

    def _record(self, device_id: str):
        """Hold this robot's memory record for a read-modify-write (across processes)."""
        return self.store.transaction(device_id, self.collection)

    def _write(self, device_id: str, data: dict) -> bool:
        return self.store.write(device_id, self.collection, data)

    # ---- the privacy gate ----
    def writes_allowed(self, device_id: str) -> bool:
        """False under `LoggingPolicy.NO_DATA` — nothing about the child is stored."""
        if self.policy is None:
            return True
        try:
            raw = self.policy(device_id) if callable(self.policy) else self.policy
        except Exception:
            return True                       # a broken resolver must not lose memory
        return _policy_value(raw) != POLICY_NO_DATA

    # ---- reads (always allowed, so a parent can inspect and erase) ----
    def load(self, device_id: str) -> dict:
        """This robot's whole `persist_data` dict (`{}` when nothing is stored)."""
        data = self.store.read(device_id, self.collection, {})
        return data if isinstance(data, dict) else {}

    def namespaces(self, device_id: str) -> list:
        return sorted(k for k in self.load(device_id) if not k.startswith("_"))

    def view(self, device_id: str) -> dict:
        """What Moxie remembers, by namespace, with provenance — the parent's read.

        Items come out as full records (migrating old files on the way past). `meta`
        carries the module bookkeeping a parent does need (`summarized_through`); other
        `_`-prefixed keys stay out of `data`."""
        data = self.load(device_id)
        out = {}
        for ns in sorted(k for k in data if not k.startswith("_")):
            block = data[ns] if isinstance(data[ns], dict) else {"value": data[ns]}
            block = normalize_block(ns, block)
            meta = block.get("_meta")
            out[ns] = {"data": {k: v for k, v in block.items() if not k.startswith("_")},
                       "provenance": block.get("_provenance", []),
                       "meta": dict(meta) if isinstance(meta, dict) else {}}
        return {"namespaces": out,
                "bytes": len(json.dumps(data)) if data else 0,
                "writes_allowed": self.writes_allowed(device_id)}

    # ---- writes (bounded, JSON-safe, policy-gated) ----
    def _bound(self, data: dict) -> dict:
        """Apply the caps: namespaces, items per list, then total bytes."""
        safe = json_safe(data) or {}
        if not isinstance(safe, dict):
            return {}
        if len(safe) > self.max_namespaces:            # oldest-inserted namespaces go
            safe = {k: safe[k] for k in list(safe)[-self.max_namespaces:]}
        for block in safe.values():
            if isinstance(block, dict):
                for k, v in block.items():
                    if isinstance(v, list) and len(v) > self.max_items:
                        block[k] = v[: self.max_items]   # newest-first lists keep the head
        # Total size last: drop whole trailing namespaces until it fits.
        while len(json.dumps(safe)) > self.max_bytes and safe:
            safe.pop(list(safe)[-1])
        return safe

    def save(self, device_id: str, data: dict) -> bool:
        """Replace this robot's memory. Returns False when the policy dropped the write."""
        if not self.writes_allowed(device_id):
            return False
        return self._write(device_id, self._bound(dict(data or {})))

    def _merge_list(self, ns: str, key: str, on_disk, new_values, *, provenance,
                    prepend: bool) -> list:
        """Merge one list value: items de-duplicated case-insensitively by text.

        Re-learning an item must not reset it: the newest copy keeps its provenance and
        position but inherits the old id, pin and use clock."""
        old = normalize_items(ns, key, on_disk if isinstance(on_disk, list) else [])
        prior = {}
        for item in old:
            if isinstance(item, dict) and item_text(item) is not None:
                prior.setdefault(_text_key(item), item)
        new = normalize_items(ns, key, new_values, provenance=provenance)
        seen, out = set(), []
        for item in (new + old) if prepend else (old + new):
            key_of = _text_key(item)
            if key_of in seen:
                continue
            seen.add(key_of)
            was = prior.get(key_of)
            if isinstance(item, dict) and isinstance(was, dict) and was is not item:
                if was.get("id"):
                    item["id"] = was["id"]
                if was.get("pinned"):
                    item["pinned"] = True
                for carry in ("use_count", "last_used_at"):
                    if was.get(carry) and not item.get(carry):
                        item[carry] = was[carry]
            out.append(item)
        return out[: self.max_items]

    @refuses_on_lock("merge", None)
    def merge(self, device_id: str, namespace: str, values: dict, *,
              provenance: dict | None = None, meta: dict | None = None,
              prepend_lists: bool = True, now=None) -> dict | None:
        """Fold `values` into one namespace and record where they came from.

        List values become items, are **prepended** (newest first) and de-duplicated
        case-insensitively, so later conversations add to what earlier ones learned;
        scalars overwrite. `provenance` is appended to the namespace's `_provenance` log
        *and* stamped on each new item. `meta` is module bookkeeping (`_`-prefixed, out of
        the parent-facing `data`).

        Merge is also the maintenance window: every namespace is migrated to items and
        stale items pruned (`prune_stale`). Returns the merged namespace, or **None**
        when the policy dropped the write.
        """
        if not self.writes_allowed(device_id):
            return None
        ns = str(namespace or "default")
        with self._record(device_id):                 # read-modify-write, across processes
            data = {k: normalize_block(str(k), v) for k, v in self.load(device_id).items()}
            block = data.get(ns) if isinstance(data.get(ns), dict) else {}
            for key, value in (values or {}).items():
                if key.startswith("_"):
                    continue                          # `_provenance` is ours, not a module's
                safe = json_safe(value)
                if safe is None and value is not None:
                    continue
                if isinstance(safe, list):
                    block[key] = self._merge_list(ns, str(key), block.get(key), safe,
                                                  provenance=provenance,
                                                  prepend=prepend_lists)
                else:
                    block[key] = safe
            if meta:
                current = block.get("_meta") if isinstance(block.get("_meta"), dict) else {}
                current.update(json_safe(meta) or {})
                block["_meta"] = current
            if provenance:
                log = block.get("_provenance")
                log = list(log) if isinstance(log, list) else []
                log.insert(0, json_safe(provenance) or {})
                block["_provenance"] = log[: self.max_items]
            data[ns] = block
            data, dropped = prune_stale(data, max_age_days=memory_max_age_days(),
                                        now=time.time() if now is None else now)
            if dropped:
                print(f"[memory] decay: forgot {dropped} unused item(s) for {device_id}",
                      flush=True)
            self._write(device_id, self._bound(data))
            return self.load(device_id).get(ns, {})

    # ---- per-item: what a parent does about one wrong line -------------------------
    # Each works on a single `id` and leaves the rest of the namespace (incl.
    # `_meta.summarized_through`) untouched (BEYOND #4).

    def find_item(self, device_id: str, namespace: str, item_id: str) -> tuple:
        """`(kind, index, item)` for one id in one namespace, or `(None, -1, None)`."""
        block = self.load(device_id).get(str(namespace))
        if isinstance(block, dict):
            for key, items in _item_lists(str(namespace), block):
                for idx, item in enumerate(items):
                    if _has_id(item, item_id):
                        return str(key), idx, item
        return None, -1, None

    @refuses_on_lock("erase_item", False)
    def erase_item(self, device_id: str, namespace: str, item_id: str) -> bool:
        """Forget exactly one remembered item. Never policy-gated, like every erase."""
        with self._record(device_id):
            data = self.load(device_id)
            block = data.get(str(namespace))
            if not isinstance(block, dict):
                return False
            for key, items in _item_lists(str(namespace), block):
                kept = [i for i in items if not _has_id(i, item_id)]
                if len(kept) != len(items):
                    block[key] = kept
                    self._write(device_id, data)
                    return True
            return False

    @refuses_on_lock("edit_item", RAISE_INSTEAD)
    def edit_item(self, device_id: str, namespace: str, item_id: str, text: str, *,
                  history=(), check=None, now=None) -> dict:
        """Correct one remembered item, keeping its id, and **pin** it (out of decay).

        Never policy-gated (a `NO_DATA` robot can still have a wrong line fixed), but not
        unchecked: the text passes the same rules as a model summary — not BLOCKed by the
        safety classifier, not a long span of the child's own words — since it flows into
        every future prompt.

        Raises `ValueError` when the item does not exist or the text is refused."""
        new_text = str(text or "").strip()[:MAX_MEMORY_ITEM_CHARS]
        if not new_text:
            raise ValueError("an empty memory is an erase, not an edit")
        if check is None:
            from .content.memory import check_text as check   # lazy: no import cycle
        if not check(new_text, history=history):
            raise ValueError("that text cannot be stored (safety or the child's own words)")
        with self._record(device_id):
            data = self.load(device_id)
            block = data.get(str(namespace))
            if not isinstance(block, dict):
                raise ValueError(f"unknown namespace {namespace!r}")
            for key, items in _item_lists(str(namespace), block):
                item = next((i for i in items if _has_id(i, item_id)), None)
                if item is not None:
                    item.update(text=new_text, pinned=True, edited_at=_stamp(now))
                    block[key] = items
                    self._write(device_id, self._bound(data))
                    return item
            raise ValueError(f"unknown memory item {item_id!r}")

    @refuses_on_lock("note_used", 0)
    def note_used(self, device_id: str, rendered: str, *, now=None) -> int:
        """Mark the items that appear in a rendered prompt as used. → how many.

        Decay's whole clock: a blunt **substring** test against the rendered prompt (a
        reworded or truncated item is not counted). Policy-gated like every write."""
        text = rendered if isinstance(rendered, str) else ""
        if not text or not device_id or not self.writes_allowed(device_id):
            return 0
        stamp = _stamp(now)
        with self._record(device_id):
            data = self.load(device_id)
            hits = 0
            for ns, block in data.items():
                if str(ns).startswith("_") or not isinstance(block, dict):
                    continue
                for key, items in _item_lists(str(ns), block):
                    for item in items:
                        body = item_text(item)
                        if isinstance(item, dict) and body and body in text:
                            item["use_count"] = int(item.get("use_count") or 0) + 1
                            item["last_used_at"] = stamp
                            hits += 1
                    block[key] = items
            if hits:
                self._write(device_id, data)
            return hits

    @refuses_on_lock("erase", False)
    def erase(self, device_id: str, namespace: str | None = None) -> bool:
        """Forget one namespace, or (None/"all") everything for this robot.
        **Never** policy-gated: a parent must always be able to delete."""
        with self._record(device_id):
            data = self.load(device_id)
            if namespace in (None, "", "all", "*"):
                self.store.delete(device_id, self.collection)
                return bool(data)
            if namespace not in data:
                return False
            data.pop(namespace)
            self._write(device_id, data)
            return True
