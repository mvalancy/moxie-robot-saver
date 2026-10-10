"""Telemetry + mentor-behavior ingest, the durable activity record, and its erasure."""
from __future__ import annotations

from moxie_sdk.wire import parse_mentor_behavior
from moxie_sdk import telemetry as telemetry_seam
from moxie_sdk.cloud_config import LoggingPolicy
from .constants import MAX_MENTOR_BEHAVIORS, MENTOR_BEHAVIORS_COLLECTION, TELEMETRY_POLICY


class TelemetryMixin:
    # ---- telemetry ingest (parent-console insights) ----
    # Durable: `robots/<id>/telemetry_packets.json` (ring of newest envelopes) and
    # `telemetry_daily.json` (one row per day); shapes, caps and policy filter live in
    # `moxie_sdk/telemetry.py`. The in-RAM buffer is a cache of the ring, hydrated on first
    # touch, so every read path sees history after a restart.
    def telemetry_policy(self, device_id) -> LoggingPolicy:
        """The LoggingPolicy for what may be written to disk about this robot's telemetry:
        the effective (fleet + per-robot) `logging_policy`, else `TELEMETRY_POLICY`."""
        raw = (self.effective_config(device_id) or {}).get("logging_policy")
        if raw is None:
            return TELEMETRY_POLICY
        try:
            return LoggingPolicy(int(raw))
        except (TypeError, ValueError):
            return TELEMETRY_POLICY

    def telemetry_persists(self, device_id) -> bool:
        """False under NO_DATA — nothing about this child's telemetry is stored."""
        return self.telemetry_policy(device_id) != LoggingPolicy.NO_DATA

    def _telemetry_buffer(self, device_id, robot=None) -> list:
        """This robot's live packet buffer (the list itself), hydrated from the durable
        ring on first touch — the single read point, so every path that registers a robot
        (including tests writing `rt.robots` directly) sees the same history."""
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
        """This robot's daily roll-up, reconciled against the ring (`{}`-safe).

        The ring is the durable log; the roll-up is a view carrying `through_seq`, so an
        uncounted envelope is detectable and the repair is written back (only when
        something was missing; best effort — this may run on the MQTT thread).
        `_persist_telemetry` bypasses this: it already holds both records. Days are the
        house's (`house_zone`), as `_persist_telemetry` keys them.
        """
        stored = self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,
                                 telemetry_seam.new_rollup())
        ring = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
        missing = telemetry_seam.unfolded_packets(stored, ring)
        rollup = telemetry_seam.reconcile_rollup(stored, ring,
                                                 tz=self.house_zone(device_id).tz)
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

        Failures are printed and swallowed (MQTT thread). Ordering and locking:
        1. The roll-up is written BEFORE the ring. The pair cannot be atomic, and every
           observer reads the ring first, so no one can see an envelope the roll-up has
           not counted; a crash in between loses one envelope from a bounded ring only.
        2. Both writes are one critical section on the ring's record (the roll-up is a
           read-modify-write; `transaction()` is reentrant and taken in a fixed order).
        3. Both return values are read: a lock refusal returns None/False, not an
           exception, and a refused roll-up is repairable from the ring.
        """
        row = telemetry_seam.storable_packet(pkt, self.telemetry_policy(device_id))
        if row is None:                       # LoggingPolicy.NO_DATA — nothing on disk
            return False
        tz = self.house_zone(device_id).tz    # a day is the house's day, not the container's
        try:
            with self.store.transaction(device_id, telemetry_seam.PACKETS_COLLECTION):
                ring = self.store.read(device_id, telemetry_seam.PACKETS_COLLECTION, [])
                stored = self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,
                                         telemetry_seam.new_rollup())
                # Stamped after the privacy gate, so a robot cannot supply its own `seq`.
                row = telemetry_seam.with_seq(row, telemetry_seam.next_seq(ring, stored))
                # Reconcile on the way past: an active robot heals a lost roll-up write.
                counted = self.store.write(
                    device_id, telemetry_seam.DAILY_COLLECTION,
                    telemetry_seam.roll_up_packet(
                        telemetry_seam.reconcile_rollup(stored, ring, tz=tz), row, tz=tz))
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

    # ---- erasing the activity record ----
    # Three files, one verb: telemetry_packets, telemetry_daily, mentor_behaviors. Never
    # policy-gated. Flipping to NO_DATA erases retroactively (as for transcripts): the
    # contract promises an empty store under NO_DATA (config-and-telemetry-contract.md §3).
    # A robot that failed closed (fleet.py `failed_closed`) is not swept: its NO_DATA is a
    # settings file that could not be read, not a parent's choice.

    #: What `erase_telemetry` removes, in order — one list, so none can be forgotten.
    ACTIVITY_COLLECTIONS = (telemetry_seam.PACKETS_COLLECTION,
                            telemetry_seam.DAILY_COLLECTION,
                            MENTOR_BEHAVIORS_COLLECTION)

    def erase_telemetry(self, device_id) -> dict:
        """Forget everything stored about what one robot's child did. Never
        policy-gated, idempotent; also drops the in-RAM buffer so nothing erased is served."""
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
            # Erasing the last of it still answers ok (same shape as `erase_memory`).
            policy = self.telemetry_policy(device_id)
            out = {"ok": True, "device_id": device_id,
                   "summary": telemetry_seam.summarize_events([]),
                   "events": [], "policy": policy.name,
                   "persisted": policy != LoggingPolicy.NO_DATA,
                   "connected": robot is not None,
                   "retention": telemetry_seam.retention(),
                   "history": telemetry_seam.history_view(
                       telemetry_seam.new_rollup(), today=self._house_today(device_id)),
                   "totals": telemetry_seam.rollup_totals(telemetry_seam.new_rollup())}
        out["erased"] = erased
        out["records"] = sorted(k for k, v in removed.items() if v)
        return out

    def purge_telemetry(self) -> int:
        """Erase the activity record of every robot now under NO_DATA (connected or not),
        except a robot that failed closed (`failed_closed`). Runs at startup and after
        config edits; best effort."""
        try:
            known = set(self.store.devices()) | set(self.robots)
        except Exception as e:
            print(f"[runtime] telemetry sweep failed: {e}", flush=True)
            return 0
        purged = 0
        for device_id in sorted(known):
            if self.telemetry_persists(device_id) or self.failed_closed(device_id):
                continue
            if self.erase_telemetry(device_id).get("erased"):
                purged += 1
        if purged:
            print(f"[runtime] 🧽 erased the activity record of {purged} robot(s) "
                  f"under NO_DATA", flush=True)
        return purged

    def _house_today(self, device_id) -> str:
        """Today on the house's clock (`house_now`), `YYYY-MM-DD`: where the daily history
        ends, so the newest bar is the family's today, not the container's."""
        return self.house_now(device_id).date().isoformat()

    def telemetry_view(self, device_id, limit: int = 20, days: int = 7) -> dict:
        """The per-robot insights view: the ring summarized, the newest `limit` events
        and `days` of daily history. A robot with stored history is `ok` even offline;
        neither -> `{ok: false}` (HTTP 404)."""
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
                # How far back the store goes, lifetime totals, whether anything is written.
                "policy": policy.name,
                "persisted": policy != LoggingPolicy.NO_DATA,
                "connected": robot is not None,
                "retention": telemetry_seam.retention(),
                "history": telemetry_seam.history_view(rollup, days=days,
                                                       today=self._house_today(device_id)),
                "totals": totals}

    # ---- mentor behaviors (what the child has already done) ----
    def mentor_behaviors(self, device_id) -> list:
        """This robot's stored MentorBehavior history, newest first (as OpenMoxie's
        `get_mbh` orders it)."""
        records = self.store.read(device_id, MENTOR_BEHAVIORS_COLLECTION, []) or []
        if not isinstance(records, list):
            return []
        return sorted(records, key=lambda r: (r or {}).get("timestamp") or 0, reverse=True)

    def ingest_mentor_behavior(self, device_id, report):
        """Store one reported MentorBehavior (`ActivityUpdate.mentor_behavior`, Cloud.proto
        :241). Returns the record, or None if unusable. Publishes nothing.

        Gated on `telemetry_policy` (it is a robot-uploaded report, like a Packet): under
        NO_DATA it is parsed and returned but never written. The in-RAM console feed line
        still fires — this is a persistence gate.
        """
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
