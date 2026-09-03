"""Remove each guard the production-hardening slice rests on, and check its test goes red.

*"A test for every fix, proven in BOTH directions."* A green suite proves the guards are
**present**; this proves they are **load-bearing**. Same shape as
`ext_mutation_check.py` / `brain_mutation_check.py` / `performance_mutation_check.py`,
and it exists for the same reason every one of those did: each of them found a real hole.

Run it by hand after touching `moxie_sdk/store.py` or the connection region of
`supervisor/moxie_runtime.py`:

    python3 sim/tools/hardening_mutation_check.py

Every row must say "caught". A row that says NOT CAUGHT means the assertion passes with
the guard deleted, i.e. it is not testing what its name claims.

Two mutations are deliberately the **half-done fixes** the brief warns about rather than
deleted guards, because those are what a plausible patch actually looks like:

* `connect_async` with `retry_first_connection` left off — the §2.2 #3 trap, and the
  reason S6 was written at all (risk R2);
* the lock moved from the `.lock` sidecar onto the data file — which looks correct,
  passes review, and serializes nothing because `os.replace` swaps the inode (risk R1).

Nothing here changes the tree permanently: each mutation is reverted in a `finally`.
`PYTHONDONTWRITEBYTECODE` is not a nicety — without it a `__pycache__` entry from an
earlier mutation can shadow a later one and a guard reads as un-caught when it is fine.
"""
import pathlib
import subprocess

WT = pathlib.Path(__file__).resolve().parents[2]
STORE = WT / "mqtt/moxie_sdk/store.py"
RT = WT / "mqtt/supervisor/moxie_runtime.py"
CFG = WT / "mqtt/config.py"
TESTS = WT / "sim/tests/test_store_concurrency.py"

STORE_TESTS = "sim/tests/test_store_concurrency.py"
CONN_TESTS = "sim/tests/test_connection_resilience.py"

