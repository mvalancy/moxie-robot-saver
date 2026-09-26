"""
Durable per-robot store — plain JSON files on disk, zero dependencies.

Holds what the robot cloud must remember across restarts: mentor behaviors, memory, the
schedule explanation, the safety journal, the content overlay and telemetry. One JSON
file per (robot, collection), written atomically. **A stepping stone, not a database**
(audit ADOPT #8, `docs/architecture/openmoxie-feature-audit.md` §4.1): the API
(read / write / append / delete / devices) is deliberately narrow so it can be
re-implemented over SQLite without touching a caller.

Layout::

    $MOXIE_DATA_DIR/robots/<device>/<collection>.json     # default: mqtt/data/
    $MOXIE_DATA_DIR/fleet/<collection>.json               # appliance-wide, no device

Properties relied on:
  * **robust to a missing directory** — reads return the default, writes create it;
  * **atomic writes** — temp file + `os.replace`, then `fsync` of the directory so the
    rename itself is durable; a crash leaves the previous good file, never a truncated one;
  * **thread-safe** and **process-safe** — see `transaction()`;
  * **pure** — no MQTT, no protobuf, no config import; unit-testable on a tmp dir.

Cross-process writes (`docs/architecture/backlog/production-hardening.md` §3)
----------------------------------------------------------------------------
A second process on the data directory is normal (the SIL harness, an operator's backup
or hand-edit), and two unlocked `append()`s interleave read-read-write-write and silently
lose an item. The fix is an **advisory `flock` on a per-record sidecar lock file** behind
a public `transaction(device, collection)`, with the JSON staying `cat`-able and `rm`-able
by a parent. Not SQLite: its only real advantage here, multi-collection transactions, is
used by no caller (§3.2).

Four traps, all load-bearing:

1. **Lock a sidecar, never the data file.** `os.replace()` swaps the inode, so a lock on
   `memory.json` is a lock on an inode the next writer never opens. The lock is
   `<path>.lock` — created once, never replaced or deleted, empty, ignored by readers.
2. **`RLock` outside, `flock` inside, one `open()` per acquisition.** `flock` is per open
   file description, so two `open()`s in one thread deadlock; `MemoryStore` depends on
   reentrancy. Only the outermost acquisition opens an fd; nesting is a no-op re-entry.
3. **Never block the MQTT loop.** Some writes run on the paho thread, so the wait is
   `LOCK_EX | LOCK_NB` with bounded backoff + jitter, capped by
   `MOXIE_STORE_LOCK_TIMEOUT_S` (default 2.0 s, chosen not measured — §9 A13). On
   exhaustion the write fails, returns False, and is recorded.
4. **`fcntl` is POSIX-only, and the fallback is loud.** Without it `transaction()` is the
   `RLock` alone and `warn_no_locking()` prints one startup line.

Not provided: multi-collection atomicity, a query layer, schema/migrations (§3.4).
`/data` on NFS/SMB is unsupported (`flock` there is best-effort).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import threading
import time

try:                                   # POSIX only; Windows degrades to the RLock alone
    import fcntl
except ImportError:                    # pragma: no cover - not reachable on Linux CI
    fcntl = None                       # type: ignore[assignment]

# The item model lives in memory_items.py; re-exported here for existing callers.
from .memory_items import (  # noqa: F401
    MEMORY_COLLECTION, POLICY_NO_DATA, MAX_MEMORY_NAMESPACES, MAX_MEMORY_ITEMS,
    MAX_MEMORY_ITEM_CHARS, MAX_MEMORY_BYTES, MEMORY_ID_BYTES, MEMORY_MAX_AGE_DAYS,
    ITEM_PROVENANCE_KEYS, memory_max_age_days, item_id, item_text, _unique_id,
    item_provenance, make_item, normalize_items, normalize_block, item_clock,
    prune_stale, _policy_value, json_safe)

# Default data dir: mqtt/data/ (git-ignored runtime state). Override with MOXIE_DATA_DIR.
_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data")

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

#: Suffix of a record's advisory lock file (trap #1).
LOCK_SUFFIX = ".lock"

#: Seconds a writer waits for another process's lock. Chosen, not measured (§9 A13), hence
#: an env var. `config.py` requires it to be strictly less than `MOXIE_BRAIN_BUDGET_S`.
DEFAULT_LOCK_TIMEOUT_S = 2.0

#: Backoff: exponential from `base`, capped at `cap`, plus uniform jitter. Measured, not
#: chosen: `flock` has no queue, so a coarse poller is starved by a writer in a tight loop.
#: Two processes x 500 appends: 10 ms/200 ms refused ~5 of 1 000, 0.5 ms/2 ms refused 0.
#: The poll must be on the order of a ~1 ms write, not of an HTTP retry.
LOCK_BACKOFF_BASE_S = 0.0005
LOCK_BACKOFF_CAP_S = 0.002
#: Clamp on the backoff exponent. `2 ** attempt` is an int and the loop runs
#: `timeout / cap` times, so unclamped it raises `OverflowError` at attempt 1024 (reached
#: by any timeout above ~2 s). The cap is hit at attempt 2, so the clamp discards nothing.
LOCK_BACKOFF_MAX_SHIFT = 32

_NO_LOCKING_NOTE = (
    "⚠️  cross-process store locking is unavailable on this platform (no fcntl): two "
    "processes writing $MOXIE_DATA_DIR can still lose each other's updates. Linux and "
    "macOS are unaffected; see docs/architecture/backlog/production-hardening.md §3.3.")

_warned_no_locking = False


class StoreLockTimeout(Exception):
    """Another process held a record's lock longer than the store would wait.

    Raised only out of `JsonStore.transaction()`; the store's own writers turn it into a
    falsy return **and a recorded failure** — never a silent one.
    """


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
    import functools

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


def locking_note() -> str:
    """The one-line warning for a platform with no `fcntl`, or `""` on POSIX."""
    return "" if fcntl is not None else _NO_LOCKING_NOTE


def warn_no_locking() -> bool:
    """Print `locking_note()` once per process (from `mqtt/run.py`). True if it printed —
    a silent downgrade would let a Windows appliance believe two processes are safe."""
    global _warned_no_locking
    note = locking_note()
    if not note or _warned_no_locking:
        return False
    _warned_no_locking = True
    print(f"[store] {note}", flush=True)
    return True


def _fsync_dir(path: str) -> None:
    """`fsync` a directory so a rename into it is durable (A12): POSIX does not promise the
    directory entry survives a power cut until the directory itself is synced."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def data_dir() -> str:
    """The configured data directory (`MOXIE_DATA_DIR`, else `mqtt/data`)."""
    return os.environ.get("MOXIE_DATA_DIR", "").strip() or _DEFAULT_DIR


