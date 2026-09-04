"""The `script-src` hashes in `sim/web/_headers` must match the pages on disk.

THE FAILURE THIS PREVENTS IS NOT A DEGRADED PAGE, IT IS A BLANK ONE. `_headers` is a static
file that only Cloudflare Pages ever sends, so an inline `<script>` can run under
`script-src 'self'` only if its exact SHA-256 is listed. Edit the block, forget the header,
and the browser refuses it — on the live domain, silently, because every local suite serves
the bytes it just built rather than the header that shipped.

This is the fast, browser-free half of that guard: it runs in the ordinary pytest job in
about a millisecond, so a stale header is caught long before the browser tier. Block 6 of
`sim/test_csp.mjs` asserts the same thing again in JavaScript, from the live headers a
browser actually received.

Run:  MOXIE_LLM_API_KEY= .venv/bin/python -m pytest sim/tests/test_csp_hashes.py -q
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, "sim", "tools", "build_csp_hashes.py")

sys.path.insert(0, os.path.join(REPO, "sim", "tools"))
import build_csp_hashes as gen  # noqa: E402


def test_script_src_matches_the_pages_on_disk():
    """The committed header is what a fresh generation would produce."""
    hashes, blocks, problems = gen.scan()
    assert not problems, "\n".join(problems)
    _text, _csp, current = gen.read_policy()
    assert current == gen.script_src(hashes, current), (
        "sim/web/_headers script-src is STALE — an inline block changed and the header did "
        "not. Shipping this BLANKS THE PAGE.\n"
        "  have: %s\n  want: %s\n  blocks: %s\n"
        "  fix:  python3 sim/tools/build_csp_hashes.py" % (
            current, gen.script_src(hashes, current),
            ", ".join("%s:%d" % (b[0], b[1]) for b in blocks)))


def test_the_check_mode_agrees_and_is_what_ci_runs():
    """The tool's own `--check` is green — this is the exact command CI runs."""
    r = subprocess.run([sys.executable, TOOL, "--check"], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_script_src_has_no_inline_escape_hatch():
    """`'unsafe-inline'` was the honest gap until 2026-09-04. It must not come back.

    `'unsafe-hashes'` is asserted too: it is the obvious thing a future pass reaches for the
    moment someone adds an `onclick=` attribute, and it re-opens the same door one handler
    at a time.
    """
    _text, csp, current = gen.read_policy()
    for bad in ("'unsafe-inline'", "'unsafe-hashes'", "'unsafe-eval'", "'strict-dynamic'"):
        assert bad not in current, "script-src must not carry %s (got %r)" % (bad, current)
    assert "'unsafe-hashes'" not in csp, "the CSP must not carry 'unsafe-hashes' anywhere"


def test_no_page_carries_an_inline_event_handler_attribute():
    """No hash this policy grants can cover one, and they fail SILENTLY.

    `<button onclick="f()">` needs `'unsafe-hashes'` plus a hash per handler. Without it the
    handler does not error — it simply never fires, which is the kind of defect that reaches
    a visitor rather than a test. Note what is NOT a violation and was miscounted in
    `_headers` until 2026-09-04: `el.onclick = function(){}` in a .js file assigns a function
    OBJECT and is not an inline script at all.
    """
    _hashes, _blocks, problems = gen.scan()
    assert not problems, "\n".join(problems)


def test_the_inline_surface_is_one_block_and_says_why():
    """Thirteen of the original fourteen blocks are FILES now. Keep it that way.

    A file cannot drift out of sync with a header, so every block that becomes a file
    removes a way to blank the page. The one survivor is `sim.html`'s importmap, which
    cannot be external in any browser: `<script type="importmap" src>` was dropped from the
    spec. If this ever grows, the fix is another file, not another hash.
    """
    _hashes, blocks, _problems = gen.scan()
    assert len(blocks) == 1, "expected one inline block, got: %s" % (
        ", ".join("%s:%d" % (b[0], b[1]) for b in blocks))
    name, _line, attrs, _h = blocks[0]
    assert name == "sim.html" and 'type="importmap"' in attrs, (name, attrs)


@pytest.mark.parametrize("mutation", [
    ('"three": "./vendor/three/three.module.js"', '"three":  "./vendor/three/three.module.js"'),
])
def test_a_drifted_block_is_caught(tmp_path, monkeypatch, mutation):
    """NEGATIVE CONTROL: a one-character edit to the hashed block must redden this.

    Without this, every assertion above is equally consistent with "the guard works" and
    "the guard cannot see anything".
    """
    page = os.path.join(gen.WEB, "sim.html")
    original = open(page, encoding="utf-8").read()
    old, new = mutation
    assert original.count(old) == 1, "the mutation no longer applies — update this control"
    try:
        open(page, "w", encoding="utf-8").write(original.replace(old, new, 1))
        hashes, _blocks, _problems = gen.scan()
        _text, _csp, current = gen.read_policy()
        assert current != gen.script_src(hashes, current), "a drifted inline block was NOT caught"
        r = subprocess.run([sys.executable, TOOL, "--check"], capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 1 and "STALE" in r.stdout
    finally:
        open(page, "w", encoding="utf-8").write(original)
