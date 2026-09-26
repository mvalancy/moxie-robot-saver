"""Import the parent console (`server/moxie_server`) in-process for a test.

One place for the three things every console test otherwise repeats: point `MOXIE_DB`
somewhere disposable BEFORE the import (`db.init()` runs at import time and would
otherwise create `server/moxie.db` in the working tree), put `server/` on the path, and
aim the supervisor proxy at a status URL. Skips cleanly when fastapi/httpx or the
server's own deps (pynacl, segno) are absent.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def console_app(db_path, status_url="http://127.0.0.1:1/status"):
    """`(TestClient class, main module)` with the supervisor proxy aimed at `status_url`."""
    pytest.importorskip("fastapi", reason="the console app")
    pytest.importorskip("httpx", reason="fastapi's TestClient")
    if "moxie_server.db" not in sys.modules:   # the DB path is fixed at first import
        os.environ["MOXIE_DB"] = str(db_path)
    os.environ["MOXIE_SUPERVISOR_STATUS"] = status_url
    if os.path.join(REPO, "server") not in sys.path:
        sys.path.insert(0, os.path.join(REPO, "server"))
    try:
        from fastapi.testclient import TestClient
        from moxie_server import main
    except Exception as e:                      # pynacl / segno not in this env
        pytest.skip(f"console app not importable: {e}")
    set_status_url(status_url)
    return TestClient, main


def set_status_url(url, monkeypatch=None):
    """Re-aim the console's supervisor proxy (read from the env only at import)."""
    from moxie_server import supervisor
    if monkeypatch is not None:
        monkeypatch.setattr(supervisor, "STATUS_URL", url)
    else:
        supervisor.STATUS_URL = url


def server_source() -> str:
    """Every `server/moxie_server/**/*.py` concatenated — for route-literal pins that must
    run where fastapi is not installed."""
    root = os.path.join(REPO, "server", "moxie_server")
    out = []
    for d, _, files in sorted(os.walk(root)):
        out += [open(os.path.join(d, f)).read() for f in sorted(files) if f.endswith(".py")]
    return "\n".join(out)


def console_js() -> str:
    """The console's scripts, in load order (index.html's <script src> tags)."""
    import re
    static = os.path.join(REPO, "server", "static")
    html = open(os.path.join(static, "index.html")).read()
    srcs = re.findall(r'<script src="/?([^"]+)"', html)
    return "\n".join(open(os.path.join(static, s)).read() for s in srcs)
