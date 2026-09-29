"""A node test may not *assign* to a global that newer Node makes getter-only.

`globalThis.navigator` became getter-only in Node 21: the assignment passed on a local
Node 20 and threw in CI's Node 24 (`test_demo_ears.mjs`, run 33732487004). Use
`Object.defineProperty(globalThis, "navigator", {...})`. Comments are stripped first.
"""
import glob
import os
import re

import pytest

SIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
#: Accessor-only on some supported Node. `fetch` is a writable data property.
GETTER_ONLY = ("navigator", "crypto")


def _code(src: str) -> str:
    return re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S), flags=re.M)


def _assigns(src: str, name: str) -> bool:
    return bool(re.search(rf"\b(?:globalThis|global)\.{name}\s*=(?!=)", _code(src)))


@pytest.mark.parametrize("src, bad", [
    ("globalThis.navigator = { mediaDevices: {} };", True),
    ("global.navigator = {};", True),
    ('Object.defineProperty(globalThis, "navigator", { value: {} });', False),
    ("// globalThis.navigator = {} would throw on Node 21+", False),
    ("/* globalThis.navigator = {} is forbidden */", False),
    ("if (globalThis.navigator == null) {}", False),
])
def test_the_pattern(src, bad):
    assert _assigns(src, "navigator") is bad


def test_no_suite_assigns_to_a_getter_only_global():
    files = glob.glob(os.path.join(SIM, "test_*.mjs")) + glob.glob(
        os.path.join(SIM, "tests", "edge", "**", "*.mjs"), recursive=True)
    assert len(files) >= 5
    offenders = [(os.path.basename(p), n) for p in files for n in GETTER_ONLY
                 if _assigns(open(p).read(), n)]
    assert not offenders, f"{offenders}: use Object.defineProperty(globalThis, name, ...)"
