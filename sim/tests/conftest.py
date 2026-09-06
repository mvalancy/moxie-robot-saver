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

# --------------------------------------------------------------------------- #
# The dotenv fence — the suite decides ONCE, here, whether a deployment's
# `mqtt/.env` is visible to it. Everything below happens at conftest *import*,
# which is the only moment early enough to matter.
# --------------------------------------------------------------------------- #
#
# Playbook rule 20 found that a git-ignored `mqtt/.env` refills variables a test
# deleted, so "nothing is configured" silently became "whatever this developer
# configured" — invisible to CI and to every worktree, because that is exactly
# where the file does not exist. The opt-out added for it, `MOXIE_SKIP_DOTENV`,
# was then set inside each affected test helper. **That does not work, and this
# block exists because it does not work.**
#
# `config._load_env` loads the file with `os.environ.setdefault(...)`. The first
# `import config` anywhere in the session therefore promotes every key in the
# file to a real environment variable, permanently — nothing ever removes them.
# From that instant `MOXIE_SKIP_DOTENV` is a no-op: it stops the *file* being
# re-read, and the values are no longer coming from the file. Measured on
# 2026-09-05 with a fixture dotenv: `test_assemble.py` and `test_voice_settings.py`
# pass when run ALONE (their helper sets the flag before anything else imports
# `config`) and fail in the full suite (something imported `config` first). So the
# flag is a **first-import-wins** switch that every existing caller sets too late,
# and whether a given test asserts anything depends on collection order.
#
# The second, independent leak in the same fix: those helpers delete a
# hand-maintained LIST of variable names. `test_assemble._fresh_config` lists nine
# and `mqtt/.env.example` documents twenty-five, so `MOXIE_PIPER_MODEL` — absent
# from the list — still reached `build_synthesizer()` and the "no voice configured"
# assertion tested a machine with a voice. A denylist that must enumerate every
# future knob is not a fence.
#
# Both are fixed by deciding before the first import instead of after it, in the
# one file pytest guarantees to import before it collects anything. With this,
# a local run and a CI run are the same run — which is the actual goal: a baseline
# nobody can reproduce cannot catch a regression.
#
# It is deliberately NOT unconditional. Rule 20 was found *by* running the suite
# against a real dotenv, and a fence that made that impossible would close the
# only door the defect ever walked through. So an explicit opinion always wins:
#   * `MOXIE_SKIP_DOTENV=0 pytest sim/tests`   → run it as this deployment sees it
#   * `MOXIE_DOTENV=<file> pytest sim/tests`   → run it against a fixture
# and `test_dotenv_cannot_perturb_the_suite.py` uses the second of those to prove,
# in CI and with a throwaway file, that the fence is real and that removing it
# turns the suite red. Never point either at a developer's own `mqtt/.env`.
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


#: `helpers_runtime.LIVE_KEYS`, imported once and lazily. Lazily because importing that
#: module pulls in `moxie_sdk` and therefore `config`, and this file's whole subject is
#: which import of `config` happens first — so the fixture below reaches for it when a
#: test is about to run, long after the block at the top of this file has decided.
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
    """The second half of the fence — the one the block at the top of this file cannot do.

    There are TWO dotenv loaders. `config._load_env` is fenced above, before the first
    import. `helpers_runtime.load_repo_dotenv` is the other one, and it deliberately is
    **not** fenced: ten `test_live_*.py` modules call it at import to find a real key,
    and a fence there would turn every one of them into a silent skip — the exact
    regression PR #157 was opened to close, and a green run that tested nothing.

    So that loader was narrowed instead (`LIVE_KEYS`): a deployment's `mqtt/.env` can now
    export credentials, endpoints and model names, and nothing else — no
    `MOXIE_ALLOW_UNVERIFIED_BOTS`, no `MOXIE_APP`, no `MOXIE_STT`. That removed 16 of the
    21 tests a maximal dotenv used to break. The remaining five cannot be fixed there, and
    it is worth being clear about why: a credential and an endpoint **are** what "is a
    gateway configured?" means. `MOXIE_STT=auto` resolves to the gateway exactly when an
    STT URL and a key are present, so `test_assemble.py`'s "auto is None without whisper"
    and three `test_voice_settings.py` defaults still moved — on the credentials the live
    tier cannot do without. Narrowing further would take the key away from the live suites;
    narrowing less leaves hermetic tests reading a developer's gateway.

    The way out is that those are different tests. A live suite reads its credentials at
    IMPORT, into module constants, before any fixture runs; a hermetic test reads the
    environment while it runs. So the credentials stay in `os.environ` for collection and
    are hidden for the duration of every non-live test. Together the two mechanisms are
    total: the allowlist bounds what a dotenv can put into the process at all, and this
    hides exactly that bound set from everything hermetic — so a hermetic test sees
    nothing of the file, which is what makes a local run and a CI run the same run.

    Deliberately keyed on the filename rather than a marker: `test_live_*` is already the
    convention `test_ci_workflows.py` enforces for "this suite needs credentials", so
    there is one definition of a live suite and not two. `test_env_hygiene_live_suites.py`
    is hermetic despite its name and is correctly treated as such — its own docstring
    explains why it refuses the prefix.

    Restores whatever it removed, so a suite that asserts a module left the environment
    as it found it still sees a symmetric picture.
    """
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
