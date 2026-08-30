"""
Pytest + Playwright harness for the Moxie SIL static site.

Self-contained: starts `sim/serve.py` on a free port and drives a real Chromium.
It reuses the locally-cached Chrome (the same binary the node/puppeteer tests use,
under ~/.cache/puppeteer) so nothing needs downloading. If neither Playwright's
chromium nor a local Chrome is available, the whole suite skips cleanly (exit 0),
exactly like the node browser tests — so CI stays green without a browser.

Run:
    sim/tests/.venv/bin/python -m pytest sim/tests -q
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVE = REPO / "sim" / "serve.py"

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except Exception:  # pragma: no cover - playwright not installed
    sync_playwright = None


def _find_chrome():
    env = os.environ.get("PUPPETEER_EXECUTABLE_PATH") or os.environ.get("CHROME")
    cands = [env] if env else []
    cache = Path.home() / ".cache" / "puppeteer" / "chrome"
    if cache.is_dir():
        for v in sorted(cache.iterdir(), reverse=True):
            for sub in ("chrome-linux64/chrome", "chrome-linux/chrome"):
                cands.append(str(v / sub))
    cands += ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server():
    """Start sim/serve.py on a free port for the whole session."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(SERVE), str(port)],
        cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    # wait for it to come up
    import urllib.request
    up = False
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.2)
    if not up:
        proc.kill()
        pytest.skip("serve.py did not come up")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    if sync_playwright is None:
        pytest.skip("playwright not installed (pip install playwright)")
    chrome = _find_chrome()
    with sync_playwright() as pw:
        launch = dict(args=["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        try:
            b = pw.chromium.launch(executable_path=chrome, **launch) if chrome \
                else pw.chromium.launch(**launch)
        except Exception as e:  # no browser available at all
            pytest.skip(f"no Chromium/Chrome available: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A fresh page that records console errors on `page.console_errors`."""
    page = browser.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"PAGEERR {e}"))
    page.console_errors = errors
    yield page
    page.close()


# Standard resolutions exercised across the suite (label, width, height).
RESOLUTIONS = [
    ("phone-portrait", 390, 844),
    ("phone-landscape", 844, 390),
    ("tablet-portrait", 768, 1024),
    ("tablet-landscape", 1024, 768),
    ("laptop", 1366, 768),
    ("desktop", 1920, 1080),
    ("ultrawide", 2560, 1080),
]

PAGES = ["index.html", "sim.html", "setup.html", "cloud.html", "docs.html"]
