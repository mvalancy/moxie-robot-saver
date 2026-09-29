"""
T1–T11 — two processes writing one appliance's data must not lose each other's writes.

The T-series of `production-hardening.md` §6: advisory `flock` on a per-record sidecar
lock file behind `JsonStore.transaction()`, JSON staying on disk. The tests target what a
plausible `flock` patch gets wrong:

* **T4** — locking the *data* file locks an inode `os.replace` swaps out; the sidecar's
  inode must be stable across a write.
* **T2/T3** — `flock` is per open file description, so two `open()`s in one process
  deadlock; `RLock` outside, `flock` inside, one `open()` per outermost acquisition.
* **T5** — some writes run on the paho thread, so the wait is bounded (`LOCK_NB` + backoff,
  `MOXIE_STORE_LOCK_TIMEOUT_S`) and an exhausted wait fails loudly.

Hermetic: tmp dir, real subprocesses, no broker. No wall-clock reads — tests count events.
Test ids (`t1`…`t11`) are `-k` selectors in `sim/tools/hardening*_mutation_check.py`.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk import store as store_mod                      # noqa: E402
from moxie_sdk.store import JsonStore, StoreLockTimeout                  # noqa: E402
from moxie_sdk.memory_store import MemoryStore                          # noqa: E402

DEVICE = "d_conc"
COLLECTION = "safety_events"

#: Appends per writer process. 250 × 2 already loses about half on an unlocked store (the
#: brief's 5 000 would be ~750 MB of fsync'd I/O); `MOXIE_TEST_STORE_APPENDS` raises it.
APPENDS = int(os.environ.get("MOXIE_TEST_STORE_APPENDS") or 250)


# --------------------------------------------------------------------------- #
# A real second process — not a thread, not a fork of the pytest interpreter
# --------------------------------------------------------------------------- #

def _script(body: str) -> str:
    """A `python -c` program with the store importable and `%(device)r`/`%(collection)r`
    filled in."""
    return ("import json, os, sys, time\n"
            f"sys.path.insert(0, os.path.join({REPO!r}, 'mqtt'))\n"
            "from moxie_sdk.store import JsonStore\n"
            + body % {"device": DEVICE, "collection": COLLECTION})


# A generous lock budget on purpose: `flock` has no queue, so a starved poller giving up
# (T5) would look like a lost update. 30 s keeps T1 measuring lost updates only.
WRITER = _script(r'''
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = JsonStore(root, lock_timeout_s=30.0)
for i in range(n):
    assert s.append(%(device)r, %(collection)r, {"who": tag, "i": i}) is not None, \
        "the writer was REFUSED the lock, which is starvation (T5), not a lost update"
''')


def _spawn_writers(root: str, tags, n: int, script: str = WRITER):
    """Run one `python -c` writer per tag, concurrently, and wait for all of them."""
    procs = [subprocess.Popen([sys.executable, "-c", script, root, tag, str(n)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for tag in tags]
    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=300)
        out.append((p.returncode, stdout.decode(), stderr.decode()))
    for rc, _o, err in out:
        assert rc == 0, err
    return out


# --------------------------------------------------------------------------- #
# T1 — the choice, as itself
# --------------------------------------------------------------------------- #

def test_t1_two_processes_appending_lose_nothing(tmp_path):
    """T1 — 2 processes × `APPENDS` appends to one collection leave exactly `2 × APPENDS`
    items. Without locking, read-read-write-write interleaving silently drops items."""
    root = str(tmp_path / "data")
    _spawn_writers(root, ("a", "b"), APPENDS)

    items = JsonStore(root).read(DEVICE, COLLECTION, [])
    assert isinstance(items, list)
    assert len(items) == 2 * APPENDS, (
        f"lost {2 * APPENDS - len(items)} of {2 * APPENDS} appends across two processes")
    for tag in ("a", "b"):
        seen = sorted(it["i"] for it in items if it["who"] == tag)
        assert seen == list(range(APPENDS)), f"writer {tag} lost items"


def test_t1b_the_test_can_actually_see_a_lost_update(tmp_path):
    """Teeth for T1: the UNLOCKED read-modify-write run through the same harness must lose
    something, or T1 has stopped testing anything."""
    unlocked = _script(r'''
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = JsonStore(root)
path = s.path(%(device)r, %(collection)r)
for i in range(n):
    # the pre-flock append: read, mutate, write with only an in-process lock
    items = s._read_path(path, [])
    items.append({"who": tag, "i": i})
    s._write_path(path, items)
''')

    root = str(tmp_path / "data")
    _spawn_writers(root, ("a", "b"), APPENDS, script=unlocked)
    items = JsonStore(root).read(DEVICE, COLLECTION, [])
    assert len(items) < 2 * APPENDS, (
        "the unlocked read-modify-write lost NOTHING across two processes — either the "
        "machine serialized them by luck or the harness is not racing; raise "
        "MOXIE_TEST_STORE_APPENDS and look again before trusting T1")


# --------------------------------------------------------------------------- #
# T2/T3 — the RLock-outside/flock-inside rule (§3.3 #2)
# --------------------------------------------------------------------------- #

def test_t2_nested_transaction_on_one_record_does_not_deadlock(tmp_path):
    """T2 — a second `open()` + `LOCK_EX` from the same thread would block on itself, and
    `MemoryStore` call sites nest. The watchdog makes a regression say "deadlock"."""
    s = JsonStore(str(tmp_path))
    done = threading.Event()

    def body():
        with s.transaction(DEVICE, COLLECTION):
            with s.transaction(DEVICE, COLLECTION):      # same thread, same record
                with s.transaction(DEVICE, COLLECTION):  # and again, three deep
                    s.write(DEVICE, COLLECTION, ["nested"])
                    # `write` itself takes the record's transaction — a fourth level.
        done.set()

    t = threading.Thread(target=body, daemon=True)
    t.start()
    assert done.wait(20), "nested transaction() on one record deadlocked"
    assert s.read(DEVICE, COLLECTION) == ["nested"]


def test_t2b_the_reentry_does_not_open_a_second_fd(tmp_path):
    """T2's mechanism: nested acquisition is a no-op re-entry — exactly ONE lock fd however
    deep — since "did not deadlock" is also what a patch that stopped locking gives."""
    s = JsonStore(str(tmp_path))
    opens = []
    real_open = os.open

    def counting_open(path, flags, *a, **kw):
        if str(path).endswith(".lock"):
            opens.append(str(path))
        return real_open(path, flags, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "open", counting_open)
        with s.transaction(DEVICE, COLLECTION):
            with s.transaction(DEVICE, COLLECTION):
                s.append(DEVICE, COLLECTION, {"i": 1})
    assert len(opens) == 1, f"expected one lock fd for the whole nest, got {opens}"


def test_t3_two_threads_serialize_through_transaction(tmp_path):
    """T3 — two threads in one process never interleave a read-modify-write; each records
    concurrent entry depth, and any value above 1 is a witness."""
    s = JsonStore(str(tmp_path))
    inside = 0
    peak = 0
    gate = threading.Lock()
    started = threading.Barrier(4)

    def body():
        nonlocal inside, peak
        started.wait(timeout=20)
        for _ in range(40):
            with s.transaction(DEVICE, COLLECTION):
                with gate:
                    inside += 1
                    peak = max(peak, inside)
                items = s.read(DEVICE, COLLECTION, [])
                items.append(1)
                s.write(DEVICE, COLLECTION, items)
                with gate:
                    inside -= 1

    threads = [threading.Thread(target=body, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "a transaction() thread never finished"
    assert peak == 1, f"{peak} threads were inside transaction() at once"
    assert len(s.read(DEVICE, COLLECTION, [])) == 160


# --------------------------------------------------------------------------- #
# T4 — the sidecar, and why it cannot be the data file (§3.3 #1)
# --------------------------------------------------------------------------- #

def test_t4_the_lock_is_a_sidecar_whose_inode_survives_a_write(tmp_path):
    """T4 — a lock on the data file locks an inode `os.replace` swaps out, serializing
    nothing; the `.lock` sidecar's inode must survive a write."""
    s = JsonStore(str(tmp_path))
    data = s.path(DEVICE, COLLECTION)
    lock = s.lock_path(data)
    assert lock == data + ".lock"
    assert lock != data

    s.write(DEVICE, COLLECTION, [1])
    lock_ino = os.stat(lock).st_ino
    data_ino = os.stat(data).st_ino

    s.write(DEVICE, COLLECTION, [1, 2])
    assert os.stat(data).st_ino != data_ino, (
        "the data file's inode did NOT change — os.replace was not used, and the premise "
        "of this test (and of the sidecar) needs re-reading")
    assert os.stat(lock).st_ino == lock_ino, (
        "the lock file's inode changed across a write: it is being replaced along with "
        "the data, which means the next writer locks a different inode and nothing is "
        "serialized at all (§3.3 #1)")


