"""A node test may not *assign* to a global that newer Node makes getter-only.

`globalThis.navigator` became a **getter-only accessor in Node 21**. Assigning to it
throws `TypeError: Cannot set property navigator of #<Object> which has only a getter`.
This machine runs Node 20, where the assignment is accepted; CI runs Node 24, where it is
not — so `sim/test_demo_ears.mjs` passed locally and failed the SIL job (run 33732487004).

That is the same shape as the Cloudflare Pages JSON-import failure recorded as playbook
rule 19: *a feature cannot be validated by the runtime the tests happen to run on*. The
fix there and here is the same — convert the deploy-only (or Node-version-only) failure
into a local one, which is what this guard does.

`Object.defineProperty(globalThis, "navigator", {...})` works on every version and is the
required form. Comments are stripped before scanning, because a guard that string-matches
over a whole file fires on the prose explaining it (playbook rule 17, learnt the same way).
"""
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")

#: Globals that are accessor-only on some supported Node version. `fetch` is deliberately
#: absent: it is a writable data property, and several suites assign it legitimately.
GETTER_ONLY = ("navigator", "crypto")

_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"^\s*//.*$", re.M)


def _code(src: str) -> str:
    """The file with comments removed, so prose about the rule cannot trip the rule."""
    return _LINE.sub("", _BLOCK.sub("", src))


def _mjs_files():
    return sorted(
        os.path.join(SIM, f) for f in os.listdir(SIM)
        if f.startswith("test_") and f.endswith(".mjs")
    )


def test_there_are_node_suites_to_check():
    """A guard that silently checks nothing is worse than no guard."""
    assert len(_mjs_files()) >= 5, _mjs_files()


@pytest.mark.parametrize("name", GETTER_ONLY)
def test_no_suite_assigns_to_a_getter_only_global(name):
    pat = re.compile(rf"\b(?:globalThis|global)\.{name}\s*=(?!=)")
    offenders = []
    for path in _mjs_files():
        with open(path) as fh:
            if pat.search(_code(fh.read())):
                offenders.append(os.path.relpath(path, REPO))
    assert not offenders, (
        f"{offenders} assign to `{name}`, which is getter-only on Node 21+ and will throw "
        f"in CI while passing on an older local Node. Use "
        f'Object.defineProperty(globalThis, "{name}", {{ configurable: true, '
        f"writable: true, value: ... }}) instead."
    )


def test_the_guard_would_catch_the_real_regression():
    """Negative control: the pattern must fire on the exact line that broke CI, and must
    NOT fire on the accepted form or on a comment describing either."""
    pat = re.compile(r"\b(?:globalThis|global)\.navigator\s*=(?!=)")
    assert pat.search(_code("globalThis.navigator = { mediaDevices: {} };"))
    assert pat.search(_code("global.navigator = {};"))
    assert not pat.search(_code(
        'Object.defineProperty(globalThis, "navigator", { value: {} });'))
    assert not pat.search(_code("// globalThis.navigator = {} would throw on Node 21+"))
    assert not pat.search(_code("/* globalThis.navigator = {} is forbidden */"))
    assert not pat.search(_code("if (globalThis.navigator == null) {}"))
