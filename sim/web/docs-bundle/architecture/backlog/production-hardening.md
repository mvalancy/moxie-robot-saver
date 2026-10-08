# Production hardening: a supervisor that stays connected, and a store two processes can share

**Status:** shipped (P0 + P1, plus per-robot settings that survive a restart). Implemented in `mqtt/moxie_sdk/store.py`, `mqtt/supervisor/moxie_runtime/{connection,lifecycle,fleet}.py`, `mqtt/moxie_sdk/roster.py`, `mqtt/moxie_sdk/conn_telemetry.py` and `sim/tools/soak.py`. Tested by `sim/tests/test_store_concurrency.py`, `test_connection_resilience.py`, `test_connect_readiness.py`, `test_roster.py`, `test_conn_telemetry.py`, `test_soak_accounting.py`, `test_fleet_config.py` and the deep-tier soak `sim/run_soak.sh`. P2 (a SQLite backend) is deliberately unscheduled.

This work fixed two problems:

1. **The supervisor's MQTT connection was fragile.** If the broker was not up yet, the first connect
   killed the process. A refused CONNACK was logged as "broker connected". A publish made during a gap
   was dropped with no record. After a supervisor restart, robots that were still connected were never
   re-registered.
2. **The JSON store was only safe inside one process.** Two processes appending to the same record
   could each lose the other's write, silently.

This page records the design, the decisions and the reasons behind them. It was written as the build
brief for [OpenMoxie feature audit](../openmoxie-feature-audit.md) §4.4 #3. Fork A
(`Noonster77/openmoxie`, MIT) had already solved the connection half against real robots. We ported its
*behaviour* and none of its code (see [`ATTRIBUTION.md`](../../../ATTRIBUTION.md)).

## 0. The ceiling

**No physical Moxie has ever connected to our broker.** Everything here was built and proved against
[`virtual_moxie.py`](../../../sim/virtual_moxie.py) and a real mosquitto. That proves our half of each
failure: a dropped socket, a restarted process, two writers on one file. It does not prove the robot's
half. Nothing here shows that a real robot reconnects, that it accepts a config push in the middle of a
session, or that it re-prompts after about 20 s. The six assumptions in §9 that need hardware are all
still open.

## 1. What shipped, at a glance

| Piece | Where | Section |
|---|---|---|
| Cross-process locking: `JsonStore.transaction()` / `transaction_shared()`, a `.lock` sidecar per record, `flock(LOCK_EX \| LOCK_NB)` with a bounded backoff, a directory fsync after every rename | [`store.py`](../../../mqtt/moxie_sdk/store.py) | §3 |
| `MOXIE_STORE_LOCK_TIMEOUT_S` (default 2.0 s), which must be `< MOXIE_BRAIN_BUDGET_S` | [`config.py`](../../../mqtt/config.py) | §3.3 |
| Connection resilience C1–C6: async first connect, a 1→60 s reconnect ladder, keepalive 30, the CONNACK check, the `_publish()` helper, registering a device on its first event | [`connection.py`](../../../mqtt/supervisor/moxie_runtime/connection.py), [`lifecycle.py`](../../../mqtt/supervisor/moxie_runtime/lifecycle.py), [`constants.py`](../../../mqtt/supervisor/moxie_runtime/constants.py) | §4 |
| Readiness that means *subscribed*: `on_subscribe` prints `[runtime] subscriptions acknowledged by the broker`, and `/status` exposes `broker_subscribed` | `connection.py` | §4.4 |
| Durable robot roster (`fleet/roster.json`), re-pushed config on every connect | [`roster.py`](../../../mqtt/moxie_sdk/roster.py) | §8 P1 |
| Durable per-robot settings (`robots/<id>/config.json`), read back when the supervisor starts, so the re-push after a restart carries each robot's own settings | [`fleet.py`](../../../mqtt/supervisor/moxie_runtime/fleet.py) | §8 |
| Connection telemetry stream (`fleet/conn_events.json`), served on `GET /conn` and as `/status.connection_health` | [`conn_telemetry.py`](../../../mqtt/moxie_sdk/conn_telemetry.py) | §8 P1 |
| Clean shutdown on SIGTERM/SIGINT (`request_stop()` → `disconnect()`) | `lifecycle.py` | §8 P1 |
| The soak: `smoke` / `quick` / `week` profiles, twelve graded bars | [`run_soak.sh`](../../../sim/run_soak.sh), [`soak.py`](../../../sim/tools/soak.py) | §5 |
| A live proof that takes the broker away and brings it back | [`run_broker_outage.sh`](../../../sim/run_broker_outage.sh) | §8 P1 |
| Mutation checks: each guard is removed in turn and its test must go red | [`hardening_mutation_check.py`](../../../sim/tools/hardening_mutation_check.py), [`hardening_p1_mutation_check.py`](../../../sim/tools/hardening_p1_mutation_check.py) | §6 |