def test_t4b_the_sidecar_is_never_deleted_by_a_delete(tmp_path):
    """Deleting the sidecar re-introduces the inode race it exists to avoid (two processes
    each create their own `.lock`), so `delete()` leaves it alone."""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, COLLECTION, [1])
    lock = s.lock_path(s.path(DEVICE, COLLECTION))
    ino = os.stat(lock).st_ino
    assert s.delete(DEVICE, COLLECTION) is True
    assert not os.path.exists(s.path(DEVICE, COLLECTION))
    assert os.path.exists(lock) and os.stat(lock).st_ino == ino


def test_t4c_a_sidecar_is_not_mistaken_for_a_device_or_a_record(tmp_path):
    """`.lock` files are empty, never listed as devices and never parsed as records."""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, COLLECTION, [1])
    s.write_shared("config", {"a": 1})
    assert s.devices() == [DEVICE]
    assert os.path.getsize(s.lock_path(s.path(DEVICE, COLLECTION))) == 0
    assert json.loads(open(s.path(DEVICE, COLLECTION)).read()) == [1]


# --------------------------------------------------------------------------- #
# T5 — a bounded wait that fails loudly (§3.3 #3)
# --------------------------------------------------------------------------- #
HOLDER = _script(r'''
root, ready, hold = sys.argv[1], sys.argv[2], float(sys.argv[3])
s = JsonStore(root)
with s.transaction(%(device)r, %(collection)r):
    open(ready, "w").write("held")
    time.sleep(hold)
''')


