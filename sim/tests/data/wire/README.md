# 🎙️ `sim/tests/data/wire/` — shareable wire recordings

Recordings of the robot bus that are safe to commit: `--share` copies only, never a raw
recording. A raw recording holds the child's words and name and the robot's address and id; a
`--share` copy keeps the shape of every message and none of that.

- [`bench-session.jsonl`](bench-session.jsonl) — one synthetic bench session (two robots, a turn
  the robot asked again, a streamed answer with its notify reports, a goodbye, a vision event,
  the module list, the recorder losing the broker), recorded through
  [`wire_record.py`](../../../../mqtt/moxie_sdk/wire_record.py) on a fake client and shared with
  [`wire_timeline.py --share`](../../../tools/wire_timeline.py). The robot ids are placeholders,
  and every address, hostname, username and word is gone.
- [`bench-session.timeline.txt`](bench-session.timeline.txt) — the timeline of that copy: the
  golden [`test_wire_record.py`](../../test_wire_record.py) compares with what the tool prints today.

Both files are regenerated from the session in `test_wire_record.py`, never edited by hand:
`python3 sim/tests/test_wire_record.py --write-fixtures`. The test fails when either is stale,
and runs the same identity greps over every file in this folder. To add a recording from a real
robot, commit only its `--share` copy, after reading it.

---
📖 [Test data](../README.md) · [Back to top](../../../../README.md)
