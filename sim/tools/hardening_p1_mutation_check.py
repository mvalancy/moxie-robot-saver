"""Remove each guard the production-hardening **P1** slice rests on, and check its test goes red.

*"A test for every feature, proven in BOTH directions."* A green suite proves the guards
are **present**; this proves they are **load-bearing**. Same shape as
`hardening_mutation_check.py` (P0, 35 mutations / 0 missed) and the three before it —
and it exists for the same reason every one of those did: each of them found a real hole,
several of them *two guards each hiding the other's absence*.

Run it by hand after touching `moxie_sdk/{roster,conn_telemetry}.py`, the connection or
shutdown region of `supervisor/moxie_runtime.py`, `moxie_sdk/store.py::_append_path`, or
`server/moxie_server/fleet.py`'s connection normalizer:

    python3 sim/tools/hardening_p1_mutation_check.py

Every row must say "caught". A row that says NOT CAUGHT means the assertion passes with
the guard deleted, i.e. it is not testing what its name claims.

Several mutations here are deliberately **plausible patches rather than deletions**,
because that is what a regression actually looks like in review:

* the roster resume marking rostered robots as *connected* — which makes the console look
  populated after a restart and is a status field reporting a belief instead of an
  observation, the exact disease this brief exists to remove;
* `gap_since` returning `0.0` instead of `None` for a first connect — a one-character
  "simplification" that puts a phantom zero-second outage at the head of every appliance's
  history and quietly changes what `gaps.count` means;
* the shutdown row written *after* `disconnect()` rather than before — which looks
  identical until the process is actually torn down, at which point the history is missing
  the row in exactly the case an operator cares about;
* `_stopping` hard-wired True, which passes *"a clean stop is not an outage"* while
  silently erasing every real outage the appliance ever has. That pair is the reason both
  directions are asserted rather than one.

Nothing here changes the tree permanently: each mutation is reverted in a `finally`.
`PYTHONDONTWRITEBYTECODE` is not a nicety — without it a `__pycache__` entry from an
earlier mutation can shadow a later one and a guard reads as un-caught when it is fine.
"""
import pathlib
import subprocess

WT = pathlib.Path(__file__).resolve().parents[2]
STORE = WT / "mqtt/moxie_sdk/store.py"
CONN = WT / "mqtt/moxie_sdk/conn_telemetry.py"
ROSTER = WT / "mqtt/moxie_sdk/roster.py"
RT = WT / "mqtt/supervisor/moxie_runtime.py"
FLEET = WT / "server/moxie_server/fleet.py"

T_CONN = "sim/tests/test_conn_telemetry.py"
T_ROSTER = "sim/tests/test_roster.py"
T_STOP = "sim/tests/test_clean_shutdown.py"
T_STORE = "sim/tests/test_store_concurrency.py"
T_CONSOLE = "sim/tests/test_console_roundtrip.py"

