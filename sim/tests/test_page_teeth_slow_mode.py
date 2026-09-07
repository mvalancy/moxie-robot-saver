"""`sim/tools/page_teeth_check.py --slow` — the plumbing, guarded without a browser.

WHY THIS FILE EXISTS. `--slow` reports a finding only when a check FLIPS from green to
red. That makes its failure mode silent and inverted: if the CPU throttle never reaches
the browser, every row reads "no findings" — which looks exactly like a clean sweep. The
tool's own docstring already records this happening once, to the *network* throttle:
`emulateNetworkConditions` threw on every page inside a bare `except`, the 24 MB stall
file arrived in 231 ms, and the whole not-loaded-yet family would have been reported as
absent. A swallowed exception in an instrument is the instrument lying.

`page_teeth_check.py --selftest` proves the live half (it runs a real suite under the
throttle and requires `cpuThrottled > 0`), but it needs Chrome and about a minute. This
file is the hermetic ratchet underneath it: it reads the two files and asserts that the
three ends of the wire still meet — the flag sets the variable, the ledger reads that
same variable, and neither half is allowed to fail quietly. A rename on one side and not
the other is caught here, in the fast tier, with no browser at all.

It deliberately asserts SHAPE, not behaviour: behaviour is `--selftest`'s job.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOL = REPO / "sim" / "tools" / "page_teeth_check.py"
LEDGER = REPO / "sim" / "tools" / "teeth_ledger.mjs"

#: The one name that has to mean the same thing in a Python file and a Node file. It is
#: not importable from either side, so it is asserted from both.
ENV = "MOXIE_TEETH_CPU"


def _tool() -> str:
    return TOOL.read_text()


def _ledger() -> str:
    return LEDGER.read_text()


def test_the_flag_exists_and_hands_the_rate_to_the_ledger():
    src = _tool()
    assert '"--slow"' in src, "page_teeth_check.py lost its --slow flag"
    assert "def slow_sweep(" in src, "--slow has no implementation"
    # The rate has to travel as the environment variable the ledger reads, not as an
    # argument the ledger cannot see.
    assert re.search(rf'{{"{ENV}": str\(rate\)}}', src), (
        f"slow_sweep no longer passes the rate through {ENV}; the ledger reads nothing else"
    )
    assert re.search(r"if\s+a\.slow:\s*\n\s*return slow_sweep\(", src), (
        "main() no longer dispatches --slow to slow_sweep"
    )


def test_the_ledger_reads_that_same_variable_and_applies_it_over_cdp():
    src = _ledger()
    assert f"process.env.{ENV}" in src, f"teeth_ledger.mjs no longer reads {ENV}"
    # Puppeteer 24.43's Page has no `emulateCPUThrottlingRate` — verified on this repo's
    # pinned version, where it throws `is not a function`. CDP is the only route, so a
    # future edit that "simplifies" it back to the API method must fail here rather than
    # silently throttling nothing.
    assert "Emulation.setCPUThrottlingRate" in src, (
        "the CPU throttle is no longer applied over CDP; puppeteer's Page has no "
        "emulateCPUThrottlingRate on the version this repo pins"
    )
    assert "cpuThrottled" in src, "the ledger no longer counts the pages it throttled"
    assert re.search(r"JSON\.stringify\(\{[^}]*cpuThrottled", src), (
        "cpuThrottled is counted but never written to the ledger, so nothing can check it"
    )


def test_a_throttle_that_did_not_apply_is_never_read_as_a_result():
    """The inverted-failure guard, on both sides of the wire.

    The ledger must RECORD a failed throttle (a note, not a swallowed exception) and the
    sweep must REFUSE to read a row whose ledger says the throttle never landed. Either
    half alone leaves "no findings" meaning two different things.
    """
    ledger = _ledger()
    cpu_block = ledger.split("if (CPU > 1)", 1)
    assert len(cpu_block) == 2, "the CPU throttle block moved; re-anchor this guard"
    body = cpu_block[1][:800]
    assert "notes.push(" in body, (
        "a CPU throttle that threw is swallowed — the sweep would report about a browser "
        "that was never slowed down"
    )
    assert "console.error(" in body, "a failed CPU throttle is not visible in the run's output"

    tool = _tool()
    assert re.search(r'if not led\.get\("cpuThrottled"\):', tool), (
        "slow_sweep no longer refuses a suite whose ledger reports no throttled page"
    )


def test_a_check_that_is_about_time_is_set_aside_rather_than_dropped():
    """A slow renderer is SUPPOSED to move a frame-budget assertion.

    Reporting those next to a drawer that never opened would bury the finding; dropping
    them would hide a real one. The tool keeps them in their own bucket, and the bucket
    has to keep existing.
    """
    src = _tool()
    assert "TIME_CLAIMS" in src, "the time-claiming filter is gone"
    assert re.search(r"SET ASIDE — checks whose own message is about time", src), (
        "set-aside checks are no longer reported, so nobody can see what was filtered"
    )
    # It must not be able to swallow an arbitrary message: a filter that matched
    # everything would silence the whole sweep.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ptc", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert not mod.TIME_CLAIMS.search(
        "phone: tapping the handle really opens the drawer"), (
        "TIME_CLAIMS matches a plain layout assertion — it would set aside real findings"
    )
    assert mod.TIME_CLAIMS.search("hidden page: 3 frames in 200 ms is under budget"), (
        "TIME_CLAIMS no longer matches a frame-budget assertion"
    )


def test_the_selftest_still_proves_the_slow_instrument_applied():
    src = _tool()
    assert "── the --slow instrument ──" in src, (
        "--selftest no longer proves the CPU throttle reaches a real browser; without it "
        "a silent throttle would report 'no findings' forever"
    )