## 2. Why the old code failed

- **The first connect was fatal.** The old code was a blocking `client.connect(host, port, 30)`
  followed by `loop_forever()`. When no broker was listening, `run()` raised and the process died.
  Compose's `depends_on: service_healthy` hid this problem. Bare metal and the SIL harness did not.
- **`30` was the keepalive, not a timeout.** It is paho's third positional argument. We kept it on
  purpose (§4.1 C2).
- **Adding `connect_async` alone does not help.** `loop_forever()` still re-raises the first `OSError`
  unless `retry_first_connection=True` is also passed. `loop_start()` sets that flag internally, which is
  why Fork A's pattern works. Test S6 catches a half-done port.
- **CONNACK was never checked.** A refusal such as `rc=5` (not authorised) printed "broker connected"
  and then subscribed anyway.
- **Publish return codes were ignored.** At QoS 0, paho drops a publish when there is no socket; it does
  not queue it. The wakeup route checked `client is None`, not `is_connected()`, so it could answer
  `published: true` while the socket was dead.
- **A restart forgot which robots were connected.** Connect detection reads `$SYS/broker/log`, which
  mosquitto publishes live and never replays. The event path answered unknown devices from a throwaway
  context, with no config push and no `app.on_connect`.
- **The store's only lock was an in-process `threading.RLock`.** `append()` is a read-modify-write, so
  two processes could interleave and lose an item without any error.

A second writer already exists in practice. Running `run_smoke.sh` next to a local supervisor, running a
backup script, or hand-editing a permit file all write the same data tree. The read side was already a
tested promise (`test_device_permits.py`: a permit written by another process takes effect on the next
connect). The write side was not.

## 3. The cross-process store

### 3.1 The options

| | (a) WAL SQLite | (b) Single-writer process | (c) Advisory `flock` |
|---|---|---|---|
| Crash safety | best | unchanged | unchanged, plus a directory fsync |
| Concurrent readers | best | fine | fine (atomic replace gives each reader a snapshot) |
| Migration | real and one-way | none | none: same layout, same API |
| Two containers sharing a volume | yes (local FS only) | forbids the case and cannot enforce it | yes (local FS only) |
| Dependencies | none (`sqlite3` is stdlib and already used by `server/`) | none | none (`fcntl` is POSIX only) |
| Readable with `cat` | no | yes | yes |
| What it adds | multi-collection transactions, queries | nothing | correct cross-process `append` |

### 3.2 The choice: (c)

We chose **advisory `flock` on a per-record sidecar, behind a public
`JsonStore.transaction(device, collection)`, with the JSON left where it is on disk.**

1. Two questions had been merged into one. "Should the appliance have a database?" is a feature
   decision. "How do two processes write one record safely?" is a bug with a small fix.
2. SQLite's real advantage is transactions across several collections. No caller uses one, so it would
   buy nothing until those callers are rewritten.
3. Being able to read and delete a child's data with `cat` and `rm` is a privacy property this project
   values.
4. **The failure mode of (c) is bounded and visible.** A wedged lock holder costs one write. That write
   fails, returns `False`, and is recorded. The MQTT loop is never hung. A recorded refusal is
   acceptable. A silent loss is not.
5. (a) stays cheap to adopt later. The API and the on-disk layout are unchanged, so `MOXIE_STORE=sqlite`
   could be added as a backend swap.

**What would change this answer:** a caller that must write two collections atomically, a `/data` that
has to live on a network filesystem, or the console writing this tree at a real rate.

### 3.3 The mechanism, and the four traps

1. **Lock a sidecar, never the data file.** `os.replace()` swaps the inode, so a lock on the data file
   protects nothing. The lock is `f"{path}.lock"`. It is created once and never replaced or deleted,
   including when the record itself is deleted.