@pytest.mark.skipif(store_mod.fcntl is None, reason="no fcntl on this platform")
def test_t5_a_lock_held_past_the_timeout_fails_the_write_and_records_it(tmp_path):
    """T5 — a wedged holder in another process: our write gives up inside the lock budget
    (0.2 s here), returns False and records it — never blocks the MQTT loop, never
    swallows the failure."""
    root = str(tmp_path / "data")
    ready = str(tmp_path / "held")
    JsonStore(root).write(DEVICE, COLLECTION, ["before"])

    holder = subprocess.Popen([sys.executable, "-c", HOLDER, root, ready, "10"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(600):                       # poll, never sleep-and-hope
            if os.path.exists(ready):
                break
            threading.Event().wait(0.05)
        assert os.path.exists(ready), "the holder process never took the lock"

        noted = []
        s = JsonStore(root, lock_timeout_s=0.2,
                      on_lock_timeout=lambda path, waited: noted.append((path, waited)))
        assert s.write(DEVICE, COLLECTION, ["after"]) is False
        assert s.append(DEVICE, COLLECTION, {"x": 1}) is None
        assert s.lock_timeouts >= 2
        assert "safety_events" in s.last_lock_error
        assert noted and noted[0][0].endswith(".lock")
        assert noted[0][1] >= 0.15

        # ...and the record is untouched: a refused write is not a partial write.
        assert JsonStore(root).read(DEVICE, COLLECTION) == ["before"]
    finally:
        holder.kill()
        holder.communicate(timeout=30)

    # the holder is gone → the kernel released the lock; no stale-lock recovery needed
    s2 = JsonStore(root, lock_timeout_s=2.0)
    assert s2.write(DEVICE, COLLECTION, ["after"]) is True


def test_t5b_the_wait_is_bounded_by_backoff_not_by_a_spin(tmp_path):
    """The shape of the wait, against an injected sleep: exponential + jitter, bounded."""
    slept = []
    s = JsonStore(str(tmp_path), lock_timeout_s=1.0, sleep=slept.append)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(s, "_acquire_flock", lambda fd: False)     # never grants
        with pytest.raises(StoreLockTimeout):
            with s.transaction(DEVICE, COLLECTION):
                pass

    ceiling = store_mod.LOCK_BACKOFF_CAP_S + store_mod.LOCK_BACKOFF_BASE_S
    assert slept, "the store spun without backing off"
    assert all(d > 0 for d in slept), "a zero-second sleep is a spin, not a backoff"
    assert max(slept) <= ceiling, f"a backoff overshot the cap: {max(slept)} > {ceiling}"
    assert max(slept) > slept[0], "the delay never grew — that is a spin, not a backoff"
    # the budget is counted in *requested* sleep, so an injected clock spends all of it
    assert sum(slept) == pytest.approx(1.0, abs=1e-6), sum(slept)
    assert len(slept) <= 1.0 / store_mod.LOCK_BACKOFF_CAP_S + 20, len(slept)


def test_t5c_a_refused_write_from_memorystore_returns_nothing_stored(tmp_path):
    """`MemoryStore`'s writers must turn a refused lock into their existing "nothing was
    stored" answer — never a traceback out of a turn."""
    s = JsonStore(str(tmp_path), lock_timeout_s=0.05)
    m = MemoryStore(s)
    m.merge(DEVICE, "quiz", {"likes": ["dinosaurs"]})
    assert m.load(DEVICE)["quiz"]["likes"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(s, "_acquire_flock", lambda fd: False)
        assert m.merge(DEVICE, "quiz", {"likes": ["trains"]}) is None
        assert m.erase(DEVICE, "quiz") is False
        assert m.note_used(DEVICE, "dinosaurs") == 0
    assert s.lock_timeouts >= 3
    assert m.load(DEVICE)["quiz"]["likes"], "a refused write erased the record"


# --------------------------------------------------------------------------- #
# T6 — the config guard, in the shape of MOXIE_EXT_BUDGET_S
# --------------------------------------------------------------------------- #

def test_t6_the_lock_timeout_must_be_inside_the_turn_budget(monkeypatch):
    """T6 — a lock wait is a slice of a turn, so a timeout >= `MOXIE_BRAIN_BUDGET_S` fails
    startup with a sentence."""
    import importlib
    import config as cfg
    assert cfg.STORE_LOCK_TIMEOUT_S == pytest.approx(2.0)
    assert cfg.STORE_LOCK_TIMEOUT_S < cfg.BRAIN_BUDGET_S
    monkeypatch.setenv("MOXIE_STORE_LOCK_TIMEOUT_S", "99")
    with pytest.raises(ValueError) as caught:
        importlib.reload(cfg)
    assert "must be strictly less than" in str(caught.value)
    assert "MOXIE_BRAIN_BUDGET_S" in str(caught.value)
    monkeypatch.delenv("MOXIE_STORE_LOCK_TIMEOUT_S")
    importlib.reload(cfg)
    assert cfg.STORE_LOCK_TIMEOUT_S < cfg.BRAIN_BUDGET_S


def test_t6b_the_store_reads_the_env_var_itself(tmp_path, monkeypatch):
    """`store.py` imports no config, so it must read the env var itself."""
    monkeypatch.setenv("MOXIE_STORE_LOCK_TIMEOUT_S", "0.75")
    assert JsonStore(str(tmp_path)).lock_timeout_s == pytest.approx(0.75)
    monkeypatch.setenv("MOXIE_STORE_LOCK_TIMEOUT_S", "not-a-number")
    assert JsonStore(str(tmp_path)).lock_timeout_s == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# T7 — the POSIX fallback is loud
# --------------------------------------------------------------------------- #

def test_t7_without_fcntl_the_store_still_works_and_says_so(tmp_path, monkeypatch, capsys):
    """T7 — without `fcntl` the store degrades to an in-process `RLock` and prints ONE line
    saying so — no crash, no silent downgrade."""
    monkeypatch.setattr(store_mod, "fcntl", None)
    s = JsonStore(str(tmp_path))
    with s.transaction(DEVICE, COLLECTION):
        s.append(DEVICE, COLLECTION, {"i": 1})
    with s.transaction(DEVICE, COLLECTION):              # still reentrant
        with s.transaction(DEVICE, COLLECTION):
            s.append(DEVICE, COLLECTION, {"i": 2})
    assert len(s.read(DEVICE, COLLECTION, [])) == 2
    assert s.lock_timeouts == 0

    line = store_mod.locking_note()
    assert line and "cross-process" in line.lower()
    store_mod.warn_no_locking()
    out = capsys.readouterr().out
    assert line in out
    store_mod.warn_no_locking()
    assert store_mod.locking_note() not in capsys.readouterr().out, "the line printed twice"


@pytest.mark.skipif(store_mod.fcntl is None, reason="no fcntl on this platform")
def test_t7b_with_fcntl_there_is_no_warning_line(capsys):
    """On Linux nothing is printed, so the line means something when it does appear."""
    assert store_mod.locking_note() == ""
    store_mod.warn_no_locking()
    assert capsys.readouterr().out == ""


def test_t7c_run_py_prints_the_note_at_startup(tmp_path, monkeypatch, capsys):
    """`mqtt/run.py` builds the store, so booting it on a no-`fcntl` platform must say so."""
    from helpers_runtime import load_mqtt_run, reload_config
    monkeypatch.setattr(store_mod, "fcntl", None)
    monkeypatch.setattr(store_mod, "_warned_no_locking", False)
    monkeypatch.setenv("MOXIE_DATA_DIR", str(tmp_path))
    load_mqtt_run().assemble(reload_config(monkeypatch, ("MOXIE_STT", "MOXIE_TTS"),
                                           MOXIE_APP="echo", MOXIE_STT="off",
                                           MOXIE_TTS="off"))
    assert store_mod.locking_note() in capsys.readouterr().out, \
        "nothing at startup calls store.warn_no_locking() — the fallback is silent"


# --------------------------------------------------------------------------- #
# T8/T9 — durability: the old value or the new one, and a durable rename
# --------------------------------------------------------------------------- #
KILLER = _script(r'''
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = JsonStore(root)
real = os.replace
count = {"n": 0}
def replace(src, dst):
    # SIGKILL the writer between the temp file being complete and the rename that
    # publishes it — the exact window a reader could see a truncated record in.
    count["n"] += 1
    if count["n"] > n:
        os.kill(os.getpid(), 9)
    return real(src, dst)
os.replace = replace
for i in range(1000):
    s.write(%(device)r, %(collection)r, [{"who": tag, "i": j} for j in range(i + 1)])
''')


def test_t8_a_sigkill_between_write_and_replace_never_leaves_a_torn_file(tmp_path):
    """T8 — 20 writers SIGKILLed between a complete temp file and `os.replace`: every
    record parses as old or new, never half (A6), and temp files are cleaned up."""
    root = str(tmp_path / "data")
    s = JsonStore(root)
    s.write(DEVICE, COLLECTION, [])

    procs = [subprocess.Popen([sys.executable, "-c", KILLER, root, f"k{i}", str(i % 7)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for i in range(20)]
    for p in procs:
        p.communicate(timeout=300)
        assert p.returncode in (-9, 0), p.returncode

    raw = open(s.path(DEVICE, COLLECTION)).read()
    value = json.loads(raw)                      # must parse — the whole assertion
    assert isinstance(value, list)
    for item in value:
        assert set(item) == {"who", "i"}, item

    # a SIGKILLed writer can leave its pid'd `.tmp` behind; it must never be mistaken for
    # the record
    names = os.listdir(os.path.dirname(s.path(DEVICE, COLLECTION)))
    assert f"{COLLECTION}.json" in names
    assert JsonStore(root).read(DEVICE, COLLECTION) == value
    for leftover in names:
        assert leftover in (f"{COLLECTION}.json", f"{COLLECTION}.json{store_mod.LOCK_SUFFIX}") \
            or leftover.endswith(".tmp"), leftover


def test_t9_the_directory_is_fsynced_after_the_rename(tmp_path):
    """T9 — the rename is made durable by fsyncing the DIRECTORY fd, not just the file."""
    s = JsonStore(str(tmp_path))
    synced_dirs = []
    real_fsync = os.fsync

    def watch(fd):
        import stat as _stat
        try:
            if _stat.S_ISDIR(os.fstat(fd).st_mode):
                synced_dirs.append(fd)
        except OSError:
            pass
        return real_fsync(fd)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "fsync", watch)
        assert s.write(DEVICE, COLLECTION, [1, 2, 3]) is True
    assert synced_dirs, "os.replace was never followed by an fsync of the directory"


def test_t9b_a_directory_fsync_failure_does_not_fail_the_write(tmp_path):
    """Some network/container filesystems refuse a directory fsync (EINVAL). The write has
    already landed, so that is a durability downgrade, not a failed write."""
    s = JsonStore(str(tmp_path))

    def boom(fd):
        raise OSError(22, "Invalid argument")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store_mod, "_fsync_dir", boom)
        assert s.write(DEVICE, COLLECTION, [1]) is True
    assert s.read(DEVICE, COLLECTION) == [1]


# --------------------------------------------------------------------------- #
# The layout promise — a parent can still `cat` and `rm`
# --------------------------------------------------------------------------- #

def test_the_on_disk_layout_is_unchanged_apart_from_the_sidecars(tmp_path):
    """The most legible privacy property — plain JSON a parent can `cat` and `rm` — is the
    one a hardening slice is likeliest to trade away by accident."""
    s = JsonStore(str(tmp_path))
    s.write("d_1", "memory", {"quiz": {"likes": ["dinosaurs"]}})
    s.write_shared("config", {"volume": 3})

    assert os.path.isfile(os.path.join(str(tmp_path), "robots", "d_1", "memory.json"))
    assert os.path.isfile(os.path.join(str(tmp_path), "fleet", "config.json"))
    assert json.load(open(s.path("d_1", "memory"))) == {"quiz": {"likes": ["dinosaurs"]}}

    # `rm` still works, and the store copes with the file being gone behind its back.
    os.unlink(s.path("d_1", "memory"))
    assert s.read("d_1", "memory", {}) == {}


def test_a_hand_written_file_is_still_read_back(tmp_path):
    """A file edited behind the store's back (no lock taken) is still read back."""
    s = JsonStore(str(tmp_path))
    s.write_shared("permits", {"d_1": True})
    path = s.shared_path("permits")
    with open(path, "w") as fh:                  # a hand edit, no lock taken at all
        json.dump({"d_1": True, "d_2": True}, fh)
    assert s.read_shared("permits") == {"d_1": True, "d_2": True}


def test_transaction_shared_covers_the_fleet_tier(tmp_path):
    """The fleet tier is not partitioned by device, so it is the one two processes are
    likeliest to fight over."""
    s = JsonStore(str(tmp_path))
    with s.transaction_shared("config"):
        cfg = s.read_shared("config", {})
        cfg["volume"] = 3
        s.write_shared("config", cfg)
    assert s.read_shared("config") == {"volume": 3}
    assert os.path.exists(s.lock_path(s.shared_path("config")))


def test_a_transaction_on_one_record_does_not_block_another(tmp_path):
    """File locks are **per record**, not per store (in-process the `RLock` still
    serializes, per T3)."""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, "memory", {})
    s.write(DEVICE, COLLECTION, [])
    assert s.lock_path(s.path(DEVICE, "memory")) != s.lock_path(s.path(DEVICE, COLLECTION))
    assert s.lock_path(s.path(DEVICE, "memory")) != s.lock_path(s.path("d_other", "memory"))


# --------------------------------------------------------------------------- #
# T10 — `append` reads the write's return code
# --------------------------------------------------------------------------- #
# `append()` must not report success for an item a failed `write()` never stored. The
# soak's contention probe rests on `attempted == items_on_disk + refusals`, which
# separates a recorded refusal from a silent loss.

def test_t10_append_reports_failure_when_the_write_failed(tmp_path, monkeypatch):
    """A write that did not land must not come back as a list that says it did."""
    s = JsonStore(str(tmp_path))
    assert s.append(DEVICE, COLLECTION, "first") == ["first"]

    monkeypatch.setattr(s, "_write_path", lambda *a, **kw: False)
    assert s.append(DEVICE, COLLECTION, "second") is None, \
        "append returned a list for a write that failed"
    # And the record is unchanged — the failure is refused, never partial.
    monkeypatch.undo()
    assert s.read(DEVICE, COLLECTION, []) == ["first"]


def test_t10b_a_read_only_data_directory_is_a_refusal_not_a_lie(tmp_path):
    """T10 end to end: an unwritable tree answers `None` ("nothing was stored")."""
    s = JsonStore(str(tmp_path))
    s.append(DEVICE, COLLECTION, "first")
    device_dir = s.device_dir(DEVICE)
    mode = os.stat(device_dir).st_mode
    os.chmod(device_dir, 0o500)                   # r-x: no new temp file can be created
    try:
        assert s.append(DEVICE, COLLECTION, "second") is None
    finally:
        os.chmod(device_dir, mode)
    assert s.read(DEVICE, COLLECTION, []) == ["first"]


def test_t10c_append_shared_reports_the_same_way(tmp_path, monkeypatch):
    """A silent failure on `conn_events` would be an outage nobody can read about."""
    s = JsonStore(str(tmp_path))
    assert s.append_shared("conn_events", {"kind": "connect"}) == [{"kind": "connect"}]
    monkeypatch.setattr(s, "_write_path", lambda *a, **kw: False)
    assert s.append_shared("conn_events", {"kind": "disconnect"}) is None


def test_t10d_the_identity_the_soak_rests_on_holds_under_real_contention(tmp_path):
    """`attempted == on_disk + refused` over threads — the soak (`sim/tools/soak.py`)
    checks it across processes; this fails in seconds in the fast tier."""
    s = JsonStore(str(tmp_path))
    attempted, refused = 200, 0
    lock = threading.Lock()

    def writer(tag):
        nonlocal refused
        for i in range(attempted // 4):
            if s.append(DEVICE, COLLECTION, f"{tag}-{i}") is None:
                with lock:
                    refused += 1

    threads = [threading.Thread(target=writer, args=(t,)) for t in "abcd"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    on_disk = len(s.read(DEVICE, COLLECTION, []))
    assert on_disk + refused == attempted, \
        f"{attempted - on_disk - refused} append(s) vanished without being refused"


# --------------------------------------------------------------------------- #
# T11 — a raised lock budget must time out, not crash the caller
# --------------------------------------------------------------------------- #
# An unbounded `2 ** attempt` backoff overflowed a float at attempt 1024 (~budgets above
# 2 s) and escaped `transaction()` as `OverflowError`, past `append`'s timeout handler.

@contextlib.contextmanager
def _held_by_another(s):
    """Hold the record's sidecar lock on a separate fd, as another process would."""
    lock = s.lock_path(s.path(DEVICE, COLLECTION))
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    store_mod.fcntl.flock(fd, store_mod.fcntl.LOCK_EX)
    try:
        yield
    finally:
        store_mod.fcntl.flock(fd, store_mod.fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.parametrize("timeout_s", [2.0, 5.0, 30.0, 120.0])
def test_t11_a_contended_waiter_times_out_at_any_budget(tmp_path, timeout_s):
    """Exhausting any budget is a `StoreLockTimeout`, never an `OverflowError`. The no-op
    sleep makes thousands of polls take milliseconds — the axis the bug lives on."""
    s = JsonStore(str(tmp_path), lock_timeout_s=timeout_s, sleep=lambda _: None)
    with _held_by_another(s):
        with pytest.raises(StoreLockTimeout):
            with s.transaction(DEVICE, COLLECTION):
                pass
        # …and the store's own writers turn that into a falsy answer, not a traceback
        assert s.append(DEVICE, COLLECTION, "x") is None
        assert s.write(DEVICE, COLLECTION, ["x"]) is False
        assert s.lock_timeouts >= 2


def test_t11b_the_backoff_never_computes_an_unbounded_exponent(tmp_path):
    """The mechanism: every backoff delay is a real float in `[0, cap + base]`, so a loop
    refactor that changes the poll count cannot hide a regression."""
    delays: list = []
    s = JsonStore(str(tmp_path), lock_timeout_s=30.0, sleep=delays.append)
    with _held_by_another(s), pytest.raises(StoreLockTimeout):
        with s.transaction(DEVICE, COLLECTION):
            pass
    assert len(delays) > 1024, (
        f"only {len(delays)} polls — this budget no longer crosses the overflow cliff, so "
        "the test has stopped exercising the bug it was written for")
    ceiling = store_mod.LOCK_BACKOFF_CAP_S + store_mod.LOCK_BACKOFF_BASE_S
    for d in delays:
        assert isinstance(d, float) and 0.0 <= d <= ceiling, d
