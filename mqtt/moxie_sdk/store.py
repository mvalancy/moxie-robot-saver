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
import time

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
# The whole memory.json, serialized. Raised 16 KB → 64 KB when every item grew from a
# bare string to `{id, text, _provenance, use_count, …}`: the byte cap drops *whole
# trailing namespaces*, so leaving it at 16 KB would have silently halved how many
# activities a robot can remember the day per-item ids landed. Still small enough that a
# runaway module cannot blow up a prompt or the disk.
MAX_MEMORY_BYTES = 65536

#: Bytes of blake2b in an item id → 8 hex characters. Short enough to put in a URL and
#: click, wide enough that a collision inside one (namespace, kind) is a curiosity.
MEMORY_ID_BYTES = 4

#: How long an unused item survives, in days (`MOXIE_MEMORY_MAX_AGE_DAYS`, 0 = off).
#: 90 days ≈ a school term: long enough that a summer holiday does not wipe the term's
#: memory, short enough that a fact nothing has used since last year stops being fed
#: back into every prompt.
MEMORY_MAX_AGE_DAYS = 90

#: The per-item provenance we keep **on the item** — the six fields the parent console
#: renders. The namespace-level `_provenance` log keeps the full record (conversation
#: id, source), so nothing is lost and the item stays about 110 bytes.
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
# A remembered thing used to be a bare string in a list. A parent who can only erase a
# whole activity is one wrong pronoun away from losing everything Moxie learned, so each
# item is now a small self-describing record instead::
#
#     {"id": "9f3ac1d0", "text": "Sam has a beagle named Pepper",
#      "_provenance": {"at": …, "date": "2026-09-02", "module_id": "MEMORY_CHAT",
#                      "content_id": "default", "turns": 4, "reason": "exit"},
#      "use_count": 3, "last_used_at": 1788352646.0, "pinned": true}
#
# `id` is `blake2b(namespace \0 kind \0 text)` taken at **creation** and then carried —
# an edit keeps the id, so a console link survives a correction. Because it is derived,
# a `memory.json` written before ids existed migrates to exactly the ids it would have
# had (`normalize_items` on read; written back on the next merge). Defaults are omitted
# from the file, so an item nobody has used or pinned costs id + text + provenance.

def item_id(namespace: str, kind: str, text: str) -> str:
    """The stable id of one remembered item — 8 hex of blake2b(namespace|kind|text)."""
    raw = f"{namespace}\x00{kind}\x00{text}".encode("utf-8", "replace")
    return hashlib.blake2b(raw, digest_size=MEMORY_ID_BYTES).hexdigest()


