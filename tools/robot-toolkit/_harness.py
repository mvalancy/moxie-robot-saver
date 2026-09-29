"""Shared scaffolding for the toolkit scripts (`test_*.py`, run by `run_tests.py`): collect
failures with `ok`, finish with `report` (exit 1 listing every failure, else one ✅ line).

What these scripts prove: the committed `embodied.*` bindings still carry the recovered
message/field/enum names and values (building a message with a missing field raises), the
`bus` registries name the right classes, and our builders/parsers produce the right message.
Serializing a message and parsing it back with the same binding proves only protobuf, so
they do not do that."""
import sys

fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)


def report(name, summary):
    if fails:
        print(f"❌ {name} toolkit test FAILED:")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print(f"✅ {name} toolkit test OK — {summary}")
