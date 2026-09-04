# 🛡️ Production hardening — a robot that stays connected, and a store two processes can share

> **Backlog brief v1 · 2026-09-03.** The build document for
> [OpenMoxie feature audit](../openmoxie-feature-audit.md) **§4.4 #3** — *"Production hardening for a
> robot that stays connected"* — which the audit ranked third and marked 🟠 **needs-a-spec** for one
> stated reason:
>
> > *"It is 🟠 rather than 🟢 because the cross-process story is a **design** decision (WAL-backed
> > SQLite? a single-writer process? file locks?) that ADOPT #8 has deferred three times; deciding it
> > in a PR review is how it gets decided badly."*
>
> **This page's whole job is to remove that decision from the PR review.** §3 makes it, with the
> argument written out so it can be argued with later. §4 settles connection resilience. §5 turns
> *"stays connected for a week"* into a test we can actually run without hardware, with numbers.
>
> **Clean-room.** Every wire claim comes from our own recovered corpus — chiefly
> [`mqtt-and-conversation.md`](../mqtt-and-conversation.md) and
> [`remote-chat-protocol.md`](../../reverse-engineering/protocol/remote-chat-protocol.md) — never from
> the vendor app. **OpenMoxie** (MIT, © Justin Beghtol) and its fork
> [`Noonster77/openmoxie`](https://github.com/Noonster77/openmoxie) are read as prior art and cited by
> path: Fork A solved exactly this problem first, and **it has run robots in houses and we have not**,
> so where its shape is right we say so and port the *behaviour*. No upstream code enters this tree.
> See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

---

## 0. The ceiling, stated first — before anything optimistic

**No physical Moxie has ever been on our broker. Not for a week, not for an hour.** Everything below is
built and proved against a simulated robot ([`virtual_moxie.py`](../../../sim/virtual_moxie.py)) and a
real mosquitto. That is enough to prove *our* half of every failure — a socket that dies, a process that
restarts, two writers on one file — and it is **not** enough to prove the other half: that a real robot
reconnects, that it tolerates a config pushed forty minutes into a session, or that its re-prompt window
is really ~20 s.

So the honest framing of this brief is: **it closes the failures we can observe, and it makes the ones we
cannot observe visible when they finally happen.** Six of the twenty assumptions in §9 (A4, A5, A6,
A7, A17, A20) need a physical robot and are marked as such. None of them blocks P0.

**One-sentence definition of done (P0):** *the supervisor comes up before the broker does, survives the
broker going away and coming back without losing a turn silently or lying about one, and two processes
writing the same appliance's data can no longer lose each other's writes.*

---

## 1. Why this is 🟠 today, and exactly what a spec has to remove

The audit's row names two defects and one owed decision. The defects are cheap. The decision is why an
agent could not start:

| | The defect | The owed decision |
|---|---|---|
| **Connection** | [`moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py):484 is a plain blocking `client.connect(...)` | none — the fix is known, and §4 just writes it down precisely, because the *precise* version is not what a PR review would guess (§2.2) |
| **Store** | [`store.py`](../../../mqtt/moxie_sdk/store.py):71 is an in-process `threading.RLock()` and nothing else | **this one.** WAL-backed SQLite, a single-writer process, or advisory file locks — three answers with different costs, and ADOPT #8 deferred it three times |

An agent briefed on "harden the store" without §3 would spend its run arguing the option table into
existence and then implementing whichever option it argued itself into, in one PR, unreviewed against
the alternatives. That is the failure mode the 🟠 marker exists to prevent.

---

## 2. What is actually true today — verified on `origin/dev`, 2026-09-03

### 2.1 The store, exactly

[`mqtt/moxie_sdk/store.py`](../../../mqtt/moxie_sdk/store.py), 763 lines. One JSON file per
`(device, collection)` under `$MOXIE_DATA_DIR`, plus a `fleet/` tier for appliance-wide records.

- **:71** — `self._lock = threading.RLock()`. That is the entire concurrency story. There is **no
  `fcntl`, no `flock`, no lock file, no advisory locking of any kind** anywhere in the module.
- **:124‑140** — `_write_path()`: `os.makedirs` → write `f"{path}.{os.getpid()}.tmp"` → `fh.flush()` →
  **:132** `os.fsync(fh.fileno())` → **:133** `os.replace(tmp, path)`. Two observations:
  - the temp name already carries the **pid**, so two processes do not collide on the scratch file —
    the module is *half* multi-process aware, and that half is the easy half;
  - the **directory is never fsynced after the rename**. The file's contents are durable; the
    *directory entry* pointing at them is not. On ext4 with `data=ordered` you get old-or-new anyway,
    which is why nobody has been bitten — but that is the filesystem being kind, not the code being
    correct (assumption A12).
- **:142‑156** — `append()`: read → mutate → write, inside `self._lock`. **This is the operation that
  loses data across processes.** Two processes appending to `safety_events` interleave read-read-write-write
  and one item vanishes, silently, with no error anywhere.
- **:562, :653, :691, :727, :753** — `MemoryStore` reaches *into* `self.store._lock` for its own
  read-modify-writes (`merge`, `erase_item`, `edit_item`, `note_used`, `erase`), with the comment
  *"read-modify-write, like `JsonStore.append`"*. Any fix must be reachable from there, which means it
  has to be a **public method on `JsonStore`**, not private machinery inside `_write_path`.

**Fourteen collections, not thirteen.** The audit's ADOPT #8 row counted thirteen on 2026‑09‑03 morning;
the sandboxed-extensions P0 slice added `ext_events` the same day. The full set, from a repo-wide sweep:

```
fleet:   config · permits · voice · content_items · content_packs · content_backup
device:  memory · mentor_behaviors · schedule_explain · safety_events · safety_counts ·
         telemetry_packets · telemetry_daily · ext_events
```

Nothing about this brief depends on the count being 14 rather than 13 — it depends on the count
**still going up**, which it does, roughly once per shipped slice.

### 2.2 The connection, exactly — and the part a PR review would get wrong

[`mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py), 3 521 lines.

- **:215** — `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="supervisor")`. A fixed client id,
  the default `clean_session`, no `will_set`.
- **:484** — `self.client.connect(self.host, self.port, 30)`; **:485** — `self.client.loop_forever()`.

Four things follow, and only the first is the one everybody names:

1. **The first connect is fatal.** `connect()` raises `ConnectionRefusedError` / `socket.gaierror`
   straight out of `run()` if the broker is not listening yet, and the supervisor process dies. Under
   `docker compose up` this is survivable only because `depends_on: broker: condition: service_healthy`
   holds the container back ([`docker-compose.yml`](../../../docker-compose.yml)); on bare metal, in the
   SIL harness, and any time the broker restarts *before* the supervisor's first connect, it is not.
2. **`30` is the keepalive, not a timeout.** `connect(host, port, keepalive)` — the third positional
   argument of paho's signature. The audit's phrase *"a plain blocking connect"* is right about the
   blocking; the `30` is a deliberate-looking number that nobody chose. §4.1 keeps it and says why.
3. **`connect_async` alone does not fix (1).** This is the trap. `loop_forever()`'s default is
   `retry_first_connection=False`, and its first block re-raises `OSError` from the initial
   `reconnect()` unless that flag is set. `loop_start()` gets it right by accident — its thread body is
   `loop_forever(retry_first_connection=True)` — which is why Fork A's `connect_async` + `loop_start()`
   works. A build agent that ports "add `connect_async`" onto our `loop_forever()` **changes nothing**
   and the test that would catch it is S6 in §6.
4. **After a successful first connect, paho already reconnects.** `_reconnect_wait()` doubles from
   `_reconnect_min_delay = 1` to `_reconnect_max_delay = 120`, **with no jitter**, and `_on_connect`
   (:943) re-subscribes on every successful reconnect. So the honest statement of the gap is *not*
   "there is no reconnect" — it is: **the first connect is fatal, the ceiling is 120 s, nothing observes
   the disconnect, and nothing notices a rejected CONNACK.**

Which brings the two defects nobody has written down yet:

- **:943‑947 — `_on_connect` never checks `rc`.** It prints `f"[runtime] broker connected rc={rc}"` and
  subscribes unconditionally. A CONNACK refusal — `rc=5`, *not authorised*, which PR #44's broker
  credential made reachable for the first time — logs the words **"broker connected"** and then
  subscribes into a socket the broker is closing. This is the same class of bug as the `wakeup` route
  that returned `{"error": null}` and published nothing (audit §4.1, PR #55): a comfortable lie in the
  one place an operator looks.
- **Eight `publish()` call sites, zero return codes checked** — :1455, :1482, :1526, :2469, :3157,
  :3410, :3459, :3521. All default **QoS 0**, no retain. paho's `publish()` at QoS 0 calls
  `_send_publish` directly and returns `MQTT_ERR_NO_CONN` on `info.rc` when there is no socket; the
  message is **not** queued. So a reply published during a gap is discarded and *nothing in this
  process knows*. And the honesty leak lands in the operator's face: :1520's connection check is
  `if self.client is None` — object existence, not `client.is_connected()` — so the wakeup route
  answers `{"published": true}` while the socket is dead.

One more, structural: **a supervisor restart forgets which robots are connected.** `self.robots` is
memory-only, and connect detection is a regex on `$SYS/broker/log` (§3.4 of the mqtt contract), which the
broker publishes **live and does not replay** on re-subscribe. `_on_state` at :1023‑1025 has the
documented fallback (*"fallback if we missed the log line"*) — but `_on_event` at :2025‑2026 does **not**:
it builds an *ephemeral* `RobotContext` and answers the turn without ever registering the robot. So after
a supervisor restart, with the robot still happily connected, the robot is answered — with no config push,
no `app.on_connect`, no presence state — potentially for the rest of the session, because a real Moxie
publishes `/state` on connect and we just missed that.

### 2.3 Who writes what, under `docker compose up`

| Process | Reads/writes | Where |
|---|---|---|
| `supervisor` (`mqtt/run.py`) | the 14 `JsonStore` collections | `moxie-supervisor-data:/data` |
| `console` (`server/run.py`) | its own SQLite — users, children, robots, pairings | `moxie-console-data:/data/moxie.db` |

A repo-wide sweep finds `JsonStore` constructed in exactly three non-test places —
[`mqtt/run.py`](../../../mqtt/run.py):57, [`mqtt/config.py`](../../../mqtt/config.py):461 and
[`store.py`](../../../mqtt/moxie_sdk/store.py):461 (`MemoryStore`'s default) — **all inside the
supervisor**. Nothing under `server/` touches it.

**So today there is exactly one writer, and the hazard is not hypothetical anyway.** The three ways a
second one appears, in ascending order of likelihood:

1. **A developer**, right now. [`sim/run_smoke.sh`](../../../sim/run_smoke.sh) launches
   `python3 mqtt/run.py` with **no `MOXIE_DATA_DIR` override**, so it uses the default `mqtt/data/`.
   Running the smoke while a local supervisor is up is two writers on one tree, today, on every
   contributor's box.
2. **An operator or a script** — a backup, a hand-edit, a future `moxie-admin`.
   [`sim/tests/test_device_permits.py`](../../../sim/tests/test_device_permits.py):239‑250 already models
   this and asserts it works: *"a permit written behind the runtime's back (another process, a hand edit)
   must take effect on the next connect"*. The **read** side of cross-process sharing is a shipped,
   tested promise. The write side is not.
3. **The console**, tomorrow. Audit §4.4 #10 (*"one identity, one guided first run"*) is the item that
   reconciles the parent app's child records with the supervisor's — and whatever shape that takes, it
   ends with a second process reading, and probably writing, this tree.

### 2.4 Prior art — Fork A, which has actually done this

`Noonster77/openmoxie` (MIT, `a97c85c0`) ships precisely this hardening, and its choices are worth
stating because they were made against real robots in real houses:

| What | Where | What it does |
|---|---|---|
| SQLite WAL | `site/hive/apps.py` | on `connection_created`: `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000` |
| Writer serialization | `site/hive/mqtt/util.py::run_db_atomic` | a **process-wide `threading.RLock`** + `transaction.atomic`, 4 attempts, `0.05 × 2ⁿ` backoff, retrying only on *"locked"* |
| BEGIN IMMEDIATE | `site/openmoxie/settings.py`:114‑117 | `'timeout': 10, 'transaction_mode': 'IMMEDIATE'` — takes the write lock at BEGIN so a read-then-write cannot fail late |
| Connection | `site/hive/mqtt/moxie_server.py`:175‑179 | `reconnect_delay_set(min_delay=1, max_delay=30)` + `connect_async(...)`, with the comment *"The previous synchronous attempt killed the supervisor thread permanently on the first failure"* — the exact bug at our :484 |
| CONNACK honesty | `moxie_server.py`:206‑215 | `on_connect` checks `rc`, logs `mqtt.connack_string(rc)`, records `last_connect_error`, and **returns without subscribing** |
| Observability | `moxie_server.py`:228‑245 | `on_connect_fail` + `on_disconnect` maintain `broker_connected` / `last_broker_connect` / `last_broker_disconnect` / `last_connect_error` |
| API pin | `moxie_server.py`:74‑80 | `CallbackAPIVersion.VERSION1` pinned explicitly, *"Without this, a future callback API default can add parameters to on_connect and silently stop all subscriptions"* |

**Read this table as three separate ports, not one.** The connection half (rows 4‑7) is right and we take
its behaviour wholesale in §4. The storage half (rows 1‑3) is right *for a Django app that already has an
ORM* — and that is the premise §3 has to examine rather than inherit, because we do not have one, and
because their cross-process lock is a `threading.RLock` too: what actually makes their story safe across
processes is **WAL + `busy_timeout`**, not the lock.

We also already run stdlib SQLite with WAL in this repo:
[`server/moxie_server/db.py`](../../../server/moxie_server/db.py):13‑17 — `sqlite3.connect(...)` then
`PRAGMA journal_mode=WAL`, no ORM, 120 lines. So "SQLite is a new dependency" is **false here** and §3
must not pretend otherwise.

---

## 3. The cross-process story — three options, evaluated, then one choice

The six questions that actually decide this are the audit's own, plus the two this codebase keeps
returning to:

**crash safety · concurrent readers · migration cost from fourteen JSON collections · does it survive
`docker compose up` with two containers · dependency weight in a slim pinned image · can a parent still
read their child's data with `cat`.**

### 3.1 The comparison

| | **(a) WAL-backed SQLite** | **(b) A single-writer process** | **(c) Advisory file locks (`fcntl.flock`)** |
|---|---|---|---|
| **The shape** | Re-implement `JsonStore` over one `moxie.db` with a `kv(scope, device, collection, value_json, updated_at)` table. The API (`read`/`write`/`append`/`delete`/`devices`) is unchanged — the module's own docstring says it was designed for exactly this. | Declare the supervisor the sole writer. Everyone else reads the files directly and writes through the supervisor's existing status HTTP (`:8930`, which already serves `POST /config`, `/permits`, `/telehealth`, …). | Keep JSON on disk. Add a **sidecar lock file** per record and a public `JsonStore.transaction(...)` that takes the in-process `RLock` then an `flock`, wrapping the whole read-modify-write. |
| **Crash safety** | **Best.** WAL + `synchronous=NORMAL` survives a process kill; `FULL` survives power loss. Torn writes are structurally impossible. | Unchanged from today: atomic replace, and the missing directory fsync. | Unchanged from today **plus** the directory fsync this brief adds. Old-or-new, never torn. `flock` adds serialization, not durability — and this row is honest that those are different things. |
| **Concurrent readers** | **Best.** WAL readers never block the writer and never block each other. | Fine. Readers read the file; `os.replace` means they see old or new. | Fine, and identically so — readers need no lock at all, because atomic replace already gives them a consistent snapshot. A reader that wants a *stable* snapshot across two collections takes `LOCK_SH`. |
| **Migration from 14 JSON collections** | **Real, and one-way.** A migration script, a schema, a fallback for an existing `$MOXIE_DATA_DIR`, and from then on two storage engines in one appliance. Callers are untouched — but the *data* is not. | **Zero.** It is what we have, written down. | **~40 lines**, callers untouched, on-disk layout byte-identical. The five `MemoryStore` sites at :562/:653/:691/:727/:753 change from `with self.store._lock:` to `with self.store.transaction(...):`. |
| **Two containers?** | **Yes**, on a local filesystem. WAL needs shared memory (`-shm`), so a **network filesystem is out** — and that is a real constraint for anyone putting `/data` on NFS. | **No — it forbids the case rather than surviving it.** And it cannot enforce itself: nothing stops a second `python3 mqtt/run.py`, which is what `run_smoke.sh` does today (§2.3). A design whose correctness is a rule you can only *assert* has no test. | **Yes**, on a local filesystem, and this is the option's whole point. Same NFS caveat (A9). `flock` is released by the kernel when the fd closes **or the holding process dies**, so there is no stale-lock recovery problem — the single best property of `flock` over any pid-file scheme. |
| **Dependency weight** | **Zero** — `sqlite3` is stdlib and already in the image, and [`server/moxie_server/db.py`](../../../server/moxie_server/db.py) already uses it. This row is *not* an argument against (a). | Zero. | Zero. `fcntl` is stdlib but **POSIX-only**; a guarded import degrades to today's behaviour on Windows, and CI + the appliance are both Linux (`runs-on: ubuntu-latest` in all three tiers). |
| **`cat`-readable?** | **No.** `cat memory.json` becomes `sqlite3 moxie.db "select …"`. This is the whole cost. | **Yes**, unchanged. | **Yes**, unchanged. The `.lock` sidecars are empty files a reader ignores. |
| **What it buys that the others don't** | **Multi-collection transactions** and, eventually, a **query layer** — which is what ADOPT #8 actually wants. | Nothing new. It is a description of the present. | Correct `append` across processes; nothing more. |

### 3.2 The choice

> **(c) — advisory `flock` on a per-record sidecar lock file, behind a public
> `JsonStore.transaction(device, collection)`, with JSON staying exactly where it is on disk.**

**The one-line reason: SQLite's only real advantage here is multi-collection transactions, and not one of
our fourteen call sites uses one — so (a) would cost the `cat`-readable layout and a one-way data
migration today to buy a property no caller could use until some later slice rewrites those callers
anyway.**

Longer form, because a build agent will be asked to defend it:

1. **The two questions have been fused and they are not the same question.** *"Should the appliance get a
   database?"* (ADOPT #8) and *"how do two processes write one record safely?"* (§4.4 #3) have been
   answered together three times, which is precisely why neither got answered. The second is a **bug**
   with a 40-line fix. The first is a **feature decision** that should be driven by a caller that needs a
   query — and the audit's own ADOPT #8 row now says *"no ranked item is blocked on the missing
   database."* Fixing a bug by shipping the feature is how the bug stays unfixed for a fourth time.
2. **SQLite's transaction advantage is unrealized without caller changes.** Dropping SQLite in behind the
   unchanged API gives every call site exactly what `flock` gives it: one atomic record write. Getting
   more means opening a transaction across `content_items` + `content_packs` + `content_backup`, or
   `telemetry_packets` + `telemetry_daily` — a caller rewrite that is out of scope for a hardening slice
   and would be a *content* PR and a *telemetry* PR, not this one. §3.3 says plainly what we lose by not
   having it.
3. **`cat` is a stated value in this codebase, not a nostalgia.** The console ships memory browse **and
   erase**; `LoggingPolicy` is a contract rather than a flag; the permits test asserts that a
   hand-written file takes effect. A parent, or a suspicious reviewer, being able to read and delete
   their child's data with `cat` and `rm` is the most legible privacy property the appliance has. Trading
   it away needs a buyer, and (a) does not have one yet.
4. **The failure mode of (c) is bounded and observable.** A wedged lock holder is the only new risk, and
   `LOCK_EX | LOCK_NB` in a bounded retry loop turns it into *"this one write failed and said so"* rather
   than *"the MQTT loop is hung"*. That is a behaviour we can test (T5, T6). By contrast (b)'s failure
   mode — a second writer that should not exist — is untestable by construction.
5. **It keeps (a) available and cheap.** The API is unchanged, the layout is unchanged, the lock lives
   inside `JsonStore`. The day a feature genuinely wants a query, `MOXIE_STORE=sqlite` is a backend swap
   behind the same five methods, with the JSON tree as the export format. **We are not choosing against
   SQLite. We are declining to pay for it before something needs it.**

**What would change this answer.** Stated so the argument is falsifiable: if a caller appears that must
write two collections atomically (an import that must not half-apply, a ledger), or if `/data` must live
on a network filesystem, or if the console starts writing this tree at any real rate, (a) becomes right
and this section should be rewritten rather than patched. §9's A9 and A11 track the two most likely.

### 3.3 The mechanism, precisely — the four things a PR review gets wrong

These are not implementation notes. They are the parts where a plausible-looking `flock` patch is wrong,
which is the whole reason this section exists rather than a one-line ADR.

1. **Lock a sidecar, never the data file.** `os.replace()` swaps the **inode**. A lock held on the data
   file is a lock on an inode the next writer will never open. So the lock is
   `f"{path}.lock"` — created once, `O_CREAT`, never replaced, never deleted (deleting it re-introduces
   the same inode race). Empty file, one per record; a reader ignores it.
2. **`RLock` outside, `flock` inside, one `open()` per acquisition.** `flock` is per *open file
   description*: two threads in one process that each `open()` the lock file get separate descriptions
   and **will deadlock each other** where the old `RLock` was reentrant. So `transaction()` takes
   `self._lock` first (preserving today's reentrancy for the same thread) and only the outermost
   acquisition opens the fd and `flock`s it. Nested `transaction()` on the same `(device, collection)`
   from the same thread must be a no-op re-entry, not a second `open()`.
3. **Never block the MQTT loop.** Some store writes happen on the paho network thread
   (`_on_state`, the permits path), and blocking it stalls every robot. So: `LOCK_EX | LOCK_NB` in a
   retry loop with the house backoff shape from
   [`moxie_sdk/chat.py`](../../../mqtt/moxie_sdk/chat.py)`::call_with_backoff` (exponential + jitter,
   injectable `sleep`), bounded by a new `MOXIE_STORE_LOCK_TIMEOUT_S` (**default 2.0 s**). On exhaustion
   the write **fails, returns `False`, and is recorded** in the runtime's `recent` ring and the
   connection telemetry — never retried forever, never silently swallowed. The value gets the same guard
   as `MOXIE_EXT_BUDGET_S` at [`config.py`](../../../mqtt/config.py):311‑317: assert it is strictly less
   than `MOXIE_BRAIN_BUDGET_S`, because a lock wait is a slice of a turn, not a claim on it.
4. **`fcntl` is POSIX-only, and the fallback must be loud.** `try: import fcntl / except ImportError:
   fcntl = None`. With no `fcntl`, `transaction()` degrades to exactly today's `RLock` behaviour and the
   supervisor prints one startup line saying cross-process locking is unavailable on this platform. Not a
   crash, not a silent downgrade.

Plus the one durability fix that belongs in the same commit and is unrelated to locking:
**fsync the directory after `os.replace`** (`fd = os.open(dirname, os.O_DIRECTORY); os.fsync(fd)`), so the
rename is durable and not merely likely (A12).

### 3.4 What we are giving up, said plainly

- **Multi-collection atomicity.** An interrupted content-pack import can leave `content_items` applied
  and `content_packs` not; an interrupted telemetry write can leave `telemetry_daily` a packet behind
  `telemetry_packets`. Both are recoverable — the roll-ups are derived, and packs already carry a digest
  and a backup collection — but neither is *transactional*, and no amount of `flock` makes it so. If that
  ever becomes unacceptable, it is the trigger in §3.2 that flips this to (a).
- **A query layer.** `devices()` still lists directories and every filter is still "read the list and
  loop in Python". *"Which robots had a safety event this week"* is O(robots) file reads. At household
  scale that is nothing; it is genuinely bad at fleet scale, and this brief does not pretend otherwise.
- **Schema and migrations.** Fourteen collections with no declared shape, versioned only by the code that
  writes them. A field rename is still a grep. That is ADOPT #8's real complaint and this brief does not
  answer it.
- **Network filesystems.** `/data` on NFS or SMB is unsupported and will be documented as such —
  `flock` there is best-effort at best. (SQLite would be *worse*, not better: WAL is flatly unsupported
  over NFS. So this is a cost of the problem, not of the choice.)
- **Windows.** No cross-process safety without `fcntl`. CI and the appliance are Linux; a Windows
  developer gets today's behaviour and a startup line saying so.
- **The illusion that hardening is finished.** §5's soak proves our side. A week in a house proves the
  rest, and nobody has run one.

---

## 4. Connection resilience — the design

### 4.1 The five changes, in order

| # | Change | Where | Why exactly this |
|--:|---|---|---|
| C1 | `reconnect_delay_set(min_delay=1, max_delay=60)` + `connect_async(host, port, 30)` + `loop_forever(retry_first_connection=True)` | `moxie_runtime.py`:484‑485 | **All three, or none.** `connect_async` without `retry_first_connection=True` changes nothing under `loop_forever` (§2.2 #3). `max_delay=60` rather than paho's 120 because a house's router reboot is ~30‑60 s and a 120 s ceiling means up to two minutes of a child talking to nothing; rather than Fork A's 30 because we would rather not hammer a broker that is down for an hour. No jitter — paho has none, and with a single supervisor there is no herd to thunder. |
| C2 | Keep **keepalive 30**, deliberately | same line | 30 s keepalive → the broker declares us dead at 45 s, and paho notices a missing PINGRESP within one keepalive. Halving the default 60 halves the worst-case detection of a half-open socket, which is the failure a NAT or a Wi-Fi drop actually produces. The audit called `30` a timeout; it is not, and now it is a **choice with a reason** instead of a number nobody picked. |
| C3 | `_on_connect` checks `rc` before subscribing | :943‑947 | On `rc != 0`: log `mqtt.connack_string(rc)`, set `broker_connected = False` + `last_connect_error`, `_note("error", …)`, and **return without subscribing**. Ports Fork A's `moxie_server.py`:206‑215 behaviour. Kills the *"broker connected rc=5"* line. |
| C4 | `on_disconnect` + `on_connect_fail` | new, beside `_build_client` at :213 | Maintain `broker_connected`, `last_broker_connect`, `last_broker_disconnect`, `last_connect_error`; push each into `self.recent` so the console's existing connection monitor renders them with no console change; count them for §5's telemetry. |
| C5 | One `_publish()` helper, return code checked, at all eight sites | :1455, :1482, :1526, :2469, :3157, :3410, :3459, :3521 | `info.rc != MQTT_ERR_SUCCESS` → record a **drop** (topic, device, reason) in `recent` + telemetry, return `(False, reason)`. `:1520`'s `if self.client is None` becomes `if not self._broker_connected()`, so the wakeup route answers `{"published": false, "reason": "The supervisor is not connected to the broker."}` — the sentence it already has, finally true. |

And one structural fix that belongs with them:

| C6 | `_on_event` registers an unknown device, like `_on_state` already does | :2025‑2026 vs :1023‑1025 | After a supervisor restart with the robot still connected, `$SYS/broker/log` has nothing to replay and the robot never re-publishes `/state`. Today `_on_event` answers from an ephemeral `RobotContext` forever: no config push, no `app.on_connect`, no presence state, invisible in `/status`. Making the two ingress paths symmetric is ~3 lines and it is the difference between "the appliance recovered" and "the appliance is answering a robot it does not know it has". |

### 4.2 What happens to an in-flight turn across a reconnect

**Decision: the turn is abandoned, marked stale by the mechanism the runtime already trusts, and
recorded. It is never replayed.**

The runtime already has exactly the right primitive. `_turn_seq` (:124, :2107‑2110) numbers each turn per
robot and `_is_stale` (:2115‑2118) suppresses an answer whose child has moved on — with the invariant
written at :2108‑2110: *"The MQTT loop is the only writer here, so a plain increment is enough."* So:

> **On `on_disconnect`, bump `_turn_seq[device_id]` for every known robot.**

Every in-flight worker's answer becomes stale by the existing check, at the existing seven call sites
(:1621, :1959, :2185, :2228, :2291, :2332, :2433), with no new machinery and no new race — `on_disconnect` fires on the
paho network thread, which *is* the MQTT loop under `loop_forever()`, so the documented single-writer
invariant still holds. The drop is then `_note`d, so the console can say a turn was lost when the
connection went away instead of the child simply hearing nothing.

**Why not replay it.** The recovered contract makes replay actively harmful. One `event_id` may be
answered by several responses ordered by `chunk_num` and closed by `consistency_control.is_completed`
([`remote-chat-protocol.md`](../../reverse-engineering/protocol/remote-chat-protocol.md):26, :63;
[`mqtt-and-conversation.md`](../mqtt-and-conversation.md) §4.5). A chunk 1 delivered after a 40-second gap
lands on a robot that re-prompted at ~20 s with a **new** `event_id` — so the child would hear the answer
to the question they gave up on, arriving after the answer to the one they asked instead. That is exactly
the failure `_is_stale` was written to prevent; a reconnect is not a reason to re-open it.

**Why not send `ERROR_TIMEOUT` (ResultCode 1) or `ERROR_OFFLINE` (4) for the abandoned turn on
reconnect.** Tempting, and unproven. `ERROR_OFFLINE` is documented as *"no connectivity → robot falls
back to the local brain"* (`remote-chat-protocol.md`:52‑63), which is a real and useful behaviour — but
our corpus does not say what a robot does with a **late** error result for an `event_id` it has already
abandoned. It might quietly discard it; it might interrupt the turn in progress. Sending it would be a
guess we cannot test without hardware, so P0 sends nothing and A5/A17 file the question.

### 4.3 QoS stays 0 — a decision, not an accident

All eight publishes are QoS 0 today by default rather than by choice. **They should stay QoS 0, and this
is the argument:**

- **QoS 1 with `clean_session=True`** (today's default) buys nothing: paho queues the message, and the
  queue is discarded when the session is not resumed.
- **QoS 1 with `clean_session=False`** buys the thing §4.2 just decided is harmful — an inflight queue
  that delivers stale turn answers after the gap. It also makes the broker keep a session for a fixed
  `client_id="supervisor"`, and a queue that grows while we are away is a memory cost paid to deliver
  messages we would then have to discard on arrival.
- **The recovery mechanism is already in the protocol**: the robot re-prompts (~20 s, A4), and the
  supervisor re-pushes config on registration (C6). Retransmission at the transport layer would duplicate
  a recovery the application layer already performs, and duplicate it *wrongly*.

The one arguable exception is `/devices/{id}/config` (:1482), where a lost push is not stale in the same
way. It stays QoS 0 too, because C6 makes a re-push happen on the robot's next event anyway, and one
mechanism beats two.

### 4.4 What the robot sees, in each of the four cases

| Case | What the robot observes | What we do |
|---|---|---|
| **Supervisor drops, broker stays up** | Nothing. Its own MQTT session is untouched. Its turn goes unanswered; it re-prompts (~20 s, A4). | C1 reconnects, C6 re-registers it on its next event and re-pushes config, C4/C5 record the gap and every dropped publish. |
| **Broker restarts** | Its own session drops. Whether it reconnects, and how fast, is **unverified — A5, needs hardware.** | Same as above from our side; the soak's Tier 2 restarts the broker 24 times and asserts *our* recovery (§5.3 A3). |
| **Supervisor restarts (container recreate)** | Nothing, if the broker stayed up. | Memory is reloaded from disk at boot (`_load_memory`), and C6 re-registers on the first event instead of serving an ephemeral context forever. |
| **Supervisor never reaches the broker at boot** | Nothing — it was never served. | C1 makes this a retry loop instead of a dead process. This is the single most valuable line in the brief for a bare-metal or non-compose install. |

---

## 5. "Stays connected for a week" as a test we can actually run

A week of wall clock is not a test; it is a wait. The property we mean by *"stays connected for a week"*
is **"no state grows without bound, no failure goes unrecorded, and every recovery path fires"** — and
all three are reachable by raising the *rate* of events rather than the *duration* of the run.

**A hard constraint the build agent must know before writing a line of it.**
[`sim/tests/test_clock_dependence.py`](../../../sim/tests/test_clock_dependence.py) is a ratchet: every
wall-clock read in the test tree must be listed with a verdict and a reason, and an unlisted one **fails
the suite**. A soak that "moves a clock" therefore does not read one — it injects `sleep` and `time`
functions. That is also better testing, and it is why every number in §5.3 is asserted against an
injected clock or a counter, never a stopwatch.

### 5.1 Tier 1 — hermetic fault injection, in the fast CI tier, seconds

Extend [`sim/tests/helpers_runtime.py`](../../../sim/tests/helpers_runtime.py)'s `FakeClient` — which
already records `publish()` calls — with three verbs: `drop(rc)` (calls the runtime's `on_disconnect`),
`up(rc=0)` (calls `_on_connect`), and `refuse(rc=5)` (a CONNACK failure). Everything in §6's S-series then
runs with no broker, no network and no sleeping.

### 5.2 Tier 2 — the SIL soak, "a week in an hour", opt-in

`sim/run_soak.sh`, built on the shape of [`run_scenarios.sh`](../../../sim/run_scenarios.sh) (which
already polls for readiness rather than sleeping) and driving
[`virtual_moxie.py`](../../../sim/virtual_moxie.py), whose `--scenario` + `--loop-seconds` flags are
already the replay loop this needs. Real mosquitto, real `mqtt/run.py`, `MOXIE_APP=echo` so nothing
reaches a gateway and the soak costs nothing.

**Profile `week` — the numbers and why each one:**

| Knob | Value | Reasoning |
|---|--:|---|
| Wall duration | **60 min** | Fits a nightly deep-tier job. |
| Turns | **≥ 2 000** | A heavy week of real use is ~100 turns/day ≈ 700. 2 000 is ~3× the worst realistic week. |
| Broker restarts | **24** | One every 2.5 min. A week behind a flaky home router is maybe one drop per 7 h ≈ 24 — so an hour here is a week's worth of disconnects. |
| Supervisor restarts | **4** | An appliance update, a `compose restart`, two power blips. |
| Virtual robots | **3, concurrent** | Multi-robot is a shipped claim (ADOPT #6, the fleet config layer). One robot proves nothing about the roster or about per-device state growth. |
| Store writers | **2 processes × 4 threads** | The subject of §3, exercised as itself: a second process appending to the same collections throughout. |
| Injected `SIGKILL` mid-write | **20** | Kill the writer between `open` and `os.replace` to prove no reader ever sees a truncated record. |

### 5.3 Acceptance criteria — numeric, all of them

| # | Criterion | Bar |
|--:|---|---|
| **A1** | Turn success rate, counting only turns issued while the broker was up | **100 %** |
| **A2** | Turns lost because of a drop | **≤ 1 per broker restart**, and **every one recorded** in `recent` — an unrecorded loss is a failure even if the count is 0 |
| **A3** | Broker-up → supervisor re-subscribed | **p95 ≤ 3 s**, **max ≤ 65 s** (one full `max_delay=60` ceiling + slack) |
| **A4** | Robots re-registered and re-pushed config after a supervisor restart | **100 %**, within **5 s** of each robot's next event (C6) |
| **A5** | Lost updates: 2 processes × 4 threads appending 10 000 items total | **0** — the final list length is exactly 10 000 |
| **A6** | Corrupt or truncated JSON after 20 mid-write `SIGKILL`s | **0** files unreadable; every one parses as either the old or the new value |
| **A7** | Supervisor RSS growth, measured from t+5 min to t+60 min | **≤ 10 %** |
| **A8** | Open file descriptors at the end vs. at t+5 min | **≤ +5** — the `flock` fds must not leak, and this is the test that catches it |
| **A9** | `_turn_seq`, `robots`, `_telehealth`, `recent` sizes at the end | **bounded**: `recent` at its cap, the other three ≤ the number of distinct devices seen |
| **A10** | Unhandled exceptions or tracebacks in the supervisor log | **0** |
| **A11** | Store writes that failed on lock timeout | **0** at these rates — and if non-zero, each one **recorded**, which is the property that actually matters |

| **A12** | Every robot `/status` lists is **re-onboarded** after a broker restart — no ghost left half-connected | **100 %**, and **0 ghosts** |

A2, A8 and A11 are the three that would have been left out of a spec written in a hurry, and they are the
three that catch the bugs this brief exists to prevent: a silent loss, a leaked descriptor, a swallowed
failure.

**A12 is new, added 2026-09-03 by P1, and it was not written from a spec — it was written from a bug.**
`sim/run_broker_outage.sh` phase 5c found (4/4 runs) that a robot returning after a broker restart was
never re-onboarded; the eleven bars above all passed while it happened, because every one of them is about
the *appliance* and none of them asks whether the **robot** got anything. It is measured against a live
stack rather than in a unit test, and it fails on the faithful regression: with `_device_connect`'s old
early-return restored, the `smoke` profile reports **`0/1 restarts re-onboarded every robot · STILL
GHOSTS: [d_…]`** and the soak exits non-zero.

### 5.4 What the soak cannot prove — read this before quoting any number above

It proves **our** half. It cannot prove, and must never be described as proving:

- that a real Moxie reconnects after a broker restart, or how quickly (**A5, hardware**);
- that a real Moxie accepts a config pushed mid-session without ending the session (**A6, hardware**);
- that the re-prompt window is really ~20 s (**A4, inherited from upstream, hardware**);
- that a real Moxie's client id is stable across reconnects, which is what makes per-device state
  survivable at all (**A17, hardware**);
- anything at all about a week. It is an hour, at a raised rate, against a simulator. **A real Moxie on
  our broker for an hour would settle more of this page than a week of building** — the audit's sentence,
  and it is as true here as anywhere.

**And now that the soak exists, one more sentence it needs.** *"A week in an hour"* is a **rate
substitution**, and P1's own experience is the argument for saying so out loud: the store contention the
harness measures is **load-dependent, not only contention-dependent**, and the same 4 × 250 configuration
produced 0 refusals on an idle box, 2 under a live soak, and (in a measurement handed to this slice) 189
on a busier one. A rate can stand in for a duration only for the failures that scale with *events*. It
cannot stand in for the ones that scale with *time* — a slow leak, a clock rolling over, a certificate
expiring, a log filling a disk — and this harness does not look for any of those. **A12 exists because a
whole class of defect sat underneath eleven green bars**, and there is no reason to think it was the last
one.

---

## 6. Tests

Hermetic first. Every one runs with no network, no broker and no sleeping.

| # | Test | File | Asserts |
|--:|---|---|---|
| **T1** | Two `JsonStore` instances on separate **processes** appending 5 000 items each lose nothing | `test_store_concurrency.py` | final length == 10 000 (the §3 choice, as itself) |
| **T2** | Nested `transaction()` on one `(device, collection)` from one thread does not deadlock | " | reentrancy — the §3.3 #2 trap |
| **T3** | Two threads in one process serialize through `transaction()` | " | no interleaved read-modify-write |
| **T4** | The lock is a sidecar and survives `os.replace` | " | the `.lock` inode is unchanged after a write — the §3.3 #1 trap |
| **T5** | A lock held past `MOXIE_STORE_LOCK_TIMEOUT_S` fails the write, returns `False`, records it | " | bounded, observable, never hangs |
| **T6** | `MOXIE_STORE_LOCK_TIMEOUT_S >= MOXIE_BRAIN_BUDGET_S` refuses to start | `test_config_*.py` | the `MOXIE_EXT_BUDGET_S` guard shape (`config.py`:311‑317) |
| **T7** | With `fcntl` unavailable, `transaction()` still works and one startup line says so | `test_store_concurrency.py` | the POSIX fallback is loud |
| **T8** | `SIGKILL` between write and `os.replace`, ×20 | " | every file parses; none truncated (A6) |
| **T9** | The directory is fsynced after `os.replace` | " | patched `os.fsync` sees the dir fd (A12) |
| **S1** | Every `publish()` during a drop returns not-ok, is recorded, and no route answers `published: true` | `test_connection_resilience.py` | C5 + the `:1520` honesty fix |
| **S2** | A turn started before a drop never publishes after the reconnect | " | §4.2, via the `_turn_seq` bump |
| **S3** | Subscriptions are installed exactly once per **successful** reconnect | " | C3 + C4 |
| **S4** | A CONNACK refusal (`rc=5`) subscribes nothing and logs `connack_string` | " | C3 — kills *"broker connected rc=5"* |
| **S5** | The reconnect delay sequence is 1, 2, 4, …, capped at 60 | " | C1, asserted against an **injected sleep** (clock ratchet) |
| **S6** | A supervisor started with no broker retries instead of dying | " | C1 — **the test that catches `connect_async` without `retry_first_connection`** |
| **S7** | An event from an unregistered device registers it and pushes config | " | C6 |
| **S8** | A drop bumps `_turn_seq` for every known robot, and only on the MQTT thread | " | the :2108‑2110 invariant is preserved |
| **K1** | `sim/run_soak.sh --profile week` meets every bar in §5.3 | deep tier, nightly | the soak itself; **not** in the fast tier |

---

## 7. Acceptance criteria

1. The supervisor **starts before the broker** and connects when the broker appears — proved by S6, not
   by `depends_on`.
2. A broker restart costs **at most one turn per robot**, that turn is **recorded**, and no answer to it
   is ever spoken afterwards (S2, A2).
3. No route, log line or status field claims a publish succeeded when the socket was down (S1) — and
   `_on_connect` never prints *"broker connected"* for a refusal (S4).
4. Two processes writing one appliance's data lose **zero** updates (T1), and the fix is reachable from
   `MemoryStore`'s five sites (T2, T3).
5. A parent can still `cat` and `rm` their child's data. The on-disk layout is **byte-identical** to
   today's, `.lock` sidecars aside.
6. The image gains **no dependency** and the Dockerfile is unchanged.
7. `sim/run_soak.sh --profile week` exists, runs on a laptop, and reports every §5.3 number — pass or
   fail, printed, not inferred. ✅ **shipped 2026-09-03**; `quick` and `smoke` profiles exist for a
   laptop and a pre-push check, because a soak nobody can run is a soak nobody runs (R5).
9. A robot that comes back after a broker restart is **re-onboarded** — config pushed, `app.on_connect`
   fired — and one that does not come back is **labelled**, never silently listed as present (A12, A22).
8. This page's §9 ledger is updated in the same PR that ships each phase, and the audit's §4.4 row 3
   status is flipped there too (the backlog README's house rule).

---

## 8. Effort and the file list

### P0 — **M**, one agent, one sitting, shippable alone — ✅ **shipped 2026-09-03**

Two independent halves that happen to share a PR; either could ship alone if an agent runs out of room.
Both shipped together. Every row below landed, plus one file the plan did not anticipate:
`sim/tools/hardening_mutation_check.py`, **35 mutations, 0 missed** — which found five holes, four of
them the same disease (two guards each covering for the other's absence, so neither was individually
load-bearing). What is **not** here is anything from P1: no soak harness, no durable roster, no
connection telemetry stream, no SIGTERM handler, and no console change.

| Order | File | Change |
|--:|---|---|
| 1 | `sim/tests/test_store_concurrency.py` | **New, first.** T1 fails today — write it before the fix. |
| 2 | `mqtt/moxie_sdk/store.py` | `transaction(device, collection)` + `transaction_shared(collection)`, the sidecar `.lock`, `RLock`-outside/`flock`-inside, `LOCK_NB` + bounded backoff, the guarded `fcntl` import, the directory fsync after `os.replace`. ~40 lines. |
| 3 | `mqtt/moxie_sdk/store.py` (`MemoryStore`) | The five `with self.store._lock:` sites at :562, :653, :691, :727, :753 → `with self.store.transaction(...)`. |
| 4 | `mqtt/config.py` | `MOXIE_STORE_LOCK_TIMEOUT_S` (2.0) + the `< MOXIE_BRAIN_BUDGET_S` assertion, mirroring :311‑317. |
| 5 | `sim/tests/test_connection_resilience.py` | S1–S8. S6 fails today. |
| 6 | `mqtt/supervisor/moxie_runtime.py` | C1–C6: `_build_client` gains `reconnect_delay_set` + `on_disconnect` + `on_connect_fail`; `run()` becomes `connect_async` + `loop_forever(retry_first_connection=True)`; `_on_connect` checks `rc`; a `_publish()` helper at all eight sites; `_broker_connected()` replaces `if self.client is None` at :1520; `_on_event` registers an unknown device. |
| 7 | `sim/tests/helpers_runtime.py` | `FakeClient.drop/up/refuse`. |
| 8 | `mqtt/supervisor/moxie_runtime.py` (`/status`) | `broker_connected`, `last_broker_connect`, `last_broker_disconnect`, `last_connect_error` — read by the console's existing monitor with **no console change**. |
| 9 | `docs/architecture/mqtt-and-conversation.md` | §3.4: what re-registration does after a supervisor restart, and that `$SYS/broker/log` is not replayed. |
| 10 | `docs/architecture/backlog/production-hardening.md` | Flip §9's ledger rows this phase settles. |
| 11 | `docs/architecture/openmoxie-feature-audit.md` | Flip §4.4 row 3 + the §4.3 brief table in the same PR. |

Deliberately **not** in P0: the soak harness, the durable roster, connection telemetry, the SIGTERM
handler, any console change, and every word about SQLite.

### P1 — **M** — ✅ **shipped 2026-09-03**

Every row landed, plus one the plan did not anticipate — a **defect class**, not a feature (below).

| # | What | Where |
|--:|---|---|
| 1 | `sim/run_soak.sh` + `sim/tools/soak.py`, three profiles (`smoke` ~1 min · `quick` ~5 min · `week` 60 min = §5.2's table) computing **every §5.3 bar**, plus **A12** (new, below) | `sim/run_soak.sh`, `sim/tools/soak.py` |
| 2 | The nightly deep-tier job (K1) — `week` on a 03:17 UTC cron, `quick` on a promotion PR, **never the fast tier** (R5) | `sim/ci/ci-deep.yml` |
| 3 | The **durable robot roster** (15th collection, `fleet/roster.json`) + `resume_roster()` on every successful CONNACK | `mqtt/moxie_sdk/roster.py` |
| 4 | The **connection telemetry stream** (16th collection, `fleet/conn_events.json`) — seven kinds, gap durations, `waited_s` on a lock timeout — on `GET /conn`, `/status`'s `connection_health`, and a strip on the console's 📈 card | `mqtt/moxie_sdk/conn_telemetry.py`, `server/moxie_server/fleet.py`, `server/static/app.js` |
| 5 | The **SIGTERM/SIGINT handler** → `request_stop()` → `disconnect()` | `moxie_runtime.py::_install_signal_handlers` |

#### What `MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` can and cannot carry — measured, on purpose

The soak's contention probe runs *N* processes against **one** record and reports the identity
`attempted == on_disk + refused`, so a **recorded refusal** (§3.2 point 4 accepts it; A11 asks it to be
recorded) is never confused with a **silent loss** (A5 forbids it). Measured 2026-09-03 at the default
2.0 s budget:

| Condition | Refused | Lost |
|---|--:|--:|
| 4 × 250, idle box | **0** of 1 000 | 0 |
| 4 × 250, 12 CPU burners on 24 cores | **1** of 1 000 | 0 |
| 4 × 250, inside a live 5-minute soak | **2** of 1 000 | 0 |
| 8 × 250 | **2** of 2 000 (0.10 %) | 0 |
| 4 × 1 000 | **1** of 4 000 (0.03 %) | 0 |
| 16 × 100 | **7** of 1 600 (0.44 %) | 0 |

**The handed-down measurement — 811 of 1 000 surviving at 2.0 s — did not reproduce here**, and the
divergence is the finding rather than a discrepancy to explain away: the refusal rate is
**load-dependent, not only contention-dependent**. So *"the default carries 4 × 250"* is not a portable
claim, which is why the harness reports a **rate** and asserts an **identity** instead of a count.

Stated honestly, then: at household rates the default carries everything; at these deliberately abusive
rates it carries ≥ 99.5 %, and **every** shortfall was a recorded refusal, never a lost write. What it
cannot carry is a promise — `flock` has no queue, so the tail is geometric and more budget buys more
polls, never certainty. **A13 is therefore still unsettled by this**: what a *real appliance* does over a
week is a different distribution, and P1 built the instrument (`lock_timeout` rows with `waited_s`) rather
than the answer.

One more thing P1 found, and it is the sharpest argument in this brief for *"a test for every fix, proven
in both directions"*: **A25**, an `OverflowError` in P0's own lock backoff that fires for **any**
`MOXIE_STORE_LOCK_TIMEOUT_S` above ~2.05 s — which the A13 guard invites. It arrived as a reported *"flake"*
and was not one. And the probe that found it **had the same disease it exists to detect**: a crashed writer
was silently excluded from `attempted`, so the identity still balanced and `lost` read 0 while a writer had
died. Ten clean-looking probe runs went past before the crash was printed rather than counted.

**Not done, and it is the row that was always going to be hardest:** A13 is **unchanged**.
`MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` is still *chosen, not measured* — P1 built the instrument
(`lock_timeout` rows carrying `waited_s`), and an instrument is not a measurement. Retuning it still needs
a week of a real appliance, which is the same thing §0 says nobody has.

#### The defect class P1 actually found

Two bugs reported a day apart turned out to be **one** bug, and the generalisation is worth more than
either fix: **a cached belief about the robot's state outliving the robot's actual state.**

| | The cached belief | What made it false | What it cost |
|---|---|---|---|
| **The roster ghost** | `_device_connect`'s `if device_id in self.robots: return` — *"already onboarded"* | the only thing that removed a robot was `_device_disconnect`, driven by a `$SYS/broker/log` line — **which dies with the broker** (A15) | a robot returning with the same id after a broker restart got **no config push and no `app.on_connect`**, silently, for the rest of the session — while `/status` listed it as present |
| **The vision/STT latch** | `_vision_subscribed[device] = module` — *"already subscribed"* | *"events are automatically unsubscribed when the module exits"* (RemoteModuleAPI §Unsubscribing) — a sentence the latch's **own docstring quoted** | after a module exit, a sleep/wake or an outage the robot had dropped the subscription while we still believed we held it; vision and QR events went nowhere, with nothing logged on either side |

Both now clear through one method, `_forget_robot_state()`, called wherever **connection continuity to
that robot breaks**. The lifetimes differ and the shorter is a **strict subset**: everything that breaks
continuity clears both; the vision latch has one extra invalidator (a module exit) that says nothing about
whether the robot is connected.

**Why the roster is not simply cleared on disconnect** — the obvious fix, and wrong three ways:

1. **It claims knowledge we do not have.** Our socket died; the robot's did not necessarily. *"The
   supervisor dropped, therefore the robot is gone"* is a belief, and a `/status` that reports beliefs as
   observations is the disease this whole brief exists to cure.
2. **It stampedes on a blip.** A 200 ms flap would drop every robot and re-onboard the lot on their next
   packet — N config pushes and N `on_connect`s at a broker that has just come back.
3. **It would have to lie about the conversation.** Removal runs through `_device_disconnect`, which fires
   `app.on_disconnect` and `_end_conversation`. Ending a child's session because *we* lost the broker is a
   worse error than the one being fixed.

So membership is kept and **confirmation** is cleared (`_seen_since_connect`). Nothing happens until a
robot gives real evidence (a `/state`, an event), and then exactly one robot is re-onboarded — reusing its
`RobotContext`, so history, presence and the telemetry buffer survive. Ghosts are **labelled**
(`seen_since_connect: false` on `/status`), not deleted.

The asymmetry that settles the whole design: both caches are pure optimisation. Being wrong by
*forgetting* costs one redundant message. Being wrong by *remembering* costs a half-connected robot, or
eyes that never report, **with nothing logged either way**.

**Ceiling, unchanged and worth repeating here:** no physical robot has ever sent this appliance a vision
event. The tests prove *we re-subscribe*; they cannot prove a robot then delivers.

### P2 — **L**

A `MOXIE_STORE=sqlite` backend behind the **unchanged** five-method API, if and only if a caller appears
that needs a transaction or a query (§3.2's trigger) — with the JSON tree kept as the export format so
§3.4's `cat` property survives as *"export, then read"* · multi-appliance / shared-volume operation, which
is where (a) becomes right · broker-side session persistence for the robot, if hardware ever shows the
robot uses one · a schema + migrations for the fourteen collections, which is ADOPT #8's actual want and
should be driven by a feature, not by this page.

### Risks

| # | Risk | Mitigation |
|--:|---|---|
| R1 | A `flock` patch that locks the data file instead of the sidecar looks correct and silently does nothing | T4 asserts the `.lock` inode is stable across a write. §3.3 #1 names the trap. |
| R2 | `connect_async` lands without `retry_first_connection=True`, so the headline fix is a no-op | S6 starts a supervisor with no broker. It fails today and must fail on a half-done fix. |
| R3 | The `RLock`/`flock` nesting deadlocks a worker thread | T2 + T3. The rule is one `open()` per outermost acquisition, stated in §3.3 #2. |
| R4 | A wedged holder blocks the MQTT loop | `LOCK_NB` + a 2.0 s cap + a recorded failure (T5), and the config assertion (T6) stops anyone raising it past the turn budget. |
| R5 | The soak is flaky on a loaded runner and gets disabled | It is **deep-tier and nightly**, never in the fast tier; readiness is polled, not slept (the `run_scenarios.sh` lesson); every assertion is a counter or an injected clock, never a stopwatch. |
| R6 | Someone reads §3 as *"we decided against a database"* | §3.2 point 5 and §3.4 say the opposite in as many words: we declined to pay for it **yet**, the API is the seam, and §3.2 names what flips the decision. |
| R7 | The `flock` fds leak, one per write, and the appliance dies of descriptors in week three | A8 in the soak, which exists precisely for this. |
| R8 | P0 ships and the row reads *"hardened"* while nothing has met a robot | §0, §5.4, and the six hardware-gated rows in §9. Every one of them is in the body of the page, not a footnote. |

---

## 9. Assumption ledger

**Twenty-five rows: seven proven, seven inferred, one measured, four FALSE-and-fixed-or-recorded, six
unverified.** The three new rows (A22, A23, A24) are not assumptions at all — they are **defects P1
found**, kept in this table rather than in a changelog because the table is what the next agent reads, and
*"this was believed and was not true"* is the most useful kind of row here. A different six —
**A4, A5, A6, A7, A17 and A20** — need a **physical robot**, and that set deliberately cuts across the
states: A4 and A7 are *inferred* and still hardware-gated, because an inference from upstream is not a
measurement. That second number is the honest ceiling on this whole area and it does not move by
building — **P0 shipping did not move it: the two rows P0 settled — A12 and A8 — are both about our own filesystem, and not one of the six hardware-gated rows budged.**

> **P1 shipped 2026-09-03.** The soak exists and **runs**: `quick` (5 min) measured **1 046 turns
> answered while the broker was up, 0 lost**, reconnect **p95 0.62 s / max 0.62 s** over 4 broker
> restarts, roster resume **≤ 1.02 s** over 2 SIGTERM restarts, **0 lost updates** across 4 processes ×
> 250 appends on one record, RSS **+3.2 %**, file descriptors **+0**, **0** tracebacks. **A22 and A23 are
> new and are both defects rather than assumptions.** **A13 and A14 are still unchanged** — P1 built the
> instrument that would settle A13 (`lock_timeout` rows with `waited_s`) and an instrument is not a
> measurement. **Not one of the six hardware-gated rows moved, again.**
>
> **P0 shipped 2026-09-03.** A12 → shipped; **A21 is new and is the only measured number here**;
> A13 and A14 are explicitly **unchanged** — the lock timeout and the reconnect ceiling remain
> *chosen*, and P1's connection telemetry is still what would measure them. **A8 was settled while
> building** (`flock` on a real Docker named volume, with a negative control). A9 (network
> filesystems) is not settled either — `/data` on NFS or SMB is **declared unsupported**, which is a
> decision rather than a measurement, and the store's module docstring says so where an implementer
> will read it.

| # | Assumption | State | How it gets settled |
|--:|---|:--:|---|
| A1 | `moxie_runtime.py`:484's third argument is the **keepalive**, not a timeout | **proven** | paho's `connect(host, port, keepalive, bind_address)` signature. Corrects the audit's implicit reading. |
| A2 | `loop_forever()` re-raises `OSError` from the first connect unless `retry_first_connection=True`, so `connect_async` alone is a no-op | **proven, by reading the installed paho** | `client.py::loop_forever` — the first `while run:` block. `loop_start()` gets it right only because `_thread_main` passes the flag. S6 pins it. |
| A3 | A QoS 0 publish while disconnected is **dropped**, not queued, and `info.rc` is `MQTT_ERR_NO_CONN` | **proven, by reading the installed paho** | `client.py::publish` — the `if qos == 0:` branch calls `_send_publish` directly. S1 pins it. |
| A4 | The robot re-prompts an unanswered turn after ~20 s | **inferred — inherited from upstream** | [`openmoxie-feature-audit.md`](../openmoxie-feature-audit.md):336 and :389, from Fork A's `ReasoningChatSession`. (The mqtt contract's §4.5 cites `:347` for this; that line has drifted — the claim is at :336/:389 today.) **Needs hardware** for the real number, and §4.2's "abandon, don't replay" rests on it being finite, not on it being 20. |
| A5 | A real Moxie reconnects to the broker on its own after a broker restart, and within what window | **unverified — needs hardware** | A robot, a broker restart, and a stopwatch. Nothing in our corpus states it. |
| A6 | A real Moxie accepts a `/config` push mid-session without ending the session | **unverified — needs hardware** | C6 re-pushes on re-registration, which after a supervisor restart may land mid-session. Today's `_device_connect` only ever pushes at the start of one. |
| A7 | A duplicate/idempotent config push is harmless | **inferred** | The face path already depends on it — `faces.py` re-keys `child_pii.id` as a deterministic UUIDv5 *so that* an idempotent re-push does not bust the Unity texture cache (audit ADOPT #9). **Needs hardware** to confirm the rest of the config behaves the same. |
| A8 | `fcntl.flock` is honoured on a Docker **named volume** | **PROVEN 2026-09-03** | Settled the way this row asked: T1's shape run inside a container against a real named volume (`docker volume create` → `-v vol:/data`), two processes × 250 `append`s → **500 of 500, zero lost**, and the `.lock` sidecar present on the volume. With the **negative control on the same volume type** — `origin/dev`'s unlocked read-modify-write — losing **250 of 500**, so the probe can see a loss and the green result is not vacuous. Not yet wired into `run_compose_smoke.sh`; that is a P1 line. |
| A9 | `flock` over NFS/SMB is unreliable | **inferred** (NFSv4 maps `flock` to POSIX locks; older/odd servers do not) | Not settled — **declared unsupported** in §3.4 instead. SQLite would be strictly worse here (WAL is unsupported over NFS), so this is a cost of the problem, not of the choice. |
| A10 | The supervisor is the **only** process writing `$MOXIE_DATA_DIR` today | **proven** | Repo-wide sweep: `JsonStore(` outside tests appears only at `mqtt/run.py`:57, `mqtt/config.py`:461, `store.py`:461. Nothing under `server/`. |
| A11 | A second writer is coming | **inferred, and near-certain** | Audit §4.4 #10 reconciles the console's child registry with the supervisor's. Plus `run_smoke.sh` already makes a developer one **today** (§2.3). |
| A12 | The directory must be fsynced for `os.replace` to be durable | **inferred** (POSIX; ext4 `data=ordered` masks it in practice) | **Shipped 2026-09-03.** `store.py::_fsync_dir`, called after every `os.replace`; `test_store_concurrency.py::test_t9_*` asserts an fsync lands on a **directory** fd, and `t9b` that a filesystem refusing it (EINVAL — some container/network mounts) is a durability downgrade rather than a failed write. Whether it *matters* on a given filesystem is still not testable from here, and does not need to be. |
| A13 | `MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` is the right number | **unverified — chosen, not measured** *(unchanged by P0)* | It is an env var, and P1's connection telemetry is what measures it. The only defensible claim today is *"strictly inside the turn budget"*, which T6 now enforces at startup. **The backoff *cadence* inside that budget is now measured** (see A21) — the budget itself is not. |
| A14 | `max_delay=60` is the right reconnect ceiling | **unverified — chosen** *(unchanged by P0)* | Between paho's 120 and Fork A's 30, reasoned from a router reboot taking 30‑60 s (§4.1 C1). A week of real gap durations (P1 telemetry) settles it. S5 pins the ladder we configured (1, 2, 4, …, 60), not that 60 is right. |
| A21 | The lock **backoff cadence** — 0.5 ms base, 2 ms cap — is fast enough that a contended writer is not starved out to the timeout | **measured 2026-09-03** *(new, found while building P0)* | `flock` has **no queue**: a `LOCK_NB` waiter takes whatever gap the holder leaves, so a process appending in a tight loop starves a coarse poller. Measured, two processes × 500 `append`s on one collection, three cadences × two runs: 10 ms/200 ms refused ~5 of 1 000 appends; 0.5 ms/10 ms refused ~2; 0.5 ms/2 ms refused **0**. Recorded on the constants in `store.py`. It is still a *poll*: fairness is not guaranteed, and a starved waiter times out — the bounded, **recorded** failure §3.2 point 4 accepts (T5), not a silent one. This is the one number in this brief that is measured rather than chosen. |
| A22 | A robot returning with the **same device id** after a broker restart is re-onboarded | **FALSE — it was not. Defect, found 2026-09-03, fixed by P1** | `_device_connect` early-returned on `device_id in self.robots`, and the only thing that removed a robot was `_device_disconnect` — driven by a `$SYS/broker/log` line, which dies with the broker (A15). Reproduced 4/4 by `sim/run_broker_outage.sh` phase 5c. The appliance answered the robot's turns while it had had **no config push and no `app.on_connect`**, and `/status` listed it as present throughout. Fixed by separating *membership* from *confirmation* (`_seen_since_connect`); now pinned by the soak's **A12** against a live stack, and by 8 hermetic tests. **This row is the reason A12 exists**: the other eleven bars all passed while it was happening, because every one of them asks about the appliance and none asked whether the *robot* got anything. |
| A23 | Our record of a robot's **vision/STT subscription** stays true while the robot holds it | **FALSE — the latch was never cleared. Defect, found 2026-09-03, fixed by P1** | `_vision_subscribed[device] = module` was set and never cleared, while the recovered contract says *"events are automatically unsubscribed when the module exits"* (RemoteModuleAPI §Unsubscribing) — quoted in the latch's own docstring. So after a module exit, a sleep/wake or an outage the robot had dropped the subscription and we never re-sent `EventSubscription.active[]`. External corroboration rather than ours alone: four independent owner reports of *"crossed ears"*, and upstream [`jbeghtol/openmoxie` PR #59](https://github.com/jbeghtol/openmoxie/pull/59) diagnoses the sleep/wake variant identically (MIT, © Justin Beghtol — read as prior art, no code copied). Same fix as A22, deliberately: both are a cached belief outliving the robot's state. **Still hardware-gated in the direction that matters** — no physical robot has ever sent this appliance a vision event, so the tests prove *we re-subscribe*, never that a robot then delivers. |
| A24 | The `.tmp` a `SIGKILL`ed writer leaves behind is cleaned up | **FALSE — measured 2026-09-03, not fixed** | The soak's kill probe reports `stray_tmp_files`: 10 mid-write `SIGKILL`s leave **2** orphaned `<record>.<pid>.tmp` files. Harmless for correctness — the record itself is whole (A6 is 0/0), and `_write_path` only unlinks its temp on an `OSError` it survives — but it is unbounded growth on a long-lived appliance and therefore an A9 problem in slow motion. Not fixed here because the safe sweep is not obvious: the temp name carries a pid, pids recycle, and deleting another live writer's scratch file is a worse bug than leaking one. Recorded rather than rounded away. |
| A25 | The lock backoff is safe at **any** `MOXIE_STORE_LOCK_TIMEOUT_S` an operator may set | **FALSE — `OverflowError` above ~2.05 s. Defect, found 2026-09-03, fixed by P1** | `_wait_flock` computed `LOCK_BACKOFF_BASE_S * (2 ** attempt)`, and `2 ** attempt` is an arbitrary-precision **int**. The loop runs ≈ `timeout / LOCK_BACKOFF_CAP_S` times, so at `attempt == 1024` the product overflows a float and raises `OverflowError` **out of `transaction()`, past `append`'s `except StoreLockTimeout`, into the caller** — on the paho thread, that is *"never take the MQTT loop down for a store write"* broken outright. **The default hides it by 24 polls** (2.0 s / 2 ms ≈ 1 000); 5 s is ~2 500 and 30 s is ~15 000, and A13's own guard (`< MOXIE_BRAIN_BUDGET_S`) positively invites the larger value. Found by chasing a reported *"`test_t1` flake"* — `test_t1` uses 30 s deliberately, so that starvation cannot be mistaken for a lost update, which is exactly why it surfaced there. Reproduced 1-in-12 under load, 12/12 clean after clamping the exponent. **This is very probably the unexplained single lost append in A21's "999 of 1 000 at 30 s": not a starved waiter, a crashed writer.** |
| A15 | `$SYS/broker/log` is live-only and is **not** replayed on re-subscribe, so a supervisor restart cannot recover the connected set from it | **proven** | mosquitto publishes log lines as they happen; `mqtt-and-conversation.md` §3.4. This is the entire reason C6 exists. |
| A16 | 3 concurrent virtual robots and 2 000 turns/hour represent a household week | **inferred** | ~100 turns/day is a heavy child; 3 robots is more than one house has. Both are knobs in `run_soak.sh`. |
| A17 | A real Moxie's `d_<uuid>` client id is **stable across reconnects**, so per-device state (`_turn_seq`, memory, permits) survives one | **unverified — needs hardware** | The id is the device id and is presumed stable, but nothing in our corpus proves it does not rotate. If it rotates, C6, the permits gate and every per-device collection are wrong in the same way. |
| A18 | A real Moxie tolerates the supervisor's **fixed** `client_id="supervisor"` reconnecting into the same broker | **inferred** | Only the broker sees it, and it evicts the older session by MQTT spec. Relevant only if two supervisors ever run at once — which §3 now makes safe for the *store* and not for the *broker*. Named so it is not mistaken for solved. |
| A19 | The `_turn_seq` bump in `on_disconnect` preserves the "MQTT loop is the only writer" invariant at :2108‑2110 | **proven, by reading paho** | `on_disconnect` is dispatched from the network loop, which under `loop_forever()` is the calling thread. S8 pins it. |
| A20 | A week is the right horizon at all | **unverified — needs a robot in a house** | Nobody has run a Moxie on our broker for a week, or an hour (§0). The horizon is inherited from the audit's phrasing, not measured — and §5's *"week in an hour"* is a **rate substitution**, which is a different claim and is labelled as one. |

---

## 10. What this brief is not

It is not a database migration, and §3 explains at length why not. It is not a claim that the appliance
has been proven in a house — §0 and §5.4 say the opposite, twice, on purpose. It is not a promise that a
robot reconnects; six rows of §9 say we do not know.

It is the smaller, checkable thing: **the supervisor stops dying when the broker is late, stops lying
when the socket is dead, stops answering a question the child abandoned, and stops losing a write to a
second process it did not know was there** — and, when the first real Moxie finally spends a week on our
broker, there is a recording of what happened rather than a shrug.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[MQTT and the conversation](../mqtt-and-conversation.md) ·
[Config & telemetry contract](../config-and-telemetry-contract.md) ·
[Broker auth](security-broker-auth.md) · [Sandboxed extensions](sandboxed-extensions.md) ·
[Live Sim demo](live-sim-demo.md) · [Orchestration plan](../orchestration-plan.md) ·
[Remote-chat protocol](../../reverse-engineering/protocol/remote-chat-protocol.md) ·
[Attribution](../../../ATTRIBUTION.md)
