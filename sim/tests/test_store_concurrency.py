"""
T1–T9 — two processes writing one appliance's data must not lose each other's writes.

The build document is
[`docs/architecture/backlog/production-hardening.md`](../../docs/architecture/backlog/production-hardening.md);
this file is its §6 T-series and it was written **before** the fix, in the shape
`test_ext_escapes.py`'s X3 was: every test here failed on `origin/dev` at 341965d, and
the ones that could pass for the wrong reason assert the *mechanism* as well as the
outcome.

The decision under test (§3.2) is **advisory `flock` on a per-record sidecar lock file,
behind a public `JsonStore.transaction()`, with JSON staying on disk** — not SQLite, and
not a single-writer rule. So these tests are deliberately about the three things a
plausible-looking `flock` patch gets wrong (§3.3):

* **T4** — a lock taken on the *data* file is a lock on an inode `os.replace` is about to
  swap out, i.e. no lock at all. The sidecar's inode must be stable across a write.
* **T2/T3** — `flock` is per *open file description*, so two `open()`s in one process
  deadlock where the old `threading.RLock` was reentrant. `RLock` outside, `flock`
  inside, one `open()` per outermost acquisition.
* **T5** — some store writes happen on the paho network thread, so the wait is bounded
  (`LOCK_EX | LOCK_NB` + backoff, `MOXIE_STORE_LOCK_TIMEOUT_S`) and an exhausted wait
  **fails loudly** rather than hanging the MQTT loop or vanishing.

Hermetic: a tmp directory, real `fork`ed/`spawn`ed subprocesses, no MQTT, no broker, no
network. No wall-clock read anywhere (see `test_clock_dependence.py`) — durations come
from `time.monotonic` inside the store, and every test here counts events instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import store as store_mod                      # noqa: E402
from moxie_sdk.store import JsonStore, MemoryStore, StoreLockTimeout   # noqa: E402

DEVICE = "d_conc"
COLLECTION = "safety_events"

#: Appends per writer process. The brief's T1 says 5 000 each; `append` rewrites the
#: whole list every time, so 5 000 × 2 is ~750 MB of fsync'd I/O and minutes of fast-tier
#: wall clock. 250 × 2 lost **half of every run** on the unfixed store measured while
#: writing this file (500 of 500 appends by one writer, three trials, plus 2-4 more from
#: starvation), which is far more than a test needs to see. The number is a knob, not a
#: claim: `MOXIE_TEST_STORE_APPENDS` raises it for a soak.
APPENDS = int(os.environ.get("MOXIE_TEST_STORE_APPENDS") or 250)


# --------------------------------------------------------------------------- #
# A real second process — not a thread, not a fork of the pytest interpreter
# --------------------------------------------------------------------------- #
WRITER = r'''
import os, sys
sys.path.insert(0, os.path.join(%(repo)r, "mqtt"))
from moxie_sdk.store import JsonStore
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
# A generous lock budget on purpose. T1 is about **lost updates**, and the store's other
# failure — a starved `LOCK_NB` poller giving up (T5) — would show up here as the same
# missing item for an entirely different reason. `flock` has no queue, so a process
# appending in a tight loop can starve its peer; 30 s makes that vanishingly unlikely and
# leaves T1 measuring the thing it claims to measure. The refusal path has its own test.
s = JsonStore(root, lock_timeout_s=30.0)
for i in range(n):
    assert s.append(%(device)r, %(collection)r, {"who": tag, "i": i}) is not None, \
        "the writer was REFUSED the lock, which is starvation (T5), not a lost update"
''' % {"repo": REPO, "device": DEVICE, "collection": COLLECTION}


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
    """T1 — 2 processes × `APPENDS` appends to one collection: the final list is exactly
    `2 × APPENDS` items and every one of them is there.

    This is the §3.2 decision stated as an assertion. It **fails on `origin/dev`**: two
    `append()` calls interleave read-read-write-write across processes and one of the
    items vanishes with no error anywhere — the single most damaging property of the
    store today, because nothing observes it.
    """
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
    """Teeth for T1. A guard that has never been observed failing proves nothing, so run
    the *unlocked* read-modify-write — today's `append`, transcribed — through the same
    harness and require that it loses something. If this ever passes, T1 above has stopped
    being a test of anything and the harness is what needs fixing.
    """
    unlocked = r'''
import json, os, sys
sys.path.insert(0, os.path.join(%(repo)r, "mqtt"))
from moxie_sdk.store import JsonStore
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
s = JsonStore(root)
path = s.path(%(device)r, %(collection)r)
for i in range(n):
    # `JsonStore.append` as it stood on origin/dev: read, mutate, write — with only an
    # in-process lock between them, which two processes do not share.
    items = s._read_path(path, [])
    items.append({"who": tag, "i": i})
    s._write_path(path, items)
''' % {"repo": REPO, "device": DEVICE, "collection": COLLECTION}

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
    """T2 — reentrancy. `flock` is per open file *description*: a second `open()` +
    `LOCK_EX` from the same thread blocks on itself forever. The old `threading.RLock`
    was reentrant and the five `MemoryStore` call sites rely on that, so the outermost
    acquisition is the only one that opens an fd.

    Guarded by a watchdog thread so a regression reports "deadlock" instead of hanging the
    fast tier until CI's job timeout.
    """
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
    """The mechanism behind T2, not just its outcome: a nested acquisition must be a
    *no-op re-entry*, so exactly ONE lock fd exists however deep the nesting goes.

    Asserted rather than inferred because "it did not deadlock" is also what you get from
    a patch that quietly stopped locking.
    """
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
    """T3 — two threads in one process must not interleave a read-modify-write.

    The probe is a *witness*, not a timing guess: each thread records the depth of
    concurrent entry, and any value above 1 means two bodies were inside at once.
    """
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
    """T4 — `os.replace()` swaps the **inode**. A lock held on the data file is a lock on
    an inode the next writer will never open, so a patch that locks `memory.json` looks
    correct, passes casual review, and serializes nothing.

    Pinned three ways: the lock path is a `.lock` sidecar, it is *not* the data path, and
    its inode is unchanged across a write that definitely replaced the data file's.
    """
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
    """Deleting the sidecar re-introduces the same inode race the sidecar exists to avoid
    (two processes each create their own `.lock` and lock different inodes). So `delete()`
    removes the record and leaves the lock file alone."""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, COLLECTION, [1])
    lock = s.lock_path(s.path(DEVICE, COLLECTION))
    ino = os.stat(lock).st_ino
    assert s.delete(DEVICE, COLLECTION) is True
    assert not os.path.exists(s.path(DEVICE, COLLECTION))
    assert os.path.exists(lock) and os.stat(lock).st_ino == ino


def test_t4c_a_sidecar_is_not_mistaken_for_a_device_or_a_record(tmp_path):
    """The `.lock` files are empty files a reader ignores — the §7 acceptance criterion
    that the on-disk layout stays byte-identical *"`.lock` sidecars aside"*. They must not
    show up as devices, and `read` must not try to parse one."""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, COLLECTION, [1])
    s.write_shared("config", {"a": 1})
    assert s.devices() == [DEVICE]
    assert os.path.getsize(s.lock_path(s.path(DEVICE, COLLECTION))) == 0
    assert json.loads(open(s.path(DEVICE, COLLECTION)).read()) == [1]


# --------------------------------------------------------------------------- #
# T5 — a bounded wait that fails loudly (§3.3 #3)
# --------------------------------------------------------------------------- #
HOLDER = r'''
import os, sys, time
sys.path.insert(0, os.path.join(%(repo)r, "mqtt"))
from moxie_sdk.store import JsonStore
root, ready, hold = sys.argv[1], sys.argv[2], float(sys.argv[3])
s = JsonStore(root)
with s.transaction(%(device)r, %(collection)r):
    open(ready, "w").write("held")
    time.sleep(hold)
''' % {"repo": REPO, "device": DEVICE, "collection": COLLECTION}


@pytest.mark.skipif(store_mod.fcntl is None, reason="no fcntl on this platform")
def test_t5_a_lock_held_past_the_timeout_fails_the_write_and_records_it(tmp_path):
    """T5 — the wedged-holder case. A second process holds the record's lock; our write
    must give up inside `MOXIE_STORE_LOCK_TIMEOUT_S`, return **False**, and leave a
    record of it — never block the MQTT loop, never swallow the failure.

    The timeout is driven down to 0.2 s and the holder waits on a *file*, so nothing here
    depends on how long a process takes to start.
    """
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

    # The holder is gone → the lock is released by the kernel, no stale-lock recovery
    # needed (§3.1, the single best property of flock over any pid-file scheme).
    s2 = JsonStore(root, lock_timeout_s=2.0)
    assert s2.write(DEVICE, COLLECTION, ["after"]) is True


def test_t5b_the_wait_is_bounded_by_backoff_not_by_a_spin(tmp_path):
    """The *shape* of the wait, asserted against an injected sleep rather than a stopwatch
    (the `test_clock_dependence.py` ratchet): exponential + jitter, like the house's
    `chat.py::call_with_backoff`, and never an unbounded number of attempts."""
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
    # The budget is counted in *requested* sleep, so an injected clock terminates exactly
    # the way the real one does — and the whole wait is spent, not a fraction of it.
    assert sum(slept) == pytest.approx(1.0, abs=1e-6), sum(slept)
    assert len(slept) <= 1.0 / store_mod.LOCK_BACKOFF_CAP_S + 20, len(slept)


def test_t5c_a_refused_write_from_memorystore_returns_nothing_stored(tmp_path):
    """The five `MemoryStore` sites (§2.1 :562/:653/:691/:727/:753) reach the same
    transaction, so a lock they cannot get must produce the *"nothing was stored"* answer
    each of them already has — never a traceback out of a turn."""
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
# T6 — the config guard, in the shape of MOXIE_EXT_BUDGET_S (config.py:311-317)
# --------------------------------------------------------------------------- #

def test_t6_the_lock_timeout_must_be_inside_the_turn_budget(monkeypatch):
    """T6 — a lock wait is a **slice** of a turn, not a claim on it. A deployment that
    sets `MOXIE_STORE_LOCK_TIMEOUT_S` at or above `MOXIE_BRAIN_BUDGET_S` has written a
    configuration in which one wedged writer can eat a whole turn, and it fails at
    startup with a sentence rather than at 3 a.m. with a silent robot.

    Exactly the guard `MOXIE_EXT_BUDGET_S` gets (`test_ext.py::test_t16_…`), for exactly
    the same reason — and 2.0 s is **chosen, not measured** (§9 A13), which is why it is
    an env var at all.
    """
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
    """`store.py` imports no config (its docstring's *"pure"* property), so the env var has
    to be read where the store is — otherwise the knob exists and does nothing."""
    monkeypatch.setenv("MOXIE_STORE_LOCK_TIMEOUT_S", "0.75")
    assert JsonStore(str(tmp_path)).lock_timeout_s == pytest.approx(0.75)
    monkeypatch.setenv("MOXIE_STORE_LOCK_TIMEOUT_S", "not-a-number")
    assert JsonStore(str(tmp_path)).lock_timeout_s == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# T7 — the POSIX fallback is loud (§3.3 #4)
# --------------------------------------------------------------------------- #

def test_t7_without_fcntl_the_store_still_works_and_says_so(tmp_path, monkeypatch, capsys):
    """T7 — `fcntl` is POSIX-only. With it absent the store degrades to exactly today's
    in-process `RLock` behaviour (a Windows developer keeps working) and the supervisor
    prints **one** line saying cross-process locking is unavailable. Not a crash, and not
    a silent downgrade — a silent downgrade is how someone ships a Windows appliance
    believing it is safe."""
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
    """The reverse direction: on Linux — CI and the appliance both — the note is empty and
    nothing is printed, so the line means what it says when it does appear."""
    assert store_mod.locking_note() == ""
    store_mod.warn_no_locking()
    assert capsys.readouterr().out == ""


def test_t7c_run_py_prints_the_note_at_startup(tmp_path):
    """The note has to be printed by something a person runs. `mqtt/run.py::assemble` is
    where the store is built, so that is where it is said."""
    src = open(os.path.join(REPO, "mqtt", "run.py")).read()
    assert "warn_no_locking" in src, (
        "nothing calls store.warn_no_locking() — the fallback is silent after all")


# --------------------------------------------------------------------------- #
# T8/T9 — durability: the old value or the new one, and a durable rename
# --------------------------------------------------------------------------- #
KILLER = r'''
import os, sys
sys.path.insert(0, os.path.join(%(repo)r, "mqtt"))
from moxie_sdk.store import JsonStore
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
''' % {"repo": REPO, "device": DEVICE, "collection": COLLECTION}


def test_t8_a_sigkill_between_write_and_replace_never_leaves_a_torn_file(tmp_path):
    """T8 — 20 writers, each SIGKILLed in the window between a complete temp file and the
    `os.replace` that publishes it. Every surviving record must parse as either the old
    value or the new one — never half of one (A6).

    This is the property `os.replace` already gave us; the test exists so a locking patch
    cannot take it away, and so the temp-file cleanup is proved rather than assumed.
    """
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

    # A killed writer can leave its own pid'd scratch file behind — `os.unlink(tmp)` never
    # runs when the process is SIGKILLed, and no design short of a startup sweep changes
    # that. What must hold is that the scratch is never mistaken for the record: the temp
    # names carry a pid and the store only ever reads `<collection>.json`.
    names = os.listdir(os.path.dirname(s.path(DEVICE, COLLECTION)))
    assert f"{COLLECTION}.json" in names
    assert JsonStore(root).read(DEVICE, COLLECTION) == value
    for leftover in names:
        assert leftover in (f"{COLLECTION}.json", f"{COLLECTION}.json{store_mod.LOCK_SUFFIX}") \
            or leftover.endswith(".tmp"), leftover


def test_t9_the_directory_is_fsynced_after_the_rename(tmp_path):
    """T9 — the file's *contents* were already durable (`fsync` before the rename); the
    **directory entry** pointing at them was not (§2.1, A12). On ext4 with `data=ordered`
    you get old-or-new anyway, which is why nobody has been bitten — but that is the
    filesystem being kind, not the code being correct.

    Asserted by watching for an fsync on a **directory** fd, which is the only thing that
    distinguishes the fix from the four lines that look like it.
    """
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
    """Not every filesystem lets you `open(dir, O_DIRECTORY)` and fsync it — some network
    and container filesystems refuse with EINVAL. The write already succeeded at that
    point, so a refused directory sync is a durability *downgrade*, not a failure, and
    reporting it as one would turn a working appliance into a broken one."""
    s = JsonStore(str(tmp_path))

    def boom(fd):
        raise OSError(22, "Invalid argument")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store_mod, "_fsync_dir", boom)
        assert s.write(DEVICE, COLLECTION, [1]) is True
    assert s.read(DEVICE, COLLECTION) == [1]


# --------------------------------------------------------------------------- #
# The layout promise (§7 #5) — a parent can still `cat` and `rm`
# --------------------------------------------------------------------------- #

def test_the_on_disk_layout_is_unchanged_apart_from_the_sidecars(tmp_path):
    """§7 #5: *"A parent can still `cat` and `rm` their child's data. The on-disk layout is
    byte-identical to today's, `.lock` sidecars aside."* The most legible privacy property
    this appliance has is the one a hardening slice is most likely to trade away by
    accident, so it is asserted, not assumed."""
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
    """`test_device_permits.py`:239-250 already asserts the READ side of cross-process
    sharing — a permit written behind the runtime's back must take effect. Locking must
    not have turned that into "only files this process wrote are visible"."""
    s = JsonStore(str(tmp_path))
    s.write_shared("permits", {"d_1": True})
    path = s.shared_path("permits")
    with open(path, "w") as fh:                  # a hand edit, no lock taken at all
        json.dump({"d_1": True, "d_2": True}, fh)
    assert s.read_shared("permits") == {"d_1": True, "d_2": True}


def test_transaction_shared_covers_the_fleet_tier(tmp_path):
    """The fleet tier (`fleet/<collection>.json`) is where the appliance-wide records live
    — `config`, `permits`, `voice`, the content overlay — and it is the tier two processes
    are most likely to fight over, because it is not partitioned by device."""
    s = JsonStore(str(tmp_path))
    with s.transaction_shared("config"):
        cfg = s.read_shared("config", {})
        cfg["volume"] = 3
        s.write_shared("config", cfg)
    assert s.read_shared("config") == {"volume": 3}
    assert os.path.exists(s.lock_path(s.shared_path("config")))


def test_a_transaction_on_one_record_does_not_block_another(tmp_path):
    """The lock is **per record**, not per store: holding `memory` must not stop a write to
    `safety_events`. (In-process the store-wide `RLock` still serializes them — that is
    today's behaviour and T3 asserts it — but the *file* locks must be distinct, which is
    what a second process sees.)"""
    s = JsonStore(str(tmp_path))
    s.write(DEVICE, "memory", {})
    s.write(DEVICE, COLLECTION, [])
    assert s.lock_path(s.path(DEVICE, "memory")) != s.lock_path(s.path(DEVICE, COLLECTION))
    assert s.lock_path(s.path(DEVICE, "memory")) != s.lock_path(s.path("d_other", "memory"))


# --------------------------------------------------------------------------- #
# T10 — `append` reads the write's return code (production hardening P1)
# --------------------------------------------------------------------------- #
#
# Found while wiring P1's connection ring onto the store. `append()` called `write()` and
# returned `items` regardless of what it said, so an `OSError` — a full disk, a read-only
# `/data`, a permission change under a running appliance — produced a **successful** append
# of an item that reached no file.
#
# That is the same disease as the eight `publish()` sites whose `info.rc` nobody read
# (§4.1 C5) and the CONNACK that logged "broker connected" for a refusal (C3): a
# comfortable answer at the one boundary that knows the truth. It also breaks the identity
# the soak's contention probe is built on —
#
#     attempted == items_on_disk + refusals
#
# — which is the only thing that tells a **recorded refusal** (§3.2 point 4 accepts it,
# A11 asks it to be recorded) apart from a **silent loss** (A5 forbids it). With `append`
# lying about a failed write, a lost item looks exactly like a successful one.

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
    """The realistic version of T10, end to end through the real write path: an
    unwritable tree. `None` is the store's own *"nothing was stored"* answer, and it is
    what the caller already handles for a refused lock."""
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
    """The fleet tier is where the appliance's own history lives (`conn_events`), so a
    silent failure there is a connection outage nobody can read about afterwards."""
    s = JsonStore(str(tmp_path))
    assert s.append_shared("conn_events", {"kind": "connect"}) == [{"kind": "connect"}]
    monkeypatch.setattr(s, "_write_path", lambda *a, **kw: False)
    assert s.append_shared("conn_events", {"kind": "disconnect"}) is None


def test_t10d_the_identity_the_soak_rests_on_holds_under_real_contention(tmp_path):
    """`attempted == on_disk + refused`, proved in-process over threads.

    The soak (`sim/tools/soak.py`) asserts this across **processes** at a rate CI cannot
    afford; this is the same invariant at a size the fast tier can run, so a regression in
    it fails in seconds rather than in the nightly job.
    """
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