2. **Take the `RLock` outside and the `flock` inside, with one `open()` per outermost acquisition.**
   `flock` belongs to an open file description, so two `open()` calls in one thread would deadlock.
   A nested `transaction()` on the same record re-enters without opening a second descriptor.
3. **Never block the MQTT loop.** Waiting is `LOCK_EX | LOCK_NB` in a retry loop:
   `LOCK_BACKOFF_BASE_S = 0.0005`, capped at `LOCK_BACKOFF_CAP_S = 0.002`, plus jitter. The retry
   exponent is clamped at `LOCK_BACKOFF_MAX_SHIFT = 32`. The clamp matters: without it, any timeout above
   about 2.05 s raised `OverflowError` (A25). The whole wait is bounded by `MOXIE_STORE_LOCK_TIMEOUT_S`
   (default **2.0 s**), and `config.py` refuses to start unless that value is `< MOXIE_BRAIN_BUDGET_S`.
   When the budget runs out, the method raises `StoreLockTimeout`, which callers turn into their own
   failure shape (`append` returns `False`). The store counts these in `JsonStore.lock_timeouts`,
   connection telemetry records a `lock_timeout` row with `waited_s`, and `/status` reports
   `store_lock_timeouts`.
4. **`fcntl` is POSIX-only, and the fallback says so.** If the import fails, `transaction()` falls back
   to the `RLock` alone, and `mqtt/run.py` prints one startup line saying cross-process locking is
   unavailable.

In the same change, `_fsync_dir()` fsyncs the directory after every `os.replace`, so the rename itself
is durable. A filesystem that refuses a directory fsync (EINVAL) costs durability, not the write.

### 3.4 What we gave up

- **Atomicity across collections.** An interrupted pack import or telemetry roll-up can leave two
  collections out of step. Both cases are recoverable.
- **A query layer.** Every filter reads the list and loops in Python.
- **Schema and migrations.** No collection declares its shape.
- **Network filesystems.** `/data` on NFS or SMB is unsupported. SQLite WAL would be worse there.
- **Windows.** Without `fcntl` there is no cross-process safety.

## 4. Connection resilience

### 4.1 The changes

| # | Change | Why |
|--:|---|---|
| C1 | `reconnect_delay_set(1, 60)` + `connect_async(host, port, 30)` + `loop_forever(retry_first_connection=True)` | All three are needed together (A2). A 60 s ceiling covers a router reboot (30–60 s). paho's default of 120 s means two minutes of a child talking to nothing. |
| C2 | Keepalive stays at **30** (`KEEPALIVE_S`) | That is half paho's default, so a half-open socket is noticed within about 30 s. The broker declares us dead at 45 s (1.5 × keepalive). |
| C3 | `_on_connect` checks `rc` | On a refusal it records `last_connect_error` (`connack_string`), writes a `refused` telemetry row and **subscribes to nothing**. |
| C4 | `on_disconnect` + `on_connect_fail` | These maintain `broker_connected`, `last_broker_connect`, `last_broker_disconnect` and `last_connect_error`, which appear in `/status`, `recent` and the telemetry stream. |
| C5 | Every publish goes through `_publish()` | It returns `(ok, reason)`, and every drop is counted (`publish_drops`) and recorded. The wakeup route answers `published: false` with the reason *"The supervisor is not connected to the broker."* |
| C6 | An event from an unregistered device registers it | The event path and the `/state` path now agree, so a robot that was connected across a supervisor restart gets its config push and `app.on_connect`. |

### 4.2 A turn in flight across a reconnect is abandoned, never replayed

`_on_disconnect` increments `_turn_seq` for every known robot. Every in-flight worker's answer then fails
the existing `_is_stale` check. No new machinery is involved, and the MQTT loop remains the only writer
of `_turn_seq` (A19). The drop is noted.

Replaying the answer would be harmful. After a gap the robot has already re-prompted with a new
`event_id`, so a late chunk would answer a question the child has given up on. We also send no late
`ERROR_TIMEOUT` or `ERROR_OFFLINE` result, because nothing in our corpus says what a robot does with an
error for an `event_id` it has abandoned.

### 4.3 QoS stays 0, on purpose

- QoS 1 with a clean session queues nothing that survives the reconnect.
- QoS 1 with a persistent session would deliver the stale answers that §4.2 refuses to deliver.
- Recovery already exists at the application layer: the robot re-prompts, and the supervisor re-pushes
  config when it registers a device (C6).