MUTATIONS = [
    # ---- the store: the cross-process lock -------------------------------------
    ("T1  never take the flock at all (back to origin/dev's RLock)", STORE,
     "        if fcntl is not None:\n                try:\n                    os.makedirs",
     "        if False:\n                try:\n                    os.makedirs",
     STORE_TESTS, "t1_two_processes"),
    ("T1  make the lock non-exclusive (LOCK_SH)", STORE,
     "            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
     "            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)",
     STORE_TESTS, "t1_two_processes"),
    ("T1  append reads and writes outside the transaction", STORE,
     "            with self.transaction(device_id, collection):\n                items = self.read(device_id, collection, [])",
     "            with contextlib.nullcontext():\n                items = self.read(device_id, collection, [])",
     STORE_TESTS, "t1_two_processes"),
    # T1b's job is *"if this ever passes, the harness is not racing"*, so the mutation
    # that proves it has teeth is one that stops the harness racing — not one that adds a
    # lock. (The first attempt locked `_write_path` and went uncaught, correctly: locking
    # half of a read-modify-write fixes nothing, which is the whole point of T1.)
    ("T1b the teeth: run the two writer processes one after the other", TESTS,
     "    procs = [subprocess.Popen([sys.executable, \"-c\", script, root, tag, str(n)],\n                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n             for tag in tags]",
     "    procs = []\n    for tag in tags:\n        p = subprocess.Popen([sys.executable, \"-c\", script, root, tag, str(n)],\n                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n        p.wait()\n        procs.append(p)",
     STORE_TESTS, "t1b"),
    ("R1  lock the DATA file instead of the sidecar (the §3.3 #1 trap)", STORE,
     '        return path + LOCK_SUFFIX', "        return path",
     STORE_TESTS, "t4_the_lock_is_a_sidecar"),
    ("T2  open a fresh fd on a nested acquisition (the §3.3 #2 deadlock)", STORE,
     "        if depths.get(lock_path):              # nested on the same record, same thread",
     "        if False:                             # nested on the same record, same thread",
     STORE_TESTS, "t2_nested or t2b"),
    ("T3  drop the store-wide RLock, leaving threads to race", STORE,
     "        self._lock.acquire()                   # RLock OUTSIDE, always (trap #2)",
     "        pass                                   # RLock OUTSIDE, always (trap #2)",
     STORE_TESTS, "t3_two_threads"),
    ("T5  wait forever instead of giving up (block the MQTT loop)", STORE,
     "        while asked < self.lock_timeout_s:",
     "        while asked < self.lock_timeout_s or True:",
     STORE_TESTS, "t5b"),
    ("T5  swallow the refusal — return False and record nothing", STORE,
     "        self.lock_timeouts += 1", "        self.lock_timeouts += 0",
     STORE_TESTS, "t5_a_lock_held or t5c"),
    ("T5  spin instead of backing off", STORE,
     "            self._sleep(delay)", "            pass",
     STORE_TESTS, "t5b"),
    ("T5c a refused MemoryStore write raises into the turn instead of answering", STORE,
     '    @refuses_on_lock("merge", None)', "    ",
     STORE_TESTS, "t5c"),
    ("T6  drop the turn-budget assertion on MOXIE_STORE_LOCK_TIMEOUT_S", CFG,
     "if STORE_LOCK_TIMEOUT_S >= BRAIN_BUDGET_S:", "if False:",
     STORE_TESTS, "t6_the_lock_timeout"),
    ("T6b ignore MOXIE_STORE_LOCK_TIMEOUT_S in the store itself", STORE,
     '        return float(os.environ.get("MOXIE_STORE_LOCK_TIMEOUT_S") or DEFAULT_LOCK_TIMEOUT_S)',
     "        return DEFAULT_LOCK_TIMEOUT_S",
     STORE_TESTS, "t6b"),
    ("T7  make the no-fcntl fallback silent", STORE,
     '    return "" if fcntl is not None else _NO_LOCKING_NOTE', '    return ""',
     STORE_TESTS, "t7_without_fcntl or t7b"),
    ("T9  drop the directory fsync after os.replace (A12)", STORE,
     "                _fsync_dir(directory)          # the rename itself, made durable (A12)",
     "                pass", STORE_TESTS, "t9_the_directory"),
    ("T9b let a refused directory fsync fail the write", STORE,
     "            except OSError:\n                pass                           # some filesystems refuse; the data is written",
     "            except OSError:\n                return False",
     STORE_TESTS, "t9b"),
    ("T4b delete the sidecar along with the record (re-opens the inode race)", STORE,
     "                os.unlink(path)\n                return True",
     "                os.unlink(path)\n                try:\n                    os.unlink(path + LOCK_SUFFIX)\n                except OSError:\n                    pass\n                return True",
     STORE_TESTS, "t4b"),

    # ---- the connection --------------------------------------------------------
    ("R2  connect_async WITHOUT retry_first_connection (the half-done fix)", RT,
     "        self.client.loop_forever(retry_first_connection=True)",
     "        self.client.loop_forever()", CONN_TESTS, "s6"),
    ("S6  go back to the blocking connect()", RT,
     "        self.client.connect_async(self.host, self.port, KEEPALIVE_S)",
     "        self.client.connect(self.host, self.port, KEEPALIVE_S)", CONN_TESTS, "s6"),
    ("S4  subscribe on a CONNACK refusal anyway", RT,
     "            return                            # and subscribe to nothing",
     "            pass                              # and subscribe to nothing",
     CONN_TESTS, "s4_a_connack"),
    ("S4  print 'broker connected' before checking rc", RT,
     "        if self._connack_failed(rc):",
     '        print(f"[runtime] broker connected rc={rc}")\n        if self._connack_failed(rc):',
     CONN_TESTS, "s4_a_connack"),
    ("S4  treat every reason code as success", RT,
     "        failed = getattr(rc, \"is_failure\", None)", "        return False\n        failed = None",
     CONN_TESTS, "s4_a_connack"),
    ("S5  go back to paho's 120 s reconnect ceiling", RT,
     "RECONNECT_MAX_DELAY_S = 60", "RECONNECT_MAX_DELAY_S = 120", CONN_TESTS, "s5"),
    ("S5  never call reconnect_delay_set", RT,
     "        self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,",
     "        None and self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,",
     CONN_TESTS, "s5"),
    ("S1  ignore info.rc again, the way all eight sites did", RT,
     "        rc = getattr(info, \"rc\", 0)           # a double that returns None means success",
     "        rc = 0", CONN_TESTS, "s1_a_publish or s1b or s1d"),
    ("S1b wakeup guards on `client is None` again (the PR #55 regression)", RT,
     "        if not self._broker_connected():\n            return {\"ok\": False, \"device_id\": device_id, \"published\": False,\n                    \"acknowledged\": False, \"error\": \"no broker connection\",",
     "        if self.client is None:\n            return {\"ok\": False, \"device_id\": device_id, \"published\": False,\n                    \"acknowledged\": False, \"error\": \"no broker connection\",",
     CONN_TESTS, "s1b"),
    ("S1  `_broker_connected` trusts object existence", RT,
     "        checker = getattr(client, \"is_connected\", None)",
     "        return True\n        checker = None",
     CONN_TESTS, "s1_a_publish or s1b"),
    ("S1  record the drop nowhere", RT,
     "        self.publish_drops += 1", "        self.publish_drops += 0",
     CONN_TESTS, "s1_a_publish"),
    ("S2  a disconnect no longer stales the in-flight turn", RT,
     "        for device_id in set(self._turn_seq) | set(self.robots):",
     "        for device_id in []:", CONN_TESTS, "s2_a_turn or s8"),
    ("S8  stale only the robots that already had a turn", RT,
     "        for device_id in set(self._turn_seq) | set(self.robots):",
     "        for device_id in set(self._turn_seq):", CONN_TESTS, "s8"),
    ("S3  subscribe once and never again on reconnect", RT,
     "        for t in self.SUBSCRIPTIONS:\n            c.subscribe(t)",
     "        if not self.last_broker_disconnect:\n            for t in self.SUBSCRIPTIONS:\n                c.subscribe(t)",
     CONN_TESTS, "s3"),
    ("S4c drop on_connect_fail, so the retry loop is invisible", RT,
     "        self.client.on_connect_fail = self._on_connect_fail",
     "        pass", CONN_TESTS, "s4c"),
    ("S4b drop the connection fields from /status", RT,
     '                "broker_connected": self.broker_connected,',
     '                "broker_connected": True,', CONN_TESTS, "s4b"),
    ("S7  _on_event goes back to an ephemeral RobotContext (C6 undone)", RT,
     "        if device_id not in self.robots:\n            self._device_connect(device_id)",
     "        if False:\n            self._device_connect(device_id)", CONN_TESTS, "s7"),
    ("S2b keepalive back to a literal nobody chose", RT,
     "KEEPALIVE_S = 30", "KEEPALIVE_S = 60", CONN_TESTS, "s2b"),
]


