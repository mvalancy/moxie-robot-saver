"""
Durable per-robot store — plain JSON files on disk, zero dependencies.

The robot cloud needs to *remember* things between restarts: what activities a child
has finished (`mentor_behaviors`), what a conversation left behind (`memory`), why a day
was planned (`schedule_explain`), the safety journal, the content overlay — and, since
2026-09-02, **telemetry** (`telemetry_packets` + `telemetry_daily`, shaped by
`moxie_sdk/telemetry.py`), which was the last collection still living in process RAM
and dying with the supervisor.

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
    so a crash mid-write leaves the previous good file, never a truncated one — and, since
    2026-09-03, an `fsync` of the **directory** after the rename, so the directory entry
    pointing at the new inode is durable and not merely likely;
  * **thread-safe** — the runtime ingests reports on a worker pool, so read-modify-write
    (`append`) is serialized by a lock;
  * **process-safe** — see `transaction()` below;
  * **pure** — no MQTT, no protobuf, no config import; unit-testable on a tmp dir.

Cross-process writes (`docs/architecture/backlog/production-hardening.md` §3)
----------------------------------------------------------------------------
An in-process `threading.RLock` is not a lock at all once a second process appears, and a
second process is not hypothetical: `sim/run_smoke.sh` starts one on every contributor's
box, an operator's backup or hand-edit is another, and the console's child registry is a
third that is coming. Two `append()`s from two processes interleave read-read-write-write
and one item vanishes **silently**.

The fix is deliberately the small one: an **advisory `flock` on a per-record sidecar lock
file**, behind a public `transaction(device, collection)`, with the JSON staying exactly
where it is on disk. Not SQLite — the brief's §3.2 argues that at length, and the short
version is that SQLite's only real advantage here is multi-collection transactions, which
not one of the fourteen call sites uses. A parent can still `cat` and `rm` their child's
data, which is the most legible privacy property this appliance has.

Four things a plausible-looking `flock` patch gets wrong, all of them load-bearing:

1. **Lock a sidecar, never the data file.** `os.replace()` swaps the *inode*, so a lock
   held on `memory.json` is a lock on an inode the next writer will never open. The lock
   is `<path>.lock` — created once, never replaced, never deleted (deleting it
   re-introduces the same race), empty, and ignored by every reader.
2. **`RLock` outside, `flock` inside, one `open()` per acquisition.** `flock` is per *open
   file description*: two `open()`s in one thread deadlock each other where the old
   `RLock` was reentrant, and the five `MemoryStore` sites depend on that reentrancy. So
   the store-wide `RLock` is taken first and only the **outermost** acquisition opens an
   fd; a nested `transaction()` on the same record is a no-op re-entry.
3. **Never block the MQTT loop.** Some writes happen on the paho network thread, so the
   wait is `LOCK_EX | LOCK_NB` in a bounded exponential-backoff-with-jitter loop
   (`chat.py::call_with_backoff`'s shape, injectable `sleep`) capped by
   `MOXIE_STORE_LOCK_TIMEOUT_S` — **default 2.0 s, chosen rather than measured** (§9 A13).
   On exhaustion the write fails, returns False, and is *recorded*: never retried forever,
   never silently swallowed.
4. **`fcntl` is POSIX-only, and the fallback is loud.** Without it `transaction()` degrades
   to exactly the old `RLock` behaviour and `warn_no_locking()` prints one startup line.

What this does **not** buy: multi-collection atomicity, a query layer, schema/migrations —
§3.4 says so plainly. `/data` on NFS or SMB is **unsupported**: `flock` there is
best-effort at best (SQLite would be worse — WAL is flatly unsupported over NFS — so this
is a cost of the problem, not of the choice).
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

# Default data dir: mqtt/data/ (sibling of moxie_sdk/). Git-ignored — it is runtime
# state, not source. Override with MOXIE_DATA_DIR (e.g. a volume in the compose stack).
_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data")

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

#: Suffix of a record's advisory lock file. Never the data file itself — see the module
#: docstring, trap #1.
LOCK_SUFFIX = ".lock"

#: How long a writer may wait for another process's lock before giving up, in seconds.
#: **Chosen, not measured** (production-hardening.md §9 A13) — which is exactly why it is
#: an env var: a week of real contention on an appliance is what settles it. `config.py`
#: refuses to start when it is not strictly less than `MOXIE_BRAIN_BUDGET_S`, because a
#: lock wait is a slice of a turn rather than a claim on it.
DEFAULT_LOCK_TIMEOUT_S = 2.0

#: Backoff shape, mirroring `moxie_sdk/chat.py::call_with_backoff`: exponential from
#: `base`, capped at `cap`, plus uniform jitter.
#:
#: These two are **measured, not chosen** — unlike the timeout (A13). `flock` has no
#: queue: a `LOCK_NB` waiter takes whatever gap the holder leaves, so a process appending
#: in a tight loop *starves* a coarse poller. Measured on 2026-09-03, two processes ×
#: 500 `append`s on one collection, three cadences × two runs:
#:
#:   base 10 ms / cap 200 ms → 2-3 timeouts per writer, ~5 of 1 000 appends refused
#:   base 0.5 ms / cap  10 ms → 0-2 timeouts,           ~2 of 1 000 refused
#:   base 0.5 ms / cap   2 ms → 0 timeouts,              0 of 1 000 refused
#:
#: The contended case is another process finishing a ~1 ms write, so the poll interval has
#: to be on the order of that write rather than of an HTTP retry. It is still a *poll*:
#: fairness is not guaranteed and a starved waiter eventually times out — which is the
#: bounded, recorded failure §3.2 point 4 accepts, not a silent one.
LOCK_BACKOFF_BASE_S = 0.0005
LOCK_BACKOFF_CAP_S = 0.002
#: Largest exponent the backoff will compute. `2 ** attempt` is an arbitrary-precision
#: int and this loop iterates `timeout / cap` times — ~1 000 at the default 2.0 s budget
#: and ~15 000 at 30 s — so without a clamp `LOCK_BACKOFF_BASE_S * (2 ** attempt)` raises
#: `OverflowError` at `attempt == 1024` and takes the *caller* down rather than timing
#: out. The cap is already reached at `attempt == 2`, so this discards nothing.
LOCK_BACKOFF_MAX_SHIFT = 32

_NO_LOCKING_NOTE = (
    "⚠️  cross-process store locking is unavailable on this platform (no fcntl): two "
    "processes writing $MOXIE_DATA_DIR can still lose each other's updates. Linux and "
    "macOS are unaffected; see docs/architecture/backlog/production-hardening.md §3.3.")

_warned_no_locking = False


class StoreLockTimeout(Exception):
    """Another process held a record's lock for longer than the store was willing to wait.

    Raised only out of `JsonStore.transaction()`; the store's own writers turn it into a
    falsy return **and a recorded failure**, because a swallowed lock failure is the same
    class of bug as the publish whose return code nobody read.
    """


#: `refuses_on_lock`'s marker for a method whose caller expects an exception rather than a
#: falsy value (the parent-facing memory *edit*, whose handler turns a `ValueError` into a
#: 400 with the reason — a correction that silently did not save is worse than an error).
RAISE_INSTEAD = object()


def refuses_on_lock(what: str, fallback):
    """Turn a `StoreLockTimeout` out of one read-modify-write into that method's own
    *"nothing was stored"* answer, plus a printed line.

    The five `MemoryStore` read-modify-writes each already have such an answer — `merge`
    returns None when the policy drops a write, `erase` returns False when there was
    nothing to erase — so a refused lock reuses it rather than inventing a new failure
    shape a route would have to learn. What it must never do is escape into a turn as a
    traceback, or vanish: `JsonStore.lock_timeouts` counts every one (§5.3 A11).
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
    """Print `locking_note()` once per process. True if it printed.

    Called from `mqtt/run.py` at startup: a silent downgrade is how somebody ships a
    Windows appliance believing two processes are safe on one data directory.
    """
    global _warned_no_locking
    note = locking_note()
    if not note or _warned_no_locking:
        return False
    _warned_no_locking = True
    print(f"[store] {note}", flush=True)
    return True


