"""Telemetry + mentor-behavior ingest, the durable activity record, and its erasure."""
from __future__ import annotations

from moxie_sdk.wire import parse_mentor_behavior
from moxie_sdk import telemetry as telemetry_seam
from moxie_sdk.cloud_config import LoggingPolicy
from .constants import MAX_MENTOR_BEHAVIORS, MENTOR_BEHAVIORS_COLLECTION, TELEMETRY_POLICY


class TelemetryMixin:
    # ---- telemetry ingest (parent-console insights) ----
    #
    # Durable since 2026-09-02. Until then an ingested Packet lived only in
    # `RobotContext.extra["telemetry"]` — a list in this process's RAM, capped at 50 —
    # so the 📈 card was an event log over one supervisor lifetime and a restart erased
    # every answer to "what did Moxie do last week". Two collections now back it
    # (`moxie_sdk/telemetry.py` owns both shapes, both caps and the policy filter):
    #
    #   robots/<id>/telemetry_packets.json  a ring of the newest envelopes  ("just now")
    #   robots/<id>/telemetry_daily.json    one row per calendar day        ("last week")
    #
    # The in-memory buffer stays — every existing read path still uses it — but it is now
    # a **cache of the ring, hydrated from disk on first touch**, so `telemetry_count`,
    # the insights view and the schedule planner all see history after a restart.
    def telemetry_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy governing what may be *written to disk* about this robot's
        telemetry — the parent's explicit `logging_policy` if there is one, else
        `TELEMETRY_POLICY`.

        Read from the **effective** config (fleet ⊕ per-robot) like `memory_policy`, so a
        house rule set once for the appliance governs every robot on it and one robot can
        still be set apart."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return TELEMETRY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return TELEMETRY_POLICY

    def telemetry_persists(self, device_id) -> bool:
        """False under `NO_DATA` — nothing about this child's telemetry is stored."""
        return self.telemetry_policy(device_id) != LoggingPolicy.NO_DATA

    def _telemetry_buffer(self, device_id, robot=None) -> list:
        """This robot's live packet buffer, **hydrated from the durable ring on first
        touch**. Returns the list itself, so callers may append to it.

        Load-on-boot lives here rather than in `_device_connect` on purpose: tests and
        the SIL harness register robots directly (`rt.robots[id] = RobotContext(...)`),
        and a robot that reconnects mid-session must see the same history as one the
        supervisor met at startup. Hydrating at the single read point makes every path
        durable without a second place to forget."""
        robot = robot if robot is not None else self.robots.get(device_id)
        if robot is None:
            return []
        buf = robot.extra.get("telemetry")
        if buf is None:
            stored = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
            buf = [p for p in stored if isinstance(p, dict)] if isinstance(stored, list) else []
            robot.extra["telemetry"] = buf
        return buf

    def telemetry_rollup(self, device_id) -> dict:
        """This robot's daily roll-up, **reconciled against the ring** (`{}`-safe).

        The read path for the roll-up, and the reason a lost write to it is recoverable
        rather than permanent. The ring is the durable log; the roll-up is a view over it
        carrying `through_seq`, so every envelope the view has not counted is a fact on
        disk rather than a guess (`telemetry.unfolded_packets`).

        The repair is **written back**, and only when something was actually missing — a
        self-heal, not a write on every read. Answering the repaired number without
        storing it would recompute it on every refresh and lose it for good the moment
        the ring wrapped past the packet it was recovering. The write is best-effort for
        the same reason `_persist_telemetry`'s are: this can run on the MQTT thread and a
        telemetry write must never cost a child their turn.

        `_persist_telemetry` deliberately does **not** come through here: it is inside its
        own transaction, it has already read the ring to number the packet, and it is
        about to write the roll-up anyway — going through this method would cost a third
        read of the ring and a duplicate write, per packet, on the MQTT thread.
        """
        stored = self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,
                                 telemetry_seam.new_rollup())
        ring = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
        missing = telemetry_seam.unfolded_packets(stored, ring)
        rollup = telemetry_seam.reconcile_rollup(stored, ring)
        if missing:
            try:
                self.store.write(device_id, telemetry_seam.DAILY_COLLECTION, rollup)
                print(f"[runtime] 📈 repaired {device_id}'s telemetry roll-up from the "
                      f"ring: {len(missing)} packet(s) it had not counted", flush=True)
            except Exception as e:                # pragma: no cover - best effort
                print(f"[runtime] telemetry roll-up repair failed: {e}", flush=True)
        return rollup

    def ingest_telemetry(self, device_id, payload):
        """Parse an incoming telemetry Packet, keep it live, and persist it per policy.
        Returns the parsed packet (or None on parse failure)."""
        try:
            pkt = telemetry_seam.parse_packet(payload)
        except Exception:
            return None
        robot = self.robots.get(device_id)
        if robot is not None:
            buf = self._telemetry_buffer(device_id, robot)
            buf.append(pkt)
            del buf[: max(0, len(buf) - telemetry_seam.max_packets())]
            self._persist_telemetry(device_id, pkt)
        self._note("telemetry", f"📈 {pkt.get('event_name', 'event')}")
        return pkt

    def _persist_telemetry(self, device_id, pkt) -> bool:
        """Write one Packet through the privacy gate. True when something was stored.

        A telemetry write must never cost a child their turn, so every failure here is
        printed and swallowed — this runs on the MQTT thread.

        **Three things about the order and the lock, all of them the 2026-09-05 fix.**

        1. **The roll-up is written BEFORE the ring**, which is the opposite of what this
           did until a `sil` red on a PR that could not reach this code (the whole
           argument is at the top of `moxie_sdk/telemetry.py`). Both writes are
           `os.replace`s of separate files and nothing can make the pair atomic, so the
           only question is which one an observer sees first — and every observer's
           leading edge is the ring: the SIL fixture waits for envelopes, the console
           lists events, a restart hydrates its buffer from it. Writing the *exact*
           record (the lifetime count, which must be right) before the *bounded* one (a
           ring that is documented to drop things) means no observer can see the ring
           hold a packet the roll-up has not counted. The inverse — a crash after the
           roll-up and before the append — costs one envelope from a record whose whole
           contract is "the newest 500", and costs the number a parent reads nothing.
        2. **Both writes are one critical section**, held on the ring's record. The
           roll-up write is a read-modify-write and it was not: two ingests landing
           together each read the same roll-up and the second's `write` overwrote the
           first's count, while the ring's `append` — which *is* transactional — kept
           both. Same divergence, no crash required. `transaction()` is reentrant and the
           two records are always taken in this order, so the nesting cannot deadlock
           with anything else here (`erase_telemetry` takes them one at a time).
        3. **Both return values are read.** `append` answers None and `write` answers
           False when another process holds the record past `lock_timeout_s` — a refusal,
           not an exception — and this function used to return True over both of them.
           That is the disease `store.py::_append_path` describes: *a comfortable lie at
           the one boundary that knows the truth.* A refused roll-up write is now said
           out loud, and it is survivable rather than permanent precisely because the
           envelope still goes into the ring for `telemetry_rollup` to reconcile from.
        """
        row = telemetry_seam.storable_packet(pkt, self.telemetry_policy(device_id))
        if row is None:                       # LoggingPolicy.NO_DATA — nothing on disk
            return False
        try:
            with self.store.transaction(device_id, telemetry_seam.PACKETS_COLLECTION):
                ring = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
                stored = self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,
                                         telemetry_seam.new_rollup())
                # Stamped here, AFTER the privacy gate — `storable_packet` keeps only
                # `_PACKET_FIELDS`, so a robot cannot hand us a `seq` of its own and mark
                # the ring as counted.
                row = telemetry_seam.with_seq(row, telemetry_seam.next_seq(ring, stored))
                # Reconciled on the way past, so a robot that keeps talking heals a lost
                # roll-up write without waiting for a parent to open the card.
                counted = self.store.write(
                    device_id, telemetry_seam.DAILY_COLLECTION,
                    telemetry_seam.roll_up_packet(
                        telemetry_seam.reconcile_rollup(stored, ring), row))
                kept = self.store.append(device_id, telemetry_seam.PACKETS_COLLECTION,
                                         row, cap=telemetry_seam.max_packets()) is not None
        except Exception as e:
            print(f"[runtime] telemetry write failed: {e}", flush=True)
            return False
        if not counted:
            print(f"[runtime] telemetry roll-up refused for {device_id} "
                  f"({self.store.last_lock_error}); the ring will repair it", flush=True)
        if not kept:
            print(f"[runtime] telemetry envelope refused for {device_id} "
                  f"({self.store.last_lock_error}); it is counted, not listed", flush=True)
        return counted or kept

    # ---- erasing the activity record (the other half of the privacy contract) ----
    #
    # Until this landed, `do_DELETE` accepted only `/memory`. Telemetry had a policy gate
    # and **no erasure path at all**, so a parent who moved the switch to NO_DATA stopped
    # new writes and kept every packet already on disk, with nothing to press. §③ of the
    # config contract says NO_DATA means *"nothing. No packet, no count, no day row. A
    # restart finds an empty store."* — a sentence that was false the moment the switch
    # was flipped rather than set.
    #
    # Three files, one verb, because they answer one question ("what did the child do")
    # and an erase that left one of them behind would not be an erase:
    #
    #   telemetry_packets.json · telemetry_daily.json · mentor_behaviors.json
    #
    # Two decisions a reader will ask about:
    #
    #  1. **Flipping to NO_DATA erases retroactively**, exactly as it does for the rolling
    #     transcript (`purge_transcripts`). The alternative — keep what was stored before
    #     the flip, as the *facts* store does — was rejected here for two reasons the
    #     facts store does not share. The contract's own table promises an empty store
    #     under NO_DATA and the insights card already tells a parent under NO_DATA that
    #     "nothing is being saved", so a surviving ring makes both of them lie. And a
    #     memory item is a sentence about the child that a parent may want to read,
    #     correct or pin (there is a UI for exactly that); a Packet envelope is a machine
    #     event in a bounded ring with no per-item value to preserve.
    #  2. **The erase is never policy-gated**, like every other erase here: it works under
    #     NO_DATA, NO_MEDIA and FULL. A parent who wants the history gone without turning
    #     recording off presses erase and leaves the switch alone — that is what the
    #     explicit `DELETE /telemetry` is for, and why the flip is not the only way.

    #: What `erase_telemetry` removes, in the order it removes it. One list, so a fourth
    #: activity record cannot be added without this erase being told about it.
    ACTIVITY_COLLECTIONS = (telemetry_seam.PACKETS_COLLECTION,
                            telemetry_seam.DAILY_COLLECTION,
                            MENTOR_BEHAVIORS_COLLECTION)

    def erase_telemetry(self, device_id) -> dict:
        """Forget everything this appliance stored about what one robot's child did.

        Symmetric with `erase_memory`: never policy-gated, idempotent, and it makes the
        **in-RAM view agree** — `robot.extra["telemetry"]` is dropped rather than left
        holding the ring we just deleted, so the very next `_telemetry_buffer` hydrates
        from the (now empty) store instead of serving what a parent just erased."""
        removed = {c: bool(self.store.delete(device_id, c))
                   for c in self.ACTIVITY_COLLECTIONS}
        robot = self.robots.get(device_id)
        if robot is not None:
            robot.extra.pop("telemetry", None)     # never serve what we just erased
        erased = any(removed.values())
        if erased:
            self._note("telemetry", "🧽 erased stored activity history")
            print(f"[runtime] 🧽 erased telemetry for {device_id}: "
                  f"{', '.join(k for k, v in removed.items() if v)}", flush=True)
        out = self.telemetry_view(device_id)
        if not out.get("ok"):
            # Erasing the last of it is still a hit — a robot that is no longer known to
            # the store because we just emptied it must not answer 404 to the parent who
            # emptied it (the same shape `erase_memory` returns).
            policy = self.telemetry_policy(device_id)
            out = {"ok": True, "device_id": device_id,
                   "summary": telemetry_seam.summarize_events([]),
                   "events": [], "policy": policy.name,
                   "persisted": policy != LoggingPolicy.NO_DATA,
                   "connected": robot is not None,
                   "retention": telemetry_seam.retention(),
                   "history": telemetry_seam.history_view(telemetry_seam.new_rollup()),
                   "totals": telemetry_seam.rollup_totals(telemetry_seam.new_rollup())}
        out["erased"] = erased
        out["records"] = sorted(k for k, v in removed.items() if v)
        return out

    def purge_telemetry(self) -> int:
        """Erase the stored activity record of every robot now under `NO_DATA`.

        Runs at startup and after any config edit that could have flipped the switch —
        per robot or fleet-wide — so "I turned recording off" means the files are gone
        *now*, for every robot this box knows about and not only the connected ones. A
        no-op under any other policy, and best-effort: a store we cannot read must not
        stop the appliance from coming up."""
        try:
            known = set(self.store.devices()) | set(self.robots)
        except Exception as e:
            print(f"[runtime] telemetry sweep failed: {e}", flush=True)
            return 0
        purged = 0
        for device_id in sorted(known):
            if self.telemetry_persists(device_id):
                continue
            if self.erase_telemetry(device_id).get("erased"):
                purged += 1
        if purged:
            print(f"[runtime] 🧽 erased the activity record of {purged} robot(s) "
                  f"under NO_DATA", flush=True)
        return purged

    def telemetry_view(self, device_id, limit: int = 20, days: int = 7) -> dict:
        """The parent console's per-robot insights view (M6): the ring rolled up by
        `summarize_events` + the newest `limit` events + the last `days` days of daily
        history from the durable roll-up.

        Known to the store but not connected is still a real answer — a parent asking
        what happened last week should get it whether or not the robot is on the broker
        right now — so a device with stored history is `ok:true` even when it is absent
        from `self.robots`. Neither → `{ok:false}` (the HTTP layer answers 404)."""
        robot = self.robots.get(device_id)
        rollup = self.telemetry_rollup(device_id)
        totals = telemetry_seam.rollup_totals(rollup)
        if robot is None:
            stored = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, None)
            if stored is None and not totals["days_kept"]:
                return {"ok": False, "device_id": device_id,
                        "error": f"unknown device_id {device_id!r}"}
            packets = [p for p in stored if isinstance(p, dict)] if isinstance(stored, list) else []
        else:
            packets = self._telemetry_buffer(device_id, robot)
        summary = telemetry_seam.summarize_events(packets, limit=limit)
        policy = self.telemetry_policy(device_id)
        return {"ok": True, "device_id": device_id,
                "summary": summary, "events": summary["latest"],
                # What a parent needs to read the card honestly: how far back the store
                # really goes, the lifetime total behind the sliding window, and whether
                # anything is being written at all.
                "policy": policy.name,
                "persisted": policy != LoggingPolicy.NO_DATA,
                "connected": robot is not None,
                "retention": telemetry_seam.retention(),
                "history": telemetry_seam.history_view(rollup, days=days),
                "totals": totals}

    # ---- mentor behaviors (what the child has already done) ----
    def mentor_behaviors(self, device_id) -> list:
        """This robot's stored MentorBehavior history, newest first.

        Newest-first mirrors OpenMoxie's field-proven server (`robot_data.py::get_mbh`
        orders by `-timestamp`); our docs record the record shape but not an ordering."""
        records = self.store.read(device_id, MENTOR_BEHAVIORS_COLLECTION, []) or []
        if not isinstance(records, list):
            return []
        return sorted(records, key=lambda r: (r or {}).get("timestamp") or 0, reverse=True)

    def ingest_mentor_behavior(self, device_id, report):
        """Store one reported MentorBehavior (`ActivityUpdate.mentor_behavior`, Cloud.proto
        :241 — see wire.parse_mentor_behavior). Returns the stored record, or None if the
        report carried nothing usable. Publishes nothing: a report is not a query.

        **Gated on the parent's `LoggingPolicy`.** This is a durable, per-child
        behavioural log — which activity was finished, which was quit, which was refused,
        with a timestamp on each — and it was the last thing this appliance wrote about a
        child with no gate at all, so a `NO_DATA` robot still accumulated a behavioural
        profile on disk while its telemetry and its transcript were being refused. It is
        `telemetry_policy` and not `memory_policy` because a `MentorBehavior` is a *report
        the robot uploads* (`client-service-activity-log`), the same kind of thing as a
        `Packet`; both resolve the parent's one `logging_policy` field either way — see
        `TELEMETRY_POLICY`.

        Under `NO_DATA` the record is parsed and returned but never written, so the
        caller (`_on_activity`) behaves exactly as it did and the turn is unaffected —
        what changes is only what survives it. The console's live feed line still fires,
        deliberately: like `ingest_telemetry`'s, and like the in-RAM conversation window
        `_save_memory`'s gate leaves alone, this is a **persistence** gate. `self.recent`
        is a 120-entry deque that dies with the process and never reaches disk, and a
        parent watching their own console lose sight of their own robot would be a
        privacy theatre with a real cost and no benefit."""
        rec = parse_mentor_behavior(report)
        if rec is None:
            return None
        if self.telemetry_persists(device_id):
            self.store.append(device_id, MENTOR_BEHAVIORS_COLLECTION, rec,
                              cap=MAX_MENTOR_BEHAVIORS)
        self._note("behavior", f"🏁 {rec.get('module_id')}"
                               f"{'/' + rec['content_id'] if rec.get('content_id') else ''}"
                               f" {rec.get('action', '')}".rstrip())
        return rec