def item_text(value):
    """The sentence a stored value carries, or None when it is not a memory item.

    Both shapes are items: a bare string (pre-ids, or written straight by a module) and
    the record above. Anything else — a number, a module's own dict — is left alone."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def _unique_id(candidate: str, taken: set) -> str:
    """`candidate`, widened until it is free. Two items with the same text under one kind
    never survive the merge dedup, so this only fires on a real hash collision."""
    if candidate not in taken:
        return candidate
    n = 1
    while f"{candidate}{n:x}" in taken:
        n += 1
    return f"{candidate}{n:x}"


def item_provenance(prov) -> dict:
    """The subset of a merge's provenance that is worth carrying on every item."""
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

    Pure and idempotent: run twice and nothing changes, which is what makes "ids are
    stable across reads" true for a file written before ids existed."""
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
    """When this item was last worth having — the last prompt that rendered it, else the
    day it was learned. **None** when neither is known: an item we cannot date is one we
    must never age out, because "undated" and "unused since 2019" look identical."""
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

    Deliberately dumb, and that is the honest part: this can only see *whether* an item
    was rendered into a prompt, never whether it was true, useful, or hurtful. It cannot
    judge that "Sam's grandad died" matters more than "Sam liked the blue crayon". It
    only stops a stale fact being re-injected forever. A parent's edit pins an item and
    takes it out of decay entirely — a human decision outranks a clock."""
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
        """What Moxie remembers, by namespace, with provenance — the parent's read.

        Every item comes out as its full record (`id`, `text`, per-item `_provenance`,
        `use_count`, `pinned`), migrating a file written before ids existed on the way
        past — so a parent reading an old robot still gets something they can erase or
        correct one line at a time. `meta` carries the module's own bookkeeping that a
        parent *does* need to see (`summarized_through`: how far through the transcript
        was written down); the rest of the `_`-prefixed engine keys stay out of `data`."""
        data = self.load(device_id)
        out = {}
        for ns in sorted(data):
            if ns.startswith("_"):
                continue
            block = data[ns] if isinstance(data[ns], dict) else {"value": data[ns]}
            block = normalize_block(ns, block)
            meta = block.get("_meta")
            out[ns] = {"data": {k: v for k, v in block.items()
                                if not k.startswith("_")},
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
              prepend_lists: bool = True, now=None) -> dict | None:
        """Fold `values` into one namespace and record where they came from.

        List values become **items** (`{id, text, _provenance, …}`), are **prepended**
        (newest first) and de-duplicated case-insensitively, so a second conversation adds
        to what the first learned instead of replacing it; scalars overwrite. `provenance`
        (conversation id, module, date, how many turns) is appended to the namespace's
        `_provenance` log *and* stamped on each item it created — Fork A's idea that a
        remembered thing must carry how it was learned, taken down to the line. `meta` is a
        module's own bookkeeping (e.g. how far through a transcript it has summarized);
        like `_provenance` it is `_`-prefixed and stays out of the parent-facing `data`.

        Merge is also the file's maintenance window: every namespace is migrated to items
        (so a `memory.json` written before ids existed gains them here, exactly the ids it
        already reads back with) and stale items are pruned (see `prune_stale`). Returns
        the merged namespace, or **None** when the policy dropped the write (nothing is
        stored, and the caller can say so).
        """
        if not self.writes_allowed(device_id):
            return None
        ns = str(namespace or "default")
        with self.store._lock:                   # read-modify-write, like JsonStore.append
            data = self.load(device_id)
            # Write-back of the id migration: whatever shape the file was in, from here on
            # every list in it is a list of items.
            data = {k: normalize_block(str(k), v) for k, v in data.items()}
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
                    # Index what is already on disk by its text, so the parent's decisions
                    # about an item (its id, its pin, its use clock) survive re-learning it
                    # whichever way round the two lists are concatenated.
                    old = normalize_items(ns, str(key), old)
                    prior = {}
                    for item in old:
                        text = item_text(item)
                        if text is not None and isinstance(item, dict):
                            prior.setdefault(text.strip().lower(), item)
                    new = normalize_items(ns, str(key), safe, provenance=provenance)
                    merged = (new + old) if prepend_lists else (old + new)
                    seen, dedup = set(), []
                    for item in merged:
                        text = item_text(item)
                        key_of = text.strip().lower() if text is not None else repr(item)
                        if key_of in seen:
                            continue
                        seen.add(key_of)
                        was = prior.get(key_of)
                        if isinstance(item, dict) and isinstance(was, dict) and was is not item:
                            # Re-learning something already remembered must not reset it:
                            # keep the newest provenance and position, inherit the rest.
                            if was.get("id"):
                                item["id"] = was["id"]
                            if was.get("pinned"):
                                item["pinned"] = True
                            for carry in ("use_count", "last_used_at"):
                                if was.get(carry) and not item.get(carry):
                                    item[carry] = was[carry]
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
            data, dropped = prune_stale(data, max_age_days=memory_max_age_days(),
                                        now=time.time() if now is None else now)
            if dropped:
                print(f"[memory] decay: forgot {dropped} unused item(s) for {device_id}",
                      flush=True)
            self.store.write(device_id, self.collection, self._bound(data))
            return self.load(device_id).get(ns, {})

    # ---- per-item: what a parent does about one wrong line -------------------------
    # BEYOND #4's other half. Erasing a whole activity because one pronoun is wrong costs
    # everything Moxie learned about a child in that activity, so both of these work on a
    # single `id` and leave the rest of the namespace (and its `_meta.summarized_through`,
    # which is what stops the same transcript being re-summarized) exactly as it was.

    def find_item(self, device_id: str, namespace: str, item_id: str) -> tuple:
        """`(kind, index, item)` for one id in one namespace, or `(None, -1, None)`."""
        block = self.load(device_id).get(str(namespace))
        if not isinstance(block, dict):
            return None, -1, None
        for key, values in block.items():
            if str(key).startswith("_") or not isinstance(values, list):
                continue
            for idx, item in enumerate(normalize_items(str(namespace), str(key), values)):
                if isinstance(item, dict) and item.get("id") == str(item_id):
                    return str(key), idx, item
        return None, -1, None

    def erase_item(self, device_id: str, namespace: str, item_id: str) -> bool:
        """Forget exactly one remembered item. Never policy-gated, like every erase."""
        with self.store._lock:
            data = self.load(device_id)
            block = data.get(str(namespace))
            if not isinstance(block, dict):
                return False
            for key, values in list(block.items()):
                if str(key).startswith("_") or not isinstance(values, list):
                    continue
                items = normalize_items(str(namespace), str(key), values)
                kept = [i for i in items
                        if not (isinstance(i, dict) and i.get("id") == str(item_id))]
                if len(kept) != len(items):
                    block[key] = kept
                    data[str(namespace)] = block
                    self.store.write(device_id, self.collection, data)
                    return True
            return False

    def edit_item(self, device_id: str, namespace: str, item_id: str, text: str, *,
                  history=(), check=None, now=None) -> dict:
        """Correct one remembered item, keeping its id, and **pin** it.

        A parent correcting a fact is the most trustworthy write this store ever takes —
        so it is never policy-gated (a `NO_DATA` robot can still have a wrong line fixed
        rather than only deleted) and the result is pinned, which takes it out of decay
        for good. It is *not* unchecked: the new text goes through the same two rules a
        model's summary does — the safety classifier must not BLOCK it, and it must not
        be a long span of the child's own words — because a text box that writes straight
        into every future prompt is exactly the hole those rules exist to close.

        Raises `ValueError` when the item does not exist or the text is refused."""
        new_text = str(text or "").strip()[:MAX_MEMORY_ITEM_CHARS]
        if not new_text:
            raise ValueError("an empty memory is an erase, not an edit")
        if check is None:
            from .content.memory import check_text as check   # lazy: no import cycle
        if not check(new_text, history=history):
            raise ValueError("that text cannot be stored (safety or the child's own words)")
        with self.store._lock:
            data = self.load(device_id)
            block = data.get(str(namespace))
            if not isinstance(block, dict):
                raise ValueError(f"unknown namespace {namespace!r}")
            for key, values in list(block.items()):
                if str(key).startswith("_") or not isinstance(values, list):
                    continue
                items = normalize_items(str(namespace), str(key), values)
                for idx, item in enumerate(items):
                    if not (isinstance(item, dict) and item.get("id") == str(item_id)):
                        continue
                    item["text"] = new_text
                    item["pinned"] = True
                    item["edited_at"] = round(
                        float(time.time() if now is None else now), 3)
                    items[idx] = item
                    block[key] = items
                    data[str(namespace)] = block
                    self.store.write(device_id, self.collection, self._bound(data))
                    return item
            raise ValueError(f"unknown memory item {item_id!r}")

    def note_used(self, device_id: str, rendered: str, *, now=None) -> int:
        """Mark the items that appear in a rendered prompt as used. → how many.

        This is decay's whole clock. It is a **substring** test against the prompt the
        module actually rendered, which is honest but blunt: an item the prompt truncated,
        reworded, or handed to the model some other way is not counted, and an item that
        appears is not necessarily one the model used. Gated like every other write, so a
        `NO_DATA` robot's clocks simply stop (and nothing is pruned either, since pruning
        happens on merge, which is also gated)."""
        text = rendered if isinstance(rendered, str) else ""
        if not text or not device_id or not self.writes_allowed(device_id):
            return 0
        stamp = round(float(time.time() if now is None else now), 3)
        with self.store._lock:
            data = self.load(device_id)
            hits = 0
            for ns, block in data.items():
                if str(ns).startswith("_") or not isinstance(block, dict):
                    continue
                for key, values in list(block.items()):
                    if str(key).startswith("_") or not isinstance(values, list):
                        continue
                    items = normalize_items(str(ns), str(key), values)
                    for item in items:
                        body = item_text(item)
                        if not isinstance(item, dict) or not body or body not in text:
                            continue
                        item["use_count"] = int(item.get("use_count") or 0) + 1
                        item["last_used_at"] = stamp
                        hits += 1
                    block[key] = items
            if hits:
                self.store.write(device_id, self.collection, data)
            return hits

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
