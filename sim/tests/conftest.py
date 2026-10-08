"""Shared pytest fixtures: the dotenv/credential fence, an isolated data dir per test, and the
Playwright harness for the static site (`sim/serve.py` on a free port + a real Chromium,
reusing puppeteer's cached Chrome; the browser tests skip when none is available)."""
import itertools
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVE = REPO / "sim" / "serve.py"

# Import roots for the mqtt/ suites (moxie_sdk, config, moxie_runtime, markup, helpers_*).
# Appended, so a module that prepends its own path still wins.
for _p in (REPO / "mqtt", REPO / "mqtt" / "supervisor", Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


def pytest_addoption(parser):
    """Counts-only goodbye supervisor state file — an option, not an env var, because
    live modules may import only the narrow dotenv allowlist."""
    parser.addoption("--moxie-campaign-state-file", default="", metavar="PATH")

# The dotenv fence, decided ONCE before anything imports `config`: `config._load_env`
# promotes a git-ignored `mqtt/.env` into os.environ on the FIRST import, permanently, so
# a developer's deployment would silently decide what the hermetic suite asserts. An
# explicit opinion still wins: `MOXIE_SKIP_DOTENV=0` (run as this deployment sees it) or
# `MOXIE_DOTENV=<file>` (a fixture; `test_dotenv_cannot_perturb_the_suite.py` uses it).
_SKIP_DOTENV = "MOXIE_SKIP_DOTENV"
_DOTENV_PATH = "MOXIE_DOTENV"
if _SKIP_DOTENV not in os.environ and _DOTENV_PATH not in os.environ:
    os.environ[_SKIP_DOTENV] = "1"

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
    """`MOXIE_DATA_DIR` -> a throwaway dir, so the suite never writes into the repo."""
    prev = os.environ.get("MOXIE_DATA_DIR")
    os.environ["MOXIE_DATA_DIR"] = str(tmp_path_factory.mktemp("moxie-data"))
    yield os.environ["MOXIE_DATA_DIR"]
    if prev is None:
        os.environ.pop("MOXIE_DATA_DIR", None)
    else:
        os.environ["MOXIE_DATA_DIR"] = prev


_TEST_DATA_DIRS = itertools.count()


@pytest.fixture(scope="session")
def _test_data_dirs(tmp_path_factory):
    return tmp_path_factory.mktemp("moxie-data-per-test")


@pytest.fixture(autouse=True)
def per_test_data_dir(isolated_data_dir, _test_data_dirs):
    """...and each TEST its own `MOXIE_DATA_DIR`. A runtime on the default store reads
    every robot's saved settings (`robots/<id>/config.json`) when it is built, so one
    test's `NO_DATA` on `d_test` must not reach the next test's `d_test`. A session- or
    module-scoped fixture is built before this runs, so it keeps the session dir. One
    parent dir and a counter: `tmp_path_factory.mktemp` would rescan the basetemp per test."""
    path = os.path.join(str(_test_data_dirs), str(next(_TEST_DATA_DIRS)))
    os.makedirs(path)
    os.environ["MOXIE_DATA_DIR"] = path
    try:
        yield path
    finally:
        os.environ["MOXIE_DATA_DIR"] = isolated_data_dir


#: `helpers_runtime.LIVE_KEYS`, imported lazily: that module imports `config`, which
#: must not happen before the fence above has decided.
_LIVE_KEYS = None


def _live_keys():
    global _LIVE_KEYS
    if _LIVE_KEYS is None:
        sys.path.insert(0, str(Path(__file__).parent))
        from helpers_runtime import LIVE_KEYS
        _LIVE_KEYS = LIVE_KEYS
    return _LIVE_KEYS


@pytest.fixture(autouse=True)
def hermetic_tier_sees_no_credentials(request):
    """Hide `LIVE_KEYS` (credentials, endpoints, models) from every non-`test_live_*`
    test while it runs: "is a gateway configured?" is exactly those keys (`MOXIE_STT=auto`
    resolves to the gateway when present). Live suites read them at IMPORT, before any
    fixture, so they keep working; the keys are restored afterwards."""
    if Path(str(request.node.path)).name.startswith("test_live_"):
        yield
        return
    hidden = {k: os.environ.pop(k) for k in _live_keys() if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(hidden)


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


# Console errors that are benign for the STATIC site with no backend: served from
# localhost, the sim probes the optional sidecar/broker, and with nothing listening the
# browser logs an unsuppressable `net::ERR_CONNECTION_REFUSED`. A genuinely missing asset
# logs "...status of 404", a different string, so real regressions still fail.
_BENIGN_CONSOLE = ("favicon", "ERR_CONNECTION_REFUSED")


def _is_benign(msg: str) -> bool:
    return any(tok in msg for tok in _BENIGN_CONSOLE)


#: Chrome logs a 404 SUBRESOURCE as a console error, with no URL in the message text.
_RESOURCE_404 = "status of 404"
#: The one EXPECTED 404: `sim/web/mode.js` probes the optional capability route
#: `/api/health` on every load, and a static server has no Pages Functions behind it —
#: the `offline` path working as designed (live-sim-demo.md §6.3).
_CAPABILITY_PROBE = "/api/health"


class ConsoleErrors(list):
    """Console errors, filtered at ACCESS time: the capability probe's 404 line is benign
    only if no OTHER 404 was seen, which is unknowable until the page has loaded. A real
    missing asset (in `unexpected`) switches the suppression off."""

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
    """A fresh page recording real console errors on `page.console_errors`."""
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