def _lock_timeout(explicit: float | None = None) -> float:
    """The lock budget: an explicit argument, else `MOXIE_STORE_LOCK_TIMEOUT_S`, else 2.0.
    Read here (not from `config`) because this module imports no config; `config.py` owns
    the guard on the same variable."""
    if explicit is not None:
        return float(explicit)
    try:
        return float(os.environ.get("MOXIE_STORE_LOCK_TIMEOUT_S") or DEFAULT_LOCK_TIMEOUT_S)
    except (TypeError, ValueError):
        return DEFAULT_LOCK_TIMEOUT_S


def safe_name(value: str) -> str:
    """A filesystem-safe directory name for an arbitrary device id.

    Robot ids (`d_<uuid>`) are already safe, but the store must never be a path-traversal
    lever for an id off the wire. Unsafe characters are replaced and a short digest of the
    original appended so two ids never collide on one directory.
    """
    value = str(value or "")
    cleaned = "".join(c if c in _SAFE else "_" for c in value).strip(".") or "_"
    if cleaned != value:
        cleaned = f"{cleaned[:48]}-{hashlib.sha256(value.encode()).hexdigest()[:8]}"
    return cleaned


class JsonStore:
    """A tiny per-robot JSON store. One file per (device_id, collection)."""

    def __init__(self, root: str | None = None, *, lock_timeout_s: float | None = None,
                 on_lock_timeout=None, sleep=time.sleep):
        self.root = root or data_dir()
        #: Store-wide, reentrant, in-process. Taken FIRST, always (trap #2).
        self._lock = threading.RLock()
        #: Per-thread nesting depth per lock path, so a nested `transaction()` re-enters
        #: instead of opening a second description of the same lock file.
        self._held = threading.local()
        self.lock_timeout_s = _lock_timeout(lock_timeout_s)
        #: `on_lock_timeout(lock_path, waited_s)` — the host's recorder (the runtime's
        #: `recent` ring), so a refused write is visible to an operator.
        self.on_lock_timeout = on_lock_timeout
        self._sleep = sleep
        self.lock_timeouts = 0
        self.last_lock_error = ""

    # ---- paths ----
    def device_dir(self, device_id: str) -> str:
        return os.path.join(self.root, "robots", safe_name(device_id))

    def lock_path(self, path: str) -> str:
        """The advisory lock **sidecar** for a record path (trap #1)."""
        return path + LOCK_SUFFIX

    def path(self, device_id: str, collection: str) -> str:
        return os.path.join(self.device_dir(device_id), f"{safe_name(collection)}.json")

    def shared_path(self, collection: str) -> str:
        """Path of a **fleet-wide** record (`fleet/<collection>.json`). Never under
        `robots/`, so it can never collide with a device id."""
        return os.path.join(self.root, "fleet", f"{safe_name(collection)}.json")

    # ---- reads ----
    def _read_path(self, path: str, default=None):
        """Read one JSON file, or `default` when missing/unreadable/corrupt — one damaged
        record must not take a robot's session down."""
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

    # ---- the cross-process lock ----
    def _depths(self) -> dict:
        """This thread's `{lock_path: depth}` (trap #2)."""
        depths = getattr(self._held, "depths", None)
        if depths is None:
            depths = self._held.depths = {}
        return depths

    def _acquire_flock(self, fd) -> bool:
        """One non-blocking `LOCK_EX` attempt (trap #3). A seam so a test can make the
        lock unobtainable without a second process."""
        if fcntl is None:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _wait_flock(self, fd, lock_path: str) -> float | None:
        """Retry `_acquire_flock` with backoff until `lock_timeout_s` is spent.

        None once held, else the seconds waited. The budget counts *requested* sleep, not
        wall clock, so an injected `sleep` terminates exactly like the real one.
        """
        if self._acquire_flock(fd):
            return None
        started = time.monotonic()
        asked = 0.0
        attempt = 0
        while asked < self.lock_timeout_s:
            # The exponent clamp prevents OverflowError past ~1024 polls (see
            # LOCK_BACKOFF_MAX_SHIFT); it discards nothing since the cap is hit at 2.
            delay = min(LOCK_BACKOFF_CAP_S,
                        LOCK_BACKOFF_BASE_S * (2 ** min(attempt, LOCK_BACKOFF_MAX_SHIFT)))
            delay += random.uniform(0, LOCK_BACKOFF_BASE_S)
            delay = min(delay, self.lock_timeout_s - asked)
            self._sleep(delay)
            asked += delay
            attempt += 1
            if self._acquire_flock(fd):
                return None
        return max(time.monotonic() - started, asked)

    def _note_lock_timeout(self, lock_path: str, waited: float) -> None:
        self.lock_timeouts += 1
        self.last_lock_error = (
            f"store lock busy after {waited:.2f}s: {os.path.basename(lock_path)}")
        if self.on_lock_timeout is not None:
            try:
                self.on_lock_timeout(lock_path, waited)
            except Exception:                  # a broken recorder must not lose the write
                pass

    @contextlib.contextmanager
    def _transaction_path(self, path: str):
        """`transaction()` over an already-resolved record path."""
        lock_path = self.lock_path(path)
        self._lock.acquire()                   # RLock OUTSIDE, always (trap #2)
        depths = self._depths()
        if depths.get(lock_path):              # nested on the same record, same thread
            depths[lock_path] += 1
            try:
                yield self
            finally:
                depths[lock_path] -= 1
                self._lock.release()
            return
        fd = None
        try:
            if fcntl is not None:
                try:
                    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
                    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
                except OSError:
                    fd = None                  # unwritable tree: the write will fail too
                if fd is not None:
                    waited = self._wait_flock(fd, lock_path)
                    if waited is not None:
                        self._note_lock_timeout(lock_path, waited)
                        raise StoreLockTimeout(self.last_lock_error)
            depths[lock_path] = 1
            try:
                yield self
            finally:
                depths.pop(lock_path, None)
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)                   # the fd is closed on EVERY path (§5.3 A8)
            self._lock.release()

    def transaction(self, device_id: str, collection: str):
        """Hold one record against every other writer, in this process and outside it.

        Reentrant on the same `(device, collection)` from the same thread; raises
        `StoreLockTimeout` when another **process** holds it past `lock_timeout_s`::

            with store.transaction(device_id, "memory"):
                data = store.read(device_id, "memory", {})
                data["quiz"] = ...
                store.write(device_id, "memory", data)

        Readers need no transaction: `os.replace` gives them a whole old or whole new record.
        """
        return self._transaction_path(self.path(device_id, collection))

    def transaction_shared(self, collection: str):
        """`transaction()` for the fleet tier (`fleet/<collection>.json`) — the tier two
        processes are likeliest to fight over, since it is not partitioned by device."""
        return self._transaction_path(self.shared_path(collection))

    # ---- writes ----
    def write(self, device_id: str, collection: str, value) -> bool:
        """Store `value` (any JSON-serializable object). True on success; False also means
        another process would not release the record (recorded, never partial)."""
        return self._locked_write(self.path(device_id, collection), value)

    def write_shared(self, collection: str, value) -> bool:
        """Store a fleet-wide `value` (`fleet/<collection>.json`). True on success."""
        return self._locked_write(self.shared_path(collection), value)

    def _locked_write(self, path: str, value) -> bool:
        try:
            with self._transaction_path(path):
                return self._write_path(path, value)
        except StoreLockTimeout:
            return False

    def _write_path(self, path: str, value) -> bool:
        with self._lock:
            try:
                directory = os.path.dirname(path)
                os.makedirs(directory, exist_ok=True)
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w") as fh:
                    json.dump(value, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)          # atomic on POSIX; readers see old or new
            except (OSError, TypeError, ValueError):
                try:
                    os.unlink(tmp)             # never leave a half-written temp behind
                except (OSError, UnboundLocalError, NameError):
                    pass
                return False
            try:
                _fsync_dir(directory)          # the rename itself, made durable (A12)
            except OSError:
                pass                           # some filesystems refuse; the data is written
            return True

    def append(self, device_id: str, collection: str, item, *, cap: int | None = None):
        """Append one item to a stored list; return the new list, or **None** when the
        write was refused or failed. `cap` keeps only the newest `cap` items."""
        return self._append_path(self.path(device_id, collection), item, cap=cap)

    def append_shared(self, collection: str, item, *, cap: int | None = None):
        """`append()` for the fleet tier (e.g. the appliance-wide connection ring)."""
        return self._append_path(self.shared_path(collection), item, cap=cap)

    def _append_path(self, path: str, item, *, cap: int | None = None):
        """Append over a resolved record path. None = refused or not written.

        The write's return value is checked: a full disk or read-only `/data` must not
        report a successful append of an item that reached no file. The soak's contention
        probe relies on `attempted == on_disk + refused` (§5.3 A5 vs A11).
        """
        try:
            with self._transaction_path(path):
                items = self._read_path(path, [])
                if not isinstance(items, list):
                    items = []
                items.append(item)
                if cap is not None and cap >= 0 and len(items) > cap:
                    del items[: len(items) - cap]
                if not self._write_path(path, items):
                    return None
                return items
        except StoreLockTimeout:
            return None

    def delete(self, device_id: str, collection: str) -> bool:
        """Remove one collection. True if a file was removed."""
        return self._locked_delete(self.path(device_id, collection))

    def delete_shared(self, collection: str) -> bool:
        """Remove one fleet-wide collection. True if a file was removed."""
        return self._locked_delete(self.shared_path(collection))

    def _locked_delete(self, path: str) -> bool:
        try:
            with self._transaction_path(path):
                return self._delete_path(path)
        except StoreLockTimeout:
            return False

    def _delete_path(self, path: str) -> bool:
        """Remove the record. The `.lock` sidecar is deliberately kept: deleting it
        re-opens the inode race (trap #1)."""
        with self._lock:
            try:
                os.unlink(path)
                return True
            except OSError:
                return False


