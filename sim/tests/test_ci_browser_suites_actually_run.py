"""The browser suites must actually RUN in CI — for months they silently did not.

`sim/browser_harness.mjs::loadPuppeteer` imports `puppeteer`, then falls back to scanning
`~/Code/*/node_modules/puppeteer` — a developer-machine path that cannot exist on a GitHub
runner. `skipper()` then called `process.exit(0)`. No workflow file installed puppeteer. So
nine suites printed "skipped — puppeteer not found" on every run and the job stayed GREEN.

That deleted the repo's best guards without anyone seeing it. `test_typed_turn.mjs` exists
*because* PR #82 shipped 770 assertions that all read a file while Web Audio was stubbed; its
teeth are a real peak-amplitude assertion so a silent clip cannot pass. It had never executed
in CI. Neither had `test_mic_spend.mjs`, which counts `/api/chat` calls to prove a refused
microphone does not spend the gateway the owner's video game shares.

A green badge over assertions that never fired is exactly what #82 taught, one layer up.
These two tests are the ratchet.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_YML = ROOT / "sim" / "ci" / "ci.yml"
HARNESS = ROOT / "sim" / "browser_harness.mjs"


def _browser_suites():
    """Suite files that need a real browser, found by what they import — not by a hand list."""
    out = []
    for p in sorted((ROOT / "sim").glob("test_*.mjs")):
        src = p.read_text(encoding="utf-8")
        if "loadPuppeteer" in src or "requireBrowser" in src:
            out.append(p.name)
    return out


def test_ci_installs_a_browser_before_it_runs_any_browser_suite():
    """An install step must come BEFORE the first browser suite, not after it.

    The original bug was subtle in exactly this way: the workflow DID install a browser
    (`playwright install chromium`), but ~40 lines *after* every browser step had already
    run and skipped. Order is the whole assertion.
    """
    yml = CI_YML.read_text(encoding="utf-8")
    suites = _browser_suites()
    assert suites, "found no browser suites — has the harness API been renamed?"

    install = re.search(r"^\s*run:\s*npm (?:install|i)\b.*\bpuppeteer\b", yml, re.M)
    assert install, (
        "sim/ci/ci.yml installs no puppeteer. Without it every browser suite skips and the "
        f"tier stays green while {len(suites)} suites assert nothing: {', '.join(suites)}"
    )

    # Match the DISPATCH (`run: node sim/test_x.mjs`), not any mention — the explanatory
    # comment above the install step names two suites, and an `index()` on the bare filename
    # finds the prose instead of the step. That false positive is exactly the kind of thing
    # that makes a guard useless, so the pattern is anchored to how a step is actually run.
    dispatches = [
        m.start()
        for m in re.finditer(r"^\s*run:\s*node\s+sim/(test_[A-Za-z0-9_]+\.mjs)", yml, re.M)
        if m.group(1) in suites
    ]
    assert dispatches, "no browser suite is dispatched by ci.yml at all"
    first_use = min(dispatches)
    assert install.start() < first_use, (
        "the browser is installed AFTER the first browser suite runs, so those suites still "
        "skip. That is the exact shape of the original defect: a real install step, in the "
        "wrong place, reading as coverage."
    )


def test_a_missing_browser_is_a_failure_under_ci_and_a_clean_skip_locally():
    """Every skip path must be CI-aware; a skip nobody can see is not a skip.

    Checked at the source level across every suite, because four suites had their OWN local
    `skip()` and a fix applied only to the shared harness would have left them silently green.
    """
    harness = HARNESS.read_text(encoding="utf-8")
    assert "process.env.CI" in harness and "process.exit(1)" in harness, (
        "browser_harness.mjs::skipper must exit non-zero under CI, or a broken install "
        "silently deletes coverage again"
    )

    offenders = []
    for name in _browser_suites():
        src = (ROOT / "sim" / name).read_text(encoding="utf-8")
        # a suite may define its own skip, but it must be CI-aware — either by delegating to
        # the shared skipper, or by checking CI itself.
        defines_own = re.search(r"function skip\s*\(", src)
        if defines_own and "process.env.CI" not in src and "skipper(" not in src:
            offenders.append(name)
    assert not offenders, (
        "these suites define their own skip() that ignores CI, so they exit 0 on a runner "
        f"with no browser: {', '.join(offenders)}"
    )