### 4.4 What each failure looks like

| Case | Robot sees | We do |
|---|---|---|
| Supervisor drops, broker stays up | nothing; its turn goes unanswered and it re-prompts | C1 reconnects, the roster resume and C6 re-push config, C4 and C5 record the gap |
| Broker restarts | its own session drops; whether and how fast it reconnects is **unverified (A5)** | as above, and every robot is re-onboarded on its next packet (§8 P1) |
| Supervisor restarts | nothing, if the broker stayed up | the roster resume re-pushes config within seconds of CONNACK, with each robot's saved settings (§8) |
| Broker absent at boot | nothing | C1 turns this into a retry loop instead of a dead process |

**Readiness.** `[runtime] broker connected` means only that CONNACK said yes. `subscribe()` merely
queues a packet, so a robot's `/state` sent right after CONNACK could be lost. The runtime therefore
subscribes in one call and prints `[runtime] subscriptions acknowledged by the broker` on the SUBACK.
`run_smoke.sh`, `run_scenarios.sh`, `helpers_stack` and `soak.py` all wait for that signal, either the
log line or `/status.broker_subscribed`. This behaviour is pinned by
[`test_sil_supervisor_readiness.py`](../../../sim/tests/test_sil_supervisor_readiness.py) and
`test_connect_readiness.py`.

## 5. "Stays connected for a week" as a runnable test

We cannot wait a week, so the soak tests the property we actually mean: **no state grows without bound,
no failure goes unrecorded, and every recovery path fires**. It gets there by raising the rate of events
instead of the length of the run. [`test_clock_dependence.py`](../../../sim/tests/test_clock_dependence.py)
forbids unlisted wall-clock reads, so every assertion is checked against a counter or an injected clock,
never a stopwatch.

### 5.1 Tier 1: hermetic fault injection (fast tier)

`FakeClient` in [`helpers_runtime.py`](../../../sim/tests/helpers_runtime.py) provides `up(rc=0)`,
`drop(rc=7)` and `refuse(rc=5)`. The whole S-series in §6 runs with no broker, no network and no sleeps.

### 5.2 Tier 2: the SIL soak (deep tier, opt-in)

`bash sim/run_soak.sh --profile {smoke|quick|week}` runs a real mosquitto in Docker, a real
`mqtt/run.py` with `MOXIE_APP=echo` (no gateway calls), and N virtual robots. It **never runs in the fast
tier**, because a slow or flaky soak would get disabled (R5). `ci-deep.yml` runs `week` nightly at 03:17
UTC and `quick` on a promotion PR.

| Profile | Minutes | Robots | Broker restarts | Supervisor restarts | Store contention | Mid-write SIGKILLs |
|---|--:|--:|--:|--:|---|--:|
| `smoke` | 1 | 1 | 1 | 1 | 4 processes × 100 appends | 3 |
| `quick` | 5 | 2 | 4 | 2 | 4 × 250 | 10 |
| `week` | 60 | 3 | 24 | 4 | 4 × 2 500 (10 000 on **one** record) | 20 |

The `week` profile is sized so one hour matches a household week. About 24 broker restarts is one drop
per 7 h behind a flaky router. The turn gap gives about 4 000 turns, against about 700 in a heavy real
week. For the store alone, with no Docker or broker needed, run
`python3 sim/tools/soak.py --only-contention --writers 4 --appends 250`.

### 5.3 The bars

