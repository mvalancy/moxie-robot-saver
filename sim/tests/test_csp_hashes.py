"""The `script-src` hashes in `sim/web/_headers` must match the pages on disk.

The failure is a BLANK page on the live domain only: `_headers` is sent by Cloudflare
Pages alone, so an edited inline `<script>` whose SHA-256 is not listed is refused there
while local suites serve fresh bytes. Browser-free half; `sim/test_csp.mjs` re-checks it
from the headers a browser received.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import build_csp_hashes as gen  # noqa: E402


def test_script_src_matches_the_pages_on_disk():
    hashes, blocks, problems = gen.scan()
    assert not problems, "\n".join(problems)
    _text, _csp, current = gen.read_policy()
    assert current == gen.script_src(hashes, current), (
        "sim/web/_headers script-src is STALE — this BLANKS THE PAGE. "
        "Run: python3 sim/tools/build_csp_hashes.py")
    assert gen.main(["--check"]) == 0, "the exact check CI runs"


def test_script_src_has_no_inline_escape_hatch():
    """`'unsafe-inline'` was the honest gap until 2026-09-04; `'unsafe-hashes'` is what a
    future `onclick=` would reach for."""
    _text, csp, current = gen.read_policy()
    for bad in ("'unsafe-inline'", "'unsafe-hashes'", "'unsafe-eval'", "'strict-dynamic'"):
        assert bad not in current
    assert "'unsafe-hashes'" not in csp


def test_the_inline_surface_is_the_one_importmap():
    """Thirteen of fourteen blocks became files (a file cannot drift from a header). The
    importmap cannot be external in any browser; anything new should be a file too."""
    _hashes, blocks, _problems = gen.scan()
    assert [(b[0], 'type="importmap"' in b[2]) for b in blocks] == [("sim.html", True)], blocks


def test_a_drifted_block_or_an_inline_handler_is_caught(tmp_path, monkeypatch):
    """Negative control on a COPY of the site (never the real page)."""
    web = tmp_path / "web"
    shutil.copytree(gen.WEB, web, ignore=shutil.ignore_patterns("vendor", "docs-bundle"))
    page = web / "sim.html"
    old = '"three": "./vendor/three/three.module.min.js"'
    assert page.read_text().count(old) == 1
    page.write_text(page.read_text().replace(old, old.replace(":", ":  ", 1))
                    + '\n<button onclick="go()">x</button>\n<a href="javascript:go()">y</a>\n')
    monkeypatch.setattr(gen, "WEB", str(web))
    hashes, _blocks, problems = gen.scan()
    _text, _csp, current = gen.read_policy()
    assert current != gen.script_src(hashes, current)
    assert len(problems) == 2 and "event-handler" in problems[0] and "javascript:" in problems[1]
    assert gen.main(["--check"]) == 1


def test_the_generator_owns_the_hashes_and_nothing_else():
    """Host allowances are policy argued in `_headers`: carried through in order."""
    got = gen.script_src(["'sha256-B'"], "script-src 'self' 'sha256-A' 'unsafe-inline' "
                                         "https://static.cloudflareinsights.com")
    assert got == "script-src 'self' 'sha256-B' https://static.cloudflareinsights.com"
    assert gen.script_src(["'sha256-B'"], "script-src https://x.example") == \
        "script-src 'self' 'sha256-B' https://x.example"