MUTATIONS = [
    # ---- the connection history: the shapes ------------------------------------
    ("C1  a first connect reports a 0.0s gap instead of None", CONN,
     "    if not last_disconnect:\n        return None",
     "    if not last_disconnect:\n        return 0.0",
     T_CONN, "first_connect_has_no_gap or second_connect_carries_the_gap"),
    ("C2  always emit gap_s, even when the kind has no gap", CONN,
     "    if gap_s is not None:\n        row[\"gap_s\"] = _duration(gap_s)",
     "    row[\"gap_s\"] = _duration(gap_s or 0)",
     T_CONN, "row_carries_only_the_fields"),
    ("C3  trust the reason off the wire (no truncation)", CONN,
     "    return str(text or \"\").strip()[:MAX_REASON_CHARS]",
     "    return str(text or \"\").strip()",
     T_CONN, "reason_off_the_wire_is_truncated"),
    ("C4  accept a pre-epoch timestamp", CONN,
     "    return val if val >= _EPOCH_FLOOR else int(time.time())",
     "    return val",
     T_CONN, "timestamp_before_the_epoch_floor"),
    ("C5  do not clamp a negative duration (a clock that stepped back)", CONN,
     "        return round(max(0.0, float(value)), 3)",
     "        return round(float(value), 3)",
     T_CONN, "negative_duration_is_clamped"),
    ("C6  count the first connect as a gap too", CONN,
     "    gaps = sorted(_duration(e[\"gap_s\"]) for e in rows if e.get(\"gap_s\") is not None)",
     "    gaps = sorted(_duration(e.get(\"gap_s\") or 0.0) for e in rows)",
     T_CONN, "summarize_counts_kinds or never_dropped_reports_no_gaps"),
    ("C7  p95 by interpolation (invents a gap nobody observed)", CONN,
     "    rank = max(1, math.ceil(0.95 * len(gaps)))",
     "    rank = max(1, int(0.5 * len(gaps)))",
     T_CONN, "p95_is_a_rank"),
    ("C8  health derives 'down' from the newest row, not the recorded state", CONN,
     "    if not connected:\n        state = \"down\"",
     "    if not connected or (s.get(\"latest\") or [{}])[0].get(\"kind\") == DISCONNECT:\n        state = \"down\"",
     T_CONN, "health_reads_the_recorded_state"),
    ("C9  fold a refusal in with the outages", CONN,
     "    return {\"state\": state, \"outages\": outages, \"refusals\": refusals, \"drops\": drops,",
     "    return {\"state\": state, \"outages\": outages + refusals, \"refusals\": 0, \"drops\": drops,",
     T_CONN, "counts_a_refusal_apart"),
    ("C10 ignore MOXIE_CONN_MAX_EVENTS", CONN,
     "    raw = os.environ.get(\"MOXIE_CONN_MAX_EVENTS\", \"\").strip()",
     "    raw = \"\"",
     T_CONN, "cap_is_an_env_knob or ring_is_capped"),

    # ---- the connection history: the runtime wiring ----------------------------
    ("C11 drop the re-entrancy guard (the recorder recurses into itself)", RT,
     "        if self._recording_conn:\n            return False",
     "        if False:\n            return False",
     T_CONN, "never_recurses_into_itself"),
    ("C12 let a broken store escape into the MQTT loop", RT,
     "            return self.store.append_shared(conn_seam.COLLECTION, row,\n"
     "                                            cap=conn_seam.max_events()) is not None\n"
     "        except Exception:\n            return False",
     "            return self.store.append_shared(conn_seam.COLLECTION, row,\n"
     "                                            cap=conn_seam.max_events()) is not None\n"
     "        except ZeroDivisionError:\n            return False",
     T_CONN, "broken_store_never_costs_a_turn"),
    ("C13 write the ring uncapped", RT,
     "            return self.store.append_shared(conn_seam.COLLECTION, row,\n"
     "                                            cap=conn_seam.max_events()) is not None",
     "            return self.store.append_shared(conn_seam.COLLECTION, row) is not None",
     T_CONN, "ring_is_capped"),
    ("C14 a CONNACK refusal records nothing", RT,
     "            self._record_conn(conn_seam.REFUSED, reason=self.last_connect_error)",
     "            pass",
     T_CONN, "connack_refusal_is_recorded"),
    ("C15 on_connect_fail records nothing (the retry loop is invisible again)", RT,
     "        self._record_conn(conn_seam.CONNECT_FAIL, reason=self.last_connect_error)",
     "        pass",
     T_CONN, "connect_fail_is_its_own_kind"),
    ("C16 a dropped publish is counted but not recorded", RT,
     "        self._record_conn(conn_seam.PUBLISH_DROP, device_id=device_id, topic=topic,\n"
     "                          reason=reason)",
     "        pass",
     T_CONN, "dropped_publish_is_recorded"),
    ("C17 a lock timeout is noted but its waited_s is dropped", RT,
     "        self._record_conn(conn_seam.LOCK_TIMEOUT, waited_s=waited,\n"
     "                          reason=os.path.basename(lock_path))",
     "        self._record_conn(conn_seam.LOCK_TIMEOUT,\n"
     "                          reason=os.path.basename(lock_path))",
     T_CONN, "lock_timeout_is_recorded_with_how_long"),
    ("C18 the reconnect gap is measured after last_broker_connect moves", RT,
     "        gap = conn_seam.gap_since(self.last_broker_disconnect, now)\n"
     "        self.broker_connected = True\n        self.last_broker_connect = now",
     "        self.broker_connected = True\n        self.last_broker_connect = now\n"
     "        gap = conn_seam.gap_since(self.last_broker_disconnect, self.last_broker_connect)\n"
     "        gap = None",
     T_CONN, "second_connect_carries_the_gap"),
    ("C19 /status drops the connection health headline", RT,
     '                "connection_health": conn_seam.health(',
     '                "connection_health_": conn_seam.health(',
     T_CONN, "status_carries_the_connection_health"),

    # ---- the durable roster ----------------------------------------------------
    ("R1  first_seen is overwritten by every later sighting", ROSTER,
     '    rows[device_id] = {"first_seen": round(min(first, now), 3),',
     '    rows[device_id] = {"first_seen": round(now, 3),',
     T_ROSTER, "first_seen_survives or clock_that_stepped_backwards"),
    ("R2  the cap evicts the MOST recently seen", ROSTER,
     "        for stale in sorted(rows, key=lambda d: _last_seen(rows[d]))[: len(rows) - limit]:",
     "        for stale in sorted(rows, key=lambda d: -_last_seen(rows[d]))[: len(rows) - limit]:",
     T_ROSTER, "cap_evicts_the_least_recently_seen"),
    ("R3  no cap at all", ROSTER,
     "    if limit and len(rows) > limit:",
     "    if False:",
     T_ROSTER, "cap_evicts_the_least_recently_seen"),
    ("R4  ids come back oldest-first", ROSTER,
     "    return sorted(rows, key=lambda d: -_last_seen(rows[d]))",
     "    return sorted(rows, key=lambda d: _last_seen(rows[d]))",
     T_ROSTER, "ids_come_back_most_recently_seen_first"),
    ("R5  the resume does not subtract the robots we can already see", ROSTER,
     "    out = [d for d in device_ids(roster) if d not in live]",
     "    out = list(device_ids(roster))",
     T_ROSTER, "resume_targets_skips_robots or restart_re_pushes_config"),
    ("R6  the resume ignores the permit gate", ROSTER,
     "    if permitted is not None:\n        out = [d for d in out if permitted(d)]",
     "    if permitted is None:\n        out = [d for d in out if permitted(d)]",
     T_ROSTER, "unpaired or unpermitted"),
    # R7 took three attempts and the two failures are the finding.
    #
    # (a) dropping `record_seen`'s `dict(...)`  → UNCAUGHT: `_rows()` already returns a
    #     fresh dict, so the copy there is redundant.
    # (b) making `_rows()` hand back the caller's own mapping → ALSO UNCAUGHT: every
    #     caller copies it again.
    #
    # So the purity `record_seen` promises is guarded **twice**, and neither guard is
    # individually load-bearing — the same shape P0's checker found four times ("two
    # guards each hiding the other's absence"). Here it is benign rather than a bug: both
    # copies are cheap and either one is sufficient. What the mutation must therefore be
    # is the plausible *optimisation* that removes both at once — reuse the caller's
    # mapping in place, which is exactly what someone chasing an allocation would write.
    ("R7  record_seen mutates the roster it was given (the copy 'optimised' away)",
     ROSTER,
     "    rows = dict(_rows(roster))\n    prev = rows.get(device_id) or {}",
     "    rows = roster.setdefault(\"devices\", {}) if isinstance(roster, dict) else {}\n"
     "    prev = rows.get(device_id) or {}",
     T_ROSTER, "never_mutates_the_roster"),
    ("R15b _roster_seen writes outside the transaction it opened", RT,
     "            with self.store.transaction_shared(roster_seam.COLLECTION):\n"
     "                current = self.roster()\n"
     "                self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                        roster_seam.record_seen(current, device_id))",
     "            with self.store.transaction_shared(roster_seam.COLLECTION):\n"
     "                current = self.roster()\n"
     "            self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                    roster_seam.record_seen(current, device_id))",
     T_ROSTER, "inside_the_lock"),
    ("R8  MOXIE_ROSTER_RESUME is ignored", ROSTER,
     '    raw = (os.environ.get("MOXIE_ROSTER_RESUME") or "1").strip().lower()',
     '    raw = "1"',
     T_ROSTER, "resume_can_be_turned_off or silent_when_it_is_turned_off"),
    ("R9  _device_connect no longer records the robot", RT,
     "        self._roster_seen(device_id)",
     "        pass",
     T_ROSTER, "every_ingress_path or roster_survives_the_process"),
    ("R10 THE LIE: the resume marks rostered robots as connected", RT,
     "                self._push_config(device_id)\n                pushed.append(device_id)",
     "                self.robots.setdefault(device_id, RobotContext(\n"
     "                    device_id=device_id, child=self.child))\n"
     "                self._push_config(device_id)\n                pushed.append(device_id)",
     T_ROSTER, "not_reported_as_connected"),
    ("R11 the reconnect-storm generation check is dropped", RT,
     "            if generation != self._connect_generation or self._stopping:\n                return",
     "            if False:\n                return",
     T_ROSTER, "reconnect_storm or scheduled_before_a_shutdown"),
    ("R12 the resume timer is not a daemon (it holds a stop open)", RT,
     "        timer.daemon = True                   # never hold a shutdown open for a re-push",
     "        timer.daemon = False",
     T_ROSTER, "never_holds_a_shutdown_open"),
    ("R13 a resume that raises kills its thread", RT,
     "            try:\n                self.resume_roster()\n            except Exception as e:\n"
     "                print(f\"[runtime] roster resume failed: {e}\", flush=True)",
     "            self.resume_roster()",
     T_ROSTER, "resume_that_raises"),
    ("R14 a broken store takes the robot's connection down with it", RT,
     "                self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                        roster_seam.record_seen(current, device_id))\n"
     "            return True\n        except Exception:\n            return False",
     "                self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                        roster_seam.record_seen(current, device_id))\n"
     "            return True\n        except ZeroDivisionError:\n            return False",
     T_ROSTER, "broken_store_never_costs_a_robot"),
    # R15's first selector was `roster_survives_the_process`, which is single-writer and
    # structurally cannot see a missing lock — it went UNCAUGHT, and the fix was a
    # two-process test, not a different mutation.
    ("R15 the roster write is not a transaction (two supervisors lose robots)", RT,
     "            with self.store.transaction_shared(roster_seam.COLLECTION):\n"
     "                current = self.roster()\n"
     "                self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                        roster_seam.record_seen(current, device_id))",
     "            if True:\n"
     "                current = self.roster()\n"
     "                self.store.write_shared(roster_seam.COLLECTION,\n"
     "                                        roster_seam.record_seen(current, device_id))",
     T_ROSTER, "two_supervisors_on_one_data_directory"),

    # ---- the clean stop --------------------------------------------------------
    ("S1  request_stop never calls disconnect()", RT,
     "                client.disconnect()",
     "                pass",
     T_STOP, "closes_the_socket_rather_than_dropping"),
    ("S2  request_stop is not idempotent", RT,
     "        if self._stopping:\n            return False\n        self._stopping = True",
     "        if False:\n            return False\n        self._stopping = True",
     T_STOP, "idempotent"),
    ("S3  the shutdown row is written AFTER the socket closes", RT,
     "        self._record_conn(conn_seam.SHUTDOWN, reason=reason)\n        client = self.client",
     "        client = self.client",
     T_STOP, "written_before_the_socket_closes"),
    ("S4  a deliberate stop is recorded as an outage", RT,
     "        if self._stopping:\n            # A disconnect we asked for.",
     "        if False:\n            # A disconnect we asked for.",
     T_STOP, "not_recorded_as_an_outage"),
    ("S5  THE PAIR: _stopping hard-wired True (every real outage vanishes)", RT,
     "        self._stopping = False",
     "        self._stopping = True",
     T_STOP, "disconnect_that_is_not_a_stop"),
    ("S6  a stop no longer stales the in-flight turns", RT,
     "        for device_id in set(self._turn_seq) | set(self.robots):\n"
     "            self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1\n"
     "        if self._stopping:",
     "        if self._stopping:",
     T_STOP, "abandons_every_in_flight_turn"),
    ("S7  a disconnect() that raises aborts the stop", RT,
     "            except Exception as e:\n"
     "                print(f\"[runtime] disconnect during shutdown failed: {e}\", flush=True)\n"
     "        return True",
     "            except ZeroDivisionError as e:\n"
     "                print(f\"[runtime] disconnect during shutdown failed: {e}\", flush=True)\n"
     "        return True",
     T_STOP, "will_not_disconnect"),
    ("S8  no signal handlers are installed at all", RT,
     "                _signal.signal(sig, self._on_stop_signal)\n                installed.append(name)",
     "                installed.append(name) if False else None",
     T_STOP, "both_stop_signals or real_supervisor_exits"),
    ("S9  the handler claims success off the main thread", RT,
     "            except (ValueError, OSError, RuntimeError):",
     "            except ZeroDivisionError:",
     T_STOP, "embedded_runtime"),
    ("S10 SIGKILL added to the catchable list", RT,
     '    STOP_SIGNALS = ("SIGTERM", "SIGINT")',
     '    STOP_SIGNALS = ("SIGTERM", "SIGINT", "SIGKILL")',
     T_STOP, "sigkill_is_deliberately_not"),

    # ---- the store: append reads the write's return code ------------------------
    ("A1  append ignores the write's return code (the P1 fix, reverted)", STORE,
     "                if not self._write_path(path, items):\n                    return None\n                return items",
     "                self._write_path(path, items)\n                return items",
     T_STORE, "t10"),

    # ---- the console -----------------------------------------------------------
    ("K1  a supervisor we never reached still gets a verdict", FLEET,
     '        "verdict": CONNECTION_STATES.get(state, "") if ok else "",',
     '        "verdict": CONNECTION_STATES.get(state, "steady"),',
     T_CONSOLE, "supervisor_that_is_down"),
    ("K2  'recovered' renders as 'healthy'", FLEET,
     '    "recovered": "Connected now — but it has not been the whole time",',
     '    "recovered": "Connected, with nothing to report",',
     T_CONSOLE, "recovered_is_not_rendered_as_healthy"),
    ("K3  a missing gap is flattened to a zero-second outage", FLEET,
     '    if e.get("gap_s") is not None:\n        row["gap_s"] = float(_num(e.get("gap_s")) or 0.0)',
     '    row["gap_s"] = float(_num(e.get("gap_s")) or 0.0)',
     T_CONSOLE, "without_a_gap"),
    ("K4  a row from a newer runtime raises instead of rendering", FLEET,
     "    e = e if isinstance(e, dict) else {}",
     "    e = e if isinstance(e, dict) else None",
     T_CONSOLE, "newer_runtime"),
    ("K5  every kind renders as its own wire name", FLEET,
     '           "label": CONNECTION_LABELS.get(kind, kind.replace("_", " ")),',
     '           "label": kind,',
     T_CONSOLE, "sentence_a_parent_can_read"),
    ("K6  the console never asks the supervisor for the history", FLEET,
     '        "events": [normalize_connection_event(e) for e in (p.get("events") or [])] if ok else [],',
     '        "events": [],',
     T_CONSOLE, "live_state_and_the_history"),
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
            # A selector that matched nothing is a silent pass, and it is the way a
            # mutation table rots: the test gets renamed, `-k` stops matching, and the row
            # reports "caught" forever without running anything.
            if "no tests ran" in r.stdout or " 0 passed" in r.stdout.replace("selected", ""):
                print(f"  NO-OP       {name}  (selector {sel!r} matched no test)")
                noop += 1
            elif r.returncode == 0:
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