| # | Bar |
|--:|---|
| A1 | Turn success counting only turns issued while the broker was up (from the fault injector's recorded windows): **100 %** |
| A2 | Turns *lost* to a fault ≤ 1 per robot per fault, and every disconnect recorded |
| A3 | Broker back up → supervisor re-subscribed: **p95 ≤ 3 s, max ≤ 65 s** |
| A4 | Every robot re-pushed config after a supervisor restart, within **5 s** |
| A5 | Lost updates across processes: **0**, and no writer crashed. The identity `attempted == on_disk + refused` must hold, so a recorded refusal is never counted as a silent loss. |
| A6 | Unreadable or truncated records after mid-write SIGKILLs: **0** |
| A7 | Supervisor RSS growth from baseline to end: **≤ 10 %** |
| A8 | Open file descriptors from baseline to end: **≤ +5** (catches leaked `flock` fds) |
| A9 | Bounded state: `recent` at its cap, `robots` ≤ robots seen, `conn_events` ≤ its cap |
| A10 | Tracebacks in the supervisor log: **0** |
| A11 | Store writes refused on lock timeout: expected 0; any refusal must be recorded, never silent |
| A12 | Every robot is re-onboarded (`seen_since_connect`) after every broker restart, and there are **0 ghosts** |

A12 was added after a bug slipped past A1–A11 (A22 in §9). Those bars ask about the appliance, and none
of them asked whether the robot got anything.

### 5.4 What the soak cannot prove

Read this before quoting any soak number. The soak proves **our** half only:

- It cannot show that a real Moxie reconnects after a broker restart, or how fast (A5).
- It cannot show that a real Moxie accepts a config push mid-session (A6).
- It cannot show that the re-prompt window is really about 20 s (A4).
- It cannot show that a real Moxie's client id is stable across reconnects (A17).
- **It says nothing about a week.** An hour at a raised rate is a *rate substitution*. That stands in
  for failures that scale with the number of events. It does not stand in for failures that scale with
  time: a slow leak, a clock rollover, an expiring certificate, a log filling a disk.

Contention results depend on load, not only on contention. The same 4 × 250 run gave 0 refusals on an
idle box and a few under load. That is why the harness reports a *rate* and asserts the *identity*.

## 6. Tests

| # | Asserts | File |
|--:|---|---|
| T1 / T1b | Two processes appending lose nothing; the negative control (no lock) does lose writes | `test_store_concurrency.py` |
| T2 / T2b | A nested `transaction()` neither deadlocks nor opens a second fd (§3.3 #2) | same |
| T3 | Two threads serialize through `transaction()` | same |
| T4 / T4b / T4c | The `.lock` inode survives a write and a delete, and is never mistaken for a record (§3.3 #1) | same |
| T5 / T5b / T5c | A lock held past the timeout fails the write, records it, and backs off rather than spinning | same |
| T6 | The lock timeout must be inside the turn budget | same |
| T7 | Without `fcntl` the store still works and `run.py` prints the note | same |
| T8 | A SIGKILL between write and replace never leaves a torn file | same |
| T9 / T9b | The directory is fsynced after the rename; a refused fsync does not fail the write | same |
| T10–T11 | `append` reports real failure; the soak's identity holds under contention; no backoff exponent is unbounded (A25) | same |
| S1 (a–e) | Every publish during a drop returns not-ok and is recorded; the wakeup route refuses; every call site uses `_publish()` | `test_connection_resilience.py` |
| S2 / S8 | A turn started before a drop never publishes after it; the drop bumps `_turn_seq` for every robot | same |
| S3 / S4 | Subscribe exactly once per successful connect; a CONNACK refusal subscribes nothing | same |
| S5 / S2b | The reconnect ladder is 1, 2, 4 … capped at 60; keepalive is 30 | same |
| S6 | A supervisor started with no broker retries instead of dying | same |
| S7 (a–c) | An event from an unregistered but permitted device registers it; a stranger is still refused | same |
| — | Roster resume, the ghost fix, per-run `MOXIE_DATA_DIR` in every SIL script | `test_roster.py` |
| — | Per-robot settings (volume, bedtime, brain, `NO_DATA`, a cleared value) survive a restart; a damaged record, a value the whitelist now refuses and a pinned-away brain are dropped at load and never pushed; a refused save is reported | `test_fleet_config.py` |
| — | The telemetry stream, its cap and summary | `test_conn_telemetry.py` |
| — | The soak's own A1/A2 accounting | `test_soak_accounting.py` |
| K1 | `run_soak.sh` meets every §5.3 bar | deep tier (`sim/ci/ci-deep.yml`) |

## 7. Known gaps

- **The lock timeout (A13) and the reconnect ceiling (A14) are chosen, not measured.** P1 built the
  instrument (`lock_timeout` rows carry `waited_s`, and reconnect gaps are recorded). Only a real
  appliance running for a week produces the measurement.
- **Stray `.tmp` files (A24).** A writer killed mid-write leaves `<record>.<pid>.tmp` behind. Correctness
  is unaffected, but the files accumulate. No sweep exists, because pids recycle and deleting a live
  writer's scratch file would be a worse bug.
- **No hardware.** A4–A7, A17 and A20 remain open (§9).
- **P2 is not scheduled** (§8).
- **Saved per-robot settings are read once, at start.** Another process's edit to
  `robots/<id>/config.json` takes effect at the next restart, not on the next push (the fleet layer,
  by contrast, is re-read on every push).
- **Two owner questions are open** about the per-robot record and a factory reset. Neither is built:
  1. Should a factory reset clear the robot's saved settings and its roster entry? That is robot-level
     state, not the child's data, which the reset keeps
     ([robot lifecycle](../../features/robot-lifecycle.md#built-here-unpair-and-factory-reset)).
  2. Should the reset sheet's optional erase gain safety-journal and transcript checkboxes? That would
     close the lifecycle page's "no erase control" gap by the parent's choice rather than
     automatically.

## 8. Phases and risks

**P0** (shipped): the §3 store locking and the §4.1 connection changes C1–C6.

**P1** (shipped) added:

- **The durable roster** (`fleet/roster.json`, capped at `MAX_DEVICES = 64` with least-recently-seen
  eviction). On every successful CONNACK, `resume_roster()` re-pushes config to permitted roster robots,
  off the network thread. `MOXIE_ROSTER_RESUME=0` disables it. Un-permitting a robot calls `forget()`.
- **The connection telemetry stream** (`fleet/conn_events.json`, `MAX_EVENTS = 400`, overridable with
  `MOXIE_CONN_MAX_EVENTS`). It records seven kinds of row: `connect`, `disconnect`, `connect_fail`,
  `refused`, `publish_drop`, `lock_timeout`, `shutdown`. `connect` rows carry gap durations and
  `lock_timeout` rows carry `waited_s`. It is shown on `GET /conn`, in `/status.connection_health`, and
  as a strip on the console's insights card.
- **Clean shutdown.** SIGTERM or SIGINT calls `request_stop()`, which writes a `shutdown` row and then
  `disconnect()`s, so the broker sees the disconnect at once instead of waiting for keepalive expiry.
- **The ghost fix.** Two bugs turned out to be one: *a cached belief about a robot outliving the robot's
  real state*.
  - The roster ghost (A22): `_device_connect` early-returned for a robot already in `self.robots`. A robot
    that came back after a broker restart was listed as present but never re-onboarded.
  - The vision/STT latch (A23): the robot drops event subscriptions when a module exits, but
    `_vision_subscribed` was never cleared.

  Both now clear through `_forget_robot_state()` whenever continuity with the robot breaks. Membership is
  kept and only *confirmation* (`_seen_since_connect`) is cleared. Clearing membership outright would
  claim knowledge we do not have, would re-onboard every robot after a 200 ms blip, and would end the
  child's conversation. Unconfirmed robots show `seen_since_connect: false` on `/status`; they are
  labelled, not deleted. `run_broker_outage.sh` phase 5c asserts exactly that field, fatally.
- **Hermetic SIL scripts.** Each SIL script now uses a per-run `MOXIE_DATA_DIR`, so one run's throwaway
  ids never reach the next run's roster resume.

**Per-robot settings survive a restart** (shipped after P1). The per-robot config layer (volume,
brightness, bedtime, wake settings, timezone, look, scheduled activities, brain pick and the
data-sharing `logging_policy`) used to live only in the supervisor's memory. A restart dropped it, and
the roster resume then re-pushed the fleet-only document: the robot's settings went back to the house
rules or the defaults, and a per-robot `NO_DATA` lapsed, so the server began keeping that child's
transcript, long-term memory, activity record and safety-journal excerpts again. Now:

- **Written on every edit.** `update_config` saves the robot's layer to `robots/<id>/config.json`
  through the store's locked, atomic write, before the `NO_DATA` purge and before the push. A write the
  store refuses still applies to the running supervisor and puts a "NOT saved" line in the activity
  feed.
- **Read once, at construction**, before the transcript sweep in `_load_memory()`. The brain picker,
  the safety journal, the status snapshot and the console's `GET /config` read the per-robot dict
  directly, so a lazy read would leave them blind until something else touched the config.
- **Only what the console could have set.** Each stored key is re-checked on its own by
  `sanitize_config_overrides`, the `POST /config` whitelist. A key it now refuses, or a brain pick the
  current `MOXIE_APP` pin refuses, is dropped at load with one log line and never pushed. A damaged or
  non-object record reads as no settings, with one log line. Loading writes nothing: the next save for
  that robot rewrites its record.
- **Not the telehealth mode.** `moxie_mode` is not in the whitelist, so "Be Moxie" is not kept. Its
  session lives in RAM, and a restart still hands the robot back to its own brain.
- **Not cleared by an unpair or a factory reset**, as before (§7 records the owner questions).

Whether a physical Moxie applies the re-pushed settings is the open A6/A7 question below, not
something this change can show.

**P2** (unscheduled, size L): a `MOXIE_STORE=sqlite` backend behind the unchanged API, only if a caller
needs a transaction or a query (§3.2). It would keep the JSON tree as the export format.

| # | Risk | Mitigation |
|--:|---|---|
| R1 | A lock on the data file instead of the sidecar looks right but protects nothing | T4 |
| R2 | `connect_async` lands without `retry_first_connection` | S6 |
| R3 | `RLock`/`flock` nesting deadlocks | T2, T3 |
| R4 | A wedged lock holder blocks the MQTT loop | `LOCK_NB` plus a bounded timeout, with the budget guard (T5, T6) |
| R5 | The soak is flaky on a loaded runner and gets disabled | deep tier only; readiness is polled; counters and injected clocks instead of stopwatches |
| R6 | §3 is read as "we decided against a database" | §3.2 point 5: declined *yet*, not declined |
| R7 | `flock` fds leak | soak bar A8 |
| R8 | "Hardened" is read as "proven with a robot" | §0, §5.4 |

## 9. Assumption ledger (the rows that still matter)

| # | Assumption | State |
|--:|---|---|
| A2 | `loop_forever()` re-raises the first `OSError` unless `retry_first_connection=True` | proven from paho source; S6 |
| A3 | A QoS 0 publish with no socket is dropped, not queued | proven from paho source; S1 |
| A4 | The robot re-prompts an unanswered turn after about 20 s | inferred from upstream; **needs hardware** |
| A5 | A real Moxie reconnects on its own after a broker restart | **unverified; needs hardware** |
| A6 | A real Moxie accepts a `/config` push mid-session | **unverified; needs hardware** |
| A7 | A duplicate config push is harmless | inferred; **needs hardware** |
| A8 | `flock` works on a Docker named volume | proven: 500/500 appends with the lock, 250/500 lost without it |
| A9 | `flock` over NFS/SMB is unreliable | inferred; declared unsupported |
| A12 | The directory must be fsynced for a rename to be durable | inferred (POSIX); implemented as `_fsync_dir`, tested by T9 |
| A13 | 2.0 s is the right lock timeout | **chosen, not measured** |
| A14 | 60 s is the right reconnect ceiling | **chosen, not measured** |
| A15 | `$SYS/broker/log` is live-only and never replayed | proven; the reason C6 exists |
| A17 | A real Moxie's `d_<uuid>` client id is stable across reconnects | **unverified; needs hardware** |
| A19 | Bumping `_turn_seq` in `on_disconnect` keeps the single-writer invariant | proven: paho dispatches it on the loop thread; S8 |
| A20 | A week is the right horizon at all | **unverified; needs a robot in a house** |
| A21 | A backoff cadence of 0.5 ms base / 2 ms cap does not starve a contended writer | measured: 0 refusals in 1 000 appends, against about 5 at 10 ms / 200 ms |
| A22 | A robot returning with the same id after a broker restart is re-onboarded | was **false**; fixed in P1; soak bar A12 |
| A23 | Our vision/STT subscription latch stays true while the robot holds it | was **false**; fixed in P1 |
| A24 | A killed writer's `.tmp` gets cleaned up | **false; not fixed** (§7) |
| A25 | The backoff is safe at any timeout | was **false** (`OverflowError` above about 2.05 s); fixed by the exponent clamp |
| A26 | A supervisor restart keeps each robot's parent settings | was **false** (memory only; the roster resume re-pushed the fleet-only document); fixed by `robots/<id>/config.json` (§8) |

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[MQTT and the conversation](../mqtt-and-conversation.md) ·
[Config & telemetry contract](../config-and-telemetry-contract.md) ·
[Broker auth](security-broker-auth.md) · [Sandboxed extensions](sandboxed-extensions.md) ·
[Live Sim demo](live-sim-demo.md) · [Orchestration plan](../agent-workflow.md) ·
[Remote-chat protocol](../../reverse-engineering/protocol/remote-chat-protocol.md) ·
[Attribution](../../../ATTRIBUTION.md)
