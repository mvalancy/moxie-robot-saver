"""Shared scaffolding for the toolkit round-trip scripts (`test_*.py`, run by
`run_tests.py`): collect failures with `ok`, round-trip a message with `rt`, finish with
`report` (exit 1 listing every failure, else one ✅ line)."""
import sys

fails = []


def ok(cond, msg):
    if not cond:
        fails.append(msg)


def rt(msg):
    """`msg` serialized and parsed back — the round trip under test."""
    out = type(msg)()
    out.ParseFromString(msg.SerializeToString())
    return out


def report(name, summary):
    if fails:
        print(f"❌ {name} toolkit test FAILED:")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print(f"✅ {name} toolkit test OK — {summary}")
