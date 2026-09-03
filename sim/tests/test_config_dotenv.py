"""
"Nothing is configured" has to be able to MEAN nothing.

`mqtt/config.py` loads `mqtt/.env` with `os.environ.setdefault(...)` at import. That is
right for an appliance — an explicit variable still wins — and wrong for a test suite:
every test that simulates an unset variable does it by deleting the variable and
reloading the module, at which point the loader **refilled it from the file**. So on any
machine that has a real `mqtt/.env` those tests asserted whatever that developer happened
to have configured.

It was invisible because `.env` is git-ignored: it exists in a main checkout and in no CI
runner and no git worktree, which is exactly where the suite normally runs. Measured on
2026-09-03 in the main checkout: **12 failures** across `test_assemble.py`,
`test_stt_gateway.py` and `test_voice_settings.py` (`build_synthesizer` returning a
`FallbackSynthesizer` where the test asserts `None`, the gateway ears selected where the
test asserts local whisper, …). Move the file aside: **3975 passed**. Orchestration
playbook rule 20.

The fix is an explicit opt-out, `MOXIE_SKIP_DOTENV`, checked before the file is opened,
plus `MOXIE_DOTENV` to point the loader at another file. Both are read from the
ENVIRONMENT only — a file cannot carry the flag that decides whether it is read — and the
second is what lets this file test the loader against a real dotenv **without going
anywhere near a developer's own `mqtt/.env`**, which no test may read, write or move.

Why an env flag rather than an injectable path alone: the tests reload the *module*, and
module-level `_load_env()` takes no arguments, so a parameter is unreachable from
`importlib.reload`. The flag is the only opt-out that reaches the import-time call.
"""
import importlib
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))

#: The repo's own git-ignored dotenv. Its EXISTENCE is all this file ever looks at.
REPO_DOTENV = os.path.join(MQTT, ".env")

#: A variable with no default and no other source, so its value can only have come from
#: whichever file the loader read.
PROBE = "MOXIE_WEBHOOK_ENDPOINT"
FROM_FILE = "http://127.0.0.1:9/from-the-dotenv"


def _fresh(monkeypatch, *, dotenv=None, skip=None):
    monkeypatch.delenv(PROBE, raising=False)
    monkeypatch.delenv("MOXIE_DOTENV", raising=False)
    monkeypatch.delenv("MOXIE_SKIP_DOTENV", raising=False)
    if dotenv is not None:
        monkeypatch.setenv("MOXIE_DOTENV", dotenv)
    if skip is not None:
        monkeypatch.setenv("MOXIE_SKIP_DOTENV", skip)
    import config as _c
    return importlib.reload(_c)


def test_a_dotenv_the_loader_can_see_is_still_suppressible(monkeypatch, tmp_path):
    """Both halves in ONE test, on purpose.

    Half two alone ("with the flag, the variable is unset") passes trivially against a
    loader that never read the file — which is precisely the state a worktree is in, and
    precisely why this defect survived. Half one is the control that makes half two mean
    something, so they have to fail together or not at all.
    """
    f = tmp_path / "dotenv"
    f.write_text(f"# a comment, and a blank line follow\n\n{PROBE}={FROM_FILE}\n")

    # 1. the file really is read when nothing opts out …
    c = _fresh(monkeypatch, dotenv=str(f))
    assert c.WEBHOOK_ENDPOINT == FROM_FILE, "the loader ignored the file it was given"
    assert c.DOTENV_LOADED == str(f)

    # 2. … and MOXIE_SKIP_DOTENV makes that same file invisible.
    c = _fresh(monkeypatch, dotenv=str(f), skip="1")
    assert c.WEBHOOK_ENDPOINT == "", "a dotenv refilled a deliberately unset variable"
    assert c.DOTENV_LOADED is None


def test_an_explicit_variable_still_beats_the_file(monkeypatch, tmp_path):
    """The appliance behaviour that must not change: `setdefault`, not overwrite. A
    `docker run -e` or a shell export outranks whatever is in the file."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    monkeypatch.setenv(PROBE, "http://127.0.0.1:9/from-the-environment")
    monkeypatch.setenv("MOXIE_DOTENV", str(f))
    monkeypatch.delenv("MOXIE_SKIP_DOTENV", raising=False)
    import config as _c
    c = importlib.reload(_c)
    assert c.WEBHOOK_ENDPOINT == "http://127.0.0.1:9/from-the-environment"


def test_the_flag_beats_even_an_explicitly_passed_path(monkeypatch, tmp_path):
    """"This process must see no file at all" has to be unconditional, or a helper that
    passes a path would quietly re-open the hole."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    monkeypatch.delenv(PROBE, raising=False)
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    import config as _c
    assert _c._load_env(str(f)) is None
    assert PROBE not in os.environ


def test_a_falsy_flag_does_not_switch_the_loader_off(monkeypatch, tmp_path):
    """`MOXIE_SKIP_DOTENV=0` must not read as "skip" — the repo's switches all spell
    false the same way (`""`/`0`/`off`/`false`/`no`), and a flag that fired on any value
    would disable configuration for anyone who wrote the obvious thing."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    for falsy in ("0", "off", "false", "no", ""):
        c = _fresh(monkeypatch, dotenv=str(f), skip=falsy)
        assert c.WEBHOOK_ENDPOINT == FROM_FILE, f"{falsy!r} was read as 'skip'"


def test_a_missing_file_is_not_an_error(monkeypatch, tmp_path):
    """A bare-metal supervisor with no dotenv at all is a supported deployment."""
    c = _fresh(monkeypatch, dotenv=str(tmp_path / "nope"))
    assert c.DOTENV_LOADED is None


# ---------------------------------------------------------------------------------
# The acceptance test, as code. It is the ONLY thing here that touches the repo's own
# `mqtt/.env`, it only asks whether the file exists, and it never opens it. In a
# worktree or on CI there is nothing to check and it skips — which is the whole reason
# this class of defect was invisible, so the skip is reported rather than silent.
# ---------------------------------------------------------------------------------

def test_the_repos_own_dotenv_is_invisible_to_a_test_that_opts_out(monkeypatch):
    import pytest
    if not os.path.exists(REPO_DOTENV):
        pytest.skip("no mqtt/.env in this checkout (a worktree or CI) — "
                    "the defect this pins is only visible in a main checkout")
    monkeypatch.delenv("MOXIE_DOTENV", raising=False)
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    import config as _c
    assert importlib.reload(_c).DOTENV_LOADED is None
