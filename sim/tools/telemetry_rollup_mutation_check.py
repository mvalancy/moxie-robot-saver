"""Break each guard that keeps the telemetry ring and its daily roll-up from durably
disagreeing — the write ORDER, the shared critical section, the `seq`/`through_seq`
watermark; the three telemetry suites must go red. Runner: `mutation_runner.py`.

    python3 sim/tools/telemetry_rollup_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

T = "mqtt/moxie_sdk/telemetry.py"
R_TELEMETRY = "mqtt/supervisor/moxie_runtime/telemetry.py"
TESTS = ["sim/tests/test_telemetry_rollup_repair.py", "sim/tests/test_telemetry.py",
         "sim/tests/test_telemetry_runtime.py"]

MUTATIONS = [
    ("M1  the ring is written before the roll-up again (the 2026-09-05 red)", R_TELEMETRY,
     "                counted = self.store.write(\n"
     "                    device_id, telemetry_seam.DAILY_COLLECTION,\n"
     "                    telemetry_seam.roll_up_packet(\n"
     "                        telemetry_seam.reconcile_rollup(stored, ring), row))\n"
     "                kept = self.store.append(device_id, telemetry_seam.PACKETS_COLLECTION,\n"
     "                                         row, cap=telemetry_seam.max_packets()) is not None",
     "                kept = self.store.append(device_id, telemetry_seam.PACKETS_COLLECTION,\n"
     "                                         row, cap=telemetry_seam.max_packets()) is not None\n"
     "                counted = self.store.write(\n"
     "                    device_id, telemetry_seam.DAILY_COLLECTION,\n"
     "                    telemetry_seam.roll_up_packet(\n"
     "                        telemetry_seam.reconcile_rollup(stored, ring), row))"),
    ("M2  the two writes stop being one critical section", R_TELEMETRY,
     "            with self.store.transaction(device_id, telemetry_seam.PACKETS_COLLECTION):",
     "            if True:"),
    ("M3  the read path stops reconciling the roll-up against the ring", R_TELEMETRY,
     "        rollup = telemetry_seam.reconcile_rollup(stored, ring)",
     "        rollup = telemetry_seam.reconcile_rollup(stored, [])"),
    ("M4  a repair is answered but never written back", R_TELEMETRY,
     "        if missing:\n            try:",
     "        if False:\n            try:"),
    ("M5  the stored envelope is not stamped with its sequence", R_TELEMETRY,
     "                row = telemetry_seam.with_seq(row, telemetry_seam.next_seq(ring, stored))",
     "                row = dict(row)"),
    ("M6  the insights view reads the raw roll-up instead of the reconciled one", R_TELEMETRY,
     "        rollup = self.telemetry_rollup(device_id)",
     "        rollup = self.store.read(device_id, telemetry_seam.DAILY_COLLECTION,\n"
     "                                 telemetry_seam.new_rollup())"),
    ("M7  next_seq trusts the ring alone, so a lost append reuses a number", T,
     "    if rollup is not None:\n"
     "        highest = max(highest, _clean_rollup(rollup)[\"through_seq\"])",
     "    if False:\n"
     "        highest = max(highest, _clean_rollup(rollup)[\"through_seq\"])"),
    ("M8  an unstamped legacy envelope counts as UNfolded (the upgrade double-count)", T,
     "    missing = [(n, r) for r in rows\n"
     "               for n in (packet_seq(r),) if n is not None and n > out[\"through_seq\"]]",
     "    missing = [(n or 0, r) for r in rows\n"
     "               for n in (packet_seq(r),) if (n or 0) >= out[\"through_seq\"]]"),
    ("M9  the roll-up never advances its watermark, so every read re-folds the ring", T,
     "    seq = packet_seq(pkt)\n"
     "    if seq is not None and seq > out[\"through_seq\"]:\n"
     "        out[\"through_seq\"] = seq",
     "    seq = packet_seq(pkt)\n"
     "    if False:\n"
     "        out[\"through_seq\"] = seq"),
    ("M10 the watermark is dropped when the record is read back off disk", T,
     "    out[\"through_seq\"] = _count(r.get(\"through_seq\"))",
     "    out[\"through_seq\"] = 0"),
    ("M11 a robot's own `seq` survives the privacy gate and forges the watermark", T,
     "    out = {k: v for k, v in pkt.items() if k in _PACKET_FIELDS}",
     "    out = dict(pkt)"),
    ("M12 an ingest stops repairing on its way past, so only a reader can heal", R_TELEMETRY,
     "                    telemetry_seam.roll_up_packet(\n"
     "                        telemetry_seam.reconcile_rollup(stored, ring), row))",
     "                    telemetry_seam.roll_up_packet(\n"
     "                        stored, row))"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS), baseline=[pytest(TESTS)]))
