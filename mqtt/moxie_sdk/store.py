"""
Durable per-robot store — plain JSON files on disk, zero dependencies.

The robot cloud needs to *remember* things between restarts: what activities a child
has finished (`mentor_behaviors`), and later schedules, memory and telemetry. Today
that memory lives in process RAM and dies with the supervisor.

This is the smallest honest fix: one JSON file per (robot, collection), written
atomically, under a data directory. **It is a stepping stone, not a database** — the
audit's ADOPT #8 (`docs/architecture/openmoxie-feature-audit.md` §4.1) calls for a real
DB, and the API here (read / write / append / delete / devices) is deliberately narrow
so it can be re-implemented over SQLite without touching a caller.

Layout::

    $MOXIE_DATA_DIR/robots/<device>/<collection>.json     # default: mqtt/data/
    $MOXIE_DATA_DIR/fleet/<collection>.json               # appliance-wide, no device

Properties we actually rely on:
  * **robust to a missing directory** — reads return the default, writes create it;
  * **atomic-ish writes** — write a temp file in the same directory, then `os.replace`,
    so a crash mid-write leaves the previous good file, never a truncated one;
  * **thread-safe** — the runtime ingests reports on a worker pool, so read-modify-write
    (`append`) is serialized by a lock;
  * **pure** — no MQTT, no protobuf, no config import; unit-testable on a tmp dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading

# Default data dir: mqtt/data/ (sibling of moxie_sdk/). Git-ignored — it is runtime
# state, not source. Override with MOXIE_DATA_DIR (e.g. a volume in the compose stack).
_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data")

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def data_dir() -> str:
    """The configured data directory (`MOXIE_DATA_DIR`, else `mqtt/data`)."""
    return os.environ.get("MOXIE_DATA_DIR", "").strip() or _DEFAULT_DIR


def safe_name(value: str) -> str:
    """A filesystem-safe directory name for an arbitrary device id.

    Robot ids are `d_<uuid>` (cloud-protocol.md), which is already safe — but the store
    must never be a path-traversal lever for an id off the wire. Unsafe characters are
    replaced and a short digest of the original is appended so two different ids can
    never collide on one directory.
    """
    value = str(value or "")
    cleaned = "".join(c if c in _SAFE else "_" for c in value).strip(".") or "_"
    if cleaned != value:
        cleaned = f"{cleaned[:48]}-{hashlib.sha256(value.encode()).hexdigest()[:8]}"
    return cleaned


class JsonStore:
    """A tiny per-robot JSON store. One file per (device_id, collection)."""

    def __init__(self, root: str | None = None):
        self.root = root or data_dir()
        self._lock = threading.RLock()

    # ---- paths ----
    def device_dir(self, device_id: str) -> str:
        return os.path.join(self.root, "robots", safe_name(device_id))

    def path(self, device_id: str, collection: str) -> str:
        return os.path.join(self.device_dir(device_id), f"{safe_name(collection)}.json")

    def shared_path(self, collection: str) -> str:
        """Path of a **fleet-wide** record — one appliance, several robots, one place to
        set house rules (`fleet/<collection>.json`). Never under `robots/`, so it can
        never collide with a device id."""
        return os.path.join(self.root, "fleet", f"{safe_name(collection)}.json")

    # ---- reads ----
    def _read_path(self, path: str, default=None):
        """Read one JSON file, or `default` when it is missing/unreadable/corrupt — a
        store that raises on a bad file would take the robot's whole session down for one
        damaged record."""
        try:
            with open(path) as fh:
                return json.load(fh)
        except (FileNotFoundError, NotADirectoryError):
            return default
        except (OSError, ValueError):
            return default

    def read(self, device_id: str, collection: str, default=None):
        """Return the stored value, or `default` when nothing is stored."""
        return self._read_path(self.path(device_id, collection), default)

    def read_shared(self, collection: str, default=None):
        """Return the fleet-wide value (`fleet/<collection>.json`), or `default`."""
        return self._read_path(self.shared_path(collection), default)

    def devices(self) -> list:
        """Directory names of every robot with stored data (sorted)."""
        try:
            return sorted(d for d in os.listdir(os.path.join(self.root, "robots"))
                          if os.path.isdir(os.path.join(self.root, "robots", d)))
        except OSError:
            return []

    # ---- writes ----
    def write(self, device_id: str, collection: str, value) -> bool:
        """Store `value` (any JSON-serializable object). Returns True on success."""
        return self._write_path(self.path(device_id, collection), value)

    def write_shared(self, collection: str, value) -> bool:
        """Store a fleet-wide `value` (`fleet/<collection>.json`). True on success."""
        return self._write_path(self.shared_path(collection), value)

    def _write_path(self, path: str, value) -> bool:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w") as fh:
                    json.dump(value, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)          # atomic on POSIX; readers see old or new
                return True
            except (OSError, TypeError, ValueError):
                try:
                    os.unlink(tmp)             # never leave a half-written temp behind
                except (OSError, UnboundLocalError, NameError):
                    pass
                return False

    def append(self, device_id: str, collection: str, item, *, cap: int | None = None):
        """Append one item to a stored list and return the new list.

        `cap` keeps only the newest `cap` items (the store is a rolling history, not an
        archive). Read-modify-write under the store lock.
        """
        with self._lock:
            items = self.read(device_id, collection, [])
            if not isinstance(items, list):
                items = []
            items.append(item)
            if cap is not None and cap >= 0 and len(items) > cap:
                del items[: len(items) - cap]
            self.write(device_id, collection, items)
            return items

    def delete(self, device_id: str, collection: str) -> bool:
        """Remove one collection. True if a file was removed."""
        return self._delete_path(self.path(device_id, collection))

    def delete_shared(self, collection: str) -> bool:
        """Remove one fleet-wide collection. True if a file was removed."""
        return self._delete_path(self.shared_path(collection))

    def _delete_path(self, path: str) -> bool:
        with self._lock:
            try:
                os.unlink(path)
                return True
            except OSError:
                return False


# ---------------------------------------------------------------------------
# Long-term memory — `persist_data` (content-module-contract.md → volley/session API)
# ---------------------------------------------------------------------------
# The contract lists `volley.persist_data` as "cross-session storage" next to the
# per-turn `volley.local_data`. This is that store: one `memory.json` per robot, a
# dict of **namespaces** (one per content module), each holding the durable facts a
# later conversation may use plus the provenance of how they got there.
#
# Three properties are not negotiable on a child's device:
#   * **bounded** — a memory that grows without limit becomes both a prompt-cost bug
#     and a privacy problem. Caps on namespaces, items per list, and total bytes.
#   * **erasable** — `erase()` removes one namespace or everything, and the runtime
#     exposes it to a parent (`DELETE /memory`).
#   * **policy-gated** — `LoggingPolicy.NO_DATA` (the child-privacy gate, enums.proto
#     via cloud_config.py) means **no memory is written**. Reads still work, so a
#     parent can inspect and erase what was stored before the switch was flipped.
#
# NO_DATA is compared **by value** (0) rather than by importing `cloud_config`, so
# this module keeps the "no config import" purity its docstring promises.
#
# Pattern credit: OpenMoxie (MIT) ships `content_modules/MemoryChat.json`, whose
# `complete_handler` summarizes a finished chat into `volley.persist_data`. The idea
# of module-namespaced durable facts is theirs. Provenance on every remembered item
# (and a namespace a parent can read and erase) is from OpenMoxie Fork A's
# `conversation_memory.py`, which stamps `source_event_id`/module/timestamp/speaker on
# stored history and quarantines anything it cannot attribute
# (docs/architecture/openmoxie-feature-audit.md §3.2, §4.2 BEYOND #4). This code,
# the caps, the policy gate and the JSON shape are ours.

#: Collection (file) name under the robot's data dir: `robots/<id>/memory.json`.
MEMORY_COLLECTION = "memory"

#: `LoggingPolicy.NO_DATA` — nothing may be stored about the child (enums.proto).
POLICY_NO_DATA = 0

#: Caps. Deliberately small: this is a handful of durable facts, not a transcript.
MAX_MEMORY_NAMESPACES = 32
MAX_MEMORY_ITEMS = 25            # items in any one list (facts, preferences, …)
MAX_MEMORY_ITEM_CHARS = 240      # one fact is a sentence, not a paragraph
MAX_MEMORY_BYTES = 16384         # the whole memory.json, serialized


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
    """`value` reduced to something `json.dump` will accept, or None if it can't be.

    Content-module code and an LLM summary both reach the store as arbitrary Python;
    a store that raises on one bad value would lose the whole memory file."""
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


class MemoryStore:
    """Durable, namespaced, bounded `persist_data` for one robot fleet.

    ``load`` / ``save`` move the whole per-robot dict; ``merge`` folds one namespace's
    new values in with provenance; ``view`` is what a parent reads; ``erase`` is what a
    parent deletes. Every write goes through the same caps and the same policy gate.

    ``policy`` is an optional ``policy(device_id) -> LoggingPolicy | int | str`` the
    host installs (the runtime passes its own per-device config override). Absent, or
    returning None, means "writes allowed" — a memory feature that defaulted to the
    RobotCloudConfig's own `NO_DATA` default would never store anything at all, so the
    gate is an explicit parent choice, exactly like the safety journal's.
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
        """What Moxie remembers, by namespace, with provenance — the parent's read."""
        data = self.load(device_id)
        out = {}
        for ns in sorted(data):
            if ns.startswith("_"):
                continue
            block = data[ns] if isinstance(data[ns], dict) else {"value": data[ns]}
            out[ns] = {"data": {k: v for k, v in block.items()
                                if not k.startswith("_")},
                       "provenance": block.get("_provenance", [])}
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
            keep = list(safe)[-self.max_namespaces:]
            safe = {k: safe[k] for k in keep}
        for ns, block in list(safe.items()):
            if isinstance(block, dict):
                for k, v in list(block.items()):
                    if isinstance(v, list) and len(v) > self.max_items:
                        block[k] = v[: self.max_items]   # newest-first lists keep the head
        # Total size last: drop whole trailing namespaces until it fits, so a runaway
        # module can never crowd out the file (or blow up a prompt).
        while len(json.dumps(safe)) > self.max_bytes and safe:
            safe.pop(list(safe)[-1])
        return safe

    def save(self, device_id: str, data: dict) -> bool:
        """Replace this robot's memory. Returns False when the policy dropped the write."""
        if not self.writes_allowed(device_id):
            return False
        return self.store.write(device_id, self.collection, self._bound(dict(data or {})))

    def merge(self, device_id: str, namespace: str, values: dict, *,
              provenance: dict | None = None, meta: dict | None = None,
              prepend_lists: bool = True) -> dict | None:
        """Fold `values` into one namespace and record where they came from.

        List values are **prepended** (newest first) and de-duplicated case-insensitively,
        so a second conversation adds to what the first learned instead of replacing it;
        scalars overwrite. `provenance` (conversation id, module, date, how many turns)
        is appended to the namespace's `_provenance` log — Fork A's idea that a remembered
        thing must carry how it was learned. `meta` is a module's own bookkeeping (e.g.
        how far through a transcript it has summarized); like `_provenance` it is
        `_`-prefixed and stays out of the parent-facing `view()`. Returns the merged namespace, or **None**
        when the policy dropped the write (nothing is stored, and the caller can say so).
        """
        if not self.writes_allowed(device_id):
            return None
        ns = str(namespace or "default")
        with self.store._lock:                   # read-modify-write, like JsonStore.append
            data = self.load(device_id)
            block = data.get(ns)
            if not isinstance(block, dict):
                block = {}
            for key, value in (values or {}).items():
                if key.startswith("_"):
                    continue                     # `_provenance` is ours, not a module's
                safe = json_safe(value)
                if safe is None and value is not None:
                    continue
                if isinstance(safe, list):
                    old = block.get(key) if isinstance(block.get(key), list) else []
                    merged = (safe + old) if prepend_lists else (old + safe)
                    seen, dedup = set(), []
                    for item in merged:
                        key_of = item.strip().lower() if isinstance(item, str) else repr(item)
                        if key_of in seen:
                            continue
                        seen.add(key_of)
                        dedup.append(item)
                    block[key] = dedup[: self.max_items]
                else:
                    block[key] = safe
            if meta:
                # Bookkeeping the module needs but a parent should not have to read
                # (e.g. how far through the transcript we have already summarized).
                # `_`-prefixed keys are ours: `view()` keeps them out of `data`.
                current = block.get("_meta") if isinstance(block.get("_meta"), dict) else {}
                current.update(json_safe(meta) or {})
                block["_meta"] = current
            if provenance:
                log = block.get("_provenance")
                log = list(log) if isinstance(log, list) else []
                log.insert(0, json_safe(provenance) or {})
                block["_provenance"] = log[: self.max_items]
            data[ns] = block
            self.store.write(device_id, self.collection, self._bound(data))
            return self.load(device_id).get(ns, {})

    def erase(self, device_id: str, namespace: str | None = None) -> bool:
        """Forget one namespace, or (namespace None/"all") everything for this robot.

        Erasure is **never** policy-gated: a parent must always be able to delete."""
        with self.store._lock:
            if namespace in (None, "", "all", "*"):
                data = self.load(device_id)
                self.store.delete(device_id, self.collection)
                return bool(data)
            data = self.load(device_id)
            if namespace not in data:
                return False
            data.pop(namespace)
            self.store.write(device_id, self.collection, data)
            return True
