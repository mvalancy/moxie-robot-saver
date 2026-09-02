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

    # ---- reads ----
    def read(self, device_id: str, collection: str, default=None):
        """Return the stored value, or `default` when nothing is stored (or the file is
        unreadable/corrupt — a store that raises on a bad file would take the robot's
        whole session down for one damaged record)."""
        try:
            with open(self.path(device_id, collection)) as fh:
                return json.load(fh)
        except (FileNotFoundError, NotADirectoryError):
            return default
        except (OSError, ValueError):
            return default

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
        path = self.path(device_id, collection)
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
        with self._lock:
            try:
                os.unlink(self.path(device_id, collection))
                return True
            except OSError:
                return False
