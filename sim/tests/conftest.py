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


@pytest.fixture(autouse=True, scope="session")
def isolated_data_dir(tmp_path_factory):
    """Keep the runtime's durable store (`moxie_sdk/store.py`) out of the working tree.

    The supervisor persists per-robot state (mentor behaviors) under `MOXIE_DATA_DIR`,
    default `mqtt/data/`. Point it at a throwaway directory for the whole test session so
    the suite stays hermetic and never leaves files in the repo."""
    prev = os.environ.get("MOXIE_DATA_DIR")
    os.environ["MOXIE_DATA_DIR"] = str(tmp_path_factory.mktemp("moxie-data"))
    yield os.environ["MOXIE_DATA_DIR"]
    if prev is None:
        os.environ.pop("MOXIE_DATA_DIR", None)
    else:
        os.environ["MOXIE_DATA_DIR"] = prev


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
        # --autoplay-policy: the SIM plays the server's CloudTTSResponse through Web
        # Audio; without this the context stays suspended until a user gesture and the
        # TTS-playback tests would be testing the gesture path, not the audio path.
        launch = dict(args=["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                            "--autoplay-policy=no-user-gesture-required"])
        try:
            b = pw.chromium.launch(executable_path=chrome, **launch) if chrome \
                else pw.chromium.launch(**launch)
        except Exception as e:  # no browser available at all
            pytest.skip(f"no Chromium/Chrome available: {e}")
        yield b
        b.close()


# Console errors that are benign for the STATIC site running with no backend.
# The sim is designed to run bus-free (hand-control mode); when served from
# localhost it probes the optional local sidecar/broker, and if nothing is
# listening the browser's network layer emits `net::ERR_CONNECTION_REFUSED`
# (unsuppressable from JS). That is expected here — CI runs the static server
# only, no broker — so it is not a page defect. A genuinely missing asset is a
# 404 ("...status of 404"), a different string, so real regressions still fail.
_BENIGN_CONSOLE = ("favicon", "ERR_CONNECTION_REFUSED")


def _is_benign(msg: str) -> bool:
    return any(tok in msg for tok in _BENIGN_CONSOLE)


#: Chrome logs a 404 SUBRESOURCE as a console error, with no URL in the message text.
_RESOURCE_404 = "status of 404"
#: The one 404 the static test server is EXPECTED to produce: `sim/web/mode.js` probes the
#: optional same-origin capability route `/api/health` on every load, and a static server
#: has no Pages Functions behind it. That miss is the `offline` path working as designed
#: (spec docs/architecture/backlog/live-sim-demo.md §6.3 — an absent route leaves the page
#: byte-identical to the pre-Functions site), and it is the same category as
#: ERR_CONNECTION_REFUSED above: an optional backend that is not there.
_CAPABILITY_PROBE = "/api/health"


class ConsoleErrors(list):
    """The console errors a test should care about.

    A `list` so the existing assertion sites keep working unchanged, but the view is
    computed at ACCESS time rather than at capture time — and that is the point. Whether
    the capability probe's 404 line is benign depends on whether any OTHER 404 was seen,
    which is only knowable once the page has finished loading. Filtering as each message
    arrived would depend on the console event and the response event racing in the right
    order; filtering when a test asserts cannot.

    A genuinely missing asset therefore still fails: its 404 lands in `unexpected` and the
    suppression switches off for the whole page, so every 404 line is reported.
    """

    def __init__(self, raw, unexpected):
        super().__init__()
        self._raw = raw
        self._unexpected = unexpected

    def _view(self):
        if self._unexpected:
            return list(self._raw)
        return [m for m in self._raw if _RESOURCE_404 not in m]

    def __iter__(self):
        return iter(self._view())

    def __len__(self):
        return len(self._view())

    def __bool__(self):
        return bool(self._view())

    def __getitem__(self, index):
        return self._view()[index]

    def __repr__(self):
        return repr(self._view())

    @property
    def unexpected_404(self):
        """404s that were NOT the optional capability probe — a real missing asset."""
        return list(self._unexpected)


@pytest.fixture
def page(browser):
    """A fresh page that records real console errors on `page.console_errors`.

    Benign 'optional backend absent' errors (see `_BENIGN_CONSOLE`) are filtered
    at capture, so the suite is hermetic — it passes with OR without a broker up.
    The optional `/api/health` capability probe's 404 is filtered at access time
    instead (see `ConsoleErrors`), because judging it needs the whole page load.
    """
    page = browser.new_page()
    raw, unexpected = [], []
    page.on("console",
            lambda m: raw.append(m.text)
            if m.type == "error" and not _is_benign(m.text) else None)
    page.on("pageerror", lambda e: raw.append(f"PAGEERR {e}"))
    page.on("response",
            lambda r: unexpected.append(r.url)
            if r.status == 404 and _CAPABILITY_PROBE not in r.url else None)
    page.console_errors = ConsoleErrors(raw, unexpected)
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