# ---------------------------------------------------------------------------
# Long-term memory — `persist_data` (content-module-contract.md → volley/session API)
# ---------------------------------------------------------------------------
# One `memory.json` per robot: a dict of **namespaces** (one per content module), each
# holding durable facts plus the provenance of how they got there. Non-negotiable on a
# child's device: **bounded** (caps on namespaces, items, bytes), **erasable** (`erase()`,
# exposed as `DELETE /memory`), and **policy-gated** (`LoggingPolicy.NO_DATA` = nothing
# written; reads still work so a parent can inspect and erase). NO_DATA is compared by
# value (0) to keep this module free of a config import.
#
# Credit: module-namespaced durable facts are OpenMoxie's (MIT) `MemoryChat.json`
# `complete_handler`; per-item provenance is from OpenMoxie Fork A's
# `conversation_memory.py` (openmoxie-feature-audit.md §3.2, §4.2 BEYOND #4). This code,
# the caps, the policy gate and the JSON shape are ours.

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

    # ---- the record lock ----
    def _record(self, device_id: str):
        """Hold this robot's memory record for a read-modify-write (across processes)."""
        return self.store.transaction(device_id, self.collection)

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
        # Total size last: drop whole trailing namespaces until it fits.
        while len(json.dumps(safe)) > self.max_bytes and safe:
            safe.pop(list(safe)[-1])
        return safe

    def save(self, device_id: str, data: dict) -> bool:
        """Replace this robot's memory. Returns False when the policy dropped the write."""
        if not self.writes_allowed(device_id):
            return False
        return self.store.write(device_id, self.collection, self._bound(dict(data or {})))

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
        with self._record(device_id):                     # read-modify-write, across processes
            data = self.load(device_id)
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
                    # Index what is on disk by text, so an item's id, pin and use clock
                    # survive re-learning it.
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
                            # Re-learning must not reset it: keep the newest provenance
                            # and position, inherit the rest.
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
    # Both work on a single `id` and leave the rest of the namespace (incl.
    # `_meta.summarized_through`) untouched (BEYOND #4).

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

    @refuses_on_lock("erase_item", False)
    def erase_item(self, device_id: str, namespace: str, item_id: str) -> bool:
        """Forget exactly one remembered item. Never policy-gated, like every erase."""
        with self._record(device_id):
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

    @refuses_on_lock("note_used", 0)
    def note_used(self, device_id: str, rendered: str, *, now=None) -> int:
        """Mark the items that appear in a rendered prompt as used. → how many.

        Decay's whole clock: a blunt **substring** test against the rendered prompt (a
        reworded or truncated item is not counted). Policy-gated like every write."""
        text = rendered if isinstance(rendered, str) else ""
        if not text or not device_id or not self.writes_allowed(device_id):
            return 0
        stamp = round(float(time.time() if now is None else now), 3)
        with self._record(device_id):
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

    @refuses_on_lock("erase", False)
    def erase(self, device_id: str, namespace: str | None = None) -> bool:
        """Forget one namespace, or (None/"all") everything for this robot.
        **Never** policy-gated: a parent must always be able to delete."""
        with self._record(device_id):
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