def main() -> int:
    caught = missed = noop = 0
    for name, path, old, new, tests, sel in MUTATIONS:
        src = path.read_text()
        if old not in src:
            print(f"  NO-OP       {name}  (anchor not found)")
            noop += 1
            continue
        backup = src
        path.write_text(src.replace(old, new, 1))
        try:
            r = subprocess.run(
                [str(WT / ".venv/bin/python"), "-m", "pytest", tests, "-q", "-k", sel,
                 "-p", "no:cacheprovider"],
                cwd=WT, capture_output=True, text=True,
                env={"PATH": "/usr/bin:/bin", "MOXIE_LLM_API_KEY": "",
                     "MOXIE_LLM_BASE_URL": "", "MOXIE_VOICE_BASE_URL": "",
                     "MOXIE_STT_BASE_URL": "",
                     "HOME": str(pathlib.Path.home()), "PYTHONDONTWRITEBYTECODE": "1"})
            if r.returncode == 0:
                print(f"  NOT CAUGHT  {name}")
                missed += 1
            else:
                print(f"  caught      {name}")
                caught += 1
        finally:
            path.write_text(backup)
    print(f"\nMUTATIONS: {caught} caught, {missed} missed, {noop} no-op")
    return 1 if (missed or noop) else 0


if __name__ == "__main__":
    raise SystemExit(main())
