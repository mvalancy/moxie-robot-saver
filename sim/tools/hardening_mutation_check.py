"""Break each guard the production-hardening P0 slice rests on (store lock, connection
resilience, connect readiness); the row's tests `-k` selector must go red.

Some rows are the plausible HALF-DONE fixes rather than deletions: `connect_async` without
`retry_first_connection`, and the lock taken on the data file instead of the `.lock`
sidecar (serializes nothing, because `os.replace` swaps the inode). The timeout is not a
nicety: T5's "wait forever" mutation once grew to 20 GB RSS in six minutes.

    python3 sim/tools/hardening_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

STORE = WT / "mqtt/moxie_sdk/store.py"
MEMSTORE = WT / "mqtt/moxie_sdk/memory_store.py"
RT_CONNECTION = WT / "mqtt/supervisor/moxie_runtime/connection.py"
RT_CONSTANTS = WT / "mqtt/supervisor/moxie_runtime/constants.py"
RT_FLEET = WT / "mqtt/supervisor/moxie_runtime/fleet.py"
RT_LIFECYCLE = WT / "mqtt/supervisor/moxie_runtime/lifecycle.py"
RT_TURNS = WT / "mqtt/supervisor/moxie_runtime/turns.py"
CFG = WT / "mqtt/config.py"
TESTS = WT / "sim/tests/test_store_concurrency.py"
STORE_TESTS = "sim/tests/test_store_concurrency.py"
CONN_TESTS = "sim/tests/test_connection_resilience.py"
READY_TESTS = "sim/tests/test_connect_readiness.py"
MUTATION_TIMEOUT_S = 300

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
    # Anchor updated 2026-09-03: P1 moved `append`'s body into `_append_path` so the fleet
    # tier could share it (`append_shared`), and added the write's return-code check the
    # original never had. The mutation is the same one — do the read-modify-write with no
    # lock around it.
    ("T1  append reads and writes outside the transaction", STORE,
     "            with self._transaction_path(path):\n                items = self._read_path(path, [])",
     "            with contextlib.nullcontext():\n                items = self._read_path(path, [])",
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
    # 2026-09-05: this row used to say `or True` — loop forever. `t5b` drives the wait with
    # an INJECTED sleep (`sleep=slept.append`), so "forever" is a tight `list.append` loop
    # with nothing to bound it: **20 GB of RSS in six minutes**, unattended, on the machine
    # running the check. `* 1000` is no better, and the reason is worth keeping: five lines
    # below the condition, `delay = min(delay, self.lock_timeout_s - asked)` clamps the
    # delay to the REMAINING BUDGET, so once `asked` reaches the timeout every subsequent
    # delay is zero and `asked` stops growing — any mutation that only raises the budget
    # ceiling is still unbounded (6.4 GB in two minutes, measured). Bounding the ITERATIONS
    # instead says the same thing — the wait is no longer governed by the budget, and the
    # MQTT loop is blocked far past it — and terminates: `t5b` goes red on the zero-length
    # sleeps the clamp then produces, on `sum(slept)`, and on the attempt ceiling.
    ("T5  wait far past the budget instead of giving up (block the MQTT loop)", STORE,
     "        while asked < self.lock_timeout_s:",
     "        while attempt < 100000:",
     STORE_TESTS, "t5b"),
    ("T5  swallow the refusal — return False and record nothing", STORE,
     "        self.lock_timeouts += 1", "        self.lock_timeouts += 0",
     STORE_TESTS, "t5_a_lock_held or t5c"),
    ("T5  spin instead of backing off", STORE,
     "            self._sleep(delay)", "            pass",
     STORE_TESTS, "t5b"),
    ("T5c a refused MemoryStore write raises into the turn instead of answering", MEMSTORE,
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
    ("R2  connect_async WITHOUT retry_first_connection (the half-done fix)", RT_LIFECYCLE,
     "        self.client.loop_forever(retry_first_connection=True)",
     "        self.client.loop_forever()", CONN_TESTS, "s6"),
    ("S6  go back to the blocking connect()", RT_LIFECYCLE,
     "        self.client.connect_async(self.host, self.port, KEEPALIVE_S)",
     "        self.client.connect(self.host, self.port, KEEPALIVE_S)", CONN_TESTS, "s6"),
    ("S4  subscribe on a CONNACK refusal anyway", RT_CONNECTION,
     "            return                            # and subscribe to nothing",
     "            pass                              # and subscribe to nothing",
     CONN_TESTS, "s4_a_connack"),
    ("S4  print 'broker connected' before checking rc", RT_CONNECTION,
     "        if self._connack_failed(rc):",
     '        print(f"[runtime] broker connected rc={rc}")\n        if self._connack_failed(rc):',
     CONN_TESTS, "s4_a_connack"),
    # `failed = getattr(rc, "is_failure", None)` is IDENTICAL in `_connack_failed` and
    # `_suback_failed` — deliberately, they are twins — so the one-line anchor matched both
    # and mutated whichever came first. The `int(rc) != 0` tail is what makes this the
    # CONNACK one, which is the guard `s4_a_connack` is about. (The old replacement was
    # also invisible to `test_mutation_tables.py`'s captured-mutation half: it left the
    # anchor still matching at the OTHER site, so `old in src` stayed true with the
    # mutation sitting in the tree. An ambiguous anchor breaks the ratchet too.)
    ("S4  treat every reason code as success", RT_CONNECTION,
     "        failed = getattr(rc, \"is_failure\", None)\n"
     "        if failed is not None:\n"
     "            return bool(failed)\n"
     "        try:\n"
     "            return int(rc) != 0",
     "        failed = False\n"
     "        if failed is not None:\n"
     "            return bool(failed)\n"
     "        try:\n"
     "            return int(rc) != 0",
     CONN_TESTS, "s4_a_connack"),
    ("S5  go back to paho's 120 s reconnect ceiling", RT_CONSTANTS,
     "RECONNECT_MAX_DELAY_S = 60", "RECONNECT_MAX_DELAY_S = 120", CONN_TESTS, "s5"),
    ("S5  never call reconnect_delay_set", RT_CONNECTION,
     "        self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,",
     "        None and self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,",
     CONN_TESTS, "s5"),
    ("S1  ignore info.rc again, the way all eight sites did", RT_CONNECTION,
     "        rc = getattr(info, \"rc\", 0)           # a double that returns None means success",
     "        rc = 0", CONN_TESTS, "s1_a_publish or s1b or s1d"),
    ("S1b wakeup guards on `client is None` again (the PR #55 regression)", RT_FLEET,
     "        if not self._broker_connected():\n            return {\"ok\": False, \"device_id\": device_id, \"published\": False,\n                    \"acknowledged\": False, \"error\": \"no broker connection\",",
     "        if self.client is None:\n            return {\"ok\": False, \"device_id\": device_id, \"published\": False,\n                    \"acknowledged\": False, \"error\": \"no broker connection\",",
     CONN_TESTS, "s1b"),
    ("S1  `_broker_connected` trusts object existence", RT_CONNECTION,
     "        checker = getattr(client, \"is_connected\", None)",
     "        return True\n        checker = None",
     CONN_TESTS, "s1_a_publish or s1b"),
    ("S1  record the drop nowhere", RT_CONNECTION,
     "        self.publish_drops += 1", "        self.publish_drops += 0",
     CONN_TESTS, "s1_a_publish"),
    ("S2  a disconnect no longer stales the in-flight turn", RT_CONNECTION,
     "        for device_id in set(self._turn_seq) | set(self.robots):",
     "        for device_id in []:", CONN_TESTS, "s2_a_turn or s8"),
    ("S8  stale only the robots that already had a turn", RT_CONNECTION,
     "        for device_id in set(self._turn_seq) | set(self.robots):",
     "        for device_id in set(self._turn_seq):", CONN_TESTS, "s8"),
    # Anchor updated 2026-09-05: the four per-topic `subscribe()` calls became ONE list
    # subscribe, so that one SUBSCRIBE is answered by one SUBACK and `_on_subscribe` has a
    # single unambiguous event to gate readiness on. The mutation is the same one — do not
    # re-subscribe when the session comes back.
    ("S3  subscribe once and never again on reconnect", RT_CONNECTION,
     "        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])",
     "        if not self.last_broker_disconnect:\n            c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])",
     CONN_TESTS, "s3"),
    # ---- the SUBACK gate (2026-09-05) ------------------------------------------
    # `[runtime] broker connected` meant "we asked", never "the broker agreed", and a
    # robot announcing in that gap lost its `/state` and the QoS-0 config answering it.
    # These three are what a plausible half-fix looks like.
    ("S9  arm readiness inside the CONNACK instead of on the SUBACK", RT_CONNECTION,
     "        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])",
     "        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])\n"
     "        self._on_subscribe(c, None, 0, None, None)",
     READY_TESTS, "not_printed_by_the_connack"),
    ("S9b subscribe topic by topic again (four SUBACKs, readiness on the first)", RT_CONNECTION,
     "        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])",
     "        [c.subscribe(t) for t in self.SUBSCRIPTIONS]",
     READY_TESTS, "one_subscribe_call"),
    ("S9c latch the SUBACK across a disconnect", RT_CONNECTION,
     "        self.subscriptions_acked.clear()\n        self.last_broker_disconnect = time.time()",
     "        self.last_broker_disconnect = time.time()",
     READY_TESTS, "disconnect_disarms"),
    ("S4c drop on_connect_fail, so the retry loop is invisible", RT_CONNECTION,
     "        self.client.on_connect_fail = self._on_connect_fail",
     "        pass", CONN_TESTS, "s4c"),
    ("S4b drop the connection fields from /status", RT_LIFECYCLE,
     '                "broker_connected": self.broker_connected,',
     '                "broker_connected": True,', CONN_TESTS, "s4b"),
    # Anchor updated 2026-09-03: the `if device_id not in self.robots:` guard is gone —
    # `_device_connect` is idempotent per broker connection now (it has to be, or a robot
    # returning after a broker restart is never re-onboarded), so `_on_event` calls it
    # unconditionally. Deleting the call is still exactly C6 undone.
    ("S7  _on_event goes back to an ephemeral RobotContext (C6 undone)", RT_TURNS,
     "        self._device_connect(device_id)\n        robot = self.robots.get(device_id) or RobotContext(device_id=device_id, child=self.child)",
     "        robot = self.robots.get(device_id) or RobotContext(device_id=device_id, child=self.child)",
     CONN_TESTS, "s7"),
    ("S2b keepalive back to a literal nobody chose", RT_CONSTANTS,
     "KEEPALIVE_S = 30", "KEEPALIVE_S = 60", CONN_TESTS, "s2b"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(r[4], r[5]), timeout=MUTATION_TIMEOUT_S,
                     baseline=[pytest(sorted({r[4] for r in MUTATIONS}))]))