def _fsync_dir(path: str) -> None:
    """`fsync` a directory so a rename into it is durable (A12).

    `os.replace` publishes the new inode; POSIX does not promise the *directory entry*
    survives a power cut until the directory itself is synced. ext4's `data=ordered` masks
    this in practice, which is why nobody has been bitten — but that is the filesystem
    being kind, not the code being correct.
    """
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

    Read here rather than imported from `config`, because this module's stated property is
    that it imports no config — and a knob the store cannot see is a knob that does
    nothing. `config.py` reads the same variable and is where the *guard* lives.
    """
    if explicit is not None:
        return float(explicit)
    try:
        return float(os.environ.get("MOXIE_STORE_LOCK_TIMEOUT_S") or DEFAULT_LOCK_TIMEOUT_S)
    except (TypeError, ValueError):
        return DEFAULT_LOCK_TIMEOUT_S


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

    def __init__(self, root: str | None = None, *, lock_timeout_s: float | None = None,
                 on_lock_timeout=None, sleep=time.sleep):
        self.root = root or data_dir()
        #: Store-wide, reentrant, in-process. Taken FIRST, always — see trap #2.
        self._lock = threading.RLock()
        #: Per-thread nesting depth per lock path, so a nested `transaction()` on one
        #: record re-enters instead of opening a second file description of the same lock
        #: file (which would block on itself forever).
        self._held = threading.local()
        self.lock_timeout_s = _lock_timeout(lock_timeout_s)
        #: `on_lock_timeout(lock_path, waited_s)` — the host's recorder. The runtime
        #: installs one that writes the runtime's `recent` ring, so a refused write is
        #: visible to an operator instead of being a number nobody reads.
        self.on_lock_timeout = on_lock_timeout
        self._sleep = sleep
        #: Observability for the failure this design newly makes possible (§5.3 A11).
        self.lock_timeouts = 0
        self.last_lock_error = ""

    # ---- paths ----
    def device_dir(self, device_id: str) -> str:
        return os.path.join(self.root, "robots", safe_name(device_id))

    def lock_path(self, path: str) -> str:
        """The advisory lock **sidecar** for a record path.

        A sidecar rather than the data file itself because `os.replace` swaps the inode
        (trap #1): a lock on `memory.json` is a lock on something the next writer will
        never open, so it looks correct and serializes nothing.
        """
        return path + LOCK_SUFFIX

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

    # ---- the cross-process lock ----
    def _depths(self) -> dict:
        """This thread's `{lock_path: depth}` — see trap #2."""
        depths = getattr(self._held, "depths", None)
        if depths is None:
            depths = self._held.depths = {}
        return depths

    def _acquire_flock(self, fd) -> bool:
        """One non-blocking `LOCK_EX` attempt. True when we got it.

        Non-blocking on purpose: some store writes happen on the paho network thread, and
        a blocking `flock` there stalls every robot on the appliance until whoever is
        wedged lets go. A seam of its own so a test can make the lock unobtainable
        without needing a second process.
        """
        if fcntl is None:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _wait_flock(self, fd, lock_path: str) -> float | None:
        """Retry `_acquire_flock` with backoff until `lock_timeout_s` is spent.

        Returns None once it is held, else the seconds waited. The budget is counted in
        *requested* sleep — not in wall clock — so an injected `sleep` (the
        `test_clock_dependence.py` ratchet: a test never reads a clock) terminates exactly
        the way the real one does.
        """
        if self._acquire_flock(fd):
            return None
        started = time.monotonic()
        asked = 0.0
        attempt = 0
        while asked < self.lock_timeout_s or True:
            # `2 ** attempt` is an arbitrary-precision **int**, and this loop runs until
            # the budget is spent — roughly `timeout / LOCK_BACKOFF_CAP_S` times. At
            # `attempt == 1024` the product overflows a float and raises
            # `OverflowError: int too large to convert to float`, straight out of
            # `transaction()`, past `append`'s `except StoreLockTimeout`, into the caller.
            #
            # The default budget hides it by 24 polls: 2.0 s / 2 ms = ~1000. **Any** larger
            # value crosses the cliff — 5 s is ~2 500 polls, 30 s is ~15 000 — and
            # `config.py` positively invites larger ones, since the only bound it enforces
            # is `< MOXIE_BRAIN_BUDGET_S`. So a contended writer under a raised timeout
            # crashed instead of waiting, and did it rarely enough to read as a flake:
            # found 2026-09-03 as a 1-in-12 failure of `test_t1` (which uses 30 s
            # deliberately) under load, and it is very likely the unexplained lost append
            # in the handed-down "999 of 1 000 at 30 s" measurement.
            #
            # The clamp costs nothing: the cap is reached at `attempt == 2`
            # (0.0005 × 4 = 0.002), so every exponent past a handful is already discarded
            # by the `min`. It is 32 rather than 3 only so the shape stays recognisably
            # "exponential, capped" to the next reader.
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

        The public seam the five `MemoryStore` read-modify-writes use instead of reaching
        into `self.store._lock`. Reentrant on the same `(device, collection)` from the
        same thread; raises `StoreLockTimeout` when another **process** has held the
        record for longer than `lock_timeout_s`::

            with store.transaction(device_id, "memory"):
                data = store.read(device_id, "memory", {})
                data["quiz"] = ...
                store.write(device_id, "memory", data)

        Readers need no transaction: `os.replace` already gives them a whole old or a
        whole new record.
        """
        return self._transaction_path(self.path(device_id, collection))

    def transaction_shared(self, collection: str):
        """`transaction()` for the fleet tier (`fleet/<collection>.json`).

        The tier two processes are likeliest to fight over — `config`, `permits`, `voice`,
        the content overlay — because it is not partitioned by device.
        """
        return self._transaction_path(self.shared_path(collection))

    # ---- writes ----
    def write(self, device_id: str, collection: str, value) -> bool:
        """Store `value` (any JSON-serializable object). Returns True on success.

        False also means *"another process held this record and would not let go"* — a
        refused write, recorded in `lock_timeouts` / `last_lock_error`, never a partial one.
        """
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
        """Append one item to a stored list and return the new list, or **None** when
        another process would not release the record.

        `cap` keeps only the newest `cap` items (the store is a rolling history, not an
        archive). Read-modify-write inside `transaction()`, which is what makes it safe
        across processes — before that fix two appenders lost one item per collision, with
        no error anywhere.
        """
        return self._append_path(self.path(device_id, collection), item, cap=cap)

    def append_shared(self, collection: str, item, *, cap: int | None = None):
        """`append()` for the fleet tier (`fleet/<collection>.json`).

        The tier the appliance's own history lives in — the connection ring
        (`conn_telemetry.py`) is appliance-wide because there is one socket, not one per
        robot, and two supervisors on one data directory must not lose each other's rows
        for exactly the reason §3 gives about `safety_events`.
        """
        return self._append_path(self.shared_path(collection), item, cap=cap)

    def _append_path(self, path: str, item, *, cap: int | None = None):
        """Append over an already-resolved record path. None = refused or not written.

        **The write's return value is checked**, and that is a fix rather than a tidy-up.
        This method used to call `write()` and return `items` regardless, so an `OSError`
        — a full disk, a read-only `/data`, a permission change — produced a *successful*
        append of an item that reached no file. That is the same disease as the eight
        publishes whose `info.rc` nobody read (§4.1 C5) and the CONNACK that logged
        "connected" for a refusal (C3): a comfortable lie at the one boundary that knows
        the truth. It also breaks the identity the soak's contention probe is built on —
        `attempted == on_disk + refused` — which is what makes a *silent* loss
        distinguishable from a *recorded* refusal at all (§5.3 A5 vs A11).
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
        """Remove the record. The `.lock` sidecar is deliberately left behind: deleting it
        re-introduces the inode race it exists to prevent (two processes each create their
        own and lock different inodes). It is an empty file."""
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

    # ---- the record lock ----
    def _record(self, device_id: str):
        """Hold this robot's memory record for a read-modify-write.

        Was `with self.store._lock:` — an in-process `RLock` reached into from outside the
        class, which serialized nothing against a second process. Now the store's public
        `transaction()`, which is why that method exists at all
        (production-hardening.md §2.1: *"any fix must be reachable from here"*).
        """
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

    @refuses_on_lock("merge", None)
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
        with self._record(device_id):                     # read-modify-write, across processes
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
        """Forget one namespace, or (namespace None/"all") everything for this robot.

        Erasure is **never** policy-gated: a parent must always be able to delete."""
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
