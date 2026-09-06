#!/usr/bin/env python3
"""📈 Delete one durable-telemetry guard at a time and require a test to go red.

The house rule is that a feature's tests are proven in BOTH directions: green with the
guard, red without it. This is the sibling of `sim/tools/brain_mutation_check.py` and
`sim/tools/subscribe_mutation_check.py` for the thing a red `sil` job on 2026-09-05
exposed — **the ring and the daily roll-up disagreeing across a restart**, on a PR whose
diff could not reach either of them.

The property the table exists to keep load-bearing is one sentence: *the two telemetry
records cannot durably disagree, because one is a log and the other is a view over it.*
Three mechanisms hold it up and each row deletes exactly one of them —

  * the **order** (the exact record written before the bounded one, so no observer whose
    leading edge is the ring can read an under-count),
  * the **critical section** (both writes as one, so two ingests cannot lose a roll-up
    update the ring keeps),
  * the **watermark** (`seq` on the envelope, `through_seq` on the roll-up, so
    *"already counted"* is a fact on disk and a lost roll-up write is replayable).

A mutation that leaves the suite GREEN is a hole in the tests, not a pass. One row had to
be rewritten before this table was honest: the first draft of M2 locked the *other* record
instead of deleting the lock, and in-process every `transaction()` is serialized by one
RLock whatever record it names — so the mutation was unobservable without a second
process, and the row proved nothing. It now deletes the section outright.

    python3 sim/tools/telemetry_rollup_mutation_check.py      # from the repo root

Uses the repo's own virtualenv if it has one, else the interpreter running this script.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = pathlib.Path(sys.executable)
TESTS = ["sim/tests/test_telemetry_rollup_repair.py",
         "sim/tests/test_telemetry.py",
         "sim/tests/test_telemetry_runtime.py"]

T = "mqtt/moxie_sdk/telemetry.py"
R = "mqtt/supervisor/moxie_runtime.py"

MUTATIONS = [
    ("M1  the ring is written before the roll-up again (the 2026-09-05 red)", R,
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
    ("M2  the two writes stop being one critical section", R,
     "            with self.store.transaction(device_id, telemetry_seam.PACKETS_COLLECTION):",
     "            if True:"),
    ("M3  the read path stops reconciling the roll-up against the ring", R,
     "        rollup = telemetry_seam.reconcile_rollup(stored, ring)",
     "        rollup = telemetry_seam.reconcile_rollup(stored, [])"),
    ("M4  a repair is answered but never written back", R,
     "        if missing:\n            try:",
     "        if False:\n            try:"),
    ("M5  the stored envelope is not stamped with its sequence", R,
     "                row = telemetry_seam.with_seq(row, telemetry_seam.next_seq(ring, stored))",
     "                row = dict(row)"),
    ("M6  the insights view reads the raw roll-up instead of the reconciled one", R,
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
    ("M12 an ingest stops repairing on its way past, so only a reader can heal", R,
     "                    telemetry_seam.roll_up_packet(\n"
     "                        telemetry_seam.reconcile_rollup(stored, ring), row))",
     "                    telemetry_seam.roll_up_packet(\n"
     "                        stored, row))"),
]


def run():
    proc = subprocess.run([str(PY), "-m", "pytest", *TESTS, "-q", "--no-header"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                               "HOME": os.environ.get("HOME", "/tmp"),
                               # Blanked explicitly: a bare run finds the main worktree's
                               # `mqtt/.env` and would spend real gateway calls.
                               "MOXIE_LLM_API_KEY": "", "MOXIE_LLM_BASE_URL": "",
                               "MOXIE_VOICE_BASE_URL": "", "MOXIE_STT_BASE_URL": "",
                               "MOXIE_SKIP_DOTENV": "1"})
    return proc.returncode, proc.stdout.strip().splitlines()[-1] if proc.stdout else ""


def main():
    caught, missed = 0, []
    for label, rel, old, new in MUTATIONS:
        path = ROOT / rel
        backup = path.read_text()
        # EXACTLY once, not merely "at least once" (the rule PR #164 made repo-wide): an
        # anchor that matches twice mutates whichever copy `str.replace` reaches first,
        # so the row proves something about a line nobody chose.
        found = backup.count(old)
        if found != 1:
            missed.append(f"{label}: {'AMBIGUOUS' if found > 1 else 'NO-OP'} anchor "
                          f"({found} matches)")
            continue
        path.write_text(backup.replace(old, new, 1))
        try:
            code, tail = run()
        finally:
            path.write_text(backup)
        if code == 0:
            missed.append(f"{label}: STILL GREEN — {tail}")
        else:
            caught += 1
            print(f"✅ {label} → {tail}")
    print(f"\n{caught}/{len(MUTATIONS)} mutations caught")
    for m in missed:
        print("❌ " + m)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
